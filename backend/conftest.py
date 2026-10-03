"""Test configuration.

Force the optional integrations OFF during tests so the suite is deterministic
and fully offline — no real OpenAI / Foursquare / weather calls (which would be
flaky and could cost money). Tests exercise the OSM + curated paths via respx
mocks. This runs before the test modules import the app/settings.
"""
import os

for _var in (
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "FOURSQUARE_API_KEY",
    "ORS_API_KEY",
    "GEOAPIFY_API_KEY",
    "AMADEUS_CLIENT_ID",
    "AMADEUS_CLIENT_SECRET",
):
    os.environ[_var] = "none" if _var == "LLM_PROVIDER" else ""
# Weather hits the network too; keep it off for deterministic tests.
os.environ["WEATHER_ENABLED"] = "false"
# Disable the per-user trip rate limit so repeated runs stay deterministic.
os.environ["TRIP_RATE_LIMIT"] = "0"

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
