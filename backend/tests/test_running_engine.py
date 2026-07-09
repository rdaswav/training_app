from datetime import date, timedelta

import pytest

from app.engines.running import (
    AthleteFitness,
    build_phases,
    build_weekly_volumes,
    generate_run_plan,
    weeks_to_race,
)


def test_weeks_to_race_counts_inclusive_of_race_week():
    today = date(2026, 7, 8)  # Wednesday
    race = date(2026, 9, 27)  # a Sunday, ~12 weeks out
    n = weeks_to_race(today, race)
    assert n >= 11


def test_build_phases_full_block_sums_to_total_weeks():
    phases = build_phases(16)
    assert phases[-1].name == "Taper"
    assert phases[-1].end_week == 15
    assert phases[0].start_week == 0
    # phases are contiguous and non-overlapping, covering every week exactly once
    covered = set()
    for p in phases:
        for w in range(p.start_week, p.end_week + 1):
            assert w not in covered
            covered.add(w)
    assert covered == set(range(16))


def test_build_phases_over_16_weeks_adds_base_phase():
    phases = build_phases(20)
    assert phases[0].name == "Base"
    assert phases[0].end_week - phases[0].start_week + 1 == 4
    assert phases[-1].name == "Taper"


def test_build_phases_compressed_block_protects_taper():
    phases = build_phases(6)
    assert phases[-1].name == "Taper"
    assert phases[-1].end_week - phases[-1].start_week + 1 == 2
    total_weeks_covered = phases[-1].end_week + 1
    assert total_weeks_covered == 6


def test_build_phases_minimum_viable_block():
    phases = build_phases(3)
    assert phases[-1].name == "Taper"
    assert sum(p.end_week - p.start_week + 1 for p in phases) == 3


def test_weekly_volumes_ramp_with_down_weeks_and_taper():
    phases = build_phases(12)
    volumes = build_weekly_volumes(12, 30.0, phases)
    taper_start = next(p.start_week for p in phases if p.name == "Taper")

    # Down week every 4th week (index 3) should dip below the previous week.
    assert volumes[3] < volumes[2]
    # Non-down, non-taper weeks should grow.
    assert volumes[1] > volumes[0]
    # Taper weeks should be well below peak volume.
    peak = max(volumes[:taper_start])
    assert volumes[taper_start] < peak
    assert volumes[taper_start + 1] < volumes[taper_start]


def test_generate_run_plan_produces_three_runs_per_week():
    today = date(2026, 7, 8)
    race = today + timedelta(weeks=14)
    fitness = AthleteFitness(
        weekly_volume_km=30.0,
        easy_pace_sec_per_km=390,
        threshold_pace_sec_per_km=330,
        aerobic_hr_ceiling=150,
    )
    phases, weeks = generate_run_plan(today, race, fitness)
    assert len(weeks) == weeks_to_race(today, race)
    for week in weeks:
        assert len(week.sessions) == 3
        roles = {s.role for s in week.sessions}
        assert roles == {"easy", "quality", "long"}
        for s in week.sessions:
            assert s.total_distance_km > 0


def test_build_2_long_run_embeds_race_pace_segment():
    today = date(2026, 7, 8)
    race = today + timedelta(weeks=14)
    fitness = AthleteFitness(30.0, 390, 330, 150)
    phases, weeks = generate_run_plan(today, race, fitness)
    build2_weeks = [w for w in weeks if w.phase_name == "Build 2"]
    assert build2_weeks, "expected at least one Build 2 week in a 14-week block"
    long_run = next(s for s in build2_weeks[0].sessions if s.role == "long")
    assert any("race" in step.label.lower() for step in long_run.steps)


def test_taper_quality_session_is_short_race_pace_touch():
    today = date(2026, 7, 8)
    race = today + timedelta(weeks=12)
    fitness = AthleteFitness(30.0, 390, 330, 150)
    phases, weeks = generate_run_plan(today, race, fitness)
    taper_weeks = [w for w in weeks if w.phase_name == "Taper"]
    assert len(taper_weeks) == 2
    quality = next(s for s in taper_weeks[0].sessions if s.role == "quality")
    assert quality.total_distance_km < 6


@pytest.mark.parametrize("total_weeks", [3, 6, 8, 12, 14, 16, 18, 24])
def test_build_phases_never_crashes_across_range(total_weeks):
    phases = build_phases(total_weeks)
    assert phases[-1].name == "Taper"
    assert sum(p.end_week - p.start_week + 1 for p in phases) == total_weeks
