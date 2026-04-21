"""HTTP integration tests for match_review endpoints."""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

import match_review
import server as srv


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(match_review, "DATA_ROOT", tmp_path / "match_reviews")
    match_review._active.clear()
    srv.app.config["TESTING"] = True
    with srv.app.test_client() as c:
        yield c
    match_review._active.clear()


def _seed(game_id=42):
    match_review.start(
        game_id=game_id, mode="x01",
        players=[{"player": 1, "name": "P1"}, {"player": 2, "name": "P2"}],
        started_at=0.0,
    )
    frame = (np.ones((100, 100, 3), dtype=np.uint8) * 60)
    match_review.record_dart(
        game_id=game_id, player=1, turn_idx=0, round_num=1, dart_idx=0,
        prediction={"label": "T20", "score": 60, "x_mm": 0, "y_mm": 0,
                    "agreement_bucket": "3cam_all_match",
                    "cam_details": [{"cam": 0, "x_px": 50, "y_px": 50,
                                      "label": "T20", "score": 60, "used": True}],
                    "ts": 0},
        frames_bgr={0: frame},
    )
    match_review.finalize(game_id, {"winner": 1, "finished_at": 100.0})


def test_get_review_200(client):
    _seed(42)
    resp = client.get("/api/matches/42/review")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["match_id"] == 42
    assert data["status"] == "completed"


def test_get_review_404_missing(client):
    resp = client.get("/api/matches/9999/review")
    assert resp.status_code == 404


def test_get_frame_per_dart_200(client):
    _seed(42)
    resp = client.get("/api/matches/42/frame/per_dart/1/1/0/0")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"
    assert len(resp.data) > 50


def test_get_frame_annotated_differs_from_raw(client):
    _seed(42)
    raw = client.get("/api/matches/42/frame/per_dart/1/1/0/0")
    ann = client.get("/api/matches/42/frame/per_dart/1/1/0/0/annotated")
    assert raw.status_code == 200 and ann.status_code == 200
    assert raw.data != ann.data


def test_get_frame_404_missing(client):
    _seed(42)
    resp = client.get("/api/matches/42/frame/per_dart/1/1/0/9")
    assert resp.status_code in (400, 404)


def test_invalid_kind_rejected(client):
    _seed(42)
    resp = client.get("/api/matches/42/frame/evil/1/1/0/0")
    assert resp.status_code == 400


def test_delete_removes_review_and_stats(client, tmp_path, monkeypatch):
    import stats
    monkeypatch.setattr(stats, "STATS_DIR", tmp_path)
    monkeypatch.setattr(stats, "STATS_FILE", tmp_path / "stats.json")
    stats.save_game({"id": 42, "mode": "x01", "winner": 1})
    _seed(42)

    resp = client.delete("/api/stats/game/42")
    assert resp.status_code == 200

    assert not (match_review.DATA_ROOT / "match_42.json").exists()
    assert not (match_review.DATA_ROOT / "match_42").exists()


def test_stats_list_includes_has_review(client, tmp_path, monkeypatch):
    import stats
    monkeypatch.setattr(stats, "STATS_DIR", tmp_path)
    monkeypatch.setattr(stats, "STATS_FILE", tmp_path / "stats.json")
    stats.save_game({"id": 42, "mode": "x01", "winner": 1})
    stats.save_game({"id": 43, "mode": "x01", "winner": 2})
    _seed(42)  # only 42 has a review

    resp = client.get("/api/stats?mode=x01")
    assert resp.status_code == 200
    payload = resp.get_json()
    recent = payload.get("recent") or []
    by_id = {r["id"]: r for r in recent}
    assert by_id[42].get("has_review") is True
    assert by_id[43].get("has_review") is False
