// DLMM Web: pools + spec v1 entry check + detail/chart + portfolio exits.
// Talks only to the local server (web.py); nothing here holds a key.
"use strict";

// Any script error ends up on the page instead of a silent "Loading…".
function showFatal(msg) {
  const el = document.getElementById("poolsEmpty");
  if (!el) return;
  el.classList.remove("hidden");
  el.innerHTML = `<span style="color:#ff5a5f">Error di browser: ${String(msg).replace(/[<>&]/g, "")}</span><br>Coba Cmd+Shift+R. Kalau tetap, screenshot ini + Console (Cmd+Option+J).`;
}
window.addEventListener("error", e => showFatal(e.message + (e.filename ? ` (${e.filename.split("/").pop()}:${e.lineno})` : "")));
window.addEventListener("unhandledrejection", e => showFatal((e.reason && e.reason.message) || e.reason));

const $ = s => document.querySelector(s);
const state = {
  view: "pools", win: "24h", sort: { key: "fees", desc: true }, data: null, sel: null,
  pf: null, cfg: null, wallet: "", loadedAt: 0,
  unit: "sol", mode: "all", ping: null, watch: [], notify: false,
};
const store = {
  get(k, d) { try { const v = localStorage.getItem("dlmmweb." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("dlmmweb." + k, JSON.stringify(v)); } catch (e) {} },
};

// ---------- formatting ----------
// 0.0000000637 -> 0.0₇637 (DexScreener style) so tiny token prices stay readable
const SUB = "₀₁₂₃₄₅₆₇₈₉";
const tiny = a => {
  const z = Math.floor(-Math.log10(a));            // zeros after the point
  if (z < 4) return a.toPrecision(3);
  const digits = Math.round(a * Math.pow(10, z + 3)).toString().slice(0, 3);
  return "0.0" + String(z).split("").map(d => SUB[+d]).join("") + digits;
};
const fmtUsd = v => {
  if (v == null || !isFinite(v)) return "--";
  const a = Math.abs(v);
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return "$" + (v / 1e3).toFixed(a >= 1e5 ? 1 : 2) + "k";
  if (a >= 1) return "$" + v.toFixed(2);
  if (a === 0) return "$0";
  return (v < 0 ? "-$" : "$") + tiny(a);
};
const fmtPct = (v, d = 1, sign = false) => v == null || !isFinite(v) ? "--" : (sign && v > 0 ? "+" : "") + v.toFixed(d) + "%";
const fmtSol = (v, d = 3) => v == null || !isFinite(v) ? "--" : "◎" + Number(v).toFixed(d);
// ◎ with k/M for big numbers and enough digits for small ones (pool sizes, token prices in SOL).
const fmtSolK = v => {
  if (v == null || !isFinite(v)) return "--";
  const a = Math.abs(v), sg = v < 0 ? "-" : "";
  if (a >= 1e6) return sg + "◎" + (a / 1e6).toFixed(2) + "M";
  if (a >= 1e4) return sg + "◎" + (a / 1e3).toFixed(1) + "k";
  if (a >= 100) return sg + "◎" + a.toFixed(0);
  if (a >= 1) return sg + "◎" + a.toFixed(2);
  if (a === 0) return "◎0";
  return sg + "◎" + tiny(a);
};
const solUsd = () => (state.data && state.data.solUsd) || null;
// Every amount goes through one of these two, so the ◎ SOL / $ USD switch changes the whole page.
const mU = usd => {                 // an amount the API gives in USD
  if (usd == null || !isFinite(usd)) return "--";
  return state.unit === "usd" || !solUsd() ? fmtUsd(usd) : fmtSolK(usd / solUsd());
};
const mS = (sol, d = 3) => {        // an amount the API gives in SOL
  if (sol == null || !isFinite(sol)) return "--";
  if (state.unit === "usd" && solUsd()) { const u = sol * solUsd(); return (u < 0 ? "-" : "") + fmtUsd(Math.abs(u)); }
  return fmtSol(sol, d);
};
// the other unit, small, for context
const mAlt = sol => sol == null || !solUsd() ? "" : state.unit === "usd" ? fmtSol(sol, 3) : (sol < 0 ? "-" : "") + fmtUsd(Math.abs(sol * solUsd()));
const cls = v => v == null ? "muted" : v > 0 ? "pos" : v < 0 ? "neg" : "muted";
const age = h => h == null ? "--" : h < 1 ? Math.round(h * 60) + "m" : h < 48 ? h.toFixed(1) + "h" : (h / 24).toFixed(1) + "d";
const short = a => a ? a.slice(0, 4) + "…" + a.slice(-4) : "";
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Spec reasons, from the server's terse form to something readable.
function nice(t) {
  let m;
  if ((m = /^sizeVsTvl: (.*)% > (.*)%/.exec(t))) return `Size ${(+m[1]).toFixed(1)}% of TVL (max ${m[2]}%)`;
  if ((m = /^poolAge: (\d+)m < (\d+)m/.exec(t))) return `Pool ${m[1]}m old (min ${m[2]}m)`;
  if ((m = /^feeApr24h: (\d+)% < (\d+)%/.exec(t))) return `Fees thin: APR ${(+m[1]).toLocaleString()}% (min ${(+m[2]).toLocaleString()}%)`;
  if ((m = /^binStep: (.*) outside (.*)/.exec(t))) return `Bin step ${m[1]} (only ${m[2]})`;
  if (/^solQuoted/.test(t)) return "Not a SOL pair";
  if ((m = /^pump1h (.*)/.exec(t))) return `Pump 1h ${m[1]}`;
  if ((m = /^drop6h (.*)/.exec(t))) return `Drop 6h ${m[1]}`;
  if ((m = /^tvl (.*)/.exec(t))) return `Thin pool: TVL ${m[1]}`;
  if ((m = /^night entry (.*)/.exec(t))) return `Night entry ${m[1]} WIB`;
  return t;
}

// 24h path from DexScreener's price changes (24h, 6h, 1h, 5m ago -> now).
function spark(r, w = 64, h = 22) {
  const legs = [["24h", r.priceChg24h], ["6h", r.priceChg6h], ["1h", r.priceChg1h], ["5m", r.chg5m]];
  const xs = { "24h": 0, "6h": 18 / 24, "1h": 23 / 24, "5m": 1 - 5 / 1440 };
  const pts = legs.filter(l => l[1] != null && l[1] > -100).map(([k, c]) => [xs[k], 1 / (1 + c / 100)]);
  if (pts.length < 2) return "";
  pts.push([1, 1]);
  const ys = pts.map(p => p[1]), lo = Math.min(...ys), hi = Math.max(...ys), span = hi - lo || 1;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${(p[0] * w).toFixed(1)},${(h - 2 - (p[1] - lo) / span * (h - 4)).toFixed(1)}`).join("");
  const up = (r.priceChg24h || 0) >= 0;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="${d}" fill="none" stroke="${up ? "#3ddc84" : "#ff5a5f"}" stroke-width="1.4"/></svg>`;
}

// ---------- data ----------
async function getJson(url, opt, timeoutMs) {
  // 90s cap by default: a request that never answers shows as an error, not an endless "Loading…".
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
  const tvl = $("#tvl").value;
  try {
    state.data = await getJson(`/api/pools?tvl=${tvl}${force ? "&force=1" : ""}`);
    state.loadedAt = Date.now();
    renderTop();
    renderPools();
    if (state.pf) renderPortfolio();
    if (state.sel) {
      const r = state.data.rows.find(x => x.pool === state.sel);
      if (r) renderDetail(r, true);
    }
  } catch (e) {
    $("#poolsEmpty").classList.remove("hidden");
    $("#poolsEmpty").textContent = "Gagal ambil pool: " + e.message + " — cek jendela Terminal yang jalanin server.";
  }
}

// PING = the signal filter (v1 gates + GMGN bands, minus dead→alive pools), top N. The server
// rebuilds it in the background every 5 min; while it runs the last list stays up.
async function loadPing(force) {
  try {
    const p = await getJson(`/api/ping${force ? "?force=1" : ""}`);
    const fresh = p.t && (!state.ping || p.t !== state.ping.t);
    state.ping = p;
    if (fresh && (p.new || []).length) {
      const names = p.rows.filter(r => p.new.includes(r.pool)).map(r => r.symbol);
      toast(`📡 Baru masuk PING: ${names.join(", ")}`);
      notify("📡 PING baru", names.join(", "));
    }
    if (state.data) renderPools();
    if (p.running) setTimeout(() => loadPing(), 8000);
  } catch (e) { state.ping = { error: e.message, rows: [] }; if (state.data) renderPools(); }
}
const pingOf = pool => state.ping && (state.ping.rows || []).find(r => r.pool === pool);
async function loadWatch() {
  try { state.watch = (await getJson("/api/watchlist")).rows || []; } catch (e) { state.watch = store.get("watch", []); }
  if (state.data) renderPools();
}
const watched = pool => state.watch.some(w => w.pool === pool);
async function toggleWatch(pool, symbol) {
  const on = !watched(pool);
  // optimistic, then the server's list (kept in data/watchlist.json, so the phone over Tailscale sees it too)
  state.watch = on ? [...state.watch, { pool, symbol, addedAt: Date.now() / 1000 }] : state.watch.filter(w => w.pool !== pool);
  store.set("watch", state.watch);
  renderPools();
  if (state.sel === pool) { const r = state.data.rows.find(x => x.pool === pool); if (r) renderDetail(r, true); }
  try {
    const res = await getJson("/api/watchlist", { method: "POST", headers: { "Content-Type": "application/json", "X-DLMM-Trade": "1" },
      body: JSON.stringify({ pool, symbol, on, pin: trade.pin }) });
    state.watch = res.rows || state.watch;
  } catch (e) { toast("Watchlist cuma disimpan di browser ini: " + e.message); }
}

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
  const q = $("#q").value.trim().toLowerCase();
  const m = state.mode;
  if (m === "ping") {
    // the PING order (top N by the signal's sort), not the table's
    return ((state.ping && state.ping.rows) || []).map(p => d.rows.find(r => r.pool === p.pool)).filter(Boolean)
      .filter(r => !q || `${r.symbol} ${r.name} ${r.pool} ${r.tokenMint}`.toLowerCase().includes(q));
  }
  let rows = d.rows.filter(r => {
    if (m === "pass" && !["OPEN", "CAREFUL"].includes(r.verdict.level)) return false;
    if (m === "watch" && !watched(r.pool)) return false;
    if (m === "watch") return !q || `${r.symbol} ${r.name} ${r.pool} ${r.tokenMint}`.toLowerCase().includes(q);
    if ($("#hideSkip").checked && r.verdict.level === "SKIP") return false;
    if (q && !(`${r.symbol} ${r.name} ${r.pool} ${r.tokenMint}`.toLowerCase().includes(q))) return false;
    return true;
  });
  const w = state.win;
  const key = state.sort.key;
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

// ---------- render: top bar ----------
function renderTop() {
  const d = state.data;
  if (d) {
    $("#stSol").innerHTML = `SOL <b>${fmtUsd(d.solUsd)}</b>`;
    $("#stCap").textContent = `max ${mS(d.maxPositionSol, 2)} / posisi`;
  }
  const pf = state.pf;
  if (pf && !pf.error) {
    $("#stNet").innerHTML = `posisi <b>${mS(pf.valueSol, 2)}</b> <span class="muted">${mAlt(pf.valueSol)}</span>`;
    $("#stFees").innerHTML = `fees unclaimed <b>${mS(pf.unclaimedSol, 3)}</b>`;
    $("#stWallet").innerHTML = `wallet <b>${mS(pf.walletSol, 3)}</b>`;
    const n = new Set(pf.rows.map(r => r.pool)).size;
    $("#posCount").textContent = n;
    $("#posCount").classList.toggle("hidden", !n);
  }
  tickAge();
}
function tickAge() {
  if (!state.loadedAt) return;
  const s = Math.round((Date.now() - state.loadedAt) / 1000);
  const el = $("#stAge");
  el.textContent = "● " + (s < 60 ? s + "s ago" : Math.round(s / 60) + "m ago");
  el.classList.toggle("stale", s > 180);
}

// ---------- render: pools ----------
function posCfg() { return (state.cfg && state.cfg.cfg && state.cfg.cfg.position) || {}; }
function ladderLabel() {
  const p = posCfg();
  return `${p.ladderSlices || 5}×${p.strategy || "Spot"} ${p.rangeBottomPct || -70}%`;
}
function renderPingInfo() {
  const el = $("#pingInfo"), p = state.ping;
  el.classList.toggle("hidden", state.mode !== "ping");
  $("#nPing").textContent = p && p.rows ? p.rows.length : "";
  $("#nWatch").textContent = state.watch.length || "";
  if (state.mode !== "ping") return;
  if (!p) { el.innerHTML = "📡 Scan PING… (pertama kali 20–60 detik)"; return; }
  const ago = p.t ? Math.round((Date.now() / 1000 - p.t) / 60) : null;
  const missing = (p.rows || []).filter(r => !state.data.rows.some(x => x.pool === r.pool));
  el.innerHTML = `<span>📡 ${esc(p.filter || "")}</span>
    ${(p.revived || []).length ? `<span class="warn">Dibuang (mati→hidup): ${p.revived.map(x => esc(x.symbol)).join(", ")}</span>` : ""}
    ${missing.length ? `<span class="warn">${missing.length} pool PING di luar filter TVL: ${missing.map(x => esc(x.symbol)).join(", ")} (turunin TVL ≥)</span>` : ""}
    <span class="spacer"></span><span>${p.running ? "scan jalan…" : ago != null ? `update ${ago}m lalu` : ""}${p.error ? ` · <span class="neg">${esc(p.error)}</span>` : ""}</span>
    <button class="btn" id="pingRefresh">↻ scan ulang</button>`;
  $("#pingRefresh").onclick = () => { loadPing(true); setTimeout(() => loadPing(), 3000); };
}
function renderPools() {
  const rows = rowsView(), w = state.win;
  const d = state.data;
  renderPingInfo();
  $("#count").textContent = `${rows.length} of ${d.rows.length} pools · Meteora DLMM`;
  $("#poolsEmpty").classList.toggle("hidden", rows.length > 0);
  if (!rows.length) $("#poolsEmpty").textContent = state.mode === "watch" ? "Belum ada pool di watchlist: klik ☆ di pool yang mau lu mainin."
    : state.mode === "ping" ? (state.ping && state.ping.running ? "Scan PING jalan…" : "Nggak ada pool yang lolos filter PING sekarang.") : "No pool matches the filters.";
  document.querySelectorAll("#pools th").forEach(th => th.classList.toggle("sorted", state.mode !== "ping" && th.dataset.sort === state.sort.key));
  document.querySelector("#pools th[data-sort=fees] .wl").textContent = w.toUpperCase();
  const size = d.sizeSol;
  const fresh = new Set((state.ping && state.ping.new) || []);
  $("#pools tbody").innerHTML = rows.map(r => {
    const v = r.verdict, pg = pingOf(r.pool);
    const reasons = [...v.reasons.filter(x => !x.ok), ...v.info].slice(0, 2).map(x => nice(x.text)).join(" · ")
      || v.reasons.filter(x => x.ok).map(x => "gates pass").join("");
    const fee1hPct = r.tvlUsd ? r.fees["1h"] / r.tvlUsd * 100 : null;
    const icon = r.icon ? `<img src="${esc(r.icon)}" alt="" loading="lazy">` : `<span class="ph"></span>`;
    const rank = pg ? `<span class="rank ${fresh.has(r.pool) ? "new" : ""}" title="PING #${pg.rank} · 1h vol/TVL ${fmtPct(pg.volTvl1h, 1)}">📡#${pg.rank}</span>` : "";
    const sub2 = pg ? ` · vol/TVL 1h ${fmtPct(pg.volTvl1h, 0)}` : "";
    return `<tr data-pool="${r.pool}" class="${r.pool === state.sel ? "sel" : ""}">
      <td><span class="sym"><button class="star ${watched(r.pool) ? "on" : ""}" data-star="${r.pool}" title="watchlist">${watched(r.pool) ? "★" : "☆"}</button>${icon}${esc(r.symbol)}${rank}</span>
        <span class="sub">${r.binStep} · ${r.baseFeePct}% · ${age(r.poolAgeHours)} · <span class="${cls(r.priceChg24h)}">${fmtPct(r.priceChg24h, 0, true)} 24h</span>${sub2} · …${r.pool.slice(-6)}</span></td>
      <td class="r"><span class="big">${mU(r.tvlUsd)}</span><span class="sub">vol ${mU(r.vol24)}</span></td>
      <td class="r"><span class="big pos">${mU(r.fees[w])}</span><span class="sub">${fmtPct(r.feePctDay[w], 2)}/day</span></td>
      <td class="r"><span class="big pos">${fmtPct(fee1hPct, 2)}</span><span class="sub">${mU(r.fees["1h"])} / jam</span></td>
      <td class="r"><span class="big">${mU(r.mcapUsd)}</span><span class="sub">${spark(r)} <span class="${cls(r.priceChg24h)}">${fmtPct(r.priceChg24h, 0, true)}</span></span></td>
      <td><span class="badge ${v.level}">● ${v.level} ${v.score > 0 ? "+" : ""}${v.score}</span><span class="reasons">${esc(reasons)}</span></td>
      <td><div class="zap">
        <button data-zap="${size / 2}">⚡${mS(size / 2, 2)}</button>
        <button data-zap="${size}">⚡${mS(size, 2)}</button>
        <button class="edit" data-zap="ask">✏️</button></div></td>
    </tr>`;
  }).join("");
}

// ---------- render: detail ----------
function renderDetail(r, keepChart) {
  state.sel = r.pool;
  document.querySelectorAll("#pools tr").forEach(tr => tr.classList.toggle("sel", tr.dataset.pool === r.pool));
  const v = r.verdict, d = state.data, size = d.sizeSol;
  const label = { OPEN: "Semua gate lolos, nggak ada flag.", CAREFUL: "Gate lolos, tapi ada flag: cek dulu.",
    WAIT: "Belum: ada gate yang belum lolos (bisa berubah).", SKIP: "Di luar universe spec (bin step / bukan SOL)." }[v.level];
  const li = [...v.reasons.map(x => `<li class="${x.ok ? "" : x.flag ? "flag" : "bad"}">${esc(nice(x.text))}</li>`),
    ...v.info.map(x => `<li class="info ${x.ok ? "" : "bad"}">${esc(x.text)}</li>`)].join("");
  const chart = `https://dexscreener.com/solana/${r.pool}?embed=1&theme=dark&trades=0&info=0`;
  const old = keepChart ? $("#detail .chart") : null;
  $("#detail").innerHTML = `
    <div class="dh"><button class="star ${watched(r.pool) ? "on" : ""}" data-star="${r.pool}" title="watchlist">${watched(r.pool) ? "★" : "☆"}</button><span class="sym">${r.icon ? `<img src="${esc(r.icon)}" alt="">` : ""}<span class="name">${esc(r.symbol)}</span></span>
      <span class="muted">${r.binStep} · ${r.baseFeePct}%</span>
      <span class="addr" title="copy pool address" data-copy="${r.pool}">${short(r.pool)} ⧉</span>
      <span class="spacer"></span>
      <a href="https://app.meteora.ag/dlmm/${r.pool}" target="_blank" rel="noopener">meteora ↗</a>
      <a href="https://dexscreener.com/solana/${r.pool}" target="_blank" rel="noopener">dex ↗</a></div>
    <div class="stats">
      <div><label class="muted">harga</label><b>${mU(r.priceUsd)}</b><span class="${cls(r.priceChg1h)}">${fmtPct(r.priceChg1h, 1, true)} 1j</span></div>
      <div><label class="muted">mcap</label><b>${mU(r.mcapUsd)}</b><span class="${cls(r.priceChg24h)}">${fmtPct(r.priceChg24h, 0, true)} 24j</span></div>
      <div><label class="muted">TVL</label><b>${mU(r.tvlUsd)}</b><span class="muted">umur ${age(r.poolAgeHours)}</span></div>
      <div><label class="muted">fee / hari</label><b class="pos">${fmtPct(r.feePctDay["24h"], 2)}</b><span class="muted">APR ${Math.round(r.feeApr24h).toLocaleString()}%</span></div>
    </div>
    <div class="vbox ${v.level}"><span class="lv">${v.level}</span>${label} <span class="muted">skor ${v.score > 0 ? "+" : ""}${v.score}</span></div>
    <ul class="rs">${li}</ul>
    ${pingLine(r)}
    <div class="zapbig">
      <button data-zap="${size / 2}">⚡ ${mS(size / 2, 2)} · ${ladderLabel()}</button>
      <button data-zap="${size}">⚡ ${mS(size, 2)} · ${ladderLabel()}</button>
      <button class="edit" data-zap="ask">✏️ custom</button>
    </div>
    <div class="note">Zap = ladder ${esc(posCfg().strategy || "Spot")} SOL-only ${posCfg().ladderSlices || 5} slice (RH), dibuka langsung pakai key di .env (simulasi dulu tiap slice). Ubah bentuknya di ⚙ Settings. Key nggak pernah ke browser.
      Skor cuma konteks: spec nggak nemu sinyal entry yang tahan di token baru, jadi level ditentuin gate + flag aja.</div>
    <div class="chart"><iframe src="${chart}" title="chart" loading="lazy"></iframe></div>`;
  if (old && old.querySelector("iframe").src === chart) $("#detail .chart").replaceWith(old);
}

function pingLine(r) {
  const p = pingOf(r.pool);
  if (!p) return state.ping && state.ping.t ? `<div class="pinfo">📡 Nggak masuk PING sekarang.</div>` : "";
  return `<div class="pinfo">📡 <b>PING #${p.rank}</b> · 1h vol/TVL <b>${fmtPct(p.volTvl1h, 1)}</b> · 1d fees/TVL <b>${fmtPct(p.feeTvl24h, 1)}</b>
    · fees 1h <b>${mU(p.fees1hUsd)}</b> · total fees pool <b>${mS(p.totalFeesSol, 1)}</b>
    · vol 24h token <b>${mU(p.volume24hUsd)}</b>${p.activeHours != null ? ` · aktif ${p.activeHours}/24 jam` : ""}${p.boost ? ` · ⚡boost ${p.boost}` : ""}</div>`;
}

// ---------- trading (key in .env on the server, like LP WEB) ----------
// The browser never sees the key, only the wallet address. Trades need the X-DLMM-Trade
// header, and the PIN when DLMM_WEB_PIN is set (that is what allows a phone to trade).
const trade = {
  info: null,
  pin: null,
  ready() { return !!(this.info && this.info.enabled && (this.info.local || this.info.pin)); },
  why() {
    const t = this.info || {};
    if (!t.enabled) return t.error ? `Key error: ${t.error}` : "Read-only: isi DLMM_PRIVATE_KEY di .env lalu start ulang server";
    if (!t.local && !t.pin) return "Trading cuma dari Mac yang jalanin server (atau set DLMM_WEB_PIN buat dari HP)";
    return "";
  },
  async call(url, body) {
    if (this.info && this.info.pin && !this.pin) {
      const p = prompt("PIN trading (DLMM_WEB_PIN):");
      if (!p) throw new Error("dibatalin");
      this.pin = p;
    }
    try {
      return await getJson(url, { method: "POST", headers: { "Content-Type": "application/json", "X-DLMM-Trade": "1" },
        body: JSON.stringify(Object.assign({}, body, { pin: this.pin })) }, 600000);
    } catch (e) {
      if (/PIN/.test(e.message)) this.pin = null;
      throw e;
    }
  },
};
function paintWallet() {
  const b = $("#keyBtn"), t = trade.info || {};
  b.classList.toggle("on", trade.ready());
  b.textContent = t.enabled ? `🔑 ${short(t.wallet)}${trade.ready() ? "" : " · read-only"}` : "🔒 read-only";
  b.title = trade.ready() ? `Trading wallet ${t.wallet}${t.pin ? " (PIN aktif)" : ""}` : trade.why();
}
const post = (url, body) => getJson(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const solscan = sig => `<a href="https://solscan.io/tx/${sig}" target="_blank" rel="noopener">${sig.slice(0, 8)}… ↗</a>`;

// ---------- zap modal: RH ladder, Spot, SOL-only ----------
function openZap(r, size) {
  $("#modalCard").classList.remove("wide");
  const d = state.data, maxSol = d.maxPositionSol, pos = (state.cfg && state.cfg.cfg && state.cfg.cfg.position) || {};
  const execMissing = state.cfg && state.cfg.executor;
  $("#modalCard").innerHTML = `
    <h3>⚡ Zap ${esc(r.symbol)} <span class="muted">bin ${r.binStep} · Spot SOL-only ladder</span></h3>
    <div class="acts">
      <label>SOL <input id="zSize" value="${+size.toFixed(4)}"></label>
      <label>slices <input id="zSlices" value="${pos.ladderSlices || 5}" style="width:50px"></label>
      <label>bottom −% <input id="zBottom" value="${Math.abs(pos.rangeBottomPct || 70)}" style="width:60px"></label>
      <label>bentuk <select id="zStrat">${["Spot", "Curve", "BidAsk"].map(x => `<option ${x === (pos.strategy || "Spot") ? "selected" : ""}>${x}</option>`).join("")}</select></label>
      <span class="muted" id="zAlt"></span>
    </div>
    <div id="zBody" style="margin-top:10px"></div>
    <div id="zLadder" class="muted">Plan ladder…</div>
    <div class="acts">
      <button class="btn go" id="zOpen" ${trade.ready() ? "" : "disabled"} title="${esc(trade.why())}">${trade.ready() ? "⚡ Buka ladder" : "🔒 " + esc(trade.why())}</button>
      <a class="btn" href="https://app.meteora.ag/dlmm/${r.pool}" target="_blank" rel="noopener">Meteora ↗</a>
      <button class="btn" id="zSkip">Log: skip</button>
      <span class="spacer"></span><button class="btn" id="zClose">Tutup</button>
    </div><div class="res" id="zMsg">${execMissing ? `⚠ Executor: ${esc(execMissing)}` : ""}</div>`;
  $("#modal").classList.remove("hidden");
  let cur = null, planTimer = null, planSeq = 0;
  const draw = () => {
    const sz = parseFloat($("#zSize").value) || 0, bottom = parseFloat($("#zBottom").value) || 70, slices = parseInt($("#zSlices").value, 10) || 5;
    const strategy = $("#zStrat").value.toLowerCase();
    $("#zAlt").textContent = solUsd() ? "≈ " + fmtUsd(sz * solUsd()) : "";
    const sizePct = r.tvlUsd ? sz * d.solUsd / r.tvlUsd * 100 : null;
    const gateFails = r.verdict.reasons.filter(x => !x.ok && !x.flag && !/^sizeVsTvl/.test(x.text)).map(x => nice(x.text));
    const maxTvlPct = state.cfg && state.cfg.cfg && state.cfg.cfg.gates.maxPositionPctOfTvl;
    if (maxTvlPct != null && sizePct != null && sizePct > maxTvlPct) gateFails.push(`Size ${sizePct.toFixed(1)}% of TVL (max ${maxTvlPct}%)`);
    const warn = [];
    if (sz > maxSol + 1e-9) warn.push(`⛔ ${fmtSol(sz, 2)} di atas cap loss budget ${fmtSol(maxSol, 2)}`);
    if (bottom < 65) warn.push(`⚠ Range bottom −${bottom}%: spec pakai −70% (semua rugi besar jatuh 44–58%)`);
    const flags = r.verdict.reasons.filter(x => x.flag).map(x => "⚠ " + nice(x.text));
    $("#zBody").innerHTML = `
      <div class="vbox ${gateFails.length ? "WAIT" : flags.length ? "CAREFUL" : "OPEN"}" style="margin:0 0 10px">
        <span class="lv">${gateFails.length ? "GATE ✗" : flags.length ? "CAREFUL" : "OPEN"}</span>
        ${gateFails.length ? esc(gateFails.join(" · ")) : `Gates lolos · ${fmtPct(sizePct, 2)} of TVL`}</div>
      ${[...warn, ...flags].map(x => `<div class="warn">${esc(x)}</div>`).join("")}`;
    cur = { sz, bottom, slices, strategy, gateFails, flags, over: sz > maxSol + 1e-9 };
    clearTimeout(planTimer);
    planTimer = setTimeout(loadPlan, 350);
  };
  async function loadPlan() {
    const seq = ++planSeq;
    $("#zLadder").innerHTML = '<span class="muted">Plan ladder…</span>';
    const p = await post("/api/ladder/plan", { pool: r.pool, sol: cur.sz, slices: cur.slices, bottom: cur.bottom, strategy: cur.strategy }).catch(e => ({ ok: false, error: e.message }));
    if (seq !== planSeq) return;
    if (!p.ok) { $("#zLadder").innerHTML = `<span class="neg">${esc(p.error || "plan gagal")}</span>`; return; }
    const pl = p.plan, mx = Math.max(...pl.slices.map(s => s.sol));
    cur.plan = pl;
    $("#zLadder").innerHTML = `<div class="muted">${pl.slices.length} slice · ${pl.bins} bin · harga → −${pl.realEnd.toFixed(1)}% · isi 1:2:…:${pl.slices.length} (paling dalam paling gede)${pl.wide ? ` · ${pl.wide} slice >70 bin = 1 posisi extended (rent lebih gede)` : ""}</div>
      <table class="ladder"><tr><th>#</th><th>range</th><th class="r">SOL</th><th></th></tr>
      ${pl.slices.map((s, i) => `<tr><td>${i + 1}</td><td>−${s.nearPct.toFixed(1)}% → −${s.farPct.toFixed(1)}% <span class="muted">(${s.width} bin)</span></td>
        <td class="r">${s.sol.toFixed(4)}</td><td><span class="lbar" style="width:${Math.round(s.sol / mx * 120)}px"></span></td></tr>`).join("")}</table>
      <div class="muted">Rent ±${(0.042 * pl.slices.length + (pl.slices.reduce((a, s) => a + Math.max(0, s.width - 70), 0)) * 0.0006).toFixed(3)} SOL buat ${pl.slices.length} posisi, balik pas close.</div>`;
  }
  draw();
  ["#zSize", "#zBottom", "#zSlices", "#zStrat"].forEach(s => { $(s).oninput = draw; });
  $("#zClose").onclick = () => $("#modal").classList.add("hidden");
  $("#zSkip").onclick = async () => {
    const reason = prompt("Alasan skip?", "");
    if (reason === null) return;
    await post("/api/decide", { pool: r.pool, symbol: r.symbol, decision: "skip", decisionReason: reason, sizeSol: cur.sz,
      level: r.verdict.level, gatesFailed: cur.gateFails, flags: cur.flags.map(f => f.slice(2)) });
    $("#zMsg").textContent = "Logged: skip → data/decisions.jsonl";
  };
  $("#zOpen").onclick = async () => {
    if (!trade.ready() || !cur.plan) return;
    const over = [...cur.gateFails, ...(cur.over ? ["di atas cap loss budget"] : [])];
    if (over.length && !confirm(`Spec v1 bilang jangan:\n${over.join("\n")}\n\nTetap buka? (dicatat sebagai override)`)) return;
    if (!confirm(`Buka ladder ${r.symbol}?\n${cur.sz} SOL · ${cur.plan.slices.length} slice ${cur.strategy} · harga → −${cur.plan.realEnd.toFixed(1)}%\nWallet ${short(trade.info.wallet)}. Ini kirim transaksi beneran.`)) return;
    const btn = $("#zOpen");
    btn.disabled = true;
    try {
      $("#zMsg").textContent = "Simulasi + kirim slice satu-satu (bisa 1–2 menit)…";
      const s = await trade.call("/api/trade/open", { pool: r.pool, symbol: r.symbol, sol: cur.sz, slices: cur.slices, bottom: cur.bottom, strategy: cur.strategy, override: over.length > 0 });
      const rows = (s.results || []).map(x => `slice ${x.slice}: ${x.ok ? "✓ " : "✗ "}${x.signature ? solscan(x.signature) : ""} ${x.error ? esc(x.error) : ""}`);
      $("#zMsg").innerHTML = rows.join("<br>") + (s.error ? `<br><span class="neg">✗ ${esc(s.error)}</span>` : "")
        + `<br>${s.ok ? "✓ Ladder kebuka." : `Kebuka ${(s.results || []).filter(x => x.ok).length} slice; sisanya tetap SOL di wallet.`}`;
      setTimeout(loadPortfolio, 4000);
    } catch (e) {
      $("#zMsg").innerHTML = `<span class="neg">✗ ${esc(e.message || e)}</span>`;
    } finally { btn.disabled = !trade.ready(); }
  };
}

// Close one position (remove 100%, claim fees, close account) with the key in .env.
async function closePosition(p, btn, rule) {
  if (!trade.ready()) { alert(trade.why()); return; }
  if (state.wallet !== trade.info.wallet) { alert(`Portfolio lagi nampilin wallet lain. Trading wallet: ${trade.info.wallet}`); return; }
  if (!confirm(`Close ${p.symbol} ${short(p.position)}?\nTarik 100% likuiditas + klaim fee + tutup akun (rent balik).\nSisa token TIDAK di-swap otomatis.`)) return;
  btn.disabled = true;
  const cell = btn.parentElement;
  try {
    cell.insertAdjacentHTML("beforeend", '<div class="res muted" data-r>closing…</div>');
    const s = await trade.call("/api/trade/close", { pool: p.pool, position: p.position, symbol: p.symbol, rule: rule || null });
    const sig = (s.signatures || [])[(s.signatures || []).length - 1];
    cell.querySelector("[data-r]").innerHTML = s.ok ? `✓ closed ${sig ? solscan(sig) : ""}` : `✗ ${esc(s.error || "gagal")}`;
    setTimeout(loadPortfolio, 4000);
  } catch (e) {
    const r = cell.querySelector("[data-r]");
    if (r) r.innerHTML = `<span class="neg">✗ ${esc(e.message || e)}</span>`;
  } finally { btn.disabled = false; }
}

// Close every slice of a ladder (one after another), no swap.
async function closeGroup(p, btn, group, rule) {
  if (group.length === 1) return closePosition(p, btn, rule);
  if (!trade.ready()) { alert(trade.why()); return; }
  if (state.wallet !== trade.info.wallet) { alert(`Portfolio lagi nampilin wallet lain. Trading wallet: ${trade.info.wallet}`); return; }
  if (!confirm(`Close ${p.symbol}: ${group.length} slice?\nTarik 100% likuiditas + klaim fee + tutup akun (rent balik).\nSisa token TIDAK di-swap (pakai Close+swap kalau mau dijual).`)) return;
  btn.disabled = true;
  const cell = btn.parentElement;
  cell.insertAdjacentHTML("beforeend", '<div class="res muted" data-r>closing…</div>');
  const r = cell.querySelector("[data-r]");
  let done = 0;
  try {
    for (const g of group) {
      r.textContent = `closing ${done + 1}/${group.length}…`;
      const s = await trade.call("/api/trade/close", { pool: g.pool, position: g.position, symbol: g.symbol, rule: rule || null });
      if (!s.ok) { r.innerHTML = `<span class="neg">✗ slice ${done + 1}: ${esc(s.error || "gagal")}</span>`; return; }
      done++;
    }
    r.innerHTML = `✓ ${done} slice closed`;
    setTimeout(loadPortfolio, 4000);
  } catch (e) { r.innerHTML = `<span class="neg">✗ ${esc(e.message || e)}</span>`; }
  finally { btn.disabled = false; }
}

// Cleanup: sell leftover tokens (worth >= 0.005 SOL, price impact <= 5%) and close empty token accounts for their rent.
async function cleanupWallet(btn) {
  if (!trade.ready()) { alert(trade.why()); return; }
  if (!confirm("CLEANUP wallet\n1) Jual semua sisa token yang nilainya ≥ 0.005 SOL ke SOL via Jupiter (batal per token kalau price impact > 5%). USDC/USDT/SOL nggak dijual.\n2) Tutup token account yang kosong (rent balik).\n\nToken yang kebeli di luar terminal ini juga ikut kejual. Lanjut?")) return;
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "cleanup…";
  try {
    const s = await trade.call("/api/trade/cleanup", {});
    const sold = (s.tokens || []).filter(t => t.status === "sold").length;
    alert(s.ok ? `Cleanup selesai: ${sold} token dijual, ${s.closed || 0} account kosong ditutup (rent ±◎${(s.rentSol || 0).toFixed(3)}).\n\n` + (s.tokens || []).map(t => `${t.mint.slice(0, 6)}… ${t.status}${t.sol ? ` (◎${t.sol.toFixed(4)})` : ""}`).join("\n") : "Gagal: " + (s.error || "?"));
    loadPortfolio();
  } catch (e) { alert("Gagal: " + (e.message || e)); }
  finally { btn.disabled = false; btn.textContent = old; }
}

// Close every position of a pool, sell the token that came out to SOL, and optionally open the ladder again at the
// current price (re-entry). Works in range too: the token that comes out is simply sold.
async function reenterPool(p, btn, reopen) {
  if (!trade.ready()) { alert(trade.why()); return; }
  if (state.wallet !== trade.info.wallet) { alert(`Portfolio lagi nampilin wallet lain. Trading wallet: ${trade.info.wallet}`); return; }
  const group = state.pf.rows.filter(x => x.pool === p.pool);
  const slices = group.length, bottom = Math.abs(Math.min(...group.map(x => x.rangeMin)) / Math.max(...group.map(x => x.rangeMax)) * 100 - 100) || 70;
  const msg = reopen
    ? `RE-ENTRY ${p.symbol}\n1) Close ${slices} posisi di pool ini (klaim fee)\n2) Jual sisa token ke SOL via Jupiter (batal kalau >5% di bawah harga pool)\n3) Buka ladder lagi di harga sekarang: ${slices} slice, sampai −${bottom.toFixed(0)}%, pakai semua SOL yang balik\n\nLanjut?`
    : `CLOSE + SWAP ${p.symbol}\nClose ${slices} posisi, lalu jual sisa token ke SOL via Jupiter (batal kalau >5% di bawah harga pool).\n\nLanjut?`;
  if (!confirm(msg)) return;
  btn.disabled = true;
  const cell = btn.parentElement;
  cell.insertAdjacentHTML("beforeend", '<div class="res muted" data-r>jalan… (bisa 1-3 menit)</div>');
  try {
    const s = await trade.call("/api/trade/reenter", { pool: p.pool, symbol: p.symbol, positions: group.map(x => x.position), reopen, slices, bottom });
    const r = cell.querySelector("[data-r]");
    r.innerHTML = s.ok ? `✓ ${reopen ? `re-entry ◎${s.reopenSol}` : `closed, balik ◎${(s.solBack || 0).toFixed(3)}`}` : `✗ ${esc(s.step || "")} ${esc(s.error || "gagal")}`;
    setTimeout(loadPortfolio, 4000);
  } catch (e) {
    const r = cell.querySelector("[data-r]");
    if (r) r.innerHTML = `<span class="neg">✗ ${esc(e.message || e)}</span>`;
  } finally { btn.disabled = false; }
}

// ---------- portfolio ----------
async function loadPortfolio() {
  const w = $("#wallet").value.trim();
  if (!w) return;
  state.wallet = w;
  store.set("wallet", w);
  $("#pfEmpty").classList.remove("hidden");
  $("#pfEmpty").textContent = "Loading positions…";
  try {
    state.pf = await getJson(`/api/portfolio?wallet=${encodeURIComponent(w)}`);
  } catch (e) {
    state.pf = { error: e.message };
  }
  renderPortfolio();
  renderTop();
}
function renderPortfolio() {
  const pf = state.pf;
  if (!pf) return;
  if (pf.error) { $("#pfEmpty").textContent = "Couldn't load: " + pf.error; $("#positions tbody").innerHTML = ""; return; }
  const pnlPct = pf.depositSol ? pf.pnlSol / pf.depositSol * 100 : null;
  $("#pfSummary").innerHTML = `<span>nilai <b>${mS(pf.valueSol, 3)}</b> <span class="muted">${mAlt(pf.valueSol)}</span></span>
    <span>fees unclaimed <b>${mS(pf.unclaimedSol, 4)}</b></span>
    <span>PnL <b class="${cls(pf.pnlSol)}">${mS(pf.pnlSol, 3)}</b> <span class="${cls(pnlPct)}">${fmtPct(pnlPct, 2, true)}</span> <span class="muted">${mAlt(pf.pnlSol)}</span></span>
    <span>deposit <b>${mS(pf.depositSol, 3)}</b></span>
    <span>wallet bebas <b>${mS(pf.walletSol, 3)}</b></span>
    <span class="muted">dicek ${new Date(pf.t * 1000).toLocaleTimeString()}</span>`;
  $("#pfEmpty").classList.toggle("hidden", pf.rows.length > 0);
  if (!pf.rows.length) $("#pfEmpty").textContent = "Nggak ada posisi DLMM SOL yang terbuka di wallet ini.";
  // One row per pool: a ladder of N slices is one position to look at, close and re-enter.
  const groups = new Map();
  for (const r of pf.rows) (groups.get(r.pool) || groups.set(r.pool, []).get(r.pool)).push(r);
  $("#positions tbody").innerHTML = [...groups.values()].map(rows => {
    const p = rows[0], n = rows.length;
    const top = Math.max(...rows.map(r => r.rangeMax || 0)), low = Math.min(...rows.map(r => r.rangeMin || Infinity));
    const price = p.price;
    const pv = top && price ? (price / top - 1) * 100 : null, bot = top && isFinite(low) ? (low / top - 1) * 100 : null;
    const pos = pv != null && bot ? Math.max(-4, Math.min(104, (1 - pv / bot) * 100)) : null;
    const ev = rows.flatMap(r => r.events || []);
    const exit = ev.find(e => e.kind === "close");
    const alerts = [...new Set(ev.filter(e => e.kind === "alert").map(e => e.msg))];
    const inRange = rows.some(r => r.inRange), allAbove = rows.every(r => r.price > r.rangeMax), below = rows.every(r => r.below);
    const holdsToken = rows.some(r => !r.solOnly) || inRange || below;
    const status = exit ? `<span class="badge WAIT">EXIT</span> <span class="neg">${esc(exit.msg)}</span>`
      : alerts.length ? `<span class="badge CAREFUL">ALERT</span> <span class="warn">${esc(alerts.join(" · "))}</span>`
      : below ? `<span class="badge CAREFUL">OUT</span> <span class="muted">di bawah range</span>`
      : `<span class="badge OPEN">OK</span> <span class="muted">${inRange ? "in range, earning" : "di atas range (masih SOL)"}</span>`;
    const dep = p.poolDepositSol || 0, pnlSol = p.poolPnlSol != null ? p.poolPnlSol : rows.reduce((a, r) => a + (r.pnlSol || 0), 0);
    const pnlPct = dep ? pnlSol / dep * 100 : p.pnlPct;
    const peak = Math.max(...rows.map(r => r.peakPnlPct || 0));
    const held = Math.max(...rows.map(r => r.heldMin || 0));
    const ids = rows.map(r => r.position).join(",");
    const val = p.poolBalancesSol, fee = p.poolUnclaimedSol;
    return `<tr>
      <td><span class="sym">${esc(p.symbol)}${pingOf(p.pool) ? `<span class="rank">📡#${pingOf(p.pool).rank}</span>` : ""}</span><span class="sub">${n} slice · ${held ? age(held / 60) : "--"}${holdsToken ? " · pegang token" : ""}</span></td>
      <td class="r"><span class="big ${cls(pnlPct)}">${fmtPct(pnlPct, 2, true)}</span><span class="sub">${mS(pnlSol, 4)} <span class="muted">${mAlt(pnlSol)}</span></span><span class="sub">nilai ${mS(val, 3)} · fee ${mS(fee, 4)} · peak ${fmtPct(peak, 2, true)}</span></td>
      <td><span class="${cls(pv)}">${fmtPct(pv, 1, true)}</span> <span class="muted">· bottom ${fmtPct(bot, 0)}</span>
        <div class="rbar"><div class="fill" style="left:0;right:0"></div><div class="third"></div>${pos != null ? `<div class="mk" style="left:${pos}%"></div>` : ""}</div></td>
      <td>${status}</td>
      <td><button class="btn ${exit ? "danger" : ""}" data-close="${ids}">${exit ? "Exit → close" : "Close"}</button>
        <button class="btn" data-swap="${ids}" title="Close semua slice + jual token ke SOL">Close+swap</button>
        <button class="btn go" data-reenter="${ids}" title="Close + jual token + buka ladder lagi di harga sekarang (juga kalau masih in range)">Re-entry</button>
        <a href="https://app.meteora.ag/dlmm/${p.pool}" target="_blank" rel="noopener">meteora ↗</a></td>
    </tr>`;
  }).join("");
  // browser notification once per new exit/alert message
  const seen = store.get("seenAlerts", {});
  for (const r of pf.rows) for (const e of r.events || []) {
    const k = r.position + "|" + e.rule;
    if (!seen[k]) { seen[k] = Date.now(); notify(`${e.kind === "close" ? "🔴 EXIT" : "⚠️ ALERT"} ${r.symbol}`, e.msg); }
  }
  for (const k of Object.keys(seen)) if (Date.now() - seen[k] > 3 * 864e5) delete seen[k];
  store.set("seenAlerts", seen);
  const x = pf.exitsCfg || {};
  $("#pfRules").textContent = `Exit rules: alert PnL ≤ ${x.alertAtPnlPct}% · alert 1/3 bawah range · close kalau ${x.closeBelowRangeAfterMin} menit di bawah range (SOL-only) · `
    + `TP ${x.takeProfitPct != null ? x.takeProfitPct : "off"} · SL ${x.stopLossPct != null ? x.stopLossPct : "off"}. Close di sini pakai key di .env (lu konfirmasi). Yang nutup sendiri 24 jam: "monitor --mode auto" di Mac.`;
}

// ---------- settings (⚙): everything the terminal decides with, saved in data/settings.json ----------
async function openSettings() {
  let st;
  try { st = await getJson("/api/settings"); } catch (e) { alert("Gagal ambil settings: " + e.message); return; }
  const fieldHtml = f => {
    const id = "s_" + f.path.replace(/\./g, "_");
    const chg = JSON.stringify(f.value) !== JSON.stringify(f.base);
    const title = `config.json: ${f.base == null ? "kosong" : f.base}`;
    let input;
    if (f.kind === "bool") input = `<input type="checkbox" id="${id}" ${f.value ? "checked" : ""}>`;
    else if (f.kind === "select") input = `<select id="${id}">${f.options.map(o => `<option ${o === f.value ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    else input = `<input id="${id}" value="${f.value == null ? "" : f.value}" placeholder="${f.kind === "numnull" ? "off" : ""}" inputmode="decimal">`;
    return `<label class="${chg ? "chg" : ""}" title="${esc(title)}">${esc(f.label)}${chg ? " •" : ""} ${input}</label>`;
  };
  const groups = [...new Set(st.form.map(f => f.group))];
  $("#modalCard").classList.add("wide");
  $("#modalCard").innerHTML = `<h3>⚙ Settings <span class="muted">dipakai web + terminal (monitor, signal, open)</span></h3>
    <div class="sgrid">${groups.map(g => `<h4>${esc(g.toUpperCase())}</h4>` + st.form.filter(f => f.group === g).map(fieldHtml).join("")).join("")}</div>
    <div class="note" style="padding:8px 0 0">• = beda dari config.json (hover buat lihat nilai asal). Kosong di kolom "off" = mati / tanpa batas.
      TP/SL otomatis sengaja mati di spec: di data 133 posisi semuanya lebih jelek dari exit manual.</div>
    <div class="acts"><button class="btn go" id="sSave">Simpan</button><button class="btn" id="sReset">Reset ke config.json</button>
      <span class="spacer"></span><button class="btn" id="sClose">Tutup</button></div><div class="res" id="sMsg"></div>`;
  $("#modal").classList.remove("hidden");
  const close = () => { $("#modal").classList.add("hidden"); $("#modalCard").classList.remove("wide"); };
  $("#sClose").onclick = close;
  const send = async body => {
    try {
      const res = await getJson("/api/settings", { method: "POST", headers: { "Content-Type": "application/json", "X-DLMM-Trade": "1" },
        body: JSON.stringify(Object.assign(body, { pin: trade.pin })) });
      if (!res.ok) throw new Error(res.error);
      $("#sMsg").innerHTML = `<span class="pos">✓ Tersimpan.</span> Pool + PING di-scan ulang…`;
      try { state.cfg = await getJson("/api/config"); } catch (e) {}
      loadPools(true);
      loadPing(true);
      setTimeout(() => loadPing(), 3000);
      if (state.wallet) loadPortfolio();
      setTimeout(close, 900);
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
    if (!Object.keys(changes).length) return close();
    send({ changes });
  };
  $("#sReset").onclick = () => { if (confirm("Balikin semua ke config.json?")) send({ reset: true }); };
}
function setUnit(u) {
  state.unit = u;
  store.set("unit", u);
  document.querySelectorAll("#unit button").forEach(b => b.classList.toggle("on", b.dataset.u === u));
  renderTop();
  if (state.data) renderPools();
  if (state.pf) renderPortfolio();
  if (state.sel && state.data) { const r = state.data.rows.find(x => x.pool === state.sel); if (r) renderDetail(r, true); }
}
function setMode(m) {
  state.mode = m;
  store.set("mode", m);
  document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.m === m));
  if (m === "ping" && !state.ping) loadPing();
  if (state.data) renderPools();
}
function paintBell() {
  const b = $("#bellBtn");
  b.textContent = state.notify ? "🔔" : "🔕";
  b.title = state.notify ? "Notifikasi nyala (PING baru, exit alert). Klik buat matiin." : "Nyalain notifikasi browser: PING baru + exit alert";
}

// ---------- wiring ----------
function setView(v) {
  state.view = v;
  document.querySelectorAll(".tab").forEach(b => b.classList.toggle("on", b.dataset.view === v));
  $("#viewPools").classList.toggle("hidden", v !== "pools");
  $("#viewPortfolio").classList.toggle("hidden", v !== "portfolio");
  store.set("view", v);
  if (v === "portfolio" && !state.pf && $("#wallet").value.trim()) loadPortfolio();
}
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
  if (!st || !state.data) return;
  e.stopPropagation();
  const r = state.data.rows.find(x => x.pool === st.dataset.star);
  toggleWatch(st.dataset.star, r ? r.symbol : "");
}, true);
["#q", "#hideSkip"].forEach(s => $(s).addEventListener("input", () => {
  store.set("filters", { skip: $("#hideSkip").checked });
  if (state.data) renderPools();
}));
$("#tvl").onchange = () => { store.set("tvl", $("#tvl").value); loadPools(); };
$("#pools tbody").addEventListener("click", e => {
  const tr = e.target.closest("tr");
  if (!tr || !state.data) return;
  const r = state.data.rows.find(x => x.pool === tr.dataset.pool);
  const z = e.target.closest("[data-zap]");
  if (z) return openZap(r, z.dataset.zap === "ask" ? state.data.sizeSol : parseFloat(z.dataset.zap));
  renderDetail(r);
});
$("#detail").addEventListener("click", async e => {
  const z = e.target.closest("[data-zap]");
  const r = state.data && state.data.rows.find(x => x.pool === state.sel);
  if (z && r) return openZap(r, z.dataset.zap === "ask" ? state.data.sizeSol : parseFloat(z.dataset.zap));
  const c = e.target.closest("[data-copy]");
  if (c) { try { await navigator.clipboard.writeText(c.dataset.copy); c.textContent = "copied ✓"; } catch (err) {} }
});
const closeModal = () => { $("#modal").classList.add("hidden"); $("#modalCard").classList.remove("wide"); };
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", e => {
  if (e.key === "Escape") return closeModal();
  if (/INPUT|SELECT|TEXTAREA/.test((e.target && e.target.tagName) || "")) return;
  if (e.key === "/") { e.preventDefault(); setView("pools"); $("#q").focus(); }        // search
  else if (e.key === "u") setUnit(state.unit === "sol" ? "usd" : "sol");                // unit
  else if (e.key >= "1" && e.key <= "4") setMode(["all", "pass", "ping", "watch"][+e.key - 1]);
});
$("#stAge").onclick = () => loadPools(true);
$("#keyBtn").onclick = () => alert(trade.ready()
  ? `Trading wallet: ${trade.info.wallet}${trade.info.pin ? "\nPIN aktif (DLMM_WEB_PIN)." : "\nTrading cuma dari Mac ini."}`
  : trade.why() + "\n\nDi dlmm_terminal/.env:\nDLMM_PRIVATE_KEY=<private key base58 dari Phantom>\nSOLANA_RPC_URL=<Helius RPC>\nOpsional: DLMM_WEB_PIN=<PIN> buat trading dari HP.");
$("#positions tbody").addEventListener("click", e => {
  const sw = e.target.closest("[data-swap],[data-reenter]");
  const cl = e.target.closest("[data-close]");
  const b = sw || cl;
  if (!b || !state.pf) return;
  const ids = (b.dataset.swap || b.dataset.reenter || b.dataset.close).split(",");
  const row = state.pf.rows.find(x => x.position === ids[0]);
  if (!row) return;
  if (sw) return reenterPool(row, sw, !!sw.dataset.reenter);
  closeGroup(row, b, state.pf.rows.filter(x => x.pool === row.pool), (row.events || []).some(e => e.kind === "close") ? "exit" : null);
});
$("#cleanupBtn").onclick = () => cleanupWallet($("#cleanupBtn"));
$("#loadWallet").onclick = loadPortfolio;
$("#wallet").addEventListener("keydown", e => { if (e.key === "Enter") loadPortfolio(); });

(async function init() {
  window.__dlmmStarted = true;
  state.win = store.get("win", "24h");
  document.querySelectorAll("#win button").forEach(x => x.classList.toggle("on", x.dataset.w === state.win));
  state.sort = store.get("sort", state.sort);
  const f = store.get("filters", null);
  if (f) $("#hideSkip").checked = f.skip !== false;
  state.unit = store.get("unit", "sol");
  document.querySelectorAll("#unit button").forEach(b => b.classList.toggle("on", b.dataset.u === state.unit));
  state.mode = store.get("mode", f && f.pass ? "pass" : "all");
  document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.m === state.mode));
  state.notify = store.get("notify", false) && "Notification" in window && Notification.permission === "granted";
  paintBell();
  state.watch = store.get("watch", []);
  $("#tvl").value = store.get("tvl", "2000");
  $("#poolsEmpty").classList.remove("hidden");
  const pools = loadPools();                       // don't wait on anything else first
  try { state.cfg = await getJson("/api/config"); } catch (e) {}
  const tw = state.cfg && state.cfg.trade && state.cfg.trade.wallet;
  $("#wallet").value = tw || store.get("wallet", "") || (state.cfg && state.cfg.wallet) || "";
  try { setView(store.get("view", "pools")); } catch (e) { setView("pools"); }
  trade.info = state.cfg && state.cfg.trade;
  paintWallet();
  await pools;
  loadWatch();
  loadPing();
  if ($("#wallet").value.trim()) loadPortfolio();
  setInterval(() => loadPing(), 5 * 60000);                    // PING: server rebuilds every 5 min
  setInterval(() => loadPools(), 60000);                       // pools: every minute (server caches 60s)
  setInterval(() => { if (state.wallet) loadPortfolio(); }, 300000);   // positions + exit rules: every 5 min
  setInterval(tickAge, 5000);
})();
