"""Application configuration.

All settings are read from environment variables with sensible defaults so the
service runs out of the box with zero configuration. Override any of these via
a .env file or your host's environment settings.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Service ---
    app_name: str = "Trip Planner"
    environment: str = "production"
    log_level: str = "INFO"

    # --- Persistence (saved plans + notes) ---
    # SQLite file for user-saved plans. Uses the Python stdlib, no extra deps.
    db_path: str = "tripplanner.db"

    # --- Rate limiting (trip generations per user) ---
    # Each user (logged-in account or guest browser token) may generate at most
    # `trip_rate_limit` plans within a rolling `trip_rate_window_hours` window.
    # Protects the LLM/search budget from abuse. Set the limit to 0 to disable.
    trip_rate_limit: int = 10
    trip_rate_window_hours: float = 24.0

    # Comma-separated list of allowed CORS origins. "*" allows all (fine for a
    # public demo; tighten for real production).
    cors_origins: str = "*"

    # --- External data (OpenStreetMap) ---
    # No API keys required. These are public community endpoints; be a good
    # citizen and keep request volume modest.
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # OSM/Nominatim REQUIRE a non-generic User-Agent that identifies the app,
    # or they reject requests with HTTP 403 ("Access denied"). A placeholder /
    # example.com contact is treated as generic and blocked — keep this real.
    http_user_agent: str = "TripPlanner/1.0 (+https://github.com/trip-planner/trip-planner)"
    http_timeout_seconds: float = 12.0
    # Search radius around the city centre, in metres.
    search_radius_m: int = 100000
    # If OSM is slow/unreachable, we fall back to curated seed data after this.
    osm_enabled: bool = True

    # --- Optional LLM narration hook ---
    # If an API key is present, the reasoner can use an LLM to phrase the
    # "why this fits you" narration. If absent, a deterministic rule-based
    # reasoner is used instead, so the app works with no keys at all.
    llm_provider: str = "none"  # "none" | "openai" | "anthropic"
    llm_api_key: str = ""
    llm_model: str = ""
  
    # ------------------------------------------------------------------
    # Optional data integrations. Each one activates only when its key is
    # set; otherwise the app silently falls back (OSM / curated / deep-links),
    # so everything keeps working with zero configuration.
    # ------------------------------------------------------------------

    # --- Weather: Open-Meteo (FREE, no key) ---
    weather_enabled: bool = True
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_geocode_url: str = "https://geocoding-api.open-meteo.com/v1/search"

    # --- Places: Foursquare (free tier; set key to prefer over OSM) ---
    # New (2025) Foursquare Places API: places-api.foursquare.com with a
    # "Bearer <service key>" and a dated X-Places-Api-Version header.
    foursquare_api_key: str = ""
    foursquare_url: str = "https://places-api.foursquare.com/places/search"
    foursquare_api_version: str = "2025-06-17"

    # --- Routing: OpenRouteService (free tier; travel time between stops) ---
    ors_api_key: str = ""
    ors_matrix_url: str = "https://api.openrouteservice.org/v2/matrix/driving-car"

    # --- Hotels (free, no credit card): Geoapify Places ---
    # Recommended Amadeus alternative. Free 3,000 req/day, simple API key, no
    # billing. Returns real hotels near the plan's coordinates.
    geoapify_api_key: str = ""
    geoapify_places_url: str = "https://api.geoapify.com/v2/places"
    geoapify_geocode_url: str = "https://api.geoapify.com/v1/geocode/search"

    # --- Hotels/flights: Amadeus Self-Service (free TEST env) ---
    amadeus_client_id: str = ""
    amadeus_client_secret: str = ""
    amadeus_base_url: str = "https://test.api.amadeus.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
