import { useState } from "react";
import type { PlanRequest } from "../lib/types";
import { detectCity } from "../lib/geo";

const INTEREST_OPTIONS = ["food", "music", "walks", "nature", "art", "shopping", "nightlife", "movies"];
const CONSTRAINT_OPTIONS = ["vegetarian", "vegan", "avoid crowded places", "budget-tight", "no alcohol"];

interface TripPreset {
  label: string;
  time: string;
  days: number;
}
const TRIP_PRESETS: TripPreset[] = [
  { label: "A few hours", time: "4 hours", days: 1 },
  { label: "Full day", time: "8 hours", days: 1 },
  { label: "Weekend", time: "2 days", days: 2 },
  { label: "3 days", time: "3 days", days: 3 },
];

interface Props {
  onSubmit: (req: PlanRequest) => void;
  loading: boolean;
  collapsed: boolean;
  onExpand: () => void;
  // A title/description for the collapsed bar, derived from the generated plan.
  title?: string;
  subtitle?: string;
}

export default function PlannerForm({ onSubmit, loading, collapsed, onExpand, title, subtitle }: Props) {
  const [mode, setMode] = useState<"structured" | "free">("structured");

  const [city, setCity] = useState("");
  const [origin, setOrigin] = useState("");
  const [locating, setLocating] = useState(false);
  const [locErr, setLocErr] = useState<string | null>(null);
  const [budget, setBudget] = useState("");
  const [time, setTime] = useState("");
  // Days is held as a string so the field can be cleared while typing; the
  // effective number defaults to 1 only when used (submit / labels).
  const [daysInput, setDaysInput] = useState("");
  const days = Math.max(1, Math.min(21, parseInt(daysInput, 10) || 1));
  const [startDate, setStartDate] = useState("");
  const [mood, setMood] = useState("");
  const [interests, setInterests] = useState<string[]>([]);
  const [constraints, setConstraints] = useState<string[]>([]);
  // Options can grow with user-added custom tags.
  const [interestOpts, setInterestOpts] = useState<string[]>(INTEREST_OPTIONS);
  const [constraintOpts, setConstraintOpts] = useState<string[]>(CONSTRAINT_OPTIONS);
  const [newInterest, setNewInterest] = useState("");
  const [newConstraint, setNewConstraint] = useState("");

  const [freeText, setFreeText] = useState("");
  const [freeCity, setFreeCity] = useState("");

  const toggle = (list: string[], set: (v: string[]) => void, value: string) => {
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  };

  const addCustom = (
    raw: string,
    opts: string[],
    setOpts: (v: string[]) => void,
    selected: string[],
    setSelected: (v: string[]) => void,
    clear: () => void
  ) => {
    const v = raw.trim().toLowerCase();
    if (!v) return;
    if (!opts.includes(v)) setOpts([...opts, v]);
    if (!selected.includes(v)) setSelected([...selected, v]);
    clear();
  };

  const useMyLocation = async () => {
    setLocErr(null);
    setLocating(true);
    try {
      setOrigin(await detectCity());
    } catch (e) {
      setLocErr((e as Error).message || "Couldn't detect your location.");
    } finally {
      setLocating(false);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const org = origin.trim() || null;
    const sd = startDate || null;
    if (mode === "structured") {
      onSubmit({
        city: city.trim(),
        origin: org,
        budget: budget ? Number(budget) : null,
        available_time: time || null,
        mood: mood || null,
        interests,
        constraints,
        days,
        start_date: sd,
      });
    } else {
      onSubmit({
        city: freeCity.trim() || "Bangalore",
        origin: org,
        interests: [],
        constraints: [],
        free_text: freeText,
        start_date: sd,
      });
    }
  };

  const originField = (
    <label>
      Starting from (for flights &amp; cost)
      <div className="origin-row">
        <input value={origin} onChange={(e) => setOrigin(e.target.value)} placeholder="your city — optional" />
        <button type="button" className="loc-btn" onClick={useMyLocation} disabled={locating}>
          {locating ? "…" : "📍 Use my location"}
        </button>
      </div>
      {locErr && <span className="loc-err">{locErr}</span>}
    </label>
  );

  // Collapsed state: a compact summary with an Edit button. Prefer the plan-
  // derived title/subtitle; fall back to the form fields only if absent.
  if (collapsed) {
    const fallback =
      mode === "structured"
        ? [city, time, interests.slice(0, 3).join(", ")].filter(Boolean).join(" · ") || "Your trip"
        : freeCity || "Your trip";
    const mainTitle = title || fallback;
    return (
      <div className="card form-collapsed">
        <div className="fc-left">
          <span className="fc-label">Your trip</span>
          <span className="fc-summary">{mainTitle}</span>
          {subtitle && <span className="fc-sub">{subtitle}</span>}
        </div>
        <button type="button" className="ghost" onClick={onExpand}>
          ✎ Edit
        </button>
      </div>
    );
  }

  return (
    <form className="card form" onSubmit={submit}>
      <div className="mode-toggle">
        <button type="button" className={mode === "structured" ? "active" : ""} onClick={() => setMode("structured")}>
          Structured
        </button>
        <button type="button" className={mode === "free" ? "active" : ""} onClick={() => setMode("free")}>
          Free text
        </button>
      </div>

      {mode === "structured" ? (
        <>
          <label>
            City / destination
            <input value={city} onChange={(e) => setCity(e.target.value)} placeholder="Bangalore" required />
          </label>

          {originField}

          <fieldset>
            <legend>Trip length</legend>
            <div className="chips">
              {TRIP_PRESETS.map((p) => (
                <button
                  type="button"
                  key={p.label}
                  className={`chip ${time === p.time && days === p.days ? "on" : ""}`}
                  onClick={() => {
                    setTime(p.time);
                    setDaysInput(String(p.days));
                  }}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </fieldset>

          <div className="row">
            <label>
              Number of days
              <input
                type="number"
                min="1"
                max="21"
                value={daysInput}
                placeholder="1"
                onChange={(e) => {
                  const raw = e.target.value;
                  setDaysInput(raw); // allow empty / partial while typing
                  const n = parseInt(raw, 10);
                  if (!isNaN(n) && n > 3) setTime(`${n} days`);
                }}
                onBlur={(e) => {
                  // Clamp to 1–21 on blur (but allow empty during typing).
                  const n = parseInt(e.target.value, 10);
                  if (!isNaN(n)) setDaysInput(String(Math.max(1, Math.min(21, n))));
                }}
              />
            </label>
            <label>
              Start date <span className="field-hint">(for day-by-day weather)</span>
              <input
                type="date"
                value={startDate}
                min={new Date().toISOString().slice(0, 10)}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </label>
          </div>

          <div className="row">
            <label>
              Budget (₹){days > 1 ? " / day" : ""}
              <input type="number" min="0" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="2000" />
            </label>
            <label>
              Time / duration
              <input value={time} onChange={(e) => setTime(e.target.value)} placeholder="4 hours" />
            </label>
          </div>

          <label>
            Mood
            <input value={mood} onChange={(e) => setMood(e.target.value)} placeholder="tired but wants to do something fun" />
          </label>

          <fieldset>
            <legend>Interests</legend>
            <div className="chips">
              {interestOpts.map((opt) => (
                <button type="button" key={opt} className={`chip ${interests.includes(opt) ? "on" : ""}`} onClick={() => toggle(interests, setInterests, opt)}>
                  {opt}
                </button>
              ))}
            </div>
            <div className="chip-add">
              <input
                value={newInterest}
                placeholder="add your own…"
                onChange={(e) => setNewInterest(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addCustom(newInterest, interestOpts, setInterestOpts, interests, setInterests, () => setNewInterest(""));
                  }
                }}
              />
              <button
                type="button"
                className="chip-add-btn"
                onClick={() => addCustom(newInterest, interestOpts, setInterestOpts, interests, setInterests, () => setNewInterest(""))}
              >
                + Add
              </button>
            </div>
          </fieldset>

          <fieldset>
            <legend>Constraints</legend>
            <div className="chips">
              {constraintOpts.map((opt) => (
                <button type="button" key={opt} className={`chip ${constraints.includes(opt) ? "on" : ""}`} onClick={() => toggle(constraints, setConstraints, opt)}>
                  {opt}
                </button>
              ))}
            </div>
            <div className="chip-add">
              <input
                value={newConstraint}
                placeholder="add your own…"
                onChange={(e) => setNewConstraint(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addCustom(newConstraint, constraintOpts, setConstraintOpts, constraints, setConstraints, () => setNewConstraint(""));
                  }
                }}
              />
              <button
                type="button"
                className="chip-add-btn"
                onClick={() => addCustom(newConstraint, constraintOpts, setConstraintOpts, constraints, setConstraints, () => setNewConstraint(""))}
              >
                + Add
              </button>
            </div>
          </fieldset>
        </>
      ) : (
        <>
          <label>
            City / destination
            <input value={freeCity} onChange={(e) => setFreeCity(e.target.value)} placeholder="Bangalore" required />
          </label>
          {originField}
          <label>
            Tell me about your ideal trip
            <textarea
              rows={5}
              value={freeText}
              placeholder="e.g. A relaxed 3-day trip to Goa for a couple, mid-range budget, love food and beaches…"
              onChange={(e) => setFreeText(e.target.value)}
            />
          </label>
        </>
      )}

      <button className="primary" type="submit" disabled={loading}>
        {loading ? "Planning…" : "✨ Plan my trip"}
      </button>
    </form>
  );
}
