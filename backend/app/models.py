import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SessionType(str, enum.Enum):
    RUN = "run"
    STRENGTH = "strength"
    REST = "rest"


class SessionStatus(str, enum.Enum):
    PLANNED = "planned"
    COMPLETED = "completed"
    MISSED = "missed"


class RacePriority(str, enum.Enum):
    A = "A"
    B = "B"
    TUNE_UP = "tune_up"


class AthleteProfile(Base):
    __tablename__ = "athlete_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="Athlete")

    # Current fitness snapshot, refreshed from intervals.icu by the daily job.
    weekly_volume_km: Mapped[float] = mapped_column(Float, default=20.0)
    easy_pace_sec_per_km: Mapped[int] = mapped_column(Integer, default=390)
    threshold_pace_sec_per_km: Mapped[int] = mapped_column(Integer, default=330)
    aerobic_hr_ceiling: Mapped[int] = mapped_column(Integer, default=150)
    max_hr: Mapped[int] = mapped_column(Integer, default=185)

    # The athlete's last manually-set (profile-edit) paces -- the reference point
    # the daily job's cumulative autoregulated drift is bounded against (see
    # engines/autoregulation.py's drift clamp). Re-baselined whenever the
    # athlete edits paces directly in Settings.
    easy_pace_baseline_sec_per_km: Mapped[int] = mapped_column(Integer, default=390)
    threshold_pace_baseline_sec_per_km: Mapped[int] = mapped_column(Integer, default=330)

    # Daily autoregulation job health, surfaced in Settings so a silent failure
    # shows up immediately rather than only as gaps in training history.
    last_job_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    last_job_error: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    # Available days: fixed defaults (3 run, 3 strength, 1 rest) but stored so it's editable.
    week_template: Mapped[dict] = mapped_column(JSON, default=dict)

    # Injury flags restrict strength movement patterns (see engines/strength.py).
    injury_flags: Mapped[list] = mapped_column(JSON, default=list)

    races: Mapped[list["Race"]] = relationship(back_populates="athlete")


class Race(Base):
    __tablename__ = "races"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.id"))
    name: Mapped[str] = mapped_column(String)
    race_date: Mapped[date] = mapped_column(Date)
    distance_km: Mapped[float] = mapped_column(Float)
    goal_time_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[RacePriority] = mapped_column(Enum(RacePriority), default=RacePriority.A)

    athlete: Mapped["AthleteProfile"] = relationship(back_populates="races")
    macrocycle: Mapped["Macrocycle | None"] = relationship(
        back_populates="race", uselist=False, cascade="all, delete-orphan"
    )


class Macrocycle(Base):
    __tablename__ = "macrocycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    race_id: Mapped[int] = mapped_column(ForeignKey("races.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)

    # The strength mesocycle clock's start-week offset, chosen at generation
    # time to nudge its deload week toward the running plan's down-weeks/
    # taper (see engines/strength.py's best_mesocycle_offset, #31). Stored
    # here rather than recomputed on every view so the displayed mesocycle
    # status can never drift from what was actually used to generate the
    # persisted strength sessions.
    mesocycle_start_week: Mapped[int] = mapped_column(Integer, default=0)

    race: Mapped["Race"] = relationship(back_populates="macrocycle")
    phases: Mapped[list["Phase"]] = relationship(
        back_populates="macrocycle", cascade="all, delete-orphan", order_by="Phase.start_date"
    )
    mesocycles: Mapped[list["Mesocycle"]] = relationship(
        back_populates="macrocycle", cascade="all, delete-orphan", order_by="Mesocycle.start_date"
    )


class Phase(Base):
    __tablename__ = "phases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    macrocycle_id: Mapped[int] = mapped_column(ForeignKey("macrocycles.id"))
    name: Mapped[str] = mapped_column(String)  # Base / Re-base / Build 1 / Build 2 / Taper
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    focus: Mapped[str] = mapped_column(String, default="")

    macrocycle: Mapped["Macrocycle"] = relationship(back_populates="phases")


class Mesocycle(Base):
    """A strength block (4-6 weeks + deload) nested inside the running macrocycle."""

    __tablename__ = "mesocycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    macrocycle_id: Mapped[int] = mapped_column(ForeignKey("macrocycles.id"))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    is_deload: Mapped[bool] = mapped_column(Boolean, default=False)

    macrocycle: Mapped["Macrocycle"] = relationship(back_populates="mesocycles")


class Exercise(Base):
    """ExerciseLibrary: exercises tagged by movement pattern, for substitution."""

    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    pattern: Mapped[str] = mapped_column(String)  # e.g. "horizontal_push", "hinge", "carry"
    injury_tags: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["axial_load", "knee"]
    rep_range: Mapped[str] = mapped_column(String, default="6-10")  # e.g. "3-5" for compounds
    is_compound: Mapped[bool] = mapped_column(Boolean, default=False)


class PlannedSession(Base):
    __tablename__ = "planned_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athlete_profiles.id"))
    date: Mapped[date] = mapped_column(Date)
    type: Mapped[SessionType] = mapped_column(Enum(SessionType))
    name: Mapped[str] = mapped_column(String)
    status: Mapped[SessionStatus] = mapped_column(Enum(SessionStatus), default=SessionStatus.PLANNED)

    # Structured content: for runs, warmup/main/cooldown steps with pace & HR targets;
    # for strength, a list of {pattern, exercise, sets, reps, rir, load_pct}.
    content: Mapped[dict] = mapped_column(JSON, default=dict)

    phase_name: Mapped[str | None] = mapped_column(String, nullable=True)
    intervals_icu_event_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    completed: Mapped["CompletedSession | None"] = relationship(
        back_populates="planned_session", uselist=False, cascade="all, delete-orphan"
    )


class CompletedSession(Base):
    __tablename__ = "completed_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    planned_session_id: Mapped[int] = mapped_column(ForeignKey("planned_sessions.id"))
    date: Mapped[date] = mapped_column(Date)

    # Runs: pulled from intervals.icu. Strength: logged in-app.
    actual: Mapped[dict] = mapped_column(JSON, default=dict)
    feedback: Mapped[str] = mapped_column(String, default="")
    next_instruction: Mapped[str] = mapped_column(String, default="")

    planned_session: Mapped["PlannedSession"] = relationship(back_populates="completed")
