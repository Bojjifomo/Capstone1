// Elysium Terminal web: Uniswap v4 + v3 on Elysium. Entry check, PING, watchlist, ladder zap, portfolio.
// and the wallet's ladders with exits. Talks only to the local server (web.py); nothing here holds a key.
"use strict";

function showFatal(msg) {
  const el = document.getElementById("poolsEmpty");
  if (!el) return;
  el.classList.remove("hidden");
  el.innerHTML = `<span style="color:#ff5a5f">Error di browser: ${String(msg).replace(/[<>&]/g, "")}</span><br>Coba Cmd+Shift+R. Kalau tetap, screenshot ini + Console (Cmd+Option+J).`;
}
window.addEventListener("error", e => showFatal(e.message + (e.filename ? ` (${e.filename.split("/").pop()}:${e.lineno})` : "")));
// A failed background fetch is not fatal: say it quietly instead of replacing the table with an error.
window.addEventListener("unhandledrejection", e => { console.warn(e.reason); try { toast("⚠ " + ((e.reason && e.reason.message) || e.reason)); } catch (x) {} });

const $ = s => document.querySelector(s);
const isPhone = () => window.matchMedia("(max-width: 760px)").matches;
const state = {
  view: "pools", win: "1h", sort: { key: "fees", desc: true }, data: null, sel: null, pf: null, cfg: null,
  wallet: "", loadedAt: 0, unit: "usd", mode: "all", watch: [], notify: false, search: null,
};
const SHARED_PREFS = ["unit", "mode", "win", "sort", "tvl", "filters", "view", "wallet"];
const store = {
  get(k, d) { try { const v = localStorage.getItem("rhterm." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) {
    try { localStorage.setItem("rhterm." + k, JSON.stringify(v)); } catch (e) {}
    if (SHARED_PREFS.includes(k)) {
      fetch("/api/prefs", { method: "POST", headers: { "Content-Type": "application/json", "X-ELY-Trade": "1" }, body: JSON.stringify({ [k]: v }) }).catch(() => {});
    }
  },
  async pull() {
    try {
      const p = await (await fetch("/api/prefs")).json();
      for (const k of SHARED_PREFS) if (p[k] !== undefined) localStorage.setItem("rhterm." + k, JSON.stringify(p[k]));
    } catch (e) {}
  },
};

// ---------- formatting ----------
const SUB = "₀₁₂₃₄₅₆₇₈₉";
const tiny = a => {
  const z = Math.floor(-Math.log10(a));
  if (z < 4) return a.toPrecision(3);
  const digits = Math.round(a * Math.pow(10, z + 3)).toString().slice(0, 3);
  return "0.0" + String(z).split("").map(d => SUB[+d]).join("") + digits;
};
const fmtUsd = v => {
  if (v == null || !isFinite(v)) return "--";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e9) return s + "$" + (a / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return s + "$" + (a / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return s + "$" + (a / 1e3).toFixed(a >= 1e5 ? 1 : 2) + "k";
  if (a >= 1) return s + "$" + a.toFixed(2);
  if (a === 0) return "$0";
  return s + "$" + tiny(a);
};
const fmtEth = v => {
  if (v == null || !isFinite(v)) return "--";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e4) return s + "H" + (a / 1e3).toFixed(1) + "k";
  if (a >= 10) return s + "H" + a.toFixed(1);
  if (a >= 0.01) return s + "H" + a.toFixed(3);
  if (a === 0) return "H0";
  return s + "H" + tiny(a);
};
const fmtPct = (v, d = 1, sign = false) => v == null || !isFinite(v) ? "--" : (sign && v > 0 ? "+" : "") + v.toFixed(d) + "%";
const ethUsd = () => (state.data && state.data.ethUsd) || (state.pf && state.pf.ethUsd) || null;
// Every dollar amount goes through mU, so the $ / H switch changes the whole page.
const mU = usd => {
  if (usd == null || !isFinite(usd)) return "--";
  return state.unit === "eth" && ethUsd() ? fmtEth(usd / ethUsd()) : fmtUsd(usd);
};
const mAlt = usd => usd == null || !ethUsd() ? "" : state.unit === "eth" ? fmtUsd(usd) : fmtEth(usd / ethUsd());
const cls = v => v == null ? "muted" : v > 0 ? "pos" : v < 0 ? "neg" : "muted";
const age = h => h == null ? "--" : h < 1 ? Math.round(h * 60) + "m" : h < 48 ? h.toFixed(1) + "h" : (h / 24).toFixed(1) + "d";
const short = a => a ? a.slice(0, 6) + "…" + a.slice(-4) : "";
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const EXPLORER = "https://elysium.kinetiq.xyz/explorer";
const txLink = h => h ? `<a href="${EXPLORER}/tx/${h}" target="_blank" rel="noopener">${h.slice(0, 10)}… ↗</a>` : "";
const qchip = r => `<span class="qchip ${esc(r.quoteSymbol)}">${esc(r.quoteSymbol)}</span>${r.dex ? `<span class="dex">${esc(r.dex === "v4" ? "v4" : r.venue === "elysiumswap" ? "ES" : "uni")}</span>` : ""}`;
const HRS = { "5m": 1 / 12, "1h": 1, "6h": 6, "24h": 24 };

// Gate / flag reasons, from the server's terse form to something readable.
function nice(t) {
  let m;
  if ((m = /^lpFee: (.*)% < (.*)%/.exec(t))) return `Fee LP ${m[1]}%: LP nggak dapat apa-apa (min ${m[2]}%)`;
  if ((m = /^lpFee: (.*)% > (.*)%/.exec(t))) return `Fee ${m[1]}%: kemungkinan honeypot (maks ${m[2]}%)`;
  if ((m = /^poolAge: (\d+)m < (\d+)m/.exec(t))) return `Pool baru ${m[1]}m (min ${m[2]}m)`;
  if (/^tvl: unknown/.test(t)) return "TVL belum kebaca (DexScreener belum index)";
  if ((m = /^tvl: (.*) < (.*)/.exec(t))) return `TVL ${m[1]} (min ${m[2]})`;
  if ((m = /^mcap: (.*) < (.*)/.exec(t))) return `Mcap ${m[1]} (min ${m[2]})`;
  if ((m = /^feePace: (.*) < (.*)/.exec(t))) return `Fee pelan: ${m[1]} (min ${m[2]})`;
  if ((m = /^pump1h (.*)/.exec(t))) return `Pump 1 jam ${m[1]}`;
  if ((m = /^drop6h (.*)/.exec(t))) return `Turun 6 jam ${m[1]}`;
  if ((m = /^tvl (.*)/.exec(t))) return `Pool tipis: TVL ${m[1]}`;
  if ((m = /^sellers (.*)/.exec(t))) return `Seller dominan: ${m[1]}`;
  if (/^hook pool/.test(t)) return "Pool pakai hook (perilaku custom)";
  if ((m = /^night entry (.*)/.exec(t))) return `Entry malam ${m[1]} WIB`;
  return t;
}

function spark(r, w = 64, h = 22) {
  const c = r.chg || {};
  const legs = [["24h", c["24h"]], ["6h", c["6h"]], ["1h", c["1h"]], ["5m", c["5m"]]];
  const xs = { "24h": 0, "6h": 18 / 24, "1h": 23 / 24, "5m": 1 - 5 / 1440 };
  const pts = legs.filter(l => l[1] != null && l[1] > -100).map(([k, v]) => [xs[k], 1 / (1 + v / 100)]);
  if (pts.length < 2) return "";
  pts.push([1, 1]);
  const ys = pts.map(p => p[1]), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${(p[0] * w).toFixed(1)},${(h - 2 - (p[1] - lo) / span * (h - 4)).toFixed(1)}`).join("");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="${d}" fill="none" stroke="${(c["24h"] || 0) >= 0 ? "#3ddc84" : "#ff5a5f"}" stroke-width="1.4"/></svg>`;
}

// ---------- data ----------
async function getJson(url, opt, timeoutMs) {
  const ctl = new AbortController();
  const ms = timeoutMs || 90000;
  const timer = setTimeout(() => ctl.abort(), ms);
  let r;
  try { r = await fetch(url, { ...(opt || {}), signal: ctl.signal }); }
  catch (e) { throw new Error(e.name === "AbortError" ? `${url.split("?")[0]} nggak jawab dalam ${Math.round(ms / 1000)} detik` : e.message); }
  finally { clearTimeout(timer); }
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
async function loadPools(force) {
  try {
    const d = await getJson(`/api/pools${force ? "?force=1" : ""}`);
    const fresh = state.data && d.ping && (d.ping.new || []).length;
    state.data = d;
    state.loadedAt = Date.now();
    if (fresh) {
      const names = d.ping.rows.filter(r => d.ping.new.includes(r.pool)).map(r => r.symbol);
      if (names.length) { toast(`📡 Baru masuk PING: ${names.join(", ")}`); notify("📡 PING baru", names.join(", ")); }
    }
    renderTop();
    renderPools();
    if (state.pf) renderPortfolio();
    const r = state.sel && findRow(state.sel);
    if (r) renderDetail(r, true);
    // the index fills in over the first minutes: poll faster until it has an hour
    if ((d.index || {}).coverageHours < 0.9 || !d.rows.length) setTimeout(() => loadPools(), 20000);
  } catch (e) {
    $("#poolsEmpty").classList.remove("hidden");
    $("#poolsEmpty").textContent = "Gagal ambil pool: " + e.message + " — cek jendela Terminal yang jalanin server.";
  }
}
const findRow = pool => (state.data && state.data.rows.find(x => x.pool === pool)) || (state.search && state.search.rows.find(x => x.pool === pool));
async function runSearch(q) {
  if (!q) { state.search = null; renderPools(); return; }
  $("#searchInfo").classList.remove("hidden");
  $("#searchInfo").innerHTML = `<span class="muted">🔎 Cari "${esc(q)}" di semua pool v3 + v4 Elysium…</span>`;
  try {
    state.search = { q, rows: (await getJson(`/api/search?q=${encodeURIComponent(q)}`)).rows };
  } catch (e) { state.search = { q, rows: [], error: e.message }; }
  renderPools();
}
async function loadWatch() {
  try { state.watch = (await getJson("/api/watchlist")).rows || []; } catch (e) { state.watch = store.get("watch", []); }
  if (state.data) renderPools();
}
const watched = pool => state.watch.some(w => w.pool === pool);
async function toggleWatch(pool, symbol) {
  const on = !watched(pool);
  state.watch = on ? [...state.watch, { pool, symbol, addedAt: Date.now() / 1000 }] : state.watch.filter(w => w.pool !== pool);
  store.set("watch", state.watch);
  renderPools();
  const r = state.sel === pool && findRow(pool);
  if (r) renderDetail(r, true);
  try { state.watch = (await trade.call("/api/watchlist", { pool, symbol, on })).rows || state.watch; loadPools(true); }
  catch (e) { toast("Watchlist cuma disimpan di browser ini: " + e.message); }
}
const pingOf = pool => state.data && state.data.ping && (state.data.ping.rows || []).find(r => r.pool === pool);

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 7000);
}
function notify(title, body) {
  if (!state.notify || !("Notification" in window) || Notification.permission !== "granted") return;
  try { new Notification(title, { body }); } catch (e) {}
}

function rowsView() {
  const d = state.data;
  if (!d) return [];
  const qtxt = $("#q").value.trim().toLowerCase();
  const tvlMin = +$("#tvl").value || 0, quote = $("#quote").value;
  const match = r => !qtxt || `${r.symbol} ${r.name} ${r.pool} ${r.token}`.toLowerCase().includes(qtxt);
  if (state.search) return state.search.rows;
  if (state.mode === "ping") {
    return ((d.ping && d.ping.rows) || []).map(p => d.rows.find(r => r.pool === p.pool)).filter(Boolean).filter(match);
  }
  let rows = d.rows.filter(r => {
    if (state.mode === "watch") return watched(r.pool) && match(r);
    if (state.mode === "pass" && !["OPEN", "CAREFUL"].includes(r.verdict.level)) return false;
    if ($("#hideSkip").checked && r.verdict.level === "SKIP") return false;
    if (tvlMin && !((r.tvlUsd || 0) >= tvlMin)) return false;
    if (quote === "HYPE" && r.quoteSymbol !== "HYPE" && r.quoteSymbol !== "WHYPE") return false;
    if (quote === "USDC" && r.quoteSymbol !== "USDC") return false;
    const dex = ($("#dex") && $("#dex").value) || "";
    if (dex === "v4" && r.dex !== "v4") return false;
    if (dex === "v3" && r.dex === "v4") return false;
    return match(r);
  });
  const w = state.win, key = state.sort.key;
  const val = r => key === "fees" ? r.feePctDay[w] : key === "fee1h" ? r.feePctDay["1h"]
    : key === "score" ? ({ OPEN: 3, CAREFUL: 2, WAIT: 1, SKIP: 0 }[r.verdict.level] * 100 + r.verdict.score)
    : key === "symbol" ? (r.symbol || "").toLowerCase() : r[key];
  rows.sort((a, b) => {
    const va = val(a), vb = val(b);
    if (va == null) return 1;
    if (vb == null) return -1;
    const c = va < vb ? -1 : va > vb ? 1 : 0;
    return state.sort.desc ? -c : c;
  });
  return rows;
}

// ---------- render: top ----------
function renderTop() {
  const d = state.data;
  if (d) {
    $("#stEth").innerHTML = `HYPE <b>${fmtUsd(d.hypeUsd || d.ethUsd)}</b>`;
    $("#stCap").textContent = `max ${mU(d.maxPositionUsd)} / posisi`;
    const ix = d.index || {};
    const chip = $("#idxChip");
    const lag = ix.lagBlocks != null ? ` · lag ${ix.lagBlocks} blk` : "";
    chip.textContent = `⛓ ${ix.pools || 0} pool · v4 ${ix.poolsV4 || 0} · v3 ${ix.poolsV3 || 0} · ${(ix.coverageHours || 0).toFixed(1)}h${lag}`;
    chip.classList.toggle("err", !!ix.error || ix.phase === "backfill");
    chip.title = ix.error ? `Index error: ${ix.error}` : `Head ${ix.head || "?"} / chain ${ix.chainHead || "?"} · retry ${ix.retries || 0}. Klik = refresh.`;
    const line = $("#idxLine");
    line.textContent = `Index: ${ix.phase || "?"} · head ${ix.head || "?"} / ${ix.chainHead || "?"} · histori ${(ix.coverageHours || 0).toFixed(1)} jam`
      + ` · retry ${ix.retries || 0}${ix.error ? " · " + ix.error : ""}`;
    line.classList.toggle("bad", !!ix.error);
    const rpc = d.rpc || {};
    const rpcEl = $("#rpcLine");
    if (rpcEl) rpcEl.textContent = `RPC hari ini (UTC): publik ${rpc.public || 0} · Alchemy ${rpc.alchemy || 0}/${rpc.alchemyCap || 0}`
      + ` · dilewati karena cap ${rpc.skipped || 0} · error ${rpc.errors || 0}`;
  }
  const pf = state.pf;
  if (pf && !pf.error) {
    $("#stNet").innerHTML = `posisi <b>${mU(pf.valueUsd)}</b>`;
    $("#stFees").innerHTML = `fees unclaimed <b>${mU(pf.feesUsd)}</b>`;
    $("#stWallet").innerHTML = `wallet <b>${mU(pf.walletUsd)}</b>`;
    $("#posCount").textContent = pf.rows.length;
    $("#posCount").classList.toggle("hidden", !pf.rows.length);
  }
  tickAge();
}
function tickAge() {
  if (!state.loadedAt) return;
  const s = Math.round((Date.now() - state.loadedAt) / 1000);
  const el = $("#stAge");
  el.textContent = "● " + (s < 60 ? s + "s ago" : Math.round(s / 60) + "m ago");
  el.classList.toggle("stale", s > 300);
}

// ---------- render: pools ----------
const posCfg = () => (state.cfg && state.cfg.cfg && state.cfg.cfg.position) || {};
const ladderLabel = () => { const p = posCfg(); return `${p.ladderSlices || 5} slice ${p.rangeTopPct ? "−" + p.rangeTopPct + "% " : ""}→ ${p.rangeBottomPct || -60}%`; };
function renderPingInfo() {
  const el = $("#pingInfo"), p = state.data && state.data.ping;
  el.classList.toggle("hidden", state.mode !== "ping" || !!state.search);
  $("#nPing").textContent = p && p.rows ? p.rows.length : "";
  $("#nWatch").textContent = state.watch.length || "";
  if (state.mode !== "ping" || !p) return;
  el.innerHTML = `<span>📡 ${esc(p.filter || "")} · ${p.total} lolos</span><span class="spacer"></span><span class="muted">dihitung ulang tiap refresh, 0 request tambahan</span>`;
}
function renderSearchInfo() {
  const el = $("#searchInfo"), s = state.search;
  el.classList.toggle("hidden", !s);
  if (!s) return;
  el.innerHTML = `<span class="muted">🔎 <b>${esc(s.q)}</b>: ${s.rows.length} pool USDC/HYPE v3+v4${s.error ? ` · <span class="neg">${esc(s.error)}</span>` : ""} · semua pool, tanpa filter TVL</span>
    <span class="spacer"></span><button class="btn" id="clearSearch">✕ balik ke daftar</button>`;
  $("#clearSearch").onclick = () => { $("#q").value = ""; runSearch(""); };
}
function renderPools() {
  const rows = rowsView(), w = state.win, d = state.data;
  renderPingInfo();
  renderSearchInfo();
  $("#count").textContent = state.search ? "" : `${rows.length} dari ${d.rows.length} pool`;
  $("#poolsEmpty").classList.toggle("hidden", rows.length > 0);
  const ix0 = d.index || {};
  if (!rows.length) $("#poolsEmpty").textContent = state.search ? "Nggak ada pool USDC/HYPE (v3/v4) buat itu."
    : state.mode === "watch" ? "Belum ada pool di watchlist: klik ☆ di pool yang mau lu mainin."
    : state.mode === "ping" ? "Nggak ada pool yang lolos filter PING sekarang (atur di ⚙)."
    : ix0.error ? "Index error: " + ix0.error
    : !d.rows.length ? "Index swap masih baca chain… pool nambah sendiri, jangan di-refresh berkali-kali." : "Nggak ada pool yang cocok sama filter.";
  document.querySelectorAll("#pools th").forEach(th => {
    const on = state.mode !== "ping" && !state.search && th.dataset.sort === state.sort.key;
    th.classList.toggle("sorted", on);
    if (on) th.dataset.dir = state.sort.desc ? "desc" : "asc";
  });
  document.querySelectorAll("#pools th .wl").forEach(x => { x.textContent = w.toUpperCase(); });
  const size = d.sizeUsd, cards = [];
  const fresh = new Set((d.ping && d.ping.new) || []);
  $("#pools tbody").innerHTML = rows.map(r => {
    const v = r.verdict, pg = pingOf(r.pool);
    const reasons = [...v.reasons.filter(x => !x.ok), ...v.info].slice(0, 2).map(x => nice(x.text)).join(" · ") || (v.gatesFailed.length ? "" : "gates pass");
    const fee1hPct = r.tvlUsd && r.fees["1h"] != null ? r.fees["1h"] / r.tvlUsd * 100 : null;
    const icon = r.icon ? `<img src="${esc(r.icon)}" alt="" loading="lazy">` : `<span class="ph"></span>`;
    const rank = pg ? `<span class="rank ${fresh.has(r.pool) ? "new" : ""}" title="PING #${pg.rank}">📡#${pg.rank}</span>` : "";
    const star = `<button class="star ${watched(r.pool) ? "on" : ""}" data-star="${r.pool}" title="watchlist">${watched(r.pool) ? "★" : "☆"}</button>`;
    const sub = `${r.lpFeePct}%${r.dynamicFee ? " dyn" : ""} · ${age(r.ageHours)} · <span class="${cls(r.chg["24h"])}">${fmtPct(r.chg["24h"], 0, true)} 24h</span>${r.traders1h ? " · " + r.traders1h + " trader" : ""}${r.feeApr ? " · APR " + fmtPct(r.feeApr, 0) : ""}`;
    const zap = `<div class="zap"><button data-zap="${size / 2}">⚡${mU(size / 2)}</button><button data-zap="${size}">⚡${mU(size)}</button><button class="edit" data-zap="ask">✏️</button></div>`;
    cards.push(`<div class="pcard ${r.pool === state.sel ? "sel" : ""}" data-pool="${r.pool}">
      <div class="pc1">${star}${icon}<b class="psym">${esc(r.symbol)}</b>${qchip(r)}${rank}<span class="spacer"></span><span class="badge ${v.level}">${v.level}</span></div>
      <div class="pc2"><span class="col-tvl"><small>TVL</small>${mU(r.tvlUsd)}</span><span class="col-fees"><small>fee ${w}</small><b class="pos">${fmtPct(r.feePctDay[w], 1)}</b>/hr</span>
        <span class="col-fee1h"><small>1 jam</small>${fmtPct(fee1hPct, 2)}</span><span class="col-mcap"><small>mcap</small>${mU(r.mcapUsd)}</span></div>
      <div class="pc3">${sub}</div>
      ${reasons ? `<div class="pc3 muted">${esc(reasons)}</div>` : ""}${zap}</div>`);
    return `<tr data-pool="${r.pool}" class="${r.pool === state.sel ? "sel" : ""}">
      <td><span class="sym">${star}${icon}${esc(r.symbol)}${qchip(r)}${rank}</span><span class="sub">${sub} · …${r.pool.slice(-6)}</span></td>
      <td class="r col-tvl"><span class="big">${mU(r.tvlUsd)}</span><span class="sub">vol ${mU(r.vol[w])}</span></td>
      <td class="r col-fees"><span class="big pos">${mU(r.fees[w])}</span><span class="sub">${fmtPct(r.feePctDay[w], 2)}/hari</span></td>
      <td class="r col-fee1h"><span class="big pos">${fmtPct(fee1hPct, 2)}</span><span class="sub">${mU(r.fees["1h"])} · ${r.swaps["1h"] ?? "--"} swap</span></td>
      <td class="r col-mcap"><span class="big">${mU(r.mcapUsd)}</span><span class="sub">${spark(r)} <span class="${cls(r.chg["24h"])}">${fmtPct(r.chg["24h"], 0, true)}</span></span></td>
      <td class="col-score"><span class="badge ${v.level}">● ${v.level} ${v.score > 0 ? "+" : ""}${v.score}</span><span class="reasons">${esc(reasons)}</span></td>
      <td>${zap}</td></tr>`;
  }).join("");
  $("#poolCards").innerHTML = cards.join("");
}

// ---------- render: detail ----------
function renderDetail(r, keepChart) {
  state.sel = r.pool;
  document.querySelectorAll("#pools tr, .pcard").forEach(tr => tr.classList.toggle("sel", tr.dataset.pool === r.pool));
  if (!keepChart && isPhone()) document.body.classList.add("sheet");
  const v = r.verdict, size = state.data.sizeUsd;
  const label = { OPEN: "Semua gate lolos, nggak ada flag.", CAREFUL: "Gate lolos, tapi ada flag: cek dulu.",
    WAIT: "Belum: ada gate yang belum lolos (bisa berubah).", SKIP: "Skip: fee LP 0% / hook, atau fee tier honeypot." }[v.level];
  const li = [...v.reasons.map(x => `<li class="${x.ok ? "" : x.flag ? "flag" : "bad"}">${esc(nice(x.text))}</li>`),
    ...v.info.map(x => `<li class="info ${x.ok ? "" : "bad"}">${esc(x.text)}</li>`)].join("");
  const old = keepChart ? $("#detail .chart") : null;
  const pg = pingOf(r.pool);
  const tx = r.txns && r.txns.h1 ? `${r.txns.h1.buys} buy / ${r.txns.h1.sells} sell (1 jam)` : "";
  $("#detail").innerHTML = `
    <div class="sheetbar"><button class="btn" data-closesheet>✕ Tutup</button><span class="spacer"></span><b>${esc(r.symbol)}</b></div>
    <div class="dh"><button class="star ${watched(r.pool) ? "on" : ""}" data-star="${r.pool}" title="watchlist">${watched(r.pool) ? "★" : "☆"}</button>
      <span class="sym">${r.icon ? `<img src="${esc(r.icon)}" alt="">` : ""}<span class="name">${esc(r.symbol)}</span>${qchip(r)}</span>
      <span class="muted">fee ${r.lpFeePct}%${r.dynamicFee ? " (dinamis)" : ""} · spacing ${r.tickSpacing}</span>
      <span class="addr" title="copy CA token" data-copy="${r.token}">CA ${short(r.token)} ⧉</span>
      <span class="spacer"></span>
      <a href="https://dexscreener.com/elysium/${r.pool}" target="_blank" rel="noopener">dex ↗</a>
      <a href="${EXPLORER}/token/${r.token}" target="_blank" rel="noopener">explorer ↗</a></div>
    <div class="stats">
      <div><label class="muted">harga</label><b>${mU(r.priceUsd)}</b><span class="${cls(r.chg["1h"])}">${fmtPct(r.chg["1h"], 1, true)} 1j</span></div>
      <div><label class="muted">mcap</label><b>${mU(r.mcapUsd)}</b><span class="${cls(r.chg["24h"])}">${fmtPct(r.chg["24h"], 0, true)} 24j</span></div>
      <div><label class="muted">TVL</label><b>${mU(r.tvlUsd)}</b><span class="muted">umur ${age(r.ageHours)}</span></div>
      <div><label class="muted">fee / hari (1j)</label><b class="pos">${fmtPct(r.feePctDay["1h"], 2)}</b><span class="muted">APR ${fmtPct(r.feeApr, 0)} · 24j ${fmtPct(r.feePctDay["24h"], 2)}</span></div>
    </div>
    <div class="vbox ${v.level}"><span class="lv">${v.level}</span>${label} <span class="muted">skor ${v.score > 0 ? "+" : ""}${v.score}</span></div>
    <ul class="rs">${li}</ul>
    <div class="pinfo">Fee ${mU(r.fees["5m"])} / 5m · ${mU(r.fees["1h"])} / jam · ${mU(r.fees["24h"])} / 24j · ${r.swaps["1h"] ?? "--"} swap 1 jam · ${r.traders1h || 0} trader${tx ? " · " + tx : ""}${r.hookName ? " · hook " + esc(r.hookName) : (r.hooks && r.hooks !== "0x0000000000000000000000000000000000000000" ? " · hook " + short(r.hooks) : "")}${r.sizeVsTvlPct != null ? ` · size ${r.sizeVsTvlPct}% dari TVL` : ""}</div>
    ${pg ? `<div class="pinfo">📡 <b>PING #${pg.rank}</b></div>` : ""}
    <div class="zapbig">
      <button data-zap="${size / 2}">⚡ ${mU(size / 2)} · ${ladderLabel()}</button>
      <button data-zap="${size}">⚡ ${mU(size)} · ${ladderLabel()}</button>
      <button class="edit" data-zap="ask">✏️ custom</button>
      ${r.hooks && r.hooks !== "0x0000000000000000000000000000000000000000" || r.dynamicFee ? "" : `<button class="edit" data-tightopen="${r.pool}" title="Range sempit dua sisi di sekitar harga sekarang: sebagian quote ditukar ke token saat buka">🎯 tight</button>`}
    </div>
    <div class="note">Zap = ladder ${esc(r.quoteSymbol)}-only (slice sama lebar, isi 1:2:…:n, paling dalam paling gede). v4 lewat PositionManager, v3 lewat NFT multicall. Key cuma di executor.
      Disimulasikan dulu sebelum dikirim. Ubah bentuk default di ⚙. Gate & flag ini nilai awal, bukan hasil backtest: atur sesuai pengalaman lu.</div>
    <div class="chart" id="miniChart"><div class="empty">Chart dari index…</div></div>`;
  if (old) $("#detail .chart").replaceWith(old);
  else loadSeries(r.pool);
}

let chartSeq = 0;
function sparkChart(pts) {
  const priced = pts.filter(p => p.px != null);
  if (priced.length < 2) return `<div class="empty">Belum cukup history di index buat chart ini.</div>`;
  const w = 640, h = 160, pad = 8;
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || t0 + 1;
  const X = t => pad + (t - t0) / (t1 - t0 || 1) * (w - pad * 2);
  const pxs = priced.map(p => p.px);
  const lo = Math.min(...pxs), hi = Math.max(...pxs), span = hi - lo || 1e-9;
  const Y = px => h - pad - (px - lo) / span * (h - pad * 2 - 28);
  const vmax = Math.max(...pts.map(p => p.vol || 0)) || 1;
  const bars = pts.map(p => `<rect class="vbar" x="${X(p.t).toFixed(1)}" y="${(h - 4 - (p.vol || 0) / vmax * 36).toFixed(1)}" width="3" height="${((p.vol || 0) / vmax * 36).toFixed(1)}"></rect>`).join("");
  const d = priced.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)},${Y(p.px).toFixed(1)}`).join("");
  const up = priced[priced.length - 1].px >= priced[0].px;
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="harga dan volume dari index">${bars}<path d="${d}" fill="none" stroke="${up ? "#3ddc84" : "#ff5a5f"}" stroke-width="1.6"></path></svg><div class="muted" style="padding:4px 10px 8px">Harga relatif + volume quote, dari bucket 5 menit yang sudah di index. Bukan chart DexScreener, 0 request RPC.</div>`;
}
async function loadSeries(pool) {
  const seq = ++chartSeq;
  const box = $("#miniChart");
  if (!box) return;
  try {
    const d = await getJson(`/api/series?pool=${encodeURIComponent(pool)}`, null, 15000);
    if (seq !== chartSeq || !$("#miniChart")) return;
    $("#miniChart").innerHTML = sparkChart(d.points || []);
  } catch (e) {
    if (seq === chartSeq && $("#miniChart")) $("#miniChart").innerHTML = `<div class="empty">Chart gagal: ${esc(e.message)}</div>`;
  }
}

