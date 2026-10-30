// Agent Office front end: the guild hall scene, the sheet, polling.
"use strict";

// Theme wording lives here so the whole page speaks "guild hall".
const W = {
  status: { working: "On a quest", needs: "Awaits your word", idle: "Resting", finished: "Returned", archived: "Archived" },
  short: { working: "questing", needs: "awaits you", idle: "resting", finished: "returned", archived: "archived" },
  modes: {
    acceptEdits: "Accept edits", auto: "Auto (guarded)", plan: "Plan only", manual: "Ask first (no prompts here)",
    dontAsk: "Don't ask (deny unknown)", bypassPermissions: "Bypass all (needs seal)",
  },
  models: { "": "Default model", opus: "Opus: hard problems", sonnet: "Sonnet: everyday", haiku: "Haiku: quick lookups" },
  quick: ["Yes, go ahead.", "Continue.", "Commit and push.", "Where are you at? Short summary please."],
};

const $ = (sel, el) => (el || document).querySelector(sel);
const esc = MD.esc;
const POLL_MS = 4000;

const UI = {
  state: null, version: null, options: null,
  mode: null,          // what the sheet shows: "session" or a panel name
  open: null,          // session id when mode === "session"
  detail: null, run: null, following: null,
  spots: new Map(), timer: null, footKey: null,
};

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Agent-Office": "1" },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || res.statusText);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function ago(ts) {
  if (!ts) return "";
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

function goldLabel(n) {
  if (n == null || n === "") return "—";
  const rounded = Math.round(Number(n) * 10) / 10;
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
}

function money(x) {
  if (!x) return "$0";
  return x < 0.01 ? "<$0.01" : "$" + x.toFixed(x < 10 ? 2 : 0);
}

let toastTimer = null;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2800);
}

function options(list, labels, selected) {
  return list.map((v) => `<option value="${esc(v)}"${v === selected ? " selected" : ""}>${esc(labels[v] || v)}</option>`).join("");
}

// Risky actions need a one-time code pushed to the phone.
async function withSeal(purpose, needed, fn) {
  if (!needed) return fn({});
  const { code_id } = await api("/api/code", { purpose });
  const code = prompt("A 6-digit guild seal was sent to your phone. Enter it (valid 2 minutes):");
  if (!code) throw new Error("No seal, nothing sent.");
  return fn({ code_id, code: code.trim() });
}
const isRisky = (mode) => UI.options && UI.options.risky.includes(mode);

// ---- the hall ---------------------------------------------------------

function statusLine(s) {
  if (s.desktop) return W.short[s.status] + " · in Claude app";
  if (s.status === "needs") return s.note || W.short.needs;
  if (s.status === "working" && s.note && s.note !== "thinking") return s.note;
  return W.short[s.status] + (s.model ? " · " + s.model : "");
}




function renderShelf(id, list) {
  const shelf = $(id);
  shelf.hidden = !list.length;
  $(".count", shelf).textContent = list.length ? `(${list.length})` : "";
  $(".shelf-list", shelf).innerHTML = list.slice().reverse().map((s) =>
    `<button type="button" class="shelf-item" data-sid="${esc(s.id)}">${Sprites.svg(s.id, null, 24)}` +
    `<span><b>${esc(s.name)}</b><small>${esc(s.project)} · ${esc(ago(s.active))}</small></span></button>`).join("");
}

const Hooks = { header: [], panels: {}, forms: {}, panelActions: {} };   // each script adds to these, so one failing script can't break the others

