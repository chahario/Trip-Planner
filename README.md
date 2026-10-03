# Trip Planner

An AI agent that plans a fun, personalised day out from your city, budget, time,
mood, interests and constraints. It uses **real places from OpenStreetMap**,
reasons about your inputs through a chain of discrete tools, and shows its work
on demand. You can **save plans with your own notes** (persisted in the backend)
and **book hotels & buses** via pre-filled deep-links to real providers.

- **Frontend:** React + TypeScript + Vite
- **Backend:** FastAPI (Python 3.11), async
- **Data:** OpenStreetMap — Nominatim (geocoding) + Overpass (places). **No API keys.**
- **Reasoning:** deterministic rule-based engine (runs with zero keys); optional LLM hook

```
├── backend/     FastAPI agent, 6 tools, OSM client, SSE streaming, tests
├── frontend/    React SPA: form, live trace panel, plan view
└── docker-compose.yml   one-command local run
```

---

## Live URL

The frontend is a static SPA and the backend is one FastAPI service — deploy each
once and you get a public URL you can open, type into and test. See
[Deployment](#deployment). The app works out of the box with **no API keys**.

---

## How the agent works

It is **not one giant prompt**. The request flows through discrete, independently
testable tools, and each one emits a line to the visible trace:

| # | Tool | What it does |
|---|------|--------------|
| 1 | `parseUserPreferences` | Normalises structured **or** free-text input → energy level, interests, diet, budget, time, crowd-aversion |
| 2 | `geocodeCity` | Nominatim: city → lat/lon (real data) |
| 3 | `getActivityOptions` | Overpass: real parks, venues, museums near the city, mapped from interests |
| 4 | `getFoodOptions` | Overpass: real cafés/restaurants, filtered by budget & diet |
| 5 | `generateFinalPlan` | Sequences an itinerary by energy & time; writes per-stop *why it fits* + *trade-offs* |
| 6 | `estimateCost` / `validatePlan` | Totals cost & time; checks budget, time, diet and crowd constraints → warnings |

**Required behaviours, all covered:**

- ✅ Understands preferences (structured JSON *and* free text)
- ✅ Uses 6 tools, not one prompt
- ✅ Real, specific plans from live OSM data
- ✅ Explains *why* each part fits the user
- ✅ **Graceful failure handling** — if OSM is unreachable or empty, it falls back to
  curated data, and if *nothing* is found it returns a safe fallback plan (never an error)
- ✅ **Shows a trace** of every tool call and its result

**Bonus behaviours included:**

- ✅ Real data (OpenStreetMap, no key) with curated fallback
- ✅ Streaming "agent is thinking" trace (Server-Sent Events)
- ✅ Fallback plan when no options match
- ✅ Clarifying questions when input is vague (non-blocking — still plans with defaults)
- ✅ Trade-off notes (e.g. "slightly over budget but fits your mood")
- ✅ Avoids unrealistic suggestions (durations/costs are heuristic-bounded; crowd/energy aware)

---

## Run locally

### Option A — Docker (one command)

```bash
docker compose up --build
# frontend → http://localhost:8080
# backend  → http://localhost:8000  (docs at /docs)
```

### Option B — run each service

**Backend**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload                        # http://localhost:8000
```

**Frontend** (in a second terminal)
```bash
cd frontend
npm install
npm run dev                                          # http://localhost:5173
```
In dev, Vite proxies `/api` to `localhost:8000`, so no CORS setup is needed.

### Run the tests
```bash
cd backend
pip install -r requirements-dev.txt
pytest            # 13 tests, OSM mocked — fully offline
```

---

## Deployment

The two services deploy independently.

### Backend → Render (or Railway / Fly / any Docker host)
- Render: **New → Blueprint** on this repo (uses `backend/render.yaml`), or a **Web Service** with root `backend/`, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- A `Dockerfile` and `railway.toml` are included too.
- Health check: `GET /health`.

### Frontend → Vercel (or Netlify / any static host)
- Set env var **`VITE_API_BASE_URL`** to your deployed backend URL (e.g. `https://trip-planner.onrender.com`).
- Vercel: import repo, root `frontend/` — `vercel.json` handles the rest.
- Netlify: `netlify.toml` is included.

> After deploying, set the backend's `CORS_ORIGINS` to your frontend URL for a tighter production setup (defaults to `*`).

---

## API

`POST /api/plan` — full plan as JSON.
`POST /api/plan/stream` — Server-Sent Events: `trace` steps, then a final `plan`.

**Saved plans** (persisted in SQLite, scoped by an `X-User-Token` header the client generates):

`GET /api/plans` — list the caller's saved plans.
`POST /api/plans` — save a plan `{ title?, notes?, plan, request? }`.
`GET /api/plans/{id}` · `PATCH /api/plans/{id}` (edit title/notes) · `DELETE /api/plans/{id}`.

```json
{
  "city": "Bangalore",
  "budget": 2000,
  "available_time": "4 hours",
  "mood": "tired but wants to do something fun",
  "interests": ["food", "music", "walks"],
  "constraints": ["vegetarian", "avoid crowded places"]
}
```
Free text is also accepted via a `free_text` field. Interactive docs at `/docs`.

`GET /api/integrations` — which optional APIs are currently active.
`GET /api/hotels?city=…` — real hotel offers when Amadeus is configured.

---

## Optional API integrations (add keys one at a time)

Every integration is **off by default and activates only when its key is set** —
the app always falls back (OSM → curated → deep-links) so nothing breaks while
keys are missing. Set these as environment variables (or in `backend/.env`):

| Feature | Env var(s) | Free? | Get a key |
|---|---|---|---|
| **Weather** (Open-Meteo) | *(none — on by default)* | ✅ free, no key | — |
| **GPT narration** | `LLM_API_KEY` (+ optional `LLM_MODEL`, default `gpt-4o-mini`) | paid | platform.openai.com |
| **Real hotels near the plan** (Geoapify) | `GEOAPIFY_API_KEY` | ✅ free 3k/day, no card | geoapify.com |
| **Travel time between stops** (OpenRouteService) | `ORS_API_KEY` | ✅ free tier | openrouteservice.org |
| **Places + food** (Foursquare) | `FOURSQUARE_API_KEY` | ⚠️ 2025 API needs paid credits | foursquare.com/developers |
| **Real hotels** (Amadeus, alt.) | `AMADEUS_CLIENT_ID`, `AMADEUS_CLIENT_SECRET` | ✅ free test env | developers.amadeus.com |

When a key is present: **Geoapify** returns real hotels near the first
recommended stop (preferred; free and no billing), OpenRouteService adds travel
legs to the itinerary, Foursquare is preferred over OSM for activities/food
*(if the account has credits — the 2025 API is no longer free)*, Amadeus is a
secondary hotel source, and GPT warms up the plan summary. Check
`GET /api/integrations` to see what's live. Keys live in `backend/.env` (never
commit it — see `backend/.gitignore`), not in source.

**Saved plans work for guest users** — no login. Each browser gets an opaque
token (stored locally, sent as `X-User-Token`); plans + notes persist in the
backend SQLite DB and sync across devices via the "Sync code".

**Bookings are location-aware:** hotels and restaurants are centred on the first
recommended stop (e.g. "hotels near Cubbon Park"), while intercity buses target
the destination city.

---

## How AI tools were used in the build

This project was built with **Claude (Claude Code / Opus)**. Claude scaffolded the
FastAPI agent and the tool-per-function architecture, wrote the OpenStreetMap
Overpass/Nominatim client and the rule-based reasoner, generated the React UI with
the live SSE trace, and wrote the pytest suite. Every backend test was run and
passing, and the frontend was type-checked and built, before hand-off. I directed
the architecture (real data + graceful fallback, zero-key operation) and reviewed
all decisions.

---

Data © OpenStreetMap contributors. Cost and time figures are estimates.
