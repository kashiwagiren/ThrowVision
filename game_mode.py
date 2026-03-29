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
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ======================================================================
# Constants
# ======================================================================

BULL_INNER_R = 6.35        # mm – double-bull radius
BULL_OUTER_R = 15.9        # mm – single-bull radius
TIEBREAK_TOLERANCE = 1.0   # mm – distances within this are "equal"

CRICKET_NUMBERS = [15, 16, 17, 18, 19, 20, 25]  # 25 = bull

# Mapping label → marks for cricket (S=1, D=2, T=3)
def _cricket_marks(label: str, score: int) -> Tuple[Optional[int], int]:
    """Return (target_number, marks) or (None, 0) if not a cricket target."""
    if label == "BULL":
        return 25, 2   # double bull = 2 marks
    if label == "S25":
        return 25, 1   # single bull = 1 mark
    if not label or label == "OFF":
        return None, 0

    prefix = label[0]   # S, D, T
    try:
        num = int(label[1:])
    except (ValueError, IndexError):
        return None, 0

    if num not in (15, 16, 17, 18, 19, 20):
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

@dataclass
class _X01Turn:
    darts: List[dict] = field(default_factory=list)   # [{label, score, coord}]
    score_before: int = 0


class GameX01:
    """Standard X01 dart game (301 / 501 / 701 / 901)."""

    def __init__(self, starting_score: int = 501) -> None:
        self.starting_score = starting_score
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

    # ------------------------------------------------------------------
    def record_dart(self, label: str, score: int,
                    coord: Optional[Tuple[float, float]] = None) -> dict:
        """Record one dart.  Returns updated state."""
        if self.winner is not None:
            return self.state()

        dart = {"label": label, "score": score, "coord": coord}
        remaining = self.scores[self.current_player] - score

        # --- Bust check ---
        is_double = (label.startswith("D") or label == "BULL")
        bust = False

        if remaining < 0:
            bust = True
        elif remaining == 1:
            bust = True          # can't finish on 1 (no valid double exists for 0.5)

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
        turn_total = sum(d["score"] for d in self.darts_this_turn
                         if not d.get("bust"))
        self.turn_history.append({
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
            "scores": list(self.scores),
            "current_player": self.current_player + 1,  # 1-indexed
            "darts_this_turn": list(self.darts_this_turn),
            "turn_history": self.turn_history[-10:],     # last 10 turns
            "winner": self.winner,
            "total_darts": self.total_darts,
        }

    def stats_summary(self) -> dict:
        """Summary for the stats module."""
        all_turns = self.turn_history
        p1_turns = [t for t in all_turns if t["player"] == 1]
        p2_turns = [t for t in all_turns if t["player"] == 2]

        def _avg(turns):
            totals = [t["total"] for t in turns if not t["busted"]]
            return round(sum(totals) / len(totals), 1) if totals else 0

        def _darts_list(turns):
            darts = []
            for t in turns:
                darts.extend(t["darts"])
            return darts

        p1_darts = _darts_list(p1_turns)
        p2_darts = _darts_list(p2_turns)

        return {
            "mode": "x01",
            "starting_score": self.starting_score,
            "winner": self.winner,
            "started_at": self.started_at,
            "finished_at": time.time(),
            "players": [
                {
                    "player": 1,
                    "avg_per_round": _avg(p1_turns),
                    "total_darts": len(p1_darts),
                    "rounds": len(p1_turns),
                    "darts": [{"label": d["label"], "score": d["score"]}
                              for d in p1_darts],
                },
                {
                    "player": 2,
                    "avg_per_round": _avg(p2_turns),
                    "total_darts": len(p2_darts),
                    "rounds": len(p2_turns),
                    "darts": [{"label": d["label"], "score": d["score"]}
                              for d in p2_darts],
                },
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

    def __init__(self) -> None:
        # marks[player][number] = count of marks (0..3+)
        self.marks: List[Dict[int, int]] = [
            {n: 0 for n in CRICKET_NUMBERS},
            {n: 0 for n in CRICKET_NUMBERS},
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

        target, raw_marks = _cricket_marks(label, score)
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
        all_closed = all(self.marks[player][n] >= 3 for n in CRICKET_NUMBERS)
        return all_closed and self.points[player] >= self.points[opp]

    def _end_turn(self) -> None:
        self.turn_history.append({
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
            "marks": [
                {str(k): v for k, v in self.marks[0].items()},
                {str(k): v for k, v in self.marks[1].items()},
            ],
            "points": list(self.points),
            "current_player": self.current_player + 1,
            "darts_this_turn": list(self.darts_this_turn),
            "turn_history": self.turn_history[-10:],
            "winner": self.winner,
            "numbers": CRICKET_NUMBERS,
        }

    def stats_summary(self) -> dict:
        def _darts_list(player_num):
            darts = []
            for t in self.turn_history:
                if t["player"] == player_num:
                    darts.extend(t["darts"])
            return darts

        p1_darts = _darts_list(1)
        p2_darts = _darts_list(2)

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
                {
                    "player": 1,
                    "points": self.points[0],
                    "marks_per_round": _marks_per_round(1),
                    "total_darts": len(p1_darts),
                    "darts": [{"label": d["label"], "score": d["score"]}
                              for d in p1_darts],
                },
                {
                    "player": 2,
                    "points": self.points[1],
                    "marks_per_round": _marks_per_round(2),
                    "total_darts": len(p2_darts),
                    "darts": [{"label": d["label"], "score": d["score"]}
                              for d in p2_darts],
                },
            ],
        }


# ======================================================================
# GameCountUp
# ======================================================================

class GameCountUp:
    """Count Up – accumulate points over N rounds.  Highest total wins."""

    def __init__(self, total_rounds: int = 8) -> None:
        self.total_rounds = total_rounds
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

        dart = {"label": label, "score": score, "coord": coord}
        self.scores[self.current_player] += score
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
        self.turn_history.append({
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
        def _darts_list(player_idx):
            darts = []
            for r in self.round_scores[player_idx]:
                darts.extend(r["darts"])
            return darts

        def _avg(player_idx):
            rounds = self.round_scores[player_idx]
            if not rounds:
                return 0
            return round(sum(r["total"] for r in rounds) / len(rounds), 1)

        return {
            "mode": "countup",
            "total_rounds": self.total_rounds,
            "winner": self.winner,
            "started_at": self.started_at,
            "finished_at": time.time(),
            "players": [
                {
                    "player": 1,
                    "total_score": self.scores[0],
                    "avg_per_round": _avg(0),
                    "total_darts": len(_darts_list(0)),
                    "darts": [{"label": d["label"], "score": d["score"]}
                              for d in _darts_list(0)],
                },
                {
                    "player": 2,
                    "total_score": self.scores[1],
                    "avg_per_round": _avg(1),
                    "total_darts": len(_darts_list(1)),
                    "darts": [{"label": d["label"], "score": d["score"]}
                              for d in _darts_list(1)],
                },
            ],
        }
