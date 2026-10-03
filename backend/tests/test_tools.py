"""Unit tests for the individual agent tools (pure functions, no network)."""
from __future__ import annotations

from app.models import Place, Plan, PlanItem, PlanRequest, Preferences
from app.tools.generate import generate_final_plan
from app.tools.parse import parse_user_preferences
from app.tools.validate import estimate_cost, validate_plan


def test_parse_structured_input():
    req = PlanRequest(
        city="Bangalore",
        budget=2000,
        available_time="4 hours",
        mood="tired but wants to do something fun",
        interests=["food", "music", "walks"],
        constraints=["vegetarian", "avoid crowded places"],
    )
    prefs = parse_user_preferences(req)
    assert prefs.city == "Bangalore"
    assert prefs.budget == 2000
    assert prefs.time_hours == 4.0
    assert prefs.energy == "low"  # "tired"
    assert "food" in prefs.interests and "music" in prefs.interests
    assert "vegetarian" in prefs.dietary
    assert prefs.avoid_crowds is True


def test_parse_free_text_only():
    req = PlanRequest(
        city="Mumbai",
        free_text="I'm feeling energetic, love live music and drinks, budget around 3000 for 5 hours",
    )
    prefs = parse_user_preferences(req)
    assert prefs.energy == "high"
    assert prefs.budget == 3000
    assert prefs.time_hours == 5.0
    assert "music" in prefs.interests


def test_estimate_cost_sums_items():
    place = Place(name="X", category="cafe", kind="food", est_cost=300)
    plan = Plan(city="Delhi", items=[
        PlanItem(order=1, time_slot="Afternoon", title="X", place=place,
                 duration_hours=1.0, est_cost=300, why_it_fits="y"),
        PlanItem(order=2, time_slot="Evening", title="X", place=place,
                 duration_hours=2.0, est_cost=300, why_it_fits="y"),
    ])
    plan = estimate_cost(plan)
    assert plan.total_cost == 600
    assert plan.total_hours == 3.0


def test_validate_flags_over_budget():
    place = Place(name="Pricey", category="bar", kind="food", est_cost=1000)
    plan = Plan(city="Delhi", items=[
        PlanItem(order=1, time_slot="Evening", title="Pricey", place=place,
                 duration_hours=2.0, est_cost=1000, why_it_fits="y"),
    ])
    plan = estimate_cost(plan)
    prefs = Preferences(city="Delhi", budget=500)
    warnings = validate_plan(plan, prefs)
    assert plan.within_budget is False
    assert any("over your" in w for w in warnings)


def test_validate_flags_crowds():
    place = Place(name="Busy Market", category="market", kind="activity", est_cost=200)
    plan = Plan(city="Delhi", items=[
        PlanItem(order=1, time_slot="Evening", title="Busy Market", place=place,
                 duration_hours=1.0, est_cost=200, why_it_fits="y"),
    ])
    plan = estimate_cost(plan)
    prefs = Preferences(city="Delhi", avoid_crowds=True)
    warnings = validate_plan(plan, prefs)
    assert any("crowd" in w.lower() for w in warnings)


def test_generate_plan_respects_energy_low_first():
    acts = [
        Place(name="Nightclub", category="live_music", kind="activity", est_cost=600),
        Place(name="Park", category="park", kind="activity", est_cost=0),
    ]
    foods = [Place(name="Cafe", category="cafe", kind="food", est_cost=300)]
    prefs = Preferences(city="Bangalore", energy="low", interests=["walks", "food"], time_hours=4)
    plan = generate_final_plan(acts, foods, prefs)
    assert plan.items
    # Low energy -> calm activity (Park) should come before the club.
    assert plan.items[0].place.category in {"park", "garden", "walk", "cafe"}


def test_generate_plan_has_why_and_summary():
    acts = [Place(name="Park", category="park", kind="activity", est_cost=0)]
    foods = [Place(name="Cafe", category="cafe", kind="food", est_cost=300)]
    prefs = Preferences(city="Bangalore", energy="low", interests=["walks", "food"], budget=2000, time_hours=4)
    plan = generate_final_plan(acts, foods, prefs)
    assert plan.summary
    for item in plan.items:
        assert item.why_it_fits
