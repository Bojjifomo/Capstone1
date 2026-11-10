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
window.addEventListener("unhandledrejection", e => showFatal((e.reason && e.reason.message) || e.reason));

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
  $("#searchInfo").innerHTML = `<span class="muted">🔎 Cari "${esc(q)}" di semua pool V4 Robinhood…</span>`;
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
    chip.textContent = `⛓ ${ix.pools || 0} pool · ${(ix.coverageHours || 0).toFixed(1)}h`;
    chip.classList.toggle("err", !!ix.error);
    chip.title = ix.error ? `Index error: ${ix.error}` : `Index swap: ${ix.pools} pool USDC/HYPE (v3+v4) yang trading, histori ${ix.coverageHours} jam, block ${ix.head || "?"}. Klik = refresh.`;
    $("#idxLine").textContent = `Index swap dari chain: ${ix.pools || 0} pool aktif · histori ${(ix.coverageHours || 0).toFixed(1)} jam (makin lama server nyala, makin lengkap sampai 24 jam) · `
      + `poll terakhir ${ix.lastPoll ? Math.round(Date.now() / 1000 - ix.lastPoll) + " detik lalu" : "--"}${ix.error ? " · error: " + ix.error : ""}`;
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
  if (!rows.length) $("#poolsEmpty").textContent = state.search ? "Nggak ada pool USDC/HYPE (v3/v4) buat itu."
    : state.mode === "watch" ? "Belum ada pool di watchlist: klik ☆ di pool yang mau lu mainin."
    : state.mode === "ping" ? "Nggak ada pool yang lolos filter PING sekarang (atur di ⚙)."
    : !d.rows.length ? "Index swap masih baca chain… (±15–30 detik)" : "Nggak ada pool yang cocok sama filter.";
  document.querySelectorAll("#pools th").forEach(th => th.classList.toggle("sorted", state.mode !== "ping" && !state.search && th.dataset.sort === state.sort.key));
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
    const sub = `${r.lpFeePct}%${r.dynamicFee ? " dyn" : ""} · ${age(r.ageHours)} · <span class="${cls(r.chg["24h"])}">${fmtPct(r.chg["24h"], 0, true)} 24h</span>`;
    const zap = `<div class="zap"><button data-zap="${size / 2}">⚡${mU(size / 2)}</button><button data-zap="${size}">⚡${mU(size)}</button><button class="edit" data-zap="ask">✏️</button></div>`;
    cards.push(`<div class="pcard ${r.pool === state.sel ? "sel" : ""}" data-pool="${r.pool}">
      <div class="pc1">${star}${icon}<b class="psym">${esc(r.symbol)}</b>${qchip(r)}${rank}<span class="spacer"></span><span class="badge ${v.level}">${v.level}</span></div>
      <div class="pc2"><span><small>TVL</small>${mU(r.tvlUsd)}</span><span><small>fee ${w}</small><b class="pos">${fmtPct(r.feePctDay[w], 1)}</b>/hr</span>
        <span><small>1 jam</small>${fmtPct(fee1hPct, 2)}</span><span><small>mcap</small>${mU(r.mcapUsd)}</span></div>
      <div class="pc3">${sub}</div>
      ${reasons ? `<div class="pc3 muted">${esc(reasons)}</div>` : ""}${zap}</div>`);
    return `<tr data-pool="${r.pool}" class="${r.pool === state.sel ? "sel" : ""}">
      <td><span class="sym">${star}${icon}${esc(r.symbol)}${qchip(r)}${rank}</span><span class="sub">${sub} · …${r.pool.slice(-6)}</span></td>
      <td class="r"><span class="big">${mU(r.tvlUsd)}</span><span class="sub">vol ${mU(r.vol[w])}</span></td>
      <td class="r"><span class="big pos">${mU(r.fees[w])}</span><span class="sub">${fmtPct(r.feePctDay[w], 2)}/hari</span></td>
      <td class="r"><span class="big pos">${fmtPct(fee1hPct, 2)}</span><span class="sub">${mU(r.fees["1h"])} · ${r.swaps["1h"] ?? "--"} swap</span></td>
      <td class="r"><span class="big">${mU(r.mcapUsd)}</span><span class="sub">${spark(r)} <span class="${cls(r.chg["24h"])}">${fmtPct(r.chg["24h"], 0, true)}</span></span></td>
      <td><span class="badge ${v.level}">● ${v.level} ${v.score > 0 ? "+" : ""}${v.score}</span><span class="reasons">${esc(reasons)}</span></td>
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
  const chart = `https://dexscreener.com/elysium/${r.pool}?embed=1&theme=dark&trades=0&info=0`;
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
      <div><label class="muted">fee / hari (1j)</label><b class="pos">${fmtPct(r.feePctDay["1h"], 2)}</b><span class="muted">24j ${fmtPct(r.feePctDay["24h"], 2)}</span></div>
    </div>
    <div class="vbox ${v.level}"><span class="lv">${v.level}</span>${label} <span class="muted">skor ${v.score > 0 ? "+" : ""}${v.score}</span></div>
    <ul class="rs">${li}</ul>
    <div class="pinfo">Fee ${mU(r.fees["5m"])} / 5m · ${mU(r.fees["1h"])} / jam · ${mU(r.fees["24h"])} / 24j · ${r.swaps["1h"] ?? "--"} swap 1 jam${tx ? " · " + tx : ""}${r.sizeVsTvlPct != null ? ` · size ${r.sizeVsTvlPct}% dari TVL` : ""}</div>
    ${pg ? `<div class="pinfo">📡 <b>PING #${pg.rank}</b></div>` : ""}
    <div class="zapbig">
      <button data-zap="${size / 2}">⚡ ${mU(size / 2)} · ${ladderLabel()}</button>
      <button data-zap="${size}">⚡ ${mU(size)} · ${ladderLabel()}</button>
      <button class="edit" data-zap="ask">✏️ custom</button>
    </div>
    <div class="note">Zap = ladder ${esc(r.quoteSymbol)}-only (slice sama lebar, isi 1:2:…:n, paling dalam paling gede). v4 lewat PositionManager, v3 lewat NFT multicall. Key cuma di executor.
      Disimulasikan dulu sebelum dikirim. Ubah bentuk default di ⚙. Gate & flag ini nilai awal, bukan hasil backtest: atur sesuai pengalaman lu.</div>
    <div class="chart"><iframe src="${chart}" title="chart" loading="lazy"></iframe></div>`;
  if (old && old.querySelector("iframe").src === chart) $("#detail .chart").replaceWith(old);
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
  $("#practiceChip").classList.toggle("hidden", !practice());
}
const post = (url, body) => getJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const stepsHtml = s => (s.steps || []).map(x => `${x.step || x.label || "tx"}: ${x.hash ? "✓ " + txLink(x.hash) : x.simulated ? "✓ simulasi lolos" : x.needed ? "perlu approve" : x.received != null ? "✓" : "–"}`).join("<br>");
const practiceNote = s => s.practice || s.dryRun ? `<br><span class="warn">PRACTICE MODE: cuma simulasi, nggak ada yang dikirim.</span>${s.note ? " " + esc(s.note) : ""}` : "";

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
function guard() {
  if (!trade.ready()) { alert(trade.why()); return false; }
  if (state.wallet.toLowerCase() !== (trade.info.wallet || "").toLowerCase()) { alert(`Portfolio lagi nampilin wallet lain. Trading wallet: ${trade.info.wallet}`); return false; }
  return true;
}
async function act(p, btn, url, body, confirmMsg, okText) {
  if (!guard()) return;
  if (!practice() && !confirm(confirmMsg)) return;
  btn.disabled = true;
  const cell = btn.parentElement;
  cell.insertAdjacentHTML("beforeend", `<div class="res muted" data-r>${practice() ? "simulasi…" : "jalan… (bisa 1–3 menit)"}</div>`);
  const out = cell.querySelector("[data-r]");
  try {
    const s = await trade.call(url, body);
    out.innerHTML = s.ok ? `${s.hash ? "✓ " + txLink(s.hash) : stepsHtml(s) || "✓"} ${okText(s)}${practiceNote(s)}` : `<span class="neg">✗ ${esc(s.step || "")} ${esc(s.error || "gagal")}</span>`;
    if (s.ok && !s.dryRun) setTimeout(() => loadPortfolio(true), 4000);
  } catch (e) { out.innerHTML = `<span class="neg">✗ ${esc(e.message || e)}</span>`; }
  finally { btn.disabled = false; }
}
const idsOf = p => p.positions.map(x => x.tokenId);
const closeLadder = (p, btn) => act(p, btn, "/api/trade/close", { pool: p.pool, symbol: p.symbol, ids: idsOf(p) },
  `Close ${p.symbol}: ${p.posCount} slice?\nTarik 100% likuiditas + klaim fee, satu transaksi.\nToken yang keluar TIDAK dijual (pakai Close+swap kalau mau).`, () => "closed");
const claimLadder = (p, btn) => act(p, btn, "/api/trade/claim", { pool: p.pool, symbol: p.symbol, ids: idsOf(p) },
  `Claim fee ${p.symbol} (${mU(p.feesUsd)})?\nLikuiditas tetap di tempat, cuma fee yang diambil.`, () => "fee diklaim");
const swapLadder = (p, btn, reopen) => {
  return act(p, btn, "/api/trade/reenter", { pool: p.pool, symbol: p.symbol, ids: idsOf(p), reopen, slices: p.posCount },
    reopen ? `RE-ENTRY ${p.symbol}\n1) Close ${p.posCount} slice (klaim fee)\n2) Jual token yang keluar ke ${p.quoteSymbol} (batal kalau > max loss di bawah harga pool)\n3) Buka ladder baru di harga sekarang, ${p.posCount} slice, bentuk dari ⚙, pakai semua ${p.quoteSymbol} yang balik\n\nLanjut?`
      : `CLOSE + SWAP ${p.symbol}\nClose ${p.posCount} slice, lalu jual token yang keluar ke ${p.quoteSymbol} (batal kalau > max loss di bawah harga pool).\n\nLanjut?`,
    () => reopen ? "re-entry selesai" : "closed + swapped");
};
async function cleanupWallet(btn) {
  if (!guard()) return;
  if (!practice() && !confirm("CLEANUP wallet\nJual sisa token dari ladder lu (yang nilainya ≥ $1) ke USDC/HYPE lewat pool terdalamnya, batal per token kalau > max loss di bawah harga pool.\nUSDC/HYPE/WHYPE nggak dijual. Lanjut?")) return;
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
function autoExitCell(p) {
  const a = (state.pf.autoExit || {})[p.pool], choices = state.pf.tpChoices || [3, 5, 10];
  const cur = a && a.status === "armed" ? a.pct : 0;
  const opts = [0, ...choices].map(v => `<option value="${v}" ${v === cur ? "selected" : ""}>${v ? "TP +" + v + "%" : "auto exit: off"}</option>`).join("");
  let st = "";
  if (a && a.status === "armed") st = `<span class="tp on">🎯 nyala · close+swap di +${a.pct}%${p.pnlPct != null ? ` (kurang ${(a.pct - p.pnlPct).toFixed(2)}%)` : ""}</span>`;
  else if (a && a.status === "firing") st = `<span class="tp warn">⏳ lagi close + swap…</span>`;
  else if (a && a.status === "done") st = `<span class="tp pos">✓ auto exit: ${esc(a.msg || "")}</span>`;
  else if (a && a.status === "failed") st = `<span class="tp neg">✗ auto exit gagal: ${esc(a.msg || "")}</span>`;
  return `<div class="tpbox"><select class="tpsel" data-tp="${p.pool}" data-sym="${esc(p.symbol)}" title="Close semua slice + jual token saat PnL ladder ≥ target. Dicek server tiap ${state.pf.autoExitCheckMin || 2} menit selama Elysium Terminal jalan.">${opts}</select>${st}</div>`;
}
async function setAutoExit(sel) {
  const pct = +sel.value;
  if (!guard()) { renderPortfolio(); return; }
  if (pct && !confirm(`Nyalain AUTO EXIT ${sel.dataset.sym} di PnL +${pct}%?\n\nKalau PnL ladder sampai +${pct}%, server langsung close semua slice + jual token pakai key di .env, tanpa nanya lagi.\nCuma jalan selama Elysium Terminal nyala di Mac.`)) { renderPortfolio(); return; }
  try {
    const r = await trade.call("/api/autoexit", { pool: sel.dataset.tp, symbol: sel.dataset.sym, pct });
    state.pf.autoExit = r.autoExit;
    toast(pct ? `🎯 Auto exit ${sel.dataset.sym} nyala di +${pct}%` : `Auto exit ${sel.dataset.sym} mati`);
  } catch (e) { alert("Gagal: " + e.message); }
  renderPortfolio();
}

// ---------- portfolio ----------
async function loadPortfolio(force) {
  const w = $("#wallet").value.trim();
  if (!w) return;
  state.wallet = w;
  store.set("wallet", w);
  if (!state.pf) { $("#pfEmpty").classList.remove("hidden"); $("#pfEmpty").textContent = "Baca posisi dari chain… (pertama kali scan histori NFT, bisa ±10–30 detik)"; }
  try { state.pf = await getJson(`/api/portfolio?wallet=${encodeURIComponent(w)}${force ? "&force=1" : ""}`, null, 300000); }
  catch (e) { state.pf = { error: e.message }; }
  renderPortfolio();
  renderTop();
}
function rangeStrip(p) {
  const rp = p.rangePct;
  const pos = rp == null ? null : Math.max(-3, Math.min(103, rp));
  const mk = p.state === "in" ? "in" : p.state === "below" ? "below" : "out";
  const word = { in: "in range", above: "di atas ladder (masih " + p.quoteSymbol + ")", below: "lewat bawah ladder (pegang token)", gap: "di celah antar slice" }[p.state];
  return `<div class="strip"><span>${mU(p.capLo)}</span><div class="rbar"><div class="fill" style="left:0;right:0"></div><div class="third"></div>${pos != null ? `<div class="mk ${mk}" style="left:${pos}%"></div>` : ""}</div><span>${mU(p.capHi)}</span>
    <b class="${p.state === "in" ? "pos" : p.state === "below" ? "neg" : "warn"}">${rp == null ? "--" : Math.round(Math.max(-999, Math.min(999, rp))) + "%"}</b></div>
    <span class="sub">${word} · mcap sekarang ${mU(p.mcapUsd)}</span>`;
}
function renderPortfolio() {
  const pf = state.pf;
  if (!pf) return;
  if (pf.error) { $("#pfEmpty").classList.remove("hidden"); $("#pfEmpty").textContent = "Gagal baca: " + pf.error; $("#positions tbody").innerHTML = ""; $("#pfCards").innerHTML = ""; return; }
  const pnlPct = pf.depositUsd ? pf.pnlUsd / pf.depositUsd * 100 : null, b = pf.balances || {};
  $("#pfSummary").innerHTML = `<span>nilai <b>${mU(pf.valueUsd)}</b></span>
    <span>fees unclaimed <b class="pos">${mU(pf.feesUsd)}</b></span>
    <span>PnL <b class="big ${cls(pf.pnlUsd)}">${pf.pnlUsd > 0 ? "+" : ""}${mU(pf.pnlUsd)}</b> <span class="${cls(pnlPct)}">${fmtPct(pnlPct, 2, true)}</span></span>
    <span>deposit <b>${mU(pf.depositUsd)}</b></span>
    <span>wallet <b>${b.usdc != null ? b.usdc.toFixed(2) : "--"} USDC · ${b.hype != null ? fmtEth(b.hype) : "--"} HYPE${b.whype ? " · " + Number(b.whype).toFixed(4) + " WHYPE" : ""}</b></span>
    <span class="muted">dicek ${new Date(pf.t * 1000).toLocaleTimeString()}</span>`;
  $("#pfEmpty").classList.toggle("hidden", pf.rows.length > 0);
  if (!pf.rows.length) $("#pfEmpty").textContent = "Nggak ada posisi v3/v4 terbuka di wallet ini.";
  const cards = [];
  $("#positions tbody").innerHTML = pf.rows.map(p => {
    const ev = p.events || [];
    const exit = ev.find(e => e.kind === "close"), alerts = ev.filter(e => e.kind === "alert").map(e => e.msg);
    const status = exit ? `<span class="badge WAIT">EXIT</span> <span class="neg">${esc(exit.msg)}</span>`
      : alerts.length ? `<span class="badge CAREFUL">ALERT</span> <span class="warn">${esc(alerts.join(" · "))}</span>`
      : `<span class="badge OPEN">OK</span> <span class="muted">${p.state === "in" ? "in range, dapet fee" : p.state === "above" ? "nunggu harga turun ke ladder" : p.state === "gap" ? "di celah slice" : ""}</span>`;
    const pg = pingOf(p.pool);
    const head = `${esc(p.symbol)}${qchip(p)}${pg ? `<span class="rank">📡#${pg.rank}</span>` : ""}`;
    const sub = `${p.posCount} slice · ${p.inRangeCount} in range · ${age(p.heldHours)} · fee ${p.lpFeePct}%`;
    const pnl = `<span class="big ${cls(p.pnlUsd)}">${p.pnlUsd > 0 ? "+" : ""}${mU(p.pnlUsd)}</span><span class="sub"><span class="${cls(p.pnlPct)}">${fmtPct(p.pnlPct, 2, true)}</span> · ${mAlt(p.pnlUsd)}</span>
      <span class="sub">nilai ${mU(p.valueUsd)} · fee <span class="pos">${mU(p.feesUsd)}</span>${p.feePctDay != null ? ` (${fmtPct(p.feePctDay, 2)}/hr)` : ""} · deposit ${mU(p.depositUsd)}</span>`;
    const btns = `<button class="btn ${exit ? "danger" : ""}" data-close="${p.pool}">${exit ? "Exit → close" : "Close"}</button>
      <button class="btn" data-claim="${p.pool}" ${p.feesUsd > 0 ? "" : "disabled"}>Claim</button>
      <button class="btn" data-swap="${p.pool}" title="Close semua slice + jual token">Close+swap</button>
      <button class="btn go" data-reenter="${p.pool}" title="Close + jual token + buka ladder lagi di harga sekarang">Re-entry</button>`;
    cards.push(`<div class="lcard"><div class="lc1"><div><b class="psym">${head}</b><div class="sub">${sub}</div></div>
      <div class="r"><div class="pnlbig ${cls(p.pnlUsd)}">${p.pnlUsd > 0 ? "+" : ""}${mU(p.pnlUsd)}</div><div class="sub"><span class="${cls(p.pnlPct)}">${fmtPct(p.pnlPct, 2, true)}</span></div></div></div>
      <div class="lc2"><span><small>nilai</small>${mU(p.valueUsd)}</span><span><small>fee</small>${mU(p.feesUsd)}</span><span><small>deposit</small>${mU(p.depositUsd)}</span><span><small>mcap</small>${mU(p.mcapUsd)}</span></div>
      ${rangeStrip(p)}<div class="lc3">${status}</div><div class="lbtns">${btns}</div>${autoExitCell(p)}</div>`);
    return `<tr><td><span class="sym">${head}</span><span class="sub">${sub}</span></td><td class="r">${pnl}</td><td>${rangeStrip(p)}</td><td>${status}</td>
      <td>${btns}${autoExitCell(p)}<a href="https://dexscreener.com/elysium/${p.pool}" target="_blank" rel="noopener">dex ↗</a></td></tr>`;
  }).join("");
  $("#pfCards").innerHTML = cards.join("");
  const seen = store.get("seenAlerts", {});
  for (const p of pf.rows) for (const e of p.events || []) {
    const k = p.pool + "|" + e.rule;
    if (!seen[k]) { seen[k] = Date.now(); notify(`${e.kind === "close" ? "🔴 EXIT" : "⚠️ ALERT"} ${p.symbol}`, e.msg); }
  }
  for (const k of Object.keys(seen)) if (Date.now() - seen[k] > 3 * 864e5) delete seen[k];
  store.set("seenAlerts", seen);
  const x = pf.exitsCfg || {};
  $("#pfRules").textContent = `Exit rules: alert PnL ≤ ${x.alertAtPnlPct ?? "off"}% · alert 1/3 bawah range ${x.alertBottomThirdOfRange ? "on" : "off"} · alert lewat semua slice ${x.alertFullyConverted ? "on" : "off"} · `
    + `TP ${x.takeProfitPct ?? "off"} · SL ${x.stopLossPct ?? "off"}. Deposit dihitung dari likuiditas (ladder single-sided), fee yang udah di-claim belum ikut PnL.`;
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
function setView(v) {
  state.view = v;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  $("#viewPools").classList.toggle("hidden", v !== "pools");
  $("#viewPortfolio").classList.toggle("hidden", v !== "portfolio");
  document.body.classList.toggle("pf", v === "portfolio");
  store.set("view", v);
  paintBottom();
  if (v === "portfolio" && !state.pf && $("#wallet").value.trim()) loadPortfolio();
}
function paintBottom() {
  document.querySelectorAll("#bottomnav button").forEach(b => b.classList.toggle("on",
    b.dataset.view === "portfolio" ? state.view === "portfolio" : state.view === "pools" && state.mode === b.dataset.m));
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
["#hideSkip", "#tvl", "#quote"].forEach(s => $(s).addEventListener("change", () => {
  store.set("filters", { skip: $("#hideSkip").checked, quote: $("#quote").value });
  store.set("tvl", $("#tvl").value);
  if (state.data) renderPools();
}));
const onPoolClick = e => {
  const tr = e.target.closest("tr, .pcard");
  if (!tr || !state.data) return;
  const r = findRow(tr.dataset.pool);
  if (!r) return;
  const z = e.target.closest("[data-zap]");
  if (z) return openZap(r, z.dataset.zap === "ask" ? state.data.sizeUsd : parseFloat(z.dataset.zap));
  renderDetail(r);
};
$("#pools tbody").addEventListener("click", onPoolClick);
$("#poolCards").addEventListener("click", onPoolClick);
$("#detail").addEventListener("click", async e => {
  if (e.target.closest("[data-closesheet]")) { document.body.classList.remove("sheet"); return; }
  const z = e.target.closest("[data-zap]"), r = state.sel && findRow(state.sel);
  if (z && r) return openZap(r, z.dataset.zap === "ask" ? state.data.sizeUsd : parseFloat(z.dataset.zap));
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
  const b = e.target.closest("[data-close],[data-claim],[data-swap],[data-reenter]");
  if (!b || !state.pf) return;
  const pool = b.dataset.close || b.dataset.claim || b.dataset.swap || b.dataset.reenter;
  const p = state.pf.rows.find(x => x.pool === pool);
  if (!p) return;
  if (b.dataset.close) return closeLadder(p, b);
  if (b.dataset.claim) return claimLadder(p, b);
  return swapLadder(p, b, !!b.dataset.reenter);
};
$("#positions tbody").addEventListener("click", onPfClick);
$("#pfCards").addEventListener("click", onPfClick);
$("#positions tbody").addEventListener("change", e => { const s = e.target.closest("[data-tp]"); if (s) setAutoExit(s); });
$("#pfCards").addEventListener("change", e => { const s = e.target.closest("[data-tp]"); if (s) setAutoExit(s); });
$("#cleanupBtn").onclick = () => cleanupWallet($("#cleanupBtn"));
$("#cleanupBtn2").onclick = () => cleanupWallet($("#cleanupBtn2"));
$("#loadWallet").onclick = () => loadPortfolio(true);
$("#wallet").addEventListener("keydown", e => { if (e.key === "Enter") loadPortfolio(true); });
document.querySelectorAll("#bottomnav button").forEach(b => b.onclick = () => {
  document.body.classList.remove("sheet");
  if (b.dataset.view === "portfolio") return setView("portfolio");
  setView("pools");
  setMode(b.dataset.m);
});

(async function init() {
  window.__elyStarted = true;
  await store.pull();
  state.win = store.get("win", "1h");
  document.querySelectorAll("#win button").forEach(x => x.classList.toggle("on", x.dataset.w === state.win));
  state.sort = store.get("sort", state.sort);
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
  setInterval(() => loadPools(), every);
  setInterval(() => { if (state.wallet) loadPortfolio(); }, every);
  setInterval(tickAge, 5000);
})();
