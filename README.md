# Training App

A personal running + strength periodization app. Generates a race-driven half-marathon
(default) training block, unifies running and RP-style strength into one calendar, and
pushes structured run workouts to intervals.icu (and from there, the Fenix 8).

See `SPEC.md` for the full build specification this implements.

## Status

MVP-complete for the deterministic core (spec build sequence steps 2-4, 5-9):

- Data model + persistence (SQLite via SQLAlchemy)
- Running periodization engine (phases, volume ramp/down-weeks/taper, pace/HR targets)
- RP-style strength engine (MEV -> MAV -> MRV -> deload, RIR progression, race-proximity modulation)
- Exercise library + injury-flag substitution
- Unified calendar with the hard-lower/key-run adjacency guardrail (auto-shuffle + flag)
- Autoregulation (run pace/HR feedback loop, strength `NxNxN` logging -> progress/hold/back-off)
- FastAPI app: race creation, calendar, today, session logging, structured plan export/apply
- Web UI: phone today-view, desktop plan-view (phase timeline + weekly calendar)
- 42 passing pytest tests covering all four engines

**Not yet live**: the intervals.icu integration (`app/integrations/intervals_icu.py`) is
written against the documented API shape but has not been exercised against a real
account -- there's no API key in this environment. See "Confirm before relying on this"
below. Nothing here calls Garmin directly (by design -- that's intervals.icu's job).

## Running it

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --reload
```

Then visit `http://localhost:8000/` (today view) and `http://localhost:8000/plan`
(desktop planning view). On first run it seeds a default athlete profile and the
exercise library. Create a race to generate a plan:

```bash
curl -X POST http://localhost:8000/api/races \
  -H "Content-Type: application/json" \
  -d '{"name":"City Half","race_date":"2026-10-04","distance_km":21.1,"priority":"A"}'
```

### Tests

```bash
cd backend
source .venv/bin/activate
python -m pytest
```

### Configuration

Environment variables (see `app/config.py`):

- `DATABASE_URL` -- defaults to a local SQLite file.
- `INTERVALS_ICU_API_KEY`, `INTERVALS_ICU_ATHLETE_ID`, `INTERVALS_ICU_BASE_URL` -- needed
  only for the intervals.icu client; unset in this environment.

## Architecture

```
app/
  models.py            SQLAlchemy models: AthleteProfile, Race, Macrocycle, Phase,
                        Mesocycle, PlannedSession, CompletedSession, Exercise
  engines/
    running.py          Pure, DB-free periodization engine (race date + fitness -> weeks)
    strength.py          RP mesocycle skeleton + race-proximity modulation
    calendar.py           Unified calendar + adjacency guardrail
    autoregulation.py    Run and strength feedback loops (pure functions)
  integrations/
    intervals_icu.py    intervals.icu client (read activities/wellness, write planned
                         workouts) -- see the "unconfirmed wire format" docstring
  plan_service.py        Wires the engines to persistence for one race
  api/routes.py           FastAPI routes
  main.py                 App wiring + today/plan HTML views
  templates/, static/     Jinja2 + vanilla JS/CSS, no build step
```

The engines are deliberately pure/dataclass-based with no DB or HTTP dependency, so the
periodization rules are unit-testable in isolation (`backend/tests/`).

## A known tension in the fixed weekly template

The default template (spec section 7: Mon strength / Tue run / Wed strength / Thu run /
Fri strength / Sat long run / Sun rest) has *two* built-in hard-lower-before-key-run
adjacencies every week: Wed (Lower) before Thu's quality run, and Fri (Hybrid, includes
posterior-chain work) before Sat's long run. With only one rest day per week, the
guardrail can resolve one (it moves Lower to Sunday, freeing Wednesday as rest) but must
flag the other in place, since swapping it would just create a new conflict. This shows
up as a "Flagged: ..." note on the Friday session every week in the current plan. Worth
revisiting: either drop the Friday Hybrid day's compound posterior-chain work in favor of
lighter carries/unilateral-only, or accept the flag as informational and rely on RIR/load
to keep it light on the day before a long run.

## Confirm before relying on this (spec section 11)

These are the explicit external unknowns called out in the spec, not yet verified against
a live account:

1. **intervals.icu endpoints + planned-workout schema.** `app/integrations/intervals_icu.py`
   assumes Basic auth (`API_KEY` / api key) and an `/api/v1/athlete/{id}/events` calendar
   endpoint with a plain-text `description` step syntax. This needs a real spike (spec
   build step 1) before the first workout is trusted to land on the watch correctly.
2. **Whether a single Fenix 8 step can carry both a pace target and an HR ceiling**, or
   whether HR needs a separate alert. The client currently emits both on one line;
   fall back to a separate HR alert if the watch doesn't render it that way.
3. **VDOT/critical-pace source.** `AthleteFitness.race_pace_sec_per_km` in
   `engines/running.py` currently derives race pace as `threshold_pace + 12s/km`, a rough
   placeholder. Replace with either a proper VDOT model or intervals.icu's own derived
   pace zones once confirmed.

## Not yet built

Per the spec's build sequence, still outstanding beyond the deterministic core:

- The daily autoregulation job (pull yesterday's intervals.icu activities/wellness,
  run autoregulation, refresh the next 7-10 days) -- the autoregulation *logic* exists
  in `engines/autoregulation.py` and is wired to the manual `/api/sessions/{id}/complete`
  and `/log` endpoints, but nothing schedules it automatically yet.
- Writing generated run sessions to intervals.icu automatically (the client exists;
  nothing calls `upsert_planned_workout` from the plan-generation flow yet, pending the
  spike above).
- Hosting/always-on deployment.
