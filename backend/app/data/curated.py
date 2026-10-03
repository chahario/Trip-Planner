"""Curated seed data.

Used as a graceful fallback when OpenStreetMap is unreachable, rate-limited, or
returns nothing for a city. Kept deliberately small — it exists so the agent
never returns an empty plan, not to replace real data.
"""
from __future__ import annotations

# Cost bands are rough per-person INR estimates used only when OSM gives us no
# price signal (OSM rarely has prices). These are heuristics, clearly labelled
# as estimates in the UI.
CATEGORY_COST_BANDS: dict[str, int] = {
    "cafe": 350,
    "restaurant": 500,
    "street_food": 150,
    "bakery": 200,
    "ice_cream": 150,
    "park": 0,
    "garden": 30,
    "viewpoint": 0,
    "museum": 100,
    "gallery": 100,
    "live_music": 600,
    "bar": 700,
    "cinema": 300,
    "market": 200,
    "walk": 0,
}

# Minimal per-city fallback so a plan is always possible for the sample cities.
# Generic entries are used for any other city.
CURATED: dict[str, list[dict]] = {
    "bangalore": [
        {"name": "Cubbon Park", "category": "park", "kind": "activity", "tags": ["walks", "nature", "quiet"]},
        {"name": "Lalbagh Botanical Garden", "category": "garden", "kind": "activity", "tags": ["walks", "nature"]},
        {"name": "Third Wave Coffee (Indiranagar)", "category": "cafe", "kind": "food", "tags": ["food", "coffee", "vegetarian"]},
        {"name": "Windmills Craftworks", "category": "live_music", "kind": "activity", "tags": ["music", "jazz", "food"]},
        {"name": "MTR (Lalbagh Road)", "category": "restaurant", "kind": "food", "tags": ["food", "vegetarian", "south-indian"]},
        {"name": "Blossom Book House", "category": "market", "kind": "activity", "tags": ["quiet", "books"]},
    ],
    "mumbai": [
        {"name": "Marine Drive", "category": "walk", "kind": "activity", "tags": ["walks", "sea", "sunset"]},
        {"name": "Sanjay Gandhi National Park", "category": "park", "kind": "activity", "tags": ["walks", "nature"]},
        {"name": "Prithvi Café", "category": "cafe", "kind": "food", "tags": ["food", "coffee", "vegetarian"]},
        {"name": "antiSOCIAL", "category": "live_music", "kind": "activity", "tags": ["music", "gigs"]},
    ],
    "delhi": [
        {"name": "Lodhi Garden", "category": "garden", "kind": "activity", "tags": ["walks", "nature", "quiet"]},
        {"name": "Hauz Khas Village", "category": "market", "kind": "activity", "tags": ["walks", "art"]},
        {"name": "Rose Café", "category": "cafe", "kind": "food", "tags": ["food", "coffee", "vegetarian"]},
        {"name": "The Piano Man Jazz Club", "category": "live_music", "kind": "activity", "tags": ["music", "jazz"]},
    ],
}

GENERIC: list[dict] = [
    {"name": "Central city park", "category": "park", "kind": "activity", "tags": ["walks", "nature", "quiet"]},
    {"name": "A well-reviewed local café", "category": "cafe", "kind": "food", "tags": ["food", "coffee", "vegetarian"]},
    {"name": "Popular vegetarian restaurant", "category": "restaurant", "kind": "food", "tags": ["food", "vegetarian"]},
    {"name": "Live music venue", "category": "live_music", "kind": "activity", "tags": ["music"]},
    {"name": "Riverside / lakeside walk", "category": "walk", "kind": "activity", "tags": ["walks", "quiet"]},
]


def curated_for(city: str) -> list[dict]:
    return CURATED.get(city.strip().lower(), GENERIC)
