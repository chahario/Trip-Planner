import type {
  PlanRequest,
  PlanResponse,
  TraceStep,
  Plan,
  HotelsResponse,
  ClarifyResponse,
  Usage,
} from "./types";
import { getUserToken } from "./user";
import { authToken } from "./auth";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

/** Identify the user (account if logged in, else guest token) on every call. */
function userHeaders(): Record<string, string> {
  const h: Record<string, string> = { "X-User-Token": getUserToken() };
  const t = authToken();
  if (t) h["X-Auth-Token"] = t;
  return h;
}

/** Pull a human-readable error (FastAPI `detail`) out of a failed response. */
async function errorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const j = await res.json();
    if (j && typeof j.detail === "string") return j.detail;
  } catch {
    /* ignore */
  }
  return fallback;
}

/** Current trip-quota usage (N of 10 left, reset countdown). */
export async function fetchUsage(): Promise<Usage | null> {
  try {
    const res = await fetch(`${API_BASE}/api/usage`, { headers: userHeaders() });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

/** Ask-first: get the agent's clarifying questions for this request. */
export async function fetchClarify(req: PlanRequest): Promise<ClarifyResponse> {
  try {
    const res = await fetch(`${API_BASE}/api/clarify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });
    if (!res.ok) return { questions: [], source: "none" };
    return res.json();
  } catch {
    return { questions: [], source: "none" };
  }
}

/** Real hotels near the plan (Foursquare/Amadeus) when configured; else enabled=false. */
export async function fetchHotels(
  city: string,
  lat?: number | null,
  lon?: number | null
): Promise<HotelsResponse> {
  try {
    const params = new URLSearchParams({ city });
    if (lat != null && lon != null) {
      params.set("lat", String(lat));
      params.set("lon", String(lon));
    }
    const res = await fetch(`${API_BASE}/api/hotels?${params.toString()}`);
    if (!res.ok) return { enabled: false, hotels: [] };
    return res.json();
  } catch {
    return { enabled: false, hotels: [] };
  }
}

/** Non-streaming call: returns the whole response at once. */
export async function fetchPlan(req: PlanRequest): Promise<PlanResponse> {
  const res = await fetch(`${API_BASE}/api/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...userHeaders() },
    body: JSON.stringify(req),
  });
  if (!res.ok) {
    throw new Error(await errorDetail(res, `Server error ${res.status}`));
  }
  return res.json();
}

interface StreamHandlers {
  onTrace: (step: TraceStep) => void;
  onPlan: (resp: PlanResponse) => void;
  onError: (message: string) => void;
}

/**
 * Streaming call: reads the SSE response and fires handlers as events arrive,
 * so the UI can show the agent "thinking" live. Uses fetch + a manual SSE
 * parser (EventSource can't POST a body).
 */
export async function streamPlan(
  req: PlanRequest,
  handlers: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/plan/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...userHeaders() },
      body: JSON.stringify(req),
      signal,
    });
  } catch (e) {
    handlers.onError(
      "Couldn't reach the planner service. Check that the backend is running."
    );
    return;
  }

  if (!res.ok || !res.body) {
    // 429 = over the trip rate limit; surface the server's friendly message.
    const fallback =
      res.status === 429
        ? "You've reached your trip limit for now. Try again later."
        : `Server error ${res.status}`;
    handlers.onError(await errorDetail(res, fallback));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line.
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        parseFrame(frame, handlers);
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      handlers.onError("Connection interrupted while planning.");
    }
  }
}

function parseFrame(frame: string, handlers: StreamHandlers): void {
  let event = "message";
  let data = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return;
  try {
    const parsed = JSON.parse(data);
    if (event === "trace") handlers.onTrace(parsed as TraceStep);
    else if (event === "plan") handlers.onPlan(parsed as PlanResponse);
    else if (event === "error") handlers.onError((parsed as { error: string }).error);
  } catch {
    /* ignore malformed frame */
  }
}

export type { Plan };
