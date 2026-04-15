"""ThrowVision – Game Statistics Persistence.

Stores per-game results in ``data/stats.json`` and provides
aggregated statistics for each game mode.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


STATS_DIR = Path("data")
STATS_FILE = STATS_DIR / "stats.json"


def _ensure_ids(records: List[dict]) -> bool:
    changed = False
    used_ids = set()
    next_id = 1

    for record in records:
        raw_id = record.get("id", 0)
        try:
            record_id = int(raw_id or 0)
        except (TypeError, ValueError):
            record_id = 0

        if record_id <= 0 or record_id in used_ids:
            while next_id in used_ids:
                next_id += 1
            record["id"] = next_id
            used_ids.add(next_id)
            next_id += 1
            changed = True
        else:
            used_ids.add(record_id)
            next_id = max(next_id, record_id + 1)

    return changed


def _load_all() -> List[dict]:
    if not STATS_FILE.is_file():
        return []
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            records = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []
    if not isinstance(records, list):
        return []
    if _ensure_ids(records):
        _save_all(records)
    return records


def _save_all(records: List[dict]) -> None:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


def _next_id(records: List[dict]) -> int:
    ids = [int(r.get("id", 0) or 0) for r in records]
    return (max(ids) if ids else 0) + 1


# ======================================================================
# Public API
# ======================================================================

def save_game(summary: dict) -> None:
    """Append a game summary (from ``game.stats_summary()``)."""
    records = _load_all()
    summary["id"] = _next_id(records)
    records.append(summary)
    _save_all(records)
    print(f"[STATS] Saved game #{summary['id']} ({summary.get('mode', '?')})")


def delete_game(game_id: int) -> bool:
    """Delete one saved game by id."""
    records = _load_all()
    remaining = [r for r in records if int(r.get("id", 0) or 0) != int(game_id)]
    if len(remaining) == len(records):
        return False
    _save_all(remaining)
    return True


def reset_stats() -> int:
    """Clear all saved game history and return the deleted count."""
    records = _load_all()
    _save_all([])
    return len(records)


def get_recent(mode: Optional[str] = None, limit: int = 20) -> List[dict]:
    """Return the most recent games, optionally filtered by mode."""
    records = _load_all()
    if mode:
        records = [r for r in records if r.get("mode") == mode]
    return list(reversed(records[-limit:]))


def get_stats(mode: Optional[str] = None) -> dict:
    """Compute aggregated statistics."""
    records = _load_all()
    if mode:
        records = [r for r in records if r.get("mode") == mode]

    if not records:
        return {"games_played": 0, "mode": mode}

    result: Dict[str, Any] = {
        "mode": mode,
        "games_played": len(records),
    }

    _AGGREGATORS = {
        "x01": _aggregate_x01,
        "cricket": _aggregate_cricket,
        "countup": _aggregate_countup,
    }

    if mode and mode in _AGGREGATORS:
        result.update(_AGGREGATORS[mode](records))
    elif not mode:
        result["by_mode"] = {}
        for m, fn in _AGGREGATORS.items():
            mode_recs = [r for r in records if r.get("mode") == m]
            if mode_recs:
                agg = {"games_played": len(mode_recs)}
                agg.update(fn(mode_recs))
                result["by_mode"][m] = agg

    # Win counts
    p1_wins = sum(1 for r in records if r.get("winner") == 1)
    p2_wins = sum(1 for r in records if r.get("winner") == 2)
    result["p1_wins"] = p1_wins
    result["p2_wins"] = p2_wins

    # Recent games
    result["recent"] = list(reversed(records[-10:]))

    return result


# ======================================================================
# Mode-specific aggregation
# ======================================================================

def _all_darts(records: List[dict], player_idx: int = 0) -> List[dict]:
    """Collect all darts for a given player index across records."""
    darts = []
    for r in records:
        players = r.get("players", [])
        if player_idx < len(players):
            darts.extend(players[player_idx].get("darts", []))
    return darts


def _round_totals(records: List[dict], player_idx: int = 0) -> List[int]:
    """Collect per-round totals for a player across records."""
    totals = []
    for r in records:
        players = r.get("players", [])
        if player_idx < len(players):
            p = players[player_idx]
            # X01 uses avg_per_round directly, but we also want per-round scores
            avg = p.get("avg_per_round", 0)
            rounds = p.get("rounds", 0)
            if rounds > 0:
                totals.append(avg)  # approximate
    return totals


def _score_counts(darts: List[dict]) -> dict:
    """Count occurrences of notable score thresholds."""
    scores_180 = 0
    scores_140_plus = 0
    scores_100_plus = 0
    # Group into rounds of 3
    for i in range(0, len(darts), 3):
        chunk = darts[i:i+3]
        total = sum(d.get("score", 0) for d in chunk)
        if total == 180:
            scores_180 += 1
        if total >= 140:
            scores_140_plus += 1
        if total >= 100:
            scores_100_plus += 1
    return {
        "count_180": scores_180,
        "count_140_plus": scores_140_plus,
        "count_100_plus": scores_100_plus,
    }


def _combined_darts(records: List[dict]) -> List[dict]:
    """Collect all darts from both players across all records."""
    return _all_darts(records, 0) + _all_darts(records, 1)


def _round_averages(darts: List[dict]):
    """Compute per-round (3-dart) avg and highest from a flat darts list."""
    totals = []
    for i in range(0, len(darts), 3):
        chunk = darts[i:i+3]
        if len(chunk) == 3:
            totals.append(sum(d.get("score", 0) for d in chunk))
    avg = round(sum(totals) / len(totals), 1) if totals else 0
    highest = max(totals) if totals else 0
    return totals, avg, highest


def _hit_rates(darts: List[dict]) -> dict:
    """Compute hit rate per segment type."""
    counts = {"single": 0, "double": 0, "triple": 0,
              "bull": 0, "miss": 0, "total": len(darts)}
    for d in darts:
        label = d.get("label", "")
        if label in ("OFF", "MISS") or not label:
            counts["miss"] += 1
        elif label == "BULL":
            counts["bull"] += 1
        elif label.startswith("D"):
            counts["double"] += 1
        elif label.startswith("T"):
            counts["triple"] += 1
        elif label.startswith("S"):
            if label == "S25":
                counts["bull"] += 1
            else:
                counts["single"] += 1
    return counts


def _aggregate_x01(records: List[dict]) -> dict:
    all_darts = _combined_darts(records)

    all_scores = [d.get("score", 0) for d in all_darts]
    avg_per_dart = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0

    round_totals, avg_per_round, highest_round = _round_averages(all_darts)

    # First 9 darts average
    first9_totals = []
    for r in records:
        for p in r.get("players", []):
            darts = p.get("darts", [])[:9]
            if len(darts) >= 9:
                first9_totals.append(sum(d.get("score", 0) for d in darts))
    first9_avg = round(sum(first9_totals) / len(first9_totals), 1) if first9_totals else 0

    # Checkout %
    checkout_attempts = 0
    checkout_success = 0
    for r in records:
        if r.get("winner"):
            checkout_success += 1
        checkout_attempts += 1
    checkout_pct = round(checkout_success / checkout_attempts * 100, 1) if checkout_attempts else 0

    # Best game (fewest darts)
    best_darts = None
    for r in records:
        if r.get("winner"):
            winner_idx = r["winner"] - 1
            players = r.get("players", [])
            if winner_idx < len(players):
                total = players[winner_idx].get("total_darts", 999)
                if best_darts is None or total < best_darts:
                    best_darts = total

    return {
        "avg_per_dart": avg_per_dart,
        "avg_per_round": avg_per_round,
        "first9_avg": first9_avg,
        "checkout_pct": checkout_pct,
        "best_game_darts": best_darts,
        "highest_round": highest_round,
        "total_darts": len(all_darts),
        **_score_counts(all_darts),
        "hit_rates": _hit_rates(all_darts),
    }


def _aggregate_cricket(records: List[dict]) -> dict:
    all_darts = _combined_darts(records)

    # Average marks per round
    total_mpr = []
    for r in records:
        for p in r.get("players", []):
            mpr = p.get("marks_per_round", 0)
            if mpr > 0:
                total_mpr.append(mpr)
    avg_mpr = round(sum(total_mpr) / len(total_mpr), 2) if total_mpr else 0

    return {
        "avg_marks_per_round": avg_mpr,
        "total_darts": len(all_darts),
        "hit_rates": _hit_rates(all_darts),
    }


def _aggregate_countup(records: List[dict]) -> dict:
    all_darts = _combined_darts(records)

    all_scores = [d.get("score", 0) for d in all_darts]
    avg_per_dart = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0

    _, avg_per_round, highest_round = _round_averages(all_darts)

    best_score = max((p.get("total_score", 0) for r in records
                      for p in r.get("players", [])), default=0)

    return {
        "avg_per_dart": avg_per_dart,
        "avg_per_round": avg_per_round,
        "best_game_score": best_score,
        "highest_round": highest_round,
        "total_darts": len(all_darts),
        **_score_counts(all_darts),
        "hit_rates": _hit_rates(all_darts),
    }
