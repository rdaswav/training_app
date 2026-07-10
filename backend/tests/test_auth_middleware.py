"""HTTP Basic Auth is disabled by default (no AUTH_USERNAME/AUTH_PASSWORD
set) so the existing `client` fixture -- and every other test using it --
keeps working with zero setup. These tests explicitly toggle it on to
confirm the gate itself works."""


def test_no_auth_enforced_when_credentials_unset(client):
    resp = client.get("/api/athlete")
    assert resp.status_code == 200


def test_request_without_credentials_is_rejected_when_auth_enabled(client, monkeypatch):
    monkeypatch.setattr("app.config.AUTH_USERNAME", "coach")
    monkeypatch.setattr("app.config.AUTH_PASSWORD", "s3cret")

    resp = client.get("/api/athlete")
    assert resp.status_code == 401
    assert "Basic" in resp.headers["www-authenticate"]


def test_request_with_wrong_credentials_is_rejected(client, monkeypatch):
    monkeypatch.setattr("app.config.AUTH_USERNAME", "coach")
    monkeypatch.setattr("app.config.AUTH_PASSWORD", "s3cret")

    resp = client.get("/api/athlete", auth=("coach", "wrong"))
    assert resp.status_code == 401


def test_request_with_correct_credentials_is_allowed(client, monkeypatch):
    monkeypatch.setattr("app.config.AUTH_USERNAME", "coach")
    monkeypatch.setattr("app.config.AUTH_PASSWORD", "s3cret")

    resp = client.get("/api/athlete", auth=("coach", "s3cret"))
    assert resp.status_code == 200
