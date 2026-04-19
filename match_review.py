"""ThrowVision - Match Review persistence.

Stores per-match review bundles under ``data/match_reviews/``:
  data/match_reviews/match_<id>.json         (review data)
  data/match_reviews/match_<id>/frames/*.jpg (raw per-dart + EOT captures)

Thread-safe: all public functions hold ``_LOCK``.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np


DATA_ROOT = Path("data") / "match_reviews"

_LOCK = threading.RLock()
_active: Dict[int, Dict[str, Any]] = {}


def _match_dir(game_id: int) -> Path:
    return DATA_ROOT / f"match_{int(game_id)}"


def _match_json(game_id: int) -> Path:
    return DATA_ROOT / f"match_{int(game_id)}.json"


def _frames_dir(game_id: int) -> Path:
    return _match_dir(game_id) / "frames"


def _flush(game_id: int) -> None:
    """Write active[game_id] to disk atomically. Caller holds _LOCK."""
    rec = _active.get(int(game_id))
    if rec is None:
        return
    path = _match_json(game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    tmp.replace(path)


def start(
    game_id: int,
    mode: str,
    players: List[Dict[str, Any]],
    started_at: float,
) -> None:
    """Begin a new match review bundle."""
    gid = int(game_id)
    with _LOCK:
        _frames_dir(gid).mkdir(parents=True, exist_ok=True)
        _active[gid] = {
            "match_id": gid,
            "mode": mode,
            "status": "in_progress",
            "abandoned_reason": None,
            "started_at": float(started_at),
            "ended_at": None,
            "winner": None,
            "players": [
                {"player": int(p["player"]),
                 "name": p.get("name", f"Player {p['player']}"),
                 "turns": []}
                for p in players
            ],
        }
        _flush(gid)


def has_review(game_id: int) -> bool:
    return _match_json(game_id).is_file()


def delete_review(game_id: int) -> None:
    """Remove JSON + folder. Idempotent."""
    gid = int(game_id)
    with _LOCK:
        _active.pop(gid, None)
        j = _match_json(gid)
        d = _match_dir(gid)
        if j.exists():
            try:
                j.unlink()
            except OSError:
                pass
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


JPEG_QUALITY = 80


def _write_jpeg(path: Path, frame_bgr: np.ndarray) -> bool:
    """Encode + write a BGR frame as JPEG. Returns True on success."""
    try:
        ok, buf = cv2.imencode(".jpg", frame_bgr,
                                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(buf.tobytes())
        return True
    except Exception as exc:
        print(f"[MATCH_REVIEW] JPEG write failed {path}: {exc}")
        return False


def _ensure_turn(player_entry: Dict[str, Any], turn_idx: int,
                 round_num: int) -> Dict[str, Any]:
    for t in player_entry["turns"]:
        if t["index"] == turn_idx:
            return t
    turn = {"index": int(turn_idx), "round": int(round_num),
            "darts": [], "end_of_turn": None}
    player_entry["turns"].append(turn)
    return turn


def record_dart(
    game_id: int,
    player: int,
    turn_idx: int,
    round_num: int,
    dart_idx: int,
    prediction: Dict[str, Any],
    frames_bgr: Dict[int, np.ndarray],
) -> None:
    """Record a confirmed dart + write per-cam JPEGs + flush JSON."""
    gid = int(game_id)
    with _LOCK:
        rec = _active.get(gid)
        if rec is None:
            print(f"[MATCH_REVIEW] record_dart: no active match {gid}")
            return
        fdir = _frames_dir(gid)
        fdir.mkdir(parents=True, exist_ok=True)

        any_written = False
        for cam_idx, frame in (frames_bgr or {}).items():
            if frame is None:
                continue
            name = f"p{int(player)}_r{int(round_num)}_d{int(dart_idx)}_cam{int(cam_idx)}.jpg"
            if _write_jpeg(fdir / name, frame):
                any_written = True

        player_entry = next(p for p in rec["players"]
                            if p["player"] == int(player))
        turn = _ensure_turn(player_entry, turn_idx, round_num)
        turn["darts"].append({
            "dart_index": int(dart_idx),
            "ts": float(prediction.get("ts") or time.time()),
            "label": prediction.get("label", ""),
            "score": int(prediction.get("score", 0) or 0),
            "x_mm": prediction.get("x_mm"),
            "y_mm": prediction.get("y_mm"),
            "agreement_bucket": prediction.get("agreement_bucket"),
            "cam_details": list(prediction.get("cam_details") or []),
            "frames": {"per_dart": bool(any_written)},
        })
        _flush(gid)


def record_turn_end(
    game_id: int,
    player: int,
    turn_idx: int,
    round_num: int,
    frames_bgr: Dict[int, np.ndarray],
    ts: Optional[float] = None,
) -> None:
    """Record end-of-turn captures + flush JSON."""
    gid = int(game_id)
    with _LOCK:
        rec = _active.get(gid)
        if rec is None:
            print(f"[MATCH_REVIEW] record_turn_end: no active match {gid}")
            return
        fdir = _frames_dir(gid)
        fdir.mkdir(parents=True, exist_ok=True)

        any_written = False
        for cam_idx, frame in (frames_bgr or {}).items():
            if frame is None:
                continue
            name = f"p{int(player)}_r{int(round_num)}_eot_cam{int(cam_idx)}.jpg"
            if _write_jpeg(fdir / name, frame):
                any_written = True

        player_entry = next(p for p in rec["players"]
                            if p["player"] == int(player))
        turn = _ensure_turn(player_entry, turn_idx, round_num)
        turn["end_of_turn"] = {
            "frames": bool(any_written),
            "ts": float(ts if ts is not None else time.time()),
        }
        _flush(gid)


def get_review(game_id: int) -> Optional[Dict[str, Any]]:
    """Return review JSON for a completed/abandoned match, or None."""
    path = _match_json(game_id)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _find_dart(review: Dict[str, Any], player: int, round_num: int,
               dart_idx: int) -> Optional[Dict[str, Any]]:
    for p in review.get("players", []):
        if int(p.get("player", 0)) != int(player):
            continue
        for t in p.get("turns", []):
            if int(t.get("round", -1)) != int(round_num):
                continue
            for d in t.get("darts", []):
                if int(d.get("dart_index", -1)) == int(dart_idx):
                    return d
    return None


def render_annotated(
    game_id: int,
    kind: str,
    player: int,
    round_num: int,
    dart_idx: int,
    cam_idx: int,
) -> Optional[bytes]:
    """Render raw frame with detection overlay. Returns JPEG bytes or None."""
    if kind not in ("per_dart", "eot"):
        return None
    fdir = _frames_dir(game_id)
    if kind == "per_dart":
        path = fdir / f"p{int(player)}_r{int(round_num)}_d{int(dart_idx)}_cam{int(cam_idx)}.jpg"
    else:
        path = fdir / f"p{int(player)}_r{int(round_num)}_eot_cam{int(cam_idx)}.jpg"
    if not path.is_file():
        return None

    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None

    if kind == "per_dart":
        review = get_review(game_id)
        if review is not None:
            dart = _find_dart(review, player, round_num, dart_idx)
            if dart is not None:
                cam_detail = None
                for cd in dart.get("cam_details") or []:
                    if int(cd.get("cam", -1)) == int(cam_idx):
                        cam_detail = cd
                        break
                if cam_detail is not None:
                    px = cam_detail.get("x_px")
                    py = cam_detail.get("y_px")
                    if px is not None and py is not None:
                        px, py = int(px), int(py)
                        cv2.drawMarker(img, (px, py), (0, 255, 0),
                                       cv2.MARKER_CROSS, 24, 2)
                        cv2.circle(img, (px, py), 14, (0, 255, 0), 2)
                        label = str(cam_detail.get("label") or dart.get("label") or "")
                        cv2.putText(img, label, (px + 18, py - 10),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                                    (0, 255, 0), 2, cv2.LINE_AA)

    ok, buf = cv2.imencode(".jpg", img,
                           [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok:
        return None
    return buf.tobytes()


def finalize(
    game_id: int,
    summary: Dict[str, Any],
    abandoned: bool = False,
    reason: Optional[str] = None,
) -> None:
    """Close the match bundle. Safe to call when no active record."""
    gid = int(game_id)
    with _LOCK:
        rec = _active.get(gid)
        if rec is None:
            return
        rec["status"] = "abandoned" if abandoned else "completed"
        rec["abandoned_reason"] = reason if abandoned else None
        if summary and "winner" in summary:
            rec["winner"] = summary.get("winner")
        rec["ended_at"] = float((summary or {}).get("finished_at") or time.time())
        _flush(gid)
        _active.pop(gid, None)
