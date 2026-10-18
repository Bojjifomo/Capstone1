"use strict";
// RH Wallet Finder dashboard. Plain JS, no build step.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ---------- formatting ----------
const num = (v) => (v === null || v === undefined || Number.isNaN(v) ? null : Number(v));
function compact(v, d = 1) {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(d) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(d) + "M";
  if (a >= 1e4) return (v / 1e3).toFixed(d) + "K";
  if (a >= 100) return v.toFixed(0);
  return v.toFixed(a >= 10 ? 1 : 2);
}
const usd = (v) => (num(v) === null ? "–" : Math.abs(v) < 0.005 ? "$0" : (v < 0 ? "-$" : "$") + compact(Math.abs(v)));
const f1 = (v) => (num(v) === null ? "–" : Number(v).toFixed(1));
const int = (v) => (num(v) === null ? "–" : Math.round(v).toString());
const pct0 = (v) => (num(v) === null ? "–" : Math.round(v) + "%");
const rangeFmt = (v) => (num(v) === null ? "–" : v >= 1e6 ? "full" : v >= 1e4 ? compact(v, 0) + "%" : Math.round(v) + "%");
function dur(s) {
  if (num(s) === null) return "–";
  s = Math.max(0, s);
  if (s < 60) return Math.round(s) + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return (s / 86400).toFixed(s < 864000 ? 1 : 0) + "d";
}
const short = (a) => a.slice(0, 6) + "…" + a.slice(-4);
function ago(ts) {
  if (!ts) return "never";
  const s = state.now - ts;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m ago";
}

