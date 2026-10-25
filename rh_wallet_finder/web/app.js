"use strict";
// RH Wallet Finder, public board. Renders the snapshot the scanner publishes
// (rh_wallet_finder/publish.py -> Vercel Blob -> /api/snapshot). Read-only:
// the same boards, filters and figures as the app, nothing personal.

const BLUE = "57,135,229", RED = "230,103,103", STEPS = [0.16, 0.30, 0.44];
const VERDICT_BG = { Elite: `rgba(${BLUE},0.46)`, Strong: `rgba(${BLUE},0.24)`, Decent: "rgba(255,255,255,0.07)",
                     Weak: `rgba(${RED},0.24)`, Avoid: `rgba(${RED},0.46)` };
const TIERS = ["Elite", "Strong", "Decent", "Weak", "Avoid"];
const FOLLOW = { "✓": "✓ yes", "✗": "✗ bot", "?": "? maybe" };
const KIND = { multiday: "Multiday", degen: "Degen" };
const PAGE = 300, REFRESH_MS = 120000, STALE_S = 1800;
const lpagent = a => `https://app.lpagent.io/portfolio?address=${encodeURIComponent(a)}&chain=ROBINHOOD`;
const explorer = a => `https://robinhoodchain.blockscout.com/address/${encodeURIComponent(a)}`;

