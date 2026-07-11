"""Tests for the additive SQLite migration in db.py: Base.metadata.create_all
only creates missing tables, never adds columns to ones that already exist,
so an already-deployed athlete_profiles table (predating the baseline/job-
health columns) needs _migrate_athlete_profiles to add them safely without
losing data."""
import os
import tempfile

from sqlalchemy import create_engine, inspect, text

from app.db import _migrate_athlete_profiles


def test_migrate_adds_missing_columns_and_backfills_baseline_from_current_pace():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            # The OLD schema, predating the baseline/job-health columns.
            conn.execute(text(
                "CREATE TABLE athlete_profiles ("
                "id INTEGER PRIMARY KEY, name VARCHAR, weekly_volume_km FLOAT, "
                "easy_pace_sec_per_km INTEGER, threshold_pace_sec_per_km INTEGER, "
                "aerobic_hr_ceiling INTEGER, max_hr INTEGER, week_template JSON, injury_flags JSON)"
            ))
            conn.execute(text(
                "INSERT INTO athlete_profiles (id, name, weekly_volume_km, easy_pace_sec_per_km, "
                "threshold_pace_sec_per_km, aerobic_hr_ceiling, max_hr) "
                "VALUES (1, 'Athlete', 30.0, 375, 315, 150, 185)"
            ))

        _migrate_athlete_profiles(engine)

        inspector = inspect(engine)
        columns = {col["name"] for col in inspector.get_columns("athlete_profiles")}
        assert {
            "easy_pace_baseline_sec_per_km",
            "threshold_pace_baseline_sec_per_km",
            "last_job_run_at",
            "last_job_error",
        } <= columns

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT easy_pace_baseline_sec_per_km, threshold_pace_baseline_sec_per_km, "
                "last_job_run_at, last_job_error FROM athlete_profiles WHERE id = 1"
            )).fetchone()
        # Backfilled to match the pre-existing pace -- there's no historical
        # record of the athlete's originally profile-set value.
        assert row[0] == 375
        assert row[1] == 315
        assert row[2] is None
        assert row[3] is None
    finally:
        os.remove(path)


def test_migrate_is_a_noop_on_a_fresh_db_with_no_athlete_profiles_table():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(f"sqlite:///{path}")
        _migrate_athlete_profiles(engine)  # must not raise -- nothing to migrate yet
        inspector = inspect(engine)
        assert "athlete_profiles" not in inspector.get_table_names()
    finally:
        os.remove(path)


def test_migrate_is_idempotent_when_columns_already_exist():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(f"sqlite:///{path}")
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE athlete_profiles ("
                "id INTEGER PRIMARY KEY, easy_pace_sec_per_km INTEGER, threshold_pace_sec_per_km INTEGER, "
                "easy_pace_baseline_sec_per_km INTEGER, threshold_pace_baseline_sec_per_km INTEGER, "
                "last_job_run_at DATETIME, last_job_error TEXT)"
            ))
        _migrate_athlete_profiles(engine)  # first run
        _migrate_athlete_profiles(engine)  # must not raise on a second run
    finally:
        os.remove(path)
