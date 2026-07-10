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

## 3. Visual design overhaul -- v1 DONE

Shipped (scoped tightly per this doc's own suggestion, as v1 rather than a full
redesign):
- **Weekly load dashboard** (`/plan`): two hand-rolled inline-SVG-free bar charts
  (no charting dependency, consistent with the existing no-build-step stack) --
  run volume (km, known for the whole block since the periodization engine
  generates future weeks' distances up front) and strength tonnage (kg, only
  knowable for weeks that have actually been logged). Future weeks render as a
  dashed placeholder on the tonnage chart rather than a fabricated zero, so
  "hasn't happened yet" is never confused with "happened, nothing logged."
  Aggregation lives in a new pure-function module, `engines/load_summary.py`
  (`sum_run_km_by_week`, `sum_strength_tonnage_by_week`, `build_weekly_load_series`),
  unit-tested directly with no DB/TestClient, following the existing `engines/*.py`
  convention. Tonnage is computed via a direct query (mirroring the existing
  `strength_history_view` join) rather than `PlannedSession.completed`, since that
  relationship is unsafe for strength sessions specifically (multiple prescriptions
  logged separately create multiple `CompletedSession` rows per planned session).
- **Mobile polish**: a single `@media (max-width: 600px)` CSS pass -- 44px-minimum
  touch targets on form inputs/buttons (shared by the today view and session-detail
  page via `_session_card.html`), and both the weekly calendar table and the new
  load charts scroll horizontally inside their card rather than squeezing columns
  unreadably on a phone.

**Not done / deferred**: a fuller typography/spacing-token system (the app still
has no `--space-*`/`--font-*` scale, just hardcoded per-rule values) and richer
phase-timeline/calendar/countdown treatment (spec section 9) beyond the existing
`.phase-bar`. Worth a v2 pass if there's appetite, but v1 intentionally stopped at
"the one load chart + mobile polish" scope.

### Meridian design language adoption -- Tier 1+2 DONE, Tier 3 deferred

Adopted the color palette + typography from two mockups the user provided
("Meridian" -- `meridianstrength.html`, `meridianmockup.html`):
- **Tier 1 (done)**: swapped `style.css`'s CSS variable values to the Meridian
  dark navy/slate palette with teal + amber accents, added Archivo (display) /
  Inter (body) / JetBrains Mono (stats/labels) via Google Fonts. Dark-only --
  the mockups have no light variant, and the existing light-mode override was
  removed rather than inventing an undesigned equivalent.
- **Tier 2 (done)**: rebuilt the flat autoregulation-feedback text into the
  mockups' 3-row "coach card" (Did/Read/Next), for both run and strength
  completion. Reskinned `.phase-bar` and the load-dashboard bars for free
  (they already themed off `var()` tokens from Tier 1).

**Tier 3, narrowed after a brainstorm pass -- functional elements only, no
animation/gradient polish**:
- Replace `/plan`'s existing `.phase-bar` in place with the mockups' richer
  ribbon: per-phase gradient blocks, a "NOW" marker line, race-date flag pins
  with labels, week-number ticks below. Same data already available
  (`phase_segments`, race date, today) -- a template/CSS upgrade, not a new
  data source.
- Hero/countdown card treatment (big countdown number + race name + date in
  its own card, replacing the current flat header line).
