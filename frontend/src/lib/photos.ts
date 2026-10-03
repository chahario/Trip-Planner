// Keyless, good-looking location imagery. We map each place category to a
// curated Unsplash photo (stable CDN URLs, free to hot-link, loaded directly by
// the browser so no backend/API key is involved). This gives every stop an
// attractive, on-theme image even when a specific photo of the venue isn't
// available. Swap individual URLs freely, or later wire a real photo API.

const CATEGORY_IMAGES: Record<string, string> = {
  cafe: "https://images.unsplash.com/photo-1501339847302-ac426a4a7cbb?w=800&q=70&auto=format&fit=crop",
  restaurant: "https://images.unsplash.com/photo-1517248135467-4c7edcad34c4?w=800&q=70&auto=format&fit=crop",
  street_food: "https://images.unsplash.com/photo-1604908176997-125f25cc6f3d?w=800&q=70&auto=format&fit=crop",
  bakery: "https://images.unsplash.com/photo-1509440159596-0249088772ff?w=800&q=70&auto=format&fit=crop",
  park: "https://images.unsplash.com/photo-1519331379826-f10be5486c6f?w=800&q=70&auto=format&fit=crop",
  garden: "https://images.unsplash.com/photo-1558693168-c370615b54e0?w=800&q=70&auto=format&fit=crop",
  walk: "https://images.unsplash.com/photo-1476900543704-4312b78632f8?w=800&q=70&auto=format&fit=crop",
  viewpoint: "https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?w=800&q=70&auto=format&fit=crop",
  museum: "https://images.unsplash.com/photo-1554907984-15263bfd63bd?w=800&q=70&auto=format&fit=crop",
  gallery: "https://images.unsplash.com/photo-1545989253-02cc26577f88?w=800&q=70&auto=format&fit=crop",
  live_music: "https://images.unsplash.com/photo-1470225620780-dba8ba36b745?w=800&q=70&auto=format&fit=crop",
  bar: "https://images.unsplash.com/photo-1514362545857-3bc16c4c7d1b?w=800&q=70&auto=format&fit=crop",
  cinema: "https://images.unsplash.com/photo-1489599849927-2ee91cede3ba?w=800&q=70&auto=format&fit=crop",
  market: "https://images.unsplash.com/photo-1488459716781-31db52582fe9?w=800&q=70&auto=format&fit=crop",
  hotel: "https://images.unsplash.com/photo-1566073771259-6a8506099945?w=800&q=70&auto=format&fit=crop",
};

const FALLBACK =
  "https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=800&q=70&auto=format&fit=crop";

export function placeImage(category: string): string {
  return CATEGORY_IMAGES[category] ?? FALLBACK;
}

// A few distinct hotel photos so recommended hotels don't all look identical.
// (Stock imagery — for the ACTUAL hotel's photos, the card links out to Google.)
const HOTEL_IMAGES = [
  "https://images.unsplash.com/photo-1566073771259-6a8506099945?w=800&q=70&auto=format&fit=crop",
  "https://images.unsplash.com/photo-1551882547-ff40c63fe5fa?w=800&q=70&auto=format&fit=crop",
  "https://images.unsplash.com/photo-1542314831-068cd1dbfeeb?w=800&q=70&auto=format&fit=crop",
  "https://images.unsplash.com/photo-1455587734955-081b22074882?w=800&q=70&auto=format&fit=crop",
  "https://images.unsplash.com/photo-1578683010236-d716f9a3f461?w=800&q=70&auto=format&fit=crop",
  "https://images.unsplash.com/photo-1611892440504-42a792e24d32?w=800&q=70&auto=format&fit=crop",
];

export function hotelImage(index: number): string {
  return HOTEL_IMAGES[index % HOTEL_IMAGES.length];
}
