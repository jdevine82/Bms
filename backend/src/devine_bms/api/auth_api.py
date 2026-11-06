"""Login, accounts and the audit log over HTTP (main spec 5.6 and 7; milestone 6A).

Every /api route needs a signed-in user (or a listed HMI kiosk), except the health check,
the login state, login itself and the first-run page. Roles are checked per route:
`require("admin")` and so on.
"""

from __future__ import annotations

import csv
import io
import ipaddress
from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import or_, select

from devine_bms.auth.service import RANK, Actor, AuthError, AuthService
from devine_bms.db import models as db

COOKIE = "bms_session"
PUBLIC = {"/api/health", "/api/auth/state", "/api/auth/login", "/api/auth/first-admin", "/api/auth/pin-users",
          "/api/auth/pin"}


def client_ip(peer: str | None, forwarded: str | None) -> str:
    """The client's address. A request from this machine with X-Forwarded-For came through a
    local proxy (tailscale serve, main spec 8), so the address it names is the client."""
    host = peer or "unknown"
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host == "localhost"
    if local and forwarded:
        last = forwarded.split(",")[-1].strip()
        try:
            ipaddress.ip_address(last)
            return last
        except ValueError:
            return "unknown"
    return host


def request_client(request: Request) -> str:
    return client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"))


def actor_of(request: Request) -> Actor:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise HTTPException(401, "Log in first.")
    return actor


def require(role: str) -> Callable[[Request], Actor]:
    def check(request: Request) -> Actor:
        actor = actor_of(request)
        if RANK[actor.role] < RANK[role]:
            raise HTTPException(403, f"This needs the {role} role.")
        if actor.kiosk and role != "viewer":
            raise HTTPException(403, "An HMI kiosk can only view.")
        return actor
    return check


def install_guard(app, get_auth: Callable[[], AuthService | None]) -> None:
    """Resolve who is calling on every /api request, and refuse anyone not signed in."""

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        auth = get_auth()
        client = request_client(request)
        if auth is None:
            # No database (tests with the in-memory store): there are no accounts to check.
            request.state.actor = Actor(client=client, role="admin", username="local")
            return await call_next(request)
        request.state.actor = await auth.actor_for(request.cookies.get(COOKIE), client,
                                                   touch=request.query_params.get("touch") != "0")
        if request.state.actor is None and path not in PUBLIC:
            return JSONResponse({"ok": False, "error": "Log in first.", "code": "login"}, status_code=401)
        return await call_next(request)


