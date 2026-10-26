// Agent Office front end: the guild hall scene, the session sheet, polling.
"use strict";

// Theme wording lives here so the whole page speaks "guild hall".
const W = {
  status: { working: "On a quest", needs: "Awaits your word", idle: "Resting", finished: "Returned", archived: "Archived" },
  short: { working: "questing", needs: "awaits you", idle: "resting", finished: "returned", archived: "archived" },
};

const $ = (sel, el) => (el || document).querySelector(sel);
const esc = MD.esc;
const POLL_MS = 4000;

const UI = { state: null, version: null, open: null, detail: null, spots: new Map(), timer: null };

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
  toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
}

// ---- the hall ---------------------------------------------------------

function statusLine(s) {
  if (s.desktop) return W.short[s.status] + " · in Claude app";
  if (s.status === "needs") return s.note || W.short.needs;
  if (s.status === "working" && s.note && s.note !== "thinking") return s.note;
  return W.short[s.status] + (s.model ? " · " + s.model : "");
}

function spotKey(s) {
  return JSON.stringify([s.name, s.status, statusLine(s), s.queue || 0, s.staff && s.staff.title]);
}

function spotInner(s) {
  const who = s.staff ? `<span class="role">${esc(s.staff.title)}</span>` : "";
  const queue = s.queue ? `<span class="queue-badge" title="Messages waiting">${s.queue}</span>` : "";
  return `<span class="bubble" aria-hidden="true">!</span>` +
    `<span class="zz" aria-hidden="true"><i>z</i><i>z</i></span>` +
    `<span class="char">${Sprites.svg(s.staff ? s.staff.seed : s.id, s.staff && s.staff.kind)}</span>` +
    `<span class="table" aria-hidden="true"><i class="candle"></i><i class="scroll"></i></span>` +
    `<span class="plate"><b>${esc(s.name)}</b>${who}<span class="st"><i class="dot"></i>${esc(statusLine(s))}</span></span>${queue}`;
}

function placeSpots(container, list) {
  const keep = new Set();
  list.forEach((s, idx) => {
    keep.add(s.id);
    let el = UI.spots.get(s.id);
    if (!el) {
      el = document.createElement("button");
      el.type = "button";
      el.dataset.sid = s.id;
      UI.spots.set(s.id, el);
    }
    const key = spotKey(s);
    if (el.dataset.key !== key) {
      el.dataset.key = key;
      el.className = "spot s-" + s.status + (s.desktop ? " desktop" : "");
      el.innerHTML = spotInner(s);
      el.setAttribute("aria-label", `${s.name}: ${W.status[s.status]}`);
    }
    if (el.parentNode !== container || container.children[idx] !== el) container.insertBefore(el, container.children[idx] || null);
  });
  return keep;
}

function renderShelf(id, list) {
  const shelf = $(id);
  shelf.hidden = !list.length;
  $(".count", shelf).textContent = list.length ? `(${list.length})` : "";
  $(".shelf-list", shelf).innerHTML = list.slice().reverse().map((s) =>
    `<button type="button" class="shelf-item" data-sid="${esc(s.id)}">${Sprites.svg(s.id, null, 24)}` +
    `<span><b>${esc(s.name)}</b><small>${esc(s.project)} · ${esc(ago(s.active))}</small></span></button>`).join("");
}

function renderHall() {
  const all = UI.state.sessions;
  const onFloor = all.filter((s) => !s.staff && !s.helper && ["working", "needs", "idle"].includes(s.status));
  const kept = placeSpots($("#floor"), onFloor);
  const wingKept = renderWings ? renderWings(all) : new Set();
  for (const [sid, el] of UI.spots) {
    if (!kept.has(sid) && !wingKept.has(sid)) { el.remove(); UI.spots.delete(sid); }
  }
  $("#empty").hidden = onFloor.length > 0;

  const needs = all.filter((s) => s.status === "needs");
  $("#notice-board").hidden = !needs.length;
  $("#notices").innerHTML = needs.map((s) =>
    `<button type="button" class="notice" data-sid="${esc(s.id)}"><b>${esc(s.name)}</b><span>${esc(s.note || W.short.needs)}</span></button>`).join("");

  renderShelf("#returned", all.filter((s) => s.status === "finished" && !s.staff));
  renderShelf("#archives", all.filter((s) => s.status === "archived"));

  const count = (st) => all.filter((s) => s.status === st && !s.helper).length;
  const today = all.reduce((sum, s) => sum + (s.cost_today || 0), 0);
  $("#tally").innerHTML =
    `<span class="t-working">⚔ ${count("working")} questing</span>` +
    `<span class="t-needs">! ${count("needs")} await you</span>` +
    `<span class="t-idle">☾ ${count("idle")} resting</span>` +
    (UI.state.cost_today !== undefined ? `<span class="t-gold">⛁ ${money(UI.state.cost_today || today)} today</span>` : "");
  $("#greeting").textContent = `Welcome back, ${UI.state.name || "guildmaster"}.`;
}

