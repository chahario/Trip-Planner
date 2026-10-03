import type { TransportOption } from "../lib/types";

interface Props {
  options: TransportOption[];
  origin?: string;
  destination: string;
  selectedMode?: string;
  onSelect?: (mode: string) => void;
}

const MODE_ICON: Record<string, string> = {
  flight: "✈️", train: "🚆", bus: "🚌", car: "🚗", ferry: "⛴️",
};

const fmt = (n?: number | null) => (n != null ? `₹${n.toLocaleString("en-IN")}` : "—");

/**
 * Round-trip travel: all realistic ways to reach the destination AND get back
 * to the starting city (flight only if it truly exists). Fares are shown one-way
 * and as a round-trip total — the same mode is assumed for the return leg.
 */
export default function GettingThere({ options, origin, destination, selectedMode, onSelect }: Props) {
  if (!options || options.length === 0) return null;
  // Recommended first, then available, then the rest.
  const sorted = [...options].sort((a, b) => {
    if (a.recommended !== b.recommended) return a.recommended ? -1 : 1;
    if (a.available !== b.available) return a.available ? -1 : 1;
    return (a.cost_inr ?? 1e9) - (b.cost_inr ?? 1e9);
  });

  return (
    <div className="card getting-there">
      <div className="section-head">
        <h3>🧭 Getting there &amp; back{origin ? `: ${origin} ⇄ ${destination}` : ""}</h3>
        <span className="hint">round trip, per person</span>
      </div>
      {origin && (
        <p className="gt-return">
          ↩️ Return from <strong>{destination}</strong> back to <strong>{origin}</strong> is planned
          by the same mode — the total below already covers both legs.
        </p>
      )}
      <p className="gt-foot">
        🔎 Checked against live web results — tap a mode to re-price your trip; confirm exact fares when booking.
      </p>
      <div className="gt-grid">
        {sorted.map((o, i) => {
          const selectable = o.available && o.cost_inr != null && !!onSelect;
          const isSelected = o.mode === selectedMode;
          const roundTrip = o.cost_inr != null ? o.cost_inr * 2 : null;
          return (
            <button
              key={i}
              type="button"
              disabled={!selectable}
              onClick={() => selectable && onSelect!(o.mode)}
              className={`gt-card ${isSelected ? "selected" : ""} ${o.recommended ? "rec" : ""} ${o.available ? "" : "unavail"} ${selectable ? "clickable" : ""}`}
            >
              <div className="gt-top">
                <span className="gt-mode">
                  <span className="gt-ico">{MODE_ICON[o.mode] || "•"}</span>
                  {o.mode.charAt(0).toUpperCase() + o.mode.slice(1)}
                </span>
                {isSelected ? (
                  <span className="gt-badge gt-sel">✓ Selected</span>
                ) : o.recommended ? (
                  <span className="gt-badge">Recommended</span>
                ) : !o.available ? (
                  <span className="gt-badge gt-na">Not available</span>
                ) : null}
              </div>
              <div className="gt-meta">
                {o.duration && <span className="gt-dur">⏱ {o.duration} each way</span>}
                {o.available && roundTrip != null && (
                  <span className="gt-cost">{fmt(roundTrip)}</span>
                )}
              </div>
              {o.available && o.cost_inr != null && (
                <div className="gt-legs">
                  <span className="gt-leg">{fmt(o.cost_inr)} there</span>
                  <span className="gt-leg">+ {fmt(o.cost_inr)} back</span>
                </div>
              )}
              {o.note && <p className="gt-note">{o.note}</p>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
