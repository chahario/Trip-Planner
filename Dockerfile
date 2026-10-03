# ===========================================================================
# Trip Planner — single-service production image.
# Builds the React frontend, then serves it FROM the FastAPI backend, so one
# container exposes both the API and the web app at the same origin (no CORS,
# one free host). Works on Render, Railway, Fly.io, Hugging Face Spaces, etc.
#
#   docker build -t trip-planner .
#   docker run -p 8000:8000 -e LLM_API_KEY=sk-... trip-planner
#   open http://localhost:8000
# ===========================================================================

# ---- Stage 1: build the SPA ----
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# Same-origin in production, so no API base URL needs baking in.
RUN npm run build

# ---- Stage 2: Python backend that also serves the built SPA ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/app ./app
# The built SPA lands next to the app; main.py auto-detects ./static.
COPY --from=web /web/dist ./static

# Hosts (Render/Railway/Fly) inject $PORT; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
