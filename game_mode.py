"""ThrowVision – Game Mode Engine.

Contains all game-mode state machines:
  • BullseyeThrow  — determines which player throws first
  • GameX01        — standard X01 (301 / 501 / 701 / 901)
  • GameCricket    — close 15-20 + bull, score on open numbers
  • GameCountUp    — accumulate points over N rounds

Each class exposes:
    record_dart(label, score, coord)  → dict  (state update for frontend)
    state()                           → dict  (full snapshot)
    is_finished                       → bool
"""

from __future__ import annotations

import math
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ======================================================================
# Constants
# ======================================================================

BULL_INNER_R = 6.35        # mm – double-bull radius
BULL_OUTER_R = 15.9        # mm – single-bull radius
TIEBREAK_TOLERANCE = 1.0   # mm – distances within this are "equal"


def _collect_darts(turns, player: int | None = None):
    """Collect all individual darts from a list of turn dicts."""
    darts = []
    for t in turns:
        if player is None or t.get("player") == player:
            darts.extend(t.get("darts", []))
    return darts


def _player_summary(player_num: int, darts: list, **extra) -> dict:
    """Build a common player stats dict."""
    d = {
        "player": player_num,
        "total_darts": len(darts),
        "darts": [{"label": d["label"], "score": d["score"]} for d in darts],
    }
    d.update(extra)
    return d

CRICKET_NUMBERS = [15, 16, 17, 18, 19, 20, 25]  # 25 = bull

# Mapping label → marks for cricket (S=1, D=2, T=3)
def _cricket_marks(
    label: str,
    score: int,
    targets: Optional[List[int]] = None,
) -> Tuple[Optional[int], int]:
    """Return (target_number, marks) or (None, 0) if not a cricket target."""
    active_targets = set(targets or CRICKET_NUMBERS)
    if label == "BULL":
        return (25, 2) if 25 in active_targets else (None, 0)
    if label == "S25":
        return (25, 1) if 25 in active_targets else (None, 0)
    if not label or label == "OFF":
        return None, 0

    prefix = label[0]   # S, D, T
    try:
        num = int(label[1:])
    except (ValueError, IndexError):
        return None, 0

    if num not in active_targets:
        return None, 0

    marks = {"S": 1, "D": 2, "T": 3}.get(prefix, 0)
    return num, marks


# ======================================================================
# BullseyeThrow – who goes first?
# ======================================================================

class BullseyePhase(str, Enum):
    IDLE = "idle"
    PLAYER1_THROW = "player1_throw"
    PLAYER2_THROW = "player2_throw"
    RESULT = "result"
    TIEBREAK_P1 = "tiebreak_p1"
    TIEBREAK_P2 = "tiebreak_p2"


