"""Pydantic models — the typed contract between the API, the agent, and the UI."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class PlanRequest(BaseModel):
    """Structured input. Every field is optional except city, because the agent
    is designed to cope with vague input and ask clarifying questions."""

    city: str = Field(..., min_length=1, examples=["Bangalore"])
    # Where the traveler is departing from (their city) — used to estimate
    # flight/travel cost accurately. Optional.
    origin: Optional[str] = Field(default=None, examples=["Delhi"])
    budget: Optional[int] = Field(
        default=None, ge=0, description="Total budget in INR", examples=[2000]
    )
    available_time: Optional[str] = Field(
        default=None, description="Free text, e.g. '4 hours'", examples=["4 hours"]
    )
    mood: Optional[str] = Field(
        default=None, examples=["tired but wants to do something fun"]
    )
    interests: list[str] = Field(default_factory=list, examples=[["food", "music", "walks"]])
    constraints: list[str] = Field(
        default_factory=list, examples=[["vegetarian", "avoid crowded places"]]
    )
    # Number of days for the trip (1 = a single outing; >1 = multi-day getaway).
    days: int = Field(default=1, ge=1, le=21, examples=[1])
    # Trip start date (YYYY-MM-DD). When set, we fetch real day-by-day weather
    # for the city you're in each day.
    start_date: Optional[str] = Field(default=None, examples=["2026-11-01"])
    # Free-text alternative to the structured fields above.
    free_text: Optional[str] = Field(default=None, examples=[None])
    # Answers to the agent's clarifying questions (freeform "Q: … A: …" text),
    # folded into the planning context to sharpen the result.
    clarifications: Optional[str] = Field(default=None, examples=[None])
    # Travel-style preferences, used by the resolver to weight city selection.
    # Any of: {"beach": float, "culture": float, "food": float, "pace": str}
    # where pace is "packed" | "balanced" | "slow". Optional.
    style_weights: Optional[dict] = Field(default=None)
    # If the user explicitly named cities to visit, skip discovery and use these.
    named_places: list[str] = Field(default_factory=list)

    @field_validator("city")
    @classmethod
    def _strip_city(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("city cannot be empty")
        return v


# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


class Preferences(BaseModel):
    """Normalised preferences produced by parseUserPreferences."""

    city: str
    budget: Optional[int] = None
    time_hours: Optional[float] = None
    mood: Optional[str] = None
    energy: Literal["low", "medium", "high"] = "medium"
    interests: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list)
    avoid_crowds: bool = False
    other_constraints: list[str] = Field(default_factory=list)
    days: int = 1
    start_date: Optional[str] = None
    extra_context: str = ""
    origin: Optional[str] = None
    # Carried through from the request so the resolver can use them.
    style_weights: dict = Field(default_factory=dict)
    named_places: list[str] = Field(default_factory=list)


class Place(BaseModel):
    """A single real-world location returned by the data tools."""

    name: str
    category: str  # e.g. "cafe", "park", "live_music"
    kind: Literal["activity", "food"]
    lat: Optional[float] = None
    lon: Optional[float] = None
    est_cost: int = 0
    tags: list[str] = Field(default_factory=list)
    source: Literal["openstreetmap", "curated", "foursquare", "ai"] = "openstreetmap"
    address: Optional[str] = None
    rating: Optional[float] = None
    image_url: Optional[str] = None
    # Which city/stop this place belongs to (multi-city trips).
    city: Optional[str] = None


class PlanItem(BaseModel):
    """One slot in the final itinerary."""

    order: int
    time_slot: str
    title: str
    place: Place
    duration_hours: float
    est_cost: int
    why_it_fits: str
    tradeoff: Optional[str] = None
    day: int = 1
    # Which city this stop is in (multi-city trips); shown as a day header.
    city: Optional[str] = None
    # What to try/do here, what to avoid, and how to reach the next stop (+ cost).
    tips_try: Optional[str] = None
    tips_avoid: Optional[str] = None
    commute_next: Optional[str] = None


class TravelLeg(BaseModel):
    """Travel between two consecutive stops (from OpenRouteService)."""

    from_name: str
    to_name: str
    distance_km: float
    duration_min: float
    mode: str = "driving"


class CostBreakdown(BaseModel):
    """Itemized trip cost estimate, per person, in INR. The AI estimates travel
    and stay; `activities` is the exact sum of the plan's per-stop costs."""

    currency: str = "INR"
    flights: Optional[int] = None        # round-trip from the origin (recommended mode)
    arrival_mode: Optional[str] = None   # the mode the flights line actually represents
    inter_city: Optional[int] = None     # transport between cities on a multi-city trip
    accommodation: Optional[int] = None  # for the whole stay
    food: Optional[int] = None
    local_transport: Optional[int] = None
    activities: Optional[int] = None     # sum of per-stop entry/spend costs
    total: Optional[int] = None
    per: str = "person"
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Multi-city route (produced by the DestinationResolver)
# ---------------------------------------------------------------------------


