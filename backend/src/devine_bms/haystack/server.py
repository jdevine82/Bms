"""The read-only Haystack HTTP API (milestone 12a-Haystack; main spec 5.12, decision 032).

Authentication: Haystack HELLO -> SCRAM (SHA-256) -> BEARER, for API client accounts only.
Operations: about, ops, formats, read, nav, hisRead, watchSub, watchPoll, watchUnsub. Reads come
from the database and the live value cache, never from the field buses.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time as _time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from devine_bms import __version__
from devine_bms.haystack import scram
from devine_bms.haystack.codec import to_datetime, value_from_zinc
from devine_bms.haystack.filter import FilterError, matches, parse
from devine_bms.haystack.kinds import MARKER, DateTime, Grid, Number, Ref, Uri
from devine_bms.haystack.model import build_model, his_value

log = logging.getLogger(__name__)

HANDSHAKE_S = 60
HIS_MAX_DAYS = 31
HIS_MAX_ROWS = 100_000
HIS_MAX_IDS = 20
WATCHES_PER_CLIENT = 10
WATCH_MAX_POINTS = 1000
LEASE_DEFAULT_S = 300
LEASE_MAX_S = 3600
RATE = (30, 10.0)  # requests per client token, per seconds
TOKEN_IDLE_MIN = 60

OPS = [("about", "Summary information for the server"), ("ops", "Operations supported by the server"),
       ("formats", "Grid data formats supported by the server"), ("read", "Read entity records in the database"),
       ("nav", "Navigate the site: site, rooms, equipment, points"), ("hisRead", "Read time-series history"),
       ("watchSub", "Watch entities for live values"), ("watchUnsub", "Close a watch or remove entities"),
       ("watchPoll", "Poll a watch for changes"), ("close", "Close this session (log out)")]


class OpError(Exception):
    """An operation's error: answered as an error grid (HTTP 200), as the Haystack API says."""


