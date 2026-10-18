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
const f1 = (v) => (num(v) === null ? "–" : Math.abs(v) < 0.05 ? "0.0" : Number(v).toFixed(1));
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
  if (s < 60) return "now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m ago";
}
const signed = (v) => (num(v) === null ? "–" : `<span class="${v >= 0 ? "pos" : "neg"}">${v > 0 ? "+" : ""}${usd(v)}</span>`);

// ---------- verdict / followable / crowding ----------
// Verdict tiers sit on the same blue <-> red scale as the heat colours.
const VERDICT_BG = { Elite: "rgba(var(--blue), .46)", Strong: "rgba(var(--blue), .24)", Decent: "rgba(255,255,255,.07)", Weak: "rgba(var(--red), .24)", Avoid: "rgba(var(--red), .46)" };
const verdictTag = (v) => (v ? `<span class="tagv" style="background:${VERDICT_BG[v] || "transparent"}">${esc(v)}</span>` : "–");
const FOLLOW = { "✓": ["follow-yes", "✓ yes"], "✗": ["follow-no", "✗ bot"], "?": ["follow-q", "? maybe"] };
const followTag = (v) => { const f = FOLLOW[v]; return f ? `<span class="${f[0]}">${f[1]}</span>` : "–"; };

// ---------- columns ----------
// g: column group. heat: "up" higher is better, "down" lower is better (blue = better, red = worse,
// against the other wallets); "signed": blue above zero, red below, $0 uncoloured.
const GROUPS = { id: "Wallet", perf: "Performance", risk: "Risk", copy: "Copy-trade", style: "Style", hold: "Holdings", links: "Open" };
const COLS = [
  { g: "id", k: "rank", t: "#", f: int, filter: "num", d: "Place on the board, by score." },
  { g: "id", k: "score", t: "Score", f: f1, heat: "up", filter: "num", d: "0–100. Percentile blend of realized PnL, ROI, win rate, green days, max drawdown, recovery, fees and sample size. Few closed positions pull it toward 50; a losing wallet is capped at 45." },
  { g: "id", k: "addr", t: "Wallet", l: 1, filter: "text", cls: "addr", f: (v, r) => esc(short(v)) + (r.label ? `<span class="lab">${esc(r.label)}</span>` : ""), d: "Click for the wallet dossier: PnL curve, positions, label, LP Agent check." },
  { g: "id", k: "verdict", t: "Tier", c: 1, filter: "text", f: verdictTag, d: "Elite ≥ 70, Strong ≥ 60, Decent ≥ 50, Weak ≥ 40, else Avoid." },
  { g: "perf", k: "net", t: "Realized", f: usd, heat: "signed", filter: "num", d: "USD PnL of positions closed in the window: everything taken out + fees − everything put in." },
  { g: "perf", k: "float", t: "Unrealized", f: usd, heat: "signed", filter: "num", d: "PnL of positions still open: current value + unclaimed fees − deposits." },
  { g: "perf", k: "roi", t: "ROI/mo", f: (v) => (num(v) === null ? "–" : f1(v) + "%"), heat: "signed", filter: "num", d: "Realized PnL per month over the capital the wallet keeps deployed (time-weighted, never below its median position)." },
  { g: "perf", k: "win", t: "Win", f: pct0, heat: "up", filter: "num", d: "Share of closed positions that made money." },
  { g: "perf", k: "daily", t: "Green days", f: pct0, heat: "up", filter: "num", d: "Share of days with closes that ended in profit." },
  { g: "perf", k: "fees", t: "Fees", f: usd, heat: "up", filter: "num", d: "LP fees earned (claimed + unclaimed), USD." },
  { g: "risk", k: "dd", t: "Max DD", f: (v) => (num(v) === null ? "–" : f1(v) + "%"), heat: "up", filter: "num", d: "Deepest drop of the realized PnL curve, as % of deployed capital." },
  { g: "risk", k: "recover", t: "Recovery", f: (v, r) => (!v && !r.recover_open ? "–" : (r.recover_open ? ">" : "") + dur(v)), heat: "down", filter: "num", fv: (v) => v / 86400, unit: "days", d: "How long the deepest drawdown took to win back (\">\" = still under water)." },
  { g: "copy", k: "copy", t: "Followable", c: 1, filter: "text", f: followTag, d: "Can a person follow it by hand? yes: median hold ≥ 30 min and ≤ 30 positions/day. bot: hold < 5 min or > 100/day. maybe: in between." },
  { g: "copy", k: "copiers", t: "Copiers", f: int, filter: "num", d: "Other wallets that opened the same pool with the exact same range within 60 s after this wallet, at least twice." },
  { g: "copy", k: "exposure", t: "Crowding", filter: "num", v: (r) => r.exposure * 100, f: (v, r) => (r.exposure >= 0.5 ? `<span class="crowd">⚠ ${Math.round(r.exposure * 100)}%</span>` : `${usd(r.exposure_usd)} · ${Math.round(r.exposure * 100)}%`), d: "Liquidity copiers add per open, as a share of the wallet's own (USD at its median size). ⚠ from 50%: following it means sharing the range." },
  { g: "style", k: "style", t: "Style", l: 1, f: (v, r) => (v ? `${esc(v)} <span class="muted">${r.style_conf}%</span>` : "–"), filter: "text", d: "Hold (Scalp < 30 min, Day < 24 h, Multiday) · token size (Degen < $1M mcap, Mid < $20M, Blue), and how many positions fit it." },
  { g: "style", k: "sim", t: "Twin", l: 1, v: (r) => r.sim_pct, f: (v, r) => (r.sim_label ? `${esc(r.sim_label)} <span class="muted">${r.sim_pct}%</span>` : "–"), filter: "num", d: "Closest starred / labelled wallet by LP style, and how close." },
  { g: "style", k: "duration", t: "Hold", f: dur, filter: "num", fv: (v) => v / 3600, unit: "hours", d: "Median time a position stays open." },
  { g: "style", k: "range", t: "Range", f: rangeFmt, filter: "num", d: "Median range width: upper price / lower price − 1." },
  { g: "style", k: "size", t: "Size", f: usd, filter: "num", d: "Median USD deposited per position." },
  { g: "style", k: "eth", t: "ETH pair", f: pct0, filter: "num", d: "Share of deposits in ETH-paired pools (the rest is USDG-paired)." },
  { g: "style", k: "mcap", t: "Token mcap", f: usd, filter: "num", d: "Median current market cap of the tokens it LPs (supply × pool price)." },
  { g: "style", k: "pos", t: "Positions", f: int, filter: "num", d: "Positions in the window (closed in it, or still open)." },
  { g: "style", k: "open", t: "Open", f: int, filter: "num", d: "Positions open right now." },
  { g: "style", k: "concur", t: "Parallel", f: (v) => (num(v) === null ? "–" : v.toFixed(1) + "×"), filter: "num", d: "Average number of positions open at the same time." },
  { g: "hold", k: "open_now", t: "Deployed", f: usd, filter: "num", d: "USD sitting in open positions now." },
  { g: "hold", k: "worth", t: "Worth", f: usd, filter: "num", d: "ETH + USDG in the wallet + open LP value." },
  { g: "hold", k: "age", t: "Seen", v: (r) => state.now - r.first_seen, f: (v, r) => (r.first_seen <= (state.status.data_from || 0) + 3600 ? ">" : "") + dur(v), filter: "num", fv: (v) => v / 86400, unit: "days", d: "Since its first LP in our data (\">\" = already active before our history starts)." },
  { g: "hold", k: "last", t: "Active", v: (r) => state.now - r.last_seen, f: (v) => dur(v) + " ago", filter: "num", fv: (v) => v / 3600, unit: "hours", d: "Since its last LP action." },
  { g: "links", k: "star", t: "★", c: 1, nosort: 1, f: (v, r) => `<button class="star ${r.starred ? "on" : ""}" data-star="${r.addr}" title="Watchlist">${r.starred ? "★" : "☆"}</button>`, d: "Watchlist: starred wallets get priced first and anchor the Twin column." },
  { g: "links", k: "lp", t: "LP Agent", c: 1, nosort: 1, f: (v, r) => `<a href="https://app.lpagent.io/portfolio?address=${r.addr}&chain=ROBINHOOD" target="_blank" rel="noopener">open ↗</a>`, d: "The wallet on LP Agent." },
  { g: "links", k: "scan", t: "Explorer", c: 1, nosort: 1, f: (v, r) => `<a href="https://robinhoodchain.blockscout.com/address/${r.addr}" target="_blank" rel="noopener">open ↗</a>`, d: "The wallet on the Robinhood Chain explorer." },
];
const colByKey = Object.fromEntries(COLS.map((c) => [c.k, c]));
const val = (c, r) => (c.v ? c.v(r) : r[c.k]);

