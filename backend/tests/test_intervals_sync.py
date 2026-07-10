import json
from datetime import date, timedelta

import httpx

from app import intervals_sync
from app.api.routes import delete_race
from app.integrations.intervals_icu import IntervalsIcuClient
from app.models import AthleteProfile, PlannedSession, RacePriority, Race, SessionStatus, SessionType
from app.plan_service import generate_and_persist_plan
from app.seed import seed_exercise_library


def _recording_client_class(deleted_ids: list):
    """Builds a fake IntervalsIcuClient class (constructible with no args, as
    delete_synced_events calls it) that appends every deleted event id into
    the given list -- proves a cleanup call happened without a real HTTP client."""

    class _RecordingClient:
        def __init__(self, *args, **kwargs):
            pass

        def delete_planned_workout(self, event_id: str) -> None:
            deleted_ids.append(event_id)

    return _RecordingClient


def _make_athlete_and_race(db_session, today: date):
    seed_exercise_library(db_session)
    athlete = AthleteProfile(injury_flags=[])
    db_session.add(athlete)
    db_session.commit()
    db_session.refresh(athlete)
    race = Race(athlete_id=athlete.id, name="Test Half", race_date=today + timedelta(weeks=14), distance_km=21.1, priority=RacePriority.A)
    db_session.add(race)
    db_session.commit()
    db_session.refresh(race)
    return athlete, race


def test_sync_is_a_noop_when_not_configured(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "")
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    result = intervals_sync.sync_upcoming_runs_to_intervals(db_session, athlete, today=today)
    assert result == {"skipped": "intervals.icu not configured", "synced": 0, "failed": 0}


