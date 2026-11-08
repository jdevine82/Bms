"""The Haystack HTTP API and the model export over HTTP (milestone 12a-Haystack, decision 032).

`/api/haystack/<op>`: GET with query parameters or POST with a grid (Haystack JSON or Zinc). It
does its own authentication (Haystack SCRAM, API client accounts only), so the web login guard
lets it through. While the API is switched off every request answers 404.
`/api/haystack-export`: the site model as a Haystack JSON or Zinc file (admins, web login).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from devine_bms.api.auth_api import request_client, require
from devine_bms.auth.service import Actor
from devine_bms.haystack.codec import (
    JSON,
    ZINC,
    DecodeError,
    choose,
    decode,
    encode,
    value_from_zinc,
)
from devine_bms.haystack.kinds import Grid
from devine_bms.haystack.model import build_model
from devine_bms.haystack.server import OPS, AuthFailure, HaystackService

OP_NAMES = {n for n, _ in OPS}


def _query_grid(request: Request) -> Grid:
    """GET: each query parameter is a Zinc value; filter (and anything that is not Zinc) is text."""
    row: dict[str, Any] = {}
    for k, v in request.query_params.items():
        if k == "filter":
            row[k] = v
            continue
        try:
            row[k] = value_from_zinc(v)
        except (DecodeError, ValueError, IndexError):
            row[k] = v
    return Grid.of([row]) if row else Grid()


def build_haystack_router(get_svc: Callable[[], HaystackService | None]) -> APIRouter:
    router = APIRouter()

    @router.api_route("/api/haystack/{op}", methods=["GET", "POST"])
    async def haystack(op: str, request: Request) -> Any:
        svc = get_svc()
        if svc is None or not await svc.enabled():
            return PlainTextResponse("The Haystack API is switched off (Setup > Security).", status_code=404)
        mime = choose(request.headers.get("accept"))
        try:
            actor = await svc.authenticate(request.headers.get("authorization"), request_client(request))
        except AuthFailure as f:
            if f.status != 200:
                return PlainTextResponse(f.message, status_code=f.status, headers=f.headers)
            # The SCRAM exchange finished: Authentication-Info carries the token; answer the op too.
            return Response(encode(await svc.op_about(Grid(), None), mime), media_type=mime, headers=f.headers)
        if op not in OP_NAMES:
            return PlainTextResponse(f"unknown or unsupported operation '{op}' (the API is read-only)",
                                     status_code=404)
        if svc.rate_limited(str(actor.session_id)):
            return PlainTextResponse("too many requests: at most 30 every 10 seconds", status_code=429)
        try:
            req = decode(await request.body(), request.headers.get("content-type")) if request.method == "POST" \
                else _query_grid(request)
        except (DecodeError, UnicodeDecodeError, ValueError) as exc:
            return Response(encode(Grid.error(f"cannot read the request: {exc}"), mime), media_type=mime)
        result = await svc.call(op, req, actor)
        return Response(encode(result, mime), media_type=mime)

    @router.get("/api/haystack-export")
    async def export(format: str = "json", _: Actor = Depends(require("admin"))) -> Any:
        svc = get_svc()
        if svc is None:
            raise HTTPException(404, "the model export needs the database")
        mime = ZINC if format == "zinc" else JSON
        m = await build_model(svc.sessions, svc.site_name, live=False)
        body = encode(Grid.of([dict(e) for e in m.entities.values()],
                              {"dis": f"Devine BMS site model: {svc.site_name}"}), mime)
        name = f"{svc.site_name}-haystack.{'zinc' if mime == ZINC else 'json'}"
        return Response(body, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return router
