"""ThrowVision – Flask + Socket.IO bridge server.

Runs the detection loop in a background thread and pushes
dart_scored / cam_status events to any connected browsers.

Usage:
    python server.py                  # 3-camera, opens http://localhost:5000
    python server.py --demo           # single camera (cam 0)
    python server.py --cameras 0,1,2  # custom camera indices
    python server.py --fps 30         # override FPS
    python server.py --no-detection   # serve UI only (demo / test mode)
"""

import argparse
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # silence HTTP request logs

from flask import Flask, send_from_directory, Response, jsonify, request
from flask_socketio import SocketIO

app = Flask(__name__, static_folder="frontend", static_url_path="")
app.config["SECRET_KEY"] = "throwvision-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── Shared state (written by detection thread, read by SocketIO) ────────────
_cam_states: dict = {}   # cam_id -> {"state": str, "fps": float, "active": bool}
_last_score: dict = {}   # last dart_scored payload
_detectors: list = []    # DartDetector instances (set by detection thread)
_calibrators: list = []  # BoardCalibrator instances
_consensus_scored_tips: list = []  # global list of (x,y) consensus tip positions
_cameras_open: bool = False       # True = cameras are open and streaming
_cameras_lock = threading.Lock()  # protects camera open/close
_state_lock = threading.Lock()    # protects shared state

from board_profile import BoardProfile
from game_mode import BullseyeThrow, GameX01, GameCricket, GameCountUp
import stats as game_stats

_board_profile = BoardProfile()
# Load first available profile if any exist
_saved = BoardProfile.list_profiles()
if _saved:
    _board_profile.load(_saved[0]["name"])
_cfg = None              # ConfigManager reference
_detection_paused: bool = True   # True = detection scoring is paused
_annotation_mode: bool = False   # True = auto-save frames on each dart scored
_annotation_count: int = 0       # running count of saved annotations

# ── Game mode state ──────────────────────────────────────────────────────────
_game_mode: Optional[str] = None       # 'bullseye' | 'x01' | 'cricket' | 'countup' | None
_bullseye: Optional[BullseyeThrow] = None
_game = None                           # active GameX01 / GameCricket / GameCountUp
_game_pending_mode: Optional[str] = None   # mode to launch after bullseye
_game_pending_opts: dict = {}              # options for the pending game

# ── Static routes ────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("frontend", "index.html")


@app.route("/api/status")
def api_status():
    with _state_lock:
        return {
            "cam_states": _cam_states.copy(),
            "last_score": _last_score.copy(),
            "annotation_count": _annotation_count,
            "num_cameras": len(_detectors),
        }


@app.route("/api/settings")
def api_settings():
    """Return current config values so the frontend can sync UI state."""
    if _cfg is None:
        return {"error": "Config not loaded"}, 503
    return {
        "resolution": f"{_cfg.resolution[0]}x{_cfg.resolution[1]}",
        "fps": _cfg.fps,
        "min_dart_area": _cfg.dart_size_min,
        "max_dart_area": _cfg.dart_size_max,
        "binary_thresh": _cfg.binary_thresh,
        "tip_offset_px": _cfg.tip_offset_px,
    }

@app.route("/api/system-stats")
def api_system_stats():
    """Return CPU, RAM, and GPU usage."""
    import subprocess
    stats = {
        "cpu_percent": 0,
        "ram_used_gb": 0, "ram_total_gb": 0, "ram_percent": 0,
        "gpu": None,
    }
    try:
        import psutil
        mem = psutil.virtual_memory()
        stats["cpu_percent"] = psutil.cpu_percent(interval=0)
        stats["ram_used_gb"] = round(mem.used / (1024 ** 3), 1)
        stats["ram_total_gb"] = round(mem.total / (1024 ** 3), 1)
        stats["ram_percent"] = mem.percent
    except ImportError:
        pass
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and r.stdout.strip():
            parts = r.stdout.strip().split(", ")
            stats["gpu"] = {
                "name": parts[0],
                "mem_used_mb": int(parts[1]),
                "mem_total_mb": int(parts[2]),
                "util_percent": int(parts[3]),
            }
    except Exception:
        pass
    return stats


# ── Calibration API ──────────────────────────────────────────────────────────


@app.route("/api/cal/frame/<int:cam_id>")
def api_cal_frame(cam_id):
    """Capture and return a JPEG frame from the specified camera."""
    import cv2, numpy as np
    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400
    det = _detectors[cam_id]
    if not det.active:
        return {"error": f"Camera {cam_id} is not active"}, 503
    # Use last_frame if it has real content (not black)
    frame = det.last_frame
    if frame is not None and np.mean(frame) > 5:
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(buf.tobytes(), mimetype='image/jpeg')
    # Otherwise keep grabbing until we get a non-black frame
    for _ in range(20):
        frame = det._grab()
        if frame is not None and np.mean(frame) > 5:
            _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return Response(buf.tobytes(), mimetype='image/jpeg')
        time.sleep(0.3)
    return {"error": "Camera not ready"}, 503


@app.route("/api/cal/resolution", methods=["POST"])
def api_cal_resolution():
    """Change capture resolution for all cameras."""
    data = request.get_json()
    w = data.get("width", 848)
    h = data.get("height", 480)
    for det in _detectors:
        det.cfg.resolution = (w, h)
        # Re-grab a frame at the new resolution
        if det.active:
            det._grab()
    print(f"[CAL] Resolution changed to {w}×{h}")
    return jsonify({"ok": True, "width": w, "height": h})


@app.route("/api/cal/accept", methods=["POST"])
def api_cal_accept():
    """Accept 4 calibration points for a camera."""
    import numpy as np
    from flask import request
    data = request.get_json()
    cam_id = data.get("cam_id", 0)
    points = data.get("points")  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    # Actual frame dimensions from the calibration canvas (may differ from cfg.resolution)
    frame_w = data.get("frame_width")
    frame_h = data.get("frame_height")
    if points is None or len(points) != 4:
        return {"error": "Exactly 4 points required"}, 400
    if cam_id < 0 or cam_id >= len(_calibrators):
        return {"error": "Invalid camera ID"}, 400
    cal = _calibrators[cam_id]
    src = np.array(points, dtype=np.float32)
    # Temporarily set cal's resolution to match the actual frame the user calibrated on,
    # so the saved .npz has coordinates and resolution that are consistent.
    if frame_w and frame_h:
        old_w, old_h = cal.w, cal.h
        cal.w, cal.h = int(frame_w), int(frame_h)
        cal.calibrate(src)
        # Restore so the calibrator continues operating at cfg resolution
        cal.w, cal.h = old_w, old_h
    else:
        cal.calibrate(src)
    # Re-init detector with new calibration
    det = _detectors[cam_id]
    det.cal = cal
    det.capture_reference()
    det.reset_to_wait()
    print(f"[CAL] Camera {cam_id}: web calibration accepted "
          f"(frame {frame_w}x{frame_h})")
    return {"ok": True, "cam_id": cam_id}


@app.route("/api/cal/info/<int:cam_id>")
def api_cal_info(cam_id):
    """Return current calibration status for a camera."""
    if cam_id < 0 or cam_id >= len(_calibrators):
        return {"error": "Invalid camera ID"}, 400
    cal = _calibrators[cam_id]
    info = {
        "cam_id": cam_id,
        "calibrated": cal.is_calibrated,
        "resolution": [cal.w, cal.h],
    }
    if cal._src_pts is not None:
        info["src_points"] = cal._src_pts.tolist()
    return info


