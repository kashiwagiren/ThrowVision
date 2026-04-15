# Accuracy Evaluation Plan

## Goal

Add a real accuracy-review workflow for practice mode and a matching accuracy dashboard so ThrowVision can answer:

- How often the system detects the dart at all
- How often it scores the correct segment without manual help
- Which cameras agreed or disagreed
- Which model / detection profile is doing better
- What kind of failures are most common

The two reference directions are:

- A stats viewer like image 1: model-level summary cards, agreement buckets, timing, and a table
- A practice correction flow like image 2: fast manual entry for missed darts and corrected labels

## What Exists Today

### Frontend

- Practice mode already has a dedicated right-side panel in [frontend/index.html](../frontend/index.html) and [frontend/style.css](../frontend/style.css).
- Practice mode already tracks live throw count, total, average, and history in [frontend/app.js](../frontend/app.js).
- Practice mode already shows per-camera breakdown via `cam_details`, but that information is debug-only and not persisted.
- Practice mode currently hides projected board dots on the practice board on purpose in `placeDot()` because the warped camera feed is the main visual.
- There is already a hidden debug camera panel in practice mode that is a good base for an "accuracy review" area.

### Backend

- `server.py` already emits per-dart `cam_details` with:
  - per-camera label
  - score
  - x/y board coordinates
  - radius
  - contour area
  - method used
  - whether that camera was used in the final decision
- `server.py` already logs lightweight latency info:
  - `motion_to_emit`
  - `detect_to_emit`
  - `collect_to_emit`
  - `pose_max`
  - OpenVINO execution device
  - detection speed profile
- `server.py` already has `/api/debug/screenshot`, which saves:
  - raw frame
  - warped frame
  - user-marked tip
  - detected tip
  - last scored payload

### Gap

- No practice session is persisted as ground-truth review data.
- No way to add a dart the system missed.
- No way to mark a predicted dart as wrong, corrected, or false positive.
- No accuracy metrics are computed from reviewed practice data.
- Existing `/api/stats` is game-result oriented only and backed by `data/stats.json`.

## Product Direction

### High-Level UX

Practice mode becomes the place where ground truth is created.

The flow should be:

1. User starts practice.
2. System records each detected dart prediction with metadata.
3. After each throw, or at minimum after the 3-dart turn, the user can review detected darts.
4. If the system missed a dart, the user adds it manually through a fast modal.
5. If the system scored the wrong segment, the user edits the final result instead of just mentally noting it.
6. Reviewed throws are saved into an accuracy session.
7. Stats page gets a new accuracy dashboard that aggregates those reviewed sessions.

### Why Practice Mode First

- Practice mode already has a simple 3-dart cycle.
- It is the safest place to gather truth data without affecting game flow.
- It gives a clean pipeline before extending the same review model into game modes later.

## Proposed UI

### 1. Stats Page: Add Accuracy View

Keep the current stats page for game stats, but add an `Accuracy` mode tab or sub-tab.

Recommended layout:

```text
[Filters: Date Range] [Model] [Profile] [Session Type=Practice]

[Active Model] [Actual Darts] [Predicted Darts] [Corrections] [Score Accuracy] [Recall] [Precision]
[Avg Detect->Emit] [Avg Pose Infer] [Avg Review Rate]

[3-Cam Agreement]
[2-Cam Agreement]
[1-Cam / No Detect]

[Runs / Sessions Table]
model | actual | predicted | corrected | missed | false+ | accuracy | detect->emit | profile | date

[Failure Breakdown]
wrong number | wrong ring | missed detection | false positive | bounce/foul confusion
```

Design notes:

- Reuse the current dark card language from the existing stats page.
- Use large monospace numerics for top-line metrics.
- Make the table horizontally scrollable, like the reference image.
- Keep filters sticky at the top of the stats content area.
- Surface one top-line `System Accuracy`, but also show `Recall` and `Precision` so misses and false positives are not hidden.

### 2. Practice Mode: Turn Review Block

Add a new review block below `BEST THROWS` and above the debug panel.

Recommended structure:

```text
TURN REVIEW
[Detected Dart 1] [Confirmed / Edit]
[Detected Dart 2] [Confirmed / Edit]
[Detected Dart 3] [Confirmed / Edit]

[+ Add Missed Dart]
[Mark False Positive]
[Save Review]
```

Behavior:

- Each detected dart becomes a review row with:
  - predicted label
  - score
  - agreement badge like `3-cam`, `2-cam`, `1-cam`
  - quick action: `Confirm`
  - quick action: `Edit`
  - quick action: `Delete` or `False Positive`
