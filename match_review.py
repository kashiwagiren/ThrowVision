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
