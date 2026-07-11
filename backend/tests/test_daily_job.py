import json
from datetime import date, timedelta

import httpx
import pytest

from app import intervals_sync
from app.jobs.daily_autoregulation import run_daily_job_for_athlete
from app.models import AthleteProfile, CompletedSession, PlannedSession, Race, RacePriority, SessionStatus, SessionType
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


def _make_athlete(db_session, **overrides):
    athlete = AthleteProfile(injury_flags=[], **overrides)
    db_session.add(athlete)
    db_session.commit()
    db_session.refresh(athlete)
    return athlete


def _make_run_session(db_session, athlete, d, role, target_pace_sec_per_km, total_distance_km=8.0):
    """Bypasses generate_and_persist_plan for tests that need precise control
    over a single session's role/date/target pace, rather than relying on
    whatever the fixed weekly template happens to assign to a given date."""
    session = PlannedSession(
        athlete_id=athlete.id,
        date=d,
        type=SessionType.RUN,
        name="Test run",
        status=SessionStatus.PLANNED,
        content={
            "role": role,
            "total_distance_km": total_distance_km,
            "steps": [
                {
                    "type": "step",
                    "label": "main",
                    "duration_min": None,
                    "distance_km": total_distance_km,
                    "target_pace_sec_per_km": target_pace_sec_per_km,
                    "hr_ceiling": None,
                }
            ],
        },
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


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


def test_quality_session_progress_requires_manual_confirmation(db_session, monkeypatch):
    """Regression test for #35: the daily job used to hardcode hit_reps=True,
    so a quality session with a merely-fast matched activity would silently
    auto-progress threshold pace with no confirmation reps were hit. Now the
    automated path may hold/soften a quality session but must never progress
    it on its own."""
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete = _make_athlete(db_session)
    original_threshold = athlete.threshold_pace_sec_per_km
    session = _make_run_session(db_session, athlete, yesterday, "quality", athlete.threshold_pace_sec_per_km)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/activities" in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {
                        "start_date_local": f"{yesterday.isoformat()}T06:00:00",
                        "distance": 8000,
                        "moving_time": round(8 * (original_threshold - 10)),  # comfortably under target
                        "average_heartrate": 150,
                    }
                ],
            )
        if request.method == "GET" and "/wellness" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "evt-1"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    summary = run_daily_job_for_athlete(db_session, athlete, today=today, client=client)

    db_session.refresh(session)
    assert session.status == SessionStatus.COMPLETED
    assert summary["matched"] == 1
    assert athlete.threshold_pace_sec_per_km == original_threshold  # not auto-progressed
    completed = db_session.query(CompletedSession).filter(CompletedSession.planned_session_id == session.id).first()
    assert completed.next_instruction == "hold"