// ---------------------------------------------------------------- format --
const num = v => (v === null || v === undefined || v === "" || Number.isNaN(+v)) ? null : +v;
function compact(v, d = 1) {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(d) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(d) + "M";
  if (a >= 1e4) return (v / 1e3).toFixed(d) + "K";
  if (a >= 100) return v.toFixed(0);
  return v.toFixed(a >= 10 ? 1 : 2);
}
const usd = v => { v = num(v); return v === null ? "–" : Math.abs(v) < 0.005 ? "$0" : (v < 0 ? "-$" : "$") + compact(Math.abs(v)); };
const pct = (v, d = 0) => { v = num(v); return v === null ? "–" : (Math.abs(v) < 0.5 / 10 ** d ? 0 : v).toFixed(d) + "%"; };
const f1 = v => { v = num(v); return v === null ? "–" : v.toFixed(1); };
const int = v => { v = num(v); return v === null ? "–" : v.toFixed(0); };
function dur(s) {
  s = num(s); if (s === null) return "–"; s = Math.max(0, s);
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${(s / 60).toFixed(0)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(0)}h`;
  return `${(s / 86400).toFixed(s < 864000 ? 1 : 0)}d`;
}
function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m ago`;
  return `${(s / 86400).toFixed(1)}d ago`;
}
const rangeFmt = v => { v = num(v); return v === null ? "–" : v >= 1e6 ? "full" : v >= 1e4 ? compact(v, 0) + "%" : v.toFixed(0) + "%"; };
const pfFmt = v => { v = num(v); return v === null ? "–" : v >= 99 ? "∞" : v < 10 ? v.toFixed(2) : v.toFixed(0); };
const money = v => v >= 1000 ? `$${v / 1000}k` : `$${v}`;
const short = a => `${a.slice(0, 6)}…${a.slice(-4)}`;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// --------------------------------------------------------------- columns --
// (key, title, group, heat: up | down | signed | tier, format, help). {d} = window in days.
const COLS = [
  ["rank", "#", "Wallet", null, int, "Place on the board, by score."],
  ["score", "Score", "Wallet", "up", f1, "0–100, against wallets of the same class only. Degen: realized PnL, ROI, profit factor, win rate, green days, max drawdown, fees, trade count. Multiday: total PnL (open positions count), ROI, green days, max drawdown, win rate, fees, recovery, trade count. Few trades pull it toward 50; a losing wallet is capped at 45."],
  ["wallet", "Wallet", "Wallet", null, null, "Click a row for the wallet's details. ◆ = a reference wallet of its class."],
  ["kind", "Class", "Wallet", null, v => KIND[v] || "–", "Multiday: its trades' median hold is at least the scanner's line (2 h by default); Degen: below it."],
  ["verdict", "Tier", "Wallet", "tier", v => v || "–", "Elite ≥ 70, Strong ≥ 60, Decent ≥ 50, Weak ≥ 40, else Avoid."],
  ["worth", "Net worth", "Wallet", null, usd, "ETH + USDG in the wallet + what its open positions are worth (other tokens it holds aren't counted)."],
  ["net", "PnL {d}D", "Performance", "signed", usd, "Realized: positions closed in the window, everything taken out + fees − everything put in."],
  ["roi_nw", "ROI {d}D", "Performance", "signed", v => pct(v, 1), "PnL {d}D ÷ net worth now (how RH Discovery reads ROI). A wallet that took its profit out reads high."],
  ["net_7d", "PnL 7D", "Performance", "signed", usd, "Realized PnL of positions closed in the last 7 days."],
  ["momentum", "7D/{d}D", "Performance", null, v => num(v) === null ? "–" : (+v).toFixed(2), "Share of the window's PnL made in the last 7 days (when that PnL is positive). About 0.23 is an even pace; higher = heating up, near 0 = gone quiet."],
  ["net_1d", "PnL 24h", "Performance", "signed", usd, "Realized PnL of positions closed in the last 24 hours."],
  ["avg_month", "Avg / month", "History", "signed", usd, "PnL ALL ÷ calendar months since its first LP."],
  ["pnl_3m", "PnL 3M", "History", "signed", usd, "Realized PnL of positions closed in the last 90 days."],
  ["pnl_all", "PnL ALL", "History", "signed", usd, "Realized PnL of every priced position, as far back as the scanner's history goes."],
  ["roi_all", "ROI ALL", "History", "signed", v => pct(v, 1), "PnL ALL ÷ everything those positions ever deposited."],
  ["float", "Open PnL", "Performance", "signed", usd, "PnL of positions still open: current value + unclaimed fees − deposits."],
  ["total", "Total {d}D", "Performance", "signed", usd, "PnL {d}D + open PnL."],
  ["win_pos", "Win", "Performance", "up", pct, "Winning positions out of positions that won or lost (RH Discovery's count). A range that ended within ±0.05% of its deposit is flat and left out."],
  ["win", "Trade win", "Performance", "up", pct, "The same per trade: ranges opened in one transaction (a ladder) count once."],
  ["pf", "Profit factor", "Performance", "up", pfFmt, "What winning trades made ÷ what losing trades lost. Above 1 makes money; ∞ = no losing trade yet."],
  ["roi_win", "Capital ROI {d}D", "Performance", "signed", v => pct(v, 1), "PnL {d}D over the capital it kept deployed in that time (time-weighted, never below its median trade)."],
  ["roi", "ROI/mo", "Performance", "signed", v => pct(v, 1), "PnL per month over the capital it keeps deployed, measured over the days it has been active."],
  ["daily", "Green days", "Performance", "up", pct, "Share of days with closes that ended in profit."],
  ["fees", "Fees {d}D", "Performance", "up", usd, "LP fees earned (claimed + unclaimed), USD."],
  ["dd", "Max DD", "Risk", "up", v => pct(v, 1), "Deepest drop of the realized PnL curve, as % of deployed capital."],
  ["recover", "Recovery", "Risk", "down", null, "How long the deepest drawdown took to win back (or has lasted so far, if it hasn't)."],
  ["usdg", "USDG %", "Style", null, pct, "Share of its deposits in USDG-paired pools (the rest ETH-paired)."],
  ["size", "Avg size", "Style", null, usd, "Median USD deposited per trade."],
  ["duration", "Hold", "Style", null, dur, "Median time a trade stays open."],
  ["trades", "Trades", "Style", null, int, "Trades in the window (closed in it, or still open). Ranges opened in one transaction count once."],
  ["style", "Style", "Style", null, null, "How its trades are built, and the share that fit: Ladder ×N (N ranges opened together) or Single range · bid (only USDG/ETH in), ask (only the token in) or 2-sided."],
  ["sim", "Like ref", "Style", null, null, "How close its LP style is to the reference wallet of its class."],
  ["range", "Range", "Style", null, rangeFmt, "Median range a trade covers (a ladder: lowest to highest price): upper / lower − 1."],
  ["mcap", "Entry mcap", "Style", null, usd, "Median market cap of the token when it opened a trade."],
  ["open", "Open", "Style", null, int, "Positions open right now."],
  ["concur", "Parallel", "Style", null, v => num(v) === null ? "–" : (+v).toFixed(1) + "×", "Average number of trades open at the same time."],
  ["copy", "Followable", "Copy-trade", null, v => FOLLOW[v] || "–", "Can a person follow it by hand? yes: median hold ≥ 30 min and ≤ 30 trades/day. bot: hold < 5 min or > 100 trades/day."],
  ["copiers", "Copiers", "Copy-trade", null, int, "Other wallets that opened the same pool with the exact same range within 60 s after this wallet, at least twice."],
  ["exposure", "Crowding", "Copy-trade", null, null, "Liquidity copiers add per open, as a share of the wallet's own. ⚠ from 50%: following it means sharing the range."],
  ["open_now", "Deployed", "Holdings", null, usd, "USD sitting in open positions now."],
  ["age", "Seen", "Holdings", null, dur, "Since its first LP in the scanner's data."],
  ["last", "Active", "Holdings", null, v => num(v) === null ? "–" : dur(v) + " ago", "Since its last LP action."],
  ["links", "Links", "Links", null, null, "The wallet on LP Agent and on the Robinhood Chain explorer."],
].map(([k, label, group, heat, fmt, help]) => ({ k, label, group, heat, fmt, help }));
const LEFT = new Set(["wallet", "kind", "verdict", "style", "sim", "copy", "links"]);
const WINDOW_ONLY = new Set(["net_7d", "momentum", "net_1d"]); // meaningless on the 24 h board

