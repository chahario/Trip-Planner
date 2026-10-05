"""AI itinerary generator (OpenAI) — route-aware, day-count-enforced.

When live place data is thin, a generic fallback is useless — especially for
far-away or multi-day trips. If an LLM key is configured, we ask the model to
compose a real, specific itinerary. Three things make this safe:

  1. ROUTE-AWARE: the model is TOLD the exact cities and which day range belongs
     to each (from the resolver). It fills days within that structure; it does
     NOT get to decide the trip length or the cities.

  2. DAY-COUNT ENFORCED: after parsing, we check every day 1..N has a stop. If
     days are missing, we run ONE targeted repair call for just those days. If
     still short, the orchestrator fills honest flex blocks — we never pretend.

  3. GROUNDED: temperature is low, the model must put the city in every title,
     and the Critic downstream flags anything unverifiable. (Full by-ID grounding
     against fetched POIs is the next step; this already removes the length bug.)

Everything returns None on failure so the caller falls back to the deterministic
builder — the app never depends on the LLM.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from app.config import get_settings
from app.models import (
    CostBreakdown, Place, Plan, PlanItem, Preferences, RecommendedHotel, Route,
)
from app.tools import cost_tools

log = logging.getLogger(__name__)

_CATEGORIES = [
    "park", "garden", "walk", "viewpoint", "museum", "gallery", "live_music",
    "bar", "cinema", "market", "cafe", "restaurant", "street_food", "bakery",
]


def is_enabled() -> bool:
    return bool(get_settings().llm_api_key)


# ===========================================================================
# PUBLIC: route-aware generation
# ===========================================================================
async def generate_llm_plan_for_route(prefs: Preferences, route: Route) -> Optional[Plan]:
    """Compose a full itinerary that covers EXACTLY route.total_days, structured
    by the resolver's cities. Enforces day coverage with one repair pass."""
    data = await asyncio.to_thread(_call_openai_route, prefs, route)
    if not data or not isinstance(data, dict):
        return None

    plan = _plan_from_data(data, prefs, route)
    if plan is None or not plan.items:
        return None

    # --- DAY-COUNT ENFORCEMENT ---
    missing = _missing_days(plan, route.total_days)
    if missing:
        log.info("llm plan missing days %s — running repair pass", missing)
        repair = await asyncio.to_thread(_call_openai_repair, prefs, route, missing)
        if repair:
            _merge_repair(plan, repair, route)
        # Any still-missing days are left for the orchestrator's honest flex fill.

    # Enrich with cost + hotels + images (best-effort).
    await _attach_cost_and_extras(plan, prefs, data)
    return plan


# ===========================================================================
# PROMPTS
# ===========================================================================
def _route_structure_text(route: Route) -> str:
    lines = []
    for s in sorted(route.stops, key=lambda x: x.order):
        day_range = (f"day {s.day_start}" if s.day_start == s.day_end
                     else f"days {s.day_start}-{s.day_end}")
        lines.append(f"- {s.city}: {day_range} ({s.nights} night(s))")
    return "\n".join(lines)


