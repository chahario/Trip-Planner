import { useState } from "react";

interface Props {
  city: string;
  savedId: string | null;
  initialTitle: string;
  initialNotes: string;
  saving: boolean;
  onSave: (title: string, notes: string) => void;
}

/**
 * Save the current plan with the user's own tips/notes, or update the notes of
 * an already-saved plan. Lives above the itinerary.
 */
export default function SavePlanBar({
  city,
  savedId,
  initialTitle,
  initialNotes,
  saving,
  onSave,
}: Props) {
  const [title, setTitle] = useState(initialTitle || `${city} plan`);
  const [notes, setNotes] = useState(initialNotes);
  const [openNotes, setOpenNotes] = useState(Boolean(initialNotes));

  return (
    <div className="save-bar">
      <div className="save-row">
        <input
          className="save-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Name this plan"
          aria-label="Plan name"
        />
        <button
          type="button"
          className="primary save-btn"
          disabled={saving}
          onClick={() => onSave(title.trim() || `${city} plan`, notes)}
        >
          {saving ? "Saving…" : savedId ? "Update plan" : "💾 Save plan"}
        </button>
      </div>

      <button
        type="button"
        className="notes-toggle"
        onClick={() => setOpenNotes((v) => !v)}
      >
        {openNotes ? "− Hide notes" : "+ Add your tips & notes"}
        {!openNotes && notes ? " (saved)" : ""}
      </button>

      {openNotes && (
        <textarea
          className="save-notes"
          rows={3}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Your own tips for this trip — what to carry, best time to go, who to invite…"
        />
      )}

      {savedId && <span className="saved-flag">✓ Saved to your library</span>}
    </div>
  );
}