// --------------------------------------------------------------- filters --
const SELECTS = [
  ["f_closed", "Min closed", ["any", 3, 10, 30, 100], v => `${v} trades`, "Closed trades in the window (ranges opened together count once)."],
  ["f_active", "Active within", ["any", 1, 3, 7], v => v === 1 ? "24h" : `${v}d`, "Last LP action (open, add, remove, close) this recent."],
  ["f_size_min", "Min avg size", ["any", 100, 300, 1000, 5000], money, "Median USD per trade at least this."],
  ["f_size_max", "Max avg size", ["any", 1000, 5000, 20000, 100000], money, "Median USD per trade at most this."],
  ["f_worth", "Min net worth", ["any", 1000, 10000, 100000], money, "ETH + USDG in the wallet + open positions."],
  ["f_roi", "Min ROI {d}D", ["any", 0, 10, 25, 50, 100], v => `${v}%`, "PnL over the window ÷ net worth now."],
  ["f_mom", "7D/{d}D at least", ["any", 0.1, 0.25, 0.5], v => `${v}`, "Share of the window's PnL made in the last 7 days: still making money now."],
  ["f_usdg", "Min USDG %", ["any", 50, 90, 100], v => `${v}%`, "Share of deposits in USDG-paired pools."],
  ["f_win", "Min win", ["any", 50, 60, 70, 80, 90], v => `${v}%`, "Winning positions out of decided ones."],
  ["f_float", "Max open loss", ["any", 100, 1000, 10000], v => "-" + money(v), "Open PnL no worse than this."],
  ["f_dd", "Max drawdown", ["any", 5, 10, 25, 50], v => `-${v}%`, "Deepest drop no worse than this."],
  ["f_tier", "Min tier", ["any", "Elite", "Strong", "Decent"], v => v === "Elite" ? v : `${v}+`, "Tier at least this."],
  ["f_follow", "Followable", ["any", "yes", "yes or maybe"], String, "Hand-followable pace (see the Followable column)."],
  ["f_style", "Style", ["any", "ladder", "single", "bid", "ask", "2-sided"], String, "Words in the Style column."],
];
const CHECKS = [
  ["f_p30", "{d}D profit only", "PnL over the window above zero."],
  ["f_p7", "7D profit only", "PnL of the last 7 days above zero."],
  ["f_usdg_only", "USDG pairs only", "Every deposit in USDG-paired pools."],
  ["f_bots", "Show bots", "Hold < 5 min or > 100 trades a day: nobody can follow those."],
];
const DEFAULTS = Object.fromEntries([...SELECTS.map(s => [s[0], "any"]), ...CHECKS.map(c => [c[0], false]), ["q", ""]]);
const PRESETS = {
  "All wallets": {},
  "Proven earners": { f_closed: 30, f_active: 7, f_size_min: 300, f_worth: 1000, f_p30: true, f_usdg_only: true },
  "Hot this week": { f_active: 3, f_p7: true, f_mom: 0.25 },
  "Followable winners": { f_follow: "yes", f_p30: true, f_win: 60 },
  "Low drawdown": { f_p30: true, f_dd: 10 },
  "Size $1k+": { f_size_min: 1000 },
  "Ladders": { f_style: "ladder" },
};

// ----------------------------------------------------------------- state --
const state = { snap: null, board: "multiday", real: false, sort: { k: "rank", dir: 1 }, shown: PAGE, filters: {} };
const $ = id => document.getElementById(id);
const store = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private window: fine */ } },
};
const filters = () => (state.filters[state.board] ||= { ...DEFAULTS, ...(store.get(`rhwf.f.${state.board}`) || {}) });
const days = () => state.board === "day" ? 1 : (state.snap?.status.metric_days || 30);
const fmtLabel = s => s.replace("{d}", days());

function readHash() {
  const parts = location.hash.replace("#", "").split("&");
  if (["multiday", "degen", "day"].includes(parts[0])) state.board = parts[0];
  state.real = parts.includes("real");
}
function writeHash() {
  history.replaceState(null, "", `#${state.board}${state.real ? "&real" : ""}`);
}

// ------------------------------------------------------------------ rows --
function boardRows() {
  const s = state.snap;
  const view = (state.board === "day" ? "d1" : "") + (state.real ? "real" : "raw");
  let rows = (s.views[view] || []).slice();
  if (state.board !== "day") rows = rows.filter(r => r.kind === state.board);
  const now = Date.now() / 1000;
  rows.forEach((r, i) => {
    r.rank = i + 1;
    r.age = r.first_seen ? now - r.first_seen : null;
    r.last = r.last_seen ? now - r.last_seen : null;
  });
  return rows;
}

