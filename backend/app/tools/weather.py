"""Weather tool — Open-Meteo (FREE, no API key).

Given coordinates (or a city name to geocode via Open-Meteo's own geocoder),
returns a short outlook for the plan date plus an advisory the planner can act
on (e.g. move outdoor stops earlier if rain is likely). Any failure returns
None so the plan is unaffected.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timedelta
from typing import Optional

import httpx

from app.config import get_settings
from app.models import Weather

log = logging.getLogger(__name__)

# WMO weather codes -> (description, emoji). Condensed to the common buckets.
_WMO: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Drizzle", "🌦️"),
    55: ("Heavy drizzle", "🌧️"),
    61: ("Light rain", "🌦️"),
    63: ("Rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    71: ("Light snow", "🌨️"),
    73: ("Snow", "🌨️"),
    75: ("Heavy snow", "❄️"),
    80: ("Rain showers", "🌦️"),
    81: ("Rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with hail", "⛈️"),
    99: ("Thunderstorm with hail", "⛈️"),
}


async def _geocode(city: str, s) -> Optional[tuple[float, float]]:
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                s.open_meteo_geocode_url,
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            r.raise_for_status()
            results = r.json().get("results") or []
            if not results:
                return None
            return float(results[0]["latitude"]), float(results[0]["longitude"])
    except Exception as exc:  # noqa: BLE001
        log.warning("open-meteo geocode failed for %s: %s", city, exc)
        return None


async def get_weather(
    city: str,
    coords: Optional[tuple[float, float]] = None,
    for_date: Optional[str] = None,
) -> Optional[Weather]:
    """Return a Weather outlook, or None if disabled/unavailable."""
    s = get_settings()
    if not s.weather_enabled:
        return None

    if coords is None:
        coords = await _geocode(city, s)
    if coords is None:
        return None
    lat, lon = coords

    target = for_date or date_cls.today().isoformat()
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                s.open_meteo_url,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "start_date": target,
                    "end_date": target,
                },
            )
            r.raise_for_status()
            daily = r.json().get("daily") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("open-meteo forecast failed: %s", exc)
        return None

    if not daily.get("time"):
        return None

    code = (daily.get("weather_code") or [0])[0]
    desc, emoji = _WMO.get(int(code), ("Mixed", "🌡️"))
    tmax = (daily.get("temperature_2m_max") or [None])[0]
    tmin = (daily.get("temperature_2m_min") or [None])[0]
    pop = (daily.get("precipitation_probability_max") or [None])[0]

    advisory = None
    if pop is not None and pop >= 60:
        advisory = "Rain likely — keep a couple of indoor options (café, museum) in reserve."
    elif pop is not None and pop >= 30:
        advisory = "Slight chance of rain — carry a light umbrella."
    elif tmax is not None and tmax >= 35:
        advisory = "Hot day — favour shaded/indoor stops in the afternoon."

    return Weather(
        date=target,
        temp_max_c=tmax,
        temp_min_c=tmin,
        precipitation_chance=int(pop) if pop is not None else None,
        description=desc,
        emoji=emoji,
        advisory=advisory,
    )


def _advisory_for(pop, tmax) -> Optional[str]:
    if pop is not None and pop >= 60:
        return "Rain likely — keep a couple of indoor options in reserve."
    if pop is not None and pop >= 30:
        return "Slight chance of rain — carry a light umbrella."
    if tmax is not None and tmax >= 35:
        return "Hot day — favour shaded/indoor stops in the afternoon."
    return None


# Open-Meteo's standard forecast only covers roughly today … +16 days.
_FORECAST_HORIZON_DAYS = 16


async def get_weather_series(
    city: str,
    coords: Optional[tuple[float, float]],
    start_date: str,
    end_date: str,
) -> dict[str, Weather]:
    """Return {YYYY-MM-DD: Weather} for a city over a date range. Dates outside
    Open-Meteo's forecast horizon are simply omitted (caller handles gaps)."""
    s = get_settings()
    if not s.weather_enabled:
        return {}
    if coords is None:
        coords = await _geocode(city, s)
    if coords is None:
        return {}
    lat, lon = coords

    # Clamp the range to the forecast horizon so the API doesn't reject it.
    try:
        today = date_cls.today()
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return {}
    horizon = today + timedelta(days=_FORECAST_HORIZON_DAYS)
    if sd > horizon:
        return {}  # whole range too far out for a real forecast
    if sd < today:
        sd = today
    if ed > horizon:
        ed = horizon

    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                s.open_meteo_url,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "start_date": sd.isoformat(),
                    "end_date": ed.isoformat(),
                },
            )
            r.raise_for_status()
            daily = r.json().get("daily") or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("open-meteo series failed for %s: %s", city, exc)
        return {}

    times = daily.get("time") or []
    codes = daily.get("weather_code") or []
    tmaxs = daily.get("temperature_2m_max") or []
    tmins = daily.get("temperature_2m_min") or []
    pops = daily.get("precipitation_probability_max") or []

    out: dict[str, Weather] = {}
    for i, d in enumerate(times):
        code = codes[i] if i < len(codes) else 0
        desc, emoji = _WMO.get(int(code), ("Mixed", "🌡️"))
        tmax = tmaxs[i] if i < len(tmaxs) else None
        tmin = tmins[i] if i < len(tmins) else None
        pop = pops[i] if i < len(pops) else None
        out[d] = Weather(
            date=d,
            temp_max_c=tmax,
            temp_min_c=tmin,
            precipitation_chance=int(pop) if pop is not None else None,
            description=desc,
            emoji=emoji,
            advisory=_advisory_for(pop, tmax),
            city=city,
        )
    return out