// ---------- trading ----------
const trade = {
  info: null, pin: null,
  ready() { return !!(this.info && this.info.enabled && (this.info.local || this.info.pin)); },
  why() {
    const t = this.info || {};
    if (!t.enabled) return t.error ? `Key error: ${t.error}` : "Read-only: isi ELYSIUM_PRIVATE_KEY di elysium_terminal/.env";
    if (!t.local && !t.pin) return "Trading cuma dari Mac yang jalanin server (atau set ELYSIUM_WEB_PIN buat dari HP)";
    return "";
  },
  async call(url, body) {
    if (this.info && this.info.pin && !this.info.local && !this.pin) {
      const p = prompt("PIN trading (ELYSIUM_WEB_PIN):");
      if (!p) throw new Error("dibatalin");
      this.pin = p;
    }
    try {
      return await getJson(url, { method: "POST", headers: { "Content-Type": "application/json", "X-ELY-Trade": "1" },
        body: JSON.stringify(Object.assign({}, body, { pin: this.pin })) }, 900000);
    } catch (e) {
      if (/PIN/.test(e.message)) this.pin = null;
      throw e;
    }
  },
};
const practice = () => !!(trade.info && trade.info.practice);
function paintWallet() {
  const b = $("#keyBtn"), t = trade.info || {};
  b.classList.toggle("on", trade.ready());
  b.textContent = t.enabled ? `🔑 ${short(t.wallet)}${trade.ready() ? "" : " · read-only"}` : "🔒 read-only";
  b.title = trade.ready() ? `Trading wallet ${t.wallet}${t.pin ? " (PIN aktif)" : ""}` : trade.why();
  const live = !!(t.practice === false);
  const chip = $("#practiceChip");
  chip.classList.toggle("hidden", false);
  chip.classList.toggle("live", live);
  chip.textContent = live ? "LIVE" : "PRACTICE";
  const bar = $("#practiceBar");
  if (bar) {
    bar.classList.toggle("live", live);
    bar.textContent = live
      ? "LIVE — tombol mengirim transaksi beneran ke Elysium."
      : "PRACTICE — tombol cuma simulasi (eth_call). Nggak ada yang dikirim.";
  }
}
const post = (url, body) => getJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const stepsHtml = s => (s.steps || []).map(x => `${esc(x.step || x.label || "tx")}: ${x.hash ? "✓ " + txLink(x.hash) : x.simulated ? "✓ simulasi lolos" : x.planned ? "direncanakan (butuh hasil langkah sebelumnya)" : x.needed ? "perlu approve dulu" : x.received != null ? "✓" : "–"}`).join("<br>");
const practiceNote = s => s.practice || s.dryRun ? `<br><span class="warn">PRACTICE MODE: cuma simulasi, nggak ada yang dikirim.</span>${s.note ? " " + esc(s.note) : ""}` : "";