function applyFilters(rows) {
  const f = filters();
  const v = k => f[k] === "any" ? null : f[k];
  const atLeast = { f_closed: "closed_trades", f_size_min: "size", f_worth: "worth", f_roi: "roi_nw", f_mom: "momentum",
                    f_usdg: "usdg", f_win: "win_pos" };
  const q = (f.q || "").trim().toLowerCase();
  return rows.filter(r => {
    if (q && !r.addr.includes(q)) return false;
    if (!f.f_bots && r.copy === "✗") return false;
    for (const [k, c] of Object.entries(atLeast)) {
      if (v(k) !== null && !(num(r[c]) !== null && r[c] >= v(k))) return false;
    }
    if (v("f_active") !== null && !(r.last !== null && r.last <= v("f_active") * 86400)) return false;
    if (v("f_size_max") !== null && !(num(r.size) !== null && r.size <= v("f_size_max"))) return false;
    if (v("f_float") !== null && !((r.float || 0) >= -v("f_float"))) return false;
    if (v("f_dd") !== null && !(num(r.dd) !== null && r.dd >= -v("f_dd"))) return false;
    if (v("f_tier") !== null && !TIERS.slice(0, TIERS.indexOf(v("f_tier")) + 1).includes(r.verdict)) return false;
    if (v("f_follow") !== null && !(v("f_follow") === "yes" ? ["✓"] : ["✓", "?"]).includes(r.copy)) return false;
    if (v("f_style") !== null && !(r.style || "").toLowerCase().includes(v("f_style"))) return false;
    if (f.f_p30 && !(r.net > 0)) return false;
    if (f.f_p7 && !(r.net_7d > 0)) return false;
    if (f.f_usdg_only && !(r.usdg >= 99.95)) return false;
    return true;
  });
}

// cell background: blue better, red worse, middle uncoloured (equal steps per arm), as in the app
function heatMap(rows, col) {
  const out = new Map();
  if (col.heat === "tier") { rows.forEach(r => out.set(r.addr, VERDICT_BG[r.verdict] || "")); return out; }
  const vals = rows.map(r => num(r[col.k]));
  if (col.heat === "signed") {
    const pos = vals.filter(v => v !== null && v > 0).sort((a, b) => a - b);
    const neg = vals.filter(v => v !== null && v < 0).map(v => -v).sort((a, b) => a - b);
    rows.forEach((r, i) => {
      const v = vals[i];
      if (v === null || Math.abs(v) < 0.005) return;
      const ref = v > 0 ? pos : neg;
      const q = ref.length > 1 ? ref.filter(x => x < Math.abs(v)).length / (ref.length - 1) : 1;
      out.set(r.addr, `rgba(${v > 0 ? BLUE : RED},${STEPS[q < 0.34 ? 0 : q < 0.67 ? 1 : 2]})`);
    });
    return out;
  }
  const valid = vals.filter(v => v !== null).sort((a, b) => a - b);
  if (new Set(valid).size < 2) return out;
  const rankOf = v => { // average rank of ties, 0..n-1
    let lo = 0, hi = 0;
    for (const x of valid) { if (x < v) lo++; if (x <= v) hi++; }
    return (lo + hi - 1) / 2;
  };
  rows.forEach((r, i) => {
    const v = vals[i]; if (v === null) return;
    let p = rankOf(v) / (valid.length - 1);
    if (col.heat === "down") p = 1 - p;
    const d = Math.abs(p - 0.5); if (d < 0.12) return;
    out.set(r.addr, `rgba(${p > 0.5 ? BLUE : RED},${STEPS[d < 0.25 ? 0 : d < 0.38 ? 1 : 2]})`);
  });
  return out;
}

function cell(col, r) {
  switch (col.k) {
    case "wallet": return `${r.ref ? '<span class="ref" title="Reference wallet of its class">◆</span> ' : ""}${esc(short(r.addr))}`;
    case "recover": return !num(r.recover) && !r.recover_open ? "–" : (r.recover_open ? ">" : "") + dur(r.recover);
    case "style": return r.style ? esc(`${r.style} ${r.style_conf ?? ""}%`) : "–";
    case "sim": return r.sim_ref && num(r.sim_pct) !== null ? `ref ${r.sim_pct}%` : "–";
    case "copy": return `<span class="${r.copy === "✓" ? "yes" : r.copy === "✗" ? "bot" : ""}">${FOLLOW[r.copy] || "–"}</span>`;
    case "exposure": {
      const v = (num(r.exposure) || 0) * 100;
      return v >= 50 ? `<span class="crowd">⚠ ${v.toFixed(0)}%</span>` : `${v.toFixed(0)}%`;
    }
    case "links": return `<a href="${lpagent(r.addr)}" target="_blank" rel="noopener">LP Agent ↗</a> · ` +
                         `<a href="${explorer(r.addr)}" target="_blank" rel="noopener">Explorer ↗</a>`;
    default: return esc(col.fmt ? col.fmt(r[col.k]) : (r[col.k] ?? "–"));
  }
}

