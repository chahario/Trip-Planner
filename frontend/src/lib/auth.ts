// Lightweight account auth. On success we store {token, email} in localStorage;
// the token is sent as X-Auth-Token so saved plans/history belong to the account
// (and sync across devices on login). Guests still work via the browser token.

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const KEY = "tp_auth";

export interface Auth {
  token: string;
  email: string;
}

export function getAuth(): Auth | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Auth) : null;
  } catch {
    return null;
  }
}

export function setAuth(a: Auth): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(a));
  } catch {
    /* ignore */
  }
}

export function clearAuth(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export function authToken(): string {
  return getAuth()?.token ?? "";
}

async function post(path: string, body: unknown): Promise<Auth> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j.detail) detail = typeof j.detail === "string" ? j.detail : detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const data = (await res.json()) as Auth;
  setAuth(data);
  return data;
}

export function signup(email: string, password: string): Promise<Auth> {
  return post("/api/auth/signup", { email, password });
}

export function login(email: string, password: string): Promise<Auth> {
  return post("/api/auth/login", { email, password });
}

export async function logout(): Promise<void> {
  const t = authToken();
  clearAuth();
  if (!t) return;
  try {
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: "POST",
      headers: { "X-Auth-Token": t },
    });
  } catch {
    /* ignore */
  }
}
