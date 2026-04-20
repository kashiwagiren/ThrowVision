"""Simulated bot darts for ThrowVision.

Two bot modes are supported:
  - ``simulated``   : rolls a virtual dart by drawing a 2D Gaussian offset
                     around an aim point (currently T20). Skill levels
                     ``easy`` / ``medium`` / ``hard`` control the sigma in
                     millimetres.
  - ``auto_advance``: emits a MISS for every bot dart. Useful when the human
                     wants to practise scoring solo without physically
                     throwing darts for the opponent.

Scoring is delegated to :class:`scorer.ScoreMapper` so bot darts look exactly
like human darts to the rest of the system. No camera / capture pipeline is
touched — callers are expected to feed the returned dart dict directly into
the game lifecycle (``_emit_dart``-style path).
"""

from __future__ import annotations

import random
from typing import Dict, Optional

from scorer import ScoreMapper


# Sigma (mm) of 2D Gaussian aim noise per skill level. Numbers chosen so
# "easy" sprays broadly (many MISS / S-area hits), "medium" lands in the
# scoring area most of the time, "hard" hits triples often.
SKILL_SIGMA_MM: Dict[str, float] = {
    "easy": 55.0,
    "medium": 28.0,
    "hard": 12.0,
}

BOT_MODES = ("simulated", "auto_advance")

# Aim point for a T20 throw: theta=90°, radius ~103 mm (middle of the triple
# ring on a regulation board).
_T20_TARGET_MM = (0.0, 103.0)


def _clamp_skill(skill: Optional[str]) -> str:
    s = (skill or "medium").strip().lower()
    return s if s in SKILL_SIGMA_MM else "medium"


def _gaussian_offset(sigma: float) -> "tuple[float, float]":
    return (random.gauss(0.0, sigma), random.gauss(0.0, sigma))


def simulated_dart(skill: str = "medium") -> Dict:
    """Return a virtual dart dict for the given skill level."""
    s = _clamp_skill(skill)
    sigma = SKILL_SIGMA_MM[s]
    ox, oy = _gaussian_offset(sigma)
    x = _T20_TARGET_MM[0] + ox
    y = _T20_TARGET_MM[1] + oy
    r, theta = ScoreMapper.to_polar(x, y)
    label, score = ScoreMapper.score_from_polar(r, theta)
    return {
        "label": label,
        "score": int(score),
        "x_mm": float(x),
        "y_mm": float(y),
        "bot": True,
        "skill": s,
        "mode": "simulated",
    }


def simulated_bullseye_dart(skill: str = "medium") -> Dict:
    """Return a virtual bullseye-aimed dart dict for the given skill level.

    Same Gaussian aim model as :func:`simulated_dart`, but the aim point is
    the board centre (0, 0) instead of T20. Includes a ``distance_mm`` field
    (distance from centre) so callers can feed it into BullseyeThrow.
    """
    s = _clamp_skill(skill)
    sigma = SKILL_SIGMA_MM[s]
    ox, oy = _gaussian_offset(sigma)
    x = float(ox)
    y = float(oy)
    r, theta = ScoreMapper.to_polar(x, y)
    label, score = ScoreMapper.score_from_polar(r, theta)
    return {
        "label": label,
        "score": int(score),
        "x_mm": x,
        "y_mm": y,
        "distance_mm": float((x * x + y * y) ** 0.5),
        "bot": True,
        "skill": s,
        "mode": "simulated",
    }


def auto_advance_bullseye_dart() -> Dict:
    """Return a guaranteed-miss bullseye dart (distance forced high)."""
    return {
        "label": "MISS",
        "score": 0,
        "x_mm": 0.0,
        "y_mm": 0.0,
        "distance_mm": 170.0,  # well outside the bull; will lose every time
        "bot": True,
        "skill": None,
        "mode": "auto_advance",
    }


def generate_bullseye_dart(mode: str = "simulated", skill: str = "medium") -> Dict:
    """Dispatch helper for the pre-game bullseye shootout."""
    m = (mode or "simulated").strip().lower()
    if m == "auto_advance":
        return auto_advance_bullseye_dart()
    return simulated_bullseye_dart(skill)


def auto_advance_dart() -> Dict:
    """Return a guaranteed-miss dart dict (score 0)."""
    return {
        "label": "MISS",
        "score": 0,
        "x_mm": 0.0,
        "y_mm": 0.0,
        "bot": True,
        "skill": None,
        "mode": "auto_advance",
    }


def generate_dart(mode: str = "simulated", skill: str = "medium") -> Dict:
    """Dispatch helper used by the server per-dart.

    Unknown ``mode`` falls back to ``simulated``. Unknown ``skill`` falls
    back to ``medium``.
    """
    m = (mode or "simulated").strip().lower()
    if m == "auto_advance":
        return auto_advance_dart()
    return simulated_dart(skill)


def sanitize_bot_config(raw) -> Optional[Dict]:
    """Normalize a client-supplied ``bot_config`` payload.

    Returns ``None`` when the bot is not enabled. Otherwise returns a dict
    with keys ``enabled`` (True), ``mode`` (one of BOT_MODES), ``skill``
    (one of SKILL_SIGMA_MM keys), ``seat`` (1 or 2 — which scoreboard seat
    the bot occupies), and ``name`` (<=24 chars, defaults to "Bot").
    """
    if not isinstance(raw, dict):
        return None
    if not raw.get("enabled"):
        return None
    mode = str(raw.get("mode", "simulated")).strip().lower()
    if mode not in BOT_MODES:
        mode = "simulated"
    skill = _clamp_skill(raw.get("skill"))
    seat_raw = raw.get("seat", 2)
    try:
        seat = int(seat_raw)
    except (TypeError, ValueError):
        seat = 2
    if seat not in (1, 2):
        seat = 2
    name_raw = raw.get("name", "")
    if isinstance(name_raw, str):
        name = name_raw.strip()[:24] or "Bot"
    else:
        name = "Bot"
    return {
        "enabled": True,
        "mode": mode,
        "skill": skill,
        "seat": seat,
        "name": name,
    }
