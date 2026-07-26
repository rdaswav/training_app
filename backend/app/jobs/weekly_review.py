"""Weekly coach-review job: generate last week's review for every athlete.

Runs on the same in-process APScheduler as the daily autoregulation job (see
main.py). This is the part that fixes the actual failure mode of asking a chat
for a review -- the weeks you most need one are the weeks you won't request it,
and a cron doesn't care whether you're motivated.

Per-athlete failures are contained the same way the daily job contains them: one
athlete's error can't abort the rest, and the failure is durable on that
athlete's CoachReview row rather than only in a log line.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.coach_service import generate_weekly_review, last_complete_week
from app.models import AthleteProfile

logger = logging.getLogger(__name__)


def run_weekly_review_job(db: Session, today: date | None = None) -> list[dict]:
    week_start = last_complete_week(today)
    summaries = []
    for athlete in db.query(AthleteProfile).all():
        try:
            review = generate_weekly_review(db, athlete, week_start=week_start)
            summaries.append(
                {
                    "athlete_id": athlete.id,
                    "week_start": week_start.isoformat(),
                    "generated": bool(review.markdown),
                    "error": review.error,
                }
            )
        except Exception as exc:  # noqa: BLE001 -- one athlete must not abort the rest
            db.rollback()
            logger.exception("weekly coach review failed for athlete %s", athlete.id)
            summaries.append(
                {
                    "athlete_id": athlete.id,
                    "week_start": week_start.isoformat(),
                    "generated": False,
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                }
            )
    return summaries
