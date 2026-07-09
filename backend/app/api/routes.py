from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import INTERVALS_ICU_API_KEY, INTERVALS_ICU_ATHLETE_ID
from app.db import get_db
from app.engines import autoregulation
from app.intervals_sync import sync_upcoming_runs_to_intervals
from app.jobs.daily_autoregulation import run_daily_job
from app.models import (
    AthleteProfile,
    CompletedSession,
    Exercise,
    PlannedSession,
    Race,
    SessionStatus,
    SessionType,
)
from app.plan_service import generate_and_persist_plan
from app.schemas import (
    AthleteOut,
    AthleteUpdate,
    ExerciseOut,
    ExerciseSwapRequest,
    PlanApplyRequest,
    RaceCreate,
    RaceOut,
    RunCompleteRequest,
    SessionOut,
    StrengthLogRequest,
)

router = APIRouter(prefix="/api")


def get_or_create_athlete(db: Session) -> AthleteProfile:
    athlete = db.query(AthleteProfile).first()
    if athlete is None:
        from app.config import DEFAULT_WEEK_TEMPLATE

        athlete = AthleteProfile(week_template=DEFAULT_WEEK_TEMPLATE, injury_flags=[])
        db.add(athlete)
        db.commit()
        db.refresh(athlete)
    return athlete


@router.get("/athlete", response_model=AthleteOut)
def get_athlete(db: Session = Depends(get_db)):
    return get_or_create_athlete(db)


