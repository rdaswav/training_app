"""Tests for the deterministic weekly-review metrics.

These matter more than most of the suite: the whole design premise is that the
LLM never does arithmetic, which is only worth anything if the arithmetic done
here is right. A silently wrong number would be laundered into confident prose.
"""
from datetime import date

from app.engines.load_summary import WeeklyLoadPoint
from app.engines.weekly_review import (
    build_review_metrics,
    compliance,
    compliance_by_type,
    hr_ceiling_check,
    prescribed_targets,
    session_lines,
    week_over_week,
)

MONDAY = date(2026, 7, 20)


def _planned(session_id, day_offset, name, session_type, status, content=None):
    return {
        "id": session_id,
        "date": date(2026, 7, 20 + day_offset),
        "name": name,
        "type": session_type,
        "status": status,
        "content": content or {},
    }


def test_compliance_counts_completed_missed_and_untouched_separately():
    """A session still sitting at `planned` is not a miss -- folding the two
    together would understate compliance on a week reviewed mid-flight."""
    rows = [
        _planned(1, 0, "Easy", "run", "completed"),
        _planned(2, 1, "Upper", "strength", "completed"),
        _planned(3, 2, "Threshold", "run", "missed"),
        _planned(4, 3, "Lower", "strength", "planned"),
    ]
    result = compliance(rows)
    assert (result.planned, result.completed, result.missed, result.still_planned) == (4, 2, 1, 1)
    assert result.completion_pct == 50.0


def test_compliance_with_no_sessions_reports_none_not_zero_pct():
    result = compliance([])
    assert result.planned == 0
    assert result.completion_pct is None  # 0% would imply a failed week, not an empty one


def test_compliance_by_type_splits_run_and_strength():
    rows = [
        _planned(1, 0, "Easy", "run", "completed"),
        _planned(2, 1, "Long", "run", "missed"),
        _planned(3, 2, "Upper", "strength", "completed"),
    ]
    by_type = compliance_by_type(rows)
    assert by_type["run"].completed == 1 and by_type["run"].missed == 1
    assert by_type["strength"].completed == 1 and by_type["strength"].planned == 1


def _load(week, run_km, tonnage):
    return WeeklyLoadPoint(
        week_start=week, run_km=run_km, run_pct=0.0, tonnage_kg=tonnage, tonnage_pct=0.0, is_future=False
    )


def test_week_over_week_computes_real_deltas_not_normalized_percentages():
    series = [_load(date(2026, 7, 13), 40.0, 8000.0), _load(MONDAY, 50.0, 6000.0)]
    delta = week_over_week(series, MONDAY)
    assert delta.run_km == 50.0 and delta.run_km_prev == 40.0
    assert delta.run_km_delta_pct == 25.0
    assert delta.tonnage_delta_pct == -25.0


def test_week_over_week_returns_none_when_there_is_no_prior_week():
    """First week of a block: a delta is undefined, not 0% -- reporting 0 would
    tell the coach volume held steady when there's simply nothing to compare."""
    delta = week_over_week([_load(MONDAY, 50.0, 6000.0)], MONDAY)
    assert delta.run_km == 50.0
    assert delta.run_km_prev is None
    assert delta.run_km_delta_pct is None


def test_week_over_week_handles_zero_prior_week_without_dividing_by_zero():
    series = [_load(date(2026, 7, 13), 0.0, 0.0), _load(MONDAY, 30.0, 5000.0)]
    delta = week_over_week(series, MONDAY)
    assert delta.run_km_delta_pct is None
    assert delta.tonnage_delta_pct is None


def test_prescribed_targets_reads_pace_from_the_longest_step_and_max_hr_ceiling():
    content = {
        "steps": [
            {"type": "step", "label": "Warmup", "distance_km": 2.0, "target_pace_sec_per_km": 400, "hr_ceiling": 145},
            {"type": "step", "label": "Steady", "distance_km": 10.0, "target_pace_sec_per_km": 390, "hr_ceiling": 150},
        ]
    }
    assert prescribed_targets(content) == (390, 150)


def test_prescribed_targets_descends_into_repeat_blocks():
    content = {
        "steps": [
            {"type": "step", "label": "Warmup", "duration_min": 15, "target_pace_sec_per_km": 400},
            {
                "type": "repeat",
                "repeat_count": 5,
                "work": {"label": "Rep", "distance_km": 1.0, "target_pace_sec_per_km": 330, "hr_ceiling": 170},
                "recovery": {"label": "Float", "duration_min": 2.0},
            },
        ]
    }
    pace, ceiling = prescribed_targets(content)
    assert pace == 330  # the 1km work rep beats the distance-less warmup
    assert ceiling == 170


