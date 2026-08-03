import html
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.api.routes import get_or_create_athlete, router
from app.auth_middleware import BasicAuthMiddleware
from app.config import (
    ATHLETE_TIMEZONE,
    DAILY_JOB_HOUR,
    DEFAULT_WEEK_TEMPLATE,
    ENABLE_SCHEDULER,
    PHYSIOLOGY_REVIEW_INTERVAL_DAYS,
    WEEKLY_REVIEW_DAY,
    WEEKLY_REVIEW_HOUR,
)
from app.db import SessionLocal, init_db
from app.engines import coaching_copy, dashboard_summary, load_summary
from app.engines import strength as strength_engine
from app.engines import vdot as vdot_engine
from app.engines.running import week_start
from app.integrations.anthropic_coach import coach_configured
from app.jobs.daily_autoregulation import MAX_PACE_DRIFT_SEC_PER_KM, run_daily_job
from app.jobs.weekly_review import run_weekly_review_job
from app.models import CoachReview, CompletedSession, PlannedSession, Race, SessionType
from app.plan_service import fitness_from_athlete
from app.seed import seed_exercise_library
from app.timeutil import local_today

PHASE_COLORS = {
    "Base": "#6b7280", "Re-base": "#5b9dff", "Build 1": "#2f6fed",
    "Build 2": "#d9a441", "Taper": "#4caf7d",
}

logger = logging.getLogger(__name__)

app = FastAPI(title="Training App")
app.add_middleware(BasicAuthMiddleware)
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


def _run_weekly_review_with_own_session():
    db = SessionLocal()
    try:
        summary = run_weekly_review_job(db)
        logger.info("weekly coach review job ran: %s", summary)
    except Exception:
        logger.exception("weekly coach review job failed")
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


_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _markdown_inline(text: str) -> str:
    text = _MD_BOLD.sub(r"<strong>\1</strong>", text)
    text = _MD_ITALIC.sub(r"<em>\1</em>", text)
    return _MD_CODE.sub(r"<code>\1</code>", text)


def format_markdown(text: str | None) -> Markup:
    """Minimal markdown -> HTML for the LLM-written coach reviews: headings,
    bold/italic/code, bullet lists, paragraphs. Deliberately hand-rolled rather
    than pulling in a markdown dependency for one page.

    The source is escaped before any tag is emitted. This only ever renders our
    own model's output, but rendering model output as unescaped HTML is exactly
    how that becomes an injection bug the first time a review quotes something."""
    if not text:
        return Markup("")
    out: list[str] = []
    list_open = False
    for raw_line in html.escape(text).split("\n"):
        line = raw_line.strip()
        if not line:
            if list_open:
                out.append("</ul>")
                list_open = False
            continue
        if line.startswith(("- ", "* ")):
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{_markdown_inline(line[2:])}</li>")
            continue
        if list_open:
            out.append("</ul>")
            list_open = False
        heading = _MD_HEADING.match(line)
        if heading:
            # Shift down one level so the page's own <h1> stays the only h1.
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{_markdown_inline(heading.group(2))}</h{level}>")
            continue
        out.append(f"<p>{_markdown_inline(line)}</p>")
    if list_open:
        out.append("</ul>")
    return Markup("".join(out))


templates.env.filters["pace"] = format_pace
templates.env.filters["pace_mmss"] = format_pace_mmss
templates.env.filters["duration"] = format_duration
templates.env.filters["goal_time"] = format_goal_time
templates.env.filters["markdown"] = format_markdown


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


# RUN CompletedSession.next_instruction stores the terse autoregulation action
# token (progress/hold/soften) -- these translate it to the same copy/direction
# app.js's ephemeral coach card used, so a reload shows the identical thing.
RUN_ACTION_LABELS = {
    "progress": "Progress pace next session",
    "hold": "Hold your current paces",
    "soften": "Ease off next time",
}
ACTION_DIRECTION = {"progress": "up", "hold": "steady", "soften": "down", "back_off": "down"}


