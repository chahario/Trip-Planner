"""Tools 2 & 3 — getActivityOptions and getFoodOptions.

Both call OpenStreetMap (or Foursquare, if configured) for real places near the
city, mapped from the user's interests, and fall back to curated seed data if
nothing is found. They also attach a cost estimate per place so downstream tools
can budget.

NEW: `get_options_for_stop()` fetches both activities and food for ONE city of a
multi-city route, tagging every returned Place with its city. The orchestrator
calls this per stop so each city gets real, local places (fixing the old bug
where a whole country fell back to one generic template set).
"""
from __future__ import annotations

from app.data.curated import CATEGORY_COST_BANDS, curated_for
from app.models import Place, Preferences, RouteStop
from app.tools import places_fsq
from app.tools.osm_client import INTEREST_TO_OSM, OSMClient

# Interests that imply food vs. activity when we widen the search.
_FOOD_INTERESTS = {"food", "coffee"}
_DEFAULT_ACTIVITY_INTERESTS = ["walks", "nature"]


def _est_cost(category: str) -> int:
    return CATEGORY_COST_BANDS.get(category, 250)


def _to_place(raw: dict, city: str | None = None) -> Place:
    return Place(
        name=raw["name"],
        category=raw["category"],
        kind=raw["kind"],
        lat=raw.get("lat"),
        lon=raw.get("lon"),
        est_cost=_est_cost(raw["category"]),
        tags=raw.get("tags", []),
        source=raw.get("source", "openstreetmap"),
        address=raw.get("address"),
        rating=raw.get("rating"),
        city=city,
    )


def _curated_places(city: str, kind: str) -> list[Place]:
    out = []
    for raw in curated_for(city):
        if raw["kind"] != kind:
            continue
        r = dict(raw)
        r["source"] = "curated"
        out.append(_to_place(r, city=city))
    return out


async def get_activity_options(
    prefs: Preferences, coords: tuple[float, float] | None, osm: OSMClient,
    *, city: str | None = None,
) -> tuple[list[Place], bool]:
    """Returns (places, used_fallback). `city` tags the places and is also used
    for name-based Foursquare search and curated fallback; defaults to prefs.city."""
    target_city = city or prefs.city
    activity_interests = [i for i in prefs.interests if i not in _FOOD_INTERESTS]
    if not activity_interests:
        activity_interests = _DEFAULT_ACTIVITY_INTERESTS

    places: list[Place] = []
    # Prefer Foursquare (rated, richer) when a key is configured. It works with
    # coordinates or by city name, so it runs even if geocoding failed.
    if places_fsq.is_enabled():
        fsq = await places_fsq.search_places(activity_interests, coords, "activity", target_city)
        places = [_to_place(r, city=target_city) for r in fsq]
    # Fall back to OpenStreetMap (needs coordinates).
    if not places and coords:
        lat, lon = coords
        filters: list[tuple[str, str]] = []
        for interest in activity_interests:
            filters.extend(INTEREST_TO_OSM.get(interest, []))
        filters = list(dict.fromkeys(filters))  # de-dup
        raw = await osm.fetch_pois(lat, lon, filters)
        places = [_to_place(r, city=target_city) for r in raw if r["kind"] == "activity"]

    if places:
        return _dedup(places), False
    return _curated_places(target_city, "activity"), True


async def get_food_options(
    prefs: Preferences, coords: tuple[float, float] | None, osm: OSMClient,
    *, city: str | None = None,
) -> tuple[list[Place], bool]:
    """Returns (places, used_fallback). Applies budget-per-meal filtering."""
    target_city = city or prefs.city
    places: list[Place] = []
    if places_fsq.is_enabled():
        fsq = await places_fsq.search_places(["food", "coffee"], coords, "food", target_city)
        places = [_to_place(r, city=target_city) for r in fsq]
    if not places and coords:
        lat, lon = coords
        filters = list(dict.fromkeys(INTEREST_TO_OSM["food"]))
        raw = await osm.fetch_pois(lat, lon, filters)
        places = [_to_place(r, city=target_city) for r in raw if r["kind"] == "food"]

    used_fallback = False
    if not places:
        places = _curated_places(target_city, "food")
        used_fallback = True

    # Soft budget preference (never wipes out all options).
    if prefs.budget is not None and places:
        affordable = [p for p in places if p.est_cost <= max(prefs.budget, 1)]
        if affordable:
            places = affordable

    return _dedup(places), used_fallback


async def get_options_for_stop(
    prefs: Preferences, stop: RouteStop, osm: OSMClient,
) -> tuple[list[Place], list[Place], bool, bool]:
    """Fetch activities + food for ONE city of a route.

    Uses the stop's already-resolved coordinates (no re-geocoding). Every place
    is tagged with the stop's city so the itinerary builder can group by city.

    Returns (activities, foods, act_fallback, food_fallback).
    """
    coords = (stop.lat, stop.lon) if stop.lat is not None and stop.lon is not None else None
    acts, act_fb = await get_activity_options(prefs, coords, osm, city=stop.city)
    foods, food_fb = await get_food_options(prefs, coords, osm, city=stop.city)
    return acts, foods, act_fb, food_fb


def _dedup(places: list[Place]) -> list[Place]:
    seen: set[str] = set()
    out: list[Place] = []
    for p in places:
        key = p.name.lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out