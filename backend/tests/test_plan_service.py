from datetime import date, timedelta

from app.models import (
    AthleteProfile,
    CompletedSession,
    PlannedSession,
    Race,
    RacePriority,
    SessionStatus,
    SessionType,
)
from app.plan_service import generate_and_persist_plan
from app.seed import seed_exercise_library


def _make_athlete_and_race(db_session, today: date, race_weeks: int = 14) -> tuple[AthleteProfile, Race]:
    seed_exercise_library(db_session)
    athlete = AthleteProfile(injury_flags=[])
    db_session.add(athlete)
    db_session.commit()
    db_session.refresh(athlete)

    race = Race(
        athlete_id=athlete.id,
        name="Test Half",
        race_date=today + timedelta(weeks=race_weeks),
        distance_km=21.1,
        priority=RacePriority.A,
    )
    db_session.add(race)
    db_session.commit()
    db_session.refresh(race)
    return athlete, race


def test_generate_plan_creates_sessions_from_today_only(db_session):
    today = date(2026, 7, 8)  # Wednesday
    athlete, race = _make_athlete_and_race(db_session, today)

    generate_and_persist_plan(db_session, athlete, race, today=today)

    sessions = db_session.query(PlannedSession).filter(PlannedSession.athlete_id == athlete.id).all()
    assert sessions
    assert all(s.date >= today for s in sessions)


def test_quality_session_repeat_step_serializes_with_discriminator(db_session):
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    build1_quality = (
        db_session.query(PlannedSession)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.type == SessionType.RUN,
            PlannedSession.phase_name == "Build 1",
            PlannedSession.name == "Cruise intervals",
        )
        .first()
    )
    assert build1_quality is not None
    repeat_steps = [s for s in build1_quality.content["steps"] if s.get("type") == "repeat"]
    assert len(repeat_steps) == 1
    repeat = repeat_steps[0]
    assert repeat["repeat_count"] == 3
    assert set(repeat["work"].keys()) == {"label", "duration_min", "distance_km", "target_pace_sec_per_km", "hr_ceiling"}
    assert repeat["work"]["distance_km"] == 1.6
    assert repeat["recovery"]["duration_min"] == 1.5


def test_regenerating_plan_preserves_completed_sessions(db_session):
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)

    generate_and_persist_plan(db_session, athlete, race, today=today)

    # Not every calendar day has a session (rest days, and the adjacency
    # guardrail can shuffle a day to rest) -- take whichever session lands
    # first on or after today.
    first_session = (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= today)
        .order_by(PlannedSession.date)
        .first()
    )
    assert first_session is not None
    marked_date = first_session.date
    original_id = first_session.id
    first_session.status = SessionStatus.COMPLETED
    db_session.add(CompletedSession(planned_session_id=first_session.id, date=marked_date, actual={"logged": True}, feedback="ok", next_instruction="hold"))
    db_session.commit()

    # Simulate the daily job re-running the same day: injury flags changed, plan regenerates.
    athlete.injury_flags = ["knee"]
    db_session.commit()
    generate_and_persist_plan(db_session, athlete, race, today=today)

    # The completed session must survive untouched.
    reloaded = db_session.query(PlannedSession).filter(PlannedSession.id == original_id).first()
    assert reloaded is not None
    assert reloaded.status == SessionStatus.COMPLETED
    assert reloaded.completed is not None

    # No duplicate planned session was created for that date.
    same_day_sessions = db_session.query(PlannedSession).filter(
        PlannedSession.athlete_id == athlete.id, PlannedSession.date == marked_date
    ).all()
    assert len(same_day_sessions) == 1

    # A future "Lower" strength session should reflect the new injury flag --
    # proof that still-planned future sessions were actually regenerated, not
    # just left in place.
    future_lower = (
        db_session.query(PlannedSession)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.date > marked_date,
            PlannedSession.name == "Lower",
            PlannedSession.status == SessionStatus.PLANNED,
        )
        .order_by(PlannedSession.date)
        .first()
    )
    assert future_lower is not None
    squat_prescription = next(p for p in future_lower.content["prescriptions"] if p["pattern"] == "squat")
    assert squat_prescription["exercise_name"] == "Front squat"  # knee flag excludes Back squat/Goblet squat


def test_future_plan_start_date_is_sticky_across_regeneration(db_session):
    """Regression test: a race created with plan_start_date in the future must
    keep that anchor on later regenerations (LLM edits, the daily job) even
    though those calls don't re-specify it -- otherwise the plan silently
    snaps to whatever day the regeneration happens to run on."""
    today = date(2026, 7, 9)
    start = date(2026, 8, 10)
    athlete, race = _make_athlete_and_race(db_session, today, race_weeks=13)

    generate_and_persist_plan(db_session, athlete, race, plan_start_date=start, today=today)

    sessions = db_session.query(PlannedSession).filter(PlannedSession.athlete_id == athlete.id).all()
    assert sessions
    assert all(s.date >= start for s in sessions), "no sessions should exist before the requested plan start"

    # Regenerate exactly like /api/plan/apply or /api/races/{id}/regenerate do:
    # no plan_start_date given, "today" has moved forward a day.
    generate_and_persist_plan(db_session, athlete, race, today=today + timedelta(days=1))

    sessions_after = db_session.query(PlannedSession).filter(PlannedSession.athlete_id == athlete.id).all()
    assert sessions_after
    assert all(s.date >= start for s in sessions_after), "regeneration must not pull the plan's start date forward to today"


def test_regenerate_does_not_touch_past_dates(db_session):
    today = date(2026, 7, 8)
    athlete, race = _make_athlete_and_race(db_session, today)
    generate_and_persist_plan(db_session, athlete, race, today=today)

    # Manually insert a stray past-dated session to ensure it's left alone.
    past = PlannedSession(
        athlete_id=athlete.id,
        date=today - timedelta(days=1),
        type=SessionType.RUN,
        name="Old run",
        status=SessionStatus.MISSED,
        content={},
    )
    db_session.add(past)
    db_session.commit()

    generate_and_persist_plan(db_session, athlete, race, today=today)

    reloaded = db_session.query(PlannedSession).filter(PlannedSession.id == past.id).first()
    assert reloaded is not None
    assert reloaded.status == SessionStatus.MISSED
