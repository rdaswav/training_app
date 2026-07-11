# Training App

A personal running + strength periodization app. Generates a race-driven half-marathon
(default) training block, unifies running and RP-style strength into one calendar, and
pushes structured run workouts to intervals.icu (and from there, the Fenix 8).

See `SPEC.md` for the original build specification this implements, `USER_GUIDE.md`
for how to actually use a running deployment day to day (creating a race,
logging sessions, editing the plan), and `PROJECT_PLAN.md` for the history of
everything built beyond the original spec -- every item on that roadmap, and every
GitHub issue raised since, is now shipped.

## Status

All ten build-sequence steps from `SPEC.md` section 10 are implemented, and every
follow-on roadmap item (`PROJECT_PLAN.md`) and GitHub issue raised since is closed --
this is a content-complete build, currently self-hosted and in daily use:

- Data model + persistence (SQLite via SQLAlchemy, with an additive migration path for
  deployments whose DB predates a later schema change -- see "Database migrations" below)
- Running periodization engine (phases, volume ramp/down-weeks/taper, pace/HR targets,
  a VDOT-based race-pace model, and an optional goal race time that overrides race-pace
  segments specifically without touching autoregulated threshold/easy paces)
- RP-style strength engine (MEV -> MAV -> MRV -> deload, RIR progression, race-proximity
  modulation, a mesocycle deload clock nudged toward the running plan's own down-weeks/
  taper, and an Epley e1RM-based load model that prescribes an actual kg target per
  movement pattern instead of just a progress/hold/back-off label)
