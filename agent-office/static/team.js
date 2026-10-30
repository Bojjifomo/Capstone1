// Stage 3: the guild's specialists: their panel, recruiting and errands.
"use strict";

function memberById(id) {
  const team = (UI.state && UI.state.team) || { staff: [] };
  return team.staff.find((m) => m.id === id);
}

function fillBrief(root, m, report) {
  const slot = root.querySelector("[data-brief-slot]");
  if (!slot) return;
  const running = (report && report.running) || m.status === "working";
  const text = (report && report.text) || "";
  const err = report && report.error && !running ? report.error : "";
  slot.dataset.briefRunning = running ? "1" : "0";
  slot.innerHTML =
    `<h3>Today's brief</h3>` +
    (running ? `<p class="sub">At the books…</p>` : "") +
    (err ? `<p class="bot-error">${esc(err)}</p>` : "") +
    (text ? `<pre class="brief-out">${esc(text)}</pre>` : `<p class="sub">No brief yet.</p>`) +
    `<form id="brief-form" class="form"><button type="submit" class="btn primary wide"${running ? " disabled" : ""}>Write today's brief</button></form>`;
}

Hooks.panels.staff = {
  title: "Guild member",
  render(body) {
    const m = memberById(UI.arg);
    if (!m) { body.innerHTML = '<p class="loading">They have left the guild.</p>'; return; }
    MD.reset();
    $("#sheet-portrait").innerHTML = Sprites.svg(m.seed, m.kind, 44);
    $("#sheet-name").textContent = m.name;
    $("#sheet-meta").textContent = `${m.title} · ${m.division}${m.builtin ? "" : " · " + (m.model || "default model")}`;
    if (m.builtin) {
      body.innerHTML = `<section class="panel-sec"><p class="bio">${esc(m.bio)}</p><p><b>Role:</b> ${esc(m.role)}</p>` +
        `<p class="cli">From a shell on the Mac: <code>office brief</code>. Read-only tools only. Write tools stay off.</p>` +
        `<div data-brief-slot data-brief-running="${m.status === "working" ? "1" : "0"}"></div></section>`;
      fillBrief(body, m, { running: m.status === "working", text: m.last || "", error: "" });
      api("/api/orchestrator").then((report) => {
        if (UI.mode === "staff" && UI.arg === m.id) fillBrief(body, m, report);
      }).catch(() => {});
      return;
    }
    body.innerHTML = `<section class="panel-sec"><p class="bio">${esc(m.bio)}</p><p><b>Role:</b> ${esc(m.role)}</p>` +
      `<p class="cli">Any session can ask them: <code>office ask ${esc(m.name.includes(" ") ? '"' + m.name + '"' : m.name)} "&lt;brief&gt;"</code></p></section>` +
      (m.last ? `<section class="panel-sec"><h3>Their last answer ${m.sid ? `<button type="button" class="btn" data-sid="${esc(m.sid)}">Open the full quest</button>` : ""}</h3>` +
        `<div class="md last-answer">${MD.render(m.last)}</div></section>` : "") +
      `<section class="panel-sec"><h3>Send them an errand</h3><form id="errand-form" class="form">` +
      `<textarea name="brief" rows="4" required placeholder="A self-contained brief: goal, paths, what done looks like."></textarea>` +
      `<button type="submit" class="btn primary wide">Send the errand</button></form></section>` +
      `<section class="panel-sec" data-edit-slot><h3>Role and instructions</h3><p class="sub">Loading…</p></section>` +
      `<section class="panel-sec"><button type="button" class="btn danger" data-panel-act="dismiss">Dismiss from the guild</button>` +
      `<small class="hint">Their folder in ~/Agents stays as it is.</small></section>`;
    fillStaffEdit(body, m.id).catch(() => {});
  },
  refresh(body) {
    const m = memberById(UI.arg);
    if (!m || !m.builtin) return;
    const slot = body.querySelector("[data-brief-slot]");
    const running = m.status === "working";
    if (!slot || (slot.dataset.briefRunning === "1") !== running) this.render(body);
  },
};