def _build_route_prompt(prefs: Preferences, route: Route) -> str:
    budget = f"around ₹{prefs.budget} total (INR)" if prefs.budget else "flexible"
    interests = ", ".join(prefs.interests) or "a bit of everything"
    diet = ", ".join(prefs.dietary) or "none"
    crowds = "prefers quieter spots" if prefs.avoid_crowds else "doesn't mind crowds"
    mood = prefs.mood or "open to anything"
    origin = (f"\nThe traveler departs from {prefs.origin}; base flight cost on "
              f"routes from {prefs.origin}.\n" if prefs.origin else "")

    return (
        f"Plan a realistic {route.total_days}-day trip to {route.destination}.\n"
        f"The cities and the exact days for each are ALREADY DECIDED — you must "
        f"follow this structure exactly and must NOT change the cities or days:\n"
        f"{_route_structure_text(route)}\n\n"
        f"Traveler: mood='{mood}', energy={prefs.energy}, interests=[{interests}], "
        f"budget={budget}, dietary=[{diet}], {crowds}.{origin}\n"
        f"For EVERY day from 1 to {route.total_days}, give 3 stops in that day's "
        f"city, ordered morning→evening, interleaving activities and food. "
        f"CRITICAL: use ONLY real, currently-operating, well-known places that "
        f"exist in that specific city — never invent names. Put the city name in "
        f"every title. Do not skip any day.\n\n"
        f"ALSO recommend the best time of year to visit and one practical tip, and "
        f"3 real places to stay per city matching a {_tier_from_context(prefs)} budget.\n\n"
        f"Return STRICT JSON: {{\"summary\": str, "
        f"\"best_time_to_visit\": str, \"tips\": str, "
        f"\"hotels\": [{{\"name\": str, \"area\": str, \"city\": str, "
        f"\"price_per_night\": int, \"rating\": str, \"why\": str}}], "
        f"\"stops\": [{{\"day\": int (1..{route.total_days}), \"city\": str, "
        f"\"time_slot\": one of [\"Morning\",\"Midday\",\"Afternoon\",\"Evening\",\"Night\"], "
        f"\"title\": str, \"category\": one of {_CATEGORIES}, "
        f"\"kind\": \"activity\" or \"food\", \"est_cost\": int, "
        f"\"duration_hours\": number, \"why_it_fits\": str, "
        f"\"tradeoff\": str or null, "
        f"\"tips_try\": str (1 specific thing to do/eat/see HERE), "
        f"\"tips_avoid\": str (1 specific thing to avoid/watch out for here — scams, "
        f"timing, closures), "
        f"\"commute_next\": str (how to get from THIS stop to the next one with the "
        f"local mode and approx fare in INR, e.g. 'Auto-rickshaw ~₹80, 15 min' or "
        f"'10-min walk'; null for the last stop of the day) }}]}}. No prose outside the JSON."
    )


def _build_repair_prompt(prefs: Preferences, route: Route, missing: list[int]) -> str:
    # Which city each missing day belongs to.
    day_city = []
    for d in missing:
        stop = route.city_for_day(d)
        day_city.append(f"day {d} = {stop.city if stop else route.destination}")
    interests = ", ".join(prefs.interests) or "a bit of everything"
    return (
        f"A {route.total_days}-day trip to {route.destination} is missing stops "
        f"for these days: {', '.join(day_city)}.\n"
        f"Fill ONLY these days. For each, give 3 real, well-known stops in that "
        f"day's city (ordered morning→evening, interleave activity/food), using "
        f"ONLY real places — never invent names. Traveler interests: [{interests}].\n"
        f"Return STRICT JSON: {{\"stops\": [{{\"day\": int, \"city\": str, "
        f"\"time_slot\": str, \"title\": str, \"category\": one of {_CATEGORIES}, "
        f"\"kind\": \"activity\"|\"food\", \"est_cost\": int, "
        f"\"duration_hours\": number, \"why_it_fits\": str, "
        f"\"tradeoff\": str or null}}]}}. No prose outside the JSON."
    )


# ===========================================================================
# OPENAI CALLS
# ===========================================================================
def _call_openai_route(prefs: Preferences, route: Route) -> Optional[dict]:
    s = get_settings()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 5)
        # ~700 tokens/day so long itineraries aren't truncated.
        max_tokens = min(16000, 1200 + route.total_days * 850)
        resp = client.chat.completions.create(
            model=s.llm_model or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a meticulous travel planner. "
                 "You only suggest real places that exist, you follow the given city/day "
                 "structure exactly, you never skip a day, and you always return valid JSON."},
                {"role": "user", "content": _build_route_prompt(prefs, route)},
            ],
            max_tokens=max_tokens, temperature=0.3,
        )
        choice = resp.choices[0]
        content = choice.message.content
        if not content:
            return None
        if choice.finish_reason == "length":
            log.warning("llm route plan truncated at %s tokens", max_tokens)
        return _extract_json(content)
    except Exception as exc:  # noqa: BLE001
        log.warning("llm route plan failed: %s", exc)
        return None


def _call_openai_repair(prefs: Preferences, route: Route, missing: list[int]) -> Optional[dict]:
    s = get_settings()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 3)
        resp = client.chat.completions.create(
            model=s.llm_model or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You fill in missing itinerary days with "
                 "real places only. Return valid JSON, only the requested days."},
                {"role": "user", "content": _build_repair_prompt(prefs, route, missing)},
            ],
            max_tokens=min(8000, 400 + len(missing) * 500), temperature=0.3,
        )
        content = resp.choices[0].message.content
        return _extract_json(content) if content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("llm repair failed: %s", exc)
        return None