class TransportOption(BaseModel):
    """One realistic way to get from the origin to the destination."""

    mode: str  # flight | train | bus | car | ferry
    duration: Optional[str] = None
    cost_inr: Optional[int] = None  # one-way, per person
    available: bool = True
    recommended: bool = False
    note: Optional[str] = None


class RouteStop(BaseModel):
    """One city/stop in the journey. A single-city trip has exactly one of these."""

    city: str
    country: Optional[str] = None
    nights: int = 1
    order: int = 1
    lat: Optional[float] = None
    lon: Optional[float] = None
    # Reasoning, split so the UI can show them separately.
    why_city: Optional[str] = None       # why this place is on the route
    why_nights: Optional[str] = None     # why this many nights here
    # Provenance / trust.
    source: Literal["wikivoyage", "user", "geocode", "single", "ai"] = "wikivoyage"
    poi_count: int = 0                   # how many real POIs we verified here
    # Which day numbers fall in this city (filled by the night allocator).
    day_start: int = 1                   # inclusive, 1-based
    day_end: int = 1                     # inclusive


class IntercityLeg(BaseModel):
    """How to travel between two consecutive cities."""

    from_city: str
    to_city: str
    mode: str = "train"  # flight | train | bus | car | ferry
    duration: Optional[str] = None
    cost_inr: Optional[int] = None
    note: Optional[str] = None


class UnusedPlace(BaseModel):
    """A candidate place discovered but not selected — the 'also considered'
    menu that lets a first-timer override the route."""

    name: str
    blurb: Optional[str] = None
    reason_skipped: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class Route(BaseModel):
    """The resolver's full output. The orchestrator passes this down the pipeline."""

    destination: str                     # what the user typed, e.g. "Thailand"
    is_country: bool = False
    total_days: int = 1
    stops: list[RouteStop] = Field(default_factory=list)
    legs: list[IntercityLeg] = Field(default_factory=list)   # between stops
    reasoning: Optional[str] = None      # narrated "why this route" (grounded)
    unused: list[UnusedPlace] = Field(default_factory=list)
    tradeoffs: Optional[str] = None      # "want it slower? drop the islands…"
    notes: list[str] = Field(default_factory=list)  # honest degrade notes

    def city_for_day(self, day: int) -> Optional[RouteStop]:
        """Which stop a given 1-based day belongs to."""
        for s in self.stops:
            if s.day_start <= day <= s.day_end:
                return s
        return self.stops[-1] if self.stops else None


class RecommendedHotel(BaseModel):
    """An AI-recommended place to stay, matched to the trip's area & budget."""

    name: str
    area: Optional[str] = None
    price_per_night: Optional[int] = None  # INR
    rating: Optional[str] = None
    why: Optional[str] = None
    category: str = "hotel"
    image_url: Optional[str] = None
    # Which city this hotel is in (multi-city trips).
    city: Optional[str] = None


class FamousActivity(BaseModel):
    """Something a place is well-known for — an experience worth planning around."""

    title: str                       # e.g. "Island-hopping to the Phi Phi Islands"
    kind: str = "activity"           # activity | experience | nature | nightlife | shopping | landmark
    why: Optional[str] = None        # what makes it famous / worth doing


class SignatureFood(BaseModel):
    """A dish/food a place is famous for, and where to get the good version."""

    dish: str                        # e.g. "Massaman curry"
    why: Optional[str] = None        # what it's famous for / what it tastes like
    where: Optional[str] = None      # a famous spot, market or area to try it


class CityHighlights(BaseModel):
    """What a single city/location is famous for — produced by the highlights
    agent, grounded in real web results. Shown per day (the city you're in)."""

    city: str
    famous_for: list[FamousActivity] = Field(default_factory=list)
    signature_foods: list[SignatureFood] = Field(default_factory=list)
    note: Optional[str] = None       # a short local tip (season, etiquette, scams)
    source: str = "ai"               # "ai" (web-grounded) | "none"