// ---------- presets ----------
const BUILTIN = [
  { name: "All", filters: {} },
  { name: "Followable winners", filters: { copy: { q: "✓" }, net: { min: "0.01" }, win: { min: "55" } } },
  { name: "Low drawdown", filters: { net: { min: "0.01" }, dd: { min: "-10" } } },
  { name: "Size ≥ $1K", filters: { size: { min: "1000" } } },
  { name: "Active 24h", filters: { last: { max: "24" } } },
  { name: "Uncrowded", filters: { exposure: { max: "20" }, net: { min: "0.01" } } },
];

// ---------- state ----------
const state = {
  tab: "wallets", real: LS.get("rhwf.real", false), rows: [], status: {}, now: Date.now() / 1000,
  sort: { k: "score", dir: -1 }, filters: {}, quick: "", ranks: {}, preset: "All",
};

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "content-type": "application/json", "x-token": window.TOKEN }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({ error: "bad reply" }));
  if (!r.ok && !j.error) j.error = "HTTP " + r.status;
  return j;
}
const view = () => (state.tab === "top1d" ? "d1" : "") + (state.real ? "real" : "raw");

// ---------- status ----------
async function loadStatus() {
  const s = await api("/api/status");
  if (s.error) return;
  state.status = s;
  state.now = s.now;
  $("#k-wallets").textContent = (s.discovered || 0).toLocaleString();
  $("#k-ranked").textContent = (s.scored || 0).toLocaleString();
  $("#k-ranked-s").textContent = `${s.settings?.MIN_CLOSED || 3}+ closed, priced positions`;
  $("#k-positions").textContent = (s.positions || 0).toLocaleString();
  $("#k-styles").textContent = (s.categories || 0).toLocaleString();
  $("#rl-sync").textContent = ago(s.synced_at);
  $("#rl-score").textContent = ago(s.scored_at);
  const hist = s.data_from ? (s.now - s.data_from) / 86400 : 0;
  $("#rl-history").textContent = s.data_from ? `${hist < 1 ? dur(hist * 86400) : hist.toFixed(hist < 3 ? 1 : 0) + "d"}/${s.backfill_days}d` : "–";
  $("#rl-window").textContent = (state.tab === "top1d" ? 1 : s.metric_days) + "d";
  $("#rl-eth").textContent = s.eth_usd ? "$" + Math.round(s.eth_usd).toLocaleString() : "–";
  $("#led-sync").className = "led " + (!s.synced_at ? "" : s.now - s.synced_at < 1800 ? "ok" : "warn");
  $("#led-score").className = "led " + (!s.scored_at ? "" : s.now - s.scored_at < 3 * 3600 ? "ok" : "warn");
  const w = s.worker || {};
  const busy = w.state === "syncing" || w.state === "scoring";
  $("#led-worker").className = "led " + (w.error ? "bad" : busy ? "busy" : s.has_rpc ? "ok" : "warn");
  $("#worker-state").textContent = w.error ? "error: " + w.error : (w.state || "idle") + (w.progress && !w.progress.done ? " · loading history" : "");
  $("#rpc-label").textContent = s.rpc ? "logs " + s.rpc + (s.call_rpc ? " · calls " + s.call_rpc : "") + (s.rpc_source && s.rpc_source !== "settings" ? " (from " + s.rpc_source + ")" : "") : "";
  $("#log").textContent = (w.log || []).join("\n");
  const setup = $("#setup");
  if (!s.has_rpc) {
    setup.innerHTML = "No RPC yet. Open <b>⚙ Settings</b> and paste your Robinhood Chain RPC URL (your SolidRPC link works). The first sync starts right after.";
    setup.classList.remove("hidden");
  } else if (!s.scored && !s.discovered) {
    setup.textContent = "First sync running: scanning Uniswap V4 LP activity from the newest blocks backwards. Wallets appear after the first ranking pass.";
    setup.classList.remove("hidden");
  } else setup.classList.add("hidden");
  realBanner();
}

