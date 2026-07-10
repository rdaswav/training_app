import logging
from datetime import date, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import get_or_create_athlete, router
from app.config import DAILY_JOB_HOUR, ENABLE_SCHEDULER
from app.db import SessionLocal, init_db
from app.engines import load_summary
from app.engines.running import week_start
from app.jobs.daily_autoregulation import run_daily_job
from app.models import CompletedSession, PlannedSession, Race, SessionType
from app.seed import seed_exercise_library

logger = logging.getLogger(__name__)

app = FastAPI(title="Training App")
app.include_router(router)

scheduler = BackgroundScheduler()


def _run_daily_job_with_own_session():
    db = SessionLocal()
    try:
        summary = run_daily_job(db)
        logger.info("daily autoregulation job ran: %s", summary)
    except Exception:
        logger.exception("daily autoregulation job failed")
    finally:
        db.close()

BASE_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def format_pace(sec_per_km: int | None) -> str:
    if not sec_per_km:
        return "-"
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


def format_pace_mmss(sec_per_km: int | None) -> str:
    if not sec_per_km:
        return ""
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}"


def format_duration(duration_min: float | None) -> str:
    if not duration_min:
        return "-"
    if duration_min < 1:
        return f"{round(duration_min * 60)}s"
    if duration_min == int(duration_min):
        return f"{int(duration_min)} min"
    return f"{duration_min:g} min"


templates.env.filters["pace"] = format_pace
templates.env.filters["pace_mmss"] = format_pace_mmss
templates.env.filters["duration"] = format_duration
templates.env.globals["timedelta"] = timedelta


@app.on_event("startup")
def on_startup():
    init_db()
    db = SessionLocal()
    try:
        seed_exercise_library(db)
    finally:
        db.close()

    if ENABLE_SCHEDULER and not scheduler.running:
        scheduler.add_job(
            _run_daily_job_with_own_session,
            "cron",
            hour=DAILY_JOB_HOUR,
            id="daily_autoregulation",
            replace_existing=True,
        )
        scheduler.start()


@app.on_event("shutdown")
def on_shutdown():
    if scheduler.running:
        scheduler.shutdown(wait=False)


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
            {"request": request, "sessions": sessions, "today": today, "race": race, "days_to_race": days_to_race, "active": "today"},
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

        run_rows = [
            {"week_start": wk, "distance_km": s.content.get("total_distance_km") or 0.0}
            for wk, sess_list in weeks.items()
            for s in sess_list
            if s.type == SessionType.RUN
        ]
        completed_strength = (
            db.query(CompletedSession)
            .join(PlannedSession, CompletedSession.planned_session_id == PlannedSession.id)
            .filter(
                PlannedSession.athlete_id == athlete.id,
                PlannedSession.type == SessionType.STRENGTH,
                PlannedSession.date >= start,
                PlannedSession.date <= end,
            )
            .all()
        )
        completed_rows = [
            {"week_start": week_start(c.date), "actual": c.actual} for c in completed_strength
        ]
        load_series = load_summary.build_weekly_load_series(
            week_starts=list(weeks.keys()),
            run_km_by_week=load_summary.sum_run_km_by_week(run_rows),
            tonnage_by_week=load_summary.sum_strength_tonnage_by_week(completed_rows),
            current_week_start=week_start(date.today()),
        )
        return templates.TemplateResponse(
            "plan.html",
            {
                "request": request,
                "race": race,
                "phase_segments": phase_segments,
                "weeks": sorted(weeks.items()),
                "load_series": load_series,
                "active": "plan",
            },
        )
    finally:
        db.close()


@app.get("/settings")
def settings_view(request: Request):
    db = SessionLocal()
    try:
        athlete = get_or_create_athlete(db)
        race = db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).first()
        macrocycle_start = race.macrocycle.start_date if race and race.macrocycle else None
        return templates.TemplateResponse(
            "settings.html",
            {"request": request, "athlete": athlete, "race": race, "macrocycle_start": macrocycle_start, "active": "settings"},
        )
    finally:
        db.close()


@app.get("/session/{session_id}")
def session_view(session_id: int, request: Request):
    db = SessionLocal()
    try:
        session = db.query(PlannedSession).filter(PlannedSession.id == session_id).first()
        if not session:
            raise HTTPException(404, "Session not found")
        return templates.TemplateResponse("session.html", {"request": request, "s": session, "active": None})
    finally:
        db.close()


@app.get("/strength-history")
def strength_history_view(request: Request):
    db = SessionLocal()
    try:
        athlete = get_or_create_athlete(db)
        completed = (
            db.query(CompletedSession)
            .join(PlannedSession, CompletedSession.planned_session_id == PlannedSession.id)
            .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.STRENGTH)
            .order_by(CompletedSession.date.desc())
            .limit(200)
            .all()
        )
        by_pattern: dict[str, list[dict]] = {}
        for c in completed:
            pattern = c.actual.get("pattern")
            if not pattern:
                continue
            exercise_name = next(
                (p.get("exercise_name") for p in c.planned_session.content.get("prescriptions", []) if p["pattern"] == pattern),
                None,
            )
            by_pattern.setdefault(pattern, []).append(
                {
                    "date": c.date,
                    "exercise_name": exercise_name,
                    "sets": c.actual.get("sets", []),
                    "feedback": c.feedback,
                    "next_instruction": c.next_instruction,
                }
            )
        return templates.TemplateResponse(
            "strength_history.html",
            {"request": request, "by_pattern": sorted(by_pattern.items()), "active": "history"},
        )
    finally:
        db.close()