// ---------------------------------------------------------------- render --
function renderHeader() {
  const s = state.snap.status, gen = state.snap.generated, now = Date.now() / 1000;
  const hist = s.data_from ? (gen - s.data_from) / 86400 : 0;
  const led = (ts, okS) => !ts ? "" : now - ts < okS ? "ok" : "warn";
  $("rail").innerHTML = [
    `<span><i class="led ${led(gen, STALE_S)}"></i><b>SNAPSHOT</b>${ago(gen)}</span>`,
    `<span><i class="led ${led(s.synced_at, STALE_S)}"></i><b>SYNC</b>${ago(s.synced_at)}</span>`,
    `<span><i class="led ${led(s.scored_at, 3 * 3600)}"></i><b>RANK</b>${ago(s.scored_at)}</span>`,
    `<span><b>HISTORY</b>${hist ? (hist < 1 ? dur(hist * 86400) : hist.toFixed(hist < 3 ? 1 : 0) + "d") : "–"}/${s.backfill_days}d</span>`,
    `<span><b>WINDOW</b>${s.metric_days}d</span>`,
    `<span><b>ETH</b>${s.eth_usd ? "$" + Math.round(s.eth_usd).toLocaleString("en-US") : "–"}</span>`,
  ].join("");
  const k = s.kinds || {};
  $("kpis").innerHTML = [
    ["Wallets", s.discovered, `LP wallets found · ${(s.scored || 0).toLocaleString("en-US")} ranked`],
    ["Multiday", k.multiday || 0, `ranked, median hold ≥ ${s.multiday_hours} h`],
    ["Degen", k.degen || 0, `ranked, median hold &lt; ${s.multiday_hours} h`],
    ["Positions", s.positions, "V4 position NFTs indexed"],
  ].map(([t, v, sub]) => `<div class="kpi"><div class="k">${t}</div><div class="v">${(v || 0).toLocaleString("en-US")}</div><div class="s">${sub}</div></div>`).join("");
  const stale = now - gen > STALE_S;
  $("notice").hidden = !stale;
  if (stale) $("notice").textContent = `This snapshot is from ${ago(gen)}: the scanner that publishes it may be offline. Figures are as of then.`;
}

function renderControls() {
  const f = filters();
  document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.board === state.board));
  $("real").checked = state.real;
  const s = state.snap.status;
  const blurb = {
    multiday: `Multiday players: median hold ≥ ${s.multiday_hours} h, scored against each other on total PnL (open positions count), ROI, green days and drawdown.`,
    degen: `Degen players: median hold under ${s.multiday_hours} h, scored against each other on realized PnL, ROI, profit factor and win rate. Bots stay hidden unless you tick Show bots.`,
    day: "Both classes, on positions closed in the last 24 hours (top 100).",
  }[state.board];
  $("blurb").textContent = `${blurb} Figures cover the last ${days()} days; ranked: ${s.min_closed}+ closed positions in them` +
    (s.active_days ? ` and an LP action within ${s.active_days} days.` : ".");
  $("mode").hidden = !state.real;
  if (state.real) {
    const fee = s.sell_fee ? (s.sell_fee * 100).toFixed(2) : "–";
    $("mode").innerHTML = `<b>Real PnL on.</b> Every figure is charged for selling back the token side a position returns (median <b>${fee}%</b>: pool fee + ${s.slippage}% slippage), and the board is re-ranked on those numbers.`;
  }
  $("fgrid").innerHTML = `<label class="fld search"><span>Search</span><input type="search" id="q" placeholder="Address…" value="${esc(f.q || "")}"></label>` +
    SELECTS.map(([k, label, opts, fmt, help]) => `<label class="fld" title="${esc(help)}"><span>${esc(fmtLabel(label))}</span><select data-f="${k}">` +
      opts.map((o, i) => `<option value="${i}"${o === f[k] ? " selected" : ""}>${o === "any" ? "any" : esc(fmt(o))}</option>`).join("") +
      "</select></label>").join("");
  $("checks").innerHTML = CHECKS.map(([k, label, help]) =>
    `<label title="${esc(help)}"><input type="checkbox" data-f="${k}"${f[k] ? " checked" : ""}>${esc(fmtLabel(label))}</label>`).join("");
  $("preset").innerHTML = Object.keys(PRESETS).map(n => `<option>${esc(n)}</option>`).join("");
  $("preset").value = f.preset && PRESETS[f.preset] ? f.preset : "All wallets";
  $("q").addEventListener("input", e => { f.q = e.target.value; saveFilters(); renderBoard(); });
  $("fgrid").querySelectorAll("select").forEach(el => el.addEventListener("change", () => {
    const opts = SELECTS.find(s => s[0] === el.dataset.f)[2];
    f[el.dataset.f] = opts[+el.value]; f.preset = null; $("preset").value = "All wallets"; saveFilters(); renderBoard();
  }));
  $("checks").querySelectorAll("input").forEach(el => el.addEventListener("change", () => {
    f[el.dataset.f] = el.checked; f.preset = null; saveFilters(); renderBoard();
  }));
}