// Stage 3 replaces this with the division wings.
let renderWings = null;

// ---- the sheet --------------------------------------------------------

function openSheet(sid, push) {
  UI.open = sid;
  UI.detail = null;
  $("#sheet-body").innerHTML = '<p class="loading">Unrolling the scroll…</p>';
  $("#sheet-foot").innerHTML = "";
  $("#sheet-actions").innerHTML = "";
  $("#sheet").hidden = false;
  $("#backdrop").hidden = false;
  document.body.classList.add("sheet-open");
  if (push !== false) history.pushState({ sheet: sid }, "");
  loadDetail(true);
}

function closeSheet(fromPop) {
  if (!UI.open) return;
  UI.open = null;
  UI.detail = null;
  $("#sheet").hidden = true;
  $("#backdrop").hidden = true;
  document.body.classList.remove("sheet-open");
  if (!fromPop && history.state && history.state.sheet) history.back();
}

async function loadDetail(first) {
  const sid = UI.open;
  if (!sid) return;
  try {
    const d = await api("/api/session/" + sid);
    if (UI.open !== sid) return;
    UI.detail = d;
    renderSheet(d, first);
  } catch (e) {
    if (first) $("#sheet-body").innerHTML = `<p class="loading">Could not read this scroll (${esc(e.message)}).</p>`;
  }
}

function messageHTML(m) {
  const tools = (m.tools || []).map((t) => `<li>${esc(t)}</li>`).join("");
  const body = m.role === "assistant" ? MD.render(m.text) : `<p>${esc(m.text).replace(/\n/g, "<br>")}</p>`;
  return `<article class="msg ${m.role}">${m.text ? `<div class="md">${body}</div>` : ""}` +
    (tools ? `<ul class="tools">${tools}</ul>` : "") +
    `<time>${esc(ago(m.ts))}</time></article>`;
}

function sheetExtras(d) { return ""; }      // stage 2: live run + queue
function sheetFooter(d) { return ""; }      // stage 2: message box

function renderSheet(d, first) {
  const body = $("#sheet-body");
  const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
  MD.reset();
  $("#sheet-portrait").innerHTML = Sprites.svg(d.staff ? d.staff.seed : d.id, d.staff && d.staff.kind, 44);
  $("#sheet-name").textContent = d.name;
  const meta = [d.staff ? d.staff.title : "", d.folder || d.project, d.branch, d.model, "began " + ago(d.started), money(d.cost)].filter(Boolean);
  $("#sheet-meta").innerHTML = `<span class="pill p-${esc(d.status)}">${esc(W.status[d.status])}</span> ` + meta.map(esc).join(" · ");
  $("#sheet-actions").innerHTML =
    `<button type="button" data-act="rename">Rename</button>` +
    `<button type="button" data-act="archive">${d.status === "archived" ? "Restore" : "Archive"}</button>` +
    (d.note && d.status !== "idle" ? `<span class="note">${esc(d.note)}</span>` : "");
  const msgs = d.messages.length ? d.messages.map(messageHTML).join("") : '<p class="loading">No words exchanged yet.</p>';
  const pending = d.pending && d.pending.length ? `<p class="pending">Working on: ${d.pending.map(esc).join(", ")}</p>` : "";
  body.innerHTML = msgs + pending + sheetExtras(d);
  const foot = sheetFooter(d);
  if (foot !== null && !$("#sheet-foot").dataset.sticky) $("#sheet-foot").innerHTML = foot;
  if (first || nearBottom) body.scrollTop = body.scrollHeight;
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
};

// ---- polling ------------------------------------------------------------

async function poll() {
  try {
    const state = await api("/api/state");
    if (UI.version && state.version !== UI.version) { location.reload(); return; }
    UI.version = state.version;
    UI.state = state;
    renderHall();
    if (typeof afterPoll === "function") afterPoll(state);
    if (UI.open) loadDetail(false);
  } catch (e) {
    $("#greeting").textContent = "The hall is unreachable. Retrying…";
  }
}

let afterPoll = null;

function startPolling() {
  clearInterval(UI.timer);
  UI.timer = setInterval(() => { if (!document.hidden) poll(); }, POLL_MS);
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
  if (act && UI.detail && ACTIONS[act.dataset.act]) {
    act.disabled = true;
    try { await ACTIONS[act.dataset.act](UI.detail, act); await poll(); }
    catch (e) { toast(e.message); }
    finally { act.disabled = false; }
    return;
  }
  const spot = ev.target.closest("[data-sid]");
  if (spot) { openSheet(spot.dataset.sid); return; }
  if (ev.target.id === "backdrop" || ev.target.id === "sheet-close") closeSheet();
});

document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeSheet(); });
window.addEventListener("popstate", (ev) => {
  if (ev.state && ev.state.sheet) openSheet(ev.state.sheet, false);
  else closeSheet(true);
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

poll();
startPolling();
