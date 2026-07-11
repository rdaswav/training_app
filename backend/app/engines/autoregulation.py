"""Autoregulation: the responsive layer that reads logged/completed sessions
and adjusts the plan (spec sections 5 and 6). Pure functions -- callers persist
the resulting adjustments."""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Running autoregulation
# ---------------------------------------------------------------------------

@dataclass
class RunAutoregResult:
    action: str  # "progress" | "hold" | "soften"
    pace_adjustment_sec_per_km: int  # negative = faster prescribed paces going forward
    note: str


def missed_session_policy() -> str:
    return "Missed sessions are not made up -- the week re-flows around the remaining prescribed sessions."


def evaluate_easy_or_long_run(
    prescribed_pace_sec_per_km: int,
    actual_pace_sec_per_km: int | None,
    actual_hr: int | None,
    hr_ceiling: int,
    wellness_ok: bool = True,
) -> RunAutoregResult:
    if actual_hr is not None and actual_hr > hr_ceiling + 5:
        return RunAutoregResult("soften", 5, "HR drifted above the aerobic ceiling -- ease prescribed pace, soften next quality session.")
    if not wellness_ok:
        return RunAutoregResult("hold", 0, "Wellness flagged (poor sleep/HRV) -- hold current paces and volume.")
    if (
        actual_pace_sec_per_km is not None
        and actual_hr is not None
        and actual_pace_sec_per_km <= prescribed_pace_sec_per_km - 10
        and actual_hr <= hr_ceiling - 5
    ):
        return RunAutoregResult("progress", -5, "Faster than target pace at/under HR ceiling -- nudge fitness estimate up.")
    return RunAutoregResult("hold", 0, "On target -- hold current prescription.")


def evaluate_quality_session(
    prescribed_pace_sec_per_km: int,
    actual_pace_sec_per_km: int | None,
    hit_reps: bool = True,
    wellness_ok: bool = True,
) -> RunAutoregResult:
    if not hit_reps or not wellness_ok:
        return RunAutoregResult("soften", 5, "Missed the prescribed reps/pace or poor wellness -- soften next quality session.")
    if actual_pace_sec_per_km is not None and actual_pace_sec_per_km <= prescribed_pace_sec_per_km - 5:
        return RunAutoregResult("progress", -3, "Hit reps comfortably under target pace -- nudge threshold pace up.")
    return RunAutoregResult("hold", 0, "Hit the prescribed target -- hold.")


# ---------------------------------------------------------------------------
# Strength autoregulation
# ---------------------------------------------------------------------------

@dataclass
class StrengthLogSet:
    reps: int
    weight_kg: float
    rir_actual: float | None = None


@dataclass
class StrengthAutoregResult:
    summary: str
    feedback: str
    next_instruction: str  # human-readable guidance
    action: str  # "progress" | "hold" | "back_off"


def _parse_rep_range(rep_range: str) -> tuple[int, int]:
    lo, hi = rep_range.split("-")
    return int(lo), int(hi)


def evaluate_strength_log(prescription, logged_sets: list[StrengthLogSet]) -> StrengthAutoregResult:
    """prescription is a StrengthPrescription (engines/strength.py)."""
    if not logged_sets:
        return StrengthAutoregResult("No sets logged.", "Nothing to evaluate.", "Log sets next session.", "hold")

    lo, _hi = _parse_rep_range(prescription.reps)
    hit_reps = all(s.reps >= lo for s in logged_sets)
    rir_values = [s.rir_actual for s in logged_sets if s.rir_actual is not None]
    avg_rir = sum(rir_values) / len(rir_values) if rir_values else None

    first = logged_sets[0]
    summary = f"{len(logged_sets)}x{first.reps}x{first.weight_kg}kg logged for {prescription.pattern} (target {prescription.sets}x{prescription.reps} @ RIR {prescription.rir})"

    if not hit_reps:
        return StrengthAutoregResult(
            summary, "Missed target reps -- fatigue outpaced the prescription.",
            "Back off: reduce load 5-10% or drop a set next session.", "back_off",
        )
    if avg_rir is not None and avg_rir < max(prescription.rir - 1, 0):
        return StrengthAutoregResult(
            summary, "Reps hit but well under target RIR -- grinding, high fatigue cost.",
            "Hold: repeat the same load/reps next session.", "hold",
        )
    if avg_rir is not None and avg_rir > prescription.rir + 1.5:
        return StrengthAutoregResult(
            summary, "Reps hit with a lot left in reserve -- undershooting the prescribed effort.",
            "Progress: add load or a rep next session.", "progress",
        )
    return StrengthAutoregResult(
        summary, "Reps and effort landed in the target window.",
        "Progress: add load or a rep next session.", "progress",
    )
