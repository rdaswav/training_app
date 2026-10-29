"""Deterministic running periodization engine, shaped per race distance.

Block length, taper, long-run share/cap and quality-session content all come
from the race's RaceProfile (engines/race_profiles.py). The "half" profile
reproduces the original half-marathon-only engine exactly.

Pure functions/dataclasses only -- no DB, no I/O -- so the periodization
rules (spec section 5) can be unit tested in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.engines import vdot as vdot_engine
from app.engines.race_profiles import HALF, HALF_MARATHON_KM, RaceProfile, profile_for_distance

GROWTH_RATE = 1.09  # ~9%/week, within the 8-10% band
DOWN_WEEK_FACTOR = 0.75  # ~25% volume cut on down weeks
STRIDES_KM = 1.2  # approximate ground covered by 6 x 20s strides + floats


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
class RunRepeatStep:
    """A work interval + recovery interval, repeated `repeat_count` times.
    `work`/`recovery` are ordinary RunSteps -- a repeat block is just two
    leaves plus a count, not a new kind of leaf."""

    label: str
    repeat_count: int
    work: RunStep
    recovery: RunStep | None = None


@dataclass
class RunSessionPlan:
    date: date
    name: str
    phase_name: str
    steps: list[RunStep | RunRepeatStep]
    total_distance_km: float
    role: str  # "easy" | "quality" | "long" | "race"


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
    race_distance_km: float = HALF_MARATHON_KM
    goal_time_sec: int | None = None

    @property
    def race_pace_sec_per_km(self) -> int:
        # A set goal time overrides the derived race pace directly -- it only
        # affects race-pace segments (Build 2/Taper touches rehearsing goal
        # effort). Threshold/easy paces stay autoregulated from actual current
        # fitness (below), by deliberate choice: reshaping those toward an
        # "implied VDOT" for the goal time risks prescribing paces harder than
        # current fitness has earned, working against the app's autoregulation
        # design.
        if self.goal_time_sec:
            return round(self.goal_time_sec / self.race_distance_km)
        # Daniels' VDOT model: derive a fitness score from threshold pace
        # (Daniels' own ~60min calibration for Threshold), then the race pace
        # for the actual race distance. See engines/vdot.py.
        return vdot_engine.race_pace_sec_per_km(self.vdot, self.race_distance_km)

    @property
    def vdot(self) -> float:
        return vdot_engine.vdot_from_threshold_pace(self.threshold_pace_sec_per_km)

    # I/R paces follow current (threshold-derived) fitness, never the goal
    # time -- same reasoning as threshold/easy above.
    @property
    def interval_pace_sec_per_km(self) -> int:
        return vdot_engine.interval_pace_sec_per_km(self.vdot)

    @property
    def repetition_pace_sec_per_km(self) -> int:
        return vdot_engine.repetition_pace_sec_per_km(self.vdot)

    @property
    def profile(self) -> RaceProfile:
        return profile_for_distance(self.race_distance_km)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weeks_to_race(today: date, race_date: date) -> int:
    """Number of training weeks from the Monday of `today`'s week through race week."""
    start = week_start(today)
    return ((race_date - start).days // 7) + 1


def build_phases(total_weeks: int, profile: RaceProfile = HALF) -> list[PhaseSpec]:
    """Allocate the phase sequence across `total_weeks` (spec section 5),
    using the race profile's template length, taper length and split."""
    if total_weeks <= 0:
        raise ValueError("total_weeks must be positive")

    focus = profile.phase_focus
    taper = min(profile.taper_weeks, total_weeks)
    remaining = total_weeks - taper

    if remaining < 4:
        # Heavily compressed block: protect the taper, spend everything else
        # on race-specific work rather than trying to fit every phase.
        phases: list[PhaseSpec] = []
        week = 0
        if remaining > 0:
            phases.append(PhaseSpec("Build 2", week, week + remaining - 1, focus["Build 2"]))
            week += remaining
        phases.append(PhaseSpec("Taper", week, week + taper - 1, focus["Taper"]))
        return phases

    base_weeks = 0
    ramp_weeks = remaining
    if total_weeks > profile.template_weeks:
        # Extra weeks beyond the profile's standard template become an
        # aerobic-base hold before the ramp begins.
        base_weeks = total_weeks - profile.template_weeks
        ramp_weeks = profile.template_weeks - taper

    rebase = round(ramp_weeks * profile.rebase_share) or 1
    build1 = round(ramp_weeks * profile.build1_share) or 1
    build2 = ramp_weeks - rebase - build1
    if build2 < 1:
        # Rounding pushed build2 to zero on short ramps; borrow a week from build1.
        build1 -= 1
        build2 = 1

    phases = []
    week = 0
    if base_weeks > 0:
        phases.append(PhaseSpec("Base", week, week + base_weeks - 1, focus["Base"]))
        week += base_weeks
    phases.append(PhaseSpec("Re-base", week, week + rebase - 1, focus["Re-base"]))
    week += rebase
    phases.append(PhaseSpec("Build 1", week, week + build1 - 1, focus["Build 1"]))
    week += build1
    phases.append(PhaseSpec("Build 2", week, week + build2 - 1, focus["Build 2"]))
    week += build2
    phases.append(PhaseSpec("Taper", week, week + taper - 1, focus["Taper"]))
    return phases


def phase_for_week(phases: list[PhaseSpec], week_index: int) -> PhaseSpec:
    for phase in phases:
        if phase.start_week <= week_index <= phase.end_week:
            return phase
    return phases[-1]


def build_weekly_volumes(
    total_weeks: int, starting_volume_km: float, phases: list[PhaseSpec], profile: RaceProfile = HALF
) -> list[float]:
    """Weekly target volumes: 8-10%/week ramp, down week every 3rd-4th week,
    then the profile's taper factors."""
    taper_start = next(p.start_week for p in phases if p.name == "Taper")
    taper_factors = profile.taper_factors
    volumes: list[float] = []
    last_up_volume = starting_volume_km
    for i in range(total_weeks):
        if i >= taper_start:
            taper_index = i - taper_start
            factor = taper_factors[min(taper_index, len(taper_factors) - 1)]
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


def _step_km(step: RunStep, easy_pace: int) -> float:
    if step.distance_km is not None:
        return step.distance_km
    if step.duration_min is not None:
        # Duration-only legs (warmup, jog recoveries, strides) are costed at
        # their own target pace, or easy pace if they have none.
        return step.duration_min * 60 / (step.target_pace_sec_per_km or easy_pace)
    return 0.0


def steps_distance_km(steps: list[RunStep | RunRepeatStep], easy_pace: int) -> float:
    """Ground covered by a session, derived from its steps rather than hand-coded."""
    total = 0.0
    for s in steps:
        if isinstance(s, RunRepeatStep):
            per_rep = _step_km(s.work, easy_pace) + (_step_km(s.recovery, easy_pace) if s.recovery else 0.0)
            total += s.repeat_count * per_rep
        else:
            total += _step_km(s, easy_pace)
    return round(total, 1)


def _reps(label: str, count: int, km: float, pace: int, recovery_min: float, recovery_label: str = "Jog recovery") -> RunRepeatStep:
    work_label = label[:-1] if label.endswith("reps") else label  # "Threshold reps" -> "Threshold rep"
    return RunRepeatStep(
        label,
        repeat_count=count,
        work=RunStep(work_label, distance_km=km, target_pace_sec_per_km=pace),
        recovery=RunStep(recovery_label, duration_min=recovery_min),
    )


def _strides(f: AthleteFitness, count: int = 6) -> RunRepeatStep:
    return RunRepeatStep(
        "Strides",
        repeat_count=count,
        work=RunStep("Stride", duration_min=20 / 60, target_pace_sec_per_km=f.repetition_pace_sec_per_km),
        recovery=RunStep("Float", duration_min=1.0),
    )


def _hill_sprints(f: AthleteFitness) -> RunRepeatStep:
    # Effort-based (no pace target): ~10s near-max uphill, walk back down.
    return RunRepeatStep(
        "Hill sprints",
        repeat_count=8,
        work=RunStep("Hill sprint", duration_min=10 / 60),
        recovery=RunStep("Walk back", duration_min=2.0),
    )


def _wrap(f: AthleteFitness, *main: RunStep | RunRepeatStep, warmup: float = 15, cooldown: float = 10) -> list:
    return [
        RunStep("Warmup", duration_min=warmup, target_pace_sec_per_km=f.easy_pace_sec_per_km),
        *main,
        RunStep("Cooldown", duration_min=cooldown, target_pace_sec_per_km=f.easy_pace_sec_per_km),
    ]


# Quality sessions for the non-half profiles: profile -> phase -> variants.
# With a single quality slot per week, alternating variants by week index is
# how one slot covers both VO2max/economy and threshold work (Daniels/Pfitzinger
# 5k-10k emphasis). Each variant: fitness -> (session name, steps).
QUALITY_LIBRARY = {
    "5k": {
        "Base": [
            lambda f: ("Strides", _wrap(f, _strides(f))),
            lambda f: ("Hill sprints", _wrap(f, _hill_sprints(f))),
        ],
        "Build 1": [
            lambda f: ("R-pace 400s", _wrap(f, _reps("R-pace reps", 8, 0.4, f.repetition_pace_sec_per_km, 2.5))),
            lambda f: ("Cruise intervals", _wrap(f, _reps("Cruise interval", 3, 1.6, f.threshold_pace_sec_per_km, 1.5, "Jog"))),
        ],
        "Build 2": [
            lambda f: ("VO2max 1km reps", _wrap(f, _reps("I-pace reps", 5, 1.0, f.interval_pace_sec_per_km, 2.5))),
            lambda f: ("5k-pace 800s", _wrap(f, _reps("Race-pace reps", 6, 0.8, f.race_pace_sec_per_km, 1.5))),
        ],
        "Taper": [
            lambda f: ("Race-pace touch", _wrap(f, _reps("Race-pace reps", 4, 0.4, f.race_pace_sec_per_km, 2.0), warmup=12, cooldown=8)),
        ],
    },
    "10k": {
        "Base": [lambda f: ("Strides", _wrap(f, _strides(f)))],
        "Build 1": [
            lambda f: ("Cruise intervals", _wrap(f, _reps("Cruise interval", 4, 1.6, f.threshold_pace_sec_per_km, 1.0, "Jog"))),
            lambda f: ("VO2max 1km reps", _wrap(f, _reps("I-pace reps", 5, 1.0, f.interval_pace_sec_per_km, 2.5))),
        ],
        "Build 2": [
            lambda f: ("10k-pace reps", _wrap(f, _reps("Race-pace reps", 4, 1.6, f.race_pace_sec_per_km, 1.5))),
            lambda f: (
                "Threshold + VO2max",
                _wrap(
                    f,
                    _reps("Threshold reps", 2, 2.0, f.threshold_pace_sec_per_km, 2.0),
                    _reps("I-pace reps", 3, 0.8, f.interval_pace_sec_per_km, 2.0),
                ),
            ),
        ],
        "Taper": [
            lambda f: ("Race-pace touch", _wrap(f, _reps("Race-pace reps", 3, 1.0, f.race_pace_sec_per_km, 2.0), warmup=12, cooldown=8)),
        ],
    },
    "marathon": {
        "Base": [lambda f: ("Strides", _wrap(f, _strides(f)))],
        "Build 1": [
            lambda f: ("Cruise intervals", _wrap(f, _reps("Cruise interval", 4, 1.6, f.threshold_pace_sec_per_km, 1.0, "Jog"))),
            lambda f: ("Threshold 4km reps", _wrap(f, _reps("Threshold reps", 2, 4.0, f.threshold_pace_sec_per_km, 3.0))),
        ],
        "Build 2": [
            lambda f: ("Threshold reps", _wrap(f, _reps("Threshold reps", 3, 2.0, f.threshold_pace_sec_per_km, 2.0))),
            lambda f: ("Marathon-pace run", _wrap(f, RunStep("Marathon pace", distance_km=8.0, target_pace_sec_per_km=f.race_pace_sec_per_km))),
        ],
        "Taper": [
            lambda f: ("Marathon-pace touch", _wrap(f, _reps("Race-pace reps", 3, 1.6, f.race_pace_sec_per_km, 2.0), warmup=12, cooldown=8)),
        ],
    },
}


def _quality_session(phase_name: str, fitness: AthleteFitness, quality_date: date, week_index: int = 0) -> RunSessionPlan:
    profile = fitness.profile
    if profile.key not in QUALITY_LIBRARY:
        return _half_quality_session(phase_name, fitness, quality_date)
    by_phase = QUALITY_LIBRARY[profile.key]
    variants = by_phase.get(phase_name) or by_phase["Base"]  # Re-base shares Base's variants
    name, steps = variants[week_index % len(variants)](fitness)
    return RunSessionPlan(
        date=quality_date,
        name=name,
        phase_name=phase_name,
        steps=steps,
        total_distance_km=steps_distance_km(steps, fitness.easy_pace_sec_per_km),
        role="quality",
    )


def _half_quality_session(phase_name: str, fitness: AthleteFitness, quality_date: date) -> RunSessionPlan:
    easy = fitness.easy_pace_sec_per_km
    threshold = fitness.threshold_pace_sec_per_km
    race_pace = fitness.race_pace_sec_per_km

    if phase_name in ("Base", "Re-base"):
        steps = [
            RunStep("Warmup", duration_min=15, target_pace_sec_per_km=easy),
            RunRepeatStep(
                "Strides",
                repeat_count=6,
                work=RunStep("Stride", duration_min=20 / 60, target_pace_sec_per_km=threshold - 20),
                recovery=RunStep("Float", duration_min=1.0),
            ),
            RunStep("Cooldown", duration_min=10, target_pace_sec_per_km=easy),
        ]
        name = "Strides"
        # 1.2km is a fixed approximation of ground covered by the strides+floats
        # combined, not mechanically derived (the work leg is duration-based, not
        # distance-based) -- kept as-is from before the repeat-block decomposition.
        total_km = round(3.0 + 1.2, 1)
    elif phase_name == "Build 1":
        steps = [
            RunStep("Warmup", duration_min=15, target_pace_sec_per_km=easy),
            RunRepeatStep(
                "Cruise interval",
                repeat_count=3,
                work=RunStep("Cruise interval", distance_km=1.6, target_pace_sec_per_km=threshold),
                recovery=RunStep("Jog", duration_min=1.5),
            ),
            RunStep("Cooldown", duration_min=10, target_pace_sec_per_km=easy),
        ]
        name = "Cruise intervals"
        total_km = 4.8 + 4.0  # 3 * 1.6 (mechanically consistent with repeat_count * work.distance_km)
    elif phase_name == "Build 2":
        # The original composite labels didn't specify a recovery duration for
        # either block (unlike Re-base/Build 1/Taper, which do) -- these
        # recovery durations are engine-chosen defaults, not derived from spec
        # text. Revisit with a coach before treating them as authoritative.
        steps = [
            RunStep("Warmup", duration_min=15, target_pace_sec_per_km=easy),
            RunRepeatStep(
                "Threshold reps",
                repeat_count=2,
                work=RunStep("Threshold rep", distance_km=2.0, target_pace_sec_per_km=threshold),
                recovery=RunStep("Jog recovery", duration_min=2.0),
            ),
            RunRepeatStep(
                "Race-pace reps",
                repeat_count=2,
                work=RunStep("Race-pace rep", distance_km=1.0, target_pace_sec_per_km=race_pace),
                recovery=RunStep("Jog recovery", duration_min=1.5),
            ),
            RunStep("Cooldown", duration_min=10, target_pace_sec_per_km=easy),
        ]
        name = "Threshold + race-pace reps"
        total_km = 4.0 + 2.0 + 4.0
    else:  # Taper
        steps = [
            RunStep("Warmup", duration_min=12, target_pace_sec_per_km=easy),
            RunRepeatStep(
                "Race-pace rep",
                repeat_count=4,
                work=RunStep("Race-pace rep", distance_km=0.4, target_pace_sec_per_km=race_pace),
                recovery=RunStep("Full recovery", duration_min=3.0),
            ),
            RunStep("Cooldown", duration_min=8, target_pace_sec_per_km=easy),
        ]
        name = "Race-pace touch"
        total_km = 1.6 + 3.0

    return RunSessionPlan(
        date=quality_date, name=name, phase_name=phase_name, steps=steps, total_distance_km=round(total_km, 1), role="quality"
    )


def _easy_session(
    fitness: AthleteFitness, distance_km: float, run_date: date, phase_name: str, with_strides: bool = False
) -> RunSessionPlan:
    # Strides eat into the easy distance rather than adding on top, so the
    # week still sums to its target volume.
    run_km = round(max(distance_km - STRIDES_KM, 3.0), 1) if with_strides else distance_km
    steps: list[RunStep | RunRepeatStep] = [
        RunStep(
            "Easy/recovery run",
            distance_km=run_km,
            target_pace_sec_per_km=fitness.easy_pace_sec_per_km,
            hr_ceiling=fitness.aerobic_hr_ceiling,
        )
    ]
    name = "Easy run"
    if with_strides:
        steps.append(_strides(fitness))
        name = "Easy run + strides"
    return RunSessionPlan(
        date=run_date, name=name, phase_name=phase_name, steps=steps, total_distance_km=round(distance_km, 1), role="easy"
    )


def _long_run_session(fitness: AthleteFitness, distance_km: float, run_date: date, phase_name: str) -> RunSessionPlan:
    profile_key = fitness.profile.key
    if phase_name == "Build 2" and profile_key in ("half", "marathon", "10k"):
        # Later long runs embed a race-specific segment (spec section 5):
        # race pace for half/marathon, a threshold finish for 10k. 5k long
        # runs stay steady -- 5k-specific work lives in the quality slot.
        if profile_key == "half":
            segment_km = min(4.0, round(distance_km * 0.25, 1))
            segment = ("Race-pace segment", fitness.race_pace_sec_per_km)
            name = "Long run w/ race-pace segment"
        elif profile_key == "marathon":
            segment_km = min(16.0, round(distance_km * 0.40, 1))
            segment = ("Marathon-pace segment", fitness.race_pace_sec_per_km)
            name = "Long run w/ marathon-pace segment"
        else:
            segment_km = min(3.0, round(distance_km * 0.25, 1))
            segment = ("Threshold finish", fitness.threshold_pace_sec_per_km)
            name = "Long run w/ threshold finish"
        steady_km = round(distance_km - segment_km, 1)
        steps = [
            RunStep("Steady", distance_km=steady_km, target_pace_sec_per_km=fitness.easy_pace_sec_per_km),
            RunStep(segment[0], distance_km=segment_km, target_pace_sec_per_km=segment[1]),
        ]
    else:
        steps = [RunStep("Steady long run", distance_km=distance_km, target_pace_sec_per_km=fitness.easy_pace_sec_per_km)]
        name = "Long run"
    return RunSessionPlan(
        date=run_date, name=name, phase_name=phase_name, steps=steps, total_distance_km=round(distance_km, 1), role="long"
    )


def _assign_run_roles(sorted_days: list[int]) -> dict[int, str]:
    """Assign easy/quality/long roles across an arbitrary number of weekly run
    days: the latest day in the week is always the long run, the middle of
    the remaining days becomes the quality session (roughly equidistant from
    the long run and the start of the week), and every other day is easy."""
    if not sorted_days:
        return {}
    long_day = sorted_days[-1]
    others = sorted_days[:-1]
    roles = {long_day: "long"}
    if others:
        quality_day = others[len(others) // 2]
        for d in others:
            roles[d] = "quality" if d == quality_day else "easy"
    return roles


def generate_week(
    week_index: int,
    start_date: date,
    target_volume_km: float,
    phase: PhaseSpec,
    fitness: AthleteFitness,
    run_days: tuple[int, ...] = (1, 3, 6),  # Tue, Thu, Sun (Monday=0) by default
) -> WeekPlan:
    """Build one run session per entry in `run_days` -- the latest day is
    always long, the middle of the remaining days is quality, the rest easy
    (see _assign_run_roles). With the historical 3-day default this reduces
    to exactly the old fixed easy/quality/long assignment."""
    sorted_days = sorted(run_days)
    roles = _assign_run_roles(sorted_days)
    easy_days = [d for d in sorted_days if roles[d] == "easy"]

    profile = fitness.profile
    long_km = round(target_volume_km * profile.long_run_share, 1)
    if profile.long_run_cap_km is not None:
        # Anything over the cap flows to the easy days via `remainder`.
        long_km = min(long_km, profile.long_run_cap_km)
    remainder = max(target_volume_km - long_km, 0.0)
    # Split the non-long volume evenly across the easy days plus one "slot"
    # for quality (quality's own distance is actually dictated by its
    # structure -- see _quality_session -- this just sizes the easy days).
    easy_km = round(remainder / (len(easy_days) + 1), 1)
    strides_day = easy_days[0] if easy_days and profile.easy_strides and phase.name != "Taper" else None

    sessions: list[RunSessionPlan] = []
    for d in sorted_days:
        session_date = start_date + timedelta(days=d)
        role = roles[d]
        if role == "long":
            sessions.append(_long_run_session(fitness, long_km, session_date, phase.name))
        elif role == "quality":
            sessions.append(_quality_session(phase.name, fitness, session_date, week_index))
        else:
            sessions.append(_easy_session(fitness, easy_km, session_date, phase.name, with_strides=d == strides_day))

    return WeekPlan(
        week_index=week_index,
        start_date=start_date,
        phase_name=phase.name,
        target_volume_km=target_volume_km,
        is_down_week=False,
        sessions=sessions,
    )


def generate_run_plan(
    today: date,
    race_date: date,
    fitness: AthleteFitness,
    run_days: tuple[int, ...] = (1, 3, 6),
) -> tuple[list[PhaseSpec], list[WeekPlan]]:
    """Top-level entry point: race date + distance (via fitness.race_distance_km,
    which picks the RaceProfile) + current fitness -> full block of weekly plans."""
    profile = fitness.profile
    total_weeks = weeks_to_race(today, race_date)
    phases = build_phases(total_weeks, profile)
    taper_start = next(p.start_week for p in phases if p.name == "Taper")
    volumes = build_weekly_volumes(total_weeks, fitness.weekly_volume_km, phases, profile)

    weeks: list[WeekPlan] = []
    base_monday = week_start(today)
    for i in range(total_weeks):
        phase = phase_for_week(phases, i)
        week_plan = generate_week(i, base_monday + timedelta(weeks=i), volumes[i], phase, fitness, run_days)
        week_plan.is_down_week = is_down_week(i, taper_start)
        weeks.append(week_plan)
    return phases, weeks
