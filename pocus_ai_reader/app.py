# SPDX-License-Identifier: AGPL-3.0-only
"""The local web server behind the app window."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__, transfer
from .library import (
    UPLOAD_EXTS,
    MAX_LABEL,
    MAX_THUMB_BYTES,
    MEDIA_TYPES,
    VIEWS,
    Library,
    LibraryError,
    clean_text,
    default_export_dir,
    file_ext,
    guess_view,
)
from .security import GuardMiddleware

STATIC_DIR = Path(__file__).parent / "static"

INDEX_CSP = (
    "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
    "style-src 'self'; script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


# --------------------------------------------------------------------------
# Request helpers
# --------------------------------------------------------------------------

async def _json_body(request: Request, required: bool = True) -> dict:
    raw = await request.body()
    if not raw:
        if required:
            raise LibraryError("Request body must be JSON.", 400)
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise LibraryError("Request body must be JSON.", 400)
    if not isinstance(data, dict):
        raise LibraryError("Request body must be a JSON object.", 400)
    return data


def _tag_list(value):
    if value is None:
        return None
    if isinstance(value, str):
        return [t for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        return value
    raise LibraryError("Tags must be a list.", 400)


def _uploads(form) -> tuple[list[UploadFile], list[str]]:
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
    views = [str(v) for v in form.getlist("views")]
    if form.get("confirm_deidentified") != "true":
        raise LibraryError("Confirm that the files are de-identified before uploading.", 400)
    for f in files:
        file_ext(f.filename)  # reject unsupported types before anything is written
    return files, views


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def create_app(library: Library, token: str, port: int, export_dir: Path | None = None,
               allow_reveal: bool = True) -> Starlette:
    export_folder = Path(export_dir) if export_dir else default_export_dir(library.root)

    async def index(request: Request):
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={"Cache-Control": "no-store", "Content-Security-Policy": INDEX_CSP},
        )

    async def info(request: Request):
        return JSONResponse({
            "version": __version__,
            "views": VIEWS,
            "extensions": sorted(UPLOAD_EXTS),
            "library_path": str(library.root),
            "export_path": str(export_folder),
            "can_reveal": allow_reveal and sys.platform == "darwin",
        })

    async def guess_views(request: Request):
        data = await _json_body(request)
        names = data.get("names")
        if not isinstance(names, list) or len(names) > 2000:
            raise LibraryError("Send a list of file names.", 400)
        return JSONResponse({"views": [guess_view(n) for n in names]})

    async def archives_list(request: Request):
        return JSONResponse(await run_in_threadpool(library.list_archives))

    async def archives_create(request: Request):
        form = await request.form(max_files=1000, max_fields=3000)
        try:
            files, views = _uploads(form)
            if not files:
                raise LibraryError("Add at least one file.", 400)
            archive = library.create_archive(
                clean_text(form.get("label", ""), MAX_LABEL),
                str(form.get("notes", "")),
                _tag_list(str(form.get("tags", ""))),
            )
            ids: list[str] = []
            try:
                for i, f in enumerate(files):
                    view = views[i] if i < len(views) else ""
                    clip = await run_in_threadpool(library.add_clip, archive["id"], f.filename, f.file, view)
                    ids.append(clip["id"])
            except BaseException:
                library.delete_archive(archive["id"])
                raise
            result = library.get_archive(archive["id"])
            result["new_clip_ids"] = ids
            return JSONResponse(result, status_code=201)
        finally:
            await form.close()

    async def archive_get(request: Request):
        return JSONResponse(library.get_archive(request.path_params["aid"]))

    async def archive_patch(request: Request):
        data = await _json_body(request)
        updated = library.update_archive(
            request.path_params["aid"],
            label=data.get("label"),
            notes=data.get("notes"),
            tags=_tag_list(data.get("tags")),
        )
        return JSONResponse(updated)

    async def archive_delete(request: Request):
        library.delete_archive(request.path_params["aid"])
        return JSONResponse({"deleted": True})

    async def clips_add(request: Request):
        aid = request.path_params["aid"]
        library.get_archive(aid)
        form = await request.form(max_files=1000, max_fields=3000)
        try:
            files, views = _uploads(form)
            if not files:
                raise LibraryError("Add at least one file.", 400)
            ids: list[str] = []
            for i, f in enumerate(files):
                view = views[i] if i < len(views) else ""
                clip = await run_in_threadpool(library.add_clip, aid, f.filename, f.file, view)
                ids.append(clip["id"])
            result = library.get_archive(aid)
            result["new_clip_ids"] = ids
            return JSONResponse(result, status_code=201)
        finally:
            await form.close()

    async def clip_patch(request: Request):
        data = await _json_body(request)
        clip = library.update_clip(
            request.path_params["aid"],
            request.path_params["cid"],
            view=data.get("view"),
            tags=_tag_list(data.get("tags")),
            notes=data.get("notes"),
        )
        return JSONResponse(clip)

    async def clip_delete(request: Request):
        library.delete_clip(request.path_params["aid"], request.path_params["cid"])
        return JSONResponse({"deleted": True})

    async def thumb_put(request: Request):
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_THUMB_BYTES:
            raise LibraryError("Thumbnail is too large.", 413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_THUMB_BYTES:
                raise LibraryError("Thumbnail is too large.", 413)
        library.set_thumbnail(request.path_params["aid"], request.path_params["cid"], bytes(body))
        return JSONResponse({"ok": True})

    async def media(request: Request):
        path, clip = library.media_path(request.path_params["aid"], request.path_params["cid"])
        return FileResponse(
            path,
            media_type=MEDIA_TYPES.get(clip["ext"], "application/octet-stream"),
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
        )

    async def thumb(request: Request):
        path = library.thumb_path(request.path_params["aid"], request.path_params["cid"])
        if path is None:
            return JSONResponse({"detail": "No thumbnail."}, status_code=404)
        kind = MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
        return FileResponse(path, media_type=kind,
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"})

    async def export(request: Request):
        data = await _json_body(request, required=False)
        ids = data.get("ids")
        if ids is not None and (not isinstance(ids, list) or not all(isinstance(i, str) for i in ids)):
            raise LibraryError("ids must be a list of archive ids.", 400)
        export_folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = export_folder / f"POCUS-AI-Reader-export-{stamp}.zip"
        n = 1
        while target.exists():
            n += 1
            target = export_folder / f"POCUS-AI-Reader-export-{stamp}-{n}.zip"
        try:
            counts = await run_in_threadpool(transfer.export_zip, library, target, ids)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return JSONResponse({
            "filename": target.name, "path": str(target), "folder": str(export_folder),
            "size": target.stat().st_size, **counts,
        })

    async def import_(request: Request):
        form = await request.form(max_files=2, max_fields=10)
        tmp_path = None
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise LibraryError("Choose a .zip file exported from this app.", 400)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix=".import-",
                                             dir=library.root) as tmp:
                tmp_path = Path(tmp.name)
                await run_in_threadpool(shutil.copyfileobj, upload.file, tmp, 1024 * 1024)
            result = await run_in_threadpool(transfer.import_zip, library, tmp_path)
            return JSONResponse(result)
        finally:
            await form.close()
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    async def reveal(request: Request):
        data = await _json_body(request, required=False)
        target = data.get("target", "library")
        flags: list[str] = []
        if target == "library":
            path = library.root
        elif target == "export":
            name = str(data.get("filename", ""))
            if name != os.path.basename(name) or not name.endswith(".zip") or not (export_folder / name).is_file():
                raise LibraryError("That export file was not found.", 404)
            path, flags = export_folder / name, ["-R"]
        else:
            raise LibraryError("Unknown target.", 400)
        revealed = False
        if allow_reveal and sys.platform == "darwin":
            subprocess.run(["open", *flags, str(path)], check=False)
            revealed = True
        return JSONResponse({"revealed": revealed, "path": str(path)})

    async def library_error(request: Request, exc: LibraryError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    routes = [
        Route("/", index),
        Route("/api/info", info),
        Route("/api/guess-views", guess_views, methods=["POST"]),
        Route("/api/archives", archives_list, methods=["GET"]),
        Route("/api/archives", archives_create, methods=["POST"]),
        Route("/api/archives/{aid}", archive_get, methods=["GET"]),
        Route("/api/archives/{aid}", archive_patch, methods=["PATCH"]),
        Route("/api/archives/{aid}", archive_delete, methods=["DELETE"]),
        Route("/api/archives/{aid}/clips", clips_add, methods=["POST"]),
        Route("/api/archives/{aid}/clips/{cid}", clip_patch, methods=["PATCH"]),
        Route("/api/archives/{aid}/clips/{cid}", clip_delete, methods=["DELETE"]),
        Route("/api/archives/{aid}/clips/{cid}/thumbnail", thumb_put, methods=["PUT"]),
        Route("/api/export", export, methods=["POST"]),
        Route("/api/import", import_, methods=["POST"]),
        Route("/api/reveal", reveal, methods=["POST"]),
        Route("/media/{aid}/{cid}", media),
        Route("/thumb/{aid}/{cid}", thumb),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    return Starlette(
        routes=routes,
        middleware=[Middleware(GuardMiddleware, token=token, port=port)],
        exception_handlers={LibraryError: library_error},
    )


# --------------------------------------------------------------------------
# Running the server
# --------------------------------------------------------------------------

def start_background_server(library: Library, token: str, export_dir: Path | None = None,
                            port: int = 0, allow_reveal: bool = True):
    """Start the server on 127.0.0.1 in a thread. Returns (server, thread, port)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    actual_port = sock.getsockname()[1]
    app = create_app(library, token, actual_port, export_dir, allow_reveal)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="off", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:
        raise RuntimeError("The local server did not start.")
    return server, thread, actual_port


def stop_server(server, thread) -> None:
    server.should_exit = True
    thread.join(timeout=10)
