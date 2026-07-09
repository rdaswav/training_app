"""Ties the deterministic engines to persistence: generates a full plan for a
race and writes PlannedSession rows via the unified, conflict-checked calendar."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.engines import calendar as calendar_engine
from app.engines import running as running_engine
from app.engines import strength as strength_engine
from app.models import (
    AthleteProfile,
    Exercise,
    Macrocycle,
    Phase,
    PlannedSession,
    Race,
    SessionStatus,
    SessionType,
)


def _fitness_from_athlete(athlete: AthleteProfile, race_distance_km: float | None = None) -> running_engine.AthleteFitness:
    kwargs = dict(
        weekly_volume_km=athlete.weekly_volume_km,
        easy_pace_sec_per_km=athlete.easy_pace_sec_per_km,
        threshold_pace_sec_per_km=athlete.threshold_pace_sec_per_km,
        aerobic_hr_ceiling=athlete.aerobic_hr_ceiling,
    )
    if race_distance_km is not None:
        kwargs["race_distance_km"] = race_distance_km
    return running_engine.AthleteFitness(**kwargs)


def _leaf_dict(step: running_engine.RunStep) -> dict:
    return {
        "label": step.label,
        "duration_min": step.duration_min,
        "distance_km": step.distance_km,
        "target_pace_sec_per_km": step.target_pace_sec_per_km,
        "hr_ceiling": step.hr_ceiling,
    }


def _run_step_dict(step: running_engine.RunStep | running_engine.RunRepeatStep) -> dict:
    if isinstance(step, running_engine.RunRepeatStep):
        return {
            "type": "repeat",
            "label": step.label,
            "repeat_count": step.repeat_count,
            "work": _leaf_dict(step.work),
            "recovery": _leaf_dict(step.recovery) if step.recovery is not None else None,
        }
    return {"type": "step", **_leaf_dict(step)}


def _select_exercises(db: Session, prescriptions: list[strength_engine.StrengthPrescription], injury_flags: list[str]) -> list[dict]:
    exercises = [
        {"name": e.name, "pattern": e.pattern, "injury_tags": e.injury_tags}
        for e in db.query(Exercise).all()
    ]
    result = []
    for p in prescriptions:
        exercise = strength_engine.select_exercise(p.pattern, exercises, injury_flags)
        result.append(
            {
                "pattern": p.pattern,
                "category": p.category,
                "exercise_name": exercise["name"] if exercise else None,
                "sets": p.sets,
                "reps": p.reps,
                "rir": p.rir,
                "note": p.note,
            }
        )
    return result


def generate_and_persist_plan(
    db: Session,
    athlete: AthleteProfile,
    race: Race,
    plan_start_date: date | None = None,
    today: date | None = None,
) -> Macrocycle:
    """Race date + distance + current fitness -> full block of planned sessions
    (spec section 10, MVP steps 2-4). Regenerates the plan for this race --
    used on creation, on LLM-driven re-plans, and by the daily autoregulation
    job.

    `plan_start_date` anchors the phase/week structure (week 0's Monday) and
    is *sticky*: if not given explicitly, it defaults to the existing
    macrocycle's own start date (so a race created with a future
    plan_start_date -- e.g. "start the block on 10 August" -- keeps that
    anchor on every later regeneration, rather than silently snapping to
    whatever day the regeneration happens to run on). Only falls back to
    `today` when there's no existing macrocycle yet (first-ever generation
    with no explicit anchor requested).

    `today` is the actual current date -- used only as the cutoff below
    which a day is never touched, whether or not it's before plan_start_date.
    Never touches a day that's already completed/missed: only still-`planned`
    sessions from max(plan_start_date, today) forward are replaced, so
    re-running this daily can't erase training history."""
    today = today or date.today()
    if plan_start_date is None:
        plan_start_date = race.macrocycle.start_date if race.macrocycle is not None else today
    regen_from = max(plan_start_date, today)
    fitness = _fitness_from_athlete(athlete, race_distance_km=race.distance_km)

    phases, weeks = running_engine.generate_run_plan(plan_start_date, race.race_date, fitness)
    total_weeks = len(weeks)
    macro_start = weeks[0].start_date
    macro_end = weeks[-1].start_date + timedelta(days=6)

    if race.macrocycle is not None:
        db.delete(race.macrocycle)
        db.flush()

    # Regenerating (e.g. after an LLM-driven edit, or the daily job) must not
    # leave stale duplicate sessions for the dates the new plan also covers --
    # but must also never discard a day that's already been logged, or one
    # before the plan's own start date.
    # synchronize_session="fetch" (not False) so the ORM's identity map
    # actually drops these rows -- otherwise a later insert whose
    # autoincrement id happens to reuse one of these just-deleted ids
    # collides with a stale cached instance still sitting in the identity
    # map (SAWarning, and a real risk of returning stale data).
    db.query(PlannedSession).filter(
        PlannedSession.athlete_id == athlete.id,
        PlannedSession.date >= regen_from,
        PlannedSession.date <= macro_end,
        PlannedSession.status == SessionStatus.PLANNED,
    ).delete(synchronize_session="fetch")

    preserved_dates = {
        d
        for (d,) in db.query(PlannedSession.date)
        .filter(
            PlannedSession.athlete_id == athlete.id,
            PlannedSession.date >= regen_from,
            PlannedSession.date <= macro_end,
        )
        .all()
    }

    macrocycle = Macrocycle(race_id=race.id, start_date=macro_start, end_date=macro_end)
    db.add(macrocycle)
    db.flush()

    for phase in phases:
        phase_start = macro_start + timedelta(weeks=phase.start_week)
        phase_end = macro_start + timedelta(weeks=phase.end_week + 1) - timedelta(days=1)
        db.add(Phase(macrocycle_id=macrocycle.id, name=phase.name, start_date=phase_start, end_date=phase_end, focus=phase.focus))

    run_session_dicts = []
    for week in weeks:
        for s in week.sessions:
            run_session_dicts.append(
                {
                    "date": s.date,
                    "name": s.name,
                    "role": s.role,
                    "phase_name": s.phase_name,
                    "total_distance_km": s.total_distance_km,
                    "steps": [_run_step_dict(step) for step in s.steps],
                }
            )

    def phase_name_for_week(week_index: int) -> str:
        return running_engine.phase_for_week(phases, week_index).name

    strength_sessions = strength_engine.generate_strength_plan(macro_start, total_weeks, phase_name_for_week)
    strength_session_dicts = []
    for s in strength_sessions:
        prescriptions = _select_exercises(db, s.prescriptions, athlete.injury_flags or [])
        strength_session_dicts.append({"date": s.date, "name": s.name, "prescriptions": prescriptions})

    unified = calendar_engine.build_unified_calendar(run_session_dicts, strength_session_dicts, macro_start, total_weeks)

    for u in unified:
        if u.session_type == "rest" or u.date < regen_from or u.date in preserved_dates:
            continue
        week_index = min(max((u.date - macro_start).days // 7, 0), total_weeks - 1)
        content = {k: v for k, v in u.content.items() if k not in ("date", "name")}
        content["note"] = u.note
        db.add(
            PlannedSession(
                athlete_id=athlete.id,
                date=u.date,
                type=SessionType.RUN if u.session_type == "run" else SessionType.STRENGTH,
                name=u.name,
                content=content,
                phase_name=phase_name_for_week(week_index),
            )
        )

    db.commit()
    db.refresh(macrocycle)
    return macrocycle
