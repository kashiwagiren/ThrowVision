"""Tests for the ThrowVision simulated-bot dart generator."""

import random

import pytest

import bot_player


def test_skill_levels_defined_with_expected_ordering():
    assert set(bot_player.SKILL_SIGMA_MM) == {"easy", "medium", "hard"}
    # Lower sigma = more accurate. Hard must tighten the spread from medium,
    # which tightens from easy.
    assert (
        bot_player.SKILL_SIGMA_MM["hard"]
        < bot_player.SKILL_SIGMA_MM["medium"]
        < bot_player.SKILL_SIGMA_MM["easy"]
    )


def test_auto_advance_dart_returns_miss():
    d = bot_player.auto_advance_dart()
    assert d["label"] == "MISS"
    assert d["score"] == 0
    assert d["bot"] is True
    assert d["mode"] == "auto_advance"


def test_simulated_dart_returns_labeled_result():
    random.seed(0)
    d = bot_player.simulated_dart("medium")
    for key in ("label", "score", "x_mm", "y_mm", "bot", "skill", "mode"):
        assert key in d
    assert isinstance(d["label"], str) and d["label"]
    assert isinstance(d["score"], int)
    assert d["bot"] is True
    assert d["mode"] == "simulated"
    assert d["skill"] == "medium"


def test_simulated_dart_accuracy_improves_with_skill():
    trials = 200
    target_x, target_y = bot_player._T20_TARGET_MM

    def avg_miss_distance(skill: str) -> float:
        random.seed(42)
        total = 0.0
        for _ in range(trials):
            d = bot_player.simulated_dart(skill)
            total += ((d["x_mm"] - target_x) ** 2
                      + (d["y_mm"] - target_y) ** 2) ** 0.5
        return total / trials

    hard = avg_miss_distance("hard")
    medium = avg_miss_distance("medium")
    easy = avg_miss_distance("easy")
    assert hard < medium < easy


def test_simulated_dart_unknown_skill_defaults_to_medium():
    random.seed(7)
    d = bot_player.simulated_dart("galaxy-brain")
    assert d["skill"] == "medium"


def test_generate_dart_dispatches_by_mode():
    random.seed(1)
    sim = bot_player.generate_dart("simulated", "hard")
    adv = bot_player.generate_dart("auto_advance", "hard")
    assert sim["mode"] == "simulated"
    assert adv["mode"] == "auto_advance"
    assert adv["label"] == "MISS"


def test_sanitize_bot_config_disabled_returns_none():
    assert bot_player.sanitize_bot_config(None) is None
    assert bot_player.sanitize_bot_config({}) is None
    assert bot_player.sanitize_bot_config({"enabled": False}) is None


def test_sanitize_bot_config_applies_defaults_and_caps():
    cfg = bot_player.sanitize_bot_config({"enabled": True})
    assert cfg == {
        "enabled": True,
        "mode": "simulated",
        "skill": "medium",
        "seat": 2,
        "name": "Bot",
    }


def test_sanitize_bot_config_normalizes_mode_skill_seat_name():
    cfg = bot_player.sanitize_bot_config({
        "enabled": True,
        "mode": "  AUTO_ADVANCE ",
        "skill": "HARD",
        "seat": "1",
        "name": "x" * 40,
    })
    assert cfg["mode"] == "auto_advance"
    assert cfg["skill"] == "hard"
    assert cfg["seat"] == 1
    assert cfg["name"] == "x" * 24


def test_sanitize_bot_config_rejects_invalid_seat_and_name():
    cfg = bot_player.sanitize_bot_config({
        "enabled": True,
        "seat": 5,
        "name": "   ",
    })
    assert cfg["seat"] == 2
    assert cfg["name"] == "Bot"
