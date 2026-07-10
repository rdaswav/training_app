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
from app.engines import dashboard_summary, load_summary
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


def format_goal_time(goal_time_sec: int | None) -> str:
    if not goal_time_sec:
        return ""
    h, rem = divmod(int(goal_time_sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


templates.env.filters["pace"] = format_pace
templates.env.filters["pace_mmss"] = format_pace_mmss
templates.env.filters["duration"] = format_duration
templates.env.filters["goal_time"] = format_goal_time


def _attach_logged_patterns(db, sessions: list[PlannedSession]) -> None:
    """For each strength session, attach a `logged_patterns` set (which
    prescriptions already have a CompletedSession row) so the template can
    show per-prescription completion state -- a session's multiple
    prescriptions are logged independently, so session.status alone can't
    tell you which ones are done."""
    strength_ids = [s.id for s in sessions if s.type == SessionType.STRENGTH]
    if not strength_ids:
        for s in sessions:
            if s.type == SessionType.STRENGTH:
                s.logged_patterns = set()
        return
    completed = (
        db.query(CompletedSession).filter(CompletedSession.planned_session_id.in_(strength_ids)).all()
    )
    by_session: dict[int, set[str]] = {}
    for c in completed:
        by_session.setdefault(c.planned_session_id, set()).add(c.actual.get("pattern"))
    for s in sessions:
        if s.type == SessionType.STRENGTH:
            s.logged_patterns = by_session.get(s.id, set())
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
        _attach_logged_patterns(db, sessions)
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
                "start_date": p.start_date,
                "end_date": p.end_date,
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
        today = date.today()
        load_series = load_summary.build_weekly_load_series(
            week_starts=list(weeks.keys()),
            run_km_by_week=load_summary.sum_run_km_by_week(run_rows),
            tonnage_by_week=load_summary.sum_strength_tonnage_by_week(completed_rows),
            current_week_start=week_start(today),
        )

        current_phase = None
        week_idx = 0
        total_weeks_count = len(weeks)
        ticks = []
        now_pct = 0.0
        flags = []
        mesocycle_status = None
        days_to_race = None
        if race:
            days_to_race = (race.race_date - today).days
            current_phase = dashboard_summary.active_phase(phase_segments, today)
            week_idx = dashboard_summary.global_week_index(start, today)
            ticks = dashboard_summary.week_ticks(total_weeks_count, week_idx)
            now_pct = dashboard_summary.timeline_pct(start, end, today)
            all_races = db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).all()
            race_dicts = [{"name": r.name, "race_date": r.race_date, "priority": r.priority.value} for r in all_races]
            flags = dashboard_summary.race_flags(race_dicts, start, end)
            mesocycle_status = dashboard_summary.strength_mesocycle_status(
                week_idx, current_phase["name"] if current_phase else "Re-base"
            )

        volume_delta_pct = None
        current_week_load = next((pt for pt in load_series if pt.week_start == week_start(today)), None)
        prior_week_load = next((pt for pt in load_series if pt.week_start == week_start(today) - timedelta(days=7)), None)
        if current_week_load and prior_week_load and prior_week_load.run_km:
            volume_delta_pct = round((current_week_load.run_km - prior_week_load.run_km) / prior_week_load.run_km * 100)

        return templates.TemplateResponse(
            "plan.html",
            {
                "request": request,
                "race": race,
                "phase_segments": phase_segments,
                "weeks": sorted(weeks.items()),
                "load_series": load_series,
                "volume_delta_pct": volume_delta_pct,
                "current_phase": current_phase,
                "week_idx": week_idx,
                "total_weeks_count": total_weeks_count,
                "ticks": ticks,
                "now_pct": now_pct,
                "flags": flags,
                "mesocycle_status": mesocycle_status,
                "days_to_race": days_to_race,
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
        _attach_logged_patterns(db, [session])
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
