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

# Fixed weekly schedule (day-of-week indices, Monday=0) per spec section 7.
# NOTE: this is descriptive metadata stored on AthleteProfile.week_template
# only -- the engines (engines/running.py's run_days, engines/strength.py's
# DAY_TEMPLATE) hardcode their own weekday assignments and don't read this
# dict. Keep the two in sync manually until the engines are made to consult
# per-athlete schedule preferences.
DEFAULT_WEEK_TEMPLATE = {
    0: "strength",  # Mon
    1: "run",       # Tue
    2: "strength",  # Wed
    3: "run",       # Thu
    4: "strength",  # Fri
    5: "rest",      # Sat
    6: "run",       # Sun (long run)
}
