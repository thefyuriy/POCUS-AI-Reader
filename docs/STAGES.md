# Stages

## Stage 1 (built)
Library, upload (drag-drop + button), viewer, tags/notes, export/import, security guard, packaging + CI.

## Stage 2: PanEcho (built; verify on a Mac and on real clips)
- Not loaded via torch.hub: the app downloads PanEcho's `src/models.py` (pinned commit) and `panecho.pt` (release v1.0) on first Analyze, checks SHA-256 of both, and builds the model itself (`pocus_ai_reader/panecho.py`). Reason: pinned, no unpinned pickle download, nothing copied into this repo (the PanEcho repo has no LICENSE file). 16 frames, 224x224, ImageNet-normalized; 40 task heads (the authors say 39).
- Preprocessing follows the authors' `EchoDataset`: consecutive 16-frame windows (4 evenly spaced), BGR channel order as OpenCV gives, predictions averaged. Whole-frame resize to 224x224 is our choice and is unvalidated.
- Study level = mean over the archive's clips (the authors aggregate over a study's views).
- No view classifier: use the user's manual view tag.
- Weights are CC BY-NC-SA 4.0: show attribution + non-commercial note. Download on first use, not bundled.
- Verify speed on Apple Silicon (upstream docs mention only Linux/NVIDIA). Run in a background job with progress.
- Store results in each clip's `results.panecho`; set `ai_status` in the summary.

## Stage 3: second RV assessment - EchoPrime DROPPED (Q16: C); replacement not yet chosen
- **Why EchoPrime was dropped (2026-10-13):** its README says MIT, but the LICENSE file in echonet/EchoPrime (commit 03874a5) is the Cedars-Sinai Academic Software License: only academic or nonprofit researchers, only for educational or academic research, no transfer of the software or any derivative to anyone, a required acknowledgment in any publication, commercial use via CSTechTransfer@cshs.org. That conflicts with a public AGPL repository. Also about 3.9 GB of downloads and a view classifier that fails without CUDA.
- **Rule for the replacement:** read the LICENSE file in the repository and on the weights' download page before building; the README alone is not enough. Nothing from it may be copied into this repo unless the licence allows it; fetch from the authors at the user's request with hash checks, as PanEcho does.
- **Candidates checked (2026-10-13, LICENSE files read, not READMEs):**
  - **echonet/RV (EchoNet-RV)**: best fit. A4C B-mode (standard or RV-focused view, no colour Doppler) gives frame-by-frame RV segmentation plus RV fractional area change (RVFAC). Published 2026 (Tokodi, He, Ouyang et al.). Reported RVFAC MAE 4.9-6.4 percentage points, AUC for RVFAC <35% 0.68-0.86 across three external sets. Needs Stanford orientation (LV on the right of the image; a flip option exists). Weights auto-download from its GitHub release v1. **No LICENSE file and no licence text anywhere in the repo** (commit ae414e3, 2026-08-17), so no permission to use or redistribute is granted. Needs the authors' written permission first. Its segmentation overlay would let the user check the model by eye.
  - **bowang-lab/EchoJEPA**: Apache-2.0 / MIT code, but it is a foundation model with probes for LVEF, RVSP and view classification only (no RV function). Checkpoints are 1.5-4.8 GB on Google Drive. Does not add an RV function opinion.
  - **echonet/echo_CLIP (EchoCLIP)**: same Cedars-Sinai academic-only licence as EchoPrime. Excluded.
  - **RVENet (Tokodi/Magyar)**: the public dataset and benchmark models for RVEF from A4C; not checked further (RVENet-Demo: GPL-3.0 code, weights terms unstated, CUDA/DICOM).
- Phenotypes of interest, for choosing a model: RV systolic function, RV size, RA size, IVC size, pericardial effusion, EF.

## Decisions from Q13 and Q14
- AI runs only when the user clicks **Analyze** (per clip or per archive), never automatically after upload.
- Analysis runs as a background job with a progress bar; the user can keep browsing while it runs.

## Deferred
RV-Net (echonet/RV: no LICENSE, A4C only, needs LV-on-right orientation); RVENet-Demo (GPL-3.0 code, weights terms unstated, CUDA/DICOM). User may email the authors.
