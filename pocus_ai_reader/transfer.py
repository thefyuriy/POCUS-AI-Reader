# SPDX-License-Identifier: AGPL-3.0-only
"""Export a library to a zip file and import one back.

Import treats the zip as untrusted: it never uses ``extractall``. Only entries
whose names match an exact, narrow pattern are read, every record is rebuilt
through the same sanitizers as normal input, and files are streamed to paths
built from validated ids, so a hostile zip cannot write outside the library.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from . import __version__
from .library import (
    ALLOWED_EXTS,
    CHUNK,
    ID_RE,
    MAX_THUMB_BYTES,
    Library,
    LibraryError,
    max_file_bytes,
    new_id,
    now_iso,
    sanitize_archive_meta,
    sanitize_clip,
)

FORMAT = "pocus-ai-reader-library"
FORMAT_VERSION = 1
MAX_JSON_BYTES = 5 * 1024 * 1024

_ENTRY_RE = re.compile(
    r"^archives/(?P<aid>[0-9a-f]{32})/"
    r"(?:(?P<json>archive\.json)"
    r"|media/(?P<mid>[0-9a-f]{32})(?P<ext>\.[a-z0-9]{2,5})"
    r"|thumbs/(?P<tid>[0-9a-f]{32})\.jpg)$"
)


def export_zip(lib: Library, dest: Path, archive_ids: list[str] | None = None) -> dict:
    ids = archive_ids if archive_ids else [a["id"] for a in lib.list_archives()]
    clips = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        manifest = {
            "format": FORMAT,
            "version": FORMAT_VERSION,
            "app_version": __version__,
            "exported": now_iso(),
            "archives": ids,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2), compress_type=zipfile.ZIP_DEFLATED)
        for aid in ids:
            record = lib.read_archive(aid)  # raises 404 for unknown ids
            clips += len(record.get("clips", []))
            base = lib.archive_dir(aid)
            zf.write(base / "archive.json", f"archives/{aid}/archive.json", compress_type=zipfile.ZIP_DEFLATED)
            for sub in ("media", "thumbs"):
                folder = base / sub
                if folder.is_dir():
                    for f in sorted(folder.iterdir()):
                        if f.is_file():
                            zf.write(f, f"archives/{aid}/{sub}/{f.name}")
    return {"archives": len(ids), "clips": clips}


def _copy_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, limit: int) -> int:
    written = 0
    with zf.open(info) as src, dest.open("wb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > limit:
                raise LibraryError("A file inside the zip is larger than the allowed maximum.", 413)
            out.write(chunk)
    return written


def import_zip(lib: Library, zip_path: Path) -> dict:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise LibraryError("That file is not a valid zip.", 400)
    with zf:
        names = {i.filename for i in zf.infolist()}
        try:
            manifest = json.loads(zf.read("manifest.json")) if "manifest.json" in names else None
        except (ValueError, KeyError):
            manifest = None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise LibraryError("That zip is not a POCUS AI Reader export.", 400)
        if int(manifest.get("version", 0)) > FORMAT_VERSION:
            raise LibraryError("That export is from a newer version of the app. Update the app first.", 400)

        groups: dict[str, dict] = {}
        ignored = 0
        for info in zf.infolist():
            name = info.filename
            if name == "manifest.json" or name.endswith("/"):
                continue
            m = _ENTRY_RE.match(name)
            if not m or (m.group("ext") and m.group("ext") not in ALLOWED_EXTS):
                ignored += 1
                continue
            g = groups.setdefault(m.group("aid"), {"json": None, "media": {}, "thumbs": {}})
            if m.group("json"):
                g["json"] = info
            elif m.group("mid"):
                g["media"][m.group("mid") + m.group("ext")] = info
            else:
                g["thumbs"][m.group("tid")] = info

        imported: list[str] = []
        clip_total = skipped = 0
        for source_id, g in groups.items():
            if g["json"] is None or g["json"].file_size > MAX_JSON_BYTES:
                skipped += len(g["media"])
                continue
            try:
                raw = json.loads(zf.read(g["json"]))
                if not isinstance(raw, dict) or not isinstance(raw.get("clips", []), list):
                    raise ValueError
            except ValueError:
                skipped += len(g["media"])
                continue

            target = source_id if not (lib.archives_dir / source_id).exists() else new_id()
            folder = lib.archive_dir(target)
            record = {"schema": 1, "id": target, **sanitize_archive_meta(raw), "clips": []}
            try:
                for rc in raw.get("clips", []):
                    cid = rc.get("id") if isinstance(rc, dict) else None
                    ext = str(rc.get("ext", "")).lower() if isinstance(rc, dict) else ""
                    media_info = g["media"].get(f"{cid}{ext}") if isinstance(cid, str) else None
                    if media_info is None or not ID_RE.match(cid) or ext not in ALLOWED_EXTS:
                        skipped += 1
                        continue
                    (folder / "media").mkdir(parents=True, exist_ok=True)
                    size = _copy_member(zf, media_info, folder / "media" / f"{cid}{ext}", max_file_bytes())
                    thumb_info = g["thumbs"].get(cid)
                    has_thumb = False
                    if thumb_info is not None and thumb_info.file_size <= MAX_THUMB_BYTES:
                        (folder / "thumbs").mkdir(parents=True, exist_ok=True)
                        _copy_member(zf, thumb_info, folder / "thumbs" / f"{cid}.jpg", MAX_THUMB_BYTES)
                        has_thumb = True
                    record["clips"].append(sanitize_clip(rc, ext=ext, size=size, has_thumb=has_thumb))
                    clip_total += 1
                lib.install_archive(target, record)
                imported.append(target)
            except BaseException:
                import shutil

                shutil.rmtree(folder, ignore_errors=True)
                raise

    if not imported:
        raise LibraryError("No archives could be imported from that zip.", 400)
    return {"archives": imported, "clips": clip_total, "skipped_clips": skipped, "ignored_entries": ignored}
