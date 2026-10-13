# SPDX-License-Identifier: AGPL-3.0-only
"""PanEcho (Yale CarDS) running locally on this Mac. Stage 2.

Nothing from PanEcho is stored in this repository except a list of task names. On first use the app
downloads PanEcho's model code and weights from the authors, from a pinned commit/release, and checks
both against SHA-256 hashes before using them. The weights are CC BY-NC-SA 4.0 (non-commercial); see NOTICE.md.

Preprocessing follows the authors' data loader (src/dataset.py, EchoDataset): consecutive 16-frame clips,
frames kept in OpenCV's BGR channel order, scaled to 0-1 and normalised with the ImageNet mean/std, and the
predictions of several clips from one video averaged. Resizing a whole frame to 224 x 224 is our choice
(the authors' videos were already stored at that size); it has not been validated on point-of-care clips.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import ssl
import sys
import threading
import types
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .jobs import Job
from .library import LibraryError

COMMIT = "836c46ef6bc4eace50b7dc36704ecef2d33affde"
CODE_URL = f"https://raw.githubusercontent.com/CarDS-Yale/PanEcho/{COMMIT}/src/models.py"
CODE_SHA256 = "b211b3c81c76fcb6ed39cf258970e859c2bda74f2be6870f46556a4cd7873d43"
WEIGHTS_URL = "https://github.com/CarDS-Yale/PanEcho/releases/download/v1.0/panecho.pt"
WEIGHTS_SHA256 = "896a279d2e5d669dd762adb2077e948ec38087006d2b4001657b87a132e2da35"
WEIGHTS_MB = 505

CLIP_LEN = 16
NUM_WINDOWS = 4
SIZE = 224
MAX_FRAMES = 1500
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)

TASKS_FILE = Path(__file__).with_name("panecho_tasks.json")


def load_tasks() -> list[dict]:
    return json.loads(TASKS_FILE.read_text(encoding="utf-8"))["tasks"]


# --------------------------------------------------------------------------
# Pure helpers (no torch), unit-tested
# --------------------------------------------------------------------------

def select_windows(n_frames: int, clip_len: int = CLIP_LEN, count: int = NUM_WINDOWS) -> list[tuple[int, int]]:
    """Start/stop frame indices of evenly spaced consecutive windows. Short videos are padded by the caller."""
    if n_frames <= clip_len:
        return [(0, n_frames)]
    last_start = n_frames - clip_len
    starts = sorted({round(i * last_start / (count - 1)) for i in range(count)}) if count > 1 else [last_start // 2]
    return [(s, s + clip_len) for s in starts]


def format_outputs(per_window: list[dict[str, list[float]]], tasks: list[dict]) -> dict:
    """Average the raw outputs of each window and name them. ``per_window`` maps task -> list of numbers."""
    out: dict[str, dict] = {}
    for t in tasks:
        rows = [w[t["name"]] for w in per_window if t["name"] in w]
        if not rows:
            continue
        mean = [sum(col) / len(col) for col in zip(*rows)]
        if not all(math.isfinite(v) for v in mean):
            continue
        if t["type"] == "regression":
            value = mean[0]
            if t["name"] == "GLS":
                value = -value  # the authors: the actual value is -1 times the model output
            out[t["name"]] = {"value": value}
        elif t["type"] == "binary_classification":
            out[t["name"]] = {"p": mean[0]}  # probability of the abnormal ("positive") class
        else:
            out[t["name"]] = {"probs": dict(zip(t["classes"], mean))}
    return out


def aggregate(results: list[dict], tasks: list[dict]) -> dict | None:
    """Study-level result: the average over clips, as the PanEcho authors do across the views of a study."""
    results = [r for r in results if isinstance(r, dict) and isinstance(r.get("tasks"), dict)]
    if not results:
        return None
    out: dict[str, dict] = {}
    for t in tasks:
        parts = [r["tasks"][t["name"]] for r in results if isinstance(r["tasks"].get(t["name"]), dict)]
        try:
            if t["type"] == "regression":
                vals = [p["value"] for p in parts]
                if vals:
                    out[t["name"]] = {"value": sum(vals) / len(vals)}
            elif t["type"] == "binary_classification":
                vals = [p["p"] for p in parts]
                if vals:
                    out[t["name"]] = {"p": sum(vals) / len(vals)}
            else:
                dicts = [p["probs"] for p in parts]
                if dicts:
                    out[t["name"]] = {"probs": {c: sum(d[c] for d in dicts) / len(dicts) for c in t["classes"]}}
        except (KeyError, TypeError):
            continue  # a result from an import that does not match: leave this task out
    return {"model": "PanEcho", "clips": len(results), "tasks": out}


def load_frames(path: Path, size: int = SIZE, max_frames: int = MAX_FRAMES):
    """Decode a clip to a uint8 array (frames, size, size, 3) in BGR order, area-resized."""
    import av
    import numpy as np

    frames = []
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise LibraryError("This file has no video.", 415)
            for frame in container.decode(container.streams.video[0]):
                frames.append(frame.to_ndarray(format="bgr24", width=size, height=size, interpolation="AREA"))
                if len(frames) >= max_frames:
                    break
    except LibraryError:
        raise
    except Exception as err:
        raise LibraryError("This clip could not be read for analysis.", 415) from err
    if not frames:
        raise LibraryError("This clip has no readable frames.", 415)
    return np.stack(frames)


def windows_from_frames(frames, clip_len: int = CLIP_LEN, count: int = NUM_WINDOWS):
    """(windows, clip_len, H, W, 3). A video shorter than clip_len carries its last frame forward, as the authors do."""
    import numpy as np

    out = []
    for start, stop in select_windows(len(frames), clip_len, count):
        w = frames[start:stop]
        if len(w) < clip_len:
            w = np.concatenate([w, np.repeat(w[-1:], clip_len - len(w), axis=0)])
        out.append(w)
    return np.stack(out)


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------

def _download(url: str, dest: Path, sha256: str, job: Job, start: float, span: float, label: str) -> None:
    import certifi  # the frozen app has no system certificate path

    # SSL_CERT_FILE lets a network that inspects TLS (some hospitals and universities) supply its own certificates.
    context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    request = urllib.request.Request(url, headers={"User-Agent": "POCUS-AI-Reader"})
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, context=context, timeout=60) as response, part.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                job.update(start + span * (done / total if total else 0),
                           f"{label}: {done / 1e6:.0f} of {total / 1e6:.0f} MB" if total else label)
        if digest.hexdigest() != sha256:
            raise LibraryError("The downloaded file did not match its expected checksum, so it was discarded. "
                               "Try again, and tell the developer if it keeps happening.", 502)
        part.replace(dest)
    except OSError as err:
        raise LibraryError(f"The download failed. Check your internet connection and try again. ({err})", 502) from err
    finally:
        part.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------

class PanEchoEngine:
    def __init__(self, models_dir: Path):
        self.dir = Path(models_dir) / "panecho"
        self.code_path = self.dir / f"models-{COMMIT[:8]}.py"
        self.weights_path = self.dir / f"panecho-v1.0-{WEIGHTS_SHA256[:8]}.pt"
        self._lock = threading.Lock()
        self._model = None

    # -- status ---------------------------------------------------------
    def status(self) -> dict:
        return {
            "installed": self.code_path.is_file() and self.weights_path.is_file(),
            "download_mb": WEIGHTS_MB,
            "commit": COMMIT,
            "license": "CC BY-NC-SA 4.0 (non-commercial)",
            "tasks": load_tasks(),
        }

    def is_installed(self) -> bool:
        return self.code_path.is_file() and self.weights_path.is_file()

    # -- download -------------------------------------------------------
    def download(self, job: Job) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.code_path.is_file():
            _download(CODE_URL, self.code_path, CODE_SHA256, job, 0.0, 0.02, "Downloading PanEcho code")
        if not self.weights_path.is_file():
            _download(WEIGHTS_URL, self.weights_path, WEIGHTS_SHA256, job, 0.02, 0.98, "Downloading PanEcho model")
        job.update(1.0, "PanEcho is installed.")

    # -- model ----------------------------------------------------------
    def _load(self):
        if self._model is not None:
            return self._model
        if not self.is_installed():
            raise LibraryError("PanEcho is not downloaded yet.", 409)
        try:
            import numpy as np
            import torch
            import torchvision
        except ImportError as err:
            raise LibraryError("The AI components are missing from this copy of the app.", 500) from err

        # models.py imports timm at the top, but only uses it for architectures PanEcho does not use.
        sys.modules.setdefault("timm", types.ModuleType("timm"))
        spec = importlib.util.spec_from_file_location("panecho_models", self.code_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        torch.set_num_threads(max(1, min(4, torch.get_num_threads())))

        class Task:  # the attributes MultiTaskModel reads
            def __init__(self, t):
                self.task_name, self.task_type = t["name"], t["type"]
                self.class_names = np.array(t["classes"])
                self.mean = 0.0  # only initialises a bias that the weights then overwrite

        # The weights file contains every parameter, so do not download ImageNet weights first.
        real = torchvision.models.convnext_tiny
        torchvision.models.convnext_tiny = lambda *a, **k: real(weights=None)
        try:
            encoder = module.FrameTransformer("convnext_tiny", 8, 4, 0.0, "mean", CLIP_LEN)
        finally:
            torchvision.models.convnext_tiny = real
        model = module.MultiTaskModel(encoder, encoder.encoder.n_features, [Task(t) for t in load_tasks()], 0.25, True)

        weights = torch.load(self.weights_path, map_location="cpu", weights_only=True)["weights"]
        weights.pop("encoder.time_encoder.pe", None)  # fixed positional encoding is rebuilt, as in the authors' hubconf
        result = model.load_state_dict(weights, strict=False)
        problems = [k for k in list(result.missing_keys) + list(result.unexpected_keys) if k != "encoder.time_encoder.pe"]
        if problems:
            raise LibraryError(f"The PanEcho model did not match its weights ({problems[0]}).", 500)
        model.eval()
        self._model = model
        return model

    def analyze_clip(self, path: Path) -> dict:
        import torch

        tasks = load_tasks()
        frames = load_frames(path)
        windows = windows_from_frames(frames)
        with self._lock:
            model = self._load()
            x = torch.from_numpy(windows).permute(0, 4, 1, 2, 3).float() / 255.0  # W x 3 x T x H x W
            mean = torch.tensor(MEAN).view(1, 3, 1, 1, 1)
            std = torch.tensor(STD).view(1, 3, 1, 1, 1)
            x = (x - mean) / std
            with torch.inference_mode():
                raw = model(x)
        per_window = [{name: out[i].flatten().tolist() for name, out in raw.items()} for i in range(len(windows))]
        return {
            "model": "PanEcho",
            "commit": COMMIT,
            "analyzed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "frames": int(len(frames)),
            "windows": int(len(windows)),
            "tasks": format_outputs(per_window, tasks),
        }