function realBanner() {
  const b = $("#real-banner");
  if (!state.real) return b.classList.add("hidden");
  const fee = state.status.sell_fee ? (state.status.sell_fee * 100).toFixed(2) : "–";
  b.innerHTML = `<b>Real PnL on.</b> Every figure is charged for selling back the token side a position returns (median <b>${fee}%</b>: pool fee + ${state.status.slippage ?? 1}% slippage), and the board is re-ranked on those numbers.`;
  b.classList.remove("hidden");
}

// ---------- heat (diverging: blue = better, red = worse, middle = no fill) ----------
function computeRanks(rows) {
  state.ranks = {};
  for (const c of COLS) {
    if (!c.heat) continue;
    const vs = rows.map((r) => val(c, r)).filter((v) => num(v) !== null);
    if (c.heat === "signed") {
      state.ranks[c.k + "+"] = vs.filter((v) => v > 0).sort((a, b) => a - b);
      state.ranks[c.k + "-"] = vs.filter((v) => v < 0).map((v) => -v).sort((a, b) => a - b);
    } else state.ranks[c.k] = vs.sort((a, b) => a - b);
  }
}
function pctOf(k, v) {
  const vs = state.ranks[k];
  if (!vs || vs.length < 2 || num(v) === null) return null;
  const bis = (right) => {
    let lo = 0, hi = vs.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (vs[m] < v || (right && vs[m] === v)) lo = m + 1; else hi = m; }
    return lo;
  };
  const lo = bis(false), hi = bis(true);
  if (hi - lo === vs.length) return null;
  return ((lo + hi - 1) / 2) / (vs.length - 1);
}
const STEPS = [0.16, 0.3, 0.44]; // equal steps per arm
function heatBg(c, r) {
  if (!c.heat) return "";
  if (c.heat === "signed") {
    const v = num(val(c, r));
    if (v === null || Math.abs(v) < 0.005) return "";
    const vs = state.ranks[c.k + (v > 0 ? "+" : "-")] || [];
    let i = 0;
    while (i < vs.length && vs[i] < Math.abs(v)) i++;
    const q = vs.length > 1 ? i / (vs.length - 1) : 1; // how big, among same-sign values
    const c2 = `rgba(var(${v > 0 ? "--blue" : "--red"}), ${STEPS[q < 0.34 ? 0 : q < 0.67 ? 1 : 2]})`;
    return `background-image: linear-gradient(${c2}, ${c2})`;
  }
  let p = pctOf(c.k, val(c, r));
  if (p === null) return "";
  if (c.heat === "down") p = 1 - p;
  const d = Math.abs(p - 0.5);
  if (d < 0.12) return "";
  const a = STEPS[d < 0.25 ? 0 : d < 0.38 ? 1 : 2];
  const c2 = `rgba(var(${p > 0.5 ? "--blue" : "--red"}), ${a})`;
  return `background-image: linear-gradient(${c2}, ${c2})`; // over the opaque row colour, so sticky cells stay solid
}

