// DLMM Web: pools + spec v1 entry check + detail/chart + portfolio exits.
// Talks only to the local server (web.py); nothing here holds a key.
"use strict";

const $ = s => document.querySelector(s);
const state = {
  view: "pools", win: "24h", sort: { key: "fees", desc: true }, data: null, sel: null,
  pf: null, cfg: null, wallet: "", loadedAt: 0,
};
const store = {
  get(k, d) { try { const v = localStorage.getItem("dlmmweb." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("dlmmweb." + k, JSON.stringify(v)); } catch (e) {} },
};

// ---------- formatting ----------
const fmtUsd = v => {
  if (v == null || !isFinite(v)) return "--";
  const a = Math.abs(v);
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return "$" + (v / 1e3).toFixed(a >= 1e5 ? 1 : 2) + "k";
  if (a >= 1) return "$" + v.toFixed(2);
  if (a === 0) return "$0";
  return "$" + v.toPrecision(3);
};
const fmtPct = (v, d = 1, sign = false) => v == null || !isFinite(v) ? "--" : (sign && v > 0 ? "+" : "") + v.toFixed(d) + "%";
const fmtSol = (v, d = 3) => v == null || !isFinite(v) ? "--" : "◎" + Number(v).toFixed(d);
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
async function getJson(url, opt) {
  const r = await fetch(url, opt);
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
    if (state.sel) {
      const r = state.data.rows.find(x => x.pool === state.sel);
      if (r) renderDetail(r, true);
    }
  } catch (e) {
    $("#poolsEmpty").classList.remove("hidden");
    $("#poolsEmpty").textContent = "Couldn't load pools: " + e.message;
  }
}