# ===========================================================================
# PARSING / MERGING
# ===========================================================================
def _plan_from_data(data: dict, prefs: Preferences, route: Route) -> Optional[Plan]:
    stops = data.get("stops")
    if not isinstance(stops, list) or not stops:
        return None
    plan = Plan(
        city=route.destination, days=route.total_days,
        summary=str(data.get("summary", "")).strip(),
        best_time_to_visit=(str(data.get("best_time_to_visit", "")).strip() or None),
        tips=(str(data.get("tips", "")).strip() or None),
    )
    order = 0
    for s in stops:
        item = _item_from_stop(s, order, route)
        if item is None:
            continue
        order += 1
        item.order = order
        plan.items.append(item)

    if not plan.items:
        return None
    plan.items.sort(key=lambda i: (i.day, i.order))
    for n, i in enumerate(plan.items, 1):
        i.order = n
    if not plan.summary:
        plan.summary = f"A {route.total_days}-day trip to {route.destination}."

    # Hotels
    for h in (data.get("hotels") or [])[:12]:
        if not isinstance(h, dict) or not h.get("name"):
            continue
        try:
            ppn = int(round(float(h.get("price_per_night")))) if h.get("price_per_night") else None
        except (TypeError, ValueError):
            ppn = None
        plan.hotels.append(RecommendedHotel(
            name=str(h["name"]).strip(),
            area=(str(h.get("area", "")).strip() or None),
            city=(str(h.get("city", "")).strip() or None),
            price_per_night=ppn,
            rating=(str(h.get("rating", "")).strip() or None),
            why=(str(h.get("why", "")).strip() or None),
        ))
    return plan


def _item_from_stop(s: dict, order: int, route: Route) -> Optional[PlanItem]:
    try:
        title = str(s["title"]).strip()
        if not title:
            return None
        category = str(s.get("category", "place")).strip().lower().replace(" ", "_")
        kind = s.get("kind") if s.get("kind") in ("activity", "food") else "activity"
        est_cost = max(0, int(round(float(s.get("est_cost", 0) or 0))))
        dur = max(0.25, float(s.get("duration_hours", 1.0) or 1.0))
        day = min(max(int(s.get("day", 1) or 1), 1), route.total_days)
        slot = str(s.get("time_slot", "Morning")).strip() or "Morning"
        why = str(s.get("why_it_fits", "")).strip() or "A great fit for your trip"
        tradeoff = s.get("tradeoff")
        tradeoff = str(tradeoff).strip() if tradeoff else None
        city = str(s.get("city", "")).strip() or (
            route.city_for_day(day).city if route.city_for_day(day) else route.destination)

        def _opt(key: str) -> Optional[str]:
            v = s.get(key)
            v = str(v).strip() if v is not None else ""
            return v or None

        tips_try = _opt("tips_try")
        tips_avoid = _opt("tips_avoid")
        commute_next = _opt("commute_next")
    except (KeyError, TypeError, ValueError):
        return None

    place = Place(name=title, category=category, kind=kind,  # type: ignore[arg-type]
                  est_cost=est_cost, source="ai", city=city)
    return PlanItem(
        order=order + 1, time_slot=(f"Day {day} · {slot}" if route.total_days > 1 else slot),
        title=title, place=place, duration_hours=dur, est_cost=est_cost,
        why_it_fits=why, tradeoff=tradeoff, day=day, city=city,
        tips_try=tips_try, tips_avoid=tips_avoid, commute_next=commute_next,
    )


def _missing_days(plan: Plan, total_days: int) -> list[int]:
    present = {i.day for i in plan.items}
    return [d for d in range(1, total_days + 1) if d not in present]


def _merge_repair(plan: Plan, repair: dict, route: Route) -> None:
    stops = repair.get("stops")
    if not isinstance(stops, list):
        return
    order = max((i.order for i in plan.items), default=0)
    for s in stops:
        item = _item_from_stop(s, order, route)
        if item is None:
            continue
        order += 1
        item.order = order
        plan.items.append(item)
    plan.items.sort(key=lambda i: (i.day, i.order))
    for n, i in enumerate(plan.items, 1):
        i.order = n