async function quickZap(r, size) {
  if (!trade.ready()) { alert(trade.why()); return; }
  const verb = practice() ? "Simulasi" : "KIRIM BENERAN";
  if (!confirm(`${verb}: ladder ${r.symbol}/${r.quoteSymbol} ${fmtUsd(size)}\n${practice() ? "Practice: eth_call, tidak ada yang dikirim." : "Ini transaksi mainnet."}`)) return;
  toast(practice() ? "Simulasi ladder…" : "Kirim ladder…");
  try {
    const s = await trade.call("/api/trade/open", { pool: r.pool, symbol: r.symbol, usd: size, override: false });
    toast(s.ok ? (s.dryRun ? `✓ Simulasi ${r.symbol} lolos` : `✓ Ladder ${r.symbol} kebuka`) : `✗ ${s.error || "gagal"}`);
    if (!s.ok) alert(s.error || "gagal");
  } catch (e) { toast("✗ " + e.message); alert(e.message); }
}
function openZap(r, size) {
  $("#modalCard").classList.remove("wide");
  const d = state.data, maxUsd = d.maxPositionUsd, pos = posCfg();
  const exec = state.cfg && state.cfg.executor;
  $("#modalCard").innerHTML = `
    <h3>⚡ Zap ${esc(r.symbol)} ${qchip(r)} <span class="muted">fee ${r.lpFeePct}% · ladder ${esc(r.quoteSymbol)}-only</span></h3>
    <div class="acts">
      <label>$ <input id="zSize" value="${+size.toFixed(2)}"></label>
      <label>slices <input id="zSlices" value="${pos.ladderSlices || 5}" style="width:50px"></label>
      <label>mulai −% <input id="zTop" value="${pos.rangeTopPct || 0}" style="width:50px"></label>
      <label>bottom −% <input id="zBottom" value="${Math.abs(pos.rangeBottomPct || 60)}" style="width:55px"></label>
      <span class="muted" id="zAlt"></span>
    </div>
    <div id="zBody" style="margin-top:10px"></div>
    <div id="zLadder" class="muted">Plan ladder…</div>
    <div class="acts">
      <button class="btn go" id="zOpen" ${trade.ready() ? "" : "disabled"} title="${esc(trade.why())}">${trade.ready() ? (practice() ? "🧪 Simulasi ladder" : "⚡ Buka ladder") : "🔒 " + esc(trade.why())}</button>
      <a class="btn" href="https://dexscreener.com/elysium/${r.pool}" target="_blank" rel="noopener">DexScreener ↗</a>
      <button class="btn" id="zSkip">Log: skip</button>
      <span class="spacer"></span><button class="btn" id="zClose">Tutup</button>
    </div><div class="res" id="zMsg">${exec ? `⚠ Executor: ${esc(exec)}` : ""}</div>`;
  $("#modal").classList.remove("hidden");
  let cur = null, planTimer = null, planSeq = 0;
  const draw = () => {
    const sz = parseFloat($("#zSize").value) || 0, bottom = parseFloat($("#zBottom").value) || 60;
    const top = parseFloat($("#zTop").value) || 0, slices = parseInt($("#zSlices").value, 10) || 5;
    $("#zAlt").textContent = r.quoteSymbol === "USDC" ? "USDC" : (ethUsd() ? `≈ ${fmtEth(sz / ethUsd())} HYPE` : (r.quoteSymbol || ""));
    const sizePct = r.tvlUsd ? sz / r.tvlUsd * 100 : null;
    const gateFails = r.verdict.gatesFailed.map(nice);
    const warn = [];
    if (sz > maxUsd + 1e-9) warn.push(`⛔ ${fmtUsd(sz)} di atas cap loss budget ${fmtUsd(maxUsd)}`);
    if (sizePct != null && sizePct > 25) warn.push(`⚠ Size ${sizePct.toFixed(0)}% dari TVL pool: lu bakal jadi bagian besar likuiditasnya`);
    if (bottom <= top) warn.push("⛔ bottom harus lebih dalam dari mulai");
    const flags = r.verdict.flags.map(f => "⚠ " + nice(f));
    $("#zBody").innerHTML = `<div class="vbox ${gateFails.length ? "WAIT" : flags.length ? "CAREFUL" : "OPEN"}" style="margin:0 0 10px">
        <span class="lv">${gateFails.length ? "GATE ✗" : flags.length ? "CAREFUL" : "OPEN"}</span>
        ${gateFails.length ? esc(gateFails.join(" · ")) : `Gate lolos · ${fmtPct(sizePct, 2)} dari TVL`}</div>
      ${[...warn, ...flags].map(x => `<div class="warn">${esc(x)}</div>`).join("")}`;
    cur = { sz, bottom, top, slices, gateFails, flags, over: sz > maxUsd + 1e-9 };
    clearTimeout(planTimer);
    planTimer = setTimeout(loadPlan, 350);
  };
  async function loadPlan() {
    const seq = ++planSeq;
    $("#zLadder").innerHTML = '<span class="muted">Plan ladder (baca harga pool dari chain)…</span>';
    const p = await post("/api/ladder/plan", { pool: r.pool, usd: cur.sz, slices: cur.slices, top: cur.top, bottom: cur.bottom }).catch(e => ({ ok: false, error: e.message }));
    if (seq !== planSeq) return;
    if (!p.ok) { $("#zLadder").innerHTML = `<span class="neg">${esc(p.error || "plan gagal")}</span>`; cur.plan = null; return; }
    const pl = p.plan, mx = Math.max(...pl.slices.map(s => s.amount));
    const unit = pl.quoteSymbol || r.quoteSymbol || "quote";
    cur.plan = pl;
    $("#zLadder").innerHTML = `<div class="muted">${pl.slices.length} slice · harga → −${pl.realEnd.toFixed(1)}% · total ${pl.total.toPrecision(6)} ${unit} · isi 1:2:…:${pl.slices.length}</div>
      <table class="ladder"><tr><th>#</th><th>range (di bawah harga)</th><th class="r">${unit}</th><th></th></tr>
      ${pl.slices.map((s, i) => `<tr><td>${i + 1}</td><td>−${s.nearPct.toFixed(1)}% → −${s.farPct.toFixed(1)}%</td>
        <td class="r">${s.amount.toPrecision(5)}</td><td><span class="lbar" style="width:${Math.round(s.amount / mx * 120)}px"></span></td></tr>`).join("")}</table>
      <div class="muted">Range nempel ke grid tick pool (spacing ${r.tickSpacing}), jadi angkanya sedikit beda dari yang diminta.</div>`;
  }
  draw();
  ["#zSize", "#zBottom", "#zTop", "#zSlices"].forEach(s => { $(s).oninput = draw; });
  $("#zClose").onclick = closeModal;
  $("#zSkip").onclick = async () => {
    const reason = prompt("Alasan skip?", "");
    if (reason === null) return;
    await post("/api/decide", { pool: r.pool, symbol: r.symbol, decision: "skip", decisionReason: reason, sizeUsd: cur.sz,
      level: r.verdict.level, gatesFailed: r.verdict.gatesFailed, flags: r.verdict.flags });
    $("#zMsg").textContent = "Logged: skip → data/decisions.jsonl";
  };
  $("#zOpen").onclick = async () => {
    if (!trade.ready() || !cur.plan) return;
    const over = [...cur.gateFails, ...(cur.over ? ["di atas cap loss budget"] : [])];
    if (over.length && !confirm(`Entry check bilang jangan:\n${over.join("\n")}\n\nTetap buka? (dicatat sebagai override)`)) return;
    if (!practice() && !confirm(`Buka ladder ${r.symbol}/${r.quoteSymbol}?\n${fmtUsd(cur.sz)} · ${cur.plan.slices.length} slice · harga → −${cur.plan.realEnd.toFixed(1)}%\nWallet ${short(trade.info.wallet)}. Ini kirim transaksi beneran.`)) return;
    const btn = $("#zOpen");
    btn.disabled = true;
    try {
      $("#zMsg").textContent = practice() ? "Simulasi di chain…" : "Approve (kalau perlu) + simulasi + kirim… (±10–60 detik)";
      const s = await trade.call("/api/trade/open", { pool: r.pool, symbol: r.symbol, usd: cur.sz, slices: cur.slices, top: cur.top, bottom: cur.bottom, override: over.length > 0 });
      $("#zMsg").innerHTML = s.ok ? `${stepsHtml(s)}<br>${s.dryRun ? "✓ Simulasi lolos." : `✓ Ladder kebuka${(s.positions || []).length ? ": posisi #" + s.positions.join(", #") : ""}.`}${practiceNote(s)}`
        : `<span class="neg">✗ ${esc(s.error || "gagal")}</span>`;
      if (s.ok && !s.dryRun) setTimeout(() => loadPortfolio(true), 4000);
    } catch (e) { $("#zMsg").innerHTML = `<span class="neg">✗ ${esc(e.message || e)}</span>`; }
    finally { btn.disabled = !trade.ready(); }
  };
}