- Rows should stay lightweight so review is fast after every turn.
- When no review changes are made, the turn can still be marked as reviewed with one click.

### 3. Practice Mode: Add Missed Dart Modal

Build this from the second reference image.

Recommended modal behavior:

- Title: `Add Missed Dart`
- Subtitle: `Dart 1 (Not Detected)` or `Add Ground Truth Dart`
- Controls:
  - Multiplier: `Bouncer`, `Miss`, `Single`, `Double`, `Triple`
  - Single ring subtype when `Single` is selected:
    - `Inner Single`
    - `Outer Single`
  - Number grid:
    - `1` to `20`
    - `25`
  - Computed final score at the bottom
- Footer actions:
  - `Cancel`
  - `Add Dart`

Recommended additions beyond the mockup:

- Optional `Link to active camera` selector only when the user also wants to save a screenshot.
- Optional `Mark approximate board position later` button for future coordinate-level review.

### 4. Optional Phase-2 Review Enhancer

When the user edits a detected dart, allow `Correct Tip Position`:

- Opens or focuses the existing debug camera panel
- Lets the user click the actual tip on the warped frame
- Saves the screenshot through `/api/debug/screenshot`
- Links that artifact to the reviewed throw

This is not needed for the first release, but the existing debug route is already a strong base for it.

## Data Model

Do not mix accuracy-review data into `data/stats.json`.

Use a separate accuracy store, preferably one file per session:

- `data/accuracy_sessions/<session_id>.json`
- optional index file: `data/accuracy_index.json`

Recommended session schema:

```json
{
  "session_id": "acc_20260415_203012",
  "mode": "practice",
  "started_at": 1776256212.14,
  "ended_at": 1776256604.52,
  "model_name": "y11-p-1280-720-26032026_openvino_model",
  "execution_device": "GPU",
  "detection_profile": "default",
  "board_profile": "default",
  "turns": [
    {
      "turn_id": "turn_001",
      "index": 1,
      "predictions": [
        {
          "prediction_id": "pred_001",
          "ts": 1776256220.21,
          "label": "T20",
          "score": 60,
          "x_mm": 4.2,
          "y_mm": 102.4,
          "cam_details": [],
          "agreement_bucket": "3cam_all_match",
          "timings": {
            "detect_to_emit_ms": 283.0,
            "pose_infer_ms": 261.0
          }
        }
      ],
      "reviewed_actual": [
        {
          "actual_id": "actual_001",
          "source": "predicted",
          "prediction_id": "pred_001",
          "final_label": "T20",
          "final_score": 60,
          "final_x_mm": 4.2,
          "final_y_mm": 102.4,
          "verdict": "confirmed"
        },
        {
          "actual_id": "actual_002",
          "source": "manual_add",
          "prediction_id": null,
          "final_label": "S20",
          "final_score": 20,
          "final_x_mm": null,
          "final_y_mm": null,
          "verdict": "missed_detection"
        }
      ],
      "false_positives": [],
      "review_status": "reviewed"
    }
  ]
}
```

Important modeling rule:

- `predictions` represent what the system thought happened.
- `reviewed_actual` represents what truly happened after user review.
- Accuracy metrics must be computed by comparing those two sets, not by overwriting predictions in place.

## Metric Definitions

Use explicit formulas so the dashboard is trustworthy.

### Core counts

- `actual_darts`: count of `reviewed_actual`
- `predicted_darts`: count of all predictions
- `matched_darts`: reviewed actual darts linked to a prediction
- `missed_darts`: reviewed actual darts with `source = manual_add`
- `false_positives`: predictions not linked to any reviewed actual dart
- `corrected_darts`: linked darts where final label or score changed
- `uncorrected_darts`: linked darts where final label and score stayed the same

### Accuracy metrics

- `score_accuracy = exact_score_matches / actual_darts`
- `label_accuracy = exact_label_matches / actual_darts`
- `recall = matched_darts / actual_darts`
- `precision = matched_darts / predicted_darts`
- `correction_rate = corrected_darts / actual_darts`
- `miss_rate = missed_darts / actual_darts`
- `false_positive_rate = false_positives / predicted_darts`

### Suggested top-line card mapping

To match the first reference while remaining honest:

- `Actual Darts`
- `Predicted Darts`
- `Corrections`
- `Missed Darts`
- `False Positives`
- `System Accuracy` = `score_accuracy`

### Agreement buckets

From existing `cam_details`, derive:

- `3cam_all_match`
- `3cam_two_match`
- `3cam_all_diff`
- `2cam_match`
- `2cam_disagree`
- `1cam_only`
- `no_detection`

