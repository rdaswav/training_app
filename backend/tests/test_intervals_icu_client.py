"""Tests the client's request construction and workout formatting against a
mock transport. The token syntax asserted here (`<mm:ss>/km Pace`, `<pct>% HR`,
the `Nx` repeat-block structure, and the whole-seconds duration format) was
verified against a live intervals.icu account on 2026-07-09 -- see
integrations/intervals_icu.py's module docstring for the full writeup."""
import json
from datetime import date

import httpx

from app.engines.running import RunRepeatStep, RunSessionPlan, RunStep
from app.integrations.intervals_icu import (
    REPEAT_BLOCK_SYNTAX_CONFIRMED,
    IntervalsIcuClient,
    _format_duration,
    repeat_step_to_lines,
    session_to_description,
    step_to_line,
)


def _sample_session() -> RunSessionPlan:
    return RunSessionPlan(
        date=date(2026, 7, 14),
        name="Easy run",
        phase_name="Re-base",
        role="easy",
        total_distance_km=8.0,
        steps=[RunStep("Easy/recovery run", distance_km=8.0, target_pace_sec_per_km=390, hr_ceiling=150)],
    )


def test_session_to_description_includes_pace_and_hr():
    description = session_to_description(_sample_session(), max_hr=185)
    assert "8.0km" in description
    assert "6:30/km Pace" in description
    assert "81% HR" in description  # round(150 / 185 * 100)


def test_session_to_description_omits_hr_without_max_hr():
    description = session_to_description(_sample_session())
    assert "8.0km" in description
    assert "6:30/km Pace" in description
    assert "HR" not in description


def test_upsert_planned_workout_posts_expected_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "evt_123"})

    client = IntervalsIcuClient(
        api_key="test-key", athlete_id="i12345", transport=httpx.MockTransport(handler)
    )
    result = client.upsert_planned_workout(_sample_session(), max_hr=185)

    assert result == {"id": "evt_123"}
    assert captured["method"] == "POST"
    assert "/athlete/i12345/events" in captured["url"]
    assert captured["body"]["type"] == "Run"
    assert captured["body"]["name"] == "Easy run"
    assert "81% HR" in captured["body"]["description"]


def test_upsert_planned_workout_puts_when_event_id_given():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path.endswith("/events/evt_999")
        return httpx.Response(200, json={"id": "evt_999"})

    client = IntervalsIcuClient(api_key="k", athlete_id="i1", transport=httpx.MockTransport(handler))
    client.upsert_planned_workout(_sample_session(), existing_event_id="evt_999")


def test_get_activities_sends_date_range():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["oldest"] == "2026-07-01"
        assert request.url.params["newest"] == "2026-07-08"
        return httpx.Response(200, json=[{"id": "a1"}])

    client = IntervalsIcuClient(api_key="k", athlete_id="i1", transport=httpx.MockTransport(handler))
    activities = client.get_activities(date(2026, 7, 1), date(2026, 7, 8))
    assert activities == [{"id": "a1"}]


def test_repeat_step_to_lines_emits_count_then_work_then_recovery():
    step = RunRepeatStep(
        label="Cruise interval",
        repeat_count=3,
        work=RunStep("Cruise interval", distance_km=1.6, target_pace_sec_per_km=330),
        recovery=RunStep("Jog", duration_min=1.5),
    )
    assert repeat_step_to_lines(step) == ["3x", "- 1.6km 5:30/km Pace", "- 90s"]


def test_repeat_step_to_lines_omits_recovery_line_when_none():
    step = RunRepeatStep(
        label="Race-pace rep",
        repeat_count=4,
        work=RunStep("Race-pace rep", distance_km=0.4, target_pace_sec_per_km=300),
        recovery=None,
    )
    lines = repeat_step_to_lines(step)
    assert len(lines) == 2
    assert lines[0] == "4x"


def test_session_to_description_interleaves_repeat_and_plain_steps():
    session = RunSessionPlan(
        date=date(2026, 7, 14),
        name="Cruise intervals",
        phase_name="Build 1",
        role="quality",
        total_distance_km=8.8,
        steps=[
            RunStep("Warmup", duration_min=15, target_pace_sec_per_km=390),
            RunRepeatStep(
                "Cruise interval",
                repeat_count=3,
                work=RunStep("Cruise interval", distance_km=1.6, target_pace_sec_per_km=330),
                recovery=RunStep("Jog", duration_min=1.5),
            ),
            RunStep("Cooldown", duration_min=10, target_pace_sec_per_km=390),
        ],
    )
    lines = session_to_description(session).split("\n")
    assert lines[0] == "- 15m 6:30/km Pace"
    assert lines[1] == "3x"
    assert lines[2] == "- 1.6km 5:30/km Pace"
    assert lines[3] == "- 90s"
    assert lines[4] == "- 10m 6:30/km Pace"


def test_repeat_block_syntax_flagged_confirmed():
    assert REPEAT_BLOCK_SYNTAX_CONFIRMED is True


def test_format_duration_whole_minutes():
    assert _format_duration(15) == "15m"
    assert _format_duration(1.0) == "1m"


def test_format_duration_fractional_minutes_converts_to_seconds():
    # "1.5m"/"0.333...m" silently failed to parse live on 2026-07-09 --
    # decimal-minute tokens must be expressed as whole seconds instead.
    assert _format_duration(1.5) == "90s"
    assert _format_duration(20 / 60) == "20s"


def test_step_to_line_uses_seconds_for_fractional_duration_step():
    step = RunStep("Stride", duration_min=20 / 60, target_pace_sec_per_km=310)
    assert step_to_line(step) == "- 20s 5:10/km Pace"
