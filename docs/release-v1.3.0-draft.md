# ThrowVision v1.3.0

Release date: April 16, 2026

## Highlights

ThrowVision v1.3.0 introduces a full accuracy-review workflow, OpenVINO-based model support, and a stronger calibration pipeline for both practice and competitive play. This release also improves post-session analysis with separate accuracy statistics, more reliable miss handling, and support for undetected manual events such as bounce-outs and falls.

## What's New

### Accuracy Review

- Added a full review workflow for Practice mode with per-dart confirm, edit, false-positive, and missed-dart correction actions.
- Added manual `+ Add Event` support for undetected throws such as bounce-outs, falls, and no-detection cases.
- Added X01 turn-review support so reviewed truth data can be captured outside Practice mode.
- Added persistent accuracy session storage and review APIs through `accuracy_stats.py`.

### Accuracy Statistics

- Split game statistics and accuracy statistics into separate dashboard sections.
- Added accuracy summary cards, camera-agreement breakdowns, and session history tables.
- Added reset flows for both game and accuracy statistics.
- Added delete actions for game history entries and reviewed-session data management.

### Detection and Inference

- Added OpenVINO runtime support for YOLO-based tip pose and calibration models.
- Added bundled OpenVINO model assets for tip detection and calibration.
- Added optional YOLO-based dart-tip verification support.
- Improved miss classification so near-outside throws are more likely to register as `MISS` instead of disappearing as no-detection events.

### Calibration and Hardware

- Added ML-assisted calibration via `ml_calibration.py`.
- Added anchor-based refinement helpers via `anchor_refine.py`.
- Expanded calibration workflow support around the newer inference and refinement pipeline.
- Added optional TF-Luna foul-line distance sensor support for oche monitoring.

### Frontend and Game Flow

- Restored and stabilized the frontend after the earlier redesign regression work.
- Improved practice review UX with focused single-dart review, clearer turn completion state, and manual event entry.
- Added review-state syncing between frontend and backend for practice and X01 review flows.
- Updated project documentation and versioning for the v1.3.0 release.

## Included In This Release

- New modules:
  - `accuracy_stats.py`
  - `anchor_refine.py`
  - `ml_calibration.py`
  - `openvino_inference.py`
  - `tfluna.py`
  - `yolo_verifier.py`
- New documentation:
  - `docs/accuracy-evaluation-plan.md`
- New model assets:
  - `models/calibration/y11-d-m-1280-rect-10-2-2026_openvino_model`
  - `models/tip/y11-p-1280-720-26032026_openvino_model`

## Validation

- Python compile checks passed for the updated backend and support modules.
- `node --check frontend/app.js` passed.

## Suggested GitHub Release Title

`ThrowVision v1.3.0`

## Suggested Tag

`v1.3.0`
