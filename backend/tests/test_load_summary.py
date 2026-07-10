from datetime import date

from app.engines.load_summary import (
    build_weekly_load_series,
    sum_run_km_by_week,
    sum_strength_tonnage_by_week,
)

WEEK1 = date(2026, 8, 10)
WEEK2 = date(2026, 8, 17)
WEEK3 = date(2026, 8, 24)


def test_sum_run_km_by_week_aggregates_multiple_runs_in_same_week():
    rows = [
        {"week_start": WEEK1, "distance_km": 5.0},
        {"week_start": WEEK1, "distance_km": 8.0},
        {"week_start": WEEK2, "distance_km": 10.0},
    ]
    totals = sum_run_km_by_week(rows)
    assert totals == {WEEK1: 13.0, WEEK2: 10.0}


def test_sum_run_km_by_week_treats_missing_or_none_distance_as_zero():
    rows = [{"week_start": WEEK1, "distance_km": None}, {"week_start": WEEK1}]
    totals = sum_run_km_by_week(rows)
    assert totals == {WEEK1: 0.0}


def test_sum_strength_tonnage_by_week_sums_across_multiple_sets_and_multiple_rows():
    rows = [
        {"week_start": WEEK1, "actual": {"sets": [{"reps": 5, "weight_kg": 100}, {"reps": 5, "weight_kg": 100}]}},
        {"week_start": WEEK1, "actual": {"sets": [{"reps": 8, "weight_kg": 40}]}},
    ]
    totals = sum_strength_tonnage_by_week(rows)
    assert totals == {WEEK1: 5 * 100 + 5 * 100 + 8 * 40}


def test_sum_strength_tonnage_by_week_ignores_rows_with_no_sets():
    rows = [{"week_start": WEEK1, "actual": {"sets": []}}, {"week_start": WEEK1, "actual": {}}]
    totals = sum_strength_tonnage_by_week(rows)
    assert totals == {WEEK1: 0.0}


def test_build_weekly_load_series_future_weeks_get_none_tonnage_not_zero():
    series = build_weekly_load_series(
        week_starts=[WEEK1, WEEK2],
        run_km_by_week={WEEK1: 20.0, WEEK2: 25.0},
        tonnage_by_week={WEEK1: 500.0},
        current_week_start=WEEK1,
    )
    assert series[0].tonnage_kg == 500.0
    assert series[1].tonnage_kg is None
    assert series[1].tonnage_pct is None
    assert series[1].is_future is True


def test_build_weekly_load_series_past_week_with_no_logged_sets_gets_honest_zero():
    series = build_weekly_load_series(
        week_starts=[WEEK1],
        run_km_by_week={WEEK1: 20.0},
        tonnage_by_week={},
        current_week_start=WEEK1,
    )
    assert series[0].tonnage_kg == 0.0
    assert series[0].tonnage_pct == 0.0
    assert series[0].is_future is False


def test_build_weekly_load_series_pct_normalized_independently_per_metric():
    series = build_weekly_load_series(
        week_starts=[WEEK1, WEEK2],
        run_km_by_week={WEEK1: 10.0, WEEK2: 40.0},
        tonnage_by_week={WEEK1: 900.0, WEEK2: 300.0},
        current_week_start=WEEK2,
    )
    by_week = {pt.week_start: pt for pt in series}
    assert by_week[WEEK2].run_pct == 100.0
    assert by_week[WEEK1].tonnage_pct == 100.0
    assert by_week[WEEK2].tonnage_pct == round(300 / 900 * 100, 1)


def test_build_weekly_load_series_handles_all_future_weeks_without_division_by_zero():
    series = build_weekly_load_series(
        week_starts=[WEEK1, WEEK2],
        run_km_by_week={WEEK1: 10.0, WEEK2: 12.0},
        tonnage_by_week={},
        current_week_start=date(2026, 1, 1),
    )
    assert all(pt.tonnage_kg is None for pt in series)
    assert all(pt.is_future for pt in series)


def test_build_weekly_load_series_handles_all_zero_run_weeks_without_division_by_zero():
    series = build_weekly_load_series(
        week_starts=[WEEK1],
        run_km_by_week={},
        tonnage_by_week={WEEK1: 100.0},
        current_week_start=WEEK1,
    )
    assert series[0].run_km == 0.0
    assert series[0].run_pct == 0.0


def test_build_weekly_load_series_sorts_by_week_start():
    series = build_weekly_load_series(
        week_starts=[WEEK3, WEEK1, WEEK2],
        run_km_by_week={},
        tonnage_by_week={},
        current_week_start=WEEK3,
    )
    assert [pt.week_start for pt in series] == [WEEK1, WEEK2, WEEK3]
