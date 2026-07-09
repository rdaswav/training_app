async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(text);
  }
  return resp.json();
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