class AuthFailure(Exception):
    def __init__(self, status: int, message: str, headers: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.headers = status, message, headers or {}


@dataclass
class Handshake:
    username: str
    user_id: Any
    keys: dict
    created: float
    c1_bare: str | None = None
    s1: str | None = None
    nonce: str | None = None


@dataclass
class Watch:
    id: str
    owner: Any
    dis: str
    lease_s: float
    ids: list[str] = field(default_factory=list)
    sent: dict[str, tuple] = field(default_factory=dict)  # id -> (curStatus, curVal) last sent
    touched: float = 0.0


def parse_auth(header: str) -> tuple[str, dict[str, str]]:
    """`SCRAM handshakeToken=x, data=y` -> ("SCRAM", {...})."""
    scheme, _, rest = header.strip().partition(" ")
    params = {}
    for part in rest.split(","):
        k, sep, v = part.strip().partition("=")
        if sep:
            params[k.strip()] = v.strip()
    return scheme.upper(), params


def _attrs(message: str) -> dict[str, str]:
    out = {}
    for part in message.split(","):
        if len(part) >= 2 and part[1] == "=":
            out[part[0]] = part[2:]
    return out


class HaystackService:
    def __init__(self, sessions, site_name: str, auth, runtime, mode: str = "full", clock=_time.monotonic) -> None:
        self.sessions = sessions
        self.site_name = site_name
        self.auth = auth
        self.runtime = runtime
        self.mode = mode
        self.clock = clock
        self.secret = secrets.token_bytes(32)
        self.handshakes: dict[str, Handshake] = {}
        self.watches: dict[str, Watch] = {}
        self.calls: dict[str, deque] = defaultdict(deque)
        self.boot = datetime.now(UTC)
        self._lock = asyncio.Lock()

    async def enabled(self) -> bool:
        if self.mode == "standby":  # the API is the site server's (main spec 5.12)
            return False
        return bool((await self.auth.security()).get("haystackApi"))

    # ---------- authentication ----------

    async def authenticate(self, header: str | None, client: str):
        """The API client for a request, or AuthFailure (401 with the next SCRAM step, or 403).
        A successful SCRAM final step raises AuthFailure(200, ...) with Authentication-Info."""
        if not header:
            raise AuthFailure(401, "log in with Haystack SCRAM authentication (an API client account)",
                              {"WWW-Authenticate": "HELLO"})
        scheme, p = parse_auth(header)
        if scheme == "BEARER":
            actor = await self.auth.api_actor(p.get("authToken", ""), client, TOKEN_IDLE_MIN)
            if actor is None:
                raise AuthFailure(401, "the auth token is not valid (log in again)", {"WWW-Authenticate": "HELLO"})
            return actor
        if scheme == "HELLO":
            return await self._hello(p, client)
        if scheme == "SCRAM":
            return await self._scram(p, client)
        raise AuthFailure(401, f"unsupported authentication {scheme}", {"WWW-Authenticate": "HELLO"})

    def _purge(self) -> None:
        now = self.clock()
        for t in [t for t, h in self.handshakes.items() if now - h.created > HANDSHAKE_S]:
            self.handshakes.pop(t, None)

    async def _hello(self, p: dict, client: str):
        if self.auth.rate_limited(client):
            raise AuthFailure(429, "too many login attempts from this address; wait a minute")
        try:
            username = scram.unb64(p.get("username", "")).decode().strip().lower()
        except (ValueError, UnicodeDecodeError):
            raise AuthFailure(400, "HELLO needs username=<base64>") from None
        rules = await self.auth.security()
        if rules["allowedNetworks"] and not self.auth.is_loopback(client):
            from devine_bms.auth.service import in_networks

            if not in_networks(client, rules["allowedNetworks"]):
                raise AuthFailure(403, "logins are not allowed from this network")
        found = await self.auth.api_credentials(username)
        user_id, keys = found if found else (None, scram.fake_keys(self.secret, username))
        self._purge()
        token = secrets.token_hex(16)
        self.handshakes[token] = Handshake(username, user_id, keys, self.clock())
        raise AuthFailure(401, "continue with SCRAM",
                          {"WWW-Authenticate": f"SCRAM handshakeToken={token}, hash=SHA-256"})

    async def _scram(self, p: dict, client: str):
        self._purge()
        token = p.get("handshakeToken", "")
        hs = self.handshakes.get(token)
        if hs is None:
            raise AuthFailure(403, "the handshake has expired or is unknown: start again with HELLO")
        try:
            data = scram.unb64(p.get("data", "")).decode()
        except (ValueError, UnicodeDecodeError):
            raise AuthFailure(400, "bad SCRAM data") from None
        if hs.s1 is None:  # client-first-message: n,,n=user,r=nonce
            if not data.startswith("n,"):
                raise AuthFailure(403, "SCRAM channel binding is not supported")
            bare = data.split(",", 2)[2] if data.count(",") >= 2 else ""
            a = _attrs(bare)
            name = a.get("n", "").replace("=2C", ",").replace("=3D", "=").strip().lower()
            if name != hs.username or not a.get("r"):
                self.handshakes.pop(token, None)
                raise AuthFailure(403, "the SCRAM user name does not match HELLO")
            hs.c1_bare = bare
            hs.nonce = a["r"] + secrets.token_hex(16)
            hs.s1 = f"r={hs.nonce},s={hs.keys['salt']},i={hs.keys['i']}"
            raise AuthFailure(401, "continue with SCRAM", {
                "WWW-Authenticate": f"SCRAM handshakeToken={token}, hash=SHA-256, data={scram.b64(hs.s1.encode())}"})
        # client-final-message: c=biws,r=nonce,p=proof
        self.handshakes.pop(token, None)
        without_proof, _, proof = data.rpartition(",p=")
        a = _attrs(without_proof)
        auth_message = f"{hs.c1_bare},{hs.s1},{without_proof}"
        ok = hs.user_id is not None and a.get("r") == hs.nonce and scram.check_proof(hs.keys, auth_message, proof)
        from devine_bms.auth.service import AuthError

        try:
            auth_token = await self.auth.api_login_done(client, hs.username, hs.user_id, ok)
        except AuthError as exc:
            raise AuthFailure(403, exc.message) from None
        signature = scram.server_signature(hs.keys, auth_message)
        raise AuthFailure(200, "logged in", {
            "Authentication-Info": f"authToken={auth_token}, data={scram.b64(('v=' + signature).encode())}"})

    def rate_limited(self, key: str) -> bool:
        limit, window = RATE
        now = self.clock()
        q = self.calls[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False

    # ---------- operations ----------

    async def call(self, op: str, req: Grid, actor) -> Grid:
        handler = getattr(self, f"op_{op}", None)
        if handler is None:
            raise KeyError(op)
        try:
            return await handler(req, actor)
        except (OpError, FilterError, ValueError, LookupError) as exc:
            return Grid.error(str(exc))

    async def _model(self, live: bool = True):
        return await build_model(self.sessions, self.site_name, self.runtime, live=live)

    async def op_about(self, req: Grid, actor) -> Grid:
        m = await self._model(live=False)
        site = next(iter(m.entities.values()))
        now = to_datetime(datetime.now(UTC), m.tz)
        row = {"haystackVersion": "4.0", "tz": m.tz, "serverName": self.site_name, "serverTime": now,
               "serverBootTime": to_datetime(self.boot, m.tz), "productName": "Devine BMS",
               "productUri": Uri("https://devine.example/bms"), "productVersion": __version__,
               "vendorName": "Devine BMS", "vendorUri": Uri("https://devine.example/"),
               "moduleName": "devine_bms", "moduleVersion": __version__, "siteDis": site.get("dis"),
               "whoami": getattr(actor, "username", None)}
        return Grid.of([row])

    async def op_ops(self, req: Grid, actor) -> Grid:
        return Grid.of([{"name": n, "summary": s} for n, s in OPS])

    async def op_formats(self, req: Grid, actor) -> Grid:
        return Grid.of([{"mime": "text/zinc", "receive": MARKER, "send": MARKER},
                        {"mime": "application/json", "receive": MARKER, "send": MARKER}])

    async def op_read(self, req: Grid, actor) -> Grid:
        m = await self._model()
        rows = req.rows
        if rows and "id" in rows[0] and "filter" not in rows[0]:
            out = []
            for r in rows:
                ref = r.get("id")
                e = m.get(ref.val) if isinstance(ref, Ref) else None
                out.append(dict(e) if e else {})
            return _grid(out)
        first = rows[0] if rows else {}
        text = first.get("filter")
        if not isinstance(text, str):
            raise OpError("read needs a filter (or ids)")
        node = parse(text)
        limit = first.get("limit")
        limit = int(limit.val) if isinstance(limit, Number) else None
        out = []
        for e in m.entities.values():
            if matches(node, e, m.get):
                out.append(dict(e))
                if limit is not None and len(out) >= limit:
                    break
        return _grid(out)

    async def op_nav(self, req: Grid, actor) -> Grid:
        m = await self._model()
        nav = (req.rows[0].get("navId") if req.rows else None)
        key = nav.val if isinstance(nav, Ref) else (nav if isinstance(nav, str) else None)
        if key is not None and key.startswith("@"):
            key = key[1:]
        out = []
        for rid in m.children.get(key, []):
            e = dict(m.entities[rid])
            if m.children.get(rid):
                e["navId"] = Ref(rid)
            out.append(e)
        return _grid(out)

    async def op_hisRead(self, req: Grid, actor) -> Grid:
        if not req.rows:
            raise OpError("hisRead needs an id and a range")
        ids = [r.get("id") for r in req.rows]
        rng = req.rows[0].get("range", req.meta.get("range"))
        if len(ids) > 1:
            rng = req.meta.get("range", rng)
        if len(ids) > HIS_MAX_IDS:
            raise OpError(f"at most {HIS_MAX_IDS} points in one hisRead")
        m = await self._model(live=False)
        refs = []
        for ref in ids:
            if not isinstance(ref, Ref) or m.kinds.get(ref.val) != "point":
                raise OpError(f"not a point: {ref}")
            if "his" not in m.entities[ref.val]:
                raise OpError(f"{m.entities[ref.val]['dis']} is not logged (no his tag)")
            refs.append(m.entities[ref.val]["id"])
        start, end = parse_range(rng, m.tz)
        if end - start > timedelta(days=HIS_MAX_DAYS, hours=1):
            raise OpError(f"the range can be at most {HIS_MAX_DAYS} days")
        series = []
        total = 0
        for ref in refs:
            rows = await self.runtime.storage.read_history(uuid.UUID(ref.val), start, end)
            e = m.entities[ref.val]
            good = [(r.ts, his_value(r.num if e["kind"] != "Str" else r.str_, e["kind"], e.get("unit")))
                    for r in rows if r.status.value == "ok" and (r.num is not None or r.str_ is not None)]
            total += len(good)
            if total > HIS_MAX_ROWS:
                raise OpError(f"more than {HIS_MAX_ROWS} rows: ask for a shorter range")
            series.append(good)
        meta = {"hisStart": to_datetime(start, m.tz), "hisEnd": to_datetime(end, m.tz)}
        if len(refs) == 1:
            meta["id"] = refs[0]
            return Grid(meta, [("ts", {}), ("val", {})],
                        [{"ts": to_datetime(ts, m.tz), "val": v} for ts, v in series[0]])
        cols = [("ts", {})] + [(f"v{i}", {"id": ref}) for i, ref in enumerate(refs)]
        merged: dict[datetime, dict] = {}
        for i, s in enumerate(series):
            for ts, v in s:
                merged.setdefault(ts, {"ts": to_datetime(ts, m.tz)})[f"v{i}"] = v
        return Grid(meta, cols, [merged[k] for k in sorted(merged)])

    async def op_close(self, req: Grid, actor) -> Grid:
        """Log out: the token stops working; this client's watches close."""
        async with self._lock:
            for wid in [w.id for w in self.watches.values() if w.owner == actor.user_id]:
                self.watches.pop(wid, None)
        await self.auth.logout(actor)
        return Grid.of([])

    # ---------- watches ----------

    def _expire_watches(self) -> None:
        now = self.clock()
        for wid in [w.id for w in self.watches.values() if now - w.touched > w.lease_s]:
            self.watches.pop(wid, None)

    async def op_watchSub(self, req: Grid, actor) -> Grid:
        async with self._lock:
            self._expire_watches()
            wid = req.meta.get("watchId")
            if wid is not None:
                w = self._own(wid, actor)
            else:
                if sum(1 for w in self.watches.values() if w.owner == actor.user_id) >= WATCHES_PER_CLIENT:
                    raise OpError(f"at most {WATCHES_PER_CLIENT} watches per API client: close one with watchUnsub")
                w = Watch(secrets.token_hex(8), actor.user_id, str(req.meta.get("watchDis") or "watch"),
                          LEASE_DEFAULT_S)
                self.watches[w.id] = w
            lease = req.meta.get("lease")
            if isinstance(lease, Number):
                w.lease_s = min(LEASE_MAX_S, max(10.0, _seconds(lease)))
            m = await self._model()
            ids = [r.get("id") for r in req.rows]
            new = [i.val for i in ids if isinstance(i, Ref) and i.val not in w.ids]
            if len(w.ids) + len(new) > WATCH_MAX_POINTS:
                raise OpError(f"at most {WATCH_MAX_POINTS} entities in a watch")
            w.ids += [i for i in new if i in m.entities]
            w.touched = self.clock()
            rows = []
            for i in ids:
                e = m.get(i.val) if isinstance(i, Ref) else None
                rows.append(dict(e) if e else {})
                if e:
                    w.sent[i.val] = (e.get("curStatus"), e.get("curVal"))
            return _grid(rows, {"watchId": w.id, "lease": Number(w.lease_s, "s")})

    async def op_watchUnsub(self, req: Grid, actor) -> Grid:
        async with self._lock:
            w = self._own(req.meta.get("watchId"), actor)
            if "close" in req.meta:
                self.watches.pop(w.id, None)
            else:
                drop = {r["id"].val for r in req.rows if isinstance(r.get("id"), Ref)}
                w.ids = [i for i in w.ids if i not in drop]
                for i in drop:
                    w.sent.pop(i, None)
            return Grid.of([])

    async def op_watchPoll(self, req: Grid, actor) -> Grid:
        async with self._lock:
            self._expire_watches()
            w = self._own(req.meta.get("watchId"), actor)
            w.touched = self.clock()
            refresh = "refresh" in req.meta
            m = await self._model()
            rows = []
            for i in w.ids:
                e = m.get(i)
                if e is None:
                    continue
                now = (e.get("curStatus"), e.get("curVal"))
                if refresh or w.sent.get(i) != now:
                    rows.append(dict(e))
                    w.sent[i] = now
            return _grid(rows, {"watchId": w.id})

    def _own(self, wid, actor) -> Watch:
        w = self.watches.get(str(wid)) if wid is not None else None
        if w is None or w.owner != actor.user_id:
            raise OpError(f"unknown or expired watch {wid}")
        return w


def _grid(rows: list[dict], meta: dict | None = None) -> Grid:
    return Grid.of(rows, meta)


def _seconds(n: Number) -> float:
    return n.val * {"ms": 0.001, "s": 1, "sec": 1, "min": 60, "h": 3600, "hr": 3600, "day": 86400}.get(n.unit or "s", 1)


def parse_range(rng: Any, tz: str) -> tuple[datetime, datetime]:
    """hisRead ranges: today, yesterday, a date, "date,date", a date-time, or "dateTime,dateTime"
    (Haystack). Dates are whole days in the site's time zone; the end date is included."""
    from devine_bms.api.timezones import iana_zone

    zone = iana_zone(tz)
    today = datetime.now(zone).date()

    def day_start(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, tzinfo=zone)

    def one(v: Any):
        if isinstance(v, str):
            v = v.strip()
            if v == "today":
                return today
            if v == "yesterday":
                return today - timedelta(days=1)
            try:
                v = value_from_zinc(v)
            except ValueError:  # DecodeError is a ValueError
                raise OpError(f"cannot read the range {rng!r}: use today, yesterday, a date, two dates, "
                              "or one or two date-times") from None
        if isinstance(v, (DateTime, date)):
            return v
        raise OpError(f"cannot read the range {rng!r}")

    if isinstance(rng, str) and "," in rng:
        a, b = (one(x) for x in rng.split(",", 1))
    elif rng is None:
        raise OpError("hisRead needs a range")
    else:
        a = one(rng)
        b = a
    start = a.val if isinstance(a, DateTime) else day_start(a)
    if isinstance(b, DateTime):
        end = b.val if b is not a else datetime.now(UTC)
    else:
        end = day_start(b + timedelta(days=1))
    if end <= start:
        raise OpError("the range ends before it starts")
    return start, end

