// The chain of command: your programs are interns with a warden each; the innovator files petitions,
// the operations manager makes rounds, and the CEO steps in when the operations manager fails.
"use strict";

const CHAIN_LEVEL = { warden: "their warden", ops: "the Operations Manager", ceo: "the CEO", you: "you" };
const CHAIN_RUNG = {
  ceo: ["👑", "CEO", "Steps in when the Operations Manager fails, gets stuck, or cannot fix an incident."],
  ops: ["⚔️", "Operations Manager", "The orchestrator: makes rounds over every intern and sends the right person."],
  innovator: ["✒️", "Innovator", "Reviews the interns and their incidents, then files petitions for you."],
};

Hooks.header.push((state) => {
  const c = state.chain || {};
  const badge = $("#chain-btn .badge");
  if (!badge) return;
  const n = (c.needs_you || 0) + (c.ops_down ? 1 : 0);
  badge.hidden = !(c.open || c.ops_down);
  badge.textContent = c.open || (c.ops_down ? "!" : "");
  badge.classList.toggle("alert", n > 0);
});

function staffOptions(staff, selected, blank) {
  return `<option value="">${esc(blank)}</option>` +
    staff.map((s) => `<option value="${esc(s.id)}"${s.id === selected ? " selected" : ""}>${esc(s.name)} · ${esc(s.title)}</option>`).join("");
}

function roundLine(r) {
  if (!r || !r.started) return "No round yet.";
  if (r.running) return `On a round since ${ago(r.started)}${r.takeover ? " (taking over)" : ""}.`;
  const how = r.ok === false ? "failed" : "done";
  return `Last round ${ago(r.ended || r.started)}: ${how}.` + (r.error ? ` ${r.error}` : "");
}

function rungHTML(role, p, staff) {
  const [icon, title, what] = CHAIN_RUNG[role];
  const r = p.round || {};
  const down = role === "ops" && p.down;
  const who = p.name
    ? `<b>${esc(p.name)}</b> <small>${esc(p.model || "default model")}</small>` +
      (down ? ` <span class="tag t-down">down</span>` : r.running ? ` <span class="tag t-work">on a round</span>` : "")
    : `<i>vacant</i>`;
  const action = role === "innovator" ? "Review now" : role === "ceo" ? "Summon now" : "Run a round now";
  return `<div class="rung r-${role}${down ? " down" : ""}"><span class="rung-icon" aria-hidden="true">${icon}</span>` +
    `<div class="rung-body"><p class="rung-title">${esc(title)} · ${who}</p><p class="sub">${esc(what)}</p>` +
    (p.name ? `<p class="sub">${esc(roundLine(r))}</p>` +
      (r.summary ? `<details><summary>Their last report</summary><div class="md">${MD.render(r.summary)}</div></details>` : "") : "") +
    `<div class="btn-row">` +
    (p.name ? `<button type="button" class="btn" data-panel-act="chain-round" data-kind="${role}">${action}</button>` +
      (r.sid ? `<button type="button" class="btn" data-sid="${esc(r.sid)}">Open the round</button>` : "")
      : `<button type="button" class="btn primary" data-panel-act="chain-recruit" data-role="${role}">Recruit one</button>`) +
    `</div><form class="form inline" data-form="chain-appoint" data-role="${role}"><select name="staff" aria-label="Appoint ${esc(title)}">` +
    staffOptions(staff, p.staff, p.name ? "Nobody (leave vacant)" : "Appoint someone already in the guild…") +
    `</select><button type="submit" class="btn">Appoint</button></form></div></div>`;
}

function healthDot(i) {
  if (i.incident && i.incident.status === "needs-you") return "dot-red";
  if (!i.health) return "dot-grey";
  return i.health.ok ? "dot-green" : "dot-red";
}

function incidentHTML(x) {
  const attempts = (x.attempts || []).map((a) =>
    `<li><b>${esc(a.who)}</b> (${esc(CHAIN_LEVEL[a.level] || a.level)}): ` +
    `${a.ended ? (a.ok === false ? "failed" : "finished") : "working…"}` +
    (a.summary ? ` <span class="sub">${esc(a.summary.slice(0, 220))}</span>` : "") +
    (a.sid ? ` <button type="button" class="btn small" data-sid="${esc(a.sid)}">Open</button>` : "") + `</li>`).join("");
  return `<div class="incident st-${esc(x.status)}"><p><b>Incident ${esc(x.id)}</b> · ${esc(ago(x.opened))} · ` +
    `${x.status === "needs-you" ? "<b>needs you</b>" : "with " + esc(CHAIN_LEVEL[x.level] || "nobody yet")}</p>` +
    `<p class="sub">${esc((x.reasons || []).join("; "))}</p>` +
    (attempts ? `<ol class="attempts">${attempts}</ol>` : "") +
    `<div class="btn-row"><button type="button" class="btn" data-panel-act="chain-escalate" data-id="${esc(x.id)}">Send up the chain</button>` +
    `<button type="button" class="btn" data-panel-act="chain-resolve" data-id="${esc(x.id)}">Mark fixed</button></div></div>`;
}

