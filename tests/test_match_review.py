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


def test_record_turn_end_writes_eot_frames():
    match_review.start(game_id=3, mode="x01",
                       players=[{"player": 1, "name": "P1"}],
                       started_at=0.0)
    match_review.record_dart(
        game_id=3, player=1, turn_idx=0, round_num=1, dart_idx=0,
        prediction={"label": "S1", "score": 1, "x_mm": 0, "y_mm": 0,
                    "agreement_bucket": "x", "cam_details": [], "ts": 0},
        frames_bgr={0: _fake_frame()},
    )
    frames = {0: _fake_frame(), 1: _fake_frame(), 2: _fake_frame()}
    match_review.record_turn_end(
        game_id=3, player=1, turn_idx=0, round_num=1,
        frames_bgr=frames, ts=1776500020.1,
    )
    fdir = match_review._frames_dir(3)
    assert (fdir / "p1_r1_eot_cam0.jpg").is_file()
    assert (fdir / "p1_r1_eot_cam1.jpg").is_file()
    assert (fdir / "p1_r1_eot_cam2.jpg").is_file()

    p1 = match_review._active[3]["players"][0]
    turn = p1["turns"][0]
    assert turn["end_of_turn"] == {"frames": True, "ts": 1776500020.1}


def test_finalize_completed_sets_status_and_merges_summary():
    match_review.start(game_id=10, mode="countup",
                       players=[{"player": 1, "name": "P1"},
                                {"player": 2, "name": "P2"}],
                       started_at=0.0)
    summary = {"winner": 2, "mode": "countup", "finished_at": 1776500600.0}
    match_review.finalize(10, summary)

    data = json.loads(match_review._match_json(10).read_text(encoding="utf-8"))
    assert data["status"] == "completed"
    assert data["winner"] == 2
    assert data["ended_at"] == 1776500600.0
    assert data["abandoned_reason"] is None
    assert 10 not in match_review._active


def test_finalize_abandoned_sets_reason():
    match_review.start(game_id=11, mode="x01",
                       players=[{"player": 1, "name": "P1"}],
                       started_at=0.0)
    match_review.finalize(11, {"mode": "x01"},
                          abandoned=True, reason="user_quit")

    data = json.loads(match_review._match_json(11).read_text(encoding="utf-8"))
    assert data["status"] == "abandoned"
    assert data["abandoned_reason"] == "user_quit"
    assert 11 not in match_review._active


def test_finalize_no_active_match_no_op():
    match_review.finalize(9999, {})  # should not raise


def test_get_review_returns_data_or_none():
    assert match_review.get_review(404) is None

    match_review.start(game_id=20, mode="x01",
                       players=[{"player": 1, "name": "P1"}],
                       started_at=0.0)
    match_review.finalize(20, {"winner": 1, "finished_at": 100.0})

    data = match_review.get_review(20)
    assert data is not None
    assert data["match_id"] == 20
    assert data["status"] == "completed"


def test_render_annotated_returns_non_empty_jpeg():
    import numpy as np
    match_review.start(game_id=30, mode="x01",
                       players=[{"player": 1, "name": "P1"}],
                       started_at=0.0)
    frame = (np.ones((200, 200, 3), dtype=np.uint8) * 50)
    match_review.record_dart(
        game_id=30, player=1, turn_idx=0, round_num=1, dart_idx=0,
        prediction={"label": "T20", "score": 60,
                    "x_mm": 0, "y_mm": 0,
                    "agreement_bucket": "x",
                    "cam_details": [
                        {"cam": 0, "label": "T20", "score": 60,
                         "x_px": 100, "y_px": 100, "used": True}
                    ],
                    "ts": 0},
        frames_bgr={0: frame},
    )
    out = match_review.render_annotated(
        game_id=30, kind="per_dart", player=1, round_num=1,
        dart_idx=0, cam_idx=0,
    )
    assert out is not None
    assert isinstance(out, (bytes, bytearray))
    assert len(out) > 100
    raw = (match_review._frames_dir(30) / "p1_r1_d0_cam0.jpg").read_bytes()
    assert bytes(out) != raw
