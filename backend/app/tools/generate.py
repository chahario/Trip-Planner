"""Tool 6 — build the itinerary.

The core function is `build_itinerary_from_route`: it LOOPS over every day of
the route (1..total_days) and fills each day from that day's city's real places.
Because the loop is driven by the route's day count — not by how many places we
happened to fetch — the plan ALWAYS spans the requested number of days. This is
the structural fix for the old "10 days -> 2 days" collapse: when a day runs out
of fresh places, it gets an honest flex block instead of ending the trip early.

Per-item "why it fits" and trade-off reasoning are rule-based (works with zero
API keys). An optional LLM hook can warm up the overall summary.

The legacy single-outing builder (`generate_final_plan`) is kept for backward
compatibility, but the orchestrator now uses the route-based path.
"""
from __future__ import annotations

from app.config import get_settings
from app.models import Place, Plan, PlanItem, Preferences, Route, RouteStop

# Duration heuristics per category, in hours.
_DURATION = {
    "cafe": 1.0, "restaurant": 1.5, "street_food": 0.75, "bakery": 0.5,
    "park": 1.5, "garden": 1.5, "walk": 1.0, "viewpoint": 0.75,
    "museum": 1.5, "gallery": 1.0, "live_music": 2.0, "bar": 1.5,
    "cinema": 2.5, "market": 1.0,
}

_DAY_SLOTS = ["Morning", "Midday", "Afternoon", "Evening"]
_SLOTS = ["Afternoon", "Late afternoon", "Early evening", "Evening", "Night"]

# How many stops per day, by energy/pace.
def _stops_per_day(prefs: Preferences) -> int:
    pace = str((prefs.style_weights or {}).get("pace", "")).lower()
    if pace == "packed":
        return 4
    if pace == "slow":
        return 2
    if prefs.energy == "high":
        return 4
    if prefs.energy == "low":
        return 2
    return 3


def _duration(cat: str) -> float:
    return _DURATION.get(cat, 1.0)


# ===========================================================================
# ROUTE-BASED BUILDER  (the day-count-authoritative path)
# ===========================================================================
def build_itinerary_from_route(
    route: Route, places_by_city: dict, prefs: Preferences,
) -> Plan:
    """Build a plan that covers EXACTLY route.total_days days.

    places_by_city: { city_name: {"activities": [Place], "foods": [Place],
                                   "act_fb": bool, "food_fb": bool} }
    """
    plan = Plan(city=route.destination, days=route.total_days)
    per_day = _stops_per_day(prefs)

    # Per-city cursors so we don't repeat places across that city's days.
    cursors: dict[str, dict[str, int]] = {
        c: {"act": 0, "food": 0} for c in places_by_city
    }
    running_cost = 0
    order = 0

    for day in range(1, route.total_days + 1):
        stop = route.city_for_day(day)
        city = stop.city if stop else route.destination
        pool = places_by_city.get(city, {"activities": [], "foods": []})
        acts = _order_by_energy(pool.get("activities", []), prefs)
        foods = pool.get("foods", [])
        cur = cursors.setdefault(city, {"act": 0, "food": 0})

        # Which day of this city's stay is this? (for nicer slot labels)
        day_in_city = day - (stop.day_start if stop else 1) + 1

        # Build this day's pattern: alternate activity/food, activity-first.
        pattern = []
        for k in range(per_day):
            pattern.append("act" if k % 2 == 0 else "food")

        day_items: list[PlanItem] = []
        for slot_idx, slot in enumerate(pattern):
            place = _take(acts, foods, cur, slot)
            if place is None:
                break
            dur = _duration(place.category)
            running_cost += place.est_cost
            order += 1
            day_items.append(PlanItem(
                order=order,
                time_slot=f"Day {day} · {_DAY_SLOTS[min(slot_idx, len(_DAY_SLOTS)-1)]}",
                title=place.name, place=place, duration_hours=dur,
                est_cost=place.est_cost,
                why_it_fits=_why(place, prefs, city),
                tradeoff=_tradeoff(place, prefs, running_cost),
                day=day, city=city,
            ))

        # If this city has no places at all for this day, honest flex block.
        if not day_items:
            order += 1
            flex = Place(name=f"Explore {city} at your own pace",
                         category="walk", kind="activity",
                         lat=stop.lat if stop else None,
                         lon=stop.lon if stop else None,
                         est_cost=0, source="curated", city=city)
            day_items.append(PlanItem(
                order=order, time_slot=f"Day {day} · Flexible",
                title=flex.name, place=flex, duration_hours=2.0, est_cost=0,
                why_it_fits=(f"Keep this day open in {city} — wander, revisit a "
                             f"favourite, or follow a local tip."),
                tradeoff="A flex day, not a verified stop.",
                day=day, city=city,
            ))

        plan.items.extend(day_items)

    plan.summary = _build_route_summary(route, plan, prefs)
    plan.route = route
    plan.route_reasoning = route.reasoning
    return plan