function saveFilters() { state.shown = PAGE; store.set(`rhwf.f.${state.board}`, filters()); }

function renderBoard() {
  const all = boardRows();
  const s = state.snap.status;
  const what = state.board === "day" ? "" : `${KIND[state.board].toLowerCase()} `;
  $("pills").innerHTML = [`<b>${all.length.toLocaleString("en-US")}</b> ranked ${what}`,
    `<b>${(s.rated || 0).toLocaleString("en-US")}</b> rated of <b>${(s.discovered || 0).toLocaleString("en-US")}</b> found`,
    `<b>${(s.queue || 0).toLocaleString("en-US")}</b> positions in queue`, `ranked <b>${ago(s.scored_at)}</b>`]
    .map(x => `<span>${x}</span>`).join("");
  let rows = applyFilters(all);
  if (state.board === "day") rows = rows.slice(0, 100);
  const hidden = all.length - rows.length;
  $("showing").innerHTML = `Showing <b>${rows.length.toLocaleString("en-US")}</b> of ${all.length.toLocaleString("en-US")}` +
    (hidden ? ` · ${hidden.toLocaleString("en-US")} hidden by filters` : "");

  const cols = COLS.filter(c => !(c.k === "kind" && state.board !== "day") && !(state.board === "day" && WINDOW_ONLY.has(c.k)));
  const heats = new Map(cols.filter(c => c.heat).map(c => [c.k, heatMap(all, c)]));
  const { k: sk, dir } = state.sort;
  if (cols.some(c => c.k === sk)) {
    const key = r => sk === "wallet" ? r.addr : sk === "sim" ? (r.sim_ref ? r.sim_pct : null) : sk === "exposure" ? r.exposure
      : sk === "style" ? r.style : r[sk];
    rows = rows.slice().sort((a, b) => {
      const x = key(a), y = key(b);
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
    });
  }
  const table = $("board");
  table.querySelector("thead").innerHTML = "<tr>" + cols.map((c, i) => {
    const pin = i < 3 ? ` pin pin${i}` : "";
    const sort = c.k === sk ? ` aria-sort="${dir > 0 ? "ascending" : "descending"}"` : "";
    const btn = c.k === "links" ? `<span style="display:block;padding:9px 10px">${c.label}</span>`
      : `<button type="button" data-sort="${c.k}">${esc(fmtLabel(c.label))}</button>`;
    return `<th class="${LEFT.has(c.k) ? "l" : ""}${pin}" title="${esc(c.group + " · " + fmtLabel(c.help))}"${sort}>${btn}</th>`;
  }).join("") + "</tr>";
  const body = rows.slice(0, state.shown).map(r => `<tr tabindex="0" data-addr="${esc(r.addr)}">` + cols.map((c, i) => {
    const bg = heats.get(c.k)?.get(r.addr);
    const cls = [LEFT.has(c.k) ? "l" : "", i < 3 ? `pin pin${i}` : "", c.k === "wallet" ? "wallet" : ""].filter(Boolean).join(" ");
    return `<td class="${cls}"${bg ? ` style="background-color:${bg}"` : ""}>${cell(c, r)}</td>`;
  }).join("") + "</tr>").join("");
  table.querySelector("tbody").innerHTML = body || `<tr><td class="empty l" colspan="${cols.length}">${
    all.length ? "No wallet passes these filters." : state.board === "day" ? "No wallet closed a priced position in the last 24 h yet." : "No ranked wallets in this class yet."}</td></tr>`;
  $("more").hidden = rows.length <= state.shown;
  $("more").textContent = `Show more (${(rows.length - state.shown).toLocaleString("en-US")} left)`;
  // pinned columns: offsets from the real widths
  const ths = table.querySelectorAll("thead th");
  if (ths.length > 2) {
    table.style.setProperty("--p1", `${ths[0].offsetWidth}px`);
    table.style.setProperty("--p2", `${ths[0].offsetWidth + ths[1].offsetWidth}px`);
  }
  state.rows = rows;
  renderRawLinks();
}

