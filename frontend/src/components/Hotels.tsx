import type { RecommendedHotel } from "../lib/types";
import { hotelImage } from "../lib/photos";

interface Props {
  hotels: RecommendedHotel[];
  city: string; // fallback city for hotels that don't carry their own
}

function agodaUrl(name: string, city: string) {
  return `https://www.agoda.com/search?text=${encodeURIComponent(`${name} ${city}`)}`;
}
function bookingUrl(name: string, city: string) {
  return `https://www.booking.com/searchresults.html?ss=${encodeURIComponent(`${name}, ${city}`)}`;
}
function photosUrl(name: string, city: string) {
  // Opens the real hotel's photos/location on Google Maps.
  return `https://www.google.com/maps/search/${encodeURIComponent(`${name} ${city}`)}`;
}

function HotelCard({ h, i, city }: { h: RecommendedHotel; i: number; city: string }) {
  return (
    <div className="hotel-rec-card">
      <a
        className="hotel-rec-img-wrap"
        href={photosUrl(h.name, city)}
        target="_blank"
        rel="noopener noreferrer"
        title="See real photos & location on Google Maps"
      >
        <img
          className="hotel-rec-img"
          src={h.image_url || hotelImage(i)}
          alt={h.name}
          loading="lazy"
          onError={(e) => {
            const img = e.currentTarget;
            if (img.src !== hotelImage(i)) img.src = hotelImage(i);
          }}
        />
        {h.rating && <span className="hotel-rec-rating">★ {h.rating}</span>}
        <span className="hotel-rec-photos">📷 More photos</span>
      </a>
      <div className="hotel-rec-body">
        <h4>{h.name}</h4>
        {h.area && <p className="hotel-rec-area">📍 {h.area}</p>}
        {h.why && <p className="hotel-rec-why">{h.why}</p>}
        <div className="hotel-rec-foot">
          {h.price_per_night != null && (
            <span className="hotel-rec-price">
              ₹{h.price_per_night.toLocaleString("en-IN")}
              <span className="hotel-rec-per">/night</span>
            </span>
          )}
          <div className="hotel-rec-links">
            <a href={agodaUrl(h.name, city)} target="_blank" rel="noopener noreferrer" className="hotel-rec-btn">
              Agoda ↗
            </a>
            <a href={bookingUrl(h.name, city)} target="_blank" rel="noopener noreferrer" className="hotel-rec-btn alt">
              Booking ↗
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Recommended stays. On a multi-city trip these are grouped by city — one block
 * per city you sleep in — so it's clear where you're staying each night.
 */
export default function Hotels({ hotels, city }: Props) {
  if (!hotels || hotels.length === 0) return null;

  // Group by city, preserving first-seen order. Hotels without a city fall back
  // to the trip city.
  const groups: { city: string; hotels: RecommendedHotel[] }[] = [];
  const index = new Map<string, number>();
  let globalI = 0;
  for (const h of hotels) {
    const c = (h.city || city).trim() || city;
    const key = c.toLowerCase();
    if (!index.has(key)) {
      index.set(key, groups.length);
      groups.push({ city: c, hotels: [] });
    }
    groups[index.get(key)!].hotels.push(h);
  }
  const multiCity = groups.length > 1;

  return (
    <div className="card hotels-rec">
      <div className="section-head">
        <h3>🏨 Where to stay{multiCity ? " — by city" : ""}</h3>
        <span className="hint">
          {multiCity ? "a base in each city you sleep in" : "AI-picked for your area & budget"}
        </span>
      </div>

      {groups.map((g) => (
        <div key={g.city} className="hotel-city-group">
          {multiCity && (
            <div className="hotel-city-head">
              <span className="hotel-city-pin">📍</span>
              <span className="hotel-city-name">{g.city}</span>
              <span className="hotel-city-count">
                {g.hotels.length} stay{g.hotels.length !== 1 ? "s" : ""}
              </span>
            </div>
          )}
          <div className="hotel-grid">
            {g.hotels.map((h) => (
              <HotelCard key={globalI} h={h} i={globalI++} city={g.city} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
