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

## How the System Works

ThrowVision is made up of three main stages that run together every time a dart is thrown: **Calibration**, **Detection**, and **Scoring**. Here is a full explanation of each.

---

### 1. Calibration — Teaching the Cameras About the Board

Before ThrowVision can score anything, each camera needs to understand where the dartboard is in its view. This is done through a **perspective calibration**.

#### The Problem: Camera Angle Distortion

A USB webcam mounted at an angle sees the dartboard as a skewed ellipse, not a perfect circle. Distances and angles measured directly in the camera image are therefore wrong — e.g. a dart in T20 and a dart in S20 might appear the same distance from the centre even though T20 is physically much closer.

#### The Solution: Perspective Homography

A **homography** is a mathematical transformation (a 3×3 matrix) that maps any pixel in the raw camera image to a pixel in a "top-down" flat view of the board. After applying the homography:
- The board appears as a perfect circle
- All ring and segment boundaries are geometrically correct
- Any point on the board surface can be accurately converted to real-world millimetres from the bullseye

#### How to Calibrate (Manual)

1. Open the dashboard → click **Calibrate** on a camera
2. Click exactly **4 wire intersections** on the outer double ring — specifically where the thin metal wires cross at:
   - The boundary between sectors **20 and 1**
   - **6 and 10**
   - **3 and 19**
   - **11 and 14**
3. Confirm — the system computes `cv2.getPerspectiveTransform()` between those 4 raw pixel points and their known real-world positions on the board, and saves the result

These 4 points were chosen because they are at known angles on the double ring (radius = 170 mm from centre), so their real-world coordinates are mathematically exact.

#### What Gets Saved

Calibration is stored in `calibration/calibration_N.npz` (one file per camera). It contains the 4 source points and the resolution they were captured at. If you change camera resolution later, ThrowVision automatically **rescales the source points** to the new resolution and recomputes the homography — you never need to recalibrate after a resolution change.

#### Auto-Calibration (Optional)

If the YOLO keypoint model (`board_kpt_best.pt`) is present, ThrowVision can detect the 4 calibration points and bullseye automatically from a single frame. The model was trained to identify specific wire intersections on the board.

---

### 2. Detection — Finding Where the Dart Landed

Detection runs continuously in the background on all 3 cameras simultaneously. Each camera runs its own independent `DartDetector` state machine.

#### The Detector State Machine

Each camera cycles through these states:

```
WAIT ──► MOTION ──► STABLE ──► DART ──► SCORED
  ▲                               │
  └───────────────────────────────┘
         (after board reset)
```

| State | What it means |
|---|---|
| `WAIT` | Camera is idle, watching for movement |
| `MOTION` | Movement detected — a dart (or hand) is flying |
| `STABLE` | Movement has stopped — waiting for the dart to settle |
| `DART` | A dart is confirmed on the board — tip is extracted |
| `HAND` | Large movement detected — a hand is in the way |

#### Step-by-Step: How a Dart is Detected

**① Frame Differencing**

Every frame, the camera image is compared to a stored **reference frame** (the board as it looked before the dart was thrown). The difference between the two images — the **diff** — highlights only what has changed, i.e. the dart.

```
diff = |current_frame − reference_frame|
```

This is done in raw camera space (before any warping) for maximum precision. The diff is blurred slightly with a Gaussian filter to reduce sensor noise, then thresholded into a binary black/white image.

**② Motion Detection**

The binary diff is checked against a size threshold. If the changed area is:
- **Too small** → noise or vibration, ignored
- **Between dart_size_min and dart_size_max** → likely a dart
- **Larger than hand_size_max** → a hand, transitions to HAND state

The camera waits for the diff to **stabilise** over several frames (correlation between consecutive frames must exceed 99%) before proceeding — this ensures the dart has stopped wobbling.

**③ Raw-Space Contour Analysis (tip extraction)**

Once stable, ThrowVision finds the dart contour in raw camera space:

