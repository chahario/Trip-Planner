"""Tool 1 — parseUserPreferences.

Turns raw request fields (structured and/or free text) into a normalised
Preferences object. Rule-based and dependency-free: fast, deterministic, and
unit-testable.

NEW: also derives
  - style_weights: {"beach","culture","food": float, "pace": str, "tier": str}
    used by the DestinationResolver to weight which cities make the route.
  - named_places: explicit cities the user said they want (skip discovery).
Both come from the structured request first, then free text as a fallback.
"""
from __future__ import annotations

import re

from app.models import PlanRequest, Preferences

_TIME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(hour|hr|h)", re.IGNORECASE)
_BUDGET_RE = re.compile(r"(?:₹|rs\.?|inr)?\s*(\d{2,6})", re.IGNORECASE)
_DAYS_RE = re.compile(r"(\d+)\s*(day|days)", re.IGNORECASE)

_LOW_ENERGY = {"tired", "exhausted", "lazy", "chill", "relax", "low-key", "low key", "calm", "sleepy"}
_HIGH_ENERGY = {"energetic", "adventurous", "excited", "hyped", "active", "party"}

_INTEREST_KEYWORDS = {
    "food": ["food", "eat", "restaurant", "dinner", "lunch", "brunch", "foodie"],
    "coffee": ["coffee", "cafe", "café"],
    "music": ["music", "gig", "concert", "live music", "jazz", "band"],
    "walks": ["walk", "walking", "stroll", "hike"],
    "nature": ["nature", "park", "garden", "outdoors", "green"],
    "art": ["art", "gallery", "museum", "exhibition"],
    "shopping": ["shopping", "shop", "market", "mall"],
    "nightlife": ["nightlife", "bar", "pub", "club", "drinks"],
    "movies": ["movie", "cinema", "film"],
    "books": ["book", "bookstore", "reading"],
}

_DIETARY = {
    "vegetarian": ["vegetarian", "veg "],
    "vegan": ["vegan"],
    "jain": ["jain"],
    "halal": ["halal"],
    "gluten-free": ["gluten"],
}

_CROWD_HINTS = ["avoid crowd", "no crowd", "quiet", "not crowded", "less crowded", "peaceful"]

# --- Style signals for the resolver ---
_BEACH_HINTS = ["beach", "island", "islands", "sea", "coast", "snorkel", "dive",
                "diving", "surf", "sand", "sun"]
_CULTURE_HINTS = ["culture", "cultural", "temple", "temples", "museum", "history",
                  "historic", "heritage", "ruins", "art", "architecture", "sightseeing"]
_FOOD_PRIORITY_HINTS = ["foodie", "street food", "cuisine", "local food", "food tour",
                        "culinary", "eat my way", "best food"]
_PACE_SLOW = ["slow", "relax", "relaxed", "leisure", "leisurely", "laid back", "laid-back",
              "take it easy", "chill", "fewer places", "deep"]
_PACE_PACKED = ["packed", "see as much", "fast", "cover a lot", "lots of places",
                "maximize", "as much as possible", "busy"]
_TIER_LUX = ["luxury", "premium", "5 star", "five star", "high end", "high-end"]
_TIER_BUDGET = ["budget", "cheap", "backpack", "backpacking", "shoestring", "affordable"]


def _extract_hours(text: str | None) -> float | None:
    if not text:
        return None
    m = _TIME_RE.search(text)
    if m:
        return float(m.group(1))
    if _DAYS_RE.search(text):
        return None
    m2 = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
    return float(m2.group(1)) if m2 else None


def _extract_budget(text: str | None) -> int | None:
    if not text:
        return None
    m = _BUDGET_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_days(text: str | None) -> int | None:
    if not text:
        return None
    m = _DAYS_RE.search(text)
    if not m:
        return None
    return max(1, min(21, int(m.group(1))))


def _detect(haystack: str, table: dict[str, list[str]]) -> list[str]:
    found = []
    for label, kws in table.items():
        if any(kw in haystack for kw in kws):
            found.append(label)
    return found