function internHTML(i, staff) {
  const h = i.health;
  return `<article class="intern"><header><span class="dot ${healthDot(i)}" aria-hidden="true"></span><b>${esc(i.name)}</b>` +
    `<span class="tag">${!h ? "unchecked" : h.ok ? "healthy" : "down"}</span></header>` +
    `<p class="sub">${esc(i.project)}</p><p class="sub">Checks: ${esc(i.checks)}</p>` +
    (h ? `<p class="sub">Checked ${esc(ago(h.checked))}${h.ok ? "" : ": " + esc(h.reasons.join("; "))}</p>` : "") +
    (i.incident ? incidentHTML(i.incident) : "") +
    `<form class="form inline" data-form="chain-warden" data-id="${esc(i.id)}"><label>Warden<select name="staff">` +
    staffOptions(staff, i.warden, "No warden (goes straight to ops)") + `</select></label><button type="submit" class="btn">Save</button></form>` +
    `<div class="btn-row"><button type="button" class="btn" data-panel-act="chain-check" data-id="${esc(i.id)}">Check now</button>` +
    `<button type="button" class="btn danger" data-panel-act="chain-remove" data-id="${esc(i.id)}">Remove</button></div></article>`;
}

function addInternHTML(projects, jobs, staff) {
  return `<form id="chain-intern-form" class="form">` +
    `<label>Name<input type="text" name="name" maxlength="60" required placeholder="e.g. RH Wallet Finder"></label>` +
    `<label>Program folder<select name="project" required>${projects.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("")}</select></label>` +
    `<label>launchd job <small>(optional: healthy while it is loaded and its last exit was 0)</small><select name="familiar"><option value="">None</option>` +
    jobs.filter((j) => !j.ours).map((j) => `<option value="${esc(j.id)}">${esc(j.label)}</option>`).join("") + `</select></label>` +
    `<label>Health URL <small>(optional: must answer below 400)</small><input type="url" name="url" maxlength="300" placeholder="http://127.0.0.1:8787/"></label>` +
    `<div class="two"><label>Fresh file <small>(optional, inside the folder)</small><input type="text" name="fresh" maxlength="200" placeholder="data/wallets.db"></label>` +
    `<label>Max age (hours)<input type="number" name="fresh_hours" min="0.1" max="720" step="0.1" value="24"></label></div>` +
    `<label>Warden<select name="warden">${staffOptions(staff, "", "No warden yet")}</select></label>` +
    `<button type="submit" class="btn primary wide">Take them on as an intern</button></form>`;
}

function settingsHTML(s, dispatched) {
  const n = (name, label, min, max) =>
    `<label>${esc(label)}<input type="number" name="${name}" min="${min}" max="${max}" value="${esc(s[name])}"></label>`;
  return `<form id="chain-settings-form" class="form">` +
    `<label class="check"><input type="checkbox" name="auto"${s.auto ? " checked" : ""}><span>Autopilot` +
    `<small>Incidents go up the chain and rounds happen on their own. Off: checks still run, people only move when you press a button.</small></span></label>` +
    `<div class="two">${n("check_minutes", "Check every (min)", 1, 240)}${n("fail_after", "Failed checks before an incident", 1, 10)}</div>` +
    `<div class="two">${n("ops_every_hours", "Ops round every (h)", 1, 72)}${n("innovator_every_days", "Innovator review every (days)", 1, 60)}</div>` +
    `<div class="two">${n("stuck_minutes", "A round is stuck after (min)", 15, 600)}${n("max_auto_per_day", "Automatic dispatches per day", 0, 100)}</div>` +
    `<p class="sub">Today: ${esc((dispatched && dispatched.n) || 0)} automatic dispatches.</p>` +
    `<button type="submit" class="btn primary wide">Save</button></form>`;
}

