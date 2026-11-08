"""Accounts, sessions and login rules (main spec 7; milestone 6A).

- Passwords are scrypt-hashed; 2FA is optional (TOTP), and an admin can require it for admins.
- Failed logins count per account: after `lockoutAttempts` the account is locked for
  `lockoutMinutes`. Logins are also rate-limited per client address, always.
- A session is a random token in a cookie; only its SHA-256 is stored. It ends after
  `idleTimeoutMin` without a request, at logout, or when the password changes.
- Every login, failure, lockout and account change goes to the audit log.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devine_bms.auth import totp
from devine_bms.auth.passwords import DUMMY_HASH, hash_password, verify_password
from devine_bms.db import models as db
from devine_bms.haystack.scram import make_keys

ROLES = ("viewer", "operator", "admin")
RANK = {"viewer": 0, "operator": 1, "admin": 2}
USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789._-@")

DEFAULT_SECURITY: dict[str, Any] = {
    "minPasswordLength": 10,
    "idleTimeoutMin": 30,
    "lockoutAttempts": 5,
    "lockoutMinutes": 10,
    "requireTotpForAdmins": False,
    "kioskAddresses": [],  # HMI kiosks: viewer access without a login (commands need a PIN, milestone 7)
    "allowedNetworks": [],  # empty: any network may log in
    "haystackApi": False,  # the read-only Haystack API (milestone 12a-Haystack): off by default
}
LOGIN_RATE = (10, 60.0)  # attempts per client address, per seconds
PIN_IDLE_MIN = 5  # an HMI PIN unlocks commands for 5 minutes idle (main spec 7)
TOUCH_EVERY_S = 30  # how often a session's last_seen is written


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.message, **({"code": self.code} if self.code else {})}


@dataclass(frozen=True)
class Actor:
    """Who is making a request: a signed-in user, a kiosk, or (no database) the server itself."""

    client: str
    role: str
    user_id: uuid.UUID | None = None
    username: str | None = None
    dis: str | None = None
    kiosk: bool = False
    session_id: uuid.UUID | None = None
    pin: bool = False  # unlocked with an HMI PIN (5 minutes idle)

    @property
    def label(self) -> str:
        who = self.username or ("kiosk" if self.kiosk else "unknown")
        return f"{who} ({self.client})"

    def can(self, role: str) -> bool:
        return RANK[self.role] >= RANK[role]


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def user_json(u: db.User) -> dict:
    return {"username": u.username, "dis": u.dis, "role": u.role, "platform": u.platform, "enabled": u.enabled,
            "apiClient": bool(u.api_client),
            "totp": u.totp_secret is not None, "pin": u.pin_hash is not None,
            "locked": bool(u.locked_until and u.locked_until > _now()),
            "lockedUntil": u.locked_until.isoformat() if u.locked_until and u.locked_until > _now() else None,
            "lastLogin": u.last_login.isoformat() if u.last_login else None}


def in_networks(client: str, networks: list[str]) -> bool:
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    for net in networks:
        try:
            if address in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


class AuthService:
    def __init__(self, sessions: async_sessionmaker, site_name: str) -> None:
        self.sessions = sessions
        self.site_name = site_name
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._security: dict | None = None  # cached; reloaded when changed
        self.setup_code: str | None = None  # standalone first boot (milestone 11a); RAM only
        self.extra_kiosks: set[str] = set()  # standalone: the Pi's own screen is a kiosk
        self._setup_fails = 0
        self._setup_failed_at = 0.0

    # ---------- site and settings ----------

    async def _site(self, session: AsyncSession) -> db.Site:
        site = await session.scalar(select(db.Site).where(db.Site.name == self.site_name))
        if site is None:
            raise AuthError(f"no site '{self.site_name}'", 500)
        return site

    async def security(self) -> dict:
        if self._security is None:
            async with self.sessions() as session:
                site = await self._site(session)
                self._security = {**DEFAULT_SECURITY, **(site.security or {})}
        return self._security

    async def set_security(self, actor: Actor, changes: dict) -> dict:
        current = await self.security()
        clean: dict[str, Any] = {}
        for key, value in changes.items():
            if key not in DEFAULT_SECURITY:
                raise AuthError(f"unknown setting '{key}'", 422)
            clean[key] = value
        merged = {**current, **clean}
        if not isinstance(merged["minPasswordLength"], int) or not 10 <= merged["minPasswordLength"] <= 128:
            raise AuthError("the shortest password can be 10 to 128 characters (main spec 7)", 422)
        if not isinstance(merged["idleTimeoutMin"], int) or not 5 <= merged["idleTimeoutMin"] <= 720:
            raise AuthError("the idle time-out can be 5 to 720 minutes", 422)
        if not isinstance(merged["lockoutAttempts"], int) or not 3 <= merged["lockoutAttempts"] <= 20:
            raise AuthError("lockout after 3 to 20 failed logins", 422)
        if not isinstance(merged["lockoutMinutes"], int) or not 1 <= merged["lockoutMinutes"] <= 1440:
            raise AuthError("a lockout lasts 1 to 1440 minutes", 422)
        for key in ("kioskAddresses", "allowedNetworks"):
            if not isinstance(merged[key], list):
                raise AuthError(f"{key} must be a list", 422)
            for item in merged[key]:
                try:
                    (ipaddress.ip_address if key == "kioskAddresses" else
                     lambda x: ipaddress.ip_network(x, strict=False))(str(item))
                except ValueError as exc:
                    raise AuthError(f"{key}: '{item}' is not a valid address", 422) from exc
        if merged["allowedNetworks"] and not in_networks(actor.client, merged["allowedNetworks"]) \
                and not self.is_loopback(actor.client):
            raise AuthError("the allowed networks would lock you out: include the network you are on", 422)
        merged["requireTotpForAdmins"] = bool(merged["requireTotpForAdmins"])
        merged["haystackApi"] = bool(merged["haystackApi"])
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            before = {k: (site.security or {}).get(k, DEFAULT_SECURITY[k]) for k in clean}
            site.security = {k: merged[k] for k in DEFAULT_SECURITY}
            self._audit(session, site.id, actor, "settings", "security", None,
                        "changed the security settings", before, clean)
        self._security = merged
        return merged

    @staticmethod
    def is_loopback(client: str) -> bool:
        """The server itself: loopback, or a trusted proxy that named no other client (milestone 12a:
        the Proxmox host, for example through an SSH tunnel)."""
        from devine_bms.auth.proxies import trusted_proxy

        return trusted_proxy(client)

    # ---------- first run ----------

    async def has_users(self) -> bool:
        async with self.sessions() as session:
            site = await self._site(session)
            return bool(await session.scalar(select(func.count()).select_from(db.User)
                                             .where(db.User.site_id == site.id)))

    def check_setup_code(self, client: str, code: str | None) -> None:
        """Standalone mode (milestone 11a): away from the Pi itself, the first admin needs the
        one-time setup code shown on its screen. Five wrong codes lock it for a minute."""
        if self.is_loopback(client):
            return
        if not self.setup_code:
            raise AuthError("The first admin can only be created on the server itself.", 403)
        now = time.monotonic()
        if self._setup_fails >= 5 and now - self._setup_failed_at < 60:
            raise AuthError("Too many wrong setup codes; wait a minute.", 429)
        if not code or not secrets.compare_digest(str(code).strip(), self.setup_code):
            self._setup_fails = self._setup_fails + 1 if now - self._setup_failed_at < 60 else 1
            self._setup_failed_at = now
            raise AuthError("The setup code is not right. It is shown on the Pi's screen.", 403)

    async def create_first_admin(self, client: str, username: str, dis: str, password: str,
                                 setup_code: str | None = None) -> tuple[dict, str]:
        """The first-run page: only while there are no accounts, and only from the server itself
        (or, in standalone mode, with the setup code)."""
        self.check_setup_code(client, setup_code)
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            await session.execute(select(db.Site.id).where(db.Site.id == site.id).with_for_update())
            if await session.scalar(select(func.count()).select_from(db.User).where(db.User.site_id == site.id)):
                raise AuthError("This site already has accounts; log in instead.", 409)
            user = await self._new_user(session, site, username, dis, "admin", password, platform=False)
            actor = Actor(client=client, role="admin", user_id=user.id, username=user.username)
            self._audit(session, site.id, actor, "first-run", "user", user.username,
                        f"created the first admin account {user.username}", None, {"role": "admin"})
        self.setup_code = None  # used: it stops working once the admin exists
        token = await self._open_session(user.id, client)
        return user_json(user), token

    # ---------- login, sessions, logout ----------

    def _rate_limited(self, client: str) -> bool:
        limit, window = LOGIN_RATE
        now = time.monotonic()
        attempts = self._attempts[client]
        while attempts and now - attempts[0] > window:
            attempts.popleft()
        if len(attempts) >= limit:
            return True
        attempts.append(now)
        return False

    async def login(self, client: str, username: str, password: str, code: str | None,
                    via: str | None = None) -> tuple[dict, str]:
        """`via`: the Tailscale user, when the login came over Tailscale (milestone 12a)."""
        rules = await self.security()
        if self._rate_limited(client):
            raise AuthError("Too many login attempts from this address. Wait a minute and try again.", 429)
        if rules["allowedNetworks"] and not in_networks(client, rules["allowedNetworks"]) \
                and not self.is_loopback(client):
            raise AuthError("Logins are not allowed from this network.", 403)
        username = (username or "").strip().lower()
        error: AuthError | None = None  # raised after the transaction, so failures and their audit entries are kept
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await session.scalar(select(db.User).where(db.User.site_id == site.id,
                                                              db.User.username == username).with_for_update())
            if user is None:
                verify_password(password or "", DUMMY_HASH)  # same time as a wrong password
                self._audit(session, site.id, Actor(client=client, role="viewer", username=username[:64] or None),
                            "login-failed", "user", username[:100] or None, "failed login: unknown user name",
                            None, None)
                error = AuthError("Wrong user name or password.", 401)
            else:
                actor = Actor(client=client, role=user.role, user_id=user.id, username=user.username)
                if user.locked_until and user.locked_until > _now():
                    self._audit(session, site.id, actor, "login-failed", "user", user.username,
                                "failed login: account locked", None, None)
                    error = AuthError(f"This account is locked until {user.locked_until:%H:%M} UTC after too many "
                                      "failed logins. An admin can unlock it.", 423, "locked")
                elif not verify_password(password or "", user.password_hash) or not user.enabled:
                    self._fail(session, site.id, user, actor, rules, "wrong password" if user.enabled else "disabled")
                    error = AuthError("Wrong user name or password.", 401)
                elif user.api_client:  # milestone 12a-Haystack: the Haystack API only
                    self._audit(session, site.id, actor, "login-failed", "user", user.username,
                                "failed login: an API client account cannot use the web app", None, None)
                    error = AuthError("This is an API client account: it can only use the Haystack API.", 403)
                elif user.totp_secret and not code:
                    error = AuthError("Enter the code from your authenticator app.", 401, "totp-required")
                elif user.totp_secret and not totp.verify(user.totp_secret, code or ""):
                    self._fail(session, site.id, user, actor, rules, "wrong 2FA code")
                    error = AuthError("That code is not right. Check the time on your phone, and try the next code.",
                                      401, "totp-required")
                else:
                    user.failed_logins = 0
                    user.locked_until = None
                    user.last_login = _now()
                    where = f" over Tailscale as {via}" if via else ""
                    if user.platform:  # support (main spec 1): visible to the customer's admin
                        self._audit(session, site.id, actor, "support-login", "user", user.username,
                                    f"support logged in{where}", None, {"tailscale": via} if via else None)
                    else:
                        self._audit(session, site.id, actor, "login", "user", user.username, f"logged in{where}",
                                    None, {"tailscale": via} if via else None)
                    info = user_json(user)
        if error is not None:
            raise error
        token = await self._open_session(user.id, client)
        info["totpSetupNeeded"] = user.role == "admin" and rules["requireTotpForAdmins"] and not info["totp"]
        return info, token

    def _fail(self, session, site_id, user: db.User, actor: Actor, rules: dict, why: str) -> None:
        user.failed_logins += 1
        summary = f"failed login: {why}"
        if user.failed_logins >= rules["lockoutAttempts"]:
            user.locked_until = _now() + timedelta(minutes=rules["lockoutMinutes"])
            user.failed_logins = 0
            summary += f"; account locked for {rules['lockoutMinutes']} minutes"
        self._audit(session, site_id, actor, "login-failed", "user", user.username, summary, None, None)

    async def _open_session(self, user_id: uuid.UUID, client: str, kind: str = "web") -> str:
        token = secrets.token_urlsafe(32)
        async with self.sessions() as session, session.begin():
            session.add(db.UserSession(user_id=user_id, token_hash=token_hash(token), client=client[:64], kind=kind))
        return token

    # ---------- HMI PIN (main spec 7 and 6.2, milestone 7A) ----------

    async def kiosk_or_server(self, client: str) -> bool:
        return self.is_loopback(client) or client in {str(a) for a in (await self.security())["kioskAddresses"]}

    async def pin_users(self, client: str) -> list[dict]:
        """The people who can unlock commands on this HMI: operators and admins with a PIN."""
        if not await self.kiosk_or_server(client):
            raise AuthError("PINs are only used on a listed HMI kiosk.", 403)
        async with self.sessions() as session:
            site = await self._site(session)
            rows = (await session.scalars(select(db.User).where(
                db.User.site_id == site.id, db.User.enabled.is_(True), db.User.pin_hash.is_not(None),
                db.User.role.in_(("operator", "admin"))).order_by(db.User.dis))).all()
            return [{"username": u.username, "dis": u.dis} for u in rows]

    async def set_pin(self, actor: Actor, pin: str, password: str) -> None:
        if not pin.isdigit() or not 4 <= len(pin) <= 12:
            raise AuthError("a PIN is 4 to 12 digits", 422)
        if len(set(pin)) == 1 or pin in "01234567890123456789" or pin in "98765432109876543210":
            raise AuthError("pick a PIN that is not all one digit or a run like 1234", 422)
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await session.get(db.User, actor.user_id)
            if not verify_password(password or "", user.password_hash):
                raise AuthError("The password is not right.", 403)
            user.pin_hash = hash_password(pin)
            self._audit(session, site.id, actor, "pin", "user", user.username, "set an HMI PIN", None, None)

    async def remove_pin(self, actor: Actor, username: str | None = None) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await self._user(session, site, username) if username else await session.get(db.User, actor.user_id)
            user.pin_hash = None
            await session.execute(delete(db.UserSession).where(db.UserSession.user_id == user.id,
                                                               db.UserSession.kind == "pin"))
            self._audit(session, site.id, actor, "pin", "user", user.username,
                        f"removed the HMI PIN of {user.username}", None, None)

    async def pin_login(self, client: str, username: str, pin: str) -> tuple[dict, str]:
        """Unlock commands on an HMI kiosk for 5 minutes idle. Lockout and rate limits as for passwords."""
        if not await self.kiosk_or_server(client):
            raise AuthError("PINs are only used on a listed HMI kiosk.", 403)
        if self._rate_limited(client):
            raise AuthError("Too many attempts. Wait a minute and try again.", 429)
        rules = await self.security()
        error: AuthError | None = None
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await session.scalar(select(db.User).where(db.User.site_id == site.id,
                                                              db.User.username == (username or "").lower())
                                        .with_for_update())
            if user is None or user.pin_hash is None or user.role not in ("operator", "admin"):
                verify_password(pin or "", DUMMY_HASH)
                error = AuthError("Wrong PIN.", 401)
            else:
                actor = Actor(client=client, role=user.role, user_id=user.id, username=user.username)
                if user.locked_until and user.locked_until > _now():
                    error = AuthError("This account is locked after too many failed attempts.", 423, "locked")
                elif not user.enabled or not verify_password(pin or "", user.pin_hash):
                    self._fail(session, site.id, user, actor, rules, "wrong HMI PIN")
                    error = AuthError("Wrong PIN.", 401)
                else:
                    user.failed_logins, user.locked_until = 0, None
                    self._audit(session, site.id, actor, "login", "user", user.username, "unlocked the HMI with a PIN",
                                None, None)
                    info = user_json(user)
        if error is not None:
            raise error
        return info, await self._open_session(user.id, client, kind="pin")

    async def actor_for(self, token: str | None, client: str, touch: bool = True) -> Actor | None:
        """The signed-in user for a session cookie, or a kiosk by its address; None if neither."""
        rules = await self.security()
        if rules["allowedNetworks"] and not in_networks(client, rules["allowedNetworks"]) \
                and not self.is_loopback(client):
            return None
        if token:
            async with self.sessions() as session, session.begin():
                row = await session.scalar(select(db.UserSession).where(db.UserSession.token_hash == token_hash(token),
                                                                       db.UserSession.kind != "api"))
                if row is not None:
                    now = _now()
                    user = await session.get(db.User, row.user_id)
                    idle = PIN_IDLE_MIN if row.kind == "pin" else rules["idleTimeoutMin"]
                    if now - row.last_seen > timedelta(minutes=idle) or user is None \
                            or not user.enabled:
                        await session.delete(row)
                    else:
                        if touch and (now - row.last_seen).total_seconds() > TOUCH_EVERY_S:
                            row.last_seen = now
                        return Actor(client=client, role=user.role, user_id=user.id, username=user.username,
                                     dis=user.dis, session_id=row.id, pin=row.kind == "pin")
        if client in {str(a) for a in rules["kioskAddresses"]} or client in self.extra_kiosks:
            return Actor(client=client, role="viewer", kiosk=True)
        return None

    # ---------- Haystack API clients (milestone 12a-Haystack) ----------

    async def api_credentials(self, username: str) -> tuple[uuid.UUID, dict] | None:
        """The SCRAM keys of an enabled API client account, or None."""
        async with self.sessions() as session:
            site = await self._site(session)
            user = await session.scalar(select(db.User).where(db.User.site_id == site.id,
                                                              db.User.username == username.strip().lower()))
        if user is None or not user.enabled or not user.api_client or not user.scram:
            return None
        return user.id, dict(user.scram)

    async def api_login_done(self, client: str, username: str, user_id: uuid.UUID | None, proof_ok: bool) -> str:
        """After the SCRAM proof: lockout, the audit log and, on success, an API token. Raises AuthError."""
        rules = await self.security()
        error: AuthError | None = None
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await session.get(db.User, user_id, with_for_update=True) if user_id else None
            if user is None:
                self._audit(session, site.id, Actor(client=client, role="viewer", username=username[:64] or None),
                            "login-failed", "user", username[:100] or None,
                            "failed Haystack API login: unknown user name or not an API client", None, None)
                error = AuthError("Wrong user name or password.", 403)
            else:
                actor = Actor(client=client, role=user.role, user_id=user.id, username=user.username)
                if user.locked_until and user.locked_until > _now():
                    self._audit(session, site.id, actor, "login-failed", "user", user.username,
                                "failed Haystack API login: account locked", None, None)
                    error = AuthError("This account is locked after too many failed logins.", 403)
                elif not proof_ok:
                    self._fail(session, site.id, user, actor, rules, "wrong password (Haystack API)")
                    error = AuthError("Wrong user name or password.", 403)
                else:
                    user.failed_logins, user.locked_until, user.last_login = 0, None, _now()
                    self._audit(session, site.id, actor, "api-login", "user", user.username,
                                "logged in to the Haystack API", None, None)
        if error is not None:
            raise error
        return await self._open_session(user_id, client, kind="api")

    async def api_actor(self, token: str, client: str, idle_min: int = 60) -> Actor | None:
        """The API client for a Haystack bearer token (only API tokens: never a web session)."""
        rules = await self.security()
        if rules["allowedNetworks"] and not in_networks(client, rules["allowedNetworks"]) \
                and not self.is_loopback(client):
            return None
        async with self.sessions() as session, session.begin():
            row = await session.scalar(select(db.UserSession).where(db.UserSession.token_hash == token_hash(token),
                                                                   db.UserSession.kind == "api"))
            if row is None:
                return None
            user = await session.get(db.User, row.user_id)
            now = _now()
            if now - row.last_seen > timedelta(minutes=idle_min) or user is None or not user.enabled \
                    or not user.api_client:
                await session.delete(row)
                return None
            if (now - row.last_seen).total_seconds() > TOUCH_EVERY_S:
                row.last_seen = now
            return Actor(client=client, role="viewer", user_id=user.id, username=user.username, dis=user.dis,
                         session_id=row.id)

    def rate_limited(self, client: str) -> bool:
        return self._rate_limited(client)

    async def logout(self, actor: Actor, everywhere: bool = False) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            if everywhere and actor.user_id:
                await session.execute(delete(db.UserSession).where(db.UserSession.user_id == actor.user_id))
            elif actor.session_id:
                await session.execute(delete(db.UserSession).where(db.UserSession.id == actor.session_id))
            if actor.user_id:
                self._audit(session, site.id, actor, "logout", "user", actor.username,
                            "signed out everywhere" if everywhere else "logged out", None, None)

    # ---------- own account ----------

    async def me(self, actor: Actor) -> dict:
        if actor.user_id is None:
            return {"username": None, "dis": "HMI kiosk" if actor.kiosk else "Server", "role": actor.role,
                    "kiosk": actor.kiosk, "totp": False}
        async with self.sessions() as session:
            user = await session.get(db.User, actor.user_id)
            info = user_json(user)
        info["pinSession"] = actor.pin
        rules = await self.security()
        info["totpSetupNeeded"] = user.role == "admin" and rules["requireTotpForAdmins"] and not info["totp"]
        return info

    async def change_own_password(self, actor: Actor, current: str, new: str) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await session.get(db.User, actor.user_id)
            if not verify_password(current or "", user.password_hash):
                raise AuthError("The current password is not right.", 403)
            await self._check_password(new, user.username)
            user.password_hash = hash_password(new)
            # Other browsers signed in as this user are signed out.
            await session.execute(delete(db.UserSession).where(db.UserSession.user_id == user.id,
                                                               db.UserSession.id != actor.session_id))
            self._audit(session, site.id, actor, "password", "user", user.username, "changed own password",
                        None, None)

    async def totp_begin(self, actor: Actor) -> dict:
        secret = totp.new_secret()
        async with self.sessions() as session, session.begin():
            user = await session.get(db.User, actor.user_id)
            user.totp_pending = secret
        site_dis = await self._site_dis()
        return {"secret": secret, "uri": totp.provisioning_uri(secret, actor.username or "user", f"Devine BMS {site_dis}")}

    async def totp_confirm(self, actor: Actor, code: str) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await session.get(db.User, actor.user_id)
            if not user.totp_pending:
                raise AuthError("Start 2FA set-up first.", 409)
            if not totp.verify(user.totp_pending, code):
                raise AuthError("That code is not right. Check the time on your phone, and try the next code.", 422)
            user.totp_secret, user.totp_pending = user.totp_pending, None
            self._audit(session, site.id, actor, "2fa", "user", user.username, "turned on 2FA", None, None)

    async def totp_remove_own(self, actor: Actor, password: str) -> None:
        rules = await self.security()
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await session.get(db.User, actor.user_id)
            if not verify_password(password or "", user.password_hash):
                raise AuthError("The password is not right.", 403)
            if user.role == "admin" and rules["requireTotpForAdmins"]:
                raise AuthError("2FA is required for admins on this site.", 409)
            user.totp_secret = user.totp_pending = None
            self._audit(session, site.id, actor, "2fa", "user", user.username, "turned off 2FA", None, None)

    async def _site_dis(self) -> str:
        async with self.sessions() as session:
            return (await self._site(session)).dis

    # ---------- users (admin) ----------

    async def list_users(self) -> list[dict]:
        async with self.sessions() as session:
            site = await self._site(session)
            rows = (await session.scalars(select(db.User).where(db.User.site_id == site.id)
                                          .order_by(db.User.username))).all()
            return [user_json(u) for u in rows]

    async def add_user(self, actor: Actor, body: dict) -> dict:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            api = bool(body.get("apiClient", False))
            user = await self._new_user(session, site, body.get("username", ""), body.get("dis", ""),
                                        "viewer" if api else body.get("role", "viewer"), body.get("password", ""),
                                        False if api else bool(body.get("platform", False)), api_client=api)
            self._audit(session, site.id, actor, "user-add", "user", user.username,
                        f"added {'Haystack API client' if api else user.role} account {user.username}", None,
                        {"role": user.role, "dis": user.dis, "platform": user.platform, "apiClient": api})
            return user_json(user)

    async def change_user(self, actor: Actor, username: str, body: dict) -> dict:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await self._user(session, site, username)
            before = {"role": user.role, "dis": user.dis, "enabled": user.enabled, "platform": user.platform}
            if user.api_client and (body.get("role", "viewer") != "viewer" or body.get("platform")):
                raise AuthError("an API client account stays a viewer", 422)
            if "role" in body:
                if body["role"] not in ROLES:
                    raise AuthError(f"role must be one of {', '.join(ROLES)}", 422)
                user.role = body["role"]
            if "dis" in body:
                if not str(body["dis"]).strip():
                    raise AuthError("give the person's name", 422)
                user.dis = str(body["dis"]).strip()
            if "enabled" in body:
                user.enabled = bool(body["enabled"])
            if "platform" in body:
                user.platform = bool(body["platform"])
            await session.flush()
            if (before["role"] == "admin" and user.role != "admin") or (before["enabled"] and not user.enabled):
                await self._keep_an_admin(session, site)
            if not user.enabled:
                await session.execute(delete(db.UserSession).where(db.UserSession.user_id == user.id))
            if before["role"] != user.role:  # a role change takes effect at the next login
                await session.execute(delete(db.UserSession).where(db.UserSession.user_id == user.id))
            after = {"role": user.role, "dis": user.dis, "enabled": user.enabled, "platform": user.platform}
            changed = {k: v for k, v in after.items() if before[k] != v}
            self._audit(session, site.id, actor, "user-change", "user", user.username,
                        f"changed account {user.username}: {', '.join(changed) or 'nothing'}",
                        {k: before[k] for k in changed}, changed)
            return user_json(user)

    async def set_user_password(self, actor: Actor, username: str, password: str) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await self._user(session, site, username)
            await self._check_password(password, user.username)
            user.password_hash = hash_password(password)
            if user.api_client:
                user.scram = make_keys(password)
            user.failed_logins, user.locked_until = 0, None
            await session.execute(delete(db.UserSession).where(db.UserSession.user_id == user.id))
            self._audit(session, site.id, actor, "password", "user", user.username,
                        f"set a new password for {user.username}", None, None)

    async def unlock(self, actor: Actor, username: str) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await self._user(session, site, username)
            user.failed_logins, user.locked_until = 0, None
            self._audit(session, site.id, actor, "unlock", "user", user.username, f"unlocked {user.username}",
                        None, None)

    async def reset_totp(self, actor: Actor, username: str) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await self._user(session, site, username)
            user.totp_secret = user.totp_pending = None
            self._audit(session, site.id, actor, "2fa", "user", user.username, f"removed 2FA from {user.username}",
                        None, None)

    async def remove_user(self, actor: Actor, username: str) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            user = await self._user(session, site, username)
            if user.id == actor.user_id:
                raise AuthError("You cannot remove your own account.", 409)
            await session.delete(user)
            await session.flush()
            await self._keep_an_admin(session, site)
            self._audit(session, site.id, actor, "user-remove", "user", username, f"removed account {username}",
                        {"role": user.role, "dis": user.dis}, None)

    async def _keep_an_admin(self, session: AsyncSession, site: db.Site) -> None:
        count = await session.scalar(select(func.count()).select_from(db.User).where(
            db.User.site_id == site.id, db.User.role == "admin", db.User.enabled.is_(True)))
        if not count:
            raise AuthError("The site needs at least one enabled admin.", 409)

    async def _user(self, session: AsyncSession, site: db.Site, username: str) -> db.User:
        user = await session.scalar(select(db.User).where(db.User.site_id == site.id, db.User.username == username))
        if user is None:
            raise AuthError(f"no account '{username}'", 404)
        return user

    async def _new_user(self, session: AsyncSession, site: db.Site, username: str, dis: str, role: str,
                        password: str, platform: bool, api_client: bool = False) -> db.User:
        username = (username or "").strip().lower()
        if not 2 <= len(username) <= 64 or not set(username) <= USERNAME_CHARS:
            raise AuthError("user names are 2 to 64 characters: letters, digits and . _ - @", 422)
        if not (dis or "").strip():
            raise AuthError("give the person's name", 422)
        if role not in ROLES:
            raise AuthError(f"role must be one of {', '.join(ROLES)}", 422)
        if await session.scalar(select(db.User.id).where(db.User.site_id == site.id, db.User.username == username)):
            raise AuthError(f"there is already an account '{username}'", 409)
        await self._check_password(password, username)
        user = db.User(site_id=site.id, username=username, dis=dis.strip(), role=role, platform=platform,
                       password_hash=hash_password(password), api_client=api_client,
                       scram=make_keys(password) if api_client else None)
        session.add(user)
        await session.flush()
        return user

    async def _check_password(self, password: str, username: str) -> None:
        rules = await self.security()
        if len(password or "") < rules["minPasswordLength"]:
            raise AuthError(f"passwords need at least {rules['minPasswordLength']} characters", 422)
        if password.strip().lower() == username.lower():
            raise AuthError("the password cannot be the user name", 422)

    # ---------- audit log ----------

    async def record(self, actor: Actor, action: str, summary: str, kind: str | None = None,
                     name: str | None = None, before: Any = None, after: Any = None) -> None:
        async with self.sessions() as session, session.begin():
            site = await self._site(session)
            self._audit(session, site.id, actor, action, kind, name, summary, before, after)

    @staticmethod
    def _audit(session: AsyncSession, site_id, actor: Actor, action: str, kind: str | None, name: str | None,
               summary: str, before: Any, after: Any) -> None:
        session.add(db.AuditEntry(site_id=site_id, client=actor.client[:64], user_id=actor.user_id,
                                  username=(actor.username or ("kiosk" if actor.kiosk else None)),
                                  action=action[:32], kind=kind, name=name, summary=summary,
                                  before=before, after=after))

    async def prune(self, keep_days: int = 365) -> int:
        """Delete audit entries older than the retention period (main spec 5.6), and old sessions."""
        cutoff = _now() - timedelta(days=keep_days)
        rules = await self.security()
        async with self.sessions() as session, session.begin():
            result = await session.execute(delete(db.AuditEntry).where(db.AuditEntry.ts < cutoff))
            await session.execute(delete(db.UserSession).where(
                db.UserSession.last_seen < _now() - timedelta(minutes=rules["idleTimeoutMin"])))
            return result.rowcount or 0