def _attach_completed_feedback(db, sessions: list[PlannedSession]) -> None:
    """Persisted Did/Read/Next coach feedback for completed sessions, so it
    survives a page reload instead of only ever existing in the DOM right
    after logging -- previously a completed run showed a bare "Completed"
    and a logged strength pattern a bare "Logged" badge, even though the
    CompletedSession.feedback/next_instruction that built the original coach
    card was sitting in the DB the whole time, just never read back.

    Attaches `completed_feedback` (dict or None) on RUN sessions and
    `completed_feedback_by_pattern` (dict, pattern -> dict) on STRENGTH ones.
    Strength's `next_instruction` is already descriptive text (unlike run's
    terse action token), and its `action` field for the direction arrow was
    never persisted -- so a reloaded strength coach card shows Next without
    the up/down arrow rather than guessing a direction from text."""
    session_ids = [s.id for s in sessions]
    if not session_ids:
        return
    completed = (
        db.query(CompletedSession)
        .filter(CompletedSession.planned_session_id.in_(session_ids))
        .order_by(CompletedSession.id)
        .all()
    )
    by_session: dict[int, list[CompletedSession]] = {}
    for c in completed:
        by_session.setdefault(c.planned_session_id, []).append(c)

    for s in sessions:
        rows = by_session.get(s.id, [])
        if s.type == SessionType.RUN:
            s.completed_feedback = None
            if rows:
                c = rows[-1]
                actual = c.actual or {}
                did_parts = []
                pace = actual.get("actual_pace_sec_per_km")
                if pace:
                    did_parts.append(format_pace(pace))
                if actual.get("actual_hr"):
                    did_parts.append(f"{actual['actual_hr']} bpm avg")
                s.completed_feedback = {
                    "did_text": " · ".join(did_parts) if did_parts else "Logged, no pace/HR entered",
                    "feedback": c.feedback,
                    "next_label": RUN_ACTION_LABELS.get(c.next_instruction, c.next_instruction),
                    "dir": ACTION_DIRECTION.get(c.next_instruction, "steady"),
                }
        elif s.type == SessionType.STRENGTH:
            by_pattern = {}
            for c in rows:
                pattern = (c.actual or {}).get("pattern")
                if not pattern:
                    continue
                sets = (c.actual or {}).get("sets", [])

                def _set_text(st):
                    if st.get("duration_sec") is not None:
                        return f"{st['duration_sec']}s"
                    return f"{st['reps']}×{st['weight_kg']}kg"

                did_text = ", ".join(_set_text(st) for st in sets) if sets else "Logged"
                by_pattern[pattern] = {"did_text": did_text, "feedback": c.feedback, "next_label": c.next_instruction}
            s.completed_feedback_by_pattern = by_pattern


def _recent_strength_completed_rows(db, athlete) -> list[dict]:
    """Most recent 200 logged strength sets, most-recent-first, shaped for
    strength_engine.latest_e1rm_by_pattern -- shared by the suggested-load
    attachment below and the /about page's live e1RM numbers."""
    completed = (
        db.query(CompletedSession)
        .join(PlannedSession, CompletedSession.planned_session_id == PlannedSession.id)
        .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.type == SessionType.STRENGTH)
        .order_by(CompletedSession.date.desc())
        .limit(200)
        .all()
    )
    return [{"pattern": c.actual.get("pattern"), "sets": c.actual.get("sets", [])} for c in completed]


def _attach_suggested_loads(db, athlete, sessions: list[PlannedSession]) -> None:
    """For each strength session, attach a `suggested_loads` dict (pattern ->
    kg) computed from the athlete's most recent logged session for that
    movement pattern (see #28) -- a concrete weight to load before starting,
    not just a progress/hold/back-off label after the fact."""
    strength_sessions = [s for s in sessions if s.type == SessionType.STRENGTH]
    if not strength_sessions:
        return
    completed_rows = _recent_strength_completed_rows(db, athlete)
    e1rm_by_pattern = strength_engine.latest_e1rm_by_pattern(completed_rows)
    for s in strength_sessions:
        suggested = {}
        for p in s.content.get("prescriptions", []):
            e1rm = e1rm_by_pattern.get(p["pattern"])
            if e1rm:
                suggested[p["pattern"]] = strength_engine.prescribe_next_load(e1rm, p["reps"], p["rir"])
        s.suggested_loads = suggested