function rowsView() {
  const d = state.data;
  if (!d) return [];
  const q = $("#q").value.trim().toLowerCase();
  let rows = d.rows.filter(r => {
    if ($("#passOnly").checked && !["OPEN", "CAREFUL"].includes(r.verdict.level)) return false;
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
    $("#stCap").textContent = `max ${fmtSol(d.maxPositionSol, 2)} / posisi`;
  }
  const pf = state.pf;
  if (pf && !pf.error) {
    const usd = d && d.solUsd;
    $("#stNet").innerHTML = `posisi <b>${fmtSol(pf.valueSol, 2)}</b>${usd ? ` <span class="muted">${fmtUsd(pf.valueSol * usd)}</span>` : ""}`;
    $("#stFees").innerHTML = `fees unclaimed <b>${fmtSol(pf.unclaimedSol, 3)}</b>`;
    const n = pf.rows.length;
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
function renderPools() {
  const rows = rowsView(), w = state.win;
  const d = state.data;
  $("#count").textContent = `${rows.length} of ${d.rows.length} pools · Meteora DLMM`;
  $("#poolsEmpty").classList.toggle("hidden", rows.length > 0);
  if (!rows.length) $("#poolsEmpty").textContent = "No pool matches the filters.";
  document.querySelectorAll("#pools th").forEach(th => th.classList.toggle("sorted", th.dataset.sort === state.sort.key));
  document.querySelector("#pools th[data-sort=fees] .wl").textContent = w.toUpperCase();
  const size = d.sizeSol;
  $("#pools tbody").innerHTML = rows.map(r => {
    const v = r.verdict;
    const reasons = [...v.reasons.filter(x => !x.ok), ...v.info].slice(0, 2).map(x => nice(x.text)).join(" · ")
      || v.reasons.filter(x => x.ok).map(x => "gates pass").join("");
    const fee1hPct = r.tvlUsd ? r.fees["1h"] / r.tvlUsd * 100 : null;
    const icon = r.icon ? `<img src="${esc(r.icon)}" alt="" loading="lazy">` : `<span class="ph"></span>`;
    return `<tr data-pool="${r.pool}" class="${r.pool === state.sel ? "sel" : ""}">
      <td><span class="sym">${icon}${esc(r.symbol)}</span>
        <span class="sub">${r.binStep} · ${r.baseFeePct}% · ${age(r.poolAgeHours)} · <span class="${cls(r.priceChg24h)}">${fmtPct(r.priceChg24h, 0, true)} 24h</span> · …${r.pool.slice(-6)}</span></td>
      <td class="r"><span class="big">${fmtUsd(r.tvlUsd)}</span><span class="sub">vol ${fmtUsd(r.vol24)}</span></td>
      <td class="r"><span class="big pos">${fmtUsd(r.fees[w])}</span><span class="sub">${fmtPct(r.feePctDay[w], 2)}/day</span></td>
      <td class="r"><span class="big pos">${fmtPct(fee1hPct, 2)}</span><span class="sub">${fmtUsd(r.fees["1h"])} / jam</span></td>
      <td class="r"><span class="big">${fmtUsd(r.mcapUsd)}</span><span class="sub">${spark(r)} <span class="${cls(r.priceChg24h)}">${fmtPct(r.priceChg24h, 0, true)}</span></span></td>
      <td><span class="badge ${v.level}">● ${v.level} ${v.score > 0 ? "+" : ""}${v.score}</span><span class="reasons">${esc(reasons)}</span></td>
      <td><div class="zap">
        <button data-zap="${size / 2}">⚡◎${+(size / 2).toFixed(2)}</button>
        <button data-zap="${size}">⚡◎${+size.toFixed(2)}</button>
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
    <div class="dh"><span class="sym">${r.icon ? `<img src="${esc(r.icon)}" alt="">` : ""}<span class="name">${esc(r.symbol)}</span></span>
      <span class="muted">${r.binStep} · ${r.baseFeePct}%</span>
      <span class="addr" title="copy pool address" data-copy="${r.pool}">${short(r.pool)} ⧉</span>
      <span class="spacer"></span>
      <a href="https://app.meteora.ag/dlmm/${r.pool}" target="_blank" rel="noopener">meteora ↗</a>
      <a href="https://dexscreener.com/solana/${r.pool}" target="_blank" rel="noopener">dex ↗</a></div>
    <div class="stats">
      <div><label class="muted">harga</label><b>${fmtUsd(r.priceUsd)}</b><span class="${cls(r.priceChg1h)}">${fmtPct(r.priceChg1h, 1, true)} 1j</span></div>
      <div><label class="muted">mcap</label><b>${fmtUsd(r.mcapUsd)}</b><span class="${cls(r.priceChg24h)}">${fmtPct(r.priceChg24h, 0, true)} 24j</span></div>
      <div><label class="muted">TVL</label><b>${fmtUsd(r.tvlUsd)}</b><span class="muted">umur ${age(r.poolAgeHours)}</span></div>
      <div><label class="muted">fee / hari</label><b class="pos">${fmtPct(r.feePctDay["24h"], 2)}</b><span class="muted">APR ${Math.round(r.feeApr24h).toLocaleString()}%</span></div>
    </div>
    <div class="vbox ${v.level}"><span class="lv">${v.level}</span>${label} <span class="muted">skor ${v.score > 0 ? "+" : ""}${v.score}</span></div>
    <ul class="rs">${li}</ul>
    <div class="zapbig">
      <button data-zap="${size / 2}">⚡ ◎${+(size / 2).toFixed(2)} · Spot −70%</button>
      <button data-zap="${size}">⚡ ◎${+size.toFixed(2)} · Spot −70%</button>
      <button class="edit" data-zap="ask">✏️ custom</button>
    </div>
    <div class="note">Zap = plan + log. Posisi dibuka di Meteora atau MET Pool di HP; web ini nggak pegang key.
      Skor cuma konteks: spec nggak nemu sinyal entry yang tahan di token baru, jadi level ditentuin gate + flag aja.</div>
    <div class="chart"><iframe src="${chart}" title="chart" loading="lazy"></iframe></div>`;
  if (old && old.querySelector("iframe").src === chart) $("#detail .chart").replaceWith(old);
}

// ---------- zap modal ----------
function openZap(r, size) {
  const d = state.data, maxSol = d.maxPositionSol;
  const draw = () => {
    const sz = parseFloat($("#zSize").value) || 0, bottom = parseFloat($("#zBottom").value) || 70;
    const sizePct = r.tvlUsd ? sz * d.solUsd / r.tvlUsd * 100 : null;
    const warn = [];
    const gateFails = r.verdict.reasons.filter(x => !x.ok && !x.flag && !/^sizeVsTvl/.test(x.text)).map(x => nice(x.text));
    if (sizePct != null && sizePct > 3) gateFails.push(`Size ${sizePct.toFixed(1)}% of TVL (max 3%)`);
    if (sz > maxSol + 1e-9) warn.push(`⛔ ${fmtSol(sz, 2)} di atas cap loss budget ${fmtSol(maxSol, 2)}`);
    if (bottom < 65) warn.push(`⚠ Range bottom −${bottom}%: spec pakai −70% (semua rugi besar jatuh 44–58%)`);
    const flags = r.verdict.reasons.filter(x => x.flag).map(x => "⚠ " + nice(x.text));
    const bottomPrice = r.priceSol ? r.priceSol * (1 - bottom / 100) : null;
    const plan = [`${r.symbol} · ${r.pool}`, `Spot · SOL-only · ${fmtSol(sz, 3)} (≈ ${fmtUsd(sz * d.solUsd)})`,
      `Range: harga sekarang → −${bottom}%${bottomPrice ? ` (${bottomPrice.toExponential(3)} SOL)` : ""}`,
      `Size vs TVL: ${fmtPct(sizePct, 2)}`, `Exit: alert PnL −10% · close kalau 10 menit di bawah range · TP/SL off`].join("\n");
    $("#zBody").innerHTML = `
      <div class="vbox ${gateFails.length ? "WAIT" : flags.length ? "CAREFUL" : "OPEN"}" style="margin:0 0 10px">
        <span class="lv">${gateFails.length ? "SKIP?" : flags.length ? "CAREFUL" : "OPEN"}</span>
        ${gateFails.length ? esc(gateFails.join(" · ")) : "Gates lolos buat size ini."}</div>
      ${[...warn, ...flags].map(x => `<div class="warn">${esc(x)}</div>`).join("")}
      <pre id="zPlan">${esc(plan)}</pre>`;
    return { sz, bottom, plan, gateFails, flags };
  };
  $("#modalCard").innerHTML = `
    <h3>⚡ Zap ${esc(r.symbol)} <span class="muted">bin ${r.binStep}</span></h3>
    <div class="acts"><label>SOL <input id="zSize" value="${+size.toFixed(4)}"></label>
      <label>range bottom −% <input id="zBottom" value="70"></label></div>
    <div id="zBody" style="margin-top:10px"></div>
    <div class="acts">
      <a class="btn primary" href="https://app.meteora.ag/dlmm/${r.pool}" target="_blank" rel="noopener">Open di Meteora ↗</a>
      <button class="btn" id="zCopy">Copy plan</button>
      <button class="btn" id="zEnter">Log: enter</button>
      <button class="btn" id="zSkip">Log: skip</button>
      <span class="spacer"></span><button class="btn" id="zClose">Tutup</button>
    </div><div class="note" id="zMsg" style="padding:8px 0 0"></div>`;
  $("#modal").classList.remove("hidden");
  let cur = draw();
  $("#zSize").oninput = $("#zBottom").oninput = () => { cur = draw(); };
  const log = async decision => {
    const reason = decision === "skip" ? prompt("Alasan skip?", "") : "";
    if (reason === null) return;
    await getJson("/api/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      pool: r.pool, symbol: r.symbol, decision, decisionReason: reason || `size ${cur.sz} SOL, bottom −${cur.bottom}%`,
      sizeSol: cur.sz, level: r.verdict.level, gatesFailed: cur.gateFails, flags: cur.flags.map(f => f.slice(2)) }) });
    $("#zMsg").textContent = `Logged: ${decision} → data/decisions.jsonl`;
  };
  $("#zEnter").onclick = () => log("enter");
  $("#zSkip").onclick = () => log("skip");
  $("#zCopy").onclick = async () => { try { await navigator.clipboard.writeText(cur.plan); $("#zMsg").textContent = "Plan copied."; } catch (e) { $("#zMsg").textContent = "Copy blocked by the browser."; } };
  $("#zClose").onclick = () => $("#modal").classList.add("hidden");
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
  const usd = state.data && state.data.solUsd;
  $("#pfSummary").innerHTML = `<span>nilai <b>${fmtSol(pf.valueSol, 3)}</b>${usd ? ` (${fmtUsd(pf.valueSol * usd)})` : ""}</span>
    <span>fees unclaimed <b>${fmtSol(pf.unclaimedSol, 4)}</b></span>
    <span>PnL <b class="${cls(pf.pnlSol)}">${fmtSol(pf.pnlSol, 3)}</b></span>
    <span>deposit <b>${fmtSol(pf.depositSol, 3)}</b></span>
    <span class="muted">dicek ${new Date(pf.t * 1000).toLocaleTimeString()}</span>`;
  $("#pfEmpty").classList.toggle("hidden", pf.rows.length > 0);
  if (!pf.rows.length) $("#pfEmpty").textContent = "Nggak ada posisi DLMM SOL yang terbuka di wallet ini.";
  $("#positions tbody").innerHTML = pf.rows.map(p => {
    const pv = p.priceVsTopPct, bot = p.rangeBottomPct;
    // range bar: left = bottom of the range, right = top (entry); marker = price
    const pos = pv != null && bot ? Math.max(-4, Math.min(104, (1 - pv / bot) * 100)) : null;
    const ev = p.events || [];
    const exit = ev.find(e => e.kind === "close");
    const alerts = ev.filter(e => e.kind === "alert").map(e => e.msg);
    const status = exit ? `<span class="badge WAIT">EXIT</span> <span class="neg">${esc(exit.msg)}</span>`
      : alerts.length ? `<span class="badge CAREFUL">ALERT</span> <span class="warn">${esc(alerts.join(" · "))}</span>`
      : p.below ? `<span class="badge CAREFUL">OUT</span> <span class="muted">di bawah range</span>`
      : `<span class="badge OPEN">OK</span> <span class="muted">${p.inRange ? "in range, earning" : "di atas range (masih SOL)"}</span>`;
    return `<tr>
      <td><span class="sym">${esc(p.symbol)}</span><span class="sub">${short(p.position)} · ${p.heldMin != null ? age(p.heldMin / 60) : "--"}${p.solOnly ? "" : " · two-sided"}</span></td>
      <td class="r"><span class="big ${cls(p.pnlPct)}">${fmtPct(p.pnlPct, 2, true)}</span><span class="sub">${fmtSol(p.pnlSol, 4)} · peak ${fmtPct(p.peakPnlPct, 2, true)}</span></td>
      <td><span class="${cls(pv)}">${fmtPct(pv, 1, true)}</span> <span class="muted">· bottom ${fmtPct(bot, 0)}</span>
        <div class="rbar"><div class="fill" style="left:0;right:0"></div><div class="third"></div>${pos != null ? `<div class="mk" style="left:${pos}%"></div>` : ""}</div></td>
      <td>${status}</td>
      <td><a href="https://app.meteora.ag/dlmm/${p.pool}" target="_blank" rel="noopener">meteora ↗</a></td>
    </tr>`;
  }).join("");
  const x = pf.exitsCfg || {};
  $("#pfRules").textContent = `Exit rules: alert PnL ≤ ${x.alertAtPnlPct}% · alert 1/3 bawah range · close kalau ${x.closeBelowRangeAfterMin} menit di bawah range (SOL-only) · `
    + `TP ${x.takeProfitPct ?? "off"} · SL ${x.stopLossPct ?? "off"}. Web ini cuma nunjukin; close dari MET Portfolio, Meteora, atau "monitor --mode auto".`;
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
["#q", "#passOnly", "#hideSkip"].forEach(s => $(s).addEventListener("input", () => {
  store.set("filters", { pass: $("#passOnly").checked, skip: $("#hideSkip").checked });
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
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });
document.addEventListener("keydown", e => { if (e.key === "Escape") $("#modal").classList.add("hidden"); });
$("#stAge").onclick = () => loadPools(true);
$("#loadWallet").onclick = loadPortfolio;
$("#wallet").addEventListener("keydown", e => { if (e.key === "Enter") loadPortfolio(); });

(async function init() {
  state.win = store.get("win", "24h");
  document.querySelectorAll("#win button").forEach(x => x.classList.toggle("on", x.dataset.w === state.win));
  state.sort = store.get("sort", state.sort);
  const f = store.get("filters", null);
  if (f) { $("#passOnly").checked = !!f.pass; $("#hideSkip").checked = f.skip !== false; }
  $("#tvl").value = store.get("tvl", "2000");
  try { state.cfg = await getJson("/api/config"); } catch (e) {}
  $("#wallet").value = store.get("wallet", "") || (state.cfg && state.cfg.wallet) || "";
  setView(store.get("view", "pools"));
  $("#poolsEmpty").classList.remove("hidden");
  await loadPools();
  if ($("#wallet").value.trim()) loadPortfolio();
  setInterval(() => loadPools(), 60_000);                       // pools: every minute (server caches 60s)
  setInterval(() => { if (state.wallet) loadPortfolio(); }, 300_000);   // positions + exit rules: every 5 min
  setInterval(tickAge, 5_000);
})();