@router.put("/athlete", response_model=AthleteOut)
def update_athlete(payload: AthleteUpdate, db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(athlete, field, value)
    db.commit()
    db.refresh(athlete)
    return athlete


@router.post("/races", response_model=RaceOut)
def create_race(payload: RaceCreate, db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    fields = payload.model_dump()
    plan_start_date = fields.pop("plan_start_date")
    race = Race(athlete_id=athlete.id, **fields)
    db.add(race)
    db.commit()
    db.refresh(race)
    generate_and_persist_plan(db, athlete, race, plan_start_date=plan_start_date)
    sync_upcoming_runs_to_intervals(db, athlete)
    return race


@router.get("/races", response_model=list[RaceOut])
def list_races(db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    return db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).all()


@router.delete("/races/{race_id}")
def delete_race(race_id: int, db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    race = db.query(Race).filter(Race.id == race_id, Race.athlete_id == athlete.id).first()
    if not race:
        raise HTTPException(404, "Race not found")
    db.query(PlannedSession).filter(
        PlannedSession.athlete_id == athlete.id,
        PlannedSession.status == SessionStatus.PLANNED,
    ).delete(synchronize_session="fetch")
    db.delete(race)
    db.commit()
    return {"status": "deleted"}


@router.post("/races/{race_id}/regenerate", response_model=RaceOut)
def regenerate_plan(race_id: int, db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    race = db.query(Race).filter(Race.id == race_id, Race.athlete_id == athlete.id).first()
    if not race:
        raise HTTPException(404, "Race not found")
    generate_and_persist_plan(db, athlete, race)
    sync_upcoming_runs_to_intervals(db, athlete)
    return race


@router.get("/calendar", response_model=list[SessionOut])
def get_calendar(start: date, end: date, db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    sessions = (
        db.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= start, PlannedSession.date <= end)
        .order_by(PlannedSession.date)
        .all()
    )
    return sessions


@router.get("/today", response_model=list[SessionOut])
def get_today(db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    today = date.today()
    return (
        db.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date == today)
        .all()
    )


def _get_planned_session(db: Session, session_id: int) -> PlannedSession:
    session = db.query(PlannedSession).filter(PlannedSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    return session


@router.post("/sessions/{session_id}/complete")
def complete_run_session(session_id: int, payload: RunCompleteRequest, db: Session = Depends(get_db)):
    session = _get_planned_session(db, session_id)
    if session.type != SessionType.RUN:
        raise HTTPException(400, "Not a run session")

    athlete = db.query(AthleteProfile).filter(AthleteProfile.id == session.athlete_id).first()
    role = session.content.get("role")
    steps = session.content.get("steps", [])
    prescribed_pace = next((s["target_pace_sec_per_km"] for s in steps if s.get("target_pace_sec_per_km")), None)

    if role == "quality":
        result = autoregulation.evaluate_quality_session(
            prescribed_pace or athlete.threshold_pace_sec_per_km, payload.actual_pace_sec_per_km, payload.hit_reps, payload.wellness_ok
        )
    else:
        result = autoregulation.evaluate_easy_or_long_run(
            prescribed_pace or athlete.easy_pace_sec_per_km,
            payload.actual_pace_sec_per_km,
            payload.actual_hr,
            athlete.aerobic_hr_ceiling,
            payload.wellness_ok,
        )

    if result.action == "progress":
        athlete.threshold_pace_sec_per_km += result.pace_adjustment_sec_per_km
        athlete.easy_pace_sec_per_km += result.pace_adjustment_sec_per_km

    session.status = SessionStatus.COMPLETED
    completed = CompletedSession(
        planned_session_id=session.id,
        date=session.date,
        actual=payload.model_dump(),
        feedback=result.note,
        next_instruction=result.action,
    )
    db.add(completed)
    db.commit()
    return {"action": result.action, "note": result.note}


@router.post("/sessions/{session_id}/log")
def log_strength_session(session_id: int, payload: StrengthLogRequest, db: Session = Depends(get_db)):
    session = _get_planned_session(db, session_id)
    if session.type != SessionType.STRENGTH:
        raise HTTPException(400, "Not a strength session")

    prescriptions = session.content.get("prescriptions", [])
    prescription_dict = next((p for p in prescriptions if p["pattern"] == payload.pattern), None)
    if not prescription_dict:
        raise HTTPException(400, f"No prescription for pattern '{payload.pattern}' in this session")

    from app.engines.strength import StrengthPrescription

    prescription = StrengthPrescription(
        pattern=prescription_dict["pattern"],
        category=prescription_dict["category"],
        sets=prescription_dict["sets"],
        reps=prescription_dict["reps"],
        rir=prescription_dict["rir"],
        note=prescription_dict["note"],
    )
    logged_sets = [autoregulation.StrengthLogSet(**s.model_dump()) for s in payload.sets]
    result = autoregulation.evaluate_strength_log(prescription, logged_sets)

    session.status = SessionStatus.COMPLETED
    completed = CompletedSession(
        planned_session_id=session.id,
        date=session.date,
        actual={"pattern": payload.pattern, "sets": [s.model_dump() for s in payload.sets]},
        feedback=result.feedback,
        next_instruction=result.next_instruction,
    )
    db.add(completed)
    db.commit()
    return {"summary": result.summary, "feedback": result.feedback, "next_instruction": result.next_instruction, "action": result.action}


@router.get("/exercises", response_model=list[ExerciseOut])
def list_exercises(pattern: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Exercise)
    if pattern:
        query = query.filter(Exercise.pattern == pattern)
    return query.order_by(Exercise.name).all()


@router.patch("/sessions/{session_id}/exercise")
def swap_exercise(session_id: int, payload: ExerciseSwapRequest, db: Session = Depends(get_db)):
    """Manually substitute the exercise for one pattern in a still-planned
    strength session -- unlike injury-flag substitution, this is a free pick
    within the pattern (engines/strength.py's select_exercise already supports
    arbitrary pattern-scoped selection; this just exposes it as an edit)."""
    session = _get_planned_session(db, session_id)
    if session.type != SessionType.STRENGTH:
        raise HTTPException(400, "Not a strength session")
    if session.status != SessionStatus.PLANNED:
        raise HTTPException(400, "Only still-planned sessions can be edited")

    prescriptions = session.content.get("prescriptions", [])
    new_prescriptions = []
    found = False
    for p in prescriptions:
        if p["pattern"] == payload.pattern:
            new_prescriptions.append({**p, "exercise_name": payload.exercise_name})
            found = True
        else:
            new_prescriptions.append(p)
    if not found:
        raise HTTPException(400, f"No prescription for pattern '{payload.pattern}' in this session")

    # Reassign (not mutate in place) so SQLAlchemy detects the JSON column changed.
    session.content = {**session.content, "prescriptions": new_prescriptions}
    db.commit()
    return {"status": "updated"}


@router.get("/plan/export")
def export_plan(db: Session = Depends(get_db)):
    """Structured JSON export for the LLM edit path (spec section 8)."""
    athlete = get_or_create_athlete(db)
    races = db.query(Race).filter(Race.athlete_id == athlete.id).all()
    return {
        "athlete": AthleteOut.model_validate(athlete).model_dump(),
        "races": [
            {
                **RaceOut.model_validate(race).model_dump(),
                "phases": [
                    {"name": p.name, "start_date": p.start_date, "end_date": p.end_date, "focus": p.focus}
                    for p in (race.macrocycle.phases if race.macrocycle else [])
                ],
            }
            for race in races
        ],
    }


@router.post("/plan/apply")
def apply_plan_edit(payload: PlanApplyRequest, db: Session = Depends(get_db)):
    """Apply a structured edit (race date shift, injury flag, volume change) and
    regenerate downstream weeks -- the LLM edit path (spec section 8)."""
    athlete = get_or_create_athlete(db)
    race = db.query(Race).filter(Race.id == payload.race_id, Race.athlete_id == athlete.id).first()
    if not race:
        raise HTTPException(404, "Race not found")

    if payload.race_date is not None:
        race.race_date = payload.race_date
    if payload.weekly_volume_km is not None:
        athlete.weekly_volume_km = payload.weekly_volume_km
    if payload.injury_flags is not None:
        athlete.injury_flags = payload.injury_flags

    db.commit()
    macrocycle = generate_and_persist_plan(db, athlete, race)
    sync_result = sync_upcoming_runs_to_intervals(db, athlete)
    return {
        "status": "regenerated",
        "macrocycle_start": macrocycle.start_date,
        "macrocycle_end": macrocycle.end_date,
        "intervals_icu_sync": sync_result,
    }


@router.post("/jobs/daily-autoregulation")
def trigger_daily_job(db: Session = Depends(get_db)):
    """Manual trigger for the daily job (spec section 3) -- useful for ops/testing
    without waiting for the in-process scheduler (see main.py)."""
    return run_daily_job(db)


@router.get("/config-check")
def config_check():
    """Reports whether the running process sees the intervals.icu credentials as
    non-empty -- never returns the actual values. Diagnostic-only, for verifying
    Fly secrets actually reached a deployed instance."""
    return {
        "intervals_icu_api_key_set": bool(INTERVALS_ICU_API_KEY),
        "intervals_icu_athlete_id_set": bool(INTERVALS_ICU_ATHLETE_ID),
    }
