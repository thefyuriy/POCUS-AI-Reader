# Third-party notices
- **PanEcho** (Yale CarDS): code AGPL-3.0; weights CC BY-NC-SA 4.0 (non-commercial). Downloaded by the app from the authors, not redistributed here.
- **EchoPrime**: MIT code. Model data downloaded from the authors' GitHub release, not redistributed here.
- **PanEcho download and checks**: the app fetches `src/models.py` (commit 836c46e) and `panecho.pt` (release v1.0) from github.com/CarDS-Yale/PanEcho at the user's request and verifies their SHA-256 hashes. Neither is stored in this repository; `pocus_ai_reader/panecho_tasks.json` lists only task names, classes and descriptions (from the authors' `content/tasks.md`). The PanEcho repository has no LICENSE file; the weights are described elsewhere as CC BY-NC-SA 4.0, so the app shows a non-commercial notice before downloading.
- **PyTorch, torchvision, NumPy, certifi**: used to run PanEcho; BSD-3-Clause / BSD / MPL-2.0 respectively (see their own licenses).
- **Starlette, uvicorn, python-multipart, pywebview, PyInstaller**: see their own licenses.
- RV-Net (echonet/RV) and RVENet-Demo are NOT used (no clear reuse license / unclear weight terms).
- **PyAV / FFmpeg (libx264)**: used to convert AVI files to MP4. PyAV is BSD-licensed; its bundled FFmpeg build includes libx264 (GPL-2.0-or-later), which is compatible with this app's AGPL-3.0 license. Source: https://github.com/PyAV-Org/PyAV and https://www.videolan.org/developers/x264.html
