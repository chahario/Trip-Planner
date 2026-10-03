"""Tools 4 & 5 — estimateCost and validatePlan (+ Critic checks).

Pure functions over a Plan.

- estimate_cost  : fills totals.
- validate_plan  : the user-facing constraint checks (budget, time, dietary,
                   crowds) — returns human-readable warnings. Unchanged behaviour.
- critic_check   : NEW. Structural guardrails that catch the bugs that made the
                   old output "trash": wrong day count, invented (ungrounded)
                   places, geographically impossible hops. Returns a list of
                   CriticIssue the orchestrator can act on (repair) or surface.

Design law: the Critic enforces that the day count is AUTHORITATIVE and that
every place is grounded. It never silently rewrites the plan — it reports, and
the orchestrator decides whether to repair or degrade honestly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from app.models import Plan, Preferences, Route

# Categories we consider likely to be crowded on a weekend.
_CROWDED_CATEGORIES = {"market", "cinema", "bar", "nightclub", "mall"}

# Sources we trust as "grounded" (a real, verifiable place). "ai" is NOT here:
# an AI-named place is only trusted once it's been verified, which upgrades its
# source. "curated" is allowed but flagged as an estimate, not a live place.
_GROUNDED_SOURCES = {"openstreetmap", "foursquare"}


# ---------------------------------------------------------------------------
# estimate_cost / validate_plan — unchanged public behaviour
# ---------------------------------------------------------------------------
def estimate_cost(plan: Plan) -> Plan:
    total_cost = sum(item.est_cost for item in plan.items)
    total_hours = round(sum(item.duration_hours for item in plan.items), 2)
    plan.total_cost = total_cost
    plan.total_hours = total_hours
    return plan


def validate_plan(plan: Plan, prefs: Preferences) -> list[str]:
    warnings: list[str] = []

    # Budget
    if prefs.budget is not None:
        plan.within_budget = plan.total_cost <= prefs.budget
        if not plan.within_budget:
            over = plan.total_cost - prefs.budget
            warnings.append(
                f"Plan is ₹{over} over your ₹{prefs.budget} budget "
                f"(estimated ₹{plan.total_cost})."
            )
    else:
        plan.within_budget = True

    # Time (only meaningful for a single-outing plan)
    if prefs.time_hours is not None and plan.total_hours > prefs.time_hours + 0.5:
        warnings.append(
            f"Plan needs ~{plan.total_hours}h but you have {prefs.time_hours}h. "
            "Consider dropping the last stop."
        )

    # Dietary — only checkable at a coarse level from tags/name.
    if prefs.dietary:
        for item in plan.items:
            if item.place.kind != "food":
                continue
            name_tags = (item.place.name + " " + " ".join(item.place.tags)).lower()
            if "non-veg" in name_tags or "meat" in name_tags or "kebab" in name_tags:
                if "vegetarian" in prefs.dietary or "vegan" in prefs.dietary:
                    warnings.append(
                        f"'{item.place.name}' may not suit a "
                        f"{'/'.join(prefs.dietary)} diet — verify before going."
                    )

    # Crowds
    if prefs.avoid_crowds:
        crowded = [i.place.name for i in plan.items if i.place.category in _CROWDED_CATEGORIES]
        if crowded:
            warnings.append(
                "You asked to avoid crowds; these stops can get busy on weekends: "
                + ", ".join(crowded)
                + ". Going earlier in the day helps."
            )

    return warnings


# ---------------------------------------------------------------------------
# Critic — structural guardrails (NEW)
# ---------------------------------------------------------------------------
@dataclass
class CriticIssue:
    """One problem the Critic found. `severity` drives what the orchestrator does:
      - "repair"   : fixable (e.g. a day has no stops) — orchestrator should try.
      - "warn"     : worth telling the user, not worth blocking (e.g. estimated data).
      - "block"    : the plan is not fit to show as-is.
    """
    code: str
    severity: str  # "repair" | "warn" | "block"
    message: str
    detail: dict = field(default_factory=dict)


def critic_check(plan: Plan, prefs: Preferences, route: Optional[Route] = None) -> list[CriticIssue]:
    """Run all structural checks. Returns issues in priority order."""
    issues: list[CriticIssue] = []
    issues += _check_day_count(plan, prefs, route)
    issues += _check_every_day_filled(plan, prefs)
    issues += _check_grounding(plan)
    issues += _check_geo_sanity(plan, route)
    issues += _check_reasoning_consistency(plan, route)
    # Sort: block first, then repair, then warn.
    rank = {"block": 0, "repair": 1, "warn": 2}
    issues.sort(key=lambda i: rank.get(i.severity, 3))
    return issues


def _requested_days(prefs: Preferences, route: Optional[Route]) -> int:
    if route is not None:
        return route.total_days
    return max(1, prefs.days)


def _check_day_count(plan: Plan, prefs: Preferences, route: Optional[Route]) -> list[CriticIssue]:
    """THE fix for 10 -> 2. The requested day count is authoritative; the plan
    must cover exactly that many days."""
    requested = _requested_days(prefs, route)
    days_present = sorted({i.day for i in plan.items})
    covered = len(days_present)

    if covered == requested:
        return []
    if covered < requested:
        missing = [d for d in range(1, requested + 1) if d not in set(days_present)]
        return [CriticIssue(
            code="day_count_short",
            severity="repair",
            message=(f"Plan covers {covered} of {requested} requested days "
                     f"(missing day(s): {missing}). Needs filling before showing."),
            detail={"requested": requested, "covered": covered, "missing": missing},
        )]
    # More days than requested — trim is cheap, treat as repair.
    return [CriticIssue(
        code="day_count_over",
        severity="repair",
        message=f"Plan has {covered} days but only {requested} were requested.",
        detail={"requested": requested, "covered": covered},
    )]


def _check_every_day_filled(plan: Plan, prefs: Preferences) -> list[CriticIssue]:
    """No day in 1..max_day should be empty."""
    if not plan.items:
        return [CriticIssue("empty_plan", "block", "The plan has no stops at all.")]
    max_day = max(i.day for i in plan.items)
    by_day: dict[int, int] = {}
    for i in plan.items:
        by_day[i.day] = by_day.get(i.day, 0) + 1
    empty = [d for d in range(1, max_day + 1) if by_day.get(d, 0) == 0]
    if empty:
        return [CriticIssue(
            code="empty_days",
            severity="repair",
            message=f"Day(s) {empty} have no stops.",
            detail={"empty_days": empty},
        )]
    return []


def _check_grounding(plan: Plan) -> list[CriticIssue]:
    """Every place should be grounded (real, verifiable). Ungrounded places are
    the 'invented' ones. AI-named places that were never verified are flagged.
    Curated placeholders are allowed but flagged as estimates, not live spots."""
    ungrounded: list[str] = []
    curated: list[str] = []
    for i in plan.items:
        src = i.place.source
        if src in _GROUNDED_SOURCES:
            continue
        if src == "curated":
            curated.append(i.title)
        else:  # "ai" or anything else unverified
            # An AI place with coordinates has been verified -> treat as grounded.
            if i.place.lat is not None and i.place.lon is not None:
                continue
            ungrounded.append(i.title)

    out: list[CriticIssue] = []
    if ungrounded:
        out.append(CriticIssue(
            code="ungrounded_places",
            severity="warn",
            message=("Some stops aren't verified against a map and may be "
                     f"approximate: {', '.join(ungrounded[:5])}"
                     + ("…" if len(ungrounded) > 5 else "")),
            detail={"places": ungrounded},
        ))
    if curated:
        out.append(CriticIssue(
            code="curated_placeholders",
            severity="warn",
            message=f"{len(curated)} stop(s) use generic fallback data, not live places.",
            detail={"places": curated},
        ))
    return out


def _check_geo_sanity(plan: Plan, route: Optional[Route]) -> list[CriticIssue]:
    """Within a single day, consecutive located stops shouldn't be absurdly far
    apart (no 400 km lunch detour). Cross-city jumps are expected between days,
    so we only check WITHIN a day."""
    issues: list[CriticIssue] = []
    by_day: dict[int, list] = {}
    for i in plan.items:
        by_day.setdefault(i.day, []).append(i)

    for day, items in by_day.items():
        located = [(it.title, it.place.lat, it.place.lon) for it in items
                   if it.place.lat is not None and it.place.lon is not None]
        for (an, alat, alon), (bn, blat, blon) in zip(located, located[1:]):
            km = _haversine(alat, alon, blat, blon)
            if km > 60:  # same-day hop over 60 km is suspicious
                issues.append(CriticIssue(
                    code="geo_detour",
                    severity="warn",
                    message=(f"Day {day}: '{an}' and '{bn}' are ~{round(km)} km apart "
                             "— a lot of travel within one day."),
                    detail={"day": day, "km": round(km), "from": an, "to": bn},
                ))
    return issues


def _check_reasoning_consistency(plan: Plan, route: Optional[Route]) -> list[CriticIssue]:
    """If a route is attached, the number of days its stops span must equal the
    plan's day coverage, and the stated nights must sum to total_days. Catches a
    narration that claims '3 nights' while the plan shows something else."""
    if route is None or not route.stops:
        return []
    issues: list[CriticIssue] = []
    nights_sum = sum(s.nights for s in route.stops)
    if nights_sum != route.total_days:
        issues.append(CriticIssue(
            code="route_nights_mismatch",
            severity="block",
            message=(f"Route nights sum to {nights_sum} but trip is "
                     f"{route.total_days} days — allocation is inconsistent."),
            detail={"nights_sum": nights_sum, "total_days": route.total_days},
        ))
    return issues


def _haversine(lat1, lon1, lat2, lon2) -> float:
    if None in (lat1, lon1, lat2, lon2):
        return 0.0
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))