Hooks.panels.chain = {
  title: "Chain of command",
  subtitle: "Your programs are interns. Wardens keep them, the Operations Manager oversees, the CEO is the backup, you have the last word.",
  icon: '<span class="panel-icon">⚜️</span>',
  async render(body) {
    const [c, { projects }, bots] = await Promise.all([api("/api/chain"), api("/api/projects"), api("/api/bots").catch(() => ({ jobs: [] }))]);
    MD.reset();
    const wardens = {};
    c.interns.forEach((i) => { if (i.warden_name) (wardens[i.warden_name] = wardens[i.warden_name] || []).push(i.name); });
    const settled = c.incidents.filter((x) => x.status === "resolved").slice(0, 8);
    body.innerHTML =
      `<section class="panel-sec chain-ladder"><h3>The ladder</h3>` +
      `<div class="rung r-you"><span class="rung-icon" aria-hidden="true">🛡️</span><div class="rung-body">` +
      `<p class="rung-title">Guildmaster · <b>bojjifomo</b></p><p class="sub">You approve petitions and seals. What nobody below could fix comes to your phone.</p>` +
      (c.needs_you ? `<p><span class="tag t-down">${esc(c.needs_you)} incident${c.needs_you > 1 ? "s" : ""} need you</span></p>` : "") + `</div></div>` +
      ["ceo", "ops", "innovator"].map((r) => rungHTML(r, c.people[r], c.staff)).join("") +
      `<div class="rung r-warden"><span class="rung-icon" aria-hidden="true">🗝️</span><div class="rung-body"><p class="rung-title">Wardens</p>` +
      `<p class="sub">${Object.keys(wardens).length ? Object.entries(wardens).map(([w, list]) => `${esc(w)}: ${list.map(esc).join(", ")}`).join("<br>") : "Nobody guards an intern yet. Pick a warden on each intern below."}</p></div></div>` +
      `<div class="rung r-intern"><span class="rung-icon" aria-hidden="true">🌱</span><div class="rung-body"><p class="rung-title">Interns · ${esc(c.interns.length)}</p>` +
      `<p class="sub">Each program you run. Checked every ${esc(c.settings.check_minutes)} min.</p></div></div></section>` +
      `<section class="panel-sec"><h3>Interns</h3>${c.interns.length ? c.interns.map((i) => internHTML(i, c.staff)).join("") : '<p class="loading">No interns yet. Add your first program below.</p>'}</section>` +
      `<section class="panel-sec"><h3>Take on an intern</h3>${addInternHTML(projects, bots.jobs || [], c.staff)}</section>` +
      `<section class="panel-sec"><h3>Autopilot</h3>${settingsHTML(c.settings, c.dispatched)}</section>` +
      (settled.length ? `<section class="panel-sec"><h3>Recently fixed</h3>${settled.map((x) =>
        `<p class="sub"><b>${esc(x.name)}</b> · ${esc(ago(x.resolved || x.opened))} · ${esc(x.resolved_by || "")}${x.note ? ": " + esc(x.note) : ""}</p>`).join("")}</section>` : "");
  },
};

Object.assign(Hooks.forms, {
  async "chain-intern-form"(form) {
    const f = (k) => form[k].value;
    const r = await api("/api/chain/interns", {
      name: f("name"), project: f("project"), familiar: f("familiar"), url: f("url"),
      fresh: f("fresh"), fresh_hours: f("fresh_hours"), warden: f("warden"),
    });
    toast(`${r.intern.name} joined as an intern. Checking them now…`);
    setTimeout(() => { if (UI.mode === "chain") Hooks.panels.chain.render($("#sheet-body")); }, 1500);
    Hooks.panels.chain.render($("#sheet-body"));
  },
  async "chain-settings-form"(form) {
    const body = { auto: form.auto.checked };
    ["check_minutes", "fail_after", "ops_every_hours", "innovator_every_days", "stuck_minutes", "max_auto_per_day"]
      .forEach((k) => { body[k] = form[k].value; });
    await api("/api/chain/settings", body);
    toast(body.auto ? "Autopilot is on." : "Autopilot is off.");
    Hooks.panels.chain.render($("#sheet-body"));
  },
  async "chain-appoint"(form) {
    await api(`/api/chain/appoint/${form.dataset.role}`, { staff: form.staff.value });
    toast("Appointed.");
    Hooks.panels.chain.render($("#sheet-body"));
  },
  async "chain-warden"(form) {
    await api(`/api/chain/interns/${form.dataset.id}/warden`, { staff: form.staff.value });
    toast("Warden saved.");
    Hooks.panels.chain.render($("#sheet-body"));
  },
});

Object.assign(Hooks.panelActions, {
  async "chain-recruit"(btn) {
    const r = await api(`/api/chain/recruit/${btn.dataset.role}`, {});
    toast(`Welcome, ${r.staff.name} the ${r.staff.title}!`);
    poll();
  },
  async "chain-round"(btn) {
    const r = await api(`/api/chain/round/${btn.dataset.kind}`, {});
    toast(`${r.who} set out.`);
  },
  async "chain-check"(btn) {
    const r = await api(`/api/chain/interns/${btn.dataset.id}/check`, {});
    toast(r.ok ? "Healthy." : "Down: " + r.reasons.join("; "));
  },
  async "chain-escalate"(btn) {
    const r = await api(`/api/chain/incidents/${btn.dataset.id}/escalate`, { note: "sent up by bojjifomo" });
    toast(r.level === "you" ? "Nobody above is free: it stays with you." : `${r.who} took it.`);
  },
  async "chain-resolve"(btn) {
    await api(`/api/chain/incidents/${btn.dataset.id}/resolve`, { note: "marked fixed from the phone" });
    toast("Marked fixed.");
  },
  async "chain-remove"(btn) {
    if (!confirm("Remove this intern from the guild? Their program is not touched.")) return false;
    await api(`/api/chain/interns/${btn.dataset.id}/remove`, {});
    toast("Removed.");
  },
});