// ---------- portfolio actions ----------
// One row = one thing you opened (a ladder or a single range). Buttons, results and auto
// rules are keyed by that row's id, so two positions in the same pool never share state.
state.busy = {};      // id -> text while an action runs
state.note = {};      // id -> { ok, html } result of the last action, kept across re-renders
function guard() {
  if (!trade.ready()) { alert(trade.why()); return false; }
  if (state.wallet.toLowerCase() !== (trade.info.wallet || "").toLowerCase()) { alert(`Portfolio lagi nampilin wallet lain. Trading wallet: ${trade.info.wallet}`); return false; }
  return true;
}
const rowOf = id => state.pf && state.pf.rows && state.pf.rows.find(x => x.id === id);
const posName = p => `${p.symbol}${p.ofPool > 1 ? " #" + p.nth : ""}`;
const idsOf = p => p.positions.map(x => x.tokenId);
const autoOf = id => (state.pf && state.pf.auto && state.pf.auto.rules && state.pf.auto.rules[id]) || null;
const autoGlobal = () => (state.pf && state.pf.auto && state.pf.auto.global) || (state.tight && state.tight.auto && state.tight.auto.global) || {};

async function act(p, url, body, confirmMsg, okText) {
  if (!guard() || state.busy[p.id]) return;
  if (!practice() && !confirm(confirmMsg)) return;
  state.busy[p.id] = practice() ? "simulasi…" : "jalan… (bisa 1–3 menit, jangan ditutup)";
  delete state.note[p.id];
  renderPortfolio();
  let target = p.id;
  try {
    const s = await trade.call(url, Object.assign({ id: p.id, pool: p.pool, symbol: p.symbol }, body));
    if (s.ok) {
      target = s.newId || s.id || p.id;
      const steps = (s.steps || []).filter(x => x.hash).map(x => `${esc(x.step || "tx")} ${txLink(x.hash)}`).join(" · ");
      state.note[target] = { ok: true, html: s.noop ? esc(okText(s)) : s.dryRun
        ? `<span class="warn">🧪 simulasi lolos, tidak ada yang dikirim.</span> ${esc(okText(s))}`
        : `✓ ${esc(okText(s))}${steps ? "<br>" + steps : s.hash ? " " + txLink(s.hash) : ""}` };
      toast(`✓ ${posName(p)}: ${s.noop ? "tidak ada yang diubah" : s.dryRun ? "simulasi lolos" : okText(s)}`);
    } else {
      state.note[p.id] = { ok: false, html: `✗ ${esc(s.stage || s.step || "")} ${esc(s.error || "gagal")}${s.sent ? "<br>Sebagian transaksi sudah terkirim: cek wallet, sisa token bisa dijual lewat Cleanup." : ""}` };
      toast(`✗ ${posName(p)}: ${s.error || "gagal"}`);
    }
  } catch (e) {
    state.note[p.id] = { ok: false, html: `✗ ${esc(e.message || e)}` };
  } finally {
    delete state.busy[p.id];
    await loadPortfolio(true);
    setTimeout(() => loadPortfolio(true), 6000);     // a slow RPC node may still show the old state
  }
}
const closePos = p => act(p, "/api/trade/close", { ids: idsOf(p) },
  `Close ${posName(p)} (${p.posCount} NFT)?\nTarik 100% likuiditas + klaim fee, satu transaksi.\nToken yang keluar TIDAK dijual (pakai Close+swap kalau mau).`, s => s.dryRun ? "Close." : "closed");
const claimPos = p => act(p, "/api/trade/claim", { ids: idsOf(p) },
  `Claim fee ${posName(p)} (${mU(p.feesUsd)})?\nLikuiditas tetap di tempat, cuma fee yang diambil.`, () => "fee diklaim");
const shapeWord = p => p.posCount > 1 ? `${p.posCount} slice, lebar tiap slice sama seperti sekarang` : `1 range, lebar ${p.widthPct != null ? p.widthPct.toFixed(2) + "%" : "sama"}`;
const swapPos = p => act(p, "/api/trade/closeswap", {},
  `CLOSE + SWAP ${posName(p)}\nClose, lalu jual token yang keluar ke ${p.quoteSymbol} (batal kalau > max loss di bawah harga pool).\n\nLanjut?`,
  s => s.dryRun ? "Close lolos; jual token baru jalan kalau live." : "closed + swapped");
const reenterPos = p => act(p, "/api/trade/reenter", {},
  `RE-ENTRY ${posName(p)}\n1) Close (fee ikut)\n2) Jual token yang keluar ke ${p.quoteSymbol} (batal kalau > max loss di bawah harga pool)\n3) Buka lagi dengan bentuk yang sama: ${shapeWord(p)}, ${p.quoteSymbol} saja, tepat di bawah harga sekarang\n\nTidak ada token yang dibeli. Lanjut?`,
  s => s.noop ? "Range sudah tepat di bawah harga dengan bentuk yang sama: tidak ada yang diubah." : s.dryRun ? `Close lolos; range baru ${shapeWord(p)}, ${p.quoteSymbol} saja, baru jalan kalau live.` : "re-entry selesai, range sama di harga sekarang");
async function cleanupWallet(btn) {
  if (!guard()) return;
  if (!practice() && !confirm("CLEANUP wallet\nJual sisa token dari posisi lu (yang nilainya ≥ $1) ke USDC/HYPE lewat pool terdalamnya, batal per token kalau > max loss di bawah harga pool.\nUSDC/HYPE/WHYPE nggak dijual. Lanjut?")) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "cleanup…";
  try {
    const s = await trade.call("/api/trade/cleanup", {});
    alert(s.ok ? (s.dryRun ? "PRACTICE: simulasi.\n\n" : "Cleanup selesai.\n\n") + ((s.tokens || []).map(t => `${t.symbol || t.token.slice(0, 8)}: ${t.status}`).join("\n") || "Nggak ada sisa token.") : "Gagal: " + (s.error || "?"));
    loadPortfolio(true);
  } catch (e) { alert("Gagal: " + (e.message || e)); }
  finally { btn.disabled = false; btn.textContent = old; }
}

// ---------- auto rules (per position) ----------
const ACT_WORD = { hold: "diam", reentry: "re-entry", rebalance: "re-entry", close: "close + swap" };
function autoCell(p) {
  const a = autoOf(p.id), g = autoGlobal();
  const chips = [];
  if (a) {
    if (a.tp) chips.push(`TP +${a.tp}%${p.pnlPct != null && a.status === "armed" ? ` <span class="muted">(kurang ${(a.tp - p.pnlPct).toFixed(2)}%)</span>` : ""}`);
    if (a.sl) chips.push(`SL ${a.sl}%`);
    if (a.above && a.above !== "hold") chips.push(`↑ ${ACT_WORD[a.above]}`);
    if (a.below && a.below !== "hold") chips.push(`↓ ${ACT_WORD[a.below]}`);
  }
  let st = "";
  if (a) {
    const m = esc(a.msg || "");
    if (a.status === "armed") st = g.paused ? `<span class="tp warn">⏸ auto dijeda (semua)</span>` : `<span class="tp on">🎯 nyala${m ? " · " + m : ""}</span>`;
    else if (a.status === "running") st = `<span class="tp warn">⏳ lagi jalan: ${m}</span>`;
    else if (a.status === "done") st = `<span class="tp pos">✓ ${m}</span>`;
    else if (a.status === "failed") st = `<span class="tp neg">✗ ${m}</span> <button class="btn mini" data-act="resume" data-id="${p.id}">coba lagi</button>`;
    else if (a.status === "attention") st = `<span class="tp neg">⚠ ${m}</span> <button class="btn mini" data-act="resume" data-id="${p.id}">sudah dicek, lanjutkan</button>`;
  }
  return `<div class="tpbox"><div class="achips"><button class="btn mini auto ${a && a.status === "armed" ? "on" : ""}" data-act="auto" data-id="${p.id}" title="TP, SL, dan apa yang dilakukan kalau harga keluar range. Dicek server tiap ${state.pf.autoCheckSec || 60} detik selama Elysium Terminal jalan.">⚙ Auto${chips.length ? "" : ": off"}</button>${chips.map(c => `<span class="achip">${c}</span>`).join("")}</div>${st}</div>`;
}
function openAuto(p) {
  if (!guard()) return;
  const a = autoOf(p.id) || {}, g = autoGlobal();
  const norm = v => v === "rebalance" ? "reentry" : (v || "hold");
  const sel = (id, cur) => `<select id="${id}">${["hold", "reentry", "close"].map(v =>
    `<option value="${v}" ${v === norm(cur) ? "selected" : ""}>${v === "hold" ? "diam (nggak ngapa-ngapain)" : v === "reentry" ? "re-entry: tutup + buka lagi range yang sama di harga itu" : "close + swap ke " + esc(p.quoteSymbol)}</option>`).join("")}</select>`;
  $("#modalCard").classList.remove("wide");
  $("#modalCard").innerHTML = `
    <h3>⚙ Auto · ${esc(posName(p))} ${qchip(p)} <span class="muted">#${p.positions[0].tokenId}${p.posCount > 1 ? " +" + (p.posCount - 1) : ""}</span></h3>
    <p class="muted">Rule ini cuma buat posisi ini. Posisi lain di pool yang sama punya rule sendiri. Dicek server tiap ${state.pf.autoCheckSec || 60} detik, selama Elysium Terminal jalan di Mac (tab boleh ditutup).</p>
    <div class="aform">
      <label>Take profit, PnL ≥ <span><input id="aTp" value="${a.tp || ""}" placeholder="off" inputmode="decimal"> %</span></label>
      <label>Stop loss, PnL ≤ −<span><input id="aSl" value="${a.sl ? Math.abs(a.sl) : ""}" placeholder="off" inputmode="decimal"> %</span></label>
      <label>Harga NAIK lewat atas range <small>posisi jadi 100% ${esc(p.quoteSymbol)}, fee berhenti</small>${sel("aAbove", a.above)}</label>
      <label>Harga TURUN lewat bawah range <small>posisi jadi 100% ${esc(p.symbol)}</small>${sel("aBelow", a.below)}</label>
    </div>
    <p class="muted">Re-entry = close, jual token ke ${esc(p.quoteSymbol)}, lalu buka lagi bentuk yang sama (${esc(shapeWord(p))}) pakai ${esc(p.quoteSymbol)} saja, tepat di bawah harga saat itu. Tidak ada token yang dibeli.</p>
    <p class="muted">TP/SL = close + swap. Aksi range nunggu harga di luar <b>${g.holdSec}s</b>, jeda antar aksi <b>${g.cooldownSec}s</b>, maks <b>${g.maxPerDay}×</b>/hari, cuma kalau nilai posisi ≤ <b>${fmtUsd(g.maxNotionalUsd)}</b>. Ubah batas ini di tab 🎯 Tight.</p>
    <p class="warn">${practice() ? "PRACTICE MODE: auto cuma mensimulasikan, tidak ada yang dikirim." : "LIVE: kalau syaratnya kena, server mengirim transaksi pakai key di .env tanpa nanya lagi."} Re-entry = close, jual, buka lagi, berurutan. Kalau berhenti di tengah, rule dijeda dan nunggu lu.</p>
    <div class="acts"><button class="btn go" id="aSave">Simpan</button><button class="btn" id="aOff">Matikan semua</button><span class="spacer"></span><button class="btn" id="aClose">Tutup</button></div>
    <div class="res" id="aMsg"></div>`;
  $("#modal").classList.remove("hidden");
  $("#aClose").onclick = closeModal;
  const send = async body => {
    try {
      const r = await trade.call("/api/auto/rule", Object.assign({ id: p.id }, body));
      if (!r.ok) throw new Error(r.error);
      state.pf.auto = r.auto;
      renderPortfolio();
      closeModal();
      toast(r.auto.rules[p.id] ? `🎯 Auto ${posName(p)} tersimpan` : `Auto ${posName(p)} mati`);
    } catch (e) { $("#aMsg").innerHTML = `<span class="neg">✗ ${esc(e.message)}</span>`; }
  };
  $("#aOff").onclick = () => send({});
  $("#aSave").onclick = () => {
    const body = { tp: $("#aTp").value.trim(), sl: $("#aSl").value.trim(), above: $("#aAbove").value, below: $("#aBelow").value };
    const does = [body.tp && `TP +${body.tp}%`, body.sl && `SL −${Math.abs(body.sl)}%`, body.above !== "hold" && `naik lewat atas → ${ACT_WORD[body.above]}`, body.below !== "hold" && `turun lewat bawah → ${ACT_WORD[body.below]}`].filter(Boolean);
    if (does.length && !practice() && !confirm(`Nyalain AUTO buat ${posName(p)}?\n\n${does.join("\n")}\n\nServer bakal mengirim transaksi sendiri saat syaratnya kena.`)) return;
    send(body);
  };
}
async function resumeAuto(id) {
  if (!guard()) return;
  const a = autoOf(id);
  if (!a || !confirm(`Lanjutkan auto buat posisi ini?\n\nTerakhir: ${a.msg || a.status}\n\nPastikan wallet dan posisinya sudah lu cek.`)) return;
  try { state.pf.auto = (await trade.call("/api/auto/resume", { id })).auto; renderPortfolio(); } catch (e) { alert(e.message); }
}
async function setAutoGlobal(patch) {
  if (!guard()) return;
  try {
    const r = await trade.call("/api/auto/global", patch);
    if (state.pf) { state.pf.auto = r.auto; renderPortfolio(); }
    if (state.tight) { state.tight.auto = r.auto; if (state.view === "tight") renderTight(); }
    toast(r.auto.global.paused ? "⏸ Semua auto dijeda" : "Setting auto tersimpan");
  } catch (e) { alert(e.message); }
}