- Exercise library + injury-flag substitution
- Unified calendar with the hard-lower/key-run adjacency guardrail (auto-shuffle, or a
  compact "Conflict" badge with detail on hover when there's no free day to shuffle into)
- Autoregulation (run pace/HR feedback loop -- bounded to a hard cap of drift from the
  athlete's profile-set baseline pace; strength `NxNxN` logging -> progress/hold/back-off
  with a computed kg target)
- intervals.icu client wired into the plan flow: creating/regenerating a plan pushes the
  next 10 days of run sessions via `upsert_planned_workout` (skipped as a safe no-op if
  credentials aren't configured), and cleans up any already-synced event before a plan
  regeneration replaces its underlying session, so regenerating never leaves orphaned
  duplicate events on the intervals.icu calendar
- Daily autoregulation job (`app/jobs/daily_autoregulation.py`): pulls activities/wellness
  since the oldest unmatched session, autoregulates (matching same-day activities by
  closest distance to the planned session rather than last-write-wins), marks sessions
  completed or missed (a transient intervals.icu fetch failure leaves a session `planned`
  rather than marking a phantom miss), regenerates the plan from today forward, and
  re-syncs to intervals.icu. Runs on an in-process APScheduler cron (default 06:00 local),
  is also reachable manually via `POST /api/jobs/daily-autoregulation`, and records its
  own last-run-at/last-error onto the athlete profile, surfaced as a "Daily job health"
  card in `/settings`
- Optional HTTP Basic Auth (`app/auth_middleware.py`) gating every route -- opt-in via
  `AUTH_USERNAME`/`AUTH_PASSWORD` env vars, unset by default
- FastAPI app: athlete/race management, calendar, today, session logging, structured
  plan export/apply
- Web UI: phone today-view, desktop plan-view (Meridian dark design language: phase
  ribbon, hero/countdown card, weekly load dashboard, strength-mesocycle status card),
  a `/settings` page for athlete profile + race management (including goal time and
  daily job health), `/strength-history`, and a `/session/{id}` detail view
- 175 passing pytest tests covering all engines, the plan-regeneration/history-
  preservation logic, the intervals.icu sync, the daily job, and a real FastAPI
  `TestClient` layer over the JSON API

**Deployment**: self-hosted via Docker on a home NAS (see "Hosting" below) -- the
original Fly.io deployment (`training-app-v1.fly.dev`) was decommissioned once the
self-hosted instance was confirmed working; `backend/fly.toml` is kept in the repo in
case it's wanted again later, but isn't the active deployment.

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
- `AUTH_USERNAME`, `AUTH_PASSWORD` -- if both are set, every route (pages and API) is
  gated behind HTTP Basic Auth (`app/auth_middleware.py`). Unset by default -- no auth,
  matching the rest of this project's "opt-in via env var" pattern. Basic Auth sends
  credentials base64-encoded, not encrypted, on every request -- fine on a LAN-only
  deployment, but put a reverse proxy with HTTPS in front before exposing this to the
  internet.

### Docker

Build-tested and confirmed working -- this is the actual production path (a home NAS's
Container Manager, but any Docker host works the same way):

```bash
cd backend
docker build -t training-app .
docker run -d \
  --name training-app \
  --restart unless-stopped \
  -p 8000:8000 \
  -v training_app_data:/app/data \
  -e ENABLE_SCHEDULER=true \
  -e DAILY_JOB_HOUR=6 \
  -e INTERVALS_ICU_API_KEY=... \
  -e INTERVALS_ICU_ATHLETE_ID=... \
  -e AUTH_USERNAME=... \
  -e AUTH_PASSWORD=... \
  training-app
```

The intervals.icu and auth env vars are all optional -- omit any of them to leave that
feature off. To pick up a new version later: `git pull`, re-run `docker build`, then
`docker stop training-app && docker rm training-app` and re-run the `docker run` command
above (the `training_app_data` volume persists your data across this). Any new columns a
schema change introduces get added and backfilled automatically on the next startup (see
"Database migrations" below) -- no manual migration step needed.

### Hosting: self-hosted (current) vs. Fly.io (available, not in active use)

**Currently hosted**: on a home NAS via Docker (see above), reachable on the LAN and
gated behind HTTP Basic Auth. This is a single-user, always-on-at-home setup -- no
external hosting cost, no internet exposure by default. If you want it reachable outside
your LAN, put a reverse proxy (e.g. your NAS's own reverse-proxy feature, or Caddy) with
a real HTTPS certificate in front rather than port-forwarding directly, since Basic Auth
sends credentials base64-encoded (not encrypted).

**Fly.io** was the original deployment target and is still fully wired up
(`backend/fly.toml`), kept in the repo in case a cloud instance is wanted again later
(e.g. if you want access away from home without your own reverse-proxy setup):

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

**Fly dashboard gotcha**: setting secrets via the dashboard's Secrets tab only *stages*
them -- the banner says so explicitly ("Run `fly deploy` to take them live"). A plain
"Restart" on the Machines tab does **not** apply staged secrets; only an actual new
deploy does (`fly deploy` from the CLI, or pushing a commit if GitHub auto-deploy is set
up). Use `GET /api/config-check` to confirm whether a deployed instance actually sees
`INTERVALS_ICU_API_KEY`/`INTERVALS_ICU_ATHLETE_ID` as non-empty (never returns the
values themselves) rather than assuming a restart was enough.

Either way, back up the SQLite file (`/app/data/training_app.db`) periodically -- it's
the only durable state.

### Database migrations

There's no Alembic (or equivalent) here -- deliberately, given the project's small
single-file-SQLite scale. `Base.metadata.create_all()` (run on every startup) only
creates tables that don't exist yet; it never adds columns to a table that's already
there. When a schema change adds a column to an existing table, `app/db.py` runs a small
additive migration alongside `create_all()`: it checks (via SQLite's own
`PRAGMA table_info`) which of the new columns are missing and `ALTER TABLE ... ADD
COLUMN`s them in, then backfills any values that need a real default computed from
existing data (e.g. the autoregulation drift-clamp's baseline pace columns backfill from
whatever pace is currently stored, since there's no historical record of the original
profile-set value). This runs automatically on every app startup and is safe to run
repeatedly (a no-op once the columns already exist) -- see `_migrate_athlete_profiles`/
`_migrate_macrocycles` in `app/db.py` for the two migrations that exist so far.

## Architecture

```
app/
  models.py                 SQLAlchemy models: AthleteProfile, Race, Macrocycle, Phase,
                             Mesocycle, PlannedSession, CompletedSession, Exercise
  db.py                      Engine/session setup + the additive SQLite migration path
                             (see "Database migrations" above)
  auth_middleware.py         Optional HTTP Basic Auth over every route (opt-in via env var)
  engines/
    running.py               Pure, DB-free periodization engine (race date + fitness ->
                              weeks); VDOT race-pace model + goal-time override live here
    vdot.py                   Daniels' VDOT formulas backing the race-pace model
    strength.py               RP mesocycle skeleton, race-proximity modulation, the
                               mesocycle-deload/running-phase coupling
                               (best_mesocycle_offset), and the e1RM/load-prescription
                               model (estimate_e1rm, prescribe_next_load)
    calendar.py                Unified calendar + adjacency guardrail (auto-shuffle, or
                               flags a UnifiedSession for the compact "Conflict" badge)
    autoregulation.py         Run and strength feedback loops (pure functions) -- strength
                              feedback now includes a computed kg target, not just a label
    dashboard_summary.py      Phase-timeline ribbon + strength-mesocycle status for /plan
    load_summary.py           Weekly run-km/strength-tonnage aggregation for /plan's
                              load dashboard
  integrations/
    intervals_icu.py        intervals.icu client (read activities/wellness, write planned
                             workouts) -- see the "unconfirmed wire format" docstring
  plan_service.py            Wires the engines to persistence for one race (history-safe:
                             only regenerates still-`planned` sessions from today forward)
  intervals_sync.py          Guarded push of upcoming run sessions to intervals.icu, and
                             cleanup of already-synced events before a regeneration
                             replaces their underlying sessions (delete_synced_events)
  jobs/
    daily_autoregulation.py  The daily job: pull activities/wellness since the oldest
                             unmatched session -> autoregulate (drift-clamped, closest-
                             match activity selection) -> refresh -> re-sync -> record
                             job health onto the athlete profile
  api/routes.py               FastAPI routes
  main.py                     App wiring, today/plan/settings/history/session HTML views,
                             APScheduler cron
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
5. **Repeat-block decomposition -- confirmed 2026-07-09 (follow-up spike).** Composite
   quality-session steps now decompose into a real `RunRepeatStep` (work + optional
   recovery, repeated N times) instead of one aggregate line, and the `Nx` wire syntax
   was verified against a live account: a standalone count line followed by nested
   dashed work/recovery lines parses as `{"reps": N, "steps": [...]}` with both legs
   present and distance/duration correctly summed across all N reps. See
   `app/integrations/intervals_icu.py`'s module docstring for the full writeup.
   Along the way this surfaced a real bug: a decimal-minute duration token like
   `"1.5m"` (or `"0.333...m"` for a 20-second stride) silently fails to parse and drops
   the whole step -- fixed by converting fractional minutes to whole seconds (`"90s"`,
   `"20s"`) in `_format_duration`.
6. **VDOT race-pace model -- implemented.** `AthleteFitness.race_pace_sec_per_km` now
   runs Daniels' VDOT formulas (`engines/vdot.py`) instead of the old
   `threshold_pace + 12s/km` placeholder, calibrated off the athlete's threshold pace
   and solved for their actual race distance via fixed-point iteration.

## Deliberately deferred / out of scope

Everything on `PROJECT_PLAN.md`'s roadmap and every GitHub issue raised is done. What's
left open is deliberate, not unfinished:

- **%HR basis spot-check** (max HR vs. LTHR) -- a one-time manual check against the
  athlete's own intervals.icu HR zone chart, not code; see the intervals.icu spike above.
- **Design polish "Tier 3"** (entrance animations, gradient background glows, phone-mockup
  marketing chrome) -- explicitly skipped as decorative for a solo daily-use tool; see
  `PROJECT_PLAN.md`'s design-overhaul section.
- **Auth is HTTP Basic, not a real login system** -- deliberately minimal for a
  single-user LAN deployment (see "Configuration" above); credentials are sent
  base64-encoded, not encrypted, so put HTTPS in front before exposing this beyond a LAN.
