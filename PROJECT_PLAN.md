# Project Plan: MVP -> Full

Status snapshot and the prioritized remaining work. See `SPEC.md` for the original
build spec and `README.md` for what's already built/confirmed. This file tracks
what's left and in what order.

## Where things stand

All ten of the spec's build-sequence steps (section 10) are implemented and tested
(53 passing tests): data model, running periodization engine, RP-style strength
engine, exercise library + substitution, unified calendar with the adjacency
guardrail, run/strength autoregulation, a confirmed-working intervals.icu
integration, the daily autoregulation job, a FastAPI backend, and a minimal web UI.
It's deployed and live at `training-app-v1.fly.dev`.

What's below is genuinely unbuilt or thin -- not spec gaps exactly, but the gap
between "MVP works end to end" and "pleasant, complete to actually live with day to
day."

---

## 1. Athlete/race management UI -- next up

**Problem**: setting your fitness profile, creating a race, and deleting/editing one
are curl-only right now (`USER_GUIDE.md`). Every edit so far in this project has
gone through me running curl against the live app on your behalf, which doesn't
scale as a normal workflow.

**Scope**:
- A `/settings` page (or similar) with:
  - An athlete profile form (weekly volume, easy/threshold pace, HR ceiling, max HR,
    injury flags) pre-filled from `GET /api/athlete`, saving via `PUT /api/athlete`.
  - Current race display (name/date/distance/priority) with an edit-in-place or
    delete-and-recreate flow, since race name/distance/priority aren't editable via
    `/api/plan/apply` today (only `race_date`, `weekly_volume_km`, `injury_flags`
    are) -- either extend `PlanApplyRequest` to cover the rest, or keep
    delete+recreate but make it a single UI action instead of two curl calls.
  - A "create race" form including `plan_start_date`.
- Nav link from the existing Today/Plan views.

**Not in scope for this pass**: auth/login (single-user app, spec explicitly doesn't
call for it).

---

## 2. Strength UI depth

**Problem**: the RP engine, substitution, and `NxNxN` logging all work, but the UI
only shows *today's* prescription -- there's no way to see what you actually lifted
last time, track a working weight over a mesocycle, or substitute an exercise
without an injury flag forcing it.

**Scope**:
- A history view: past `CompletedSession` rows for strength, grouped by
  pattern/exercise, so you can see trend (e.g. squat 3x5 across the last 5 sessions).
- A "swap exercise" control on today's view: pick any other exercise tagged with
  the same movement pattern, not just the injury-flag-forced substitution.
  `engines/strength.py`'s `select_exercise` already supports arbitrary
  pattern-scoped selection; this is a UI + a small API endpoint
  (`PATCH /api/sessions/{id}` to override one prescription's exercise) away.
- Retroactive logging: right now if you forget to log a session the day of, the
  daily job will mark it missed at the next run. Worth a "log a past session"
  entry point on the plan view for sessions still sitting as `missed` or `planned`
  in the past.

---

## 3. Visual design overhaul

**Problem**: current UI is functional CSS variables + Jinja templates, no charts, no
real visual system. Spec section 7 explicitly wants a "weekly dashboard: run
volume, strength tonnage, combined load trend" and section 9 wants richer phase
timeline/calendar/countdown treatment on the desktop planning view -- none of that
exists yet.

**Scope**:
- Pick a charting approach (spec leaves this open -- a lightweight canvas/SVG
  approach with no dependency, or a small charting lib via CDN, fits this
  no-build-step stack best).
- Weekly load dashboard: run volume (km) and strength tonnage (sum of sets x reps x
  weight, or just sets, depending on how meaningful raw tonnage is without knowing
  actual logged loads yet) trended across the block, probably on `/plan`.
- A genuine design pass: typography, spacing, color use beyond "make it not look
  broken," and mobile-specific polish on the today view (it's the one meant to be
  used one-handed on a phone).
- This is the most open-ended item on this list -- worth scoping tightly (e.g. "one
  load chart on /plan" as v1) rather than treating it as one big redesign.

---

## 4. intervals.icu polish

**Problem**: the integration works and is confirmed against a real account, but two
things documented in the README as known gaps remain:

**Scope**:
- **Interval/repeat-block decomposition**: composite quality-session steps (e.g.
  "6 x 20s strides w/ 60s float") currently get sent to intervals.icu as one
  aggregate distance+pace line, not a real `Nx` repeat block. Fixing this means
  teaching `engines/running.py`'s quality-session builders to emit a list of
  sub-steps (work interval + recovery, repeated N times) instead of one flattened
  `RunStep`, and teaching `integrations/intervals_icu.py`'s formatter to emit
  intervals.icu's repeat-block syntax for those.
- **VDOT/critical-pace model**: `AthleteFitness.race_pace_sec_per_km` is currently
  `threshold_pace + 12s/km`, a placeholder. Replace with a real VDOT table lookup
  or critical-pace calculation once weekly_volume/paces are more than manually
  entered defaults (this matters more once there's real logged run history to
  derive fitness from automatically, which is arguably its own future item: "derive
  the athlete's current fitness from intervals.icu history instead of manual entry").
- **%HR basis spot-check**: confirm whether intervals.icu's `%HR` target is a
  percentage of max HR or LTHR by comparing one generated event against the
  athlete's own HR zone chart in the intervals.icu UI.

---

## 5. Smaller hardening items

- **API-level tests**: current test suite covers engines and `plan_service`/
  `intervals_sync`/the daily job directly; nothing exercises `api/routes.py` through
  FastAPI's `TestClient`. Worth adding once the UI work above starts touching routes
  more, so regressions get caught by CI rather than manual curl checks.
- **Docker build verification**: `backend/Dockerfile` has never actually been
  built in an environment with a Docker daemon (blocked in this sandbox). Fly.io
  deploys via its own remote builder and that's confirmed working, but the Dockerfile
  itself should still get a real `docker build` once, for anyone who wants to run it
  outside Fly.
- **Daily job multi-day backlog handling**: it only pulls *yesterday's* activities
  per the spec's literal wording. Fine as long as the job runs daily without gaps;
  widen the fetch window if that assumption ever breaks in practice.

---

## Suggested order

1. Athlete/race management UI (this session's next task)
2. Strength UI depth
3. intervals.icu polish (interval decomposition first, VDOT/​%HR are lower-stakes)
4. Visual design overhaul (scope tightly -- e.g. one load chart, not a full redesign)
5. Hardening items, opportunistically alongside 2-4 rather than as a dedicated pass