// ---------- columns ----------
// heat: "good" = higher is better (green), "low" = lower is better, "n" = neutral tint
const COLS = [
  { k: "rank", t: "Rank", f: int, filter: "num", d: "Position on the board, by score." },
  { k: "score", t: "Score", f: f1, heat: "good", filter: "num", d: "0–100. Percentile blend of net, ROI, win rate, daily win rate, drawdown, recovery, fees and sample size; few closed positions pull it toward 50, losing wallets are capped at 45." },
  { k: "addr", t: "Address", l: 1, filter: "text", cls: "addr", f: (v, r) => esc(short(v)) + (r.label ? `<span class="lab">${esc(r.label)}</span>` : ""), d: "Click for positions, labels and the LP Agent check." },
  { k: "verdict", t: "Verdict", l: 1, filter: "text", d: "Elite ≥ 70, Strong ≥ 60, Decent ≥ 50, Weak ≥ 40, else Avoid." },
  { k: "net", t: "Net", f: usd, heat: "good", filter: "num", d: "Realised PnL in USD of positions closed in the window: everything taken out + fees − everything put in." },
  { k: "float", t: "Float", f: usd, heat: "good", filter: "num", d: "Unrealised PnL of positions still open (current value + unclaimed fees − deposits)." },
  { k: "roi", t: "ROI %", f: f1, heat: "good", filter: "num", d: "Net per month over the capital the wallet actually keeps deployed (time-weighted, never below its median position size)." },
  { k: "copy", t: "Copy", c: 1, filter: "text", f: (v) => `<span class="${v === "✓" ? "copy-ok" : v === "✗" ? "copy-no" : "copy-q"}">${esc(v)}</span>`, d: "Can a person follow it? ✓ median hold ≥ 30 min and ≤ 30 positions/day, ✗ bot pace (hold < 5 min or > 100/day), ? in between." },
  { k: "copiers", t: "Copiers", f: int, filter: "num", d: "Other wallets that opened the same pool with the exact same range within 60 s after this wallet, at least twice." },
  { k: "exposure", t: "Exposure", filter: "num", v: (r) => r.exposure * 100, f: (v, r) => `${usd(r.exposure_usd)} · ${Math.round(r.exposure * 100)}%`, amber: (r) => r.exposure >= 0.5, d: "Liquidity those copiers add on average per open, as a share of the wallet's own (and in USD at its median size). Amber at 50%+: the wallet gets crowded." },
  { k: "win", t: "Win %", f: int, heat: "good", filter: "num", d: "Closed positions that made money." },
  { k: "daily", t: "Daily%", f: pct0, heat: "good", filter: "num", d: "Days with closes that ended in profit." },
  { k: "dd", t: "DD %", f: f1, heat: "good", filter: "num", d: "Deepest drop of the realised PnL curve, as % of deployed capital." },
  { k: "recover", t: "Recover", f: (v, r) => (!v && !r.recover_open ? "–" : (r.recover_open ? ">" : "") + dur(v)), heat: "low", filter: "num", fv: (v) => v / 86400, unit: "days", d: "How long the deepest drawdown took to win back (\">\" = still under water)." },
  { k: "size", t: "Size/Pos", f: usd, heat: "n", filter: "num", d: "Median USD deposited per position." },
  { k: "eth", t: "ETH%", f: f1, heat: "n", filter: "num", d: "Share of deposits in ETH-paired pools (the rest is USDG-paired)." },
  { k: "mcap", t: "Mcap", f: usd, heat: "n", filter: "num", d: "Median current market cap of the tokens it LPs (supply × pool price)." },
  { k: "duration", t: "Duration", f: dur, heat: "n", filter: "num", fv: (v) => v / 3600, unit: "hours", d: "Median time a position stays open." },
  { k: "range", t: "Range", f: rangeFmt, heat: "n", filter: "num", d: "Median range width: upper price / lower price − 1." },
  { k: "fees", t: "Fees", f: usd, heat: "good", filter: "num", d: "LP fees earned (claimed + unclaimed), USD." },
  { k: "pos", t: "Pos", f: int, heat: "n", filter: "num", d: "Positions in the window (closed in it, or still open)." },
  { k: "open", t: "Open", f: int, filter: "num", d: "Positions open right now." },
  { k: "concur", t: "Concur", f: (v) => (num(v) === null ? "–" : v.toFixed(1) + "×"), heat: "n", filter: "num", d: "Average number of positions open at the same time." },
  { k: "open_now", t: "Open now", f: usd, heat: "n", filter: "num", d: "USD value sitting in open positions now." },
  { k: "worth", t: "Worth", f: usd, heat: "n", filter: "num", d: "ETH + USDG in the wallet + open LP value." },
  { k: "age", t: "Age", v: (r) => state.now - r.first_seen, f: (v, r) => (r.first_seen <= (state.status.data_from || 0) + 3600 ? ">" : "") + dur(v), filter: "num", fv: (v) => v / 86400, unit: "days", d: "Since its first LP in our data (\">\" = it was already active before our history starts)." },
  { k: "last", t: "Last", v: (r) => state.now - r.last_seen, f: dur, filter: "num", fv: (v) => v / 3600, unit: "hours", d: "Since its last LP action." },
  { k: "sim", t: "Pred-sim", l: 1, v: (r) => r.sim_pct, f: (v, r) => (r.sim_label ? `${esc(r.sim_label)} ${r.sim_pct}%` : "–"), heat: "n", filter: "num", d: "Closest starred/labelled wallet by LP style, and how close." },
  { k: "style", t: "Pred-model", l: 1, f: (v, r) => (v ? `${esc(v)} ${r.style_conf}%` : "–"), filter: "text", d: "Style: hold (Scalp < 30 min, Day < 24 h, Multiday) · token size (Degen < $1M mcap, Mid < $20M, Blue), with how many positions fit." },
  { k: "star", t: "★", c: 1, nosort: 1, f: (v, r) => `<button class="star ${r.starred ? "on" : ""}" data-star="${r.addr}">${r.starred ? "★" : "☆"}</button>`, d: "Watchlist: starred wallets get priced first and anchor Pred-sim." },
  { k: "lp", t: "LP", c: 1, nosort: 1, f: (v, r) => `<a href="https://app.lpagent.io/portfolio?address=${r.addr}&chain=ROBINHOOD" target="_blank" rel="noopener">LP ↗</a>`, d: "Open the wallet on LP Agent." },
  { k: "scan", t: "Scan", c: 1, nosort: 1, f: (v, r) => `<a href="https://robinhoodchain.blockscout.com/address/${r.addr}" target="_blank" rel="noopener">Scan ↗</a>`, d: "Open the wallet on the block explorer." },
];
const colByKey = Object.fromEntries(COLS.map((c) => [c.k, c]));
const val = (c, r) => (c.v ? c.v(r) : r[c.k]);

// ---------- state ----------
const state = {
  tab: "wallets", real: LS.get("rhwf.real", false), rows: [], status: {}, now: Date.now() / 1000,
  sort: { k: "score", dir: -1 }, filters: {}, quick: "", ranks: {},
};

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "content-type": "application/json", "x-token": window.TOKEN }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: "bad reply" }));
  if (!r.ok && !j.error) j.error = "HTTP " + r.status;
  return j;
}

function view() {
  const base = state.tab === "top1d" ? "d1" : "";
  return base + (state.real ? "real" : "raw");
}

