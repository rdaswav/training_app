"""The daily scheduled job (spec section 3 & 10 step 8): pull yesterday's
completed activities/wellness from intervals.icu, autoregulate, then refresh
and re-sync the next 7-10 days. Assumes it runs daily without gaps; a
multi-day backlog will mark unmatched older sessions missed rather than
retroactively fetching a wider activity window.

Field names used to read intervals.icu activities/wellness (`start_date_local`,
`distance`, `moving_time`, `average_heartrate`, `readiness`) are best-effort
guesses at the documented shape -- unconfirmed against a live account, same
caveat as integrations/intervals_icu.py."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.engines import autoregulation
from app.integrations.intervals_icu import IntervalsIcuClient
from app.intervals_sync import intervals_icu_configured, sync_upcoming_runs_to_intervals
from app.models import (
    AthleteProfile,
    CompletedSession,
    PlannedSession,
    Race,
    SessionStatus,
    SessionType,
)
from app.plan_service import generate_and_persist_plan


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _extract_run_actuals(activity: dict) -> dict:
    distance_m = activity.get("distance")
    moving_time_s = activity.get("moving_time") or activity.get("elapsed_time")
    pace = None
    if distance_m and moving_time_s and distance_m > 0:
        pace = round(moving_time_s / (distance_m / 1000))
    hr = activity.get("average_heartrate") or activity.get("icu_average_hr")
    return {"actual_pace_sec_per_km": pace, "actual_hr": hr}


def _wellness_ok(wellness: dict | None) -> bool:
    if not wellness:
        return True
    readiness = wellness.get("readiness", wellness.get("sleepScore"))
    if readiness is None:
        return True
    return readiness >= 65


def run_daily_job_for_athlete(
    db: Session,
    athlete: AthleteProfile,
    today: date | None = None,
    client: IntervalsIcuClient | None = None,
) -> dict:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    summary = {"athlete_id": athlete.id, "matched": 0, "missed_marked": 0, "regenerated": False, "sync": None}

    activities_by_date: dict[date, dict] = {}
    wellness_by_date: dict[date, dict] = {}
    if intervals_icu_configured():
        client = client or IntervalsIcuClient()
        try:
            for activity in client.get_activities(yesterday, yesterday):
                d = _parse_date(activity.get("start_date_local") or activity.get("start_date"))
                if d:
                    activities_by_date[d] = activity
        except Exception:  # noqa: BLE001 -- third-party call must not abort the job
            pass
        try:
            for record in client.get_wellness(yesterday, yesterday):
                d = _parse_date(record.get("id") or record.get("date"))
                if d:
                    wellness_by_date[d] = record
        except Exception:  # noqa: BLE001
            pass

    yesterday_wellness_ok = _wellness_ok(wellness_by_date.get(yesterday))

    stale_sessions = (
        db.query(PlannedSession)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.date < today,
            PlannedSession.status == SessionStatus.PLANNED,
        )
        .all()
    )

    for session in stale_sessions:
        activity = activities_by_date.get(session.date)
        if session.type == SessionType.RUN and activity:
            actuals = _extract_run_actuals(activity)
            role = session.content.get("role")
            steps = session.content.get("steps", [])
            prescribed_pace = next((s["target_pace_sec_per_km"] for s in steps if s.get("target_pace_sec_per_km")), None)
            if role == "quality":
                result = autoregulation.evaluate_quality_session(
                    prescribed_pace or athlete.threshold_pace_sec_per_km,
                    actuals["actual_pace_sec_per_km"],
                    True,
                    yesterday_wellness_ok,
                )
            else:
                result = autoregulation.evaluate_easy_or_long_run(
                    prescribed_pace or athlete.easy_pace_sec_per_km,
                    actuals["actual_pace_sec_per_km"],
                    actuals["actual_hr"],
                    athlete.aerobic_hr_ceiling,
                    yesterday_wellness_ok,
                )
            if result.action == "progress":
                athlete.threshold_pace_sec_per_km += result.pace_adjustment_sec_per_km
                athlete.easy_pace_sec_per_km += result.pace_adjustment_sec_per_km
            session.status = SessionStatus.COMPLETED
            db.add(
                CompletedSession(
                    planned_session_id=session.id,
                    date=session.date,
                    actual=actuals,
                    feedback=result.note,
                    next_instruction=result.action,
                )
            )
            summary["matched"] += 1
        else:
            # Missed sessions are not made up -- the week re-flows (spec section 5).
            session.status = SessionStatus.MISSED
            summary["missed_marked"] += 1

    db.commit()

    race = (
        db.query(Race)
        .filter(Race.athlete_id == athlete.id, Race.race_date >= today)
        .order_by(Race.race_date)
        .first()
    )
    if race:
        generate_and_persist_plan(db, athlete, race, today=today)
        summary["regenerated"] = True
        summary["sync"] = sync_upcoming_runs_to_intervals(db, athlete, today=today, client=client)

    return summary


def run_daily_job(db: Session, today: date | None = None) -> list[dict]:
    return [run_daily_job_for_athlete(db, athlete, today=today) for athlete in db.query(AthleteProfile).all()]