- All change blobs are found using `cv2.findContours()`
- Blobs are filtered to dart-sized areas
- **Aspect ratio filter**: when multiple darts are already on the board, only elongated blobs (aspect ratio ≥ 2.5) are kept — this filters out flights (which are wide and fan-shaped) vs. the dart shaft (which is narrow and needle-like)
- **Novelty filter**: among remaining blobs, the one furthest from previously scored tips is selected (= the newest dart), with a penalty for blobs that appear off the board in warped coordinates

**④ PCA Line Fitting (finding the dart axis)**

On the selected contour, **PCA (Principal Component Analysis)** is used to find the major axis of the blob — i.e. the direction the dart is pointing. This gives a direction vector `(vx, vy)` and the two extreme endpoints along that axis.

**⑤ Two-Stage Tip Refinement**

The physical dart tip is the very end of the dart, but the whole shaft is visible in the diff. To isolate only the tip:

1. **Stage 1 (25% zone):** Only the 25% of pixels closest to the tip end of the axis are kept. This cuts out shaft and flight pixels.
2. **Stage 2 (5% extremum):** Within that 25%, only the most extreme 5% of pixels along the axis are averaged. This pinpoints the very tip contact point rather than anywhere along the shaft.

**⑥ Tip Disambiguation — Which End is the Tip?**

The dart has two ends. The system must decide which is the tip. The rule: **the tip is always the end closest to the board centre** in warped (top-down) space. This works because:

- The dart tip is physically touching the board surface → the homography maps it correctly → it appears close to the board centre
- The barrel and flights are elevated above the board surface → camera parallax shifts them outward in warped space → they appear further from centre

**When the two ends are ambiguous** (within 15% of each other in warped distance), a second test is used: in **raw camera space**, the end closest to the raw pixel position of the bullseye (`board_centre_cam`) is chosen. This is a reliable fallback because the raw camera position of the bullseye is a fixed reference that doesn't depend on any 3D math.

**⑦ Dark-Segment Correction**

Black scoring segments (like S20 dark or S3 dark) have very low contrast against the dart tip. The camera cannot see pixels it cannot distinguish from the background, so the detected blob may end at the shaft rather than at the actual tip. ThrowVision corrects this:

- Sample the **raw image brightness** at the detected tip position
- If brightness < 80 (dark segment): push the tip **18% further** along the dart axis
- If brightness ≥ 80 (bright segment): push **5%** for fine-tuning

This extrapolation compensates for the invisible tip pixels without overshooting.

**⑧ Raw → mm Coordinate**

The raw-space tip pixel is converted to real-world millimetres from the bullseye using a **direct homography** (`_M_mm`). This avoids the double-conversion error that comes from going raw→warped→mm. The result is the dart tip position as `(x_mm, y_mm)` where (0, 0) is the bullseye.

---

### 3. Cross-Camera Mask Intersection — Cancelling Parallax

This is the most important accuracy step and the reason ThrowVision uses three cameras.

#### The Parallax Problem

The cameras are mounted at an angle (~45°) to the board. This means anything **above the board surface** — the dart barrel, shaft, and flights — appears shifted in the camera image. Each camera is at a different angle, so the barrel appears shifted in a **different direction** for each camera.

