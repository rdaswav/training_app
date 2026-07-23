"""Phase-timeline ribbon + strength-mesocycle status computations for the
/plan dashboard. Pure functions -- callers shape ORM rows into plain
dicts/dates first (see main.py's plan_view), no DB dependency here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class WeekTick:
    week_num: int  # 1-indexed
    status: str  # "done" | "now" | "upcoming"


@dataclass
class RaceFlag:
    label: str
    tag: str  # "target" | "tune-up"
    pct: float  # 0-100 position along the macrocycle timeline


@dataclass
class WeekDetail:
    week_num: int  # 1-indexed
    phase_name: str
    is_running_recovery: bool  # running plan's own down-week or taper week
    is_strength_deload: bool


@dataclass
class MesocycleStatus:
    local_week: int  # 0-indexed within the current mesocycle
    mesocycle_length: int
    mode: str  # "accumulate" | "maintenance" | "minimal"
    current_rir: float
    effort_pct: float  # 0-100, RIR 4 (low effort) -> 0%, RIR 1 (high effort) -> 100%
    note: str


def active_phase(phases: list[dict], today: date) -> dict | None:
    for p in phases:
        if p["start_date"] <= today <= p["end_date"]:
            return p
    return None


def global_week_index(macrocycle_start: date, today: date) -> int:
    """0-indexed week number since the macrocycle's Monday-aligned start,
    matching engines/strength.py's generate_strength_plan week indexing."""
    from app.engines.running import week_start

    return (week_start(today) - week_start(macrocycle_start)).days // 7


def week_ticks(total_weeks: int, current_week_index: int) -> list[WeekTick]:
    ticks = []
    for i in range(total_weeks):
        if i < current_week_index:
            status = "done"
        elif i == current_week_index:
            status = "now"
        else:
            status = "upcoming"
        ticks.append(WeekTick(week_num=i + 1, status=status))
    return ticks


def timeline_pct(start: date, end: date, target: date) -> float:
    total_days = max((end - start).days, 1)
    offset_days = (target - start).days
    return round(max(0.0, min(100.0, offset_days / total_days * 100)), 2)


def race_flags(races: list[dict], macrocycle_start: date, macrocycle_end: date) -> list[RaceFlag]:
    flags = []
    for r in races:
        if macrocycle_start <= r["race_date"] <= macrocycle_end:
            flags.append(
                RaceFlag(
                    label=r["name"],
                    tag="target" if r["priority"] == "A" else "tune-up",
                    pct=timeline_pct(macrocycle_start, macrocycle_end, r["race_date"]),
                )
            )
    return flags


def macrocycle_week_grid(
    phases: list[dict], macro_start: date, total_weeks: int, taper_start_week: int, mesocycle_start_week: int = 0
) -> list[WeekDetail]:
    """Week-by-week view of how the running plan's own recovery weeks (down-
    weeks + taper) line up against the strength mesocycle's deload weeks --
    the concrete evidence behind the "two systems are coupled" claim (see
    engines/strength.py's best_mesocycle_offset), rendered as a small grid on
    the /about page rather than just asserted in prose."""
    from app.engines.running import is_down_week
    from app.engines.strength import is_deload_week, mesocycle_week_local

    grid = []
    for i in range(total_weeks):
        week_monday = macro_start + timedelta(weeks=i)
        phase = active_phase(phases, week_monday) or (phases[-1] if phases else None)
        local_week = mesocycle_week_local(i, mesocycle_start_week)
        grid.append(
            WeekDetail(
                week_num=i + 1,
                phase_name=phase["name"] if phase else "",
                is_running_recovery=is_down_week(i, taper_start_week) or i >= taper_start_week,
                is_strength_deload=is_deload_week(local_week),
            )
        )
    return grid


def strength_mesocycle_status(week_idx: int, current_phase_name: str, mesocycle_start_week: int = 0) -> MesocycleStatus:
    """Reuses engines/strength.py's own prescribe() as the single source of
    truth for the current RIR/note, rather than re-deriving that math here.
    `mesocycle_start_week` must be the same offset used to actually generate
    the persisted strength sessions (Macrocycle.mesocycle_start_week, see
    #31) -- otherwise this status can drift from what the athlete is really
    prescribed that week."""
    from app.engines.strength import MESOCYCLE_LENGTH, mesocycle_week_local, prescribe, race_proximity_mode

    mode = race_proximity_mode(current_phase_name)
    local_week = mesocycle_week_local(week_idx, mesocycle_start_week)
    sample = prescribe("squat", "compound", local_week, mode)
    effort_pct = round(max(0.0, min(100.0, (4.0 - sample.rir) / 3.0 * 100)), 1)
    return MesocycleStatus(
        local_week=local_week,
        mesocycle_length=MESOCYCLE_LENGTH,
        mode=mode,
        current_rir=sample.rir,
        effort_pct=effort_pct,
        note=sample.note,
    )
