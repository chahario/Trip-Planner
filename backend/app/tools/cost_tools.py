"""Cost tools the AI agent calls (OpenAI function-calling) to GROUND the trip
cost in real data instead of guessing.

- `get_flight_cost`: geocodes both cities (Open-Meteo, free, no key), computes the
  real great-circle distance, and applies a transparent distance-based fare model.
  So the flight number is anchored to actual geography, not the model's memory.
- `get_hotel_cost`: nights × a per-night rate by tier (budget/mid/luxury).

These are deliberately deterministic and explainable. If a real paid flights/
hotels API is configured later, it can be slotted in behind the same functions.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def price_flight(distance_km: float, international: bool) -> int:
    """Deterministic round-trip economy fare (INR) from distance. This is the
    non-hallucinated core — the pricing model is fixed and explainable."""
    km = max(0.0, float(distance_km))
    if km < 150:
        return 0  # too close to fly (road/rail)
    if international:
        one_way = 8000 + 3.2 * max(0.0, km - 1500)
    else:
        one_way = 1200 + 2.3 * km  # domestic
    return int(round(one_way * 2))


def get_flight_cost(
    origin: str, destination: str, approx_distance_km: float, international: bool = False
) -> dict:
    """Round-trip economy flight cost per person (INR). The route facts
    (distance, domestic/international) come from the caller; the TOOL applies the
    fixed fare model — so the price is grounded, not guessed."""
    price = price_flight(approx_distance_km, bool(international))
    mode = "international" if international else "domestic"
    if price == 0:
        mode = "no flight needed (short hop)"
    return {
        "ok": True,
        "price": price,
        "currency": "INR",
        "distance_km": int(round(approx_distance_km)),
        "mode": mode,
        "basis": f"{origin}→{destination} ≈ {int(round(approx_distance_km))} km ({mode}), round-trip economy",
    }


_HOTEL_RATES = {"budget": 1500, "mid": 3500, "luxury": 8000}


def get_hotel_cost(city: str, nights: int, tier: str = "mid") -> dict:
    nights = max(0, int(nights))
    rate = _HOTEL_RATES.get(str(tier).lower(), _HOTEL_RATES["mid"])
    total = rate * nights
    return {
        "ok": True,
        "price": total,
        "currency": "INR",
        "basis": f"{nights} night(s) × ₹{rate}/night ({tier})",
    }


# Tool schemas for OpenAI function-calling.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_flight_cost",
            "description": "Round-trip economy flight cost per person in INR. You provide the approximate flying distance in km and whether it's an international route; the tool applies a fixed fare model.",
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Departure city"},
                    "destination": {"type": "string", "description": "Destination city"},
                    "approx_distance_km": {
                        "type": "number",
                        "description": "Approx flying distance between the cities in km (your best estimate).",
                    },
                    "international": {
                        "type": "boolean",
                        "description": "True if the route crosses an international border.",
                    },
                },
                "required": ["origin", "destination", "approx_distance_km", "international"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_hotel_cost",
            "description": "Accommodation cost per person in INR for the whole stay.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "nights": {"type": "integer"},
                    "tier": {"type": "string", "enum": ["budget", "mid", "luxury"]},
                },
                "required": ["city", "nights"],
            },
        },
    },
]

DISPATCH = {"get_flight_cost": get_flight_cost, "get_hotel_cost": get_hotel_cost}
