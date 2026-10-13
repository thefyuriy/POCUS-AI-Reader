# SPDX-License-Identifier: AGPL-3.0-only
"""Plain-file library storage for POCUS AI Reader.

Everything lives in ordinary files so a library can be copied, backed up or
inspected without special tools:

    <root>/archives/<archive id>/archive.json
    <root>/archives/<archive id>/media/<clip id>.<ext>
    <root>/archives/<archive id>/thumbs/<clip id>.jpg

Ids are random 32-character hex strings. Every id that reaches the file system
is validated first, so user input can never build a path outside the library.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable

from .convert import ConversionError, avi_to_mp4

SCHEMA_VERSION = 1
VIEWS = ["PLAX", "PSAX", "A4C", "SC4C", "IVC", "Other"]
CLIP_EXTS = {".mp4", ".m4v", ".mov", ".webm"}
STILL_EXTS = {".jpg", ".jpeg", ".png", ".gif"}
ALLOWED_EXTS = CLIP_EXTS | STILL_EXTS  # what can be stored and played
CONVERT_EXTS = {".avi"}                 # accepted on upload, converted to MP4 first
UPLOAD_EXTS = ALLOWED_EXTS | CONVERT_EXTS
ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_TAGS = 30
MAX_NOTES = 20_000
MAX_LABEL = 200
MAX_THUMB_BYTES = 2 * 1024 * 1024
MAX_RESULTS_BYTES = 200_000
CHUNK = 1024 * 1024

MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}


class LibraryError(Exception):
    """A problem the person using the app can understand and act on."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Locations and small helpers
# --------------------------------------------------------------------------

def max_file_bytes() -> int:
    return int(os.environ.get("POCUS_AI_READER_MAX_FILE_BYTES", 4 * 1024**3))


def default_library_dir() -> Path:
    """Where the library lives.

    On macOS this is deliberately *not* inside Documents or Desktop, because
    iCloud can sync those folders between Macs and the library is meant to stay
    on one computer (use Export / Import to move it).
    """
    env = os.environ.get("POCUS_AI_READER_LIBRARY")
    if env:
        return Path(env).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "POCUS AI Reader"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "pocus-ai-reader"


def default_export_dir(root: Path) -> Path:
    env = os.environ.get("POCUS_AI_READER_EXPORT_DIR")
    if env:
        return Path(env).expanduser()
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path(root) / "exports"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    return uuid.uuid4().hex


def check_id(value: str, what: str = "item") -> str:
    if not isinstance(value, str) or not ID_RE.match(value):
        raise LibraryError(f"Invalid {what} id.", 404)
    return value


def clean_text(value, limit: int) -> str:
    text = "" if value is None else str(value)
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    return text.strip()[:limit]


def clean_filename(name) -> str:
    text = "" if name is None else str(name)
    text = text.replace("\\", "/").split("/")[-1]
    text = "".join(ch for ch in text if ord(ch) >= 32).strip()
    return text[:200] or "file"


def normalize_tags(tags: Iterable | None) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        t = str(tag).strip().lstrip("#").lower()
        t = re.sub(r"\s+", "-", t)
        t = re.sub(r"[^a-z0-9_\-]", "", t)[:40]
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS]


def normalize_view(value, strict: bool = True) -> str:
    v = "" if value is None else str(value).strip()
    if v == "" or v in VIEWS:
        return v
    if strict:
        raise LibraryError(f"Unknown view '{v}'. Use one of: {', '.join(VIEWS)}.", 400)
    return ""


_VIEW_RULES = [
    ("IVC", re.compile(r"(?<![a-z])(ivc|inferior[ _-]?vena)(?![a-z])")),
    ("SC4C", re.compile(r"(?<![a-z])(sc4c|subcostal|subxiphoid|sub[ _-]?4)(?![a-z])")),
    ("PLAX", re.compile(r"(?<![a-z])(plax|psl|parasternal[ _-]?long)(?![a-z])")),
    ("PSAX", re.compile(r"(?<![a-z])(psax|pss|parasternal[ _-]?short)(?![a-z])")),
    ("A4C", re.compile(r"(?<![a-z])(a4c|ap4|apical[ _-]?(4|four)|4ch|four[ _-]?chamber)(?![a-z])")),
]


def guess_view(filename) -> str:
    """Best guess at the view from a file name; empty string if there is none."""
    stem = os.path.splitext(clean_filename(filename))[0].lower()
    for view, rx in _VIEW_RULES:
        if rx.search(stem):
            return view
    return ""


def file_ext(filename) -> str:
    ext = Path(clean_filename(filename)).suffix.lower()
    if ext not in UPLOAD_EXTS:
        raise LibraryError(
            f"'{clean_filename(filename)}' is not a supported file type. "
            "Use MP4, M4V, MOV, AVI, WEBM, JPG, PNG or GIF.",
            415,
        )
    return ext