def _count_hits(haystack: str, words: list[str]) -> int:
    return sum(1 for w in words if w in haystack)


def _derive_style_weights(req: PlanRequest, blob: str, interests: list[str]) -> dict:
    """Build the resolver's scoring weights. If the request already carries
    style_weights (from the clarifying step), those win; free text fills gaps."""
    sw: dict = dict(req.style_weights or {})

    def set_default(key, value):
        if key not in sw or sw[key] in (None, ""):
            sw[key] = value

    # Beach / culture / food leanings (1.0 = neutral; higher = wanted more).
    beach = 1.0 + 0.6 * _count_hits(blob, _BEACH_HINTS)
    culture = 1.0 + 0.6 * _count_hits(blob, _CULTURE_HINTS)
    food = 1.0 + 0.6 * _count_hits(blob, _FOOD_PRIORITY_HINTS)
    if "nature" in interests or "walks" in interests:
        culture += 0.3
    if "food" in interests or "coffee" in interests:
        food += 0.3
    if "nightlife" in interests:
        beach += 0.2
    set_default("beach", round(beach, 2))
    set_default("culture", round(culture, 2))
    set_default("food", round(food, 2))

    # Pace.
    if "pace" not in sw or not sw.get("pace"):
        if _count_hits(blob, _PACE_SLOW) > _count_hits(blob, _PACE_PACKED):
            sw["pace"] = "slow"
        elif _count_hits(blob, _PACE_PACKED) > 0:
            sw["pace"] = "packed"
        else:
            sw["pace"] = "balanced"

    # Tier (used for hotel/cost estimates).
    if "tier" not in sw or not sw.get("tier"):
        if _count_hits(blob, _TIER_LUX):
            sw["tier"] = "luxury"
        elif _count_hits(blob, _TIER_BUDGET):
            sw["tier"] = "budget"
        else:
            sw["tier"] = "mid"

    return sw


def parse_user_preferences(req: PlanRequest) -> Preferences:
    blob = " ".join(
        filter(None, [
            req.free_text or "",
            req.mood or "",
            req.clarifications or "",
            " ".join(req.interests),
            " ".join(req.constraints),
        ])
    ).lower()

    # Energy from mood text.
    energy = "medium"
    if any(w in blob for w in _LOW_ENERGY):
        energy = "low"
    if any(w in blob for w in _HIGH_ENERGY):
        energy = "high"

    # Interests.
    interests = [i.lower().strip() for i in req.interests if i.strip()]
    for extra in _detect(blob, _INTEREST_KEYWORDS):
        if extra not in interests:
            interests.append(extra)

    # Dietary + crowd constraints.
    dietary = _detect(blob, _DIETARY)
    avoid_crowds = any(h in blob for h in _CROWD_HINTS)
    other = [
        c for c in req.constraints
        if c.lower() not in {d for d in dietary} and "crowd" not in c.lower()
    ]

    # Days: explicit field wins; else detect from free text / available_time.
    days = req.days if req.days and req.days > 1 else (
        _extract_days(req.available_time) or _extract_days(req.free_text) or 1
    )

    # Time budget.
    time_hours = _extract_hours(req.available_time) or _extract_hours(req.free_text)
    if time_hours is None and days > 1:
        time_hours = days * 8.0

    # Style weights + named places for the resolver.
    style_weights = _derive_style_weights(req, blob, interests)
    named_places = [p.strip() for p in (req.named_places or []) if p and p.strip()]

    return Preferences(
        city=req.city,
        budget=req.budget if req.budget is not None else _extract_budget(req.free_text),
        time_hours=time_hours,
        mood=req.mood or (req.free_text if req.free_text else None),
        energy=energy,
        interests=interests,
        dietary=dietary,
        avoid_crowds=avoid_crowds,
        other_constraints=other,
        days=days,
        start_date=(req.start_date or "").strip() or None,
        extra_context=(req.clarifications or "").strip(),
        origin=(req.origin or "").strip() or None,
        style_weights=style_weights,
        named_places=named_places,
    )