// ---------- filtering / sorting ----------
function passes(r, filters) {
  if (state.quick) {
    const q = state.quick.toLowerCase();
    if (!r.addr.includes(q) && !(r.label || "").toLowerCase().includes(q)) return false;
  }
  for (const [k, f] of Object.entries(filters)) {
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

// ---------- table html ----------
const STICKY = { rank: "sticky0", score: "sticky1", addr: "sticky2" };
function groupStarts(cols) {
  return cols.map((c, i) => i > 0 && c.g !== cols[i - 1].g);
}
function headHtml(cols, opts = {}) {
  const starts = groupStarts(cols);
  let groups = "";
  if (opts.groups) {
    let i = 0;
    while (i < cols.length) {
      let j = i;
      while (j < cols.length && cols[j].g === cols[i].g) j++;
      groups += `<th colspan="${j - i}" class="g-${cols[i].g}"><span>${esc(GROUPS[cols[i].g] || "")}</span></th>`;
      i = j;
    }
    groups = `<tr class="groups">${groups}</tr>`;
  }
  const sort = opts.sort;
  const row = cols.map((c, i) => {
    const cls = [c.l ? "l" : "", c.c ? "c" : "", sort && sort.k === c.k ? "sorted" : "", starts[i] ? "gs" : "", opts.sticky ? STICKY[c.k] || "" : ""].join(" ");
    const arrow = sort && sort.k === c.k ? (sort.dir > 0 ? " ▲" : " ▼") : "";
    const fl = c.filter && opts.filters ? `<span class="f ${opts.filters[c.k] ? "on" : ""}" data-filter="${c.k}" title="Filter">◆</span>` : "";
    return `<th class="${cls}" data-sort="${c.nosort || !sort ? "" : c.k}" title="${esc(c.d || "")}">${esc(c.t)}${arrow}${fl}</th>`;
  }).join("");
  return groups + `<tr class="cols">${row}</tr>`;
}
function rowHtml(r, cols, opts = {}) {
  const starts = groupStarts(cols);
  return `<tr data-addr="${r.addr || ""}">` + cols.map((c, i) => {
    const v = val(c, r);
    const txt = c.f ? c.f(v, r) : esc(v ?? "–");
    const cls = [c.l ? "l" : "", c.c ? "c" : "", c.cls || "", starts[i] ? "gs" : "", opts.sticky ? STICKY[c.k] || "" : ""].join(" ");
    return `<td class="${cls}" style="${opts.heat === false ? "" : heatBg(c, r)}">${txt}</td>`;
  }).join("") + "</tr>";
}

async function loadRows() {
  const j = await api("/api/wallets?view=" + view());
  state.rows = j.rows || [];
  computeRanks(state.rows);
  render();
}

function render() {
  const rows = sorted(state.rows.filter((r) => passes(r, state.filters)), state.sort);
  $("#grid thead").innerHTML = headHtml(COLS, { groups: true, sort: state.sort, filters: state.filters, sticky: true });
  const MAX = 1500;
  $("#grid tbody").innerHTML = rows.slice(0, MAX).map((r) => rowHtml(r, COLS, { sticky: true })).join("");
  const g = $("#grid thead tr.groups");
  if (g) $("#grid").querySelectorAll("thead tr.cols th").forEach((th) => (th.style.top = g.offsetHeight + "px"));
  const ths = $("#grid thead tr.cols").children; // pin #, score and wallet at their real widths
  $("#grid").style.setProperty("--s1", ths[0].offsetWidth + "px");
  $("#grid").style.setProperty("--s2", ths[0].offsetWidth + ths[1].offsetWidth + "px");
  $("#row-count").textContent = `${rows.length.toLocaleString()} / ${state.rows.length.toLocaleString()}${rows.length > MAX ? " (first " + MAX + ")" : ""}`;
  const empty = $("#empty");
  if (!state.rows.length) {
    empty.textContent = state.tab === "top1d" ? "No wallet closed a priced position in the last 24 h yet." : "No ranked wallets yet. They appear after the first sync + ranking pass (see the console below).";
    empty.classList.remove("hidden");
  } else if (!rows.length) {
    empty.textContent = "No wallet matches this preset / these filters.";
    empty.classList.remove("hidden");
  } else empty.classList.add("hidden");
  renderPresets();
}

// ---------- presets ----------
function savedPresets() {
  const p = LS.get("rhwf.presets", null);
  if (p) return p;
  const old = LS.get("rhwf.views", {}); // earlier versions called them views
  return Object.entries(old).map(([name, v]) => ({ name, filters: v.filters || {}, sort: v.sort }));
}
function renderPresets() {
  const saved = savedPresets();
  const chip = (p, i, own) => `<button class="chip ${state.preset === p.name ? "on" : ""}" data-preset="${own ? "s" : "b"}${i}">${esc(p.name)}${own ? `<span class="x" data-del="${i}" title="Delete">×</span>` : ""}</button>`;
  $("#presets").innerHTML = BUILTIN.map((p, i) => chip(p, i, false)).join("") + saved.map((p, i) => chip(p, i, true)).join("") +
    `<button class="chip ghost" id="preset-save">+ Save current</button>` +
    (Object.keys(state.filters).length ? `<button class="chip ghost" id="preset-clear">Clear filters</button>` : "");
}
function applyPreset(p) {
  state.filters = JSON.parse(JSON.stringify(p.filters || {}));
  if (p.sort) state.sort = p.sort;
  state.preset = p.name;
  render();
}

// ---------- clicks ----------
document.addEventListener("click", async (e) => {
  const del = e.target.closest("[data-del]");
  if (del) {
    e.stopPropagation();
    const saved = savedPresets();
    saved.splice(+del.dataset.del, 1);
    LS.set("rhwf.presets", saved);
    return renderPresets();
  }
  const pr = e.target.closest("[data-preset]");
  if (pr) {
    const id = pr.dataset.preset;
    return applyPreset(id[0] === "b" ? BUILTIN[+id.slice(1)] : savedPresets()[+id.slice(1)]);
  }
  if (e.target.closest("#preset-save")) {
    const name = prompt("Name this preset (current filters + sort):");
    if (!name) return;
    const saved = savedPresets().filter((p) => p.name !== name);
    saved.push({ name, filters: state.filters, sort: state.sort });
    LS.set("rhwf.presets", saved);
    state.preset = name;
    return renderPresets();
  }
  if (e.target.closest("#preset-clear")) return applyPreset(BUILTIN[0]);
  const fEl = e.target.closest("#grid [data-filter]");
  if (fEl) { e.stopPropagation(); return openFilter(fEl); }
  const th = e.target.closest("#grid th[data-sort]");
  if (th && th.dataset.sort) {
    const k = th.dataset.sort;
    state.sort = state.sort.k === k ? { k, dir: -state.sort.dir } : { k, dir: ["rank", "recover", "last", "age"].includes(k) ? 1 : -1 };
    return render();
  }
  const star = e.target.closest("[data-star]");
  if (star) return toggleStar(star);
  const rm = e.target.closest("[data-rm]");
  if (rm) { await api("/api/label", { addr: rm.dataset.rm, delete: true }); updateWatchCount(); return loadWatch(); }
  const td = e.target.closest(".grid td.addr");
  if (td) return openDrawer(td.parentElement.dataset.addr);
  const close = e.target.closest("[data-close]");
  if (close) return $("#" + close.dataset.close).classList.add("hidden");
  if (e.target.classList.contains("modal")) return e.target.classList.add("hidden");
  if (!e.target.closest("#pop")) $("#pop").classList.add("hidden");
});

function openFilter(el) {
  const k = el.dataset.filter, c = colByKey[k], f = state.filters[k] || {};
  const pop = $("#pop");
  const unit = c.unit ? ` (${c.unit})` : "";
  pop.innerHTML = (c.filter === "text"
    ? `<div>${esc(c.t)} contains</div><input id="pf-q" value="${esc(f.q || "")}">`
    : `<div>${esc(c.t)}${unit}</div><div class="row"><input id="pf-min" placeholder="min" value="${esc(f.min ?? "")}"><input id="pf-max" placeholder="max" value="${esc(f.max ?? "")}"></div>`) +
    `<div class="acts"><button class="chip" id="pf-clear">Clear</button><button class="chip on" id="pf-ok">Apply</button></div>`;
  const rect = el.getBoundingClientRect();
  pop.style.left = Math.min(rect.left - 10, window.innerWidth - 240) + "px";
  pop.style.top = rect.bottom + 6 + "px";
  pop.classList.remove("hidden");
  const apply = () => {
    const nf = c.filter === "text" ? { q: $("#pf-q").value.trim() } : { min: $("#pf-min").value.trim(), max: $("#pf-max").value.trim() };
    if (Object.values(nf).every((v) => v === "")) delete state.filters[k]; else state.filters[k] = nf;
    state.preset = null;
    pop.classList.add("hidden");
    render();
  };
  $("#pf-ok").onclick = apply;
  $("#pf-clear").onclick = () => { delete state.filters[k]; state.preset = null; pop.classList.add("hidden"); render(); };
  pop.querySelectorAll("input").forEach((i) => i.addEventListener("keydown", (ev) => { if (ev.key === "Enter") apply(); }));
  pop.querySelector("input")?.focus();
}
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
  $("#rl-window").textContent = (state.tab === "top1d" ? 1 : state.status.metric_days || 30) + "d";
  if (state.tab === "wallets" || state.tab === "top1d") loadRows();
  if (state.tab === "watch") loadWatch();
};
$("#real-toggle").checked = state.real;
$("#real-toggle").onchange = (e) => {
  state.real = e.target.checked;
  LS.set("rhwf.real", state.real);
  realBanner();
  if (state.tab === "similar") runSimilar(); else if (state.tab === "watch") loadWatch(); else loadRows();
  if (drawerAddr) openDrawer(drawerAddr);
};

// ---------- watchlist star ----------
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

// ---------- dossier ----------
let drawerAddr = null;
async function openDrawer(addr) {
  drawerAddr = addr;
  const d = await api("/api/wallet/" + addr);
  if (drawerAddr !== addr) return;
  $("#drawer").classList.remove("hidden");
  $("#dr-addr").textContent = addr;
  const m = d.metrics ? d.metrics[state.real ? "real" : "raw"] : null;
  $("#dr-chips").innerHTML = m ? [verdictTag(m.verdict), m.style ? `<span class="chip">${esc(m.style)}</span>` : "", `<span class="chip">${followTag(m.copy)}</span>`,
    m.copiers ? `<span class="chip crowd">⚠ ${m.copiers} copier${m.copiers > 1 ? "s" : ""}</span>` : ""].join("") : '<span class="chip">not ranked yet</span>';
  $("#dr-links").innerHTML = `<a href="${esc(d.lpagent_link)}" target="_blank" rel="noopener">LP Agent ↗</a><a href="${esc(d.explorer)}" target="_blank" rel="noopener">Explorer ↗</a><a href="#" id="dr-sim">Find twins →</a><a href="#" id="dr-copy">Copy address</a>`;
  $("#dr-sim").onclick = (e) => { e.preventDefault(); closeDrawer(); $("#sim-addr").value = addr; document.querySelector('[data-tab="similar"]').click(); runSimilar(); };
  $("#dr-copy").onclick = (e) => { e.preventDefault(); navigator.clipboard?.writeText(addr); e.target.textContent = "Copied"; };
  const lab = d.label || {};
  $("#dr-label").value = lab.label || "";
  $("#dr-notes").value = lab.notes || "";
  const st = $("#dr-star");
  st.classList.toggle("on", !!lab.starred);
  st.textContent = lab.starred ? "★" : "☆";
  const cells = m ? [
    ["Score", f1(m.score)], ["Realized", usd(m.net)], ["Unrealized", usd(m.float)], ["ROI/mo", f1(m.roi) + "%"],
    ["Win", pct0(m.win)], ["Green days", pct0(m.daily)], ["Max DD", f1(m.dd) + "%"], ["Recovery", !m.recover && !m.recover_open ? "–" : (m.recover_open ? ">" : "") + dur(m.recover)],
    ["Fees", usd(m.fees)], ["Size", usd(m.size)], ["Hold", dur(m.duration)], ["Range", rangeFmt(m.range)],
    ["Closed", int(m.closed)], ["Open", int(m.open)], ["Deployed", usd(m.open_now)], ["Parallel", f1(m.concur) + "×"],
  ] : [["Not ranked yet", "needs " + (state.status.settings?.MIN_CLOSED || 3) + "+ closed, priced positions"]];
  $("#dr-metrics").innerHTML = cells.map(([k, v]) => `<div><small>${esc(k)}</small><span>${esc(v)}</span></div>`).join("");
  drawCurve(d.positions);
  $("#dr-lpagent").innerHTML = state.status.has_lpagent_key
    ? "Pulls LP Agent's own PnL, win rate and open positions for this wallet, next to ours."
    : `No LP Agent API key yet, so only the <a href="${esc(d.lpagent_link)}" target="_blank" rel="noopener">portfolio link</a> works. Add a key in ⚙ Settings to compare numbers here.`;
  const pcols = [
    { k: "pair", t: "Pair", l: 1 }, { k: "open_ts", t: "Opened", f: (v) => dur(state.now - v) + " ago" },
    { k: "hold", t: "Hold", v: (r) => (r.close_ts || state.now) - r.open_ts, f: (v, r) => dur(v) + (r.close_ts ? "" : " · open") },
    { k: "range", t: "Range", f: rangeFmt }, { k: "dep_usd", t: "In", f: usd },
    { k: "out", t: "Out", v: (r) => (r.wd_usd || 0) + (r.now_usd || 0), f: (v, r) => (r.valued === 1 ? usd(v) : "–") },
    { k: "fee_usd", t: "Fees", f: usd },
    { k: "pnl", t: state.real ? "Real PnL" : "PnL", v: (r) => posPnl(r), f: signed },
    { k: "st", t: "Status", l: 1, v: (r) => r, f: (v, r) => (!r.minted ? "opened before history" : r.valued === 2 ? "not priceable" : r.launch ? "token launch · not scored" : r.valued === 1 ? (r.single ? "single-sided" : "two-sided") : "not priced yet") },
    { k: "token_id", t: "NFT", f: (v) => `<a href="https://robinhoodchain.blockscout.com/token/0x58daec3116aae6d93017baaea7749052e8a04fa7/instance/${v}" target="_blank" rel="noopener">#${v}</a>` },
  ];
  $("#dr-poscount").textContent = `${d.positions.length}${d.positions.length >= 300 ? "+ (newest)" : ""}`;
  $("#dr-pos thead").innerHTML = headHtml(pcols);
  $("#dr-pos tbody").innerHTML = d.positions.map((p) => rowHtml(p, pcols, { heat: false })).join("");
}
const posPnl = (r) => (r.pnl_usd === null || r.pnl_usd === undefined ? null : r.pnl_usd - (state.real ? r.cost_usd || 0 : 0));

function drawCurve(positions) {
  const box = $("#dr-curve");
  const pts = positions.filter((p) => p.close_ts && p.minted && p.valued === 1 && !p.launch && posPnl(p) !== null)
    .sort((a, b) => a.close_ts - b.close_ts);
  $("#dr-curve-note").textContent = pts.length ? `${pts.length} closed position${pts.length > 1 ? "s" : ""}${state.real ? " · real" : ""}` : "";
  if (pts.length < 2) { box.innerHTML = '<div class="empty">Needs at least two closed, priced positions.</div>'; return; }
  let cum = 0;
  const series = [{ t: pts[0].open_ts, y: 0 }, ...pts.map((p) => ({ t: p.close_ts, y: (cum += posPnl(p)), p }))];
  const W = box.clientWidth || 720, H = box.clientHeight || 170, L = 60, R = 14, T = 12, B = 24;
  const t0 = series[0].t, t1 = series[series.length - 1].t || t0 + 1;
  const ys = series.map((s) => s.y), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys);
  const pad = (hi - lo) * 0.08 || 1;
  const x = (t) => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0);
  const y = (v) => T + (H - T - B) * (1 - (v - (lo - pad)) / (hi - lo + 2 * pad));
  let d = `M${x(series[0].t)},${y(0)}`;
  for (let i = 1; i < series.length; i++) d += `H${x(series[i].t)}V${y(series[i].y)}`;
  const area = d + `V${y(0)}Z`;
  const date = (t) => new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ");
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Cumulative realized PnL">
    <defs><clipPath id="cAbove"><rect x="0" y="0" width="${W}" height="${y(0)}"/></clipPath><clipPath id="cBelow"><rect x="0" y="${y(0)}" width="${W}" height="${H}"/></clipPath></defs>
    <path d="${area}" fill="rgba(57,135,229,.18)" clip-path="url(#cAbove)"/>
    <path d="${area}" fill="rgba(230,103,103,.18)" clip-path="url(#cBelow)"/>
    <line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="#2b3a4f" stroke-dasharray="3 3"/>
    <path d="${d}" fill="none" stroke="#3987e5" stroke-width="2" stroke-linejoin="round"/>
    <g font-family="JetBrains Mono, monospace" font-size="10.5" fill="#8592a6">
      <text x="${L - 8}" y="${y(hi) + 4}" text-anchor="end">${usd(hi)}</text>
      <text x="${L - 8}" y="${y(0) + 4}" text-anchor="end">$0</text>
      ${lo < 0 ? `<text x="${L - 8}" y="${y(lo) + 4}" text-anchor="end">${usd(lo)}</text>` : ""}
      <text x="${L}" y="${H - 6}">${date(t0)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end">${date(t1)}</text>
    </g>
    <line id="cv-x" y1="${T}" y2="${H - B}" stroke="#ffc533" stroke-width="1" opacity="0"/>
    <circle id="cv-dot" r="4" fill="#3987e5" stroke="#0f151f" stroke-width="2" opacity="0"/>
    <rect x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent" id="cv-hit"/>
  </svg><div class="tip hidden" id="cv-tip"></div>`;
  const hit = $("#cv-hit"), tip = $("#cv-tip"), vx = $("#cv-x"), dot = $("#cv-dot");
  hit.addEventListener("mousemove", (ev) => {
    const rect = box.getBoundingClientRect();
    const px = (ev.clientX - rect.left) * (W / rect.width);
    let best = series[1];
    for (const s of series.slice(1)) if (Math.abs(x(s.t) - px) < Math.abs(x(best.t) - px)) best = s;
    vx.setAttribute("x1", x(best.t)); vx.setAttribute("x2", x(best.t)); vx.setAttribute("opacity", ".6");
    dot.setAttribute("cx", x(best.t)); dot.setAttribute("cy", y(best.y)); dot.setAttribute("opacity", "1");
    tip.innerHTML = `${date(best.t)} · ${esc(best.p.pair)}<br>this close ${signed(posPnl(best.p))} · total ${signed(best.y)}`;
    tip.style.left = (x(best.t) / W) * rect.width + "px";
    tip.style.top = (y(best.y) / H) * rect.height + "px";
    tip.classList.remove("hidden");
  });
  hit.addEventListener("mouseleave", () => { tip.classList.add("hidden"); vx.setAttribute("opacity", "0"); dot.setAttribute("opacity", "0"); });
}

function closeDrawer() { $("#drawer").classList.add("hidden"); drawerAddr = null; }
$("#dr-close").onclick = closeDrawer;
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  closeDrawer();
  ["#settings", "#guide", "#pop"].forEach((s) => $(s).classList.add("hidden"));
});
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
    const oursMap = ours ? { total_pnl: usd(ours.net + ours.float), win_rate: pct0(ours.win), total_fee: usd(ours.fees), closed_lp: int(ours.closed) } : {};
    for (const p of protos) {
      h += `<div class="lp-grid" style="margin-bottom:8px"><div class="h">${esc(p)}</div><div class="h">LP Agent</div><div class="h">This app (${state.status.metric_days}d)</div>`;
      for (const [f, label, kind] of j.fields) h += `<div>${esc(label)}</div><div>${fmtv(ov[p][f], kind)}</div><div>${oursMap[f] || ""}</div>`;
      h += "</div>";
    }
  } else if (!(j.errors || []).length) h += "<div>LP Agent has no Uniswap overview for this wallet.</div>";
  if ((j.opening || []).length) {
    h += `<div class="d-title" style="margin-top:10px">Open on LP Agent (${j.opening.length})</div><div class="lp-grid">` +
      '<div class="h">Pair</div><div class="h">Value</div><div class="h">PnL</div>' +
      j.opening.map((o) => `<div>${esc(o.pair)}${o.in_range === false ? " · out of range" : ""}</div><div>${usd(o.value)}</div><div>${signed(o.pnl)}${num(o.pnl_pct) !== null ? " (" + f1(o.pnl_pct) + "%)" : ""}</div>`).join("") + "</div>";
  }
  h += `<div style="margin-top:8px"><a href="${esc(j.link)}" target="_blank" rel="noopener">Open on LP Agent ↗</a>${j.cached ? ' <span class="muted">(cached)</span>' : ""}</div>`;
  return h;
}

// ---------- twin finder ----------
const SIM_COLS = ["score", "addr", "verdict", "net", "roi", "win", "dd", "style", "duration", "range", "size", "eth", "mcap", "pos", "concur", "star", "lp"].map((k) => colByKey[k]);
const SIM_COL = { g: "id", k: "sim", t: "Match", v: (r) => r.sim, f: (v) => v + "%" };
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
  $("#sim-target").innerHTML = `Matching <b>${esc(short(t.addr))}</b>${t.label ? " (" + esc(t.label) + ")" : ""}: ${esc(t.style || "")} · hold ${dur(t.duration)} · range ${rangeFmt(t.range)} · size ${usd(t.size)} · ETH ${pct0(t.eth)} · score ${f1(t.score)}`;
  const keep = state.ranks;
  computeRanks(j.rows);
  $("#sim-grid thead").innerHTML = headHtml([SIM_COL, ...SIM_COLS]);
  $("#sim-grid tbody").innerHTML = j.rows.map((r) => rowHtml(r, [SIM_COL, ...SIM_COLS])).join("");
  state.ranks = keep;
}
$("#sim-go").onclick = runSimilar;
$("#sim-addr").addEventListener("keydown", (e) => { if (e.key === "Enter") runSimilar(); });

// ---------- watchlist ----------
async function loadWatch() {
  const [lab, wl] = await Promise.all([api("/api/labels"), api("/api/wallets?view=" + (state.real ? "real" : "raw"))]);
  const scored = Object.fromEntries((wl.rows || []).map((r) => [r.addr, r]));
  const rows = (lab.rows || []).map((l) => ({ ...(scored[l.addr] || {}), addr: l.addr, label: l.label, notes: l.notes, starred: !!l.starred, _scored: !!scored[l.addr] }));
  const keep = state.ranks;
  computeRanks(wl.rows || []);
  const cols = [
    colByKey.addr, { g: "id", k: "notes", t: "Notes", l: 1, f: (v) => esc(v || "") },
    { g: "id", k: "score", t: "Score", f: (v, r) => (r._scored ? f1(v) : "not ranked yet"), heat: "up" },
    colByKey.verdict, colByKey.net, colByKey.roi, colByKey.win, colByKey.pos, colByKey.style, colByKey.last,
    colByKey.star, colByKey.lp, colByKey.scan,
    { g: "links", k: "rm", t: "", c: 1, f: (v, r) => `<button class="chip" data-rm="${r.addr}">remove</button>` },
  ];
  $("#watch-grid thead").innerHTML = headHtml(cols);
  $("#watch-grid tbody").innerHTML = rows.map((r) => rowHtml(r, cols)).join("");
  $("#watch-empty").classList.toggle("hidden", rows.length > 0);
  $("#watch-count").textContent = rows.length ? `(${rows.length})` : "";
  state.ranks = keep;
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
  ["RPC_URL", "CALL_RPC_URL", "LPAGENT_API_KEY"].forEach((k) => ($("#set-" + k).value = ""));
  const src = state.status.rpc_source && state.status.rpc_source !== "settings" ? ", from " + state.status.rpc_source : "";
  $("#set-rpc-now").textContent = state.status.rpc ? `(now: ${state.status.rpc}${src})` : "(not set)";
  $("#set-call-now").textContent = state.status.call_rpc ? `(now: ${state.status.call_rpc}${src})` : "(not set: calls use the logs RPC)";
  $("#set-lp-now").textContent = state.status.has_lpagent_key ? "(set)" : "(not set)";
  $("#set-msg").textContent = "";
  $("#settings").classList.remove("hidden");
};
$("#set-save").onclick = async () => {
  const body = {};
  document.querySelectorAll("[id^=set-]").forEach((el) => { if (el.tagName === "INPUT" && el.value.trim()) body[el.id.slice(4)] = el.value.trim(); });
  const j = await api("/api/settings", body);
  $("#set-msg").textContent = j.error ? j.error : "Saved. Sync restarts with the new settings.";
  if (!j.error) setTimeout(() => $("#settings").classList.add("hidden"), 900);
  loadStatus();
};

// ---------- field guide ----------
$("#btn-guide").onclick = () => $("#guide").classList.remove("hidden");
$("#legend").innerHTML = "Cell colour against the other ranked wallets (Realized, Unrealized, ROI: above / below zero): " +
  `<span>worse</span>${[0.44, 0.3, 0.16].map((a) => `<span class="sw" style="background:rgba(var(--red),${a})"></span>`).join("")}` +
  '<span class="sw"></span>' +
  `${[0.16, 0.3, 0.44].map((a) => `<span class="sw" style="background:rgba(var(--blue),${a})"></span>`).join("")}<span>better</span>` +
  '<span class="muted">· uncoloured columns describe style, not quality</span>';
$("#guide-body").innerHTML = Object.entries(GROUPS).map(([g, name]) =>
  `<div class="grp"><h4>${esc(name)}</h4>${COLS.filter((c) => c.g === g && c.d).map((c) => `<p><b>${esc(c.t)}</b>${esc(c.d)}</p>`).join("")}</div>`).join("") +
  `<div class="grp"><h4>Modes</h4><p><b>Real PnL</b>Charges each position for selling back the token it returns (pool fee + slippage from Settings) and re-ranks on those figures.</p>
   <p><b>Last 24h</b>The same board computed only on positions closed in the last 24 hours.</p>
   <p><b>Token launches</b>Positions that seed 2%+ of a token's supply are launches, not trading, and are left out of every number.</p>
   <p><b>Data</b>Everything comes from Robinhood Chain through your RPC: Uniswap V4 PoolManager / PositionManager events, prices from pool state at each block, ETH from the ETH/USDG pool.</p></div>`;

// ---------- console ----------
$("#btn-log").onclick = () => $("#log").classList.toggle("hidden");
$("#btn-sync").onclick = async () => { await api("/api/sync", {}); $("#worker-state").textContent = "starting…"; };

// ---------- boot ----------
renderPresets();
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
