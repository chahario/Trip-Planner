import type { Weather } from "../lib/types";

interface Props {
  // Single outlook (single-city trips) — kept for backward compatibility.
  weather?: Weather | null;
  // Per-city outlooks (multi-city trips). When present, these are shown.
  weatherByCity?: Weather[];
}

function Card({ w }: { w: Weather }) {
  return (
    <div className="weather-card">
      {w.city && <span className="weather-city">{w.city}</span>}
      <span className="weather-emoji">{w.emoji}</span>
      <div className="weather-body">
        <span className="weather-desc">{w.description}</span>
        <span className="weather-temps">
          {w.temp_max_c != null && <>{Math.round(w.temp_max_c)}°C</>}
          {w.temp_min_c != null && (
            <span className="weather-min"> / {Math.round(w.temp_min_c)}°C</span>
          )}
          {w.precipitation_chance != null && (
            <span className="weather-rain"> · {w.precipitation_chance}% rain</span>
          )}
        </span>
        {w.advisory && <span className="weather-advisory">{w.advisory}</span>}
      </div>
    </div>
  );
}

/**
 * Shows the weather outlook. For a multi-city trip it renders one compact card
 * per city; for a single-city trip it renders the single outlook.
 */
export default function WeatherBar({ weather, weatherByCity }: Props) {
  const cities = (weatherByCity ?? []).filter(Boolean);

  if (cities.length > 1) {
    return (
      <div className="card weather weather-multi">
        <div className="weather-multi-grid">
          {cities.map((w, i) => (
            <Card key={`${w.city ?? "city"}-${i}`} w={w} />
          ))}
        </div>
      </div>
    );
  }

  const single = cities[0] ?? weather;
  if (!single) return null;

  return (
    <div className="card weather">
      <Card w={single} />
    </div>
  );
}