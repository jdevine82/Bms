"""Alarm delivery (main spec 5.5.1; milestone 6 decisions 2 to 4): email over SMTP, SMS through
ClickSend and push through ntfy, to the site's recipient list.

- Default channels per level: Info screen only; Warning email and ntfy; Critical email, ntfy
  and SMS. A site can change them.
- Each recipient ticks the levels they receive. Quiet hours (optional, per person) hold
  **Warning** only; held warnings go out as one summary when the window ends. Critical is never
  held. Info never leaves the screen unless a site adds a channel for it.
- Every notification is a row in the delivery log: pending (and retrying), held, sent or failed.
  Failed sends retry with back-off for up to a day, so an internet outage only delays them.
- Credentials are never stored: the settings name environment variables that hold them.
- Message text carries no sensitive detail: the alarm, the value and the site name only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import smtplib
import ssl
import uuid
from datetime import UTC, datetime, time as dtime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from devine_bms.db import models as db

log = logging.getLogger(__name__)

LEVELS = ("info", "warning", "critical")
CHANNELS = ("email", "sms", "ntfy")
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
RETRY_S = (30, 60, 300, 900, 3600)  # then hourly
GIVE_UP = timedelta(hours=24)
LEVEL_TITLE = {"info": "Info", "warning": "Warning", "critical": "Critical"}

DEFAULT_SETTINGS: dict[str, Any] = {
    "channels": {"info": [], "warning": ["email", "ntfy"], "critical": ["email", "ntfy", "sms"]},
    "email": {"host": "", "port": 587, "security": "starttls", "username": "", "passwordEnv": "BMS_SMTP_PASSWORD",
              "from": ""},
    "sms": {"provider": "clicksend", "username": "", "apiKeyEnv": "BMS_CLICKSEND_API_KEY", "from": "",
            "baseUrl": "https://rest.clicksend.com"},
    "ntfy": {"server": "https://ntfy.sh", "tokenEnv": None},
    "recipients": [],
}


class DeliveryError(Exception):
    pass


class SettingsError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def merged(stored: dict | None) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_SETTINGS.items()}
    for key, value in (stored or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict) and key != "channels":
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def check_settings(s: dict) -> list[str]:
    """Raise SettingsError for mistakes; return warnings (things that will not deliver)."""
    channels = s.get("channels", {})
    if set(channels) != set(LEVELS) or any(not set(v) <= set(CHANNELS) for v in channels.values()):
        raise SettingsError(f"channels must give a list from {list(CHANNELS)} for each of {list(LEVELS)}")
    email, sms, ntfy = s["email"], s["sms"], s["ntfy"]
    if email.get("security") not in ("starttls", "ssl", "none"):
        raise SettingsError("email security is starttls, ssl or none")
    if not isinstance(email.get("port"), int) or not 1 <= email["port"] <= 65535:
        raise SettingsError("the SMTP port is 1 to 65535")
    for label, value in (("email passwordEnv", email.get("passwordEnv")), ("sms apiKeyEnv", sms.get("apiKeyEnv")),
                         ("ntfy tokenEnv", ntfy.get("tokenEnv"))):
        if value and not ENV_NAME.match(value):
            raise SettingsError(f"{label} names an environment variable, for example BMS_SMTP_PASSWORD; "
                                "never put the secret itself here")
    if sms.get("provider") != "clicksend":
        raise SettingsError("the SMS provider is clicksend (the only plug-in so far)")
    if not str(ntfy.get("server", "")).startswith(("http://", "https://")):
        raise SettingsError("the ntfy server is a web address, for example https://ntfy.sh")
    ids = set()
    warnings: list[str] = []
    for r in s.get("recipients", []):
        if not r.get("id") or r["id"] in ids:
            raise SettingsError("each recipient needs a unique id")
        ids.add(r["id"])
        if not str(r.get("name", "")).strip():
            raise SettingsError("each recipient needs a name")
        q = r.get("quietHours")
        if q and (not HHMM.match(str(q.get("start", ""))) or not HHMM.match(str(q.get("end", "")))):
            raise SettingsError(f"{r['name']}: quiet hours are start and end times like 22:00 and 06:00")
        if r.get("phone") and not re.fullmatch(r"\+?[0-9 ]{8,16}", r["phone"]):
            raise SettingsError(f"{r['name']}: give the phone number with its country code, for example +61400000000")
        if r.get("ntfyTopic") and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", r["ntfyTopic"]):
            raise SettingsError(f"{r['name']}: an ntfy topic is letters, digits, - and _ (up to 64)")
    if not any(r.get("enabled", True) and r.get("levels", {}).get("critical") for r in s.get("recipients", [])):
        warnings.append("Nobody receives Critical alarms: tick Critical for at least one recipient.")
    used = {c for level in LEVELS for c in channels[level]}
    if "email" in used and not (email.get("host") and email.get("from")):
        warnings.append("Email is used but the SMTP server or sender address is not set.")
    if "sms" in used and not (sms.get("username") and sms.get("apiKeyEnv")):
        warnings.append("SMS is used but the ClickSend user name or API key variable is not set.")
    for label, env in (("SMTP password", email.get("passwordEnv") if email.get("username") else None),
                       ("ClickSend API key", sms.get("apiKeyEnv") if "sms" in used else None),
                       ("ntfy token", ntfy.get("tokenEnv"))):
        if env and not os.environ.get(env):
            warnings.append(f"The {label} variable {env} is not set on the server.")
    return warnings


def in_quiet_hours(quiet: dict | None, now_local: datetime) -> datetime | None:
    """When the person's quiet hours end (local time), if `now_local` is inside them; else None."""
    if not quiet:
        return None
    start = dtime.fromisoformat(quiet["start"])
    end = dtime.fromisoformat(quiet["end"])
    t = now_local.timetz().replace(tzinfo=None)
    if start == end:
        return None
    inside = (start <= t < end) if start < end else (t >= start or t < end)
    if not inside:
        return None
    end_dt = now_local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if end_dt <= now_local:
        end_dt += timedelta(days=1)
    return end_dt


