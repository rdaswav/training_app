# UI/UX red team: training_app

Reviewed at commit HEAD, 2026-07-27. Files: `backend/app/templates/*.html`, `backend/app/static/style.css` (494 lines), `static/app.js`.

The diagnosis in one line: **the CSS has a good design system and the templates don't use it.** Tokens, type scale, and card styles are all there, but `.card` is applied to seven different semantic levels, there's no base template, and the one screen that matters most (logging sets, in a gym, tired) was built as a web form rather than a gym interface.

---

## P0 — the gym is the hostile environment, and the app isn't built for it

Context that should drive every decision on this screen: you are standing, hot, breathing hard, holding the phone one-handed, screen has sweat on it, you have a 60–90 second rest window, and you look at the phone maybe four times per exercise from a different body position each time.

### 1. Set logging needs 9 typed inputs per exercise

`_session_card.html` lines 61–72. Each set row is three `<input type="number">` fields (reps, kg, RIR). Three sets = **nine numeric keyboard entries per exercise**, four exercises = 36. That is the single biggest problem in the app.

Also in that block:

- **Placeholder-as-label.** `placeholder="reps"`, `placeholder="kg"`, `placeholder="RIR"` with no `<label>`. The label vanishes the moment you type, so on the second set you're looking at three anonymous boxes containing `5`, `72.5`, `2`. Classic accessibility failure, and worse when tired.
- **`width: 4.5rem`** (style.css) on those inputs — 72px boxes side by side.
- **`type="number"`** instead of `inputmode="decimal"` — you get spinner arrows and a keyboard that isn't optimised for the entry.
- **The remove button is 1.8rem = 28.8px** (`.set-remove`), well under the 44px minimum, it's destructive, and there is no undo.

**Fix:** the target is already known (`suggested_loads`, `p.sets`, `p.reps`, `p.rir` are all in context). Prefill everything, show one big stepper, make logging a set a single tap. Typing becomes the exception, not the default. See `gym-mode.html`.

### 2. No rest timer

Nothing in the codebase. Logging a set is the natural trigger and the rest window is the only time you're actually looking at the phone. Every competitor has this. Absent.

### 3. Nothing is pinned

Scroll to the fourth exercise and you've lost the header, the session name, and your target. Add a sticky bar carrying exercise + set number + target load.

### 4. Actions are underlined text links

`.link-button` renders "Swap exercise" and "+ Add set" as `background: none; border: none; text-decoration: underline`, font-size 0.8rem. These are physical actions taken mid-session. They need to be chips or buttons with real hit areas.

---

## P0 — contrast and type size fail WCAG, and the gym makes it worse

Measured against `--ink: #0e1522`:

| Token | Used for | Ratio | Verdict |
|---|---|---|---|
| `--dim` `#5e7085` | `.load-col-label`, `.wklabels`, `.countdown-card .date`, `.metrics-detail summary`, `.flag` | **3.6:1** | Fails AA (needs 4.5:1) |
| `--muted` `#8da0b6` | body secondary text | 6.8:1 | Passes |

And `button { background: var(--accent); color: white }` — white on teal `#35c4ae` is **2.2:1**. That's your primary action button. Ink-dark text on teal is 8.5:1; switch it.

Separately, `0.6rem` (9.6px) is used in `.wk`, `.wklabels`, `.load-col-label`, `.weekgrid-label`, `.flag`, and 0.65rem in several more. Sub-12px is unreadable at arm's length in bright light. Floor the scale at 12px and let the small stuff be 12–13px.

---

## P1 — `.card` means nothing, which is why About looks messy

You asked specifically about About. It isn't a content problem, it's a hierarchy problem. `about.html` is **eight sibling `.card`s in a flat stack**, every title an `<h3>`, and it mixes four unrelated content types with no ordering principle:

1. Live operational state ("Right now")
2. Raw engine diagnostics (VDOT, threshold/easy pace, MEV/MAV/MRV table, e1RM table)
3. Whole-plan charts (run volume, tonnage, week grid)
4. Design-rationale prose ("Two systems, one plan", "Why the plan moves")

Then it **nests cards inside cards**: line 150 onward puts `.card` inside `.stats` inside `.card` for the phases and strength modes. Same background, same border, same radius, three levels deep. Depth stops reading as depth.

Compounding it:

