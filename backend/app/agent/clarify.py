"""Clarifying-question logic.

The agent asks at most 2 questions, and only when the answer would actually
change the plan. It never blocks: it plans with sensible defaults AND returns
the questions so the UI can offer a refine step.

Priority of questions (each must flip a real downstream decision):
  1. Multi-day country trip + no style signal  -> beach-vs-culture balance
     (this is THE lever that changes which cities the resolver picks)
  2. Multi-day trip + no pace signal           -> pace (controls #cities & nights)
  3. Vague/empty interests                      -> what are you in the mood for
  4. No budget                                  -> rough budget (assume mid otherwise)

We cap at 2 so a first-timer isn't fatigued. For a single-outing plan we keep
the lighter original questions.
"""
from __future__ import annotations

from app.models import ClarifyingQuestion, Preferences

# Interests broad enough that we can't tailor without more info.
_VAGUE = {"fun", "something", "anything", "chill"}


def _has_style_signal(prefs: Preferences) -> bool:
    """True if the user has already expressed a beach/culture/food leaning."""
    sw = prefs.style_weights or {}
    # Any lean meaningfully away from neutral (1.0) counts as a signal.
    for key in ("beach", "culture", "food"):
        try:
            if abs(float(sw.get(key, 1.0)) - 1.0) >= 0.3:
                return True
        except (TypeError, ValueError):
            pass
    # Interests that already imply a lean.
    strong = {"nature", "art", "food", "nightlife", "shopping"}
    return bool(set(prefs.interests) & strong)


def _has_pace_signal(prefs: Preferences) -> bool:
    sw = prefs.style_weights or {}
    pace = sw.get("pace")
    return pace in ("slow", "packed")  # "balanced" is the default, not a signal


def detect_clarifying_questions(prefs: Preferences) -> list[ClarifyingQuestion]:
    qs: list[ClarifyingQuestion] = []
    multi_day = prefs.days > 1

    # --- Multi-day trips: the route-shaping questions come first ---
    if multi_day:
        if not _has_style_signal(prefs):
            qs.append(ClarifyingQuestion(
                field="style_balance",
                question=("How do you want to split your time — more beaches & islands, "
                          "more culture & cities, or a mix?"),
                placeholder="a mix",
            ))
        if not _has_pace_signal(prefs) and len(qs) < 2:
            qs.append(ClarifyingQuestion(
                field="pace",
                question=("What pace suits you — see as much as possible (packed), "
                          "balanced, or fewer places done slowly?"),
                placeholder="balanced",
            ))
        # Fill remaining slot(s) with budget if still unknown.
        if prefs.budget is None and len(qs) < 2:
            qs.append(ClarifyingQuestion(
                field="budget",
                question="Roughly what's your total budget (in ₹)? I'll assume mid-range otherwise.",
                placeholder="mid-range",
            ))
        return qs[:2]

    # --- Single outing: lighter questions (original behaviour) ---
    if not prefs.interests or set(prefs.interests) <= _VAGUE:
        qs.append(ClarifyingQuestion(
            field="interests",
            question="What are you in the mood for — food, music, walks, art, or something else?",
        ))
    if prefs.budget is None:
        qs.append(ClarifyingQuestion(
            field="budget",
            question="Roughly what's your budget for the day (in ₹)? I'll assume mid-range for now.",
        ))
    if prefs.time_hours is None and len(qs) < 2:
        qs.append(ClarifyingQuestion(
            field="available_time",
            question="How much time do you have — a couple of hours, or the whole afternoon?",
        ))
    return qs[:2]