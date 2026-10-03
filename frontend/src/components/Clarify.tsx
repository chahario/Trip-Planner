import type { ClarifyingQuestion } from "../lib/types";

interface Props {
  questions: ClarifyingQuestion[];
}

export default function Clarify({ questions }: Props) {
  if (questions.length === 0) return null;
  return (
    <div className="card clarify">
      <h3>A couple of things would sharpen this</h3>
      <p className="hint">I planned with sensible defaults, but answering these and re-running gives a better fit:</p>
      <ul>
        {questions.map((q) => (
          <li key={q.field}>{q.question}</li>
        ))}
      </ul>
    </div>
  );
}