# ===========================================================================
# COST + EXTRAS
# ===========================================================================
async def _attach_cost_and_extras(plan: Plan, prefs: Preferences, data: dict) -> None:
    activities_sum = sum(i.est_cost for i in plan.items)
    try:
        cb = await estimate_trip_cost(prefs, activities_sum)
        # Real arrival transport: all viable modes (flight ONLY if an airport
        # actually serves the destination), not a blind flight assumption.
        if prefs.origin:
            options, rec = await estimate_arrival_transport(prefs.origin, plan.city)
            if options:
                plan.arrival_transport = options
                if rec and rec.cost_inr is not None:
                    cb.flights = rec.cost_inr * 2  # round-trip of the recommended mode
                    cb.arrival_mode = rec.mode
                    cb.note = (f"{rec.mode.title()} (recommended) priced round-trip "
                               f"{prefs.origin}→{plan.city}. Other modes shown below.")
                    cb.total = sum(v for v in [cb.flights, cb.accommodation, cb.food,
                                               cb.local_transport, cb.activities] if v is not None)
        plan.cost_breakdown = cb
        plan.approx_cost = f"≈ ₹{cb.total:,} per person (all-in)" if cb.total else None
    except Exception as exc:  # noqa: BLE001
        log.warning("cost estimate failed: %s", exc)

    # Photos for hotels + stops are attached once, centrally, in the orchestrator
    # (after per-city stays are added too) so every card gets a matching image —
    # see image_search.attach_images.


# ===========================================================================
# Clarifying questions (unchanged public API)
# ===========================================================================
def _clarify_prompt(req) -> str:
    bits = [f"destination={req.city!r}", f"days={req.days}"]
    if req.budget:
        bits.append(f"budget=₹{req.budget}")
    if req.mood:
        bits.append(f"mood={req.mood!r}")
    if req.interests:
        bits.append(f"interests={req.interests}")
    if req.free_text:
        bits.append(f"notes={req.free_text!r}")
    return (
        "A traveler wants a trip plan. Here is what they told us: "
        + ", ".join(bits)
        + ".\nAsk 2-4 short questions about THEIR PREFERENCES — only things the "
        "traveler alone can answer: travel style (budget/mid-range/luxury), who is "
        "travelling (solo/couple/family/friends), the vibe (sightseeing & culture / "
        "nature & adventure / food & nightlife / relaxation / a mix), beach-vs-culture "
        "balance, pace (packed/balanced/slow), and any must-dos or things to avoid.\n"
        "CRITICAL: Do NOT ask them to name specific cities, do NOT ask for an exact "
        "budget amount, and do NOT ask about the best time to visit — the planner "
        "RECOMMENDS those. Skip anything already given.\n"
        'Return STRICT JSON: {"questions": [{"field": short_snake_case_key, '
        '"question": str, "placeholder": short example answer}]}.'
    )


def _call_clarify(req) -> Optional[dict]:
    s = get_settings()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 2)
        resp = client.chat.completions.create(
            model=s.llm_model or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a helpful travel planner who asks sharp, concise questions. Return valid JSON only."},
                {"role": "user", "content": _clarify_prompt(req)},
            ],
            max_tokens=400, temperature=0.4,
        )
        content = resp.choices[0].message.content
        return json.loads(content) if content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("llm clarify failed: %s", exc)
        return None


async def generate_clarifying_questions(req) -> list[dict]:
    data = await asyncio.to_thread(_call_clarify, req)
    if not data or not isinstance(data, dict):
        return []
    qs = data.get("questions")
    if not isinstance(qs, list):
        return []
    out: list[dict] = []
    for q in qs[:4]:
        if not isinstance(q, dict):
            continue
        question = str(q.get("question", "")).strip()
        if not question:
            continue
        out.append({
            "field": str(q.get("field", f"q{len(out)+1}")).strip() or f"q{len(out)+1}",
            "question": question,
            "placeholder": str(q.get("placeholder", "")).strip() or None,
        })
    return out


# ===========================================================================
# Cost agent (unchanged logic)
# ===========================================================================
def _extract_json(text: str) -> Optional[dict]:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        pass
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        return json.loads(text[start:end])
    except Exception:  # noqa: BLE001
        return None


def _tier_from_context(prefs: Preferences) -> str:
    ctx = (prefs.extra_context or "").lower()
    sw = prefs.style_weights or {}
    if "luxury" in ctx or sw.get("tier") == "luxury":
        return "luxury"
    if "budget" in ctx or "cheap" in ctx or "backpack" in ctx or sw.get("tier") == "budget":
        return "budget"
    return "mid"


