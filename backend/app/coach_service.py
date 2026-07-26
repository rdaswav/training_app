"""Ties the deterministic weekly-review metrics to persistence and the LLM call.

Mirrors plan_service.py's role: the one place the pure engines meet the DB. The
review is written whether or not the model call succeeds -- a skipped or failed
run still produces a CoachReview row carrying `error`, which is what lets the
latest row double as the weekly job's health record.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.engines import dashboard_summary, load_summary, weekly_review
from app.engines import strength as strength_engine
from app.engines import vdot as vdot_engine
from app.engines.running import week_start as monday_of
from app.integrations.anthropic_coach import generate_review, render_prompt
from app.jobs.daily_autoregulation import MAX_PACE_DRIFT_SEC_PER_KM
from app.models import AthleteProfile, CoachReview, CompletedSession, PlannedSession, Race, SessionType
from app.plan_service import fitness_from_athlete

# Weeks of load history handed to the model. One week-over-week delta can't tell
# a real trend from noise, which is exactly what the review is asked to judge.
LOAD_HISTORY_WEEKS = 6

# Prior reviews included for continuity -- enough to notice a multi-week pattern
# or whether a previously requested change actually happened.
PRIOR_REVIEWS = 3


def last_complete_week(today: date | None = None) -> date:
    """Monday of the most recently finished week."""
    return monday_of(today or date.today()) - timedelta(days=7)


def _athlete_metrics(athlete: AthleteProfile, race: Race | None) -> dict:
    vdot = vdot_engine.vdot_from_threshold_pace(athlete.threshold_pace_sec_per_km)
    fitness = fitness_from_athlete(
        athlete,
        race_distance_km=race.distance_km if race else None,
        goal_time_sec=race.goal_time_sec if race else None,
    )
    return {
        "vdot": round(vdot, 1),
        "weekly_volume_km": athlete.weekly_volume_km,
        "easy_pace_sec_per_km": athlete.easy_pace_sec_per_km,
        "threshold_pace_sec_per_km": athlete.threshold_pace_sec_per_km,
        "easy_pace_baseline_sec_per_km": athlete.easy_pace_baseline_sec_per_km,
        "threshold_pace_baseline_sec_per_km": athlete.threshold_pace_baseline_sec_per_km,
        "max_pace_drift_sec_per_km": MAX_PACE_DRIFT_SEC_PER_KM,
        "aerobic_hr_ceiling": athlete.aerobic_hr_ceiling,
        "max_hr": athlete.max_hr,
        "race_pace_sec_per_km": fitness.race_pace_sec_per_km,
    }


def _plan_metrics(race: Race | None, week_start: date) -> dict:
    """Where the reviewed week sat in the block. Empty when no plan covers it."""
    if not race or not race.macrocycle:
        return {}
    macro = race.macrocycle
    phases = [
        {"name": p.name, "start_date": p.start_date, "end_date": p.end_date, "focus": p.focus}
        for p in macro.phases
    ]
    phase = dashboard_summary.active_phase(phases, week_start)
    phase_name = phase["name"] if phase else None
    week_idx = dashboard_summary.global_week_index(macro.start_date, week_start)
    meso = dashboard_summary.strength_mesocycle_status(
        week_idx, phase_name or "Re-base", macro.mesocycle_start_week or 0
    )
    return {
        "phase_name": phase_name,
        "phase_focus": phase["focus"] if phase else None,
        "week_index": week_idx + 1,
        "total_weeks": (macro.end_date - macro.start_date).days // 7 + 1,
        "mesocycle": {
            "local_week": meso.local_week + 1,
            "mesocycle_length": meso.mesocycle_length,
            "mode": meso.mode,
            "current_rir": meso.current_rir,
            "note": meso.note,
        },
    }


def _load_series(db: Session, athlete: AthleteProfile, week_start: date):
    """Weekly run-km / strength-tonnage over the history window ending at the
    reviewed week. Same CompletedSession-join shape as /plan's load dashboard."""
    start = week_start - timedelta(weeks=LOAD_HISTORY_WEEKS - 1)
    end = week_start + timedelta(days=6)

    sessions = (
        db.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= start, PlannedSession.date <= end)
        .all()
    )
    run_rows = [
        {"week_start": monday_of(s.date), "distance_km": s.content.get("total_distance_km") or 0.0}
        for s in sessions
        if s.type == SessionType.RUN
    ]
    completed = (
        db.query(CompletedSession)
        .join(PlannedSession, CompletedSession.planned_session_id == PlannedSession.id)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.type == SessionType.STRENGTH,
            PlannedSession.date >= start,
            PlannedSession.date <= end,
        )
        .all()
    )
    completed_rows = [{"week_start": monday_of(c.date), "actual": c.actual} for c in completed]
    week_starts = [start + timedelta(weeks=i) for i in range(LOAD_HISTORY_WEEKS)]
    return load_summary.build_weekly_load_series(
        week_starts=week_starts,
        run_km_by_week=load_summary.sum_run_km_by_week(run_rows),
        tonnage_by_week=load_summary.sum_strength_tonnage_by_week(completed_rows),
        current_week_start=monday_of(date.today()),
    )