def _take(acts: list[Place], foods: list[Place], cur: dict, slot: str) -> Place | None:
    """Pull the next unused place for the requested slot; borrow from the other
    pool if the preferred one is exhausted."""
    if slot == "act":
        if cur["act"] < len(acts):
            p = acts[cur["act"]]; cur["act"] += 1; return p
        if cur["food"] < len(foods):
            p = foods[cur["food"]]; cur["food"] += 1; return p
    else:
        if cur["food"] < len(foods):
            p = foods[cur["food"]]; cur["food"] += 1; return p
        if cur["act"] < len(acts):
            p = acts[cur["act"]]; cur["act"] += 1; return p
    return None


def _build_route_summary(route: Route, plan: Plan, prefs: Preferences) -> str:
    n = len(plan.items)
    if len(route.stops) > 1:
        cities = ", ".join(s.city for s in sorted(route.stops, key=lambda x: x.order))
        base = (f"A {route.total_days}-day trip through {cities} with {n} stops, "
                f"tuned to your {prefs.energy}-energy pace.")
    else:
        base = (f"A {route.total_days}-day plan for {route.stops[0].city if route.stops else route.destination} "
                f"with {n} stops, tuned to your {prefs.energy}-energy pace.")
    if prefs.mood:
        base += f" You said you're feeling {prefs.mood}."
    return _maybe_llm_rephrase(base, plan, prefs)


# ===========================================================================
# Reasoning helpers (rule-based)
# ===========================================================================
def _why(place: Place, prefs: Preferences, city: str | None = None) -> str:
    reasons: list[str] = []
    interest_map = {
        "park": "walks", "garden": "walks", "walk": "walks", "viewpoint": "walks",
        "cafe": "food", "restaurant": "food", "street_food": "food",
        "live_music": "music", "bar": "nightlife", "museum": "art", "gallery": "art",
    }
    matched = interest_map.get(place.category)
    if matched and matched in prefs.interests:
        reasons.append(f"matches your interest in {matched}")
    if prefs.energy == "low" and place.category in {"cafe", "park", "garden", "walk", "viewpoint"}:
        reasons.append("low-key, good when you're tired")
    if prefs.energy == "high" and place.category in {"live_music", "bar", "market"}:
        reasons.append("lively, matches your energy")
    if place.est_cost == 0:
        reasons.append("free")
    elif prefs.budget is not None and place.est_cost <= prefs.budget * 0.3:
        reasons.append("easy on the budget")
    if prefs.avoid_crowds and place.category in {"park", "garden", "cafe", "gallery"}:
        reasons.append("usually calm")
    if place.source in {"openstreetmap", "foursquare"} and place.address:
        reasons.append(f"real spot near {place.address.split(',')[0]}")
    elif place.source in {"openstreetmap", "foursquare"}:
        reasons.append("a verified local spot")
    if not reasons:
        reasons.append("a solid, well-rounded choice for the day")
    return _cap(", ".join(reasons))


def _tradeoff(place: Place, prefs: Preferences, running_cost: int) -> str | None:
    if prefs.budget is not None and running_cost > prefs.budget:
        return (f"Slightly pushes you over budget, but it's the best "
                f"{place.category} fit for your mood — swap it for a free walk "
                "to stay under.")
    if prefs.avoid_crowds and place.category in {"market", "live_music", "bar"}:
        return "Can get busy in the evenings; going earlier keeps it calmer."
    if prefs.energy == "low" and place.category in {"live_music", "bar", "market"}:
        return "A bit high-energy for a tired day — keep it short or skip if you fade."
    return None


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _order_by_energy(activities: list[Place], prefs: Preferences) -> list[Place]:
    calm = {"park", "garden", "walk", "viewpoint", "cafe", "gallery", "museum"}
    if prefs.energy == "high":
        return sorted(activities, key=lambda p: p.category in calm)
    return sorted(activities, key=lambda p: p.category not in calm)


