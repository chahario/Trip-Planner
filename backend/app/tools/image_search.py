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


async def search_images(queries: list[str], concurrency: int = 3) -> list[Optional[str]]:
    """Return an image URL (or None) for each query, fetched concurrently."""
    sem = asyncio.Semaphore(concurrency)

    async def one(q: str) -> Optional[str]:
        async with sem:
            return await asyncio.to_thread(_search_one, q)

    return await asyncio.gather(*(one(q) for q in queries))
