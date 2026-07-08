from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import get_or_create_athlete, router
from app.db import SessionLocal, init_db
from app.models import PlannedSession, Race
from app.seed import seed_exercise_library

app = FastAPI(title="Training App")
app.include_router(router)

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def format_pace(sec_per_km: int | None) -> str:
    if not sec_per_km:
        return "-"
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


templates.env.filters["pace"] = format_pace
templates.env.globals["timedelta"] = timedelta


@app.on_event("startup")
def on_startup():
    init_db()
    db = SessionLocal()
    try:
        seed_exercise_library(db)
    finally:
        db.close()


@app.get("/")
def today_view(request: Request):
    db = SessionLocal()
    try:
        athlete = get_or_create_athlete(db)
        today = date.today()
        sessions = (
            db.query(PlannedSession)
            .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date == today)
            .all()
        )
        race = db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).first()
        days_to_race = (race.race_date - today).days if race else None
        return templates.TemplateResponse(
            "today.html",
            {"request": request, "sessions": sessions, "today": today, "race": race, "days_to_race": days_to_race},
        )
    finally:
        db.close()


@app.get("/plan")
def plan_view(request: Request):
    db = SessionLocal()
    try:
        athlete = get_or_create_athlete(db)
        race = db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).first()
        phases = race.macrocycle.phases if race and race.macrocycle else []
        start = race.macrocycle.start_date if race and race.macrocycle else date.today()
        end = race.macrocycle.end_date if race and race.macrocycle else date.today() + timedelta(days=7)

        total_days = max((end - start).days + 1, 1)
        phase_colors = {
            "Base": "#6b7280", "Re-base": "#5b9dff", "Build 1": "#2f6fed",
            "Build 2": "#d9a441", "Taper": "#4caf7d",
        }
        phase_segments = [
            {
                "name": p.name,
                "focus": p.focus,
                "pct": round(((p.end_date - p.start_date).days + 1) / total_days * 100, 2),
                "color": phase_colors.get(p.name, "#888"),
            }
            for p in phases
        ]
        sessions = (
            db.query(PlannedSession)
            .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= start, PlannedSession.date <= end)
            .order_by(PlannedSession.date)
            .all()
        )
        weeks: dict[date, list[PlannedSession]] = {}
        for s in sessions:
            week_monday = s.date - timedelta(days=s.date.weekday())
            weeks.setdefault(week_monday, []).append(s)
        return templates.TemplateResponse(
            "plan.html",
            {"request": request, "race": race, "phase_segments": phase_segments, "weeks": sorted(weeks.items())},
        )
    finally:
        db.close()