// ---------- status ----------
async function loadStatus() {
  const s = await api("/api/status");
  if (s.error) return;
  state.status = s;
  state.now = s.now;
  $("#st-discovered").textContent = (s.discovered || 0).toLocaleString();
  $("#st-scored").textContent = (s.scored || 0).toLocaleString();
  $("#st-categories").textContent = (s.categories || 0).toLocaleString();
  $("#pl-metrics").textContent = (state.tab === "top1d" ? 1 : s.metric_days) + "d";
  $("#pl-data").textContent = ago(s.synced_at);
  $("#pl-score").textContent = ago(s.scored_at);
  const hist = s.data_from ? (s.now - s.data_from) / 86400 : 0;
  $("#pl-history").textContent = s.data_from ? `${hist < 1 ? dur(hist * 86400) : hist.toFixed(hist < 3 ? 1 : 0) + "d"} of ${s.backfill_days}d` : "–";
  $("#pl-eth").textContent = s.eth_usd ? "$" + Math.round(s.eth_usd).toLocaleString() : "–";
  $("#dot-data").className = "dot " + (!s.synced_at ? "" : s.now - s.synced_at < 1800 ? "ok" : "warn");
  $("#dot-score").className = "dot " + (!s.scored_at ? "" : s.now - s.scored_at < 3 * 3600 ? "ok" : "warn");
  const w = s.worker || {};
  const busy = w.state === "syncing" || w.state === "scoring";
  $("#dot-worker").className = "dot " + (w.error ? "bad" : busy ? "busy" : s.has_rpc ? "ok" : "warn");
  $("#worker-state").textContent = w.error ? "error: " + w.error : (w.state || "idle") + (w.progress && !w.progress.done ? " · loading history" : "");
  $("#rpc-label").textContent = s.rpc ? "RPC " + s.rpc : "";
  $("#log").textContent = (w.log || []).join("\n");
  const setup = $("#setup");
  if (!s.has_rpc) {
    setup.innerHTML = 'No RPC yet. Click <b>⚙ Settings</b> and paste your Robinhood Chain RPC URL (e.g. your SolidRPC link). The first sync starts right after.';
    setup.classList.remove("hidden");
  } else if (!s.scored && s.discovered === 0) {
    setup.textContent = "First sync is running: scanning Uniswap V4 LP activity from the newest blocks backwards. Wallets show up after the first scoring pass.";
    setup.classList.remove("hidden");
  } else setup.classList.add("hidden");
  realBanner();
}

function realBanner() {
  const b = $("#real-banner");
  if (!state.real) return b.classList.add("hidden");
  const fee = state.status.sell_fee ? (state.status.sell_fee * 100).toFixed(2) : "–";
  b.innerHTML = `<b>Real PnL</b>: Net, ROI, win rate, drawdown and recovery are all charged for selling the token a position returns, and the board is re-ranked on those figures. It costs <b>${fee}%</b> of the token sold back (pool fee + ${state.status.slippage ?? 1}% slippage).`;
  b.classList.remove("hidden");
}

// ---------- table ----------
async function loadRows() {
  const j = await api("/api/wallets?view=" + view());
  state.rows = j.rows || [];
  computeRanks(state.rows);
  render();
}

function computeRanks(rows) {
  // percentile per column for the heat colours
  state.ranks = {};
  for (const c of COLS) {
    if (!c.heat) continue;
    const vs = rows.map((r) => val(c, r)).filter((v) => num(v) !== null).sort((a, b) => a - b);
    state.ranks[c.k] = vs;
  }
}

function pctOf(c, v) {
  const vs = state.ranks[c.k];
  if (!vs || vs.length < 2 || num(v) === null) return null;
  const bis = (right) => {
    let lo = 0, hi = vs.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (vs[m] < v || (right && vs[m] === v)) lo = m + 1; else hi = m; }
    return lo;
  };
  const lo = bis(false), hi = bis(true);
  if (hi - lo === vs.length) return null; // everyone the same: no colour
  return ((lo + hi - 1) / 2) / (vs.length - 1);
}

function heatStyle(c, r) {
  if (!c.heat) return "";
  const p = pctOf(c, val(c, r));
  if (p === null) return "";
  if (c.heat === "n") return `background: rgba(var(--good), ${(p * 0.22).toFixed(3)})`;
  const g = c.heat === "low" ? 1 - p : p;
  if (g >= 0.5) return `background: rgba(var(--good), ${((g - 0.5) * 2 * 0.6).toFixed(3)})`;
  return `background: rgba(var(--bad), ${((0.5 - g) * 2 * 0.6).toFixed(3)})`;
}

function passes(r) {
  if (state.quick) {
    const q = state.quick.toLowerCase();
    if (!r.addr.includes(q) && !(r.label || "").toLowerCase().includes(q)) return false;
  }
  for (const [k, f] of Object.entries(state.filters)) {
    const c = colByKey[k];
    if (!c) continue;
    if (c.filter === "text") {
      const s = String(k === "addr" ? r.addr + " " + (r.label || "") : r[k] ?? "").toLowerCase();
      if (f.q && !s.includes(f.q.toLowerCase())) return false;
    } else {
      let v = val(c, r);
      if (num(v) === null) return false;
      if (c.fv) v = c.fv(v);
      if (f.min !== "" && f.min !== undefined && v < Number(f.min)) return false;
      if (f.max !== "" && f.max !== undefined && v > Number(f.max)) return false;
    }
  }
  return true;
}

