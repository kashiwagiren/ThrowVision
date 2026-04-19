"""Tests for match_review module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import match_review


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(match_review, "DATA_ROOT", tmp_path / "match_reviews")
    match_review._active.clear()
    yield
    match_review._active.clear()


def test_start_creates_frames_dir_and_active_record():
    match_review.start(
        game_id=42,
        mode="countup",
        players=[{"player": 1, "name": "Player 1"},
                 {"player": 2, "name": "Player 2"}],
        started_at=1776500000.0,
    )
    frames = match_review.DATA_ROOT / "match_42" / "frames"
    assert frames.is_dir()
    assert 42 in match_review._active
    rec = match_review._active[42]
    assert rec["mode"] == "countup"
    assert rec["status"] == "in_progress"
    assert len(rec["players"]) == 2


def test_has_review_true_when_json_exists():
    (match_review.DATA_ROOT / "match_7").mkdir(parents=True)
    (match_review.DATA_ROOT / "match_7.json").write_text("{}", encoding="utf-8")
    assert match_review.has_review(7) is True


def test_has_review_false_when_missing():
    assert match_review.has_review(999) is False


def test_delete_review_removes_json_and_folder():
    match_review.start(game_id=5, mode="x01",
                       players=[{"player": 1, "name": "P1"}],
                       started_at=0.0)
    match_review._flush(5)
    assert (match_review.DATA_ROOT / "match_5.json").exists()
    assert (match_review.DATA_ROOT / "match_5").exists()

    match_review.delete_review(5)

    assert not (match_review.DATA_ROOT / "match_5.json").exists()
    assert not (match_review.DATA_ROOT / "match_5").exists()


def test_delete_review_idempotent():
    match_review.delete_review(12345)  # no-op, no error
