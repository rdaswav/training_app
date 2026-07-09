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

## 4. intervals.icu polish -- DONE

Shipped:
- **VDOT race-pace model**: `AthleteFitness.race_pace_sec_per_km` now runs Daniels'
  VDOT formulas (`engines/vdot.py`) instead of the old `threshold_pace + 12s/km`
  placeholder -- threshold pace calibrates a VDOT, then a fixed-point iteration
  solves for the pace matching the athlete's actual race distance
  (`AthleteFitness.race_distance_km`, threaded through from `Race.distance_km`).
- **Interval/repeat-block decomposition**: quality-session builders in
  `engines/running.py` now emit a `RunRepeatStep` (work + optional recovery,
  repeated N times) for every composite session (Re-base strides, Build 1 cruise
  intervals, Build 2 threshold/race-pace reps, Taper race-pace touch) instead of one
  flattened `RunStep`. Serializes to a `"type": "repeat"` JSON shape
  (`plan_service.py`) with full backward-compatibility for already-persisted
  flat-shape rows (`intervals_sync.py` defaults missing/unrecognized `type` to a
  plain step). `integrations/intervals_icu.py` emits the corresponding `Nx`
  repeat-block wire syntax.
  - **Caveat**: the `Nx` repeat-block syntax itself is UNCONFIRMED against a live
    intervals.icu account (unlike the pace/HR tokens, which were confirmed
    2026-07-09) -- tracked via `REPEAT_BLOCK_SYNTAX_CONFIRMED = False` in that
    module. A follow-up live spike (post a repeat-block workout, inspect the parsed
    `workout_doc`, correct the syntax if needed) is a separate future step, not
    bundled into this one.
- **%HR basis spot-check**: still an open manual follow-up, not code -- once real
  syncing runs against the live account, compare a synced event's `%HR` value
  against the athlete's own HR zone chart to confirm whether it's %max HR or %LTHR.

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

## 6. Maintenance mode (v2, not yet scoped)

**Idea**: when there's no race currently planned (between blocks, or before the
first one is ever created), the app currently has no concept of what to generate --
`generate_and_persist_plan` requires a `Race`. Worth a "maintenance mode" that
prescribes a sensible steady-state week (strength at MEV/MAV-ish accumulation,
running at a stable base volume, no phase progression/taper logic) when there's no
active race, rather than leaving the athlete with an empty calendar between blocks.

Not scoped yet -- flagged here for a future pass. Open questions when it's picked
up: does it need its own `PlannedSession` generation path independent of
`Race`/`Macrocycle`, or a synthetic "maintenance race" placeholder far in the
future? How does the daily job/UI distinguish "no race" from "maintenance active"?

---

## Suggested order

1. ~~Athlete/race management UI~~ -- done
2. ~~Strength UI depth~~ -- done
3. ~~intervals.icu polish~~ -- done (repeat-block live-syntax spike still an open follow-up)
4. Visual design overhaul (scope tightly -- e.g. one load chart, not a full redesign) -- next up
5. Hardening items, opportunistically alongside 4 rather than as a dedicated pass
6. Maintenance mode (v2, not yet scoped) -- whenever there's appetite for it