function sorted(rows, sort) {
  const c = colByKey[sort.k] || colByKey.score;
  return rows.slice().sort((a, b) => {
    const x = val(c, a), y = val(c, b);
    if (typeof x === "string" || typeof y === "string") return String(x ?? "").localeCompare(String(y ?? "")) * sort.dir;
    if (num(x) === null) return 1;
    if (num(y) === null) return -1;
    return (x - y) * sort.dir;
  });
}

function headHtml(cols, sort, filters, extra = "") {
  return "<tr>" + extra + cols.map((c) => {
    const cls = [c.l ? "l" : "", c.c ? "c" : "", sort && sort.k === c.k ? "sorted" : ""].join(" ");
    const arrow = sort && sort.k === c.k ? (sort.dir > 0 ? " ↑" : " ↓") : "";
    const fl = c.filter && filters ? `<span class="f ${filters[c.k] ? "on" : ""}" data-filter="${c.k}">▽</span>` : "";
    return `<th class="${cls}" data-sort="${c.nosort ? "" : c.k}" title="${esc(c.d || "")}">${esc(c.t)}${arrow}${fl}</th>`;
  }).join("") + "</tr>";
}

function rowHtml(r, cols, extra = "") {
  return `<tr data-addr="${r.addr}">` + extra + cols.map((c) => {
    const v = val(c, r);
    const txt = c.f ? c.f(v, r) : esc(v ?? "–");
    const cls = [c.l ? "l" : "", c.c ? "c" : "", c.cls || "", c.amber && c.amber(r) ? "amber" : ""].join(" ");
    return `<td class="${cls}" style="${c.amber && c.amber(r) ? "" : heatStyle(c, r)}">${txt}</td>`;
  }).join("") + "</tr>";
}

function render() {
  const rows = sorted(state.rows.filter(passes), state.sort);
  $("#grid thead").innerHTML = headHtml(COLS, state.sort, state.filters);
  const MAX = 1500;
  $("#grid tbody").innerHTML = rows.slice(0, MAX).map((r) => rowHtml(r, COLS)).join("");
  $("#row-count").textContent = `${rows.length.toLocaleString()} of ${state.rows.length.toLocaleString()}${rows.length > MAX ? " (first " + MAX + " shown)" : ""}`;
  const empty = $("#empty");
  if (!state.rows.length) {
    empty.textContent = state.tab === "top1d" ? "No wallet closed a priced position in the last 24 h yet." : "No scored wallets yet. They appear after the first sync + scoring pass (see the log below).";
    empty.classList.remove("hidden");
  } else if (!rows.length) {
    empty.textContent = "No wallet matches these filters.";
    empty.classList.remove("hidden");
  } else empty.classList.add("hidden");
}

// clicks: sort, filter, star, remove, open wallet
document.addEventListener("click", async (e) => {
  const fEl = e.target.closest("#grid [data-filter]");
  if (fEl) { e.stopPropagation(); return openFilter(fEl); }
  const th = e.target.closest("#grid th[data-sort]");
  if (th && th.dataset.sort) {
    const k = th.dataset.sort;
    state.sort = state.sort.k === k ? { k, dir: -state.sort.dir } : { k, dir: k === "rank" || k === "recover" || k === "last" ? 1 : -1 };
    return render();
  }
  const star = e.target.closest("[data-star]");
  if (star) return toggleStar(star);
  const rm = e.target.closest("[data-rm]");
  if (rm) { await api("/api/label", { addr: rm.dataset.rm, delete: true }); updateWatchCount(); return loadWatch(); }
  const td = e.target.closest(".grid td.addr");
  if (td) return openDrawer(td.parentElement.dataset.addr);
  if (!e.target.closest("#pop")) $("#pop").classList.add("hidden");
});