// ------------------------------------------------------------ raw data --
function exportUrl(format) {
  return `${location.origin}/api/export?board=${state.board}${state.real ? "&real=1" : ""}${format === "json" ? "&format=json" : ""}`;
}

function renderRawLinks() {
  const csv = exportUrl("csv");
  $("rawdata").innerHTML = `Raw data of this whole board (no filters): <a href="${esc(csv)}">CSV</a> · ` +
    `<a href="${esc(exportUrl("json"))}">JSON</a> · in Google Sheets: <code>=IMPORTDATA("${esc(csv)}")</code>`;
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportShown(format) {
  const X = window.RHWF_EXPORT, rows = state.rows || [];
  if (!X || !state.snap) return;
  const name = X.fileName(state.board, state.real, state.snap.generated, format).replace(/\.(csv|json)$/, "-shown.$1");
  if (format === "csv") return download(name, X.toCSV(rows, days()), "text/csv;charset=utf-8");
  download(name, JSON.stringify({ board: state.board, real: state.real, window_days: days(),
                                  generated_utc: new Date(state.snap.generated * 1000).toISOString(),
                                  filters: filters(), rows: X.records(rows, days()) }, null, 1), "application/json");
}

function renderGuide() {
  const legend = STEPS.slice().reverse().map(a => `<span class="sw" style="background:rgba(${RED},${a})"></span>`).join("") +
    '<span class="sw"></span>' + STEPS.map(a => `<span class="sw" style="background:rgba(${BLUE},${a})"></span>`).join("");
  const groups = {};
  COLS.forEach(c => (groups[c.group] ||= []).push([fmtLabel(c.label), fmtLabel(c.help)]));
  groups.Filters = [...SELECTS.map(s => [fmtLabel(s[1]), s[4]]), ...CHECKS.map(c => [fmtLabel(c[1]), c[2]])];
  groups["Player classes"] = [
    ["Multiday", "Its trades' median hold is at least the scanner's line (2 h by default). Ranked only against other multiday players, with open positions counted."],
    ["Degen", "Median hold under that line. Ranked only against other degen players, on realized PnL and how reliably the trades pay."],
    ["Trades", "Ranges opened in one transaction (a ladder) are one trade for win rate, size, hold and pace. PnL, fees and drawdown still add up every position."]];
  $("guidebody").innerHTML = `<div class="legend">Cell colour against the other ranked wallets of the board (PnL / Open PnL / ROI: above or below zero): <span>worse</span>${legend}<span>better</span> · uncoloured columns describe style, not quality</div>` +
    Object.entries(groups).map(([g, items]) => `<h4>${esc(g)}</h4>` + items.map(([l, h]) => `<p><b>${esc(l)}</b>${esc(h)}</p>`).join("")).join("");
}

// ---------------------------------------------------------------- drawer --
let lastFocus = null;
function openDrawer(addr) {
  const r = (state.rows || []).find(x => x.addr === addr);
  if (!r) return;
  lastFocus = document.activeElement;
  const d = `${days()}D`;
  const mom = num(r.momentum);
  const since = r.hist_from ? ` (since ${new Date(r.hist_from * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" })})` : "";
  const cells = [["Score", f1(r.score)], [`PnL ${d}`, usd(r.net)], [`ROI ${d}`, pct(r.roi_nw, 1)], [`Capital ROI ${d}`, pct(r.roi_win, 1)],
    ["ROI/mo", pct(r.roi, 1)], ["PnL 7D", usd(r.net_7d)], [`7D/${d}`, mom === null ? "–" : mom.toFixed(2)], ["PnL 24h", usd(r.net_1d)],
    ["Open PnL", usd(r.float)], [`Total ${d}`, usd(r.total)], ["Avg / month", usd(r.avg_month)], ["PnL 3M", usd(r.pnl_3m)],
    [`PnL ALL${since}`, usd(r.pnl_all)], ["ROI ALL", pct(r.roi_all, 1)], ["Win", pct(r.win_pos)], ["Trade win", pct(r.win)],
    ["Profit factor", pfFmt(r.pf)], ["Green days", pct(r.daily)],
    ["Max DD", pct(r.dd, 1)], ["Recovery", cell({ k: "recover" }, r)], [`Fees ${d}`, usd(r.fees)], ["Avg size", usd(r.size)],
    ["Hold", dur(r.duration)], ["Range", rangeFmt(r.range)], ["Trades", `${int(r.trades)} · ${int(r.closed_trades)} closed`],
    ["Flat trades", pct(r.flat)], ["Open", `${int(r.open)} positions`], ["Deployed", usd(r.open_now)], ["Net worth", usd(r.worth)],
    ["USDG %", pct(r.usdg)], ["Entry mcap", usd(r.mcap)], ["Parallel", cell(COLS.find(c => c.k === "concur"), r)],
    ["Active", r.last === null ? "–" : dur(r.last) + " ago"]];
  $("dtitle").textContent = r.addr;
  $("dbody").innerHTML =
    `<div class="chips"><span class="tagv" style="background:${VERDICT_BG[r.verdict] || ""}">${esc(r.verdict || "–")}</span>` +
    (r.kind ? `<span class="chip" style="color:var(--gold)">${KIND[r.kind]} player · ${r.ref ? "reference" : `${r.kind_fit ?? 0}% of trades fit`}</span>` : "") +
    (r.style ? `<span class="chip">${esc(r.style)}</span>` : "") + `<span class="chip">${FOLLOW[r.copy] || "–"}</span>` +
    (r.copiers ? `<span class="chip" style="color:var(--warn)">⚠ ${r.copiers} copier(s)</span>` : "") + "</div>" +
    `<div class="chips"><a class="chip" href="${lpagent(r.addr)}" target="_blank" rel="noopener">LP Agent ↗</a>` +
    `<a class="chip" href="${explorer(r.addr)}" target="_blank" rel="noopener">Explorer ↗</a>` +
    `<button type="button" class="chip" id="copyaddr">Copy address</button></div>` +
    `<div class="dk">${cells.map(([k, v]) => `<div><small>${esc(k)}</small><span>${v}</span></div>`).join("")}</div>` +
    `<p class="muted" style="font-size:13px">#${r.rank} on the ${state.board === "day" ? "24 h" : KIND[state.board].toLowerCase()} board` +
    `${state.real ? " (Real PnL)" : ""}. Positions, labels and the PnL chart stay in the scanner app.</p>`;
  $("copyaddr").addEventListener("click", e => {
    navigator.clipboard?.writeText(r.addr).then(() => { e.target.textContent = "Copied"; }, () => {});
  });
  $("scrim").hidden = false; $("drawer").hidden = false; $("dclose").focus();
}
function closeDrawer() {
  $("scrim").hidden = true; $("drawer").hidden = true;
  if (lastFocus) lastFocus.focus();
}

// ------------------------------------------------------------------ load --
function renderAll() {
  renderHeader(); renderControls(); renderBoard(); renderGuide();
}

async function load(first) {
  try {
    const r = await fetch("api/snapshot", { cache: "no-store" });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || !data.views) throw new Error(data.error || `HTTP ${r.status}`);
    const changed = !state.snap || state.snap.generated !== data.generated;
    state.snap = data;
    if (first || changed) renderAll(); else renderHeader();
  } catch (e) {
    if (first) {
      $("notice").hidden = false;
      $("notice").textContent = `The board isn't available right now: ${e.message}`;
      $("board").querySelector("tbody").innerHTML = '<tr><td class="empty">Nothing to show yet.</td></tr>';
    }
  }
}