def test_session_lines_joins_actuals_and_carries_autoregulation_feedback():
    planned = [
        _planned(1, 0, "Easy run", "run", "completed", {"role": "easy", "total_distance_km": 8.0, "steps": [
            {"type": "step", "distance_km": 8.0, "target_pace_sec_per_km": 390, "hr_ceiling": 150},
        ]}),
    ]
    completed = [
        {
            "planned_session_id": 1,
            "actual": {"actual_pace_sec_per_km": 385, "actual_hr": 148},
            "feedback": "On target -- hold current prescription.",
            "next_instruction": "hold",
        }
    ]
    (line,) = session_lines(planned, completed)
    assert line.role == "easy"
    assert line.prescribed_pace_sec_per_km == 390 and line.actual_pace_sec_per_km == 385
    assert line.prescribed_hr_ceiling == 150 and line.actual_hr == 148
    assert line.next_instruction == "hold"


def test_session_lines_leaves_actuals_empty_for_a_missed_session():
    planned = [_planned(1, 0, "Long run", "run", "missed", {"role": "long"})]
    (line,) = session_lines(planned, [])
    assert line.status == "missed"
    assert line.actual_pace_sec_per_km is None and line.actual_hr is None
    assert line.feedback == ""


def _line_rows(role, actual_hr, ceiling):
    planned = [
        _planned(1, 0, "Run", "run", "completed", {
            "role": role,
            "steps": [{"type": "step", "distance_km": 8.0, "hr_ceiling": ceiling}],
        })
    ]
    completed = [{"planned_session_id": 1, "actual": {"actual_hr": actual_hr}, "feedback": "", "next_instruction": ""}]
    return session_lines(planned, completed)


def test_hr_ceiling_check_flags_an_easy_run_above_its_ceiling():
    flags = hr_ceiling_check(_line_rows("easy", 158, 150), aerobic_hr_ceiling=150)
    assert len(flags) == 1
    assert flags[0].over_by == 8


def test_hr_ceiling_check_ignores_quality_sessions():
    """A quality session's average HR is expected to exceed the aerobic ceiling --
    flagging it would be noise, not a signal."""
    assert hr_ceiling_check(_line_rows("quality", 175, 150), aerobic_hr_ceiling=150) == []


def test_hr_ceiling_check_ignores_sessions_with_no_logged_hr():
    assert hr_ceiling_check(_line_rows("easy", None, 150), aerobic_hr_ceiling=150) == []


def test_hr_ceiling_check_falls_back_to_the_profile_ceiling():
    flags = hr_ceiling_check(_line_rows("long", 160, None), aerobic_hr_ceiling=150)
    assert len(flags) == 1 and flags[0].hr_ceiling == 150


def test_build_review_metrics_is_json_serializable_and_carries_every_section():
    """The same dict is persisted to CoachReview.metrics and rendered into the
    prompt, so it has to survive json.dumps without a custom encoder."""
    import json

    planned = [
        _planned(1, 0, "Easy run", "run", "completed", {"role": "easy", "total_distance_km": 8.0}),
        _planned(2, 2, "Threshold", "run", "missed", {"role": "quality"}),
    ]
    completed = [{"planned_session_id": 1, "actual": {"actual_hr": 152}, "feedback": "ok", "next_instruction": "hold"}]

    metrics = build_review_metrics(
        week_start=MONDAY,
        athlete={"aerobic_hr_ceiling": 150, "vdot": 36.0},
        race={"name": "City Half", "race_date": "2026-10-04"},
        plan={"phase_name": "Build 1", "week_index": 4},
        planned_rows=planned,
        completed_rows=completed,
        load_series=[_load(date(2026, 7, 13), 40.0, 8000.0), _load(MONDAY, 44.0, 8200.0)],
        e1rm_by_pattern={"squat": 102.4567},
    )

    assert metrics["week_start"] == "2026-07-20" and metrics["week_end"] == "2026-07-26"
    assert metrics["compliance"]["overall"]["completed"] == 1
    assert metrics["load"]["run_km_delta_pct"] == 10.0
    assert len(metrics["sessions"]) == 2
    assert metrics["hr_ceiling_flags"][0]["over_by"] == 2  # 152 vs the profile's 150
    assert metrics["e1rm_by_pattern"]["squat"] == 102.5  # rounded for the prompt
    assert [w["week_start"] for w in metrics["recent_load"]] == ["2026-07-13", "2026-07-20"]

    json.dumps(metrics)  # must not raise
