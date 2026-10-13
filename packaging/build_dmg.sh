#!/bin/bash
# Builds dist/POCUS-AI-Reader.dmg from dist/POCUS AI Reader.app (macOS only).
set -euo pipefail
cd "$(dirname "$0")/.."
APP="dist/POCUS AI Reader.app"
codesign --force --deep --sign - "$APP"   # ad-hoc signature (no Apple developer account)
rm -rf dmg_stage && mkdir dmg_stage
cp -R "$APP" dmg_stage/
ln -s /Applications dmg_stage/Applications
hdiutil create -volname "POCUS AI Reader" -srcfolder dmg_stage -ov -format UDZO dist/POCUS-AI-Reader.dmg
rm -rf dmg_stage