def build_auth_router(get_auth: Callable[[], AuthService | None]) -> APIRouter:
    router = APIRouter()

    def auth() -> AuthService:
        svc = get_auth()
        if svc is None:
            raise HTTPException(503, "logins need the database")
        return svc

    async def run(call) -> Any:
        try:
            return await call
        except AuthError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)

    def with_cookie(request: Request, body: dict, token: str | None) -> JSONResponse:
        response = JSONResponse(body)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        if token is None:
            response.delete_cookie(COOKIE, path="/")
        else:
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, path="/")
        return response

    # ---------- signing in ----------

    @router.get("/api/auth/state")
    async def state(request: Request, svc: AuthService = Depends(auth)) -> Any:
        actor: Actor | None = request.state.actor
        has_users = await svc.has_users()
        client = request_client(request)
        return {"firstRun": not has_users, "firstRunHere": not has_users and svc.is_loopback(client),
                "user": await svc.me(actor) if actor else None}

    @router.post("/api/auth/first-admin")
    async def first_admin(request: Request, body: dict = Body(...), svc: AuthService = Depends(auth)) -> Any:
        try:
            user, token = await svc.create_first_admin(request_client(request), body.get("username", ""),
                                                       body.get("dis", ""), body.get("password", ""))
        except AuthError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)
        return with_cookie(request, {"ok": True, "user": user}, token)

    @router.post("/api/auth/login")
    async def login(request: Request, body: dict = Body(...), svc: AuthService = Depends(auth)) -> Any:
        try:
            user, token = await svc.login(request_client(request), str(body.get("username", "")),
                                          str(body.get("password", "")), body.get("code"))
        except AuthError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)
        return with_cookie(request, {"ok": True, "user": user}, token)

    @router.post("/api/auth/logout")
    async def logout(request: Request, body: dict = Body(default={}), svc: AuthService = Depends(auth)) -> Any:
        await svc.logout(actor_of(request), everywhere=bool(body.get("everywhere")))
        return with_cookie(request, {"ok": True}, None)

    # ---------- HMI PIN (milestone 7A) ----------

    @router.get("/api/auth/pin-users")
    async def pin_users(request: Request, svc: AuthService = Depends(auth)) -> Any:
        return await run(svc.pin_users(request_client(request)))

    @router.post("/api/auth/pin")
    async def pin_login(request: Request, body: dict = Body(...), svc: AuthService = Depends(auth)) -> Any:
        try:
            user, token = await svc.pin_login(request_client(request), str(body.get("username", "")),
                                              str(body.get("pin", "")))
        except AuthError as exc:
            return JSONResponse(exc.as_dict(), status_code=exc.status)
        return with_cookie(request, {"ok": True, "user": user}, token)

    # ---------- own account ----------

    @router.get("/api/auth/me")
    async def me(actor: Actor = Depends(require("viewer")), svc: AuthService = Depends(auth)) -> Any:
        return await svc.me(actor)

    def person(actor: Actor = Depends(require("viewer"))) -> Actor:
        if actor.user_id is None:
            raise HTTPException(403, "This needs a person's account.")
        return actor

    @router.post("/api/auth/me/password")
    async def own_password(body: dict = Body(...), actor: Actor = Depends(person),
                           svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.change_own_password(actor, body.get("current", ""), body.get("password", ""))))

    @router.post("/api/auth/me/pin")
    async def own_pin(body: dict = Body(...), actor: Actor = Depends(person), svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.set_pin(actor, str(body.get("pin", "")), str(body.get("password", "")))))

    @router.post("/api/auth/me/pin/remove")
    async def own_pin_remove(actor: Actor = Depends(person), svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.remove_pin(actor)))

    @router.post("/api/auth/me/totp")
    async def totp_begin(actor: Actor = Depends(person), svc: AuthService = Depends(auth)) -> Any:
        return await run(svc.totp_begin(actor))

    @router.post("/api/auth/me/totp/confirm")
    async def totp_confirm(body: dict = Body(...), actor: Actor = Depends(person),
                           svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.totp_confirm(actor, str(body.get("code", "")))))

    @router.post("/api/auth/me/totp/remove")
    async def totp_remove(body: dict = Body(...), actor: Actor = Depends(person),
                          svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.totp_remove_own(actor, str(body.get("password", "")))))

    # ---------- users (admin) ----------

    admin = require("admin")

    @router.get("/api/users")
    async def users(actor: Actor = Depends(admin), svc: AuthService = Depends(auth)) -> Any:
        return await svc.list_users()

    @router.post("/api/users")
    async def add_user(body: dict = Body(...), actor: Actor = Depends(admin), svc: AuthService = Depends(auth)) -> Any:
        return await run(svc.add_user(actor, body))

    @router.put("/api/users/{username}")
    async def change_user(username: str, body: dict = Body(...), actor: Actor = Depends(admin),
                          svc: AuthService = Depends(auth)) -> Any:
        return await run(svc.change_user(actor, username, body))

    @router.delete("/api/users/{username}")
    async def remove_user(username: str, actor: Actor = Depends(admin), svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.remove_user(actor, username)))

    @router.post("/api/users/{username}/password")
    async def user_password(username: str, body: dict = Body(...), actor: Actor = Depends(admin),
                            svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.set_user_password(actor, username, str(body.get("password", "")))))

    @router.post("/api/users/{username}/unlock")
    async def unlock(username: str, actor: Actor = Depends(admin), svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.unlock(actor, username)))

    @router.post("/api/users/{username}/pin/remove")
    async def reset_pin(username: str, actor: Actor = Depends(admin), svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.remove_pin(actor, username)))

    @router.post("/api/users/{username}/totp/remove")
    async def reset_totp(username: str, actor: Actor = Depends(admin), svc: AuthService = Depends(auth)) -> Any:
        return await run(_ok(svc.reset_totp(actor, username)))

    # ---------- security settings (admin) ----------

    @router.get("/api/auth/settings")
    async def get_settings(actor: Actor = Depends(admin), svc: AuthService = Depends(auth)) -> Any:
        return await svc.security()

    @router.put("/api/auth/settings")
    async def put_settings(body: dict = Body(...), actor: Actor = Depends(admin),
                           svc: AuthService = Depends(auth)) -> Any:
        return await run(svc.set_security(actor, body))

    # ---------- audit log (main spec 5.6) ----------

    async def audit_rows(actor: Actor, svc: AuthService, user: str | None, action: str | None, kind: str | None,
                         q: str | None, start: datetime | None, end: datetime | None, limit: int,
                         offset: int) -> list[dict]:
        if actor.kiosk or not actor.can("operator"):
            raise HTTPException(403, "The audit log needs the operator or admin role.")
        async with svc.sessions() as session:
            site = await svc._site(session)
            query = select(db.AuditEntry).where(db.AuditEntry.site_id == site.id)
            if not actor.can("admin"):  # operators see their own actions (main spec 7)
                query = query.where(db.AuditEntry.user_id == actor.user_id)
            elif user:
                query = query.where(db.AuditEntry.username == user)
            if action:
                query = query.where(db.AuditEntry.action == action)
            if kind:
                query = query.where(db.AuditEntry.kind == kind)
            if q:
                like = f"%{q}%"
                query = query.where(or_(db.AuditEntry.summary.ilike(like), db.AuditEntry.name.ilike(like)))
            if start:
                query = query.where(db.AuditEntry.ts >= start)
            if end:
                query = query.where(db.AuditEntry.ts < end)
            rows = (await session.scalars(query.order_by(db.AuditEntry.ts.desc()).offset(offset).limit(limit))).all()
        return [{"ts": r.ts.isoformat(), "user": r.username, "client": r.client, "action": r.action, "kind": r.kind,
                 "name": r.name, "summary": r.summary, "before": r.before, "after": r.after} for r in rows]

    @router.get("/api/audit")
    async def audit(request: Request, user: str | None = None, action: str | None = None, kind: str | None = None,
                    q: str | None = None, start: datetime | None = None, end: datetime | None = None,
                    limit: int = Query(200, ge=1, le=5000), offset: int = Query(0, ge=0),
                    svc: AuthService = Depends(auth)) -> Any:
        return await audit_rows(actor_of(request), svc, user, action, kind, q, start, end, limit, offset)

    @router.get("/api/audit.csv", response_class=PlainTextResponse)
    async def audit_csv(request: Request, user: str | None = None, action: str | None = None,
                        kind: str | None = None, q: str | None = None, start: datetime | None = None,
                        end: datetime | None = None, svc: AuthService = Depends(auth)) -> Any:
        rows = await audit_rows(actor_of(request), svc, user, action, kind, q, start, end, 100000, 0)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["time_utc", "user", "client", "action", "kind", "name", "summary"])
        for r in rows:
            writer.writerow([r["ts"], r["user"] or "", r["client"], r["action"], r["kind"] or "", r["name"] or "",
                             r["summary"]])
        return PlainTextResponse(out.getvalue(), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})

    return router


async def _ok(call) -> dict:
    await call
    return {"ok": True}