// The edit form loads the full text on demand; /api/state never carries instructions.
let staffStanding = null;
async function fillStaffEdit(body, id) {
  const slot = body.querySelector("[data-edit-slot]");
  if (!slot) return;
  const [{ staff: s }, tpl] = await Promise.all([api(`/api/staff/${id}`), staffStanding ? Promise.resolve(staffStanding) : api("/api/team/templates")]);
  staffStanding = tpl;
  if (UI.mode !== "staff" || UI.arg !== id) return;
  const standard = tpl.standing[s.name.toLowerCase().replace(/ [ivx]+$/, "")];
  slot.innerHTML = `<h3>Role and instructions</h3>` +
    `<details${s.instructions ? "" : " open"}><summary>Edit what ${esc(s.name)} does (${esc(s.instructions.length)} characters)</summary>` +
    `<form id="staff-edit-form" class="form" data-id="${esc(s.id)}">` +
    `<label>Role <small>(a short title, up to 80 characters)</small><input type="text" name="role" maxlength="80" required value="${esc(s.role)}"></label>` +
    `<label>Division<input type="text" name="division" maxlength="40" value="${esc(s.division)}"></label>` +
    `<label>Instructions <small>(this becomes their CLAUDE.md; up to 8000 characters)</small>` +
    `<textarea name="instructions" rows="16" maxlength="8000">${esc(s.instructions)}</textarea></label>` +
    (standard ? `<button type="button" class="btn" data-panel-act="staff-standard" data-name="${esc(standard.name.toLowerCase())}">Load the latest standard text for ${esc(standard.name)}</button>` : "") +
    `<button type="submit" class="btn primary wide">Save</button></form></details>`;
}

function dismissQuestion(m) {
  return `Dismiss ${m.name} from the guild?` + (m.chain ? `\n\n${m.name} is ${m.chain}. That seat becomes vacant.` : "") +
    "\n\nTheir folder in ~/Agents stays as it is.";
}

const HIRE_GROUPS = ["Build", "Guard", "Research", "Words", "Plan"];
let hireTemplates = [];

