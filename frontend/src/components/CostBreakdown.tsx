import type { CostBreakdown as CB } from "../lib/types";

interface Props {
  cost: CB;
  days: number;
}

const fmt = (n?: number | null) => (n != null ? `₹${n.toLocaleString("en-IN")}` : "—");

/**
 * Transparent, itemized trip cost (per person). Flights + stay + food + local
 * transport are AI estimates; "Activities" is the exact sum of the plan's
 * per-stop costs. The total is the sum of the parts — the cumulative cost.
 */
const MODE_ICON: Record<string, string> = {
  flight: "✈️", train: "🚆", bus: "🚌", car: "🚗", ferry: "⛴️",
};

export default function CostBreakdown({ cost, days }: Props) {
  const mode = cost.arrival_mode || "flight";
  const arrivalLabel =
    mode === "flight" ? "Flights" : `${mode.charAt(0).toUpperCase()}${mode.slice(1)} (getting there)`;
  const rows: { icon: string; label: string; value?: number | null; hint?: string }[] = [
    { icon: MODE_ICON[mode] || "✈️", label: arrivalLabel, value: cost.flights, hint: "round-trip" },
    { icon: "🏨", label: "Stay", value: cost.accommodation, hint: `${Math.max(1, days - 1)} night(s)` },
    { icon: "🍽️", label: "Food", value: cost.food },
    { icon: "🚕", label: "Local transport", value: cost.local_transport },
    { icon: "🎟️", label: "Activities", value: cost.activities, hint: "sum of stops" },
  ];

  return (
    <div className="cost-card">
      <div className="cost-card-head">
        <h3>💳 Trip cost (per person)</h3>
        <span className="cost-total">{fmt(cost.total)}</span>
      </div>
      <ul className="cost-rows">
        {rows.map((r) => (
          <li key={r.label} className="cost-row">
            <span className="cost-label">
              <span className="cost-ico">{r.icon}</span>
              {r.label}
              {r.hint && <span className="cost-hint">({r.hint})</span>}
            </span>
            <span className="cost-val">{fmt(r.value)}</span>
          </li>
        ))}
        <li className="cost-row cost-row-total">
          <span className="cost-label">Cumulative total</span>
          <span className="cost-val">{fmt(cost.total)}</span>
        </li>
      </ul>
      <p className="cost-foot">
        {cost.note ? `${cost.note} ` : ""}Estimates for planning — confirm live prices when booking.
      </p>
    </div>
  );
}
