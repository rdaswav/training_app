from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.engines import autoregulation
from app.models import (
    AthleteProfile,
    CompletedSession,
    PlannedSession,
    Race,
    SessionStatus,
    SessionType,
)
from app.plan_service import generate_and_persist_plan
from app.schemas import (
    AthleteOut,
    AthleteUpdate,
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
    race = Race(athlete_id=athlete.id, **payload.model_dump())
    db.add(race)
    db.commit()
    db.refresh(race)
    generate_and_persist_plan(db, athlete, race)
    return race


@router.get("/races", response_model=list[RaceOut])
def list_races(db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    return db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).all()


@router.post("/races/{race_id}/regenerate", response_model=RaceOut)
def regenerate_plan(race_id: int, db: Session = Depends(get_db)):
    athlete = get_or_create_athlete(db)
    race = db.query(Race).filter(Race.id == race_id, Race.athlete_id == athlete.id).first()
    if not race:
        raise HTTPException(404, "Race not found")
    generate_and_persist_plan(db, athlete, race)
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
    return {"status": "regenerated", "macrocycle_start": macrocycle.start_date, "macrocycle_end": macrocycle.end_date}