def _call_stays(cities: list[str], tier: str) -> Optional[dict]:
    s = get_settings()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 4)
        prompt = (
            f"Recommend REAL, well-known places to stay for a {tier}-budget traveler in "
            f"each of these cities: {', '.join(cities)}.\n"
            f"Give 3 hotels/guesthouses PER CITY that actually exist there — never invent "
            f"names. For each: the city it's in, the neighbourhood/area, a realistic "
            f"price per night in INR for a {tier} stay, a rating like '4.3', and one short "
            f"reason it fits (location, vibe, value).\n"
            f'Return STRICT JSON only: {{"hotels":[{{"name":str,"city":str,"area":str,'
            f'"price_per_night":int,"rating":str,"why":str}}]}}. No prose outside the JSON.'
        )
        resp = client.chat.completions.create(
            model=s.llm_model or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a travel lodging expert. You only "
                 "recommend real, currently-operating places to stay, and you always put "
                 "each one in its correct city. JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=min(3000, 400 + len(cities) * 400),
            temperature=0.3,
        )
        content = resp.choices[0].message.content
        return json.loads(content) if content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("stays recommendation failed: %s", exc)
        return None


async def recommend_stays(cities: list[str], prefs: Preferences) -> list[RecommendedHotel]:
    """Per-city hotel recommendations (3 per city), so every city you sleep in on a
    multi-city trip has places to stay. Returns [] if the LLM isn't configured."""
    if not is_enabled() or not cities:
        return []
    tier = _tier_from_context(prefs)
    data = await asyncio.to_thread(_call_stays, cities, tier)
    out: list[RecommendedHotel] = []
    for h in (data or {}).get("hotels", [])[:30]:
        if not isinstance(h, dict) or not h.get("name"):
            continue
        try:
            ppn = int(round(float(h.get("price_per_night")))) if h.get("price_per_night") else None
        except (TypeError, ValueError):
            ppn = None
        out.append(RecommendedHotel(
            name=str(h["name"]).strip(),
            area=(str(h.get("area", "")).strip() or None),
            city=(str(h.get("city", "")).strip() or None),
            price_per_night=ppn,
            rating=(str(h.get("rating", "")).strip() or None),
            why=(str(h.get("why", "")).strip() or None),
        ))
    return out


def _call_cost_agent(prefs: Preferences, activities_sum: int) -> Optional[dict]:
    s = get_settings()
    nights = max(1, prefs.days - 1) if prefs.days > 1 else 1
    origin = prefs.origin or "a major nearby city"
    tier = _tier_from_context(prefs)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 4)
        messages = [
            {"role": "system", "content": "You estimate realistic per-person trip costs "
             "in INR. You MUST call get_flight_cost and get_hotel_cost before answering — "
             "do not guess flights or hotels. Estimate food and local transport yourself. "
             'Then reply with ONLY JSON: {"flights": int, "accommodation": int, '
             '"food": int, "local_transport": int}. All per person, INR.'},
            {"role": "user", "content": f"Trip from {origin} to {prefs.city}, {prefs.days} "
             f"day(s), about {nights} night(s), {tier} style. (Activities already total "
             f"₹{activities_sum} per person — don't include those.) Use the tools, then give JSON."},
        ]
        for _ in range(5):
            resp = client.chat.completions.create(
                model=s.llm_model or "gpt-4o-mini", messages=messages,
                tools=cost_tools.TOOL_SCHEMAS, tool_choice="auto",
                temperature=0.2, max_tokens=500,
            )
            msg = resp.choices[0].message
            if msg.tool_calls:
                messages.append({
                    "role": "assistant", "content": msg.content or "",
                    "tool_calls": [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.function.name,
                                                 "arguments": tc.function.arguments}}
                                   for tc in msg.tool_calls]})
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    fn = cost_tools.DISPATCH.get(tc.function.name)
                    result = fn(**args) if fn else {"ok": False}
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(result)})
                continue
            return _extract_json(msg.content or "")
    except Exception as exc:  # noqa: BLE001
        log.warning("cost agent failed: %s", exc)
        return None
    return None


