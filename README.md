# Training App

A personal running + strength periodization app. Generates a race-driven half-marathon
(default) training block, unifies running and RP-style strength into one calendar, and
pushes structured run workouts to intervals.icu (and from there, the Fenix 8).

See `SPEC.md` for the full build specification this implements.

## Status

All ten build-sequence steps from `SPEC.md` section 10 are implemented:

- Data model + persistence (SQLite via SQLAlchemy)
- Running periodization engine (phases, volume ramp/down-weeks/taper, pace/HR targets)
- RP-style strength engine (MEV -> MAV -> MRV -> deload, RIR progression, race-proximity modulation)
- Exercise library + injury-flag substitution
- Unified calendar with the hard-lower/key-run adjacency guardrail (auto-shuffle + flag)
- Autoregulation (run pace/HR feedback loop, strength `NxNxN` logging -> progress/hold/back-off)
- intervals.icu client wired into the plan flow: creating/regenerating a plan pushes the
  next 10 days of run sessions via `upsert_planned_workout` (skipped as a safe no-op if
  credentials aren't configured)
- Daily autoregulation job (`app/jobs/daily_autoregulation.py`): pulls yesterday's
  activities/wellness, marks sessions completed (with pace/HR feedback) or missed,
  regenerates the plan from today forward, and re-syncs to intervals.icu. Runs on an
  in-process APScheduler cron (default 06:00 local) and is also reachable manually via
  `POST /api/jobs/daily-autoregulation`
- FastAPI app: race creation, calendar, today, session logging, structured plan export/apply
- Web UI: phone today-view, desktop plan-view (phase timeline + weekly calendar)
- 51 passing pytest tests covering all engines, the plan-regeneration/history-preservation
  logic, the intervals.icu sync, and the daily job

**Not yet live**: the intervals.icu integration (`app/integrations/intervals_icu.py`) is
written against the documented API shape but has not been exercised against a real
account -- there's no API key in this environment. Every write path degrades gracefully
(no-ops) without credentials, so the app is fully usable standalone; see "Confirm before
relying on this" below before pointing it at a real intervals.icu account. Nothing here
calls Garmin directly (by design -- that's intervals.icu's job).

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
  only for the intervals.icu client; unset in this environment, in which case every
  intervals.icu-touching code path (plan sync, daily job's activity pull) no-ops safely.
- `DAILY_JOB_HOUR` -- local hour the in-process scheduler runs the daily autoregulation
  job (default `6`).
- `ENABLE_SCHEDULER` -- set to `false` to disable the in-process APScheduler entirely
  (e.g. if you'd rather trigger `POST /api/jobs/daily-autoregulation` from an external cron).

### Docker

```bash
cd backend
docker build -t training-app .
docker run -p 8000:8000 -v training_app_data:/app/data training-app
```

(Not build-tested in this environment -- no Docker daemon available here. It's a
straightforward pip-install-and-run image; sanity-check it once before relying on it.)

### Hosting: Fly.io (recommended)

The spec just needs "a small always-on cloud instance" (single user, one SQLite file,
an in-process daily cron) -- Fly.io fits that with the least setup, deploying straight
from the existing `Dockerfile`. `backend/fly.toml` is set up for this:

```bash
cd backend
fly auth login
# app names are globally unique on Fly -- edit the `app = "..."` line in fly.toml first
fly volumes create training_app_data --size 1 --region iad   # match primary_region in fly.toml
fly secrets set INTERVALS_ICU_API_KEY=... INTERVALS_ICU_ATHLETE_ID=...  # only if using intervals.icu
fly deploy
```

Note `auto_stop_machines = false` in `fly.toml`: the daily autoregulation job runs via an
in-process APScheduler cron regardless of HTTP traffic, so the machine must never be
suspended for being idle, unlike Fly's usual scale-to-zero default.

Alternative: any cheap VPS (Hetzner/DigitalOcean) running the same image via
`docker compose`, with Caddy in front for automatic HTTPS -- more manual setup (your own
restart/backup story) but full control. Either way, back up the SQLite file
(`/app/data/training_app.db`) periodically -- it's the only durable state.

## Architecture

```
app/
  models.py                 SQLAlchemy models: AthleteProfile, Race, Macrocycle, Phase,
                             Mesocycle, PlannedSession, CompletedSession, Exercise
  engines/
    running.py              Pure, DB-free periodization engine (race date + fitness -> weeks)
    strength.py              RP mesocycle skeleton + race-proximity modulation
    calendar.py               Unified calendar + adjacency guardrail
    autoregulation.py        Run and strength feedback loops (pure functions)
  integrations/
    intervals_icu.py        intervals.icu client (read activities/wellness, write planned
                             workouts) -- see the "unconfirmed wire format" docstring
  plan_service.py            Wires the engines to persistence for one race (history-safe:
                             only regenerates still-`planned` sessions from today forward)
  intervals_sync.py          Guarded push of upcoming run sessions to intervals.icu
  jobs/
    daily_autoregulation.py  The daily job: pull yesterday -> autoregulate -> refresh
                             -> re-sync (spec section 3 & build step 8)
  api/routes.py               FastAPI routes
  main.py                     App wiring, today/plan HTML views, APScheduler cron
  templates/, static/         Jinja2 + vanilla JS/CSS, no build step
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

## Not yet built / hardened

- **The intervals.icu spike itself** (build step 1) -- the client, sync, and daily-job
  field-name guesses (`start_date_local`, `distance`, `moving_time`, `average_heartrate`,
  `readiness`, the `/events` calendar schema) all need checking against a real account
  before the first automated write is trusted to land correctly on the Fenix.
- **Multi-day backlog handling** in the daily job: it only pulls *yesterday's* activities
  per the spec's wording. If the job doesn't run for several days, older stale `planned`
  sessions get marked missed rather than retroactively matched -- fine for a job that
  runs daily without gaps, worth widening the fetch window if that assumption breaks.
- **Deployment**: a `Dockerfile` exists (`backend/Dockerfile`) but hasn't been build-tested
  here (no Docker daemon in this environment) -- verify it once, and add whatever your
  actual host needs (reverse proxy/TLS, process supervision, backups of the SQLite file).
