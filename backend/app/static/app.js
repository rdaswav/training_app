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
    const div = document.createElement("div");
    div.className = "feedback";
    div.textContent = `${result.action}: ${result.note}`;
    card.appendChild(div);
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
    form.remove();
    const target = document.getElementById(`feedback-${sessionId}`);
    const div = document.createElement("div");
    div.className = "feedback";
    div.textContent = `${pattern}: ${result.feedback} -- ${result.next_instruction}`;
    target.appendChild(div);
  } catch (e) {
    alert("Failed to log set: " + e.message);
  }
  return false;
}
