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


def _load_all() -> List[dict]:
    if not STATS_FILE.is_file():
        return []
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_all(records: List[dict]) -> None:
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)


# ======================================================================
# Public API
# ======================================================================

def save_game(summary: dict) -> None:
    """Append a game summary (from ``game.stats_summary()``)."""
    records = _load_all()
    summary["id"] = len(records) + 1
    records.append(summary)
    _save_all(records)
    print(f"[STATS] Saved game #{summary['id']} ({summary.get('mode', '?')})")


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

    if mode == "x01":
        result.update(_aggregate_x01(records))
    elif mode == "cricket":
        result.update(_aggregate_cricket(records))
    elif mode == "countup":
        result.update(_aggregate_countup(records))
    else:
        # Overall — combine everything
        result["by_mode"] = {}
        for m in ("x01", "cricket", "countup"):
            mode_recs = [r for r in records if r.get("mode") == m]
            if mode_recs:
                agg = {"games_played": len(mode_recs)}
                if m == "x01":
                    agg.update(_aggregate_x01(mode_recs))
                elif m == "cricket":
                    agg.update(_aggregate_cricket(mode_recs))
                elif m == "countup":
                    agg.update(_aggregate_countup(mode_recs))
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


def _hit_rates(darts: List[dict]) -> dict:
    """Compute hit rate per segment type."""
    counts = {"single": 0, "double": 0, "triple": 0,
              "bull": 0, "miss": 0, "total": len(darts)}
    for d in darts:
        label = d.get("label", "")
        if label == "OFF" or not label:
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
    all_darts_p1 = _all_darts(records, 0)
    all_darts_p2 = _all_darts(records, 1)
    all_darts = all_darts_p1 + all_darts_p2

    # Per-dart average
    all_scores = [d.get("score", 0) for d in all_darts]
    avg_per_dart = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0

    # Per-round (3-dart) average
    round_totals = []
    for i in range(0, len(all_darts), 3):
        chunk = all_darts[i:i+3]
        if len(chunk) == 3:
            round_totals.append(sum(d.get("score", 0) for d in chunk))
    avg_per_round = round(sum(round_totals) / len(round_totals), 1) if round_totals else 0

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

    # Highest round
    highest_round = max(round_totals) if round_totals else 0

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
    all_darts_p1 = _all_darts(records, 0)
    all_darts_p2 = _all_darts(records, 1)
    all_darts = all_darts_p1 + all_darts_p2

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
    all_darts_p1 = _all_darts(records, 0)
    all_darts_p2 = _all_darts(records, 1)
    all_darts = all_darts_p1 + all_darts_p2

    all_scores = [d.get("score", 0) for d in all_darts]
    avg_per_dart = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0

    # Per-round average
    round_totals = []
    for i in range(0, len(all_darts), 3):
        chunk = all_darts[i:i+3]
        if len(chunk) == 3:
            round_totals.append(sum(d.get("score", 0) for d in chunk))
    avg_per_round = round(sum(round_totals) / len(round_totals), 1) if round_totals else 0

    # Best game score
    best_score = 0
    for r in records:
        for p in r.get("players", []):
            total = p.get("total_score", 0)
            if total > best_score:
                best_score = total

    highest_round = max(round_totals) if round_totals else 0

    return {
        "avg_per_dart": avg_per_dart,
        "avg_per_round": avg_per_round,
        "best_game_score": best_score,
        "highest_round": highest_round,
        "total_darts": len(all_darts),
        **_score_counts(all_darts),
        "hit_rates": _hit_rates(all_darts),
    }
