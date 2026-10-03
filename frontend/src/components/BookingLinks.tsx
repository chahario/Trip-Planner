import { useEffect, useMemo, useState } from "react";
import {
  addDays,
  busLinks,
  flightLinks,
  hotelLinks,
  nextSaturday,
  restaurantLinks,
} from "../lib/bookings";
import { fetchHotels } from "../lib/api";
import type { Hotel, Plan } from "../lib/types";

interface Props {
  plan: Plan;
  defaultOrigin?: string;
}

/**
 * "Book around your plan" — collapsible (secondary content). Hotels and
 * restaurants are anchored to the first recommended stop; flights and buses
 * target the destination city. Opens real providers pre-filled; real hotel
 * cards appear when the backend has a hotel provider configured.
 */
export default function BookingLinks({ plan, defaultOrigin }: Props) {
  const city = plan.city;
  const tripDays = plan.days ?? 1;
  const [open, setOpen] = useState(false);
  const [checkin, setCheckin] = useState(() => nextSaturday());
  const [nights, setNights] = useState(Math.max(1, tripDays - 1 || 1));
  const [origin, setOrigin] = useState(defaultOrigin ?? "");
  const [realHotels, setRealHotels] = useState<Hotel[]>([]);

  const anchorStop = useMemo(() => {
    const act = plan.items.find((i) => i.place.kind === "activity");
    return act ?? plan.items[0];
  }, [plan]);
  const anchor = anchorStop?.title;
  const anchorLat = anchorStop?.place.lat ?? null;
  const anchorLon = anchorStop?.place.lon ?? null;

  const hotels = hotelLinks(city, checkin, nights, anchor);
  const restaurants = restaurantLinks(city, anchor);
  const flights = flightLinks(city, origin, checkin);
  const buses = busLinks(city, origin, checkin);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchHotels(city, anchorLat, anchorLon).then((res) => {
      if (!cancelled) setRealHotels(res.enabled ? res.hotels : []);
    });
    return () => {
      cancelled = true;
    };
  }, [city, anchorLat, anchorLon, open]);

  const linkList = (links: { provider: string; label: string; url: string }[], cls: string) => (
    <div className="booking-links">
      {links.map((l) => (
        <a key={l.provider} className={`book-btn ${cls}`} href={l.url} target="_blank" rel="noopener noreferrer">
          {l.label}
          <span className="book-provider">{l.provider} ↗</span>
        </a>
      ))}
    </div>
  );

  return (
    <div className="card bookings">
      <button type="button" className="bookings-toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="bt-left">
          <strong>🧳 Book around your plan</strong>
          <span className="hint">hotels · flights · buses · dining{anchor ? ` · near ${anchor}` : ""}</span>
        </span>
        <span className={`chev ${open ? "up" : ""}`}>⌄</span>
      </button>

      {open && (
        <div className="bookings-body">
          <div className="booking-controls">
            <label>
              Date
              <input
                type="date"
                value={checkin}
                min={new Date().toISOString().slice(0, 10)}
                onChange={(e) => setCheckin(e.target.value || nextSaturday())}
              />
            </label>
            <label>
              Nights
              <input
                type="number"
                min={1}
                max={30}
                value={nights}
                onChange={(e) => setNights(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
            <label>
              From (flights/buses)
              <input value={origin} placeholder="your city" onChange={(e) => setOrigin(e.target.value)} />
            </label>
          </div>

          <div className="booking-groups">
            <div className="booking-group">
              <h4>🏨 Hotels {anchor && <span className="near-tag">near {anchor}</span>}</h4>
              <p className="booking-sub">
                {checkin} → {addDays(checkin, Math.max(1, nights))}
              </p>
              {realHotels.length > 0 && (
                <div className="real-hotels">
                  {realHotels.slice(0, 5).map((h, i) => (
                    <a key={i} className="hotel-card" href={h.booking_url ?? "#"} target="_blank" rel="noopener noreferrer">
                      <span className="hotel-name">{h.name}</span>
                      <span className="hotel-meta">
                        {h.rating ? `★ ${h.rating}` : ""}
                        {h.address ? ` · ${h.address}` : ""}
                      </span>
                    </a>
                  ))}
                  <span className="real-hotels-src">Live hotels near your plan</span>
                </div>
              )}
              {linkList(hotels, "hotel")}
            </div>

            <div className="booking-group">
              <h4>✈️ Flights</h4>
              <p className="booking-sub">{origin ? `${origin} → ${city}` : `Add a "From" city`}</p>
              {linkList(flights, "flight")}
            </div>

            <div className="booking-group">
              <h4>🍽️ Restaurants {anchor && <span className="near-tag">near {anchor}</span>}</h4>
              <p className="booking-sub">Dine close to your stops</p>
              {linkList(restaurants, "food")}
            </div>

            <div className="booking-group">
              <h4>🚌 Buses to {city}</h4>
              <p className="booking-sub">{origin ? `${origin} → ${city}` : `Add a "From" city for a route`}</p>
              {linkList(buses, "bus")}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
