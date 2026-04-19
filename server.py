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
import atexit
import base64
import json
import math
import os
import random
import signal
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

os.environ["OPENCV_LOG_LEVEL"] = "ERROR"

import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # silence HTTP request logs

import sys
import cv2
import numpy as np

from flask import Flask, send_from_directory, Response, jsonify, request
from flask_socketio import SocketIO

# ── Base directory: exe dir when frozen (PyInstaller), script dir in dev ────
BASE_DIR = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent

app = Flask(__name__, static_folder=str(BASE_DIR / "frontend"), static_url_path="")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # no static file caching in dev


app.config["SECRET_KEY"] = "throwvision-secret"
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    ping_interval=25,
    ping_timeout=60,
)

# ── Shared state (written by detection thread, read by SocketIO) ────────────
_cam_states: dict = {}   # cam_id -> {"state": str, "fps": float, "active": bool}
_last_score: dict = {}   # last dart_scored payload
_detectors: list = []    # DartDetector instances (set by detection thread)
_calibrators: list = []  # BoardCalibrator instances
_lens_cals: dict    = {}  # cam_id -> LensCalibrator (loaded lazily)
_consensus_scored_tips: list = []  # global list of (x,y) consensus tip positions
_cameras_open: bool = False       # True = cameras are open and streaming
_cameras_lock = threading.Lock()  # protects camera open/close
_state_lock = threading.Lock()    # protects shared state
_pose_model = None   # OpenVINO pose model (set by detection thread)
_cal_model = None    # OpenVINO calibration model (set by detection thread)
_tfluna = None       # TFLunaReader instance (oche distance sensor)
_tfluna_auto_port: str = ""  # last auto-detected port for hotplug tracking

# ── Confirmation thread tracking ────────────────────────────────────────────
_confirm_thread_count = 0
_confirm_thread_lock  = threading.Lock()

from board_profile import BoardProfile
from game_mode import BullseyeThrow, GameX01, GameCricket, GameCountUp
import accuracy_stats
import match_review
import stats as game_stats
from lens_calibrator import LensCalibrator
from anchor_refine import auto_calibrate_from_anchors, refine_anchor_points

_board_profile = BoardProfile()
# Load first available profile if any exist
_saved = BoardProfile.list_profiles()
if _saved:
    _board_profile.load(_saved[0]["name"])
_cfg = None              # ConfigManager reference
_detection_paused: bool = True   # True = detection scoring is paused
_last_model_reload_info: dict = {"pose_exec": "disabled", "cal_exec": "disabled"}
_practice_accuracy_session_id: Optional[str] = None
_practice_accuracy_turn_id: Optional[str] = None
_accuracy_session_context: Optional[str] = None
_accuracy_session_mode: Optional[str] = None


def _profile_calibration_points(frame: np.ndarray) -> Optional[np.ndarray]:
    """Return board-profile matched calibration points when available."""
    if not _board_profile.is_registered:
        return None
    try:
        pts = _board_profile.detect(frame)
        if pts is None:
            return None
        pts = np.asarray(pts, dtype=np.float32)
        if len(pts) not in (4, 8):
            return None
        return pts
    except Exception as e:
        print(f"[BOARD] Profile detect failed: {e}")
        traceback.print_exc()
        return None


def _run_auto_calibration(cam_id: int) -> Tuple[dict, int]:
    """Run the full auto-calibration pipeline for one active camera."""
    try:
        if cam_id < 0 or cam_id >= len(_detectors):
            return {"success": False, "reason": "invalid_cam_id"}, 400

        det = _detectors[cam_id]
        if not det.active:
            return {"success": False, "reason": "camera_not_active"}, 400

        frame = det.last_frame
        if frame is None or np.mean(frame) <= 5:
            frame = det._grab() if det.active else None
        if frame is None:
            return {"success": False, "reason": "no_frame"}, 503
        frame = _apply_undistort(cam_id, frame)

        cal = _calibrators[cam_id]

        result = None
        ml_result_raw = None
        if _cal_model is not None:
            try:
                from ml_calibration import ml_calibrate
                print(f"[CAL] Auto Cam {cam_id}: trying ML calibration…")
                ml_result_raw = ml_calibrate(frame, _cal_model, cal)
                if ml_result_raw["success"]:
                    result = ml_result_raw
                    print(f"[CAL] Auto Cam {cam_id}: ML calibration succeeded "
                          f"({result.get('rings_found', 0)} correspondences, "
                          f"err={result.get('reprojection_error', 0):.2f}px)")
                else:
                    print(f"[CAL] Auto Cam {cam_id}: ML calibration failed "
                          f"({ml_result_raw.get('reason')}), falling back…")
            except Exception as e:
                print(f"[CAL] Auto Cam {cam_id}: ML calibration crashed: {e}")

        if (result is not None
                and result.get("points") is not None
                and result.get("H") is None):
            try:
                ml_pts = np.asarray(result["points"], dtype=np.float32)
                if len(ml_pts) in (4, 8):
                    refined = refine_anchor_points(
                        frame, ml_pts, cal,
                        search_radius_px=max(16, int(cal.board_size * 0.05)),
                        iters=2,
                    )
                    if refined is not None:
                        refined_pts, _vis, metrics = refined
                        shift = metrics["mean_shift_px"]
                        if shift < cal.board_size * 0.15:
                            result["points"] = refined_pts.tolist()
                            result["reprojection_error"] = round(shift, 3)
                            print(f"[CAL] Auto Cam {cam_id}: ML anchors refined "
                                  f"(mean_shift={shift:.1f}px)")
                        else:
                            print(f"[CAL] Auto Cam {cam_id}: anchor refine shift "
                                  f"too large ({shift:.1f}px), keeping ML points")
                    else:
                        print(f"[CAL] Auto Cam {cam_id}: anchor refine returned "
                              f"None, keeping ML points")
            except Exception as e:
                print(f"[CAL] Auto Cam {cam_id}: anchor refine crashed: {e}")

        if result is None and ml_result_raw is not None and ml_result_raw.get("H") is not None:
            try:
                from auto_ellipse import refine_calibration
                H_seed = np.asarray(ml_result_raw["H"], dtype=np.float64)
                seed_pts = cal._anchor_src_points_from_homography(H_seed, n_points=8)
                ell_result = refine_calibration(frame, seed_pts, cal)
                if ell_result is not None:
                    refined_src, refined_dst_mm, _vis = ell_result
                    H_ell, _mask = cv2.findHomography(
                        refined_src, refined_dst_mm, cv2.RANSAC, 5.0)
                    if H_ell is not None:
                        anchor_pts = cal._anchor_src_points_from_homography(
                            H_ell, n_points=8)
                        result = {
                            "success": True,
                            "reason": "ml_seeded_ellipse",
                            "rings_found": 8,
                            "reprojection_error": 0.0,
                            "points": anchor_pts.tolist(),
                            "n_points": 8,
                            "preview_b64": ml_result_raw.get("preview_b64", ""),
                        }
                        print(f"[CAL] Auto Cam {cam_id}: ML-seeded ellipse "
                              f"refinement succeeded")
            except Exception as e:
                print(f"[CAL] Auto Cam {cam_id}: ML-seeded ellipse crashed: {e}")

        if result is None:
            profile_pts = _profile_calibration_points(frame)
            if profile_pts is not None:
                print(f"[CAL] Auto Cam {cam_id}: using board profile '{_board_profile.name}'")
                result = {
                    "success": True,
                    "rings_found": int(len(profile_pts)),
                    "reprojection_error": 0.0,
                    "points": profile_pts.tolist(),
                    "n_points": int(len(profile_pts)),
                    "preview_b64": base64.b64encode(
                        cv2.imencode('.jpg', cal.draw_anchor_points(frame, profile_pts),
                                     [cv2.IMWRITE_JPEG_QUALITY, 82])[1].tobytes()
                    ).decode("ascii"),
                }

        if result is None:
            result = auto_calibrate_from_anchors(frame, cal)

        if not result["success"]:
            print(f"[CAL] Auto Cam {cam_id}: failed — "
                  f"{result.get('reason')} (rings={result.get('rings_found', 0)})")
            return result, 422

        try:
            committed_via_h = False
            if result.get("reason") == "ml_calibration" and result.get("H") is not None:
                H_mm = np.asarray(result["H"], dtype=np.float64)
                committed_src = cal.commit_homography(H_mm, n_points=8)
                result["points"] = committed_src.tolist()
                result["n_points"] = int(len(committed_src))
                committed_via_h = True
                print(f"[CAL] Auto Cam {cam_id}: committed direct ML homography "
                      f"as {len(committed_src)} canonical anchors")
            else:
                src_points = np.asarray(result["points"], dtype=np.float32)
                cal.calibrate(src_points)

            if (ml_result_raw is not None
                    and ml_result_raw.get("success")
                    and ml_result_raw.get("H") is not None):
                try:
                    import math as _m
                    from ml_calibration import _CLASS_RADIUS
                    detections = _cal_model.predict(frame) if _cal_model else []
                    ml_src, ml_dst = [], []
                    for det_obj in detections:
                        r_mm = _CLASS_RADIUS.get(det_obj.class_name)
                        if r_mm is None:
                            continue
                        cx_d, cy_d = det_obj.cx, det_obj.cy
                        ml_src.append([cx_d, cy_d])
                        pt_mm = cal.transform_to_mm(cx_d, cy_d)
                        angle = _m.atan2(pt_mm[1], pt_mm[0])
                        ml_dst.append([r_mm * _m.cos(angle),
                                       r_mm * _m.sin(angle)])
                    if len(ml_src) >= 8:
                        cal.build_radial_bias_from_correspondences(
                            np.array(ml_src, dtype=np.float32),
                            np.array(ml_dst, dtype=np.float32),
                        )
                        print(f"[CAL] Auto Cam {cam_id}: enhanced radial bias "
                              f"from {len(ml_src)} ML correspondences")
                except Exception as e:
                    print(f"[CAL] Auto Cam {cam_id}: enhanced radial bias "
                          f"failed: {e}")

            det.cal = cal
            det.capture_reference()
            det.reset_to_wait()

            quality = cal.calibration_quality()
            n_rings = result["rings_found"]
            err = result["reprojection_error"]
            result["calibration_quality"] = quality
            metric_name = "reproj" if committed_via_h else "mean_shift"
            print(f"[CAL] Auto Cam {cam_id}: committed — "
                  f"{n_rings} anchors, {metric_name}={err:.2f}px, "
                  f"quality={quality:.1%}")
        except Exception as e:
            print(f"[CAL] Auto Cam {cam_id}: commit failed: {e}")
            traceback.print_exc()
            return {"success": False, "reason": f"commit_error: {e}"}, 500

        return result, 200
    except Exception as e:
        print(f"[CAL] Auto Cam {cam_id}: route crashed: {e}")
        traceback.print_exc()
        return {
            "success": False,
            "reason": f"auto_route_crashed: {type(e).__name__}: {e}",
        }, 500


def _run_startup_auto_calibration() -> None:
    """Auto-calibrate active cameras once during startup when enabled."""
    if _cfg is None or not getattr(_cfg, "calibrate_on_startup", False):
        return

    active_ids = [det.cam_id for det in _detectors if det.active]
    if not active_ids:
        print("[CAL] Startup auto calibration skipped — no active cameras")
        return

    print(f"[CAL] Startup auto calibration enabled — calibrating {len(active_ids)} camera(s)")
    socketio.emit("srv_status", {
        "message": "Auto-calibrating cameras on startup…",
        "type": "loading",
    })

    failures = []
    success_count = 0
    for cam_id in active_ids:
        result, status = _run_auto_calibration(cam_id)
        if status == 200 and result.get("success"):
            success_count += 1
        else:
            failures.append(f"Cam {cam_id + 1}: {result.get('reason', 'failed')}")

    if success_count:
        _refresh_detection_reference()

    if failures:
        print("[CAL] Startup auto calibration finished with issues: " + "; ".join(failures))
    else:
        print("[CAL] Startup auto calibration finished successfully")

    socketio.emit("srv_status", {
        "message": (
            f"Startup auto calibration complete ({success_count}/{len(active_ids)})"
            if not failures else
            f"Startup auto calibration finished with issues ({success_count}/{len(active_ids)})"
        ),
        "type": "ready",
    })


def _apply_undistort(cam_id: int, frame):
    """Apply lens undistortion if K+dist were previously computed for cam_id."""
    if cam_id not in _lens_cals:
        # Lazy-load from disk on first call
        K, dist = LensCalibrator.load(cam_id)
        _lens_cals[cam_id] = (K, dist)  # store tuple; None means uncalibrated
    entry = _lens_cals[cam_id]
    if isinstance(entry, tuple) and entry[0] is not None:
        return LensCalibrator.undistort(frame, entry[0], entry[1])
    if isinstance(entry, LensCalibrator) and entry.is_calibrated:
        K, dist = LensCalibrator.load(cam_id)
        if K is not None:
            return LensCalibrator.undistort(frame, K, dist)
    return frame

# ── Game mode state ──────────────────────────────────────────────────────────
_game_mode: Optional[str] = None       # 'bullseye' | 'x01' | 'cricket' | 'countup' | None
_bullseye: Optional[BullseyeThrow] = None
_game = None                           # active GameX01 / GameCricket / GameCountUp
_match_review_game_id: Optional[int] = None
_game_pending_mode: Optional[str] = None   # mode to launch after bullseye
_game_pending_opts: dict = {}              # options for the pending game
_practice_dart_count: int = 0              # darts thrown in current practice turn

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
            "num_cameras": len(_detectors),
        }


@app.route("/api/cameras/probe")
def api_cameras_probe():
    """Quick hardware probe: check how many cameras are physically connected.

    If cameras are already open, returns the cached cam_states (accurate).
    If cameras are closed, attempts a lightweight VideoCapture open per camera
    with a 2-second timeout so we can detect unplugged cameras without the
    full 8-second warmup.
    """
    import threading

    cam_ids = [det.cam_id for det in _detectors]
    total = len(cam_ids)

    # Fast path: cameras already open — cam_states is accurate
    if _cameras_open:
        with _state_lock:
            states = _cam_states.copy()
        cameras = [
            {"id": cid, "connected": bool(states.get(cid, {}).get("active", False))}
            for cid in cam_ids
        ]
        return {
            "total": total,
            "connected": sum(1 for c in cameras if c["connected"]),
            "cameras": cameras,
        }

    # Slow path: cameras not open — do a quick probe per camera
    def _probe_one(cam_id, result_list, idx):
        """Try to open camera briefly; store True/False in result_list[idx]."""
        try:
            import cv2 as _cv2
            cap = _cv2.VideoCapture(cam_id, _cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                result_list[idx] = False
                return
            # One read confirms the hardware actually delivers frames
            ok, _ = cap.read()
            cap.release()
            result_list[idx] = ok
        except Exception:
            result_list[idx] = False

    results = [False] * total
    threads = []
    for i, cid in enumerate(cam_ids):
        t = threading.Thread(target=_probe_one, args=(cid, results, i), daemon=True)
        threads.append(t)
        t.start()

    # Wait up to 3 seconds for all probes to finish
    for t in threads:
        t.join(timeout=3.0)

    cameras = [{"id": cam_ids[i], "connected": results[i]} for i in range(total)]
    return {
        "total": total,
        "connected": sum(1 for c in cameras if c["connected"]),
        "cameras": cameras,
    }


# Ordered list of candidate resolutions to probe (high→low)
_CANDIDATE_RESOLUTIONS: List[Tuple[int, int]] = [  # noqa: UP006
    (1920, 1080),
    (1280, 720),
    (848, 480),
    (640, 480),
    (640, 360),
    (424, 240),
    (320, 240),
]


@app.route("/api/cameras/resolutions")
def api_cameras_resolutions():
    """Return the list of resolutions this camera hardware actually supports.

    Safety rules:
      * Fast path: cameras already open  → borrow the first active cap, probe,
        and restore the original resolution.  Protected by _cameras_lock so it
        doesn't race with a concurrent close.
      * Detection-thread exists but cameras not yet open → return the full
        candidate list WITHOUT opening any cap.  The detection thread owns the
        hardware; opening a second VideoCapture simultaneously causes
        OpenCV/DirectShow heap corruption (exit code 0xC0000374).
      * No-detection mode (no _detectors) → safe to open briefly for probing.
    """
    import cv2 as _cv2

    _FALLBACK = [{"width": w, "height": h} for w, h in _CANDIDATE_RESOLUTIONS]

    # ── Fast path: cameras already open ──────────────────────────────────────
    if _cameras_open and _detectors:
        supported = []
        with _cameras_lock:
            det = next((d for d in _detectors if d.active and d.cap is not None), None)
            if det:
                cw, ch = _cfg.resolution if _cfg else (848, 480)
                for w, h in _CANDIDATE_RESOLUTIONS:
                    try:
                        det.cap.set(_cv2.CAP_PROP_FRAME_WIDTH,  w)
                        det.cap.set(_cv2.CAP_PROP_FRAME_HEIGHT, h)
                        actual_w = int(det.cap.get(_cv2.CAP_PROP_FRAME_WIDTH))
                        actual_h = int(det.cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))
                        if actual_w == w and actual_h == h:
                            supported.append({"width": w, "height": h})
                    except Exception:
                        pass
                # Restore original resolution so detection is unaffected
                try:
                    det.cap.set(_cv2.CAP_PROP_FRAME_WIDTH,  cw)
                    det.cap.set(_cv2.CAP_PROP_FRAME_HEIGHT, ch)
                except Exception:
                    pass
        if supported:
            return jsonify({"resolutions": supported, "probed": True})
        # Borrow gave nothing — fall back to full list (don't open a second cap)
        return jsonify({"resolutions": _FALLBACK, "probed": False})

    # ── Detection thread exists but cameras not open yet ─────────────────────
    # Opening any VideoCapture here would race with the detection thread.
    # Return the full candidate list; when the user next opens Settings after
    # cameras come up, the fast path above will give accurate results.
    if _detectors:
        return jsonify({"resolutions": _FALLBACK, "probed": False})

    # ── No-detection mode: safe to open a cap briefly ────────────────────────
    cam_id = 0
    cap = None
    supported = []
    try:
        cap = _cv2.VideoCapture(cam_id, _cv2.CAP_DSHOW)
        if not cap.isOpened():
            return jsonify({"resolutions": _FALLBACK, "probed": False})
        for w, h in _CANDIDATE_RESOLUTIONS:
            try:
                cap.set(_cv2.CAP_PROP_FRAME_WIDTH,  w)
                cap.set(_cv2.CAP_PROP_FRAME_HEIGHT, h)
                actual_w = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))
                if actual_w == w and actual_h == h:
                    supported.append({"width": w, "height": h})
            except Exception:
                pass
    except Exception as e:
        print(f"[CFG] Resolution probe failed: {e}")
    finally:
        if cap is not None:
            cap.release()

    return jsonify({"resolutions": supported or _FALLBACK, "probed": bool(supported)})