class BullseyeThrow:
    """State machine for the bullseye-throw first-player determination."""

    def __init__(self) -> None:
        self.phase = BullseyePhase.IDLE
        self.p1_distance: Optional[float] = None
        self.p2_distance: Optional[float] = None
        self.p1_label: Optional[str] = None
        self.p2_label: Optional[str] = None
        self.p1_coord: Optional[Tuple[float, float]] = None
        self.p2_coord: Optional[Tuple[float, float]] = None
        self.winner: Optional[int] = None  # 1 or 2
        self.tiebreak_count = 0

    # ------------------------------------------------------------------
    def start(self) -> dict:
        """Begin the sequence."""
        self.phase = BullseyePhase.PLAYER1_THROW
        self.p1_distance = None
        self.p2_distance = None
        self.p1_label = None
        self.p2_label = None
        self.p1_coord = None
        self.p2_coord = None
        self.winner = None
        return self.state()

    # ------------------------------------------------------------------
    def record_dart(self, label: str, score: int,
                    coord: Optional[Tuple[float, float]],
                    distance_mm: float) -> dict:
        """Record a bullseye throw.  Returns state update."""

        if self.phase in (BullseyePhase.PLAYER1_THROW,
                          BullseyePhase.TIEBREAK_P1):
            self.p1_distance = distance_mm
            self.p1_label = label
            self.p1_coord = coord

            # Exact bullseye → auto-win
            if distance_mm <= BULL_INNER_R:
                self.winner = 1
                self.phase = BullseyePhase.RESULT
                return self.state()

            # Move to player 2
            if self.phase == BullseyePhase.PLAYER1_THROW:
                self.phase = BullseyePhase.PLAYER2_THROW
            else:
                self.phase = BullseyePhase.TIEBREAK_P2
            return self.state()

        elif self.phase in (BullseyePhase.PLAYER2_THROW,
                            BullseyePhase.TIEBREAK_P2):
            self.p2_distance = distance_mm
            self.p2_label = label
            self.p2_coord = coord

            # Exact bullseye → auto-win
            if distance_mm <= BULL_INNER_R:
                self.winner = 2
                self.phase = BullseyePhase.RESULT
                return self.state()

            # Compare distances
            if self.p1_distance is not None:
                diff = abs(self.p1_distance - self.p2_distance)
                if diff <= TIEBREAK_TOLERANCE:
                    # Tie → re-throw
                    self.tiebreak_count += 1
                    self.phase = BullseyePhase.TIEBREAK_P1
                    return self.state()
                elif self.p1_distance < self.p2_distance:
                    self.winner = 1
                else:
                    self.winner = 2
                self.phase = BullseyePhase.RESULT

            return self.state()

        return self.state()

    # ------------------------------------------------------------------
    @property
    def is_finished(self) -> bool:
        return self.phase == BullseyePhase.RESULT

    def state(self) -> dict:
        return {
            "type": "bullseye",
            "phase": self.phase.value,
            "p1_distance": round(self.p1_distance, 1) if self.p1_distance is not None else None,
            "p2_distance": round(self.p2_distance, 1) if self.p2_distance is not None else None,
            "p1_label": self.p1_label,
            "p2_label": self.p2_label,
            "p1_coord": self.p1_coord,
            "p2_coord": self.p2_coord,
            "winner": self.winner,
            "tiebreak_count": self.tiebreak_count,
        }


# ======================================================================
# GameX01
# ======================================================================

