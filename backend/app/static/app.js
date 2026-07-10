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

function formatPaceSec(sec) {
  if (!sec) return null;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}/km`;
}

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
    lab.className = `clab ${row.cls}`;
    lab.textContent = row.label;
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
  const body = {
    name: form.name.value,
    race_date: form.race_date.value,
    distance_km: Number(form.distance_km.value),
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

async function submitRunComplete(event, sessionId) {
  event.preventDefault();
  const form = event.target;
  const body = {
    actual_pace_sec_per_km: form.actual_pace_sec_per_km.value ? Number(form.actual_pace_sec_per_km.value) : null,
    actual_hr: form.actual_hr.value ? Number(form.actual_hr.value) : null,
  };
  try {
    const result = await postJSON(`/api/sessions/${sessionId}/complete`, body);
    const card = form.closest(".card");
    form.remove();
    const didParts = [];
    const pace = formatPaceSec(body.actual_pace_sec_per_km);
    if (pace) didParts.push(pace);
    if (body.actual_hr) didParts.push(`${body.actual_hr} bpm avg`);
    const coach = buildCoachCard([
      { label: "Did", cls: "cl-log", text: didParts.length ? didParts.join(" · ") : "Logged, no pace/HR entered" },
      { label: "Read", cls: "cl-read", text: result.note },
      { label: "Next", cls: "cl-next", text: RUN_ACTION_LABELS[result.action] || result.action },
    ]);
    card.appendChild(coach);
  } catch (e) {
    alert("Failed to log session: " + e.message);
  }
  return false;
}

async function submitStrengthLog(event, sessionId, pattern) {
  event.preventDefault();
  const form = event.target;
  const body = {
    pattern,
    sets: [
      {
        reps: Number(form.reps.value),
        weight_kg: Number(form.weight_kg.value),
        rir_actual: form.rir_actual.value ? Number(form.rir_actual.value) : null,
      },
    ],
  };
  try {
    const result = await postJSON(`/api/sessions/${sessionId}/log`, body);
    const prescription = form.closest(".prescription");
    const swapBtn = prescription.querySelector(".link-button");
    const swapPicker = prescription.querySelector(".swap-picker");
    form.remove();
    if (swapBtn) swapBtn.remove();
    if (swapPicker) swapPicker.remove();
    const badge = document.createElement("span");
    badge.className = "stat st-done";
    badge.textContent = "Logged";
    prescription.appendChild(badge);
    const target = document.getElementById(`feedback-${sessionId}`);
    const coach = buildCoachCard([
      { label: "Did", cls: "cl-log", text: result.summary },
      { label: "Read", cls: "cl-read", text: result.feedback },
      { label: "Next", cls: "cl-next", text: result.next_instruction },
    ]);
    target.appendChild(coach);
  } catch (e) {
    alert("Failed to log set: " + e.message);
  }
  return false;
}

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
