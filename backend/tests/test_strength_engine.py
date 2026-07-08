from datetime import date

from app.engines.strength import (
    ACCUMULATION_WEEKS,
    generate_strength_session,
    is_deload_week,
    prescribe,
    race_proximity_mode,
    select_exercise,
)


def test_race_proximity_mode_table():
    assert race_proximity_mode("Re-base") == "accumulate"
    assert race_proximity_mode("Build 1") == "accumulate"
    assert race_proximity_mode("Build 2") == "maintenance"
    assert race_proximity_mode("Taper") == "minimal"


def test_accumulation_progresses_sets_up_and_rir_down():
    week0 = prescribe("squat", "compound", local_week=0, mode="accumulate")
    week_last = prescribe("squat", "compound", local_week=ACCUMULATION_WEEKS - 1, mode="accumulate")
    assert week_last.sets > week0.sets
    assert week_last.rir < week0.rir
    assert week0.rir == 3.0
    assert week_last.rir == 1.0


def test_deload_week_drops_below_mev():
    deload = prescribe("squat", "compound", local_week=ACCUMULATION_WEEKS, mode="accumulate")
    week0 = prescribe("squat", "compound", local_week=0, mode="accumulate")
    assert deload.sets < week0.sets
    assert deload.rir > week0.rir
    assert is_deload_week(ACCUMULATION_WEEKS)
    assert not is_deload_week(0)


def test_maintenance_mode_holds_load_regardless_of_week():
    early = prescribe("hinge", "compound", local_week=0, mode="maintenance")
    late = prescribe("hinge", "compound", local_week=3, mode="maintenance")
    assert early.sets == late.sets
    assert early.rir == late.rir


def test_minimal_mode_is_movement_only():
    p = prescribe("hinge", "compound", local_week=2, mode="minimal")
    assert p.sets == 1
    assert p.rir == 4.0


def test_compounds_stay_in_strength_rep_range():
    p = prescribe("squat", "compound", local_week=0, mode="accumulate")
    assert p.reps == "3-5"
    acc = prescribe("core", "core", local_week=0, mode="accumulate")
    assert acc.reps != "3-5"


def test_generate_strength_session_upper_lower_hybrid():
    upper = generate_strength_session(0, date(2026, 7, 6), 0, "Re-base")
    lower = generate_strength_session(2, date(2026, 7, 8), 0, "Re-base")
    hybrid = generate_strength_session(4, date(2026, 7, 10), 0, "Re-base")
    tuesday = generate_strength_session(1, date(2026, 7, 7), 0, "Re-base")

    assert upper.name == "Upper"
    assert lower.name == "Lower"
    assert hybrid.name == "Hybrid"
    assert tuesday is None

    lower_patterns = {p.pattern for p in lower.prescriptions}
    assert "squat" in lower_patterns and "hinge" in lower_patterns


def test_build2_strength_is_maintenance_across_the_week():
    session = generate_strength_session(2, date(2026, 7, 8), week_index := 5, "Build 2")
    for p in session.prescriptions:
        assert "Maintenance" in p.note or "maintenance" in p.note


def test_select_exercise_respects_injury_flags():
    exercises = [
        {"name": "Back squat", "pattern": "squat", "injury_tags": ["lower_back", "knee"]},
        {"name": "Goblet squat", "pattern": "squat", "injury_tags": ["knee"]},
        {"name": "Leg press", "pattern": "squat", "injury_tags": []},
    ]
    pick = select_exercise("squat", exercises, injury_flags=["lower_back"])
    assert pick["name"] == "Goblet squat"

    pick_knee = select_exercise("squat", exercises, injury_flags=["lower_back", "knee"])
    assert pick_knee["name"] == "Leg press"

    pick_none = select_exercise("hinge", exercises, injury_flags=[])
    assert pick_none is None