// Errors on the phone are otherwise invisible: show them once and send them to server.log.
const seenErrors = new Set();
function reportError(err, where) {
  const message = String((err && err.message) || err || "unknown error").slice(0, 300);
  const key = where + message;
  if (seenErrors.has(key)) return;
  seenErrors.add(key);
  console.error(where, err);
  let bar = $("#client-error");
  if (!bar) {
    bar = document.createElement("button");
    bar.type = "button";
    bar.id = "client-error";
    bar.className = "client-error";
    bar.addEventListener("click", () => bar.remove());
    document.body.appendChild(bar);
  }
  bar.textContent = `⚠ ${where}: ${message} (tap to hide)`;
  api("/api/client-error", { where, message, stack: String((err && err.stack) || "").slice(0, 2000), ua: navigator.userAgent })
    .catch(() => {});
}
window.addEventListener("error", (ev) => {
  // a script that failed to download fires on its element, not as an ErrorEvent
  if (ev.target && ev.target.tagName === "SCRIPT") {
    const old = ev.target;
    if (old.dataset.retried) { reportError("could not load " + old.src, "load"); return; }
    // try once more; async=false keeps retried scripts in their original order
    const again = document.createElement("script");
    again.src = old.src.split("?")[0] + "?retry=" + Date.now();
    again.async = false;
    again.dataset.retried = "1";
    old.after(again);
  }
  else if (ev instanceof ErrorEvent) reportError(ev.error || ev.message, "page" + (ev.filename ? " " + ev.filename.split("/").pop() + ":" + ev.lineno : ""));
}, true);
window.addEventListener("unhandledrejection", (ev) => reportError(ev.reason, "promise"));

function renderHall() {
  const all = UI.state.sessions;

  const needs = all.filter((s) => s.status === "needs");
  $("#notice-board").hidden = !needs.length;
  $("#notices").innerHTML = needs.map((s) =>
    `<button type="button" class="notice" data-sid="${esc(s.id)}"><b>${esc(s.name)}</b><span>${esc(s.note || W.short.needs)}</span></button>`).join("");

  renderShelf("#returned", all.filter((s) => s.status === "finished" && !s.staff));
  renderShelf("#archives", all.filter((s) => s.status === "archived"));

  const count = (st) => all.filter((s) => s.status === st && !s.helper).length;
  $("#tally").innerHTML =
    `<span class="t-working">⚔ ${count("working")} questing</span>` +
    `<span class="t-needs">! ${count("needs")} await you</span>` +
    `<span class="t-idle">☾ ${count("idle")} resting</span>` +
    (UI.state.gold ? `<span class="t-gold${UI.state.gold.paused ? " broke" : ""}">⛁ ${goldLabel(UI.state.gold.gold)} gold</span>` : "") +
    (UI.state.cost_today !== undefined ? `<span class="t-cost">${money(UI.state.cost_today)} today</span>` : "");
  $("#greeting").textContent = `Welcome back, ${UI.state.name || "guildmaster"}.`;
  // each hook on its own, so one failing part can't take the others down
  Hooks.header.forEach((fn) => { try { fn(UI.state); } catch (e) { reportError(e, "header"); } });
}

// ---- the sheet: shared frame ------------------------------------------

function showSheet() {
  $("#sheet").hidden = false;
  $("#backdrop").hidden = false;
  document.body.classList.add("sheet-open");
}

function openSheet(sid, push) {
  UI.mode = "session";
  UI.open = sid;
  UI.detail = null;
  UI.footKey = null;
  if (!UI.run || UI.run.sid !== sid) UI.run = null;
  $("#sheet-body").innerHTML = '<p class="loading">Unrolling the scroll…</p>';
  $("#sheet-foot").innerHTML = "";
  $("#sheet-actions").innerHTML = "";
  showSheet();
  if (push !== false) history.pushState({ sheet: sid }, "");
  loadDetail(true);
}

// A panel is any non-session sheet: settings, new job, and the stage 4 panels.
function openPanel(name, push, arg) {
  const panel = Hooks.panels[name];
  if (!panel) return;
  UI.mode = name;
  UI.arg = arg || null;
  UI.open = null;
  UI.detail = null;
  UI.footKey = null;
  $("#sheet-portrait").innerHTML = panel.icon || "";
  $("#sheet-name").textContent = panel.title;
  $("#sheet-meta").textContent = panel.subtitle || "";
  $("#sheet-actions").innerHTML = "";
  $("#sheet-foot").innerHTML = "";
  $("#sheet-body").innerHTML = '<p class="loading">One moment…</p>';
  showSheet();
  if (push !== false) history.pushState({ panel: name, arg: UI.arg }, "");
  Promise.resolve(panel.render($("#sheet-body"), true)).catch((e) => {
    $("#sheet-body").innerHTML = `<p class="loading">${esc(e.message)}</p>`;
  });
}