def _attach_last_logged_sets(db, athlete, sessions: list[PlannedSession]) -> None:
    """For each strength session, attach a `last_logged` dict (pattern ->
    {set_count, reps, weight_kg}) -- the athlete's most recent *actual* logged
    sets for that pattern, distinct from suggested_loads' derived e1RM target.
    This is the "Last: 3x5 @ 70" inline reference on the gym-mode sticky bar
    (UI_AUDIT.md suggestion #4), not a training recommendation."""
    strength_sessions = [s for s in sessions if s.type == SessionType.STRENGTH]
    if not strength_sessions:
        return
    completed_rows = _recent_strength_completed_rows(db, athlete)
    last_by_pattern: dict[str, dict] = {}
    for row in completed_rows:
        pattern = row.get("pattern")
        sets = row.get("sets") or []
        if not pattern or pattern in last_by_pattern or not sets:
            continue  # rows are latest-first -- first hit per pattern is the most recent
        # A bodyweight_timed set has no reps/weight_kg (see movement_type on
        # Exercise) -- skip it here too (same reasoning as
        # strength_engine.latest_e1rm_by_pattern) so a pattern shared with a
        # weighted exercise (e.g. "core") doesn't surface "Last: None x Nonekg"
        # on the sticky bar; falls through to the next, older, usable row.
        if sets[0].get("weight_kg") is None or sets[0].get("reps") is None:
            continue
        last_by_pattern[pattern] = {
            "set_count": len(sets),
            "reps": sets[0]["reps"],
            "weight_kg": sets[0]["weight_kg"],
        }
    for s in strength_sessions:
        s.last_logged = last_by_pattern


def _weeks_by_monday(sessions: list[PlannedSession]) -> dict[date, list[PlannedSession]]:
    weeks: dict[date, list[PlannedSession]] = {}
    for s in sessions:
        week_monday = s.date - timedelta(days=s.date.weekday())
        weeks.setdefault(week_monday, []).append(s)
    return weeks


