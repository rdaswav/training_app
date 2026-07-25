import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'training_app.db'}")

INTERVALS_ICU_API_KEY = os.environ.get("INTERVALS_ICU_API_KEY", "")
INTERVALS_ICU_ATHLETE_ID = os.environ.get("INTERVALS_ICU_ATHLETE_ID", "")
INTERVALS_ICU_BASE_URL = os.environ.get("INTERVALS_ICU_BASE_URL", "https://intervals.icu/api/v1")

# HTTP Basic Auth in front of the whole app. Disabled (no auth) unless both
# are set -- keeps local dev/tests working with zero setup.
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")

# Local hour the daily autoregulation job runs at (spec section 3: pull
# yesterday's sessions, autoregulate, refresh the next 7-10 days).
DAILY_JOB_HOUR = int(os.environ.get("DAILY_JOB_HOUR", "6"))
ENABLE_SCHEDULER = os.environ.get("ENABLE_SCHEDULER", "true").lower() not in ("false", "0", "")

# Default weekly schedule (day-of-week indices, Monday=0), applied to a new
# athlete profile and used as the fallback whenever an athlete's own
# week_template is empty. plan_service.py reads the *athlete's* week_template
# (falling back to this) and threads it into engines/running.py's run_days and
# engines/strength.py's strength_days -- it's the real source of truth, not
# just descriptive metadata, and is editable per-athlete from Settings.
DEFAULT_WEEK_TEMPLATE = {
    0: "run",       # Mon
    1: "strength",  # Tue
    2: "run",       # Wed
    3: "strength",  # Thu
    4: "run",       # Fri
    5: "rest",      # Sat
    6: "run",       # Sun (long run)
}
