# Backend — Trip Planner (FastAPI)

Async FastAPI service. The agent runs a chain of six discrete tools and emits a
trace step after each. Real place data from OpenStreetMap; no API keys required.

## Quick start
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload      # http://localhost:8000, docs at /docs
```

## Tests
```bash
pip install -r requirements-dev.txt
pytest        # OSM is mocked (respx) — runs fully offline
```

## Layout
```
app/
  main.py            FastAPI app: /health, /api/plan, /api/plan/stream (SSE)
  config.py          env-driven settings (all have defaults)
  models.py          Pydantic request/response/domain models
  agent/
    orchestrator.py  chains the tools, emits the trace, handles fallbacks
    clarify.py       detects vague input → 1–2 clarifying questions
  tools/
    parse.py         parseUserPreferences
    osm_client.py    Nominatim + Overpass client (graceful on failure)
    options.py       getActivityOptions / getFoodOptions
    validate.py      estimateCost / validatePlan
    generate.py      generateFinalPlan (+ optional LLM narration hook)
  data/curated.py    fallback seed data
tests/               unit tests (tools) + integration tests (API, mocked OSM)
```

## Config
Copy `.env.example` → `.env` to override defaults. Nothing is required; the
service runs with zero configuration. Set `LLM_PROVIDER`/`LLM_API_KEY` only if you
want the optional LLM-phrased summary.
