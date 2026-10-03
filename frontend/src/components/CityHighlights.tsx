import type { CityHighlights as CH } from "../lib/types";

interface Props {
  highlights: CH;
}

const KIND_ICON: Record<string, string> = {
  activity: "🎯",
  experience: "✨",
  nature: "🌿",
  nightlife: "🌃",
  shopping: "🛍️",
  landmark: "📍",
};

/**
 * "What <city> is famous for" — the standout experiences a place is known for,
 * plus its signature dishes and where to try them. Produced by the highlights
 * agent (web-grounded), shown for the city you're in on the active day.
 */
export default function CityHighlights({ highlights }: Props) {
  const { city, famous_for, signature_foods, note } = highlights;
  if (!famous_for.length && !signature_foods.length) return null;

  return (
    <div className="highlights-card">
      <div className="highlights-head">
        <span className="highlights-ico">⭐</span>
        <h4>What {city} is famous for</h4>
      </div>

      {famous_for.length > 0 && (
        <ul className="famous-list">
          {famous_for.map((a, i) => (
            <li key={i} className="famous-item">
              <span className="famous-ico">{KIND_ICON[a.kind] || "🎯"}</span>
              <span className="famous-body">
                <span className="famous-title">{a.title}</span>
                {a.why && <span className="famous-why">{a.why}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}

      {signature_foods.length > 0 && (
        <div className="signature-foods">
          <div className="sig-head">🍽️ Signature food — what to eat here</div>
          <ul className="sig-list">
            {signature_foods.map((f, i) => (
              <li key={i} className="sig-item">
                <span className="sig-dish">{f.dish}</span>
                {f.why && <span className="sig-why"> — {f.why}</span>}
                {f.where && <span className="sig-where"> · 📍 {f.where}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {note && <p className="highlights-note">💡 {note}</p>}
    </div>
  );
}
