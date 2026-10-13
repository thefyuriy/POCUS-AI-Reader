#!/bin/bash
# Builds dist/POCUS-AI-Reader.dmg from dist/POCUS AI Reader.app (macOS only).
set -euo pipefail
cd "$(dirname "$0")/.."
APP="dist/POCUS AI Reader.app"
codesign --force --deep --sign - "$APP"   # ad-hoc signature (no Apple developer account)
rm -rf dmg_stage && mkdir dmg_stage
cp -R "$APP" dmg_stage/
ln -s /Applications dmg_stage/Applications
# hdiutil sometimes fails with "Resource busy" on CI runners; a short wait and another try fixes it.
for attempt in 1 2 3 4; do
  if hdiutil create -volname "POCUS AI Reader" -srcfolder dmg_stage -ov -format UDZO dist/POCUS-AI-Reader.dmg; then
    break
  fi
  if [ "$attempt" = 4 ]; then echo "hdiutil failed 4 times" >&2; exit 1; fi
  echo "hdiutil failed (attempt $attempt), retrying in $((attempt * 10)) s" >&2
  sleep $((attempt * 10))
done
rm -rf dmg_stage
