import { useEffect, useState } from "react";
import type { Plan, PlanItem, RouteStop, IntercityLeg, Weather } from "../lib/types";
import { placeImage } from "../lib/photos";
import CostBreakdown from "./CostBreakdown";
import GettingThere from "./GettingThere";
import CityHighlights from "./CityHighlights";

function sourceLabel(source: string): string {
  if (source === "openstreetmap") return "OpenStreetMap";
  if (source === "foursquare") return "Foursquare";
  if (source === "ai") return "✨ AI pick";
  return "curated";
}

const LEG_ICON: Record<string, string> = {
  flight: "✈️",
  train: "🚆",
  bus: "🚌",
  car: "🚗",
  ferry: "⛴️",
};

interface Props {
  plan: Plan;
  warnings: string[];
  weatherByDay?: Weather[];
  origin?: string;
}

export default function PlanView({ plan, warnings, weatherByDay = [], origin }: Props) {
  const weatherForDay = (d: number): Weather | undefined =>
    weatherByDay.find((w) => w.day === d);
  const route = plan.route ?? null;
  const multiCity = !!route && route.is_country && route.stops.length > 1;
  const multiDay = (plan.days ?? 1) > 1;
  const hasAI = plan.items.some((i) => i.place.source === "ai");

  // Selectable arrival transport: picking a mode re-prices the trip.
  const arrival = plan.arrival_transport ?? [];
  const initialMode =
    plan.cost_breakdown?.arrival_mode ||
    arrival.find((o) => o.recommended)?.mode ||
    arrival.find((o) => o.available && o.cost_inr != null)?.mode;
  const [selectedMode, setSelectedMode] = useState<string | undefined>(initialMode);
  useEffect(() => {
    setSelectedMode(initialMode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan]);

  // Recompute the cost breakdown for the selected mode (round-trip).
  const effectiveCost = (() => {
    const cb = plan.cost_breakdown;
    if (!cb) return cb;
    const opt = arrival.find((o) => o.mode === selectedMode && o.available && o.cost_inr != null);
    if (!opt) return cb;
    const flights = (opt.cost_inr as number) * 2;
    const total = [flights, cb.accommodation, cb.food, cb.local_transport, cb.activities]
      .reduce((s: number, v) => s + (v ?? 0), 0);
    return { ...cb, flights, arrival_mode: opt.mode, total };
  })();

  // Group items by day, preserving global index (for leg lookup).
  const indexed = plan.items.map((item, idx) => ({ item, idx }));
  const byDay = new Map<number, { item: PlanItem; idx: number }[]>();
  for (const entry of indexed) {
    const d = entry.item.day ?? 1;
    if (!byDay.has(d)) byDay.set(d, []);
    byDay.get(d)!.push(entry);
  }
  const dayNumbers = [...byDay.keys()].sort((a, b) => a - b);

  const [activeDay, setActiveDay] = useState(dayNumbers[0] ?? 1);
  useEffect(() => {
    setActiveDay(dayNumbers[0] ?? 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plan]);

  const dayCost = (d: number) =>
    (byDay.get(d) || []).reduce((s, e) => s + e.item.est_cost, 0);

  // City for a given day: prefer the route, fall back to the item's own city/address.
  const cityForDay = (d: number): string => {
    if (route) {
      const stop = route.stops.find((s) => d >= s.day_start && d <= s.day_end);
      if (stop) return stop.city;
    }
    const first = byDay.get(d)?.[0]?.item;
    return (
      first?.city ||
      first?.place.city ||
      first?.place.address?.split(",").slice(-1)[0]?.trim() ||
      ""
    );
  };

  // The leg that departs AFTER a given city (for the route strip).
  const legAfterCity = (city: string): IntercityLeg | undefined =>
    route?.legs.find((l) => l.from_city === city);

  // What a given city is famous for (highlights agent).
  const highlightsFor = (city: string) =>
    plan.highlights?.find(
      (h) => h.city.trim().toLowerCase() === city.trim().toLowerCase()
    );

  // Recommended stays in a given city (falls back to hotels with no city set).
  const staysFor = (city: string) => {
    const hotels = plan.hotels ?? [];
    const c = city.trim().toLowerCase();
    const inCity = hotels.filter((h) => (h.city || "").trim().toLowerCase() === c);
    // Single-city trips: hotels may carry no city — show them anyway.
    if (inCity.length === 0 && !hotels.some((h) => h.city)) return hotels;
    return inCity;
  };

  const renderStop = ({ item, idx }: { item: PlanItem; idx: number }) => {
    const leg = plan.legs?.[idx];
    const isLast = idx === plan.items.length - 1;
    const slot = item.time_slot.replace(/^Day\s*\d+\s*·\s*/, "");
    const isFlex = item.place.category === "walk" && /explore/i.test(item.title);
    const verifyCity = item.city || item.place.city || plan.city;
    return (
      <li key={item.order} className="timeline-item">
        <div className="timeline-time">{slot}</div>
        <div className="timeline-body">
          {!isFlex && (
            <div className="stop-photo-wrap">
              <img
                className="stop-photo"
                src={item.place.image_url || placeImage(item.place.category)}
                alt={item.place.category.replace("_", " ")}
                loading="lazy"
                onError={(e) => {
                  const img = e.currentTarget;
                  const fb = placeImage(item.place.category);
                  if (img.src !== fb) img.src = fb;
                }}
              />
              <span className="stop-photo-cat">
                {item.place.category.replace("_", " ")}
              </span>
            </div>
          )}
          <div className="timeline-title-row">
            <h4>{item.title}</h4>
            <span className="pill">
              {item.est_cost === 0 ? "free" : `₹${item.est_cost}`}
            </span>
          </div>
          <div className="meta">
            <span className="tag">{item.place.category.replace("_", " ")}</span>
            <span className="tag">{item.duration_hours}h</span>
            {!isFlex && (
              <span className={`tag src-${item.place.source}`}>
                {sourceLabel(item.place.source)}
              </span>
            )}
            {isFlex && <span className="tag src-curated">flex day</span>}
            {item.place.rating != null && (
              <span className="tag rating">★ {item.place.rating.toFixed(1)}</span>
            )}
            {item.place.address && <span className="addr">{item.place.address}</span>}
          </div>
          <p className="why">
            <strong>Why this fits:</strong> {item.why_it_fits}
          </p>
          {item.tradeoff && (
            <p className="tradeoff">
              <strong>Trade-off:</strong> {item.tradeoff}
            </p>
          )}
          {(item.tips_try || item.tips_avoid) && (
            <div className="stop-tips">
              {item.tips_try && (
                <p className="tip-try">
                  <strong>✅ Try:</strong> {item.tips_try}
                </p>
              )}
              {item.tips_avoid && (
                <p className="tip-avoid">
                  <strong>⚠️ Avoid:</strong> {item.tips_avoid}
                </p>
              )}
            </div>
          )}
          {item.place.source === "ai" && !isFlex && (
            <a
              className="verify-link"
              href={`https://www.google.com/maps/search/${encodeURIComponent(
                `${item.title} ${verifyCity}`
              )}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              🔎 Verify on Google Maps
            </a>
          )}
          {item.commute_next ? (
            <div className="leg commute">
              <span className="commute-ico">➡️</span> To next stop: {item.commute_next}
              {leg && <> · {leg.distance_km} km</>}
            </div>
          ) : (
            !isLast && leg && (
              <div className="leg">
                🚗 {leg.distance_km} km · ~{Math.round(leg.duration_min)} min to next stop
              </div>
            )
          )}
        </div>
      </li>
    );
  };

  const activeIdx = dayNumbers.indexOf(activeDay);
  const activeStop: RouteStop | undefined = route?.stops.find(
    (s) => activeDay >= s.day_start && activeDay <= s.day_end
  );

  return (
    <div className="card plan">
      <div className="plan-head">
        <h2>Your plan for {route?.destination || plan.city}</h2>
        {multiDay && <span className="badge days">{plan.days} days</span>}
        {multiCity && (
          <span className="badge cities">{route!.stops.length} cities</span>
        )}
        {plan.is_fallback && <span className="badge fallback">fallback plan</span>}
      </div>

      <p className="summary">{plan.summary}</p>

      {/* ---------- WHY THIS ROUTE (multi-city only) ---------- */}
      {multiCity && (
        <div className="route-card">
          <div className="route-card-head">
            <span className="route-ico">🧭</span>
            <h3>Why this route</h3>
          </div>
          {(plan.route_reasoning || route!.reasoning) && (
            <p className="route-reasoning">
              {plan.route_reasoning || route!.reasoning}
            </p>
          )}

          {/* Horizontal route strip: city (nights) → leg → city … */}
          <div className="route-strip">
            {[...route!.stops]
              .sort((a, b) => a.order - b.order)
              .map((stop, i, arr) => {
                const leg = legAfterCity(stop.city);
                const isActive =
                  activeDay >= stop.day_start && activeDay <= stop.day_end;
                return (
                  <div className="route-strip-seg" key={stop.city}>
                    <button
                      type="button"
                      className={`route-node ${isActive ? "active" : ""}`}
                      onClick={() => setActiveDay(stop.day_start)}
                      title={stop.why_city || ""}
                    >
                      <span className="route-node-city">{stop.city}</span>
                      <span className="route-node-nights">
                        {stop.nights}n · days {stop.day_start}–{stop.day_end}
                      </span>
                    </button>
                    {i < arr.length - 1 && (
                      <span className="route-leg" title={leg?.note || ""}>
                        <span className="route-leg-ico">
                          {LEG_ICON[leg?.mode || "bus"] || "→"}
                        </span>
                        {leg?.duration && (
                          <span className="route-leg-dur">{leg.duration}</span>
                        )}
                      </span>
                    )}
                  </div>
                );
              })}
          </div>

          {route!.tradeoffs && (
            <p className="route-tradeoffs">
              <strong>Adjusting the trip:</strong> {route!.tradeoffs}
            </p>
          )}
        </div>
      )}

      {(plan.best_time_to_visit || plan.tips) && (
        <div className="trip-guide">
          {plan.best_time_to_visit && (
            <div className="guide-item">
              <span className="guide-ico">📅</span>
              <div>
                <span className="guide-label">Best time to visit</span>
                <span className="guide-val">{plan.best_time_to_visit}</span>
              </div>
            </div>
          )}
          {plan.tips && (
            <div className="guide-item">
              <span className="guide-ico">💡</span>
              <div>
                <span className="guide-label">Tip</span>
                <span className="guide-val">{plan.tips}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {arrival.length > 0 && (
        <GettingThere
          options={arrival}
          origin={origin}
          destination={route?.destination || plan.city}
          selectedMode={selectedMode}
          onSelect={setSelectedMode}
        />
      )}

      {effectiveCost && (
        <CostBreakdown cost={effectiveCost} days={plan.days ?? 1} />
      )}

      <div className="stats">
        <div className="stat">
          <span className="stat-val">₹{plan.total_cost}</span>
          <span className="stat-label">spend at stops</span>
        </div>
        <div className="stat">
          <span className="stat-val">{plan.total_hours}h</span>
          <span className="stat-label">time at stops</span>
        </div>
        <div className="stat">
          <span className="stat-val">{plan.items.length}</span>
          <span className="stat-label">stops</span>
        </div>
        <div className="stat">
          <span className="stat-val">{plan.days ?? 1}</span>
          <span className="stat-label">{(plan.days ?? 1) > 1 ? "days" : "day"}</span>
        </div>
      </div>

      {hasAI && (
        <div className="ai-note">
          ✨ Some stops were suggested by AI (live map data was thin). They're real,
          well-known places — but double-check hours, prices and bookings with the{" "}
          <strong>Verify on Google Maps</strong> link on each stop.
        </div>
      )}

      {warnings.length > 0 && (
        <ul className="warnings">
          {warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      {multiDay ? (
        <>
          <div className="day-nav">
            <div className="day-strip">
              {dayNumbers.map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`day-pill ${d === activeDay ? "active" : ""}`}
                  onClick={() => setActiveDay(d)}
                >
                  <span className="day-pill-n">Day {d}</span>
                  <span className="day-pill-city">{cityForDay(d)}</span>
                  <span className="day-pill-sub">
                    {(byDay.get(d) || []).length} stops · ₹{dayCost(d)}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="day-view">
            <div className="day-view-head">
              <h3>
                Day {activeDay}
                {cityForDay(activeDay) ? ` · ${cityForDay(activeDay)}` : ""}
              </h3>
              <div className="day-pager">
                <button
                  type="button"
                  className="pager-btn"
                  disabled={activeIdx <= 0}
                  onClick={() => setActiveDay(dayNumbers[activeIdx - 1])}
                >
                  ← Prev
                </button>
                <span className="pager-count">
                  {activeIdx + 1} / {dayNumbers.length}
                </span>
                <button
                  type="button"
                  className="pager-btn"
                  disabled={activeIdx >= dayNumbers.length - 1}
                  onClick={() => setActiveDay(dayNumbers[activeIdx + 1])}
                >
                  Next →
                </button>
              </div>
            </div>

            {/* Day-by-day weather for the city you're in this day */}
            {(() => {
              const w = weatherForDay(activeDay);
              if (!w) return null;
              const hasForecast = w.temp_max_c != null;
              return (
                <div className={`day-weather ${hasForecast ? "" : "seasonal"}`}>
                  <span className="dw-emoji">{w.emoji}</span>
                  <span className="dw-text">
                    <strong>{w.date}</strong>
                    {w.city ? ` · ${w.city}` : ""} —{" "}
                    {hasForecast ? (
                      <>
                        {w.description}
                        {w.temp_max_c != null && <>, {Math.round(w.temp_max_c)}°</>}
                        {w.temp_min_c != null && <>/{Math.round(w.temp_min_c)}°C</>}
                        {w.precipitation_chance != null && <> · {w.precipitation_chance}% rain</>}
                      </>
                    ) : (
                      <>beyond the 16-day forecast — check seasonal guidance above</>
                    )}
                    {w.advisory && <span className="dw-advisory"> · {w.advisory}</span>}
                  </span>
                </div>
              );
            })()}

            {/* Per-city context for the active day */}
            {activeStop && (activeStop.why_nights || activeStop.why_city) && (
              <div className="day-city-note">
                {activeStop.why_nights || activeStop.why_city}
              </div>
            )}

            {/* Where you're staying tonight (the active day's city) */}
            {(() => {
              const c = cityForDay(activeDay);
              const stays = staysFor(c);
              if (!c || stays.length === 0) return null;
              const top = stays[0];
              return (
                <div className="day-stay">
                  <span className="day-stay-ico">🏨</span>
                  <span className="day-stay-text">
                    Staying in <strong>{c}</strong>: {top.name}
                    {top.price_per_night != null && (
                      <> · ₹{top.price_per_night.toLocaleString("en-IN")}/night</>
                    )}
                    {stays.length > 1 && (
                      <span className="day-stay-more"> · +{stays.length - 1} more below</span>
                    )}
                  </span>
                </div>
              );
            })()}

            {/* What the city you're in today is famous for */}
            {(() => {
              const h = highlightsFor(cityForDay(activeDay));
              return h ? <CityHighlights highlights={h} /> : null;
            })()}

            <ol className="timeline">
              {(byDay.get(activeDay) || []).map(renderStop)}
            </ol>
          </div>
        </>
      ) : (
        <>
          {(() => {
            const h = highlightsFor(cityForDay(dayNumbers[0] ?? 1));
            return h ? <CityHighlights highlights={h} /> : null;
          })()}
          <ol className="timeline">{indexed.map(renderStop)}</ol>
        </>
      )}

      {/* ---------- ALSO CONSIDERED (multi-city only) ---------- */}
      {multiCity && route!.unused.length > 0 && (
        <div className="also-considered">
          <h3>Also considered</h3>
          <p className="also-sub">
            These didn't fit {route!.total_days} days, but you could swap one in:
          </p>
          <div className="also-grid">
            {route!.unused.map((u) => (
              <div className="also-chip" key={u.name}>
                <span className="also-name">{u.name}</span>
                {u.blurb && <span className="also-blurb">{u.blurb}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}