# 🗺️ Trip Planner

An AI travel agent that turns a loose idea — *"10 days in Thailand, I like beaches
and food"* — into a real, day-by-day itinerary: which **cities** to visit and in
what order, **where to stay** each night, **what each place is famous for**, the
day's **activities**, **how to get there and back**, and a transparent **cost
breakdown** — all grounded in real data so it doesn't make places up.

It plans a **single city**, a **country**, or a whole **continent**, and shows its
agents' work on demand.

- **Frontend:** React + TypeScript + Vite
- **Backend:** FastAPI (Python), async, Server-Sent-Events streaming
- **AI:** OpenAI `gpt-4o-mini` for reasoning, grounded by **web search + live geo/weather data**
- **Everything else is free & keyless:** Open-Meteo, OpenStreetMap, Wikivoyage, DuckDuckGo

---

## ✨ Features

**Planning**
- **City / country / continent aware** — a classifier decides what kind of place you typed and plans accordingly (e.g. *Thailand* → Bangkok → Chiang Mai → islands; *Bangalore* → one city).
- **AI city selection, matched to you** — the agent picks and orders the cities from your interests, pace and budget, grounded in live web search. Same country + different interests → different cities.
- **Day-by-day itinerary** with the city you're in each day, real named stops (morning→evening), and per-stop **✅ Try / ⚠️ Avoid** tips and **➡️ commute to the next stop** (mode + fare).
- **"What this city is famous for"** — signature experiences and local dishes (with where to eat them), shown only on the days you're in that city.
- **Where to stay** — real hotels per city, with a "🏨 staying in *X* tonight" pointer on each day.
- **Getting there & back** — every realistic transport mode (flight only if an airport exists), round-trip priced, **click a mode to re-price the whole trip**.
- **Transparent cost breakdown** — travel + stay + food + local transport + activities, with the travel cost **fetched via tool calls**, not guessed.
- **Day-by-day weather** for the city you're in, on that day's real date.

**Trust & UX**
- **Anti-hallucination** — every city/place is verified against real geo data; a Critic step enforces the exact day count and flags ungrounded output, then repairs it.
- **Clarifying questions** that add insight (it *recommends* cities/budget/best-time rather than asking you for them).
- **Visible agent trace** — the whole pipeline streams live, hidden until you click.
- **Accounts + history** — every trip is auto-saved; log in to sync across devices, or stay a guest.
- **Per-user rate limit** (default 10 trips / 24 h) to protect the API budget.
- **Graceful degradation** — if the LLM or an API is down, it falls back to curated data and never crashes.

---

## 🏗️ Architecture

One deployable service: **FastAPI serves the API *and* the built React app at the
same origin** (no CORS, one free host).

```
┌──────────────────────────────────────────────────────────────┐
│                      Browser (React SPA)                       │
│  PlannerForm · ClarifyPanel · TracePanel (live agent work)     │
│  PlanView (day tabs · route strip · weather · highlights ·     │
│            stays · stops+tips · getting-there · cost)          │
└───────────────┬────────────────────────────────────────────────┘
                │  HTTPS  (POST /api/plan/stream  → SSE: trace…then plan)
                ▼
┌──────────────────────────────────────────────────────────────┐
│                   FastAPI backend (async)                      │
│                                                                │
│   Agent orchestrator  ── streams a TraceStep per stage ──┐     │
│        │                                                 │     │
│        ▼                                                 ▼     │
│   Tools layer                                     SQLite store │
│   destination · llm_plan · highlights · weather   users ·      │
│   web_search · image_search · cost_tools ·        sessions ·   │
│   osm_client · validate(critic) · options         saved_plans ·│
│                                                   trip_events  │
└───────────────┬────────────────────────────────────────────────┘
                │
     ┌──────────┼───────────────┬───────────────┬──────────────┐
     ▼          ▼               ▼               ▼              ▼
  OpenAI    Open-Meteo     OpenStreetMap    Wikivoyage     DuckDuckGo
 (LLM)    (weather +      (Nominatim geo +  (city          (web text +
          geocode/        Overpass POIs)    discovery       image search)
          classify)                         fallback)
```

