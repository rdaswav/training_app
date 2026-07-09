"""Tests the client's request construction and workout formatting against a
mock transport. The token syntax asserted here (`<mm:ss>/km Pace`, `<pct>% HR`)
was verified against a live intervals.icu account on 2026-07-09 -- see
integrations/intervals_icu.py's module docstring for the full writeup."""
import json
from datetime import date

import httpx

from app.engines.running import RunSessionPlan, RunStep
from app.integrations.intervals_icu import IntervalsIcuClient, session_to_description


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
