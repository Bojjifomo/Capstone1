// The header: clock, earnings, the four tiles, the Mac's health, Claude limits, the Eco button,
// the icons next to Find someone, the People / Chat / Alerts tabs, and the Alerts view.
"use strict";

const Header = (() => {
  const gbs = (b) => b == null ? "" : b < 1e9 ? Math.round(b / 1e6) + " MB" : (b / 1e9).toFixed(b < 1e10 ? 1 : 0) + " GB";
  const hm = (ts) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const day = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d.getDay()];
    const sameDay = new Date().toDateString() === d.toDateString();
    return (sameDay ? "" : day + " ") + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  };
  const days = (s) => s >= 86400 ? (s / 86400).toFixed(1) + "d" : s >= 3600 ? Math.floor(s / 3600) + "h" : Math.floor(s / 60) + "m";
  let view = "people";

  function clock() {
    const c = $("#clock");
    if (!c) return;
    try {
      c.textContent = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date());
    } catch (e) { c.textContent = new Date().toTimeString().slice(0, 5); }
  }

  function tiles(state) {
    const all = state.sessions || [];
    const staff = (state.team && state.team.staff) || [];
    const working = all.filter((s) => s.status === "working" && !s.helper).length + staff.filter((m) => m.status === "working" && !m.sid).length;
    const needs = all.filter((s) => s.status === "needs").length + ((state.chain && state.chain.needs_you) || 0);
    const pr = state.programs || {};
    const trouble = (pr.trouble || 0) + ((state.chain && state.chain.open) || 0);
    $("#tiles").innerHTML =
      `<button type="button" class="tile t-work" data-tile="work"><b>${working}</b><small>working</small></button>` +
      `<button type="button" class="tile t-needs${needs ? " hot" : ""}" data-tile="needs"><b>${needs}</b><small>need you</small></button>` +
      `<button type="button" class="tile t-bots" data-open="bots"><b>${pr.up || 0}</b><small>bots up</small></button>` +
      `<button type="button" class="tile t-trouble${trouble ? " hot" : ""}" data-view="alerts"><b>${trouble}</b><small>in trouble</small></button>`;
  }

  function health(state) {
    const h = state.system || {};
    const bits = [];
    if (h.disk && h.disk.total) {
      bits.push(`<span>Disk <b>${gbs(h.disk.free)}</b> free</span>`);
      if (h.can_free) bits.push(`<span class="good">can free <b>${gbs(h.can_free)}</b></span>`);
    }
    if (h.backup && h.backup.last) bits.push(`<span>Backup <b>${esc(ago(h.backup.last))}</b></span>`);
    if (h.cpu && h.cpu.pct != null) bits.push(`<span${h.cpu.pct > 80 ? ' class="warn"' : ""}>CPU <b>${h.cpu.pct}%</b></span>`);
    if (h.temperature && h.temperature.c) bits.push(`<span>🌡 <b>${Math.round(h.temperature.c)}°C</b></span>`);
    if (h.memory && h.memory.free_pct != null) bits.push(`<span${h.memory.free_pct < 15 ? ' class="warn"' : ""}>Memory <b>${h.memory.free_pct}%</b> free</span>`);
    if (h.swap && h.swap.used != null) bits.push(`<span>Swap <b>${gbs(h.swap.used)}</b></span>`);
    if (h.uptime && h.uptime.seconds) bits.push(`<span>Up <b>${days(h.uptime.seconds)}</b></span>`);
    const petitions = state.petitions ? `<button type="button" class="hl" data-open="petitions">Proposals <b>${state.petitions} open</b></button>` : "";
    $("#health").innerHTML = petitions + bits.join("");
  }

  function limits(state) {
    const box = $("#limits");
    const L = state.limits || {};
    const rows = [];
    const bar = (label, pct, right, cls) =>
      `<div class="lim"><span class="lim-l">${label}</span><span class="lim-bar ${cls || ""}"><i data-w="${Math.max(0, Math.min(100, pct)).toFixed(1)}"></i></span><span class="lim-r">${right}</span></div>`;
    if (L.on) {
      if (L.windows && L.windows.length) {
        L.windows.filter((w) => w.key === "five_hour" || w.key === "seven_day").forEach((w) => {
          const cls = w.pct >= 90 ? "red" : w.pct >= 70 ? "amber" : "";
          rows.push(bar(`${esc(w.label)} <b class="${cls}">${Math.round(w.pct)}%</b>`, w.pct, w.resets ? "resets " + esc(hm(w.resets)) : "", cls));
        });
      } else {
        rows.push(`<div class="lim muted">Claude limits: ${esc(L.error || (L.checking ? "checking…" : "not checked yet"))}</div>`);
      }
    }
    const spend = state.cost_today || 0;
    const g = state.gold || {};
    const right = g.metered ? `${money(spend)} of ${money(g.budget)} today` : `${money(spend)} today`;
    rows.push(bar(`API prices <b>${money(spend)}</b>`, g.metered && g.budget ? spend / g.budget * 100 : 0, right, g.paused ? "red" : "green"));
    if (state.grok_today != null && (state.grok_today > 0 || (state.eco && state.eco.helpers && state.eco.helpers.includes("grok")))) {
      rows.push(`<div class="lim muted">Grok <b>${money(state.grok_today)}</b> today</div>`);
    }
    box.innerHTML = rows.join("");
    if (typeof applyBars === "function") applyBars(box);
  }

  function eco(state) {
    const b = $("#eco-btn");
    const e = state.eco || {};
    b.classList.toggle("on", !!e.all);
    b.setAttribute("aria-pressed", e.all ? "true" : "false");
    b.textContent = e.all ? `🌱 Eco: ${e.lead} plans · ${(e.helpers || []).join("/")} work` : "🌱 Eco: off";
    b.title = e.all ? "Every new session: the lead plans and reviews on " + e.lead + ", helpers do the work" : "Tap to run every new session in eco mode";
  }

  function icons(state) {
    const all = state.sessions || [];
    const staff = (state.team && state.team.staff) || [];
    const done = all.filter((s) => s.status === "finished").length;
    const queued = all.reduce((n, s) => n + (s.queue || 0), 0);
    const alerts = (state.alerts || []).length;
    $("#hdr-icons").innerHTML =
      `<button type="button" class="hic" data-open="chain" title="Team">👥<b>${staff.length}</b></button>` +
      `<button type="button" class="hic" data-view="chat" title="Chat">💬</button>` +
      `<button type="button" class="hic ok" data-tile="done" title="Finished recently">✅<b>${done}</b></button>` +
      `<button type="button" class="hic" data-tile="queued" title="Queued messages">📋<b>${queued}</b></button>` +
      `<button type="button" class="hic${alerts ? " hot" : ""}" data-view="alerts" title="Alerts">🔔${alerts ? `<b>${alerts}</b>` : ""}</button>`;
    const badge = $("#alerts-badge");
    badge.hidden = !alerts;
    badge.textContent = alerts;
  }

  function earned(state) {
    const b = $("#earned");
    const m = state.money_summary;
    if (!m) { b.hidden = true; return; }
    b.hidden = false;
    const v = m.net_today;
    b.className = "earned " + (v >= 0 ? "pos" : "neg");
    b.textContent = (v >= 0 ? "+" : "−") + money(Math.abs(v)) + " today";
  }

  const ALERT_ICON = { needs: "🙋", incident: "🚨", bot: "🖥", petition: "📋", budget: "💵" };
  function alerts(state) {
    const box = $("#alerts");
    if (!box) return;
    const list = state.alerts || [];
    box.innerHTML = `<h3>Alerts <small>${list.length ? list.length + " open" : "all clear"}</small></h3>` +
      (list.length ? list.map((a) => {
        const attrs = a.sid ? `data-sid="${esc(a.sid)}"` : a.href ? `data-href="${esc(a.href)}"` : "";
        return `<button type="button" class="alert-row k-${esc(a.kind)}" ${attrs}><span class="ai">${ALERT_ICON[a.kind] || "•"}</span>` +
          `<span><b>${esc(a.title)}</b><small>${esc(a.text || "")}${a.level ? " · at " + esc(a.level) : ""}</small></span><time>${esc(ago(a.ts))}</time></button>`;
      }).join("") : `<p class="sub">Nothing needs you right now.</p>`);
  }

  function showView(name) {
    view = name;
    document.querySelectorAll("[data-view]").forEach((b) => { if (b.classList.contains("on") || b.closest(".view-tabs")) b.classList.toggle("on", b.dataset.view === name); });
    ["people", "chat", "alerts"].forEach((v) => { const sec = $("#view-" + v); if (sec) sec.hidden = v !== name; });
    if (name === "chat") { const box = $("#chat-lines"); if (box) box.scrollTop = box.scrollHeight; }
    const chips = $("#division-chips");
    if (chips) chips.hidden = name !== "people";
  }

  function render(state) {
    clock();
    $("#greeting").textContent = (typeof Office !== "undefined" ? Office.greeting() : "Today") + " · " + (state.name || "");
    $("#live-dot").classList.add("ok");
    tiles(state);
    health(state);
    limits(state);
    eco(state);
    icons(state);
    earned(state);
    alerts(state);
  }

  document.addEventListener("click", async (ev) => {
    const v = ev.target.closest("[data-view]");
    if (v) { showView(v.dataset.view); if (v.closest("#tiles, #hdr-icons, #earned")) $("#view-" + v.dataset.view).scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    const t = ev.target.closest("[data-tile]");
    if (t) {
      showView("people");
      if (typeof Directory !== "undefined") {
        const f = { work: "work", needs: "needs" }[t.dataset.tile];
        if (f) { const btn = document.querySelector(`[data-dir-filter="${f}"]`); if (btn) btn.click(); }
      }
      const target = t.dataset.tile === "done" ? $("#returned") : $("#everyone");
      if (target) { if (target.tagName === "DETAILS") target.open = true; target.scrollIntoView({ behavior: "smooth", block: "start" }); }
      return;
    }
    const a = ev.target.closest(".alert-row[data-href]");
    if (a) { location.hash = a.dataset.href; return; }
    if (ev.target.closest("#eco-btn")) {
      const btn = $("#eco-btn");
      btn.disabled = true;
      try {
        const e = await api("/api/eco", { on: !(UI.state && UI.state.eco && UI.state.eco.all) });
        toast(e.all ? `Eco on: ${e.lead} plans and reviews, ${e.helpers.join(" and ")} do the work.` : "Eco off: sessions run as you set them.");
        await poll();
      } catch (e) { toast(e.message); } finally { btn.disabled = false; }
    }
  });

  setInterval(clock, 15000);
  Hooks.header.push(render);
  if (UI.state) render(UI.state);
  return { showView };
})();