function closeSheet(fromPop) {
  if (!UI.mode) return;
  UI.mode = null;
  UI.open = null;
  UI.detail = null;
  $("#sheet").hidden = true;
  $("#backdrop").hidden = true;
  document.body.classList.remove("sheet-open");
  if (!fromPop && history.state && (history.state.sheet || history.state.panel)) history.back();
}

// ---- the session sheet ----------------------------------------------------

async function loadDetail(first) {
  const sid = UI.open;
  if (!sid) return;
  try {
    const d = await api("/api/session/" + sid);
    if (UI.open !== sid) return;
    UI.detail = d;
    if (d.run && !d.run.done && (!UI.run || UI.run.id !== d.run.id)) { UI.run = d.run; followRun(d.run.id); }
    renderSheet(d, first);
  } catch (e) {
    if (e.status === 404 && UI.run && UI.run.sid === sid) {
      UI.detail = { id: sid, name: "A new quest", status: "working", messages: [], queue_items: [], pending: [] };
      renderSheet(UI.detail, first);
    } else if (first) {
      $("#sheet-body").innerHTML = `<p class="loading">Could not read this scroll (${esc(e.message)}).</p>`;
    }
  }
}

function messageHTML(m) {
  const tools = (m.tools || []).map((t) => `<li>${esc(t)}</li>`).join("");
  const body = m.role === "assistant" ? MD.render(m.text) : `<p>${esc(m.text).replace(/\n/g, "<br>")}</p>`;
  return `<article class="msg ${m.role}">${m.text ? `<div class="md">${body}</div>` : ""}` +
    (tools ? `<ul class="tools">${tools}</ul>` : "") +
    `<time>${esc(ago(m.ts))}</time></article>`;
}

function runHTML(run) {
  if (!run) return "";
  if (run.done && run.ok) return "";  // the reply is in the transcript now
  const events = run.events.map((e) => {
    if (e.k === "text") return `<div class="md">${MD.render(e.t)}</div>`;
    if (e.k === "tool") return `<ul class="tools"><li>${esc(e.t)}</li></ul>`;
    return `<p class="run-error">${esc(e.t)}</p>`;
  }).join("");
  const head = run.done
    ? `<div class="run-head failed">The reply did not finish.</div>`
    : `<div class="run-head"><span class="quill" aria-hidden="true"></span>Writing a reply… <button type="button" class="btn" data-act="stop" data-rid="${esc(run.id)}">Stop</button></div>`;
  return `<section id="run-live" class="run">${head}<article class="msg user"><div class="md"><p>${esc(run.text)}</p></div></article>${events}</section>`;
}

function queueHTML(d) {
  const items = d.queue_items || [];
  if (!items.length) return "";
  return `<section class="queue"><h4>Scrolls waiting to be read (${items.length})</h4>` + items.map((q) =>
    `<div class="q-item"><p>${esc(q.text)}</p><div class="q-row"><small>${esc(W.modes[q.mode] || q.mode)} · ${esc(ago(q.ts))}</small>` +
    `<button type="button" class="btn" data-act="qnow" data-qid="${esc(q.id)}">Send now</button>` +
    `<button type="button" class="btn" data-act="qremove" data-qid="${esc(q.id)}">Remove</button></div></div>`).join("") + `</section>`;
}

