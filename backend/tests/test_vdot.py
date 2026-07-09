from app.engines.vdot import race_pace_sec_per_km, vdot_from_threshold_pace


def test_vdot_from_threshold_pace_is_in_sane_range():
    vdot = vdot_from_threshold_pace(330)  # 5:30/km, this app's athlete default
    assert 30 <= vdot <= 45


def test_race_pace_matches_hand_verified_value():
    vdot = vdot_from_threshold_pace(330)
    pace = race_pace_sec_per_km(vdot, 21.0975)
    # Hand-verified via the Daniels formulas during planning: ~345s/km (5:45/km).
    assert abs(pace - 345) <= 5


def test_faster_threshold_pace_produces_faster_race_pace():
    slow_vdot = vdot_from_threshold_pace(360)  # 6:00/km
    fast_vdot = vdot_from_threshold_pace(300)  # 5:00/km
    slow_race_pace = race_pace_sec_per_km(slow_vdot, 21.0975)
    fast_race_pace = race_pace_sec_per_km(fast_vdot, 21.0975)
    assert fast_race_pace < slow_race_pace


def test_race_pace_is_deterministic():
    vdot = vdot_from_threshold_pace(330)
    first = race_pace_sec_per_km(vdot, 21.0975)
    second = race_pace_sec_per_km(vdot, 21.0975)
    assert first == second


def test_shorter_race_distance_has_faster_pace_than_longer_for_same_vdot():
    vdot = vdot_from_threshold_pace(330)
    pace_10k = race_pace_sec_per_km(vdot, 10.0)
    pace_half = race_pace_sec_per_km(vdot, 21.0975)
    assert pace_10k < pace_half