function openFilter(el) {
  const k = el.dataset.filter, c = colByKey[k], f = state.filters[k] || {};
  const pop = $("#pop");
  const unit = c.unit ? ` (${c.unit})` : "";
  pop.innerHTML = c.filter === "text"
    ? `<div>${esc(c.t)} contains</div><input id="pf-q" value="${esc(f.q || "")}" autofocus>`
    : `<div>${esc(c.t)}${unit}</div><div class="row"><input id="pf-min" placeholder="min" value="${esc(f.min ?? "")}"><input id="pf-max" placeholder="max" value="${esc(f.max ?? "")}"></div>`;
  pop.innerHTML += `<div class="acts"><button class="mini" id="pf-clear">Clear</button><button class="mini" id="pf-ok">Apply</button></div>`;
  const rect = el.getBoundingClientRect();
  pop.style.left = Math.min(rect.left, window.innerWidth - 230) + "px";
  pop.style.top = rect.bottom + 6 + "px";
  pop.classList.remove("hidden");
  const apply = () => {
    const nf = c.filter === "text" ? { q: $("#pf-q").value.trim() } : { min: $("#pf-min").value.trim(), max: $("#pf-max").value.trim() };
    if (Object.values(nf).every((v) => v === "")) delete state.filters[k]; else state.filters[k] = nf;
    pop.classList.add("hidden");
    render();
  };
  $("#pf-ok").onclick = apply;
  $("#pf-clear").onclick = () => { delete state.filters[k]; pop.classList.add("hidden"); render(); };
  pop.querySelectorAll("input").forEach((i) => i.addEventListener("keydown", (ev) => { if (ev.key === "Enter") apply(); }));
  (pop.querySelector("input") || {}).focus?.();
}

// ---------- saved views ----------
function refreshViews() {
  const views = LS.get("rhwf.views", {});
  const sel = $("#view-select");
  const cur = sel.value;
  sel.innerHTML = '<option value="">— no view —</option>' + Object.keys(views).map((n) => `<option ${n === cur ? "selected" : ""}>${esc(n)}</option>`).join("");
  $("#view-note").textContent = Object.keys(views).length ? Object.keys(views).length + " saved" : "none saved yet";
}
$("#view-select").onchange = (e) => {
  const v = LS.get("rhwf.views", {})[e.target.value];
  if (v) { state.filters = JSON.parse(JSON.stringify(v.filters || {})); state.sort = v.sort || state.sort; render(); }
};
$("#view-save").onclick = () => {
  const name = prompt("Name this view (filters + sort):");
  if (!name) return;
  const views = LS.get("rhwf.views", {});
  views[name] = { filters: state.filters, sort: state.sort };
  LS.set("rhwf.views", views);
  refreshViews();
  $("#view-select").value = name;
};
$("#view-clear").onclick = () => {
  const sel = $("#view-select");
  if (sel.value && confirm(`Delete saved view "${sel.value}"?`)) {
    const views = LS.get("rhwf.views", {});
    delete views[sel.value];
    LS.set("rhwf.views", views);
  }
  state.filters = {}; state.sort = { k: "score", dir: -1 }; state.quick = ""; $("#quick").value = "";
  sel.value = "";
  refreshViews();
  render();
};
$("#quick").oninput = (e) => { state.quick = e.target.value.trim(); render(); };

// ---------- tabs ----------
$("#tabs").onclick = (e) => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  state.tab = b.dataset.tab;
  document.querySelectorAll("#tabs button").forEach((x) => x.classList.toggle("on", x === b));
  $("#pane-table").classList.toggle("hidden", !(state.tab === "wallets" || state.tab === "top1d"));
  $("#pane-similar").classList.toggle("hidden", state.tab !== "similar");
  $("#pane-watch").classList.toggle("hidden", state.tab !== "watch");
  $("#pl-metrics").textContent = (state.tab === "top1d" ? 1 : state.status.metric_days || 30) + "d";
  if (state.tab === "wallets" || state.tab === "top1d") loadRows();
  if (state.tab === "watch") loadWatch();
};

$("#real-toggle").checked = state.real;
$("#real-toggle").onchange = (e) => {
  state.real = e.target.checked;
  LS.set("rhwf.real", state.real);
  realBanner();
  if (state.tab === "similar") runSimilar(); else if (state.tab === "watch") loadWatch(); else loadRows();
};

// ---------- star / labels ----------
async function toggleStar(btn) {
  const addr = btn.dataset.star;
  const starred = !btn.classList.contains("on");
  await api("/api/label", { addr, starred });
  btn.classList.toggle("on", starred);
  btn.textContent = starred ? "★" : "☆";
  const r = state.rows.find((x) => x.addr === addr);
  if (r) r.starred = starred;
  updateWatchCount();
}
async function updateWatchCount() {
  const j = await api("/api/labels");
  $("#watch-count").textContent = (j.rows || []).length ? `(${j.rows.length})` : "";
}

