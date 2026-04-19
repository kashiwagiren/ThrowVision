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


def _fake_frame():
    import numpy as np
    return (np.ones((10, 10, 3), dtype=np.uint8) * 127)


def test_record_dart_writes_three_jpegs_and_appends():
    match_review.start(
        game_id=1, mode="x01",
        players=[{"player": 1, "name": "P1"},
                 {"player": 2, "name": "P2"}],
        started_at=0.0,
    )
    prediction = {
        "label": "T20", "score": 60,
        "x_mm": -12.3, "y_mm": -98.1,
        "agreement_bucket": "3cam_all_match",
        "cam_details": [
            {"cam": 0, "label": "T20", "score": 60, "x_mm": -12.0, "y_mm": -98.0, "used": True},
            {"cam": 1, "label": "T20", "score": 60, "x_mm": -12.5, "y_mm": -98.2, "used": True},
            {"cam": 2, "label": "T20", "score": 60, "x_mm": -12.3, "y_mm": -98.1, "used": True},
        ],
        "ts": 1776500012.5,
    }
    frames = {0: _fake_frame(), 1: _fake_frame(), 2: _fake_frame()}

    match_review.record_dart(
        game_id=1, player=1, turn_idx=0, round_num=1,
        dart_idx=0, prediction=prediction, frames_bgr=frames,
    )

    fdir = match_review._frames_dir(1)
    assert (fdir / "p1_r1_d0_cam0.jpg").is_file()
    assert (fdir / "p1_r1_d0_cam1.jpg").is_file()
    assert (fdir / "p1_r1_d0_cam2.jpg").is_file()

    rec = match_review._active[1]
    p1 = next(p for p in rec["players"] if p["player"] == 1)
    assert len(p1["turns"]) == 1
    turn = p1["turns"][0]
    assert turn["index"] == 0 and turn["round"] == 1
    assert len(turn["darts"]) == 1
    dart = turn["darts"][0]
    assert dart["label"] == "T20" and dart["score"] == 60
    assert dart["frames"] == {"per_dart": True}
    assert match_review._match_json(1).is_file()


def test_record_dart_skips_missing_cams():
    match_review.start(game_id=2, mode="x01",
                       players=[{"player": 1, "name": "P1"}],
                       started_at=0.0)
    frames = {0: _fake_frame(), 2: _fake_frame()}  # cam 1 missing
    match_review.record_dart(
        game_id=2, player=1, turn_idx=0, round_num=1, dart_idx=0,
        prediction={"label": "S20", "score": 20, "x_mm": 0, "y_mm": 0,
                    "agreement_bucket": "1cam_only", "cam_details": [], "ts": 0},
        frames_bgr=frames,
    )
    fdir = match_review._frames_dir(2)
    assert (fdir / "p1_r1_d0_cam0.jpg").is_file()
    assert not (fdir / "p1_r1_d0_cam1.jpg").exists()
    assert (fdir / "p1_r1_d0_cam2.jpg").is_file()
