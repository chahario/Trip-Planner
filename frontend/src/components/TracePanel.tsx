import { useState } from "react";
import type { TraceStep } from "../lib/types";

const ICON: Record<TraceStep["status"], string> = {
  start: "◌",
  ok: "✓",
  warn: "!",
  error: "✕",
  repair: "🔧",
};

// Human-friendly label for each status (used for the a11y title).
const LABEL: Record<TraceStep["status"], string> = {
  start: "in progress",
  ok: "done",
  warn: "note",
  error: "failed",
  repair: "fixed automatically",
};

interface Props {
  trace: TraceStep[];
  running: boolean;
}

/**
 * The agent's work is hidden by default — the user sees only a compact live
 * status of what it's doing right now, and can click to reveal the full trace.
 *
 * `repair` steps are shown as a positive signal (the agent caught and fixed a
 * problem), not as an error — this is what makes the agent feel trustworthy.
 */
export default function TracePanel({ trace, running }: Props) {
  const [open, setOpen] = useState(false);
  if (trace.length === 0 && !running) return null;

  const latest = trace[trace.length - 1];
  const repairCount = trace.filter((s) => s.status === "repair").length;

  return (
    <div className="card trace">
      <button
        type="button"
        className="trace-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="trace-toggle-left">
          <span className={`trace-dot ${running ? "live" : "done"}`} />
          <strong>{running ? "Agent is working…" : "Agent work"}</strong>
          {!open && latest && <span className="trace-now">{latest.message}</span>}
        </span>
        <span className="trace-toggle-right">
          {repairCount > 0 && (
            <span className="trace-repairs" title="The agent caught and fixed problems">
              🔧 {repairCount} auto-fix{repairCount === 1 ? "" : "es"}
            </span>
          )}
          <span className="trace-count">
            {trace.length} step{trace.length === 1 ? "" : "s"}
          </span>
          <span className={`chev ${open ? "up" : ""}`}>⌄</span>
        </span>
      </button>

      {open && (
        <ol className="trace-list">
          {trace.map((s) => (
            <li
              key={s.step}
              className={`trace-step ${s.status}`}
              title={LABEL[s.status]}
            >
              <span className="trace-icon">{ICON[s.status]}</span>
              <span className="trace-tool">{s.tool}</span>
              <span className="trace-msg">{s.message}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}