**Design law:** *APIs supply facts · code makes the decisions · the LLM suggests &
narrates.* The day count and night allocation are **pure code** (guaranteed exact);
the LLM proposes cities and prose but never silently changes the numbers, and
anything it names is verified against real geo data before it reaches you.

### Project structure
```
trip-planner/
├── Dockerfile            # single-image build (frontend + backend)
├── render.yaml           # one-click free deploy (Render Blueprint)
├── DEPLOY.md             # step-by-step hosting guide
├── backend/
│   └── app/
│       ├── main.py       # FastAPI routes, SSE, auth, rate limit, serves the SPA
│       ├── agent/
│       │   └── orchestrator.py   # the multi-stage agent pipeline
│       ├── tools/        # one module per capability (see below)
│       ├── models.py     # Pydantic schemas (the data contract)
│       └── store.py      # SQLite: users, sessions, saved plans, rate-limit events
└── frontend/
    └── src/
        ├── components/   # PlanView, GettingThere, Hotels, CityHighlights, …
        └── lib/          # api client, auth, types
```

---

## 🤖 AI agent architecture

The request is **not one giant prompt**. It flows through discrete, independently
testable stages, each of which streams a line to the live trace. Grounding (web
search + real geo/weather) happens at every stage that could otherwise hallucinate.

```
  user input
     │
 1 ▸ parseUserPreferences        normalise structured OR free-text → interests, pace, budget, days, origin
     │
 2 ▸ askClarifyingQuestions      (non-blocking) AI asks for insight; recommends cities/time/budget
     │
 3 ▸ resolveDestination ─────────────────────────────────────────────┐
     │     classify (Open-Meteo feature codes) → city? country? region? continent?
     │        ├─ city      → single-stop route
     │        └─ multi     → AI CITY SELECTION  (web-grounded, interest-matched)
     │                         → verify every city exists (geocode)   ← anti-hallucination
     │                         → allocate nights   (PURE CODE, exact day count)
     │                         → order route (geographic) + inter-city legs
     │                         (fallback: Wikivoyage city discovery + scoring)
     │                                                                └────────┘
 4 ▸ enrichCities                 per-city weather (+ live OSM places for single-city)
     │
 5 ▸ generateWithAI / buildItinerary
     │     multi-city or thin data → AI itinerary of REAL named places per city
     │     else                    → curated/live builder
     │
 5b▸ discoverHighlights           per city: "famous for" experiences + signature foods  (web-grounded)
 5c▸ recommendStays               per city: real hotels for the nights you sleep there
     │
 6 ▸ estimateCost                 LLM cost agent that MUST call tools:
     │                              get_flight_cost / get_hotel_cost  (deterministic pricing)
     │                            + estimate_arrival_transport  (web-grounded, round-trip, modes)
     │
 6b▸ criticCheck                  enforce exact day count · flag ungrounded · REPAIR gaps
 6c▸ validatePlan                 check against budget / time / diet / crowd constraints
     │
 7 ▸ routeStops                   travel legs between stops (OpenRouteService, optional)
 8 ▸ dayWeather                   per-day forecast for the city you're in that day
     │
     ▼
  streamed plan  (trace steps first, then the final plan)
```

### The agents / tools

| Stage (trace name) | Module | Grounded by | What it does |
|---|---|---|---|
| `parseUserPreferences` | `tools/parse.py` | — | structured **or** free-text → normalized preferences |
| `askClarifyingQuestions` | `tools/llm_plan.py` | LLM | asks for *insight*; recommends, never interrogates |
| `resolveDestination` | `tools/destination.py` | Open-Meteo, web search, OSM | classify place → **AI city selection** → verify → allocate nights (code) |
| `enrichCities` | `tools/weather.py`, `options.py` | Open-Meteo, OSM | per-city weather (+ real places for single-city) |
| `generateWithAI` | `tools/llm_plan.py` | LLM + verify | day-by-day itinerary of **real named places** |
| `discoverHighlights` | `tools/highlights.py` | web search + LLM | what each city is **famous for** + signature food |
| `recommendStays` | `tools/llm_plan.py` | LLM | real hotels **per city** |
| `estimateCost` | `tools/llm_plan.py`, `cost_tools.py` | LLM **tool calls** + web | round-trip transport + itemized cost (fetched, not guessed) |
| `criticCheck` | `tools/validate.py` | code | day-count authority, grounding checks, **auto-repair** |
| `validatePlan` | `tools/validate.py` | code | budget / time / diet / crowd warnings |
| `routeStops` | `tools/routing.py` | OpenRouteService | travel time between stops (optional) |
| `dayWeather` | `tools/weather.py` | Open-Meteo | per-day forecast for that day's city |

