// Profile cards: persona, facts, pay, Haiku summary and Haiku quick replies.
"use strict";

const DIVISIONS = ["The Forge", "The Watchtower", "The Scriptorium", "The Counting House", "The Observatory",
  "The Atelier", "The War Room", "The Common Room"];
const PROFILE = { open: true, summaries: {}, replies: {}, busy: {} };

function personaCard(p, seed, kind, extra) {
  if (!p) return "";
  return `<div class="pf-top"><div class="pf-portrait">${Sprites.svg(seed, kind, 46)}</div>` +
    `<div class="pf-who"><h3>${esc(p.name)}</h3><span class="pf-title">✦ ${esc(p.title)}</span>` +
    `<p class="pf-line">${esc(p.age)} · ${esc(p.quirk)}. ${esc(p.home)}</p></div></div>` +
    `<p class="pf-bio">${esc(p.bio)}</p><p class="pf-motto">“${esc(p.motto)}”</p>` + (extra || "");
}

function facts(rows) {
  return `<dl class="facts">` + rows.filter((r) => r[1] !== undefined && r[1] !== null && r[1] !== "")
    .map(([k, v, raw]) => `<dt>${esc(k)}</dt><dd>${raw ? v : esc(v)}</dd>`).join("") + `</dl>`;
}

function divisionPicker(d) {
  if (d.staff) return esc(d.staff.division);
  const known = new Set(DIVISIONS.concat(((UI.state && UI.state.team) || {}).divisions || []));
  if (d.division) known.add(d.division);
  return `<select class="pf-division" data-sid="${esc(d.id)}" aria-label="Division">` +
    `<option value="">Guild Hall</option>` +
    [...known].map((x) => `<option${x === d.division ? " selected" : ""}>${esc(x)}</option>`).join("") + `</select>`;
}

const MODEL_NAMES = { opus: "Opus", sonnet: "Sonnet", haiku: "Haiku" };

function modelPicker(d) {
  const staffModel = d.staff && memberById(d.staff.id) ? memberById(d.staff.id).model : "";
  const current = d.staff ? staffModel : d.model_pref;
  const keep = d.staff ? "" : `<option value="">Same as now${d.model ? " (" + esc(d.model) + ")" : ""}</option>`;
  return `<select class="pf-model" data-sid="${esc(d.id)}"${d.staff ? ` data-staff="${esc(d.staff.name)}"` : ""} aria-label="Model">` + keep +
    ["opus", "sonnet", "haiku"].map((m) => `<option value="${m}"${m === current ? " selected" : ""}>${MODEL_NAMES[m]}</option>`).join("") +
    `</select>` + (d.model && (d.staff || d.model_pref) ? `<small class="pf-now">now: ${esc(d.model)}</small>` : "");
}

