"""Smoke tests for the HTTP API using FastAPI's in-process test client."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def valid_payload():
    return {
        "exposures": [
            {"name": "A", "duration": 3, "est": 0, "lst": 10,
             "equipment": "D1", "cooldown": 1},
            {"name": "B", "duration": 2, "est": 0, "lst": 10,
             "equipment": "D1", "cooldown": 0},
            {"name": "C", "duration": 4, "est": 1, "lst": 12,
             "equipment": "D2", "cooldown": 2},
            {"name": "D", "duration": 1, "est": 0, "lst": 20,
             "equipment": "D2", "cooldown": 0},
            {"name": "E", "duration": 2, "est": 0, "lst": 20,
             "equipment": "D1", "cooldown": 0},
        ],
        "links": [{"a": 0, "b": 1, "min_gap": 0, "max_gap": 9}],
    }


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_feasible_schedule_shape():
    r = client.post("/api/schedule", json=valid_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "feasible"
    assert len(body["starts"]) == 5
    assert isinstance(body["final_end"], int)
    assert body["objective"]["sum_starts"] == sum(body["starts"])
    assert set(body["equipment_order"]) == {"D1", "D2"}
    assert any(m["type"] == "equipment" for m in body["margins"])


def test_invalid_input_returns_422_and_no_schedule():
    payload = valid_payload()
    payload["exposures"][0]["duration"] = -1
    payload["exposures"][2]["est"] = 9
    payload["exposures"][2]["lst"] = 1
    r = client.post("/api/schedule", json=payload)
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "invalid_input"
    assert body["schedule"] is None
    assert body["starts"] is None
    codes = {e["code"] for e in body["errors"]}
    assert "bad_range" in codes and "inverted_window" in codes


def test_wrong_count_rejected():
    payload = valid_payload()
    payload["exposures"] = payload["exposures"][:3]
    r = client.post("/api/schedule", json=payload)
    assert r.status_code == 422
    assert any(
        e["code"] == "count_out_of_range" for e in r.json()["errors"]
    )


def test_infeasible_returns_409_distinct_from_validation():
    payload = valid_payload()
    for e in payload["exposures"]:
        e["equipment"] = "ONLY"
        e["est"] = 0
        e["lst"] = 1
        e["duration"] = 3
        e["cooldown"] = 0
    r = client.post("/api/schedule", json=payload)
    assert r.status_code == 409
    body = r.json()
    assert body["status"] == "infeasible"
    assert body["reason"] == "no_feasible_schedule"
    assert body["schedule"] is None
    assert body["starts"] is None
    assert body["reason_detail"]


def test_bad_json_is_invalid_input():
    r = client.post(
        "/api/schedule",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["status"] == "invalid_input"
