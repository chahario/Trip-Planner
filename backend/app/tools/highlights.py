"""Highlights agent — "what is this place famous for?"

For each city on the trip it answers, day-wise/location-wise:
  * the activities & experiences the place is famous for (e.g. Phuket ->
    island-hopping to Phi Phi, Big Buddha, Old Town, Bangla Road nightlife), and
  * the signature local food/dishes and where to try them (what a place is
    "famous for" when it's a restaurant/food town).

Design law (same as the rest of the agent): the web supplies facts, the LLM only
organises them. We ground every answer in real DuckDuckGo results so the model
doesn't invent attractions, then ask it to structure them into strict JSON.

Best-effort: with no LLM key, or on any failure, it returns an empty list and the
rest of the plan is unaffected.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from app.config import get_settings
from app.models import CityHighlights, FamousActivity, SignatureFood
from app.tools import web_search

log = logging.getLogger(__name__)


def is_enabled() -> bool:
    return bool(get_settings().llm_api_key)


async def discover_highlights(
    cities: list[str], *, interests: Optional[list[str]] = None, max_cities: int = 6
) -> list[CityHighlights]:
    """Return per-city highlights for up to `max_cities`, grounded in web search.
    Cities are processed concurrently. Returns [] if the LLM isn't configured."""
    if not is_enabled():
        return []
    seen: set[str] = set()
    unique: list[str] = []
    for c in cities:
        k = (c or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            unique.append(c.strip())
        if len(unique) >= max_cities:
            break

    results = await asyncio.gather(
        *[_highlights_for_city(c, interests or []) for c in unique]
    )
    return [r for r in results if r is not None]


async def _highlights_for_city(
    city: str, interests: list[str]
) -> Optional[CityHighlights]:
    evidence = await _gather_evidence(city)
    data = await asyncio.to_thread(_call_llm, city, interests, evidence)
    if not data:
        return None

    famous = []
    for a in (data.get("famous_for") or [])[:8]:
        if not isinstance(a, dict) or not a.get("title"):
            continue
        famous.append(FamousActivity(
            title=str(a["title"]).strip(),
            kind=(str(a.get("kind", "activity")).strip().lower() or "activity"),
            why=(str(a.get("why", "")).strip() or None),
        ))

    foods = []
    for f in (data.get("signature_foods") or [])[:8]:
        if not isinstance(f, dict) or not f.get("dish"):
            continue
        foods.append(SignatureFood(
            dish=str(f["dish"]).strip(),
            why=(str(f.get("why", "")).strip() or None),
            where=(str(f.get("where", "")).strip() or None),
        ))

    if not famous and not foods:
        return None
    return CityHighlights(
        city=city,
        famous_for=famous,
        signature_foods=foods,
        note=(str(data.get("note", "")).strip() or None),
        source="ai",
    )


async def _gather_evidence(city: str) -> Optional[str]:
    """Compact, real web snippets about what the city is known for + its food."""
    queries = [
        f"what is {city} famous for top things to do",
        f"{city} famous local food and dishes to try",
        f"best experiences in {city} for tourists",
    ]
    results = await web_search.search_text(queries)
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


def _call_llm(city: str, interests: list[str], evidence: Optional[str]) -> Optional[dict]:
    s = get_settings()
    try:
        from openai import OpenAI

        client = OpenAI(api_key=s.llm_api_key, timeout=s.http_timeout_seconds * 3)
        ev_block = (
            f"\n\nGround your answer in THESE real web results — prefer them over "
            f"your own memory and do not invent places that aren't real:\n{evidence}\n"
            if evidence else ""
        )
        interest_hint = (
            f" The traveler is especially into: {', '.join(interests)}. Lead with "
            f"things that match, but still cover the city's signature highlights."
            if interests else ""
        )
        prompt = (
            f"What is {city} most famous for? List the standout ACTIVITIES and "
            f"experiences a first-time visitor should know it for, and the SIGNATURE "
            f"local foods/dishes (what the place is famous to eat) with where to try "
            f"them.{interest_hint}{ev_block}\n"
            f"Return STRICT JSON only:\n"
            f'{{"famous_for":[{{"title":str,"kind":"activity|experience|nature|'
            f'nightlife|shopping|landmark","why":str}}],'
            f'"signature_foods":[{{"dish":str,"why":str,"where":str}}],'
            f'"note":str}}\n'
            f"Give 4-6 famous_for and 3-5 signature_foods. 'why' is one short, "
            f"concrete phrase. 'note' is one local tip (best season, etiquette, or a "
            f"common tourist trap to avoid). Keep everything specific to {city}."
        )
        resp = client.chat.completions.create(
            model=s.llm_model or "gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a precise local-expert travel "
                 "guide. You only describe real, well-known places and dishes, grounded "
                 "in the evidence provided. JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=0.3,
        )
        content = resp.choices[0].message.content
        return json.loads(content) if content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("highlights failed for %s: %s", city, exc)
        return None
