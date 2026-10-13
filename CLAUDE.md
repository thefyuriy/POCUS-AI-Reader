# CLAUDE.md: handoff for Claude Code

## Project
POCUS AI Reader: local-only Mac app (Apple Silicon) for de-identified cardiac POCUS clips; SonoClipShare-like but no accounts/sharing. Owner: Yuriy Bronshteyn, MD (Duke), a **non-technical** user.
Planned AI: PanEcho (Stage 2) and EchoPrime (Stage 3) run locally, shown side by side. See `docs/STAGES.md`, `docs/DESIGN.md`, `NOTICE.md`.

## First tasks
1. Verify `LICENSE` exists and is AGPL-3.0 (it was omitted from the ZIP; the repo's license choice should supply it). If missing, add the official text.
2. The ZIP the user uploaded may still be in the repo: unzip into the root, commit, delete the ZIP. Keep `.github/` intact.
3. Run `python -m unittest discover -s tests -t .` (36 tests passed originally; Starlette/unittest, not FastAPI/pytest).
4. Check the GitHub Actions run. The PyInstaller spec, DMG script and workflow were NEVER run (the build sandbox had no PyPI access): expect to fix hidden imports, data paths, codesign.
5. `static/app.js` passed `node --check` but was never exercised in a browser. Verify with Playwright; also verify pywebview drag-and-drop onto WKWebView (fallback: "Add files" button).

## User constraints
- No Terminal, no Node.js; click-by-click plain instructions; must work from either Mac (both Apple Silicon).
- Concise, precise; point out contradictions in requests directly.
- Number questions consecutively across batches; never reuse a Q number (last used: Q14).
- For fix/revise requests: open a PR, commit subject under 60 chars; after pushing rewrite PR title/body into a real summary (what changed, how tested, what is not covered).
- Commits end with `Co-Authored-By:` and `Claude-Session:` lines; PRs per the session reminder.
- Repo is public. Models are downloaded on first launch, never bundled. Default library outside iCloud-synced folders.

## Confirmed decisions
- Q13: AI runs only after the user clicks Analyze. Q14: background jobs with a progress bar.

## Open verification items
PanEcho speed on Mac; EchoPrime CPU run + `map_location` patch; GitHub Actions macOS minutes (public repo, likely fine).
