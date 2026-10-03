# Deploying Trip Planner (free hosting)

This app ships as **one service**: the FastAPI backend serves both the API and
the built React app at the same origin. That means **one free host, no CORS, no
second deployment**. The included `Dockerfile` builds the frontend and the
backend into a single image that runs anywhere.

```
┌─────────────────────────────────────────────┐
│  one container (Dockerfile)                   │
│   • React app  → served at  /                 │
│   • FastAPI API → served at /api/*, /health   │
│   • SQLite file → saved trips / accounts      │
└─────────────────────────────────────────────┘
```

---

## Before you start

1. **An OpenAI API key** (`LLM_API_KEY`). The AI itineraries, AI city selection
   and "famous for" highlights use it. Costs are on your OpenAI account (the
   model is `gpt-4o-mini`, which is cheap). Without a key the app still runs but
   falls back to curated data only.
2. **A GitHub repo.** The free hosts below deploy from GitHub.

   ```bash
   cd Trip-planner
   git init
   git add .
   git commit -m "Trip Planner"
   # create an empty repo on github.com, then:
   git remote add origin https://github.com/<you>/trip-planner.git
   git push -u origin main
   ```  

   ⚠️ **`backend/.env` is git-ignored and must stay that way** — it holds your
   real keys. Only `.env.example` (no secrets) is committed. If any real key was
   ever shared or committed, **rotate it** before going public.

---

## Option A — Render (simplest, recommended)

Free, no credit card for a web service, one-click from the included
`render.yaml`.

1. Go to **render.com → New → Blueprint**.
2. Connect your GitHub repo and pick it. Render reads `render.yaml` and proposes
   one service named **trip-planner**.
3. Click **Apply**. The first build takes a few minutes (it compiles the
   frontend, then the backend).
4. Open the service → **Environment** → add your secret:
   - `LLM_API_KEY` = `sk-...your key...`
   (optionally `GEOAPIFY_API_KEY`, `ORS_API_KEY` — both free, for real hotels /
   travel times.)
5. Save → it redeploys. Visit the URL Render gives you (e.g.
   `https://trip-planner.onrender.com`) — the full app loads.

**Free-tier caveats (Render):**
- The service **sleeps after ~15 min idle**; the next request wakes it in
  ~30–60s. Normal for free.
- The disk is **ephemeral** — saved trips, accounts and trip history reset on
  every deploy/restart. The planner itself works fine; only the *saved library*
  doesn't persist. For persistence use Option B, or attach a Render paid disk
  and set `DB_PATH=/var/data/tripplanner.db`.

---

## Option B — Fly.io (free, keeps your saved trips)

Fly gives a small **free persistent volume**, so saved trips/accounts survive
restarts. Needs the `flyctl` CLI and a card for verification (still free to run
small).

```bash
# install flyctl: https://fly.io/docs/hands-on/install-flyctl/
fly launch --no-deploy          # detects the Dockerfile; name the app
fly volumes create data --size 1 --region <your-region>   # 1 GB, free
```

In the generated `fly.toml`, mount the volume and point the DB at it:

```toml
[mounts]
  source = "data"
  destination = "/data"

[env]
  DB_PATH = "/data/tripplanner.db"
  PORT    = "8080"
```

Then set the secret and deploy:

```bash
fly secrets set LLM_API_KEY=sk-...        # also GEOAPIFY_API_KEY etc. if you have them
fly deploy
fly open
```

---

## Option C — Railway / Hugging Face Spaces

- **Railway** (`railway.toml` included): New Project → Deploy from GitHub → it
  builds the `Dockerfile`. Add `LLM_API_KEY` in Variables. Free credit each
  month; has a usable ephemeral/volume option.
- **Hugging Face Spaces**: New Space → **Docker** (blank) → push this repo. Add
  `LLM_API_KEY` as a Space secret. Spaces are free and don't aggressively sleep.

---

## Run the production image locally (optional sanity check)

Requires Docker Desktop running:

```bash
docker build -t trip-planner .
docker run -p 8000:8000 -e LLM_API_KEY=sk-... trip-planner
# open http://localhost:8000
```

---

## Environment variables

| Variable | Needed? | Default | What it does |
|---|---|---|---|
| `LLM_API_KEY` | **Yes** (for AI) | — | OpenAI key for itineraries, city selection, highlights |
| `LLM_PROVIDER` | no | `openai` | provider (`openai` / `none`) |
| `LLM_MODEL` | no | `gpt-4o-mini` | model id |
| `PORT` | auto | `8000` | the host injects this; don't hardcode |
| `DB_PATH` | no | `tripplanner.db` | SQLite file; point at a volume to persist |
| `CORS_ORIGINS` | no | `*` | leave empty for same-origin single-service |
| `TRIP_RATE_LIMIT` | no | `10` | plan generations per user per window (0 = off) |
| `TRIP_RATE_WINDOW_HOURS` | no | `24` | the rate-limit window |
| `HTTP_USER_AGENT` | no | app default | **must not be generic** — Nominatim 403s placeholders |
| `GEOAPIFY_API_KEY` | no | — | real hotels near the plan (free, 3k/day) |
| `ORS_API_KEY` | no | — | travel time between stops (free tier) |

Everything except `LLM_API_KEY` has a safe default — the app boots with just
that one secret.

---

## Security checklist before going public

- [ ] `backend/.env` is **not** committed (it's git-ignored).
- [ ] Secrets are set in the host's dashboard, **not** in the repo.
- [ ] Any key that was ever pasted into source or shared has been **rotated**.
- [ ] `TRIP_RATE_LIMIT` is set (default 10/day) so a stranger can't burn your
      OpenAI credits.
