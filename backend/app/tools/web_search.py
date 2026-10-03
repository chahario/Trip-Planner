"""Keyless web text search (DuckDuckGo) used to GROUND facts the model would
otherwise guess — e.g. whether a flight route exists and typical fares/durations
for a transport leg. Best-effort: failures return empty so callers degrade.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)


def _search_one(query: str, max_results: int = 4) -> list[dict]:
    try:
        from ddgs import DDGS

        with DDGS() as d:
            out = []
            for r in d.text(query, max_results=max_results):
                out.append({
                    "title": (r.get("title") or "")[:120],
                    "body": (r.get("body") or "")[:300],
                })
            return out
    except Exception as exc:  # noqa: BLE001
        log.warning("web search failed for %r: %s", query, exc)
        return []


async def search_text(queries: list[str], concurrency: int = 3) -> dict[str, list[dict]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(q: str):
        async with sem:
            return q, await asyncio.to_thread(_search_one, q)

    pairs = await asyncio.gather(*(one(q) for q in queries))
    return {q: res for q, res in pairs}


async def transport_evidence(origin: str, destination: str) -> Optional[str]:
    """Gather real web snippets about getting from origin to destination, as a
    compact text block to feed the LLM so it extracts real durations/fares and
    doesn't invent non-existent routes (e.g. a flight to a city with no airport)."""
    queries = [
        f"flights from {origin} to {destination}",
        f"{origin} to {destination} train time fare",
        f"{origin} to {destination} bus time fare",
        f"{origin} to {destination} by road distance time",
    ]
    results = await search_text(queries)
    lines: list[str] = []
    for q, snippets in results.items():
        if not snippets:
            continue
        lines.append(f"# {q}")
        for s in snippets[:3]:
            text = f"{s['title']} — {s['body']}".strip(" —")
            if text:
                lines.append(f"- {text}")
    blob = "\n".join(lines)
    return blob[:3500] if blob else None