def sanitize_clip(raw: dict, *, ext: str, size: int, has_thumb: bool) -> dict:
    """Rebuild a clip record from untrusted JSON (used by import)."""
    results = raw.get("results")
    try:
        if not isinstance(results, dict) or len(json.dumps(results)) > MAX_RESULTS_BYTES:
            results = {}
    except (TypeError, ValueError):
        results = {}
    return {
        "id": raw["id"],
        "filename": clean_filename(raw.get("filename", "file")),
        "ext": ext,
        "kind": "still" if ext in STILL_EXTS else "clip",
        "view": normalize_view(raw.get("view", ""), strict=False),
        "tags": normalize_tags(raw.get("tags") if isinstance(raw.get("tags"), list) else []),
        "notes": clean_text(raw.get("notes", ""), MAX_NOTES),
        "size": int(size),
        "added": clean_text(raw.get("added") or now_iso(), 40),
        "has_thumb": bool(has_thumb),
        "results": results,
    }


def sanitize_archive_meta(raw: dict) -> dict:
    return {
        "label": clean_text(raw.get("label", ""), MAX_LABEL) or "Untitled archive",
        "notes": clean_text(raw.get("notes", ""), MAX_NOTES),
        "tags": normalize_tags(raw.get("tags") if isinstance(raw.get("tags"), list) else []),
        "created": clean_text(raw.get("created") or now_iso(), 40),
    }


def summarize(a: dict) -> dict:
    """The compact record the dashboard needs for one archive."""
    clips = a.get("clips", [])
    views = [v for v in VIEWS if any(c.get("view") == v for c in clips)]
    clip_tags = [t for c in clips for t in c.get("tags", [])]
    thumb = next((c["id"] for c in clips if c.get("has_thumb") or c.get("kind") == "still"), None)
    search = " ".join(
        [a["label"], a.get("notes", ""), *a.get("tags", []), *clip_tags]
        + [c.get("filename", "") for c in clips]
        + [c.get("notes", "") for c in clips]
    ).lower()
    return {
        "id": a["id"],
        "label": a["label"],
        "notes": a.get("notes", ""),
        "created": a["created"],
        "updated": a.get("updated", a["created"]),
        "tags": sorted(set(a.get("tags", [])).union(clip_tags)),
        "clip_count": sum(1 for c in clips if c.get("kind") == "clip"),
        "still_count": sum(1 for c in clips if c.get("kind") == "still"),
        "views": views,
        "unassigned": sum(1 for c in clips if not c.get("view")),
        "thumb_clip": thumb,
        "ai_status": "not_analyzed",
        "search": search,
    }


# --------------------------------------------------------------------------
# The library
# --------------------------------------------------------------------------

