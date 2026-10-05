"""Web image search tool (DuckDuckGo, keyless).

Fetches a real photo URL for a query like "The Chancery Pavilion Bangalore hotel".
Used to give AI-recommended hotels and itinerary stops actual photos instead of
stock images. Fully optional and resilient: any failure yields None and the UI
falls back to a themed stock image.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.config import get_settings

log = logging.getLogger(__name__)

_MIN_W = 400  # skip tiny thumbnails


def _search_one(query: str) -> Optional[str]:
    try:
        from ddgs import DDGS

        with DDGS() as d:
            results = list(d.images(query, max_results=6, safesearch="moderate"))
            for r in results:
                url = r.get("image")
                try:
                    w = int(r.get("width") or 0)
                except (TypeError, ValueError):
                    w = 0
                if url and str(url).startswith("https") and w >= _MIN_W:
                    return url
            # fall back to the first https result regardless of size
            for r in results:
                url = r.get("image")
                if url and str(url).startswith("https"):
                    return url
    except Exception as exc:  # noqa: BLE001
        log.warning("image search failed for %r: %s", query, exc)
    return None


async def search_images(queries: list[str], concurrency: int = 4) -> list[Optional[str]]:
    """Return an image URL (or None) for each query, fetched concurrently."""
    sem = asyncio.Semaphore(concurrency)

    async def one(q: str) -> Optional[str]:
        async with sem:
            return await asyncio.to_thread(_search_one, q)

    return await asyncio.gather(*(one(q) for q in queries))


async def attach_images(plan, *, only_missing: bool = True) -> int:
    """Fill `image_url` on every hotel and itinerary stop with a REAL photo found
    by searching the web for that specific hotel/place + its city — so images
    actually match what they label. Centralised here (run once, after hotels and
    stops are final) so nothing is left with a generic stock image.

    Duck-typed to avoid importing models. Returns how many images were attached.
    """
    if not get_settings().image_search_enabled:
        return 0
    targets: list[tuple[str, object]] = []

    for h in (getattr(plan, "hotels", None) or []):
        if only_missing and getattr(h, "image_url", None):
            continue
        name = (getattr(h, "name", "") or "").strip()
        if not name:
            continue
        city = (getattr(h, "city", None) or getattr(h, "area", None) or "").strip()
        # Name + city + "hotel" is the most identifying query for a property.
        targets.append((f"{name} {city} hotel".strip(), h))

    for it in (getattr(plan, "items", None) or []):
        place = getattr(it, "place", None)
        if place is None:
            continue
        if only_missing and getattr(place, "image_url", None):
            continue
        title = (getattr(it, "title", "") or "").strip()
        if not title:
            continue
        # Skip open "explore the city" flex blocks — there's no specific place.
        cat = (getattr(place, "category", "") or "")
        if cat == "walk" and "explore" in title.lower():
            continue
        city = (getattr(it, "city", None) or getattr(place, "city", None) or "").strip()
        targets.append((f"{title} {city}".strip(), place))

    if not targets:
        return 0

    urls = await search_images([q for q, _ in targets])
    n = 0
    misses: list[tuple[str, object]] = []
    for (q, obj), url in zip(targets, urls):
        if url:
            obj.image_url = url  # type: ignore[attr-defined]
            n += 1
        else:
            misses.append((q, obj))

    # Relaxed retry for anything that found nothing: drop the trailing "hotel"
    # qualifier and try the bare "<name> <city>" so near-misses still get a photo.
    if misses:
        retry_qs = [q[:-6].strip() if q.lower().endswith(" hotel") else q
                    for q, _ in misses]
        retry_urls = await search_images(retry_qs)
        for (_, obj), url in zip(misses, retry_urls):
            if url:
                obj.image_url = url  # type: ignore[attr-defined]
                n += 1
    return n
