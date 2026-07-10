"""Phase-timeline ribbon + strength-mesocycle status computations for the
/plan dashboard. Pure functions -- callers shape ORM rows into plain
dicts/dates first (see main.py's plan_view), no DB dependency here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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


def strength_mesocycle_status(week_idx: int, current_phase_name: str) -> MesocycleStatus:
    """Reuses engines/strength.py's own prescribe() as the single source of
    truth for the current RIR/note, rather than re-deriving that math here."""
    from app.engines.strength import MESOCYCLE_LENGTH, mesocycle_week_local, prescribe, race_proximity_mode

    mode = race_proximity_mode(current_phase_name)
    local_week = mesocycle_week_local(week_idx)
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