class Library:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser()
        self.archives_dir = self.root / "archives"
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # -- paths and raw records ------------------------------------------
    def archive_dir(self, aid: str) -> Path:
        check_id(aid, "archive")
        return self.archives_dir / aid

    def read_archive(self, aid: str) -> dict:
        path = self.archive_dir(aid) / "archive.json"
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            raise LibraryError("Archive not found.", 404)
        except (json.JSONDecodeError, OSError):
            raise LibraryError("This archive's file could not be read.", 500)

    def write_archive(self, aid: str, data: dict, touch: bool = True) -> None:
        folder = self.archive_dir(aid)
        folder.mkdir(parents=True, exist_ok=True)
        if touch:
            data["updated"] = now_iso()
        tmp = folder / "archive.json.tmp"
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, folder / "archive.json")

    @staticmethod
    def _find_clip(a: dict, cid: str) -> dict:
        check_id(cid, "clip")
        for clip in a.get("clips", []):
            if clip["id"] == cid:
                return clip
        raise LibraryError("Clip not found.", 404)

    # -- archives -------------------------------------------------------
    def list_archives(self) -> list[dict]:
        out = []
        for folder in sorted(self.archives_dir.iterdir()):
            if not folder.is_dir() or not ID_RE.match(folder.name):
                continue
            try:
                out.append(summarize(self.read_archive(folder.name)))
            except LibraryError:
                continue  # one unreadable archive must not break the dashboard
        out.sort(key=lambda s: s["created"], reverse=True)
        return out

    def create_archive(self, label: str = "", notes: str = "", tags: Iterable | None = None) -> dict:
        aid = new_id()
        data = {
            "schema": SCHEMA_VERSION,
            "id": aid,
            "label": clean_text(label, MAX_LABEL) or "Untitled archive",
            "notes": clean_text(notes, MAX_NOTES),
            "tags": normalize_tags(tags),
            "created": now_iso(),
            "clips": [],
        }
        with self._lock:
            self.write_archive(aid, data)
        return data

    def get_archive(self, aid: str) -> dict:
        return self.read_archive(aid)

    def update_archive(self, aid: str, label=None, notes=None, tags=None) -> dict:
        with self._lock:
            a = self.read_archive(aid)
            if label is not None:
                a["label"] = clean_text(label, MAX_LABEL) or a["label"]
            if notes is not None:
                a["notes"] = clean_text(notes, MAX_NOTES)
            if tags is not None:
                a["tags"] = normalize_tags(tags)
            self.write_archive(aid, a)
            return a

    def delete_archive(self, aid: str) -> None:
        folder = self.archive_dir(aid)
        if not folder.is_dir():
            raise LibraryError("Archive not found.", 404)
        with self._lock:
            shutil.rmtree(folder)

    def install_archive(self, aid: str, record: dict) -> None:
        """Write an archive record that import has already sanitized."""
        with self._lock:
            self.write_archive(aid, record, touch=False)

    # -- clips ----------------------------------------------------------
    def add_clip(self, aid: str, filename: str, stream: BinaryIO, view: str = "") -> dict:
        ext = file_ext(filename)
        chosen = normalize_view(view) or guess_view(filename)
        self.read_archive(aid)  # 404 early if the archive is gone
        cid = new_id()
        media_dir = self.archive_dir(aid) / "media"
        media_dir.mkdir(exist_ok=True)
        converting = ext in CONVERT_EXTS
        dest = media_dir / (f"{cid}.upload{ext}" if converting else f"{cid}{ext}")
        size, limit = 0, max_file_bytes()
        try:
            with dest.open("wb") as out:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise LibraryError("That file is larger than the allowed maximum.", 413)
                    out.write(chunk)
            if size == 0:
                raise LibraryError(f"'{clean_filename(filename)}' is empty.", 400)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        thumb = b""
        if converting:
            # The window cannot play AVI: keep an H.264 MP4 instead (see convert.py).
            ext = ".mp4"
            final = media_dir / f"{cid}{ext}"
            try:
                thumb = avi_to_mp4(dest, final)
                size = final.stat().st_size
            except ConversionError as err:
                raise LibraryError(f"'{clean_filename(filename)}': {err}", 415) from err
            finally:
                dest.unlink(missing_ok=True)
        clip = {
            "id": cid,
            "filename": clean_filename(filename),
            "ext": ext,
            "kind": "still" if ext in STILL_EXTS else "clip",
            "view": chosen,
            "tags": [],
            "notes": "",
            "size": size,
            "added": now_iso(),
            "has_thumb": bool(thumb),
            "results": {},
        }
        if thumb:
            thumbs = self.archive_dir(aid) / "thumbs"
            thumbs.mkdir(exist_ok=True)
            (thumbs / f"{cid}.jpg").write_bytes(thumb)
        with self._lock:
            a = self.read_archive(aid)
            a["clips"].append(clip)
            self.write_archive(aid, a)
        return clip

    def update_clip(self, aid: str, cid: str, view=None, tags=None, notes=None) -> dict:
        with self._lock:
            a = self.read_archive(aid)
            clip = self._find_clip(a, cid)
            if view is not None:
                clip["view"] = normalize_view(view)
            if tags is not None:
                clip["tags"] = normalize_tags(tags)
            if notes is not None:
                clip["notes"] = clean_text(notes, MAX_NOTES)
            self.write_archive(aid, a)
            return clip

    def delete_clip(self, aid: str, cid: str) -> None:
        with self._lock:
            a = self.read_archive(aid)
            clip = self._find_clip(a, cid)
            a["clips"] = [c for c in a["clips"] if c["id"] != cid]
            self.write_archive(aid, a)
            folder = self.archive_dir(aid)
            (folder / "media" / f"{cid}{clip['ext']}").unlink(missing_ok=True)
            (folder / "thumbs" / f"{cid}.jpg").unlink(missing_ok=True)

    def set_thumbnail(self, aid: str, cid: str, data: bytes) -> None:
        if len(data) > MAX_THUMB_BYTES:
            raise LibraryError("Thumbnail is too large.", 413)
        if not data.startswith(b"\xff\xd8\xff"):
            raise LibraryError("Thumbnail must be a JPEG image.", 415)
        with self._lock:
            a = self.read_archive(aid)
            clip = self._find_clip(a, cid)
            thumbs = self.archive_dir(aid) / "thumbs"
            thumbs.mkdir(exist_ok=True)
            (thumbs / f"{cid}.jpg").write_bytes(data)
            clip["has_thumb"] = True
            self.write_archive(aid, a)

    def media_path(self, aid: str, cid: str) -> tuple[Path, dict]:
        a = self.read_archive(aid)
        clip = self._find_clip(a, cid)
        path = self.archive_dir(aid) / "media" / f"{cid}{clip['ext']}"
        if not path.is_file():
            raise LibraryError("The file for this clip is missing from the library folder.", 404)
        return path, clip

    def thumb_path(self, aid: str, cid: str) -> Path | None:
        a = self.read_archive(aid)
        clip = self._find_clip(a, cid)
        thumb = self.archive_dir(aid) / "thumbs" / f"{cid}.jpg"
        if thumb.is_file():
            return thumb
        if clip["kind"] == "still":
            media = self.archive_dir(aid) / "media" / f"{cid}{clip['ext']}"
            return media if media.is_file() else None
        return None
