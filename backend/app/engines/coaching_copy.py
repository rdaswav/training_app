"""Plain-English coaching copy for the /about page and the Today-view
session cue. Pure, DB-free -- callers pass in a phase name / mesocycle mode
already resolved via dashboard_summary, no DB dependency here.
"""
from __future__ import annotations

from app.models import SessionType

PHASE_ORDER = ["Base", "Re-base", "Build 1", "Build 2", "Taper"]

PHASE_COPY: dict[str, str] = {
    "Base": (
        "Building the aerobic engine. Volume climbs gradually, effort stays easy, "
        "and the goal is simply more comfortable miles -- not speed."
    ),
    "Re-base": (
        "A short reset after a hard block or a down-week, rebuilding aerobic "
        "volume before the next push so fitness gains hold rather than fray."
    ),
    "Build 1": (
        "Threshold work enters the picture. Easy running still makes up most of "
        "the week, but quality sessions start teaching the body to hold a harder pace."
    ),
    "Build 2": (
        "Race-specific work. Sessions start to look like race day -- pace, "
        "distance, and effort all converge on what's actually needed on the day."
    ),
    "Taper": (
        "Volume drops, intensity stays sharp. The fitness is already built -- "
        "this phase is about arriving fresh, not fitter."
    ),
}

MODE_COPY: dict[str, str] = {
    "accumulate": (
        "Strength volume is building. Sets climb toward MRV week over week, RIR "
        "gets tighter, and the weight room asks for real effort."
    ),
    "maintenance": (
        "Just enough strength work to hold what's been built, without adding "
        "fatigue that would blunt the running. Lighter, shorter, still purposeful."
    ),
    "minimal": (
        "Strength work steps back almost entirely so the legs are fresh for "
        "racing. A touch of movement, nothing that costs recovery."
    ),
}

_RUN_CUES: dict[str, str] = {
    "Base": "Easy running -- the point is aerobic volume, not pace.",
    "Re-base": "Rebuilding volume after a reset -- keep it comfortable.",
    "Build 1": "Threshold work -- hold the effort, don't chase the number.",
    "Build 2": "Race-specific work -- this is what race day should feel like.",
    "Taper": "Sharp but short -- fitness is banked, just stay loose.",
}

_STRENGTH_CUES: dict[str, str] = {
    "accumulate": "Building phase -- expect the weights to feel heavier by the last set.",
    "maintenance": "Maintenance mode -- enough to hold strength, not enough to add fatigue.",
    "minimal": "Minimal load -- keep this light, the legs need to stay fresh.",
}

_FALLBACK_CUE = "No active race plan -- log this session as usual."


def session_cue(session_type: SessionType, phase_name: str | None, mesocycle_mode: str | None) -> str:
    if session_type == SessionType.RUN:
        if phase_name and phase_name in _RUN_CUES:
            return _RUN_CUES[phase_name]
    elif session_type == SessionType.STRENGTH:
        if mesocycle_mode and mesocycle_mode in _STRENGTH_CUES:
            return _STRENGTH_CUES[mesocycle_mode]
    return _FALLBACK_CUE