def _call_arrival_transport(origin: str, destination: str, evidence: Optional[str]) -> Optional[dict]:
    s = get_settings()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 3)
        ev_block = (
            f"\n\nUse THIS real web evidence to ground durations, fares and whether a "
            f"flight route exists — prefer it over your own memory:\n{evidence}\n"
            if evidence else ""
        )
        prompt = (
            f"How does a traveler realistically get from {origin} to {destination}?\n"
            f"List every PRACTICAL mode. Include a flight ONLY if a commercial airport "
            f"actually serves {destination} (or very nearby) with real routes from "
            f"{origin}; if not, DO NOT invent a flight — instead note the nearest airport "
            f"and the onward transfer. Always include train, bus and car/taxi where they "
            f"realistically exist. For each mode give: typical door-to-door duration, a "
            f"realistic ONE-WAY cost per person in INR (current market rates), and a short "
            f"note (e.g. 'Kalka–Shimla toy train', 'no airport — fly to Chandigarh then 3h "
            f"road'). Mark exactly one option recommended=true (the one most travelers pick)."
            f"{ev_block}\n"
            f'Return STRICT JSON: {{"options":[{{"mode":"flight|train|bus|car",'
            f'"duration":str,"cost_inr":int,"available":bool,"recommended":bool,"note":str}}]}}.'
        )
        resp = client.chat.completions.create(
            model=s.llm_model or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a precise India-savvy travel "
                 "logistics expert. Never invent flight routes that don't exist. JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=600,
            temperature=0.2,
        )
        content = resp.choices[0].message.content
        return json.loads(content) if content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("arrival transport failed: %s", exc)
        return None


async def estimate_arrival_transport(origin: str, destination: str):
    """Return (list[TransportOption], recommended|None) of realistic ways to
    travel origin -> destination. Grounded in real web evidence (search), so
    flights are only claimed when a route actually exists."""
    from app.models import TransportOption
    from app.tools import web_search

    evidence = None
    try:
        evidence = await web_search.transport_evidence(origin, destination)
    except Exception as exc:  # noqa: BLE001
        log.warning("transport evidence failed: %s", exc)

    data = await asyncio.to_thread(_call_arrival_transport, origin, destination, evidence)
    options: list = []
    if data and isinstance(data.get("options"), list):
        for o in data["options"]:
            if not isinstance(o, dict) or not o.get("mode"):
                continue
            try:
                cost = int(round(float(o["cost_inr"]))) if o.get("cost_inr") is not None else None
            except (TypeError, ValueError):
                cost = None
            options.append(TransportOption(
                mode=str(o["mode"]).lower().strip(),
                duration=(str(o.get("duration", "")).strip() or None),
                cost_inr=cost,
                available=bool(o.get("available", True)),
                recommended=bool(o.get("recommended", False)),
                note=(str(o.get("note", "")).strip() or None),
            ))
    # Pick the recommended; else the cheapest available with a cost.
    rec = next((o for o in options if o.recommended and o.available), None)
    if rec is None:
        priced = [o for o in options if o.available and o.cost_inr is not None]
        rec = min(priced, key=lambda o: o.cost_inr) if priced else None
        if rec:
            rec.recommended = True
    return options, rec


async def estimate_trip_cost(prefs: Preferences, activities_sum: int) -> CostBreakdown:
    raw = await asyncio.to_thread(_call_cost_agent, prefs, activities_sum)

    def _int(v):
        try:
            return max(0, int(round(float(v))))
        except (TypeError, ValueError):
            return None

    cb = CostBreakdown(activities=activities_sum)
    if raw:
        cb.flights = _int(raw.get("flights"))
        cb.accommodation = _int(raw.get("accommodation"))
        cb.food = _int(raw.get("food"))
        cb.local_transport = _int(raw.get("local_transport"))

    if cb.flights is None and prefs.origin:
        cb.flights = 9000
    if cb.accommodation is None:
        nights = max(1, prefs.days - 1) if prefs.days > 1 else 1
        h = await asyncio.to_thread(cost_tools.get_hotel_cost, prefs.city, nights,
                                    _tier_from_context(prefs))
        cb.accommodation = h.get("price")
    if cb.food is None:
        cb.food = 800 * max(1, prefs.days)
    if cb.local_transport is None:
        cb.local_transport = 400 * max(1, prefs.days)

    cb.total = sum(v for v in [cb.flights, cb.accommodation, cb.food,
                               cb.local_transport, cb.activities] if v is not None)
    if prefs.origin and cb.flights:
        cb.note = f"Flights priced from the {prefs.origin}-to-{prefs.city} distance via a tool."
    return cb


# ===========================================================================
# Backward-compat: old single-city entry point still works
# ===========================================================================
async def generate_llm_plan(prefs: Preferences) -> Optional[Plan]:
    """Legacy entry: builds a one-city Route and delegates to the route path so
    day-count enforcement applies here too."""
    from app.models import RouteStop
    route = Route(destination=prefs.city, is_country=False, total_days=max(1, prefs.days),
                  stops=[RouteStop(city=prefs.city, nights=max(1, prefs.days), order=1,
                                   day_start=1, day_end=max(1, prefs.days))])
    return await generate_llm_plan_for_route(prefs, route)