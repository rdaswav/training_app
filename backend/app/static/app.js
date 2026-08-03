async function postJSON(url, body, method = "POST") {
  const resp = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text);
  }
  return resp.json();
}

async function deleteRequest(url) {
  const resp = await fetch(url, { method: "DELETE" });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text);
  }
  return resp.json();
}

async function getJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text);
  }
  return resp.json();
}

function showFormStatus(id, message, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = message;
  el.className = "form-status " + (ok ? "success" : "error");
}

function paceToSeconds(str) {
  const parts = String(str).split(":").map(Number);
  if (parts.length !== 2 || parts.some((n) => Number.isNaN(n))) return null;
  return parts[0] * 60 + parts[1];
}

function goalTimeToSeconds(str) {
  if (!str) return null;
  const parts = String(str).split(":").map(Number);
  if (parts.some((n) => Number.isNaN(n))) return null;
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return null;
}

function formatPaceSec(sec) {
  if (!sec) return null;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}/km`;
}

// Direction the autoregulation loop is nudging next -- shared across run
// actions (progress/hold/soften) and strength actions (progress/hold/back_off),
// so the coach card's "Next" row reads at a glance without parsing the text.
const ACTION_DIRECTION = {
  progress: "up",
  hold: "steady",
  soften: "down",
  back_off: "down",
};
const DIRECTION_ARROW = { up: "▲", steady: "●", down: "▼" };

function buildCoachCard(rows) {
  const coach = document.createElement("div");
  coach.className = "coach";
  const hd = document.createElement("div");
  hd.className = "hd";
  hd.textContent = "Coach";
  coach.appendChild(hd);
  for (const row of rows) {
    const crow = document.createElement("div");
    crow.className = "crow";
    const lab = document.createElement("span");
    let cls = row.cls;
    let labelText = row.label;
    if (row.action) {
      const dir = ACTION_DIRECTION[row.action] || "steady";
      cls += ` dir-${dir}`;
      labelText = `${DIRECTION_ARROW[dir]} ${labelText}`;
    }
    lab.className = `clab ${cls}`;
    lab.textContent = labelText;
    const ctxt = document.createElement("span");
    ctxt.className = "ctxt";
    ctxt.textContent = row.text;
    crow.appendChild(lab);
    crow.appendChild(ctxt);
    coach.appendChild(crow);
  }
  return coach;
}

const RUN_ACTION_LABELS = {
  progress: "Progress pace next session",
  hold: "Hold your current paces",
  soften: "Ease off next time",
};

async function submitAthleteProfile(event) {
  event.preventDefault();
  const form = event.target;
  const easyPace = paceToSeconds(form.easy_pace.value);
  const thresholdPace = paceToSeconds(form.threshold_pace.value);
  if (easyPace === null || thresholdPace === null) {
    showFormStatus("athlete-status", "Enter paces as M:SS, e.g. 6:30", false);
    return false;
  }
  const body = {
    weekly_volume_km: Number(form.weekly_volume_km.value),
    easy_pace_sec_per_km: easyPace,
    threshold_pace_sec_per_km: thresholdPace,
    aerobic_hr_ceiling: Number(form.aerobic_hr_ceiling.value),
    max_hr: Number(form.max_hr.value),
    injury_flags: form.injury_flags.value
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  };
  try {
    await postJSON("/api/athlete", body, "PUT");
    showFormStatus("athlete-status", "Saved.", true);
  } catch (e) {
    showFormStatus("athlete-status", "Failed to save: " + e.message, false);
  }
  return false;
}

async function submitRaceForm(event, existingRaceId) {
  event.preventDefault();
  const form = event.target;
  let goalTimeSec = null;
  if (form.goal_time.value.trim()) {
    goalTimeSec = goalTimeToSeconds(form.goal_time.value.trim());
    if (goalTimeSec === null) {
      showFormStatus("race-status", "Enter goal time as H:MM:SS or MM:SS, e.g. 1:45:00", false);
      return false;
    }
  }
  if (existingRaceId) {
    const confirmed = window.confirm(
      "Saving deletes and regenerates every still-planned session for this race. Continue?"
    );
    if (!confirmed) return false;
  }
  const body = {
    name: form.name.value,
    race_date: form.race_date.value,
    distance_km: Number(form.distance_km.value),
    goal_time_sec: goalTimeSec,
    priority: form.priority.value,
    plan_start_date: form.plan_start_date.value || null,
  };
  try {
    if (existingRaceId) {
      await deleteRequest(`/api/races/${existingRaceId}`);
    }
    await postJSON("/api/races", body);
    showFormStatus("race-status", "Saved. Reloading...", true);
    setTimeout(() => window.location.reload(), 800);
  } catch (e) {
    showFormStatus("race-status", "Failed to save: " + e.message, false);
  }
  return false;
}

async function submitScheduleForm(event) {
  event.preventDefault();
  const form = event.target;
  const weekTemplate = {};
  for (let i = 0; i < 7; i++) {
    weekTemplate[i] = form[`day_${i}`].value;
  }
  if (!Object.values(weekTemplate).includes("run")) {
    showFormStatus("schedule-status", "Pick at least one running day.", false);
    return false;
  }
  const confirmed = window.confirm(
    "Saving regenerates every still-planned session across your races. Continue?"
  );
  if (!confirmed) return false;
  try {
    await postJSON("/api/athlete", { week_template: weekTemplate }, "PUT");
    showFormStatus("schedule-status", "Saved. Reloading...", true);
    setTimeout(() => window.location.reload(), 800);
  } catch (e) {
    showFormStatus("schedule-status", "Failed to save: " + e.message, false);
  }
  return false;
}

// ---------------------------------------------------------------------------
// Run logging: prefilled pace/HR steppers, aligned with strength's gym-mode
// entry pattern (StrengthEntry below) instead of bare number inputs.
// ---------------------------------------------------------------------------
const PACE_STEP_SEC = 5;
const HR_STEP_BPM = 1;
const DEFAULT_PACE_SEC = 360; // 6:00/km -- used only when the session has no target pace to prefill from
const DEFAULT_HR_BPM = 140;

function formatPaceVal(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

class RunEntry {
  constructor(entryEl) {
    this.el = entryEl;
    this.sessionId = entryEl.dataset.sessionId;
    this.pace = Number(entryEl.dataset.targetPace) || DEFAULT_PACE_SEC;
    this.hr = Number(entryEl.dataset.targetHr) || DEFAULT_HR_BPM;

    this.paceVal = entryEl.querySelector(".paceval");
    this.hrVal = entryEl.querySelector(".hrval");
    this.paceInput = entryEl.querySelector(".pace-edit-input");
    this.hrInput = entryEl.querySelector(".hr-edit-input");
    this.ctaBtn = entryEl.querySelector(".run-log-btn");

    entryEl.querySelectorAll(".stepbtn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const dir = Number(btn.dataset.dir);
        if (btn.dataset.runStep === "pace") {
          this.pace = Math.max(120, this.pace + dir * PACE_STEP_SEC);
        } else {
          this.hr = Math.max(0, this.hr + dir * HR_STEP_BPM);
        }
        this.paint();
      });
    });
    this._wireTapEdit(this.paceVal, this.paceInput, "pace");
    this._wireTapEdit(this.hrVal, this.hrInput, "hr");
    if (this.ctaBtn) this.ctaBtn.addEventListener("click", () => this.submit());
    this.paint();
  }

  // Steppers are fine for nudging a value close to the prefilled target, but
  // painful for reaching a far-off actual -- tapping the number swaps it for
  // a real input so an exact value can be typed directly.
  _wireTapEdit(displayEl, inputEl, kind) {
    if (!displayEl || !inputEl) return;
    const open = () => {
      inputEl.value = kind === "pace" ? formatPaceVal(this.pace) : String(this.hr);
      displayEl.style.display = "none";
      inputEl.style.display = "";
      inputEl.focus();
      inputEl.select();
    };
    const commit = () => {
      if (kind === "pace") {
        const parsed = paceToSeconds(inputEl.value);
        if (parsed !== null && parsed >= 120) this.pace = parsed;
      } else {
        const parsed = parseInt(inputEl.value, 10);
        if (!Number.isNaN(parsed) && parsed > 0) this.hr = parsed;
      }
      inputEl.style.display = "none";
      displayEl.style.display = "";
      this.paint();
    };
    displayEl.addEventListener("click", open);
    displayEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    });
    inputEl.addEventListener("blur", commit);
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        inputEl.blur();
      }
    });
  }

  paint() {
    if (this.paceVal) this.paceVal.textContent = formatPaceVal(this.pace);
    if (this.hrVal) this.hrVal.textContent = this.hr;
  }

  async submit() {
    this.ctaBtn.disabled = true;
    this.ctaBtn.textContent = "Logging...";
    const body = { actual_pace_sec_per_km: this.pace, actual_hr: this.hr };
    try {
      const result = await postJSON(`/api/sessions/${this.sessionId}/complete`, body);
      const card = this.el.closest(".card");
      this.el.remove();
      const coach = buildCoachCard([
        { label: "Did", cls: "cl-log", text: `${formatPaceSec(body.actual_pace_sec_per_km)} · ${body.actual_hr} bpm avg` },
        { label: "Read", cls: "cl-read", text: result.note },
        { label: "Next", cls: "cl-next", text: RUN_ACTION_LABELS[result.action] || result.action, action: result.action },
      ]);
      card.appendChild(coach);
    } catch (e) {
      alert("Failed to log session: " + e.message);
      this.ctaBtn.disabled = false;
      this.ctaBtn.textContent = "Log complete";
    }
  }
}

function initRunMode() {
  document.querySelectorAll(".run-entry").forEach((el) => new RunEntry(el));
}

// ---------------------------------------------------------------------------
// Strength logging: prefilled steppers, one set at a time (gym mode)
//
// Each prescription accumulates logged sets client-side as the athlete steps
// through them (matching a fixed set count -- no add/remove-row UI needed).
// Only the *last* set for a pattern triggers a real network call, and even
// that call is deferred behind the undo window: POST /api/sessions/{id}/log
// still takes the whole `sets` array in one request (evaluate_strength_log
// needs all sets together to compute average RIR / hit-reps), so nothing
// about the backend contract changed -- only when the one call fires.
// ---------------------------------------------------------------------------

const WEIGHT_STEP_KG = 2.5;
const REST_DURATION_SEC = 150; // no per-exercise rest convention in the data; a sane default, easy to tune here
const UNDO_WINDOW_MS = 6000;

function parseRepDefault(repsRange) {
  const parts = String(repsRange).split("-").map(Number);
  if (parts.length !== 2 || parts.some((n) => Number.isNaN(n))) return Number(repsRange) || 1;
  return Math.round((parts[0] + parts[1]) / 2);
}

function formatWeight(w) {
  return Number.isInteger(w) ? String(w) : w.toFixed(1);
}

function nextUpAfter(prescriptionEl) {
  const card = prescriptionEl.closest(".card");
  if (!card) return "Session complete";
  const prescriptions = Array.from(card.querySelectorAll(".prescription"));
  const idx = prescriptions.indexOf(prescriptionEl);
  for (let i = idx + 1; i < prescriptions.length; i++) {
    const p = prescriptions[i];
    if (p.querySelector(".entry")) {
      return `${p.dataset.exerciseName} &middot; set 1 of ${p.dataset.sets}<br>${p.dataset.reps} reps &middot; RIR ${p.dataset.rir}`;
    }
  }
  return "Session complete";
}

function showUndoToast(msg, onUndo) {
  const toast = document.getElementById("gymToast");
  if (!toast) return;
  toast.querySelector("span").textContent = msg;
  toast.classList.add("on");
  clearTimeout(showUndoToast._t);
  toast.querySelector("button").onclick = () => {
    toast.classList.remove("on");
    clearTimeout(showUndoToast._t);
    onUndo();
  };
  showUndoToast._t = setTimeout(() => toast.classList.remove("on"), UNDO_WINDOW_MS);
}

let gymRestInterval = null;
function startRestTimer(durationSec, nextUpHtml) {
  const rest = document.getElementById("gymRest");
  if (!rest) return;
  let left = durationSec;
  const clock = document.getElementById("gymRestClock");
  const ring = document.getElementById("gymRestRing");
  const nextUp = document.getElementById("gymRestNext");
  nextUp.innerHTML = "Up next<br><b>" + nextUpHtml + "</b>";
  const paint = () => {
    const m = Math.floor(left / 60), s = left % 60;
    clock.textContent = `${m}:${String(s).padStart(2, "0")}`;
    ring.style.width = Math.max(0, (left / durationSec) * 100) + "%";
  };
  paint();
  rest.classList.add("on");
  clearInterval(gymRestInterval);
  gymRestInterval = setInterval(() => {
    left--;
    paint();
    if (left <= 0) stopRestTimer();
  }, 1000);
  document.getElementById("gymRestSkip").onclick = stopRestTimer;
  document.getElementById("gymRestAdd30").onclick = () => {
    left += 30;
    paint();
  };
}
function stopRestTimer() {
  clearInterval(gymRestInterval);
  const rest = document.getElementById("gymRest");
  if (rest) rest.classList.remove("on");
}

function updateStickyBarFor(prescriptionEl) {
  const stick = document.getElementById("gymStick");
  if (!stick || !prescriptionEl) return;
  const entry = prescriptionEl._entry;
  const loggedCount = entry ? entry.logged.length : 0;
  const total = Number(prescriptionEl.dataset.sets);
  const target = prescriptionEl.dataset.suggested
    ? `${prescriptionEl.dataset.reps} &times; ${prescriptionEl.dataset.suggested}`
    : `${prescriptionEl.dataset.reps} reps`;
  const lastChip = prescriptionEl.dataset.lastSets
    ? `<span class="chip">Last <b>${prescriptionEl.dataset.lastSets}&times;${prescriptionEl.dataset.lastReps} @ ${prescriptionEl.dataset.lastWeight}</b></span>`
    : "";
  stick.innerHTML = `
    <div class="r1">
      <div>
        <div class="ptn">${prescriptionEl.dataset.pattern.replace(/_/g, " ")}</div>
        <h4>${prescriptionEl.dataset.exerciseName}</h4>
      </div>
      <div class="setof">set<b>${Math.min(loggedCount + 1, total)} / ${total}</b></div>
    </div>
    <div class="r2">
      <span class="chip tgt">Target <b>${target}</b></span>
      <span class="chip">RIR <b>${prescriptionEl.dataset.rir}</b></span>
      ${lastChip}
    </div>`;
  stick.classList.add("on");
}

class StrengthEntry {
  constructor(prescriptionEl) {
    this.el = prescriptionEl;
    this.el._entry = this;
    this.sessionId = prescriptionEl.dataset.sessionId;
    this.pattern = prescriptionEl.dataset.pattern;
    this.totalSets = Number(prescriptionEl.dataset.sets);
    this.rirTarget = prescriptionEl.dataset.rir;
    this.weight = prescriptionEl.dataset.suggested ? Number(prescriptionEl.dataset.suggested) : 20;
    this.reps = parseRepDefault(prescriptionEl.dataset.reps);
    this.logged = [];
    this.pendingTimeoutId = null;

    this.entryEl = prescriptionEl.querySelector(".entry");
    this.wVal = prescriptionEl.querySelector(".wval");
    this.rVal = prescriptionEl.querySelector(".rval");
    this.rirInput = prescriptionEl.querySelector(".rir-input");
    this.doneRow = prescriptionEl.querySelector(".done-row");
    this.ctaBtn = prescriptionEl.querySelector(".log-set-btn");

    prescriptionEl.querySelectorAll(".stepbtn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const dir = Number(btn.dataset.dir);
        if (btn.dataset.step === "w") {
          this.weight = Math.max(0, Math.round((this.weight + dir * WEIGHT_STEP_KG) * 10) / 10);
        } else {
          this.reps = Math.max(1, this.reps + dir);
        }
        this.paint();
      });
    });
    if (this.ctaBtn) this.ctaBtn.addEventListener("click", () => this.logCurrentSet());
    this.paint();
  }

  paint() {
    if (this.wVal) this.wVal.textContent = formatWeight(this.weight);
    if (this.rVal) this.rVal.textContent = this.reps;
    if (this.doneRow) {
      Array.from(this.doneRow.children).forEach((chip, i) => {
        const done = this.logged[i];
        chip.className = done ? "dchip" : "dchip pending";
        chip.textContent = done ? `${done.reps} × ${formatWeight(done.weight)}` : `set ${i + 1}`;
      });
    }
    if (this.ctaBtn) {
      const nextSet = this.logged.length + 1;
      if (nextSet <= this.totalSets) {
        this.ctaBtn.textContent = `✓ Log set ${nextSet}`;
        this.ctaBtn.disabled = false;
      } else {
        this.ctaBtn.textContent = "Logged";
        this.ctaBtn.disabled = true;
      }
    }
  }

  logCurrentSet() {
    if (this.logged.length >= this.totalSets) return;
    const setEntry = { reps: this.reps, weight: this.weight };
    this.logged.push(setEntry);
    this.paint();
    const isLast = this.logged.length >= this.totalSets;
    const nextUpHtml = isLast
      ? nextUpAfter(this.el)
      : `${this.el.dataset.exerciseName} &middot; set ${this.logged.length + 1} of ${this.totalSets}<br>${this.reps} × ${formatWeight(this.weight)} &middot; RIR ${this.rirTarget}`;
    showUndoToast(
      isLast ? `Session logged · ${setEntry.reps} × ${formatWeight(setEntry.weight)}` : `Set logged · ${setEntry.reps} × ${formatWeight(setEntry.weight)}`,
      () => this.undoLastSet()
    );
    startRestTimer(REST_DURATION_SEC, nextUpHtml);
    updateStickyBarFor(this.el);
    if (isLast) this.scheduleSubmit();
  }

  undoLastSet() {
    if (!this.logged.length) return;
    if (this.pendingTimeoutId) {
      clearTimeout(this.pendingTimeoutId);
      this.pendingTimeoutId = null;
    }
    this.logged.pop();
    stopRestTimer();
    this.paint();
    updateStickyBarFor(this.el);
  }

  scheduleSubmit() {
    const rirActual = this.rirInput && this.rirInput.value ? Number(this.rirInput.value) : null;
    const payload = {
      pattern: this.pattern,
      sets: this.logged.map((s) => ({ reps: s.reps, weight_kg: s.weight, rir_actual: rirActual })),
    };
    this.pendingTimeoutId = setTimeout(async () => {
      this.pendingTimeoutId = null;
      try {
        const result = await postJSON(`/api/sessions/${this.sessionId}/log`, payload);
        const badge = document.createElement("span");
        badge.className = "stat st-done";
        badge.textContent = "✓ Logged";
        this.el.querySelector(".prescription-head").appendChild(badge);
        this.entryEl.remove();
        const swapBtn = this.el.querySelector(".swap-toggle");
        const swapPicker = this.el.querySelector(".swap-picker");
        if (swapBtn) swapBtn.remove();
        if (swapPicker) swapPicker.remove();
        const target = document.getElementById(`feedback-${this.sessionId}`);
        if (target) {
          target.appendChild(
            buildCoachCard([
              { label: "Did", cls: "cl-log", text: result.summary },
              { label: "Read", cls: "cl-read", text: result.feedback },
              { label: "Next", cls: "cl-next", text: result.next_instruction, action: result.action },
            ])
          );
        }
      } catch (e) {
        alert("Failed to log set: " + e.message);
      }
    }, UNDO_WINDOW_MS);
  }
}

function initGymMode() {
  const prescriptions = document.querySelectorAll(".prescription .entry");
  if (!prescriptions.length) return;

  if (!document.getElementById("gymStick")) {
    const stick = document.createElement("div");
    stick.id = "gymStick";
    stick.className = "stick";
    document.body.insertBefore(stick, document.body.firstChild.nextSibling);
  }
  if (!document.getElementById("gymToast")) {
    const toast = document.createElement("div");
    toast.id = "gymToast";
    toast.className = "toast";
    toast.innerHTML = '<span></span><button type="button">Undo</button>';
    document.body.appendChild(toast);
  }
  if (!document.getElementById("gymRest")) {
    const rest = document.createElement("div");
    rest.id = "gymRest";
    rest.className = "rest";
    rest.innerHTML = `
      <div class="lab">Rest</div>
      <div class="clock" id="gymRestClock">2:30</div>
      <div class="ring"><i id="gymRestRing"></i></div>
      <div class="acts">
        <button type="button" id="gymRestAdd30">+30s</button>
        <button type="button" class="go" id="gymRestSkip">Skip &rarr; next set</button>
      </div>
      <div class="nextup" id="gymRestNext"></div>`;
    document.body.appendChild(rest);
  }

  document.querySelectorAll(".prescription").forEach((el) => {
    if (el.querySelector(".entry")) new StrengthEntry(el);
  });

  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio);
      if (visible.length) updateStickyBarFor(visible[0].target.closest(".prescription"));
    },
    { threshold: [0.3, 0.6, 0.9] }
  );
  document.querySelectorAll(".prescription .entry").forEach((el) => observer.observe(el));
}

document.addEventListener("DOMContentLoaded", initGymMode);
document.addEventListener("DOMContentLoaded", initRunMode);

// ---------------------------------------------------------------------------
// Cold-launch splash (today.html only): shown synchronously by an inline
// script in the template (sessionStorage-gated, so a repeat visit within the
// same tab hides it before first paint instead of flashing). This just fades
// the still-visible one out once the rest of the page has actually loaded,
// with a minimum display time so it isn't a single-frame flicker on a fast
// connection -- "fades on first content paint, not a fixed timer."
// ---------------------------------------------------------------------------
const SPLASH_MIN_DISPLAY_MS = 500;

function initSplash() {
  const splash = document.getElementById("splash");
  if (!splash || splash.style.display === "none") return;
  const shownAt = Date.now();
  window.addEventListener("load", () => {
    const wait = Math.max(0, SPLASH_MIN_DISPLAY_MS - (Date.now() - shownAt));
    setTimeout(() => {
      splash.classList.add("hide");
      setTimeout(() => splash.remove(), 450);
    }, wait);
  });
}
initSplash();

async function toggleSwap(button, sessionId, pattern) {
  const container = button.nextElementSibling;
  if (container.childElementCount > 0) {
    container.innerHTML = "";
    return;
  }
  let exercises;
  try {
    exercises = await getJSON(`/api/exercises?pattern=${encodeURIComponent(pattern)}`);
  } catch (e) {
    alert("Failed to load exercises: " + e.message);
    return;
  }
  const select = document.createElement("select");
  for (const ex of exercises) {
    const opt = document.createElement("option");
    opt.value = ex.name;
    opt.textContent = ex.name;
    select.appendChild(opt);
  }
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.textContent = "Confirm swap";
  confirmBtn.onclick = async () => {
    try {
      await postJSON(`/api/sessions/${sessionId}/exercise`, { pattern, exercise_name: select.value }, "PATCH");
      window.location.reload();
    } catch (e) {
      alert("Failed to swap: " + e.message);
    }
  };
  container.appendChild(select);
  container.appendChild(confirmBtn);
}
