# ThrowVision 🎯

**ThrowVision** is an open-source, camera-based automatic dart scoring system. It uses three USB webcams arranged at 120° intervals around the dartboard to detect dart tips with millimetre accuracy using frame differencing, perspective homography, and multi-camera consensus fusion.

---

## Features

- **3-camera automatic scoring** — triangulates the dart tip position from three viewing angles, eliminating shaft/barrel parallax
- **Cross-camera mask intersection** — intersects each camera's diff mask in warped board space; only the dart tip (on the board surface) survives, flights and shaft are cancelled
- **Multi-camera consensus** — majority vote, outlier rejection, quality-weighted averaging, and near-boundary tiebreaking
- **Game modes** — X01 (301/501/701/901), Cricket, Count Up, and Bullseye throw-off for first-player determination
- **Live web dashboard** — real-time scoring via Flask + Socket.IO; open `http://localhost:5000` in any browser
- **4-point perspective calibration** — saved per-camera, auto-rescaled if resolution changes
- **Auto-calibration** — YOLO keypoint model (`board_kpt_best.pt`) detects the 4 calibration points and bullseye automatically
- **Dart annotation mode** — saves labelled frames on every scored dart for dataset collection
- **Board profiles** — save/load custom board positioning configurations

---

## Hardware Requirements

| Item | Spec |
|---|---|
| Cameras | 3× USB webcams, 1080p recommended |
| USB | Each camera on its own USB controller (avoid bandwidth conflicts) |
| OS | Windows (tested) / Linux |
| CPU | Intel/AMD x86-64, any modern multi-core |
| GPU | Optional — used only if YOLO models are enabled (CUDA) |

**Camera placement:** Mount cameras at equal 120° horizontal spacing around the board at dartboard height, angled slightly downward at ~45°.

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/kashiwagiren/ThrowVision.git
cd ThrowVision

# 2. Create a virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # Linux/macOS

# 3. Install dependencies
pip install flask flask-socketio opencv-python numpy psutil

# Optional — for YOLO tip/calibration detection
pip install ultralytics
```

---

## Quick Start

```bash
# Start with 3 cameras (default, cameras 0, 1, 2)
python server.py

# Single camera demo
python server.py --demo

# Custom camera indices
python server.py --cameras 0,1,2

# Override FPS
python server.py --fps 30

# UI only (no detection — for testing the frontend)
python server.py --no-detection
```

Open **http://localhost:5000** in your browser.

---

## Calibration

Each camera needs a one-time perspective calibration. From the dashboard:

1. Click **Calibrate** for a camera
2. Click the **4 wire intersections** at the boundary of sectors 20/1, 6/10, 3/19, and 11/14 at the outer double ring
3. Click **Confirm** — the homography is saved to `calibration/calibration_N.npz`

Calibration data is saved as `.npz` files with the source points and the resolution they were captured at. If you later change resolution, the points are automatically rescaled — no need to recalibrate.

**Auto-calibration** (if `board_kpt_best.pt` model is present): The YOLO keypoint model detects the 4 calibration points and bullseye automatically with a single click.

---

## Project Structure

```
ThrowVision/
├── server.py          # Flask + Socket.IO server, detection loop, scoring pipeline
├── detector.py        # DartDetector — per-camera state machine, tip extraction
├── calibrator.py      # BoardCalibrator — perspective transform, board geometry, masks
├── scorer.py          # ScoreMapper — multi-camera consensus, dart scoring
├── config.py          # ConfigManager — all tuneable parameters
├── game_mode.py       # Game engines — X01, Cricket, CountUp, BullseyeThrow
├── board_profile.py   # Board profiles (save/load custom positions)
├── board_annotator.py # Frame annotation for dataset collection
├── stats.py           # Per-game statistics
├── frontend/
│   ├── index.html     # Dashboard UI
│   ├── app.js         # Socket.IO client, game UI logic
│   └── style.css      # Styling
└── calibration/       # Per-camera .npz calibration files (gitignored)
```

---

## Detection Pipeline

```
Camera frame
    │
    ▼
[Frame diff vs reference]   ← frame differencing in raw camera space
    │
    ▼
[Contour filtering]         ← dart-sized blobs, aspect ratio ≥ 2.5 (shaft, not flights)
    │
    ▼
[Line-fit tip extraction]   ← PCA axis → two-stage 25%/5% extremum refinement
    │
    ▼