def build_metrics(db: Session, athlete: AthleteProfile, week_start: date) -> dict:
    """Assemble the full deterministic payload for one week. No LLM involved --
    callable on its own to inspect exactly what the model would be told."""
    race = db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).first()
    week_end = week_start + timedelta(days=6)

    planned = (
        db.query(PlannedSession)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= week_start, PlannedSession.date <= week_end)
        .order_by(PlannedSession.date)
        .all()
    )
    planned_rows = [
        {
            "id": s.id,
            "date": s.date,
            "name": s.name,
            "type": s.type.value,
            "status": s.status.value,
            "content": s.content or {},
        }
        for s in planned
    ]
    completed = (
        db.query(CompletedSession)
        .join(PlannedSession, CompletedSession.planned_session_id == PlannedSession.id)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= week_start, PlannedSession.date <= week_end)
        .all()
    )
    completed_rows = [
        {
            "planned_session_id": c.planned_session_id,
            "actual": c.actual or {},
            "feedback": c.feedback or "",
            "next_instruction": c.next_instruction or "",
        }
        for c in completed
    ]

    strength_history = (
        db.query(CompletedSession)
        .join(PlannedSession, CompletedSession.planned_session_id == PlannedSession.id)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.STRENGTH)
        .order_by(CompletedSession.date.desc())
        .limit(200)
        .all()
    )
    e1rm = strength_engine.latest_e1rm_by_pattern(
        [{"pattern": c.actual.get("pattern"), "sets": c.actual.get("sets", [])} for c in strength_history]
    )

    race_metrics = None
    if race:
        race_metrics = {
            "name": race.name,
            "race_date": race.race_date.isoformat(),
            "distance_km": race.distance_km,
            "goal_time_sec": race.goal_time_sec,
            "priority": race.priority.value,
            "days_out_at_week_end": (race.race_date - week_end).days,
        }

    return weekly_review.build_review_metrics(
        week_start=week_start,
        athlete=_athlete_metrics(athlete, race),
        race=race_metrics,
        plan=_plan_metrics(race, week_start),
        planned_rows=planned_rows,
        completed_rows=completed_rows,
        load_series=_load_series(db, athlete, week_start),
        e1rm_by_pattern=e1rm,
    )


def generate_weekly_review(
    db: Session, athlete: AthleteProfile, week_start: date | None = None, client=None
) -> CoachReview:
    """Build the metrics, ask the model to interpret them, persist the result.

    Idempotent per (athlete, week): re-running updates the existing row rather
    than stacking duplicates, so a manual re-run after a failed scheduled run
    replaces it cleanly."""
    week_start = week_start or last_complete_week()
    metrics = build_metrics(db, athlete, week_start)

    prior = (
        db.query(CoachReview)
        .filter(
            CoachReview.athlete_id == athlete.id,
            CoachReview.week_start < week_start,
            CoachReview.markdown != "",
        )
        .order_by(CoachReview.week_start.desc())
        .limit(PRIOR_REVIEWS)
        .all()
    )
    prior_reviews = [{"week_start": r.week_start.isoformat(), "markdown": r.markdown} for r in prior]

    result = generate_review(metrics, prior_reviews, client=client)

    review = (
        db.query(CoachReview)
        .filter(CoachReview.athlete_id == athlete.id, CoachReview.week_start == week_start)
        .first()
    )
    if review is None:
        review = CoachReview(athlete_id=athlete.id, week_start=week_start)
        db.add(review)

    review.metrics = metrics
    review.prompt = render_prompt(metrics, prior_reviews)
    review.markdown = result.markdown
    review.model = result.model
    review.input_tokens = result.input_tokens
    review.output_tokens = result.output_tokens
    review.error = result.error
    # Refreshed on every run, not just the first insert: this row is the weekly
    # job's health record, so the timestamp has to mean "last generated".
    review.created_at = datetime.utcnow()

    db.commit()
    db.refresh(review)
    return review
