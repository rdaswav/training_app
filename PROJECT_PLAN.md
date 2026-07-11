# Project Plan: MVP -> Full

Status snapshot and history of prioritized work beyond the original build spec. See
`SPEC.md` for the original build spec and `README.md` for what's already
built/confirmed. Everything this file once tracked as "left to do" is now done --
it's kept as the historical record of what shipped and why, in the order it shipped.

## Where things stand

All ten of the spec's build-sequence steps (section 10) are implemented and tested
(175 passing tests): data model, running periodization engine, RP-style strength
engine, exercise library + substitution, unified calendar with the adjacency
guardrail, run/strength autoregulation, a confirmed-working intervals.icu
integration, the daily autoregulation job, a FastAPI backend, and a web UI covering
today/plan/settings/history/session-detail views.

Every item below (1-8) is done, and every issue raised after that -- filed and
tracked directly as GitHub issues (#21-#35) rather than in this file -- is closed
too (see section 11 near the end). This file is now a complete record of what
shipped, not a queue of what's left; there is currently nothing open.

**Deployment**: self-hosted via Docker on a home NAS. The original Fly.io deployment
(`training-app-v1.fly.dev`) was decommissioned once the self-hosted instance was
confirmed working -- see `README.md`'s "Hosting" section.

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
animation/gradient polish -- DONE**:
- Replaced `/plan`'s `.phase-bar` in place with the richer ribbon: per-phase
  blocks (date range + focus text, active phase highlighted), a week-tick bar
  with a "NOW" marker, and race-date flag pins (red "target" for priority-A
  races, muted "tune-up" styling for others) -- all races within the
  macrocycle's date range get a flag, not just the nearest one. New pure
  module `engines/dashboard_summary.py` (`active_phase`, `global_week_index`,
  `week_ticks`, `timeline_pct`, `race_flags`) computes all of this from data
  already available (`phase_segments`, `Race` rows, today) -- no new data
  source needed.
- Hero/countdown card: race name, distance, goal time (when set), current
  phase's focus text, and a big days-to-race countdown card, replacing the
  old flat header line.
- Status pills: current phase + week number ("RE-BASE · WEEK 1 OF 14"),
  race priority.
- Stat cards: the existing weekly-load chart gained a "▲/▼ N% vs last week"
  delta on the run-volume title; a new "Strength Mesocycle" card shows block
  position, mode (accumulate/maintenance/minimal), and effort target (RIR) --
  computed via `dashboard_summary.strength_mesocycle_status`, which reuses
  `engines/strength.py`'s own `prescribe()` as the single source of truth for
  the RIR/note text rather than re-deriving that math. The VO2max sparkline
  from the mockup stayed out, since nothing in this app estimates/tracks
  VO2max over time (the chart is easy; the data source doesn't exist).

Did not do entrance animations, gradient background glows, or phone-mockup
marketing chrome -- user leans functional over decorative (per the
adjacency-flag feedback below), and the phone mockup is a marketing
illustration device in the source mockups, not a real screen to port.

---

## 6. Near-term feature requests -- DONE

- ~~**Goal race time -> pace targets**~~ -- done. A set `Race.goal_time_sec`
  now overrides `AthleteFitness.race_pace_sec_per_km` directly
  (`goal_time_sec / race_distance_km`), affecting only race-pace segments
  (Build 2's race-pace reps, Taper's race-pace touch). Threshold/easy paces
  stay derived from the athlete's actual current fitness via the VDOT model,
  untouched by the goal time -- a deliberate choice confirmed with the user to
  preserve autoregulation (not prescribing paces harder than current fitness
  has earned). Settable via the `/settings` race-edit form ("Goal time
  (H:MM:SS)", optional).
- ~~**Simplify the adjacency-conflict flag**~~ -- done. `UnifiedSession` now
  carries a `flagged: bool` field; an unresolved adjacency conflict renders as
  a compact `.stat.st-flag` pill ("Conflict") with the full detail sentence
  available via a hover tooltip, instead of an always-visible `.note`
  paragraph, on both `/plan` and the session-detail card. Auto-shuffled
  conflicts (the resolved case) are unaffected -- still a plain informational
  note.

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
- **Docker build verification -- since fully confirmed on real hardware**: at the time
  this bullet was first written, `docker build` couldn't complete in this dev sandbox
  specifically (an intercepting proxy without a trusted CA broke `pip install`'s HTTPS
  calls) -- the Dockerfile itself was already structurally valid. It has since been
  built and run for real on the athlete's home NAS (self-hosted deployment, see
  `README.md`'s "Hosting" section) -- fully confirmed working, no longer a caveat.

---

## 9. Self-hosting: HTTP Basic Auth -- DONE

Once the app moved from Fly.io to self-hosted on a home NAS, there was no access
control in front of it at all. Added `app/auth_middleware.py`'s `BasicAuthMiddleware`,
gating every route (pages, API, static assets alike) behind HTTP Basic Auth --
opt-in via `AUTH_USERNAME`/`AUTH_PASSWORD` env vars, unset by default so existing
tests/local dev are unaffected. Documented in `README.md`'s Configuration section,
including the caveat that Basic Auth is unencrypted in transit (fine on a LAN, put
HTTPS in front before exposing beyond one).

---

## 10. Fix duplicate intervals.icu events on plan regeneration -- DONE

Reported live: shifting a race's plan start date by a single day created duplicate
workouts on the intervals.icu calendar. Root cause: two bulk-delete call sites
(`plan_service.py`'s regeneration, and `delete_race`) removed `PlannedSession` rows --
including their `intervals_icu_event_id` -- without ever telling intervals.icu to
delete the corresponding event; the regenerated sessions then synced as brand-new
events, leaving the originals orphaned. Fixed with `intervals_sync.py`'s new
`delete_synced_events()`, called with the about-to-be-removed rows in hand before
each bulk delete.

---

## 11. Post-roadmap bug/feature batch (GitHub issues #21-#35) -- DONE

A code-review pass surfaced 14 issues (bugs, enhancements, design questions, and
features), logged directly to GitHub rather than this file. Every design question was
resolved with the user before its dependent work was built; every issue is closed.

**Autoregulation correctness (tier 1 -- small, high-impact fixes):**
- **#21**: easy/long run "progress"/"soften" no longer bleeds pace adjustments into
  `threshold_pace_sec_per_km`, and quality-session results no longer bleed into
  `easy_pace_sec_per_km` -- each session role only ever moves its own pace.
- **#35**: the daily job no longer hardcodes `hit_reps=True` for quality-session
  progression. Automated matching can still hold/soften a quality session, but never
  auto-progresses one without a manual confirmation (decided direction, closed #26).
- **#22**: a transient intervals.icu activity-fetch failure now leaves stale RUN
  sessions `PLANNED` (retried on the next successful run) instead of marking a
  phantom `MISSED`. Strength sessions (never dependent on that fetch) are unaffected.

**Autoregulation guardrails (tier 2):**
- **#23**: "soften" on easy/long runs now actually eases the prescribed pace (`+5s/km`)
  instead of behaving identically to "hold" (`0` adjustment).
- **#24**: a hard cap (`MAX_PACE_DRIFT_SEC_PER_KM = 20`) bounds cumulative
  autoregulated drift from the athlete's profile-set baseline pace, in both directions
  (decided direction, closed #25 -- a hard cap, not decay-to-baseline or a per-week
  rate cap). Manually editing paces in Settings re-baselines the clamp. Required a new
  `AthleteProfile.easy_pace_baseline_sec_per_km`/`threshold_pace_baseline_sec_per_km`
  pair of columns, added via `db.py`'s additive-migration path (see `README.md`).

**Observability and matching (tier 3):**
- **#33**: the daily job now records `last_job_run_at`/`last_job_error` on the
  athlete profile, surfaced as a "Daily job health" card in Settings.
- **#34**: same-day activity matching now prefers the activity closest in distance to
  the planned session instead of last-write-wins by fetch order (decided direction,
  closed #27).

**Strength (tier 4 -- larger features, built last so the data pipeline above was
trustworthy first):**
- **#31**: the strength mesocycle's deload week used to tick on its own independent
  5-week clock regardless of the running plan. `engines/strength.py`'s new
  `best_mesocycle_offset` nudges the deload week toward the running plan's own
  down-weeks/taper without forcing exact alignment (decided direction, closed #32 --
  the two clocks have different periods and can't coincide every cycle). The chosen
  offset is persisted on a new `Macrocycle.mesocycle_start_week` column so the
  `/plan` dashboard's mesocycle status can never drift from what was actually used to
  generate the persisted strength sessions.
- **#28**: a real strength load/progression model. `engines/strength.py` adds
  `estimate_e1rm` (Epley formula, decided in closed #29) and `prescribe_next_load`,
  tracked per movement pattern rather than per specific exercise (also decided in
  #29, since exercise selection is flexible/self-directed). `evaluate_strength_log`
  now embeds a concrete kg target into "progress"/"back_off" feedback instead of
  prose-only guidance, and the log form itself suggests/prefills a weight before the
  athlete starts a session, based on their most recent log for that pattern. #30
  (weight/reps/RIR already captured on every log) confirmed no new data-capture work
  was needed first.

Every dependent design question above was resolved via `AskUserQuestion` before its
implementation started, following the same pattern as items 6-8's goal-time and
adjacency-flag decisions.

---

## Suggested order

1. ~~Athlete/race management UI~~ -- done
2. ~~Strength UI depth~~ -- done
3. ~~intervals.icu polish~~ -- done, including the live repeat-block syntax spike
4. ~~Visual design overhaul~~ -- v1 done (load dashboard + mobile polish)
5. ~~Hardening items~~ -- done (API-level TestClient tests, Docker build
   verification, daily-job backlog handling)
6. ~~Meridian UX build-out~~ -- done (phase ribbon, hero/countdown, status
   pills, stat cards -- animations/gradients/phone-mockup chrome deliberately
   skipped)
7. ~~Goal race time -> pace targets~~ -- done (race-pace segments only, per
   user decision)
8. ~~Simplify the adjacency-conflict flag~~ -- done (compact badge + tooltip)
9. ~~Self-hosting: HTTP Basic Auth~~ -- done
10. ~~Fix duplicate intervals.icu events on plan regeneration~~ -- done
11. ~~Post-roadmap bug/feature batch (GitHub issues #21-#35)~~ -- done, including
    #31 (strength mesocycle/running-phase coupling) and #28 (strength
    load/progression model)

**Nothing is currently open.** Further work would start as a new GitHub issue or a
new section here, not a resumption of this list.
