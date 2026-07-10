from datetime import date

from app.engines.dashboard_summary import (
    active_phase,
    global_week_index,
    race_flags,
    strength_mesocycle_status,
    timeline_pct,
    week_ticks,
)

PHASES = [
    {"name": "Re-base", "start_date": date(2026, 7, 6), "end_date": date(2026, 7, 19), "focus": "Aerobic rebuild"},
    {"name": "Build 1", "start_date": date(2026, 7, 20), "end_date": date(2026, 8, 16), "focus": "Threshold intro"},
    {"name": "Taper", "start_date": date(2026, 8, 17), "end_date": date(2026, 8, 30), "focus": "Sharpen"},
]


def test_active_phase_finds_matching_phase():
    assert active_phase(PHASES, date(2026, 7, 25))["name"] == "Build 1"


def test_active_phase_returns_none_when_today_outside_all_phases():
    assert active_phase(PHASES, date(2026, 9, 1)) is None


def test_global_week_index_is_zero_on_start_week():
    start = date(2026, 7, 6)  # a Monday
    assert global_week_index(start, start) == 0
    assert global_week_index(start, date(2026, 7, 10)) == 0  # same week


def test_global_week_index_advances_by_one_per_week():
    start = date(2026, 7, 6)
    assert global_week_index(start, date(2026, 7, 13)) == 1
    assert global_week_index(start, date(2026, 7, 20)) == 2


def test_week_ticks_classifies_done_now_upcoming():
    ticks = week_ticks(total_weeks=5, current_week_index=2)
    statuses = [t.status for t in ticks]
    assert statuses == ["done", "done", "now", "upcoming", "upcoming"]
    assert [t.week_num for t in ticks] == [1, 2, 3, 4, 5]


def test_timeline_pct_at_start_and_end():
    start, end = date(2026, 7, 6), date(2026, 8, 30)
    assert timeline_pct(start, end, start) == 0.0
    assert timeline_pct(start, end, end) == 100.0


def test_timeline_pct_clamped_within_bounds():
    start, end = date(2026, 7, 6), date(2026, 8, 30)
    assert timeline_pct(start, end, date(2026, 6, 1)) == 0.0
    assert timeline_pct(start, end, date(2026, 12, 1)) == 100.0


def test_race_flags_only_includes_races_within_macrocycle_range():
    start, end = date(2026, 7, 6), date(2026, 8, 30)
    races = [
        {"name": "Tune-up 5k", "race_date": date(2026, 8, 2), "priority": "tune_up"},
        {"name": "Goal Half", "race_date": date(2026, 8, 30), "priority": "A"},
        {"name": "Next season race", "race_date": date(2026, 12, 1), "priority": "A"},
    ]
    flags = race_flags(races, start, end)
    assert len(flags) == 2
    tune_up = next(f for f in flags if f.label == "Tune-up 5k")
    goal = next(f for f in flags if f.label == "Goal Half")
    assert tune_up.tag == "tune-up"
    assert goal.tag == "target"
    assert goal.pct == 100.0


def test_strength_mesocycle_status_accumulate_mode_mid_block():
    status = strength_mesocycle_status(week_idx=1, current_phase_name="Re-base")
    assert status.mode == "accumulate"
    assert status.local_week == 1
    assert 1.0 <= status.current_rir <= 3.0
    assert 0.0 <= status.effort_pct <= 100.0


def test_strength_mesocycle_status_deload_week():
    # ACCUMULATION_WEEKS=4, MESOCYCLE_LENGTH=5 -> local_week 4 is the deload week.
    status = strength_mesocycle_status(week_idx=4, current_phase_name="Re-base")
    assert status.local_week == 4
    assert "deload" in status.note.lower()


def test_strength_mesocycle_status_maintenance_mode_for_build2():
    status = strength_mesocycle_status(week_idx=0, current_phase_name="Build 2")
    assert status.mode == "maintenance"


def test_strength_mesocycle_status_minimal_mode_for_taper():
    status = strength_mesocycle_status(week_idx=0, current_phase_name="Taper")
    assert status.mode == "minimal"
