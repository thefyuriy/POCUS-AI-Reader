# SPDX-License-Identifier: AGPL-3.0-only
"""Keeps the local server private to the app's own window.

The server listens only on 127.0.0.1, but any web page open in a browser on the
same computer could still try to call it. Three checks stop that:

* the Host header must be the app's own address (blocks DNS-rebinding),
* every API and media request needs a random per-launch session cookie that the
  app window receives once through the ``?t=`` launch link,
* state-changing requests must not carry a foreign Origin header.
"""
from __future__ import annotations

import hmac

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

COOKIE = "pocus_session"


class GuardMiddleware:
    def __init__(self, app, token: str, port: int):
        self.app = app
        self.token = token.encode()
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        refusal = self._gate(Request(scope))
        if refusal is not None:
            await refusal(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _gate(self, request: Request) -> Response | None:
        if request.headers.get("host", "") not in self.hosts:
            return PlainTextResponse("Forbidden.", status_code=403)
        path = request.url.path
        if path.startswith("/static/"):
            return None  # only the app's own scripts and styles
        supplied = request.query_params.get("t")
        if path == "/" and supplied and hmac.compare_digest(supplied.encode(), self.token):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE, self.token.decode(), httponly=True, samesite="strict", path="/")
            return response
        cookie = request.cookies.get(COOKIE, "")
        if not hmac.compare_digest(cookie.encode(), self.token):
            return PlainTextResponse("Open this app from its own window.", status_code=401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin is not None and origin not in self.origins:
                return PlainTextResponse("Forbidden origin.", status_code=403)
        return None
