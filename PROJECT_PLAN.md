# Project Plan: MVP -> Full

Status snapshot and the prioritized remaining work. See `SPEC.md` for the original
build spec and `README.md` for what's already built/confirmed. This file tracks
what's left and in what order.

## Where things stand

All ten of the spec's build-sequence steps (section 10) are implemented and tested
(57 passing tests): data model, running periodization engine, RP-style strength
engine, exercise library + substitution, unified calendar with the adjacency
guardrail, run/strength autoregulation, a confirmed-working intervals.icu
integration, the daily autoregulation job, a FastAPI backend, and a web UI covering
today/plan/settings/history/session-detail views. It's deployed and live at
`training-app-v1.fly.dev`.

What's below is genuinely unbuilt or thin -- not spec gaps exactly, but the gap
between "MVP works end to end" and "pleasant, complete to actually live with day to
day."

---

## 1. Athlete/race management UI -- DONE

Shipped: a `/settings` page with an athlete profile form (weekly volume, paces
entered as M:SS, HR ceiling/max HR, injury flags) and a race form (name, date,
distance, priority, plan start date). Editing an existing race deletes and
recreates it transparently in one action, pre-filling the current macrocycle's
start date so it stays anchored unless deliberately changed.

**Not in scope**: auth/login (single-user app, spec explicitly doesn't call for it).

---

## 2. Strength UI depth -- DONE

Shipped:
- **History view** (`/strength-history`): past completed strength sessions grouped
  by movement pattern, showing exercise, sets/reps/weight/RIR, and the
  autoregulation feedback for each, most recent first.
- **Manual exercise substitution**: a "Swap exercise" control on any still-planned
  strength prescription, backed by `GET /api/exercises?pattern=X` and
  `PATCH /api/sessions/{id}/exercise` -- picks freely within the pattern, not just
  the injury-flag-forced substitution.
- **Retroactive logging**: a `/session/{id}` detail page (linked from every day on
  `/plan`) shows the log/complete form for any session that isn't yet `completed`,
  including ones sitting as `missed` or `planned` in the past -- not just today's.

Shared a `_session_card.html` Jinja macro between the today-view and the new
session-detail page to avoid duplicating the run/strength rendering logic.

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

- **API-level tests**: `test_exercise_swap.py` calls route functions directly (not
  through FastAPI's `TestClient`/HTTP layer) for the new swap endpoint; the rest of
  `api/routes.py` still isn't covered that way. Worth a real `TestClient` pass at
  some point so regressions get caught by CI rather than manual curl checks.
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

1. ~~Athlete/race management UI~~ -- done
2. ~~Strength UI depth~~ -- done
3. intervals.icu polish (interval decomposition first, VDOT/​%HR are lower-stakes) -- next up
4. Visual design overhaul (scope tightly -- e.g. one load chart, not a full redesign)
5. Hardening items, opportunistically alongside 3-4 rather than as a dedicated pass
