import { useState } from "react";
import type { ClarifyingQuestion } from "../lib/types";

interface Props {
  questions: ClarifyingQuestion[];
  loading: boolean;
  source: string;
  onSubmit: (clarifications: string) => void;
  onSkip: () => void;
}

/**
 * Ask-first: before planning, the agent asks a few clarifying questions. The
 * user's answers are folded into the plan for a sharper, more grounded result.
 */
export default function ClarifyPanel({ questions, loading, source, onSubmit, onSkip }: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({});

  if (loading) {
    return (
      <div className="card clarify-panel">
        <h3>🤔 Thinking of a few questions…</h3>
        <p className="hint">The agent is working out what it needs to know to plan well.</p>
      </div>
    );
  }

  const answered = Object.values(answers).filter((v) => v.trim()).length;

  const build = () =>
    questions
      .map((q) => {
        const a = (answers[q.field] || "").trim();
        return a ? `Q: ${q.question}\nA: ${a}` : null;
      })
      .filter(Boolean)
      .join("\n");

  return (
    <div className="card clarify-panel">
      <div className="section-head">
        <h3>🤔 A few quick questions</h3>
        <span className="hint">{source === "ai" ? "asked by AI" : ""}</span>
      </div>
      <p className="hint clarify-intro">
        Answer what you can for a sharper plan — or skip and I'll use smart defaults.
      </p>

      <div className="clarify-qs">
        {questions.map((q) => (
          <label key={q.field} className="clarify-q">
            {q.question}
            <input
              value={answers[q.field] || ""}
              placeholder={q.placeholder || "your answer…"}
              onChange={(e) => setAnswers((p) => ({ ...p, [q.field]: e.target.value }))}
            />
          </label>
        ))}
      </div>

      <div className="clarify-actions">
        <button type="button" className="primary" onClick={() => onSubmit(build())}>
          {answered > 0 ? `Plan with my ${answered} answer${answered > 1 ? "s" : ""}` : "✨ Plan my trip"}
        </button>
        <button type="button" className="ghost" onClick={onSkip}>
          Skip
        </button>
      </div>
    </div>
  );
}