def test_sync_upserts_planned_runs_and_stores_event_id(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    seen_dates = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen_dates.append(body["start_date_local"][:10])
        return httpx.Response(200, json={"id": f"evt-{len(seen_dates)}"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    result = intervals_sync.sync_upcoming_runs_to_intervals(db_session, athlete, today=today, window_days=10, client=client)

    assert result["failed"] == 0
    assert result["synced"] > 0
    assert result["synced"] == len(seen_dates)

    run_sessions = (
        db_session.query(PlannedSession)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.type == SessionType.RUN,
            PlannedSession.status == SessionStatus.PLANNED,
            PlannedSession.date >= today,
            PlannedSession.date <= today + timedelta(days=10),
        )
        .all()
    )
    assert run_sessions
    assert all(s.intervals_icu_event_id is not None for s in run_sessions)


def test_sync_records_failure_without_aborting_batch(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"id": f"evt-{calls['n']}"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    result = intervals_sync.sync_upcoming_runs_to_intervals(db_session, athlete, today=today, window_days=10, client=client)

    assert result["failed"] == 1
    assert result["synced"] >= 1


def test_sync_handles_repeat_step_sessions(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    descriptions = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        descriptions.append(body["description"])
        return httpx.Response(200, json={"id": f"evt-{len(descriptions)}"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    result = intervals_sync.sync_upcoming_runs_to_intervals(db_session, athlete, today=today, window_days=10, client=client)

    assert result["failed"] == 0
    # The Re-base "Strides" quality session (6x repeat block) should fall within
    # the 10-day window and produce an "Nx" line in its synced description.
    assert any("6x" in d for d in descriptions)


def test_sync_backward_compatible_with_legacy_flat_step_rows(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    today = date(2026, 7, 8)
    athlete, _race = _make_athlete_and_race(db_session, today)

    # Manually construct a row in the OLD shape (no "type" key at all) --
    # simulates data persisted before repeat-block support existed.
    legacy_session = PlannedSession(
        athlete_id=athlete.id,
        date=today,
        type=SessionType.RUN,
        name="Cruise intervals",
        status=SessionStatus.PLANNED,
        content={
            "steps": [
                {
                    "label": "3 x 1.6km cruise interval @ threshold, 90s jog",
                    "distance_km": 4.8,
                    "duration_min": None,
                    "target_pace_sec_per_km": 330,
                    "hr_ceiling": None,
                }
            ],
            "total_distance_km": 4.8,
            "role": "quality",
        },
        phase_name="Build 1",
    )
    db_session.add(legacy_session)
    db_session.commit()

    plan = intervals_sync._to_run_session_plan(legacy_session)
    assert len(plan.steps) == 1
    assert not hasattr(plan.steps[0], "repeat_count")  # a plain RunStep, not RunRepeatStep
    assert plan.steps[0].distance_km == 4.8

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "evt-legacy"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    result = intervals_sync.sync_upcoming_runs_to_intervals(db_session, athlete, today=today, window_days=0, client=client)
    assert result["synced"] == 1
    assert result["failed"] == 0


def test_delete_synced_events_is_a_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "")
    deleted_ids: list = []
    monkeypatch.setattr(intervals_sync, "IntervalsIcuClient", _recording_client_class(deleted_ids))

    session = PlannedSession(intervals_icu_event_id="evt-1")
    intervals_sync.delete_synced_events([session])
    assert deleted_ids == []


def test_delete_synced_events_skips_sessions_with_no_event_id(monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    deleted_ids: list = []
    monkeypatch.setattr(intervals_sync, "IntervalsIcuClient", _recording_client_class(deleted_ids))

    never_synced = PlannedSession(intervals_icu_event_id=None)
    intervals_sync.delete_synced_events([never_synced])
    assert deleted_ids == []


def test_delete_synced_events_deletes_each_event_and_never_raises(monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    deleted_ids: list = []

    class _FlakyClient:
        def __init__(self, *a, **kw):
            pass

        def delete_planned_workout(self, event_id: str) -> None:
            if event_id == "evt-bad":
                raise RuntimeError("boom")
            deleted_ids.append(event_id)

    monkeypatch.setattr(intervals_sync, "IntervalsIcuClient", _FlakyClient)

    sessions = [
        PlannedSession(intervals_icu_event_id="evt-1"),
        PlannedSession(intervals_icu_event_id="evt-bad"),
        PlannedSession(intervals_icu_event_id="evt-2"),
    ]
    intervals_sync.delete_synced_events(sessions)  # must not raise despite the middle failure
    assert deleted_ids == ["evt-1", "evt-2"]


def test_regenerating_plan_deletes_previously_synced_events_before_replacing(db_session, monkeypatch):
    """Regression test: previously, generate_and_persist_plan's bulk-delete of
    replaced PlannedSession rows never told intervals.icu to delete the
    corresponding event -- so a plan regeneration (e.g. shifting the start
    date by a day, as the settings-page race-edit flow does via delete+
    recreate) orphaned the old events. The regenerated sessions then synced
    as brand-new events, leaving the originals behind looking like
    duplicates on the intervals.icu calendar."""
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    # Simulate a previous successful sync having assigned intervals.icu event ids.
    sessions = db_session.query(PlannedSession).filter(PlannedSession.athlete_id == athlete.id).all()
    for i, s in enumerate(sessions):
        s.intervals_icu_event_id = f"evt-{i}"
    db_session.commit()
    shifted_start = today + timedelta(days=1)
    expected_deleted = {s.intervals_icu_event_id for s in sessions if s.date >= shifted_start}
    assert expected_deleted, "test setup needs at least one session past the shifted start date"

    deleted_ids: list = []
    monkeypatch.setattr(intervals_sync, "IntervalsIcuClient", _recording_client_class(deleted_ids))

    # Mirrors the settings-page "change plan start date by a day" flow that
    # surfaced this bug.
    generate_and_persist_plan(db_session, athlete, race, plan_start_date=shifted_start, today=today)

    assert set(deleted_ids) == expected_deleted


def test_delete_race_deletes_previously_synced_events(db_session, monkeypatch):
    """Regression test for the same orphaning bug via the other code path:
    deleting a race entirely (the first half of the settings-page race-edit
    flow, which deletes then recreates) must also clean up any already-synced
    intervals.icu events before the local rows are removed."""
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    sessions = db_session.query(PlannedSession).filter(PlannedSession.athlete_id == athlete.id).all()
    for i, s in enumerate(sessions):
        s.intervals_icu_event_id = f"evt-{i}"
    db_session.commit()
    expected_deleted = {s.intervals_icu_event_id for s in sessions if s.status == SessionStatus.PLANNED}

    deleted_ids: list = []
    monkeypatch.setattr(intervals_sync, "IntervalsIcuClient", _recording_client_class(deleted_ids))

    delete_race(race.id, db_session)

    assert set(deleted_ids) == expected_deleted
