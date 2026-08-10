"""Deterministic weekly-review metrics: everything the coach LLM is told about a
training week is computed here, in pure Python, and handed over already-computed.

The model's job is the layer above the numbers -- is a trend real, does the goal
still hold, what changes next week. It is never asked to do arithmetic over raw
session data, because an LLM summing tonnage or computing a compliance rate from
a dump will occasionally be confidently wrong, and a wrong number in a coaching
review is worse than no review.

Pure/DB-free like the other engines: callers shape ORM rows into plain dicts
first (see coach_service.py). Everything returned is JSON-serializable, because
the exact same dict is both persisted to CoachReview.metrics and rendered into
the prompt -- so the stored inputs can never drift from what the model saw.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

# Roles whose prescribed pace is a steady target the session average can fairly
# be compared against. A quality session's average pace blends warmup, work,
# recovery and cooldown, so comparing it to any single prescribed pace is
# meaningless -- those are assessed on hitting the reps instead.
STEADY_ROLES = {"easy", "long"}


@dataclass
class Compliance:
    planned: int
    completed: int
    missed: int
    still_planned: int
    completion_pct: float | None  # None when nothing was scheduled


@dataclass
class LoadDelta:
    run_km: float | None
    run_km_prev: float | None
    run_km_delta_pct: float | None
    tonnage_kg: float | None
    tonnage_kg_prev: float | None
    tonnage_delta_pct: float | None


@dataclass
class SessionLine:
    date: str  # ISO
    name: str
    type: str  # "run" | "strength"
    role: str | None  # "easy" | "quality" | "long" for runs
    status: str  # "planned" | "completed" | "missed"
    planned_distance_km: float | None
    prescribed_pace_sec_per_km: int | None
    prescribed_hr_ceiling: int | None
    actual_pace_sec_per_km: int | None
    actual_hr: int | None
    feedback: str
    next_instruction: str
    note: str | None  # athlete-written context, e.g. why a session was missed


@dataclass
class HrFlag:
    date: str  # ISO
    name: str
    actual_hr: int
    hr_ceiling: int
    over_by: int


def _leaf_steps(content: dict) -> list[dict]:
    """Flatten a run session's steps, descending into repeat blocks."""
    leaves: list[dict] = []
    for step in content.get("steps", []) or []:
        if step.get("type") == "repeat":
            for leg in ("work", "recovery"):
                if step.get(leg):
                    leaves.append(step[leg])
        else:
            leaves.append(step)
    return leaves


def prescribed_targets(content: dict) -> tuple[int | None, int | None]:
    """(pace, hr_ceiling) for a run session.

    Pace is taken from the longest leaf step by distance -- the steady body of an
    easy/long run. HR ceiling is the highest across steps (the aerobic cap the
    autoregulation loop actually judges easy runs against)."""
    leaves = _leaf_steps(content)
    paced = [s for s in leaves if s.get("target_pace_sec_per_km")]
    pace = None
    if paced:
        pace = max(paced, key=lambda s: s.get("distance_km") or 0)["target_pace_sec_per_km"]
    ceilings = [s["hr_ceiling"] for s in leaves if s.get("hr_ceiling")]
    return pace, (max(ceilings) if ceilings else None)


def compliance(planned_rows: list[dict]) -> Compliance:
    """Planned vs completed vs missed for a set of sessions.

    `still_planned` is kept separate from `missed`: a session that hasn't been
    touched yet is not the same as one the daily job marked missed, and folding
    them together would understate compliance on a week reviewed mid-flight."""
    planned = len(planned_rows)
    completed = sum(1 for r in planned_rows if r["status"] == "completed")
    missed = sum(1 for r in planned_rows if r["status"] == "missed")
    still_planned = planned - completed - missed
    pct = round(completed / planned * 100, 1) if planned else None
    return Compliance(
        planned=planned,
        completed=completed,
        missed=missed,
        still_planned=still_planned,
        completion_pct=pct,
    )


def compliance_by_type(planned_rows: list[dict]) -> dict[str, Compliance]:
    return {
        session_type: compliance([r for r in planned_rows if r["type"] == session_type])
        for session_type in ("run", "strength")
    }