This causes per-camera tip detection to sometimes pick up the barrel or flights (which appear "on the board" in that camera's warped view, just at the wrong position).

#### The Intersection Solution

After all 3 cameras have detected a dart, ThrowVision:

1. Takes each camera's **raw diff mask** (the full binary image showing all changed pixels — shaft, flights, tip)
2. **Warps each mask** into board space using that camera's homography
3. Adds all three warped masks together into a **vote map** (each pixel = how many cameras see change there)
4. Keeps only pixels where **≥ 2 cameras agree** (2-of-3 vote)

Why does this work?

- **The dart TIP** is on the board surface. All three cameras' homographies correctly map it to the same board position. It **survives** the vote.
- **The shaft and flights** are elevated above the board. Each camera's parallax shifts them in a different direction in warped space. They **cancel out** because they land in different positions for each camera and can't get 2+ votes.

The centroid of the surviving intersection region is then the true dart tip position, converted to millimetres.

#### When Intersection Fails

If the intersection is empty (e.g. one camera has bad visibility of the tip), the system falls back to **majority vote** and **quality-weighted averaging** of the individual per-camera detections.

---

### 4. Scoring — Converting Position to Score

Once the final tip position in millimetres is known, scoring is straightforward geometry.

#### Coordinate System

- **(0, 0)** = bullseye centre
- **+Y** = upward (towards sector 20)
- **+X** = rightward (towards sector 6)
- Distances in **millimetres**

#### Polar Conversion

The `(x_mm, y_mm)` position is converted to polar coordinates:
```
r     = sqrt(x² + y²)         # distance from centre in mm
theta = atan2(y, x) in degrees # angle from horizontal
```

#### Ring Lookup

| Condition | Score |
|---|---|
| r ≤ 6.35 mm | Double Bull (50) |
| r ≤ 15.9 mm | Single Bull (25) |
| 99 ≤ r ≤ 107 mm | Triple ring |
| 162 ≤ r ≤ 170 mm | Double ring |
| r > 170 mm | Off board (0) |

#### Sector Lookup

The board has 20 sectors each spanning 18°. Sector 20 is at the top (90°). The `theta` angle is used to find which sector the dart landed in using the standard dartboard order:

```
20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5
```

#### Multi-Camera Consensus

When 3 cameras each report a tip position, the final score is determined by this priority chain:

1. **Cross-camera intersection** (highest trust) — used if 2+ cameras' masks overlapped
2. **Majority vote** — if 2/3 cameras independently score the same segment label
3. **Outlier rejection** — if one camera is >40 mm from the other two, it is discarded
4. **Quality-weighted average** — remaining cameras are averaged, with higher weight for more reliable detection methods (e.g. `LINE_FIT` weight=4.0 vs `LINE_FIT_WEAK` weight=1.5)
5. **Near-boundary correction** — if the averaged position is within 6 mm of a wire, the single best-quality camera's reading is used instead to avoid averaging across a boundary

---

## Detection Pipeline (Summary)

```
Camera frame
    │
    ▼
[Frame diff vs reference]   ← highlight only what changed (the new dart)
    │
    ▼
[Motion / stability check]  ← wait for dart to stop wobbling
    │
    ▼
[Contour filtering]         ← dart-sized blobs; aspect ratio ≥ 2.5 to reject flights
    │
    ▼
[Novelty filter]            ← pick blob furthest from already-scored tips
    │
    ▼
[PCA line fit]              ← find dart axis direction
    │
    ▼
[Two-stage tip refinement]  ← 25% zone → 5% extremum to isolate physical tip
    │
    ▼
[Tip disambiguation]        ← warped-space board-centre distance;
                              raw camera-space fallback if ambiguous
    │
    ▼
[Dark-segment correction]   ← extrapolate 18% if tip is in a dark wedge
    │
    ▼
[Raw → mm coordinate]       ← direct homography, no double-conversion
    │
    ▼
[Cross-camera intersection] ← 2-of-3 vote in warped board space
    │                         tip survives, shaft/flights cancel out
    ▼
[Consensus scoring]         ← majority vote → outlier reject → weighted average
    │
    ▼
Score emitted via Socket.IO → browser dashboard
```

---

## Game Modes

| Mode | Description |
|---|---|
| **X01** | Standard 301 / 501 / 701 / 901. Double-out required. Bust returns score to start of turn. |
| **Cricket** | Close 15–20 + Bull (25). Score points on numbers the opponent hasn't closed. Win by closing everything with score ≥ opponent. |
| **Count Up** | Accumulate points over N rounds (default 8). Highest total wins. |
| **Bullseye Throw-off** | Each player throws once; closest to bull goes first. Tiebreaks (re-throw) if within 1 mm. |

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
