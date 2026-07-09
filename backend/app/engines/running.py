"""Deterministic half-marathon-default running periodization engine.

Pure functions/dataclasses only -- no DB, no I/O -- so the periodization
rules (spec section 5) can be unit tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

TAPER_WEEKS = 2
GROWTH_RATE = 1.09  # ~9%/week, within the 8-10% band
DOWN_WEEK_FACTOR = 0.75  # ~25% volume cut on down weeks
TAPER_FACTORS = [0.70, 0.45]  # applied to taper week 1, week 2 (race week)
LONG_RUN_SHARE = 0.35  # fraction of weekly volume assigned to the long run


@dataclass
class PhaseSpec:
    name: str
    start_week: int  # 0-indexed week number within the macrocycle
    end_week: int  # inclusive
    focus: str


@dataclass
class RunStep:
    label: str
    duration_min: float | None = None
    distance_km: float | None = None
    target_pace_sec_per_km: int | None = None
    hr_ceiling: int | None = None


@dataclass
class RunSessionPlan:
    date: date
    name: str
    phase_name: str
    steps: list[RunStep]
    total_distance_km: float
    role: str  # "easy" | "quality" | "long"


@dataclass
class WeekPlan:
    week_index: int
    start_date: date
    phase_name: str
    target_volume_km: float
    is_down_week: bool
    sessions: list[RunSessionPlan] = field(default_factory=list)


@dataclass
class AthleteFitness:
    weekly_volume_km: float
    easy_pace_sec_per_km: int
    threshold_pace_sec_per_km: int
    aerobic_hr_ceiling: int

    @property
    def race_pace_sec_per_km(self) -> int:
        # Rough half-marathon race pace derived from threshold pace.
        # TODO(spec 11): replace with VDOT/critical-pace model once the
        # intervals.icu fitness data source is confirmed.
        return self.threshold_pace_sec_per_km + 12


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weeks_to_race(today: date, race_date: date) -> int:
    """Number of training weeks from the Monday of `today`'s week through race week."""
    start = week_start(today)
    return ((race_date - start).days // 7) + 1


def build_phases(total_weeks: int) -> list[PhaseSpec]:
    """Allocate the phase sequence across `total_weeks` (spec section 5)."""
    if total_weeks <= 0:
        raise ValueError("total_weeks must be positive")

    taper = min(TAPER_WEEKS, total_weeks)
    remaining = total_weeks - taper

    if remaining < 4:
        # Heavily compressed block: protect the taper, spend everything else
        # on race-specific work rather than trying to fit every phase.
        phases: list[PhaseSpec] = []
        week = 0
        if remaining > 0:
            phases.append(PhaseSpec("Build 2", week, week + remaining - 1, "race-specific"))
            week += remaining
        phases.append(PhaseSpec("Taper", week, week + taper - 1, "sharpen"))
        return phases

    base_weeks = 0
    ramp_weeks = remaining
    if total_weeks > 16:
        # Extra weeks beyond the standard 16-week template become an
        # aerobic-base hold before the ramp begins.
        base_weeks = total_weeks - 16
        ramp_weeks = 16 - taper

    rebase = round(ramp_weeks * 0.30) or 1
    build1 = round(ramp_weeks * 0.35) or 1
    build2 = ramp_weeks - rebase - build1
    if build2 < 1:
        # Rounding pushed build2 to zero on short ramps; borrow a week from build1.
        build1 -= 1
        build2 = 1

    phases = []
    week = 0
    if base_weeks > 0:
        phases.append(PhaseSpec("Base", week, week + base_weeks - 1, "aerobic hold"))
        week += base_weeks
    phases.append(PhaseSpec("Re-base", week, week + rebase - 1, "aerobic rebuild"))
    week += rebase
    phases.append(PhaseSpec("Build 1", week, week + build1 - 1, "threshold intro"))
    week += build1
    phases.append(PhaseSpec("Build 2", week, week + build2 - 1, "race-specific"))
    week += build2
    phases.append(PhaseSpec("Taper", week, week + taper - 1, "sharpen"))
    return phases


def phase_for_week(phases: list[PhaseSpec], week_index: int) -> PhaseSpec:
    for phase in phases:
        if phase.start_week <= week_index <= phase.end_week:
            return phase
    return phases[-1]


def build_weekly_volumes(total_weeks: int, starting_volume_km: float, phases: list[PhaseSpec]) -> list[float]:
    """Weekly target volumes: 8-10%/week ramp, down week every 3rd-4th week, 2-week taper."""
    taper_start = next(p.start_week for p in phases if p.name == "Taper")
    volumes: list[float] = []
    last_up_volume = starting_volume_km
    for i in range(total_weeks):
        if i >= taper_start:
            taper_index = i - taper_start
            factor = TAPER_FACTORS[min(taper_index, len(TAPER_FACTORS) - 1)]
            volumes.append(round(last_up_volume * factor, 1))
            continue
        is_down_week = i > 0 and (i + 1) % 4 == 0
        if i == 0:
            volume = starting_volume_km
        elif is_down_week:
            volume = last_up_volume * DOWN_WEEK_FACTOR
        else:
            volume = last_up_volume * GROWTH_RATE
            last_up_volume = volume
        volumes.append(round(volume, 1))
    return volumes


def is_down_week(week_index: int, taper_start: int) -> bool:
    return week_index < taper_start and week_index > 0 and (week_index + 1) % 4 == 0


def _quality_session(phase_name: str, fitness: AthleteFitness, quality_date: date) -> RunSessionPlan:
    easy = fitness.easy_pace_sec_per_km
    threshold = fitness.threshold_pace_sec_per_km
    race_pace = fitness.race_pace_sec_per_km

    if phase_name in ("Base", "Re-base"):
        steps = [
            RunStep("Warmup", duration_min=15, target_pace_sec_per_km=easy),
            RunStep("6 x 20s strides w/ 60s float", distance_km=1.2, target_pace_sec_per_km=threshold - 20),
            RunStep("Cooldown", duration_min=10, target_pace_sec_per_km=easy),
        ]
        name = "Strides"
        total_km = round(3.0 + 1.2, 1)
    elif phase_name == "Build 1":
        steps = [
            RunStep("Warmup", duration_min=15, target_pace_sec_per_km=easy),
            RunStep("3 x 1.6km cruise interval @ threshold, 90s jog", distance_km=4.8, target_pace_sec_per_km=threshold),
            RunStep("Cooldown", duration_min=10, target_pace_sec_per_km=easy),
        ]
        name = "Cruise intervals"
        total_km = 4.8 + 4.0
    elif phase_name == "Build 2":
        steps = [
            RunStep("Warmup", duration_min=15, target_pace_sec_per_km=easy),
            RunStep("2 x 2km @ threshold", distance_km=4.0, target_pace_sec_per_km=threshold),
            RunStep("2 x 1km @ race pace", distance_km=2.0, target_pace_sec_per_km=race_pace),
            RunStep("Cooldown", duration_min=10, target_pace_sec_per_km=easy),
        ]
        name = "Threshold + race-pace reps"
        total_km = 4.0 + 2.0 + 4.0
    else:  # Taper
        steps = [
            RunStep("Warmup", duration_min=12, target_pace_sec_per_km=easy),
            RunStep("4 x 400m @ race pace, full recovery", distance_km=1.6, target_pace_sec_per_km=race_pace),
            RunStep("Cooldown", duration_min=8, target_pace_sec_per_km=easy),
        ]
        name = "Race-pace touch"
        total_km = 1.6 + 3.0

    return RunSessionPlan(
        date=quality_date, name=name, phase_name=phase_name, steps=steps, total_distance_km=round(total_km, 1), role="quality"
    )


def _easy_session(fitness: AthleteFitness, distance_km: float, run_date: date, phase_name: str) -> RunSessionPlan:
    steps = [
        RunStep(
            "Easy/recovery run",
            distance_km=distance_km,
            target_pace_sec_per_km=fitness.easy_pace_sec_per_km,
            hr_ceiling=fitness.aerobic_hr_ceiling,
        )
    ]
    return RunSessionPlan(
        date=run_date, name="Easy run", phase_name=phase_name, steps=steps, total_distance_km=round(distance_km, 1), role="easy"
    )


def _long_run_session(fitness: AthleteFitness, distance_km: float, run_date: date, phase_name: str) -> RunSessionPlan:
    if phase_name == "Build 2":
        # Later long runs embed race-pace segments (spec section 5).
        race_segment_km = min(4.0, round(distance_km * 0.25, 1))
        steady_km = round(distance_km - race_segment_km, 1)
        steps = [
            RunStep("Steady", distance_km=steady_km, target_pace_sec_per_km=fitness.easy_pace_sec_per_km),
            RunStep("Race-pace segment", distance_km=race_segment_km, target_pace_sec_per_km=fitness.race_pace_sec_per_km),
        ]
        name = "Long run w/ race-pace segment"
    else:
        steps = [RunStep("Steady long run", distance_km=distance_km, target_pace_sec_per_km=fitness.easy_pace_sec_per_km)]
        name = "Long run"
    return RunSessionPlan(
        date=run_date, name=name, phase_name=phase_name, steps=steps, total_distance_km=round(distance_km, 1), role="long"
    )


def generate_week(
    week_index: int,
    start_date: date,
    target_volume_km: float,
    phase: PhaseSpec,
    fitness: AthleteFitness,
    run_days: tuple[int, int, int] = (1, 3, 5),  # Tue, Thu, Sat (Monday=0), per spec section 7
) -> WeekPlan:
    """Build the 3 run sessions for one week: easy, quality, long."""
    long_km = round(target_volume_km * LONG_RUN_SHARE, 1)
    remainder = max(target_volume_km - long_km, 0.0)
    easy_km = round(remainder / 2, 1)
    quality_km = round(remainder - easy_km, 1)

    easy_date = start_date + timedelta(days=run_days[0])
    quality_date = start_date + timedelta(days=run_days[1])
    long_date = start_date + timedelta(days=run_days[2])

    quality_session = _quality_session(phase.name, fitness, quality_date)
    # Quality session distance is dictated by its structure; use it as the source of truth.
    week = WeekPlan(
        week_index=week_index,
        start_date=start_date,
        phase_name=phase.name,
        target_volume_km=target_volume_km,
        is_down_week=False,
        sessions=[
            _easy_session(fitness, easy_km, easy_date, phase.name),
            quality_session,
            _long_run_session(fitness, long_km, long_date, phase.name),
        ],
    )
    return week


def generate_run_plan(
    today: date,
    race_date: date,
    fitness: AthleteFitness,
    run_days: tuple[int, int, int] = (1, 3, 5),
) -> tuple[list[PhaseSpec], list[WeekPlan]]:
    """Top-level entry point: race date + distance (implicit via fitness/race context) +
    current fitness -> full block of weekly plans."""
    total_weeks = weeks_to_race(today, race_date)
    phases = build_phases(total_weeks)
    taper_start = next(p.start_week for p in phases if p.name == "Taper")
    volumes = build_weekly_volumes(total_weeks, fitness.weekly_volume_km, phases)

    weeks: list[WeekPlan] = []
    base_monday = week_start(today)
    for i in range(total_weeks):
        phase = phase_for_week(phases, i)
        week_plan = generate_week(i, base_monday + timedelta(weeks=i), volumes[i], phase, fitness, run_days)
        week_plan.is_down_week = is_down_week(i, taper_start)
        weeks.append(week_plan)
    return phases, weeks
