import json
from datetime import date, timedelta

import httpx

from app import intervals_sync
from app.integrations.intervals_icu import IntervalsIcuClient
from app.models import AthleteProfile, PlannedSession, RacePriority, Race, SessionStatus, SessionType
from app.plan_service import generate_and_persist_plan
from app.seed import seed_exercise_library


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
