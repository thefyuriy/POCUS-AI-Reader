# CLAUDE.md: handoff for Claude Code

## Project
POCUS AI Reader: local-only Mac app (Apple Silicon) for de-identified cardiac POCUS clips; SonoClipShare-like but no accounts/sharing. Owner: Yuriy Bronshteyn, MD (Duke), a **non-technical** user.
AI: PanEcho (Stage 2, built) runs locally. EchoPrime was dropped (Q16: C, licence); a different second RV model is being chosen. See `docs/STAGES.md`, `docs/DESIGN.md`, `NOTICE.md`.

## First tasks
1. Verify `LICENSE` exists and is AGPL-3.0 (it was omitted from the ZIP; the repo's license choice should supply it). If missing, add the official text.
2. The ZIP the user uploaded may still be in the repo: unzip into the root, commit, delete the ZIP. Keep `.github/` intact.
3. Run `python -m unittest discover -s tests -t .` (36 tests passed originally; Starlette/unittest, not FastAPI/pytest).
4. Check the GitHub Actions run. The PyInstaller spec, DMG script and workflow were NEVER run (the build sandbox had no PyPI access): expect to fix hidden imports, data paths, codesign.
5. `static/app.js` passed `node --check` but was never exercised in a browser. Verify with Playwright; also verify pywebview drag-and-drop onto WKWebView (fallback: "Add files" button).

## User constraints
- No Terminal, no Node.js; click-by-click plain instructions; must work from either Mac (both Apple Silicon).
- Concise, precise; point out contradictions in requests directly.
- Number questions consecutively across batches; never reuse a Q number (last used: Q16).
- For fix/revise requests: open a PR, commit subject under 60 chars; after pushing rewrite PR title/body into a real summary (what changed, how tested, what is not covered).
- Commits end with `Co-Authored-By:` and `Claude-Session:` lines; PRs per the session reminder.
- Repo is public. Models are downloaded on first launch, never bundled. Default library outside iCloud-synced folders.

## Medical wording (owner rule)
NEVER use formal/informal or official/unofficial to describe any distinction in medicine, in UI text, docs or replies. Say "consultative echocardiogram" (vs point-of-care). `tests/test_wording.py` enforces this for user-facing files.

## Confirmed decisions
- Q13: AI runs only after the user clicks Analyze. Q14: background jobs with a progress bar.

- Q15 (asked, not yet answered): PanEcho weights download. Implemented as ask first: Analyze shows a dialog (505 MB, CC BY-NC-SA, research/teaching only) and downloads only after the user clicks. Confirm or change.
- Q16: C. EchoPrime is dropped. Its README says MIT but its LICENSE file (commit 03874a5) is a Cedars-Sinai academic-research-only licence with no transfer of the software or derivatives; nothing from it may enter this public repo. Next: choose another RV model, checking the LICENSE file (not the README) first; see docs/STAGES.md.

## Open verification items
PanEcho speed on Mac (CPU: about 3 s per clip on a 4-core Linux box; Mac untested); PanEcho outputs have not been checked against real point-of-care clips (only test patterns were run); resizing the whole frame to 224x224 is our choice, not the authors'; GitHub Actions macOS minutes (public repo, likely fine).