// ---------- liquidity depth (per position) ----------
state.depthOpen = store.get("depthOpen", {});   // id -> true while the chart is shown
state.depth = {};                               // id -> last read (or { error })
async function loadDepth(id) {
  try { state.depth[id] = await getJson(`/api/depth?wallet=${encodeURIComponent(state.wallet)}&id=${encodeURIComponent(id)}`, null, 30000); }
  catch (e) { state.depth[id] = Object.assign({}, state.depth[id], { error: e.message, t: Date.now() / 1000 }); }
  paintDepth(id);
}
function toggleDepth(id) {
  if (state.depthOpen[id]) delete state.depthOpen[id]; else state.depthOpen[id] = true;
  store.set("depthOpen", state.depthOpen);
  renderPortfolio();
}
function paintDepth(id) {
  const p = rowOf(id);
  document.querySelectorAll(`[data-depth="${id}"]`).forEach(el => { el.innerHTML = p ? depthChart(p, state.depth[id]) : ""; });
}
function depthChart(p, d) {
  if (!d) return `<div class="muted">Baca liquidity depth dari pool…</div>`;
  if (!d.segments) return `<div class="neg">Depth gagal: ${esc(d.error || "?")}</div>`;
  const N = isPhone() ? 40 : 72, W = 720, H = 150, top = 26, base = H - 4;
  const span = d.hi - d.lo, step = span / N;
  // tick-weighted average liquidity per bar
  const bars = [];
  for (let i = 0; i < N; i++) {
    const a = d.lo + i * step, b = a + step;
    let sum = 0;
    for (const s of d.segments) { const o = Math.min(b, s.b) - Math.max(a, s.a); if (o > 0) sum += s.L * o; }
    bars.push({ a, b, L: sum / step });
  }
  const max = Math.max(...bars.map(x => x.L)) || 1;
  // price rises left to right. When the quote is token0 a higher tick is a lower token price, so mirror.
  const X = t => (p.flip ? (d.hi - t) : (t - d.lo)) / span * W;
  const px = t => p.tokenUsd == null ? null : p.tokenUsd * Math.pow(1.0001, p.flip ? -(t - d.tick) : (t - d.tick));
  const inMine = (a, b) => d.ranges.some(r => Math.min(b, r[1]) - Math.max(a, r[0]) > 0);
  const bw = W / N;
  const rects = bars.map(x => {
    const h = Math.max(x.L > 0 ? 1.5 : 0.5, x.L / max * (base - top));
    const x0 = Math.min(X(x.a), X(x.b));
    return `<rect class="dbar ${inMine(x.a, x.b) ? "mine" : ""}" x="${(x0 + 0.5).toFixed(1)}" y="${(base - h).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${h.toFixed(1)}"><title>${fmtUsd(px(p.flip ? x.b : x.a))} → ${fmtUsd(px(p.flip ? x.a : x.b))}</title></rect>`;
  }).join("");
  const band = d.ranges.map(r => { const x0 = Math.min(X(r[0]), X(r[1])), x1 = Math.max(X(r[0]), X(r[1])); return `<rect class="dmine" x="${x0.toFixed(1)}" y="${top}" width="${Math.max(1, x1 - x0).toFixed(1)}" height="${base - top}"></rect>`; }).join("");
  const xp = Math.max(1, Math.min(W - 1, X(d.tick + 0.5)));
  const label = `harga pool ${fmtUsd(p.tokenUsd)}`;
  const lw = label.length * 6.6 + 14, lx = Math.max(2, Math.min(W - lw - 2, xp - lw / 2));
  const share = d.sharePct == null ? "" : d.mine > 0
    ? ` · likuiditas lu di harga sekarang <b>${d.sharePct < 0.01 ? "<0.01" : d.sharePct.toFixed(2)}%</b> dari yang aktif`
    : ` · posisi lu lagi tidak aktif di harga sekarang`;
  const loPx = px(p.flip ? d.hi : d.lo), hiPx = px(p.flip ? d.lo : d.hi);
  return `<svg class="depth" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="liquidity depth">
      ${band}${rects}
      <line class="dpx" x1="${xp.toFixed(1)}" x2="${xp.toFixed(1)}" y1="${top - 4}" y2="${base}"></line>
    </svg>
    <div class="dlabel" style="left:${(lx / W * 100).toFixed(2)}%">${label}</div>
    <div class="daxis"><span>${fmtUsd(loPx)}</span><span class="muted"><i class="dkey mine"></i> range lu <i class="dkey"></i> likuiditas pool${share}${d.clipped ? " · dipotong di sekitar harga" : ""}${d.error ? ` · <span class="warn">refresh gagal</span>` : ""}</span><span>${fmtUsd(hiPx)}</span></div>`;
}
function depthBox(p) {
  if (!state.depthOpen[p.id]) return "";
  const d = state.depth[p.id];
  if (!d || (Date.now() / 1000 - (d.t || 0) > 25 && !d.loading)) {
    state.depth[p.id] = Object.assign({}, d, { loading: true, t: d && d.t });
    setTimeout(() => loadDepth(p.id), 0);
  }
  return `<div class="depthbox"><div class="dtitle">LIQUIDITY DEPTH <span class="muted">${esc(p.symbol)} / ${esc(p.quoteSymbol)} · dibaca dari tick pool</span></div><div class="dwrap" data-depth="${p.id}">${depthChart(p, d && d.segments ? d : (d && d.error ? d : null))}</div></div>`;
}

