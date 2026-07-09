# Training App

A personal running + strength periodization app. Generates a race-driven half-marathon
(default) training block, unifies running and RP-style strength into one calendar, and
pushes structured run workouts to intervals.icu (and from there, the Fenix 8).

See `SPEC.md` for the full build specification this implements, `USER_GUIDE.md`
for how to actually use a running deployment day to day (creating a race,
logging sessions, editing the plan), and `PROJECT_PLAN.md` for the prioritized
roadmap of what's left between this MVP and a fuller build.

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
- 52 passing pytest tests covering all engines, the plan-regeneration/history-preservation
  logic, the intervals.icu sync, and the daily job

**intervals.icu integration**: spiked and confirmed against a real account on 2026-07-09
(auth, activity/wellness field names, and the planned-workout `description` syntax all
verified live -- see "intervals.icu spike" below for what changed as a result). One item
remains genuinely unconfirmed: whether intervals.icu's `%HR` target is a percentage of
max HR or of LTHR. Every write path still degrades gracefully (no-ops) without
credentials configured. Nothing here calls Garmin directly (by design -- that's
intervals.icu's job).

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

The default template is Mon strength / Tue run / Wed strength / Thu run / Fri strength /
Sat rest / Sun long run (`engines/running.py`'s `run_days` and `engines/strength.py`'s
`DAY_TEMPLATE`; changed from the spec's original Sat-long-run/Sun-rest layout so Saturday
is the athlete's rest day). This still has one built-in hard-lower-before-key-run
adjacency every week: Wed (Lower) before Thu's quality run. Previously (with Sunday as
the rest day) the guardrail could shuffle this to the free rest day; now it can't --
Saturday's day-after is Sunday's long run, so swapping Wednesday there would just create
a new conflict, and the guardrail correctly refuses and flags it in place instead. Net
effect: still exactly one flagged conflict per week (same as before), just consistently
on Wednesday now rather than occasionally on Friday -- the Fri (Hybrid) -> Sat (long run)
adjacency the spec's original layout had is fully eliminated by this swap, since Saturday
is rest now. Worth revisiting if you want zero flags: either drop Wednesday's squat/hinge
compound work in favor of something lighter, or accept the flag as informational and rely
on RIR/load to keep it light the day before a quality run.

## intervals.icu spike -- confirmed 2026-07-09 (spec section 11)

The spec's build step 1 spike happened against a real account. Findings, folded into
`app/integrations/intervals_icu.py` (see its module docstring for the full detail):

1. **Auth + endpoints confirmed.** Basic auth (`API_KEY` / the api key) against
   `/api/v1/athlete/{id}` and `/api/v1/athlete/{id}/events` works exactly as assumed.
   Reading activities/wellness also matched the guessed field names
   (`start_date_local`, `distance`, `moving_time`, `average_heartrate`; wellness `id`
   as the ISO date, `readiness`/`sleepScore`) -- no changes needed in
   `jobs/daily_autoregulation.py`.
2. **The original `description` syntax was wrong and silently dropped every target.**
   intervals.icu parses `description` into a structured `workout_doc`, but the original
   `"- {label}: {distance}, Pace {pace}, HR <= {bpm}"` format only got the distance
   parsed -- pace and HR were both silently ignored (confirmed by inspecting the real
   `workout_doc` response). The actual syntax needs bare space-separated tokens per
   dashed line: `"<mm:ss>/km Pace"` (value **then** the word "Pace" -- reversed order
   fails) and `"<pct>% HR"`. This is now fixed in `step_to_line`/`session_to_description`.
3. **A single step CAN carry both a pace target and an HR target** -- resolves the
   previously-open question. Confirmed: `"8km 6:30/km Pace 75% HR"` parses both fields
   on one step.
4. **HR only accepts a percentage (or a zone), never an absolute bpm value.** Raw bpm
   forms (`"150bpm HR"`, `"140-150bpm HR"`) were tested live and silently ignored. Since
   this app models HR ceilings as absolute bpm (`AthleteProfile.max_hr`,
   `aerobic_hr_ceiling`), the client now converts bpm to `%max_hr` when writing to
   intervals.icu. **Not independently confirmed**: whether intervals.icu's "%HR" base is
   %max HR or %LTHR -- spot-check one generated event's target against the athlete's own
   HR zone chart before trusting the exact percentage.
5. **Known limitation, not yet fixed**: composite steps in this app (e.g. "6 x 20s
   strides w/ 60s float" as a single `RunStep`) are sent as one aggregate distance+pace
   line, not decomposed into intervals.icu's native `Nx` repeat-block syntax -- so
   interval/recovery structure within a quality session won't show on the watch, only
   the aggregate target will. Follow-up: teach the running engine to emit proper repeat
   blocks.
6. **VDOT/critical-pace source still unconfirmed** -- unrelated to the spike above.
   `AthleteFitness.race_pace_sec_per_km` in `engines/running.py` still derives race pace
   as `threshold_pace + 12s/km`, a rough placeholder. Replace with either a proper VDOT
   model or intervals.icu's own derived pace zones (not checked during this spike).

## Not yet built / hardened

- **Interval/repeat-block decomposition** for intervals.icu writes (see point 5 above).
- **Multi-day backlog handling** in the daily job: it only pulls *yesterday's* activities
  per the spec's wording. If the job doesn't run for several days, older stale `planned`
  sessions get marked missed rather than retroactively matched -- fine for a job that
  runs daily without gaps, worth widening the fetch window if that assumption breaks.
- **Deployment**: a `Dockerfile` and `fly.toml` exist but haven't been build/deploy-tested
  here (no Docker daemon in this environment, and no Fly account) -- verify once, and add
  whatever your actual host needs (backups of the SQLite file, in particular).
