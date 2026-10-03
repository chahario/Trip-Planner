"""Routing tool — OpenRouteService (free tier; activates when ORS_API_KEY set).

Computes travel distance/time between consecutive plan stops that have
coordinates, producing TravelLeg entries the UI can show between cards. Without
a key, or if any stop lacks coordinates, it simply returns no legs.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import get_settings
from app.models import Plan, TravelLeg

log = logging.getLogger(__name__)


def is_enabled() -> bool:
    return bool(get_settings().ors_api_key)


async def add_travel_legs(plan: Plan) -> Plan:
    """Fill plan.legs with travel between consecutive geolocated stops."""
    s = get_settings()
    if not s.ors_api_key or len(plan.items) < 2:
        return plan

    # Build the coordinate list in [lon, lat] order (ORS convention).
    coords: list[list[float]] = []
    names: list[str] = []
    for item in plan.items:
        p = item.place
        if p.lon is None or p.lat is None:
            return plan  # need every stop located for a clean matrix
        coords.append([float(p.lon), float(p.lat)])
        names.append(item.title)

    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.post(
                s.ors_matrix_url,
                headers={
                    "Authorization": s.ors_api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "locations": coords,
                    "metrics": ["distance", "duration"],
                    "units": "km",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("openrouteservice matrix failed: %s", exc)
        return plan

    distances = data.get("distances") or []
    durations = data.get("durations") or []
    legs: list[TravelLeg] = []
    for i in range(len(coords) - 1):
        try:
            dist_km = float(distances[i][i + 1])
            dur_min = float(durations[i][i + 1]) / 60.0
        except (IndexError, TypeError, ValueError):
            continue
        legs.append(
            TravelLeg(
                from_name=names[i],
                to_name=names[i + 1],
                distance_km=round(dist_km, 1),
                duration_min=round(dur_min),
            )
        )
    plan.legs = legs
    return plan
