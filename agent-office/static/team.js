// Stage 3: the office's specialists: their panel, hireing and tasks.
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
  title: "Team member",
  render(body) {
    const m = memberById(UI.arg);
    if (!m) { body.innerHTML = '<p class="loading">They have left the office.</p>'; return; }
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
      (m.last ? `<section class="panel-sec"><h3>Their last answer ${m.sid ? `<button type="button" class="btn" data-sid="${esc(m.sid)}">Open the full session</button>` : ""}</h3>` +
        `<div class="md last-answer">${MD.render(m.last)}</div></section>` : "") +
      `<section class="panel-sec"><h3>Give them a task</h3><form id="task-form" class="form">` +
      `<textarea name="brief" rows="4" required placeholder="A self-contained brief: goal, paths, what done looks like."></textarea>` +
      `<button type="submit" class="btn primary wide">Send the task</button></form></section>` +
      `<section class="panel-sec" data-edit-slot><h3>Role and instructions</h3><p class="sub">Loading…</p></section>` +
      `<section class="panel-sec"><button type="button" class="btn danger" data-panel-act="dismiss">Let them go</button>` +
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

function dismissSessionion(m) {
  return `Dismiss ${m.name} from the office?` + (m.chain ? `\n\n${m.name} is ${m.chain}. That seat becomes vacant.` : "") +
    "\n\nTheir folder in ~/Agents stays as it is.";
}

const HIRE_GROUPS = ["Build", "Guard", "Research", "Fund", "Words", "Plan"];
let hireTemplates = [];

Hooks.panels.hire = {
  title: "Hire a specialist",
  subtitle: "They join the office for good and get better with every job.",
  icon: '<span class="panel-icon">★</span>',
  async render(body) {
    const [team, tpl, chain, o] = await Promise.all([api("/api/team"), api("/api/team/templates"), api("/api/chain").catch(() => null), getOptions()]);
    hireTemplates = tpl.templates;
    const divisions = [...new Set([...tpl.divisions, ...team.known_divisions])].sort();
    const seats = chain ? chain.settings.opus_seats : 2;
    const used = team.staff.filter((m) => m.model === "opus").length;
    const models = ["sonnet", "opus", "haiku", "grok"].filter((m) => (o.models || []).includes(m));
    body.innerHTML = `<form id="hire-form" class="form">` +
      `<label>Name <small>(blank: Sonnet picks one)</small><input type="text" name="name" maxlength="40" placeholder="e.g. Hyperion"></label>` +
      `<label>Role <small>(a short title, up to 80 characters)</small><input type="text" name="role" maxlength="80" required placeholder="e.g. Copy Trading Specialist, Video editor, QC reviewer"></label>` +
      `<label>Division<select name="division"><option value="">Automatic: from the role</option>` +
      divisions.map((d) => `<option>${esc(d)}</option>`).join("") + `</select>` +
      `<input type="text" name="newdivision" maxlength="40" placeholder="…or name a new division"></label>` +
      `<label>What they do and how <small id="hire-count">(standing instructions · 0 / 8000)</small>` +
      `<textarea name="instructions" rows="10" maxlength="8000" placeholder="Write as little as you like, even just 'makes thumbnails'. Sonnet fills in the rest."></textarea></label>` +
      `<label>Start from a template <small>(optional)</small><select name="template" id="hire-template"><option value="">None</option>` +
      HIRE_GROUPS.map((g) => `<optgroup label="${esc(g)}">` + hireTemplates.filter((t) => t.group === g).map((t) =>
        `<option value="${esc(t.key)}">${esc(t.label)}</option>`).join("") + `</optgroup>`).join("") + `</select>` +
      `<small id="hire-summary">A template fills in the role, division, model and a full set of instructions.</small></label>` +
      `<div class="two"><label>Model <small>(${used} of ${esc(seats)} Opus seats used)</small><select name="model"><option value="auto">Auto (Opus sizes the job)</option>` +
      models.map((m) => `<option value="${m}">${esc(W.models[m])}</option>`).join("") + `</select></label>` +
      `<label>Effort<select name="effort"><option value="">Auto</option>${options((o.efforts || []).filter(Boolean), W.efforts, "")}</select></label></div>` +
      `<div class="btn-row"><button type="button" class="btn ai" data-panel-act="hire-draft">✨ Fill in and polish with Sonnet</button></div>` +
      `<p class="hint">Write as little as you like. Sonnet fills in any empty field and turns rough notes into clear instructions; you can still edit them. On Auto, Opus reads the final brief and picks their model and effort. They get their own folder in ~/Agents with their own memory, and can work on any of your projects.</p>` +
      `<button type="submit" class="btn primary wide">Hire</button><div id="hire-progress" hidden></div></form>`;
  },
};

function hireFields(form) {
  return { name: form.name.value, role: form.role.value, instructions: form.instructions.value,
    division: form.newdivision.value.trim() || form.division.value, model: form.model.value, effort: form.effort.value };
}

function hireProgress(form, label) {
  const box = $("#hire-progress");
  if (!box) return;
  if (!label) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.className = "hire-progress";
  box.innerHTML = `<b>${esc(label)}</b> <small class="sub" data-hire-timer>0s</small><span class="bar"><i></i></span>`;
  const t0 = Date.now();
  const tick = () => { const el = box.querySelector("[data-hire-timer]"); if (!el || box.hidden) return; el.textContent = Math.round((Date.now() - t0) / 1000) + "s"; setTimeout(tick, 1000); };
  tick();
}

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
  if (el) el.textContent = `(standing instructions · ${form.instructions.value.length} / 8000)`;
}

