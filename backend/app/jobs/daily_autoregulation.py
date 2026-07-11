"""The daily scheduled job (spec section 3 & 10 step 8): pull completed
activities/wellness from intervals.icu since the oldest unmatched planned
session, autoregulate, then refresh and re-sync the next 7-10 days. If the job
runs daily without gaps this is just yesterday; if it missed a few days (e.g.
the server was down), the fetch window widens to cover the whole backlog
instead of just yesterday, so those days get properly matched rather than
marked missed.

Field names used to read intervals.icu activities/wellness (`start_date_local`,
`distance`, `moving_time`, `average_heartrate`, `readiness`) are best-effort
guesses at the documented shape -- unconfirmed against a live account, same
caveat as integrations/intervals_icu.py."""
from __future__ import annotations

from datetime import date, datetime, timedelta

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

# Hard cap on cumulative autoregulated drift from the athlete's profile-set
# baseline pace, per macrocycle-ish window -- prevents a run of "progress"
# results from compounding into an unrealistic prescribed pace. The athlete
# re-baselines manually in Settings once fitness has genuinely improved.
MAX_PACE_DRIFT_SEC_PER_KM = 20


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


def _best_matching_activity(candidates: list[dict], session: PlannedSession) -> dict | None:
    """When multiple activities land on the same date (e.g. a shakeout plus
    the prescribed session), prefer whichever is closest in distance to the
    planned session over pure last-write-wins by fetch order."""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    prescribed_km = session.content.get("total_distance_km")

    def _distance_diff(activity: dict) -> float:
        distance_m = activity.get("distance")
        if prescribed_km is None or not distance_m:
            return float("inf")
        return abs(distance_m / 1000 - prescribed_km)

    return min(candidates, key=_distance_diff)


def _clamp_to_baseline(current: int, baseline: int, adjustment: int) -> int:
    new_value = current + adjustment
    return max(baseline - MAX_PACE_DRIFT_SEC_PER_KM, min(baseline + MAX_PACE_DRIFT_SEC_PER_KM, new_value))


def run_daily_job_for_athlete(
    db: Session,
    athlete: AthleteProfile,
    today: date | None = None,
    client: IntervalsIcuClient | None = None,
) -> dict:
    """Wraps _run_daily_job_for_athlete to record job health (last run,
    last error) on the athlete's profile regardless of outcome (#33), so a
    silent failure is visible in Settings rather than only as gaps in
    training history days later."""
    try:
        summary = _run_daily_job_for_athlete(db, athlete, today=today, client=client)
    except Exception as exc:
        db.rollback()
        athlete.last_job_run_at = datetime.utcnow()
        athlete.last_job_error = str(exc)[:500]
        db.commit()
        raise
    athlete.last_job_run_at = datetime.utcnow()
    athlete.last_job_error = None
    db.commit()
    return summary


def _run_daily_job_for_athlete(
    db: Session,
    athlete: AthleteProfile,
    today: date | None = None,
    client: IntervalsIcuClient | None = None,
) -> dict:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    summary = {"athlete_id": athlete.id, "matched": 0, "missed_marked": 0, "regenerated": False, "sync": None}

    stale_sessions = (
        db.query(PlannedSession)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.date < today,
            PlannedSession.status == SessionStatus.PLANNED,
        )
        .all()
    )
    fetch_start = min([s.date for s in stale_sessions] + [yesterday])

    activities_by_date: dict[date, list[dict]] = {}
    wellness_by_date: dict[date, dict] = {}
    activities_fetch_ok = True
    if intervals_icu_configured():
        client = client or IntervalsIcuClient()
        try:
            for activity in client.get_activities(fetch_start, yesterday):
                d = _parse_date(activity.get("start_date_local") or activity.get("start_date"))
                if d:
                    activities_by_date.setdefault(d, []).append(activity)
        except Exception:  # noqa: BLE001 -- third-party call must not abort the job
            activities_fetch_ok = False
        try:
            for record in client.get_wellness(fetch_start, yesterday):
                d = _parse_date(record.get("id") or record.get("date"))
                if d:
                    wellness_by_date[d] = record
        except Exception:  # noqa: BLE001
            pass

    for session in stale_sessions:
        activity = _best_matching_activity(activities_by_date.get(session.date, []), session)
        if session.type == SessionType.RUN and activity:
            actuals = _extract_run_actuals(activity)
            session_wellness_ok = _wellness_ok(wellness_by_date.get(session.date))
            role = session.content.get("role")
            steps = session.content.get("steps", [])
            prescribed_pace = next((s["target_pace_sec_per_km"] for s in steps if s.get("target_pace_sec_per_km")), None)
            if role == "quality":
                result = autoregulation.evaluate_quality_session(
                    prescribed_pace or athlete.threshold_pace_sec_per_km,
                    actuals["actual_pace_sec_per_km"],
                    True,
                    session_wellness_ok,
                )
                if result.action == "progress":
                    # Automated matching can't confirm reps were actually hit --
                    # only a manually-logged confirmation should progress a
                    # quality-session pattern (see #26/#35).
                    result = autoregulation.RunAutoregResult(
                        "hold", 0,
                        "Hit target pace via automated matching, but reps weren't manually "
                        "confirmed -- log this session to progress.",
                    )
            else:
                result = autoregulation.evaluate_easy_or_long_run(
                    prescribed_pace or athlete.easy_pace_sec_per_km,
                    actuals["actual_pace_sec_per_km"],
                    actuals["actual_hr"],
                    athlete.aerobic_hr_ceiling,
                    session_wellness_ok,
                )
            if result.action in ("progress", "soften"):
                if role == "quality":
                    athlete.threshold_pace_sec_per_km = _clamp_to_baseline(
                        athlete.threshold_pace_sec_per_km,
                        athlete.threshold_pace_baseline_sec_per_km,
                        result.pace_adjustment_sec_per_km,
                    )
                else:
                    athlete.easy_pace_sec_per_km = _clamp_to_baseline(
                        athlete.easy_pace_sec_per_km,
                        athlete.easy_pace_baseline_sec_per_km,
                        result.pace_adjustment_sec_per_km,
                    )
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
        elif session.type == SessionType.RUN and not activities_fetch_ok:
            # Transient intervals.icu fetch failure -- leave PLANNED rather than
            # marking a phantom miss; the next successful job run picks it up.
            continue
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
