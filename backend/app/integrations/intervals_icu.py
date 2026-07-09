"""intervals.icu API client.

Wire format confirmed 2026-07-09 against a live account (spec section 10,
step 1 -- see README "Confirm before relying on this" for the full writeup):

- Auth: HTTP Basic with username "API_KEY", the API key as the password.
  Confirmed via GET /api/v1/athlete/{id}.
- Activities/wellness field names (`start_date_local`, `distance`,
  `moving_time`, `average_heartrate`; wellness `id` as the ISO date,
  `readiness`/`sleepScore`) all matched what `jobs/daily_autoregulation.py`
  already assumed -- no changes needed there.
- Planned workouts: POST/PUT /api/v1/athlete/{id}/events with
  category="WORKOUT". The `description` field is parsed into a structured
  `workout_doc` -- but NOT with the syntax originally guessed here. Confirmed
  token syntax, one dashed line per step, tokens space-separated (no commas,
  no free-text label prefix -- a label prefix silently prevented the
  distance/duration token itself from being parsed as a target in earlier
  testing):
    - distance: "<km>km" (decimals fine, e.g. "4.8km")
    - duration: "<min>m"
    - pace target: "<mm:ss>/km Pace" (value THEN the literal word "Pace" --
      the reverse order silently fails to parse)
    - HR target: "<pct>% HR" -- percent only. Raw bpm ("150bpm HR",
      "140-150bpm HR") was tested and silently ignored; there is no absolute-
      bpm syntax. A zone form ("Z2 HR") also works if the athlete has HR
      zones configured on intervals.icu, but this app only models an
      absolute bpm ceiling, so bpm is converted to %max_hr instead (see
      `session_to_description`'s `max_hr` param). NOT independently
      confirmed whether intervals.icu's "%HR" base is max HR or LTHR --
      spot-check a generated event's target against the athlete's own HR
      zone chart once before trusting the exact percentage.
    - Confirmed a single step CAN carry both a pace AND an HR target at once
      (e.g. "8km 6:30/km Pace 75% HR" parsed both fields) -- this resolves
      the previously-open spec section 11 question.
  Composite multi-part steps (e.g. "6 x 20s strides w/ 60s float") are now
  decomposed into a repeat-block form: a standalone count line ("Nx")
  followed by nested dashed work/recovery lines (see `repeat_step_to_lines`).
  This nested-block syntax itself is UNCONFIRMED -- unlike every token
  documented above, it has NEVER been tested against the live account, only
  based on public docs. `REPEAT_BLOCK_SYNTAX_CONFIRMED` stays False until a
  follow-up live spike (post a repeat-block workout, inspect the parsed
  structure) confirms or corrects it, the same way the pace/HR-token guesses
  above were corrected on 2026-07-09.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import httpx

from app.config import INTERVALS_ICU_API_KEY, INTERVALS_ICU_ATHLETE_ID, INTERVALS_ICU_BASE_URL
from app.engines.running import RunRepeatStep, RunSessionPlan, RunStep

REPEAT_BLOCK_SYNTAX_CONFIRMED = False  # flip to True (and update this module's docstring) once spiked live


def _format_pace(sec_per_km: int) -> str:
    m, s = divmod(sec_per_km, 60)
    return f"{m}:{s:02d}/km"


def step_to_line(step: RunStep, max_hr: int | None = None) -> str:
    tokens = []
    if step.distance_km:
        tokens.append(f"{step.distance_km}km")
    elif step.duration_min:
        tokens.append(f"{step.duration_min}m")
    if step.target_pace_sec_per_km:
        tokens.append(f"{_format_pace(step.target_pace_sec_per_km)} Pace")
    if step.hr_ceiling and max_hr:
        pct = round(step.hr_ceiling / max_hr * 100)
        tokens.append(f"{pct}% HR")
    return f"- {' '.join(tokens)}" if tokens else f"- {step.label}"


def repeat_step_to_lines(step: RunRepeatStep, max_hr: int | None = None) -> list[str]:
    """UNCONFIRMED wire format -- see module docstring. A standalone count
    line ("Nx") followed by the work leg's dashed line, then (if present) the
    recovery leg's dashed line."""
    lines = [f"{step.repeat_count}x", step_to_line(step.work, max_hr)]
    if step.recovery is not None:
        lines.append(step_to_line(step.recovery, max_hr))
    return lines


def session_to_description(session: RunSessionPlan, max_hr: int | None = None) -> str:
    """`max_hr` is required to express `hr_ceiling` (an absolute bpm value in
    this app's data model) as the %HR token intervals.icu's parser actually
    recognizes -- without it, HR targets are silently omitted rather than
    guessed at."""
    lines: list[str] = []
    for step in session.steps:
        if isinstance(step, RunRepeatStep):
            lines.extend(repeat_step_to_lines(step, max_hr))
        else:
            lines.append(step_to_line(step, max_hr))
    return "\n".join(lines)


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

    def upsert_planned_workout(
        self, session: RunSessionPlan, existing_event_id: str | None = None, max_hr: int | None = None
    ) -> dict:
        """Write (or update) a structured run workout onto the intervals.icu
        calendar so it syncs to the Fenix the morning of the session. Pass
        `max_hr` (AthleteProfile.max_hr) so HR-ceiling steps convert to the
        %HR token intervals.icu's parser recognizes -- see module docstring."""
        payload = {
            "category": "WORKOUT",
            "type": "Run",
            "start_date_local": f"{session.date.isoformat()}T00:00:00",
            "name": session.name,
            "description": session_to_description(session, max_hr),
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
