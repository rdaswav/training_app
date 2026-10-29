# Training App — Build Specification

A personal running + strength periodization app. Replaces a Runna subscription and Garmin's built-in plans with a flexible, race-driven engine that prioritises strength correctly around running load. Single user (you). Built in Claude Code.

---

## 1. Goals

- Generate a progressive running block from a **race date + distance + current fitness**, shaped per distance (5K / 10K / half / marathon) following each distance's periodization best practice. Originally built half-marathon-only; see "Race profiles" in section 5.
- Run and strength live in **one unified calendar**, with strength load automatically modulated by race proximity.
- Plans are **rule-based (deterministic)**; an LLM (Claude) edits them as life/goals change.
- View today's session on **phone**; plan and visualise on **desktop**.
- Structured run workouts push to a **Garmin Fenix 8** and execute on-watch.

Non-goals for v1: multi-user, social features, Strava integration, an in-app LLM chat widget (you'll edit via Claude directly).

---

## 2. Architecture

**Key decision: don't rebuild the plumbing. intervals.icu is the backbone; we build the brain.**

```
┌─────────────────────────────┐
│   OUR APP (the "brain")     │
│  - periodization engine     │
│  - strength engine (RP)     │
│  - unified calendar logic   │
│  - LLM edit interface       │
└──────────────┬──────────────┘
               │  intervals.icu open API (read + write)
┌──────────────▼──────────────┐
│        intervals.icu        │
│  - stores history + wellness│
│  - HR/pace zones            │
│  - calendar of planned wkts │
│  - SANCTIONED Garmin sync   │
└──────────────┬──────────────┘
               │  official Garmin Connect link
┌──────────────▼──────────────┐
│      Garmin Fenix 8         │
│  executes structured wkts   │
└─────────────────────────────┘
```

- **Read** from intervals.icu: completed activities, paces, HR, wellness, fitness trend, zones.
- **Write** to intervals.icu: planned structured run workouts onto the calendar → intervals.icu pushes them to the Fenix automatically the morning of the session.
- Strength lives **only in our app** (Garmin blocks third-party strength push; that's fine — strength doesn't need to be on the watch).
- We never touch Garmin's private API directly. That's intervals.icu's problem, and it's a sanctioned link.

> Build note: confirm exact intervals.icu API endpoints, auth (API key + athlete ID), and the planned-workout / calendar-event schema against the current API docs at the start of the build. The logic below is ours and fully specified; the intervals.icu wire format is the one external unknown to pin down first.

---

## 3. Tech stack

- **Backend:** Python (FastAPI). Matches the fitness ecosystem and keeps the periodization logic testable in isolation.
- **Persistence:** SQLite to start (single user, simple). Postgres only if it ever outgrows that.
- **Frontend:** lightweight responsive web app (one codebase, works on phone + desktop). React or plain templated HTML + a charting lib — builder's choice; keep it simple.
- **Hosting:** small always-on cloud instance (needed so scheduled workout writes to intervals.icu fire reliably). A single cheap VM or container is enough.
- **Scheduler:** a daily job that (a) pulls yesterday's completed sessions from intervals.icu, (b) runs autoregulation, (c) writes/refreshes the next ~7–10 days of planned workouts.

---

## 4. Data model (core entities)

- **Athlete profile** — current paces/zones (synced from intervals.icu), HR zones, VO2max, available days (fixed: 3 run, 3 strength, 1 rest), injury flags.
- **Race** — date, distance, goal time, priority (A/B/tune-up).
- **Macrocycle** — spans "today → race", holds the phase sequence.
- **Phase** — name, week range, focus (see §5).
- **Mesocycle** — strength block (4–6 wks + deload) nested inside the macrocycle.
- **PlannedSession** — date, type (run/strength), structured content, targets, status (planned/completed/missed).
- **CompletedSession** — pulled back from intervals.icu (runs) or logged in-app (strength); actual vs prescribed.
- **ExerciseLibrary** — exercises tagged by **movement pattern** (for substitution).

---

## 5. Running periodization engine

### Inputs
Race date, distance, and current fitness derived from intervals.icu (recent weekly volume, recent easy/threshold paces, VO2max/fitness trend).

### Race profiles
The race's distance snaps to a profile (`backend/app/engines/race_profiles.py`): ≤7.5 km → 5K, ≤15 km → 10K, ≤30 km → half, longer → marathon. No race type is stored, so a custom distance just picks the nearest profile.

| | 5K | 10K | Half | Marathon |
|---|---|---|---|---|
| Standard block | 10 wk | 12 wk | 16 wk | 18 wk |
| Taper (volume vs last up-week) | 1 wk: 60% | 2 wk: 80%, 55% | 2 wk: 70%, 45% | 3 wk: 80%, 65%, 45% |
| Re-base / Build 1 share of ramp | 25% / 40% | 30% / 35% | 30% / 35% | 30% / 35% |
| Long run | 25% of week, ≤16 km | 28%, ≤19 km | 35%, no cap | 35%, ≤34 km |
| Strides on an easy run | yes | yes | no | no |
| Strength flavour | power (plyometrics) | power | standard | endurance (Build 1 capped at MAV) |

Taper lengths follow the Mujika/Bosquet taper meta-analysis (8–14 days, cut volume 40–60%, keep intensity): shorter for 5K, longer for the marathon. Paces come from Daniels' VDOT, which is derived from threshold pace: easy, T, I (97.5% VO2max), R (≈ mile race pace) and race pace. A goal time only overrides race pace.

### Block construction
Weeks-to-race determines the shape:
- If the profile's taper plus **≥ 4 weeks** fit: full build. If it's **longer than the profile's standard block**, the extra weeks become a Base phase before the ramp starts.
- Otherwise: the block is Build 2 + Taper only, and the taper is protected.
- The block ends on race day: a race session on the date itself, an easy shakeout with strides the day before, and nothing after.

Phase sequence (maps to what we already discussed):

| Phase | Focus | Quality session | Long run |
|-------|-------|-----------------|----------|
| **Re-base** | Aerobic rebuild | strides / short hills | steady, building duration |
| **Build 1** | Threshold intro | tempo / cruise intervals | steady + light progression |
| **Build 2** | Race-specific | threshold + race-pace reps | long run w/ race-pace segments |
| **Taper** | Sharpen | short race-pace touches | volume cut, freshness |

That table is the half profile. With one quality slot a week, the other profiles alternate two variants week to week so both VO2max/economy and threshold work get covered:

| Phase | 5K | 10K | Marathon |
|---|---|---|---|
| Base / Re-base | strides ↔ 8×10s hill sprints | strides | strides |
| Build 1 | 8×400 @R ↔ 3×1.6 km @T | 4×1.6 km @T ↔ 5×1 km @I | 4×1.6 km @T ↔ 2×4 km @T |
| Build 2 | 5×1 km @I ↔ 6×800 @5K pace | 4×1.6 km @10K pace ↔ 2×2 km @T + 3×800 @I | 3×2 km @T ↔ 8 km @M |
| Taper | 4×400 @race pace | 3×1 km @race pace | 3×1.6 km @M |

Build 2 long runs: 5K steady; 10K a 3 km threshold finish; half a race-pace segment ≤4 km; marathon a marathon-pace segment ≤16 km (40%).

### Weekly structure (3 runs)
1. **Easy/recovery** — prescribed by pace, but **conservative pace + HR ceiling guardrail** (keep sub-aerobic-threshold; solves the Z4 problem). Fenix 8 displays both.
2. **Quality** — evolves by phase per table above.
3. **Long run** — duration progresses; later long runs embed race-pace segments.

### Volume & progression
- Weekly volume ramps ~**8–10%/week** with a **down week every 3rd–4th week**.
- Last **2 weeks taper** (progressive volume cut, intensity retained but trimmed).
- **Pace targets** derived from current fitness (VDOT / critical-pace model off intervals.icu data), recalculated as fitness improves.

### Autoregulation (runs)
Daily job compares completed vs prescribed:
- Missed sessions → don't "make up"; re-flow the week.
- Paces consistently beaten at target HR → nudge fitness estimate up, recompute paces.
- HR drift / paces missed / poor wellness → hold volume, soften next quality session.

---

## 6. Strength engine (RP-style)

### Periodization vs autoregulation — both, as two layers
- **Periodization** = the scheduled mesocycle skeleton: volume landmarks ramping across the block, deload timing, and race-proximity down-modulation. Set in advance.
- **Autoregulation** = the per-session adjustment driven by your logged sets. Responsive.

The engine schedules the skeleton; your logs fill it in.

### Template (consistent sessions, by movement pattern)
- **Mon — Upper:** horizontal push, vertical pull, horizontal pull, shoulder/accessory, core.
- **Wed — Lower:** squat pattern, hinge, single-leg, core.
- **Fri — Hybrid:** unilateral / carry / posterior-chain + run-supportive core & injury-prevention.

Each slot is a **movement pattern**, not a fixed exercise.

### Substitution
- Exercises are tagged by pattern. Swap freely **within a pattern** (preference) — stimulus preserved.
- **Injury flag** restricts patterns/loading (e.g. flag "lower back" → excludes axial-loaded squat/hinge, offers supported/machine/single-leg alternatives automatically).

### Volume & intensity (RP model)
- Volume landmarks per pattern: **MEV → MAV → MRV** across the mesocycle, then **deload to MV**.
- Intensity via **RIR progression**: start block ~**3 RIR**, progress toward **1 RIR** by block end, then deload.
- Compounds stay in your **3–5 rep strength range**; accessories higher-rep.

### Autoregulation from logs
You log `sets x reps x weight` (e.g. `3x5x50`). Each session the engine returns:
1. **Clean summary** of what you did.
2. **Feedback** — performance + fatigue read (hit target reps at target RIR?).
3. **Next instruction** — progress load/reps (targets hit, RIR in range) / hold (borderline) / back off (missed reps or excess fatigue). No grinding, no unnecessary failure.

### Race-proximity modulation (the key integration)
Strength mesocycles **nest inside the running macrocycle**. As the race nears, strength yields the fatigue budget to running:

| Run phase | Strength behaviour |
|-----------|--------------------|
| Re-base / Build 1 | Full accumulation — normal RP mesocycle, progress strength |
| Build 2 | **Maintenance (MEV)** — hold load, cut volume, minimise soreness |
| Taper | **Minimal** — movement pattern only, strip fatigue, no new stimulus |

### Strength by race profile
- **Power (5K/10K):** a plyometric block goes first on Lower / Full Body days, while you're fresh: pogo hops, box jumps, bounds and so on. It's logged as ground contacts per set (5–8), with no load and no RIR. Sets go 2→4 through accumulation, 2 in maintenance and deloads, and 1 as a race-week primer. Plyometric days count as hard lower-body for the guardrail. This follows the evidence that heavy strength plus plyometrics improves running economy, which matters most over shorter races.
- **Endurance (marathon):** Build 1 strength ramps sets to **MAV, not MRV**, while weekly mileage peaks. The marathon's 3-week taper holds maintenance in its first week, then goes minimal.
- **Standard (half):** unchanged.

---

## 7. Unified calendar & load balancing

- One calendar, both modalities. Fixed week: Mon strength / Tue run / Wed strength / Thu run / Fri strength / Sat long run / Sun rest (adjustable).
- Guardrail: avoid hard lower-body strength the day before a key run; the scheduler checks adjacency and flags/auto-shuffles conflicts.
- Weekly dashboard: run volume, strength tonnage, combined load trend.

---

## 8. LLM editing layer (v1: via Claude directly)

No embedded widget yet. The app exposes plan state (and accepts edits) in a clean, structured form so you can converse with Claude to alter it: "push the race two weeks", "I tweaked my knee — no lunges for 10 days", "swap Friday to a run this week". Claude reads current state, proposes the rule-consistent change, and the app applies it (regenerating downstream weeks). Keep an **export/apply** path (structured JSON in/out) so this is clean.

---

## 9. UI

- **Phone (default view):** today's session — the workout, targets, and (strength) the log interface + coaching response.
- **Desktop (planning view):** full calendar, phase timeline, drag context, load charts, race countdown.

---

## 10. Build sequence (MVP → full)

1. **Spike the intervals.icu API** — auth, read one activity, write one planned structured workout, confirm it lands on the Fenix. De-risks the only external unknown first.
2. **Data model + persistence.**
3. **Running periodization engine** — race date → full block of planned sessions.
4. **Write runs to intervals.icu calendar** + verify Garmin push end-to-end.
5. **Strength engine** — template, pattern library + substitution, RP mesocycle scheduling.
6. **Strength logging + autoregulation loop** (the `3x5x50` coaching response).
7. **Race-proximity strength modulation** (nest strength in run macrocycle).
8. **Daily autoregulation job** (pull completed, adjust, refresh next 7–10 days).
9. **Web UI** — phone today-view, then desktop planning view.
10. **LLM edit path** — structured export/apply.

MVP = steps 1–4 + 9a (a race-driven run plan that pushes to your watch and shows today on your phone). Strength layers on next.

---

## 11. Confirm during build

- Exact intervals.icu API endpoints + planned-workout/calendar schema.
- Whether intervals.icu passes both a pace target **and** an HR ceiling into a single Fenix 8 step (fallback: pace target + separate HR alert).
- VDOT/critical-pace source: compute ourselves from intervals.icu data, or read any pace zones intervals.icu already derives.
