"""Per-distance block shape: how long the block is, how long the taper is,
how big the long run gets, and which strength flavour rides alongside.

Pure, DB-free -- derived from Race.distance_km on the fly (no stored race
type), so any distance the athlete types snaps to the nearest profile.

The "half" profile reproduces the engine's original (pre-profile) constants
exactly; the others follow the usual distance-specific conventions:
  - taper: ~1 week for 5k, ~10-14 days for 10k/half, ~3 weeks for marathon
    (Mujika/Bosquet taper meta-analysis: cut volume 40-60%, keep intensity).
  - long run: a smaller share of the week and a hard cap for 5k/10k, where
    the long run supports the aerobic base rather than rehearsing race
    duration.
  - strength: "power" adds plyometrics for 5k/10k (economy matters most at
    shorter distances); "endurance" caps lifting volume for the marathon
    while weekly mileage peaks.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HALF_MARATHON_KM = 21.0975


@dataclass(frozen=True)
class RaceProfile:
    key: str  # "5k" | "10k" | "half" | "marathon"
    label: str
    template_weeks: int  # standard block length; extra weeks become Base
    taper_factors: tuple[float, ...]  # one per taper week, applied to the last up-week volume
    rebase_share: float  # share of the ramp (non-Base, non-Taper weeks)
    build1_share: float
    long_run_share: float  # fraction of weekly volume assigned to the long run
    long_run_cap_km: float | None
    easy_strides: bool  # append strides to one easy run each non-taper week
    strength_flavour: str  # "standard" | "power" | "endurance"
    phase_focus: dict[str, str] = field(default_factory=dict)

    @property
    def taper_weeks(self) -> int:
        return len(self.taper_factors)


_DEFAULT_FOCUS = {
    "Base": "aerobic hold",
    "Re-base": "aerobic rebuild",
    "Build 1": "threshold intro",
    "Build 2": "race-specific",
    "Taper": "sharpen",
}

FIVE_K = RaceProfile(
    key="5k",
    label="5K",
    template_weeks=10,
    taper_factors=(0.60,),
    rebase_share=0.25,
    build1_share=0.40,
    long_run_share=0.25,
    long_run_cap_km=16.0,
    easy_strides=True,
    strength_flavour="power",
    phase_focus={
        **_DEFAULT_FOCUS,
        "Re-base": "aerobic rebuild + strides/hills",
        "Build 1": "economy + threshold",
        "Build 2": "VO2max + 5k pace",
    },
)

TEN_K = RaceProfile(
    key="10k",
    label="10K",
    template_weeks=12,
    taper_factors=(0.80, 0.55),
    rebase_share=0.30,
    build1_share=0.35,
    long_run_share=0.28,
    long_run_cap_km=19.0,
    easy_strides=True,
    strength_flavour="power",
    phase_focus={
        **_DEFAULT_FOCUS,
        "Build 1": "threshold + VO2max",
        "Build 2": "10k pace + threshold",
    },
)

HALF = RaceProfile(
    key="half",
    label="Half marathon",
    template_weeks=16,
    taper_factors=(0.70, 0.45),
    rebase_share=0.30,
    build1_share=0.35,
    long_run_share=0.35,
    long_run_cap_km=None,
    easy_strides=False,
    strength_flavour="standard",
    phase_focus=dict(_DEFAULT_FOCUS),
)

MARATHON = RaceProfile(
    key="marathon",
    label="Marathon",
    template_weeks=18,
    taper_factors=(0.80, 0.65, 0.45),
    rebase_share=0.30,
    build1_share=0.35,
    long_run_share=0.35,
    long_run_cap_km=34.0,
    easy_strides=False,
    strength_flavour="endurance",
    phase_focus={
        **_DEFAULT_FOCUS,
        "Build 1": "threshold + volume",
        "Build 2": "marathon pace",
    },
)

PROFILES = {p.key: p for p in (FIVE_K, TEN_K, HALF, MARATHON)}


def profile_for_distance(distance_km: float | None) -> RaceProfile:
    """Snap any race distance to the nearest training profile."""
    if distance_km is None:
        return HALF
    if distance_km <= 7.5:
        return FIVE_K
    if distance_km <= 15:
        return TEN_K
    if distance_km <= 30:
        return HALF
    return MARATHON
