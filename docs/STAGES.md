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

## Stage 3: EchoPrime (second opinion instead of RV-Net)
- MViT-v2-S encoder, 11-view classifier, kNN over ~230k reference studies; large model_data from GitHub Releases v1.0.0.
- Code selects only cuda/cpu: run on CPU (or patch for MPS). The view-classifier `torch.load` lacks `map_location`: patch to CPU.
- Phenotypes of interest: rv_systolic_function_depressed, right_ventricle_dilation, right_atrium_dilation, dilated_ivc, pericardial_effusion, ejection_fraction. Use `roc_thresholds.csv`.
- UI: PanEcho and EchoPrime side by side per clip.
- Concordance rule (user Q9) and view-mismatch warning (user Q10): when EchoPrime's predicted view differs from the manual tag, warn. Reconfirm exact wording with the user before building.

## Decisions from Q13 and Q14
- AI runs only when the user clicks **Analyze** (per clip or per archive), never automatically after upload.
- Analysis runs as a background job with a progress bar; the user can keep browsing while it runs.

## Deferred
RV-Net (echonet/RV: no LICENSE, A4C only, needs LV-on-right orientation); RVENet-Demo (GPL-3.0 code, weights terms unstated, CUDA/DICOM). User may email the authors.