# ── Settings persistence ─────────────────────────────────────────────────────
SETTINGS_FILE = BASE_DIR / "settings.json"

def _load_settings_from_disk() -> dict:
    """Load persisted settings from settings.json, return empty dict if missing."""
    try:
        if SETTINGS_FILE.exists():
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[CFG] Could not read settings.json: {e}")
    return {}

def _save_settings_to_disk(data: dict) -> bool:
    """Persist settings dict to settings.json."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"[CFG] Could not write settings.json: {e}")
        return False

def _apply_settings_to_cfg(cfg, data: dict):
    """Apply a settings dict to a ConfigManager instance."""
    if not data:
        return
    if "resolution" in data:
        parts = str(data["resolution"]).split("x")
        if len(parts) == 2:
            try:
                cfg.resolution = (int(parts[0]), int(parts[1]))
                cfg.__post_init__()
            except ValueError:
                pass
    if "fps" in data:
        try:
            cfg.fps = int(data["fps"])
        except (ValueError, TypeError):
            pass
    if "min_dart_area" in data:
        try:
            cfg.dart_size_min = int(data["min_dart_area"])
        except (ValueError, TypeError):
            pass
    if "max_dart_area" in data:
        try:
            cfg.dart_size_max = int(data["max_dart_area"])
        except (ValueError, TypeError):
            pass
    if "binary_thresh" in data:
        try:
            cfg.binary_thresh = int(data["binary_thresh"])
        except (ValueError, TypeError):
            pass
    if "tip_offset_px" in data:
        try:
            cfg.tip_offset_px = float(data["tip_offset_px"])
        except (ValueError, TypeError):
            pass
    if "triangle_k_factor" in data:
        try:
            cfg.triangle_k_factor = float(data["triangle_k_factor"])
        except (ValueError, TypeError):
            pass
    if "num_cameras" in data:
        try:
            cfg.num_cameras = int(data["num_cameras"])
        except (ValueError, TypeError):
            pass
    if "standby_time" in data:
        cfg.standby_time = str(data["standby_time"])
    if "approximate_distortion" in data:
        cfg.approximate_distortion = bool(data["approximate_distortion"])
    if "calibrate_on_startup" in data:
        cfg.calibrate_on_startup = bool(data["calibrate_on_startup"])
    if "blur_kernel" in data:
        try:
            k = int(data["blur_kernel"])
            cfg.blur_kernel = (k, k)
        except (ValueError, TypeError):
            pass
    from config import DetectionSpeed
    if "detection_speed" in data:
        speed_map = {
            "very_low": DetectionSpeed.VERY_LOW,
            "low": DetectionSpeed.LOW,
            "default": DetectionSpeed.DEFAULT,
            "high": DetectionSpeed.HIGH,
            "very_high": DetectionSpeed.VERY_HIGH,
            # frontend legacy aliases
            "fast": DetectionSpeed.HIGH,
            "careful": DetectionSpeed.LOW,
        }
        spd = speed_map.get(str(data["detection_speed"]).lower())
        if spd is not None:
            cfg.detection_speed = spd
            cfg.apply_latency_profile(
                spd,
                preserve_stable_frames=("stable_frames" in data),
                preserve_openvino_device=("openvino_device" in data),
            )
    if "stable_frames" in data:
        try:
            cfg.stable_frames = max(3, int(data["stable_frames"]))
        except (ValueError, TypeError):
            pass
    if "collect_seconds" in data:
        try:
            cfg.collect_seconds = max(0.2, float(data["collect_seconds"]))
        except (ValueError, TypeError):
            pass
    if "confirm_seconds" in data:
        try:
            cfg.confirm_seconds = max(0.2, float(data["confirm_seconds"]))
        except (ValueError, TypeError):
            pass
    if "cooldown_frames" in data:
        try:
            cfg.cooldown_frames = max(0, int(data["cooldown_frames"]))
        except (ValueError, TypeError):
            pass
    # --- OpenVINO / ML model settings ---
    if "openvino_device" in data:
        cfg.openvino_device = str(data["openvino_device"]).upper()
    if "pose_enabled" in data:
        cfg.pose_enabled = bool(data["pose_enabled"])
    if "pose_conf_threshold" in data:
        try:
            cfg.pose_conf_threshold = float(data["pose_conf_threshold"])
        except (ValueError, TypeError):
            pass
    if "cal_enabled" in data:
        cfg.cal_enabled = bool(data["cal_enabled"])
    if "cal_conf_threshold" in data:
        try:
            cfg.cal_conf_threshold = float(data["cal_conf_threshold"])
        except (ValueError, TypeError):
            pass
    # --- TF-Luna oche sensor settings ---
    if "tfluna_enabled" in data:
        cfg.tfluna_enabled = bool(data["tfluna_enabled"])
    if "tfluna_port" in data:
        cfg.tfluna_port = str(data["tfluna_port"]).strip()
    if "tfluna_baud" in data:
        try:
            cfg.tfluna_baud = int(data["tfluna_baud"])
        except (ValueError, TypeError):
            pass
    if "tfluna_foul_distance_cm" in data:
        try:
            cfg.tfluna_foul_distance_cm = int(data["tfluna_foul_distance_cm"])
        except (ValueError, TypeError):
            pass
    if "tfluna_tolerance_cm" in data:
        try:
            cfg.tfluna_tolerance_cm = int(data["tfluna_tolerance_cm"])
        except (ValueError, TypeError):
            pass


def _reload_openvino_models(
    cfg,
    *,
    detectors: Optional[list] = None,
    reason: str = "runtime",
) -> dict:
    """(Re)load the OpenVINO pose/calibration models and bind them live."""
    global _pose_model, _cal_model, _last_model_reload_info

    from openvino_inference import OpenVINOModel, find_models_in_dir

    active_detectors = detectors if detectors is not None else list(_detectors)
    requested_device = str(getattr(cfg, "openvino_device", "CPU")).upper()

    pose_model = None
    if cfg.pose_enabled:
        pose_dir = cfg.pose_model_dir
        if not pose_dir:
            tip_dirs = find_models_in_dir(
                str(BASE_DIR / "models" / "tip"), task="pose")
            if tip_dirs:
                pose_dir = str(tip_dirs[0])
                print(f"[OV] Auto-discovered tip model: {tip_dirs[0].name}")
        if pose_dir:
            pose_model = OpenVINOModel(
                model_dir=pose_dir,
                device=requested_device,
                conf_threshold=cfg.pose_conf_threshold,
                iou_threshold=cfg.pose_iou_threshold,
            )
            if not pose_model.available:
                pose_model = None
        else:
            print("[OV] No pose model directory found — pose detection disabled")

    cal_model = None
    if cfg.cal_enabled:
        cal_dir = cfg.cal_model_dir
        if not cal_dir:
            cal_dirs = find_models_in_dir(
                str(BASE_DIR / "models" / "calibration"), task="detect")
            if cal_dirs:
                cal_dir = str(cal_dirs[0])
                print(f"[OV] Auto-discovered calibration model: "
                      f"{cal_dirs[0].name}")
        if cal_dir:
            cal_model = OpenVINOModel(
                model_dir=cal_dir,
                device=requested_device,
                conf_threshold=cfg.cal_conf_threshold,
            )
            if not cal_model.available:
                cal_model = None
        else:
            print("[OV] No calibration model directory found — ML calibration disabled")

    _pose_model = pose_model
    _cal_model = cal_model

    for det in active_detectors:
        det._pose_model = pose_model

    pose_exec = (getattr(pose_model, "execution_devices", None)
                 if pose_model is not None else "disabled")
    cal_exec = (getattr(cal_model, "execution_devices", None)
                if cal_model is not None else "disabled")
    print(f"[OV] Runtime reload ({reason}) complete: "
          f"requested={requested_device} pose={pose_exec} cal={cal_exec}")
    _last_model_reload_info = {
        "pose_loaded": pose_model is not None,
        "pose_exec": pose_exec,
        "cal_loaded": cal_model is not None,
        "cal_exec": cal_exec,
    }
    return dict(_last_model_reload_info)


@app.route("/api/settings")
def api_settings():
    """Return current config values so the frontend can sync UI state."""
    if _cfg is None:
        return {"error": "Config not loaded"}, 503
    return {
        "resolution": f"{_cfg.resolution[0]}x{_cfg.resolution[1]}",
        "fps": _cfg.fps,
        "num_cameras": _cfg.num_cameras,
        "standby_time": _cfg.standby_time,
        "min_dart_area": _cfg.dart_size_min,
        "max_dart_area": _cfg.dart_size_max,
        "binary_thresh": _cfg.binary_thresh,
        "tip_offset_px": _cfg.tip_offset_px,
        "triangle_k_factor": _cfg.triangle_k_factor,
        "approximate_distortion": _cfg.approximate_distortion,
        "calibrate_on_startup": _cfg.calibrate_on_startup,
        "blur_kernel": _cfg.blur_kernel[0],
        "detection_speed": _cfg.detection_speed.name.lower(),
        "stable_frames": _cfg.stable_frames,
        "collect_seconds": _cfg.collect_seconds,
        "confirm_seconds": _cfg.confirm_seconds,
        "cooldown_frames": _cfg.cooldown_frames,
        "openvino_device": _cfg.openvino_device,
        "pose_enabled": _cfg.pose_enabled,
        "pose_conf_threshold": _cfg.pose_conf_threshold,
        "cal_enabled": _cfg.cal_enabled,
        "cal_conf_threshold": _cfg.cal_conf_threshold,
        "tfluna_enabled": _cfg.tfluna_enabled,
        "tfluna_port": _cfg.tfluna_port,
        "tfluna_baud": _cfg.tfluna_baud,
        "tfluna_foul_distance_cm": _cfg.tfluna_foul_distance_cm,
        "tfluna_tolerance_cm": _cfg.tfluna_tolerance_cm,
    }


@app.route("/api/settings", methods=["POST"])
def api_settings_save():
    """Save settings to disk and apply live to the running config."""
    if _cfg is None:
        return {"error": "Config not loaded"}, 503
    data = request.get_json(force=True, silent=True) or {}
    _apply_settings_to_cfg(_cfg, data)
    ov_related_keys = {
        "detection_speed",
        "openvino_device",
        "pose_enabled",
        "pose_conf_threshold",
        "pose_iou_threshold",
        "pose_model_dir",
        "cal_enabled",
        "cal_conf_threshold",
        "cal_model_dir",
    }
    reload_info = None
    if any(key in data for key in ov_related_keys):
        reload_info = _reload_openvino_models(_cfg, reason="settings save")
    tfluna_related_keys = {
        "tfluna_enabled",
        "tfluna_port",
        "tfluna_baud",
        "tfluna_foul_distance_cm",
        "tfluna_tolerance_cm",
    }
    if any(key in data for key in tfluna_related_keys):
        if not _cfg.tfluna_enabled or not _cfg.tfluna_port:
            _stop_tfluna_reader(clear_auto_port=not _cfg.tfluna_enabled)
            socketio.emit("tfluna_status", {
                "detected": bool(_cfg.tfluna_port),
                "port": _cfg.tfluna_port,
                "connected": False,
                "enabled": _cfg.tfluna_enabled,
            })
            socketio.emit("distance_update", {
                "distance_cm": 0,
                "strength": 0,
                "connected": False,
                "foul_threshold_cm": _cfg.tfluna_foul_distance_cm,
                "is_trespassing": False,
            })
        else:
            should_restart = any(
                key in data for key in {"tfluna_enabled", "tfluna_port", "tfluna_baud"}
            )
            connected = _ensure_tfluna_running(force_restart=should_restart)
            socketio.emit("tfluna_status", {
                "detected": bool(_cfg.tfluna_port),
                "port": _cfg.tfluna_port,
                "connected": connected and _tfluna is not None and _tfluna.connected,
                "enabled": _cfg.tfluna_enabled,
            })
    merged = _load_settings_from_disk()
    merged.update(data)
    # Persist the effective runtime values too so speed presets save the
    # latency/device choices they actually resolved to.
    merged["stable_frames"] = _cfg.stable_frames
    merged["collect_seconds"] = _cfg.collect_seconds
    merged["confirm_seconds"] = _cfg.confirm_seconds
    merged["cooldown_frames"] = _cfg.cooldown_frames
    merged["openvino_device"] = _cfg.openvino_device
    if _save_settings_to_disk(merged):
        print(f"[CFG] Settings saved to {SETTINGS_FILE}")
        resp = {"ok": True}
        if reload_info is not None:
            resp["models_reloaded"] = True
            resp["pose_exec"] = reload_info["pose_exec"]
            resp["cal_exec"] = reload_info["cal_exec"]
        return jsonify(resp)
    return jsonify({"ok": False, "error": "Could not write settings.json"}), 500

# ── TF-Luna auto-detection ─────────────────────────────────────────────────
# Common USB-UART bridges used with TF-Luna:
#   CP210x  (Silicon Labs)  VID:PID = 10C4:EA60
#   CH340/CH341             VID:PID = 1A86:7523
#   CH9102                  VID:PID = 1A86:55D4
#   FTDI FT232              VID:PID = 0403:6001
_TFLUNA_KEYWORDS = (
    "cp210", "silicon labs", "10c4:ea60",
    "ch340", "ch341", "ch9102", "1a86:7523", "1a86:55d4",
    "ftdi", "ft232", "0403:6001",
    "tf-luna", "usb-serial", "usb serial",
)


def _scan_tfluna_port() -> str | None:
    """Scan serial ports for a TF-Luna sensor. Returns COM port or None."""
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return None
    for p in comports():
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        mfr = (p.manufacturer or "").lower()
        combined = f"{desc} {hwid} {mfr}"
        if any(kw in combined for kw in _TFLUNA_KEYWORDS):
            return p.device
    return None


def _stop_tfluna_reader(*, clear_auto_port: bool = False) -> None:
    """Stop the active TF-Luna reader, if any."""
    global _tfluna, _tfluna_auto_port
    if _tfluna is not None:
        try:
            _tfluna.stop()
        except Exception:
            pass
        _tfluna = None
    if clear_auto_port:
        _tfluna_auto_port = ""


def _ensure_tfluna_running(*, port: str | None = None, force_restart: bool = False) -> bool:
    """Ensure the TF-Luna reader is alive for the configured or supplied port."""
    global _tfluna, _tfluna_auto_port
    if _cfg is None:
        return False

    target_port = str(port or _cfg.tfluna_port or "").strip()
    if not _cfg.tfluna_enabled or not target_port:
        return False

    if _tfluna is not None:
        same_port = _tfluna.port == target_port
        if not force_restart and same_port and _tfluna.connected:
            return True
        _stop_tfluna_reader()

    from tfluna import TFLunaReader

    reader = TFLunaReader(target_port, _cfg.tfluna_baud)
    if not reader.start():
        return False

    _tfluna = reader
    _cfg.tfluna_port = target_port
    _tfluna_auto_port = target_port
    print(f"[TF-LUNA] Reader ready on {target_port}")
    return True


def _tfluna_hotplug_check():
    """Check if sensor was plugged/unplugged and auto-configure."""
    global _tfluna, _tfluna_auto_port
    if _cfg is None:
        return

    detected_port = _scan_tfluna_port()

    # Sensor just plugged in
    if detected_port and (
        _tfluna is None
        or not _tfluna.connected
        or _tfluna.port != detected_port
    ):
        print(f"[TF-LUNA] Auto-detected sensor on {detected_port}")
        _cfg.tfluna_port = detected_port
        _cfg.tfluna_enabled = True
        _tfluna_auto_port = detected_port
        if not _ensure_tfluna_running(port=detected_port, force_restart=True):
            print(f"[TF-LUNA] Auto-start failed on {detected_port}")
            _tfluna = None
        _existing = _load_settings_from_disk()
        _existing["tfluna_enabled"] = True
        _existing["tfluna_port"] = detected_port
        _save_settings_to_disk(_existing)
        socketio.emit("tfluna_status", {
            "detected": True,
            "port": detected_port,
            "connected": _tfluna is not None and _tfluna.connected,
            "enabled": _cfg.tfluna_enabled,
        })
        return

    # Sensor unplugged
    if not detected_port and _tfluna_auto_port:
        print(f"[TF-LUNA] Sensor removed (was on {_tfluna_auto_port})")
        _stop_tfluna_reader(clear_auto_port=True)
        _cfg.tfluna_enabled = False
        _cfg.tfluna_port = ""
        _existing = _load_settings_from_disk()
        _existing["tfluna_enabled"] = False
        _existing["tfluna_port"] = ""
        _save_settings_to_disk(_existing)
        socketio.emit("tfluna_status", {
            "detected": False,
            "port": "",
            "connected": False,
            "enabled": False,
        })
        socketio.emit("distance_update", {
            "distance_cm": 0, "strength": 0,
            "connected": False,
            "foul_threshold_cm": _cfg.tfluna_foul_distance_cm,
            "is_trespassing": False,
        })


@app.route("/api/tfluna/scan")
def api_tfluna_scan():
    """Scan serial ports for a TF-Luna sensor."""
    port = _scan_tfluna_port()
    if port:
        return jsonify({"found": True, "port": port})
    return jsonify({"found": False, "port": ""})


@app.route("/api/tfluna/ports")
def api_tfluna_ports():
    """List all serial ports for debugging — helps identify the right one."""
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        return jsonify({"ports": [], "error": "pyserial not installed"})
    ports = []
    for p in comports():
        ports.append({
            "device": p.device,
            "description": p.description or "",
            "hwid": p.hwid or "",
            "manufacturer": p.manufacturer or "",
        })
    return jsonify({"ports": ports})


@app.route("/api/distance")
def api_distance():
    """Return current TF-Luna distance sensor reading."""
    if _tfluna is None or not _tfluna.connected:
        return jsonify({"connected": False, "distance_cm": 0, "strength": 0})
    return jsonify({
        "connected": True,
        "distance_cm": _tfluna.distance_cm,
        "strength": _tfluna.strength,
        "stale": _tfluna.is_stale,
        "port": _tfluna.port,
    })

@app.route("/api/tfluna/probe", methods=["POST"])
def api_tfluna_probe():
    """Quick probe: try to open the configured TF-Luna port and read a packet."""
    global _tfluna, _tfluna_auto_port
    # If already running and connected, report live status
    if _tfluna is not None and _tfluna.connected:
        return jsonify({
            "ok": True,
            "status": "connected",
            "distance_cm": _tfluna.distance_cm,
            "strength": _tfluna.strength,
            "port": _tfluna.port,
        })

    # Try a fresh connection with current config
    if _cfg is None:
        return jsonify({"ok": False, "status": "no_config", "msg": "Config not loaded"})
    # Accept port from request body (from UI), or config, or auto-scan
    data = request.get_json(force=True, silent=True) or {}
    port = (data.get("port") or "").strip() or _cfg.tfluna_port
    if not port:
        # Try auto-detecting
        port = _scan_tfluna_port()
    if not port:
        return jsonify({"ok": False, "status": "no_port", "msg": "No COM port configured and auto-scan found nothing"})

    # If we have an existing reader on this port that lost connection, stop it first
    if _tfluna is not None:
        try:
            _tfluna.stop()
        except Exception:
            pass
        _tfluna = None

    from tfluna import TFLunaReader
    probe = TFLunaReader(port, _cfg.tfluna_baud)
    if not probe.start():
        return jsonify({"ok": False, "status": "failed", "msg": f"Cannot open {port}"})

    # Wait briefly for a reading
    import time
    time.sleep(0.5)
    dist = probe.distance_cm
    strength = probe.strength
    stale = probe.is_stale

    if stale:
        probe.stop()
        return jsonify({"ok": False, "status": "no_data", "msg": f"Port opened but no data received on {port}"})

    # Keep the probe connection alive as the main reader
    _tfluna = probe
    _tfluna_auto_port = port
    _cfg.tfluna_port = port
    _cfg.tfluna_enabled = True
    print(f"[TF-LUNA] Probe promoted to live reader on {port}")

    return jsonify({
        "ok": True,
        "status": "detected",
        "distance_cm": dist,
        "strength": strength,
        "port": port,
    })


@app.route("/api/tfluna/simulate", methods=["POST"])
def api_tfluna_simulate():
    """Simulate a TF-Luna distance reading for testing foul detection."""
    data = request.get_json(silent=True) or {}
    dist = int(data.get("distance_cm", 200))
    if _cfg is None:
        return jsonify({"ok": False, "msg": "Config not loaded"})

    threshold = _cfg.tfluna_foul_distance_cm - _cfg.tfluna_tolerance_cm
    is_foul = 0 < dist < threshold

    # Emit distance_update so the frontend shows the live indicator
    socketio.emit("distance_update", {
        "distance_cm": dist,
        "strength": 9999,
        "connected": True,
        "foul_threshold_cm": _cfg.tfluna_foul_distance_cm,
        "is_trespassing": is_foul,
    })

    # If foul, also emit foul_warning
    if is_foul:
        socketio.emit("foul_warning", {
            "distance_cm": dist,
            "threshold_cm": _cfg.tfluna_foul_distance_cm,
        })

    return jsonify({
        "ok": True,
        "distance_cm": dist,
        "threshold_cm": threshold,
        "is_foul": is_foul,
    })


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
        frame = _apply_undistort(cam_id, frame)
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return Response(buf.tobytes(), mimetype='image/jpeg')
    # Otherwise keep grabbing until we get a non-black frame
    for _ in range(20):
        frame = det._grab()
        if frame is not None and np.mean(frame) > 5:
            frame = _apply_undistort(cam_id, frame)
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
    """Accept 4 or 8 calibration points for a camera."""
    import numpy as np
    from flask import request
    data = request.get_json()
    cam_id   = data.get("cam_id", 0)
    points   = data.get("points")   # [[x,y] x4 or x8]
    frame_w  = data.get("frame_width")
    frame_h  = data.get("frame_height")
    if points is None or len(points) not in (4, 8):
        return {"error": "4 or 8 points required"}, 400
    if cam_id < 0 or cam_id >= len(_calibrators):
        return {"error": "Invalid camera ID"}, 400
    cal = _calibrators[cam_id]
    src = np.array(points, dtype=np.float32)
    if frame_w and frame_h:
        old_w, old_h = cal.w, cal.h
        cal.w, cal.h = int(frame_w), int(frame_h)
        cal.calibrate(src)
        cal.w, cal.h = old_w, old_h
    else:
        cal.calibrate(src)
    det = _detectors[cam_id]
    det.cal = cal
    det.capture_reference()
    det.reset_to_wait()
    n = len(points)
    print(f"[CAL] Camera {cam_id}: {n}-point calibration accepted "
          f"(frame {frame_w}x{frame_h})")
    return {"ok": True, "cam_id": cam_id, "n_points": n}


@app.route("/api/cal/info/<int:cam_id>")
def api_cal_info(cam_id):
    """Return current calibration status for a camera."""
    if cam_id < 0 or cam_id >= len(_calibrators):
        return {"error": "Invalid camera ID"}, 400
    cal = _calibrators[cam_id]
    info = {
        "cam_id": cam_id,
        "calibrated": cal.is_calibrated,
        "calibration_quality": cal.calibration_quality(),
        "resolution": [cal.w, cal.h],
    }
    if cal._src_pts is not None:
        info["src_points"] = cal._src_pts.tolist()
        info["n_points"] = len(cal._src_pts)
    return info


@app.route("/api/cal/refine/<int:cam_id>", methods=["POST"])
def api_cal_refine(cam_id):
    """Refine board calibration by snapping rough points to local board anchors.

    Body JSON: {"pts": [[x,y], ...]}  – 4 or 8 rough calibration points.
    Warps the board using the rough calibration, searches near each canonical
    anchor for the strongest local corner/wire intersection, and recommits
    calibration using the same 4/8-point model as manual calibration.
    Returns JPEG of the warped detection view + JSON headers:
      X-Refine-Accepted : "1" if calibration was committed, "0" otherwise
      X-Refine-Rings    : number of anchor points detected
    """
    try:
        import cv2
        import numpy as np
        if cam_id < 0 or cam_id >= len(_detectors):
            return jsonify({"ok": False, "error": "Invalid cam_id"}), 400

        body = request.get_json(force=True, silent=True) or {}
        pts_raw = body.get("pts")
        if not pts_raw or len(pts_raw) < 4:
            return jsonify({"ok": False, "error": "Need at least 4 pts"}), 400

        rough_src = np.array(pts_raw, dtype=np.float32)

        det   = _detectors[cam_id]
        frame = det.last_frame
        if frame is None or np.mean(frame) <= 5:
            frame = det._grab() if det.active else None
        if frame is None:
            return jsonify({"ok": False, "error": "No frame — open cameras first"}), 503

        frame = _apply_undistort(cam_id, frame)

        cal = _calibrators[cam_id]
        profile_pts = _profile_calibration_points(frame)
        if profile_pts is not None and len(profile_pts) == len(rough_src):
            refined_src = profile_pts
            vis = cal.draw_anchor_points(frame, refined_src)
            metrics = {
                "mean_shift_px": float(np.mean(np.linalg.norm(refined_src - rough_src, axis=1))),
                "max_shift_px": float(np.max(np.linalg.norm(refined_src - rough_src, axis=1))),
            }
            n_rings = len(refined_src)
            print(f"[CAL] Camera {cam_id}: refine using board profile '{_board_profile.name}'")
        else:
            result = refine_anchor_points(frame, rough_src, cal)
            if result is None:
                return jsonify({"ok": False,
                                "error": "Anchor refinement failed — adjust rough points and try again"}), 422
            refined_src, vis, metrics = result
            n_rings = len(refined_src)

        accepted = False
        try:
            cal.calibrate(refined_src)
            det.cal = cal
            det.capture_reference()
            det.reset_to_wait()
            accepted = True
            print(f"[CAL] Camera {cam_id}: anchor refinement committed "
                  f"({n_rings} anchors, mean_shift={metrics['mean_shift_px']:.2f}px)")
        except Exception as e:
            print(f"[CAL] Camera {cam_id}: refine commit failed: {e}")

        _, buf = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
        resp = Response(buf.tobytes(), mimetype='image/jpeg')
        resp.headers['X-Refine-Accepted'] = '1' if accepted else '0'
        resp.headers['X-Refine-Rings']    = str(n_rings)
        resp.headers['Access-Control-Expose-Headers'] = 'X-Refine-Accepted,X-Refine-Rings'
        return resp
    except Exception as e:
        print(f"[CAL] Camera {cam_id}: refine route crashed: {e}")
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"Refine crashed: {type(e).__name__}: {e}"}), 500



@app.route("/api/cal/auto/<int:cam_id>")
def api_cal_auto(cam_id):
    """Fully automatic dartboard calibration via rough anchor detection.

    Pipeline:
      1. Detect a rough outer-board ellipse and synthesize 8 anchor guesses
      2. Warp using those rough anchors
      3. Locally refine each manual anchor intersection in warped space
      4. Commit calibration through the same 8-point path as manual mode

    Returns JSON:
      {"success": true,  "rings_found": N, "reprojection_error": float,
       "H": [[...3×3...]], "preview_b64": "..."}
      {"success": false, "reason": "insufficient_rings", "rings_found": N, ...}
    """
    result, status = _run_auto_calibration(cam_id)
    return jsonify(result), status


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
    """Return a warped homography preview JPEG for calibration points."""
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
    frame = _apply_undistort(cam_id, frame)

    # Accepts comma-sep x1,y1,...xN,yN for 4 or 8 points
    pts_str = request.args.get("pts")
    if not pts_str:
        return {"error": "pts parameter required"}, 400
    try:
        vals = [float(v) for v in pts_str.split(",")]
        assert len(vals) in (8, 16), "Need 4 or 8 points"
        n = len(vals) // 2
        src = np.array(vals, dtype=np.float32).reshape(n, 2)
    except Exception:
        return {"error": "Invalid points format (need 4 or 8 points)"}, 400

    cal = _calibrators[cam_id]
    dst_px = cal._dst_pts_8 if n == 8 else cal._dst_pts_4
    if n == 4:
        M = cv2.getPerspectiveTransform(src, dst_px)
    else:
        M, _ = cv2.findHomography(src, dst_px, cv2.RANSAC, 3.0)
        if M is None:
            return {"error": "RANSAC failed"}, 500
    board_size = min(cal.w, cal.h)
    warped = cv2.warpPerspective(frame, M, (board_size, board_size))

    _, buf = cv2.imencode('.jpg', warped, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(buf.tobytes(), mimetype='image/jpeg')



# ── Lens Distortion Calibration API ──────────────────────────────────────────

@app.route("/api/lens/status/<int:cam_id>")
def api_lens_status(cam_id):
    """Return lens calibration status for a camera."""
    rms = LensCalibrator.rms_saved(cam_id)
    lc  = _lens_cals.get(cam_id)
    count = lc.count if isinstance(lc, LensCalibrator) else 0
    return jsonify({
        "cam_id":     cam_id,
        "count":      count,
        "calibrated": rms is not None,
        "rms":        round(rms, 3) if rms is not None else None,
    })


@app.route("/api/lens/autoframe/<int:cam_id>")
def api_lens_autoframe(cam_id):
    """Grab frame, auto-detect corners, capture if found. Returns JPEG + X-Lens-* headers."""
    import cv2
    import numpy as np
    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400
    det = _detectors[cam_id]
    frame = det.last_frame
    if frame is None or np.mean(frame) <= 5:
        frame = det._grab() if det.active else None
    if frame is None:
        return {"error": "No frame — open cameras first"}, 503

    lc = _lens_cals.setdefault(cam_id, LensCalibrator(cam_id))
    if not isinstance(lc, LensCalibrator):
        lc = LensCalibrator(cam_id)
        _lens_cals[cam_id] = lc

    found, vis = lc.detect(frame)
    count = lc.count
    coverage = lc.coverage_pct()
    if found and coverage < 95:   # keep capturing until 95% coverage
        _, count = lc.add_frame(frame)
        coverage = lc.coverage_pct()

    # Composite red coverage heatmap onto the frame
    vis = lc.draw_coverage_overlay(vis)

    _, buf = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 75])
    resp = Response(buf.tobytes(), mimetype='image/jpeg')
    resp.headers['X-Lens-Count']    = str(count)
    resp.headers['X-Lens-Coverage'] = str(coverage)
    resp.headers['X-Lens-Found']    = '1' if found else '0'
    resp.headers['Access-Control-Expose-Headers'] = 'X-Lens-Count,X-Lens-Coverage,X-Lens-Found'
    return resp


@app.route("/api/lens/frame/<int:cam_id>")
def api_lens_frame(cam_id):
    """Return a JPEG showing live corner detection (for UI preview)."""
    import cv2
    import numpy as np
    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400
    det = _detectors[cam_id]
    # Try last_frame first (works even if cameras aren't actively open)
    frame = det.last_frame
    if frame is None or np.mean(frame) <= 5:
        frame = det._grab() if det.active else None
    if frame is None:
        return {"error": "No frame — open cameras first"}, 503
    # Use a LensCalibrator instance for detection preview (don't add to collection)
    lc = _lens_cals.setdefault(cam_id, LensCalibrator(cam_id))
    if not isinstance(lc, LensCalibrator):
        lc = LensCalibrator(cam_id)
        _lens_cals[cam_id] = lc
    _, vis = lc.detect(frame)
    _, buf = cv2.imencode('.jpg', vis, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(buf.tobytes(), mimetype='image/jpeg')


@app.route("/api/lens/capture/<int:cam_id>", methods=["POST"])
def api_lens_capture(cam_id):
    """Capture one frame, detect corners, add to collection if valid."""
    import numpy as np
    if cam_id < 0 or cam_id >= len(_detectors):
        return {"error": "Invalid camera ID"}, 400
    det = _detectors[cam_id]
    frame = det.last_frame
    if frame is None or np.mean(frame) <= 5:
        frame = det._grab() if det.active else None
    if frame is None:
        return jsonify({"ok": False, "error": "No frame — open cameras first", "count": 0, "need": 20})
    lc = _lens_cals.setdefault(cam_id, LensCalibrator(cam_id))
    if not isinstance(lc, LensCalibrator):
        lc = LensCalibrator(cam_id)
        _lens_cals[cam_id] = lc
    added, count = lc.add_frame(frame)
    return jsonify({"ok": added, "count": count, "need": 20})


@app.route("/api/lens/compute/<int:cam_id>", methods=["POST"])
def api_lens_compute(cam_id):
    """Compute K+dist from collected frames and save to disk."""
    lc = _lens_cals.get(cam_id)
    if not isinstance(lc, LensCalibrator):
        return jsonify({"ok": False, "error": "No frames captured yet"})
    ok, rms, msg = lc.compute()
    if ok:
        # Invalidate cached (K, dist) tuple so _apply_undistort reloads from disk
        _lens_cals[cam_id] = lc
        # Refresh the detector's undistort callback so new coeff take effect immediately
        if 0 <= cam_id < len(_detectors):
            cid = cam_id
            _detectors[cam_id]._undistort_fn = lambda f, c=cid: _apply_undistort(c, f)
    return jsonify({"ok": ok, "rms": round(rms, 3) if ok else None, "message": msg})


@app.route("/api/lens/reset/<int:cam_id>", methods=["POST"])
def api_lens_reset(cam_id):
    """Clear collected frames for a camera (does NOT delete saved calibration)."""
    lc = _lens_cals.get(cam_id)
    if isinstance(lc, LensCalibrator):
        lc.reset()
    elif cam_id in _lens_cals:
        _lens_cals[cam_id] = LensCalibrator(cam_id)
    return jsonify({"ok": True, "count": 0})



@app.route("/api/lens/checkerboard")
def api_lens_checkerboard():
    """Return a printable SVG checkerboard (9×6 inner corners = 10×7 squares, 25 mm each)."""
    from flask import Response as FR
    cols, rows = 10, 7          # squares (outer count = inner corners + 1)
    sq_mm = 25                  # square size in mm
    px_per_mm = 3.7795          # 96 dpi
    sq = sq_mm * px_per_mm
    W  = cols * sq
    H  = rows * sq
    rects = []
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                x, y = c * sq, r * sq
                rects.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{sq:.1f}" height="{sq:.1f}" fill="black"/>')
    svg = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.1f}" height="{H:.1f}" '
        f'viewBox="0 0 {W:.1f} {H:.1f}" style="background:white">'
        + "".join(rects) +
        f'<text x="4" y="{H - 4:.1f}" font-size="9" fill="#666">'
        f'ThrowVision Lens Calibration — 9×6 inner corners, 25 mm squares — print at 100% scale'
        f'</text></svg>'
    )
    return FR(svg, mimetype="image/svg+xml",
              headers={"Content-Disposition": "inline; filename=checkerboard.svg"})

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

def _gen_mjpeg(cam_id: int, warped: bool = False, clean: bool = False):
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
        if warped and not clean:
            frame = frame.copy()
            cal = _calibrators[cam_id] if cam_id < len(_calibrators) else None
            # Scale marker sizes relative to board_size (tuned at 1080px)
            _bs = cal.board_size if cal else 480
            _ds = _bs / 1080.0
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
                _r_fill = max(2, int(round(6 * _ds)))
                _r_halo = max(3, int(round(7 * _ds)))
                for x_mm, y_mm in _consensus_scored_tips:
                    px, py = cal._mm_to_px(x_mm, y_mm, cx_w, cy_w)
                    pt = (int(px), int(py))
                    cv2.circle(frame, pt, _r_fill, (0, 200, 0), -1)
                    cv2.circle(frame, pt, _r_halo, (255, 255, 255), 1)
            # Latest detected tip — bright red crosshair
            if det.dart_tip is not None:
                tx, ty = int(det.dart_tip[0]), int(det.dart_tip[1])
                _r_ring = max(4, int(round(10 * _ds)))
                _r_ctr  = max(1, int(round(3 * _ds)))
                _ch     = max(6, int(round(16 * _ds)))
                cv2.circle(frame, (tx, ty), _r_ring, (0, 0, 255), 2)
                cv2.line(frame, (tx - _ch, ty), (tx + _ch, ty), (0, 0, 255), 2)
                cv2.line(frame, (tx, ty - _ch), (tx, ty + _ch), (0, 0, 255), 2)
                cv2.circle(frame, (tx, ty), _r_ctr, (0, 255, 255), -1)

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
    clean = str(request.args.get("clean", "")).lower() in {"1", "true", "yes", "on"}
    return Response(_gen_mjpeg(cam_id, warped=True, clean=clean),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ── Socket.IO events ─────────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    """Re-emit current server/camera status to newly connected client."""
    from flask_socketio import emit
    if _cameras_open:
        emit("srv_status", {"message": "Cameras ready", "type": "ready"})
    else:
        emit("srv_status", {"message": "Opening cameras\u2026", "type": "loading"})
    if _cam_states:
        socketio.emit("cam_status", _cam_states, to=None)
    if _last_score:
        socketio.emit("dart_scored", _last_score, to=None)
    if _practice_accuracy_session_id:
        _emit_accuracy_session_state()
    if _cfg is not None:
        emit("tfluna_status", {
            "detected": bool(_cfg.tfluna_port),
            "port": _cfg.tfluna_port,
            "connected": _tfluna is not None and _tfluna.connected,
            "enabled": _cfg.tfluna_enabled,
        })
        if _tfluna is not None and _tfluna.connected:
            threshold = _cfg.tfluna_foul_distance_cm - _cfg.tfluna_tolerance_cm
            dist = _tfluna.distance_cm
            emit("distance_update", {
                "distance_cm": dist,
                "strength": _tfluna.strength,
                "connected": True,
                "foul_threshold_cm": _cfg.tfluna_foul_distance_cm,
                "is_trespassing": 0 < dist < threshold,
            })
        else:
            emit("distance_update", {
                "distance_cm": 0,
                "strength": 0,
                "connected": False,
                "foul_threshold_cm": _cfg.tfluna_foul_distance_cm,
                "is_trespassing": False,
            })


@socketio.on("test_dart")
def on_test_dart(data):
    """Allows manual injection of a dart event for UI testing."""
    _emit_dart(data.get("label", "T20"),
               data.get("score", 60),
               data.get("x_mm", 0.0),
               data.get("y_mm", 170.0))


@socketio.on("start_detection")
def on_start_detection():
    global _detection_paused, _practice_dart_count
    # Auto-open cameras if not already open
    if not _cameras_open:
        _do_open_cameras()
    _ensure_tfluna_running()
    _detection_paused = False
    _practice_dart_count = 0
    _start_accuracy_session(context="practice", mode="practice")
    print("[SRV] Detection RESUMED by client.")
    socketio.emit("detection_state", {"paused": False})


@socketio.on("stop_detection")
def on_stop_detection():
    global _detection_paused, _practice_dart_count, _confirm_thread_count
    _detection_paused  = True
    _practice_dart_count = 0
    _end_accuracy_session(finalize_open_turn=True)
    with _confirm_thread_lock:
        _confirm_thread_count = 0
    print("[SRV] Detection PAUSED by client.")
    socketio.emit("detection_state", {"paused": True})


@socketio.on("open_cameras")
def on_open_cameras():
    """Open cameras on demand (calibration, preview, debug)."""
    if not _cameras_open:
        _do_open_cameras()
    _ensure_tfluna_running()


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
        socketio.emit("srv_status", {"message": "Opening cameras\u2026", "type": "loading"})
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

        # --- Wait for cameras to stabilize (phase-correlation logic) ---
        print("[SRV] Waiting for cameras to stabilize...")
        socketio.emit("srv_status", {"message": "Waiting for cameras to stabilize...", "type": "loading"})

        stable_cams = set()
        prev_frames = {det.cam_id: None for det in active}

        # Max 4 seconds; stable = consecutive frames differ by < 2.0 mean pixel
        t0 = time.perf_counter()
        while len(stable_cams) < len(active) and (time.perf_counter() - t0) < 4.0:
            for det in active:
                if det.cam_id in stable_cams:
                    continue

                frame = det._grab()
                if frame is None:
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray_small = cv2.resize(gray, (320, 180))
                curr = np.float32(gray_small)

                if prev_frames[det.cam_id] is not None:
                    mad = float(np.mean(np.abs(curr - prev_frames[det.cam_id])))
                    if mad < 2.0:
                        print(f"[SRV] Cam {det.cam_id} stable (mad={mad:.2f})")
                        stable_cams.add(det.cam_id)

                prev_frames[det.cam_id] = curr

            time.sleep(0.1)

        # Capture the stable reference for detection
        for det in _detectors:
            if det.active:
                det.capture_reference()

        time.sleep(0.5)
        for det in _detectors:
            if det.active:
                det._grab()
                det.capture_reference()

        _cameras_open = True
        print("[SRV] Cameras ready.")
        socketio.emit("srv_status", {"message": "Cameras ready", "type": "ready"})
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
        # TODO: match_review — cameras_lost abandonment
        _emit_cam_status()
        print("[SRV] Cameras released.")
        socketio.emit("cameras_state", {"open": False})


def _refresh_detection_reference() -> None:
    """Capture a fresh empty-board baseline on already-open cameras."""
    active = [d for d in _detectors if d.active]
    if not active:
        return

    for det in active:
        det.clear_scored_tips()
        det.reset_to_wait()

    # Pull a few fresh frames so the baseline reflects the current board state
    # and recent exposure/reader changes before we start scoring.
    for _ in range(3):
        for det in active:
            det._grab()
        time.sleep(0.04)

    for det in active:
        det.capture_reference()
        det.reset_to_wait(with_cooldown=True)


@socketio.on("update_settings")
def on_update_settings(data):
    """Legacy socket path — apply settings live but do NOT persist (use POST /api/settings)."""
    if _cfg is not None and data:
        _apply_settings_to_cfg(_cfg, data)


@socketio.on("clear_tips")
def on_clear_tips():
    """Clear scored tip markers from all cameras (removes green dots)."""
    for det in _detectors:
        det.clear_scored_tips()
    _consensus_scored_tips.clear()
    print("[SRV] Scored tips cleared (board reset)")





# ── Game Mode Socket.IO Events ──────────────────────────────────────────────

@socketio.on("start_bullseye")
def on_start_bullseye(data=None):
    """Begin bullseye throw sequence to determine first player."""
    global _game_mode, _bullseye, _game, _detection_paused
    global _game_pending_mode, _game_pending_opts
    global _awaiting_takeout, _takeout_hand_seen

    if _game is not None and not _game.is_finished:
        _abandon_current_match("user_quit")
    _end_accuracy_session(finalize_open_turn=False)

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
    else:
        _refresh_detection_reference()
    _ensure_tfluna_running()
    _detection_paused = False
    socketio.emit("detection_state", {"paused": False})

    state = _bullseye.start()
    socketio.emit("bullseye_state", state)
    print(f"[GAME] Bullseye throw started (pending mode: {_game_pending_mode})")


@socketio.on("start_game")
def on_start_game(data):
    """Start a game directly (skip bullseye if desired)."""
    global _game_mode, _game, _bullseye, _detection_paused

    if _game is not None and not _game.is_finished:
        _abandon_current_match("user_quit")
    _end_accuracy_session(finalize_open_turn=False)

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
    if mode == "x01":
        _start_accuracy_session(
            context="game",
            mode=mode,
            metadata={"started_via": "direct"},
        )
    # --- Match review bundle start (all modes) ---
    global _match_review_game_id
    try:
        _match_review_game_id = game_stats.reserve_id()
        match_review.start(
            game_id=_match_review_game_id,
            mode=mode,
            players=[{"player": 1, "name": "Player 1"},
                     {"player": 2, "name": "Player 2"}],
            started_at=time.time(),
        )
    except Exception as exc:
        print(f"[MATCH_REVIEW] start failed: {exc}")
        _match_review_game_id = None

    # Auto-open cameras and start detection
    if not _cameras_open:
        _do_open_cameras()
    _ensure_tfluna_running()
    _detection_paused = False
    socketio.emit("detection_state", {"paused": False})

    socketio.emit("game_state", _game.state())
    print(f"[GAME] {mode.upper()} game started (first player: {first_player})")


@socketio.on("undo_dart")
def on_undo_dart():
    """Undo the last dart in the current game."""
    global _awaiting_takeout, _takeout_hand_seen, _pending_turn_state
    global _takeout_reason, _needs_takeout_init, _turn_continue_pending
    global _detection_paused
    if _game is not None and not _game.is_finished:
        # If Undo is pressed while we're waiting for darts to be removed
        # after the 3rd dart, cancel the takeout so we stay on the same player
        if (_awaiting_takeout and _takeout_reason == 'turn') or _turn_continue_pending:
            _awaiting_takeout    = False
            _takeout_hand_seen   = False
            _pending_turn_state  = None
            _needs_takeout_init  = False
            _takeout_reason      = ''
            _turn_continue_pending = False
            _detection_paused = False
            socketio.emit("detection_state", {"paused": False})
            socketio.emit('cancel_takeout', {})     # dismiss Remove Darts banner
            print("[GAME] Undo during takeout — takeout cancelled")

        state = _game.undo_dart()
        socketio.emit("game_state", state)
        print("[GAME] Dart undone")


@socketio.on("end_game")
def on_end_game():
    """End the current game and return to idle."""
    global _game_mode, _game, _bullseye, _awaiting_takeout, _takeout_hand_seen
    global _game_pending_mode, _game_pending_opts, _practice_dart_count
    global _confirm_thread_count, _pending_turn_state, _turn_continue_pending
    # Stats were already saved when the game finished (in the dart-detection path).
    # Do NOT save again here — that caused every win to be recorded twice.

    with _confirm_thread_lock:
        _confirm_thread_count = 0
    _game_mode          = None
    _game               = None
    _bullseye           = None
    _awaiting_takeout   = False
    _takeout_hand_seen  = False
    _game_pending_mode  = None   # reset so next game start is clean
    _game_pending_opts  = {}
    _practice_dart_count = 0
    _pending_turn_state = None
    _turn_continue_pending = False
    if _game is not None and not _game.is_finished:
        _abandon_current_match("user_quit")
    _end_accuracy_session(finalize_open_turn=True)
    # Clear scored tips so next session starts clean
    for det in _detectors:
        det.clear_scored_tips()
    _consensus_scored_tips.clear()
    socketio.emit("game_state", {"type": "idle"})
    print("[GAME] Game ended")


@socketio.on("skip_takeout")
def on_skip_takeout():
    """User clicked Continue — skip automatic takeout detection."""
    global _awaiting_takeout, _takeout_hand_seen, _takeout_reason, _practice_dart_count
    global _pending_turn_state, _turn_continue_pending, _detection_paused
    if not _awaiting_takeout:
        if not _turn_continue_pending:
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
    elif _takeout_reason == 'practice':
        _reset_practice_turn("manual_takeout")
        print("[PRACTICE] Takeout skipped by user — board reset")
    elif (_takeout_reason == 'turn' or _turn_continue_pending) and _pending_turn_state is not None:
        socketio.emit('game_state', _pending_turn_state)
        socketio.emit('clear_board_dots', {})
        _advance_accuracy_turn("turn_continue")
        _pending_turn_state = None
        _awaiting_takeout = False
        _takeout_hand_seen = False
        _takeout_reason = ''
        _turn_continue_pending = False
        _detection_paused = False
        socketio.emit("detection_state", {"paused": False})
        socketio.emit('state', {'state': 'WAIT'})
        print("[GAME] Turn review completed — switched to next player")
    else:
        # Between-turn takeout — just resume scoring
        print("[GAME] Turn takeout completed by user — resuming")
        _awaiting_takeout = False
        _takeout_hand_seen = False
        _takeout_reason = ''
        _turn_continue_pending = False
        socketio.emit('state', {'state': 'WAIT'})


@socketio.on("practice_reset_turn")
def on_practice_reset_turn():
    _reset_practice_turn("manual_reset")


@socketio.on("get_stats")
def on_get_stats(data=None):
    """Return statistics for a game mode."""
    mode = data.get("mode") if data else None
    stats = game_stats.get_stats(mode)
    # Annotate the shared-shape stats dict the same way the HTTP /api/stats does.
    _annotate_has_review(stats.get("recent"))
    for mode_bucket in (stats.get("by_mode") or {}).values():
        _annotate_has_review(mode_bucket.get("recent"))
    socketio.emit("stats_data", stats)


@socketio.on("get_recent_games")
def on_get_recent_games(data=None):
    """Return recent game history."""
    mode = data.get("mode") if data else None
    limit = data.get("limit", 20) if data else 20
    recent = game_stats.get_recent(mode, limit)
    _annotate_has_review(recent)
    socketio.emit("recent_games", {"games": recent})


def _create_game(mode: str, options: dict):
    """Factory for game instances."""
    if mode == "x01":
        starting = int(options.get("starting_score", 501))
        finish_rule = str(options.get("finish_rule", "straight_out") or "straight_out")
        return GameX01(starting_score=starting, finish_rule=finish_rule)
    elif mode == "cricket":
        variant = str(options.get("variant", "standard") or "standard")
        target_numbers = None
        if variant == "random":
            target_numbers = sorted(random.sample(range(1, 21), 6), reverse=True) + [25]
        return GameCricket(variant=variant, target_numbers=target_numbers)
    elif mode == "countup":
        rounds = int(options.get("total_rounds", 8))
        variant = str(options.get("variant", "standard") or "standard")
        return GameCountUp(total_rounds=rounds, variant=variant)
    return None


def _build_x01_takeout_state(game_state: dict, previous_player: int) -> dict:
    """Show the finished turn while holding back the next-player state."""
    held_back = dict(game_state)
    held_back["current_player"] = previous_player + 1
    held_back["awaiting_takeout"] = True
    held_back["turn_info"] = "Remove darts from the board!"
    if game_state.get("turn_history"):
        last_turn = game_state["turn_history"][-1]
        held_back["darts_this_turn"] = last_turn.get("darts", [])
    return held_back


def _queue_x01_turn_takeout(game_state: dict, previous_player: int, *, emit_state: bool = True) -> dict:
    """Enter the between-turn takeout flow for X01."""
    global _awaiting_takeout, _takeout_hand_seen, _takeout_ready_at, _takeout_reason
    global _needs_takeout_init, _pending_turn_state, _turn_continue_pending, _detection_paused

    held_back = _build_x01_takeout_state(game_state, previous_player)
    if emit_state:
        socketio.emit("game_state", held_back)

    _pending_turn_state = game_state
    _awaiting_takeout = True
    _takeout_hand_seen = False
    _takeout_ready_at = time.time() + 3.0
    _takeout_reason = "turn"
    _needs_takeout_init = True
    _turn_continue_pending = False
    _detection_paused = False
    socketio.emit("turn_takeout", {"message": "Remove darts from the board!"})
    return held_back


_awaiting_takeout: bool = False
_takeout_hand_seen: bool = False
_takeout_ready_at: float = 0.0
_takeout_reason: str = ''
_practice_reset_at: float = 0.0
_needs_takeout_init: bool = False
_pending_turn_state: dict | None = None   # held-back game_state until turn takeout completes
_takeout_darts_snapshot: dict = {}        # cam_id → warped frame saved at prepare_for_takeout time
_turn_continue_pending: bool = False      # x01 review gate after turn takeout

# ── Dart confirmation constants (background thread approach) ──────────
DART_CONFIRM_SEC  = 2.5    # seconds to wait before confirming dart stayed
DART_DIFF_THRESH  = 15     # pixel threshold for warped diff to detect dart presence
DART_MIN_BLOB_PX  = 40     # minimum diff blob area to count as dart present
DART_CONFIRM_CORE_R_PX = 16   # tip-core radius inside ROI; shaft wobble outside this should not count as bounce
DART_CONFIRM_CORE_MIN_PX = 28 # changed pixels near the actual tip required before a camera can vote "gone"
DART_CONFIRM_STRONG_AREA_PX = 240  # total ROI change required for a strong bounce vote


def _confirm_dart_async(
    label, score, coord, cam_details,
    detectors_ref, calibrators_ref,
    ref_frames,
    confirm_sec, diff_thresh, min_blob_px,
    timings=None,
    oche_distance_cm=-1,
):
    """Background worker — waits confirm_sec, then checks if dart is still
    on the board by diffing current warped frames against stored references.

    If dart blob is still present: emit the original score.
    If dart blob is gone: emit BOUNCE instead.

    Runs in a daemon thread — does not block the main detection loop.
    """
    import time as _time
    import cv2 as _cv2
    import numpy as _np

    # Must declare global so the assignment inside `with` block isn't treated
    # as a local variable (which causes UnboundLocalError at runtime).
    global _confirm_thread_count

    # MISS darts wobble longer on the hard outer ring — extend their window
    wait = confirm_sec + (1.0 if label == 'MISS' else 0.0)
    _time.sleep(wait)

    dart_gone_votes = 0
    total_votes = 0

    for i, det in enumerate(detectors_ref):
        if not det.active:
            continue
        ref = ref_frames.get(i)
        if ref is None:
            continue

        current = det.warped_frame
        if current is None:
            continue

        try:
            cal = calibrators_ref[i] if i < len(calibrators_ref) else None
            if cal is None or not cal.is_calibrated:
                continue

            # Convert scored tip from mm to warped pixel coordinates
            bs = cal.board_size
            scale = cal._scale
            tip_wx = int(bs / 2 + coord[0] * scale)
            tip_wy = int(bs / 2 - coord[1] * scale)

            # Define ROI around the tip — large enough to contain the dart
            # shaft visible in warped space, small enough to ignore board noise
            roi_r = 55

            ref_gray = _cv2.cvtColor(ref, _cv2.COLOR_BGR2GRAY)
            cur_gray = _cv2.cvtColor(current, _cv2.COLOR_BGR2GRAY)

            if ref_gray.shape != cur_gray.shape:
                cur_gray = _cv2.resize(
                    cur_gray, (ref_gray.shape[1], ref_gray.shape[0]))

            h_f, w_f = ref_gray.shape
            x0 = max(0, tip_wx - roi_r)
            y0 = max(0, tip_wy - roi_r)
            x1 = min(w_f, tip_wx + roi_r)
            y1 = min(h_f, tip_wy + roi_r)

            if x1 <= x0 or y1 <= y0:
                # ROI out of bounds — skip this camera
                continue

            ref_roi = ref_gray[y0:y1, x0:x1]
            cur_roi = cur_gray[y0:y1, x0:x1]

            # Equalise mean brightness of the two ROI crops to neutralise
            # auto-exposure drift between snapshot and current frame
            ref_mean = float(ref_roi.mean()) + 1e-6
            cur_mean = float(cur_roi.mean()) + 1e-6
            cur_roi_norm = _cv2.convertScaleAbs(
                cur_roi, alpha=ref_mean / cur_mean)

            diff = _cv2.absdiff(ref_roi, cur_roi_norm)
            _, thresh = _cv2.threshold(
                diff, diff_thresh, 255, _cv2.THRESH_BINARY)

            # A real bounce-out changes the pixels right at the insertion point.
            # Plain shaft wobble often changes the larger ROI while leaving the
            # tip core mostly stable, so require both core and total evidence.
            core_mask = _np.zeros_like(thresh)
            _cv2.circle(core_mask, (tip_wx - x0, tip_wy - y0),
                        DART_CONFIRM_CORE_R_PX, 255, thickness=-1)
            core_change = int(_cv2.countNonZero(_cv2.bitwise_and(thresh, core_mask)))

            contours, _ = _cv2.findContours(
                thresh, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)

            blob_area = sum(_cv2.contourArea(c) for c in contours)
            total_votes += 1
            strong_change = (
                blob_area >= max(min_blob_px, DART_CONFIRM_STRONG_AREA_PX)
                and core_change >= DART_CONFIRM_CORE_MIN_PX
            )
            if strong_change:
                dart_gone_votes += 1
                print(f"[CONFIRM] Cam {i}: change in ROI "
                      f"(area={blob_area:.0f}px core={core_change}px) — dart may have left")
            else:
                print(f"[CONFIRM] Cam {i}: ROI unchanged "
                      f"(area={blob_area:.0f}px core={core_change}px) — dart present")
        except Exception as e:
            print(f"[CONFIRM] Cam {i}: diff error — {e}")
            total_votes += 1
            # Safe default: assume dart stayed on error, do not emit BOUNCE

    if total_votes == 0:
        # No cameras available to check — emit the original score safely
        print(f"[CONFIRM] No cameras available — confirming {label} = {score}")
        try:
            _emit_dart(label, score, coord[0], coord[1], cam_details,
                       oche_distance_cm=oche_distance_cm)
        finally:
            with _confirm_thread_lock:
                _confirm_thread_count = max(0, _confirm_thread_count - 1)
                print(f"[CONFIRM] Thread finished — "
                      f"active={_confirm_thread_count}")
        return

    # Bounce-out should be conservative: a single camera still seeing the dart
    # is enough to keep the original score. Require unanimous "gone" votes from
    # the cameras we were able to check.
    try:
        bounce_confirmed = (
            total_votes == 1 and dart_gone_votes == 1
        ) or (
            total_votes >= 2 and dart_gone_votes == total_votes
        )
        if bounce_confirmed:
            print(f"[CONFIRM] Dart left the board "
                  f"({dart_gone_votes}/{total_votes} cams saw change) — "
                  f"emitting BOUNCE")
            # Pass original coord+cam_details so frontend history shows the
            # bounce-out position rather than a blank 0,0 entry
            _emit_dart("BOUNCE", 0, coord[0], coord[1], cam_details,
                       oche_distance_cm=oche_distance_cm)
        else:
            print(f"[CONFIRM] Dart confirmed present "
                  f"({total_votes - dart_gone_votes}/{total_votes} cams no change) — "
                  f"emitting {label} = {score}")
            _emit_dart(label, score, coord[0], coord[1], cam_details,
                       timings=timings,
                       oche_distance_cm=oche_distance_cm)
    finally:
        with _confirm_thread_lock:
            _confirm_thread_count = max(0, _confirm_thread_count - 1)
            print(f"[CONFIRM] Thread finished — "
                  f"active={_confirm_thread_count}")

def _start_pending_game(winner: int):
    """Called after bullseye throw completes — waits for takeout before starting."""
    global _awaiting_takeout, _pending_game_winner, _takeout_hand_seen, _takeout_ready_at
    global _takeout_reason, _needs_takeout_init
    _pending_game_winner = winner
    _awaiting_takeout = True
    _takeout_hand_seen = False
    _takeout_ready_at = time.time() + 3.0  # 3s cooldown — enough time to press Undo
    _takeout_reason = 'bullseye'
    _needs_takeout_init = True              # trigger one-shot reference capture
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
    if mode == "x01":
        _start_accuracy_session(
            context="game",
            mode=mode,
            metadata={"started_via": "bullseye"},
        )
    # --- Match review bundle start (all modes) ---
    global _match_review_game_id
    try:
        _match_review_game_id = game_stats.reserve_id()
        match_review.start(
            game_id=_match_review_game_id,
            mode=mode,
            players=[{"player": 1, "name": "Player 1"},
                     {"player": 2, "name": "Player 2"}],
            started_at=time.time(),
        )
    except Exception as exc:
        print(f"[MATCH_REVIEW] start failed (bullseye path): {exc}")
        _match_review_game_id = None
    socketio.emit("game_state", _game.state())
    print(f"[GAME] {mode.upper()} game started (winner of bullseye: Player {_pending_game_winner})")


def _current_pose_model_name() -> str:
    model_dir = getattr(_pose_model, "model_dir", None)
    if model_dir is not None:
        return Path(model_dir).name
    pose_dir = getattr(_cfg, "pose_model_dir", "") if _cfg is not None else ""
    if pose_dir:
        return Path(str(pose_dir)).name
    return "pose-disabled"


def _current_execution_device() -> str:
    pose_exec = str(_last_model_reload_info.get("pose_exec") or "").strip()
    if pose_exec and pose_exec.lower() != "disabled":
        return pose_exec
    if _pose_model is not None:
        return str(getattr(_pose_model, "execution_devices", "unknown"))
    if _cfg is not None:
        return str(getattr(_cfg, "openvino_device", "unknown"))
    return "unknown"


def _current_detection_profile() -> str:
    if _cfg is not None and getattr(_cfg, "detection_speed", None) is not None:
        return str(_cfg.detection_speed.name)
    return "unknown"


def _is_x01_accuracy_review_active() -> bool:
    return (
        _accuracy_session_context == "game"
        and _accuracy_session_mode == "x01"
        and _practice_accuracy_session_id is not None
    )


def _emit_accuracy_session_state() -> None:
    socketio.emit(
        "accuracy_session",
        {
            "session_id": _practice_accuracy_session_id,
            "turn_id": _practice_accuracy_turn_id,
            "model_name": _current_pose_model_name(),
            "execution_device": _current_execution_device(),
            "detection_profile": _current_detection_profile(),
            "board_profile": str(getattr(_board_profile, "name", "") or ""),
            "context": _accuracy_session_context,
            "session_mode": _accuracy_session_mode,
        },
    )


def _start_accuracy_session(
    *,
    context: str = "practice",
    mode: str = "practice",
    metadata: Optional[dict] = None,
) -> None:
    global _practice_accuracy_session_id, _practice_accuracy_turn_id
    global _accuracy_session_context, _accuracy_session_mode
    if _practice_accuracy_session_id:
        accuracy_stats.end_session(_practice_accuracy_session_id, finalize_open_turn=False)
    session_metadata = {
        "server_time": datetime.now().isoformat(),
        "context": context,
        "mode": mode,
    }
    if metadata:
        session_metadata.update(metadata)
    started = accuracy_stats.start_session(
        model_name=_current_pose_model_name(),
        execution_device=_current_execution_device(),
        detection_profile=_current_detection_profile(),
        board_profile=str(getattr(_board_profile, "name", "") or ""),
        metadata=session_metadata,
    )
    _practice_accuracy_session_id = started["session"]["id"]
    _practice_accuracy_turn_id = started["turn"]["id"]
    _accuracy_session_context = context
    _accuracy_session_mode = mode
    _emit_accuracy_session_state()
    print(f"[ACC] {context}:{mode} accuracy session started: {_practice_accuracy_session_id}")


def _end_accuracy_session(*, finalize_open_turn: bool = False) -> None:
    global _practice_accuracy_session_id, _practice_accuracy_turn_id
    global _accuracy_session_context, _accuracy_session_mode
    if _practice_accuracy_session_id:
        accuracy_stats.end_session(
            _practice_accuracy_session_id,
            finalize_open_turn=finalize_open_turn,
        )
        print(f"[ACC] Accuracy session ended: {_practice_accuracy_session_id}")
    _practice_accuracy_session_id = None
    _practice_accuracy_turn_id = None
    _accuracy_session_context = None
    _accuracy_session_mode = None
    _emit_accuracy_session_state()


def _abandon_current_match(reason: str) -> None:
    """Finalize the in-flight match as abandoned. Safe to call multiple times."""
    global _match_review_game_id, _game
    gid = _match_review_game_id
    if gid is None:
        return
    try:
        summary = {}
        if _game is not None and not _game.is_finished:
            try:
                summary = _game.stats_summary()
            except Exception:
                summary = {}
            summary["status"] = "abandoned"
            summary["abandoned_reason"] = reason
            summary["id"] = gid
            try:
                game_stats.save_game(summary)
            except Exception as exc:
                print(f"[MATCH_REVIEW] abandoned save_game failed: {exc}")
        match_review.finalize(gid, summary, abandoned=True, reason=reason)
    except Exception as exc:
        print(f"[MATCH_REVIEW] abandon failed: {exc}")
    finally:
        _match_review_game_id = None


def _on_shutdown(*_args):
    try:
        _abandon_current_match("server_restart")
    except Exception:
        pass


atexit.register(_on_shutdown)
try:
    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)
except (ValueError, AttributeError):
    # signal.signal not allowed off main thread; atexit still covers it.
    pass


def _attach_accuracy_prediction(
    payload: dict,
    *,
    label: str,
    score: int,
    x_mm: float,
    y_mm: float,
    cam_details: list | None,
    timings: dict | None,
) -> None:
    if not _practice_accuracy_session_id or not _practice_accuracy_turn_id:
        return
    agreement_bucket = _categorize_agreement(cam_details)
    prediction = accuracy_stats.record_prediction(
        _practice_accuracy_session_id,
        _practice_accuracy_turn_id,
        {
            "label": label,
            "score": score,
            "x_mm": x_mm,
            "y_mm": y_mm,
            "cam_details": cam_details or [],
            "agreement_bucket": agreement_bucket,
            "timings": timings or {},
            "model_name": _current_pose_model_name(),
            "execution_device": _current_execution_device(),
            "detection_profile": _current_detection_profile(),
            "ts": payload["ts"],
        },
    )
    if prediction is None:
        return
    payload["prediction_id"] = prediction["id"]
    payload["session_id"] = _practice_accuracy_session_id
    payload["turn_id"] = _practice_accuracy_turn_id
    payload["agreement_bucket"] = agreement_bucket
    payload["model_name"] = prediction.get("model_name")
    payload["execution_device"] = prediction.get("execution_device")
    payload["detection_profile"] = prediction.get("detection_profile")


def _categorize_agreement(cam_details: list | None) -> str:
    if not cam_details:
        return "no_detection"
    detected = [item for item in cam_details if item.get("label")]
    count = len(detected)
    if count <= 0:
        return "no_detection"
    label_counts: dict[str, int] = {}
    for item in detected:
        label = str(item.get("label") or "")
        label_counts[label] = label_counts.get(label, 0) + 1
    max_count = max(label_counts.values()) if label_counts else 0
    if count >= 3:
        if max_count >= 3:
            return "3cam_all_match"
        if max_count == 2:
            return "3cam_two_match"
        return "3cam_all_diff"
    if count == 2:
        return "2cam_match" if max_count == 2 else "2cam_disagree"
    return "1cam_only"


def _advance_accuracy_turn(reset_reason: str) -> dict:
    global _practice_accuracy_turn_id
    payload = {
        "session_id": _practice_accuracy_session_id,
        "turn_id": _practice_accuracy_turn_id,
        "reset_reason": reset_reason,
    }
    if not _practice_accuracy_session_id or not _practice_accuracy_turn_id:
        return payload
    accuracy_stats.finalize_turn(
        _practice_accuracy_session_id,
        _practice_accuracy_turn_id,
        reset_reason=reset_reason,
    )
    next_turn = accuracy_stats.create_turn(
        _practice_accuracy_session_id,
        started_reason=reset_reason,
    )
    if next_turn is not None:
        _practice_accuracy_turn_id = next_turn["id"]
        payload["turn_id"] = _practice_accuracy_turn_id
    _emit_accuracy_session_state()
    return payload


def _reset_practice_turn(reset_reason: str = "manual_reset") -> dict:
    global _practice_dart_count, _awaiting_takeout, _takeout_hand_seen
    global _takeout_reason, _practice_reset_at, _pending_turn_state
    global _needs_takeout_init, _turn_continue_pending

    active_dets = [det for det in _detectors if det.active]
    if active_dets:
        _full_det_reset(active_dets, _consensus_scored_tips)
    else:
        _consensus_scored_tips.clear()

    _practice_dart_count = 0
    _awaiting_takeout = False
    _takeout_hand_seen = False
    _takeout_reason = ""
    _practice_reset_at = 0.0
    _needs_takeout_init = False
    _pending_turn_state = None
    _turn_continue_pending = False
    _takeout_darts_snapshot.clear()

    payload = _advance_accuracy_turn(reset_reason)
    socketio.emit("practice_reset", payload)
    socketio.emit("state", {"state": "WAIT"})
    return payload


# ── Stats API endpoints ─────────────────────────────────────────────────────

def _annotate_has_review(rows):
    for row in rows or []:
        try:
            row["has_review"] = match_review.has_review(int(row.get("id") or 0))
        except Exception:
            row["has_review"] = False


@app.route("/api/stats")
def api_stats():
    mode = request.args.get("mode")
    result = game_stats.get_stats(mode)
    _annotate_has_review(result.get("recent"))
    for mode_bucket in (result.get("by_mode") or {}).values():
        _annotate_has_review(mode_bucket.get("recent"))
    return jsonify(result)


@app.route("/api/stats/recent")
def api_stats_recent():
    mode = request.args.get("mode")
    limit = int(request.args.get("limit", 20))
    games = game_stats.get_recent(mode, limit)
    _annotate_has_review(games)
    return jsonify({"games": games})


@app.route("/api/stats/reset", methods=["POST"])
def api_stats_reset():
    deleted = game_stats.reset_stats()
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/api/stats/game/<int:game_id>", methods=["DELETE"])
def api_stats_delete_game(game_id: int):
    if not game_stats.delete_game(game_id):
        return jsonify({"ok": False, "error": "Game not found"}), 404
    try:
        match_review.delete_review(game_id)
    except Exception as exc:
        print(f"[MATCH_REVIEW] delete_review failed: {exc}")
    return jsonify({"ok": True, "id": game_id})


@app.route("/api/matches/<int:match_id>/review", methods=["GET"])
def api_get_match_review(match_id: int):
    data = match_review.get_review(match_id)
    if data is None:
        return ("", 404)
    return jsonify(data)


_ALLOWED_KINDS = {"per_dart", "eot"}


def _mr_frame_filename(kind: str, p: int, r: int, d: int, cam: int) -> Optional[str]:
    if kind == "per_dart":
        return f"p{int(p)}_r{int(r)}_d{int(d)}_cam{int(cam)}.jpg"
    if kind == "eot":
        return f"p{int(p)}_r{int(r)}_eot_cam{int(cam)}.jpg"
    return None


def _mr_validate_coords(p: int, r: int, d: int, cam: int) -> bool:
    return 1 <= p <= 4 and 1 <= r <= 40 and 0 <= d <= 2 and 0 <= cam <= 2


@app.route("/api/matches/<int:match_id>/frame/<kind>/<int:p>/<int:r>/<int:d>/<int:cam>",
           methods=["GET"])
def api_get_match_frame(match_id: int, kind: str, p: int, r: int,
                        d: int, cam: int):
    if kind not in _ALLOWED_KINDS:
        return ("invalid kind", 400)
    if not _mr_validate_coords(p, r, d, cam):
        return ("out of range", 400)
    fname = _mr_frame_filename(kind, p, r, d, cam)
    if fname is None:
        return ("invalid kind", 400)
    folder = (match_review.DATA_ROOT / f"match_{match_id}" / "frames").resolve()
    if not folder.is_dir():
        return ("", 404)
    return send_from_directory(str(folder), fname, mimetype="image/jpeg")


@app.route("/api/matches/<int:match_id>/frame/<kind>/<int:p>/<int:r>/<int:d>/<int:cam>/annotated",
           methods=["GET"])
def api_get_match_frame_annotated(match_id: int, kind: str, p: int, r: int,
                                   d: int, cam: int):
    if kind not in _ALLOWED_KINDS:
        return ("invalid kind", 400)
    if not _mr_validate_coords(p, r, d, cam):
        return ("out of range", 400)
    blob = match_review.render_annotated(
        game_id=match_id, kind=kind, player=p,
        round_num=r, dart_idx=d, cam_idx=cam,
    )
    if blob is None:
        return ("", 404)
    return Response(blob, mimetype="image/jpeg")


@app.route("/api/accuracy/summary")
def api_accuracy_summary():
    limit = int(request.args.get("limit", 50))
    return jsonify(accuracy_stats.get_summary(limit_sessions=limit))


@app.route("/api/accuracy/sessions")
def api_accuracy_sessions():
    limit = int(request.args.get("limit", 50))
    return jsonify({"sessions": accuracy_stats.get_sessions(limit=limit)})


@app.route("/api/accuracy/session/<session_id>")
def api_accuracy_session(session_id: str):
    session = accuracy_stats.get_session(session_id)
    if session is None:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session)


@app.route("/api/accuracy/review", methods=["POST"])
def api_accuracy_review():
    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "")
    turn_id = str(data.get("turn_id") or "")
    if not session_id or not turn_id:
        return jsonify({"ok": False, "error": "session_id and turn_id are required"}), 400
    turn = accuracy_stats.update_review(
        session_id,
        turn_id,
        data.get("actual_darts") or [],
        notes=str(data.get("notes") or ""),
    )
    if turn is None:
        return jsonify({"ok": False, "error": "Accuracy turn not found"}), 404
    return jsonify({"ok": True, "turn": turn})


@app.route("/api/accuracy/review/x01-action", methods=["POST"])
def api_accuracy_review_x01_action():
    global _pending_turn_state, _awaiting_takeout, _takeout_hand_seen, _takeout_reason
    global _turn_continue_pending, _detection_paused

    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "")
    turn_id = str(data.get("turn_id") or "")
    action = str(data.get("action") or "").strip().lower()
    actual_darts = data.get("actual_darts") or []

    if not session_id or not turn_id:
        return jsonify({"ok": False, "error": "session_id and turn_id are required"}), 400
    if action not in {"finish_turn", "continue_turn"}:
        return jsonify({"ok": False, "error": "Unsupported X01 review action"}), 400
    if _game_mode != "x01" or _game is None:
        return jsonify({"ok": False, "error": "No active X01 game"}), 409

    turn = accuracy_stats.update_review(
        session_id,
        turn_id,
        actual_darts,
        notes=str(data.get("notes") or ""),
    )
    if turn is None:
        return jsonify({"ok": False, "error": "Accuracy turn not found"}), 404

    if action == "finish_turn":
        if _turn_continue_pending or (_awaiting_takeout and _takeout_reason == "turn"):
            return jsonify({"ok": False, "error": "Turn is already awaiting takeout"}), 409
        previous_player = _game.current_player
        game_state = _game.apply_reviewed_current_turn(actual_darts, force_end=True)

        if _game.is_finished:
            socketio.emit("game_state", game_state)
            socketio.emit("game_over", game_state)
            try:
                _summary = _game.stats_summary()
                if _match_review_game_id is not None:
                    _summary["id"] = _match_review_game_id
                game_stats.save_game(_summary)
                if _match_review_game_id is not None:
                    try:
                        match_review.finalize(_match_review_game_id, _summary)
                    except Exception as exc:
                        print(f'[MATCH_REVIEW] finalize failed: {exc}')
                    finally:
                        _match_review_game_id = None
            except Exception as e:
                print(f"[STATS] Error saving: {e}")
            return jsonify({
                "ok": True,
                "action": action,
                "status": "game_over",
                "turn": turn,
                "game_state": game_state,
            })

        if _game.current_player == previous_player:
            socketio.emit("game_state", game_state)
            return jsonify({
                "ok": False,
                "action": action,
                "error": "Reviewed turn is not ready to finish yet",
                "turn": turn,
                "game_state": game_state,
            }), 409

        held_back = _queue_x01_turn_takeout(game_state, previous_player, emit_state=True)
        print("[GAME] X01 review finished a live turn — awaiting takeout")
        return jsonify({
            "ok": True,
            "action": action,
            "status": "awaiting_takeout",
            "turn": turn,
            "game_state": held_back,
        })

    if not _turn_continue_pending:
        return jsonify({"ok": False, "error": "Turn is not ready to continue"}), 409

    game_state = _game.apply_reviewed_last_turn(actual_darts)
    if _game.is_finished:
        _pending_turn_state = None
        _awaiting_takeout = False
        _takeout_hand_seen = False
        _takeout_reason = ""
        _turn_continue_pending = False
        _detection_paused = False
        socketio.emit("detection_state", {"paused": False})
        socketio.emit("game_state", game_state)
        socketio.emit("game_over", game_state)
        try:
            _summary = _game.stats_summary()
            if _match_review_game_id is not None:
                _summary["id"] = _match_review_game_id
            game_stats.save_game(_summary)
            if _match_review_game_id is not None:
                try:
                    match_review.finalize(_match_review_game_id, _summary)
                except Exception as exc:
                    print(f'[MATCH_REVIEW] finalize failed: {exc}')
                finally:
                    _match_review_game_id = None
        except Exception as e:
            print(f"[STATS] Error saving: {e}")
        return jsonify({
            "ok": True,
            "action": action,
            "status": "game_over",
            "turn": turn,
            "game_state": game_state,
        })

    _pending_turn_state = game_state
    on_skip_takeout()
    return jsonify({
        "ok": True,
        "action": action,
        "status": "continued",
        "turn": turn,
        "game_state": game_state,
    })


@app.route("/api/accuracy/reset", methods=["POST"])
def api_accuracy_reset():
    _end_accuracy_session(finalize_open_turn=False)
    deleted = accuracy_stats.reset_sessions()
    return jsonify({"ok": True, "deleted": deleted})


# ── Helpers called by the detection thread ───────────────────────────────────

def _emit_dart(label: str, score: int, x_mm: float, y_mm: float,
               cam_details: list | None = None,
               timings: dict | None = None,
               oche_distance_cm: int = -1):
    global _last_score, _game_mode, _bullseye, _game
    global _awaiting_takeout, _takeout_hand_seen, _takeout_ready_at, _takeout_reason
    global _needs_takeout_init, _pending_turn_state

    # ── Foul-line check (TF-Luna oche sensor) ─────────────────────────
    if _cfg is not None and _cfg.tfluna_enabled:
        if oche_distance_cm > 0:
            threshold = _cfg.tfluna_foul_distance_cm - _cfg.tfluna_tolerance_cm
            print(f"[TF-LUNA] Foul Check: read {oche_distance_cm} cm (threshold < {threshold} cm)")
            if label not in ('OFF', 'SKIP', 'BOUNCE'):
                if oche_distance_cm < threshold:
                    print(f"[FOUL] Player at {oche_distance_cm} cm "
                          f"caused FOUL! Overriding {label}={score} → FOUL")
                    label = "FOUL"
                    score = 0
                    socketio.emit("foul_warning", {
                        "distance_cm": oche_distance_cm,
                        "threshold_cm": _cfg.tfluna_foul_distance_cm,
                    })
                else:
                    print("[TF-LUNA] Clear: Valid throw.")
            else:
                print(f"[TF-LUNA] Skip foul check for label={label}")
        else:
            print("[TF-LUNA] Foul Check completely skipped: oche_distance_cm was -1 (sensor stale or not connected during detection)")
    else:
        # Avoid spamming if disabled
        pass

    payload = {"label": label, "score": score, "x_mm": x_mm, "y_mm": y_mm,
               "ts": time.time()}
    if cam_details:
        payload["cam_details"] = cam_details
    if timings:
        payload["timings"] = timings
    _attach_accuracy_prediction(
        payload,
        label=label,
        score=score,
        x_mm=x_mm,
        y_mm=y_mm,
        cam_details=cam_details,
        timings=timings,
    )
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
        if label == "BOUNCE":
            # Bounce-out: emit for history display but do NOT record into game state
            # (the dart didn't stick — player gets a re-throw)
            socketio.emit('dart_scored', payload)
            return
        prev_player = _game.current_player
        # Snapshot dart_idx BEFORE record_dart (turn may end internally)
        dart_idx_snapshot = len(getattr(_game, "darts_this_turn", []) or [])
        turn_idx_snapshot = len(getattr(_game, "turn_history", []) or []) // 2
        player_1based_snapshot = int(_game.current_player) + 1
        game_state = _game.record_dart(label, score, (x_mm, y_mm))

        # --- Match review capture (all modes) ---
        if _match_review_game_id is not None:
            try:
                frames = {}
                for idx, det in enumerate(_detectors):
                    if det is None or not getattr(det, "active", False):
                        continue
                    frame = getattr(det, "last_frame", None)
                    if frame is not None:
                        frames[idx] = frame.copy()
                prediction = {
                    "label": label,
                    "score": int(score),
                    "x_mm": float(x_mm) if x_mm is not None else None,
                    "y_mm": float(y_mm) if y_mm is not None else None,
                    "agreement_bucket": _categorize_agreement(cam_details),
                    "cam_details": cam_details or [],
                    "ts": time.time(),
                }
                match_review.record_dart(
                    game_id=_match_review_game_id,
                    player=player_1based_snapshot,
                    turn_idx=turn_idx_snapshot,
                    round_num=turn_idx_snapshot + 1,
                    dart_idx=dart_idx_snapshot,
                    prediction=prediction,
                    frames_bgr=frames,
                )
                if dart_idx_snapshot == 2:  # 3rd dart — capture EOT after settle
                    time.sleep(0.250)
                    eot_frames = {}
                    for idx, det in enumerate(_detectors):
                        if det is None or not getattr(det, "active", False):
                            continue
                        try:
                            eot_frame = det._grab() if hasattr(det, "_grab") else getattr(det, "last_frame", None)
                        except Exception:
                            eot_frame = None
                        if eot_frame is not None:
                            eot_frames[idx] = eot_frame
                    match_review.record_turn_end(
                        game_id=_match_review_game_id,
                        player=player_1based_snapshot,
                        turn_idx=turn_idx_snapshot,
                        round_num=turn_idx_snapshot + 1,
                        frames_bgr=eot_frames,
                        ts=time.time(),
                    )
            except Exception as exc:
                print(f"[MATCH_REVIEW] capture failed: {exc}")

        if _game.is_finished:
            socketio.emit('game_state', game_state)
            socketio.emit('game_over', game_state)
            try:
                _summary = _game.stats_summary()
                if _match_review_game_id is not None:
                    _summary["id"] = _match_review_game_id
                game_stats.save_game(_summary)
                if _match_review_game_id is not None:
                    try:
                        match_review.finalize(_match_review_game_id, _summary)
                    except Exception as exc:
                        print(f'[MATCH_REVIEW] finalize failed: {exc}')
                    finally:
                        _match_review_game_id = None
            except Exception as e:
                print(f'[STATS] Error saving: {e}')
        elif _game.current_player != prev_player:
            _queue_x01_turn_takeout(game_state, prev_player)
            print(f"[GAME] Turn ended — awaiting takeout before Player {_game.current_player + 1} throws")
        else:
            socketio.emit('game_state', game_state)          # mid-turn dart, emit normally
        # Also emit dart_scored for board dot placement
        socketio.emit('dart_scored', payload)
        return

    # No active game — normal practice mode
    socketio.emit("dart_scored", payload)
    if label == "BOUNCE":
        # Dart bounced out — show in history but don't count toward the 3-throw limit
        return
    # ── Practice 3-throw auto-takeout ──────────────────────────────
    global _practice_dart_count
    _practice_dart_count += 1
    if _practice_dart_count >= 3:
        _awaiting_takeout = True
        _takeout_hand_seen = False
        _takeout_ready_at = time.time() + 3.0  # 3s cooldown
        _takeout_reason = 'practice'
        _needs_takeout_init = True
        socketio.emit('practice_awaiting_takeout', {'message': 'Remove darts from the board!'})
        print(f"[PRACTICE] 3 darts thrown — awaiting takeout")


def _emit_cam_status():
    with _state_lock:
        states_copy = _cam_states.copy()
    socketio.emit("cam_status", states_copy)


def _emit_log(msg: str):
    """Push a log line to connected browsers."""
    socketio.emit("server_log", {"msg": msg, "ts": time.time()})





class _TeeStdout:
    """Intercept stdout: echo to real terminal AND push to Socket.IO."""
    def __init__(self, real):
        self._real = real
        self._buf = ""

        # Cooldown
        self._cooldown: int = 0
        self._COOLDOWN_FRAMES: int = 15   # ~500ms at 30fps (was 30 = 1s)

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


sys.stdout = _TeeStdout(sys.stdout)


# ── Detection thread ──────────────────────────────────────────────────────────

def _empty_collected(n: int):
    """Return fresh (tips, areas, methods, mm, yolo_mm) collection buffers."""
    return [None]*n, [0]*n, ['NONE']*n, [None]*n, [None]*n


def _full_det_reset(dets, scored_tips_list):
    """Clear scored tips, capture reference, and reset all detectors to WAIT."""
    for det in dets:
        det.clear_scored_tips()
        det.capture_reference()
        det.reset_to_wait()
    scored_tips_list.clear()


def _log_detection_latency(
    detectors,
    collected_tips,
    cfg,
    *,
    collect_started_at: float = 0.0,
    prefix: str = "",
) -> dict:
    """Emit a compact timing line for the next live speed test."""
    now = time.perf_counter()
    selected = [
        detectors[i]
        for i in range(min(len(detectors), len(collected_tips)))
        if collected_tips[i] is not None
    ]
    if not selected:
        return {}

    parts = []
    timings: dict[str, float] = {}
    motion_times = [
        float(getattr(det, "last_motion_started_at", 0.0))
        for det in selected
        if getattr(det, "last_motion_started_at", 0.0) > 0.0
    ]
    dart_times = [
        float(getattr(det, "last_dart_detected_at", 0.0))
        for det in selected
        if getattr(det, "last_dart_detected_at", 0.0) > 0.0
    ]
    pose_times = [
        float(getattr(det, "_last_pose_inference_ms", 0.0))
        for det in selected
        if getattr(det, "_last_pose_inference_ms", 0.0) > 0.0
    ]
    if motion_times:
        motion_ms = round((now - min(motion_times)) * 1000, 2)
        timings["motion_to_emit_ms"] = motion_ms
        parts.append(f"motion_to_emit={motion_ms:.0f}ms")
    if dart_times:
        detect_ms = round((now - min(dart_times)) * 1000, 2)
        timings["detect_to_emit_ms"] = detect_ms
        parts.append(f"detect_to_emit={detect_ms:.0f}ms")
    if collect_started_at > 0.0:
        collect_ms = round((now - collect_started_at) * 1000, 2)
        timings["collect_to_emit_ms"] = collect_ms
        parts.append(f"collect_to_emit={collect_ms:.0f}ms")
    if pose_times:
        pose_ms = round(max(pose_times), 2)
        timings["pose_max_ms"] = pose_ms
        parts.append(f"pose_max={pose_ms:.0f}ms")

    ov_exec = (getattr(_pose_model, "execution_devices", None)
               if _pose_model is not None else None)
    if ov_exec:
        parts.append(f"ov={ov_exec}")
    parts.append(f"profile={cfg.detection_speed.name}")
    parts.append(f"collect={cfg.collect_seconds:.2f}s")

    label = f"[LATENCY] {prefix}".rstrip()
    print(f"{label}: " + "  ".join(parts))
    return timings


def _run_detection(cam_ids: List[int], cfg) -> None:
    """Mirrors the logic of main.run() but emits events instead of cv2.imshow."""
    global _takeout_hand_seen, _takeout_ready_at
    import cv2
    import numpy as np
    from calibrator import BoardCalibrator
    from detector import DartDetector, State
    from scorer import ScoreMapper

    global _detectors, _calibrators, _cfg
    global _pose_model, _cal_model
    global _awaiting_takeout, _takeout_hand_seen, _takeout_ready_at
    global _takeout_reason, _practice_dart_count, _practice_reset_at
    global _needs_takeout_init, _pending_turn_state
    global _confirm_thread_count, _turn_continue_pending, _detection_paused
    _cfg = cfg

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

    # ── YOLO tip verifier (legacy bounding-box model) ───────────────────────
    yolo = None
    if cfg.yolo_enabled:
        from yolo_verifier import YoloVerifier
        _model_path = str(BASE_DIR / "models" / "tip" / "darttipbox1.1.pt")
        yolo = YoloVerifier(
            model_path=_model_path,
            conf_threshold=cfg.yolo_conf_threshold,
            match_radius_mm=cfg.yolo_match_radius_mm,
        )
        if not yolo.available:
            yolo = None

    _reload_openvino_models(cfg, detectors=detectors, reason="startup")

    # ── TF-Luna oche distance sensor ────────────────────────────────────────
    global _tfluna, _tfluna_auto_port
    # Only initialise if not already connected (e.g. from a probe)
    if _tfluna is not None and _tfluna.connected:
        print(f"[TF-LUNA] Already connected on {_tfluna.port} — skipping re-init")
        _tfluna_auto_port = _tfluna.port
    elif cfg.tfluna_enabled and cfg.tfluna_port:
        from tfluna import TFLunaReader
        _tfluna = TFLunaReader(cfg.tfluna_port, cfg.tfluna_baud)
        if not _tfluna.start():
            _tfluna = None
        else:
            _tfluna_auto_port = cfg.tfluna_port  # track for hotplug removal
    else:
        _tfluna = None
        _tfluna_auto_port = ""

    # Wire lens undistortion into every detector so _grab() applies it
    # immediately after every cap.read(). Uses a closure to capture cid.
    def _make_undistort(cid):
        return lambda f: _apply_undistort(cid, f)
    for det in detectors:
        det._undistort_fn = _make_undistort(det.cam_id)
        # Wire pose model into each detector
        if _pose_model is not None:
            det._pose_model = _pose_model

    if cfg.calibrate_on_startup:
        try:
            _do_open_cameras()
            _run_startup_auto_calibration()
        finally:
            if _cameras_open:
                _do_close_cameras()

    # ── Cameras stay OFF until explicitly opened ──────────────────────────────
    # Set initial cam states to OFFLINE
    for det in detectors:
        _cam_states[det.cam_id] = {
            "state": "OFFLINE", "fps": 0.0, "active": False,
        }
    _emit_cam_status()
    scorer = ScoreMapper(cfg, calibrators)

    MIN_DART_AREA   = 100
    HEALTH_LOG_INTERVAL = 60.0  # seconds between per-camera health reports
    collect_deadline: float = 0.0
    _dart_oche_distance_cm: int = -1  # captured at dart detection time
    _last_health_log: float = 0.0
    _hand_was_active: bool = False  # track HAND→WAIT transition for frontend
    collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))

    _throw_count = 0
    _DEBUG_LOG = BASE_DIR / "throw_debug.log"
    _dbg_lines: list = []
    _run_detection._collect_started_at = 0.0

    while True:
        # When cameras are closed, still handle TF-Luna then sleep
        if not _cameras_open:
            now = time.perf_counter()
            # TF-Luna distance broadcast (every ~500ms)
            should_emit_distance = (
                _tfluna is not None
                and _tfluna.connected
                and (
                    not getattr(_run_detection, '_last_dist_emit', None)
                    or now - getattr(_run_detection, '_last_dist_emit', 0) >= 0.5
                )
            )
            if should_emit_distance:
                _run_detection._last_dist_emit = now
                dist = _tfluna.distance_cm
                threshold = cfg.tfluna_foul_distance_cm - cfg.tfluna_tolerance_cm
                socketio.emit("distance_update", {
                    "distance_cm": dist,
                    "strength": _tfluna.strength,
                    "connected": True,
                    "foul_threshold_cm": cfg.tfluna_foul_distance_cm,
                    "is_trespassing": 0 < dist < threshold,
                })
            # TF-Luna hotplug (every ~2s)
            if (not getattr(_run_detection, '_last_hotplug', None)
                    or now - getattr(_run_detection, '_last_hotplug', 0) >= 2.0):
                _run_detection._last_hotplug = now
                _tfluna_hotplug_check()
            time.sleep(0.1)
            continue

        # When paused, still grab frames (keep cameras alive) but skip detection
        if _detection_paused:
            for det in detectors:
                if det.active:
                    det._grab()
            time.sleep(0.05)
            continue

        # While a throw is still being confirmed, keep cameras alive but do not
        # run a second scoring pass on the same physical dart.
        with _confirm_thread_lock:
            confirm_active = _confirm_thread_count > 0
        if confirm_active:
            collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
            collect_deadline = 0.0
            _run_detection._collect_started_at = 0.0
            for det in detectors:
                if det.active:
                    det._grab()
                    if det.state == State.DART:
                        det.reset_to_wait(with_cooldown=True)
            time.sleep(0.03)
            continue

        active_dets = [d for d in detectors if d.active]

        states = [det.step() for det in detectors]
        now = time.perf_counter()

        # ── One-shot takeout initialisation ───────────────────────────────
        # When _needs_takeout_init is set, do ONE capture_reference() pass so
        # the cameras treat the board-with-darts as the new baseline.
        # Without this, cameras keep re-detecting the static darts as "motion"
        # and never reach HAND state.  We also discard any partial collection.
        if _needs_takeout_init:
            _needs_takeout_init = False
            collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
            collect_deadline = 0.0
            _run_detection._collect_started_at = 0.0
            for det in active_dets:
                # Save warped frame snapshot BEFORE baking darts into reference.
                # Used later to verify darts were actually removed (not just hand).
                if det.warped_frame is not None:
                    _takeout_darts_snapshot[det.cam_id] = det.warped_frame.copy()
                # prepare_for_takeout(): bakes darts into reference,
                # no cooldown, no settle, disables stale-ref guard so
                # the hand reaching in doesn't trigger auto-recapture.
                det.prepare_for_takeout()


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

        # ── TF-Luna distance broadcast (every ~500ms) ────────────────────
        should_emit_distance = (
            _tfluna is not None
            and _tfluna.connected
            and (
                not getattr(_run_detection, '_last_dist_emit', None)
                or now - getattr(_run_detection, '_last_dist_emit', 0) >= 0.5
            )
        )
        if should_emit_distance:
            _run_detection._last_dist_emit = now
            dist = _tfluna.distance_cm
            threshold = cfg.tfluna_foul_distance_cm - cfg.tfluna_tolerance_cm
            socketio.emit("distance_update", {
                "distance_cm": dist,
                "strength": _tfluna.strength,
                "connected": True,
                "foul_threshold_cm": cfg.tfluna_foul_distance_cm,
                "is_trespassing": 0 < dist < threshold,
            })

        # ── TF-Luna hotplug detection (every ~2s) ────────────────────────
        if (not getattr(_run_detection, '_last_hotplug', None)
                or now - getattr(_run_detection, '_last_hotplug', 0) >= 2.0):
            _run_detection._last_hotplug = now
            _tfluna_hotplug_check()

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

        # During takeout wait, cameras should only look for hand/removal.
        # Do not collect or score dart blobs while the user is meant to be
        # removing darts from the board.
        if _awaiting_takeout:
            collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
            collect_deadline = 0.0
            _run_detection._collect_started_at = 0.0
            for det in active_dets:
                if det.state == State.DART:
                    det.reset_to_wait(with_cooldown=True)

        # ── Dart collection ───────────────────────────────────────────────
        for i, det in enumerate(detectors):
            if det.state == State.DART and det.dart_tip is not None:
                if det.dart_area >= MIN_DART_AREA:
                    collected_tips[i]    = det.dart_tip
                    collected_areas[i]   = det.dart_area
                    collected_methods[i] = det.dart_tip_method
                    collected_mm[i]      = None  # CV methods: no direct mm

        any_dart = any(d.state == State.DART for d in active_dets)
        if any_dart and collect_deadline == 0.0:
            collect_deadline = now + max(0.2, float(cfg.collect_seconds))
            _run_detection._collect_started_at = now
            # Capture oche distance at the moment a dart is first detected
            _dart_oche_distance_cm = (
                _tfluna.distance_cm
                if _tfluna is not None and _tfluna.connected and not _tfluna.is_stale
                else -1
            )
            socketio.emit('state', {'state': 'STABLE'})

        if collect_deadline > 0.0 and now >= collect_deadline:
            _throw_count += 1
            _dbg_lines = [
                f"\n{'='*64}",
                f"THROW #{_throw_count}  @  {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"{'='*64}",
            ]
            # ── Opportunistic scan ────────────────────────────────────────
            # For every active camera that hasn't contributed a tip yet
            # (i.e. didn't reach DART state in time), run a one-shot diff
            # scan on its current frame.  This ensures all cameras always
            # participate in locating the dart tip.
            for i, det in enumerate(detectors):
                if (det.active
                        and collected_tips[i] is None
                        and det.state not in (State.HAND, State.TAKEOUT)):
                    scan = det.try_opportunistic_scan(scored_mm=_consensus_scored_tips)
                    if scan is not None:
                        s_x, s_y, s_method = scan
                        collected_tips[i]    = (0.0, 0.0)  # placeholder (mm used directly)
                        collected_areas[i]   = 300
                        collected_methods[i] = s_method
                        collected_mm[i]      = (s_x, s_y)
                        print(f"[DET] Cam {i}: opportunistic {s_method} "
                              f"-> ({s_x:.1f},{s_y:.1f})mm")
                    else:
                        print(f"[DET] Cam {i}: opportunistic scan "
                              f"FAILED (state={det.state.name} "
                              f"motion={det.motion_change}px "
                              f"calibrated={det.cal.is_calibrated})")

            bullseye_mode = (
                _game_mode == 'bullseye'
                and _bullseye is not None
                and not _bullseye.is_finished
            )

            def _is_strong_tip_method(method: str) -> bool:
                return (method.startswith('LINE_FIT')
                        or method.startswith('HOUGH_LINE')
                        or method.startswith('POSE'))

            def _correct_bullseye_single_cam_coord(
                cam_idx: int,
                cv_mm: tuple[float, float],
                method: str,
                yolo_mm: tuple[float, float] | None,
            ) -> tuple[float, float]:
                def _blend(alpha: float) -> tuple[float, float]:
                    return (
                        (1.0 - alpha) * cv_mm[0] + alpha * yolo_mm[0],
                        (1.0 - alpha) * cv_mm[1] + alpha * yolo_mm[1],
                    )

                if yolo_mm is None:
                    return cv_mm
                dist = float(np.hypot(cv_mm[0] - yolo_mm[0], cv_mm[1] - yolo_mm[1]))
                if dist < 4.0 or dist > 35.0:
                    return cv_mm
                cv_r, cv_t = scorer.to_polar(cv_mm[0], cv_mm[1])
                yolo_r, yolo_t = scorer.to_polar(yolo_mm[0], yolo_mm[1])
                cv_label, _ = scorer.score_from_polar(cv_r, cv_t)
                yolo_label, _ = scorer.score_from_polar(yolo_r, yolo_t)

                # If CV and YOLO already agree on a non-bull segment, don't
                # invent a new wedge by extrapolating. Just tighten the point
                # between them and keep that same label.
                if cv_label == yolo_label and cv_label not in ('SB', 'DB'):
                    alpha = min(0.60, max(0.35, dist / 18.0))
                    return scorer._project_coord_into_label(_blend(alpha), cv_label)

                az = calibrators[cam_idx].camera_azimuth()
                if az is None or not _is_strong_tip_method(method):
                    alpha = min(0.55, max(0.30, dist / 30.0))
                    return _blend(alpha)

                axis = np.array([
                    math.cos(math.radians(az)),
                    math.sin(math.radians(az)),
                ], dtype=np.float64)
                delta = np.array([
                    yolo_mm[0] - cv_mm[0],
                    yolo_mm[1] - cv_mm[1],
                ], dtype=np.float64)
                along = float(delta[0] * axis[0] + delta[1] * axis[1])
                lateral = float(abs(delta[0] * (-axis[1]) + delta[1] * axis[0]))

                # A confirmed YOLO box centre is still above-plane geometry.
                # In bullseye single-camera mode, push it a little further
                # toward the camera azimuth to approximate the true contact point.
                if along < -2.0 or lateral > 18.0:
                    alpha = min(0.55, max(0.30, dist / 30.0))
                    return _blend(alpha)

                extra_mm = min(10.0, max(6.0, dist * 0.5))
                corrected = (
                    float(yolo_mm[0] + axis[0] * extra_mm),
                    float(yolo_mm[1] + axis[1] * extra_mm),
                )
                corr_r, corr_t = scorer.to_polar(corrected[0], corrected[1])
                corr_label, _ = scorer.score_from_polar(corr_r, corr_t)

                # Guard against collapsing an already-good outer/single read
                # inward into the bull just because the camera-axis push
                # points back toward centre.
                if (
                    corr_r < min(cv_r, yolo_r) - 4.0
                    or corr_r < max(cv_r, yolo_r) * 0.72
                    or (
                        corr_label in ('SB', 'DB')
                        and cv_label not in ('SB', 'DB')
                        and yolo_label not in ('SB', 'DB')
                    )
                ):
                    alpha = min(0.60, max(0.35, dist / 18.0))
                    keep_label = cv_label if cv_label == yolo_label else cv_label
                    return scorer._project_coord_into_label(_blend(alpha), keep_label)
                return corrected

            # ── Pre-consensus area filter ─────────────────────────────
            # Reject any camera with massive area (>8000px) that lacks
            # YOLO confirmation.  These are residual diff blobs (prior
            # dart, hand shadow, lighting shift) — not real darts.
            # Applies to ALL cameras, not just single-camera path.
            _MAX_AREA_NO_YOLO = 8000
            for i in range(len(collected_tips)):
                if (collected_tips[i] is not None
                        and collected_areas[i] > _MAX_AREA_NO_YOLO
                        and '+YOLO' not in (collected_methods[i] or '')):
                    if bullseye_mode and _is_strong_tip_method(collected_methods[i] or ''):
                        print(f"[DET] Cam {i}: keeping large-area strong CV "
                              f"(area={collected_areas[i]}) during bullseye")
                        continue
                    print(f"[DET] Cam {i}: FILTERED — "
                          f"area={collected_areas[i]} > {_MAX_AREA_NO_YOLO} "
                          f"without YOLO (likely residual diff)")
                    collected_tips[i] = None
                    collected_areas[i] = 0
                    collected_methods[i] = 'NONE'
                    collected_mm[i] = None
                    collected_yolo_mm[i] = None

            # ── Debug: per-camera CV snapshot ─────────────────────────
            _dbg_lines.append("--- CV Tips (post-opportunistic-scan) ---")
            for _di, _ddet in enumerate(detectors):
                if collected_tips[_di] is not None:
                    _dmm = (collected_mm[_di] if collected_mm[_di] is not None
                            else scorer.tip_to_board_mm(_di, collected_tips[_di]))
                    _dr, _dt = scorer.to_polar(_dmm[0], _dmm[1])
                    _dlbl, _dsc = scorer.score_from_polar(_dr, _dt)
                    _dpx = collected_tips[_di]
                    _dbg_lines.append(
                        f"  Cam {_di}: px=({_dpx[0]:.0f},{_dpx[1]:.0f})"
                        f"  mm=({_dmm[0]:+.1f},{_dmm[1]:+.1f})"
                        f"  r={_dr:.1f}mm  θ={_dt:.1f}°"
                        f"  → {_dlbl}({_dsc})"
                        f"  method={collected_methods[_di]}"
                    )
                else:
                    _dbg_lines.append(f"  Cam {_di}: NO TIP")

            # ── YOLO tip verification ─────────────────────────────────
            _dbg_lines.append("--- YOLO Verification ---")
            if yolo is not None:
                _YOLO_HARD_MISMATCH_MM = 120.0
                _BULLSEYE_LARGE_AREA_SUSPECT = 9000
                _BULLSEYE_YOLO_RESCUE_MIN_MM = 18.0
                _BULLSEYE_YOLO_RESCUE_MAX_MM = 85.0
                for i, det in enumerate(detectors):
                    if collected_tips[i] is None or det.last_frame is None:
                        continue
                    if not calibrators[i].is_calibrated:
                        continue

                    yres = yolo.verify_tip(
                        frame=det.last_frame,
                        calibrator=calibrators[i],
                        cv_tip_warped=collected_tips[i],
                        scored_tips_mm=list(_consensus_scored_tips),
                        cv_tip_method=collected_methods[i],
                    )

                    if yres is None:
                        collected_yolo_mm[i] = None
                        print(f"[YOLO] Cam {i}: no board-valid tip detected, keeping CV")
                        _dbg_lines.append(f"  Cam {i} YOLO: no board-valid tip")
                        continue

                    if yres.confirms:
                        # YOLO confirms the CV tip is real — boost method weight.
                        # CV position is kept as-is; YOLO never replaces it.
                        collected_methods[i] += '+YOLO'
                        collected_yolo_mm[i] = yres.nearest_mm
                        print(f"[YOLO] Cam {i}: CONFIRMED CV "
                              f"dist={yres.distance_mm:.1f}mm "
                              f"conf={yres.confidence:.2f}")
                        _dbg_lines.append(
                                f"  Cam {i} YOLO: CONFIRMED  dist={yres.distance_mm:.1f}mm"
                                f"  conf={yres.confidence:.2f}  → method now {collected_methods[i]}"
                            )
                    else:
                        # YOLO found something but it's too far from the CV tip.
                        if (
                            bullseye_mode
                            and collected_areas[i] >= _BULLSEYE_LARGE_AREA_SUSPECT
                            and _BULLSEYE_YOLO_RESCUE_MIN_MM <= yres.distance_mm <= _BULLSEYE_YOLO_RESCUE_MAX_MM
                        ):
                            yr, yt = scorer.to_polar(yres.nearest_mm[0], yres.nearest_mm[1])
                            ylbl, _ = scorer.score_from_polar(yr, yt)
                            rescued_mm = scorer._project_coord_into_label(yres.nearest_mm, ylbl)
                            collected_mm[i] = rescued_mm
                            collected_tips[i] = (0.0, 0.0)
                            collected_methods[i] += '+YOLO_RESCUE'
                            collected_yolo_mm[i] = yres.nearest_mm
                            print(f"[YOLO] Cam {i}: large-area CV rescued by YOLO "
                                  f"(area={collected_areas[i]} dist={yres.distance_mm:.1f}mm "
                                  f"-> {ylbl})")
                            _dbg_lines.append(
                                f"  Cam {i} YOLO: RESCUE  dist={yres.distance_mm:.1f}mm"
                                f"  conf={yres.confidence:.2f}"
                                f"  yolo_label={ylbl}  area={collected_areas[i]}"
                            )
                        elif bullseye_mode and yres.distance_mm >= _YOLO_HARD_MISMATCH_MM:
                            print(f"[YOLO] Cam {i}: REJECTED CV — far from YOLO "
                                  f"(dist={yres.distance_mm:.1f}mm)")
                            _dbg_lines.append(
                                f"  Cam {i} YOLO: HARD REJECT  dist={yres.distance_mm:.1f}mm"
                                f"  conf={yres.confidence:.2f}"
                            )
                            collected_tips[i] = None
                            collected_areas[i] = 0
                            collected_methods[i] = 'NONE'
                            collected_mm[i] = None
                            collected_yolo_mm[i] = None
                        elif (
                            bullseye_mode
                            and collected_areas[i] >= _BULLSEYE_LARGE_AREA_SUSPECT
                            and yres.distance_mm > _BULLSEYE_YOLO_RESCUE_MAX_MM
                        ):
                            print(f"[YOLO] Cam {i}: REJECTED huge-area CV — "
                                  f"YOLO mismatch too large "
                                  f"(area={collected_areas[i]} dist={yres.distance_mm:.1f}mm)")
                            _dbg_lines.append(
                                f"  Cam {i} YOLO: HUGE-AREA REJECT  dist={yres.distance_mm:.1f}mm"
                                f"  conf={yres.confidence:.2f}  area={collected_areas[i]}"
                            )
                            collected_tips[i] = None
                            collected_areas[i] = 0
                            collected_methods[i] = 'NONE'
                            collected_mm[i] = None
                            collected_yolo_mm[i] = None
                        else:
                            collected_yolo_mm[i] = None
                            # Keep CV as-is — don't boost, don't penalise.
                            print(f"[YOLO] Cam {i}: dart found but far from CV "
                                  f"(dist={yres.distance_mm:.1f}mm), keeping CV")
                            _dbg_lines.append(
                                f"  Cam {i} YOLO: far from CV  dist={yres.distance_mm:.1f}mm"
                                f"  conf={yres.confidence:.2f}  → CV kept"
                            )

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
                        # Pre-compute individual camera tip mm coords and
                        # median radius — used for candidate selection below.
                        individual_radii = []
                        individual_mm_list = []
                        for i, tip in enumerate(collected_tips):
                            if tip is not None:
                                _mm = (collected_mm[i] if (collected_mm and collected_mm[i] is not None)
                                       else scorer.tip_to_board_mm(i, tip))
                                individual_radii.append(float(np.hypot(_mm[0], _mm[1])))
                                individual_mm_list.append(_mm)
                        if individual_radii:
                            _sorted_r = sorted(individual_radii)
                            _mid = len(_sorted_r) // 2
                            if len(_sorted_r) % 2:
                                median_individual_r = _sorted_r[_mid]
                            else:
                                median_individual_r = (_sorted_r[_mid - 1] + _sorted_r[_mid]) / 2.0
                        else:
                            median_individual_r = None

                        # Average position of individual tips — used as the
                        # reference point for picking the best Xcam contour.
                        if individual_mm_list:
                            _ref_x = sum(m[0] for m in individual_mm_list) / len(individual_mm_list)
                            _ref_y = sum(m[1] for m in individual_mm_list) / len(individual_mm_list)
                        else:
                            _ref_x = _ref_y = None

                        cal0 = calibrators[0]

                        # Evaluate ALL contours and pick the best valid one —
                        # not necessarily the biggest.  When 2+ darts are already
                        # on the board the largest contour in the intersection can
                        # be a residual overlap from old-dart vibration, not the
                        # actual new tip.  We rank candidates by proximity to the
                        # individual camera readings and only discard contours
                        # that are effectively the exact same old hole.
                        _SCORED_DUPLICATE_MM = 8.0   # mm — exact old-dart residuals
                        _SCORED_NEAR_MM = 22.0       # mm — close old tips get a soft penalty
                        _RADIUS_TOL_MM  = 18.0   # mm — radius must match individual tips
                        _MIN_AREA       = 10

                        _dbg_lines.append("--- Cross-Camera Intersection ---")
                        _dbg_lines.append(
                            f"  Cams={mask_cam_ids}  contours={len(contours_xc)}"
                            f"  median_r={f'{median_individual_r:.1f}' if median_individual_r is not None else 'N/A'}mm"
                            f"  ref=({f'{_ref_x:+.1f}' if _ref_x is not None else 'N/A'}"
                            f",{f'{_ref_y:+.1f}' if _ref_y is not None else 'N/A'})mm"
                        )
                        if _consensus_scored_tips:
                            _dbg_lines.append(
                                f"  Scored tips on board: {[f'({sx:+.1f},{sy:+.1f})' for sx,sy in _consensus_scored_tips]}"
                            )

                        candidates = []
                        for _c in contours_xc:
                            _area = cv2.contourArea(_c)
                            if _area < _MIN_AREA:
                                continue
                            _M = cv2.moments(_c)
                            if _M['m00'] <= 0:
                                continue
                            _cx = _M['m10'] / _M['m00']
                            _cy = _M['m01'] / _M['m00']
                            _xm, _ym = cal0.board_px_to_mm(_cx, _cy)
                            _r = float(np.hypot(_xm, _ym))
                            if _r > 180.0:
                                _dbg_lines.append(
                                    f"  Contour: mm=({_xm:+.1f},{_ym:+.1f}) r={_r:.1f}mm"
                                    f" area={_area:.0f}px  → DISCARD (r>180)")
                                continue
                            _min_old_dist = min(
                                (np.hypot(_xm - sx, _ym - sy)
                                 for sx, sy in _consensus_scored_tips),
                                default=float('inf'),
                            )
                            if _min_old_dist < _SCORED_DUPLICATE_MM:
                                _old_dists = [f"({sx:+.1f},{sy:+.1f})d={np.hypot(_xm-sx,_ym-sy):.1f}mm"
                                              for sx,sy in _consensus_scored_tips]
                                _dbg_lines.append(
                                    f"  Contour: mm=({_xm:+.1f},{_ym:+.1f}) r={_r:.1f}mm"
                                    f" area={_area:.0f}px  → DISCARD (same old dart: {_old_dists})")
                                continue
                            # Radius sanity: must be close to individual tips.
                            if median_individual_r is not None:
                                if abs(_r - median_individual_r) > _RADIUS_TOL_MM:
                                    _dbg_lines.append(
                                        f"  Contour: mm=({_xm:+.1f},{_ym:+.1f}) r={_r:.1f}mm"
                                        f" area={_area:.0f}px  → DISCARD"
                                        f" (|r-medianR|={abs(_r-median_individual_r):.1f}mm > {_RADIUS_TOL_MM}mm)")
                                    continue
                            # Score candidate: prefer the one closest to the
                            # average of individual camera tips.
                            if _ref_x is not None:
                                _dist_to_ref = np.hypot(_xm - _ref_x, _ym - _ref_y)
                            else:
                                _dist_to_ref = 0.0
                            _near_old_penalty = 0.0
                            if _min_old_dist < _SCORED_NEAR_MM:
                                _near_old_penalty = _SCORED_NEAR_MM - _min_old_dist
                            _rank = _dist_to_ref + _near_old_penalty
                            _dbg_lines.append(
                                f"  Contour: mm=({_xm:+.1f},{_ym:+.1f}) r={_r:.1f}mm"
                                f" area={_area:.0f}px  dist_to_ref={_dist_to_ref:.1f}mm"
                                f" old_penalty={_near_old_penalty:.1f}  → CANDIDATE")
                            candidates.append((
                                _rank, _dist_to_ref, _near_old_penalty,
                                _min_old_dist, _area, _xm, _ym, _r,
                            ))

                        if candidates:
                            # Best = smallest distance to individual tips
                            # while only softly penalizing candidates near old darts.
                            candidates.sort(key=lambda t: (t[0], t[1], t[2], -t[4]))
                            _, _, _, _, area_xc, x_mm, y_mm, r_mm = candidates[0]
                            cross_tip_mm = (x_mm, y_mm)
                            _xc_r, _xc_t = scorer.to_polar(x_mm, y_mm)
                            _xc_lbl, _xc_sc = scorer.score_from_polar(_xc_r, _xc_t)
                            print(f"[Xcam] Cross-camera tip: "
                                  f"({x_mm:+.1f},{y_mm:+.1f})mm r={r_mm:.1f} "
                                  f"area={area_xc:.0f}px "
                                  f"from {len(board_masks)} cams ({mask_cam_ids})"
                                  + (f" individual_median_r={median_individual_r:.1f}mm"
                                     if median_individual_r is not None else ""))
                            _dbg_lines.append(
                                f"  Xcam SELECTED: mm=({x_mm:+.1f},{y_mm:+.1f})"
                                f"  r={r_mm:.1f}mm  θ={_xc_t:.1f}°"
                                f"  → {_xc_lbl}({_xc_sc})  area={area_xc:.0f}px"
                            )
                        else:
                            _discarded = len([c for c in contours_xc
                                              if cv2.contourArea(c) >= _MIN_AREA])
                            print(f"[Xcam] No valid candidates in intersection "
                                  f"({_discarded} contour(s) discarded — "
                                  f"old-dart residuals or radius mismatch)")
                            _dbg_lines.append(
                                f"  Xcam: NO VALID CANDIDATES"
                                f"  ({_discarded} discarded)"
                            )
                    else:
                        print(f"[Xcam] No contours in intersection "
                              f"of {len(board_masks)} masks")
                        _dbg_lines.append("  Xcam: NO CONTOURS in intersection")

            # ── Debug: log consensus inputs ───────────────────────────────
            _dbg_lines.append("--- Consensus Inputs ---")
            for _di in range(len(detectors)):
                if collected_tips[_di] is not None:
                    _cmm = (collected_mm[_di] if collected_mm[_di] is not None
                            else scorer.tip_to_board_mm(_di, collected_tips[_di]))
                    _cr, _ct = scorer.to_polar(_cmm[0], _cmm[1])
                    _clbl, _csc = scorer.score_from_polar(_cr, _ct)
                    _dbg_lines.append(
                        f"  Cam {_di}: mm=({_cmm[0]:+.1f},{_cmm[1]:+.1f})"
                        f"  r={_cr:.1f}mm  θ={_ct:.1f}°"
                        f"  → {_clbl}({_csc})  method={collected_methods[_di]}"
                    )
            if cross_tip_mm is not None:
                _xcr, _xct = scorer.to_polar(cross_tip_mm[0], cross_tip_mm[1])
                _xclbl, _xcsc = scorer.score_from_polar(_xcr, _xct)
                _dbg_lines.append(
                    f"  Xcam: mm=({cross_tip_mm[0]:+.1f},{cross_tip_mm[1]:+.1f})"
                    f"  r={_xcr:.1f}mm  θ={_xct:.1f}°  → {_xclbl}({_xcsc})"
                )
            else:
                _dbg_lines.append("  Xcam: none")

            if n_cams >= min_cams:
                label, score, coord = scorer.consensus(
                    collected_tips, collected_areas, collected_methods,
                    mm_coords_direct=collected_mm,
                    cross_camera_mm=cross_tip_mm)
                # ── Debug: write final result + dump to file ──────────────
                _dbg_lines.append("--- Final Result ---")
                _dbg_lines.append(
                    f"  RESULT: {label} = {score}"
                    f"  coord=({coord[0]:+.1f},{coord[1]:+.1f})mm"
                    f"  n_cams={n_cams}"
                )
                try:
                    with open(_DEBUG_LOG, "a", encoding="utf-8") as _df:
                        _df.write("\n".join(_dbg_lines) + "\n")
                except Exception as _de:
                    print(f"[DBG] Could not write debug log: {_de}")

                if score >= 0:
                    if label not in ('OFF', 'SKIP'):
                        cam_details = _build_cam_details()
                        timings = _log_detection_latency(
                            detectors,
                            collected_tips,
                            cfg,
                            collect_started_at=getattr(
                                _run_detection, "_collect_started_at", 0.0),
                            prefix="multi-cam",
                        )
                        scorer.broadcast(label, score, coord)
                        # Snapshot warped frames for confirmation thread
                        ref_snapshot = {
                            i: (det.warped_frame.copy()
                                if det.active and det.warped_frame is not None
                                else None)
                            for i, det in enumerate(detectors)
                        }
                        # Skip if a confirmation thread is already active —
                        # prevents hand-removal motion from ghost scoring
                        with _confirm_thread_lock:
                            threads_active = _confirm_thread_count
                        if threads_active > 0:
                            print(f"[CONFIRM] Scoring suppressed — "
                                  f"{threads_active} confirmation thread(s) active")
                        else:
                            with _confirm_thread_lock:
                                _confirm_thread_count += 1
                            t = threading.Thread(
                                target=_confirm_dart_async,
                                args=(
                                    label, score, coord, cam_details,
                                    detectors, calibrators,
                                    ref_snapshot,
                                    max(0.2, float(cfg.confirm_seconds)),
                                    DART_DIFF_THRESH,
                                    DART_MIN_BLOB_PX,
                                    timings,
                                    _dart_oche_distance_cm,
                                ),
                                daemon=True,
                            )
                            t.start()
                            did_score = True
                            print(f"[CONFIRM] Pending: {label} = {score} — "
                                  f"confirming in {cfg.confirm_seconds:.2f}s via thread")
                    else:
                        print(f"[SCR] Suppressed OFF — not emitting")
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
                # Single-camera area threshold:
                # PROFILE uses tighter area (very reliable blob shape).
                # All CV line-fit methods use a standard threshold.
                # With 2+ active cameras, require a higher area to reduce
                # false positives from uncorroborated single-cam readings.
                if single_method == 'PROFILE':
                    area_thresh = 120
                elif n_active >= 2:
                    area_thresh = 500
                else:
                    area_thresh = 300
                # Upper area guard: single-camera blobs above 8000px are
                # almost certainly residual diff (takeout, lighting, hand).
                # Real darts produce 800-4000px.  Allow up to 8000 if YOLO
                # confirmed (physical dart present), reject otherwise.
                _yolo_ok = '+YOLO' in single_method
                _area_too_large = (
                    single_area > 8000
                    and not _yolo_ok
                    and not (bullseye_mode and _is_strong_tip_method(single_method))
                )
                if _area_too_large:
                    print(f"[DET] Single camera (Cam {single_idx}, "
                          f"area={single_area} method={single_method}) "
                          f"— REJECTED (area>{8000} without YOLO, "
                          f"likely residual diff)")
                elif single_area >= area_thresh:
                    mm_direct_override = collected_mm
                    if bullseye_mode and _yolo_ok and collected_yolo_mm[single_idx] is not None:
                        mm_direct_override = list(collected_mm)
                        base_mm = (
                            collected_mm[single_idx]
                            if collected_mm[single_idx] is not None
                            else scorer.tip_to_board_mm(single_idx, collected_tips[single_idx])
                        )
                        corrected_mm = _correct_bullseye_single_cam_coord(
                            single_idx,
                            base_mm,
                            single_method,
                            collected_yolo_mm[single_idx],
                        )
                        mm_direct_override[single_idx] = corrected_mm
                        _base_r, _base_t = scorer.to_polar(base_mm[0], base_mm[1])
                        _base_lbl, _ = scorer.score_from_polar(_base_r, _base_t)
                        _corr_r, _corr_t = scorer.to_polar(corrected_mm[0], corrected_mm[1])
                        _corr_lbl, _ = scorer.score_from_polar(_corr_r, _corr_t)
                        _dbg_lines.append(
                            f"  Bullseye single-cam correct: CV=({base_mm[0]:+.1f},{base_mm[1]:+.1f})"
                            f" YOLO=({collected_yolo_mm[single_idx][0]:+.1f},{collected_yolo_mm[single_idx][1]:+.1f})"
                            f" {_base_lbl}->{_corr_lbl}"
                            f" -> ({corrected_mm[0]:+.1f},{corrected_mm[1]:+.1f})"
                        )
                    label, score, coord = scorer.consensus(
                        collected_tips, collected_areas, collected_methods,
                        mm_coords_direct=mm_direct_override,
                        cross_camera_mm=None)  # single-cam: no intersection
                    # Debug: log single-camera result
                    _dbg_lines.append(f"--- Single-Camera Path (Cam {single_idx}) ---")
                    _dbg_lines.append(
                        f"  method={single_method}  area={single_area}  thresh={area_thresh}"
                    )
                    _dbg_lines.append("--- Final Result ---")
                    _dbg_lines.append(
                        f"  RESULT: {label} = {score}"
                        f"  coord=({coord[0]:+.1f},{coord[1]:+.1f})mm"
                        f"  n_cams=1 (single)"
                    )
                    try:
                        with open(_DEBUG_LOG, "a", encoding="utf-8") as _df:
                            _df.write("\n".join(_dbg_lines) + "\n")
                    except Exception as _de:
                        print(f"[DBG] Could not write debug log: {_de}")

                    if score >= 0 and label not in ('OFF', 'SKIP'):
                        cam_details = _build_cam_details()
                        timings = _log_detection_latency(
                            detectors,
                            collected_tips,
                            cfg,
                            collect_started_at=getattr(
                                _run_detection, "_collect_started_at", 0.0),
                            prefix=f"single-cam#{single_idx}",
                        )
                        scorer.broadcast(label, score, coord)
                        # Snapshot warped frames for confirmation thread
                        ref_snapshot = {
                            i: (det.warped_frame.copy()
                                if det.active and det.warped_frame is not None
                                else None)
                            for i, det in enumerate(detectors)
                        }
                        # Skip if a confirmation thread is already active —
                        # prevents hand-removal motion from ghost scoring
                        with _confirm_thread_lock:
                            threads_active = _confirm_thread_count
                        if threads_active > 0:
                            print(f"[CONFIRM] Scoring suppressed — "
                                  f"{threads_active} confirmation thread(s) active")
                        else:
                            with _confirm_thread_lock:
                                _confirm_thread_count += 1
                            t = threading.Thread(
                                target=_confirm_dart_async,
                                args=(
                                    label, score, coord, cam_details,
                                    detectors, calibrators,
                                    ref_snapshot,
                                    max(0.2, float(cfg.confirm_seconds)),
                                    DART_DIFF_THRESH,
                                    DART_MIN_BLOB_PX,
                                    timings,
                                    _dart_oche_distance_cm,
                                ),
                                daemon=True,
                            )
                            t.start()
                            did_score = True
                            print(f"[CONFIRM] Pending: {label} = {score} — "
                                  f"confirming in {cfg.confirm_seconds:.2f}s via thread")
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

            collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
            collect_deadline = 0.0
            _run_detection._collect_started_at = 0.0
            if not did_score:
                socketio.emit('state', {'state': 'WAIT'})


        # ── TAKEOUT ───────────────────────────────────────────────────────
        # Only trigger if no camera detected DART and no collection pending
        any_takeout = any(s == State.TAKEOUT for s in states)
        any_dart_now = any(s == State.DART for s in states)
        if any_takeout and not any_dart_now and collect_deadline == 0.0:
            _full_det_reset(active_dets, _consensus_scored_tips)
            collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
            collect_deadline = 0.0
            _run_detection._collect_started_at = 0.0
            socketio.emit("takeout", {})
            # — user must click Continue

        # ── HAND — collective: if ANY camera sees hand, pause ALL ────────
        any_hand = any(d.state == State.HAND for d in active_dets)
        if any_hand:
            if _awaiting_takeout:
                # Reset the 1.5s practice hold timer — hand is back, wait again
                if _takeout_reason == 'practice' and _practice_reset_at > 0.0:
                    _practice_reset_at = 0.0
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
            collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
            collect_deadline = 0.0
            _run_detection._collect_started_at = 0.0
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
                # ── Verify darts were actually removed ───────────────────────
                # Compare each camera's current warped frame against the
                # snapshot taken when prepare_for_takeout() was called (which
                # had darts on board).  If current ≈ snapshot, the darts are
                # still there and the hand just left without taking them.
                # Only proceed when at least one camera shows a significant diff.
                _darts_gone = True
                _checked = 0
                for det in active_dets:
                    snap = _takeout_darts_snapshot.get(det.cam_id)
                    if snap is None or det.warped_frame is None:
                        continue
                    _checked += 1
                    _diff = cv2.absdiff(det.warped_frame, snap)
                    _mad  = float(np.mean(_diff))
                    if _mad < 4.0:
                        # Board looks the same as when darts were on it
                        _darts_gone = False
                        break
                if _checked > 0 and not _darts_gone:
                    # Hand left but darts still on board — keep waiting
                    pass
                elif _takeout_reason == 'practice':
                    # Practice: start a hold timer so the banner stays visible
                    if _practice_reset_at == 0.0:
                        _practice_reset_at = time.time() + 1.5
                        print("[PRACTICE] Board settled — resetting in 1.5 s")
                    elif time.time() >= _practice_reset_at:
                        # 1.5 s has elapsed — do the reset now
                        collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
                        collect_deadline = 0.0
                        _run_detection._collect_started_at = 0.0
                        _reset_practice_turn("auto_takeout")
                        print("[PRACTICE] Auto-reset after takeout")
                else:
                    # Game / bullseye takeout
                    _full_det_reset(active_dets, _consensus_scored_tips)
                    collected_tips, collected_areas, collected_methods, collected_mm, collected_yolo_mm = _empty_collected(len(detectors))
                    collect_deadline = 0.0
                    _run_detection._collect_started_at = 0.0
                    _takeout_hand_seen = False
                    if _takeout_reason == 'turn' and _pending_turn_state is not None:
                        if _is_x01_accuracy_review_active():
                            # Hold the next-player state until the user reviews
                            # the finished turn and explicitly continues.
                            _awaiting_takeout = False
                            _takeout_reason = ''
                            _turn_continue_pending = True
                            _detection_paused = True
                            socketio.emit("detection_state", {"paused": True})
                            socketio.emit('state', {'state': 'WAIT'})
                            print("[GAME] Turn takeout done — waiting for X01 review continue")
                            socketio.emit("takeout_ready", {"reason": "turn_review"})
                        else:
                            # Now it's safe to switch player — darts are gone
                            socketio.emit('game_state', _pending_turn_state)
                            socketio.emit('clear_board_dots', {})   # clear dart dots for new player
                            _advance_accuracy_turn("turn_auto_switch")
                            _pending_turn_state = None
                            _awaiting_takeout = False
                            _takeout_reason = ''
                            socketio.emit('state', {'state': 'WAIT'})
                            print("[GAME] Turn takeout done — switched to next player")
                    else:
                        # Bullseye path — show Continue button
                        print("[GAME] Takeout completed — darts removed, waiting for user to click Continue")
                        socketio.emit("takeout_ready", {"reason": "bullseye"})

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
    # Load persisted settings and apply them on top of defaults
    _saved_settings = _load_settings_from_disk()
    if _saved_settings:
        _apply_settings_to_cfg(cfg, _saved_settings)
        print(f"[CFG] Loaded settings from {SETTINGS_FILE}: resolution={cfg.resolution} fps={cfg.fps}")

    # Expose cfg globally so /api/settings works before the detection thread starts
    global _cfg
    _cfg = cfg

    if args.cameras:
        cam_ids = [int(c.strip()) for c in args.cameras.split(",")]
    elif args.demo:
        cam_ids = [0]
        cfg = ConfigManager(num_cameras=1, fps=cfg.fps)
        _cfg = cfg  # update global if demo mode changes cfg
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

