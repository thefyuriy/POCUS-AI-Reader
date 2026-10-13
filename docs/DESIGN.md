# Design notes
- Local-first: Starlette on 127.0.0.1 (free port) serves a vanilla JS/CSS UI; pywebview shows it in a native window (`--no-window` opens the browser).
- Storage: `<root>/archives/<id>/{archive.json,media/,thumbs/}`; ids are 32-hex and validated; atomic JSON writes.
- Security: Host check, per-launch session cookie via `/?t=TOKEN`, Origin check on writes, CSP (no inline script/style), zip import never uses extractall.
- UI: dark default, light via prefers-color-scheme; contact-sheet grid; permanent research-only banner; de-identification confirmation required client- and server-side.
- Views: PLAX, PSAX, A4C, SC4C, IVC, Other; manual tags, free-text label, no patient fields.
