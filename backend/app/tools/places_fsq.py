"""Foursquare Places tool — new (2025) Places API.

Activates when FOURSQUARE_API_KEY is set. Returns real, rated places near the
plan. Works with coordinates (`ll`) or, when geocoding is unavailable, by city
name (`near`) — so it still works even if OSM geocoding is blocked. Any failure
returns [] so callers fall back to OSM, then curated data.

New API specifics vs. the deprecated v3:
  host    places-api.foursquare.com/places/search
  auth    Authorization: Bearer <service key>
  header  X-Places-Api-Version: <date>
  result  flat latitude/longitude, location.formatted_address, categories[].name
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Robust query terms per interest (text search avoids category-id churn).
_ACTIVITY_QUERIES: dict[str, str] = {
    "music": "live music",
    "walks": "park",
    "nature": "garden",
    "art": "museum",
    "shopping": "shopping mall",
    "nightlife": "bar",
    "movies": "cinema",
    "books": "bookstore",
}

_FOOD_WORDS = {"restaurant", "cafe", "café", "coffee", "bakery", "bar", "pub", "food", "diner", "bistro", "eatery"}


def is_enabled() -> bool:
    return bool(get_settings().foursquare_api_key)


def _headers(s) -> dict:
    return {
        "Authorization": f"Bearer {s.foursquare_api_key}",
        "X-Places-Api-Version": s.foursquare_api_version,
        "accept": "application/json",
    }


def _classify(cat_name: str) -> tuple[str, str]:
    """Return (simplified_category, kind) from a Foursquare category name."""
    n = (cat_name or "").lower()
    if any(w in n for w in _FOOD_WORDS):
        if "caf" in n or "coffee" in n:
            return "cafe", "food"
        if "bakery" in n:
            return "bakery", "food"
        return "restaurant", "food"
    if "park" in n:
        return "park", "activity"
    if "garden" in n:
        return "garden", "activity"
    if "museum" in n:
        return "museum", "activity"
    if "gallery" in n or "art" in n:
        return "gallery", "activity"
    if "music" in n or "concert" in n or "club" in n:
        return "live_music", "activity"
    if "theater" in n or "cinema" in n or "movie" in n:
        return "cinema", "activity"
    if "mall" in n or "market" in n or "shop" in n:
        return "market", "activity"
    if "view" in n or "scenic" in n:
        return "viewpoint", "activity"
    return (n.replace(" ", "_") or "place"), "activity"


async def _search_one(
    query: str, coords: Optional[tuple[float, float]], city: str, limit: int
) -> list[dict]:
    s = get_settings()
    params: dict = {
        "query": query,
        "limit": min(limit, 20),
        "fields": "name,latitude,longitude,location,rating,categories",
    }
    if coords is not None:
        params["ll"] = f"{coords[0]},{coords[1]}"
        params["radius"] = min(s.search_radius_m, 20000)
    else:
        params["near"] = city

    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(s.foursquare_url, params=params, headers=_headers(s))
            r.raise_for_status()
            return r.json().get("results", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("foursquare search '%s' failed: %s", query, exc)
        return []


def _to_raw(p: dict) -> Optional[dict]:
    name = p.get("name")
    if not name:
        return None
    cat_name = (p.get("categories") or [{}])[0].get("name") or ""
    category, kind = _classify(cat_name)
    loc = p.get("location") or {}
    rating = p.get("rating")
    return {
        "name": name,
        "category": category,
        "kind": kind,
        "lat": p.get("latitude"),
        "lon": p.get("longitude"),
        "tags": [cat_name] if cat_name else [],
        "address": loc.get("formatted_address") or loc.get("address"),
        "source": "foursquare",
        # Foursquare rates 0–10; convert to a familiar 5-point scale.
        "rating": round(rating / 2, 1) if isinstance(rating, (int, float)) else None,
    }


async def search_places(
    interests: list[str],
    coords: Optional[tuple[float, float]],
    kind: str,
    city: str,
    limit: int = 20,
) -> list[dict]:
    """Search Foursquare for places of `kind` matching `interests`.

    Issues a small number of text searches, aggregates, de-dupes by name, and
    keeps only the requested kind.
    """
    s = get_settings()
    if not s.foursquare_api_key:
        return []

    # Build the query list (cap to keep request volume modest).
    if kind == "food":
        queries = ["restaurant", "cafe"]
    else:
        queries = []
        for i in interests:
            q = _ACTIVITY_QUERIES.get(i)
            if q and q not in queries:
                queries.append(q)
        if not queries:
            queries = ["park"]
    queries = queries[:3]

    seen: set[str] = set()
    out: list[dict] = []
    for q in queries:
        for p in await _search_one(q, coords, city, limit):
            raw = _to_raw(p)
            if not raw or raw["kind"] != kind:
                continue
            key = raw["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(raw)
    return out
