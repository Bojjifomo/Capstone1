// Sheets: Budget (usage), Programs (launchd bots), Disk, Search, Log.
"use strict";

function gb(bytes) {
  if (bytes == null) return "";
  if (bytes < 1e6) return "<1 MB";
  if (bytes < 1e9) return Math.round(bytes / 1e6) + " MB";
  return (bytes / 1e9).toFixed(bytes < 1e10 ? 1 : 0) + " GB";
}

function tokens(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return Math.round(n / 1e3) + "k";
  return String(n);
}

// Bar widths are set through the CSSOM because the CSP forbids inline style attributes.
function applyBars(root) {
  root.querySelectorAll("[data-w]").forEach((el) => { el.style.width = Math.max(1, Number(el.dataset.w)) + "%"; });
}

function bar(fraction, cls) {
  return `<span class="bar ${cls || ""}"><i data-w="${(Math.min(1, fraction || 0) * 100).toFixed(1)}"></i></span>`;
}

const ICON = (e) => `<span class="panel-icon">${e}</span>`;

Hooks.panels.usage = {
  title: "Budget",
  subtitle: "Tokens and estimated cost, from the transcripts.",
  icon: ICON("💵"),
  async render(body) {
    const u = await api("/api/usage");
    const top = Math.max(...u.days.map((d) => d.cost), 0.0001);
    const topModel = Math.max(...u.models.map((m) => m.cost), 0.0001);
    const day = (d) => new Date(d + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
    body.innerHTML =
      `<section class="panel-sec stats"><div><b>${money(u.today)}</b><small>today</small></div><div><b>${money(u.week)}</b><small>last 7 days</small></div></section>` +
      `<section class="panel-sec"><h3>Each day</h3><div class="rows">` + u.days.slice().reverse().map((d) =>
        `<div class="row"><span class="k">${esc(day(d.day))}</span>${bar(d.cost / top, "gold")}<span class="v">${money(d.cost)}</span>` +
        (d.cost ? `<small class="sub">${tokens(d.input + d.cache_read + d.cache_write)} in · ${tokens(d.output)} out</small>` : "") + `</div>`).join("") + `</div></section>` +
      `<section class="panel-sec"><h3>By model</h3><div class="rows">` + u.models.map((m) =>
        `<div class="row"><span class="k">${esc(m.model)}</span>${bar(m.cost / topModel)}<span class="v">${money(m.cost)}</span></div>`).join("") + `</div></section>` +
      `<section class="panel-sec"><h3>Costliest sessions (30 days)</h3>` + u.sessions.map((s) =>
        `<button type="button" class="list-row" data-sid="${esc(s.id)}"><span><b>${esc(s.name)}</b><small>${esc(s.project)}${s.today ? " · " + money(s.today) + " today" : ""}</small></span><span class="v">${money(s.cost)}</span></button>`).join("") +
      `</section><p class="hint">${esc(u.note)}</p>`;
    applyBars(body);
  },
};

Hooks.panels.bots = {
  title: "Programs",
  subtitle: "Every program in the server room, then every launchd job on the Mac.",
  icon: ICON("🖥"),
  async render(body) {
    const [r, pr] = await Promise.all([api("/api/bots"), api("/api/programs").catch(() => ({ rows: [] }))]);
    const rows = (pr.rows || []).map((p) => {
      const state = p.ok ? "running" : p.ok === false ? "failed" : "unknown";
      return `<article class="bot b-${state}"><header><i class="dot"></i><b>${esc(p.name)}</b><span class="tag">${p.ok ? "up" : p.ok === false ? "in trouble" : "unchecked"}</span>` +
        (p.intern ? `<span class="tag">intern</span>` : "") + `</header>` +
        `<p class="sub">${esc(p.project)}${p.url ? " · " + esc(p.url) : ""}${p.launchd ? " · " + esc(p.launchd) : ""}${p.warden ? " · warden " + esc(p.warden) : ""}</p>` +
        (p.reasons && p.reasons.length ? `<p class="bot-error">${esc(p.reasons.join("; "))}</p>` : "") + `</article>`;
    }).join("");
    const servers = `<section class="panel-sec"><h3>Server room</h3>` + (rows || `<p class="sub">No known programs found in your project folders yet.</p>`) +
      `<p class="hint">Checked every 90 s: a URL that answers, a launchd job that is loaded, a file that is fresh. Add one as an intern in the Org chart to get a warden and incidents.</p></section>`;
    if (!r.jobs.length) {
      body.innerHTML = servers + `<p class="loading">${r.mac ? "No jobs in ~/Library/LaunchAgents." : "launchd jobs live in ~/Library/LaunchAgents on the Mac."}</p>`;
      return;
    }
    body.innerHTML = servers + `<section class="panel-sec"><h3>launchd jobs</h3>` + r.jobs.map((j) => {
      const state = j.running ? "running" : j.failed ? "failed" : j.loaded ? "sleeping" : "unloaded";
      return `<article class="bot b-${state}"><header><i class="dot"></i><b>${esc(j.label.replace(/^com\./, ""))}</b><span class="tag">${esc(state)}</span></header>` +
        `<p class="sub">${esc(j.schedule)} · ${esc(j.program)}${j.last_run ? " · last ran " + esc(ago(j.last_run)) : ""}${j.exit && j.exit !== "0" ? " · exit " + esc(j.exit) : ""}</p>` +
        (j.error ? `<p class="bot-error">${esc(j.error)}</p>` : "") +
        (j.ours || !j.loaded ? "" : `<button type="button" class="btn" data-panel-act="kick" data-job="${esc(j.id)}">Run now</button>`) + `</article>`;
    }).join("") + `</section>`;
  },
  refresh(body) { if (!body.contains(document.activeElement)) this.render(body); },
};

Hooks.panels.disk = {
  title: "Disk",
  subtitle: "Free space, the biggest folders, and a few safe cleaners.",
  icon: ICON("🏛"),
  async render(body, first) {
    const d = await api("/api/disk" + (first ? "" : ""));
    const used = 1 - d.free / d.total;
    const biggest = Math.max(...d.folders.map((f) => f.bytes), 1);
    body.innerHTML =
      `<section class="panel-sec"><div class="stats"><div><b>${gb(d.free)}</b><small>free of ${gb(d.total)}</small></div></div>${bar(used, used > 0.9 ? "red" : "gold")}</section>` +
      `<section class="panel-sec"><h3>Biggest folders ${d.scanning ? '<small class="sub">measuring…</small>' : `<button type="button" class="btn" data-panel-act="rescan">Measure again</button>`}</h3><div class="rows">` +
      (d.folders.length ? d.folders.map((f) => `<div class="row"><span class="k path">${esc(f.path)}</span>${bar(f.bytes / biggest)}<span class="v">${gb(f.bytes)}</span></div>`).join("") : `<p class="sub">${d.scanning ? "Measuring the disk, this takes a minute…" : "Nothing measured yet."}</p>`) +
      `</div></section><section class="panel-sec"><h3>Cleaners</h3><p class="hint">Only these fixed cleaners exist. Each needs a fresh code from your phone.</p>` +
      d.cleaners.map((c) => `<article class="cleaner"><div><b>${esc(c.title)}</b>${c.bytes ? ` <span class="v">${gb(c.bytes)}</span>` : ""}<small>${esc(c.what)}${c.last ? " Last run " + esc(ago(c.last.ts)) + ", freed " + gb(c.last.freed) + "." : ""}</small></div>` +
        (c.available ? `<button type="button" class="btn danger" data-panel-act="clean" data-cleaner="${esc(c.id)}">Clean</button>` : `<small class="sub">not installed</small>`) + `</article>`).join("") +
      `</section>`;
    applyBars(body);
  },
  refresh(body) { if (!body.contains(document.activeElement)) this.render(body); },
};

Hooks.panels.search = {
  title: "Search",
  subtitle: "Search every conversation, newest first.",
  icon: ICON("🔮"),
  render(body) {
    body.innerHTML = `<form id="search-form" class="search-form" role="search"><input type="search" name="q" placeholder="Words from any session…" aria-label="Search" autocomplete="off" enterkeyhint="search">` +
      `<button type="submit" class="btn primary">Search</button></form><div id="search-results"></div>`;
    const input = $("#search-form input");
    if (UI.lastSearch) { input.value = UI.lastSearch; Hooks.forms["search-form"]($("#search-form")); }
    setTimeout(() => input.focus(), 50);
  },
};

Hooks.forms["search-form"] = async (form) => {
  const q = form.q.value.trim();
  UI.lastSearch = q;
  const out = $("#search-results");
  if (q.length < 2) { out.innerHTML = '<p class="sub">Type at least two letters.</p>'; return; }
  out.innerHTML = '<p class="sub">Searching…</p>';
  const r = await api("/api/search?q=" + encodeURIComponent(q));
  out.innerHTML = (r.results.length ? r.results.map((h) =>
    `<button type="button" class="hit" data-sid="${esc(h.id)}"><span class="hit-head"><b>${esc(h.name)}</b><small>${esc(h.project)} · ${h.role === "user" ? "you" : "them"} · ${esc(ago(h.ts))}</small></span>` +
    `<span class="hit-text">${esc(h.before)}<mark>${esc(h.match)}</mark>${esc(h.after)}</span></button>`).join("") : '<p class="sub">Nothing found.</p>') +
    (r.truncated ? '<p class="hint">Showing the newest matches only.</p>' : "");
};

Hooks.panels.ledger = {
  title: "Log",
  subtitle: "Everything done from this page, plus token calls from outside (marked with their origin).",
  icon: ICON("📖"),
  async render(body) {
    const { entries } = await api("/api/ledger");
    body.innerHTML = entries.length ? entries.map((e) =>
      `<div class="ledger-row"><span class="tag t-${esc(e.kind)}">${esc(e.kind)}</span><span class="ledger-text">` +
      (e.origin ? `<span class="tag">${esc(e.origin)}</span> ` : "") +
      ((e.kind === "guild" || e.kind === "chatter") && e.by ? esc(e.by) + ": " : "") + `${esc(e.text)}</span><time>${esc(ago(e.ts))}</time>` +
      (e.sid ? `<button type="button" class="btn" data-sid="${esc(e.sid)}">Open</button>` : "") + `</div>`).join("")
      : '<p class="loading">Nothing logged yet.</p>';
  },
};

Object.assign(Hooks.panelActions, {
  async kick(btn) {
    const r = await api(`/api/bots/${btn.dataset.job}/run`, {});
    toast(`Woke ${r.started}.`);
  },
  async rescan() {
    await api("/api/disk?rescan=1");
    toast("Measuring the disk…");
  },
  async clean(btn) {
    const id = btn.dataset.cleaner;
    if (!confirm("Run this cleaner? A code will be sent to your phone.")) return false;
    const r = await withSeal("clean:" + id, true, (seal) => api(`/api/disk/clean/${id}`, seal));
    toast(`${r.title}: freed ${gb(r.freed)}.`);
  },
});