function footerHTML(d) {
  if (d.desktop) {
    return `<div class="desktop-note"><p><b>${esc(d.name)}</b> is open in the Claude app on your Mac, so I won't type into it from here.</p>` +
      `<a class="btn primary" href="claude://">Open in Claude</a></div>`;
  }
  if (d.status === "archived") return `<p class="foot-note">Archived. Restore it to send word.</p>`;
  const o = UI.options || { modes: ["acceptEdits"], default_mode: "acceptEdits" };
  return `<form id="say" class="say" autocomplete="off">` +
    `<div class="chips">${(Hooks.chips ? Hooks.chips(d) : "") + W.quick.map((q) => `<button type="button" class="chip-btn" data-act="quick" data-text="${esc(q)}">${esc(q)}</button>`).join("")}</div>` +
    `<textarea name="text" rows="2" maxlength="20000" placeholder="Send word to ${esc(d.name)}…" aria-label="Message"></textarea>` +
    `<div class="say-row"><select name="mode" aria-label="Permission mode">${options(o.modes, W.modes, o.default_mode)}</select>` +
    `<button type="submit" class="btn primary">Send</button></div></form>`;
}

// The sheet is rebuilt on every poll (every 4 s, and while a run streams). A dropdown or text field inside it would be
// destroyed under the finger, which on a phone means the picker never opens. While someone is using a control in the
// sheet body, hold the rebuild and catch up as soon as they let go.
const Hold = { until: 0, deferred: null };
const BODY_CONTROL = "#sheet-body select, #sheet-body input, #sheet-body textarea";

function usingControl() {
  const a = document.activeElement;
  if (a && a.closest && a.closest(BODY_CONTROL)) return true;
  return Date.now() < Hold.until;
}

function catchUp() {
  Hold.until = 0;
  const d = Hold.deferred;
  Hold.deferred = null;
  if (d && UI.open === d.id) renderSheet(UI.detail || d, false);
}

document.addEventListener("pointerdown", (ev) => {
  if (ev.target.closest && ev.target.closest(BODY_CONTROL)) Hold.until = Date.now() + 10000;
}, true);
document.addEventListener("change", (ev) => {
  if (ev.target.closest && ev.target.closest(BODY_CONTROL)) { Hold.until = 0; setTimeout(catchUp, 600); }
}, true);
document.addEventListener("focusout", (ev) => {
  if (ev.target.closest && ev.target.closest(BODY_CONTROL)) setTimeout(() => { if (!usingControl()) catchUp(); }, 400);
}, true);

function renderSheet(d, first) {
  if (!first && usingControl()) { Hold.deferred = d; return; }
  const body = $("#sheet-body");
  const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 120;
  MD.reset();
  $("#sheet-portrait").innerHTML = Sprites.svg(d.staff ? d.staff.seed : d.id, d.staff && d.staff.kind, 44);
  $("#sheet-name").textContent = d.staff ? d.staff.name : d.name;
  const meta = [d.staff ? d.staff.title : "", d.folder || d.project, d.branch, d.model, d.started ? "began " + ago(d.started) : "", d.cost ? money(d.cost) : ""].filter(Boolean);
  $("#sheet-meta").innerHTML = `<span class="pill p-${esc(d.status)}">${esc(W.status[d.status])}</span> ` + meta.map(esc).join(" · ");
  $("#sheet-actions").innerHTML = d.started === undefined ? "" :
    `<button type="button" data-act="rename">Rename</button>` +
    `<button type="button" data-act="archive">${d.status === "archived" ? "Restore" : "Archive"}</button>` +
    (d.note && d.status !== "idle" ? `<span class="note n-${esc(d.status)}">${esc(d.note)}</span>` : "");
  const run = UI.run && UI.run.sid === d.id ? UI.run : d.run;
  const msgs = d.messages.length ? d.messages.map(messageHTML).join("") : (run ? "" : '<p class="loading">No words exchanged yet.</p>');
  const pending = d.pending && d.pending.length && !(run && !run.done) ? `<p class="pending">Working on: ${d.pending.map(esc).join(", ")}</p>` : "";
  const top = Hooks.sheetTop ? Hooks.sheetTop(d) : "";
  body.innerHTML = top + msgs + pending + runHTML(run) + queueHTML(d);
  const footKey = JSON.stringify([d.id, !!d.desktop, d.status === "archived"]);
  if (UI.footKey !== footKey) { UI.footKey = footKey; $("#sheet-foot").innerHTML = footerHTML(d); }
  // a new sheet opens on the profile card; after that, stay pinned to the latest words
  if (first && top) body.scrollTop = 0;
  else if (first || nearBottom) body.scrollTop = body.scrollHeight;
}