@app.route("/api/cal/auto/<int:cam_id>")
def api_cal_auto(cam_id):
    """Auto-detect dartboard and return 4 calibration points.

    Detection priority:
      1. Board profile feature matching
      2. HoughCircles / ellipse fitting (geometric fallback)
    """
    import cv2
    import numpy as np
    import math

    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400
    det = _detectors[cam_id]
    if not det.active:
        return {"error": f"Camera {cam_id} is not active"}, 400
    frame = det._grab()
    if frame is None:
        return {"error": "Failed to capture frame"}, 500

    # ── Strategy 1: Board profile feature matching ───────────────
    if _board_profile.is_registered:
        matched = _board_profile.detect(frame)
        if matched is not None:
            points = [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in matched]
            print(f"[CAL] Auto-detect Cam {cam_id}: board profile match -> 4 points")
            return {
                "ok": True, "cam_id": cam_id, "method": "profile",
                "center": [0, 0], "radius": 0, "points": points,
            }

    # ── Strategy 3: HoughCircles / ellipse ────────────────────────

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    blurred = cv2.GaussianBlur(gray, (9, 9), 2)
    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1.2,
        minDist=min(w, h) // 4,
        param1=100, param2=60,
        minRadius=int(min(w, h) * 0.15),
        maxRadius=int(min(w, h) * 0.48),
    )

    best_cx, best_cy, best_r = None, None, None

    if circles is not None:
        circles = np.round(circles[0]).astype(int)
        img_cx, img_cy = w / 2, h / 2
        best_dist = float('inf')
        for cx, cy, r in circles:
            d = math.hypot(cx - img_cx, cy - img_cy)
            if d < best_dist:
                best_dist = d
                best_cx, best_cy, best_r = float(cx), float(cy), float(r)

    # Contour ellipse fit (fallback)
    if best_cx is None:
        thresh = cv2.adaptiveThreshold(
            blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 31, 5)
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_area = 0
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < (min(w, h) * 0.1) ** 2:
                continue
            if len(cnt) < 5:
                continue
            ellipse = cv2.fitEllipse(cnt)
            (ecx, ecy), (ma, Mi), angle = ellipse
            ratio = min(ma, Mi) / max(ma, Mi) if max(ma, Mi) > 0 else 0
            if ratio > 0.5 and area > best_area:
                best_area = area
                best_cx, best_cy = float(ecx), float(ecy)
                best_r = float((ma + Mi) / 4)

    if best_cx is None:
        return {"error": "Could not detect dartboard. Try adjusting camera."}, 404

    # Compute 4 wire-intersection points from circle
    from calibrator import _wire_angle
    angles_deg = [
        _wire_angle(20, 1),
        _wire_angle(6, 10),
        _wire_angle(3, 19),
        _wire_angle(11, 14),
    ]

    points = []
    for ang in angles_deg:
        rad = math.radians(ang)
        px = best_cx + best_r * math.cos(rad)
        py = best_cy - best_r * math.sin(rad)
        points.append([round(px, 1), round(py, 1)])

    print(f"[CAL] Auto-detect Cam {cam_id}: center=({best_cx:.0f},{best_cy:.0f}) "
          f"r={best_r:.0f} -> 4 points computed")

    return {
        "ok": True,
        "cam_id": cam_id,
        "method": "circle",
        "center": [round(best_cx, 1), round(best_cy, 1)],
        "radius": round(best_r, 1),
        "points": points,
    }


@app.route("/api/board/register", methods=["POST"])
def api_board_register():
    """Register the current board from a calibrated camera."""
    import cv2
    import numpy as np
    from flask import request

    data = request.get_json()
    cam_id = data.get("cam_id", 0)
    name = data.get("name", "default")

    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400
    det = _detectors[cam_id]
    if not det.active:
        return {"error": f"Camera {cam_id} is not active"}, 400
    cal = _calibrators[cam_id]
    if not cal.is_calibrated:
        return {"error": f"Camera {cam_id} is not calibrated. Calibrate first."}, 400
    if cal._src_pts is None:
        return {"error": "No calibration points saved"}, 400

    frame = det._grab()
    if frame is None:
        return {"error": "Failed to capture frame"}, 500

    pts = cal._src_pts
    cx = float(pts[:, 0].mean())
    cy = float(pts[:, 1].mean())
    r = float(np.mean(np.sqrt((pts[:, 0] - cx)**2 + (pts[:, 1] - cy)**2)))

    try:
        _board_profile.register(frame, pts, (cx, cy), r, name=name)
        return {"ok": True, "name": name, "features": int(len(_board_profile.ref_kp_pts))}
    except ValueError as e:
        return {"error": str(e)}, 400


@app.route("/api/board/list")
def api_board_list():
    """List all saved board profiles."""
    from board_profile import BoardProfile
    profiles = BoardProfile.list_profiles()
    active = _board_profile.name if _board_profile.is_registered else None
    return {"profiles": profiles, "active": active}


@app.route("/api/board/select", methods=["POST"])
def api_board_select():
    """Load a specific board profile as active."""
    from flask import request
    data = request.get_json()
    name = data.get("name")
    if not name:
        return {"error": "Name required"}, 400
    if _board_profile.load(name):
        return {"ok": True, "name": name, "features": int(len(_board_profile.ref_kp_pts))}
    return {"error": f"Profile '{name}' not found"}, 404


@app.route("/api/board/delete", methods=["POST"])
def api_board_delete():
    """Delete a board profile."""
    from flask import request
    from board_profile import BoardProfile
    data = request.get_json()
    name = data.get("name")
    if not name:
        return {"error": "Name required"}, 400
    if BoardProfile.delete_profile(name):
        # If we deleted the active profile, clear it
        if _board_profile.name == name:
            _board_profile.name = None
            _board_profile.ref_gray = None
        return {"ok": True}
    return {"error": f"Profile '{name}' not found"}, 404


@app.route("/api/board/status")
def api_board_status():
    """Return board registration status."""
    return {
        "registered": _board_profile.is_registered,
        "active": _board_profile.name,
        "features": int(len(_board_profile.ref_kp_pts)) if _board_profile.ref_kp_pts is not None else 0,
    }