[Tip disambiguation]        ← warped-space distance to board centre;
                              raw camera-space fallback when ambiguous
    │
    ▼
[Dark-segment extrapolation]← tip pushed along axis when brightness < 80
    │
    ▼
[Per-camera mm coordinate]  ← direct raw→mm homography (no double-conversion)
    │
    ▼
[Cross-camera intersection] ← 2-of-3 cameras' diff masks intersected in board space;
                              shaft/flights cancel, tip survives
    │
    ▼
[Consensus scoring]         ← majority vote → outlier rejection → weighted average
    │
    ▼
Score emitted via Socket.IO
```

### Scoring Priority (highest to lowest)

| Source | When used |
|---|---|
| Cross-camera intersection | 2+ cameras' diff masks overlap at same board-surface point |
| Majority vote | 2/3 cameras report the same segment label |
| Outlier rejection | 1 camera is > 40 mm from the other two |
| Quality-weighted average | Remaining cameras averaged by detection method quality |

### Detection Method Weights

| Method | Weight | Description |
|---|---|---|
| `LINE_FIT` | 4.0 | PCA line fit with clear tip direction |
| `YOLO_MOTION` | 4.5 | YOLO bbox + motion-refined tip (if model enabled) |
| `LINE_FIT_WEAK` | 1.5 | Line fit, ambiguous tip direction |
| `WARPED` | 0.5 | Warped-space centroid fallback (least reliable) |

---

## Game Modes

| Mode | Description |
|---|---|
| **X01** | Standard 301 / 501 / 701 / 901. Double-in not required, double-out required. Bust returns score to start of turn. |
| **Cricket** | Close 15–20 + Bull (25). Score points on numbers the opponent hasn't closed. Win by closing everything with score ≥ opponent. |
| **Count Up** | Accumulate points over N rounds (default 8). Highest total wins. |
| **Bullseye Throw-off** | Each player throws once; closest to bull goes first. Tiebreaks if within 1 mm. |

---

## Configuration

Key parameters in `config.py` (`ConfigManager`):

| Parameter | Default | Description |
|---|---|---|
| `resolution` | `(1920, 1080)` | Camera capture resolution |
| `fps` | `30` | Capture frame rate |
| `dart_size_min` | `800` | Minimum contour area (px²) for a dart blob |
| `dart_size_max` | `25000` | Maximum contour area (px²) |
| `binary_thresh` | `30` | Frame-diff binarisation threshold |
| `tip_offset_px` | `8.0` | Fine tip offset along dart axis (px) |
| `detection_speed` | `DEFAULT` | `VERY_LOW`/`LOW`/`DEFAULT`/`HIGH`/`VERY_HIGH` — trades sensitivity for accuracy |
| `yolo_enabled` | `True` | Enable YOLO tip/calibration detection |
| `yolo_model_path` | `models/darttipbox1.1.pt` | YOLO dart tip model path |
| `board_kpt_model_path` | `models/board_kpt_best.pt` | YOLO board keypoint model path |

---

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Dashboard UI |
| `/api/status` | GET | Camera states, last score, detector count |
| `/api/settings` | GET | Current config values |
| `/api/system-stats` | GET | CPU, RAM, GPU usage |

### Socket.IO Events

| Event | Direction | Payload |
|---|---|---|
| `dart_scored` | Server → Client | `{label, score, x_mm, y_mm, cam_details, game_state}` |
| `cam_status` | Server → Client | `{cam_id, state, fps, active}` |
| `calibrate` | Client → Server | `{cam_id, points: [[x,y]×4]}` |
| `reset_board` | Client → Server | — |
| `start_game` | Client → Server | `{mode, options}` |
| `undo_dart` | Client → Server | — |

---

## Tips for Best Accuracy

- **Use bright, contrasting dart flights** — pink/orange/yellow flights are easiest to detect; avoid dark flights on dark segments
- **Keep cameras stable** — any camera wobble after calibration degrades accuracy
- **Good lighting** — even, diffuse lighting reduces shadows on the board
- **Throw cleanly** — remove your hand from the camera's field of view promptly after each throw (the system waits for the HAND state to clear before scoring)
- **Recalibrate if you move a camera** — calibration is stored per-camera index

---

## License

MIT License — see [LICENSE](LICENSE) for details.