// Long-poll a run so the reply streams into the sheet.
async function followRun(rid) {
  if (UI.following === rid) return;
  UI.following = rid;
  let v = -1;
  while (UI.following === rid) {
    let run;
    try { run = await api(`/api/run/${rid}?v=${v}`); }
    catch (e) { await new Promise((r) => setTimeout(r, 2000)); continue; }
    v = run.v;
    UI.run = run;
    const live = $("#run-live");
    if (UI.mode === "session" && UI.open === run.sid) {
      if (live && !run.done) {
        const body = $("#sheet-body");
        const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 120;
        MD.reset();
        live.outerHTML = runHTML(run);
        if (nearBottom) body.scrollTop = body.scrollHeight;
      } else {
        loadDetail(false);
      }
    }
    if (run.done) {
      UI.following = null;
      setTimeout(() => loadDetail(false), 600);
      poll();
      break;
    }
  }
}

const ACTIONS = {
  async rename(d) {
    const name = prompt("A new name for this adventurer:", d.name);
    if (name === null) return;
    await api(`/api/session/${d.id}/rename`, { name });
    toast(name.trim() ? "Renamed." : "Name reset.");
  },
  async archive(d) {
    const archived = d.status !== "archived";
    await api(`/api/session/${d.id}/archive`, { archived });
    toast(archived ? "Sent to the archives." : "Back in the hall.");
  },
  async stop(d, btn) {
    await api(`/api/run/${btn.dataset.rid}/stop`, {});
    toast("Asked them to stop.");
  },
  async qnow(d, btn) {
    const r = await api(`/api/session/${d.id}/queue/${btn.dataset.qid}/now`, {});
    if (r.run) followRun(r.run);
    toast(r.front ? "Moved to the front; it goes right after the current reply." : "Sent.");
  },
  async qremove(d, btn) {
    await api(`/api/session/${d.id}/queue/${btn.dataset.qid}/remove`, {});
    toast("Removed.");
  },
  async quick(d, btn) {
    const ta = $("#say textarea");
    if (ta) { ta.value = btn.dataset.text; ta.focus(); }
  },
};

async function sendWord(form) {
  const d = UI.detail;
  if (!d) return;
  const text = form.text.value.trim();
  const mode = form.mode.value;
  if (!text) { form.text.focus(); return; }
  const r = await withSeal("bypass:" + d.id, isRisky(mode), (seal) =>
    api(`/api/session/${d.id}/send`, Object.assign({ text, mode }, seal)));
  form.text.value = "";
  if (r.run) { UI.run = { id: r.run, sid: d.id, events: [], text, done: false, v: -1 }; followRun(r.run); }
  toast(r.queued ? "They're busy; your message is queued." : "Sent.");
  loadDetail(false);
}

// ---- panels: new job + settings --------------------------------------------

Hooks.panels.job = {
  title: "Post a quest",
  subtitle: "Start a new session in one of your project folders.",
  icon: '<span class="panel-icon">📜</span>',
  async render(body) {
    const [{ projects }, o] = await Promise.all([api("/api/projects"), getOptions()]);
    body.innerHTML = `<form id="job-form" class="form">` +
      `<label>Project folder<select name="project" required>${projects.map((p) => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join("")}</select></label>` +
      `<label>The quest (first message)<textarea name="text" rows="6" maxlength="20000" required placeholder="What should they do?"></textarea></label>` +
      `<label>Permissions<select name="mode">${options(o.modes, W.modes, o.default_mode)}</select></label>` +
      `<label>Model<select name="model">${options(o.models, W.models, "")}</select></label>` +
      (Hooks.jobExtras ? Hooks.jobExtras() : "") +
      (o.claude ? "" : `<p class="run-error">claude was not found on the Mac. Set claude_bin in config.json.</p>`) +
      `<button type="submit" class="btn primary wide">Post the quest</button></form>`;
  },
};

