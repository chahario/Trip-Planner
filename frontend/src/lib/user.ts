// A lightweight per-user identity: an opaque token kept in localStorage and
// sent to the backend as X-User-Token. No login, but the same token reused on
// another device syncs the same saved plans. Users can view/replace it in the
// UI to move their library between devices.

const KEY = "tp_user_token";

function randomToken(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

export function getUserToken(): string {
  let t = "";
  try {
    t = localStorage.getItem(KEY) ?? "";
  } catch {
    /* storage blocked (private mode) — fall back to an in-memory token */
  }
  if (!t) {
    t = randomToken();
    try {
      localStorage.setItem(KEY, t);
    } catch {
      /* ignore */
    }
  }
  return t;
}

export function setUserToken(token: string): void {
  const t = token.trim();
  if (!t) return;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* ignore */
  }
}
