from datetime import date, timedelta

from app.engines.strength import best_e1rm_from_sets, prescribe_next_load
from app.engines.autoregulation import StrengthLogSet
from app.main import _attach_suggested_loads
from app.models import AthleteProfile, CompletedSession, PlannedSession, SessionStatus, SessionType


def _make_athlete(db_session):
    athlete = AthleteProfile(injury_flags=[])
    db_session.add(athlete)
    db_session.commit()
    db_session.refresh(athlete)
    return athlete


def test_attach_suggested_loads_uses_the_most_recent_logged_session_for_the_pattern(db_session):
    """Regression test for #28: the log form should suggest an actual kg
    target derived from the athlete's most recent logged session for that
    movement pattern, not just show a bare progress/hold/back-off label."""
    athlete = _make_athlete(db_session)
    today = date(2026, 7, 8)

    prior_session = PlannedSession(
        athlete_id=athlete.id,
        date=today - timedelta(days=7),
        type=SessionType.STRENGTH,
        name="Lower",
        status=SessionStatus.COMPLETED,
        content={"prescriptions": [{"pattern": "squat", "category": "compound", "sets": 3, "reps": "3-5", "rir": 2.0}]},
    )
    db_session.add(prior_session)
    db_session.commit()
    db_session.add(
        CompletedSession(
            planned_session_id=prior_session.id,
            date=prior_session.date,
            actual={"pattern": "squat", "sets": [{"reps": 5, "weight_kg": 100, "rir_actual": 2.0}]},
        )
    )
    db_session.commit()

    today_session = PlannedSession(
        athlete_id=athlete.id,
        date=today,
        type=SessionType.STRENGTH,
        name="Lower",
        status=SessionStatus.PLANNED,
        content={"prescriptions": [{"pattern": "squat", "category": "compound", "sets": 3, "reps": "3-5", "rir": 1.7}]},
    )
    db_session.add(today_session)
    db_session.commit()
    db_session.refresh(today_session)

    _attach_suggested_loads(db_session, athlete, [today_session])

    e1rm = best_e1rm_from_sets([StrengthLogSet(reps=5, weight_kg=100, rir_actual=2.0)])
    expected = prescribe_next_load(e1rm, "3-5", 1.7)  # today's own prescription's reps/RIR
    assert today_session.suggested_loads == {"squat": expected}


def test_attach_suggested_loads_omits_patterns_with_no_prior_log(db_session):
    athlete = _make_athlete(db_session)
    session = PlannedSession(
        athlete_id=athlete.id,
        date=date(2026, 7, 8),
        type=SessionType.STRENGTH,
        name="Lower",
        status=SessionStatus.PLANNED,
        content={"prescriptions": [{"pattern": "squat", "category": "compound", "sets": 3, "reps": "3-5", "rir": 2.0}]},
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    _attach_suggested_loads(db_session, athlete, [session])

    assert session.suggested_loads == {}


def test_attach_suggested_loads_is_a_noop_with_no_strength_sessions(db_session):
    athlete = _make_athlete(db_session)
    run_session = PlannedSession(
        athlete_id=athlete.id, date=date(2026, 7, 8), type=SessionType.RUN, name="Easy run",
        status=SessionStatus.PLANNED, content={},
    )
    db_session.add(run_session)
    db_session.commit()
    db_session.refresh(run_session)

    _attach_suggested_loads(db_session, athlete, [run_session])  # must not raise
    assert not hasattr(run_session, "suggested_loads")