class GameX01:
    """Standard X01 dart game (301 / 501 / 701 / 901)."""

    def __init__(
        self,
        starting_score: int = 501,
        finish_rule: str = "straight_out",
    ) -> None:
        self.starting_score = starting_score
        self.finish_rule = str(finish_rule or "straight_out")
        self.scores = [starting_score, starting_score]   # [p1, p2]
        self.current_player = 0                          # 0-indexed
        self.darts_this_turn: List[dict] = []
        self.turn_history: List[dict] = []               # all completed turns
        self.winner: Optional[int] = None                # 1 or 2
        self.started_at = time.time()
        self._turn_score_before = starting_score

    # ------------------------------------------------------------------
    def set_first_player(self, player: int) -> None:
        """Set who goes first (1 or 2 → stored as 0-indexed)."""
        self.current_player = player - 1

    def _is_double_finish(self, label: str) -> bool:
        normalized = str(label or "").upper()
        return normalized == "BULL" or normalized.startswith("D")

    def _normalize_review_dart(self, dart: dict) -> dict:
        """Normalize reviewed dart payloads into game-state dart objects."""
        label = str(dart.get("label") or "MISS").upper()
        score = int(dart.get("score") or 0)
        coord = dart.get("coord")
        if isinstance(coord, (list, tuple)) and len(coord) >= 2:
            coord = (coord[0], coord[1])
        else:
            coord = None
        if label in ("MISS", "BOUNCE", "FOUL", "OFF"):
            score = 0
        return {"label": label, "score": score, "coord": coord}

    def _simulate_review_turn(
        self,
        player_index: int,
        score_before: int,
        review_darts: List[dict],
        *,
        force_end: bool = False,
    ) -> dict:
        """Rebuild a turn from reviewed darts, optionally forcing turn end."""
        remaining_score = score_before
        built_darts: List[dict] = []
        busted = False
        winner = None

        review_darts = review_darts or []
        ordered_review_darts: List[Optional[dict]] = [None, None, None]
        used_indexes = set()

        for index, source in enumerate(review_darts):
            try:
                slot = int(source.get("turn_slot"))
            except (TypeError, ValueError):
                slot = 0
            if 1 <= slot <= 3 and ordered_review_darts[slot - 1] is None:
                ordered_review_darts[slot - 1] = source
                used_indexes.add(index)

        for index, source in enumerate(review_darts):
            if index in used_indexes:
                continue
            for slot_index in range(3):
                if ordered_review_darts[slot_index] is None:
                    ordered_review_darts[slot_index] = source
                    used_indexes.add(index)
                    break

        for source in [dart for dart in ordered_review_darts if dart is not None][:3]:
            dart = self._normalize_review_dart(source)
            label = dart["label"]
            score = dart["score"]

            if label in ("MISS", "BOUNCE", "FOUL", "OFF"):
                built_darts.append(dart)
            else:
                remaining = remaining_score - score
                bust = remaining < 0
                if self.finish_rule == "double_out":
                    if remaining == 1:
                        bust = True
                    elif remaining == 0 and not self._is_double_finish(label):
                        bust = True
                if bust:
                    dart["bust"] = True
                    built_darts.append(dart)
                    remaining_score = score_before
                    busted = True
                    break

                remaining_score = remaining
                built_darts.append(dart)
                if remaining == 0:
                    winner = player_index + 1
                    break

            if len(built_darts) >= 3:
                break

        completed = busted or winner is not None or len(built_darts) >= 3
        if force_end and built_darts:
            completed = True

        return {
            "darts": built_darts,
            "score_after": remaining_score,
            "busted": busted,
            "winner": winner,
            "completed": completed,
        }

    def apply_reviewed_current_turn(
        self,
        review_darts: List[dict],
        *,
        force_end: bool = False,
    ) -> dict:
        """Replace the live turn with reviewed darts, optionally ending it."""
        if self.winner is not None:
            return self.state()

        player_index = self.current_player
        simulated = self._simulate_review_turn(
            player_index,
            self._turn_score_before,
            review_darts,
            force_end=force_end,
        )

        self.scores[player_index] = simulated["score_after"]
        self.darts_this_turn = simulated["darts"]
        self.winner = simulated["winner"]

        if simulated["completed"]:
            self._end_turn(busted=simulated["busted"])

        return self.state()

    def apply_reviewed_last_turn(self, review_darts: List[dict]) -> dict:
        """Rewrite the most recently completed turn from reviewed darts."""
        if not self.turn_history:
            return self.state()

        last_turn = self.turn_history.pop()
        player_index = max(0, min(1, int(last_turn.get("player", 1)) - 1))
        score_before = int(last_turn.get("score_before", self.starting_score))
        simulated = self._simulate_review_turn(
            player_index,
            score_before,
            review_darts,
            force_end=True,
        )

        self.current_player = player_index
        self._turn_score_before = score_before
        self.scores[player_index] = simulated["score_after"]
        self.darts_this_turn = simulated["darts"]
        self.winner = simulated["winner"]

        if simulated["completed"]:
            self._end_turn(busted=simulated["busted"])

        return self.state()

    # ------------------------------------------------------------------
    def record_dart(self, label: str, score: int,
                    coord: Optional[Tuple[float, float]] = None) -> dict:
        """Record one dart.  Returns updated state."""
        if self.winner is not None:
            return self.state()
        # BOUNCE / MISS — counts as thrown dart, zero score, never bust
        if label in ('BOUNCE', 'MISS', 'FOUL'):
            dart = {"label": label, "score": 0, "coord": coord}
            self.darts_this_turn.append(dart)
            if len(self.darts_this_turn) >= 3:
                self._end_turn(busted=False)
            return self.state()

        dart = {"label": label, "score": score, "coord": coord}
        remaining = self.scores[self.current_player] - score

        is_double = self._is_double_finish(label)
        bust = False

        if remaining < 0:
            bust = True
        elif self.finish_rule == "double_out":
            if remaining == 1:
                bust = True
            elif remaining == 0 and not is_double:
                bust = True

        if bust:
            dart["bust"] = True
            self.darts_this_turn.append(dart)
            # Revert score to start of turn
            self.scores[self.current_player] = self._turn_score_before
            self._end_turn(busted=True)
            return self.state()

        # Valid dart
        self.scores[self.current_player] = remaining
        self.darts_this_turn.append(dart)

        # Check win
        if remaining == 0:
            self.winner = self.current_player + 1   # 1-indexed
            self._end_turn(busted=False)
            return self.state()

        # End turn after 3 darts
        if len(self.darts_this_turn) >= 3:
            self._end_turn(busted=False)

        return self.state()

    # ------------------------------------------------------------------
    def undo_dart(self) -> dict:
        """Undo the last dart thrown, including across a just-completed turn.

        When the 3rd dart triggers _end_turn(), darts_this_turn is emptied and
        current_player is switched *before* the server can call undo_dart().
        In that case we restore the completed turn from turn_history so the
        player stays the same and the score is fully reverted.
        """
        if not self.darts_this_turn:
            # Turn already ended — restore from history
            if not self.turn_history:
                return self.state()
            last_turn = self.turn_history.pop()
            # Switch back to the player who threw that turn
            self.current_player = last_turn["player"] - 1
            # Restore score to what it was at the START of that turn
            self.scores[self.current_player] = last_turn["score_before"]
            # Restore all darts of that turn EXCEPT the last one (the one being undone)
            restored_darts = list(last_turn["darts"])
            if restored_darts:
                restored_darts.pop()   # remove the dart we're undoing
            self.darts_this_turn = restored_darts
            # Reapply the remaining darts' scores
            for d in self.darts_this_turn:
                if not d.get("bust"):
                    self.scores[self.current_player] -= d["score"]
            self._turn_score_before = last_turn["score_before"]
            return self.state()
        dart = self.darts_this_turn.pop()
        if not dart.get("bust"):
            self.scores[self.current_player] += dart["score"]
        return self.state()

    # ------------------------------------------------------------------
    def _end_turn(self, busted: bool) -> None:
        turn_index = len(self.turn_history) + 1
        turn_total = sum(d["score"] for d in self.darts_this_turn
                         if not d.get("bust"))
        self.turn_history.append({
            "turn_index": turn_index,
            "player": self.current_player + 1,
            "darts": list(self.darts_this_turn),
            "total": turn_total if not busted else 0,
            "busted": busted,
            "score_before": self._turn_score_before,   # ← store for undo
        })
        self.darts_this_turn = []
        # Switch player
        self.current_player = 1 - self.current_player
        self._turn_score_before = self.scores[self.current_player]

    # ------------------------------------------------------------------
    @property
    def is_finished(self) -> bool:
        return self.winner is not None

    @property
    def total_darts(self) -> List[int]:
        """Total darts thrown per player."""
        counts = [0, 0]
        for turn in self.turn_history:
            counts[turn["player"] - 1] += len(turn["darts"])
        counts[self.current_player] += len(self.darts_this_turn)
        return counts

    def state(self) -> dict:
        return {
            "type": "x01",
            "starting_score": self.starting_score,
            "finish_rule": self.finish_rule,
            "scores": list(self.scores),
            "current_player": self.current_player + 1,  # 1-indexed
            "darts_this_turn": list(self.darts_this_turn),
            "turn_history": self.turn_history[-10:],     # last 10 turns
            "winner": self.winner,
            "total_darts": self.total_darts,
        }

    def stats_summary(self) -> dict:
        """Summary for the stats module."""
        def _avg(player):
            totals = [t["total"] for t in self.turn_history
                      if t["player"] == player and not t["busted"]]
            return round(sum(totals) / len(totals), 1) if totals else 0

        p1d = _collect_darts(self.turn_history, 1)
        p2d = _collect_darts(self.turn_history, 2)
        p1_rounds = sum(1 for t in self.turn_history if t["player"] == 1)
        p2_rounds = sum(1 for t in self.turn_history if t["player"] == 2)

        return {
            "mode": "x01",
            "starting_score": self.starting_score,
            "winner": self.winner,
            "started_at": self.started_at,
            "finished_at": time.time(),
            "players": [
                _player_summary(1, p1d, avg_per_round=_avg(1), rounds=p1_rounds),
                _player_summary(2, p2d, avg_per_round=_avg(2), rounds=p2_rounds),
            ],
        }