- Headings are conversational, not scannable: "Under the hood -- this athlete's live numbers", "How the two clocks line up".
- `--` double hyphens throughout instead of proper punctuation (about.html and README both).
- Two `landmark-table`s sitting inside a grid column, which is why it reads as a spreadsheet.

**Fix — split by audience, cap at two content types per page:**

- **Live numbers → Plan or Settings.** VDOT, paces, e1RM, landmarks are operational readouts you check, not explanation.
- **Charts → Plan.** They're already partly duplicated there.
- **About keeps only: what the two engines do, the phase strip, the guardrail note.** Everything deeper goes in a collapsed `<details>` or stays in the README where it belongs.

Then introduce surface tiers so nesting is legible: `.panel` (primary, raised, border), `.subpanel` (inset, no border, darker), `.inline-stat` (no chrome). See `about-redesign.html`.

---

## P1 — tables and links where cards were designed

Confirmed, matching your read:

- `table.calendar` with `min-width: 480px` on mobile → you horizontally scroll a data table to see your week. The plan grid should be day cards that stack.
- `table.landmark-table` ×2 on About.
- `table.review-sessions` with `white-space: nowrap` — another horizontal scroll.
- `.metrics-json` dumps raw JSON into a 22rem scroll box in the UI.

Tables are right for dense comparison on desktop. They're wrong as the primary mobile layout for a 7-item week.

---

## P1 — navigation

`_nav.html` is six text links, mono, 0.9rem, `margin-right: 1rem`, `padding: 0.25rem 0` — **no horizontal padding, no min-height**. On mobile that's a wrapping row of small grey text with sub-30px touch targets, at the top of the screen, furthest from your thumb.

Six equal top-level destinations is also flat. Real hierarchy: Today, Plan, History are daily; Reviews, Settings, About are occasional.

**Fix:** bottom tab bar on mobile, three or four items, 48px targets, icon + label, secondary items behind an overflow or moved into Settings. Keep the top nav on desktop.

---

## P2 — colour semantics collide

- Teal is `--accent`, `--good`, nav active state, **and** `.session-easy`.
- Amber is `--warn`, the "now" marker, `.st-flag` conflict, **and** `.session-hard`.

So amber simultaneously means "quality run" and "something's wrong". Split them: keep amber for intensity/effort, move warnings to `--red`, and use violet consistently for strength.

Also `.stat.st-done` uses `rgba(76, 175, 125, 0.14)` — a green that exists nowhere in the token set. Leftover; should be `--teal`.

And `.form-status.error` uses `#d9534f` (Bootstrap red) while the palette defines `--red: #ff6b6b`.

---

## P2 — structural cause of the drift

**There is no `base.html`.** All seven templates repeat the same eight-line `<head>` with the font preconnects and stylesheet link. Nothing enforces consistency, so each page has drifted independently. Extract a base template with blocks and the styling problems stop recurring.

Also missing: `prefers-reduced-motion` handling, `:focus-visible` states anywhere, and any loading state (`.empty` italic text is the only non-happy path).

---

## Suggestions beyond fixing what's there

1. **An explicit gym mode.** Not a page — a mode. Full-bleed, one exercise at a time, sticky target, huge steppers, rest timer, swipe or big button to advance. Screen-wake-lock so it doesn't sleep between sets.
2. **Never show an empty field where a target exists.** Prefill from `suggested_loads` and last session. Logging becomes confirm-or-adjust.
3. **Undo, not confirm.** Destructive actions in the gym should be reversible for ~10 seconds rather than gated behind a dialogue.
4. **Show last week inline.** "Last: 3×5 @ 70" next to today's target is the single most useful number and it's already in the DB.
5. **One primary action per screen, in the thumb zone.** Bottom, full width, 56px.
6. **Post-set coaching stays where it is conceptually** — the `.coach` component with DID/READ/NEXT is genuinely the best-designed thing in the CSS. Reuse that pattern more; it's the app's voice.
7. **Progressive disclosure everywhere on About/Reviews.** Summary visible, detail in `<details>`.

---

## Priority order for Claude Code

1. `base.html` extraction + type-scale floor at 12px + fix button contrast + `--dim` usage audit.
2. Rebuild the strength logging component: prefilled steppers, labels, 44px targets, sticky target bar, rest timer, undo.
3. Split About; introduce `.panel` / `.subpanel` tiers; kill nested `.card`.
4. Replace `table.calendar` with stacking day cards on mobile.
5. Bottom tab bar on mobile; demote Reviews/Settings/About.
6. Colour semantics cleanup; remove off-palette values.