@app.route("/api/cal/preview/<int:cam_id>")
def api_cal_preview(cam_id):
    """Return a warped homography preview JPEG for the given calibration points."""
    import cv2
    import numpy as np
    from flask import request, Response

    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400
    det = _detectors[cam_id]
    if not det.active:
        return {"error": f"Camera {cam_id} is not active"}, 400

    frame = det._grab()
    if frame is None:
        return {"error": "Failed to capture frame"}, 500

    # Get points from query string (comma-sep x1,y1,x2,y2,...x4,y4)
    pts_str = request.args.get("pts")
    if not pts_str:
        return {"error": "pts parameter required"}, 400
    try:
        vals = [float(v) for v in pts_str.split(",")]
        assert len(vals) == 8
        src = np.array(vals, dtype=np.float32).reshape(4, 2)
    except Exception:
        return {"error": "Invalid points format"}, 400

    # Compute warped using same dst_pts as BoardCalibrator
    cal = _calibrators[cam_id]
    M = cv2.getPerspectiveTransform(src, cal._dst_pts)
    board_size = min(cal.w, cal.h)
    warped = cv2.warpPerspective(frame, M, (board_size, board_size))

    _, buf = cv2.imencode('.jpg', warped, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(buf.tobytes(), mimetype='image/jpeg')

# ── Board Annotation (training-data collection) ──────────────────────────────

@app.route("/api/board-annotate/preview", methods=["POST"])
def api_board_annotate_preview():
    """Return a JPEG with perspective-correct dartboard wireframe overlay.

    Accepts 4 calibration src_points ([[x,y]×4]) and an optional
    dart_tip [x, y] in camera-space pixels.
    """
    import cv2
    from flask import request, Response
    from board_annotator import BoardAnnotation

    data = request.get_json()
    cam_id = data.get("cam_id", 0)
    src_points = data.get("src_points")   # [[x,y], [x,y], [x,y], [x,y]]
    dart_tip = data.get("dart_tip")       # [x, y] or None

    if not src_points or len(src_points) != 4:
        return {"error": "Need exactly 4 src_points"}, 400
    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400

    det = _detectors[cam_id]
    if not det.active or det.last_frame is None:
        return {"error": "Camera not active"}, 400

    frame = det.last_frame.copy()
    h, w = frame.shape[:2]
    ann = BoardAnnotation(src_points, frame_w=w, frame_h=h)
    overlay = ann.draw_wireframe(frame, dart_tip=dart_tip)

    _, buf = cv2.imencode('.jpg', overlay, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(buf.tobytes(), mimetype='image/jpeg')


@app.route("/api/board-annotate/save", methods=["POST"])
def api_board_annotate_save():
    """Save annotated camera frame + label as training data.

    Accepts 4 calibration src_points ([[x,y]×4]).
    Optional dart_tip [x, y] in camera-space pixels; if omitted the server
    auto-detects the tip from the detector's last dart_tip (warped→raw).
    """
    import numpy as np
    from flask import request
    from board_annotator import BoardAnnotation, save_annotation

    data = request.get_json()
    cam_id = data.get("cam_id", 0)
    src_points = data.get("src_points")   # [[x,y]×4]
    dart_tip = data.get("dart_tip")       # [x, y] in cam px, or None

    if not src_points or len(src_points) != 4:
        return {"error": "Need exactly 4 src_points"}, 400
    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400

    det = _detectors[cam_id]
    if not det.active or det.last_frame is None:
        return {"error": "Camera not active"}, 400

    # Auto-detect dart tip: inverse-project warped tip → raw cam space
    if dart_tip is None and det.dart_tip is not None and det.cal.matrix_inv is not None:
        import cv2 as _cv2
        wp = np.array([[[det.dart_tip[0], det.dart_tip[1]]]], dtype=np.float32)
        rp = _cv2.perspectiveTransform(wp, det.cal.matrix_inv)
        dart_tip = [round(float(rp[0, 0, 0]), 1), round(float(rp[0, 0, 1]), 1)]

    h, w = det.last_frame.shape[:2]
    ann = BoardAnnotation(src_points, frame_w=w, frame_h=h)
    lbl_path = save_annotation(det.last_frame, ann, cam_id,
                               dart_tip=dart_tip)

    # Count total annotations
    from pathlib import Path
    count = len(list(Path("board_annotations/labels").glob("*.json")))

    return {"ok": True, "label_path": lbl_path, "total_annotations": count}



@app.route("/api/board-annotate/count")
def api_board_annotate_count():
    """Return the number of saved board annotations."""
    from pathlib import Path
    lbl_dir = Path("board_annotations/labels")
    count = len(list(lbl_dir.glob("*.json"))) if lbl_dir.exists() else 0
    return {"count": count}


# ── Debug Screenshot ─────────────────────────────────────────────────────────

@app.route("/api/debug/screenshot", methods=["POST"])
def api_debug_screenshot():
    """Save current camera frames + user-marked tip position for training."""
    import cv2, json, os
    from datetime import datetime

    data = request.get_json(silent=True) or {}
    tip_x = data.get("tip_x")          # user-clicked tip in warped-frame coords
    tip_y = data.get("tip_y")
    cam_id = data.get("cam_id", 0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("debug_screenshots", ts)
    os.makedirs(out_dir, exist_ok=True)

    saved = []
    # Save frames from the specific camera the user annotated
    if cam_id < len(_detectors):
        det = _detectors[cam_id]
        if det.active and det.last_frame is not None:
            raw_path = os.path.join(out_dir, f"cam{cam_id}_raw.jpg")
            cv2.imwrite(raw_path, det.last_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            saved.append(raw_path)
        if det.warped_frame is not None:
            warp_path = os.path.join(out_dir, f"cam{cam_id}_warped.jpg")
            cv2.imwrite(warp_path, det.warped_frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            saved.append(warp_path)
            # Draw the user-marked tip on a copy for visual reference
            if tip_x is not None and tip_y is not None:
                marked = det.warped_frame.copy()
                px = int(tip_x)
                py = int(tip_y)
                cv2.drawMarker(marked, (px, py), (0, 0, 255),
                               cv2.MARKER_CROSS, 20, 2)
                cv2.circle(marked, (px, py), 6, (0, 255, 0), -1)
                marked_path = os.path.join(out_dir, f"cam{cam_id}_marked.jpg")
                cv2.imwrite(marked_path, marked, [cv2.IMWRITE_JPEG_QUALITY, 95])
                saved.append(marked_path)

    # Save metadata with tip position, last score, and detected tip
    meta = {
        "timestamp": ts,
        "cam_id": cam_id,
        "user_tip": [tip_x, tip_y] if tip_x is not None else None,
        "last_score": _last_score,
    }
    # Include the detector's own tip for comparison
    if cam_id < len(_detectors):
        det = _detectors[cam_id]
        if det.dart_tip is not None:
            meta["detected_tip"] = list(det.dart_tip)
        meta["detected_method"] = getattr(det, "dart_tip_method", "NONE")

    meta_path = os.path.join(out_dir, "meta.json")
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2, default=str)
    saved.append(meta_path)

    print(f"[DBG] Saved inaccuracy report to {out_dir}/ ({len(saved)} files)")
    return jsonify({"ok": True, "path": out_dir, "files": len(saved)})


# ── MJPEG Camera Streams ─────────────────────────────────────────────────────

def _gen_mjpeg(cam_id: int, warped: bool = False):
    """Yield JPEG frames as an MJPEG stream (~10 fps)."""
    import cv2
    while True:
        if cam_id < 0 or cam_id >= len(_detectors):
            break
        det = _detectors[cam_id]
        if not det.active:
            time.sleep(0.5)
            continue
        frame = det.warped_frame if warped else det.last_frame
        if frame is None:
            time.sleep(0.1)
            continue

        # Draw dart tip overlays on warped view
        if warped:
            frame = frame.copy()
            cal = _calibrators[cam_id] if cam_id < len(_calibrators) else None
            # Dartboard wireframe — draw directly (fast, no extra copy)
            if cal and cal.is_calibrated:
                wf = cal.get_wireframe_primitives()
                colour = (255, 255, 0)
                for r_px in wf['circles']:
                    cv2.circle(frame, wf['center'], r_px, colour, 1, cv2.LINE_AA)
                for (x1, y1), (x2, y2) in wf['lines']:
                    cv2.line(frame, (x1, y1), (x2, y2), colour, 1, cv2.LINE_AA)
            # Previously scored tips — convert mm to warped-pixel coords
            if cal:
                cx_w = cy_w = cal.board_size // 2
                for x_mm, y_mm in _consensus_scored_tips:
                    px, py = cal._mm_to_px(x_mm, y_mm, cx_w, cy_w)
                    pt = (int(px), int(py))
                    cv2.circle(frame, pt, 6, (0, 200, 0), -1)
                    cv2.circle(frame, pt, 7, (255, 255, 255), 1)
            # Latest detected tip — bright red crosshair
            if det.dart_tip is not None:
                tx, ty = int(det.dart_tip[0]), int(det.dart_tip[1])
                cv2.circle(frame, (tx, ty), 10, (0, 0, 255), 2)
                cv2.line(frame, (tx - 16, ty), (tx + 16, ty), (0, 0, 255), 2)
                cv2.line(frame, (tx, ty - 16), (tx, ty + 16), (0, 0, 255), 2)
                cv2.circle(frame, (tx, ty), 3, (0, 255, 255), -1)

        # Scale down for stream to save CPU and bandwidth
        if frame.shape[0] > 600 or frame.shape[1] > 600:
            s = 600 / max(frame.shape[0], frame.shape[1])
            frame = cv2.resize(frame, (0, 0), fx=s, fy=s)

        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
        time.sleep(0.1)  # ~10 fps


@app.route("/api/stream/raw/<int:cam_id>")
def stream_raw(cam_id):
    """MJPEG stream of raw camera feed."""
    return Response(_gen_mjpeg(cam_id, warped=False),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route("/api/stream/warped/<int:cam_id>")
def stream_warped(cam_id):
    """MJPEG stream of warped (homography-transformed) view."""
    return Response(_gen_mjpeg(cam_id, warped=True),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ── Socket.IO events ─────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    # Send current snapshot to newly connected client
    if _cam_states:
        socketio.emit("cam_status", _cam_states, to=None)
    if _last_score:
        socketio.emit("dart_scored", _last_score, to=None)


@socketio.on("test_dart")
def on_test_dart(data):
    """Allows manual injection of a dart event for UI testing."""
    _emit_dart(data.get("label", "T20"),
               data.get("score", 60),
               data.get("x_mm", 0.0),
               data.get("y_mm", 170.0))


@socketio.on("start_detection")
def on_start_detection():
    global _detection_paused
    # Auto-open cameras if not already open
    if not _cameras_open:
        _do_open_cameras()
    _detection_paused = False
    print("[SRV] Detection RESUMED by client.")
    socketio.emit("detection_state", {"paused": False})


@socketio.on("stop_detection")
def on_stop_detection():
    global _detection_paused
    _detection_paused = True
    print("[SRV] Detection PAUSED by client.")
    socketio.emit("detection_state", {"paused": True})


@socketio.on("open_cameras")
def on_open_cameras():
    """Open cameras on demand (calibration, preview, debug)."""
    if not _cameras_open:
        _do_open_cameras()


@socketio.on("close_cameras")
def on_close_cameras():
    """Release cameras when no feature needs them."""
    global _detection_paused
    _detection_paused = True
    _do_close_cameras()


def _do_open_cameras():
    """Open all cameras, warmup, and start reader threads."""
    global _cameras_open
    import cv2
    with _cameras_lock:
        if _cameras_open:
            return
        print("[SRV] Opening cameras…")
        for i, det in enumerate(_detectors):
            if i > 0:
                time.sleep(0.5)
            ok = det.open_camera()
            _cam_states[det.cam_id] = {
                "state": "ACTIVE" if ok else "OFFLINE",
                "fps": 0.0,
                "active": ok,
            }
        _emit_cam_status()

        active = [d for d in _detectors if d.active]
        if not active:
            print("[SRV] No cameras available.")
            return

        for det in active:
            det.start_reader()
        time.sleep(0.3)

        # Quick warmup (2 s instead of 5)
        print("[SRV] Warming up cameras (2 s)…")
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 1.5:
            for det in _detectors:
                det._grab()
        for det in _detectors:
            det.capture_reference()
        time.sleep(0.5)
        for det in _detectors:
            det._grab()
            det.capture_reference()

        _cameras_open = True
        print("[SRV] Cameras ready.")
        socketio.emit("cameras_state", {"open": True})


def _do_close_cameras():
    """Release all cameras and stop reader threads."""
    global _cameras_open
    with _cameras_lock:
        if not _cameras_open:
            return
        print("[SRV] Closing cameras…")
        for det in _detectors:
            if det._reader is not None:
                det._reader.release()
                det._reader = None
            elif det.cap is not None:
                det.cap.release()
            det.cap = None          # always reset so open_camera() starts fresh
            det.active = False
            det.last_frame = None
            det.warped_frame = None
            _cam_states[det.cam_id] = {
                "state": "OFFLINE", "fps": 0.0, "active": False,
            }
        _cameras_open = False
        _emit_cam_status()
        print("[SRV] Cameras released.")
        socketio.emit("cameras_state", {"open": False})


@socketio.on("update_settings")
def on_update_settings(data):
    global _annotation_mode
    if isinstance(data, dict):
        _annotation_mode = bool(data.get("annotation_mode", False))
        print(f"[SRV] Annotation mode: {'ON' if _annotation_mode else 'OFF'}")
        socketio.emit("annotation_count", {"count": _annotation_count})


@socketio.on("clear_tips")
def on_clear_tips():
    """Clear scored tip markers from all cameras (removes green dots)."""
    for det in _detectors:
        det.clear_scored_tips()
    _consensus_scored_tips.clear()
    print("[SRV] Scored tips cleared (board reset)")


@socketio.on("save_annotation")
def on_save_annotation(data):
    """User clicked Save in annotation modal — write images + JSONL."""
    global _annotation_count, _pending_annotation
    import cv2

    with _state_lock:
        if _pending_annotation is None:
            print("[ANN] No pending annotation to save")
            return

        pa = _pending_annotation
        _pending_annotation = None

        ann_dir = Path("dart_annotations")
        img_dir = ann_dir / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = ann_dir / "labels.jsonl"

        _annotation_count += 1
        throw_id = _annotation_count

    actual_score = data.get('actual_score', '').strip()
    user_tips = data.get('tips', {})  # {"0": {"0": [x,y], ...}, "1": ...}
    num_darts = data.get('num_darts', 1)

    entry = {
        "id": throw_id,
        "timestamp": datetime.now().isoformat(),
        "cv_score": pa['label'],
        "cv_score_val": pa['score'],
        "num_darts": num_darts,
    }
    if actual_score:
        entry["actual_score"] = actual_score
        entry["cv_correct"] = actual_score.upper() == pa['label'].upper()
    else:
        entry["actual_score"] = pa['label']
        entry["cv_correct"] = True

    detectors = pa['detectors']
    for i, det in enumerate(detectors):
        cam_key = f"cam{i}"
        fname = f"throw_{throw_id:04d}_cam{i}.png"

        if det.active and det.last_frame is not None:
            cv2.imwrite(str(img_dir / fname), det.last_frame)
            entry[f"{cam_key}_image"] = fname

        # User-clicked tips
        cam_tips = user_tips.get(str(i), {})
        if cam_tips:
            tips_list = []
            for dart_n in sorted(cam_tips.keys(), key=int):
                pos = cam_tips[dart_n]
                tips_list.append({
                    "dart": int(dart_n) + 1,
                    "tip": [round(pos[0], 1), round(pos[1], 1)],
                })
            entry[f"{cam_key}_tips"] = tips_list

        # CV tip
        tip = pa['collected_tips'][i] if i < len(pa['collected_tips']) else None
        method = pa['collected_methods'][i] if i < len(pa['collected_methods']) else 'NONE'
        if tip is not None:
            entry[f"{cam_key}_cv_tip"] = [round(tip[0], 1), round(tip[1], 1)]
        entry[f"{cam_key}_method"] = method

    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    print(f"[ANN] Saved annotation #{throw_id} ({num_darts} dart(s), "
          f"score={actual_score or pa['label']})")
    socketio.emit("annotation_count", {"count": _annotation_count})


@socketio.on("skip_annotation")
def on_skip_annotation():
    """User clicked Skip — discard without saving."""
    global _pending_annotation
    with _state_lock:
        _pending_annotation = None
    print("[ANN] Annotation skipped")


# ── Game Mode Socket.IO Events ──────────────────────────────────────────────

@socketio.on("start_bullseye")
def on_start_bullseye(data=None):
    """Begin bullseye throw sequence to determine first player."""
    global _game_mode, _bullseye, _game, _detection_paused
    global _game_pending_mode, _game_pending_opts
    global _awaiting_takeout, _takeout_hand_seen

    _game_pending_mode = data.get("mode", "x01") if data else "x01"
    _game_pending_opts = data.get("options", {}) if data else {}
    _bullseye = BullseyeThrow()
    _game = None
    _game_mode = "bullseye"

    # Clear stale state from previous sessions
    _awaiting_takeout = False
    _takeout_hand_seen = False
    for det in _detectors:
        det.clear_scored_tips()
        if det.active:
            det.reset_to_wait()
    _consensus_scored_tips.clear()

    # Auto-open cameras and start detection
    if not _cameras_open:
        _do_open_cameras()
    _detection_paused = False
    socketio.emit("detection_state", {"paused": False})

    state = _bullseye.start()
    socketio.emit("bullseye_state", state)
    print(f"[GAME] Bullseye throw started (pending mode: {_game_pending_mode})")


@socketio.on("start_game")
def on_start_game(data):
    """Start a game directly (skip bullseye if desired)."""
    global _game_mode, _game, _bullseye, _detection_paused

    mode = data.get("mode", "x01")
    options = data.get("options", {})
    first_player = data.get("first_player", 1)

    _bullseye = None
    _game = _create_game(mode, options)
    if _game is None:
        socketio.emit("game_state", {"error": f"Unknown mode: {mode}"})
        return

    _game.set_first_player(first_player)
    _game_mode = mode

    # Auto-open cameras and start detection
    if not _cameras_open:
        _do_open_cameras()
    _detection_paused = False
    socketio.emit("detection_state", {"paused": False})

    socketio.emit("game_state", _game.state())
    print(f"[GAME] {mode.upper()} game started (first player: {first_player})")


@socketio.on("undo_dart")
def on_undo_dart():
    """Undo the last dart in the current game."""
    if _game is not None and not _game.is_finished:
        state = _game.undo_dart()
        socketio.emit("game_state", state)
        print("[GAME] Dart undone")


@socketio.on("end_game")
def on_end_game():
    """End the current game and return to idle."""
    global _game_mode, _game, _bullseye, _awaiting_takeout, _takeout_hand_seen
    if _game is not None and _game.is_finished:
        try:
            game_stats.save_game(_game.stats_summary())
        except Exception as e:
            print(f"[STATS] Error saving on end: {e}")
    _game_mode = None
    _game = None
    _bullseye = None
    _awaiting_takeout = False
    _takeout_hand_seen = False
    # Clear scored tips so next session starts clean
    for det in _detectors:
        det.clear_scored_tips()
    _consensus_scored_tips.clear()
    socketio.emit("game_state", {"type": "idle"})
    print("[GAME] Game ended")


@socketio.on("skip_takeout")
def on_skip_takeout():
    """User clicked Continue — skip automatic takeout detection."""
    global _awaiting_takeout, _takeout_hand_seen, _takeout_reason
    if not _awaiting_takeout:
        return
    # Clear all scored tips manually
    for det in _detectors:
        det.clear_scored_tips()
        if det.active:
            det.capture_reference()
            det.reset_to_wait()
    _consensus_scored_tips.clear()

    if _takeout_reason == 'bullseye':
        print("[GAME] Takeout skipped by user — tips cleared, starting game")
        _do_start_pending_game()
    else:
        # Between-turn takeout — just resume scoring
        print("[GAME] Turn takeout completed by user — resuming")
        _awaiting_takeout = False
        _takeout_hand_seen = False
        _takeout_reason = ''
        socketio.emit('state', {'state': 'WAIT'})


@socketio.on("get_stats")
def on_get_stats(data=None):
    """Return statistics for a game mode."""
    mode = data.get("mode") if data else None
    stats = game_stats.get_stats(mode)
    socketio.emit("stats_data", stats)


@socketio.on("get_recent_games")
def on_get_recent_games(data=None):
    """Return recent game history."""
    mode = data.get("mode") if data else None
    limit = data.get("limit", 20) if data else 20
    recent = game_stats.get_recent(mode, limit)
    socketio.emit("recent_games", {"games": recent})


def _create_game(mode: str, options: dict):
    """Factory for game instances."""
    if mode == "x01":
        starting = int(options.get("starting_score", 501))
        return GameX01(starting_score=starting)
    elif mode == "cricket":
        return GameCricket()
    elif mode == "countup":
        rounds = int(options.get("total_rounds", 8))
        return GameCountUp(total_rounds=rounds)
    return None


_awaiting_takeout: bool = False  # True = waiting for darts to be removed before game start
_takeout_hand_seen: bool = False  # True = hand was detected during takeout wait
_takeout_ready_at: float = 0.0   # timestamp after which hand detection starts for takeout
_takeout_reason: str = ''        # 'bullseye' or 'turn'

def _start_pending_game(winner: int):
    """Called after bullseye throw completes — waits for takeout before starting."""
    global _awaiting_takeout, _pending_game_winner, _takeout_hand_seen, _takeout_ready_at, _takeout_reason
    _pending_game_winner = winner
    _awaiting_takeout = True
    _takeout_hand_seen = False
    _takeout_ready_at = time.time() + 3.0  # 3 second cooldown before looking for hand
    _takeout_reason = 'bullseye'
    socketio.emit("awaiting_takeout", {"message": "Remove darts from the board to start the game!"})
    print(f"[GAME] Awaiting takeout before starting game (winner: Player {winner})")


def _do_start_pending_game():
    """Actually create and start the game after takeout confirmed."""
    global _game_mode, _game, _bullseye, _awaiting_takeout, _takeout_hand_seen
    mode = _game_pending_mode or "x01"
    opts = _game_pending_opts or {}
    _game = _create_game(mode, opts)
    if _game is None:
        _game_mode = None
        return
    _game.set_first_player(_pending_game_winner)
    _game_mode = mode
    _bullseye = None
    _awaiting_takeout = False
    _takeout_hand_seen = False
    socketio.emit("game_state", _game.state())
    print(f"[GAME] {mode.upper()} game started (winner of bullseye: Player {_pending_game_winner})")


# ── Stats API endpoints ─────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    mode = request.args.get("mode")
    return jsonify(game_stats.get_stats(mode))


@app.route("/api/stats/recent")
def api_stats_recent():
    mode = request.args.get("mode")
    limit = int(request.args.get("limit", 20))
    return jsonify({"games": game_stats.get_recent(mode, limit)})


# ── Helpers called by the detection thread ───────────────────────────────────

def _emit_dart(label: str, score: int, x_mm: float, y_mm: float,
               cam_details: list | None = None):
    global _last_score, _game_mode, _bullseye, _game
    global _awaiting_takeout, _takeout_hand_seen, _takeout_ready_at, _takeout_reason
    payload = {"label": label, "score": score, "x_mm": x_mm, "y_mm": y_mm,
               "ts": time.time()}
    if cam_details:
        payload["cam_details"] = cam_details
    with _state_lock:
        _last_score = payload

    # ── Route through active game mode ──
    # Ignore darts while waiting for takeout (residual detections)
    if _awaiting_takeout:
        return

    if _game_mode == 'bullseye' and _bullseye is not None:
        import math
        dist = math.sqrt(x_mm ** 2 + y_mm ** 2)
        state = _bullseye.record_dart(label, score, (x_mm, y_mm), dist)
        socketio.emit('bullseye_state', state)
        if _bullseye.is_finished:
            socketio.emit('bullseye_result', state)
            _start_pending_game(state.get('winner', 1))
        return  # don't emit dart_scored during bullseye

    if _game is not None and not _game.is_finished:
        prev_player = _game.current_player
        game_state = _game.record_dart(label, score, (x_mm, y_mm))
        socketio.emit('game_state', game_state)
        if _game.is_finished:
            socketio.emit('game_over', game_state)
            # Save stats
            try:
                game_stats.save_game(_game.stats_summary())
            except Exception as e:
                print(f'[STATS] Error saving: {e}')
        elif _game.current_player != prev_player:
            # Turn ended — need takeout before next player throws
            _awaiting_takeout = True
            _takeout_hand_seen = False
            _takeout_ready_at = time.time() + 2.0  # 2 second cooldown
            _takeout_reason = 'turn'
            socketio.emit('turn_takeout', {'message': 'Remove darts from the board!'})
            print(f"[GAME] Turn ended — awaiting takeout before Player {_game.current_player + 1} throws")
        # Also emit dart_scored for board dot placement
        socketio.emit('dart_scored', payload)
        return

    # No active game — normal practice mode
    socketio.emit("dart_scored", payload)


def _emit_cam_status():
    with _state_lock:
        states_copy = _cam_states.copy()
    socketio.emit("cam_status", states_copy)


def _emit_log(msg: str):
    """Push a log line to connected browsers."""
    socketio.emit("server_log", {"msg": msg, "ts": time.time()})


def _send_annotation_prompt(label, score, coord, detectors,
                            collected_tips, collected_methods):
    """Send camera frames to frontend for interactive annotation."""
    global _annotation_count, _pending_annotation
    if not _annotation_mode:
        return

    import cv2
    import base64
    import numpy as np

    frames_b64 = []
    cv_tips_raw = []

    for i, det in enumerate(detectors):
        if det.active and det.last_frame is not None:
            # Encode as JPEG base64
            _, buf = cv2.imencode('.jpg', det.last_frame,
                                 [cv2.IMWRITE_JPEG_QUALITY, 90])
            b64 = base64.b64encode(buf).decode('ascii')
            frames_b64.append(b64)

            # CV-detected tip in raw cam coords
            if det.dart_tip is not None:
                tp = np.array([[[det.dart_tip[0], det.dart_tip[1]]]],
                              dtype=np.float32)
                raw_pt = cv2.perspectiveTransform(tp, det.cal.matrix_inv)
                rx, ry = float(raw_pt[0, 0, 0]), float(raw_pt[0, 0, 1])
                cv_tips_raw.append([round(rx, 1), round(ry, 1)])
            else:
                cv_tips_raw.append(None)
        else:
            frames_b64.append(None)
            cv_tips_raw.append(None)

    # Get frame dimensions for coordinate scaling
    frame_sizes = []
    for det in detectors:
        if det.active and det.last_frame is not None:
            h, w = det.last_frame.shape[:2]
            frame_sizes.append([w, h])
        else:
            frame_sizes.append(None)

    # Store pending data for when user clicks Save
    with _state_lock:
        _pending_annotation = {
            'label': label,
            'score': score,
            'coord': coord,
            'detectors': detectors,
            'collected_tips': collected_tips,
            'collected_methods': collected_methods,
            'frame_sizes': frame_sizes,
        }
        current_count = _annotation_count

    payload = {
        'frames': frames_b64,
        'cv_label': label,
        'cv_score': score,
        'cv_tips': cv_tips_raw,
        'methods': list(collected_methods),
        'frame_sizes': frame_sizes,
        'annotation_count': current_count,
    }

    print(f"[ANN] Sending annotation prompt (score={label})")
    socketio.emit('annotation_prompt', payload)

_pending_annotation = None


class _TeeStdout:
    """Intercept stdout: echo to real terminal AND push to Socket.IO."""
    def __init__(self, real):
        self._real = real
        self._buf = ""

    def write(self, s):
        self._real.write(s)
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                try:
                    _emit_log(line)
                except Exception:
                    pass

    def flush(self):
        self._real.flush()

    def __getattr__(self, name):
        return getattr(self._real, name)


import sys
sys.stdout = _TeeStdout(sys.stdout)


# ── Detection thread ──────────────────────────────────────────────────────────

def _run_detection(cam_ids: List[int], cfg) -> None:
    """Mirrors the logic of main.run() but emits events instead of cv2.imshow."""
    global _takeout_hand_seen, _takeout_ready_at
    import cv2
    import numpy as np
    from calibrator import BoardCalibrator
    from detector import DartDetector, State
    from scorer import ScoreMapper

    global _detectors, _calibrators, _cfg, _annotation_count
    _cfg = cfg

    # Count existing annotations to continue numbering
    jsonl_path = Path("dart_annotations/labels.jsonl")
    if jsonl_path.exists():
        _annotation_count = sum(1 for _ in open(jsonl_path, encoding="utf-8") if _.strip())

    # ── Calibrators ──────────────────────────────────────────────────────────
    calibrators = []
    for cid in cam_ids:
        cal = BoardCalibrator(cfg, cam_id=cid)
        if not cal.load_cached():
            print(f"[SRV] Camera {cid}: no calibration cache. "
                  f"Run  python main.py --calibrate  first.")
        calibrators.append(cal)
    _calibrators = calibrators

    # ── Detectors ────────────────────────────────────────────────────────────
    detectors: List[DartDetector] = []
    for cid, cal in zip(cam_ids, calibrators):
        detectors.append(DartDetector(cid, cfg, cal))
    _detectors = detectors

    # ── Cameras stay OFF until explicitly opened ──────────────────────────────
    # Set initial cam states to OFFLINE
    for det in detectors:
        _cam_states[det.cam_id] = {
            "state": "OFFLINE", "fps": 0.0, "active": False,
        }
    _emit_cam_status()
    scorer = ScoreMapper(cfg, calibrators)

    COLLECT_SECONDS = 1.4   # wider window — gives all 3 cameras time to detect
    MIN_DART_AREA   = 100
    HEALTH_LOG_INTERVAL = 60.0  # seconds between per-camera health reports
    collect_deadline: float = 0.0
    _last_health_log: float = 0.0
    _hand_was_active: bool = False  # track HAND→WAIT transition for frontend
    collected_tips    = [None]   * len(detectors)
    collected_areas   = [0]      * len(detectors)
    collected_methods = ['NONE'] * len(detectors)
    collected_mm      = [None]   * len(detectors)  # direct raw→mm coords

    while True:
        # When cameras are closed, just sleep
        if not _cameras_open:
            time.sleep(0.1)
            continue

        # When paused, still grab frames (keep cameras alive) but skip detection
        if _detection_paused:
            for det in detectors:
                if det.active:
                    det._grab()
            time.sleep(0.05)
            continue

        active_dets = [d for d in detectors if d.active]

        states = [det.step() for det in detectors]
        now = time.perf_counter()

        # ── Update camera status every ~30 frames ─────────────────────────
        for det in detectors:
            cid = det.cam_id
            fps = det.camera_fps if det.camera_fps > 0 else cfg.fps
            new_state = det.state.name if det.active else "OFFLINE"
            if _cam_states.get(cid, {}).get("state") != new_state or \
               abs(_cam_states.get(cid, {}).get("fps", 0) - fps) > 1:
                _cam_states[cid] = {
                    "state": new_state,
                    "fps": round(fps, 1),
                    "active": det.active,
                }
                _emit_cam_status()

        # ── Periodic per-camera health report ────────────────────────────
        if now - _last_health_log >= HEALTH_LOG_INTERVAL:
            _last_health_log = now
            for det in detectors:
                grab_rate = (det._health_grab_fails /
                             max(1, det._health_frames + det._health_grab_fails)
                             ) * 100
                motion_rate = (det._health_motion_hits /
                               max(1, det._health_darts)) * 100
                print(f"[HEALTH] Cam {det.cam_id}: "
                      f"frames={det._health_frames} "
                      f"darts={det._health_darts} "
                      f"grabs_failed={det._health_grab_fails} "
                      f"({grab_rate:.1f}%) "
                      f"motion_hits={det._health_motion_hits} "
                      f"({motion_rate:.0f}% of darts) "
                      f"active={det.active}")

        # ── Dart collection ───────────────────────────────────────────────
        for i, det in enumerate(detectors):
            if det.state == State.DART and det.dart_tip is not None:
                if det.dart_area >= MIN_DART_AREA:
                    collected_tips[i]    = det.dart_tip
                    collected_areas[i]   = det.dart_area
                    collected_methods[i] = det.dart_tip_method
                    collected_mm[i]      = det.dart_tip_mm  # may be None for non-YOLO

        any_dart = any(d.state == State.DART for d in active_dets)
        if any_dart and collect_deadline == 0.0:
            collect_deadline = now + COLLECT_SECONDS
            socketio.emit('state', {'state': 'STABLE'})

        if collect_deadline > 0.0 and now >= collect_deadline:
            # ── Opportunistic scan ────────────────────────────────────────
            # For every active camera that hasn't contributed a tip yet
            # (i.e. didn't reach DART state in time), run a one-shot YOLO
            # scan on its current frame.  This ensures all cameras always
            # participate in locating the dart tip.
            for i, det in enumerate(detectors):
                if (det.active
                        and collected_tips[i] is None
                        and det.state not in (State.HAND, State.TAKEOUT)):
                    scan = det.try_yolo_scan(scored_mm=_consensus_scored_tips)
                    if scan is not None:
                        s_x, s_y, s_method = scan
                        collected_tips[i]    = (0.0, 0.0)  # placeholder (mm used directly)
                        collected_areas[i]   = 300
                        collected_methods[i] = s_method
                        collected_mm[i]      = (s_x, s_y)
                        print(f"[DET] Cam {i}: opportunistic {s_method} "
                              f"-> ({s_x:.1f},{s_y:.1f})mm")
                    else:
                        print(f"[DET] Cam {i}: opportunistic YOLO scan "
                              f"FAILED (state={det.state.name} "
                              f"motion={det.motion_change}px "
                              f"calibrated={det.cal.is_calibrated})")

            n_cams   = sum(1 for t in collected_tips if t is not None)
            n_active = sum(1 for d in detectors if d.active)
            min_cams = 2 if n_active >= 2 else 1
            did_score = False

            def _build_cam_details():
                """Build per-camera breakdown from scorer state."""
                details = []
                used_set = set()
                if scorer.last_tips_mm:
                    for ci, mm in scorer.last_tips_mm:
                        used_set.add(ci)
                for i in range(len(detectors)):
                    if collected_tips[i] is not None:
                        if collected_mm[i] is not None:
                            mm = collected_mm[i]
                        else:
                            mm = scorer.tip_to_board_mm(i, collected_tips[i])
                        r, theta = scorer.to_polar(mm[0], mm[1])
                        lbl, sc = scorer.score_from_polar(r, theta)
                        details.append({
                            "cam": i,
                            "label": lbl, "score": sc,
                            "x_mm": round(mm[0], 1),
                            "y_mm": round(mm[1], 1),
                            "r_mm": round(r, 1),
                            "area": collected_areas[i],
                            "method": collected_methods[i],
                            "used": i in used_set,
                        })
                    else:
                        details.append({
                            "cam": i, "label": None, "score": None,
                            "x_mm": None, "y_mm": None, "r_mm": None,
                            "area": 0, "method": "NONE", "used": False,
                        })
                return details

            # ── Cross-camera mask intersection ─────────────────────────
            # Warp each camera's raw diff mask to board space and AND
            # them.  Shaft pixels (above surface) project to different
            # board-space positions per camera → cancel out.  Tip pixels
            # (on surface) project consistently → survive.
            cross_tip_mm = None
            if n_cams >= 2:
                board_masks = []
                mask_cam_ids = []
                for i, det in enumerate(detectors):
                    if collected_tips[i] is not None:
                        mask_b = det.get_dart_mask_board()
                        if mask_b is not None:
                            board_masks.append(mask_b)
                            mask_cam_ids.append(i)

                if len(board_masks) >= 2:
                    # Dilate each mask for calibration alignment tolerance
                    _xcam_kern = np.ones((7, 7), np.uint8)
                    dilated = [cv2.dilate(m, _xcam_kern, iterations=1)
                               for m in board_masks]

                    # Normalise to 0/1, sum, threshold at N-1 agreement
                    normed = [(d // 255).astype(np.uint16) for d in dilated]
                    vote_map = normed[0].copy()
                    for n in normed[1:]:
                        vote_map += n
                    n_agree = max(2, len(board_masks) - 1)
                    intersection = np.where(
                        vote_map >= n_agree, 255, 0).astype(np.uint8)

                    contours_xc, _ = cv2.findContours(
                        intersection, cv2.RETR_EXTERNAL,
                        cv2.CHAIN_APPROX_SIMPLE)

                    if contours_xc:
                        biggest = max(contours_xc, key=cv2.contourArea)
                        M_xc = cv2.moments(biggest)
                        if M_xc['m00'] > 0:
                            cx_b = M_xc['m10'] / M_xc['m00']
                            cy_b = M_xc['m01'] / M_xc['m00']
                            # board px → mm
                            cal0 = calibrators[0]
                            x_mm, y_mm = cal0.board_px_to_mm(cx_b, cy_b)
                            r_mm = float(np.hypot(x_mm, y_mm))
                            area_xc = cv2.contourArea(biggest)
                            if r_mm <= 180.0 and area_xc >= 10:
                                cross_tip_mm = (x_mm, y_mm)
                                print(f"[Xcam] Cross-camera tip: "
                                      f"({x_mm:+.1f},{y_mm:+.1f})mm "
                                      f"r={r_mm:.1f} area={area_xc:.0f}px "
                                      f"from {len(board_masks)} cams "
                                      f"({mask_cam_ids})")
                            else:
                                print(f"[Xcam] Intersection off-board or "
                                      f"too small (r={r_mm:.1f} "
                                      f"area={area_xc:.0f}) — skipping")
                    else:
                        print(f"[Xcam] No contours in intersection "
                              f"of {len(board_masks)} masks")

            if n_cams >= min_cams:
                label, score, coord = scorer.consensus(
                    collected_tips, collected_areas, collected_methods,
                    mm_coords_direct=collected_mm,
                    cross_camera_mm=cross_tip_mm)
                if score >= 0:
                    if label != 'OFF' and score > 0:
                        cam_details = _build_cam_details()
                        scorer.broadcast(label, score, coord)
                        _emit_dart(label, score, coord[0], coord[1], cam_details)
                        did_score = True
                        _send_annotation_prompt(label, score, coord, detectors,
                                                collected_tips, collected_methods)
                    else:
                        print(f"[SCR] Suppressed OFF/miss — not emitting")
                    # Record scored tips — use actual warped coords, not placeholders.
                    # Opportunistic scans set tips to (0,0) placeholder; use the
                    # consensus coord projected to warped space instead.
                    bs = detectors[0].cal.board_size if detectors else 800
                    sc = detectors[0].cal._scale if detectors else 1.0
                    cons_wx = coord[0] * sc + bs / 2
                    cons_wy = bs / 2 - coord[1] * sc
                    cons_warped = (cons_wx, cons_wy)
                    for i, tip in enumerate(collected_tips):
                        if tip is not None:
                            # Use real tip if it's a real detection, else consensus
                            real_tip = tip if tip != (0.0, 0.0) else cons_warped
                            for det in active_dets:
                                det.record_scored_tip(real_tip)
                    # Store consensus coordinate for overlay
                    _consensus_scored_tips.append(coord[:2])
            elif n_cams == 1:
                single_idx    = next(i for i, t in enumerate(collected_tips)
                                     if t is not None)
                single_area   = collected_areas[single_idx]
                single_method = collected_methods[single_idx]
                # When multiple cameras are active but only one detected,
                # require a much larger area for plain YOLO_BOX detections
                # (single-cam bbox-only is less reliable without cross-validation).
                # YOLO_MOTION is more reliable (motion-refined) so use lower threshold.
                if single_method == 'PROFILE':
                    area_thresh = 120
                elif single_method == 'YOLO_MOTION':
                    area_thresh = 300   # motion-refined tip is reliable
                elif single_method in ('YOLO_BOX', 'YOLO_SEG') and n_active >= 2:
                    area_thresh = 2000
                else:
                    area_thresh = 300
                if single_area >= area_thresh:
                    label, score, coord = scorer.consensus(
                        collected_tips, collected_areas, collected_methods,
                        mm_coords_direct=collected_mm,
                        cross_camera_mm=None)  # single-cam: no intersection
                    if score >= 0 and label != 'OFF' and score > 0:
                        cam_details = _build_cam_details()
                        scorer.broadcast(label, score, coord)
                        _emit_dart(label, score, coord[0], coord[1], cam_details)
                        did_score = True
                        _send_annotation_prompt(label, score, coord, detectors,
                                                collected_tips, collected_methods)
                        # Record scored tip — use consensus warped coords if placeholder
                        tip_to_record = collected_tips[single_idx]
                        if tip_to_record == (0.0, 0.0):
                            bs_ = detectors[0].cal.board_size if detectors else 800
                            sc_ = detectors[0].cal._scale if detectors else 1.0
                            tip_to_record = (coord[0] * sc_ + bs_ / 2,
                                             bs_ / 2 - coord[1] * sc_)
                        for det in active_dets:
                            det.record_scored_tip(tip_to_record)
                        _consensus_scored_tips.append(coord[:2])
                    print(f"[DET] Single camera (Cam {single_idx}, "
                          f"area={single_area} method={single_method}) — scored")
                else:
                    print(f"[DET] Only 1 camera detected "
                          f"(Cam {single_idx}, area={single_area} "
                          f"method={single_method}) "
                          f"— skipping (need 2+ or area>={area_thresh})")

            for det in active_dets:
                det.update_reference()
                det.reset_to_wait(with_cooldown=True)

            collected_tips    = [None]   * len(detectors)
            collected_areas   = [0]      * len(detectors)
            collected_methods = ['NONE'] * len(detectors)
            collected_mm      = [None]   * len(detectors)
            collect_deadline = 0.0
            if not did_score:
                socketio.emit('state', {'state': 'WAIT'})

        # ── TAKEOUT ───────────────────────────────────────────────────────
        # Only trigger if no camera detected DART and no collection pending
        any_takeout = any(s == State.TAKEOUT for s in states)
        any_dart_now = any(s == State.DART for s in states)
        if any_takeout and not any_dart_now and collect_deadline == 0.0:
            for det in active_dets:
                det.capture_reference()
                det.reset_to_wait()
                det.clear_scored_tips()
            _consensus_scored_tips.clear()
            collected_tips    = [None]   * len(detectors)
            collected_areas   = [0]      * len(detectors)
            collected_methods = ['NONE'] * len(detectors)
            collected_mm      = [None]   * len(detectors)
            collect_deadline = 0.0
            socketio.emit("takeout", {})
            # — user must click Continue

        # ── HAND — collective: if ANY camera sees hand, pause ALL ────────
        any_hand = any(d.state == State.HAND for d in active_dets)
        if any_hand:
            if _awaiting_takeout:
                # During takeout wait: DON'T update reference (keep darts-on-board ref)
                # Just reset to WAIT with short cooldown so detection resumes quickly
                for det in active_dets:
                    det.reset_to_wait(with_cooldown=True)
            else:
                # Normal mode: update reference to include the hand/changed board
                for det in active_dets:
                    det.update_reference()
                    det.reset_to_wait(with_cooldown=True)
            # Cancel any pending dart collection (hand could create false tips)
            collected_tips    = [None]   * len(detectors)
            collected_areas   = [0]      * len(detectors)
            collected_methods = ['NONE'] * len(detectors)
            collected_mm      = [None]   * len(detectors)
            collect_deadline = 0.0
            socketio.emit('state', {'state': 'HAND'})
            _hand_was_active = True
            # Only activate after the cooldown period to avoid false triggers from dart impact
            if _awaiting_takeout and not _takeout_hand_seen and time.time() >= _takeout_ready_at:
                _takeout_hand_seen = True
                print("[GAME] Hand detected during takeout wait")
        elif _hand_was_active:
            # Hand just left — tell frontend to return to Waiting state
            _hand_was_active = False
            socketio.emit('state', {'state': 'WAIT'})

        # ── Takeout-wait completion: hand was seen, board is settled ──────
        if _awaiting_takeout and _takeout_hand_seen and not any_hand:
            # Check if all cameras have settled back to WAIT
            all_wait = all(d.state == State.WAIT for d in active_dets)
            if all_wait:
                for det in active_dets:
                    det.clear_scored_tips()
                    det.capture_reference()
                    det.reset_to_wait()
                _consensus_scored_tips.clear()
                collected_tips    = [None]   * len(detectors)
                collected_areas   = [0]      * len(detectors)
                collected_methods = ['NONE'] * len(detectors)
                collected_mm      = [None]   * len(detectors)
                collect_deadline = 0.0
                print("[GAME] Takeout completed — darts removed, waiting for user to click Continue")
                # Tell frontend that takeout is done, show Continue button
                socketio.emit("takeout_ready", {})
                _takeout_hand_seen = False  # Reset so we don't re-trigger

        time.sleep(0.010)   # yield CPU (10ms to prevent 100% CPU lock)


# ── CLI & startup ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ThrowVision server")
    parser.add_argument("--demo",        action="store_true")
    parser.add_argument("--cameras",     type=str, default=None)
    parser.add_argument("--fps",         type=int, default=None)
    parser.add_argument("--no-detection",action="store_true",
                        help="Serve UI only — no camera detection")
    parser.add_argument("--port",        type=int, default=5000)
    args = parser.parse_args()

    from config import ConfigManager
    cfg = ConfigManager(fps=args.fps) if args.fps else ConfigManager()

    if args.cameras:
        cam_ids = [int(c.strip()) for c in args.cameras.split(",")]
    elif args.demo:
        cam_ids = [0]
        cfg = ConfigManager(num_cameras=1, fps=cfg.fps)
    else:
        cam_ids = [0, 1, 2]

    if not args.no_detection:
        t = threading.Thread(target=_run_detection, args=(cam_ids, cfg),
                             daemon=True)
        t.start()
    else:
        print("[SRV] No-detection mode — UI only.")

    print(f"[SRV] ThrowVision dashboard -> http://localhost:{args.port}")
    socketio.run(app, host="0.0.0.0", port=args.port, use_reloader=False,
                 log_output=False, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()