Hooks.jobExtras = () =>
  `<label class="check eco"><input type="checkbox" name="eco"><span>🌱 Eco mode<small>The lead runs on Opus and only plans and reviews. Every piece of work goes to a helper: Haiku for look-ups and simple edits, Sonnet for coding. Stays on for every message you send this session.</small></span></label>` +
  `<label class="check"><input type="checkbox" name="pm"><span>PM mode<small>The lead splits the task, hands pieces to the team and to subagents (Haiku for lookups, Sonnet for coding, Opus for hard problems), then reports who did what.</small></span></label>`;

Object.assign(Hooks.forms, {
  async "staff-edit-form"(form) {
    const r = await api(`/api/staff/${form.dataset.id}/update`, {
      role: form.role.value, division: form.division.value, instructions: form.instructions.value,
    });
    toast(`${r.staff.name}'s role and instructions are saved.`);
    await poll();
  },
  async "hire-form"(form) {
    const f = hireFields(form);
    hireProgress(form, `Hiring ${f.name || "someone new"} · ${f.model === "auto" || !f.instructions.trim() ? "Sonnet is writing the brief" : "working"}`);
    let r;
    try { r = await api("/api/staff", f); } finally { hireProgress(form, ""); }
    await poll();
    toast(r.renamed_from ? `${r.renamed_from} was taken, so welcome ${r.staff.name}!` : `Welcome, ${r.staff.name} the ${r.staff.title}!` + (r.reason ? " " + r.reason : ""));
    history.replaceState({ panel: "staff", arg: r.staff.id }, "");
    openPanel("staff", false, r.staff.id);
  },
  async "brief-form"() {
    const m = memberById(UI.arg);
    await api("/api/orchestrator/brief", {});
    toast(`${m ? m.name : "Quill"} is writing today's brief.`);
    if (Hooks.panels.staff) Hooks.panels.staff.render($("#sheet-body"));
  },
  async "task-form"(form) {
    const m = memberById(UI.arg);
    const brief = form.brief.value.trim();
    if (!m || !brief) return;
    const r = await api("/api/team/ask", { name: m.name, brief });
    if (r.queued_for_gold) { toast(r.reason || "Queued until the daily budget resets."); return; }
    if (r.briefing) { toast(`${m.name} is writing today's brief.`); return; }
    UI.run = { id: r.run, sid: r.sid, events: [], text: brief, done: false, v: -1 };
    history.replaceState({ sheet: r.sid }, "");
    openSheet(r.sid, false);
    followRun(r.run);
    toast(`${m.name} is on it.`);
  },
});

Object.assign(Hooks.panelActions, {
  async "hire-draft"(btn) {
    const form = $("#hire-form");
    if (!form) return false;
    hireProgress(form, "Sonnet is filling in the form");
    try {
      const d = await api("/api/team/draft", hireFields(form));
      form.name.value = d.name; form.role.value = d.role; form.instructions.value = d.instructions;
      const known = [...form.division.options].some((o) => o.textContent === d.division);
      if (known) { form.division.value = d.division; form.newdivision.value = ""; } else form.newdivision.value = d.division;
      if ([...form.model.options].some((o) => o.value === d.model)) form.model.value = d.model;
      if ([...form.effort.options].some((o) => o.value === d.effort)) form.effort.value = d.effort;
      updateHireCount(form);
      toast(d.reason ? "Filled in. " + d.reason : "Filled in. Read it over, then Hire.");
    } finally { hireProgress(form, ""); }
    return false;
  },
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
    if (!m || !confirm(dismissSessionion(m))) return false;
    await api(`/api/staff/${m.id}/dismiss`, { force: true });
    toast(`${m.name} has left the office.`);
    closeSheet();
    return false;
  },
});
