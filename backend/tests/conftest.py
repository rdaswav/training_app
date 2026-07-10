import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module
from app.db import Base, get_db
from app import models  # noqa: F401 register models on Base.metadata


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(monkeypatch):
    """A real TestClient against the FastAPI app, wired to an isolated
    in-memory DB via dependency override. Scoped to `api/routes.py`'s
    Depends(get_db)-based JSON endpoints -- main.py's HTML page routes
    (today_view/plan_view/session_view) call SessionLocal() directly rather
    than through FastAPI's DI, so they're out of scope for this fixture."""
    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)
    monkeypatch.setattr(db_module, "engine", test_engine)
    monkeypatch.setattr(db_module, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.config.ENABLE_SCHEDULER", False)

    from app import main as main_module
    from app.main import app

    # main.py imported SessionLocal by value at module load time (`from app.db
    # import SessionLocal`), so patching app.db.SessionLocal above doesn't
    # affect main.py's own binding -- the startup event's exercise seeding
    # uses main.py's SessionLocal directly, not Depends(get_db).
    monkeypatch.setattr(main_module, "SessionLocal", TestSessionLocal)

    def override_get_db():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
