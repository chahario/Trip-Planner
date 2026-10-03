"""FastAPI application entrypoint.

Endpoints:
  GET  /health           -> liveness
  POST /api/plan         -> full plan (JSON), runs the whole agent
  POST /api/plan/stream  -> Server-Sent Events: trace steps then final plan

Run locally:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app import store
from app.agent.orchestrator import Agent
from app.config import get_settings
from app.agent.clarify import detect_clarifying_questions
from app.models import (
    AuthResponse,
    ClarifyingQuestion,
    ClarifyResponse,
    HotelsResponse,
    LoginRequest,
    PlanRequest,
    PlanResponse,
    SavedPlan,
    SavePlanRequest,
    SignupRequest,
    TraceStep,
    UpdatePlanRequest,
)
from app.tools import hotels as hotels_tool
from app.tools import llm_plan, places_fsq, routing
from app.tools.parse import parse_user_preferences

settings = get_settings()
logging.basicConfig(level=settings.log_level)
log = logging.getLogger("saturday-planner")

@asynccontextmanager
async def lifespan(_: FastAPI):
    store.init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="An AI agent that plans a fun, personalised trip.",
    lifespan=lifespan,
)

origins = ["*"] if settings.cors_origins.strip() == "*" else [
    o.strip() for o in settings.cors_origins.split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = Agent()


def _resolve_key(x_user_token: str | None, x_auth_token: str | None) -> str:
    """Decide which key saved plans belong to: a logged-in account (stable,
    syncs across devices) if a valid auth token is present, else the guest
    browser token."""
    auth = store.user_for_token((x_auth_token or "").strip())
    if auth:
        return f"user:{auth['id']}"
    token = (x_user_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Sign in or send a guest token.")
    return token


def _rate_key(x_user_token: str | None, x_auth_token: str | None) -> str:
    """Like _resolve_key but never raises — used for rate limiting, where a
    missing token just means an anonymous bucket rather than an error."""
    auth = store.user_for_token((x_auth_token or "").strip())
    if auth:
        return f"user:{auth['id']}"
    return (x_user_token or "").strip() or "anon"


def _usage(key: str) -> dict:
    """Current rate-limit usage for a user key."""
    limit = settings.trip_rate_limit
    window = settings.trip_rate_window_hours * 3600
    now = time.time()
    count, oldest = store.count_recent_trips(key, now - window)
    unlimited = limit <= 0
    remaining = None if unlimited else max(0, limit - count)
    reset_seconds = None
    if not unlimited and count >= limit and oldest:
        reset_seconds = max(1, int((oldest + window) - now))
    return {
        "limit": limit,
        "used": count,
        "remaining": remaining,
        "unlimited": unlimited,
        "window_hours": settings.trip_rate_window_hours,
        "reset_seconds": reset_seconds,
    }


def _enforce_rate_limit(x_user_token: str | None, x_auth_token: str | None) -> str:
    """Raise 429 if the user is over their trip quota; otherwise record this
    generation against their quota and return the resolved key."""
    key = _rate_key(x_user_token, x_auth_token)
    limit = settings.trip_rate_limit
    if limit <= 0:
        return key  # limiting disabled
    u = _usage(key)
    if u["remaining"] == 0:
        retry = u["reset_seconds"] or int(settings.trip_rate_window_hours * 3600)
        hrs, mins = retry // 3600, (retry % 3600) // 60
        when = f"{hrs}h {mins}m" if hrs else f"{mins}m"
        raise HTTPException(
            status_code=429,
            detail=(
                f"You've reached the limit of {limit} trips per "
                f"{int(settings.trip_rate_window_hours)} hours. Try again in {when}. "
                f"Your past trips are saved in your history."
            ),
            headers={"Retry-After": str(retry)},
        )
    store.record_trip_event(key)
    return key


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": settings.app_name, "version": "1.0.0"}


@app.get("/")
async def root():
    # Serve the SPA home when a frontend build is present; else API info (dev).
    if _STATIC_DIR is not None:
        return FileResponse(_STATIC_DIR / "index.html")
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "endpoints": [
            "/health",
            "/api/plan",
            "/api/plan/stream",
            "/api/plans",
        ],
    }


@app.post("/api/plan", response_model=PlanResponse)
async def plan(
    req: PlanRequest,
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> PlanResponse:
    """Run the full agent and return the plan + trace in one JSON response."""
    _enforce_rate_limit(x_user_token, x_auth_token)
    try:
        return await agent.run(req)
    except Exception as exc:  # noqa: BLE001 — surface a clean error, never a 500 stacktrace
        log.exception("plan failed")
        return PlanResponse(error=f"Planning failed: {exc}", warnings=["Please try again."])


@app.post("/api/clarify", response_model=ClarifyResponse)
async def clarify(req: PlanRequest) -> ClarifyResponse:
    """Ask-first: the agent returns a few clarifying questions to get more insight
    before planning. Uses the LLM when available, else rule-based detection."""
    if llm_plan.is_enabled():
        qs = await llm_plan.generate_clarifying_questions(req)
        if qs:
            return ClarifyResponse(
                questions=[ClarifyingQuestion(**q) for q in qs], source="ai"
            )
    # Fallback: deterministic rule-based questions.
    prefs = parse_user_preferences(req)
    rule_qs = detect_clarifying_questions(prefs)
    return ClarifyResponse(questions=rule_qs, source="rules")


@app.post("/api/plan/stream")
async def plan_stream(
    req: PlanRequest,
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> StreamingResponse:
    """Stream the agent's trace as Server-Sent Events, then the final plan.

    Event format (SSE):
      event: trace | plan | error
      data:  <json>
    """
    # Enforced before the stream opens, so an over-quota user gets a clean 429.
    _enforce_rate_limit(x_user_token, x_auth_token)

    async def event_generator():
        try:
            async for event in agent.run_streaming(req):
                if isinstance(event, TraceStep):
                    payload = json.dumps(event.model_dump())
                    yield f"event: trace\ndata: {payload}\n\n"
                elif isinstance(event, PlanResponse):
                    payload = json.dumps(event.model_dump())
                    yield f"event: plan\ndata: {payload}\n\n"
        except Exception as exc:  # noqa: BLE001
            log.exception("stream failed")
            err = json.dumps({"error": str(exc)})
            yield f"event: error\ndata: {err}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx/render)
        },
    )


# ---------------------------------------------------------------------------
# Saved plans (persisted per user via the X-User-Token header)
# ---------------------------------------------------------------------------


@app.get("/api/usage")
async def usage(
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> dict:
    """How many trips the user has generated in the current window, and how many
    remain. Lets the client show "N of 10 trips left" and a reset countdown."""
    return _usage(_rate_key(x_user_token, x_auth_token))


@app.get("/api/plans", response_model=list[SavedPlan])
async def list_saved_plans(
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> list[dict]:
    key = _resolve_key(x_user_token, x_auth_token)
    return store.list_plans(key)


@app.post("/api/plans", response_model=SavedPlan, status_code=201)
async def save_plan(
    body: SavePlanRequest,
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> dict:
    key = _resolve_key(x_user_token, x_auth_token)
    title = (body.title or "").strip() or f"{body.plan.city} plan"
    saved = store.create_plan(
        user_token=key,
        title=title,
        city=body.plan.city,
        plan=body.plan.model_dump(),
        notes=body.notes,
        request=body.request.model_dump() if body.request else None,
    )
    return saved


@app.get("/api/plans/{plan_id}", response_model=SavedPlan)
async def get_saved_plan(
    plan_id: str,
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> dict:
    key = _resolve_key(x_user_token, x_auth_token)
    saved = store.get_plan(key, plan_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return saved


@app.patch("/api/plans/{plan_id}", response_model=SavedPlan)
async def update_saved_plan(
    plan_id: str,
    body: UpdatePlanRequest,
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> dict:
    key = _resolve_key(x_user_token, x_auth_token)
    title = body.title.strip() if body.title is not None else None
    saved = store.update_plan(key, plan_id, title=title, notes=body.notes)
    if not saved:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return saved


@app.delete("/api/plans/{plan_id}", status_code=204)
async def delete_saved_plan(
    plan_id: str,
    x_user_token: str | None = Header(default=None),
    x_auth_token: str | None = Header(default=None),
) -> None:
    key = _resolve_key(x_user_token, x_auth_token)
    if not store.delete_plan(key, plan_id):
        raise HTTPException(status_code=404, detail="Plan not found.")


# ---------------------------------------------------------------------------
# Authentication (signup / login / logout)
# ---------------------------------------------------------------------------


@app.post("/api/auth/signup", response_model=AuthResponse, status_code=201)
async def signup(body: SignupRequest) -> AuthResponse:
    user = store.create_user(body.email, body.password)
    if not user:
        raise HTTPException(status_code=409, detail="That email is already registered.")
    token = store.create_session(user["id"])
    return AuthResponse(token=token, email=user["email"])


@app.post("/api/auth/login", response_model=AuthResponse)
async def login(body: LoginRequest) -> AuthResponse:
    user = store.verify_user(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Wrong email or password.")
    token = store.create_session(user["id"])
    return AuthResponse(token=token, email=user["email"])


@app.post("/api/auth/logout", status_code=204)
async def logout(x_auth_token: str | None = Header(default=None)) -> None:
    if x_auth_token:
        store.delete_session(x_auth_token.strip())


@app.get("/api/auth/me")
async def auth_me(x_auth_token: str | None = Header(default=None)) -> dict:
    user = store.user_for_token((x_auth_token or "").strip())
    return {"email": user["email"]} if user else {"email": None}


# ---------------------------------------------------------------------------
# Optional integrations
# ---------------------------------------------------------------------------


@app.get("/api/integrations")
async def integrations() -> dict:
    """Which optional data APIs are currently active (by whether a key is set)."""
    return {
        "weather": settings.weather_enabled,          # Open-Meteo: free, no key
        "foursquare": places_fsq.is_enabled(),         # real rated places
        "routing": routing.is_enabled(),               # OpenRouteService
        "hotels": hotels_tool.is_enabled(),            # Amadeus
        "llm": bool(settings.llm_api_key),
    }


@app.get("/api/hotels", response_model=HotelsResponse)
async def hotels(
    city: str, lat: float | None = None, lon: float | None = None
) -> HotelsResponse:
    """Real hotels near the plan (Foursquare, then Amadeus) when configured; else
    enabled=false so the client shows keyless deep-links instead. Pass lat/lon of
    a recommended stop to centre the search on it."""
    if not hotels_tool.is_enabled():
        return HotelsResponse(enabled=False, note="No hotel provider configured — using deep-links.")
    results, note = await hotels_tool.search_hotels(city, lat, lon)
    return HotelsResponse(enabled=True, hotels=results, note=note)


# ---------------------------------------------------------------------------
# Serve the built React app (production single-service deploy)
# ---------------------------------------------------------------------------
# In production the Docker image builds the frontend and drops it next to the
# backend, so ONE service serves both the API and the SPA (same origin -> no
# CORS, one free host). Locally, with no build present, this is skipped and the
# backend runs API-only (Vite serves the frontend in dev).
def _find_static_dir() -> Path | None:
    candidates = []
    env_dir = os.environ.get("FRONTEND_DIST")
    if env_dir:
        candidates.append(Path(env_dir))
    here = Path(__file__).resolve()
    candidates.append(here.parent.parent / "static")          # /app/static (Docker)
    candidates.append(here.parents[2] / "frontend" / "dist")  # repo layout (local build)
    for c in candidates:
        if c.is_dir() and (c / "index.html").exists():
            return c
    return None


_STATIC_DIR = _find_static_dir()
if _STATIC_DIR is not None:
    log.info("Serving frontend from %s", _STATIC_DIR)

    # SPA fallback: any non-API path that isn't a real file returns index.html.
    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        # Never swallow API/docs routes (they're matched earlier anyway).
        if full_path.startswith(("api/", "health", "docs", "openapi.json", "redoc")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (_STATIC_DIR / full_path).resolve()
        # Serve the real asset if it exists and is inside the static dir.
        if full_path and candidate.is_file() and _STATIC_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
else:
    log.info("No frontend build found — running API-only (dev mode).")
