"""Pushes upcoming planned run sessions to intervals.icu so they sync to the
Fenix (spec section 10, MVP step 4). Guarded by whether credentials are
configured: with no INTERVALS_ICU_API_KEY / INTERVALS_ICU_ATHLETE_ID set,
this is a safe no-op -- see integrations/intervals_icu.py for the confirmed
wire format (verified against a live account 2026-07-09)."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import INTERVALS_ICU_API_KEY, INTERVALS_ICU_ATHLETE_ID
from app.engines.running import RunSessionPlan, RunStep
from app.integrations.intervals_icu import IntervalsIcuClient
from app.models import AthleteProfile, PlannedSession, SessionStatus, SessionType

DEFAULT_SYNC_WINDOW_DAYS = 10


def intervals_icu_configured() -> bool:
    return bool(INTERVALS_ICU_API_KEY and INTERVALS_ICU_ATHLETE_ID)


def _to_run_session_plan(session: PlannedSession) -> RunSessionPlan:
    steps = [
        RunStep(
            label=s["label"],
            duration_min=s.get("duration_min"),
            distance_km=s.get("distance_km"),
            target_pace_sec_per_km=s.get("target_pace_sec_per_km"),
            hr_ceiling=s.get("hr_ceiling"),
        )
        for s in session.content.get("steps", [])
    ]
    return RunSessionPlan(
        date=session.date,
        name=session.name,
        phase_name=session.phase_name or "",
        steps=steps,
        total_distance_km=session.content.get("total_distance_km", 0.0),
        role=session.content.get("role", ""),
    )


def sync_upcoming_runs_to_intervals(
    db: Session,
    athlete: AthleteProfile,
    today: date | None = None,
    window_days: int = DEFAULT_SYNC_WINDOW_DAYS,
    client: IntervalsIcuClient | None = None,
) -> dict:
    """Upsert every still-planned run session in [today, today+window_days] onto
    the intervals.icu calendar. Returns a small summary dict; never raises --
    a single bad write is logged into the result and skipped rather than
    aborting the rest of the batch, since intervals.icu is a third party."""
    if not intervals_icu_configured():
        return {"skipped": "intervals.icu not configured", "synced": 0, "failed": 0}

    today = today or date.today()
    client = client or IntervalsIcuClient()

    sessions = (
        db.query(PlannedSession)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.type == SessionType.RUN,
            PlannedSession.status == SessionStatus.PLANNED,
            PlannedSession.date >= today,
            PlannedSession.date <= today + timedelta(days=window_days),
        )
        .all()
    )

    synced, failures = 0, []
    for session in sessions:
        try:
            plan = _to_run_session_plan(session)
            result = client.upsert_planned_workout(
                plan, existing_event_id=session.intervals_icu_event_id, max_hr=athlete.max_hr
            )
            session.intervals_icu_event_id = str(result.get("id")) if result.get("id") is not None else session.intervals_icu_event_id
            synced += 1
        except Exception as exc:  # noqa: BLE001 -- third-party call, don't let one bad write abort the batch
            failures.append({"session_id": session.id, "date": session.date.isoformat(), "error": str(exc)})

    db.commit()
    return {"synced": synced, "failed": len(failures), "failures": failures}
