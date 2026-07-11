from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added to athlete_profiles after some deployments' DBs already
# existed. Base.metadata.create_all only creates missing TABLES -- it never
# adds columns to ones that already exist -- and this project has no Alembic,
# so this is the minimal safe substitute now that real deployments carry
# persistent user data that can't just be recreated from scratch.
_ATHLETE_PROFILE_NEW_COLUMNS = {
    # No SQL-level DEFAULT here (deliberately) -- existing rows must come back
    # NULL so the backfill below can tell "needs backfilling from current
    # pace" apart from "already migrated." The model's Python-level default
    # only applies to brand-new rows created through SQLAlchemy.
    "easy_pace_baseline_sec_per_km": "INTEGER",
    "threshold_pace_baseline_sec_per_km": "INTEGER",
    "last_job_run_at": "DATETIME",
    "last_job_error": "TEXT",
}


def _migrate_athlete_profiles(engine) -> None:
    if not DATABASE_URL.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "athlete_profiles" not in inspector.get_table_names():
        return  # fresh DB -- create_all already created it with every current column
    existing = {col["name"] for col in inspector.get_columns("athlete_profiles")}
    with engine.begin() as conn:
        for name, coltype in _ATHLETE_PROFILE_NEW_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE athlete_profiles ADD COLUMN {name} {coltype}"))
        # Backfill baselines for pre-existing rows: there's no historical
        # record of the athlete's originally profile-set pace, so treat
        # whatever's currently stored as the fresh baseline going forward.
        conn.execute(text(
            "UPDATE athlete_profiles SET easy_pace_baseline_sec_per_km = easy_pace_sec_per_km "
            "WHERE easy_pace_baseline_sec_per_km IS NULL"
        ))
        conn.execute(text(
            "UPDATE athlete_profiles SET threshold_pace_baseline_sec_per_km = threshold_pace_sec_per_km "
            "WHERE threshold_pace_baseline_sec_per_km IS NULL"
        ))


def init_db():
    from app import models  # noqa: F401 ensure models are registered

    Base.metadata.create_all(bind=engine)
    _migrate_athlete_profiles(engine)
