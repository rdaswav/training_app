"""intervals.icu API client.

*** UNCONFIRMED WIRE FORMAT -- spike this first (spec section 10, step 1) ***

This client is written against the publicly documented shape of the
intervals.icu API as of this build (HTTP Basic auth with username "API_KEY"
and the API key as the password; JSON over /api/v1; calendar entries are
"events" scoped to an athlete). It has NOT been exercised against a live
account in this environment (no credentials configured here) -- before
relying on it:
  1. Confirm auth works (GET /api/v1/athlete/{id}) with a real API key.
  2. Confirm the planned-workout "event" schema below (category/type/
     description fields) actually renders a structured Fenix 8 workout,
     not just a calendar note.
  3. Confirm whether a single step can carry both a pace target and an HR
     ceiling, or whether HR needs a separate alert (spec section 11).

The structured-workout `description` uses intervals.icu's plain-text step
syntax (one step per line, "- <duration> <target>"). This is the documented
convention but must be checked against a real render on the watch.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from app.config import INTERVALS_ICU_API_KEY, INTERVALS_ICU_ATHLETE_ID, INTERVALS_ICU_BASE_URL
from app.engines.running import RunSessionPlan, RunStep


def _format_pace(sec_per_km: int) -> str:
    m, s = divmod(sec_per_km, 60)
    return f"{m}:{s:02d}/km"


def step_to_line(step: RunStep) -> str:
    parts = []
    if step.distance_km:
        parts.append(f"{step.distance_km}km")
    elif step.duration_min:
        parts.append(f"{step.duration_min}m")
    if step.target_pace_sec_per_km:
        parts.append(f"Pace {_format_pace(step.target_pace_sec_per_km)}")
    if step.hr_ceiling:
        parts.append(f"HR <= {step.hr_ceiling}")
    return f"- {step.label}: " + ", ".join(parts) if parts else f"- {step.label}"


def session_to_description(session: RunSessionPlan) -> str:
    return "\n".join(step_to_line(step) for step in session.steps)


@dataclass
class IntervalsIcuClient:
    api_key: str = INTERVALS_ICU_API_KEY
    athlete_id: str = INTERVALS_ICU_ATHLETE_ID
    base_url: str = INTERVALS_ICU_BASE_URL
    transport: httpx.BaseTransport | None = None  # inject a MockTransport in tests

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            auth=("API_KEY", self.api_key),
            transport=self.transport,
            timeout=15.0,
        )

    def get_activities(self, oldest: date, newest: date) -> list[dict]:
        with self._client() as client:
            resp = client.get(
                f"/athlete/{self.athlete_id}/activities",
                params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
            )
            resp.raise_for_status()
            return resp.json()

    def get_wellness(self, oldest: date, newest: date) -> list[dict]:
        with self._client() as client:
            resp = client.get(
                f"/athlete/{self.athlete_id}/wellness",
                params={"oldest": oldest.isoformat(), "newest": newest.isoformat()},
            )
            resp.raise_for_status()
            return resp.json()

    def upsert_planned_workout(self, session: RunSessionPlan, existing_event_id: str | None = None) -> dict:
        """Write (or update) a structured run workout onto the intervals.icu
        calendar so it syncs to the Fenix the morning of the session."""
        payload = {
            "category": "WORKOUT",
            "type": "Run",
            "start_date_local": f"{session.date.isoformat()}T00:00:00",
            "name": session.name,
            "description": session_to_description(session),
        }
        with self._client() as client:
            if existing_event_id:
                resp = client.put(f"/athlete/{self.athlete_id}/events/{existing_event_id}", json=payload)
            else:
                resp = client.post(f"/athlete/{self.athlete_id}/events", json=payload)
            resp.raise_for_status()
            return resp.json()

    def delete_planned_workout(self, event_id: str) -> None:
        with self._client() as client:
            resp = client.delete(f"/athlete/{self.athlete_id}/events/{event_id}")
            resp.raise_for_status()
