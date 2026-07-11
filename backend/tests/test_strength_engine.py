from datetime import date

from app.engines.strength import (
    ACCUMULATION_WEEKS,
    MESOCYCLE_LENGTH,
    all_prescriptions_logged,
    best_mesocycle_offset,
    generate_strength_session,
    is_deload_week,
    mesocycle_week_local,
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


def test_all_prescriptions_logged_true_when_every_pattern_logged():
    prescriptions = [{"pattern": "squat"}, {"pattern": "hinge"}]
    assert all_prescriptions_logged(prescriptions, {"squat", "hinge"}) is True


def test_all_prescriptions_logged_false_when_some_still_missing():
    prescriptions = [{"pattern": "squat"}, {"pattern": "hinge"}, {"pattern": "carry"}]
    assert all_prescriptions_logged(prescriptions, {"squat"}) is False


def test_all_prescriptions_logged_ignores_unrelated_extra_patterns():
    prescriptions = [{"pattern": "squat"}]
    assert all_prescriptions_logged(prescriptions, {"squat", "some_other_pattern"}) is True


def test_all_prescriptions_logged_true_for_empty_prescriptions():
    assert all_prescriptions_logged([], set()) is True


def test_best_mesocycle_offset_minimizes_distance_to_running_recovery_weeks():
    """A 16-week block with a 2-week taper (weeks 14-15) has running
    down-weeks at 3, 7, 11 (every 4th week, engines/running.py's
    is_down_week) plus the taper itself -- targets {3, 7, 11, 14, 15}.
    Checked by hand: offset 3 puts deload weeks at {2, 7, 12}, landing
    exactly on week 7 and only 1 week off from 3 and 12 -- the minimum
    total distance (2) of any of the 5 possible offsets."""
    assert best_mesocycle_offset(total_weeks=16, taper_start=14) == 3


def test_best_mesocycle_offset_deload_weeks_actually_move_closer_than_the_old_default():
    """Regression test for #31: the mesocycle used to always start at
    offset 0 regardless of the running plan. Confirm the chosen offset's
    deload weeks land strictly closer to the running plan's down-weeks/
    taper, in aggregate, than the old hardcoded offset 0 would have."""
    total_weeks, taper_start = 16, 14
    targets = sorted({i for i in range(total_weeks) if (i > 0 and i < taper_start and (i + 1) % 4 == 0) or i >= taper_start})

    def _total_distance(offset: int) -> int:
        deload_weeks = [i for i in range(total_weeks) if (i - offset) % MESOCYCLE_LENGTH == ACCUMULATION_WEEKS]
        return sum(min(abs(w - t) for t in targets) for w in deload_weeks)

    chosen = best_mesocycle_offset(total_weeks, taper_start)
    assert _total_distance(chosen) < _total_distance(0)


def test_best_mesocycle_offset_defaults_to_zero_with_no_recovery_weeks():
    # A block too short to have any down-week or taper-only week (degenerate
    # edge case) -- must not crash, and 0 is as good an offset as any other.
    assert best_mesocycle_offset(total_weeks=0, taper_start=0) == 0


def test_generate_strength_session_honors_a_nonzero_mesocycle_offset():
    """generate_strength_session's mesocycle_start_week must actually shift
    which week is the deload week -- confirms the plumbing, not just the
    offset-selection math above."""
    default_offset = generate_strength_session(2, date(2026, 7, 8), 4, "Re-base", mesocycle_start_week=0)
    shifted_offset = generate_strength_session(2, date(2026, 7, 8), 4, "Re-base", mesocycle_start_week=1)
    # local_week=4 with offset 0 is the deload week (light); with offset 1,
    # local_week=(4-1)%5=3, the last accumulation week (heaviest working set).
    assert mesocycle_week_local(4, 0) == ACCUMULATION_WEEKS
    assert mesocycle_week_local(4, 1) == ACCUMULATION_WEEKS - 1
    default_squat = next(p for p in default_offset.prescriptions if p.pattern == "squat")
    shifted_squat = next(p for p in shifted_offset.prescriptions if p.pattern == "squat")
    assert "Deload" in default_squat.note
    assert "Deload" not in shifted_squat.note