# ======================================================================
# GameCricket
# ======================================================================

class GameCricket:
    """Standard Cricket dart game.

    Close 15-20 and Bull (25).  Once a player closes a number the
    opponent hasn't, hits score points.  Win by closing everything with
    score ≥ opponent.
    """

    def __init__(
        self,
        variant: str = "standard",
        target_numbers: Optional[List[int]] = None,
    ) -> None:
        self.variant = str(variant or "standard")
        self.numbers = list(target_numbers or CRICKET_NUMBERS)
        # marks[player][number] = count of marks (0..3+)
        self.marks: List[Dict[int, int]] = [
            {n: 0 for n in self.numbers},
            {n: 0 for n in self.numbers},
        ]
        self.points = [0, 0]
        self.current_player = 0
        self.darts_this_turn: List[dict] = []
        self.turn_history: List[dict] = []
        self.winner: Optional[int] = None
        self.started_at = time.time()

    # ------------------------------------------------------------------
    def set_first_player(self, player: int) -> None:
        self.current_player = player - 1

    # ------------------------------------------------------------------
    def record_dart(self, label: str, score: int,
                    coord: Optional[Tuple[float, float]] = None) -> dict:
        if self.winner is not None:
            return self.state()
        # BOUNCE / MISS — counts as thrown dart, zero score, no marks
        if label in ('BOUNCE', 'MISS', 'FOUL'):
            dart = {"label": label, "score": 0, "coord": coord,
                    "target": None, "marks_added": 0, "points_added": 0}
            self.darts_this_turn.append(dart)
            if len(self.darts_this_turn) >= 3:
                self._end_turn()
            return self.state()

        target, raw_marks = _cricket_marks(label, score, self.numbers)
        dart = {
            "label": label, "score": score, "coord": coord,
            "target": target, "marks_added": 0, "points_added": 0,
        }

        if target is not None and raw_marks > 0:
            p = self.current_player
            opp = 1 - p
            current_marks = self.marks[p][target]
            needed = max(0, 3 - current_marks)

            if needed > 0:
                added = min(raw_marks, needed)
                self.marks[p][target] += added
                dart["marks_added"] = added
                raw_marks -= added

            # Remaining marks → score points (if opponent hasn't closed)
            if raw_marks > 0 and self.marks[opp][target] < 3:
                point_val = target if target != 25 else 25
                pts = raw_marks * point_val
                self.points[p] += pts
                dart["points_added"] = pts

        self.darts_this_turn.append(dart)

        # Check win: all targets closed AND score ≥ opponent
        if self._check_win(self.current_player):
            self.winner = self.current_player + 1
            self._end_turn()
            return self.state()

        if len(self.darts_this_turn) >= 3:
            self._end_turn()

        return self.state()

    # ------------------------------------------------------------------
    def undo_dart(self) -> dict:
        """Undo the last dart thrown, including across a just-completed turn."""
        if not self.darts_this_turn:
            if not self.turn_history:
                return self.state()
            last_turn = self.turn_history.pop()
            self.current_player = last_turn["player"] - 1
            # Restore marks and points snapshots from just before that turn
            if last_turn.get("marks_snapshot") and last_turn.get("points_snapshot"):
                # snapshot is AFTER the turn — reapply the previous snapshot
                # We don't store a pre-turn snapshot, so we reverse dartwise
                pass
            restored_darts = list(last_turn["darts"])
            p = self.current_player
            # Undo all darts of this turn from the stored marks/points snapshot
            if last_turn.get("marks_snapshot") and last_turn.get("points_snapshot"):
                # Restore marks and points to the start of that turn by undoing each dart
                for d in reversed(restored_darts):
                    if d["target"] is not None:
                        self.marks[p][d["target"]] -= d["marks_added"]
                        self.points[p] -= d["points_added"]
            # Remove only the last dart (the one being undone), keep the rest in-turn
            if restored_darts:
                restored_darts.pop()
            self.darts_this_turn = restored_darts
            return self.state()
        dart = self.darts_this_turn.pop()
        p = self.current_player
        if dart["target"] is not None:
            self.marks[p][dart["target"]] -= dart["marks_added"]
            self.points[p] -= dart["points_added"]
        return self.state()

    # ------------------------------------------------------------------
    def _check_win(self, player: int) -> bool:
        opp = 1 - player
        all_closed = all(self.marks[player][n] >= 3 for n in self.numbers)
        return all_closed and self.points[player] >= self.points[opp]

    def _end_turn(self) -> None:
        turn_index = len(self.turn_history) + 1
        self.turn_history.append({
            "turn_index": turn_index,
            "player": self.current_player + 1,
            "darts": list(self.darts_this_turn),
            "marks_snapshot": [{k: v for k, v in m.items()} for m in self.marks],
            "points_snapshot": list(self.points),
        })
        self.darts_this_turn = []
        self.current_player = 1 - self.current_player

    # ------------------------------------------------------------------
    @property
    def is_finished(self) -> bool:
        return self.winner is not None

    def state(self) -> dict:
        return {
            "type": "cricket",
            "variant": self.variant,
            "marks": [
                {str(k): v for k, v in self.marks[0].items()},
                {str(k): v for k, v in self.marks[1].items()},
            ],
            "points": list(self.points),
            "current_player": self.current_player + 1,
            "darts_this_turn": list(self.darts_this_turn),
            "turn_history": self.turn_history[-10:],
            "winner": self.winner,
            "numbers": list(self.numbers),
        }

    def stats_summary(self) -> dict:
        def _marks_per_round(player_num):
            turns = [t for t in self.turn_history if t["player"] == player_num]
            if not turns:
                return 0
            total_marks = sum(d.get("marks_added", 0)
                              for t in turns for d in t["darts"])
            return round(total_marks / len(turns), 1)

        return {
            "mode": "cricket",
            "winner": self.winner,
            "started_at": self.started_at,
            "finished_at": time.time(),
            "players": [
                _player_summary(1, _collect_darts(self.turn_history, 1),
                                points=self.points[0], marks_per_round=_marks_per_round(1)),
                _player_summary(2, _collect_darts(self.turn_history, 2),
                                points=self.points[1], marks_per_round=_marks_per_round(2)),
            ],
        }