// ---------- portfolio ----------
let pfSeq = 0;
async function loadPortfolio(force) {
  const w = $("#wallet").value.trim();
  if (!w) return;
  const seq = ++pfSeq;
  state.wallet = w;
  store.set("wallet", w);
  if (!state.pf) { $("#pfEmpty").classList.remove("hidden"); $("#pfEmpty").textContent = "Baca posisi dari chain… (pertama kali scan histori NFT, bisa ±10–30 detik)"; }
  let pf;
  try { pf = await getJson(`/api/portfolio?wallet=${encodeURIComponent(w)}${force ? "&force=1" : ""}`, null, 300000); }
  catch (e) {
    // keep showing the last good read; a failed refresh should not blank the table
    if (seq !== pfSeq) return;
    if (state.pf && !state.pf.error) { state.pf.stale = e.message; renderPortfolio(); return; }
    pf = { error: e.message };
  }
  if (seq !== pfSeq) return;
  state.pf = pf;
  renderPortfolio();
  renderTop();
}
function stateWord(p) {
  const single = p.posCount === 1;
  if (p.state === "in") return single && p.edgePct != null ? `in range · ${p.edgePct.toFixed(2)}% ke tepi terdekat` : "in range, dapet fee";
  if (p.state === "above") return single ? `harga di ATAS range: 100% ${p.quoteSymbol}, fee berhenti` : `di atas ladder (masih ${p.quoteSymbol}), nunggu harga turun`;
  if (p.state === "below") return single ? `harga di BAWAH range: 100% ${p.symbol}` : "lewat bawah ladder (pegang token)";
  return "di celah antar slice";
}
function rangeStrip(p) {
  const rp = p.rangePct;
  const pos = rp == null ? null : Math.max(-3, Math.min(103, rp));
  const mk = p.state === "in" ? "in" : p.state === "below" ? "below" : "out";
  return `<div class="strip"><span>${mU(p.capLo)}</span><div class="rbar"><div class="fill" style="left:0;right:0"></div>${p.posCount > 1 ? `<div class="third"></div>` : ""}${pos != null ? `<div class="mk ${mk}" style="left:${pos}%"></div>` : ""}</div><span>${mU(p.capHi)}</span>
    <b class="${p.state === "in" ? "pos" : p.state === "below" ? "neg" : "warn"}">${rp == null ? "--" : Math.round(Math.max(-999, Math.min(999, rp))) + "%"}</b></div>
    <span class="sub">${esc(stateWord(p))} · mcap sekarang ${mU(p.mcapUsd)}</span>`;
}
function kindChip(p) {
  if (p.kind === "ladder" || p.posCount > 1) return `<span class="kchip">ladder ${p.posCount}</span>`;
  const w = p.widthPct != null ? " " + p.widthPct.toFixed(p.widthPct < 10 ? 1 : 0) + "%" : "";
  return `<span class="kchip tight">${p.kind === "tight" ? "tight" : "range"}${w}${p.rebalances ? " · ↻" + p.rebalances : ""}</span>`;
}
function renderPortfolio() {
  const pf = state.pf;
  if (!pf) return;
  if (pf.error) { $("#pfEmpty").classList.remove("hidden"); $("#pfEmpty").textContent = "Gagal baca: " + pf.error + " (dicoba lagi otomatis)"; $("#positions tbody").innerHTML = ""; $("#pfCards").innerHTML = ""; return; }
  const pnlPct = pf.depositUsd ? pf.pnlUsd / pf.depositUsd * 100 : null, b = pf.balances || {};
  const g = autoGlobal();
  const armed = Object.values((pf.auto && pf.auto.rules) || {}).filter(r => r.status === "armed").length;
  const warn = pf.stale ? `<span class="warn">⚠ refresh gagal (${esc(pf.stale)}), ini data terakhir</span>`
    : pf.partial ? `<span class="warn" title="${esc((pf.warnings || []).join("\n"))}">⚠ sebagian posisi gagal dibaca, auto nunggu baca berikutnya</span>` : "";
  $("#pfSummary").innerHTML = `<span>nilai <b>${mU(pf.valueUsd)}</b>${pf.valueHype != null ? ` <span class="muted">${fmtEth(pf.valueHype)}</span>` : ""}</span>
    <span>fees unclaimed <b class="pos">${mU(pf.feesUsd)}</b></span>
    <span>PnL <b class="big ${cls(pf.pnlUsd)}">${pf.pnlUsd > 0 ? "+" : ""}${mU(pf.pnlUsd)}</b> <span class="${cls(pnlPct)}">${fmtPct(pnlPct, 2, true)}</span></span>
    <span>deposit <b>${mU(pf.depositUsd)}</b></span>
    <span>wallet <b>${b.usdc != null ? b.usdc.toFixed(2) : "--"} USDC · ${b.hype != null ? fmtEth(b.hype) : "--"} HYPE${b.whype ? " · " + Number(b.whype).toFixed(4) + " WHYPE" : ""}</b></span>
    <span><button class="btn mini ${g.paused ? "danger" : ""}" id="autoPause" title="Jeda / lanjutkan semua rule auto">${g.paused ? "▶ lanjutkan auto" : `⏸ jeda auto (${armed} nyala)`}</button></span>
    <span class="muted">dicek ${new Date(pf.t * 1000).toLocaleTimeString()}</span>${warn}`;
  const ap = $("#autoPause");
  if (ap) ap.onclick = () => setAutoGlobal({ paused: !g.paused });
  $("#pfEmpty").classList.toggle("hidden", pf.rows.length > 0);
  if (!pf.rows.length) $("#pfEmpty").textContent = "Nggak ada posisi v3/v4 terbuka di wallet ini.";
  const cards = [];
  $("#positions tbody").innerHTML = pf.rows.map(p => {
    const ev = p.events || [];
    const exit = ev.find(e => e.kind === "close"), alerts = ev.filter(e => e.kind === "alert").map(e => e.msg);
    const status = exit ? `<span class="badge WAIT">EXIT</span> <span class="neg">${esc(exit.msg)}</span>`
      : alerts.length ? `<span class="badge CAREFUL">ALERT</span> <span class="warn">${esc(alerts.join(" · "))}</span>`
      : p.state === "in" || p.state === "gap" ? `<span class="badge OPEN">OK</span> <span class="muted">${p.state === "in" ? "in range, dapet fee" : "di celah slice"}</span>`
      : p.posCount > 1 && p.state === "above" ? `<span class="badge OPEN">OK</span> <span class="muted">nunggu harga turun ke ladder</span>`
      : `<span class="badge CAREFUL">OOR</span> <span class="warn">${p.state === "above" ? "di atas range, fee berhenti" : "di bawah range, pegang token"}</span>`;
    const pg = pingOf(p.pool), busy = state.busy[p.id], note = state.note[p.id];
    const head = `${esc(p.symbol)}${p.ofPool > 1 ? `<span class="nth">#${p.nth}</span>` : ""}${qchip(p)}${kindChip(p)}${pg ? `<span class="rank">📡#${pg.rank}</span>` : ""}`;
    const sub = `NFT #${p.positions[0].tokenId}${p.posCount > 1 ? "–" + p.positions[p.posCount - 1].tokenId + " · " + p.inRangeCount + "/" + p.posCount + " in range" : ""} · ${age(p.heldHours)} · fee ${p.lpFeePct}%`;
    const dep = `${p.depositEst ? "≈" : ""}${mU(p.depositUsd)}`;
    const pnl = `<span class="big ${cls(p.pnlUsd)}">${p.pnlUsd > 0 ? "+" : ""}${mU(p.pnlUsd)}</span><span class="sub"><span class="${cls(p.pnlPct)}">${fmtPct(p.pnlPct, 2, true)}</span> · ${mAlt(p.pnlUsd)}</span>
      <span class="sub">nilai ${mU(p.valueUsd)} · fee <span class="pos">${mU(p.feesUsd)}</span>${p.claimedUsd ? ` · claimed ${mU(p.claimedUsd)}` : ""} · deposit <span ${p.depositEst ? `title="Posisi ini tidak dibuka dari terminal: deposit dihitung dari likuiditas seolah satu sisi, jadi bisa kegedean."` : ""}>${dep}</span></span>`;
    const dis = busy ? "disabled" : "";
    const btns = `<button class="btn ${exit ? "danger" : ""}" data-act="close" data-id="${p.id}" ${dis}>${exit ? "Exit → close" : "Close"}</button>
      <button class="btn" data-act="claim" data-id="${p.id}" ${p.feesUsd > 0 && !busy ? "" : "disabled"}>Claim</button>
      <button class="btn" data-act="swap" data-id="${p.id}" ${dis} title="Close + jual token ke ${esc(p.quoteSymbol)}">Close+swap</button>
      <button class="btn ${state.depthOpen[p.id] ? "on" : ""}" data-act="depth" data-id="${p.id}" title="Liquidity depth pool di sekitar posisi ini">📊 Depth</button>
      <button class="btn go" data-act="reenter" data-id="${p.id}" ${dis} title="Close + jual token ke ${esc(p.quoteSymbol)} + buka lagi range yang sama (${esc(p.quoteSymbol)} saja) di harga sekarang">Re-entry</button>`;
    const res = busy ? `<div class="res rowres muted">⏳ ${esc(busy)}</div>` : note ? `<div class="res rowres ${note.ok ? "" : "neg"}">${note.html}</div>` : "";
    cards.push(`<div class="lcard"><div class="lc1"><div><b class="psym">${head}</b><div class="sub">${sub}</div></div>
      <div class="r"><div class="pnlbig ${cls(p.pnlUsd)}">${p.pnlUsd > 0 ? "+" : ""}${mU(p.pnlUsd)}</div><div class="sub"><span class="${cls(p.pnlPct)}">${fmtPct(p.pnlPct, 2, true)}</span></div></div></div>
      <div class="lc2"><span><small>nilai</small>${mU(p.valueUsd)}</span><span><small>fee</small>${mU(p.feesUsd)}</span><span><small>deposit</small>${dep}</span><span><small>mcap</small>${mU(p.mcapUsd)}</span></div>
      ${rangeStrip(p)}${depthBox(p)}<div class="lc3">${status}</div><div class="lbtns">${btns}</div>${res}${autoCell(p)}</div>`);
    return `<tr class="${busy ? "busy" : ""}"><td><span class="sym">${head}</span><span class="sub">${sub}</span></td><td class="r">${pnl}</td><td>${rangeStrip(p)}</td><td>${status}</td>
      <td>${btns}${res}${autoCell(p)}<a href="https://dexscreener.com/elysium/${p.pool}" target="_blank" rel="noopener">dex ↗</a></td></tr>
      ${state.depthOpen[p.id] ? `<tr class="depthrow"><td colspan="5">${depthBox(p)}</td></tr>` : ""}`;
  }).join("");
  $("#pfCards").innerHTML = cards.join("");
  for (const id of Object.keys(state.note)) if (!rowOf(id)) delete state.note[id];
  const seen = store.get("seenAlerts", {});
  for (const p of pf.rows) for (const e of p.events || []) {
    const k = p.id + "|" + e.rule;
    if (!seen[k]) { seen[k] = Date.now(); notify(`${e.kind === "close" ? "🔴 EXIT" : "⚠️ ALERT"} ${posName(p)}`, e.msg); }
  }
  for (const k of Object.keys(seen)) if (Date.now() - seen[k] > 3 * 864e5) delete seen[k];
  store.set("seenAlerts", seen);
  const x = pf.exitsCfg || {};
  $("#pfRules").textContent = `Satu baris = satu pembukaan (ladder atau satu range). Dua posisi di pool yang sama tetap dua baris, PnL dan rule auto-nya sendiri-sendiri. `
    + `Alert: PnL ≤ ${x.alertAtPnlPct ?? "off"}% · 1/3 bawah ladder ${x.alertBottomThirdOfRange ? "on" : "off"} · lewat semua slice ${x.alertFullyConverted ? "on" : "off"}. `
    + `Deposit: yang dicatat terminal saat buka; posisi dari luar terminal dihitung dari likuiditas (≈).`;
}

