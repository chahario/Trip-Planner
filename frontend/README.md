# Frontend — Trip Planner (React + Vite)

React + TypeScript SPA. A structured/free-text form, a live agent-trace panel fed
by Server-Sent Events, and a plan view with per-stop reasoning and trade-offs.

## Quick start
```bash
npm install
npm run dev        # http://localhost:5173 (proxies /api to localhost:8000)
```

## Build
```bash
npm run build      # type-checks + outputs static site to dist/
npm run preview    # serve the production build locally
```

## Configuration
- **`VITE_API_BASE_URL`** — the deployed backend URL. Leave empty for local dev
  (Vite proxies to `localhost:8000`). Copy `.env.example` → `.env` to set it.

## Layout
```
src/
  App.tsx                 wires form → streaming agent → trace + plan
  lib/
    types.ts              TS mirror of the backend models
    api.ts                fetch + manual SSE parser (streamPlan / fetchPlan)
  components/
    PlannerForm.tsx       structured + free-text input
    TracePanel.tsx        live "agent is thinking" trace
    PlanView.tsx          itinerary, costs, why-it-fits, trade-offs, warnings
    Clarify.tsx           clarifying questions for vague input
  styles.css
```

## Deploy
Static host (Vercel/Netlify/any). Set `VITE_API_BASE_URL` to your backend URL.
`vercel.json` and `netlify.toml` are included, plus a Docker + nginx setup.