### Timing metrics

Available now:

- `detect_to_emit_ms`
- `motion_to_emit_ms`
- `collect_to_emit_ms`
- `pose_infer_ms`
- execution device
- detection profile

New instrumentation needed for a closer match to image 1:

- preprocess ms
- inference queue ms
- decode ms
- candidate select ms

That work should happen in `openvino_inference.py` and be attached to each prediction payload.

## Backend Changes

### New module

Create a dedicated module, recommended name:

- `accuracy_stats.py`

Responsibilities:

- create and close practice accuracy sessions
- append predictions to the active turn
- persist reviews and manual missed darts
- aggregate summary metrics for the dashboard
- return session list, summary cards, agreement stats, and model table data

### `server.py`

Recommended additions:

- start an accuracy session when practice starts
- close the accuracy session when practice stops or the user leaves practice
- when `_emit_dart()` fires in practice mode, write a prediction record to the active session
- expose accuracy APIs:
  - `GET /api/accuracy/summary`
  - `GET /api/accuracy/sessions`
  - `GET /api/accuracy/session/<id>`
  - `POST /api/accuracy/review`
  - `POST /api/accuracy/missed`
  - `POST /api/accuracy/false-positive`
- optionally extend `/api/debug/screenshot` so it accepts:
  - `session_id`
  - `turn_id`
  - `prediction_id`
  - `actual_id`

### Data payload upgrade

Augment the practice prediction payload with:

- `prediction_id`
- `session_id`
- `turn_id`
- `model_name`
- `execution_device`
- `detection_profile`
- `agreement_bucket`
- `timings`

## Frontend Changes

### `frontend/app.js`

Add a dedicated review state for practice:

- `accuracySession`
- `currentTurnReview`
- `pendingPredictions`
- `reviewedTurns`

Recommended behavior changes:

- `onDartScored()` should create a review item, not just update summary labels.
- `throwData` can be repurposed into a structured per-turn prediction list.
- do not reset reviewed turn data in `resetTurn()` until the review has been saved
- add modal handlers for:
  - open add missed dart modal
  - edit predicted dart
  - confirm reviewed turn
  - mark false positive

### `frontend/index.html`

Add:

- accuracy review block in practice side panel
- missed-dart modal
- stats `Accuracy` tab
- summary/table containers for the accuracy dashboard

### `frontend/style.css`

Add styling for:

- accuracy summary cards
- agreement cards
- reviewed turn rows
- modal buttons and number grid
- correction-state badges:
  - `confirmed`
  - `corrected`
  - `missed`
  - `false positive`

## Rollout Plan

### Phase 1: Practice Review MVP

Scope:

- persist practice session start and stop
- save every predicted dart in practice
- add `Add Missed Dart` modal
- add `Confirm / Edit / False Positive` review actions
- save reviewed truth records

Success check:

- after a practice session, we can answer:
  - how many darts were actually thrown
  - how many were detected
  - how many needed correction
  - how many were missed entirely

### Phase 2: Accuracy Dashboard MVP

Scope:

- add stats-page accuracy tab
- top summary cards
- agreement cards
- sessions table
- basic model filter

Success check:

- user can compare reviewed accuracy across multiple practice sessions

### Phase 3: Advanced Failure Analysis

Scope:

- link screenshot artifacts to reviewed throws
- bucket failures by type
- add tip-position review and coordinate error metrics
- add richer timing breakdown from OpenVINO preprocessing and postprocessing

Success check:

- the dashboard starts telling us what to improve, not just that errors happened

## File Impact Map

Likely touched files for implementation:

- [frontend/index.html](../frontend/index.html)
- [frontend/app.js](../frontend/app.js)
- [frontend/style.css](../frontend/style.css)
- [server.py](../server.py)
- [openvino_inference.py](../openvino_inference.py)
- [stats.py](../stats.py) only if shared helpers are reused
- new `accuracy_stats.py`

## Open Questions

These do not block Phase 1, but they affect polish:

- Should review happen after every dart or only after the 3-dart turn?
- Should the first release require exact tip-position correction, or just label/score truth?
- Should practice accuracy sessions be grouped by model name only, or also by board profile and detection profile?
- Do we want game-mode accuracy later, or keep this practice-only until the review UX is proven?

## Recommended Decision

Start with:

- turn-level review
- score and label truth only
- practice-only storage
- model + detection profile filters
- top-line metrics: accuracy, recall, precision, corrections, misses, false positives

That gives useful real numbers quickly without overloading the first version.