function wire() {
  document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => {
    state.board = b.dataset.board; state.shown = PAGE; state.sort = { k: "rank", dir: 1 }; writeHash();
    if (state.snap) { renderControls(); renderBoard(); renderGuide(); }
  }));
  $("real").addEventListener("change", e => { state.real = e.target.checked; writeHash(); if (state.snap) { renderControls(); renderBoard(); } });
  $("preset").addEventListener("change", e => {
    state.filters[state.board] = { ...DEFAULTS, ...PRESETS[e.target.value], preset: e.target.value };
    saveFilters(); renderControls(); renderBoard();
  });
  $("clear").addEventListener("click", () => {
    state.filters[state.board] = { ...DEFAULTS }; saveFilters(); renderControls(); renderBoard();
  });
  $("board").querySelector("thead").addEventListener("click", e => {
    const k = e.target.closest("button")?.dataset.sort; if (!k) return;
    const numeric = !["wallet", "kind", "verdict", "style", "copy"].includes(k);
    state.sort = state.sort.k === k ? { k, dir: -state.sort.dir } : { k, dir: k === "rank" || !numeric ? 1 : -1 };
    renderBoard();
  });
  const tbody = $("board").querySelector("tbody");
  tbody.addEventListener("click", e => {
    if (e.target.closest("a")) return;
    const tr = e.target.closest("tr[data-addr]"); if (tr) openDrawer(tr.dataset.addr);
  });
  tbody.addEventListener("keydown", e => {
    const tr = e.target.closest("tr[data-addr]");
    if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDrawer(tr.dataset.addr); }
  });
  $("more").addEventListener("click", () => { state.shown += PAGE; renderBoard(); });
  $("exp_csv").addEventListener("click", () => exportShown("csv"));
  $("exp_json").addEventListener("click", () => exportShown("json"));
  $("dclose").addEventListener("click", closeDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
  window.addEventListener("hashchange", () => { readHash(); if (state.snap) { renderControls(); renderBoard(); renderGuide(); } });
}

readHash();
wire();
load(true);
setInterval(() => load(false), REFRESH_MS);
