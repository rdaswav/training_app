from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.routes import list_exercises, swap_exercise
from app.models import AthleteProfile, PlannedSession, Race, RacePriority, SessionStatus, SessionType
from app.plan_service import generate_and_persist_plan
from app.schemas import ExerciseSwapRequest
from app.seed import seed_exercise_library


def _make_plan(db_session, today: date):
    seed_exercise_library(db_session)
    athlete = AthleteProfile(injury_flags=[])
    db_session.add(athlete)
    db_session.commit()
    db_session.refresh(athlete)
    race = Race(athlete_id=athlete.id, name="Test", race_date=today + timedelta(weeks=14), distance_km=21.1, priority=RacePriority.A)
    db_session.add(race)
    db_session.commit()
    db_session.refresh(race)
    generate_and_persist_plan(db_session, athlete, race, today=today)
    return athlete


def _first_lower_session(db_session, athlete):
    return (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.STRENGTH, PlannedSession.name == "Lower")
        .order_by(PlannedSession.date)
        .first()
    )


def test_swap_exercise_persists_via_json_reassignment(db_session):
    today = date(2026, 7, 8)
    athlete = _make_plan(db_session, today)
    lower = _first_lower_session(db_session, athlete)
    assert lower is not None

    swap_exercise(lower.id, ExerciseSwapRequest(pattern="squat", exercise_name="Leg press"), db_session)

    db_session.expire(lower)
    reloaded = db_session.query(PlannedSession).filter(PlannedSession.id == lower.id).first()
    updated = next(p["exercise_name"] for p in reloaded.content["prescriptions"] if p["pattern"] == "squat")
    assert updated == "Leg press"
    # Other prescriptions in the same session must be untouched.
    hinge = next(p for p in reloaded.content["prescriptions"] if p["pattern"] == "hinge")
    assert hinge["exercise_name"] != "Leg press"


def test_swap_exercise_rejects_completed_session(db_session):
    today = date(2026, 7, 8)
    athlete = _make_plan(db_session, today)
    lower = _first_lower_session(db_session, athlete)
    lower.status = SessionStatus.COMPLETED
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        swap_exercise(lower.id, ExerciseSwapRequest(pattern="squat", exercise_name="Leg press"), db_session)
    assert exc_info.value.status_code == 400


def test_swap_exercise_rejects_unknown_pattern(db_session):
    today = date(2026, 7, 8)
    athlete = _make_plan(db_session, today)
    lower = _first_lower_session(db_session, athlete)

    with pytest.raises(HTTPException) as exc_info:
        swap_exercise(lower.id, ExerciseSwapRequest(pattern="not_a_real_pattern", exercise_name="Leg press"), db_session)
    assert exc_info.value.status_code == 400


def test_list_exercises_filters_by_pattern(db_session):
    seed_exercise_library(db_session)
    squats = list_exercises(pattern="squat", db=db_session)
    assert squats
    assert all(e.pattern == "squat" for e in squats)

    all_exercises = list_exercises(pattern=None, db=db_session)
    assert len(all_exercises) > len(squats)
