"""RP-style strength engine (spec section 6).

Two layers, both implemented here:
  - periodization: the scheduled mesocycle skeleton (MEV -> MAV -> MRV -> deload,
    RIR 3 -> 1), set in advance.
  - race-proximity modulation: nests that skeleton inside the running macrocycle,
    overriding it toward maintenance/minimal as the race nears.

Autoregulation from actual logged sets lives in engines/autoregulation.py --
this module only produces the *prescription* skeleton.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

ACCUMULATION_WEEKS = 4  # + 1 deload week = 5-week mesocycle (spec: "4-6 wks + deload")
MESOCYCLE_LENGTH = ACCUMULATION_WEEKS + 1

# Movement-pattern day template (spec section 6). Keyed by weekday, Monday=0.
DAY_TEMPLATE: dict[int, list[tuple[str, str]]] = {
    0: [  # Mon — Upper
        ("horizontal_push", "compound"),
        ("vertical_pull", "compound"),
        ("horizontal_pull", "compound"),
        ("shoulder_accessory", "accessory"),
        ("core", "core"),
    ],
    2: [  # Wed — Lower
        ("squat", "compound"),
        ("hinge", "compound"),
        ("single_leg", "accessory"),
        ("core", "core"),
    ],
    4: [  # Fri — Hybrid
        ("unilateral", "accessory"),
        ("carry", "accessory"),
        ("posterior_chain", "compound"),
        ("core_running_support", "core"),
    ],
}


@dataclass
class VolumeLandmark:
    mev: int
    mav: int
    mrv: int
    mv: int | None = None

    def mv_or_default(self) -> int:
        return self.mv if self.mv is not None else max(1, round(self.mev * 0.5))


DEFAULT_LANDMARKS: dict[str, VolumeLandmark] = {
    "compound": VolumeLandmark(mev=2, mav=4, mrv=6),
    "accessory": VolumeLandmark(mev=2, mav=3, mrv=4),
    "core": VolumeLandmark(mev=2, mav=3, mrv=4),
}

RUN_PHASE_MODE = {
    "Base": "accumulate",
    "Re-base": "accumulate",
    "Build 1": "accumulate",
    "Build 2": "maintenance",
    "Taper": "minimal",
}


@dataclass
class StrengthPrescription:
    pattern: str
    category: str
    sets: int
    reps: str
    rir: float
    note: str


def race_proximity_mode(run_phase_name: str) -> str:
    """Table from spec section 6: strength behaviour by running-phase proximity to race."""
    return RUN_PHASE_MODE.get(run_phase_name, "accumulate")


def mesocycle_week_local(global_week_index: int, mesocycle_start_week: int = 0) -> int:
    return (global_week_index - mesocycle_start_week) % MESOCYCLE_LENGTH


def is_deload_week(local_week: int) -> bool:
    return local_week >= ACCUMULATION_WEEKS


def _running_recovery_weeks(total_weeks: int, taper_start: int) -> set[int]:
    """Weeks the running plan already treats as lower-load: its down-weeks
    (engines/running.py's is_down_week -- every 4th week) plus every Taper
    week (see #31)."""
    from app.engines.running import is_down_week

    return {i for i in range(total_weeks) if is_down_week(i, taper_start) or i >= taper_start}


def best_mesocycle_offset(total_weeks: int, taper_start: int) -> int:
    """Nudges the strength mesocycle's deload week toward the running plan's
    down-weeks/taper without forcing exact alignment every cycle -- the two
    clocks have different periods (4 vs 5 weeks), so they can't coincide on
    every repeat; forcing it would mean stretching/compressing the strength
    mesocycle's length instead (rejected -- see #31's decision).

    Picks whichever of the MESOCYCLE_LENGTH possible start offsets minimizes
    the total distance from each deload week to its nearest running
    down-week/taper week across the whole macrocycle."""
    targets = sorted(_running_recovery_weeks(total_weeks, taper_start))
    if not targets:
        return 0

    def _nearest_distance(week: int) -> int:
        return min(abs(week - t) for t in targets)

    best_offset, best_total = 0, None
    for offset in range(MESOCYCLE_LENGTH):
        deload_weeks = [i for i in range(total_weeks) if (i - offset) % MESOCYCLE_LENGTH == ACCUMULATION_WEEKS]
        total_distance = sum(_nearest_distance(w) for w in deload_weeks)
        if best_total is None or total_distance < best_total:
            best_offset, best_total = offset, total_distance
    return best_offset


def _parse_rep_range(reps: str) -> tuple[int, int]:
    lo, hi = reps.split("-")
    return int(lo), int(hi)


def estimate_e1rm(weight_kg: float, reps: int) -> float:
    """Epley formula (decided direction, see #29): 1RM = weight * (1 + reps/30).
    A single-rep set is just the weight itself, Epley's formula degenerates
    to that at reps=1 anyway but reps<=1 is guarded explicitly for reps=0."""
    if reps <= 1:
        return weight_kg
    return weight_kg * (1 + reps / 30)


def best_e1rm_from_sets(logged_sets) -> float | None:
    """logged_sets: objects with .weight_kg/.reps (StrengthLogSet or
    equivalent). Takes the single best (highest-implied-1RM) set rather than
    an average -- a fatigued later set in the same session underestimates
    true current capacity."""
    if not logged_sets:
        return None
    return max(estimate_e1rm(s.weight_kg, s.reps) for s in logged_sets)


def latest_e1rm_by_pattern(completed_rows: list[dict]) -> dict[str, float]:
    """completed_rows: [{"pattern": str, "sets": [{"reps": int, "weight_kg": float, ...}, ...]}, ...],
    ordered most-recent-first (as strength_history_view's query already
    returns them) -- tracked per movement pattern rather than per specific
    exercise (decided direction, see #29), since exercise selection is
    flexible/self-directed and isn't tracked as one continuous lift history.
    Returns each pattern's e1RM from its single most recent logged session,
    the "current" fitness estimate prescribe_next_load projects from."""
    result: dict[str, float] = {}
    for row in completed_rows:
        pattern = row.get("pattern")
        if not pattern or pattern in result:
            continue  # rows are latest-first -- first hit per pattern is the most recent
        sets = row.get("sets") or []
        if not sets:
            continue
        result[pattern] = max(estimate_e1rm(s["weight_kg"], s["reps"]) for s in sets)
    return result


def prescribe_next_load(e1rm: float, reps: str, rir: float) -> float:
    """Given an e1RM (per movement pattern) and a target rep-range/RIR
    prescription, back out an actual kg working-weight target via Epley:
    treats (target reps + RIR) as the approximate reps-to-failure the
    working weight should be pegged against."""
    lo, hi = _parse_rep_range(reps)
    target_reps = (lo + hi) / 2
    effective_reps = max(target_reps + rir, 1)
    return round(e1rm / (1 + effective_reps / 30), 1)


def _reps_for(category: str) -> str:
    # Compounds stay in the 3-5 rep strength range; accessories/core higher-rep (spec section 6).
    return "3-5" if category == "compound" else "8-12"


def prescribe(pattern: str, category: str, local_week: int, mode: str) -> StrengthPrescription:
    landmark = DEFAULT_LANDMARKS[category]

    if mode == "minimal":
        return StrengthPrescription(
            pattern, category, sets=1, reps=_reps_for(category), rir=4.0,
            note="Taper: movement-pattern only, strip fatigue, no new stimulus",
        )
    if mode == "maintenance":
        return StrengthPrescription(
            pattern, category, sets=landmark.mev, reps=_reps_for(category), rir=2.5,
            note="Race build: maintenance at MV, hold load, minimise soreness",
        )

    # mode == "accumulate": follow the mesocycle skeleton.
    if is_deload_week(local_week):
        return StrengthPrescription(
            pattern, category, sets=landmark.mv_or_default(), reps=_reps_for(category), rir=4.0,
            note="Deload: light, strip fatigue before next block",
        )
    frac = local_week / max(ACCUMULATION_WEEKS - 1, 1)
    sets = round(landmark.mev + (landmark.mrv - landmark.mev) * frac)
    rir = round(3.0 - (3.0 - 1.0) * frac, 1)
    return StrengthPrescription(
        pattern, category, sets=sets, reps=_reps_for(category), rir=rir,
        note="Accumulation: progress load next session if reps hit at/below target RIR",
    )


def select_exercise(pattern: str, exercises: list[dict], injury_flags: list[str]) -> dict | None:
    """Pattern-based substitution: swap freely within a pattern, excluding injury-flagged lifts."""
    excluded = set(injury_flags)
    for exercise in exercises:
        if exercise["pattern"] == pattern and not (set(exercise.get("injury_tags", [])) & excluded):
            return exercise
    return None


@dataclass
class StrengthSessionPlan:
    date: date
    name: str
    prescriptions: list[StrengthPrescription]


def generate_strength_session(
    weekday: int,
    session_date: date,
    global_week_index: int,
    run_phase_name: str,
    mesocycle_start_week: int = 0,
) -> StrengthSessionPlan | None:
    """Build one strength day's prescription skeleton (patterns only; exercise
    selection is a separate DB-backed step via select_exercise)."""
    patterns = DAY_TEMPLATE.get(weekday)
    if not patterns:
        return None

    mode = race_proximity_mode(run_phase_name)
    local_week = mesocycle_week_local(global_week_index, mesocycle_start_week)
    prescriptions = [prescribe(pattern, category, local_week, mode) for pattern, category in patterns]
    names = {0: "Upper", 2: "Lower", 4: "Hybrid"}
    return StrengthSessionPlan(date=session_date, name=names.get(weekday, "Strength"), prescriptions=prescriptions)


def all_prescriptions_logged(prescriptions: list[dict], logged_patterns: set[str]) -> bool:
    return {p["pattern"] for p in prescriptions} <= logged_patterns


def generate_strength_plan(
    start_date: date,
    total_weeks: int,
    phase_for_week_index,  # callable: int -> phase name, from the running engine
    mesocycle_start_week: int = 0,
) -> list[StrengthSessionPlan]:
    """Generate strength sessions for every week of the macrocycle, on the fixed
    Mon/Wed/Fri template, modulated by race proximity. `mesocycle_start_week`
    should be best_mesocycle_offset's output, computed by the caller once
    against the same running plan (see #31) -- defaults to 0 (the old,
    phase-unaware behavior) for callers that don't care."""
    from app.engines.running import week_start

    base_monday = week_start(start_date)
    sessions: list[StrengthSessionPlan] = []
    for week_index in range(total_weeks):
        week_monday = base_monday + timedelta(weeks=week_index)
        phase_name = phase_for_week_index(week_index)
        for weekday in DAY_TEMPLATE:
            session_date = week_monday + timedelta(days=weekday)
            session = generate_strength_session(weekday, session_date, week_index, phase_name, mesocycle_start_week)
            if session:
                sessions.append(session)
    return sessions
