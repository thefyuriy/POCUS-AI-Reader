# SPDX-License-Identifier: AGPL-3.0-only
# PyInstaller spec. Run from the repo root:  pyinstaller packaging/pocus_ai_reader.spec
# Verified to build and start on Linux; the macOS build runs in GitHub Actions.
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # SPECPATH is provided by PyInstaller

av_datas, av_binaries, av_hidden = collect_all("av")
# torchvision loads its compiled operators (torchvision::nms ...) from .so files that are not Python imports
tv_datas, tv_binaries, tv_hidden = collect_all("torchvision")
hidden = (collect_submodules("uvicorn") + collect_submodules("starlette")
          + ["multipart", "webview", "anyio._backends._asyncio"] + av_hidden + tv_hidden)

a = Analysis(
    [os.path.join(SPECPATH, "run_app.py")],
    pathex=[ROOT],
    binaries=av_binaries + tv_binaries,
    datas=[(os.path.join(ROOT, "pocus_ai_reader", "static"), "pocus_ai_reader/static"),
           (os.path.join(ROOT, "pocus_ai_reader", "panecho_tasks.json"), "pocus_ai_reader")] + av_datas + tv_datas,
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
