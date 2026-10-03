"""OpenStreetMap client: geocode a city (Nominatim) and fetch POIs (Overpass).

No API key required. All network failures are swallowed and surfaced as empty
results so callers can fall back to curated data — the agent must never crash
because a public endpoint had a bad day.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Maps our internal interest keywords to Overpass tag filters. Each entry is a
# list of (key, value) OSM tags; a node matching any of them qualifies.
INTEREST_TO_OSM: dict[str, list[tuple[str, str]]] = {
    "food": [("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "fast_food")],
    "coffee": [("amenity", "cafe")],
    "music": [("amenity", "nightclub"), ("amenity", "bar"), ("leisure", "music_venue")],
    "walks": [("leisure", "park"), ("leisure", "garden"), ("tourism", "viewpoint")],
    "nature": [("leisure", "park"), ("leisure", "garden"), ("natural", "wood")],
    "art": [("tourism", "gallery"), ("tourism", "museum")],
    "museum": [("tourism", "museum")],
    "shopping": [("shop", "mall"), ("amenity", "marketplace")],
    "nightlife": [("amenity", "bar"), ("amenity", "pub"), ("amenity", "nightclub")],
    "movies": [("amenity", "cinema")],
    "books": [("shop", "books")],
}

# Which OSM amenity/leisure values we treat as "food".
FOOD_AMENITIES = {"restaurant", "cafe", "fast_food", "bar", "pub", "ice_cream", "food_court"}


def _normalise_category(tags: dict) -> tuple[str, str]:
    """Return (category, kind) from an OSM element's tags."""
    amenity = tags.get("amenity")
    leisure = tags.get("leisure")
    tourism = tags.get("tourism")
    shop = tags.get("shop")

    if amenity in FOOD_AMENITIES:
        return amenity, "food"
    if leisure in {"park", "garden"}:
        return leisure, "activity"
    if leisure == "music_venue" or amenity == "nightclub":
        return "live_music", "activity"
    if tourism in {"museum", "gallery", "viewpoint"}:
        return tourism, "activity"
    if amenity == "cinema":
        return "cinema", "activity"
    if shop == "mall" or amenity == "marketplace":
        return "market", "activity"
    return (amenity or leisure or tourism or shop or "place"), "activity"


class OSMClient:
    def __init__(self) -> None:
        self.s = get_settings()
        self._headers = {"User-Agent": self.s.http_user_agent}

    async def geocode(self, city: str) -> Optional[tuple[float, float]]:
        """City name -> (lat, lon). Returns None on any failure."""
        if not self.s.osm_enabled:
            return None
        params = {"q": city, "format": "json", "limit": 1}
        try:
            async with httpx.AsyncClient(timeout=self.s.http_timeout_seconds) as client:
                r = await client.get(self.s.nominatim_url, params=params, headers=self._headers)
                r.raise_for_status()
                data = r.json()
                if not data:
                    return None
                return float(data[0]["lat"]), float(data[0]["lon"])
        except Exception as exc:  # noqa: BLE001 — deliberate: degrade gracefully
            log.warning("geocode failed for %s: %s", city, exc)
            return None

    def _build_query(self, lat: float, lon: float, filters: list[tuple[str, str]]) -> str:
        radius = self.s.search_radius_m
        clauses = "".join(
            f'node["{k}"="{v}"](around:{radius},{lat},{lon});' for k, v in filters
        )
        return f"[out:json][timeout:20];({clauses});out center 60;"

    async def fetch_pois(
        self, lat: float, lon: float, filters: list[tuple[str, str]]
    ) -> list[dict]:
        """Run one Overpass query. Returns a list of raw element dicts, or []."""
        if not self.s.osm_enabled or not filters:
            return []
        query = self._build_query(lat, lon, filters)
        try:
            async with httpx.AsyncClient(timeout=self.s.http_timeout_seconds) as client:
                r = await client.post(
                    self.s.overpass_url, data={"data": query}, headers=self._headers
                )
                r.raise_for_status()
                elements = r.json().get("elements", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("overpass fetch failed: %s", exc)
            return []

        results: list[dict] = []
        for el in elements:
            tags = el.get("tags", {})
            name = tags.get("name")
            if not name:
                continue  # unnamed nodes are useless to a human plan
            category, kind = _normalise_category(tags)
            results.append(
                {
                    "name": name,
                    "category": category,
                    "kind": kind,
                    "lat": el.get("lat") or (el.get("center") or {}).get("lat"),
                    "lon": el.get("lon") or (el.get("center") or {}).get("lon"),
                    "tags": [t for t in (tags.get("cuisine", "").split(";")) if t],
                    "address": _format_address(tags),
                    "source": "openstreetmap",
                }
            )
        return results


def _format_address(tags: dict) -> Optional[str]:
    parts = [
        tags.get("addr:street"),
        tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
        tags.get("addr:city"),
    ]
    parts = [p for p in parts if p]
    return ", ".join(parts) if parts else None