class Plan(BaseModel):
    city: str
    items: list[PlanItem] = Field(default_factory=list)
    total_cost: int = 0
    total_hours: float = 0.0
    within_budget: bool = True
    summary: str = ""
    is_fallback: bool = False
    legs: list[TravelLeg] = Field(default_factory=list)
    days: int = 1
    # AI-provided trip guidance (the planner recommends these; the user needn't know).
    best_time_to_visit: Optional[str] = None
    approx_cost: Optional[str] = None  # realistic per-person range incl. travel & stay
    tips: Optional[str] = None
    cost_breakdown: Optional[CostBreakdown] = None
    hotels: list["RecommendedHotel"] = Field(default_factory=list)
    # How to get there from the origin (all realistic modes, recommended marked).
    arrival_transport: list[TransportOption] = Field(default_factory=list)
    # --- Multi-city route (filled by the DestinationResolver) ---
    route: Optional[Route] = None            # the resolved multi-city route
    route_reasoning: Optional[str] = None    # "why this route" shown above the days
    # What each city on the trip is famous for (highlights agent, web-grounded).
    highlights: list["CityHighlights"] = Field(default_factory=list)


class Weather(BaseModel):
    """A short weather outlook for the plan's city/date (Open-Meteo)."""

    date: str
    temp_max_c: Optional[float] = None
    temp_min_c: Optional[float] = None
    precipitation_chance: Optional[int] = None  # %
    description: str = ""
    emoji: str = ""
    advisory: Optional[str] = None  # e.g. "Rain likely — keep indoor options handy"
    # Which city this outlook is for (multi-city trips).
    city: Optional[str] = None
    # Which trip day this outlook is for (day-by-day weather).
    day: Optional[int] = None


class Hotel(BaseModel):
    """A real hotel offer (Amadeus) for the booking panel."""

    name: str
    price: Optional[float] = None
    currency: str = "INR"
    rating: Optional[str] = None
    address: Optional[str] = None
    booking_url: Optional[str] = None


class TraceStep(BaseModel):
    """One line of the agent's visible reasoning trace."""

    step: int
    tool: str
    # "repair" = the agent caught a problem and fixed it (a trust signal, not an error).
    status: Literal["start", "ok", "warn", "error", "repair"]
    message: str
    detail: Optional[dict] = None


class ClarifyingQuestion(BaseModel):
    field: str
    question: str
    placeholder: Optional[str] = None


class ClarifyResponse(BaseModel):
    """Questions the agent asks before planning, to get more insight."""

    questions: list[ClarifyingQuestion] = Field(default_factory=list)
    source: str = "rules"  # "ai" | "rules"


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class PlanResponse(BaseModel):
    plan: Optional[Plan] = None
    preferences: Optional[Preferences] = None
    trace: list[TraceStep] = Field(default_factory=list)
    clarifying_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    weather: Optional[Weather] = None
    # Per-city weather outlooks for multi-city trips (weather above stays single).
    weather_by_city: list[Weather] = Field(default_factory=list)
    # Day-by-day weather for the city you're in each day (needs a start_date).
    weather_by_day: list[Weather] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Saved plans (persistence)
# ---------------------------------------------------------------------------


class SavePlanRequest(BaseModel):
    """Body for POST /api/plans — persist a generated plan plus optional notes."""

    title: Optional[str] = Field(default=None, examples=["Chill Saturday in Bangalore"])
    notes: str = Field(default="", description="The user's own tips/notes for this plan")
    plan: Plan
    request: Optional[PlanRequest] = None


class UpdatePlanRequest(BaseModel):
    """Body for PATCH /api/plans/{id} — edit title and/or notes."""

    title: Optional[str] = None
    notes: Optional[str] = None


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3, examples=["you@example.com"])
    password: str = Field(..., min_length=6)

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v:
            raise ValueError("Enter a valid email address.")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    email: str


class HotelsResponse(BaseModel):
    """Response for GET /api/hotels — real offers when Amadeus is configured."""

    enabled: bool = False
    hotels: list[Hotel] = Field(default_factory=list)
    note: Optional[str] = None


class SavedPlan(BaseModel):
    """A persisted plan as returned to the client."""

    id: str
    title: str
    city: str
    notes: str = ""
    plan: Plan
    request: Optional[PlanRequest] = None
    created_at: float
    updated_at: float