from datetime import date, datetime

from pydantic import BaseModel, Field

from app.models import RacePriority


class AthleteUpdate(BaseModel):
    name: str | None = None
    weekly_volume_km: float | None = None
    easy_pace_sec_per_km: int | None = None
    threshold_pace_sec_per_km: int | None = None
    aerobic_hr_ceiling: int | None = None
    max_hr: int | None = None
    injury_flags: list[str] | None = None
    week_template: dict[str, str] | None = None


class AthleteOut(BaseModel):
    id: int
    name: str
    weekly_volume_km: float
    easy_pace_sec_per_km: int
    threshold_pace_sec_per_km: int
    aerobic_hr_ceiling: int
    max_hr: int
    injury_flags: list[str]
    week_template: dict[str, str]

    model_config = {"from_attributes": True}


class RaceCreate(BaseModel):
    """`plan_start_date` anchors the generated plan's week 0 there instead of
    today (e.g. a block that starts a few weeks out). Sessions are only ever
    generated from that date forward -- nothing is backfilled for the gap
    between today and plan_start_date."""

    name: str
    race_date: date
    distance_km: float
    goal_time_sec: int | None = None
    priority: RacePriority = RacePriority.A
    plan_start_date: date | None = None


class RaceOut(BaseModel):
    id: int
    name: str
    race_date: date
    distance_km: float
    goal_time_sec: int | None
    priority: RacePriority

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: int
    date: date
    type: str
    name: str
    status: str
    phase_name: str | None
    content: dict

    model_config = {"from_attributes": True}


class StrengthSetLog(BaseModel):
    # reps/weight_kg for a normal or bodyweight_reps set; duration_sec for a
    # bodyweight_timed hold instead -- see Exercise.movement_type. Optional
    # (rather than two separate request shapes) since which ones apply is
    # determined by the prescription's movement_type, not the request itself.
    reps: int | None = None
    weight_kg: float | None = None
    duration_sec: int | None = None
    rir_actual: float | None = None


class StrengthLogRequest(BaseModel):
    pattern: str
    sets: list[StrengthSetLog]


class RunCompleteRequest(BaseModel):
    actual_pace_sec_per_km: int | None = None
    actual_hr: int | None = None
    distance_km: float | None = None
    hit_reps: bool = True
    wellness_ok: bool = True


class CompletedSessionOut(BaseModel):
    id: int
    date: date
    actual: dict
    feedback: str
    next_instruction: str

    model_config = {"from_attributes": True}


class PlanApplyRequest(BaseModel):
    """Structured edit payload for the LLM edit path (spec section 8)."""
    race_id: int
    race_date: date | None = None
    weekly_volume_km: float | None = None
    injury_flags: list[str] | None = None


class ExerciseOut(BaseModel):
    name: str
    pattern: str
    injury_tags: list[str]

    model_config = {"from_attributes": True}


class ExerciseSwapRequest(BaseModel):
    pattern: str
    exercise_name: str


class CoachReviewRequest(BaseModel):
    """`week_start` is any date inside the week to review -- it's normalized to
    that week's Monday. Defaults to the most recently finished week."""

    week_start: date | None = None


class CoachReviewExternalRequest(BaseModel):
    """For a review authored outside this app (e.g. a scheduled routine running
    its own Claude session against Strava) -- no ANTHROPIC_API_KEY call, no
    token cost, same week_start normalization as CoachReviewRequest."""

    week_start: date | None = None
    markdown: str = Field(min_length=1)


class CoachReviewOut(BaseModel):
    id: int
    week_start: date
    markdown: str
    model: str
    input_tokens: int
    output_tokens: int
    error: str | None
    created_at: datetime

    # The computed inputs travel with the prose deliberately: the review is only
    # trustworthy if its numbers can be checked at a glance.
    metrics: dict

    model_config = {"from_attributes": True}