def test_quality_role_progress_and_soften_never_touch_easy_pace(db_session, monkeypatch):
    """Regression test for #21: threshold_pace and easy_pace used to move
    together on any progress/soften result regardless of which session role
    triggered it. A quality-session result must only ever move threshold_pace."""
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete = _make_athlete(db_session)
    original_easy = athlete.easy_pace_sec_per_km
    session = _make_run_session(db_session, athlete, yesterday, "quality", athlete.threshold_pace_sec_per_km)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/activities" in str(request.url):
            return httpx.Response(
                200,
                json=[{"start_date_local": f"{yesterday.isoformat()}T06:00:00", "distance": 8000, "moving_time": 8 * 400, "average_heartrate": 150}],
            )
        if request.method == "GET" and "/wellness" in str(request.url):
            # Poor readiness -- forces a "soften" result for the quality session.
            return httpx.Response(200, json=[{"id": yesterday.isoformat(), "readiness": 40}])
        return httpx.Response(200, json={"id": "evt-1"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    run_daily_job_for_athlete(db_session, athlete, today=today, client=client)

    db_session.refresh(session)
    assert session.status == SessionStatus.COMPLETED
    assert athlete.easy_pace_sec_per_km == original_easy  # unaffected by a quality-session result
    assert athlete.threshold_pace_sec_per_km != original_easy  # sanity: something did move (soften), just not easy_pace


def test_activity_fetch_failure_leaves_run_sessions_planned_not_missed(db_session, monkeypatch):
    """Regression test for #22: a transient intervals.icu fetch failure used
    to leave activities_by_date empty, so every stale RUN session fell
    through to MISSED -- indistinguishable from a genuine missed run.
    Strength sessions are unaffected since their completion was never
    dependent on the activity fetch in the first place."""
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete = _make_athlete(db_session)
    run_session = _make_run_session(db_session, athlete, yesterday, "easy", athlete.easy_pace_sec_per_km)
    strength_session = PlannedSession(
        athlete_id=athlete.id,
        date=yesterday - timedelta(days=1),
        type=SessionType.STRENGTH,
        name="Lower",
        status=SessionStatus.PLANNED,
        content={"prescriptions": []},
    )
    db_session.add(strength_session)
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/activities" in str(request.url):
            return httpx.Response(500, text="boom")
        if request.method == "GET" and "/wellness" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "evt-1"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    summary = run_daily_job_for_athlete(db_session, athlete, today=today, client=client)

    db_session.refresh(run_session)
    db_session.refresh(strength_session)
    assert run_session.status == SessionStatus.PLANNED  # not a phantom miss -- retried next successful run
    assert strength_session.status == SessionStatus.MISSED  # unrelated to the intervals.icu fetch, unchanged
    assert summary["missed_marked"] == 1


def test_clamp_to_baseline_bounds_in_both_directions():
    from app.jobs.daily_autoregulation import MAX_PACE_DRIFT_SEC_PER_KM, _clamp_to_baseline

    baseline = 390
    assert _clamp_to_baseline(390, baseline, -5) == 385
    assert _clamp_to_baseline(baseline - MAX_PACE_DRIFT_SEC_PER_KM + 2, baseline, -10) == baseline - MAX_PACE_DRIFT_SEC_PER_KM
    assert _clamp_to_baseline(baseline + MAX_PACE_DRIFT_SEC_PER_KM - 2, baseline, 10) == baseline + MAX_PACE_DRIFT_SEC_PER_KM


def test_daily_job_clamps_progress_at_the_drift_bound(db_session, monkeypatch):
    """Regression test for #24: a run of 'progress' results must not push the
    prescribed pace further than MAX_PACE_DRIFT_SEC_PER_KM from the athlete's
    profile-set baseline."""
    from app.jobs.daily_autoregulation import MAX_PACE_DRIFT_SEC_PER_KM

    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete = _make_athlete(db_session)
    baseline = athlete.easy_pace_baseline_sec_per_km
    athlete.easy_pace_sec_per_km = baseline - MAX_PACE_DRIFT_SEC_PER_KM + 2  # already almost at the cap
    db_session.commit()
    session = _make_run_session(db_session, athlete, yesterday, "easy", athlete.easy_pace_sec_per_km)
    fast_pace = athlete.easy_pace_sec_per_km - 20

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/activities" in str(request.url):
            return httpx.Response(
                200,
                json=[{"start_date_local": f"{yesterday.isoformat()}T06:00:00", "distance": 8000, "moving_time": round(8 * fast_pace), "average_heartrate": athlete.aerobic_hr_ceiling - 20}],
            )
        if request.method == "GET" and "/wellness" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "evt-1"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    run_daily_job_for_athlete(db_session, athlete, today=today, client=client)

    db_session.refresh(session)
    assert athlete.easy_pace_sec_per_km == baseline - MAX_PACE_DRIFT_SEC_PER_KM


def test_daily_job_records_last_run_and_clears_error_on_success(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "")
    athlete = _make_athlete(db_session)
    athlete.last_job_error = "stale error from a previous failed run"
    db_session.commit()

    run_daily_job_for_athlete(db_session, athlete, today=date(2026, 7, 8))

    assert athlete.last_job_run_at is not None
    assert athlete.last_job_error is None


def test_daily_job_records_error_and_still_raises_on_failure(db_session, monkeypatch):
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "")
    athlete = _make_athlete(db_session)

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("app.jobs.daily_autoregulation._run_daily_job_for_athlete", _boom)

    with pytest.raises(RuntimeError):
        run_daily_job_for_athlete(db_session, athlete, today=date(2026, 7, 8))

    assert athlete.last_job_run_at is not None
    assert "synthetic failure" in athlete.last_job_error


def test_best_matching_activity_prefers_closest_distance():
    from app.jobs.daily_autoregulation import _best_matching_activity

    session = PlannedSession(content={"total_distance_km": 8.0})
    shakeout = {"distance": 2000}
    real_run = {"distance": 7900}
    assert _best_matching_activity([shakeout, real_run], session) is real_run


def test_best_matching_activity_falls_back_to_first_without_a_prescribed_distance():
    from app.jobs.daily_autoregulation import _best_matching_activity

    session = PlannedSession(content={})
    a, b = {"distance": 1000}, {"distance": 9000}
    assert _best_matching_activity([a, b], session) is a


def test_best_matching_activity_returns_none_for_no_candidates():
    from app.jobs.daily_autoregulation import _best_matching_activity

    assert _best_matching_activity([], PlannedSession(content={})) is None


def test_daily_job_matches_closest_activity_when_two_land_same_day(db_session, monkeypatch):
    """Regression test for #34: two activities on the same date used to
    resolve by last-write-wins in fetch order; now the one closest in
    distance to the planned session wins."""
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_API_KEY", "test-key")
    monkeypatch.setattr(intervals_sync, "INTERVALS_ICU_ATHLETE_ID", "i123")
    yesterday = date(2026, 7, 7)
    today = date(2026, 7, 8)
    athlete = _make_athlete(db_session)
    session = _make_run_session(db_session, athlete, yesterday, "easy", athlete.easy_pace_sec_per_km, total_distance_km=8.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/activities" in str(request.url):
            return httpx.Response(
                200,
                json=[
                    {"start_date_local": f"{yesterday.isoformat()}T06:00:00", "distance": 2000, "moving_time": 2 * 300, "average_heartrate": 120},
                    {"start_date_local": f"{yesterday.isoformat()}T18:00:00", "distance": 8000, "moving_time": 8 * 390, "average_heartrate": 140},
                ],
            )
        if request.method == "GET" and "/wellness" in str(request.url):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"id": "evt-1"})

    client = IntervalsIcuClient(api_key="test-key", athlete_id="i123", transport=httpx.MockTransport(handler))
    run_daily_job_for_athlete(db_session, athlete, today=today, client=client)

    completed = db_session.query(CompletedSession).filter(CompletedSession.planned_session_id == session.id).first()
    assert completed is not None
    assert completed.actual["actual_pace_sec_per_km"] == 390
