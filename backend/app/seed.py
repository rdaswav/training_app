"""Seed data: default exercise library, tagged by movement pattern + injury tags."""
from sqlalchemy.orm import Session

from app.models import Exercise

WEIGHTED = "weighted"
BODYWEIGHT_REPS = "bodyweight_reps"
BODYWEIGHT_TIMED = "bodyweight_timed"

EXERCISE_LIBRARY = [
    # pattern, name, injury_tags, is_compound, movement_type
    ("horizontal_push", "Barbell bench press", ["shoulder"], True, WEIGHTED),
    ("horizontal_push", "Dumbbell bench press", [], True, WEIGHTED),
    ("horizontal_push", "Push-up", [], False, BODYWEIGHT_REPS),
    ("vertical_pull", "Pull-up", [], True, BODYWEIGHT_REPS),
    ("vertical_pull", "Lat pulldown", [], True, WEIGHTED),
    ("horizontal_pull", "Barbell row", ["lower_back"], True, WEIGHTED),
    ("horizontal_pull", "Chest-supported row", [], True, WEIGHTED),
    ("shoulder_accessory", "Lateral raise", [], False, WEIGHTED),
    ("shoulder_accessory", "Face pull", [], False, WEIGHTED),
    ("core", "Plank", [], False, BODYWEIGHT_TIMED),
    ("core", "Pallof press", [], False, WEIGHTED),
    ("core", "Dead bug", [], False, BODYWEIGHT_REPS),
    ("squat", "Back squat", ["lower_back", "knee"], True, WEIGHTED),
    ("squat", "Front squat", ["lower_back"], True, WEIGHTED),
    ("squat", "Goblet squat", ["knee"], True, WEIGHTED),
    ("squat", "Leg press", [], True, WEIGHTED),
    ("hinge", "Conventional deadlift", ["lower_back"], True, WEIGHTED),
    ("hinge", "Romanian deadlift", ["lower_back"], True, WEIGHTED),
    ("hinge", "Hip thrust", [], True, WEIGHTED),
    ("hinge", "Cable pull-through", [], True, WEIGHTED),
    ("single_leg", "Bulgarian split squat", ["knee"], False, WEIGHTED),
    ("single_leg", "Walking lunge", ["knee"], False, WEIGHTED),
    ("single_leg", "Step-up", ["knee"], False, WEIGHTED),
    ("unilateral", "Single-arm dumbbell row", [], False, WEIGHTED),
    ("unilateral", "Single-leg RDL", ["lower_back"], False, WEIGHTED),
    ("carry", "Farmer's carry", [], False, WEIGHTED),
    ("carry", "Suitcase carry", [], False, WEIGHTED),
    ("posterior_chain", "Back extension", ["lower_back"], False, WEIGHTED),
    ("posterior_chain", "Nordic curl", [], False, BODYWEIGHT_REPS),
    ("posterior_chain", "Glute bridge", [], False, BODYWEIGHT_REPS),
    ("core_running_support", "Copenhagen plank", ["knee"], False, BODYWEIGHT_TIMED),
    ("core_running_support", "Single-leg calf raise", [], False, BODYWEIGHT_REPS),
    ("core_running_support", "Side plank", [], False, BODYWEIGHT_TIMED),
]


def seed_exercise_library(db: Session) -> None:
    if db.query(Exercise).count() > 0:
        return
    for pattern, name, injury_tags, is_compound, movement_type in EXERCISE_LIBRARY:
        # bodyweight_timed's rep_range is seconds-per-hold, not a rep count --
        # see Exercise.movement_type's docstring in models.py.
        rep_range = "20-40" if movement_type == BODYWEIGHT_TIMED else ("3-5" if is_compound else "8-12")
        db.add(
            Exercise(
                name=name,
                pattern=pattern,
                injury_tags=injury_tags,
                is_compound=is_compound,
                movement_type=movement_type,
                rep_range=rep_range,
            )
        )
    db.commit()
