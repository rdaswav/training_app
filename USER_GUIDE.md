# User Guide

Day-to-day usage of a running deployment. Everything below assumes a self-hosted
instance (see `README.md`'s "Hosting" section) reachable at some base URL --
substitute your own, e.g. `http://<your-nas-ip>:8000`. If you've set
`AUTH_USERNAME`/`AUTH_PASSWORD`, your browser will prompt for them the first time it
hits the site; `curl` examples below need `-u username:password` added if auth is on.

## 1. Set your current fitness and create a race (via the UI)

Open `/settings`. Two forms live there:

- **Athlete profile**: weekly volume, easy/threshold pace (entered as `M:SS`, e.g.
  `6:30`), aerobic HR ceiling, max HR, injury flags (comma-separated, e.g.
  `knee, lower_back` -- excludes matching strength exercises from selection, see
  `backend/app/seed.py` for the tagged exercise library).
- **Race**: name, date, distance (`21.1` half, `42.195` full, `10`, `5`, etc.),
  an optional **goal time** (`H:MM:SS` or `M:SS`, e.g. `1:45:00`) -- if set, this
  overrides the race-pace target used in race-pace-specific segments (Build 2's
  race-pace reps, the Taper's race-pace touch) directly. Your threshold/easy paces
  stay derived from your actual current fitness either way -- a goal time doesn't
  push you into paces harder than you've earned, it only sets the aspirational
  target for the segments that are explicitly about racing that goal. Priority
  (`A`/`B`/tune-up), and an optional plan start date (leave blank to start today).

Creating a race immediately generates the full phase sequence and every planned
session from today through race week, and (if intervals.icu credentials are set)
pushes the next 10 days of runs to your calendar. Editing an existing race deletes
and recreates it -- completed/missed history is preserved, and the current start date
stays anchored unless you deliberately change it.

The same fields are available over the API if you'd rather script it:

```bash
curl -X PUT http://localhost:8000/api/athlete \
  -H "Content-Type: application/json" \
  -d '{
    "weekly_volume_km": 25,
    "easy_pace_sec_per_km": 360,
    "threshold_pace_sec_per_km": 300,
    "aerobic_hr_ceiling": 148,
    "max_hr": 186,
    "injury_flags": []
  }'

curl -X POST http://localhost:8000/api/races \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Your Race Name",
    "race_date": "2026-10-04",
    "distance_km": 21.1,
    "goal_time_sec": 6300,
    "priority": "A"
  }'
```

All fields are optional in `PUT /api/athlete` -- only send what you want to change.

## 2. Use the app day to day

- **Today's session**: the site root (`/`). Shows whatever's planned for today --
  run steps with pace/HR targets (repeat blocks like "3x 1.6km cruise interval"
  render as a single grouped step, not three separate lines), or strength
  prescriptions with exercise names and sets/reps/RIR. If you've logged that
  movement pattern before, the log form pre-fills a suggested working weight and
  shows it in the prescription's meta line -- computed from your most recent log
  for that pattern (Epley e1RM projected at this session's rep/RIR target), not
  just a guess.
- **Log a strength set**: fill in reps/weight/RIR per set (add/remove rows as
  needed) and hit "Log sets" -- you'll get an inline coach response (what you did /
  how it read / what's next), and "progress"/"back off" now include an actual kg
  target for next time, not just the label.
- **Complete a run**: fill in actual pace/HR and hit "Log complete" -- same
  three-row coach response (progress/hold/soften).
- **Full plan view**: `/plan` -- hero/countdown card (race name, distance, goal
  time if set, days to race), phase timeline ribbon, weekly load dashboard (run
  volume + strength tonnage), a strength-mesocycle status card, and the full
  weekly calendar. An unresolved adjacency conflict (hard lower-body strength the
  day before a key run, with no free rest day to auto-shuffle into) shows as a
  compact "Conflict" badge -- hover it for the detail sentence, rather than an
  always-visible warning paragraph.
- **Strength history**: `/strength-history` -- past completed strength sessions
  grouped by movement pattern.
- **Any past/present/future session's detail**: `/session/{id}` (linked from every
  day on `/plan`) -- works for logging retroactively too, not just today.

If a day shows no session, that's expected sometimes -- the adjacency guardrail
occasionally shuffles a strength day to rest (see the README's "known tension in
the fixed weekly template").

## 3. Check the daily job's health

`/settings` also shows a "Daily job health" card: last successful run time, and
whether the last run errored (with the error visible on hover). Worth a glance
occasionally -- it's the one place a silent failure (e.g. an intervals.icu API
change, a network blip) would actually surface, rather than only showing up days
later as unexplained gaps in your training history.

## 4. Editing the plan later (the LLM edit path, spec section 8)

This is meant to be driven by asking Claude directly ("push the race back two
weeks", "I tweaked my knee, no lunges for 10 days") rather than hand-written curl --
Claude reads/writes this endpoint:

```bash
curl -X POST http://localhost:8000/api/plan/apply \
  -H "Content-Type: application/json" \
  -d '{
    "race_id": 1,
    "race_date": "2026-10-18",
    "injury_flags": ["knee"]
  }'
```

Any of `race_date`, `weekly_volume_km`, `injury_flags` can be included; omitted
fields are left unchanged. This regenerates every still-`planned` session from
today forward (completed/missed history is never touched) and re-syncs upcoming
runs to intervals.icu.

To export the current plan state (e.g. to hand to Claude for an edit):
```bash
curl http://localhost:8000/api/plan/export
```

## 5. intervals.icu sync (optional)

Only needed if you want plans pushed to your Fenix via intervals.icu. Set as env
vars on the container (see `README.md`'s Configuration/Docker sections):

```bash
docker run -d ... \
  -e INTERVALS_ICU_API_KEY=... \
  -e INTERVALS_ICU_ATHLETE_ID=i165321 \
  training-app
```

Without these set, the app works standalone -- every intervals.icu-touching code
path no-ops safely (see README's "intervals.icu spike" section for the confirmed
wire format and known limitations). `GET /api/config-check` confirms whether a
running instance actually sees both env vars as non-empty (never returns the
values themselves).

Regenerating a plan (editing a race, or the daily job) automatically cleans up any
already-synced intervals.icu events for sessions being replaced, so you shouldn't
see duplicate/orphaned workouts on the intervals.icu calendar after a plan change.

## 6. Manually trigger the daily job

Normally runs automatically at `DAILY_JOB_HOUR` (default 06:00) via an in-process
scheduler. To run it on demand (e.g. right after logging something, or for
testing):

```bash
curl -X POST http://localhost:8000/api/jobs/daily-autoregulation
```
