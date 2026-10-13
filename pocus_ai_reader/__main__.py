# SPDX-License-Identifier: AGPL-3.0-only
"""Launch POCUS AI Reader: ``python -m pocus_ai_reader``.

Opens the app in its own window (macOS, through pywebview). Use ``--no-window``
to open it in your normal browser instead, which is handy during development.
"""
from __future__ import annotations

import argparse
import secrets
import webbrowser
from pathlib import Path

from .app import start_background_server, stop_server
from .library import Library, default_library_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pocus-ai-reader")
    parser.add_argument("--no-window", action="store_true", help="open in the default browser instead of an app window")
    parser.add_argument("--library", type=Path, help="library folder (default: the app's standard location)")
    parser.add_argument("--port", type=int, default=0, help="local port (default: pick a free one)")
    args = parser.parse_args(argv)

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
