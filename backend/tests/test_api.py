"""Integration tests for the API, with OSM mocked so tests are offline & fast."""
from __future__ import annotations

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from app.config import get_settings
from app.main import app

client = TestClient(app)
S = get_settings()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@respx.mock
def test_plan_with_mocked_osm():
    # Geocode
    respx.get(S.nominatim_url).mock(
        return_value=Response(200, json=[{"lat": "12.97", "lon": "77.59"}])
    )
    # Overpass returns a park and a cafe
    respx.post(S.overpass_url).mock(
        return_value=Response(200, json={"elements": [
            {"type": "node", "lat": 12.97, "lon": 77.59,
             "tags": {"name": "Cubbon Park", "leisure": "park"}},
            {"type": "node", "lat": 12.98, "lon": 77.60,
             "tags": {"name": "Green Cafe", "amenity": "cafe"}},
        ]})
    )
    r = client.post("/api/plan", json={
        "city": "Bangalore", "budget": 2000, "available_time": "4 hours",
        "mood": "tired but wants to do something fun",
        "interests": ["food", "walks"], "constraints": ["vegetarian", "avoid crowded places"],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert body["plan"]["items"]
    assert body["plan"]["city"] == "Bangalore"
    # Trace must show the tools that ran.
    tools = {t["tool"] for t in body["trace"]}
    assert {"parseUserPreferences", "resolveDestination", "enrichCities",
            "estimateCost", "validatePlan"} <= tools


@respx.mock
def test_plan_falls_back_when_osm_down():
    respx.get(S.nominatim_url).mock(return_value=Response(500))
    respx.post(S.overpass_url).mock(return_value=Response(500))
    r = client.post("/api/plan", json={"city": "Bangalore", "interests": ["walks", "food"]})
    assert r.status_code == 200
    body = r.json()
    # Should still produce a plan from curated data, with a warning.
    assert body["plan"]["items"]
    assert any("curated" in w.lower() or "fallback" in w.lower() for w in body["warnings"])


@respx.mock
def test_vague_input_gets_clarifying_questions():
    respx.get(S.nominatim_url).mock(return_value=Response(200, json=[{"lat": "12.9", "lon": "77.5"}]))
    respx.post(S.overpass_url).mock(return_value=Response(200, json={"elements": []}))
    r = client.post("/api/plan", json={"city": "Bangalore"})
    body = r.json()
    assert len(body["clarifying_questions"]) >= 1


def test_missing_city_is_422():
    r = client.post("/api/plan", json={"budget": 1000})
    assert r.status_code == 422


@respx.mock
def test_stream_emits_events():
    respx.get(S.nominatim_url).mock(return_value=Response(200, json=[{"lat": "12.9", "lon": "77.5"}]))
    respx.post(S.overpass_url).mock(return_value=Response(200, json={"elements": [
        {"type": "node", "lat": 12.9, "lon": 77.5, "tags": {"name": "Park", "leisure": "park"}},
    ]}))
    with client.stream("POST", "/api/plan/stream", json={"city": "Bangalore", "interests": ["walks"]}) as r:
        assert r.status_code == 200
        text = "".join(chunk for chunk in r.iter_text())
    assert "event: trace" in text
    assert "event: plan" in text
