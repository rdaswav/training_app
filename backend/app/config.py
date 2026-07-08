import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'training_app.db'}")

INTERVALS_ICU_API_KEY = os.environ.get("INTERVALS_ICU_API_KEY", "")
INTERVALS_ICU_ATHLETE_ID = os.environ.get("INTERVALS_ICU_ATHLETE_ID", "")
INTERVALS_ICU_BASE_URL = os.environ.get("INTERVALS_ICU_BASE_URL", "https://intervals.icu/api/v1")

# Fixed weekly schedule (day-of-week indices, Monday=0) per spec section 7.
# Adjustable, but this is the default single-user template.
DEFAULT_WEEK_TEMPLATE = {
    0: "strength",  # Mon
    1: "run",       # Tue
    2: "strength",  # Wed
    3: "run",       # Thu
    4: "strength",  # Fri
    5: "run",       # Sat (long run)
    6: "rest",      # Sun
}