async function postQuest(form) {
  const body = {
    project: form.project.value, text: form.text.value.trim(), mode: form.mode.value, model: form.model.value,
    pm: form.pm ? form.pm.checked : false,
    override: !!(form.override && form.override.checked),
  };
  if (!body.text) return;
  const r = await withSeal("bypass:new", isRisky(body.mode), (seal) => api("/api/job", Object.assign(body, seal)));
  if (r.queued_for_gold) { toast(r.reason || "Queued until the treasury refills."); closeSheet(); return; }
  UI.run = { id: r.run, sid: r.sid, events: [], text: body.text, done: false, v: -1 };
  history.replaceState({ sheet: r.sid }, "");
  openSheet(r.sid, false);
  followRun(r.run);
  toast("Quest posted.");
}

Hooks.panels.settings = {
  title: "Ravens & seals",
  subtitle: "Notifications to your phone, and the codes for risky actions.",
  icon: '<span class="panel-icon">🪶</span>',
  async render(body) {
    const key = await api("/api/push/key");
    const secure = window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window;
    body.innerHTML = `<section class="panel-sec"><h3>Ravens (push notifications)</h3>` +
      `<p>${key.subscribed ? "A phone is listening for ravens." : "No phone is listening yet."} ` +
      `You'll hear when someone awaits your word or returns from a long quest. Phone seals arrive the same way.</p>` +
      (secure
        ? `<div class="btn-row"><button type="button" class="btn primary" data-panel-act="subscribe">${key.subscribed ? "Use this phone instead" : "Send ravens to this phone"}</button>` +
          (key.subscribed ? `<button type="button" class="btn" data-panel-act="test">Send a test raven</button><button type="button" class="btn" data-panel-act="unsubscribe">Stop ravens</button>` : "") + `</div>`
        : `<p class="warn">Ravens need https. On the Mac run <code>tailscale serve --bg --https=443 http://127.0.0.1:7777</code>, then open the <code>https://…ts.net</code> address on the phone and add it to the Home Screen.</p>`) +
      `</section><section class="panel-sec"><h3>Exact "needs you" for permission prompts</h3>` +
      `<p>On the Mac run <code>~/Projects/agent-office/tools/office install-hook</code>. It adds a Notification hook so a session waiting on a permission prompt shows up here at once.</p></section>` +
      `<section class="panel-sec"><h3>The hall</h3><p>Version <code>${esc(UI.version || "")}</code>. Restart on the Mac: <code>launchctl kickstart -k gui/$(id -u)/com.bojjifomo.agent-office</code></p></section>`;
  },
};

function b64ToBytes(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((b64.length + 3) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}

const PANEL_ACTIONS = {
  async subscribe() {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") throw new Error("Notifications were not allowed.");
    const reg = await navigator.serviceWorker.ready;
    const { key } = await api("/api/push/key");
    const old = await reg.pushManager.getSubscription();
    if (old) await old.unsubscribe();
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
    await api("/api/push/subscribe", { subscription: sub.toJSON() });
    toast("Ravens will find this phone.");
  },
  async test() {
    const r = await api("/api/push/test", {});
    toast(r.sent ? "Raven sent." : "The raven got lost: " + r.error);
  },
  async unsubscribe() {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) await sub.unsubscribe();
    await api("/api/push/unsubscribe", {});
    toast("No more ravens.");
  },
};

async function getOptions() {
  if (!UI.options) UI.options = await api("/api/options");
  return UI.options;
}

// ---- polling + events -------------------------------------------------------

