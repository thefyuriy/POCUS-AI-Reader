# POCUS AI Reader

A local-only Mac app for organizing de-identified cardiac point-of-care ultrasound (POCUS) clips
(PLAX, PSAX, A4C, SC4C, IVC long-axis). It works like a private, single-user SonoClipShare:
archives of clips, tags, notes, a frame-stepping viewer and a dashboard. Nothing leaves your Mac.

**For research and teaching only. Not for clinical decisions.**

## Status
- Stage 1 (this release): library, upload, viewer, export/import. AI panels are placeholders.
- Stage 2 (built): PanEcho results. A second RV assessment is still being chosen; see `docs/STAGES.md`.

## Install (Apple Silicon Mac)
1. On GitHub open the **Actions** tab, click the latest green "Build Mac app" run, and download
   **POCUS-AI-Reader-dmg** at the bottom. Unzip it.
2. Open the `.dmg` and drag **POCUS AI Reader** onto **Applications**.
3. The app is not signed by Apple. The first time, right-click it in Applications, choose **Open**, then **Open** again.

## Analyze (PanEcho)
Click **Analyze archive** (or **Analyze this clip**). The first time, the app asks before downloading the PanEcho model (505 MB, from its authors at Yale; non-commercial licence). Analysis then runs on your Mac, in the background with a progress bar, and your clips never leave it. Results are the model's output for teaching and research, not a diagnosis, and the model was not validated on point-of-care clips.

## Supported files
MP4, M4V, MOV, WEBM, AVI, and JPG/PNG/GIF stills. AVI files are converted to MP4 (H.264, near-lossless, no audio) when you add them, because the app window cannot play AVI; the original AVI is not kept, so keep your own copy.

## Where your data lives
`~/Library/Application Support/POCUS AI Reader` (deliberately outside iCloud-synced folders).
Use **Export library** / **Import library** (a ZIP) to move clips between Macs. Exports go to Downloads.

## Run from source (developers)
    pip install -r requirements.txt
    python -m pocus_ai_reader            # window; add --no-window to use your browser
    python -m unittest discover -s tests -t .

## License
AGPL-3.0. AI model weights are downloaded from their authors and carry their own terms; see `NOTICE.md`.
