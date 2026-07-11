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


def test_macrocycle_persists_a_mesocycle_offset_consistent_with_generated_sessions(db_session):
    """Regression test for #31: the strength mesocycle used to always start
    at offset 0 regardless of the running plan's down-weeks/taper. Confirm
    generate_and_persist_plan computes and stores a real offset on the
    Macrocycle, and that the persisted strength sessions actually reflect
    that same offset (not the old hardcoded 0)."""
    from app.engines.strength import ACCUMULATION_WEEKS, best_mesocycle_offset, mesocycle_week_local

    today = date(2026, 7, 6)  # Monday -- a clean week-aligned start
    athlete, race = _make_athlete_and_race(db_session, today, race_weeks=16)

    macrocycle = generate_and_persist_plan(db_session, athlete, race, today=today)

    from app.models import Phase

    phase_rows = db_session.query(Phase).filter(Phase.macrocycle_id == macrocycle.id).all()
    total_weeks = (macrocycle.end_date - macrocycle.start_date).days // 7 + 1
    taper_phase = next(p for p in phase_rows if p.name == "Taper")
    taper_start_week = (taper_phase.start_date - macrocycle.start_date).days // 7

    expected_offset = best_mesocycle_offset(total_weeks, taper_start_week)
    assert macrocycle.mesocycle_start_week == expected_offset

    # Find a strength session on a deload week per the persisted offset, and
    # confirm its content actually says "Deload" -- proof the offset was
    # really threaded into generation, not just stored decoratively.
    deload_week_indices = [
        w for w in range(total_weeks)
        if mesocycle_week_local(w, expected_offset) == ACCUMULATION_WEEKS
    ]
    strength_sessions = (
        db_session.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.STRENGTH)
        .all()
    )
    deload_dates = {macrocycle.start_date + timedelta(weeks=w) for w in deload_week_indices}
    deload_week_sessions = [s for s in strength_sessions if any(abs((s.date - d).days) < 7 for d in deload_dates)]
    assert deload_week_sessions
    assert any(
        "deload" in p.get("note", "").lower()
        for s in deload_week_sessions
        for p in s.content.get("prescriptions", [])
    )


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


def test_goal_race_time_overrides_race_pace_segments_only(db_session):
    """Race.goal_time_sec flows through to race-pace segments (Build 2's
    race-pace reps, Taper's race-pace touch) but must not change threshold-
    pace segments (Build 1's cruise intervals, which key off the athlete's
    actual current threshold pace, not the goal)."""
    today = date(2026, 7, 8)
    seed_exercise_library(db_session)
    athlete = AthleteProfile(injury_flags=[])
    db_session.add(athlete)
    db_session.commit()
    db_session.refresh(athlete)

    goal_time_sec = 6300  # 1:45:00 half marathon
    race = Race(
        athlete_id=athlete.id,
        name="Goal Half",
        race_date=today + timedelta(weeks=14),
        distance_km=21.1,
        goal_time_sec=goal_time_sec,
        priority=RacePriority.A,
    )
    db_session.add(race)
    db_session.commit()
    db_session.refresh(race)

    generate_and_persist_plan(db_session, athlete, race, today=today)

    build2_quality = (
        db_session.query(PlannedSession)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.type == SessionType.RUN,
            PlannedSession.phase_name == "Build 2",
            PlannedSession.name == "Threshold + race-pace reps",
        )
        .first()
    )
    assert build2_quality is not None
    repeats = [s for s in build2_quality.content["steps"] if s.get("type") == "repeat"]
    race_pace_reps = next(r for r in repeats if r["work"]["distance_km"] == 1.0)
    threshold_reps = next(r for r in repeats if r["work"]["distance_km"] == 2.0)

    assert race_pace_reps["work"]["target_pace_sec_per_km"] == round(goal_time_sec / race.distance_km)
    # Threshold-pace segment must stay tied to the athlete's actual current
    # threshold pace, unaffected by the goal time override.
    assert threshold_reps["work"]["target_pace_sec_per_km"] == athlete.threshold_pace_sec_per_km


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