async function poll() {
  try {
    const state = await api("/api/state");
    if (UI.version && state.version !== UI.version) { location.reload(); return; }
    UI.version = state.version;
    UI.state = state;
    try { renderHall(); } catch (e) { reportError(e, "hall"); }
    if (UI.mode === "session") loadDetail(false);
    else if (UI.mode && Hooks.panels[UI.mode] && Hooks.panels[UI.mode].refresh) Hooks.panels[UI.mode].refresh($("#sheet-body"));
  } catch (e) {
    $("#greeting").textContent = "The hall is unreachable. Retrying…";
  }
}

function startPolling() {
  clearInterval(UI.timer);
  UI.timer = setInterval(() => { if (!document.hidden) poll(); }, POLL_MS);
}

async function guarded(btn, fn) {
  btn.disabled = true;
  try { await fn(); await poll(); }
  catch (e) { toast(e.message); }
  finally { btn.disabled = false; }
}

document.addEventListener("click", async (ev) => {
  const copyBtn = ev.target.closest(".copy");
  if (copyBtn) {
    const ok = await MD.copy(MD.copyText(copyBtn.dataset.copy) || "");
    copyBtn.classList.add(ok ? "done" : "fail");
    setTimeout(() => copyBtn.classList.remove("done", "fail"), 1200);
    toast(ok ? "Copied." : "Copy failed. Long-press to select instead.");
    return;
  }
  const act = ev.target.closest("[data-act]");
  if (act && UI.detail && ACTIONS[act.dataset.act]) return guarded(act, () => ACTIONS[act.dataset.act](UI.detail, act));
  const pact = ev.target.closest("[data-panel-act]");
  if (pact) {
    const fn = PANEL_ACTIONS[pact.dataset.panelAct] || (Hooks.panelActions || {})[pact.dataset.panelAct];
    if (fn) return guarded(pact, async () => { if (await fn(pact) !== false && UI.mode && Hooks.panels[UI.mode]) Hooks.panels[UI.mode].render($("#sheet-body")); });
  }
  const open = ev.target.closest("[data-open]");
  if (open) { openPanel(open.dataset.open, true, open.dataset.arg); return; }
  const member = ev.target.closest("[data-staff]");
  if (member) { openPanel("staff", true, member.dataset.staff); return; }
  const spot = ev.target.closest("[data-sid]");
  if (spot) { openSheet(spot.dataset.sid); return; }
  if (ev.target.id === "backdrop" || ev.target.closest("#sheet-close")) closeSheet();
});

document.addEventListener("submit", (ev) => {
  const form = ev.target;
  ev.preventDefault();
  const btn = form.querySelector("[type=submit]");
  if (form.id === "say") guarded(btn, () => sendWord(form));
  else if (form.id === "job-form") guarded(btn, () => postQuest(form));
  else if (Hooks.forms && Hooks.forms[form.id || form.dataset.form]) guarded(btn, () => Hooks.forms[form.id || form.dataset.form](form, ev.submitter));
});

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape") closeSheet();
  // Cmd/Ctrl+Enter sends from the message box.
  if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey) && ev.target.closest("#say")) ev.target.form.requestSubmit();
});
window.addEventListener("popstate", (ev) => {
  if (ev.state && ev.state.sheet) openSheet(ev.state.sheet, false);
  else if (ev.state && ev.state.panel) openPanel(ev.state.panel, false, ev.state.arg);
  else closeSheet(true);
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

function openFromHash() {
  const m = location.hash.match(/^#s=([0-9a-f-]{36})$/);
  if (m) { history.replaceState(null, "", "/"); openSheet(m[1]); }
  const p = location.hash.match(/^#p=([0-9a-f]{8})$/);
  if (p) { history.replaceState(null, "", "/"); openPanel("petitions", true, p[1]); }
  if (location.hash === "#usage") { history.replaceState(null, "", "/"); openPanel("report", true); }
  if (location.hash === "#chain") { history.replaceState(null, "", "/"); openPanel("chain", true); }
}
window.addEventListener("hashchange", openFromHash);

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}

getOptions().catch(() => {});
poll().then(openFromHash);
startPolling();