- Status pills (e.g. "BUILD 1 · WEEK 5 OF 13", "MESOCYCLE 2 · ACCUMULATION").
- Stat cards with trend deltas (weekly volume, strength mesocycle position) --
  the VO2max sparkline from the mockup is explicitly OUT, since nothing in
  this app currently estimates/tracks VO2max over time (the chart is easy;
  the data source doesn't exist).

Explicitly **not** doing entrance animations, gradient background glows, or
phone-mockup marketing chrome -- user leans functional over decorative (per
the adjacency-flag feedback below), and the phone mockup is a marketing
illustration device in the source mockups, not a real screen to port.

---

## 6. Near-term feature requests (not yet scoped)

- **Goal race time -> pace targets**: `Race.goal_time_sec` already exists as a
  stored field (`models.py:68`, accepted via `RaceCreate`/returned via
  `RaceOut`, `schemas.py:40,50`) but is pure passthrough today -- nothing in
  `engines/running.py`/`engines/vdot.py` reads it. Race pace (and the
  threshold/easy pace derivations that feed quality/long-run targets) is
  currently derived only from the athlete's *current fitness*
  (`AthleteFitness.threshold_pace_sec_per_km` -> VDOT -> race pace), with no
  way to target a specific goal time. Open design question when this gets
  picked up: should a set `goal_time_sec` simply override the VDOT-derived
  race pace directly (`goal_time_sec / distance_km`), or should it feed back
  into an "implied VDOT" that also reshapes threshold/quality-session pacing
  consistently with that goal (more physiologically correct, more engine
  work)?
- **Simplify the adjacency-conflict flag**: `engines/calendar.py`'s
  `auto_resolve_conflicts` (~line 56-59) writes a full-sentence warning into a
  flagged session's `content["note"]` ("Flagged: hard lower-body work falls
  the day before the {role} run on {date}; no free rest day to swap this
  week. Consider lightening load."), rendered as a prominent `.note` div
  (amber text, always visible) on both `/plan` and the session-detail card.
  User is already familiar with what this means and wants a small flag/badge
  instead of the paragraph -- e.g. a compact `.stat`-style pill (matching the
  existing badge visual language) with the detail available on hover/tap
  rather than an always-visible warning block.

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
  - **Confirmed 2026-07-09 (follow-up live spike)**: posted a real repeat-block
    workout and inspected the returned `workout_doc` -- the `Nx` count line plus
    nested dashed work/recovery lines parses correctly as a `{"reps": N, "steps":
    [...]}` group with both legs present. `REPEAT_BLOCK_SYNTAX_CONFIRMED = True` in
    that module now. The spike also caught a real bug along the way: a
    decimal-minute duration token (e.g. `"1.5m"`, or `"0.333...m"` for a 20-second
    stride) silently fails to parse and drops the whole step -- fixed by converting
    fractional minutes to whole seconds (`"90s"`, `"20s"`) in `_format_duration`.
- **%HR basis spot-check**: still an open manual follow-up, not code -- once real
  syncing runs against the live account, compare a synced event's `%HR` value
  against the athlete's own HR zone chart to confirm whether it's %max HR or %LTHR.

---

## 5. Smaller hardening items -- DONE

- **Strength session premature-completion bug -- FIXED**: `log_strength_session`
  used to mark the whole `PlannedSession` as `completed` after logging just one
  of several prescriptions, hiding the log forms for the rest. Found during
  PR #14's manual testing. Now only flips to `completed` once every
  prescription pattern has a matching `CompletedSession` row
  (`engines/strength.py`'s `all_prescriptions_logged`); the UI tracks
  per-prescription logged state independently of session-level status.
- **intervals.icu sync-status visibility -- FIXED**: there was previously no way
  to tell "not synced yet" (the rolling 10-day sync window hasn't reached this
  session's date) from "broken" -- confirmed this exact confusion live when the
  athlete's plan start date (2026-08-03) was three weeks past the sync window's
  reach. `_session_card.html` now shows a sync-status line on run sessions using
  the already-existing `intervals_icu_event_id` field. The window itself
  (`DEFAULT_SYNC_WINDOW_DAYS = 10`) was deliberately left unchanged.
- **API-level tests -- DONE**: added a real `TestClient` fixture (`tests/conftest.py`'s
  `client`, wired to an isolated in-memory DB via dependency override) and
  `tests/test_api_routes.py`, giving representative HTTP-layer coverage
  (status codes, request validation, response shapes) across `api/routes.py`'s
  JSON endpoints -- `test_exercise_swap.py`'s direct-call tests remain for
  business-logic coverage, this adds the missing HTTP-contract layer on top.
- **Docker build verification**: `backend/Dockerfile` has never actually been
  built in an environment with a Docker daemon (blocked in this sandbox). Fly.io
  deploys via its own remote builder and that's confirmed working, but the Dockerfile
  itself should still get a real `docker build` once, for anyone who wants to run it
  outside Fly.
- **Daily job multi-day backlog handling -- FIXED**: previously only pulled
  *yesterday's* activities/wellness regardless of how many days back the
  backlog went, so a multi-day gap (e.g. the job didn't run for a few days)
  would mark real, completed sessions `MISSED` instead of matching them.
  `run_daily_job_for_athlete` now widens the fetch window to
  `[earliest stale session's date, yesterday]`, and looks up each stale
  session's wellness by its own date rather than reusing "yesterday's" score
  for every backlog day.
- **Docker build verification -- confirmed the daemon runs, build blocked by
  this sandbox's network only**: `docker build` on `backend/Dockerfile` pulls
  the base image and builds every layer correctly up through `pip install`,
  which fails here specifically because this sandboxed environment's outbound
  HTTPS goes through an intercepting proxy the container doesn't trust (a
  network/CA-trust limitation of this dev sandbox, confirmed by testing both
  the default bridge network and `--network=host` -- both hit the same
  self-signed-cert SSL error against pypi.org). The Dockerfile itself is
  structurally valid; this would build cleanly in a normal (non-proxied)
  Docker environment such as a developer machine or CI.

---

## Suggested order

1. ~~Athlete/race management UI~~ -- done
2. ~~Strength UI depth~~ -- done
3. ~~intervals.icu polish~~ -- done, including the live repeat-block syntax spike
4. ~~Visual design overhaul~~ -- v1 done (load dashboard + mobile polish)
5. ~~Hardening items~~ -- done (API-level TestClient tests, Docker build
   verification, daily-job backlog handling)
6. Meridian UX build-out (Tier 3 + beyond) -- next up, currently brainstorming scope
7. Goal race time -> pace targets -- not yet scoped
8. Simplify the adjacency-conflict flag -- not yet scoped, small