# ===========================================================================
# LEGACY single-outing builder (kept for backward compatibility)
# ===========================================================================
def generate_final_plan(activities: list[Place], foods: list[Place],
                        prefs: Preferences) -> Plan:
    """Original single-/multi-day builder based on flat place lists. Retained so
    any older call sites keep working; the orchestrator uses the route path."""
    days = max(1, prefs.days)
    plan = Plan(city=prefs.city, days=days)
    if days == 1:
        _build_single_day(plan, activities, foods, prefs)
    else:
        _build_multi_day(plan, activities, foods, prefs, days)
    plan.summary = _build_summary(plan, prefs)
    return plan


def _build_single_day(plan: Plan, activities, foods, prefs) -> None:
    time_budget = prefs.time_hours or 4.0
    ordered_acts = _order_by_energy(activities, prefs)
    picks: list[Place] = []
    if ordered_acts:
        picks.append(ordered_acts[0])
    if foods:
        picks.append(foods[0])
    if len(ordered_acts) > 1:
        picks.append(ordered_acts[1])
    if len(foods) > 1 and time_budget >= 5:
        picks.append(foods[1])

    running_cost = 0
    running_time = 0.0
    order = 0
    for place in picks:
        dur = _duration(place.category)
        if running_time + dur > time_budget + 0.5 and order >= 2:
            break
        running_cost += place.est_cost
        running_time += dur
        plan.items.append(PlanItem(
            order=order + 1, time_slot=_SLOTS[min(order, len(_SLOTS) - 1)],
            title=place.name, place=place, duration_hours=dur,
            est_cost=place.est_cost, why_it_fits=_why(place, prefs),
            tradeoff=_tradeoff(place, prefs, running_cost), day=1,
        ))
        order += 1


def _build_multi_day(plan: Plan, activities, foods, prefs, days: int) -> None:
    acts = _order_by_energy(activities, prefs)
    ai = fi = 0
    order = 0
    running_cost = 0
    per_day_pattern = ["act", "food", "act", "food"]
    for d in range(1, days + 1):
        slot_idx = 0
        day_has = 0
        for slot in per_day_pattern:
            place: Place | None = None
            if slot == "act" and ai < len(acts):
                place = acts[ai]; ai += 1
            elif slot == "food" and fi < len(foods):
                place = foods[fi]; fi += 1
            if place is None:
                if ai < len(acts):
                    place = acts[ai]; ai += 1
                elif fi < len(foods):
                    place = foods[fi]; fi += 1
            if place is None:
                break
            dur = _duration(place.category)
            running_cost += place.est_cost
            order += 1
            plan.items.append(PlanItem(
                order=order,
                time_slot=f"Day {d} · {_DAY_SLOTS[min(slot_idx, len(_DAY_SLOTS) - 1)]}",
                title=place.name, place=place, duration_hours=dur,
                est_cost=place.est_cost, why_it_fits=_why(place, prefs),
                tradeoff=_tradeoff(place, prefs, running_cost), day=d,
            ))
            slot_idx += 1
            day_has += 1
        if day_has == 0:
            break


def _build_summary(plan: Plan, prefs: Preferences) -> str:
    if not plan.items:
        return "Couldn't assemble a plan from the available options."
    mood = f" You said you're feeling {prefs.mood}." if prefs.mood else ""
    n = len(plan.items)
    pace = "a relaxed pace" if prefs.energy == "low" else "a lively pace"
    if plan.days > 1:
        base = (f"A {plan.days}-day trip to {prefs.city} with {n} stops across the days, "
                f"tuned to your {prefs.energy}-energy mood and mixing {pace} with your "
                f"interests.{mood}")
    else:
        base = (f"A {n}-stop day in {prefs.city} tuned to your {prefs.energy}-energy mood, "
                f"mixing {pace} with your interests.{mood}")
    return _maybe_llm_rephrase(base, plan, prefs)


def _maybe_llm_rephrase(text: str, plan: Plan, prefs: Preferences) -> str:
    """Optional: if an LLM key is set, warm up the summary. Any failure returns
    the deterministic text unchanged, so the app never depends on it."""
    s = get_settings()
    if not s.llm_api_key:
        return text
    provider = s.llm_provider if s.llm_provider != "none" else "openai"
    try:  # pragma: no cover
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=s.llm_api_key)
            stops = ", ".join(i.title for i in plan.items[:12])
            resp = client.chat.completions.create(
                model=s.llm_model or "gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Rewrite the plan summary in 2 warm, concise sentences. No emojis."},
                    {"role": "user", "content": f"{text}\nStops: {stops}"},
                ],
                max_tokens=120,
            )
            return resp.choices[0].message.content.strip() or text
    except Exception:  # noqa: BLE001
        return text
    return text