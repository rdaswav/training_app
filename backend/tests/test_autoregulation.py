from app.engines.autoregulation import (
    StrengthLogSet,
    evaluate_easy_or_long_run,
    evaluate_quality_session,
    evaluate_strength_log,
)
from app.engines.strength import prescribe


def test_easy_run_softens_on_hr_drift():
    result = evaluate_easy_or_long_run(390, actual_pace_sec_per_km=395, actual_hr=165, hr_ceiling=150)
    assert result.action == "soften"


def test_easy_run_holds_on_poor_wellness():
    result = evaluate_easy_or_long_run(390, actual_pace_sec_per_km=390, actual_hr=145, hr_ceiling=150, wellness_ok=False)
    assert result.action == "hold"


def test_easy_run_progresses_when_faster_at_lower_hr():
    result = evaluate_easy_or_long_run(390, actual_pace_sec_per_km=375, actual_hr=140, hr_ceiling=150)
    assert result.action == "progress"
    assert result.pace_adjustment_sec_per_km < 0


def test_quality_session_softens_on_missed_reps():
    result = evaluate_quality_session(330, actual_pace_sec_per_km=335, hit_reps=False)
    assert result.action == "soften"


def test_quality_session_progresses_when_comfortably_under_pace():
    result = evaluate_quality_session(330, actual_pace_sec_per_km=320, hit_reps=True)
    assert result.action == "progress"


def test_strength_log_back_off_on_missed_reps():
    prescription = prescribe("squat", "compound", local_week=2, mode="accumulate")
    logged = [StrengthLogSet(reps=2, weight_kg=80, rir_actual=0.5)]
    result = evaluate_strength_log(prescription, logged)
    assert result.action == "back_off"


def test_strength_log_progress_when_reps_hit_with_reserve():
    prescription = prescribe("squat", "compound", local_week=0, mode="accumulate")  # RIR target 3.0
    logged = [StrengthLogSet(reps=5, weight_kg=80, rir_actual=5.0)]
    result = evaluate_strength_log(prescription, logged)
    assert result.action == "progress"


def test_strength_log_hold_when_grinding_under_target_rir():
    prescription = prescribe("squat", "compound", local_week=0, mode="accumulate")  # RIR target 3.0
    logged = [StrengthLogSet(reps=5, weight_kg=80, rir_actual=0.5)]
    result = evaluate_strength_log(prescription, logged)
    assert result.action == "hold"


def test_strength_log_no_sets_logged():
    prescription = prescribe("squat", "compound", local_week=0, mode="accumulate")
    result = evaluate_strength_log(prescription, [])
    assert result.action == "hold"