def mask(channel: str, address: str) -> str:
    """For people who are not admins: enough to recognise, not the whole address."""
    if channel == "email" and "@" in address:
        user, _, domain = address.partition("@")
        return f"{user[:2]}…@{domain}"
    if channel == "sms":
        return f"…{address[-3:]}"
    return f"{address[:3]}…"


class Senders:
    """The three channels. `clients` can be swapped in tests."""

    def __init__(self, settings: dict) -> None:
        self.s = settings

    async def send(self, channel: str, address: str, level: str, subject: str, body: str) -> None:
        if channel == "email":
            await asyncio.to_thread(self._email, address, subject, body)
        elif channel == "sms":
            await self._sms(address, f"{subject}. {body}"[:300])
        elif channel == "ntfy":
            await self._ntfy(address, level, subject, body)
        else:
            raise DeliveryError(f"unknown channel {channel}")

    @staticmethod
    def _secret(env: str | None, what: str) -> str | None:
        if not env:
            return None
        value = os.environ.get(env)
        if not value:
            raise DeliveryError(f"the {what} environment variable {env} is not set on the server")
        return value

    def _email(self, to: str, subject: str, body: str) -> None:
        e = self.s["email"]
        if not e.get("host") or not e.get("from"):
            raise DeliveryError("the SMTP server or sender address is not set")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = e["from"], to, subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain="devine-bms")
        msg.set_content(body)
        password = self._secret(e.get("passwordEnv"), "SMTP password") if e.get("username") else None
        try:
            if e["security"] == "ssl":
                server: smtplib.SMTP = smtplib.SMTP_SSL(e["host"], e["port"], timeout=20,
                                                        context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(e["host"], e["port"], timeout=20)
            with server:
                if e["security"] == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if e.get("username"):
                    server.login(e["username"], password or "")
                server.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"SMTP: {exc}") from exc

    async def _sms(self, phone: str, text: str) -> None:
        sms = self.s["sms"]
        if not sms.get("username"):
            raise DeliveryError("the ClickSend user name is not set")
        key = self._secret(sms.get("apiKeyEnv"), "ClickSend API key")
        message: dict[str, Any] = {"source": "devine-bms", "body": text, "to": phone.replace(" ", "")}
        if sms.get("from"):
            message["from"] = sms["from"]
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(f"{sms['baseUrl'].rstrip('/')}/v3/sms/send", auth=(sms["username"], key or ""),
                                      json={"messages": [message]})
        except httpx.HTTPError as exc:
            raise DeliveryError(f"ClickSend: {exc}") from exc
        if r.status_code != 200:
            raise DeliveryError(f"ClickSend answered {r.status_code}: {r.text[:200]}")
        try:
            status = r.json()["data"]["messages"][0]["status"]
        except (ValueError, KeyError, IndexError, TypeError):
            status = "UNKNOWN"
        if status != "SUCCESS":
            raise DeliveryError(f"ClickSend did not accept the message ({status})")

    async def _ntfy(self, topic: str, level: str, title: str, body: str) -> None:
        n = self.s["ntfy"]
        headers = {}
        token = self._secret(n.get("tokenEnv"), "ntfy token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = {"topic": topic, "title": title, "message": body,
                   "priority": {"critical": 5, "warning": 4, "info": 3}[level],
                   "tags": [{"critical": "rotating_light", "warning": "warning", "info": "information_source"}[level]]}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(n["server"].rstrip("/") + "/", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise DeliveryError(f"ntfy: {exc}") from exc
        if r.status_code // 100 != 2:
            raise DeliveryError(f"ntfy answered {r.status_code}: {r.text[:200]}")


def address_for(recipient: dict, channel: str) -> str | None:
    return {"email": recipient.get("email"), "sms": recipient.get("phone"),
            "ntfy": recipient.get("ntfyTopic")}[channel] or None


class Delivery:
    """Queues notifications for raised alarms and sends them, retrying while sends fail."""

    def __init__(self, sessions, site_name: str, tz: str = "Australia/Sydney", poll_s: float = 5.0,
                 senders: type[Senders] = Senders) -> None:
        self.sessions = sessions
        self.site_name = site_name
        self.tz = ZoneInfo(tz)
        self.poll_s = poll_s
        self.senders = senders
        self.wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def _site(self, session) -> db.Site:
        return await session.scalar(select(db.Site).where(db.Site.name == self.site_name))

    async def settings(self) -> dict:
        async with self.sessions() as session:
            s = merged((await self._site(session)).alarm_settings)
        if not self.sms_allowed:  # standalone: the default Critical channels without SMS
            s["channels"] = {level: [c for c in chans if c != "sms"] for level, chans in s["channels"].items()}
        return s

    sms_allowed = True  # standalone mode offers on-screen, ntfy and email only (main spec 3.1)

    async def save_settings(self, data: dict) -> tuple[dict, list[str]]:
        s = merged(data)
        warnings = check_settings(s)
        if not self.sms_allowed and any("sms" in v for v in s["channels"].values()):
            raise SettingsError("SMS is not offered in standalone mode; use ntfy or email")
        for r in s["recipients"]:
            r.setdefault("enabled", True)
            r.setdefault("levels", {"info": False, "warning": False, "critical": False})
        async with self.sessions() as session, session.begin():
            (await self._site(session)).alarm_settings = s
        return s, warnings

    # ---------- queueing ----------

    async def queue_for(self, alarm: dict) -> int:
        s = await self.settings()
        level = alarm["level"]
        now = datetime.now(UTC)
        local = now.astimezone(self.tz)
        rows = 0
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            subject, body = self.compose(site.dis, alarm)
            for r in s["recipients"]:
                if not r.get("enabled", True) or not r.get("levels", {}).get(level):
                    continue
                hold = in_quiet_hours(r.get("quietHours"), local) if level == "warning" else None
                for channel in s["channels"][level]:
                    address = address_for(r, channel)
                    if not address:
                        continue
                    session.add(db.AlarmDelivery(
                        site_id=site.id, alarm_id=uuid.UUID(alarm["id"]), recipient=r["name"][:100], channel=channel,
                        address=address[:200], level=level, text=f"{subject}\n{body}",
                        status="held" if hold else "pending", next_attempt_at=None if hold else now,
                        hold_until=hold.astimezone(UTC) if hold else None))
                    rows += 1
        self.wake.set()
        return rows

    def compose(self, site_dis: str, alarm: dict) -> tuple[str, str]:
        subject = f"[{LEVEL_TITLE[alarm['level']]}] {site_dis}: {alarm['message']}"
        raised = datetime.fromisoformat(alarm["raisedAt"]).astimezone(self.tz)
        d = alarm.get("detail") or {}
        lines = [f"{alarm['message']} at {raised:%H:%M} on {raised:%d %b %Y}."]
        if "value" in d:
            value = d["value"]
            shown = round(value, 2) if isinstance(value, float) else value
            lines.append(f"Value {shown}{(' ' + d['unit']) if d.get('unit') else ''}"
                         + (f", limit {d['limit']}" if d.get("limit") is not None else "") + ".")
        lines.append("Open the BMS to acknowledge it.")
        return subject, " ".join(lines)

    # ---------- sending ----------

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="alarm delivery")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("alarm delivery pass failed")
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.poll_s)
            except TimeoutError:
                pass
            self.wake.clear()

    async def run_once(self) -> None:
        s = await self.settings()
        senders = self.senders(s)
        now = datetime.now(UTC)
        async with self.sessions() as session:
            site = await self._site(session)
            due = (await session.scalars(select(db.AlarmDelivery).where(
                db.AlarmDelivery.site_id == site.id, db.AlarmDelivery.status == "pending",
                db.AlarmDelivery.next_attempt_at <= now).order_by(db.AlarmDelivery.created_at).limit(50))).all()
            held = (await session.scalars(select(db.AlarmDelivery).where(
                db.AlarmDelivery.site_id == site.id, db.AlarmDelivery.status == "held",
                db.AlarmDelivery.hold_until <= now).order_by(db.AlarmDelivery.created_at))).all()
            site_dis = site.dis
        for row in due:
            subject, _, body = row.text.partition("\n")
            await self._attempt([row], senders, subject, body)
        groups: dict[tuple[str, str, str], list[db.AlarmDelivery]] = {}
        for row in held:
            groups.setdefault((row.recipient, row.channel, row.address), []).append(row)
        for rows in groups.values():
            subject = f"[Warning] {site_dis}: {len(rows)} warning{'s' if len(rows) > 1 else ''} during quiet hours"
            body = " ".join(f"{r.text.partition(chr(10))[0].split(': ', 1)[-1]}." for r in rows)
            await self._attempt(rows, senders, subject, body)

    async def _attempt(self, rows: list[db.AlarmDelivery], senders: Senders, subject: str, body: str) -> None:
        first = rows[0]
        error = None
        try:
            await senders.send(first.channel, first.address, first.level, subject, body)
        except DeliveryError as exc:
            error = str(exc)
        except Exception as exc:  # an unexpected failure is retried like any other
            error = f"{type(exc).__name__}: {exc}"
        now = datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            for row in rows:
                row = await session.merge(row)
                row.attempts += 1
                if error is None:
                    row.status, row.sent_at, row.error = "sent", now, None
                    continue
                row.error = error[:500]
                if now - row.created_at >= GIVE_UP:
                    row.status = "failed"
                else:
                    row.status = "pending"
                    row.next_attempt_at = now + timedelta(seconds=RETRY_S[min(row.attempts - 1, len(RETRY_S) - 1)])
        if error:
            log.warning("alarm delivery to %s by %s failed: %s", first.recipient, first.channel, error)

    async def send_test(self, recipient_id: str, channel: str) -> dict:
        s = await self.settings()
        r = next((x for x in s["recipients"] if x.get("id") == recipient_id), None)
        if r is None:
            raise SettingsError("no such recipient")
        if channel not in CHANNELS:
            raise SettingsError(f"channel is one of {list(CHANNELS)}")
        address = address_for(r, channel)
        if not address:
            raise SettingsError(f"{r['name']} has no {'email address' if channel == 'email' else 'phone number' if channel == 'sms' else 'ntfy topic'}")
        async with self.sessions() as session:
            site_dis = (await self._site(session)).dis
        subject = f"[Test] {site_dis}: test message"
        body = f"A test message for {r['name']} from the Devine BMS at {site_dis}."
        error = None
        try:
            await self.senders(s).send(channel, address, "info", subject, body)
        except DeliveryError as exc:
            error = str(exc)
        now = datetime.now(UTC)
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            session.add(db.AlarmDelivery(site_id=site.id, recipient=r["name"][:100], channel=channel, address=address,
                                         level="info", text=f"{subject}\n{body}", status="failed" if error else "sent",
                                         attempts=1, error=error, test=True, sent_at=None if error else now))
        return {"ok": error is None, "error": error}
