"""Unified calendar: merges running + strength sessions into one week, applying
the adjacency guardrail from spec section 7 (no hard lower-body strength the
day before a key run)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

HARD_LOWER_PATTERNS = {"squat", "hinge", "single_leg", "unilateral", "posterior_chain"}
KEY_RUN_ROLES = {"quality", "long"}


@dataclass
class UnifiedSession:
    date: date
    session_type: str  # "run" | "strength" | "rest"
    name: str
    role: str | None = None  # run role: easy/quality/long
    patterns: list[str] = field(default_factory=list)  # strength patterns present this session
    content: dict = field(default_factory=dict)
    note: str = ""


def find_adjacency_conflicts(week_sessions: list[UnifiedSession]) -> list[tuple[UnifiedSession, UnifiedSession]]:
    by_date = {s.date: s for s in week_sessions}
    conflicts = []
    for s in week_sessions:
        if s.session_type != "strength" or not (set(s.patterns) & HARD_LOWER_PATTERNS):
            continue
        nxt = by_date.get(s.date + timedelta(days=1))
        if nxt and nxt.session_type == "run" and nxt.role in KEY_RUN_ROLES:
            conflicts.append((s, nxt))
    return conflicts


def auto_resolve_conflicts(week_sessions: list[UnifiedSession]) -> list[UnifiedSession]:
    """Swap a conflicting strength day with a same-week rest day when that
    doesn't just move the conflict elsewhere; otherwise flag it in place."""
    by_date = {s.date: s for s in week_sessions}
    for strength_session, run_session in find_adjacency_conflicts(week_sessions):
        rest_days = [s for s in week_sessions if s.session_type == "rest"]
        swapped = False
        for rest in rest_days:
            after_rest = by_date.get(rest.date + timedelta(days=1))
            if after_rest and after_rest.session_type == "run" and after_rest.role in KEY_RUN_ROLES:
                continue  # swapping here would just create a new conflict
            strength_session.date, rest.date = rest.date, strength_session.date
            strength_session.note = (
                f"Auto-shuffled to avoid hard lower-body load the day before the "
                f"{run_session.role} run on {run_session.date.isoformat()}."
            )
            rest.note = "Auto-shuffled to rest to protect the adjacent key run."
            swapped = True
            break
        if not swapped:
            strength_session.note = (
                f"Flagged: hard lower-body work falls the day before the {run_session.role} run on "
                f"{run_session.date.isoformat()}; no free rest day to swap this week. Consider lightening load."
            )
    return sorted(week_sessions, key=lambda s: s.date)


def build_unified_week(
    run_sessions_by_date: dict[date, dict],
    strength_sessions_by_date: dict[date, dict],
    week_dates: list[date],
) -> list[UnifiedSession]:
    """Combine one week's run + strength sessions (already keyed by date) into a
    single conflict-checked calendar, filling any remaining day as rest."""
    sessions: list[UnifiedSession] = []
    for d in week_dates:
        if d in run_sessions_by_date:
            r = run_sessions_by_date[d]
            sessions.append(UnifiedSession(date=d, session_type="run", name=r["name"], role=r["role"], content=r))
        elif d in strength_sessions_by_date:
            st = strength_sessions_by_date[d]
            patterns = [p["pattern"] for p in st["prescriptions"]]
            sessions.append(UnifiedSession(date=d, session_type="strength", name=st["name"], patterns=patterns, content=st))
        else:
            sessions.append(UnifiedSession(date=d, session_type="rest", name="Rest"))
    return auto_resolve_conflicts(sessions)


def build_unified_calendar(run_sessions: list[dict], strength_sessions: list[dict], start_date: date, total_weeks: int) -> list[UnifiedSession]:
    run_by_date = {r["date"]: r for r in run_sessions}
    strength_by_date = {s["date"]: s for s in strength_sessions}
    from app.engines.running import week_start

    base_monday = week_start(start_date)
    calendar: list[UnifiedSession] = []
    for week_index in range(total_weeks):
        week_monday = base_monday + timedelta(weeks=week_index)
        week_dates = [week_monday + timedelta(days=i) for i in range(7)]
        calendar.extend(build_unified_week(run_by_date, strength_by_date, week_dates))
    return calendar
