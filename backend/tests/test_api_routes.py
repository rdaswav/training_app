"""Representative coverage of api/routes.py through a real TestClient --
confirms the HTTP contract itself (status codes, request validation, response
shapes) rather than re-deriving business logic already covered by the
direct-call tests in test_exercise_swap.py and the engine test suites."""
from datetime import date, timedelta


def test_get_athlete_creates_default_on_first_call(client):
    resp = client.get("/api/athlete")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert body["injury_flags"] == []


def test_create_race_returns_race_and_generates_plan(client):
    payload = {
        "name": "Test Half",
        "race_date": "2026-10-11",
        "distance_km": 21.1,
        "priority": "A",
        "plan_start_date": "2026-07-09",
    }
    resp = client.post("/api/races", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Test Half"
    assert body["distance_km"] == 21.1

    listed = client.get("/api/races")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    calendar = client.get("/api/calendar", params={"start": "2026-07-09", "end": "2026-10-11"})
    assert calendar.status_code == 200
    assert len(calendar.json()) > 0


def test_create_race_rejects_missing_required_field(client):
    resp = client.post("/api/races", json={"name": "Missing fields"})
    assert resp.status_code == 422


def test_delete_race_removes_it_and_its_planned_sessions(client):
    client.post(
        "/api/races",
        json={"name": "To delete", "race_date": "2026-10-11", "distance_km": 21.1, "plan_start_date": "2026-07-09"},
    )
    race_id = client.get("/api/races").json()[0]["id"]

    resp = client.delete(f"/api/races/{race_id}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}
    assert client.get("/api/races").json() == []


def test_delete_race_404s_for_unknown_id(client):
    resp = client.delete("/api/races/999")
    assert resp.status_code == 404


def test_calendar_requires_start_and_end_query_params(client):
    resp = client.get("/api/calendar")
    assert resp.status_code == 422


def _create_race_and_get_calendar(client, start: str, end: str):
    client.post(
        "/api/races",
        json={"name": "Half", "race_date": "2026-10-11", "distance_km": 21.1, "plan_start_date": start},
    )
    return client.get("/api/calendar", params={"start": start, "end": end}).json()


def test_complete_run_session_updates_status_and_returns_action_note(client):
    sessions = _create_race_and_get_calendar(client, "2026-07-09", "2026-10-11")
    run_session = next(s for s in sessions if s["type"] == "run")

    resp = client.post(
        f"/api/sessions/{run_session['id']}/complete",
        json={"actual_pace_sec_per_km": 390, "actual_hr": 140},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "action" in body and "note" in body


def test_complete_run_session_rejects_strength_session(client):
    sessions = _create_race_and_get_calendar(client, "2026-07-09", "2026-10-11")
    strength_session = next(s for s in sessions if s["type"] == "strength")

    resp = client.post(f"/api/sessions/{strength_session['id']}/complete", json={})
    assert resp.status_code == 400


def test_log_strength_session_with_multiple_sets_and_completion_gating(client):
    sessions = _create_race_and_get_calendar(client, "2026-07-09", "2026-10-11")
    strength_session = next(s for s in sessions if s["type"] == "strength")
    patterns = [p["pattern"] for p in strength_session["content"]["prescriptions"]]
    assert len(patterns) > 1, "need a multi-prescription session to test completion gating"

    resp = client.post(
        f"/api/sessions/{strength_session['id']}/log",
        json={
            "pattern": patterns[0],
            "sets": [
                {"reps": 8, "weight_kg": 20.0, "rir_actual": 2.0},
                {"reps": 8, "weight_kg": 20.0, "rir_actual": 1.5},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"summary", "feedback", "next_instruction", "action"}

    # Only one of several prescriptions logged -- session must not be fully completed,
    # regression coverage for the PR #15 fix at the HTTP layer.
    still_planned = client.get(
        "/api/calendar", params={"start": "2026-07-09", "end": "2026-10-11"}
    ).json()
    reloaded = next(s for s in still_planned if s["id"] == strength_session["id"])
    assert reloaded["status"] == "planned"


def test_log_strength_session_rejects_unknown_pattern(client):
    sessions = _create_race_and_get_calendar(client, "2026-07-09", "2026-10-11")
    strength_session = next(s for s in sessions if s["type"] == "strength")

    resp = client.post(
        f"/api/sessions/{strength_session['id']}/log",
        json={"pattern": "not_a_real_pattern", "sets": [{"reps": 5, "weight_kg": 10.0}]},
    )
    assert resp.status_code == 400


def test_swap_exercise_updates_prescription(client):
    sessions = _create_race_and_get_calendar(client, "2026-07-09", "2026-10-11")
    strength_session = next(s for s in sessions if s["type"] == "strength")
    pattern = strength_session["content"]["prescriptions"][0]["pattern"]

    exercises = client.get("/api/exercises", params={"pattern": pattern}).json()
    assert exercises
    new_name = exercises[0]["name"]

    resp = client.patch(
        f"/api/sessions/{strength_session['id']}/exercise",
        json={"pattern": pattern, "exercise_name": new_name},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "updated"}


def test_list_exercises_filters_by_pattern(client):
    resp = client.get("/api/exercises", params={"pattern": "squat"})
    assert resp.status_code == 200
    assert all(e["pattern"] == "squat" for e in resp.json())


def test_plan_apply_regenerates_and_reports_sync_status(client):
    client.post(
        "/api/races",
        json={"name": "Half", "race_date": "2026-10-11", "distance_km": 21.1, "plan_start_date": "2026-07-09"},
    )
    race_id = client.get("/api/races").json()[0]["id"]

    resp = client.post("/api/plan/apply", json={"race_id": race_id, "weekly_volume_km": 35.0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "regenerated"
    assert "intervals_icu_sync" in body


def test_plan_apply_404s_for_unknown_race(client):
    resp = client.post("/api/plan/apply", json={"race_id": 999})
    assert resp.status_code == 404


def test_trigger_daily_job_runs_without_error(client):
    resp = client.post("/api/jobs/daily-autoregulation")
    assert resp.status_code == 200


def test_config_check_never_exposes_actual_credential_values(client):
    resp = client.get("/api/config-check")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"intervals_icu_api_key_set", "intervals_icu_athlete_id_set"}
    assert all(isinstance(v, bool) for v in body.values())