Hooks.sheetTop = (d) => {
  if (!d.persona) return "";
  const pay = d.pay || {};
  const summary = PROFILE.summaries[d.id];
  const rows = [
    ["Quest", d.name], ["Started as", d.started_as], ["Division", divisionPicker(d), true], ["Project", d.project],
    ["Branch", d.branch], ["Model", modelPicker(d), true], ["Pay today", money(pay.today)], ["Pay 7 days", money(pay.week)],
    ["Pay 30 days", money(pay.month)], ["Last activity", ago(d.active)], ["Last tool", d.last_tool], ["Folder", d.folder],
  ];
  const last = d.messages && [...d.messages].reverse().find((m) => m.role === "assistant" && m.text);
  return `<details class="profile"${PROFILE.open ? " open" : ""}><summary>Profile</summary>` +
    personaCard(d.persona, d.staff ? d.staff.seed : d.id, d.staff && d.staff.kind, facts(rows)) +
    (last ? `<div class="pf-last"><small>Their last message · scroll down for the whole conversation</small>` +
      `<p>${esc(last.text.replace(/```[\s\S]*?```/g, " [code] ").replace(/[#*`>|_]/g, "").slice(0, 280))}${last.text.length > 280 ? "…" : ""}</p>` +
      (summary ? `<div class="pf-summary md">${MD.render(summary)}</div>` :
        `<button type="button" class="btn" data-act="summarize"${PROFILE.busy["s" + d.id] ? " disabled" : ""}>✨ ${PROFILE.busy["s" + d.id] ? "Summarizing…" : "Summarize with Haiku"}</button>`) +
      `</div>` : "") +
    `</details>`;
};

Hooks.chips = (d) => {
  const replies = PROFILE.replies[d.id];
  if (replies) {
    return replies.map((r) => `<button type="button" class="chip-btn ai" data-act="quick" data-text="${esc(r)}">✨ ${esc(r)}</button>`).join("");
  }
  return `<button type="button" class="chip-btn ai" data-act="suggest">${PROFILE.busy["r" + d.id] ? "✨ Thinking…" : "✨ Suggest replies"}</button>`;
};

function refreshFoot(d) {
  UI.footKey = null;
  const ta = $("#say textarea");
  const draft = ta ? ta.value : "";
  renderSheet(d, false);
  const again = $("#say textarea");
  if (again) again.value = draft;
}

Object.assign(ACTIONS, {
  async summarize(d) {
    PROFILE.busy["s" + d.id] = true;
    renderSheet(d, false);
    try {
      const r = await api(`/api/session/${d.id}/summarize`, {});
      PROFILE.summaries[d.id] = r.summary;
    } finally {
      PROFILE.busy["s" + d.id] = false;
    }
  },
  async suggest(d) {
    PROFILE.busy["r" + d.id] = true;
    refreshFoot(d);
    try {
      const r = await api(`/api/session/${d.id}/suggest`, {});
      PROFILE.replies[d.id] = r.replies;
    } finally {
      PROFILE.busy["r" + d.id] = false;
      refreshFoot(UI.detail || d);
    }
  },
});

document.addEventListener("toggle", (ev) => {
  if (ev.target.classList && ev.target.classList.contains("profile")) PROFILE.open = ev.target.open;
}, true);

document.addEventListener("change", async (ev) => {
  const pick = ev.target.closest(".pf-model");
  if (!pick) return;
  try {
    if (pick.dataset.staff) {
      const r = await api("/api/team/model", { name: pick.dataset.staff, model: pick.value });
      toast(`${r.name} now works on ${MODEL_NAMES[r.model] || r.model}.`);
      await poll();
    } else {
      await api(`/api/session/${pick.dataset.sid}/model`, { model: pick.value });
      toast(pick.value ? `Your next message from here runs on ${MODEL_NAMES[pick.value]}. In a terminal, type /model there.` : "Back to the model it already uses.");
      if (UI.open === pick.dataset.sid) loadDetail(false);
    }
  } catch (e) { toast(e.message); }
});

document.addEventListener("change", async (ev) => {
  const sel = ev.target.closest(".pf-division");
  if (!sel) return;
  try {
    await api(`/api/session/${sel.dataset.sid}/division`, { division: sel.value });
    toast(sel.value ? `Moved to ${sel.value}.` : "Back in the Guild Hall.");
    poll();
  } catch (e) { toast(e.message); }
});

// The staff panel gets the same card, with their pay summed over every errand.
(() => {
  if (!Hooks.panels.staff) return;
  const base = Hooks.panels.staff.render;
  Hooks.panels.staff.render = function (body) {
    base.call(this, body);
    const m = memberById(UI.arg);
    if (!m || !m.persona) return;
    const pay = m.pay || {};
    const card = document.createElement("section");
    card.className = "panel-sec profile-card";
    card.innerHTML = personaCard(m.persona, m.seed, m.kind, facts([
      ["Role", m.role], ["Division", m.division], ["Model", m.model || "default"],
      ["Pay today", money(pay.today)], ["Pay 7 days", money(pay.week)], ["Pay 30 days", money(pay.month)],
      ["Last activity", m.active ? ago(m.active) : "not yet"],
    ]));
    const bio = body.querySelector(".bio");
    if (bio) bio.remove();
    body.prepend(card);
  };
})();