# ======================================================================
# GameCountUp
# ======================================================================

class GameCountUp:
    """Count Up – accumulate points over N rounds.  Highest total wins."""

    def __init__(self, total_rounds: int = 8, variant: str = "standard") -> None:
        self.total_rounds = total_rounds
        self.variant = str(variant or "standard")
        self.scores = [0, 0]
        self.current_player = 0
        self.current_round = 1                           # 1-indexed
        self.darts_this_turn: List[dict] = []
        self.round_scores: List[List[dict]] = [[], []]   # per-player rounds
        self.turn_history: List[dict] = []
        self.winner: Optional[int] = None
        self.started_at = time.time()

    # ------------------------------------------------------------------
    def set_first_player(self, player: int) -> None:
        self.current_player = player - 1

    # ------------------------------------------------------------------
    def record_dart(self, label: str, score: int,
                    coord: Optional[Tuple[float, float]] = None) -> dict:
        if self.winner is not None:
            return self.state()
        # BOUNCE / MISS — counts as thrown dart, zero score
        if label in ('BOUNCE', 'MISS', 'FOUL'):
            multiplier = len(self.darts_this_turn) + 1 if self.variant == "multiple" else 1
            dart = {
                "label": label,
                "score": 0,
                "base_score": 0,
                "multiplier": multiplier,
                "coord": coord,
            }
            self.darts_this_turn.append(dart)
            if len(self.darts_this_turn) >= 3:
                self._end_turn()
            return self.state()

        multiplier = len(self.darts_this_turn) + 1 if self.variant == "multiple" else 1
        applied_score = score * multiplier
        dart = {
            "label": label,
            "score": applied_score,
            "base_score": score,
            "multiplier": multiplier,
            "coord": coord,
        }
        self.scores[self.current_player] += applied_score
        self.darts_this_turn.append(dart)

        if len(self.darts_this_turn) >= 3:
            self._end_turn()

        return self.state()

    # ------------------------------------------------------------------
    def undo_dart(self) -> dict:
        """Undo the last dart thrown, including across a just-completed turn."""
        if not self.darts_this_turn:
            if not self.turn_history:
                return self.state()
            last_turn = self.turn_history.pop()
            self.current_player = last_turn["player"] - 1
            # Remove this turn's total from the running score
            self.scores[self.current_player] -= last_turn["total"]
            # Restore all darts except the last one
            restored_darts = list(last_turn["darts"])
            if restored_darts:
                last_dart = restored_darts.pop()
                # Re-subtract the remaining darts (they stay in-turn)
                # Score was already removed above as a total; re-add back the kept ones
                for d in restored_darts:
                    self.scores[self.current_player] += d["score"]
            self.darts_this_turn = restored_darts
            return self.state()
        dart = self.darts_this_turn.pop()
        self.scores[self.current_player] -= dart["score"]
        return self.state()

    # ------------------------------------------------------------------
    def _end_turn(self) -> None:
        turn_total = sum(d["score"] for d in self.darts_this_turn)
        self.round_scores[self.current_player].append({
            "round": len(self.round_scores[self.current_player]) + 1,
            "darts": list(self.darts_this_turn),
            "total": turn_total,
        })
        turn_index = len(self.turn_history) + 1
        self.turn_history.append({
            "turn_index": turn_index,
            "player": self.current_player + 1,
            "round": len(self.round_scores[self.current_player]),
            "darts": list(self.darts_this_turn),
            "total": turn_total,
        })
        self.darts_this_turn = []

        # Check if game is over
        p1_done = len(self.round_scores[0]) >= self.total_rounds
        p2_done = len(self.round_scores[1]) >= self.total_rounds

        if p1_done and p2_done:
            if self.scores[0] > self.scores[1]:
                self.winner = 1
            elif self.scores[1] > self.scores[0]:
                self.winner = 2
            else:
                self.winner = 0  # tie
        else:
            # Switch player
            self.current_player = 1 - self.current_player

    # ------------------------------------------------------------------
    @property
    def is_finished(self) -> bool:
        return self.winner is not None

    @property
    def rounds_completed(self) -> List[int]:
        return [len(self.round_scores[0]), len(self.round_scores[1])]

    def state(self) -> dict:
        return {
            "type": "countup",
            "total_rounds": self.total_rounds,
            "variant": self.variant,
            "scores": list(self.scores),
            "current_player": self.current_player + 1,
            "current_round": max(len(self.round_scores[0]),
                                 len(self.round_scores[1])) + 1,
            "rounds_completed": self.rounds_completed,
            "darts_this_turn": list(self.darts_this_turn),
            "round_scores": [list(r) for r in self.round_scores],
            "turn_history": self.turn_history[-10:],
            "winner": self.winner,
        }

    def stats_summary(self) -> dict:
        def _avg(player_idx):
            rounds = self.round_scores[player_idx]
            if not rounds:
                return 0
            return round(sum(r["total"] for r in rounds) / len(rounds), 1)

        def _cu_darts(idx):
            darts = []
            for r in self.round_scores[idx]:
                darts.extend(r["darts"])
            return darts

        return {
            "mode": "countup",
            "total_rounds": self.total_rounds,
            "winner": self.winner,
            "started_at": self.started_at,
            "finished_at": time.time(),
            "players": [
                _player_summary(1, _cu_darts(0),
                                total_score=self.scores[0], avg_per_round=_avg(0)),
                _player_summary(2, _cu_darts(1),
                                total_score=self.scores[1], avg_per_round=_avg(1)),
            ],
        }
