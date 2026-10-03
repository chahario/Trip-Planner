// Keyless "booking integrations": we build deep-links that open the real
// provider with the destination city and dates pre-filled. The user completes
// the booking on the provider's own site. No API keys, no cost, nothing stored.

export interface BookingLink {
  label: string;
  provider: string;
  url: string;
}

function slug(s: string): string {
  return s
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Format a Date as a local YYYY-MM-DD (no UTC shift — important in +offset TZs). */
function fmt(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Next Saturday from `from` (or today), as a YYYY-MM-DD string. */
export function nextSaturday(from: Date = new Date()): string {
  const d = new Date(from);
  const delta = (6 - d.getDay() + 7) % 7 || 7; // always a future Saturday
  d.setDate(d.getDate() + delta);
  return fmt(d);
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(isoDate + "T00:00:00");
  d.setDate(d.getDate() + days);
  return fmt(d);
}

/**
 * Hotel links. When an `anchor` (a recommended stop's name/area) is given, the
 * searches are centred on that spot so hotels are *near the plan*, not just the
 * city. Falls back to city-level when no anchor is available.
 */
export function hotelLinks(
  city: string,
  checkin: string,
  nights = 1,
  anchor?: string
): BookingLink[] {
  const checkout = addDays(checkin, Math.max(1, nights));
  // Booking.com's free-text search handles "Area, City" well.
  const ss = encodeURIComponent(anchor ? `${anchor}, ${city}` : city);
  const nearText = anchor ? `hotels near ${anchor}, ${city}` : `hotels in ${city}`;
  return [
    {
      provider: "Booking.com",
      label: anchor ? `Stays near ${anchor}` : "Stays on Booking.com",
      url: `https://www.booking.com/searchresults.html?ss=${ss}&checkin=${checkin}&checkout=${checkout}&group_adults=2&no_rooms=1`,
    },
    {
      provider: "Google Hotels",
      label: "Compare on Google Hotels",
      url: `https://www.google.com/travel/search?q=${encodeURIComponent(nearText)}`,
    },
    {
      provider: "Google Maps",
      label: anchor ? `Map hotels near ${anchor}` : "Hotels on the map",
      url: `https://www.google.com/maps/search/${encodeURIComponent(nearText)}`,
    },
  ];
}

/** Restaurant discovery near a recommended stop (or the city). */
export function restaurantLinks(city: string, anchor?: string): BookingLink[] {
  const nearText = anchor ? `restaurants near ${anchor}, ${city}` : `restaurants in ${city}`;
  return [
    {
      provider: "Google Maps",
      label: anchor ? `Dining near ${anchor}` : `Dining in ${city}`,
      url: `https://www.google.com/maps/search/${encodeURIComponent(nearText)}`,
    },
    {
      provider: "EazyDiner",
      label: "Book a table (EazyDiner)",
      url: `https://www.eazydiner.com/${slug(city)}/restaurants`,
    },
  ];
}

/** Flight search links to the destination city. Origin is optional. */
export function flightLinks(city: string, origin: string, dateIso: string): BookingLink[] {
  const to = encodeURIComponent(city);
  const from = encodeURIComponent(origin);
  if (origin) {
    return [
      {
        provider: "Google Flights",
        label: `${origin} → ${city}`,
        url: `https://www.google.com/travel/flights?q=${encodeURIComponent(
          `flights from ${origin} to ${city} on ${dateIso}`
        )}`,
      },
      {
        provider: "Skyscanner",
        label: `Compare fares ${origin} → ${city}`,
        url: `https://www.skyscanner.co.in/transport/flights/?adults=1&departure=${dateIso}&origin=${from}&destination=${to}`,
      },
      {
        provider: "MakeMyTrip",
        label: `MakeMyTrip flights`,
        url: `https://www.makemytrip.com/flights/`,
      },
    ];
  }
  return [
    {
      provider: "Google Flights",
      label: `Flights to ${city}`,
      url: `https://www.google.com/travel/flights?q=${encodeURIComponent(`flights to ${city}`)}`,
    },
    {
      provider: "Skyscanner",
      label: `Explore fares to ${city}`,
      url: `https://www.skyscanner.co.in/transport/flights-to/${to}/`,
    },
  ];
}

/** Bus search links to the destination city. Origin is optional. */
export function busLinks(city: string, origin: string, dateIso: string): BookingLink[] {
  const to = slug(city);
  const from = slug(origin);
  const links: BookingLink[] = [];

  if (from && to) {
    links.push({
      provider: "RedBus",
      label: `RedBus: ${origin} → ${city}`,
      url: `https://www.redbus.in/bus-tickets/${from}-to-${to}`,
    });
    links.push({
      provider: "Google",
      label: `Search buses ${origin} → ${city}`,
      url: `https://www.google.com/search?q=${encodeURIComponent(
        `bus from ${origin} to ${city} ${dateIso}`
      )}`,
    });
  } else {
    links.push({
      provider: "RedBus",
      label: `Find buses to ${city}`,
      url: `https://www.redbus.in/`,
    });
    links.push({
      provider: "Google",
      label: `Search buses to ${city}`,
      url: `https://www.google.com/search?q=${encodeURIComponent(
        `bus tickets to ${city}`
      )}`,
    });
  }
  return links;
}
