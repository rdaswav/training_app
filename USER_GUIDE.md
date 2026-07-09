# User Guide

Day-to-day usage of the deployed app. Most of the planning workflow only has an
API today (no UI form yet) -- this guide covers the curl commands for that,
plus what's already available in the browser.

Replace `https://training-app-v1.fly.dev` below with your own deployment URL.

## 1. Set your current fitness (do this before creating a race)

The plan is generated from your `AthleteProfile` at the moment you create the
race, so get this right first -- there's no UI for it yet either.

```bash
curl -X PUT https://training-app-v1.fly.dev/api/athlete \
  -H "Content-Type: application/json" \
  -d '{
    "weekly_volume_km": 25,
    "easy_pace_sec_per_km": 360,
    "threshold_pace_sec_per_km": 300,
    "aerobic_hr_ceiling": 148,
    "max_hr": 186,
    "injury_flags": []
  }'
```

- `weekly_volume_km` -- your typical current weekly running volume.
- `easy_pace_sec_per_km` / `threshold_pace_sec_per_km` -- seconds per km (e.g. `360` = 6:00/km).
- `aerobic_hr_ceiling` -- the bpm ceiling for easy runs (spec's "Z4 problem" guardrail).
- `max_hr` -- used both for the easy-run HR guardrail and to convert HR targets to `%HR` when pushing to intervals.icu.
- `injury_flags` -- e.g. `["lower_back"]` or `["knee"]`; excludes matching strength exercises from selection (see `backend/app/seed.py` for the tagged exercise library).

All fields are optional in the request -- only send what you want to change; the rest keep their current value.

## 2. Create your race

```bash
curl -X POST https://training-app-v1.fly.dev/api/races \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Your Race Name",
    "race_date": "2026-10-04",
    "distance_km": 21.1,
    "priority": "A"
  }'
```

- `race_date` -- `YYYY-MM-DD`.
- `distance_km` -- `21.1` half, `42.195` full, `10`, `5`, etc.
- `priority` -- `"A"`, `"B"`, or `"tune_up"`.
- `goal_time_sec` is optional (not currently used by the engine, but stored).

This immediately generates the full phase sequence and every planned session
from today through race week, and (if intervals.icu secrets are set) pushes
the next 10 days of runs to your calendar.

## 3. Use the app day to day (this part has a UI)

- **Today's session**: open the site root (`https://training-app-v1.fly.dev/`). Shows whatever's planned for today -- run steps with pace/HR targets, or strength prescriptions with exercise names, sets/reps/RIR.
- **Log a strength set**: fill in the reps/weight/RIR fields under each exercise and hit "Log" -- you'll get an inline progress/hold/back-off response.
- **Complete a run**: fill in actual pace/HR and hit "Log complete" -- you'll get an inline autoregulation response (progress/hold/soften).
- **Full plan view**: `/plan` -- phase timeline, weekly calendar, and any adjacency-guardrail notes (e.g. "Flagged: hard lower-body work falls the day before...").

If a day shows no session, that's expected sometimes -- the adjacency guardrail occasionally shuffles a strength day to rest (see the README's "known tension in the fixed weekly template").

## 4. Editing the plan later (the LLM edit path, spec section 8)

This is meant to be driven by asking Claude directly ("push the race back two weeks", "I tweaked my knee, no lunges for 10 days") rather than hand-written curl -- Claude reads/writes this endpoint:

```bash
curl -X POST https://training-app-v1.fly.dev/api/plan/apply \
  -H "Content-Type: application/json" \
  -d '{
    "race_id": 1,
    "race_date": "2026-10-18",
    "injury_flags": ["knee"]
  }'
```

Any of `race_date`, `weekly_volume_km`, `injury_flags` can be included; omitted
fields are left unchanged. This regenerates every still-`planned` session from
today forward (completed/missed history is never touched) and re-syncs
upcoming runs to intervals.icu.

To export the current plan state (e.g. to hand to Claude for an edit):
```bash
curl https://training-app-v1.fly.dev/api/plan/export
```

## 5. intervals.icu sync (optional)

Only needed if you want plans pushed to your Fenix via intervals.icu. Set as
Fly secrets (not in the repo):

```bash
fly secrets set INTERVALS_ICU_API_KEY=... INTERVALS_ICU_ATHLETE_ID=i165321 -a training-app-v1
```

Without these set, the app works standalone -- every intervals.icu-touching
code path no-ops safely (see README's "intervals.icu spike" section for the
confirmed wire format and known limitations).

## 6. Manually trigger the daily job

Normally runs automatically at `DAILY_JOB_HOUR` (default 06:00) via an
in-process scheduler. To run it on demand (e.g. right after logging something,
or for testing):

```bash
curl -X POST https://training-app-v1.fly.dev/api/jobs/daily-autoregulation
```
