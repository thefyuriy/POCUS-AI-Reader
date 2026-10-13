# SPDX-License-Identifier: AGPL-3.0-only
# PyInstaller spec. Run from the repo root:  pyinstaller packaging/pocus_ai_reader.spec
# UNTESTED until the first GitHub Actions run; see CLAUDE.md.
from PyInstaller.utils.hooks import collect_submodules

hidden = (collect_submodules("uvicorn") + collect_submodules("starlette")
          + ["multipart", "webview", "anyio._backends._asyncio"])

a = Analysis(
    ["run_app.py"],
    pathex=[".."],
    datas=[("../pocus_ai_reader/static", "pocus_ai_reader/static")],
    hiddenimports=hidden,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="POCUS AI Reader", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="POCUS AI Reader")
app = BUNDLE(
    coll,
    name="POCUS AI Reader.app",
    bundle_identifier="org.pocusaireader.app",
    info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "0.1.0"},
)
