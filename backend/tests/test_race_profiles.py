import pytest

from app.engines.race_profiles import FIVE_K, HALF, MARATHON, PROFILES, TEN_K, profile_for_distance


@pytest.mark.parametrize(
    "km, expected",
    [(3.0, FIVE_K), (5.0, FIVE_K), (7.5, FIVE_K), (8.0, TEN_K), (10.0, TEN_K), (15.0, TEN_K), (16.1, HALF),
     (21.1, HALF), (30.0, HALF), (42.195, MARATHON), (50.0, MARATHON), (None, HALF)],
)
def test_profile_for_distance_buckets(km, expected):
    assert profile_for_distance(km) is expected


def test_half_profile_matches_the_original_engine_constants():
    # The pre-profile engine's constants -- the half profile must not drift.
    assert HALF.template_weeks == 16
    assert HALF.taper_factors == (0.70, 0.45)
    assert (HALF.rebase_share, HALF.build1_share) == (0.30, 0.35)
    assert HALF.long_run_share == 0.35 and HALF.long_run_cap_km is None
    assert not HALF.easy_strides and HALF.strength_flavour == "standard"


def test_every_profile_has_focus_for_every_phase():
    for p in PROFILES.values():
        assert set(p.phase_focus) == {"Base", "Re-base", "Build 1", "Build 2", "Taper"}
