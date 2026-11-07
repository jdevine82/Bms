"""Standalone mode services (milestone 11a): the RAM log's window, the USB export, CSV
downloads, the first-boot setup code and the Pi's network settings.

Settings (kept in the state file's `standalone` section):
    logWindowH (72), usbEnabled (true), usbIntervalMin (60)
"""

from __future__ import annotations

import asyncio
import csv
import io
import ipaddress
import logging
import os
import secrets
import shlex
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from devine_bms.db import models as db

log = logging.getLogger(__name__)

DEFAULTS = {"logWindowH": 72, "usbEnabled": True, "usbIntervalMin": 60}
PRUNE_EVERY_S = 600


class StandaloneError(Exception):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status

    def as_dict(self) -> dict:
        return {"ok": False, "error": self.message}


def _run(cmd: str, *args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run([*shlex.split(cmd), *args], capture_output=True, text=True, timeout=timeout, check=False)


class StandaloneService:
    def __init__(self, runtime, sessions, site_name: str, settings, keeper, tz: str, record=None,
                 usb_tick_s: float = 30.0) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.site_name = site_name
        self.cfg = settings  # devine_bms.settings.Settings
        self.keeper = keeper  # StateKeeper
        self.tz = ZoneInfo(tz)
        self.record = record
        self.usb_tick_s = usb_tick_s
        self.settings = {**DEFAULTS, **(keeper.standalone or {})}
        keeper.standalone = dict(self.settings)
        self.exported_until: datetime | None = None  # the end of the last exported minute
        self.last_export: datetime | None = None
        self.last_result: str | None = None  # "ok" or why it failed
        self.last_rows = 0
        self.next_export: datetime | None = None
        self._tasks: list[asyncio.Task] = []
        self._export_lock = asyncio.Lock()

    # ---------- life ----------

    async def start(self) -> None:
        now = datetime.now(UTC)
        self.next_export = now + timedelta(minutes=self.settings["usbIntervalMin"])
        await self.prune()
        self._tasks = [asyncio.create_task(self._prune_loop(), name="log window"),
                       asyncio.create_task(self._usb_loop(), name="usb export")]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---------- settings ----------

    async def save_settings(self, body: dict) -> dict:
        new = dict(self.settings)
        if "logWindowH" in body:
            h = body["logWindowH"]
            if not isinstance(h, int | float) or not 1 <= h <= 168:
                raise StandaloneError("the log window is 1 to 168 hours")
            new["logWindowH"] = h
        if "usbEnabled" in body:
            new["usbEnabled"] = bool(body["usbEnabled"])
        if "usbIntervalMin" in body:
            m = body["usbIntervalMin"]
            if not isinstance(m, int | float) or not 5 <= m <= 1440:
                raise StandaloneError("the export interval is 5 minutes to a day")
            new["usbIntervalMin"] = m
            self.next_export = datetime.now(UTC) + timedelta(minutes=m)
        self.settings = new
        self.keeper.standalone = dict(new)
        self.keeper.mark()
        return new

    # ---------- the RAM log ----------

    async def _prune_loop(self) -> None:
        while True:
            await asyncio.sleep(PRUNE_EVERY_S)
            try:
                await self.prune()
            except Exception:
                log.exception("pruning the log failed")

    async def prune(self) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=self.settings["logWindowH"])
        async with self.sessions() as session, session.begin():
            result = await session.execute(delete(db.PointHis).where(db.PointHis.ts < cutoff))
        return result.rowcount or 0

    def _logged_points(self) -> list:
        return sorted([p for p in self.runtime.config.points if p.his], key=lambda p: p.name)

    async def table(self, start: datetime, end: datetime) -> tuple[list[str], list[list[str]], list[datetime]]:
        """The log as one row a minute and a column a point: (header, rows, minute times in UTC).
        A minute's value is the last Good value logged in it; empty when there is none."""
        points = self._logged_points()
        ids = {p.id: i for i, p in enumerate(points)}
        async with self.sessions() as session:
            rows = (await session.execute(
                select(db.PointHis.point_id, db.PointHis.ts, db.PointHis.num, db.PointHis.str_, db.PointHis.status)
                .where(db.PointHis.ts >= start, db.PointHis.ts < end, db.PointHis.point_id.in_(list(ids)))
                .order_by(db.PointHis.ts))).all()
        minutes: dict[datetime, list[str]] = {}
        for pid, ts, num, text, status in rows:
            minute = ts.replace(second=0, microsecond=0)
            cells = minutes.setdefault(minute, [""] * len(points))
            if status == "ok":
                cells[ids[pid]] = f"{num:g}" if num is not None else (text or "")
            else:
                cells[ids[pid]] = ""
        header = ["time"] + [f"{p.name} ({p.unit})" if p.unit else p.name for p in points]
        times = sorted(minutes)
        body = [[t.astimezone(self.tz).strftime("%Y-%m-%d %H:%M")] + minutes[t] for t in times]
        return header, body, times

    async def csv_text(self, hours: float) -> str:
        end = datetime.now(UTC)
        header, rows, _ = await self.table(end - timedelta(hours=hours), end)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(header)
        w.writerows(rows)
        return out.getvalue()

    # ---------- USB export ----------

    async def _usb_loop(self) -> None:
        while True:
            await asyncio.sleep(self.usb_tick_s)
            if self.settings["usbEnabled"] and self.next_export and datetime.now(UTC) >= self.next_export:
                try:
                    await self.export()
                except Exception:
                    log.exception("USB export failed")
                self.next_export = datetime.now(UTC) + timedelta(minutes=self.settings["usbIntervalMin"])

    async def export(self) -> dict:
        """Append the log since the last export to the monthly CSV files on the stick."""
        async with self._export_lock:
            now = datetime.now(UTC)
            end = now.replace(second=0, microsecond=0)  # whole minutes only
            window_start = now - timedelta(hours=self.settings["logWindowH"])
            start = max(self.exported_until or window_start, window_start)
            header, rows, _times = await self.table(start, end)
            try:
                written = await asyncio.to_thread(self._write_usb, header, rows)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                self.last_result = str(exc) or type(exc).__name__
                self.last_export = now
                log.warning("USB export: %s", self.last_result)
                return self.status()
            self.exported_until = end
            self.last_export, self.last_result, self.last_rows = now, "ok", written
            return self.status()

    def _write_usb(self, header: list[str], rows: list[list[str]]) -> int:
        cfg = self.cfg
        if cfg.usb_mount_cmd:
            r = _run(cfg.usb_mount_cmd)
            if r.returncode != 0:
                raise RuntimeError("no USB stick (it could not be mounted)" + (f": {r.stderr.strip()}" if r.stderr.strip() else ""))
        try:
            base = Path(cfg.usb_path)
            if not base.is_dir():
                raise RuntimeError("no USB stick")
            by_month: dict[str, list[list[str]]] = {}
            for row in rows:
                by_month.setdefault(row[0][:7], []).append(row)
            for month, month_rows in by_month.items():
                path = self._file_for(base, month, header)
                new = not path.exists()
                with path.open("a", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(header)
                    w.writerows(month_rows)
                    f.flush()
                    os.fsync(f.fileno())
            os.sync()
            return len(rows)
        finally:
            if cfg.usb_umount_cmd:
                _run(cfg.usb_umount_cmd)

    @staticmethod
    def _file_for(base: Path, month: str, header: list[str]) -> Path:
        """2026-11.csv, or 2026-11-2.csv (and so on) when the points have changed."""
        buf = io.StringIO()
        csv.writer(buf).writerow(header)
        line = buf.getvalue().rstrip("\r\n")
        n = 1
        while True:
            path = base / (f"{month}.csv" if n == 1 else f"{month}-{n}.csv")
            if not path.exists():
                return path
            with path.open(encoding="utf-8") as f:
                first = f.readline().rstrip("\r\n")
            if first == line:
                return path
            n += 1

    def status(self) -> dict:
        return {"enabled": self.settings["usbEnabled"], "intervalMin": self.settings["usbIntervalMin"],
                "lastExport": self.last_export.isoformat() if self.last_export else None,
                "lastResult": self.last_result, "lastRows": self.last_rows,
                "exportedUntil": self.exported_until.isoformat() if self.exported_until else None,
                "nextExport": self.next_export.isoformat() if self.next_export and self.settings["usbEnabled"] else None}

    # ---------- first boot ----------

    async def prepare_setup_code(self, auth) -> None:
        """With no accounts yet, make the one-time setup code the Pi's screen shows."""
        if not await auth.has_users():
            auth.setup_code = f"{secrets.randbelow(10**6):06d}"

    @staticmethod
    def addresses() -> list[str]:
        out = []
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                addr = info[4][0]
                if not addr.startswith("127.") and addr not in out:
                    out.append(addr)
        except OSError:
            pass
        try:  # the address used to reach other machines, when there is a route
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.0.2.1", 9))
            addr = s.getsockname()[0]
            s.close()
            if addr not in out and not addr.startswith("127."):
                out.insert(0, addr)
        except OSError:
            pass
        return out

    # ---------- network (the Pi's helper) ----------

    def network(self) -> dict:
        if not self.cfg.net_cmd:
            return {"managed": False, "addresses": self.addresses()}
        r = _run(self.cfg.net_cmd, "show")
        if r.returncode != 0:
            raise StandaloneError(f"cannot read the network settings: {r.stderr.strip() or r.stdout.strip()}", 502)
        import json

        try:
            current = json.loads(r.stdout)
        except ValueError:
            current = {"raw": r.stdout.strip()}
        return {"managed": True, "addresses": self.addresses(), **current}

    def set_network(self, body: dict) -> dict:
        if not self.cfg.net_cmd:
            raise StandaloneError("network settings are changed by the Pi's install (BMS_NET_CMD is not set)", 409)
        mode = body.get("mode")
        if mode == "dhcp":
            args = ["dhcp"]
        elif mode == "static":
            try:
                iface = ipaddress.ip_interface(str(body.get("address", "")))
                if iface.network.prefixlen in (0, 32):
                    raise ValueError("give the address with its network size, for example 192.168.1.20/24")
                gw = ipaddress.ip_address(str(body.get("gateway", "")))
                if gw not in iface.network:
                    raise ValueError("the gateway must be on the same network")
                dns = [str(ipaddress.ip_address(d.strip())) for d in str(body.get("dns", "")).split(",") if d.strip()]
            except ValueError as exc:
                raise StandaloneError(str(exc)) from exc
            args = ["static", str(iface), str(gw), ",".join(dns) or str(gw)]
        else:
            raise StandaloneError("mode is dhcp or static")
        r = _run(self.cfg.net_cmd, *args, timeout=60)
        if r.returncode != 0:
            raise StandaloneError(f"the network change failed: {r.stderr.strip() or r.stdout.strip()}", 502)
        return {"ok": True, "applied": args}

    # ---------- summary ----------

    def summary(self) -> dict[str, Any]:
        k = self.keeper
        return {"mode": "standalone", "settings": self.settings, "usb": self.status(),
                "state": {"file": str(k.path), "lastSaved": k.last_saved.isoformat() if k.last_saved else None,
                          "lastError": k.last_error, "saves": k.saves},
                "points": len(self.runtime.config.points), "pointLimit": 50,
                "loggedPoints": len(self._logged_points())}
