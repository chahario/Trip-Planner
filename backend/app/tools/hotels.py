"""Hotels tool — real hotels near the recommended plan.

Providers, tried in order of what's configured:

1. **Foursquare** (reuses FOURSQUARE_API_KEY) — searches real hotels *near the
   plan's coordinates* (or the city), with ratings. No extra signup, and it's
   location-aware, which is exactly what we want. This is the recommended
   alternative to Amadeus.
2. **Amadeus Self-Service** (free TEST env) — kept as a secondary option.

If neither is configured (or a call fails), callers fall back to keyless
deep-links. Everything degrades gracefully.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from app.config import get_settings
from app.models import Hotel

log = logging.getLogger(__name__)

_token: Optional[str] = None
_token_exp: float = 0.0

def is_enabled() -> bool:
    s = get_settings()
    return bool(
        s.geoapify_api_key
        or s.foursquare_api_key
        or (s.amadeus_client_id and s.amadeus_client_secret)
    )


def _booking_url(name: str, city: str) -> str:
    q = f"{name}, {city}".replace(" ", "+")
    return f"https://www.booking.com/searchresults.html?ss={q}"


async def _geoapify_geocode(s, city: str) -> Optional[tuple[float, float]]:
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                s.geoapify_geocode_url,
                params={"text": city, "limit": 1, "type": "city", "apiKey": s.geoapify_api_key},
            )
            r.raise_for_status()
            feats = r.json().get("features") or []
            if not feats:
                return None
            lon, lat = feats[0]["geometry"]["coordinates"]
            return float(lat), float(lon)
    except Exception as exc:  # noqa: BLE001
        log.warning("geoapify geocode failed: %s", exc)
        return None


async def _geoapify_hotels(
    city: str, lat: Optional[float], lon: Optional[float], limit: int
) -> list[Hotel]:
    """Real hotels near coords (preferred) or the city centre, via Geoapify.

    Free, no billing. Returns hotel names + addresses near the recommended spot.
    """
    s = get_settings()
    if not s.geoapify_api_key:
        return []

    if lat is None or lon is None:
        coords = await _geoapify_geocode(s, city)
        if not coords:
            return []
        lat, lon = coords

    radius = min(s.search_radius_m, 20000)
    params = {
        "categories": "accommodation.hotel,accommodation.guest_house",
        "filter": f"circle:{lon},{lat},{radius}",
        "bias": f"proximity:{lon},{lat}",
        "limit": min(limit, 20),
        "apiKey": s.geoapify_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(s.geoapify_places_url, params=params)
            r.raise_for_status()
            feats = r.json().get("features") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("geoapify hotel search failed: %s", exc)
        return []

    hotels: list[Hotel] = []
    for f in feats:
        p = f.get("properties") or {}
        name = p.get("name")
        if not name:
            continue
        addr = p.get("address_line2") or p.get("formatted")
        hotels.append(
            Hotel(name=name, address=addr, booking_url=_booking_url(name, city))
        )
    return hotels


async def _foursquare_hotels(
    city: str, lat: Optional[float], lon: Optional[float], limit: int
) -> list[Hotel]:
    """Real hotels near coords (preferred) or the city, via the new Foursquare
    Places API (Bearer auth + version header)."""
    s = get_settings()
    if not s.foursquare_api_key:
        return []

    params: dict = {
        "query": "hotel",
        "limit": min(limit, 20),
        "fields": "name,latitude,longitude,location,rating,website",
    }
    if lat is not None and lon is not None:
        params["ll"] = f"{lat},{lon}"
        params["radius"] = min(s.search_radius_m, 20000)
    else:
        params["near"] = city  # Foursquare resolves the city for us

    headers = {
        "Authorization": f"Bearer {s.foursquare_api_key}",
        "X-Places-Api-Version": s.foursquare_api_version,
        "accept": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(s.foursquare_url, params=params, headers=headers)
            r.raise_for_status()
            results = r.json().get("results", [])
    except Exception as exc:  # noqa: BLE001
        log.warning("foursquare hotel search failed: %s", exc)
        return []

    hotels: list[Hotel] = []
    for p in results:
        name = p.get("name")
        if not name:
            continue
        loc = p.get("location") or {}
        addr = loc.get("formatted_address") or loc.get("address")
        rating = p.get("rating")
        hotels.append(
            Hotel(
                name=name,
                rating=f"{rating/2:.1f}/5" if isinstance(rating, (int, float)) else None,
                address=addr,
                booking_url=(p.get("website") or _booking_url(name, city)),
            )
        )
    return hotels


async def search_hotels(
    city: str,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    limit: int = 8,
) -> tuple[list[Hotel], Optional[str]]:
    """Return (hotels, note). Prefers Foursquare (near the plan), then Amadeus."""
    s = get_settings()

    # 1. Geoapify (free, no billing) — the recommended provider.
    if s.geoapify_api_key:
        hotels = await _geoapify_hotels(city, lat, lon, limit)
        if hotels:
            return hotels, None

    # 2. Foursquare (note: its 2025 API needs paid credits).
    if s.foursquare_api_key:
        hotels = await _foursquare_hotels(city, lat, lon, limit)
        if hotels:
            return hotels, None

    # 3. Amadeus (free test env).
    if s.amadeus_client_id and s.amadeus_client_secret:
        hotels, note = await _amadeus_hotels(city, limit)
        if hotels:
            return hotels, note

    return [], f"No live hotels for {city} — showing deep-links instead."


async def _get_token(s) -> Optional[str]:
    global _token, _token_exp
    if _token and time.time() < _token_exp - 30:
        return _token
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.post(
                f"{s.amadeus_base_url}/v1/security/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": s.amadeus_client_id,
                    "client_secret": s.amadeus_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            r.raise_for_status()
            data = r.json()
            _token = data["access_token"]
            _token_exp = time.time() + float(data.get("expires_in", 1799))
            return _token
    except Exception as exc:  # noqa: BLE001
        log.warning("amadeus auth failed: %s", exc)
        return None


async def _city_code(s, token: str, city: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                f"{s.amadeus_base_url}/v1/reference-data/locations/cities",
                params={"keyword": city, "max": 1},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json().get("data") or []
            if not data:
                return None
            return data[0].get("iataCode") or (data[0].get("address") or {}).get("cityCode")
    except Exception as exc:  # noqa: BLE001
        log.warning("amadeus city lookup failed: %s", exc)
        return None


async def _amadeus_hotels(city: str, limit: int = 8) -> tuple[list[Hotel], Optional[str]]:
    """Return (hotels, note) from Amadeus. note explains any empty result."""
    s = get_settings()
    token = await _get_token(s)
    if not token:
        return [], "Couldn't authenticate with Amadeus."

    code = await _city_code(s, token, city)
    if not code:
        return [], f"No Amadeus city code found for {city} (TEST data is limited)."

    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                f"{s.amadeus_base_url}/v1/reference-data/locations/hotels/by-city",
                params={"cityCode": code, "radius": 20, "radiusUnit": "KM"},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json().get("data") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("amadeus hotel search failed: %s", exc)
        return [], "Amadeus hotel search failed."

    hotels: list[Hotel] = []
    for h in data[:limit]:
        name = h.get("name")
        if not name:
            continue
        addr = (h.get("address") or {})
        line = ", ".join(filter(None, [*(addr.get("lines") or []), addr.get("cityName")]))
        hotels.append(
            Hotel(
                name=name.title(),
                rating=str(h.get("rating")) if h.get("rating") else None,
                address=line or None,
                booking_url=f"https://www.booking.com/searchresults.html?ss={name.replace(' ', '+')}",
            )
        )
    note = None if hotels else f"No hotels returned for {city} in Amadeus TEST data."
    return hotels, note
