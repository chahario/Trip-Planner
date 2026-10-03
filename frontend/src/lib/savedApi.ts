import type { Plan, PlanRequest, SavedPlan } from "./types";
import { getUserToken } from "./user";
import { authToken } from "./auth";

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

function headers(): HeadersInit {
  const h: Record<string, string> = {
    "Content-Type": "application/json",
    "X-User-Token": getUserToken(),
  };
  const t = authToken();
  if (t) h["X-Auth-Token"] = t; // logged in → plans belong to the account
  return h;
}

export async function listSavedPlans(): Promise<SavedPlan[]> {
  const res = await fetch(`${API_BASE}/api/plans`, { headers: headers() });
  if (!res.ok) throw new Error(`Couldn't load saved plans (${res.status})`);
  return res.json();
}

export async function savePlan(input: {
  title?: string;
  notes?: string;
  plan: Plan;
  request?: PlanRequest | null;
}): Promise<SavedPlan> {
  const res = await fetch(`${API_BASE}/api/plans`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      title: input.title ?? null,
      notes: input.notes ?? "",
      plan: input.plan,
      request: input.request ?? null,
    }),
  });
  if (!res.ok) throw new Error(`Couldn't save plan (${res.status})`);
  return res.json();
}

export async function updateSavedPlan(
  id: string,
  patch: { title?: string; notes?: string }
): Promise<SavedPlan> {
  const res = await fetch(`${API_BASE}/api/plans/${id}`, {
    method: "PATCH",
    headers: headers(),
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`Couldn't update plan (${res.status})`);
  return res.json();
}

export async function deleteSavedPlan(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/plans/${id}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!res.ok && res.status !== 204) {
    throw new Error(`Couldn't delete plan (${res.status})`);
  }
}
