# ThrowVision 🎯

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-4.x-green?logo=opencv)
![Flask](https://img.shields.io/badge/Flask-Socket.IO-black?logo=flask)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Cameras](https://img.shields.io/badge/Cameras-3×_USB-orange)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey)

**ThrowVision** is an open-source, camera-based automatic dart scoring system. It uses three USB webcams arranged at 120° intervals around the dartboard to detect dart tips with millimetre accuracy using frame differencing, perspective homography, and multi-camera consensus fusion.

---

## Features

- 🎯 **3-camera automatic scoring** — triangulates the dart tip from three viewing angles, eliminating shaft/barrel parallax
- 🔀 **Cross-camera mask intersection** — only the dart tip (on the board surface) survives the 2-of-3 vote; flights and shaft cancel out
- 🧮 **Multi-camera consensus** — majority vote, outlier rejection, quality-weighted averaging, near-boundary tiebreaking
- 🎮 **Game modes** — X01, Cricket, Count Up, Bullseye throw-off
- 🌐 **Live web dashboard** — real-time scoring at `http://localhost:5000`
- 📐 **4-point perspective calibration** — saved per-camera, auto-rescaled on resolution change
- 🤖 **Auto-calibration** — YOLO keypoint model detects calibration points automatically

---

## Hardware Requirements

| Item | Spec |
|---|---|
| Cameras | 3× USB webcams, 1080p recommended |
| USB | USB 2.0 — each camera on its own USB controller |
| OS | Windows (tested) / Linux |
| CPU | Any modern x86-64 multi-core |
| GPU | Optional — only needed if YOLO models are enabled |

### Camera Mounting

Mount 3 cameras at **equal 120° intervals** around the board. The even spacing is critical — it ensures the cross-camera parallax cancellation works correctly from all three directions.

| View | Diagram |
|:---:|:---:|
| **Top-down** — 120° spacing | **Side view** — ~45° angle, 35–45 cm above board |
| ![Top-down camera layout](docs/camera_mount_final.png) | ![Side view camera mount](docs/camera_mount_side.png) |

**Checklist before you start:**
- ✅ All 3 cameras are at the same height (board centre level or slightly above)
- ✅ Each camera is angled ~45° downward toward the board centre
- ✅ The full board is visible in each camera's frame
- ✅ Each USB camera is on its own USB controller (avoids bandwidth conflicts)
- ✅ Cameras are **rigidly mounted** — any wobble after calibration breaks accuracy

---

## Installation

```bash
git clone https://github.com/kashiwagiren/ThrowVision.git
cd ThrowVision
python -m venv .venv && .venv\Scripts\activate
pip install flask flask-socketio opencv-python numpy psutil
pip install ultralytics   # optional: YOLO models
```

---

## Quick Start

```bash
python server.py                  # 3 cameras (default)
python server.py --demo           # single camera
python server.py --cameras 0,1,2  # custom indices
python server.py --fps 30         # override FPS
python server.py --no-detection   # UI only
```

Open **http://localhost:5000**

---

## Project Structure

```
ThrowVision/
├── server.py          # Flask + Socket.IO server, detection loop
├── detector.py        # DartDetector — per-camera state machine & tip extraction
├── calibrator.py      # BoardCalibrator — perspective transform & board geometry
├── scorer.py          # ScoreMapper — multi-camera consensus & scoring
├── config.py          # ConfigManager — all tuneable parameters
├── game_mode.py       # X01, Cricket, CountUp, BullseyeThrow engines
├── board_profile.py   # Save/load board position profiles
├── board_annotator.py # Frame annotation for dataset collection
├── stats.py           # Per-game statistics
├── frontend/          # Dashboard (HTML + JS + CSS)
└── calibration/       # Per-camera .npz files (gitignored)
```

---

## How the System Works

ThrowVision has three stages that run every time a dart is thrown: **Calibration**, **Detection**, and **Scoring**.

---

### 1. Calibration — Teaching the Cameras About the Board

#### The Problem

A webcam mounted at an angle sees the board as a distorted ellipse. Distances and angles measured directly in the image are wrong.

```
Raw camera view:          After calibration (warped):
                               20
  ╔══════════════╗          ╭──────╮
  ║  (skewed     ║         /   T20  \
  ║   ellipse)   ║        │   ●bull  │
  ║              ║         \        /
  ╚══════════════╝          ╰──────╯
 Distances are wrong        Perfect circle, true geometry
```

#### The Solution: Perspective Homography

A **homography** is a 3×3 matrix that maps any pixel in the raw camera image to a flat top-down position on the board. After applying it, all ring and segment boundaries are geometrically correct and any point can be accurately converted to real-world millimetres.

#### How to Calibrate

```
Step 1: Click "Calibrate" in the dashboard
Step 2: Click these 4 wire intersections on the outer double ring:

         ┌── 20/1  ──┐
         │           │
    11/14┤  BOARD    ├6/10
         │           │
         └── 3/19  ──┘

Step 3: Confirm — homography saved to calibration/calibration_N.npz
```

These 4 points are at known real-world positions (radius = 170 mm from centre, exact angles), so `cv2.getPerspectiveTransform()` can compute the perfect mapping.

---

### 2. Detection — Finding Where the Dart Landed

#### Detector State Machine

Each of the 3 cameras runs its own independent state machine:

```mermaid
stateDiagram-v2
    [*] --> WAIT
    WAIT --> MOTION : change detected
    MOTION --> STABLE : movement stopped
    MOTION --> HAND : blob too large
    STABLE --> DART : diff stabilised\n(correlation ≥ 99%)
    DART --> WAIT : scored, board reset
    HAND --> WAIT : hand removed
```

#### Step-by-Step Detection

**① Frame Differencing**

Every frame is compared to a stored reference (board without the new dart):

```
diff = | current_frame − reference_frame |
           ↓ threshold + blur
       binary change mask
          (highlights only the dart)
```

**② Motion & Stability Check**

```
blob area < dart_size_min  → noise, ignore
blob area < hand_size_max  → DART candidate
blob area > hand_size_max  → HAND state

Once in candidate state:
  Wait until frame-to-frame correlation ≥ 99%
  (dart has stopped wobbling)
```

**③ Aspect Ratio Filter**

When other darts are already on the board, the diff also picks up flights and shaft residuals. The filter keeps only **elongated blobs**:

```
Dart shaft:   narrow needle shape   → aspect ratio 5:1 to 15:1  ✅ KEEP
Dart flights: wide fan shape        → aspect ratio 1.5:1 to 2:1 ❌ REJECT
```

Threshold: `aspect ratio ≥ 2.5`

**④ PCA Line Fitting**

PCA (Principal Component Analysis) finds the **major axis** of the dart blob — the direction the dart is pointing.

```
   ●●●●●●●●●●●
  ●●●●●●●●●●●●●  ──── major axis (vx, vy)
   ●●●●●●●●●●●
        ↑ dart shaft contour pixels
```

**⑤ Two-Stage Tip Refinement**

```
Full blob:  [FLIGHTS]──[SHAFT]──[BARREL]──[TIP●]

Stage 1 — keep the 25% closest to the tip end:
                              [BARREL]──[TIP●]

Stage 2 — keep the most extreme 5% along the axis:
                                         [TIP●]
         → average these pixels = physical tip point
```

**⑥ Tip Disambiguation — Which End is the Tip?**

```mermaid
flowchart TD
    A[Two axis endpoints: A and B] --> B{Warped-space distance\nto board centre}
    B -->|difference > 15%| C[Pick the closer end = TIP ✅]
    B -->|difference ≤ 15%\nambiguous| D{Raw camera-space\ndistance to bullseye pixel}
    D --> E[Pick the closer end in raw space = TIP ✅]
```

Why does warped distance work? The dart **tip is on the board surface**, so the homography maps it correctly (close to centre). The **barrel is elevated above the board**, so parallax pushes it outward in warped space.

**⑦ Dark-Segment Correction**

Black board segments have near-zero contrast. The detected blob ends at the shaft boundary, not the actual tip.

```
Bright segment (≥80 brightness):
  ══════[SHAFT]═══●tip          detected correctly → push +5%

Dark segment (<80 brightness):
  ══════[SHAFT]              tip invisible → push +18% along axis
          ↑ blob ends here        to reach the actual tip
```

**⑧ Raw → mm Coordinate**

The raw-space tip pixel is converted to real-world millimetres using a **direct homography** (raw px → mm in one step), avoiding the double-conversion error of going raw→warped→mm.

---

### 3. Cross-Camera Intersection — Cancelling Parallax

This is the key accuracy step.

#### The Parallax Problem

```
          Camera 0 (top)
               ▼
    ┌──────────┼──────────┐
    │          ●tip       │
    │         /barrel     │  ← barrel appears shifted
    │        / (elevated) │     differently per camera
    └──────────────────────┘
Cam 1 (left)     Cam 2 (right)
    sees barrel  →     sees barrel ←
    shifted left        shifted right
```

#### The 2-of-3 Vote

```mermaid
flowchart LR
    A[Cam 0 warped mask] --> D
    B[Cam 1 warped mask] --> D
    C[Cam 2 warped mask] --> D
    D{Vote map\nsum masks} --> E{pixel ≥ 2 votes?}
    E -->|Yes| F[✅ Tip pixel survives]
    E -->|No| G[❌ Shaft/flight cancelled]
    F --> H[Centroid = true dart tip]
```

The dart **tip** (on the board surface) maps to the **same board pixel** for all cameras → gets 3 votes → survives.  
The **shaft/flights** (elevated above the board) map to **different board pixels** per camera due to parallax → gets only 1 vote each → cancelled.

---

### 4. Scoring — Position to Score

#### Board Coordinate System

```
              +Y  (sector 20)
               ↑
               │  ┌─ DB (r ≤ 6.35mm)
               │  ├─ SB (r ≤ 15.9mm)
               │  ├─ Single
               │  ├─ Triple (r 99–107mm)
  ─────────────┼────────────── +X  (sector 6)
  (0,0)=bull   │  ├─ Single
               │  ├─ Double (r 162–170mm)
               │  └─ OFF    (r > 170mm)
               ↓
```

#### Scoring Logic

```mermaid
flowchart TD
    A["tip position (x_mm, y_mm)"] --> B["r = √(x²+y²)\nθ = atan2(y,x)°"]
    B --> C{r ≤ 6.35mm?}
    C -->|Yes| D[Double Bull = 50]
    C -->|No| E{r ≤ 15.9mm?}
    E -->|Yes| F[Single Bull = 25]
    E -->|No| G{r > 170mm?}
    G -->|Yes| H[OFF = 0]
    G -->|No| I[Look up sector from θ\ncheck ring from r]
    I --> J["S/T/D + sector value"]
```

#### Multi-Camera Consensus Priority

```
Priority 1: Cross-camera mask intersection  ← most accurate
Priority 2: Majority vote (2/3 agree)
Priority 3: Outlier rejection (drop if >40mm from others)
Priority 4: Quality-weighted average
Priority 5: Near-boundary → best single camera wins
```

| Method | Weight | Notes |
|---|---|---|
| `LINE_FIT` | 4.0 | Clear tip direction |
| `YOLO_MOTION` | 4.5 | YOLO bbox + motion-refined |
| `LINE_FIT_WEAK` | 1.5 | Ambiguous tip direction |
| `WARPED` | 0.5 | Last-resort fallback |

---

## Detection Pipeline (Full Flow)

```mermaid
flowchart TD
    A[📷 Camera Frame] --> B[Frame diff vs reference]
    B --> C[Motion / stability check]
    C --> D{Blob size?}
    D -->|too small| A
    D -->|hand-sized| E[HAND state — wait]
    D -->|dart-sized| F[Aspect ratio filter ≥ 2.5]
    F --> G[Novelty filter — pick newest dart blob]
    G --> H[PCA line fit — find dart axis]
    H --> I[Two-stage tip refinement\n25% zone → 5% extremum]
    I --> J[Tip disambiguation\nwarped-space + raw fallback]
    J --> K{Dark segment?}
    K -->|brightness < 80| L[Extrapolate +18% along axis]
    K -->|bright| M[Fine-tune +5%]
    L --> N[Raw px → mm via direct homography]
    M --> N
    N --> O[Cross-camera 2-of-3 mask vote]
    O --> P[Consensus scoring\nmajority → outlier reject → weighted avg]
    P --> Q[🎯 Score emitted via Socket.IO]
```

---

## Game Modes

| Mode | How to Win | Key Rule |
|---|---|---|
| **X01** (301/501/701/901) | Reach exactly 0 | Must finish on a **double**. Bust = score back to turn start. |
| **Cricket** | Close 15–20 + Bull, score ≥ opponent | Hit a number 3 times to close it. Extra hits score points if opponent hasn't closed. |
| **Count Up** | Highest total after N rounds | No bust. Pure accumulation. |
| **Bullseye Throw-off** | Closest to bull goes first | Tiebreak re-throw if within 1 mm. |

---

## Configuration

| Parameter | Default | Description |
|---|---|---|
| `resolution` | `(1920, 1080)` | Camera capture resolution |
| `fps` | `30` | Capture frame rate |
| `dart_size_min` | `800` | Minimum contour area (px²) |
| `dart_size_max` | `25000` | Maximum contour area (px²) |
| `binary_thresh` | `30` | Frame-diff threshold |
| `tip_offset_px` | `8.0` | Fine tip offset along dart axis |
| `detection_speed` | `DEFAULT` | `VERY_LOW` / `LOW` / `DEFAULT` / `HIGH` / `VERY_HIGH` |

---

## API

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard UI |
| `GET /api/status` | Camera states, last score |
| `GET /api/settings` | Current config |
| `GET /api/system-stats` | CPU, RAM, GPU |

**Socket.IO:** `dart_scored` · `cam_status` · `calibrate` · `reset_board` · `start_game` · `undo_dart`

---

## Tips for Best Accuracy

- 🌈 **Bright contrasting flights** — pink/orange/yellow work best; avoid dark flights on dark segments
- 📷 **Keep cameras stable** — any wobble after calibration degrades accuracy
- 💡 **Even lighting** — reduce shadows across the board
- ✋ **Remove hand quickly** — the system waits for the HAND state to clear before scoring
- 🔧 **Recalibrate** if you move a camera

---

## License

MIT License — see [LICENSE](LICENSE) for details.
