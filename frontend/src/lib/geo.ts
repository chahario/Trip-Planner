// Detect the user's city from the browser's geolocation, so "Starting from"
// can be auto-filled for an accurate cost estimate. Uses BigDataCloud's free,
// keyless, client-side reverse-geocoding endpoint (CORS-enabled). The browser
// asks the user for permission first.

export async function detectCity(): Promise<string> {
  const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
    if (!("geolocation" in navigator)) {
      reject(new Error("Geolocation isn't available in this browser."));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: false,
      timeout: 10000,
      maximumAge: 300000,
    });
  });

  const { latitude, longitude } = pos.coords;
  const url = `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${latitude}&longitude=${longitude}&localityLanguage=en`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Couldn't look up your location.");
  const data = await res.json();
  const city =
    data.city || data.locality || data.principalSubdivision || data.countryName;
  if (!city) throw new Error("Couldn't determine your city.");
  return String(city);
}
