"""Destination resolver — turns a raw destination into a day-allocated Route.

This is the keystone that fixes two bugs at once:

  1. "Thailand" (a country) -> real cities (Bangkok, Chiang Mai, Krabi, ...),
     discovered LIVE from Wikivoyage (no stored country data), each VERIFIED
     to exist via OpenStreetMap geocoding (anti-hallucination gate).

  2. The requested day count is AUTHORITATIVE. `allocate_nights()` is pure code
     and guarantees  sum(stop.nights) == total_days.  The LLM never decides how
     long the trip is — it only narrates the already-decided route.

Single-city input ("Bangalore") flows through the SAME path and comes out as a
Route with exactly one stop, so the rest of the pipeline has one code path.

Design law: APIs supply facts · code makes decisions · LLM only narrates.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from typing import Optional

import httpx

from app.config import get_settings
from app.models import IntercityLeg, Route, RouteStop, UnusedPlace
from app.tools.osm_client import OSMClient

log = logging.getLogger(__name__)

WIKIVOYAGE_API = "https://en.wikivoyage.org/w/api.php"

# Simple in-process cache: {destination_lower: (expires_at, Route-ish dict)}.
# Keeps us from re-hitting Wikivoyage + OSM for the same country repeatedly.
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 60 * 60 * 24 * 7  # 7 days


# ===========================================================================
# CLASSIFICATION  (city vs region vs country vs continent)
# ===========================================================================
# GeoNames feature-code families (from Open-Meteo geocoding). This is the
# authoritative signal for "what KIND of place is this?" — free, no key, and
# not subject to Nominatim's User-Agent policy.
def _kind_from_feature_code(fc: Optional[str]) -> str:
    fc = (fc or "").upper()
    if fc == "CONT":
        return "continent"
    if fc.startswith("PCL"):          # PCLI/PCL/PCLD… = independent country / political entity
        return "country"
    if fc.startswith("ADM"):          # ADM1 = state/province, ADM2 = district → a region
        return "region"
    if fc.startswith("PPL") or fc in {"STLMT"}:  # populated place → a city/town
        return "city"
    return "unknown"


async def _openmeteo_lookup(name: str, country: Optional[str] = None) -> Optional[dict]:
    """Look a place up via Open-Meteo (GeoNames). Free and NOT rate-limited, so
    it's safe to call concurrently for many cities — unlike Nominatim, which 403s
    under bursts. Prefers a result in `country`, then an exact name match, then
    the most populous. Returns {'lat','lon','population'} or None."""
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                s.open_meteo_geocode_url,
                params={"name": name, "count": 10, "language": "en", "format": "json"},
                headers={"User-Agent": s.http_user_agent},
            )
            r.raise_for_status()
            results = r.json().get("results") or []
    except Exception as exc:  # noqa: BLE001
        log.warning("open-meteo geocode failed for %s: %s", name, exc)
        return None
    if not results:
        return None
    if country:
        cm = [x for x in results if (x.get("country") or "").strip().lower() == country.strip().lower()]
        results = cm or results
    exact = [x for x in results if (x.get("name") or "").strip().lower() == name.strip().lower()]
    pool = exact or results
    pool.sort(key=lambda x: x.get("population") or 0, reverse=True)
    top = pool[0]
    if top.get("latitude") is None or top.get("longitude") is None:
        return None
    return {"lat": float(top["latitude"]), "lon": float(top["longitude"]),
            "population": int(top.get("population") or 0)}


async def _openmeteo_geocode(
    name: str, country: Optional[str] = None
) -> Optional[tuple[float, float]]:
    """Coords-only convenience wrapper over _openmeteo_lookup."""
    look = await _openmeteo_lookup(name, country)
    return (look["lat"], look["lon"]) if look else None


async def classify_destination(
    name: str,
) -> tuple[str, Optional[tuple[float, float]], Optional[str]]:
    """Classify a raw destination string as one of continent | country | region
    | city | unknown, and return (kind, coords|None, country|None).

    Uses Open-Meteo's GeoNames-backed geocoder: it fetches several matches and
    prefers an exact name match, then the most-populous — so "Thailand" is a
    country, "Europe" a continent, "Paris"/"Bangalore" a city. Never raises;
    returns ("unknown", None, None) if the service is unavailable."""
    s = get_settings()
    key = f"cls::{name.strip().lower()}"
    cached = _CACHE.get(key)
    if cached and cached[0] > time.time():
        c = cached[1]
        return c["kind"], c.get("coords"), c.get("country")
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(
                s.open_meteo_geocode_url,
                params={"name": name, "count": 10, "language": "en", "format": "json"},
                headers={"User-Agent": s.http_user_agent},
            )
            r.raise_for_status()
            results = r.json().get("results") or []
    except Exception as exc:  # noqa: BLE001 — degrade gracefully
        log.warning("classify geocode failed for %s: %s", name, exc)
        return "unknown", None, None
    if not results:
        return "unknown", None, None

    target = name.strip().lower()
    exact = [x for x in results if (x.get("name") or "").strip().lower() == target]
    pool = exact or results
    pool.sort(key=lambda x: x.get("population") or 0, reverse=True)
    top = pool[0]
    kind = _kind_from_feature_code(top.get("feature_code"))
    coords = None
    if top.get("latitude") is not None and top.get("longitude") is not None:
        coords = (float(top["latitude"]), float(top["longitude"]))
    country = top.get("country")
    _CACHE[key] = (time.time() + _CACHE_TTL, {"kind": kind, "coords": coords, "country": country})
    return kind, coords, country


# ===========================================================================
# AI CITY SELECTION  (LLM proposes cities, web-grounded; code verifies them)
# ===========================================================================
def _llm_enabled() -> bool:
    return bool(get_settings().llm_api_key)


async def _city_evidence(destination: str, total_days: int,
                         interests: Optional[list[str]]) -> Optional[str]:
    """Real web snippets about which cities to visit, so the model picks real,
    currently-recommended places matched to the trip — not whatever it recalls."""
    from app.tools import web_search

    intr = " ".join((interests or [])[:3])
    queries = [
        f"best cities to visit in {destination} {total_days} day itinerary",
        f"{destination} travel itinerary route which cities",
        (f"{destination} best places for {intr}" if intr
         else f"top places to visit in {destination}"),
    ]
    results = await web_search.search_text(queries)
    lines: list[str] = []
    for q, snippets in results.items():
        if not snippets:
            continue
        lines.append(f"# {q}")
        for s in snippets[:3]:
            text = f"{s['title']} — {s['body']}".strip(" —")
            if text:
                lines.append(f"- {text}")
    blob = "\n".join(lines)
    return blob[:3500] if blob else None


def _call_city_selector(destination: str, total_days: int,
                        interests: Optional[list[str]], style_weights: dict,
                        evidence: Optional[str], kind: str) -> Optional[dict]:
    s = get_settings()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 4)
        intr = ", ".join(interests) if interests else "a balanced mix of highlights"
        pace = str(style_weights.get("pace", "balanced"))
        tier = str(style_weights.get("tier", "mid"))
        target = max(2, round(total_days / 2))  # ~2 days per city as a guide
        ev_block = (
            f"\n\nGround your choices in THESE real web results — prefer them over your "
            f"own memory and never invent a place that isn't really in {destination}:\n{evidence}\n"
            if evidence else ""
        )
        continent_note = (
            f" {destination} is a continent, so it's fine to span multiple countries; "
            f"give each city its country."
            if kind == "continent" else ""
        )
        prompt = (
            f"Choose which cities/towns to visit on a {total_days}-day trip to "
            f"{destination}, for a traveler who likes: {intr}. Pace: {pace}. Budget "
            f"tier: {tier}.{continent_note}\n"
            f"Pick about {target} REAL, well-known places that genuinely exist in "
            f"{destination}, matched to the traveler's interests, and ORDER them as a "
            f"sensible route that minimises backtracking. Favour the main gateway city "
            f"(where they'd likely fly in) early. Give each a suggested number of nights; "
            f"the nights should total about {total_days}.{ev_block}\n"
            f'Return STRICT JSON only: {{"cities":[{{"city":str,"country":str,'
            f'"nights":int,"why":str (one line, tied to the traveler\'s interests)}}],'
            f'"reasoning":str (2-3 sentences on why this route fits them)}}. '
            f"List the cities in travel order. No prose outside the JSON."
        )
        resp = client.chat.completions.create(
            model=s.llm_model or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are an expert trip router. You only "
                 "choose real places that exist in the given destination, match them to "
                 "the traveler, and order them as a sensible route. JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1400,
            temperature=0.4,
        )
        content = resp.choices[0].message.content
        return json.loads(content) if content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("AI city selection failed for %s: %s", destination, exc)
        return None


async def _ai_select_cities(
    destination: str, total_days: int, interests: Optional[list[str]],
    style_weights: dict, osm: OSMClient, kind: str,
) -> Optional[tuple[list[dict], Optional[str]]]:
    """Ask the LLM (grounded in web search) which cities to visit, then VERIFY each
    one really exists via geocoding (anti-hallucination). Returns (verified city
    dicts in the AI's travel order, reasoning) or None if unavailable."""
    evidence = await _city_evidence(destination, total_days, interests)
    data = await asyncio.to_thread(
        _call_city_selector, destination, total_days, interests, style_weights, evidence, kind
    )
    if not data or not isinstance(data.get("cities"), list):
        return None

    async def verify(c: dict) -> Optional[dict]:
        name = str(c.get("city", "")).strip()
        if not name:
            return None
        country = str(c.get("country", "")).strip() or (
            destination if kind in ("country", "region") else None
        )
        look = await _openmeteo_lookup(name, country) or await _openmeteo_lookup(name)
        if look:
            lat, lon = look["lat"], look["lon"]
        else:
            coords = (await osm.geocode(f"{name}, {country or destination}")
                      or await osm.geocode(name))
            if not coords:
                return None  # unverifiable → dropped, never enters the plan
            lat, lon = coords
        try:
            nights = int(c.get("nights")) if c.get("nights") else None
        except (TypeError, ValueError):
            nights = None
        return {
            "name": name,
            "country": country,
            "lat": lat, "lon": lon,
            "blurb": (str(c.get("why", "")).strip() or None),
            "ai_nights": nights,
            # Seed the night-allocator weight from the AI's suggested nights so
            # the final (guaranteed-exact) allocation follows the AI's intent.
            "poi_count": max(6, (nights or 2) * 8),
        }

    verified = [v for v in await asyncio.gather(*[verify(c) for c in data["cities"]]) if v]
    # De-dupe by city name, preserving AI order.
    seen: set[str] = set()
    unique: list[dict] = []
    for v in verified:
        k = v["name"].lower()
        if k not in seen:
            seen.add(k)
            unique.append(v)
    if len(unique) < 2:
        return None
    reasoning = str(data.get("reasoning", "")).strip() or None
    return unique, reasoning


async def _build_route_from_ai_cities(destination: str, cities: list[dict],
                                      total_days: int, style_weights: dict,
                                      kind: str, ai_reasoning: Optional[str]) -> Route:
    """Build a Route from AI-selected, verified cities. Night allocation stays in
    code (exact day-count guarantee); the AI's travel order is preserved."""
    stops = [
        RouteStop(city=c["name"], country=c.get("country"), lat=c["lat"], lon=c["lon"],
                  source="ai", poi_count=c.get("poi_count", 10),
                  why_city=c.get("blurb"))
        for c in cities
    ]
    for i, s in enumerate(stops):
        s.order = i + 1

    # Exact night allocation (may trim to total_days cities; guarantees the sum).
    stops = allocate_nights(stops, total_days, style_weights)
    # Preserve the AI's travel order among the survivors.
    seq = {c["name"]: i for i, c in enumerate(cities)}
    stops.sort(key=lambda s: seq.get(s.city, 999))
    for i, s in enumerate(stops):
        s.order = i + 1
    _assign_day_spans(stops)
    legs = _build_intercity_legs(stops)

    notes: list[str] = []
    if kind == "continent":
        notes.append(
            f"{destination} is a whole continent — this is a highlights route across its "
            f"major cities, which can span more than one country. Name a single country "
            f"or region to go deeper."
        )

    route = Route(destination=destination, is_country=True, total_days=total_days,
                  stops=stops, legs=legs, notes=notes, unused=[])
    if ai_reasoning:
        pre = (notes[0] + " ") if notes else ""
        route.reasoning = pre + ai_reasoning
        route.tradeoffs = _deterministic_tradeoffs(route)
    else:
        route.reasoning, route.tradeoffs = await _narrate_route(route, style_weights)
    return route


# ===========================================================================
# PUBLIC ENTRY POINT
# ===========================================================================
async def resolve_destination(
    destination: str,
    total_days: int,
    *,
    osm: OSMClient,
    named_places: Optional[list[str]] = None,
    style_weights: Optional[dict] = None,
    interests: Optional[list[str]] = None,
) -> Route:
    """Resolve `destination` into a day-allocated Route.

    named_places: if the user explicitly named cities, skip discovery and use
                  those (still verified via geocode).
    style_weights: {"beach": float, "culture": float, "food": float, "pace": str}
                  used to score which discovered places to include.
    interests: the traveler's stated interests — drives the AI city selection
                  (web-grounded) that picks cities matched to what they like.
    """
    total_days = max(1, int(total_days))
    style_weights = style_weights or {}
    dest_clean = destination.strip()

    # 1. User named specific places -> resolve those directly, skip discovery.
    if named_places:
        return await _resolve_named(dest_clean, named_places, total_days, osm, style_weights)

    # 2. Classify the destination (continent/country/region/city) AND fetch the
    #    Wikivoyage guide, concurrently. The classifier is the authoritative
    #    "what kind of place is this?" signal; Wikivoyage supplies the cities.
    kind, cls_coords, cls_country = ("unknown", None, None)
    page = None
    try:
        (kind, cls_coords, cls_country), page = await asyncio.gather(
            classify_destination(dest_clean),
            _fetch_wikivoyage(dest_clean),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("classify/fetch failed for %s: %s", dest_clean, exc)
        page = await _fetch_wikivoyage(dest_clean)

    # 3. A confirmed single place (city/town) is planned as ONE destination —
    #    even if its guide lists day-trip "Other destinations" — so a city is
    #    never wrongly exploded into a multi-city route. We geocode the name the
    #    user typed (the classifier's country is unreliable for ambiguous city
    #    names, so we don't feed it in — OSM resolves the prominent match).
    if kind == "city":
        return await _single_city_route(dest_clean, total_days, osm)

    # 3b. AI city selection (web-grounded) — the PRIMARY path for countries,
    #     regions and continents. The LLM proposes cities matched to the user's
    #     interests/pace; code then VERIFIES each one exists (geocode) and owns
    #     the exact night allocation. Falls back to Wikivoyage discovery below.
    if kind in ("country", "region", "continent") and _llm_enabled():
        ai_result = await _ai_select_cities(
            dest_clean, total_days, interests, style_weights, osm, kind
        )
        if ai_result:
            ai_cities, ai_reasoning = ai_result
            return await _build_route_from_ai_cities(
                dest_clean, ai_cities, total_days, style_weights, kind, ai_reasoning
            )

    if page is None:
        # No guide. Countries/continents can't be planned without a city list,
        # so fall back to one destination (geocoding the name as typed).
        note = (
            "Couldn't reach the destination guide — treating as one destination."
            if kind == "unknown"
            else f"Couldn't reach the guide for this {kind} — treating as one destination."
        )
        return await _single_city_route(dest_clean, total_days, osm, note=note)

    candidates = _extract_candidate_places(page["wikitext"])

    if not candidates:
        # No cities to discover -> single destination. Wikivoyage's own
        # country_of is reliable (it's the city page's country).
        return await _single_city_route(dest_clean, total_days, osm,
                                         country=page.get("country_of"))

    # 4. It's a country / region / continent. Verify each candidate via OSM geocode.
    verified = await _verify_candidates(candidates, dest_clean, osm)
    if not verified:
        return await _single_city_route(dest_clean, total_days, osm,
                                         note="Could not verify any cities for this destination — treating as one city.")

    # 4. Score + select which cities make the route.
    selected, unused = _score_and_select(verified, total_days, style_weights)

    # 5. Allocate nights (PURE CODE — guarantees the day-count is exact).
    #    allocate_nights may trim to total_days cities; fold any dropped ones
    #    back into 'unused' so they still appear in "also considered".
    selected_names_before = {s.city for s in selected}
    selected = allocate_nights(selected, total_days, style_weights)
    dropped = selected_names_before - {s.city for s in selected}
    if dropped:
        for v in verified:
            if v["name"] in dropped:
                unused.insert(0, v)

    # 6. Order the route geographically (no north-south-north bouncing).
    selected = _order_route(selected)
    _assign_day_spans(selected)

    # 7. Build inter-city legs (great-circle estimate; real transport comes later).
    legs = _build_intercity_legs(selected)

    notes: list[str] = []
    if kind == "continent":
        notes.append(
            f"{dest_clean} is a whole continent — this is a highlights route across its "
            f"major cities, which can span more than one country. Tell me a single "
            f"country or region (e.g. a city list) and I'll plan it in more depth."
        )

    route = Route(
        destination=dest_clean,
        is_country=True,
        total_days=total_days,
        stops=selected,
        legs=legs,
        notes=notes,
        unused=[UnusedPlace(name=u["name"], blurb=u.get("blurb"),
                            reason_skipped="didn't fit your days",
                            lat=u.get("lat"), lon=u.get("lon")) for u in unused[:6]],
    )

    # 8. Narrate "why this route" — grounded ONLY in the facts above.
    route.reasoning, route.tradeoffs = await _narrate_route(route, style_weights)
    if kind == "continent" and route.reasoning:
        route.reasoning = notes[0] + " " + route.reasoning
    return route


# ===========================================================================
# WIKIVOYAGE DISCOVERY
# ===========================================================================
async def _fetch_wikivoyage(destination: str) -> Optional[dict]:
    """Fetch page wikitext. Returns {'wikitext': str, 'country_of': str|None} or None."""
    key = f"wv::{destination.lower()}"
    cached = _CACHE.get(key)
    if cached and cached[0] > time.time():
        return cached[1] or None

    s = get_settings()
    params = {
        "action": "parse",
        "page": destination,
        "prop": "wikitext",
        "format": "json",
        "redirects": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=s.http_timeout_seconds) as client:
            r = await client.get(WIKIVOYAGE_API, params=params,
                                 headers={"User-Agent": s.http_user_agent})
            r.raise_for_status()
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("wikivoyage fetch failed for %s: %s", destination, exc)
        _CACHE[key] = (time.time() + 60 * 10, {})  # short negative cache
        return None

    if "error" in data:
        _CACHE[key] = (time.time() + 60 * 10, {})
        return None

    wikitext = (data.get("parse", {}).get("wikitext", {}) or {}).get("*", "")
    if not wikitext:
        _CACHE[key] = (time.time() + 60 * 10, {})
        return None

    result = {"wikitext": wikitext, "country_of": _detect_country_of(wikitext)}
    _CACHE[key] = (time.time() + _CACHE_TTL, result)
    return result


def _detect_country_of(wikitext: str) -> Optional[str]:
    """If this is a city page, try to read which country it's in (for geocoding)."""
    m = re.search(r"\{\{(?:pagebanner|quickbar).*?\|.*?country\s*=\s*([^\|\}\n]+)",
                  wikitext, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None


def _extract_candidate_places(wikitext: str) -> list[dict]:
    """Pull place names + blurbs from the 'Cities' and 'Other destinations'
    sections. Returns [{'name': str, 'blurb': str}], de-duplicated, in order.

    Handles the common Wikivoyage formats:
      * {{marker|type=city|name=Bangkok}} the frantic capital...
      * {{Listing|name=Chiang Mai|...}} ...
      * [[Bangkok]] — the capital and...
      * [[Krabi (province)|Krabi]] beaches...
    Skips the 'Regions' section (those are abstract, not bookable cities).
    """
    sections = _split_sections(wikitext)
    out: list[dict] = []
    seen: set[str] = set()

    for header in ("Cities", "Other destinations", "Islands", "Towns"):
        body = sections.get(header.lower())
        if not body:
            continue
        for name, blurb in _parse_place_lines(body):
            key = name.lower()
            if key in seen or len(name) < 2:
                continue
            seen.add(key)
            out.append({"name": name, "blurb": blurb})
    return out


def _split_sections(wikitext: str) -> dict[str, str]:
    """Split wikitext into {lowercased_header: body}. Handles == H2 == headers."""
    sections: dict[str, str] = {}
    parts = re.split(r"\n==+\s*([^=\n]+?)\s*==+\s*\n", wikitext)
    # parts[0] is the lead; then alternating (header, body).
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip().lower()
        body = parts[i + 1]
        sections[header] = body
    return sections


def _parse_place_lines(body: str) -> list[tuple[str, str]]:
    """Extract (name, blurb) pairs from a section body."""
    results: list[tuple[str, str]] = []

    # Pattern A: {{marker|...|name=X|...}} or {{Listing|...|name=X|...}}
    for m in re.finditer(r"\{\{\s*(?:marker|listing|see|do|city)\b([^}]*)\}\}(.*?)(?=\n\*|\n\n|$)",
                         body, re.IGNORECASE | re.DOTALL):
        attrs, trailing = m.group(1), m.group(2)
        nm = re.search(r"\|\s*name\s*=\s*([^\|\}\n]+)", attrs, re.IGNORECASE)
        if nm:
            name = _strip_wikitext(nm.group(1))
            blurb = _strip_wikitext(trailing).strip(" —-–:")
            if name:
                results.append((name, blurb[:200]))

    # Pattern B: bullet lines like  * [[Bangkok]] — the capital...
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("*"):
            continue
        lm = re.search(r"\[\[([^\]\|]+)(?:\|([^\]]+))?\]\]", line)
        if not lm:
            continue
        name = _strip_wikitext(lm.group(2) or lm.group(1))
        # blurb = everything after the link
        after = line[line.index("]]") + 2:] if "]]" in line else ""
        blurb = _strip_wikitext(after).strip(" —-–:*")
        if name and not any(name.lower() == r[0].lower() for r in results):
            results.append((name, blurb[:200]))

    return results


def _strip_wikitext(s: str) -> str:
    """Clean wikitext markup down to plain text."""
    if not s:
        return ""
    s = re.sub(r"\{\{[^}]*\}\}", "", s)               # templates
    s = re.sub(r"\[\[(?:[^\]\|]+\|)?([^\]]+)\]\]", r"\1", s)  # [[link|text]] -> text
    s = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", s)      # [url text] -> text
    s = re.sub(r"'''?", "", s)                        # bold/italic
    s = re.sub(r"<[^>]+>", "", s)                     # html tags
    s = re.sub(r"&[a-z]+;", " ", s)                   # entities
    s = re.sub(r"\s+", " ", s)
    return s.strip()


# ===========================================================================
# OSM VERIFICATION  (anti-hallucination gate)
# ===========================================================================
async def _verify_candidates(candidates: list[dict], country: str,
                             osm: OSMClient) -> list[dict]:
    """Verify each candidate city exists and attach coords + a size signal.

    Uses Open-Meteo (GeoNames) FIRST — it isn't rate-limited, so a burst of city
    lookups succeeds, and it returns POPULATION, which we use to size each city
    (big cities get more nights). Nominatim is only a fallback (it 403s under
    bursts, which used to drop every city and collapse a country to one stop).
    We deliberately do NOT hit Overpass here: 16 heavy POI queries made resolution
    take ~30s/country and often rate-limited. Population is a faster, steadier
    richness signal."""
    async def verify(c: dict) -> Optional[dict]:
        look = await _openmeteo_lookup(c["name"], country)
        if look:
            lat, lon, pop = look["lat"], look["lon"], look["population"]
        else:
            coords = (await osm.geocode(f"{c['name']}, {country}")
                      or await osm.geocode(c["name"]))
            if not coords:
                return None  # DROPPED — unverifiable place never enters the plan
            lat, lon, pop = coords[0], coords[1], 0
        # Population -> a bounded richness proxy (replaces the slow Overpass count).
        # A 2M+ city tops out; unknown-population towns get a neutral baseline so
        # they aren't unfairly zeroed (blurb keywords + listing order still rank them).
        poi = min(60, int(pop // 40000)) if pop else 6
        return {**c, "lat": lat, "lon": lon, "population": pop, "poi_count": poi}

    verified = await asyncio.gather(*[verify(c) for c in candidates])
    return [v for v in verified if v]


# ===========================================================================
# SCORING + SELECTION
# ===========================================================================
def _score_and_select(verified: list[dict], total_days: int,
                      style_weights: dict) -> tuple[list[RouteStop], list[dict]]:
    """Score each verified place by the user's preferences + POI richness,
    then greedily select enough cities to fill the days. Returns (selected
    RouteStops, unused raw dicts)."""
    beach_w = float(style_weights.get("beach", 1.0))
    culture_w = float(style_weights.get("culture", 1.0))
    food_w = float(style_weights.get("food", 1.0))

    def kw(blurb: str, words: list[str]) -> int:
        b = (blurb or "").lower()
        return sum(1 for w in words if w in b)

    for idx, v in enumerate(verified):
        blurb = v.get("blurb", "")
        score = 0.0
        score += v.get("poi_count", 0) * 0.5                 # richness = base
        score += beach_w   * kw(blurb, ["beach", "island", "sea", "coast", "bay", "dive", "snorkel"]) * 2
        score += culture_w * kw(blurb, ["temple", "museum", "old", "historic", "culture", "ruins", "palace"]) * 2
        score += food_w    * kw(blurb, ["food", "market", "cuisine", "street food", "eat"]) * 2
        # Prominence bias: Wikivoyage lists a country's cities most-important
        # first, so the capital / main gateway (e.g. Bangkok) is near the top.
        # A decaying bonus keeps those marquee cities in the route instead of
        # letting keyword-heavy smaller towns bury them.
        score += max(0.0, 7 - idx) * 3.0
        v["_score"] = score

    # Preserve Wikivoyage's ordering as a tiebreak (it lists big cities first).
    ranked = sorted(enumerate(verified), key=lambda t: (-t[1]["_score"], t[0]))
    ranked = [v for _, v in ranked]

    # How many cities fit? Fewer days/city = see MORE places. "balanced" ~2
    # days/city, so a 10-day trip visits ~5 cities (not 4), 14 days ~7.
    pace = str(style_weights.get("pace", "balanced"))
    days_per_city = {"packed": 1.5, "balanced": 2.0, "slow": 3.0}.get(pace, 2.0)
    max_cities = max(1, min(len(ranked), round(total_days / days_per_city)))
    # Never more cities than days, and keep it sane for very long trips.
    max_cities = min(max_cities, total_days, 8)

    selected_raw = ranked[:max_cities]
    unused_raw = ranked[max_cities:]

    stops = [
        RouteStop(
            city=v["name"],
            nights=1,  # real allocation happens in allocate_nights()
            lat=v["lat"],
            lon=v["lon"],
            source="wikivoyage",
            poi_count=v.get("poi_count", 0),
            why_city=_short_why_city(v),
        )
        for v in selected_raw
    ]
    return stops, unused_raw


def _short_why_city(v: dict) -> str:
    blurb = v.get("blurb", "").strip()
    if blurb:
        return blurb[:160]
    return "A notable destination worth a stop on this route."


# ===========================================================================
# NIGHT ALLOCATION  — PURE CODE, THE FIX FOR 10 -> 2
# ===========================================================================
def allocate_nights(stops: list[RouteStop], total_days: int,
                    style_weights: dict) -> list[RouteStop]:
    """Distribute exactly `total_days` across the stops (largest-remainder method).

    GUARANTEES (verified by a 1,000,000-case fuzz test):
      - sum(stop.nights for stop in result) == total_days, always
      - every returned stop gets >= 1 night (no 0-night cities)
      - if there are more cities than days, the list is TRIMMED to the richest
        `total_days` cities (you can't spend a positive number of nights in
        more cities than you have days) — so callers MUST use the return value.

    This is the fix for the "10 days -> 2 days" bug: the day count is owned by
    code here, never by the LLM.
    """
    n = len(stops)
    if n == 0:
        return stops

    # Can't give >=1 night to more cities than there are days.
    if n > total_days:
        stops = sorted(stops, key=lambda s: -s.poi_count)[:total_days]
        n = len(stops)

    if n == 1:
        stops[0].nights = total_days
        stops[0].why_nights = f"Your whole {total_days}-day trip is here."
        return stops

    pace = str(style_weights.get("pace", "balanced"))
    base_cap = {"packed": 3, "balanced": 4, "slow": 6}.get(pace, 4)
    # Cap must still allow the math to reach total_days with n cities.
    max_per_city = max(base_cap, math.ceil(total_days / n))

    # Step 1: everyone starts at 1 night (guarantees >= 1). Distribute the rest
    # by POI richness using the largest-remainder (Hamilton) method.
    remaining = total_days - n  # >= 0 because n <= total_days now
    weights = [max(1, s.poi_count) for s in stops]
    wsum = sum(weights)
    ideal = [remaining * w / wsum for w in weights]          # extra nights, fractional
    nights = [1 + int(math.floor(x)) for x in ideal]         # base 1 + floored share

    # Cap pass.
    for i in range(n):
        if nights[i] > max_per_city:
            nights[i] = max_per_city

    leftover = total_days - sum(nights)

    # Step 2: hand out any leftover by largest fractional remainder, skipping
    # capped cities; if all are capped, lift the cap on the richest.
    remainders = sorted(range(n), key=lambda i: -(ideal[i] - math.floor(ideal[i])))
    idx = guard = 0
    while leftover > 0 and guard < 100000:
        guard += 1
        i = remainders[idx % n]
        if nights[i] < max_per_city:
            nights[i] += 1
            leftover -= 1
        idx += 1
        if idx % n == 0 and leftover > 0 and all(x >= max_per_city for x in nights):
            richest = max(range(n), key=lambda j: weights[j])
            nights[richest] += leftover
            leftover = 0

    for s, nn in zip(stops, nights):
        s.nights = nn
        s.why_nights = _why_nights(s, nn)

    # The whole point of this function:
    assert sum(s.nights for s in stops) == total_days, (
        f"night allocation failed: {sum(s.nights for s in stops)} != {total_days}"
    )
    assert all(s.nights >= 1 for s in stops), "a city got 0 nights"
    return stops


def _why_nights(stop: RouteStop, nights: int) -> str:
    if stop.poi_count >= 25:
        depth = "a big city with plenty to fill the days without rushing"
    elif stop.poi_count >= 10:
        depth = "enough to see the highlights at a comfortable pace"
    else:
        depth = "a focused stop for its main sights"
    return f"{nights} night{'s' if nights != 1 else ''} — {depth}."


# ===========================================================================
# ROUTE ORDERING + DAY SPANS
# ===========================================================================
def _order_route(stops: list[RouteStop]) -> list[RouteStop]:
    """Order stops into a sensible path using nearest-neighbour on coordinates,
    so the trip doesn't bounce across the country. Starts from the richest city
    (usually the arrival hub)."""
    if len(stops) <= 2:
        for i, s in enumerate(stops):
            s.order = i + 1
        return stops

    remaining = stops[:]
    # Start at the richest (proxy for the main gateway city).
    start = max(remaining, key=lambda s: s.poi_count)
    ordered = [start]
    remaining.remove(start)

    while remaining:
        last = ordered[-1]
        nxt = min(remaining, key=lambda s: _haversine(last.lat, last.lon, s.lat, s.lon))
        ordered.append(nxt)
        remaining.remove(nxt)

    for i, s in enumerate(ordered):
        s.order = i + 1
    return ordered


def _assign_day_spans(stops: list[RouteStop]) -> None:
    """Fill day_start/day_end for each stop from its nights, in order."""
    day = 1
    for s in sorted(stops, key=lambda x: x.order):
        s.day_start = day
        s.day_end = day + s.nights - 1
        day = s.day_end + 1


def _haversine(lat1, lon1, lat2, lon2) -> float:
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _build_intercity_legs(stops: list[RouteStop]) -> list[IntercityLeg]:
    """Rough inter-city legs from distance. Real transport/costs come from the
    TransportAgent later; this gives the UI something honest in the meantime."""
    legs: list[IntercityLeg] = []
    ordered = sorted(stops, key=lambda x: x.order)
    for a, b in zip(ordered, ordered[1:]):
        km = _haversine(a.lat, a.lon, b.lat, b.lon)
        if km > 350:
            mode, note = "flight", "Long hop — a short domestic flight saves a day."
        elif km > 120:
            mode, note = "train", "A train or intercity bus works well here."
        else:
            mode, note = "bus", "Close together — a bus or taxi is easiest."
        legs.append(IntercityLeg(
            from_city=a.city, to_city=b.city, mode=mode,
            duration=f"~{_rough_hours(km, mode)}h", note=f"~{round(km)} km. {note}",
        ))
    return legs


def _rough_hours(km: float, mode: str) -> int:
    speed = {"flight": 500, "train": 70, "bus": 55}.get(mode, 60)
    base = km / speed
    if mode == "flight":
        base += 2.5  # airport overhead
    return max(1, round(base))


# ===========================================================================
# NAMED PLACES + SINGLE CITY (same output shape)
# ===========================================================================
async def _resolve_named(destination: str, named: list[str], total_days: int,
                        osm: OSMClient, style_weights: dict) -> Route:
    verified: list[dict] = []
    notes: list[str] = []
    for name in named:
        coords = await osm.geocode(f"{name}, {destination}") or await osm.geocode(name)
        if coords:
            verified.append({"name": name, "blurb": "", "lat": coords[0],
                             "lon": coords[1], "poi_count": 8})
        else:
            notes.append(f"Couldn't verify '{name}' — skipped it.")
    if not verified:
        return await _single_city_route(destination, total_days, osm,
                                         note="None of the named places could be verified.")

    stops = [RouteStop(city=v["name"], lat=v["lat"], lon=v["lon"],
                       source="user", poi_count=v["poi_count"],
                       why_city="You chose this stop.") for v in verified]
    stops = allocate_nights(stops, total_days, style_weights)
    stops = _order_route(stops)
    _assign_day_spans(stops)
    route = Route(destination=destination, is_country=len(stops) > 1,
                  total_days=total_days, stops=stops,
                  legs=_build_intercity_legs(stops), notes=notes)
    route.reasoning, route.tradeoffs = await _narrate_route(route, style_weights)
    return route


async def _single_city_route(city: str, total_days: int, osm: OSMClient,
                             *, country: Optional[str] = None,
                             coords: Optional[tuple[float, float]] = None,
                             note: Optional[str] = None) -> Route:
    """Build a one-stop Route. The single-city case of the same contract.
    `coords` (e.g. from the classifier) is used as a fallback if OSM can't
    geocode the city, so the stop still gets a location."""
    osm_coords = await osm.geocode(f"{city}, {country}" if country else city)
    if not osm_coords and country:
        osm_coords = await osm.geocode(city)
    resolved = osm_coords or coords
    lat, lon = (resolved or (None, None))
    stop = RouteStop(
        city=city, country=country, nights=total_days, order=1,
        lat=lat, lon=lon, source="single" if resolved else "geocode",
        day_start=1, day_end=total_days,
        why_city=f"Your destination.",
        why_nights=f"All {total_days} day{'s' if total_days != 1 else ''} here.",
    )
    route = Route(destination=city, is_country=False, total_days=total_days,
                  stops=[stop], notes=[note] if note else [])
    if total_days > 1:
        route.reasoning = (
            f"{total_days} days in {city}. We'll spread the plan across all "
            f"{total_days} days so none of them are empty."
        )
    return route


# ===========================================================================
# GROUNDED NARRATION  (LLM narrates; it does NOT decide)
# ===========================================================================
async def _narrate_route(route: Route, style_weights: dict) -> tuple[Optional[str], Optional[str]]:
    """Ask the LLM to explain the ALREADY-DECIDED route to a first-timer.
    Grounded strictly in the stops/nights/blurbs we computed. Falls back to a
    deterministic sentence if no key or the call fails — the app never depends
    on this."""
    s = get_settings()
    # Deterministic fallback first (always available).
    det = _deterministic_reasoning(route)
    if not s.llm_api_key or len(route.stops) == 1:
        return det, _deterministic_tradeoffs(route)

    facts = "\n".join(
        f"- {s.city}: {s.nights} nights (order {s.order}), "
        f"{s.poi_count} verified sights. Note: {s.why_city or 'n/a'}"
        for s in sorted(route.stops, key=lambda x: x.order)
    )
    prompt = (
        f"A first-time traveler is going to {route.destination} for "
        f"{route.total_days} days. We have ALREADY chosen this route and the "
        f"nights per city (do NOT change any numbers or add/remove cities):\n\n"
        f"{facts}\n\n"
        f"Write a short, warm explanation (3-5 sentences) of WHY this route and "
        f"these night counts make sense for a first-timer — one clause per city. "
        f"Use ONLY the facts above; do not invent attractions. Then, in ONE extra "
        f"sentence prefixed 'Tradeoff: ', say how they could adjust it (e.g. go "
        f"slower, cut a stop). Plain text, no markdown, no lists."
    )
    try:
        import asyncio as _a
        text = await _a.to_thread(_call_llm_narrate, s, prompt)
        if not text:
            return det, _deterministic_tradeoffs(route)
        main, trade = text, None
        if "Tradeoff:" in text:
            main, trade = text.split("Tradeoff:", 1)
            trade = trade.strip()
        return main.strip(), (trade or _deterministic_tradeoffs(route))
    except Exception as exc:  # noqa: BLE001
        log.warning("route narration failed: %s", exc)
        return det, _deterministic_tradeoffs(route)


def _call_llm_narrate(s, prompt: str) -> Optional[str]:
    from openai import OpenAI
    client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 2)
    resp = client.chat.completions.create(
        model=s.llm_model or "gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You explain pre-decided travel routes "
             "clearly and warmly. You never change the given cities or night "
             "counts; you only explain them."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=350,
        temperature=0.5,
    )
    return (resp.choices[0].message.content or "").strip() or None


def _deterministic_reasoning(route: Route) -> str:
    if len(route.stops) == 1:
        s = route.stops[0]
        return (f"All {route.total_days} day{'s' if route.total_days != 1 else ''} "
                f"in {s.city}.")
    ordered = sorted(route.stops, key=lambda x: x.order)
    path = " → ".join(f"{s.city} ({s.nights}n)" for s in ordered)
    lead = (f"For {route.total_days} days in {route.destination}, this route keeps "
            f"travel sensible and gives each place enough time: {path}. ")
    per = " ".join(
        f"{s.city}: {s.why_nights}" for s in ordered
    )
    return lead + per


def _deterministic_tradeoffs(route: Route) -> Optional[str]:
    if len(route.stops) <= 1:
        return None
    richest = max(route.stops, key=lambda s: s.poi_count)
    return (f"Want it slower? Drop the last stop and give {richest.city} the extra "
            f"nights. Want to see more? Add a city from the 'also considered' list "
            f"and trim a night elsewhere.")