// ---------- settings ----------
async function openSettings() {
  let st;
  try { st = await getJson("/api/settings"); } catch (e) { alert("Gagal ambil settings: " + e.message); return; }
  const fieldHtml = f => {
    const id = "s_" + f.path.replace(/\./g, "_");
    const chg = JSON.stringify(f.value) !== JSON.stringify(f.base);
    let input;
    if (f.kind === "bool") input = `<input type="checkbox" id="${id}" ${f.value ? "checked" : ""}>`;
    else if (f.kind === "select") input = `<select id="${id}">${f.options.map(o => `<option ${o === f.value ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    else input = `<input id="${id}" value="${f.value == null ? "" : f.value}" placeholder="${f.kind === "numnull" ? "off" : ""}" inputmode="decimal">`;
    return `<label class="${chg ? "chg" : ""}" title="${esc(`config.json: ${f.base == null ? "kosong" : f.base}`)}">${esc(f.label)}${chg ? " •" : ""} ${input}</label>`;
  };
  const groups = [...new Set(st.form.map(f => f.group))];
  $("#modalCard").classList.add("wide");
  $("#modalCard").innerHTML = `<h3>⚙ Settings <span class="muted">dipakai web + CLI</span></h3>
    <div class="sgrid">${groups.map(g => `<h4>${esc(g.toUpperCase())}</h4>` + st.form.filter(f => f.group === g).map(fieldHtml).join("")).join("")}</div>
    <div class="note" style="padding:8px 0 0">• = beda dari config.json (hover buat lihat nilai asal). Kosong di kolom "off" = mati / tanpa batas.</div>
    <div class="acts"><button class="btn go" id="sSave">Simpan</button><button class="btn" id="sReset">Reset ke config.json</button>
      <span class="spacer"></span><button class="btn" id="sClose">Tutup</button></div><div class="res" id="sMsg"></div>`;
  $("#modal").classList.remove("hidden");
  $("#sClose").onclick = closeModal;
  const send = async body => {
    try {
      const res = await trade.call("/api/settings", body);
      if (!res.ok) throw new Error(res.error);
      $("#sMsg").innerHTML = `<span class="pos">✓ Tersimpan.</span>`;
      try { state.cfg = await getJson("/api/config"); } catch (e) {}
      loadPools(true);
      if (state.wallet) loadPortfolio(true);
      setTimeout(closeModal, 800);
    } catch (e) { $("#sMsg").innerHTML = `<span class="neg">✗ ${esc(e.message)}</span>`; }
  };
  $("#sSave").onclick = () => {
    const changes = {};
    for (const f of st.form) {
      const el = document.getElementById("s_" + f.path.replace(/\./g, "_"));
      const v = f.kind === "bool" ? el.checked : el.value.trim();
      const cur = f.kind === "bool" || f.kind === "select" ? f.value : f.value == null ? "" : String(f.value);
      if (String(v) !== String(cur)) changes[f.path] = v;
    }
    if (!Object.keys(changes).length) return closeModal();
    send({ changes });
  };
  $("#sReset").onclick = () => { if (confirm("Balikin semua ke config.json?")) send({ reset: true }); };
}

// ---------- wiring ----------
function setUnit(u) {
  state.unit = u;
  store.set("unit", u);
  document.querySelectorAll("#unit button").forEach(b => b.classList.toggle("on", b.dataset.u === u));
  renderTop();
  if (state.data) renderPools();
  if (state.pf) renderPortfolio();
  const r = state.sel && findRow(state.sel);
  if (r) renderDetail(r, true);
}
function setMode(m) {
  state.mode = m;
  store.set("mode", m);
  if (state.search) { state.search = null; $("#q").value = ""; }
  paintBottom();
  document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.m === m));
  if (state.data) renderPools();
}
function tightPref(name, fallback) {
  try { const v = localStorage.getItem(name); return v == null || v === "" ? fallback : v; } catch (e) { return fallback; }
}
function saveTightPref(name, value) { try { localStorage.setItem(name, value); } catch (e) {} }
let tightTimer = null;
async function loadTight() {
  const root = $("#tightRoot");
  const width = tightPref("elyterm.tightWidth", "2");
  const target = tightPref("elyterm.tightTarget", "5");
  try {
    state.tight = await getJson(`/api/tight?width=${encodeURIComponent(width)}&target=${encodeURIComponent(target)}`, null, 20000);
    // do not redraw under the user's cursor while they are typing in one of the boxes
    const typing = document.activeElement && root.contains(document.activeElement) && /INPUT|SELECT/.test(document.activeElement.tagName);
    if (!typing) renderTight();
  } catch (e) {
    if (!state.tight) root.innerHTML = `<div class="empty">Gagal ambil tight range: ${esc(e.message)}</div>`;
  }
  clearTimeout(tightTimer);
  if (state.view === "tight") tightTimer = setTimeout(loadTight, 20000);
}
function renderTight() {
  const d = state.tight;
  if (!d) return;
  const width = d.screen.widthPct, target = d.screen.targetPctPerHour;
  const usd = tightPref("elyterm.tightUsd", String((state.data && state.data.sizeUsd) || 100));
  const rows = d.rows || [];
  $("#tightRoot").innerHTML = `
    <div class="tightwrap">
      <div class="tbox">
        <h3>Cari pool buat range sempit</h3>
        <p class="muted">${esc(d.note || "")} Sumber fee: ${esc(d.feeSource || "?")}. ${d.snapshotAgeSec == null ? "" : "Snapshot pool " + Math.round(d.snapshotAgeSec) + " dtk."}</p>
        <div class="tacts">
          <label>lebar % <input id="tw" value="${width}" style="width:64px" inputmode="decimal"></label>
          <label>target %/jam <input id="tt" value="${target}" style="width:64px" inputmode="decimal" title="Selera lo, bukan patokan aman"></label>
          <label>ukuran $ <input id="tu" value="${esc(usd)}" style="width:72px" inputmode="decimal"></label>
          <button class="btn" id="tReload">Terapkan</button>
        </div>
        <p class="muted">${d.scanned} pool di-scan dari snapshot · ${d.listed} di daftar (butuh beberapa kali lolos biar tidak kedip) · ${d.rejected} tidak lolos filter. Yang tidak lolos tetap ditampilkan dengan alasannya: lu yang mutusin. Pool mana pun (tanpa hook) juga bisa dibuka dari tombol 🎯 tight di detail pool.</p>
      </div>
      <div class="tgrid">${rows.length ? rows.map(tightCard).join("") : `<div class="empty">Belum ada pool yang lolos filter: sampel tipis, data basi, spacing terlalu kasar, atau gerak harga lebih lebar dari range. Coba lebar lebih besar atau target lebih rendah.</div>`}</div>
      ${autoBox(d)}
    </div>`;
  $("#tReload").onclick = () => {
    saveTightPref("elyterm.tightWidth", $("#tw").value);
    saveTightPref("elyterm.tightTarget", $("#tt").value);
    saveTightPref("elyterm.tightUsd", $("#tu").value);
    loadTight().then(renderTight);
  };
  $("#tightRoot").querySelectorAll("[data-plan]").forEach(b => b.onclick = () => openTightPlan(b.dataset.plan));
  $("#gSave").onclick = () => setAutoGlobal({
    holdSec: $("#gHold").value, cooldownSec: $("#gCool").value, maxPerDay: $("#gMax").value,
    maxNotionalUsd: $("#gNot").value, slippageBps: $("#gSlip").value,
  });
  $("#gPause").onclick = () => setAutoGlobal({ paused: !((d.auto || {}).global || {}).paused });
}
function tightCard(c) {
  const v = c.verdict || {}, band = c.band;
  const gross = v.grossPctPerHour == null ? "--" : v.grossPctPerHour.toFixed(2) + "%/jam";
  const apr = v.simpleAprPct == null ? "--" : Math.round(v.simpleAprPct).toLocaleString("en-US") + "%";
  return `<article class="tcard ${c.listed ? "listed" : ""}">
    <h3>${esc(c.symbol || "?")} <span class="muted">${esc(c.quoteSymbol || "")} ${esc(c.dex || "")}</span> <span class="st ${v.status === "lolos filter" ? "ok" : ""}">${c.listed ? "di daftar" : esc(v.status || "")}</span></h3>
    <div class="tnums">
      <span><small>fee pool/TVL</small><b>${gross}</b></span>
      <span><small>APR sederhana</small><b>${apr}</b></span>
      <span><small>lebar di grid</small><b>${band ? band.actualWidthPct.toFixed(2) + "%" + (band.spacingCoarse ? " (grid kasar)" : "") : "--"}</b></span>
      <span><small>TVL</small><b>${fmtUsd(c.tvlUsd)}</b></span>
      <span><small>gerak 1 jam</small><b class="${band && v.movePct > band.actualWidthPct ? "warn" : ""}">${v.movePct == null ? "--" : v.movePct.toFixed(1) + "%"}</b></span>
    </div>
    <div class="muted">${esc(v.label || "")} · ${esc(v.confidence || "")}</div>
    ${(v.notes || []).concat(v.rejects || []).slice(0, 3).map(x => `<div class="muted">${esc(x)}</div>`).join("")}
    <div class="tacts">
      ${c.supported ? `<button class="btn go" data-plan="${esc(c.pool)}">Buka tight…</button>` : `<span class="muted">tidak didukung (hook / fee dinamis / spacing)</span>`}
    </div>
  </article>`;
}
function autoBox(d) {
  const a = d.auto || {}, g = a.global || {}, rules = a.rules || {};
  const names = {};
  for (const r of (state.pf && state.pf.rows) || []) names[r.id] = posName(r);
  const list = Object.entries(rules).map(([id, r]) => {
    const what = [r.tp && `TP +${r.tp}%`, r.sl && `SL ${r.sl}%`, r.above !== "hold" && `↑ ${ACT_WORD[r.above]}`, r.below !== "hold" && `↓ ${ACT_WORD[r.below]}`].filter(Boolean).join(" · ");
    return `<div class="arow"><b>${esc(names[id] || r.symbol || id)}</b> <span class="muted">${esc(id)}</span> · ${esc(what)} · <span class="${r.status === "armed" ? "pos" : r.status === "done" ? "muted" : "warn"}">${esc(r.status)}</span>${r.msg ? ` <span class="muted">${esc(r.msg)}</span>` : ""}</div>`;
  }).join("") || `<div class="muted">Belum ada rule. Pasang dari tombol ⚙ Auto di baris posisi (tab Portfolio), atau langsung saat buka tight.</div>`;
  const when = t => t ? new Date(t * 1000).toLocaleTimeString() : "";
  const log = (d.log || []).slice().reverse().map(x => `<div class="muted">${when(x.ts)} · ${esc(x.symbol || x.id || "")} · ${esc(x.kind || "")} ${esc(x.action || "")} ${esc(x.reason || x.error || "")}</div>`).join("") || `<div class="muted">log kosong</div>`;
  return `<div class="tbox">
    <h3>Auto ${g.paused ? "· ⏸ DIJEDA" : ""} <span class="muted">${d.practice ? "PRACTICE: cuma simulasi" : "LIVE"} · dicek tiap ${d.autoCheckSec || 60} dtk di proses server</span></h3>
    <p class="muted">Batas ini berlaku buat semua rule range (re-entry / close saat keluar range). TP dan SL tidak nunggu.</p>
    <div class="aform cols">
      <label>tahan di luar range <span><input id="gHold" value="${g.holdSec}" inputmode="numeric"> dtk</span></label>
      <label>jeda antar aksi <span><input id="gCool" value="${g.cooldownSec}" inputmode="numeric"> dtk</span></label>
      <label>maks aksi / posisi / hari <span><input id="gMax" value="${g.maxPerDay}" inputmode="numeric"> ×</span></label>
      <label>maks nilai posisi <span>$ <input id="gNot" value="${g.maxNotionalUsd}" inputmode="decimal"></span></label>
      <label>slippage swap buka tight <span><input id="gSlip" value="${g.slippageBps}" inputmode="numeric"> bps</span></label>
    </div>
    <div class="tacts"><button class="btn go" id="gSave">Simpan batas</button><button class="btn ${g.paused ? "" : "kill"}" id="gPause">${g.paused ? "▶ Lanjutkan semua auto" : "⏸ Jeda semua auto"}</button></div>
    <h3 style="margin-top:12px">Rule yang terpasang</h3>${list}
    <h3 style="margin-top:12px">Log</h3>${log}
  </div>`;
}
function openTightPlan(pool) {
  const g = autoGlobal();
  const card = ((state.tight && state.tight.rows) || []).find(r => r.pool === pool) || findRow(pool) || {};
  $("#modalCard").classList.remove("wide");
  $("#modalCard").innerHTML = `
    <h3>🎯 Buka tight ${esc(card.symbol || "")} <span class="muted">${esc(card.quoteSymbol || "")} ${esc(card.dex || "")}</span></h3>
    <div class="acts">
      <label>$ <input id="pUsd" value="${esc(tightPref("elyterm.tightUsd", String((state.data && state.data.sizeUsd) || 100)))}" style="width:80px" inputmode="decimal"></label>
      <label>lebar % <input id="pWidth" value="${esc(tightPref("elyterm.tightWidth", "2"))}" style="width:60px" inputmode="decimal"></label>
      <label>slippage bps <input id="pSlip" value="${g.slippageBps || 100}" style="width:60px" inputmode="numeric"></label>
    </div>
    <div id="pBody" class="muted" style="margin:10px 0">Hitung…</div>
    <div class="aform">
      <label>Kalau harga NAIK lewat atas range <select id="pAbove"><option value="hold">diam</option><option value="reentry">re-entry (range sama, quote saja)</option><option value="close">close + swap</option></select></label>
      <label>Kalau harga TURUN lewat bawah range <select id="pBelow"><option value="hold">diam</option><option value="reentry">re-entry (range sama, quote saja)</option><option value="close">close + swap</option></select></label>
      <label>Take profit, PnL ≥ <span><input id="pTp" placeholder="off" inputmode="decimal"> %</span></label>
      <label>Stop loss, PnL ≤ −<span><input id="pSl" placeholder="off" inputmode="decimal"> %</span></label>
    </div>
    <p class="muted">Rule di atas langsung nempel ke posisi yang kebuka (bisa diubah di ⚙ Auto). Di practice mode tidak ada posisi yang dibuat, jadi rule tidak dipasang.</p>
    <div class="acts"><button class="btn go" id="pGo" ${trade.ready() ? "" : "disabled"} title="${esc(trade.why())}">${trade.ready() ? (practice() ? "🧪 Simulasi" : "⚡ Buka tight") : "🔒 " + esc(trade.why())}</button><span class="spacer"></span><button class="btn" id="pClose">Tutup</button></div>
    <div class="res" id="pMsg"></div>`;
  $("#modal").classList.remove("hidden");
  $("#pAbove").value = tightPref("elyterm.tightAbove", "hold");
  $("#pBelow").value = tightPref("elyterm.tightBelow", "hold");
  $("#pClose").onclick = closeModal;
  let rev = null, timer = null, seq = 0;
  const vals = () => ({ usd: parseFloat($("#pUsd").value) || 0, widthPct: parseFloat($("#pWidth").value) || 0, slippageBps: parseInt($("#pSlip").value, 10) || 100 });
  async function plan() {
    const my = ++seq, v = vals();
    if (!(v.usd > 0) || !(v.widthPct > 0)) { $("#pBody").innerHTML = `<span class="neg">Isi ukuran dan lebar.</span>`; rev = null; return; }
    let r;
    try { r = await post("/api/tight/plan", Object.assign({ pool }, v)); } catch (e) { r = { ok: false, error: e.message }; }
    if (my !== seq || !$("#pBody")) return;
    if (!r.ok) { rev = null; $("#pBody").innerHTML = `<span class="neg">${esc(r.error || "plan gagal")}</span>`; return; }
    rev = r;
    const band = r.band, net = r.net || {}, sizePct = r.tvlUsd ? v.usd / r.tvlUsd * 100 : null;
    $("#pBody").innerHTML = `<div class="tnums">
        <span><small>range di grid pool</small><b>${band.actualWidthPct.toFixed(2)}%${band.spacingCoarse ? " (grid lebih kasar dari yang diminta)" : ""}</b></span>
        <span><small>tick</small><b>${band.tickLower} → ${band.tickUpper}</b></span>
        <span><small>di-swap dulu</small><b>${r.swapFrac != null ? (r.swapFrac * 100).toFixed(0) + "% ≈ " + fmtUsd(v.usd * r.swapFrac) : "--"}</b></span>
        <span><small>ukuran vs TVL</small><b class="${sizePct > 25 ? "warn" : ""}">${sizePct == null ? "--" : sizePct.toFixed(1) + "%"}</b></span>
      </div>
      <div>${(r.steps || []).map(s => `<span title="${esc(s.note)}">${s.n}. <b>${esc(s.what)}</b> ${s.usd != null ? fmtUsd(s.usd) : ""}</span>`).join(" → ")}</div>
      <div class="muted" style="margin-top:6px">${esc(r.flowNote || "")}</div>
      <div class="muted">${net.known ? `Proxy fee 1 jam ${fmtUsd(net.grossUsd)} − fee swap ${fmtUsd(net.swapCostUsd)}. ${esc(r.costNote || "")}` : esc(net.note || "")}</div>`;
  }
  ["#pUsd", "#pWidth", "#pSlip"].forEach(s => { $(s).oninput = () => { clearTimeout(timer); timer = setTimeout(plan, 300); }; });
  plan();
  $("#pGo").onclick = async () => {
    if (!trade.ready() || !rev) return;
    const v = vals();
    const rule = { above: $("#pAbove").value, below: $("#pBelow").value, tp: $("#pTp").value.trim(), sl: $("#pSl").value.trim() };
    if (!practice() && !confirm(`Buka tight ${card.symbol || ""}?\n${fmtUsd(v.usd)} · range ${rev.band.actualWidthPct.toFixed(2)}% · slippage maks ${v.slippageBps / 100}%\n${rule.above !== "hold" || rule.below !== "hold" ? `Auto: atas → ${ACT_WORD[rule.above]}, bawah → ${ACT_WORD[rule.below]}\n` : ""}Wallet ${short(trade.info.wallet)}. Ini kirim transaksi beneran (swap, lalu mint).`)) return;
    saveTightPref("elyterm.tightUsd", String(v.usd));
    saveTightPref("elyterm.tightAbove", rule.above);
    saveTightPref("elyterm.tightBelow", rule.below);
    const btn = $("#pGo");
    btn.disabled = true;
    $("#pMsg").textContent = practice() ? "Simulasi di chain…" : "Swap + mint… (±10–60 detik, jangan ditutup)";
    try {
      const s = await trade.call("/api/trade/tight-open", Object.assign({ pool, symbol: card.symbol, rule }, v));
      if (!$("#pMsg")) return;
      setTimeout(() => { const m = $("#pMsg"); if (m) m.scrollIntoView({ block: "nearest" }); }, 0);
      $("#pMsg").innerHTML = s.ok
        ? `${stepsHtml(s)}<br>${s.dryRun ? "✓ Simulasi lolos." : `✓ Tight kebuka${(s.positions || []).length ? ": NFT #" + s.positions.join(", #") : ""}${s.ruleArmed ? " · auto terpasang" : ""}.`}${practiceNote(s)}`
        : `<span class="neg">✗ ${esc(s.stage || "")} ${esc(s.error || "gagal")}${s.sent ? "<br>Sebagian transaksi sudah terkirim (token ada di wallet). Cleanup bisa jual balik." : ""}</span>`;
      if (s.ok && !s.dryRun) { loadPortfolio(true); setTimeout(() => loadPortfolio(true), 6000); }
    } catch (e) { if ($("#pMsg")) $("#pMsg").innerHTML = `<span class="neg">✗ ${esc(e.message || e)}</span>`; }
    finally { btn.disabled = !trade.ready(); }
  };
}
function setView(v) {
  state.view = v;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  $("#viewPools").classList.toggle("hidden", v !== "pools");
  $("#viewPortfolio").classList.toggle("hidden", v !== "portfolio");
  $("#viewTight").classList.toggle("hidden", v !== "tight");
  document.body.classList.toggle("pf", v === "portfolio");
  document.body.classList.toggle("tight", v === "tight");
  store.set("view", v);
  paintBottom();
  if (v === "portfolio" && !state.pf && $("#wallet").value.trim()) loadPortfolio();
  if (v === "tight") loadTight();
}
function paintBottom() {
  document.querySelectorAll("#bottomnav button").forEach(b => b.classList.toggle("on",
    b.dataset.view === "tight" ? state.view === "tight"
      : b.dataset.view === "portfolio" ? state.view === "portfolio"
      : state.view === "pools" && state.mode === b.dataset.m));
}
function paintBell() {
  const b = $("#bellBtn");
  b.textContent = state.notify ? "🔔" : "🔕";
  b.title = state.notify ? "Notifikasi nyala (PING baru, exit alert). Klik buat matiin." : "Nyalain notifikasi browser: PING baru + exit alert";
}
const closeModal = () => { $("#modal").classList.add("hidden"); $("#modalCard").classList.remove("wide"); };

