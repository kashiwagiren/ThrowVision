"""ThrowVision – Practice accuracy session storage and aggregation."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


DATA_DIR = Path("data")
ACCURACY_DIR = DATA_DIR / "accuracy_sessions"
_LOCK = threading.RLock()

_AGREEMENT_KEYS = (
    "3cam_all_match",
    "3cam_two_match",
    "3cam_all_diff",
    "2cam_match",
    "2cam_disagree",
    "1cam_only",
    "no_detection",
)

_TIMING_OUTPUTS = {
    "detect_to_emit_ms": "avg_detect_to_score_ms",
    "motion_to_emit_ms": "avg_motion_to_score_ms",
    "collect_to_emit_ms": "avg_collect_to_score_ms",
    "pose_max_ms": "avg_pose_infer_ms",
    "queue_ms": "avg_queue_ms",
    "preprocess_ms": "avg_preprocess_ms",
    "decode_ms": "avg_decode_ms",
    "select_ms": "avg_select_ms",
}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


def _session_path(session_id: str) -> Path:
    return ACCURACY_DIR / f"{session_id}.json"


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _round1(value: Any) -> Optional[float]:
    num = _safe_float(value)
    return None if num is None else round(num, 1)


def _load_session_unlocked(session_id: str) -> Optional[dict]:
    path = _session_path(session_id)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_session_unlocked(session: dict) -> None:
    ACCURACY_DIR.mkdir(parents=True, exist_ok=True)
    with open(_session_path(session["id"]), "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)


def _sanitize_prediction(prediction: dict) -> dict:
    timings = prediction.get("timings") or {}
    clean_timings = {
        key: _round1(value)
        for key, value in timings.items()
        if _safe_float(value) is not None
    }
    return {
        "id": str(prediction.get("id") or _new_id("pred")),
        "ts": _safe_float(prediction.get("ts")) or _now(),
        "label": str(prediction.get("label") or ""),
        "score": _safe_int(prediction.get("score"), 0),
        "x_mm": _round1(prediction.get("x_mm")),
        "y_mm": _round1(prediction.get("y_mm")),
        "agreement_bucket": str(prediction.get("agreement_bucket") or ""),
        "cam_details": prediction.get("cam_details") or [],
        "timings": clean_timings,
        "model_name": prediction.get("model_name"),
        "execution_device": prediction.get("execution_device"),
        "detection_profile": prediction.get("detection_profile"),
    }


def _sanitize_actual(actual: dict) -> dict:
    label = str(actual.get("label") or "").upper()
    score = _safe_int(actual.get("score"), 0)
    prediction_id = actual.get("prediction_id")
    turn_slot = _safe_int(actual.get("turn_slot"), 0)
    return {
        "id": str(actual.get("id") or _new_id("actual")),
        "prediction_id": str(prediction_id) if prediction_id else None,
        "source": str(actual.get("source") or ("prediction" if prediction_id else "manual")),
        "label": label,
        "score": score,
        "multiplier": actual.get("multiplier"),
        "number": _safe_int(actual.get("number"), 0) if actual.get("number") is not None else None,
        "single_ring": actual.get("single_ring"),
        "turn_slot": turn_slot if 1 <= turn_slot <= 3 else None,
        "created_at": _safe_float(actual.get("created_at")) or _now(),
    }


def _ensure_review_defaults(turn: dict) -> None:
    review = turn.setdefault("review", {})
    review.setdefault("actual_darts", [])
    review.setdefault("submitted_at", None)
    review.setdefault("notes", "")


def _default_actuals(predictions: List[dict]) -> List[dict]:
    actuals: List[dict] = []
    for prediction in predictions:
        actuals.append(
            {
                "id": _new_id("actual"),
                "prediction_id": prediction["id"],
                "source": "prediction",
                "label": str(prediction.get("label") or ""),
                "score": _safe_int(prediction.get("score"), 0),
                "multiplier": None,
                "number": None,
                "single_ring": None,
                "created_at": _now(),
            }
        )
    return actuals


def _new_turn(index: int, started_reason: str = "session_start") -> dict:
    return {
        "id": _new_id("turn"),
        "index": index,
        "status": "open",
        "started_at": _now(),
        "ended_at": None,
        "started_reason": started_reason,
        "reset_reason": None,
        "predictions": [],
        "review": {
            "actual_darts": [],
            "submitted_at": None,
            "notes": "",
        },
    }


def _get_turn(session: dict, turn_id: str) -> Optional[dict]:
    for turn in session.get("turns", []):
        if turn.get("id") == turn_id:
            _ensure_review_defaults(turn)
            return turn
    return None


def _get_open_turn(session: dict) -> Optional[dict]:
    for turn in reversed(session.get("turns", [])):
        if turn.get("status") == "open":
            _ensure_review_defaults(turn)
            return turn
    return None


def start_session(
    *,
    model_name: str,
    execution_device: str,
    detection_profile: str,
    board_profile: str = "",
    metadata: Optional[dict] = None,
) -> dict:
    with _LOCK:
        session = {
            "id": _new_id("acc"),
            "started_at": _now(),
            "ended_at": None,
            "status": "active",
            "model_name": model_name,
            "execution_device": execution_device,
            "detection_profile": detection_profile,
            "board_profile": board_profile,
            "metadata": metadata or {},
            "turns": [],
        }
        turn = _new_turn(1)
        session["turns"].append(turn)
        _save_session_unlocked(session)
        return {"session": session, "turn": turn}


def create_turn(session_id: str, *, started_reason: str = "manual_reset") -> Optional[dict]:
    with _LOCK:
        session = _load_session_unlocked(session_id)
        if session is None:
            return None
        open_turn = _get_open_turn(session)
        if open_turn is not None:
            return open_turn
        turn = _new_turn(len(session.get("turns", [])) + 1, started_reason=started_reason)
        session.setdefault("turns", []).append(turn)
        _save_session_unlocked(session)
        return turn


def record_prediction(session_id: str, turn_id: str, prediction: dict) -> Optional[dict]:
    with _LOCK:
        session = _load_session_unlocked(session_id)
        if session is None:
            return None
        turn = _get_turn(session, turn_id) or _get_open_turn(session)
        if turn is None:
            turn = _new_turn(len(session.get("turns", [])) + 1, started_reason="implicit")
            session.setdefault("turns", []).append(turn)
        clean = _sanitize_prediction(prediction)
        turn.setdefault("predictions", []).append(clean)
        _save_session_unlocked(session)
        return clean


def update_review(
    session_id: str,
    turn_id: str,
    actual_darts: List[dict],
    *,
    notes: str = "",
) -> Optional[dict]:
    with _LOCK:
        session = _load_session_unlocked(session_id)
        if session is None:
            return None
        turn = _get_turn(session, turn_id)
        if turn is None:
            return None
        _ensure_review_defaults(turn)
        turn["review"]["actual_darts"] = [_sanitize_actual(item) for item in actual_darts]
        turn["review"]["notes"] = str(notes or "")
        turn["review"]["submitted_at"] = _now()
        _save_session_unlocked(session)
        return turn


def finalize_turn(session_id: str, turn_id: str, *, reset_reason: str = "manual_reset") -> Optional[dict]:
    with _LOCK:
        session = _load_session_unlocked(session_id)
        if session is None:
            return None
        turn = _get_turn(session, turn_id)
        if turn is None:
            return None
        _ensure_review_defaults(turn)
        if not turn["review"]["actual_darts"] and turn.get("predictions"):
            turn["review"]["actual_darts"] = _default_actuals(turn["predictions"])
        if turn["review"]["submitted_at"] is None:
            turn["review"]["submitted_at"] = _now()
        if turn.get("status") != "completed":
            turn["status"] = "completed"
            turn["ended_at"] = _now()
            turn["reset_reason"] = reset_reason
            _save_session_unlocked(session)
        return turn


def end_session(session_id: str, *, finalize_open_turn: bool = False) -> Optional[dict]:
    with _LOCK:
        session = _load_session_unlocked(session_id)
        if session is None:
            return None
        open_turn = _get_open_turn(session)
        if finalize_open_turn and open_turn is not None:
            _ensure_review_defaults(open_turn)
            if open_turn.get("predictions") or open_turn["review"]["actual_darts"]:
                if not open_turn["review"]["actual_darts"] and open_turn.get("predictions"):
                    open_turn["review"]["actual_darts"] = _default_actuals(open_turn["predictions"])
                if open_turn["review"]["submitted_at"] is None:
                    open_turn["review"]["submitted_at"] = _now()
                open_turn["status"] = "completed"
                open_turn["ended_at"] = _now()
                open_turn["reset_reason"] = "session_end"
        session["status"] = "ended"
        session["ended_at"] = _now()
        _save_session_unlocked(session)
        return session


def reset_sessions() -> int:
    """Delete all stored accuracy sessions and return the deleted count."""
    with _LOCK:
        if not ACCURACY_DIR.is_dir():
            return 0
        deleted = 0
        for path in ACCURACY_DIR.glob("*.json"):
            try:
                path.unlink()
                deleted += 1
            except OSError:
                continue
        return deleted


def get_session(session_id: str) -> Optional[dict]:
    with _LOCK:
        session = _load_session_unlocked(session_id)
        if session is None:
            return None
        result = dict(session)
        result["summary"] = _summarize_session(session)
        return result


def _timing_summary(samples: Dict[str, List[float]]) -> dict:
    out = {}
    for src_key, out_key in _TIMING_OUTPUTS.items():
        values = samples.get(src_key, [])
        out[out_key] = round(sum(values) / len(values), 2) if values else None
    return out


def _empty_metrics() -> dict:
    return {
        "turns": 0,
        "completed_turns": 0,
        "predicted_darts": 0,
        "actual_darts": 0,
        "matched_darts": 0,
        "missed_darts": 0,
        "false_positives": 0,
        "corrected_darts": 0,
        "uncorrected_darts": 0,
        "exact_label_matches": 0,
        "exact_score_matches": 0,
        "agreement_counts": {key: 0 for key in _AGREEMENT_KEYS},
        "timing_samples": {key: [] for key in _TIMING_OUTPUTS},
    }


def _apply_turn_metrics(target: dict, turn: dict) -> None:
    predictions = turn.get("predictions", [])
    review = turn.get("review", {}) or {}
    actuals = review.get("actual_darts", []) or []
    if not predictions and not actuals:
        return

    target["turns"] += 1
    if turn.get("status") == "completed":
        target["completed_turns"] += 1

    prediction_map = {item.get("id"): item for item in predictions}
    matched_prediction_ids = set()

    target["predicted_darts"] += len(predictions)
    target["actual_darts"] += len(actuals)

    for prediction in predictions:
        bucket = prediction.get("agreement_bucket")
        if bucket in target["agreement_counts"]:
            target["agreement_counts"][bucket] += 1
        timings = prediction.get("timings") or {}
        for key in _TIMING_OUTPUTS:
            value = _safe_float(timings.get(key))
            if value is not None:
                target["timing_samples"][key].append(value)

    for actual in actuals:
        prediction_id = actual.get("prediction_id")
        prediction = prediction_map.get(prediction_id)
        if prediction is None or prediction_id in matched_prediction_ids:
            target["missed_darts"] += 1
            target["corrected_darts"] += 1
            target["agreement_counts"]["no_detection"] += 1
            continue

        matched_prediction_ids.add(prediction_id)
        target["matched_darts"] += 1

        label_match = str(actual.get("label") or "") == str(prediction.get("label") or "")
        score_match = _safe_int(actual.get("score"), 0) == _safe_int(prediction.get("score"), 0)

        if label_match:
            target["exact_label_matches"] += 1
        if score_match:
            target["exact_score_matches"] += 1

        if label_match and score_match:
            target["uncorrected_darts"] += 1
        else:
            target["corrected_darts"] += 1

    false_positives = max(0, len(predictions) - len(matched_prediction_ids))
    target["false_positives"] += false_positives
    target["corrected_darts"] += false_positives


def _finalize_metrics(metrics: dict) -> dict:
    predicted = metrics["predicted_darts"]
    actual = metrics["actual_darts"]
    exact_labels = metrics["exact_label_matches"]
    exact_scores = metrics["exact_score_matches"]
    matched = metrics["matched_darts"]

    accuracy_pct = round((exact_labels / actual) * 100, 2) if actual else None
    score_accuracy_pct = round((exact_scores / actual) * 100, 2) if actual else None
    precision_pct = round((matched / predicted) * 100, 2) if predicted else None
    recall_pct = round((matched / actual) * 100, 2) if actual else None
    correction_rate_pct = round((metrics["corrected_darts"] / actual) * 100, 2) if actual else None
    miss_rate_pct = round((metrics["missed_darts"] / actual) * 100, 2) if actual else None
    false_positive_rate_pct = round((metrics["false_positives"] / predicted) * 100, 2) if predicted else None

    return {
        "turns": metrics["turns"],
        "completed_turns": metrics["completed_turns"],
        "predicted_darts": predicted,
        "actual_darts": actual,
        "matched_darts": matched,
        "missed_darts": metrics["missed_darts"],
        "false_positives": metrics["false_positives"],
        "corrected_darts": metrics["corrected_darts"],
        "uncorrected_darts": metrics["uncorrected_darts"],
        "exact_label_matches": exact_labels,
        "exact_score_matches": exact_scores,
        "accuracy_pct": accuracy_pct,
        "score_accuracy_pct": score_accuracy_pct,
        "precision_pct": precision_pct,
        "recall_pct": recall_pct,
        "correction_rate_pct": correction_rate_pct,
        "miss_rate_pct": miss_rate_pct,
        "false_positive_rate_pct": false_positive_rate_pct,
        "agreement_counts": dict(metrics["agreement_counts"]),
        **_timing_summary(metrics["timing_samples"]),
    }


def _summarize_session(session: dict) -> dict:
    metrics = _empty_metrics()
    for turn in session.get("turns", []):
        _apply_turn_metrics(metrics, turn)
    summary = _finalize_metrics(metrics)
    metadata = session.get("metadata") or {}
    summary.update(
        {
            "session_id": session.get("id"),
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "status": session.get("status"),
            "model_name": session.get("model_name") or "Unknown",
            "execution_device": session.get("execution_device") or "Unknown",
            "detection_profile": session.get("detection_profile") or "Unknown",
            "board_profile": session.get("board_profile") or "",
            "context": str(metadata.get("context") or "practice"),
            "session_mode": str(metadata.get("mode") or metadata.get("context") or "practice"),
        }
    )
    return summary


def get_sessions(limit: Optional[int] = 50) -> List[dict]:
    with _LOCK:
        if not ACCURACY_DIR.is_dir():
            return []
        sessions: List[dict] = []
        for path in sorted(ACCURACY_DIR.glob("*.json")):
            session = _load_session_unlocked(path.stem)
            if session is None:
                continue
            sessions.append(_summarize_session(session))
        sessions.sort(key=lambda item: float(item.get("started_at") or 0.0), reverse=True)
        if limit is not None:
            sessions = sessions[:limit]
        return sessions


def get_summary(limit_sessions: int = 50) -> dict:
    with _LOCK:
        if not ACCURACY_DIR.is_dir():
            return {
                "sessions": [],
                "sessions_count": 0,
                "active_model": None,
                "latest_session_id": None,
                **_finalize_metrics(_empty_metrics()),
            }

        full_sessions: List[dict] = []
        for path in ACCURACY_DIR.glob("*.json"):
            session = _load_session_unlocked(path.stem)
            if session is not None:
                full_sessions.append(session)

        full_sessions.sort(key=lambda item: float(item.get("started_at") or 0.0), reverse=True)

        metrics = _empty_metrics()
        for session in full_sessions:
            for turn in session.get("turns", []):
                _apply_turn_metrics(metrics, turn)

        latest = full_sessions[0] if full_sessions else None
        summary = _finalize_metrics(metrics)
        summary.update(
            {
                "active_model": latest.get("model_name") if latest else None,
                "latest_session_id": latest.get("id") if latest else None,
                "sessions_count": len(full_sessions),
                "sessions": [_summarize_session(session) for session in full_sessions[:limit_sessions]],
            }
        )
        return summary
