"""The agent orchestrator (multi-city).

Pipeline:
  1. parseUserPreferences
  2. resolveDestination   -> Route (country->cities, nights allocated, reasoning)
  3. per-city enrichment  -> weather + real POIs/food for EACH stop (parallel)
  4. buildItinerary       -> day-by-day LOOP across the route (day count is law)
  5. estimateCost
  6. criticCheck          -> repair short days / flag ungrounded, then validate
  7. routeStops           -> intra-/inter-city travel (optional)

Two entry points share one core:
  - run(req)           -> PlanResponse
  - run_streaming(req) -> async generator of TraceStep / PlanResponse events

Every failure mode degrades to a smaller-but-honest result, never a crash and
never silent generic placeholders.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional

from app.agent.clarify import detect_clarifying_questions
from app.models import (
    Place,
    Plan,
    PlanItem,
    PlanRequest,
    PlanResponse,
    Preferences,
    Route,
    RouteStop,
    TraceStep,
    Weather,
)
from app.tools import highlights as highlights_tool
from app.tools import llm_plan, routing
from app.tools import destination as destination_tool
from app.tools.generate import build_itinerary_from_route
from app.tools.options import get_options_for_stop
from app.tools.osm_client import OSMClient
from app.tools.parse import parse_user_preferences
from app.tools.validate import critic_check, estimate_cost, validate_plan
from app.tools.weather import get_weather, get_weather_series


class Agent:
    def __init__(self) -> None:
        self.osm = OSMClient()

    # -- shared core -------------------------------------------------------

    async def _run_core(self, req: PlanRequest) -> AsyncIterator[object]:
        trace: list[TraceStep] = []
        warnings: list[str] = []
        weather: Weather | None = None
        weather_by_city: list[Weather] = []
        step = 0

        def emit(tool: str, status: str, message: str, detail: dict | None = None) -> TraceStep:
            nonlocal step
            step += 1
            ts = TraceStep(step=step, tool=tool, status=status, message=message, detail=detail)
            trace.append(ts)
            return ts

        # 1. Parse preferences ------------------------------------------------
        yield emit("parseUserPreferences", "start", "Reading your preferences…")
        prefs: Preferences = parse_user_preferences(req)
        yield emit(
            "parseUserPreferences", "ok",
            f"Understood: {prefs.energy}-energy, interests={prefs.interests or ['(open)']}, "
            f"budget={prefs.budget or 'unset'}, days={prefs.days}",
            detail=prefs.model_dump(),
        )

        # 2. Clarifying questions (non-blocking) ------------------------------
        questions = detect_clarifying_questions(prefs)
        if questions:
            yield emit(
                "askClarifyingQuestions", "warn",
                f"Input is a bit vague — noting {len(questions)} question(s), "
                "planning with smart defaults anyway.",
                detail={"questions": [q.model_dump() for q in questions]},
            )

        # 3. Resolve destination into a day-allocated Route -------------------
        yield emit("resolveDestination", "start",
                   f"Working out the best route for {prefs.days} day(s) in {prefs.city}…")
        route: Route = await destination_tool.resolve_destination(
            prefs.city, prefs.days, osm=self.osm,
            named_places=prefs.named_places or None,
            style_weights=prefs.style_weights or None,
            interests=prefs.interests or None,
        )
        for note in route.notes:
            warnings.append(note)
        if route.is_country and len(route.stops) > 1:
            path = " → ".join(f"{s.city} ({s.nights}n)" for s in sorted(route.stops, key=lambda x: x.order))
            yield emit("resolveDestination", "ok",
                       f"Planned a {len(route.stops)}-city route: {path}",
                       detail={"stops": [s.model_dump() for s in route.stops],
                               "unused": [u.model_dump() for u in route.unused]})
        else:
            s0 = route.stops[0] if route.stops else None
            yield emit("resolveDestination", "ok",
                       f"Single-city trip: {prefs.days} day(s) in {s0.city if s0 else prefs.city}.",
                       detail={"stops": [s.model_dump() for s in route.stops]})

        if not route.stops:
            # Nothing resolvable at all — honest fallback, not a fake plan.
            yield emit("resolveDestination", "error",
                       "Couldn't resolve this destination to any real place.")
            resp = PlanResponse(
                plan=_empty_plan(prefs, "We couldn't resolve this destination. "
                                 "Try a more specific city or country name."),
                preferences=prefs, trace=trace, clarifying_questions=questions,
                warnings=warnings + ["Destination could not be resolved."],
                weather=weather,
            )
            yield resp
            return

        # 4. Per-city enrichment: weather + real places, in parallel ----------
        # On multi-city trips we compose the itinerary with AI (real named places
        # per city), so we SKIP the slow per-city Overpass place-fetch here — it
        # was pure overhead (several cities × heavy queries) and its data went
        # unused. We still fetch weather (cheap, Open-Meteo) for every city.
        multi_city_trip = len(route.stops) > 1
        yield emit("enrichCities", "start",
                   (f"Fetching weather for {len(route.stops)} cities…" if multi_city_trip else
                    "Fetching weather and real places…"))

        async def enrich(stop: RouteStop):
            coords = (stop.lat, stop.lon) if stop.lat is not None and stop.lon is not None else None
            wx = await get_weather(stop.city, coords)
            if multi_city_trip:
                # Skip Overpass; AI will name the places. Mark as fallback so the
                # prefer-AI path kicks in (it already does for multi-city).
                return stop, wx, [], [], True, True
            acts, foods, act_fb, food_fb = await get_options_for_stop(prefs, stop, self.osm)
            return stop, wx, acts, foods, act_fb, food_fb

        enriched = await asyncio.gather(*[enrich(s) for s in route.stops])

        places_by_city: dict[str, dict] = {}
        any_live = False
        all_fallback = True
        for stop, wx, acts, foods, act_fb, food_fb in enriched:
            if wx:
                wx.city = stop.city
                weather_by_city.append(wx)
                if wx.advisory:
                    warnings.append(f"{stop.city}: {wx.advisory}")
            places_by_city[stop.city] = {"activities": acts, "foods": foods,
                                         "act_fb": act_fb, "food_fb": food_fb}
            live = (not act_fb) or (not food_fb)
            any_live = any_live or live
            all_fallback = all_fallback and (act_fb and food_fb)
            status = "ok" if live else "warn"
            yield emit("enrichCities", status,
                       f"{stop.city}: {len(acts)} activities, {len(foods)} food "
                       f"({'live' if live else 'fallback'})"
                       + (f" · {wx.emoji}{round(wx.temp_max_c)}°C" if wx and wx.temp_max_c is not None else ""),
                       detail={"city": stop.city,
                               "sample": [p.name for p in (acts[:3] + foods[:2])]})

        # Weather shown at the top = first stop (keeps your existing single field).
        weather = weather_by_city[0] if weather_by_city else None

        # 5. Build the itinerary — day-by-day across the WHOLE route ----------
        # Prefer a real, AI-composed itinerary (named, well-known places per city)
        # when an LLM key exists AND either no city got live data, OR it's a
        # multi-city trip — OSM/Foursquare coverage for foreign cities is patchy,
        # so the curated builder would otherwise emit generic "a local café"
        # stubs. The AI path names real places (Wat Pho, Thip Samai, …).
        multi_city = len(route.stops) > 1
        prefer_ai = llm_plan.is_enabled() and (all_fallback or multi_city)
        plan: Optional[Plan] = None
        if prefer_ai:
            yield emit("generateWithAI", "start",
                       ("Composing a multi-city itinerary with real, named places per city, "
                        "then verifying each…") if multi_city else
                       ("Live place data was thin — composing the full itinerary with AI, "
                        "then verifying each place…"))
            ai_plan = await llm_plan.generate_llm_plan_for_route(prefs, route)
            if ai_plan and ai_plan.items:
                warnings = [w for w in warnings if "fallback" not in w.lower()]
                plan = ai_plan
                yield emit("generateWithAI", "ok",
                           f"Composed {len(ai_plan.items)} stops across {route.total_days} days.")
            else:
                yield emit("generateWithAI", "warn",
                           "AI itinerary unavailable — assembling from available data.")

        if plan is None:
            yield emit("buildItinerary", "start", "Assembling your day-by-day plan…")
            plan = build_itinerary_from_route(route, places_by_city, prefs)
            yield emit("buildItinerary", "ok",
                       f"Built a {len(plan.items)}-stop plan across {route.total_days} day(s).")

        # Attach route + reasoning so the UI can show "why this route".
        plan.route = route
        plan.route_reasoning = route.reasoning
        plan.days = route.total_days
        plan.hotels = _dedup_hotels(getattr(plan, "hotels", []))

        # 5b. Highlights agent: what each city is FAMOUS FOR (activities +
        #     signature food), grounded in real web results. Day-wise/location
        #     aware — the UI shows the highlights for the city you're in each day.
        if highlights_tool.is_enabled():
            cities = [s.city for s in sorted(route.stops, key=lambda x: x.order)]
            yield emit("discoverHighlights", "start",
                       f"Finding what {'these places are' if len(cities) > 1 else cities[0]+' is'} "
                       f"famous for…")
            try:
                plan.highlights = await highlights_tool.discover_highlights(
                    cities, interests=prefs.interests or None
                )
            except Exception as exc:  # noqa: BLE001
                plan.highlights = []
                yield emit("discoverHighlights", "warn", f"Couldn't fetch highlights: {exc}")
            if plan.highlights:
                total_acts = sum(len(h.famous_for) for h in plan.highlights)
                total_food = sum(len(h.signature_foods) for h in plan.highlights)
                yield emit("discoverHighlights", "ok",
                           f"{total_acts} famous experiences and {total_food} signature "
                           f"dishes across {len(plan.highlights)} "
                           f"{'city' if len(plan.highlights)==1 else 'cities'}.",
                           detail={"cities": [h.city for h in plan.highlights]})
            else:
                yield emit("discoverHighlights", "warn",
                           "No highlights found for these places.")

        # 5c. Per-city stays: make sure EVERY city you sleep in has places to
        #     stay (the AI itinerary gives per-city hotels; live/curated plans
        #     don't, and multi-city trips need one set per city).
        ordered_cities = [s.city for s in sorted(route.stops, key=lambda x: x.order)]
        # Hotels with no city (single-city plans) belong to the only stop.
        if plan.hotels and not any(h.city for h in plan.hotels) and len(ordered_cities) == 1:
            for h in plan.hotels:
                h.city = ordered_cities[0]
        have_cities = {(h.city or "").strip().lower() for h in plan.hotels if h.city}
        missing_cities = [c for c in ordered_cities if c.strip().lower() not in have_cities]
        if missing_cities and llm_plan.is_enabled():
            yield emit("recommendStays", "start",
                       f"Finding places to stay in {', '.join(missing_cities)}…")
            try:
                extra = await llm_plan.recommend_stays(missing_cities, prefs)
            except Exception as exc:  # noqa: BLE001
                extra = []
                yield emit("recommendStays", "warn", f"Couldn't fetch stays: {exc}")
            if extra:
                plan.hotels.extend(extra)
                plan.hotels = _dedup_hotels(plan.hotels)
                covered = len({(h.city or "").lower() for h in plan.hotels if h.city})
                yield emit("recommendStays", "ok",
                           f"{len(plan.hotels)} stays across {covered} "
                           f"{'city' if covered == 1 else 'cities'}.")
            else:
                yield emit("recommendStays", "warn", "No stays found for these cities.")

        # 5d. Photos — attach a REAL, matching image to every hotel and stop, in
        #     one pass now that all hotels (incl. per-city stays) and stops exist.
        #     This is what stops hotels showing random/stock images.
        from app.tools import image_search
        yield emit("attachPhotos", "start", "Finding real photos for stays and stops…")
        try:
            n_photos = await image_search.attach_images(plan)
            yield emit("attachPhotos", "ok", f"Matched {n_photos} real photos to hotels & stops.")
        except Exception as exc:  # noqa: BLE001
            yield emit("attachPhotos", "warn", f"Couldn't fetch some photos: {exc}")

        # 6. Cost + Critic + validate ----------------------------------------
        yield emit("estimateCost", "start", "Estimating total cost & time…")
        plan = estimate_cost(plan)
        yield emit("estimateCost", "ok",
                   f"~₹{plan.total_cost} and ~{plan.total_hours}h in-plan spend.",
                   detail={"total_cost": plan.total_cost, "total_hours": plan.total_hours})

        # Critic: enforce day-count authority + grounding before we show anything.
        yield emit("criticCheck", "start", "Checking the plan is complete and realistic…")
        issues = critic_check(plan, prefs, route)
        repairs = [i for i in issues if i.severity == "repair"]
        blocks = [i for i in issues if i.severity == "block"]

        if repairs:
            # Try ONE targeted repair pass: fill empty/missing days.
            yield emit("criticCheck", "repair",
                       "Caught " + "; ".join(i.message for i in repairs[:2])
                       + (" …" if len(repairs) > 2 else ""),
                       detail={"issues": [i.__dict__ for i in repairs]})
            plan = _repair_missing_days(plan, route, places_by_city, prefs)
            plan = estimate_cost(plan)
            issues = critic_check(plan, prefs, route)
            repairs_after = [i for i in issues if i.severity == "repair"]
            if repairs_after:
                # Couldn't fully fill — degrade honestly (don't fabricate).
                for i in repairs_after:
                    if i.code in ("day_count_short", "empty_days"):
                        warnings.append(
                            "Some days are lighter than planned — we show honest "
                            "'explore freely' blocks rather than invented stops.")
                        break
                yield emit("criticCheck", "warn",
                           "Some days remain light; showing honest flex blocks.")
            else:
                yield emit("criticCheck", "ok", "Repaired — all days now covered.")
        elif blocks:
            yield emit("criticCheck", "warn",
                       "; ".join(i.message for i in blocks[:2]))
            warnings += [i.message for i in blocks]
        else:
            yield emit("criticCheck", "ok", "Plan is complete and grounded.")

        # User-facing constraint warnings (budget/time/diet/crowds).
        yield emit("validatePlan", "start", "Checking against your constraints…")
        plan_warnings = validate_plan(plan, prefs)
        warnings.extend(plan_warnings)
        # Surface grounding/geo warnings from the critic too.
        warnings.extend(i.message for i in issues if i.severity == "warn")
        yield emit("validatePlan", "warn" if (plan_warnings) else "ok",
                   (f"{len(plan_warnings)} note(s): " + " ".join(plan_warnings))
                   if plan_warnings else "All constraints satisfied.")

        # 7. Travel times between stops (optional) ----------------------------
        if routing.is_enabled() and len(plan.items) >= 2:
            yield emit("routeStops", "start", "Working out travel between stops…")
            plan = await routing.add_travel_legs(plan)
            if plan.legs:
                total_min = round(sum(leg.duration_min for leg in plan.legs))
                yield emit("routeStops", "ok",
                           f"~{total_min} min total travel across {len(plan.legs)} hop(s).",
                           detail={"legs": [leg.model_dump() for leg in plan.legs]})
            else:
                yield emit("routeStops", "warn", "Couldn't compute travel times for these stops.")

        # 8. Day-by-day weather for the city you're in each day (needs a date) --
        weather_by_day: list[Weather] = []
        if prefs.start_date:
            yield emit("dayWeather", "start",
                       f"Fetching day-by-day weather from {prefs.start_date}…")
            weather_by_day = await _per_day_weather(route, prefs.start_date, plan.days or route.total_days)
            got = sum(1 for w in weather_by_day if w.temp_max_c is not None)
            if got:
                yield emit("dayWeather", "ok",
                           f"Got real forecasts for {got} of {len(weather_by_day)} day(s).")
            else:
                yield emit("dayWeather", "warn",
                           "Dates are beyond the 16-day forecast window — showing seasonal guidance instead.")

        resp = PlanResponse(
            plan=plan, preferences=prefs, trace=trace,
            clarifying_questions=questions, warnings=_dedup_str(warnings),
            weather=weather, weather_by_city=weather_by_city,
            weather_by_day=weather_by_day,
        )
        yield resp

    # -- public API --------------------------------------------------------

    async def run(self, req: PlanRequest) -> PlanResponse:
        result: PlanResponse | None = None
        async for event in self._run_core(req):
            if isinstance(event, PlanResponse):
                result = event
        assert result is not None
        return result

    async def run_streaming(self, req: PlanRequest) -> AsyncIterator[object]:
        async for event in self._run_core(req):
            yield event
            if isinstance(event, TraceStep):
                await asyncio.sleep(0.2)


# ===========================================================================
# Repair + helpers
# ===========================================================================
def _repair_missing_days(plan: Plan, route: Route, places_by_city: dict,
                         prefs: Preferences) -> Plan:
    """Fill any day in 1..total_days that has no stops. Uses leftover real
    places for that day's city first; if none remain, inserts an honest
    'explore freely' flex block (never a fabricated named place)."""
    present = {i.day for i in plan.items}
    # Track which places are already used so we don't duplicate.
    used_names = {i.title.lower() for i in plan.items}
    next_order = (max((i.order for i in plan.items), default=0))

    for day in range(1, route.total_days + 1):
        if day in present and any(i.day == day for i in plan.items):
            continue
        stop = route.city_for_day(day)
        city = stop.city if stop else plan.city
        pool = places_by_city.get(city, {})
        candidates = [p for p in (pool.get("activities", []) + pool.get("foods", []))
                      if p.name.lower() not in used_names]
        next_order += 1
        if candidates:
            p = candidates[0]
            used_names.add(p.name.lower())
            plan.items.append(PlanItem(
                order=next_order, time_slot=f"Day {day} · Afternoon",
                title=p.name, place=p, duration_hours=1.5, est_cost=p.est_cost,
                why_it_fits=f"A real spot in {city} to round out the day.",
                day=day, city=city,
            ))
        else:
            # Honest flex block — grounded as "the city itself", with its coords.
            flex = Place(name=f"Explore {city} at your own pace",
                         category="walk", kind="activity",
                         lat=stop.lat if stop else None, lon=stop.lon if stop else None,
                         est_cost=0, source="curated", city=city)
            plan.items.append(PlanItem(
                order=next_order, time_slot=f"Day {day} · Flexible",
                title=flex.name, place=flex, duration_hours=2.0, est_cost=0,
                why_it_fits=(f"We couldn't verify enough spots for this day, so "
                             f"keep it open — wander {city}, revisit a favourite, "
                             f"or ask locals."),
                tradeoff="This is a flex day, not a verified stop.",
                day=day, city=city,
            ))
    plan.items.sort(key=lambda i: (i.day, i.order))
    # Renumber order cleanly.
    for n, i in enumerate(plan.items, 1):
        i.order = n
    return plan


async def _per_day_weather(route: Route, start_date: str, total_days: int) -> list[Weather]:
    """For each trip day, the weather for the city you're in that day, on that
    day's real date. One Open-Meteo range call per city. Days beyond the forecast
    horizon come back as a placeholder (no temps) so the UI can say 'seasonal'."""
    from datetime import datetime, timedelta

    try:
        base = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return []

    def day_date(day: int) -> str:
        return (base + timedelta(days=day - 1)).isoformat()

    # One series fetch per city, over the city's visited date range.
    series_by_city: dict[str, dict] = {}
    for stop in route.stops:
        sd = day_date(stop.day_start)
        ed = day_date(stop.day_end)
        coords = (stop.lat, stop.lon) if stop.lat is not None and stop.lon is not None else None
        series_by_city[stop.city] = await get_weather_series(stop.city, coords, sd, ed)

    out: list[Weather] = []
    for day in range(1, total_days + 1):
        stop = route.city_for_day(day)
        city = stop.city if stop else route.destination
        d = day_date(day)
        series = series_by_city.get(city, {})
        w = series.get(d)
        if w:
            w.day = day
            w.city = city
            out.append(w)
        else:
            out.append(Weather(date=d, description="", emoji="📅", city=city, day=day,
                               advisory=None))
    return out


def _empty_plan(prefs: Preferences, message: str) -> Plan:
    plan = Plan(city=prefs.city, days=prefs.days, is_fallback=True)
    plan.summary = message
    return plan


def _dedup_str(xs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _dedup_hotels(hotels: list):
    seen: set[str] = set()
    out = []
    for h in hotels:
        key = (h.name or "").lower()
        if key and key not in seen:
            seen.add(key)
            out.append(h)
    return out