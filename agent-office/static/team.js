// Stage 3: the guild's specialists: their panel, recruiting and errands.
"use strict";

function memberById(id) {
  const team = (UI.state && UI.state.team) || { staff: [] };
  return team.staff.find((m) => m.id === id);
}

Hooks.panels.staff = {
  title: "Guild member",
  render(body) {
    const m = memberById(UI.arg);
    if (!m) { body.innerHTML = '<p class="loading">They have left the guild.</p>'; return; }
    MD.reset();
    $("#sheet-portrait").innerHTML = Sprites.svg(m.seed, m.kind, 44);
    $("#sheet-name").textContent = m.name;
    $("#sheet-meta").textContent = `${m.title} · ${m.division} · ${m.model || "default model"}`;
    body.innerHTML = `<section class="panel-sec"><p class="bio">${esc(m.bio)}</p><p><b>Role:</b> ${esc(m.role)}</p>` +
      `<p class="cli">Any session can ask them: <code>office ask ${esc(m.name.includes(" ") ? '"' + m.name + '"' : m.name)} "&lt;brief&gt;"</code></p></section>` +
      (m.last ? `<section class="panel-sec"><h3>Their last answer ${m.sid ? `<button type="button" class="btn" data-sid="${esc(m.sid)}">Open the full quest</button>` : ""}</h3>` +
        `<div class="md last-answer">${MD.render(m.last)}</div></section>` : "") +
      `<section class="panel-sec"><h3>Send them an errand</h3><form id="errand-form" class="form">` +
      `<textarea name="brief" rows="4" required placeholder="A self-contained brief: goal, paths, what done looks like."></textarea>` +
      `<button type="submit" class="btn primary wide">Send the errand</button></form></section>` +
      `<section class="panel-sec"><button type="button" class="btn danger" data-panel-act="dismiss">Dismiss from the guild</button>` +
      `<small class="hint">Their folder in ~/Agents stays as it is.</small></section>`;
  },
};

Hooks.panels.hire = {
  title: "Recruit a specialist",
  subtitle: "They get a folder in ~/Agents, a title and a seat in a division.",
  icon: '<span class="panel-icon">🛡️</span>',
  async render(body) {
    const team = await api("/api/team");
    body.innerHTML = `<form id="hire-form" class="form">` +
      `<label>Name <small>(left blank or taken? I'll pick a free one)</small><input type="text" name="name" maxlength="40" placeholder="e.g. Lyra"></label>` +
      `<label>Role<input type="text" name="role" maxlength="80" required placeholder="e.g. Python backend engineer, DeFi researcher"></label>` +
      `<label>Instructions<textarea name="instructions" rows="5" maxlength="8000" placeholder="How they should work, what they know, what to avoid."></textarea></label>` +
      `<label>Division<select name="division"><option value="">Pick from the role</option>` +
      team.known_divisions.map((d) => `<option>${esc(d)}</option>`).join("") + `</select></label>` +
      `<label>Model<select name="model">${options(["sonnet", "opus", "haiku"], W.models, "sonnet")}</select></label>` +
      `<button type="submit" class="btn primary wide">Welcome them to the guild</button></form>`;
  },
};

Hooks.jobExtras = () =>
  `<label class="check"><input type="checkbox" name="pm"><span>Party leader mode<small>The lead splits the quest, hands pieces to the guild and to subagents (Haiku for lookups, Sonnet for coding, Opus for hard problems), then reports who did what.</small></span></label>`;

Object.assign(Hooks.forms, {
  async "hire-form"(form) {
    const r = await api("/api/staff", {
      name: form.name.value, role: form.role.value, instructions: form.instructions.value,
      division: form.division.value, model: form.model.value,
    });
    await poll();
    toast(r.renamed_from ? `${r.renamed_from} was taken, so welcome ${r.staff.name}!` : `Welcome, ${r.staff.name} the ${r.staff.title}!`);
    history.replaceState({ panel: "staff", arg: r.staff.id }, "");
    openPanel("staff", false, r.staff.id);
  },
  async "errand-form"(form) {
    const m = memberById(UI.arg);
    const brief = form.brief.value.trim();
    if (!m || !brief) return;
    const r = await api("/api/team/ask", { name: m.name, brief });
    UI.run = { id: r.run, sid: r.sid, events: [], text: brief, done: false, v: -1 };
    history.replaceState({ sheet: r.sid }, "");
    openSheet(r.sid, false);
    followRun(r.run);
    toast(`${m.name} is on it.`);
  },
});

Object.assign(Hooks.panelActions, {
  async dismiss() {
    const m = memberById(UI.arg);
    if (!m || !confirm(`Dismiss ${m.name} from the guild?`)) return false;
    await api(`/api/staff/${m.id}/dismiss`, {});
    toast(`${m.name} has left the guild.`);
    closeSheet();
    return false;
  },
});
