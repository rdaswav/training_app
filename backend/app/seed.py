"""Seed data: default exercise library, tagged by movement pattern + injury tags."""
from sqlalchemy.orm import Session

from app.models import Exercise

EXERCISE_LIBRARY = [
    # pattern, name, injury_tags, is_compound
    ("horizontal_push", "Barbell bench press", ["shoulder"], True),
    ("horizontal_push", "Dumbbell bench press", [], True),
    ("horizontal_push", "Push-up", [], False),
    ("vertical_pull", "Pull-up", [], True),
    ("vertical_pull", "Lat pulldown", [], True),
    ("horizontal_pull", "Barbell row", ["lower_back"], True),
    ("horizontal_pull", "Chest-supported row", [], True),
    ("shoulder_accessory", "Lateral raise", [], False),
    ("shoulder_accessory", "Face pull", [], False),
    ("core", "Plank", [], False),
    ("core", "Pallof press", [], False),
    ("core", "Dead bug", [], False),
    ("squat", "Back squat", ["lower_back", "knee"], True),
    ("squat", "Front squat", ["lower_back"], True),
    ("squat", "Goblet squat", ["knee"], True),
    ("squat", "Leg press", [], True),
    ("hinge", "Conventional deadlift", ["lower_back"], True),
    ("hinge", "Romanian deadlift", ["lower_back"], True),
    ("hinge", "Hip thrust", [], True),
    ("hinge", "Cable pull-through", [], True),
    ("single_leg", "Bulgarian split squat", ["knee"], False),
    ("single_leg", "Walking lunge", ["knee"], False),
    ("single_leg", "Step-up", ["knee"], False),
    ("unilateral", "Single-arm dumbbell row", [], False),
    ("unilateral", "Single-leg RDL", ["lower_back"], False),
    ("carry", "Farmer's carry", [], False),
    ("carry", "Suitcase carry", [], False),
    ("posterior_chain", "Back extension", ["lower_back"], False),
    ("posterior_chain", "Nordic curl", [], False),
    ("posterior_chain", "Glute bridge", [], False),
    ("core_running_support", "Copenhagen plank", ["knee"], False),
    ("core_running_support", "Single-leg calf raise", [], False),
    ("core_running_support", "Side plank", [], False),
]


def seed_exercise_library(db: Session) -> None:
    if db.query(Exercise).count() > 0:
        return
    for pattern, name, injury_tags, is_compound in EXERCISE_LIBRARY:
        db.add(
            Exercise(
                name=name,
                pattern=pattern,
                injury_tags=injury_tags,
                is_compound=is_compound,
                rep_range="3-5" if is_compound else "8-12",
            )
        )
    db.commit()
