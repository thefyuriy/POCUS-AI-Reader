# SPDX-License-Identifier: AGPL-3.0-only
"""Shared helpers: a live local server and a tiny HTTP client (standard library only)."""
from __future__ import annotations

import http.client
import json
import os
import tempfile
import uuid
from pathlib import Path

from pocus_ai_reader.app import start_background_server, stop_server
from pocus_ai_reader.library import Library

TOKEN = "test-token-0123456789"


def fake_mp4(size: int = 4096) -> bytes:
    return b"\x00\x00\x00\x18ftypmp42" + os.urandom(size)


def multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes]]):
    boundary = "----pocus" + uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, filename, data in files:
        head = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'
        )
        parts.append(head.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body)


class Client:
    def __init__(self, port: int, cookie: bool = True):
        self.port, self.cookie = port, cookie

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> Response:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        if self.cookie:
            h.setdefault("Cookie", f"pocus_session={TOKEN}")
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def send_json(self, method: str, path: str, data=None, headers: dict | None = None) -> Response:
        h = {"Content-Type": "application/json", **(headers or {})}
        return self.request(method, path, json.dumps(data if data is not None else {}).encode(), h)

    def upload(self, path: str, fields, files, method: str = "POST") -> Response:
        body, ctype = multipart(fields, files)
        return self.request(method, path, body, {"Content-Type": ctype})


class ServerFixture:
    """Starts a real server on a free port with a throwaway library."""

    def __init__(self, engine=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.library = Library(self.root / "library")
        self.export_dir = self.root / "exports"
        self.server, self.thread, self.port = start_background_server(
            self.library, TOKEN, export_dir=self.export_dir, allow_reveal=False, engine=engine
        )
        self.client = Client(self.port)

    def close(self):
        stop_server(self.server, self.thread)
        self.tmp.cleanup()