def _delta_pct(current: float | None, previous: float | None) -> float | None:
    """None rather than a fake 0%/inf when there's no comparable prior week."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


def week_over_week(load_series, week_start: date) -> LoadDelta:
    """Run-km and tonnage change vs the prior week.

    WeeklyLoadPoint.run_pct/tonnage_pct are normalized to the series maximum for
    bar heights -- they are not trends, so the deltas have to be computed from
    the raw values here."""
    by_week = {pt.week_start: pt for pt in load_series}
    current = by_week.get(week_start)
    previous = by_week.get(week_start - timedelta(days=7))
    run_km = current.run_km if current else None
    run_km_prev = previous.run_km if previous else None
    tonnage = current.tonnage_kg if current else None
    tonnage_prev = previous.tonnage_kg if previous else None
    return LoadDelta(
        run_km=run_km,
        run_km_prev=run_km_prev,
        run_km_delta_pct=_delta_pct(run_km, run_km_prev),
        tonnage_kg=tonnage,
        tonnage_kg_prev=tonnage_prev,
        tonnage_delta_pct=_delta_pct(tonnage, tonnage_prev),
    )


def session_lines(planned_rows: list[dict], completed_rows: list[dict]) -> list[SessionLine]:
    """One line per planned session, joined to whatever was actually logged.

    Strength sessions log one CompletedSession row per movement pattern, so the
    first matching row is used for the autoregulation feedback -- per-pattern
    detail belongs in the e1RM summary, not here."""
    completed_by_planned: dict[int, dict] = {}
    for row in completed_rows:
        completed_by_planned.setdefault(row["planned_session_id"], row)

    lines = []
    for row in sorted(planned_rows, key=lambda r: r["date"]):
        content = row.get("content") or {}
        done = completed_by_planned.get(row["id"], {})
        actual = done.get("actual") or {}
        pace, ceiling = prescribed_targets(content) if row["type"] == "run" else (None, None)
        lines.append(
            SessionLine(
                date=row["date"].isoformat(),
                name=row["name"],
                type=row["type"],
                role=content.get("role"),
                status=row["status"],
                planned_distance_km=content.get("total_distance_km"),
                prescribed_pace_sec_per_km=pace,
                prescribed_hr_ceiling=ceiling,
                actual_pace_sec_per_km=actual.get("actual_pace_sec_per_km"),
                actual_hr=actual.get("actual_hr"),
                feedback=done.get("feedback", ""),
                next_instruction=done.get("next_instruction", ""),
                note=row.get("note"),
            )
        )
    return lines


def hr_ceiling_check(lines: list[SessionLine], aerobic_hr_ceiling: int | None) -> list[HrFlag]:
    """Easy/long runs whose *average* HR exceeded the prescribed aerobic ceiling.

    NOT a polarization / time-in-zone check. intervals.icu activity streams are
    not fetched by this app (app/integrations/intervals_icu.py has no streams
    endpoint), so there is no within-activity HR series to compute "share of easy
    time above X bpm" from -- only the session average the daily job stores.

    An average under the ceiling can still hide a hot second half, so treat a
    clean result here as "no obvious problem", not as proof the easy runs were
    genuinely easy. Wiring the streams endpoint is what would make this a real
    polarization check."""
    flags = []
    for line in lines:
        ceiling = line.prescribed_hr_ceiling or aerobic_hr_ceiling
        if line.role not in STEADY_ROLES or not line.actual_hr or not ceiling:
            continue
        if line.actual_hr > ceiling:
            flags.append(
                HrFlag(
                    date=line.date,
                    name=line.name,
                    actual_hr=line.actual_hr,
                    hr_ceiling=ceiling,
                    over_by=line.actual_hr - ceiling,
                )
            )
    return flags


def build_review_metrics(
    *,
    week_start: date,
    athlete: dict,
    race: dict | None,
    plan: dict,
    planned_rows: list[dict],
    completed_rows: list[dict],
    load_series,
    e1rm_by_pattern: dict[str, float],
) -> dict:
    """The single payload that is both persisted and rendered into the prompt.

    `athlete`, `race` and `plan` are already-shaped dicts (the caller resolves
    them via the existing dashboard_summary/vdot/plan_service helpers); the rest
    is computed here."""
    lines = session_lines(planned_rows, completed_rows)
    return {
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "athlete": athlete,
        "race": race,
        "plan": plan,
        "compliance": {
            "overall": asdict(compliance(planned_rows)),
            "by_type": {k: asdict(v) for k, v in compliance_by_type(planned_rows).items()},
        },
        "load": asdict(week_over_week(load_series, week_start)),
        # Raw history so the model can judge whether a week-over-week move is a
        # real trend or noise -- a single delta can't distinguish them.
        "recent_load": [
            {
                "week_start": pt.week_start.isoformat(),
                "run_km": pt.run_km,
                "tonnage_kg": pt.tonnage_kg,
            }
            for pt in sorted(load_series, key=lambda p: p.week_start)
            if pt.week_start <= week_start
        ],
        "sessions": [asdict(line) for line in lines],
        "hr_ceiling_flags": [asdict(f) for f in hr_ceiling_check(lines, athlete.get("aerobic_hr_ceiling"))],
        "e1rm_by_pattern": {k: round(v, 1) for k, v in sorted(e1rm_by_pattern.items())},
    }
