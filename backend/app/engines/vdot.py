"""Daniels' VDOT model: derives a fitness score from threshold pace, then a
race pace for a given distance. Pure, no DB/HTTP deps, matching this
package's engine convention. Replaces the previous `threshold_pace + 12s/km`
placeholder in `engines/running.py`'s `AthleteFitness.race_pace_sec_per_km`.
"""
from __future__ import annotations

import math

THRESHOLD_CALIBRATION_MINUTES = 60.0  # Daniels defines Threshold as the pace
# sustainable for about 60 minutes -- this is the textbook definition, not an
# assumption made here.


def _vo2_from_velocity(v_m_per_min: float) -> float:
    return -4.60 + 0.182258 * v_m_per_min + 0.000104 * v_m_per_min**2


def _pct_vo2max(t_min: float) -> float:
    return 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)


def _velocity_from_vo2(vo2: float) -> float:
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b**2 - 4 * a * c)) / (2 * a)


def vdot_from_threshold_pace(threshold_pace_sec_per_km: int) -> float:
    v = 1000 / threshold_pace_sec_per_km * 60  # meters per minute
    return _vo2_from_velocity(v) / _pct_vo2max(THRESHOLD_CALIBRATION_MINUTES)


def race_pace_sec_per_km(vdot: float, distance_km: float, iterations: int = 6) -> int:
    """Fixed-point iteration: predicted race time and %VO2max depend on each
    other (a longer race sustains a lower %VO2max), so this converges rather
    than solving in closed form. Converges in ~3 iterations in practice for
    any realistic distance/VDOT; 6 gives margin."""
    distance_m = distance_km * 1000
    t_guess = distance_km * 4.0  # seed at an arbitrary 4:00/km; converges regardless of seed
    v = 0.0
    for _ in range(iterations):
        target_vo2 = _pct_vo2max(t_guess) * vdot
        v = _velocity_from_vo2(target_vo2)
        t_guess = distance_m / v
    return round(1000 / v * 60)