document.querySelectorAll(".tab").forEach(b => b.onclick = () => setView(b.dataset.view));
document.querySelectorAll("#win button").forEach(b => b.onclick = () => {
  state.win = b.dataset.w;
  document.querySelectorAll("#win button").forEach(x => x.classList.toggle("on", x === b));
  store.set("win", state.win);
  if (state.data) renderPools();
});
document.querySelectorAll("#pools th[data-sort]").forEach(th => th.onclick = () => {
  const k = th.dataset.sort;
  state.sort = { key: k, desc: state.sort.key === k ? !state.sort.desc : k !== "symbol" };
  store.set("sort", state.sort);
  renderPools();
});
document.querySelectorAll("#mode button").forEach(b => b.onclick = () => setMode(b.dataset.m));
document.querySelectorAll("#unit button").forEach(b => b.onclick = () => setUnit(b.dataset.u));
$("#setBtn").onclick = openSettings;
$("#idxChip").onclick = () => loadPools(true);
$("#stAge").onclick = () => loadPools(true);
$("#bellBtn").onclick = async () => {
  if (!state.notify && "Notification" in window && Notification.permission !== "granted") {
    const p = await Notification.requestPermission().catch(() => "denied");
    if (p !== "granted") { alert("Browser nolak notifikasi. Izinin di setting browser buat localhost."); return; }
  }
  state.notify = !state.notify;
  store.set("notify", state.notify);
  paintBell();
};
document.addEventListener("click", e => {
  const st = e.target.closest("[data-star]");
  if (!st) return;
  e.stopPropagation();
  const r = findRow(st.dataset.star);
  toggleWatch(st.dataset.star, r ? r.symbol : "");
}, true);
$("#q").addEventListener("input", () => { if (state.search && !$("#q").value.trim()) { state.search = null; } if (state.data) renderPools(); });
$("#q").addEventListener("keydown", e => { if (e.key === "Enter") runSearch($("#q").value.trim()); });
["#hideSkip", "#tvl", "#quote", "#dex"].forEach(s => $(s).addEventListener("change", () => {
  store.set("filters", { skip: $("#hideSkip").checked, quote: $("#quote").value });
  store.set("tvl", $("#tvl").value);
  try { localStorage.setItem("elyterm.dex", $("#dex").value); } catch (e) {}
  if (state.data) renderPools();
}));
function applyCols() {
  let cols = null;
  try { cols = JSON.parse(localStorage.getItem("elyterm.cols") || "null"); } catch (e) {}
  document.querySelectorAll("#cols input").forEach(el => {
    const on = !cols || cols[el.dataset.col] !== false;
    el.checked = on;
    document.body.classList.toggle("hide-" + el.dataset.col, !on);
  });
}
$("#cols").addEventListener("change", () => {
  const cols = {};
  document.querySelectorAll("#cols input").forEach(el => { cols[el.dataset.col] = el.checked; });
  try { localStorage.setItem("elyterm.cols", JSON.stringify(cols)); } catch (e) {}
  applyCols();
});
const onPoolClick = e => {
  const tr = e.target.closest("tr, .pcard");
  if (!tr || !state.data) return;
  const r = findRow(tr.dataset.pool);
  if (!r) return;
  const z = e.target.closest("[data-zap]");
  if (z) {
    if (z.dataset.zap === "ask") return openZap(r, state.data.sizeUsd);
    return quickZap(r, parseFloat(z.dataset.zap));
  }
  renderDetail(r);
};
$("#pools tbody").addEventListener("click", onPoolClick);
$("#poolCards").addEventListener("click", onPoolClick);
$("#detail").addEventListener("click", async e => {
  if (e.target.closest("[data-closesheet]")) { document.body.classList.remove("sheet"); return; }
  const z = e.target.closest("[data-zap]"), r = state.sel && findRow(state.sel);
  if (z && r) return z.dataset.zap === "ask" ? openZap(r, state.data.sizeUsd) : quickZap(r, parseFloat(z.dataset.zap));
  const t = e.target.closest("[data-tightopen]");
  if (t) return openTightPlan(t.dataset.tightopen);
  const c = e.target.closest("[data-copy]");
  if (c) { try { await navigator.clipboard.writeText(c.dataset.copy); c.textContent = "copied ✓"; } catch (err) {} }
});
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape") return closeModal();
  if (/INPUT|SELECT|TEXTAREA/.test((e.target && e.target.tagName) || "")) return;
  if (e.key === "/") { e.preventDefault(); setView("pools"); $("#q").focus(); }
  else if (e.key === "u") setUnit(state.unit === "usd" ? "eth" : "usd");
  else if (e.key >= "1" && e.key <= "4") setMode(["all", "pass", "ping", "watch"][+e.key - 1]);
});
$("#keyBtn").onclick = async () => {
  try { state.cfg = await getJson("/api/config"); trade.info = state.cfg && state.cfg.trade; paintWallet(); } catch (e) {}
  if (trade.ready()) {
    $("#wallet").value = trade.info.wallet;
    return alert(`Trading wallet: ${trade.info.wallet}${trade.info.pin ? "\nPIN aktif (ELYSIUM_WEB_PIN)." : "\nTrading cuma dari Mac ini."}${practice() ? "\n\nPRACTICE MODE nyala: semua tombol cuma simulasi." : ""}`);
  }
  const chk = (trade.info && trade.info.envCheck) || [];
  alert(trade.why() + (chk.length ? "\n\nCek:\n• " + chk.join("\n• ") : "")
    + "\n\nDi elysium_terminal/.env (satu baris, tanpa spasi, tanpa kutip):\nELYSIUM_PRIVATE_KEY=<64 hex>\n\nKey Robinhood (RH_PRIVATE_KEY) nggak dipakai di sini.\nSave, lalu klik chip ini lagi. Di Terminal: python3 -m elysium_terminal doctor");
};
const onPfClick = e => {
  const b = e.target.closest("[data-act]");
  if (!b || !state.pf) return;
  const p = rowOf(b.dataset.id);
  if (!p) return;
  const a = b.dataset.act;
  if (a === "close") return closePos(p);
  if (a === "claim") return claimPos(p);
  if (a === "swap") return swapPos(p);
  if (a === "reenter") return reenterPos(p);
  if (a === "auto") return openAuto(p);
  if (a === "depth") return toggleDepth(p.id);
  if (a === "resume") return resumeAuto(p.id);
};
$("#positions tbody").addEventListener("click", onPfClick);
$("#pfCards").addEventListener("click", onPfClick);
$("#cleanupBtn").onclick = () => cleanupWallet($("#cleanupBtn"));
$("#cleanupBtn2").onclick = () => cleanupWallet($("#cleanupBtn2"));
$("#loadWallet").onclick = () => loadPortfolio(true);
$("#wallet").addEventListener("keydown", e => { if (e.key === "Enter") loadPortfolio(true); });
document.querySelectorAll("#bottomnav button").forEach(b => b.onclick = () => {
  document.body.classList.remove("sheet");
  if (b.dataset.view === "portfolio") return setView("portfolio");
  if (b.dataset.view === "tight") return setView("tight");
  setView("pools");
  setMode(b.dataset.m);
});

(async function init() {
  window.__elyStarted = true;
  await store.pull();
  state.win = store.get("win", "1h");
  document.querySelectorAll("#win button").forEach(x => x.classList.toggle("on", x.dataset.w === state.win));
  state.sort = store.get("sort", state.sort);
  try { if (localStorage.getItem("elyterm.dex") != null) $("#dex").value = localStorage.getItem("elyterm.dex"); } catch (e) {}
  applyCols();
  const f = store.get("filters", null);
  if (f) { $("#hideSkip").checked = f.skip !== false; $("#quote").value = f.quote || ""; }
  $("#tvl").value = store.get("tvl", "2000");
  state.unit = store.get("unit", "usd");
  document.querySelectorAll("#unit button").forEach(b => b.classList.toggle("on", b.dataset.u === state.unit));
  state.mode = store.get("mode", "all");
  document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.m === state.mode));
  state.notify = store.get("notify", false) && "Notification" in window && Notification.permission === "granted";
  paintBell();
  state.watch = store.get("watch", []);
  $("#poolsEmpty").classList.remove("hidden");
  const pools = loadPools();
  try { state.cfg = await getJson("/api/config"); } catch (e) {}
  trade.info = state.cfg && state.cfg.trade;
  paintWallet();
  $("#wallet").value = (trade.info && trade.info.wallet) || store.get("wallet", "") || (state.cfg && state.cfg.wallet) || "";
  try { setView(store.get("view", "pools")); } catch (e) { setView("pools"); }
  await pools;
  loadWatch();
  if ($("#wallet").value.trim()) loadPortfolio();
  const every = Math.max(1, +((state.cfg && state.cfg.cfg && state.cfg.cfg.web && state.cfg.cfg.web.refreshMin) || 2)) * 60000;
  const beat = () => { if (!document.hidden) fetch("/api/heartbeat").catch(() => {}); };
  beat();
  setInterval(beat, 20000);
  setInterval(() => { if (!document.hidden) loadPools(); }, every);
  // portfolio: every 30 s while you look at it (the server caches 25 s), every few minutes otherwise
  let pfTick = 0;
  setInterval(() => {
    if (document.hidden || !state.wallet || Object.keys(state.busy).length) return;
    pfTick++;
    if (state.view === "portfolio" || pfTick % 6 === 0) loadPortfolio();
  }, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { loadPools(); if (state.wallet) loadPortfolio(); } });
  setInterval(tickAge, 5000);
})();
