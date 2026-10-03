import { useState } from "react";
import type { SavedPlan } from "../lib/types";

interface Props {
  plans: SavedPlan[];
  activeId: string | null;
  loading: boolean;
  userToken: string;
  onLoad: (plan: SavedPlan) => void;
  onDelete: (id: string) => void;
  onSetToken: (token: string) => void;
}

export default function SavedPlans({
  plans,
  activeId,
  loading,
  userToken,
  onLoad,
  onDelete,
  onSetToken,
}: Props) {
  const [showSync, setShowSync] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(userToken);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="card saved">
      <div className="section-head">
        <h3>🕑 Your trips</h3>
        <span className="hint">{plans.length} in history</span>
      </div>

      {loading ? (
        <p className="hint">Loading…</p>
      ) : plans.length === 0 ? (
        <p className="hint saved-empty">
          No trips yet. Every trip you plan is saved here automatically — open one
          anytime, or hit <strong>Save</strong> to add your own title and notes.
        </p>
      ) : (
        <ul className="saved-list">
          {plans.map((p) => (
            <li key={p.id} className={`saved-item ${p.id === activeId ? "active" : ""}`}>
              <button type="button" className="saved-main" onClick={() => onLoad(p)}>
                <span className="saved-title">{p.title}</span>
                <span className="saved-meta">
                  {p.city} · {new Date(p.updated_at * 1000).toLocaleDateString()}
                  {p.notes ? " · 📝 notes" : ""}
                </span>
              </button>
              <button
                type="button"
                className="saved-del"
                title="Delete"
                onClick={() => onDelete(p.id)}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      <button type="button" className="sync-toggle" onClick={() => setShowSync((v) => !v)}>
        {showSync ? "Hide sync code" : "🔗 Sync to another device"}
      </button>

      {showSync && (
        <div className="sync-box">
          <p className="hint">
            Your plans are tied to this code. Copy it, then paste it on another device to see the same library.
          </p>
          <div className="sync-row">
            <input readOnly value={userToken} className="sync-code" onFocus={(e) => e.target.select()} />
            <button type="button" className="ghost" onClick={copy}>
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
          <div className="sync-row">
            <input
              value={tokenInput}
              placeholder="Paste a code to switch"
              onChange={(e) => setTokenInput(e.target.value)}
            />
            <button
              type="button"
              className="ghost"
              disabled={!tokenInput.trim()}
              onClick={() => {
                onSetToken(tokenInput.trim());
                setTokenInput("");
              }}
            >
              Use
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
