"""Tests for stats.reserve_id()."""
from __future__ import annotations

import json

import pytest

import stats


@pytest.fixture(autouse=True)
def _tmp_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "STATS_DIR", tmp_path)
    monkeypatch.setattr(stats, "STATS_FILE", tmp_path / "stats.json")
    yield


def test_reserve_id_on_empty_returns_one():
    assert stats.reserve_id() == 1


def test_reserve_id_is_one_past_max():
    stats.STATS_FILE.write_text(
        json.dumps([{"id": 1}, {"id": 4}, {"id": 2}]),
        encoding="utf-8",
    )
    assert stats.reserve_id() == 5


def test_reserve_id_then_save_matches_reserved():
    rid = stats.reserve_id()
    summary = {"id": rid, "mode": "x01", "winner": 1}
    stats.save_game(summary)

    data = json.loads(stats.STATS_FILE.read_text(encoding="utf-8"))
    assert data[-1]["id"] == rid
