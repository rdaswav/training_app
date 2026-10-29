from app.models import Exercise
from app.seed import EXERCISE_LIBRARY, seed_exercise_library


def test_seed_adds_missing_exercises_to_an_existing_library_without_duplicates(db_session):
    # Simulate a database seeded before the plyometrics existed.
    db_session.add(Exercise(name="Back squat", pattern="squat", injury_tags=[], is_compound=True, movement_type="weighted", rep_range="3-5"))
    db_session.commit()

    seed_exercise_library(db_session)
    seed_exercise_library(db_session)  # idempotent

    names = [n for (n,) in db_session.query(Exercise.name).all()]
    assert len(names) == len(set(names)) == len(EXERCISE_LIBRARY)
    pogo = db_session.query(Exercise).filter_by(name="Pogo hops").one()
    assert pogo.movement_type == "plyometric" and pogo.rep_range == "5-8"
