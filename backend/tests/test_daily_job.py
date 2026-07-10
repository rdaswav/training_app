import json
from datetime import date, timedelta

import httpx

from app import intervals_sync
from app.jobs.daily_autoregulation import run_daily_job_for_athlete
from app.models import AthleteProfile, PlannedSession, Race, RacePriority, SessionStatus, SessionType
from app.plan_service import generate_and_persist_plan
from app.seed import seed_exercise_library
from app.integrations.intervals_icu import IntervalsIcuClient


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


def _first_planned_session_on_or_after(db_session, athlete, d):
    return (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= d)
        .order_by(PlannedSession.date)
        .first()
    )


def test_stale_session_marked_missed_when_not_configured(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "")

    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, yesterday)
    generate_and_persist_plan(db_session, athlete, race, today=yesterday)

    stale = _first_planned_session_on_or_after(db_session, athlete, yesterday)
    assert stale is not None
    assert stale.date < today  # it's yesterday's slot, now stale relative to "today"

    summary = run_daily_job_for_athlete(db_session, athlete, today=today)

    db_session.refresh(stale)
    assert stale.status == SessionStatus.MISSED
    assert summary["missed_marked"] >= 1
    assert summary["matched"] == 0
    assert summary["regenerated"] is True


def test_matched_run_activity_marks_completed_and_progresses_pace(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")

    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, yesterday)
    generate_and_persist_plan(db_session, athlete, race, today=yesterday)

    stale_run = (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.RUN, PlannedSession.date == yesterday)
        .first()
    )
    assert stale_run is not None
    original_easy_pace = athlete.easy_pace_sec_per_km

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/activities" in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {
                        "start_date_local": f"{yesterday.isoformat()}T06:00:00",
                        "distance": 8000,
                        "moving_time": 8 * 260,  # 2080s over 8km = 260s/km, comfortably fast
                        "average_heartrate": 130,
                    }
                ],
            )
        if request.method == "GET" and "/wellness" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "evt-1"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    summary = run_daily_job_for_athlete(db_session, athlete, today=today, client=client)

    db_session.refresh(stale_run)
    assert stale_run.status == SessionStatus.COMPLETED
    assert stale_run.completed is not None
    assert summary["matched"] == 1
    assert summary["missed_marked"] == 0
    # A comfortably-fast easy run at low HR should nudge paces faster (progress).
    assert athlete.easy_pace_sec_per_km <= original_easy_pace


def test_multi_day_backlog_widens_fetch_window_and_matches_all_stale_runs(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")

    plan_start = date(2026, 7, 6)
    athlete, race = _make_athlete_and_race(db_session, plan_start)
    generate_and_persist_plan(db_session, athlete, race, today=plan_start)

    stale_runs = (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.RUN)
        .order_by(PlannedSession.date)
        .limit(2)
        .all()
    )
    assert len(stale_runs) == 2, "need at least two run sessions to test a multi-day backlog"
    day1, day2 = stale_runs[0].date, stale_runs[1].date
    assert day1 < day2

    # The job hasn't run since before day1 -- both day1 and day2 are stale relative to "today".
    today = day2 + timedelta(days=1)

    captured_params = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/activities" in str(request.url):
            captured_params.append(dict(request.url.params))
            return httpx.Response(
                200,
                json=[
                    {"start_date_local": f"{day1.isoformat()}T06:00:00", "distance": 8000, "moving_time": 8 * 300, "average_heartrate": 130},
                    {"start_date_local": f"{day2.isoformat()}T06:00:00", "distance": 8000, "moving_time": 8 * 300, "average_heartrate": 130},
                ],
            )
        if request.method == "GET" and "/wellness" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "evt-1"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    summary = run_daily_job_for_athlete(db_session, athlete, today=today, client=client)

    db_session.refresh(stale_runs[0])
    db_session.refresh(stale_runs[1])
    assert stale_runs[0].status == SessionStatus.COMPLETED
    assert stale_runs[1].status == SessionStatus.COMPLETED
    assert summary["matched"] == 2

    # The fetch window must have widened to cover the earliest stale session (of any
    # type), not just "yesterday" (today - 1 == day2) -- confirms the backlog fix.
    earliest_stale_date = (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date < today)
        .order_by(PlannedSession.date)
        .first()
        .date
    )
    assert earliest_stale_date <= day1
    assert captured_params
    assert captured_params[0]["oldest"] == earliest_stale_date.isoformat()
    assert captured_params[0]["newest"] == (today - timedelta(days=1)).isoformat()


def test_strength_session_always_marked_missed_if_unlogged(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "")

    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, yesterday)
    generate_and_persist_plan(db_session, athlete, race, today=yesterday)

    strength_yesterday = (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.STRENGTH, PlannedSession.date == yesterday)
        .first()
    )
    if strength_yesterday is None:
        return  # the fixed template doesn't guarantee a strength day on this particular date

    run_daily_job_for_athlete(db_session, athlete, today=today)
    db_session.refresh(strength_yesterday)
    assert strength_yesterday.status == SessionStatus.MISSED
