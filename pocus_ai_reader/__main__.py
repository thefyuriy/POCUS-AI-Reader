# SPDX-License-Identifier: AGPL-3.0-only
"""Launch POCUS AI Reader: ``python -m pocus_ai_reader``.

Opens the app in its own window (macOS, through pywebview). Use ``--no-window``
to open it in your normal browser instead, which is handy during development.
"""
from __future__ import annotations

import argparse
import os
import secrets
import webbrowser
from pathlib import Path

from .app import start_background_server, stop_server
from .library import Library, default_library_dir


def selftest() -> int:
    """Exercise what the packaged app needs without opening a window: video conversion, torch, TLS certificates."""
    import tempfile

    import av
    import certifi
    import numpy as np
    import torch
    import torchvision

    from . import panecho
    from .convert import avi_to_mp4

    with tempfile.TemporaryDirectory() as tmp:
        src, dest = Path(tmp) / "t.avi", Path(tmp) / "t.mp4"
        with av.open(str(src), "w", format="avi") as out:
            stream = out.add_stream("mpeg4", rate=10)
            stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
            for i in range(10):
                frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 10, dtype=np.uint8), format="rgb24").reformat(format="yuv420p")
                frame.pts = i
                for packet in stream.encode(frame):
                    out.mux(packet)
            for packet in stream.encode(None):
                out.mux(packet)
        avi_to_mp4(src, dest)                      # needs the bundled H.264 encoder
        frames = panecho.load_frames(dest)         # needs decoding and resizing
        windows = panecho.windows_from_frames(frames)
        models = os.environ.get("POCUS_SELFTEST_MODELS")   # optional: run the real model if it is already downloaded here
        if models and panecho.PanEchoEngine(Path(models)).is_installed():
            result = panecho.PanEchoEngine(Path(models)).analyze_clip(dest)
            assert len(result["tasks"]) == 40, "PanEcho returned the wrong number of tasks"
            print("PanEcho ran on the test clip")
    conv = torch.nn.Conv3d(3, 4, 3)                # needs torch's native libraries
    y = conv(torch.from_numpy(windows).permute(0, 4, 1, 2, 3).float() / 255)
    assert y.shape[0] == len(windows) and torchvision.models.convnext_tiny(weights=None) is not None
    assert Path(certifi.where()).is_file()
    assert len(panecho.load_tasks()) == 40
    print("SELFTEST OK", f"torch {torch.__version__}", f"av {av.__version__}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pocus-ai-reader")
    parser.add_argument("--no-window", action="store_true", help="open in the default browser instead of an app window")
    parser.add_argument("--library", type=Path, help="library folder (default: the app's standard location)")
    parser.add_argument("--port", type=int, default=0, help="local port (default: pick a free one)")
    parser.add_argument("--selftest", action="store_true", help="check that the bundled libraries work, then exit (used by the build)")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()

    library = Library(args.library or default_library_dir())
    token = secrets.token_urlsafe(32)
    server, thread, port = start_background_server(library, token, port=args.port)
    url = f"http://127.0.0.1:{port}/?t={token}"

    try:
        import webview  # type: ignore
    except ImportError:
        webview = None

    try:
        if args.no_window or webview is None:
            print(f"POCUS AI Reader is running. Library: {library.root}\nOpen: {url}\nPress Ctrl+C to stop.")
            webbrowser.open(url)
            while thread.is_alive():
                thread.join(0.5)
        else:
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.create_window("POCUS AI Reader", url, width=1360, height=880, min_size=(960, 620))
            webview.start()
    except KeyboardInterrupt:
        pass
    finally:
        stop_server(server, thread)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