// ---------- drawer ----------
let drawerAddr = null;
async function openDrawer(addr) {
  drawerAddr = addr;
  const d = await api("/api/wallet/" + addr);
  $("#drawer").classList.remove("hidden");
  $("#dr-addr").textContent = addr;
  $("#dr-links").innerHTML = `<a href="${esc(d.lpagent_link)}" target="_blank" rel="noopener">LP Agent ↗</a><a href="${esc(d.explorer)}" target="_blank" rel="noopener">Explorer ↗</a><a href="#" id="dr-sim">Find similar →</a><a href="#" id="dr-copy">Copy address</a>`;
  $("#dr-sim").onclick = (e) => { e.preventDefault(); closeDrawer(); $("#sim-addr").value = addr; document.querySelector('[data-tab="similar"]').click(); runSimilar(); };
  $("#dr-copy").onclick = (e) => { e.preventDefault(); navigator.clipboard?.writeText(addr); e.target.textContent = "Copied"; };
  const lab = d.label || {};
  $("#dr-label").value = lab.label || "";
  $("#dr-notes").value = lab.notes || "";
  const st = $("#dr-star");
  st.classList.toggle("on", !!lab.starred);
  st.textContent = lab.starred ? "★" : "☆";
  const m = d.metrics ? d.metrics[state.real ? "real" : "raw"] : null;
  const cells = m ? [
    ["Score", f1(m.score) + " " + m.verdict], ["Net", usd(m.net)], ["Float", usd(m.float)], ["ROI/mo", f1(m.roi) + "%"],
    ["Win", int(m.win) + "%"], ["Daily", pct0(m.daily)], ["DD", f1(m.dd) + "%"], ["Recover", !m.recover && !m.recover_open ? "–" : (m.recover_open ? ">" : "") + dur(m.recover)],
    ["Fees", usd(m.fees)], ["Size/pos", usd(m.size)], ["Hold", dur(m.duration)], ["Range", rangeFmt(m.range)],
    ["Closed", int(m.closed)], ["Open", int(m.open)], ["Copiers", int(m.copiers)], ["Style", m.style || "–"],
  ] : [["Not scored yet", "needs " + (state.status.settings?.MIN_CLOSED || 3) + "+ closed, priced positions"]];
  $("#dr-metrics").innerHTML = cells.map(([k, v]) => `<div><small>${esc(k)}</small><span>${esc(v)}</span></div>`).join("");
  $("#dr-lpagent").innerHTML = state.status.has_lpagent_key
    ? 'Pulls LP Agent\'s own PnL / win rate / open positions for this wallet to compare with ours.'
    : `No LP Agent API key set, so only the <a href="${esc(d.lpagent_link)}" target="_blank" rel="noopener">portfolio link</a> works. Add a key in ⚙ Settings to compare numbers here.`;
  const pcols = [
    { k: "pair", t: "Pair", l: 1 }, { k: "open_ts", t: "Opened", f: (v) => dur(state.now - v) + " ago" },
    { k: "hold", t: "Hold", v: (r) => (r.close_ts || state.now) - r.open_ts, f: (v, r) => dur(v) + (r.close_ts ? "" : " (open)") },
    { k: "range", t: "Range", f: rangeFmt }, { k: "dep_usd", t: "In", f: usd }, { k: "out", t: "Out", v: (r) => (r.wd_usd || 0) + (r.now_usd || 0), f: (v, r) => (r.valued === 1 ? usd(v) : "–") },
    { k: "fee_usd", t: "Fees", f: usd },
    { k: "pnl", t: state.real ? "Real PnL" : "PnL", v: (r) => (r.pnl_usd === null ? null : r.pnl_usd - (state.real ? r.cost_usd || 0 : 0)), f: (v) => (num(v) === null ? "–" : `<span class="${v >= 0 ? "pos" : "neg"}">${usd(v)}</span>`) },
    { k: "st", t: "Status", l: 1, v: (r) => r, f: (v, r) => (!r.minted ? "opened before history" : r.valued === 2 ? "not priceable" : r.launch ? "token launch (not scored)" : r.valued === 1 ? (r.single ? "single-sided" : "two-sided") : "not priced yet") },
    { k: "token_id", t: "NFT", f: (v) => `<a href="https://robinhoodchain.blockscout.com/token/0x58daec3116aae6d93017baaea7749052e8a04fa7/instance/${v}" target="_blank" rel="noopener">#${v}</a>` },
  ];
  $("#dr-poscount").textContent = `(${d.positions.length}${d.positions.length >= 300 ? "+, newest" : ""})`;
  $("#dr-pos thead").innerHTML = headHtml(pcols, null, null);
  $("#dr-pos tbody").innerHTML = d.positions.map((p) => rowHtml(p, pcols)).join("");
}
function closeDrawer() { $("#drawer").classList.add("hidden"); drawerAddr = null; }
$("#dr-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { closeDrawer(); $("#settings").classList.add("hidden"); $("#pop").classList.add("hidden"); } });
$("#dr-star").onclick = () => { const s = $("#dr-star"); s.classList.toggle("on"); s.textContent = s.classList.contains("on") ? "★" : "☆"; };
$("#dr-save").onclick = async () => {
  const starred = $("#dr-star").classList.contains("on");
  await api("/api/label", { addr: drawerAddr, label: $("#dr-label").value.trim() || null, notes: $("#dr-notes").value.trim() || null, starred });
  $("#dr-save").textContent = "Saved";
  setTimeout(() => ($("#dr-save").textContent = "Save"), 1200);
  updateWatchCount();
  if (state.tab === "watch") loadWatch(); else loadRows();
};
$("#dr-lp").onclick = async () => {
  const box = $("#dr-lpagent");
  box.innerHTML = "Asking LP Agent…";
  const j = await api(`/api/lpagent/${drawerAddr}?fresh=1`);
  box.innerHTML = lpAgentHtml(j);
};

function lpAgentHtml(j) {
  if (!j.has_key) return `No API key. <a href="${esc(j.link)}" target="_blank" rel="noopener">Open on LP Agent ↗</a>`;
  let h = (j.errors || []).map((e) => `<div class="err">${esc(e)}</div>`).join("");
  const ours = (drawerAddr && state.rows.find((r) => r.addr === drawerAddr)) || null;
  const ov = j.overview || {};
  const protos = Object.keys(ov);
  if (protos.length) {
    const fmtv = (v, kind) => (num(v) === null ? "–" : kind === "usd" ? usd(v) : kind === "ratio" ? Math.round(v * (v <= 1 ? 100 : 1)) + "%" : int(v));
    const oursMap = ours ? { total_pnl: usd(ours.net + ours.float), win_rate: int(ours.win) + "%", total_fee: usd(ours.fees), closed_lp: int(ours.closed) } : {};
    for (const p of protos) {
      h += `<div class="lp-grid" style="margin-bottom:8px"><div class="h">${esc(p)}</div><div class="h">LP Agent</div><div class="h">This app (${state.status.metric_days}d)</div>`;
      for (const [f, label, kind] of j.fields) {
        h += `<div>${esc(label)}</div><div>${fmtv(ov[p][f], kind)}</div><div>${oursMap[f] || ""}</div>`;
      }
      h += "</div>";
    }
  } else if (!(j.errors || []).length) h += "<div>LP Agent has no Uniswap overview for this wallet.</div>";
  if ((j.opening || []).length) {
    h += `<div class="dr-title" style="margin-top:10px">Open on LP Agent (${j.opening.length})</div><div class="lp-grid">` +
      '<div class="h">Pair</div><div class="h">Value</div><div class="h">PnL</div>' +
      j.opening.map((o) => `<div>${esc(o.pair)} ${o.in_range === false ? "· out" : ""}</div><div>${usd(o.value)}</div><div class="${(o.pnl || 0) >= 0 ? "pos" : "neg"}">${usd(o.pnl)}${num(o.pnl_pct) !== null ? " (" + f1(o.pnl_pct) + "%)" : ""}</div>`).join("") + "</div>";
  }
  h += `<div style="margin-top:8px"><a href="${esc(j.link)}" target="_blank" rel="noopener">Open on LP Agent ↗</a>${j.cached ? ' <span class="muted">(cached)</span>' : ""}</div>`;
  return h;
}

// ---------- find similar ----------
const SIM_COLS = ["score", "addr", "verdict", "net", "roi", "win", "dd", "size", "eth", "mcap", "duration", "range", "pos", "concur", "style", "star", "lp"].map((k) => colByKey[k]);
async function runSimilar() {
  const addr = $("#sim-addr").value.trim();
  if (!addr) return;
  $("#sim-empty").classList.add("hidden");
  const j = await api(`/api/similar?addr=${encodeURIComponent(addr)}&view=${state.real ? "real" : "raw"}`);
  if (j.error) {
    $("#sim-target").textContent = "";
    $("#sim-grid thead").innerHTML = "";
    $("#sim-grid tbody").innerHTML = "";
    $("#sim-empty").textContent = j.error;
    $("#sim-empty").classList.remove("hidden");
    return;
  }
  const t = j.target;
  $("#sim-target").innerHTML = `Matching <b>${esc(short(t.addr))}</b>${t.label ? " (" + esc(t.label) + ")" : ""}: ${esc(t.style || "")} · hold ${dur(t.duration)} · range ${rangeFmt(t.range)} · size ${usd(t.size)} · ETH ${f1(t.eth)}% · score ${f1(t.score)}`;
  const simCol = { k: "sim", t: "Sim", v: (r) => r.sim, f: (v) => v + "%", heat: "n" };
  computeRanks(j.rows);
  state.ranks.sim = j.rows.map((r) => r.sim).sort((a, b) => a - b);
  $("#sim-grid thead").innerHTML = headHtml([simCol, ...SIM_COLS], null, null);
  $("#sim-grid tbody").innerHTML = j.rows.map((r) => rowHtml(r, [simCol, ...SIM_COLS])).join("");
  computeRanks(state.rows);
}
$("#sim-go").onclick = runSimilar;
$("#sim-addr").addEventListener("keydown", (e) => { if (e.key === "Enter") runSimilar(); });
// ---------- watchlist ----------
async function loadWatch() {
  const [lab, wl] = await Promise.all([api("/api/labels"), api("/api/wallets?view=" + (state.real ? "real" : "raw"))]);
  const scored = Object.fromEntries((wl.rows || []).map((r) => [r.addr, r]));
  const rows = (lab.rows || []).map((l) => ({ ...(scored[l.addr] || {}), addr: l.addr, label: l.label, notes: l.notes, starred: !!l.starred, source: l.source, _scored: !!scored[l.addr] }));
  computeRanks(wl.rows || []);
  const cols = [
    colByKey.addr, { k: "notes", t: "Notes", l: 1, f: (v) => esc(v || "") },
    { k: "score", t: "Score", f: (v, r) => (r._scored ? f1(v) : "not scored yet"), heat: "good" },
    colByKey.verdict, colByKey.net, colByKey.roi, colByKey.win, colByKey.pos, colByKey.style, colByKey.last,
    colByKey.star, colByKey.lp, colByKey.scan,
    { k: "rm", t: "", c: 1, f: (v, r) => `<button class="mini" data-rm="${r.addr}">remove</button>` },
  ];
  $("#watch-grid thead").innerHTML = headHtml(cols, null, null);
  $("#watch-grid tbody").innerHTML = rows.map((r) => rowHtml(r, cols)).join("");
  $("#watch-empty").classList.toggle("hidden", rows.length > 0);
  $("#watch-count").textContent = rows.length ? `(${rows.length})` : "";
  computeRanks(state.rows);
}
$("#import-go").onclick = async () => {
  const j = await api("/api/import", { text: $("#import-text").value, source: "paste" });
  $("#import-go").textContent = j.error ? j.error : `Added ${j.added}`;
  setTimeout(() => ($("#import-go").textContent = "Add to watchlist"), 1500);
  $("#import-text").value = "";
  loadWatch();
};

// ---------- settings ----------
$("#btn-settings").onclick = () => {
  const s = state.status.settings || {};
  for (const [k, v] of Object.entries(s)) { const el = $("#set-" + k); if (el) el.value = v; }
  $("#set-RPC_URL").value = "";
  $("#set-LPAGENT_API_KEY").value = "";
  $("#set-rpc-now").textContent = state.status.rpc ? "(now: " + state.status.rpc + ")" : "(not set)";
  $("#set-lp-now").textContent = state.status.has_lpagent_key ? "(set)" : "(not set)";
  $("#set-msg").textContent = "";
  $("#settings").classList.remove("hidden");
};
$("#set-close").onclick = () => $("#settings").classList.add("hidden");
$("#set-save").onclick = async () => {
  const body = {};
  document.querySelectorAll("[id^=set-]").forEach((el) => { if (el.tagName === "INPUT" && el.value.trim()) body[el.id.slice(4)] = el.value.trim(); });
  const j = await api("/api/settings", body);
  $("#set-msg").textContent = j.error ? j.error : "Saved. Sync restarts with the new settings.";
  if (!j.error) setTimeout(() => $("#settings").classList.add("hidden"), 900);
  loadStatus();
};

// ---------- footer ----------
$("#btn-log").onclick = () => $("#log").classList.toggle("hidden");
$("#btn-sync").onclick = async () => { await api("/api/sync", {}); $("#worker-state").textContent = "starting…"; };

// ---------- explain ----------
$("#explain-body").innerHTML = COLS.filter((c) => c.d).map((c) => `<div><b>${esc(c.t.toUpperCase())}</b>${esc(c.d)}</div>`).join("") +
  '<div><b>REAL PNL</b>Charges each position for selling back the token it returns (pool fee + slippage, set in Settings) and re-ranks on those figures.</div>' +
  '<div><b>TOP 100 · 1D</b>The same board computed on positions closed in the last 24 hours.</div>' +
  '<div><b>DATA</b>Every number comes from Robinhood Chain itself through your RPC: Uniswap V4 PoolManager / PositionManager events, prices from pool state at each block, ETH price from the ETH/USDG pool.</div>';

// ---------- boot ----------
refreshViews();
updateWatchCount();
loadStatus().then(loadRows);
let lastScored = null;
setInterval(async () => {
  await loadStatus();
  if (state.status.scored_at !== lastScored) {
    if (lastScored !== null && (state.tab === "wallets" || state.tab === "top1d")) loadRows();
    lastScored = state.status.scored_at;
  }
}, 10000);