### How hallucination is prevented
1. **Classification & geo come from data, not the model** — place *type* and coordinates are from Open-Meteo (GeoNames) and OSM.
2. **Every city/place the LLM proposes is verified** by geocoding; anything that can't be located is dropped before it reaches the plan.
3. **The web grounds the content** — city choices, "famous for", transport existence/fares and durations are fed from real DuckDuckGo results.
4. **A Critic owns the numbers** — nights/day-count are pure code; the Critic re-checks completeness and repairs or honestly flags gaps instead of inventing stops.

---

## 🔌 Data sources

| Source | Used for | Key? |
|---|---|---|
| **OpenAI** (`gpt-4o-mini`) | city selection, itinerary, highlights, cost reasoning | your key (cheap) |
| **Open-Meteo** | weather, geocoding, **place classification** (GeoNames feature codes) | ✅ free, no key |
| **OpenStreetMap** (Nominatim + Overpass) | geocoding, live POIs | ✅ free, no key |
| **Wikivoyage** | city-discovery fallback | ✅ free, no key |
| **DuckDuckGo** (`ddgs`) | web text grounding + real photos | ✅ free, no key |
| **Geoapify** *(optional)* | real hotels near the plan | ✅ free 3k/day |
| **OpenRouteService** *(optional)* | travel time between stops | ✅ free tier |

The app boots with **only** an OpenAI key; everything else has a keyless default.

---

## ▶️ Run locally

**Backend**
```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # add your LLM_API_KEY
uvicorn app.main:app --reload # http://localhost:8000
```

**Frontend** (second terminal)
```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173  (Vite proxies /api → :8000)
```

**Tests**
```bash
cd backend && pip install -r requirements-dev.txt && pytest   # 13 tests, fully offline
```

---

## 🚀 Deploy (free)

The production `Dockerfile` builds the frontend and serves it from FastAPI, so
**one free service** runs the whole app. Fastest path is Render:

1. Push to GitHub.
2. render.com → **New → Blueprint** → pick the repo → **Apply** (reads `render.yaml`).
3. Set `LLM_API_KEY` in the service's Environment tab.

Full step-by-step (plus Fly.io for persistent saved trips, Railway, HF Spaces) is
in **[DEPLOY.md](DEPLOY.md)**.

---

## 📡 API

| Endpoint | Purpose |
|---|---|
| `POST /api/plan` | full plan as JSON |
| `POST /api/plan/stream` | **SSE**: `trace` steps, then the final `plan` |
| `POST /api/clarify` | the agent's clarifying questions for a request |
| `GET /api/usage` | trip-quota usage (N of 10 left, reset countdown) |
| `GET /api/plans` · `POST` · `GET/PATCH/DELETE /{id}` | saved-trip history (scoped by user/guest token) |
| `POST /api/auth/signup \| login \| logout` · `GET /api/auth/me` | accounts |
| `GET /api/integrations` | which optional APIs are live |
| `GET /health` | liveness |

Interactive docs at `/docs`.

---

## ⚙️ Key environment variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_API_KEY` | — | OpenAI key (required for the AI features) |
| `LLM_MODEL` | `gpt-4o-mini` | model id |
| `DB_PATH` | `tripplanner.db` | SQLite file (point at a volume to persist) |
| `TRIP_RATE_LIMIT` / `TRIP_RATE_WINDOW_HOURS` | `10` / `24` | per-user trip quota (0 disables) |
| `HTTP_USER_AGENT` | app default | **must not be generic** — Nominatim 403s placeholder agents |
| `GEOAPIFY_API_KEY`, `ORS_API_KEY` | — | optional free integrations |

See `backend/.env.example` for the full list. Never commit `backend/.env`.

---

Built with [Claude Code](https://claude.com/claude-code). Data © OpenStreetMap
contributors · weather © Open-Meteo. Costs, times and fares are grounded
estimates — confirm exact prices when booking.