def _load_series_for_race(
    db, athlete, start: date, end: date, weeks: dict[date, list[PlannedSession]]
) -> list[load_summary.WeeklyLoadPoint]:
    """Weekly run-km/strength-tonnage series across a race's full macrocycle
    (start..end) -- shared by /plan's load dashboard and /about's
    periodization chart, which both cover the same full-plan window. `weeks`
    is the caller's already-queried PlannedSession-by-week-Monday map (see
    _weeks_by_monday), so this doesn't re-query sessions the caller already has."""
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
    return load_summary.build_weekly_load_series(
        week_starts=list(weeks.keys()),
        run_km_by_week=load_summary.sum_run_km_by_week(run_rows),
        tonnage_by_week=load_summary.sum_strength_tonnage_by_week(completed_rows),
        current_week_start=week_start(local_today()),
    )


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
        # timezone=ATHLETE_TIMEZONE -- APScheduler's cron trigger otherwise fires
        # in the server's local time (UTC in a container regardless of where
        # it's hosted), so DAILY_JOB_HOUR=6/WEEKLY_REVIEW_HOUR=18 would fire at
        # 6am/6pm UTC, not the athlete's actual local morning/evening.
        scheduler.add_job(
            _run_daily_job_with_own_session,
            "cron",
            hour=DAILY_JOB_HOUR,
            timezone=ATHLETE_TIMEZONE,
            id="daily_autoregulation",
            replace_existing=True,
        )
        scheduler.add_job(
            _run_weekly_review_with_own_session,
            "cron",
            day_of_week=WEEKLY_REVIEW_DAY,
            hour=WEEKLY_REVIEW_HOUR,
            timezone=ATHLETE_TIMEZONE,
            id="weekly_coach_review",
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
        today = local_today()
        sessions = (
            db.query(PlannedSession)
            .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date == today)
            .all()
        )
        _attach_logged_patterns(db, sessions)
        _attach_suggested_loads(db, athlete, sessions)
        _attach_last_logged_sets(db, athlete, sessions)
        _attach_completed_feedback(db, sessions)
        race = db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).first()
        days_to_race = (race.race_date - today).days if race else None

        current_phase_name = None
        mesocycle_mode = None
        if race and race.macrocycle:
            phases = [{"name": p.name, "start_date": p.start_date, "end_date": p.end_date} for p in race.macrocycle.phases]
            current_phase = dashboard_summary.active_phase(phases, today)
            current_phase_name = current_phase["name"] if current_phase else None
            week_idx = dashboard_summary.global_week_index(race.macrocycle.start_date, today)
            mesocycle_status = dashboard_summary.strength_mesocycle_status(
                week_idx, current_phase_name or "Re-base", race.macrocycle.mesocycle_start_week or 0
            )
            mesocycle_mode = mesocycle_status.mode
        cues = {s.id: coaching_copy.session_cue(s.type, current_phase_name, mesocycle_mode) for s in sessions}

        return templates.TemplateResponse(
            "today.html",
            {
                "request": request,
                "sessions": sessions,
                "today": today,
                "race": race,
                "days_to_race": days_to_race,
                "cues": cues,
                "active": "today",
            },
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
        start = race.macrocycle.start_date if race and race.macrocycle else local_today()
        end = race.macrocycle.end_date if race and race.macrocycle else local_today() + timedelta(days=7)

        total_days = max((end - start).days + 1, 1)
        phase_segments = [
            {
                "name": p.name,
                "focus": p.focus,
                "pct": round(((p.end_date - p.start_date).days + 1) / total_days * 100, 2),
                "color": PHASE_COLORS.get(p.name, "#888"),
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
        weeks = _weeks_by_monday(sessions)
        today = local_today()
        load_series = _load_series_for_race(db, athlete, start, end, weeks)

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
            mesocycle_start_week = (race.macrocycle.mesocycle_start_week or 0) if race.macrocycle else 0
            mesocycle_status = dashboard_summary.strength_mesocycle_status(
                week_idx, current_phase["name"] if current_phase else "Re-base", mesocycle_start_week
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


@app.get("/about")
def about_view(request: Request):
    db = SessionLocal()
    try:
        athlete = get_or_create_athlete(db)
        race = db.query(Race).filter(Race.athlete_id == athlete.id).order_by(Race.race_date).first()
        today = local_today()

        context = {
            "request": request,
            "race": race,
            "current_phase_name": None,
            "weeks_out": None,
            "mesocycle_status": None,
            "phase_copy": coaching_copy.PHASE_COPY,
            "phase_order": coaching_copy.PHASE_ORDER,
            "mode_copy": coaching_copy.MODE_COPY,
            "phase_colors": PHASE_COLORS,
            "phase_segments": [],
            "load_series": [],
            "week_grid": [],
            "total_weeks": 0,
            "week_idx": None,
            "vdot": None,
            "race_pace_sec_per_km": None,
            "landmarks": strength_engine.DEFAULT_LANDMARKS,
            "e1rm_by_pattern": {},
            "athlete": athlete,
            "max_pace_drift": MAX_PACE_DRIFT_SEC_PER_KM,
            "active": "about",
        }

        if race and race.macrocycle:
            start, end = race.macrocycle.start_date, race.macrocycle.end_date
            phases = [
                {"name": p.name, "start_date": p.start_date, "end_date": p.end_date, "focus": p.focus}
                for p in race.macrocycle.phases
            ]
            current_phase = dashboard_summary.active_phase(phases, today)
            current_phase_name = current_phase["name"] if current_phase else None
            total_days = max((end - start).days + 1, 1)
            phase_segments = [
                {
                    "name": p["name"],
                    "focus": p["focus"],
                    "pct": round(((p["end_date"] - p["start_date"]).days + 1) / total_days * 100, 2),
                    "color": PHASE_COLORS.get(p["name"], "#888"),
                    "start_date": p["start_date"],
                    "end_date": p["end_date"],
                }
                for p in phases
            ]
            weeks_out = (race.race_date - today).days // 7
            week_idx = dashboard_summary.global_week_index(start, today)
            mesocycle_start_week = race.macrocycle.mesocycle_start_week or 0
            mesocycle_status = dashboard_summary.strength_mesocycle_status(
                week_idx, current_phase_name or "Re-base", mesocycle_start_week
            )

            sessions = (
                db.query(PlannedSession)
                .filter(PlannedSession.athlete_id == athlete.id, PlannedSession.date >= start, PlannedSession.date <= end)
                .order_by(PlannedSession.date)
                .all()
            )
            weeks = _weeks_by_monday(sessions)
            load_series = _load_series_for_race(db, athlete, start, end, weeks)

            total_weeks = (end - start).days // 7 + 1
            taper_phase = next((p for p in phases if p["name"] == "Taper"), None)
            taper_start_week = (
                dashboard_summary.global_week_index(start, taper_phase["start_date"]) if taper_phase else total_weeks
            )
            week_grid = dashboard_summary.macrocycle_week_grid(
                phases, start, total_weeks, taper_start_week, mesocycle_start_week
            )

            fitness = fitness_from_athlete(athlete, race_distance_km=race.distance_km, goal_time_sec=race.goal_time_sec)
            vdot = vdot_engine.vdot_from_threshold_pace(athlete.threshold_pace_sec_per_km)

            completed_rows = _recent_strength_completed_rows(db, athlete)
            e1rm_by_pattern = strength_engine.latest_e1rm_by_pattern(completed_rows)

            context.update(
                {
                    "current_phase_name": current_phase_name,
                    "phase_segments": phase_segments,
                    "weeks_out": weeks_out,
                    "mesocycle_status": mesocycle_status,
                    "load_series": load_series,
                    "week_grid": week_grid,
                    "total_weeks": total_weeks,
                    "week_idx": week_idx,
                    "vdot": round(vdot, 1),
                    "race_pace_sec_per_km": fitness.race_pace_sec_per_km,
                    "e1rm_by_pattern": e1rm_by_pattern,
                }
            )

        return templates.TemplateResponse("about.html", context)
    finally:
        db.close()


@app.get("/reviews")
def reviews_view(request: Request):
    db = SessionLocal()
    try:
        athlete = get_or_create_athlete(db)
        reviews = (
            db.query(CoachReview)
            .filter(CoachReview.athlete_id == athlete.id)
            .order_by(CoachReview.week_start.desc())
            .limit(12)
            .all()
        )
        return templates.TemplateResponse(
            "reviews.html",
            {
                "request": request,
                "reviews": reviews,
                "coach_configured": coach_configured(),
                "timedelta": timedelta,
                "active": "reviews",
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
        week_template = athlete.week_template or {str(k): v for k, v in DEFAULT_WEEK_TEMPLATE.items()}
        if athlete.physiology_reviewed_at is None:
            physiology_stale_days = None  # never confirmed -- always stale
        else:
            physiology_stale_days = (datetime.utcnow() - athlete.physiology_reviewed_at).days
        physiology_stale = physiology_stale_days is None or physiology_stale_days >= PHYSIOLOGY_REVIEW_INTERVAL_DAYS
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "athlete": athlete,
                "race": race,
                "macrocycle_start": macrocycle_start,
                "week_template": week_template,
                "active": "settings",
                "physiology_stale": physiology_stale,
                "physiology_stale_days": physiology_stale_days,
            },
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
        athlete = get_or_create_athlete(db)
        _attach_logged_patterns(db, [session])
        _attach_suggested_loads(db, athlete, [session])
        _attach_last_logged_sets(db, athlete, [session])
        _attach_completed_feedback(db, [session])
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
