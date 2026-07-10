from datetime import date, timedelta

from app.engines.calendar import build_unified_week, find_adjacency_conflicts


def _week_dates(monday: date) -> list[date]:
    return [monday + timedelta(days=i) for i in range(7)]


def test_default_template_resolves_one_conflict_and_flags_the_other():
    # The fixed default template (Mon/Wed/Fri strength, Tue/Thu/Sat run) has two
    # built-in adjacencies: Wed(Lower) before Thu(quality), and Fri(Hybrid)
    # before Sat(long). With only one rest day (Sunday) to swap into, the
    # scheduler can resolve the first conflict it encounters and must flag the
    # other in place.
    monday = date(2026, 7, 6)
    run_by_date = {
        monday + timedelta(days=1): {"name": "Easy run", "role": "easy"},
        monday + timedelta(days=3): {"name": "Cruise intervals", "role": "quality"},
        monday + timedelta(days=5): {"name": "Long run", "role": "long"},
    }
    strength_by_date = {
        monday: {"name": "Upper", "prescriptions": [{"pattern": "horizontal_push"}]},
        monday + timedelta(days=2): {"name": "Lower", "prescriptions": [{"pattern": "squat"}, {"pattern": "hinge"}]},
        monday + timedelta(days=4): {"name": "Hybrid", "prescriptions": [{"pattern": "posterior_chain"}, {"pattern": "carry"}]},
    }
    week = build_unified_week(run_by_date, strength_by_date, _week_dates(monday))

    wednesday = next(s for s in week if s.date == monday + timedelta(days=2))
    friday = next(s for s in week if s.date == monday + timedelta(days=4))
    sunday = next(s for s in week if s.date == monday + timedelta(days=6))

    # The Wed/Thu conflict (first encountered chronologically) gets auto-shuffled:
    # Wednesday becomes rest, and the Lower session moves to Sunday.
    assert wednesday.session_type == "rest"
    assert sunday.session_type == "strength"
    assert sunday.name == "Lower"

    # The Fri/Sat conflict has no remaining free rest day, so it's flagged in place.
    assert friday.session_type == "strength"
    assert friday.note != ""
    assert friday.flagged is True


def test_no_conflict_when_no_hard_lower_pattern_precedes_key_run():
    monday = date(2026, 7, 6)
    run_by_date = {
        monday + timedelta(days=5): {"name": "Long run", "role": "long"},
    }
    strength_by_date = {
        monday + timedelta(days=4): {"name": "Hybrid", "prescriptions": [{"pattern": "carry"}, {"pattern": "core_running_support"}]},
    }
    week = build_unified_week(run_by_date, strength_by_date, _week_dates(monday))
    friday = next(s for s in week if s.date == monday + timedelta(days=4))
    assert friday.session_type == "strength"
    assert friday.note == ""
    assert friday.flagged is False


def test_find_adjacency_conflicts_detects_easy_run_as_non_key():
    monday = date(2026, 7, 6)
    from app.engines.calendar import UnifiedSession

    strength = UnifiedSession(date=monday, session_type="strength", name="Lower", patterns=["squat"])
    easy_run = UnifiedSession(date=monday + timedelta(days=1), session_type="run", name="Easy", role="easy")
    conflicts = find_adjacency_conflicts([strength, easy_run])
    assert conflicts == []