Hooks.panels.hire = {
  title: "Recruit a specialist",
  subtitle: "Start from a template or write your own. They get a folder in ~/Agents, a title and a seat in a division.",
  icon: '<span class="panel-icon">🛡️</span>',
  async render(body) {
    const [team, tpl, chain, o] = await Promise.all([api("/api/team"), api("/api/team/templates"), api("/api/chain").catch(() => null), getOptions()]);
    hireTemplates = tpl.templates;
    const divisions = [...new Set([...tpl.divisions, ...team.known_divisions])].sort();
    const seats = chain ? chain.settings.opus_seats : 2;
    const used = team.staff.filter((m) => m.model === "opus").length;
    body.innerHTML = `<form id="hire-form" class="form">` +
      `<label>Start from a template<select name="template" id="hire-template"><option value="">Blank: I will write it myself</option>` +
      HIRE_GROUPS.map((g) => `<optgroup label="${esc(g)}">` + hireTemplates.filter((t) => t.group === g).map((t) =>
        `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join("") + `</optgroup>`).join("") + `</select>` +
      `<small id="hire-summary">Fills in the role, division, model and a full set of instructions. Change anything you like.</small></label>` +
      `<label>Name <small>(left blank or taken? I'll pick a free one)</small><input type="text" name="name" maxlength="40" placeholder="e.g. Lyra"></label>` +
      `<label>Role <small>(a short title, up to 80 characters)</small><input type="text" name="role" maxlength="80" required placeholder="e.g. Python backend engineer, DeFi researcher"></label>` +
      `<label>Division<select name="division"><option value="">Pick from the role</option>` +
      divisions.map((d) => `<option>${esc(d)}</option>`).join("") + `</select>` +
      `<input type="text" name="newdivision" maxlength="40" placeholder="…or name a new division"></label>` +
      `<label>Model <small>Haiku: checks, lookups, summaries. Sonnet: everyday work. Opus: hard design and bugs (${used} of ${esc(seats)} seats in use).</small>` +
      `<select name="model">${options(["sonnet", "opus", "haiku"].filter((m) => (o.models || []).includes(m)), W.models, tpl.models.includes("sonnet") ? "sonnet" : tpl.models[0])}</select></label>` +
      `<label>Instructions <small id="hire-count">0 / 8000</small>` +
      `<textarea name="instructions" rows="14" maxlength="8000" placeholder="Their mission, what they know, the steps they follow, what good looks like, what they never do, and how they report. A template fills this in."></textarea></label>` +
      `<button type="submit" class="btn primary wide">Welcome them to the guild</button></form>`;
  },
};

document.addEventListener("change", (ev) => {
  if (ev.target.id !== "hire-template") return;
  const form = ev.target.form;
  const t = hireTemplates.find((x) => x.key === ev.target.value);
  const summary = $("#hire-summary");
  if (!t) { if (summary) summary.textContent = "Fills in the role, division, model and a full set of instructions. Change anything you like."; return; }
  form.role.value = t.role;
  form.instructions.value = t.instructions;
  form.model.value = t.model;
  const known = [...form.division.options].some((o) => o.value === t.division || o.textContent === t.division);
  form.newdivision.value = known ? "" : t.division;
  if (known) form.division.value = t.division;
  if (summary) summary.textContent = t.summary;
  updateHireCount(form);
});

document.addEventListener("input", (ev) => {
  if (ev.target.name === "instructions" && ev.target.form && ev.target.form.id === "hire-form") updateHireCount(ev.target.form);
});

function updateHireCount(form) {
  const el = $("#hire-count");
  if (el) el.textContent = `${form.instructions.value.length} / 8000`;
}

Hooks.jobExtras = () =>
  `<label class="check"><input type="checkbox" name="pm"><span>Party leader mode<small>The lead splits the quest, hands pieces to the guild and to subagents (Haiku for lookups, Sonnet for coding, Opus for hard problems), then reports who did what.</small></span></label>`;

Object.assign(Hooks.forms, {
  async "staff-edit-form"(form) {
    const r = await api(`/api/staff/${form.dataset.id}/update`, {
      role: form.role.value, division: form.division.value, instructions: form.instructions.value,
    });
    toast(`${r.staff.name}'s role and instructions are saved.`);
    await poll();
  },
  async "hire-form"(form) {
    const r = await api("/api/staff", {
      name: form.name.value, role: form.role.value, instructions: form.instructions.value,
      division: form.newdivision.value.trim() || form.division.value, model: form.model.value,
    });
    await poll();
    toast(r.renamed_from ? `${r.renamed_from} was taken, so welcome ${r.staff.name}!` : `Welcome, ${r.staff.name} the ${r.staff.title}!`);
    history.replaceState({ panel: "staff", arg: r.staff.id }, "");
    openPanel("staff", false, r.staff.id);
  },
  async "brief-form"() {
    const m = memberById(UI.arg);
    await api("/api/orchestrator/brief", {});
    toast(`${m ? m.name : "Quill"} is writing today's brief.`);
    if (Hooks.panels.staff) Hooks.panels.staff.render($("#sheet-body"));
  },
  async "errand-form"(form) {
    const m = memberById(UI.arg);
    const brief = form.brief.value.trim();
    if (!m || !brief) return;
    const r = await api("/api/team/ask", { name: m.name, brief });
    if (r.queued_for_gold) { toast(r.reason || "Queued until the treasury refills."); return; }
    if (r.briefing) { toast(`${m.name} is writing today's brief.`); return; }
    UI.run = { id: r.run, sid: r.sid, events: [], text: brief, done: false, v: -1 };
    history.replaceState({ sheet: r.sid }, "");
    openSheet(r.sid, false);
    followRun(r.run);
    toast(`${m.name} is on it.`);
  },
});

Object.assign(Hooks.panelActions, {
  "staff-standard"(btn) {
    const p = staffStanding && staffStanding.standing[btn.dataset.name];
    const form = $("#staff-edit-form");
    if (!p || !form) return false;
    form.role.value = p.role;
    form.division.value = p.division;
    form.instructions.value = p.instructions;
    toast("Loaded. Read it over, then Save.");
    return false;
  },
  async dismiss() {
    const m = memberById(UI.arg);
    if (!m || !confirm(dismissQuestion(m))) return false;
    await api(`/api/staff/${m.id}/dismiss`, { force: true });
    toast(`${m.name} has left the guild.`);
    closeSheet();
    return false;
  },
});
