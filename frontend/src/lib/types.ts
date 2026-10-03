// Mirrors the backend Pydantic models.

export interface PlanRequest {
  city: string;
  origin?: string | null;
  budget?: number | null;
  available_time?: string | null;
  mood?: string | null;
  interests: string[];
  constraints: string[];
  days?: number;
  start_date?: string | null;
  free_text?: string | null;
  clarifications?: string | null;
  // Travel-style weights used by the resolver (beach/culture/food/pace/tier).
  style_weights?: Record<string, number | string> | null;
  // Explicit cities the user named (skip discovery).
  named_places?: string[];
}

export interface Place {
  name: string;
  category: string;
  kind: "activity" | "food";
  lat?: number | null;
  lon?: number | null;
  est_cost: number;
  tags: string[];
  source: "openstreetmap" | "curated" | "foursquare" | "ai";
  address?: string | null;
  rating?: number | null;
  image_url?: string | null;
  // Which city/stop this place belongs to (multi-city trips).
  city?: string | null;
}

export interface TravelLeg {
  from_name: string;
  to_name: string;
  distance_km: number;
  duration_min: number;
  mode: string;
}

export interface TransportOption {
  mode: string; // flight | train | bus | car | ferry
  duration?: string | null;
  cost_inr?: number | null;
  available: boolean;
  recommended: boolean;
  note?: string | null;
}

export interface CostBreakdown {
  currency: string;
  flights?: number | null;
  arrival_mode?: string | null;
  inter_city?: number | null;
  accommodation?: number | null;
  food?: number | null;
  local_transport?: number | null;
  activities?: number | null;
  total?: number | null;
  per: string;
  note?: string | null;
}

export interface Weather {
  date: string;
  temp_max_c?: number | null;
  temp_min_c?: number | null;
  precipitation_chance?: number | null;
  description: string;
  emoji: string;
  advisory?: string | null;
  // Which city this outlook is for (multi-city trips).
  city?: string | null;
  // Which trip day this outlook is for (day-by-day weather).
  day?: number | null;
}

export interface Hotel {
  name: string;
  price?: number | null;
  currency: string;
  rating?: string | null;
  address?: string | null;
  booking_url?: string | null;
}

export interface HotelsResponse {
  enabled: boolean;
  hotels: Hotel[];
  note?: string | null;
}

export interface PlanItem {
  order: number;
  time_slot: string;
  title: string;
  place: Place;
  duration_hours: number;
  est_cost: number;
  why_it_fits: string;
  tradeoff?: string | null;
  day?: number;
  // Which city this stop is in (multi-city trips).
  city?: string | null;
  // What to try here, what to avoid, and how to reach the next stop (+ cost).
  tips_try?: string | null;
  tips_avoid?: string | null;
  commute_next?: string | null;
}

// ---------------------------------------------------------------------------
// Multi-city route (produced by the DestinationResolver)
// ---------------------------------------------------------------------------

export interface RouteStop {
  city: string;
  country?: string | null;
  nights: number;
  order: number;
  lat?: number | null;
  lon?: number | null;
  why_city?: string | null;
  why_nights?: string | null;
  source: "wikivoyage" | "user" | "geocode" | "single" | "ai";
  poi_count: number;
  day_start: number;
  day_end: number;
}

export interface IntercityLeg {
  from_city: string;
  to_city: string;
  mode: string; // flight | train | bus | car | ferry
  duration?: string | null;
  cost_inr?: number | null;
  note?: string | null;
}

export interface UnusedPlace {
  name: string;
  blurb?: string | null;
  reason_skipped?: string | null;
  lat?: number | null;
  lon?: number | null;
}

export interface Route {
  destination: string;
  is_country: boolean;
  total_days: number;
  stops: RouteStop[];
  legs: IntercityLeg[];
  reasoning?: string | null;
  unused: UnusedPlace[];
  tradeoffs?: string | null;
  notes: string[];
}

export interface Plan {
  city: string;
  items: PlanItem[];
  total_cost: number;
  total_hours: number;
  within_budget: boolean;
  summary: string;
  is_fallback: boolean;
  legs?: TravelLeg[];
  days?: number;
  best_time_to_visit?: string | null;
  approx_cost?: string | null;
  tips?: string | null;
  cost_breakdown?: CostBreakdown | null;
  hotels?: RecommendedHotel[];
  arrival_transport?: TransportOption[];
  // Multi-city route (filled by the resolver).
  route?: Route | null;
  route_reasoning?: string | null;
  // What each city on the trip is famous for (highlights agent).
  highlights?: CityHighlights[];
}

export interface FamousActivity {
  title: string;
  kind: string; // activity | experience | nature | nightlife | shopping | landmark
  why?: string | null;
}

export interface SignatureFood {
  dish: string;
  why?: string | null;
  where?: string | null;
}

export interface CityHighlights {
  city: string;
  famous_for: FamousActivity[];
  signature_foods: SignatureFood[];
  note?: string | null;
  source: string;
}

export interface RecommendedHotel {
  name: string;
  area?: string | null;
  price_per_night?: number | null;
  rating?: string | null;
  why?: string | null;
  category?: string;
  image_url?: string | null;
  // Which city this hotel is in (multi-city trips).
  city?: string | null;
}

export interface TraceStep {
  step: number;
  tool: string;
  // "repair" = the agent caught a problem and fixed it (a trust signal, not an error).
  status: "start" | "ok" | "warn" | "error" | "repair";
  message: string;
  detail?: Record<string, unknown> | null;
}

export interface ClarifyingQuestion {
  field: string;
  question: string;
  placeholder?: string | null;
}

export interface ClarifyResponse {
  questions: ClarifyingQuestion[];
  source: string;
}

export interface PlanResponse {
  plan?: Plan | null;
  preferences?: Record<string, unknown> | null;
  trace: TraceStep[];
  clarifying_questions: ClarifyingQuestion[];
  warnings: string[];
  error?: string | null;
  weather?: Weather | null;
  // Per-city weather outlooks for multi-city trips.
  weather_by_city?: Weather[];
  // Day-by-day weather for the city you're in each day (needs a start date).
  weather_by_day?: Weather[];
}

export interface Usage {
  limit: number;
  used: number;
  remaining: number | null; // null = unlimited
  unlimited: boolean;
  window_hours: number;
  reset_seconds: number | null;
}

export interface SavedPlan {
  id: string;
  title: string;
  city: string;
  notes: string;
  plan: Plan;
  request?: PlanRequest | null;
  created_at: number;
  updated_at: number;
}