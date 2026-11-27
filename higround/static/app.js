/* HiGround Run Club: one small page app. Hash routes: #/ #/leaderboard #/me #/runner/ID #/admin */
"use strict";

const $app = document.getElementById("app");
const $nav = document.getElementById("nav");
const $tip = document.getElementById("tip");
let ME = null, CLUB = "HIGROUND RUN CLUB", DEMO = false, CAN_LOGIN = false;
const state = { period: "month", gender: "all", metric: "distance" };

// ── helpers ─────────────────────────────────────────────────────────────────
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const km = (v) => (v == null ? "–" : Number(v).toLocaleString("id-ID", { maximumFractionDigits: 1 }));
const pace = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}` : "–");
const dur = (s) => { s = Math.round(s || 0); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? `${h}j ${m}m` : `${m}m`; };
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
const DAYS = ["Min", "Sen", "Sel", "Rab", "Kam", "Jum", "Sab"];
const dshort = (iso) => { const d = new Date(iso + "T00:00:00"); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
const dlong = (iso) => { const d = new Date(iso.slice(0, 10) + "T00:00:00"); return `${DAYS[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`; };
const ago = (t) => { if (!t) return "belum pernah"; const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? "barusan" : m < 60 ? `${m} menit lalu` : m < 1440 ? `${Math.round(m / 60)} jam lalu` : `${Math.round(m / 1440)} hari lalu`; };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(d.error || `HTTP ${r.status}`), { status: r.status });
  return d;
}

function seg(name, options, current) {
  return `<div class="seg" role="group" aria-label="${esc(name)}">${options.map(([v, l]) =>
    `<button type="button" data-seg="${esc(name)}" data-v="${esc(v)}" class="${v === current ? "on" : ""}" aria-pressed="${v === current}">${esc(l)}</button>`).join("")}</div>`;
}

function tipShow(e, html) {
  $tip.innerHTML = html; $tip.hidden = false;
  const r = $tip.getBoundingClientRect();
  const p = e.touches ? e.touches[0] : e;
  let x = (p.clientX ?? 0) + 14, y = (p.clientY ?? 0) - r.height - 10;
  if (p.clientX === undefined && e.target.getBoundingClientRect) { const b = e.target.getBoundingClientRect(); x = b.left + b.width / 2; y = b.top - r.height - 8; }
  x = Math.min(x, innerWidth - r.width - 8); y = Math.max(8, y);
  $tip.style.left = x + "px"; $tip.style.top = y + "px";
}
const tipHide = () => { $tip.hidden = true; };
function bindTips(root) {
  root.querySelectorAll("[data-tip]").forEach((el) => {
    const h = (e) => tipShow(e, el.getAttribute("data-tip"));
    el.addEventListener("mousemove", h); el.addEventListener("focus", h);
    el.addEventListener("mouseleave", tipHide); el.addEventListener("blur", tipHide);
    el.addEventListener("touchstart", h, { passive: true });
  });
}
document.addEventListener("scroll", tipHide, { passive: true });

function loginButton(label = "Connect intervals.icu") {
  if (!CAN_LOGIN) return `<span class="muted">Login belum disetel admin.</span>`;
  return `<a class="btn" href="/auth/login">${esc(label)} →</a>`;
}

// ── nav & routing ───────────────────────────────────────────────────────────
function renderNav(route) {
  const link = (href, label, key) => `<a href="${href}" class="${route === key ? "on" : ""}">${label}</a>`;
  let h = link("#/leaderboard", "Leaderboard", "leaderboard");
  if (ME) {
    h += link("#/me", "Dashboard", "me");
    if (ME.is_admin) h += link("#/admin", "Admin", "admin");
  } else if (CAN_LOGIN) {
    h += `<a href="${DEMO ? "/auth/demo" : "/auth/login"}">Masuk</a>`;
  }
  $nav.innerHTML = h;
}

async function route() {
  tipHide();
  const [path, query] = location.hash.replace(/^#/, "").split("?");
  const q = new URLSearchParams(query || "");
  const parts = (path || "/").split("/").filter(Boolean);
  const key = parts[0] || "home";
  renderNav(key);
  $app.innerHTML = `<p class="skeleton">Memuat…</p>`;
  try {
    if (key === "leaderboard") await pageBoard();
    else if (key === "me") ME ? await pageDash(null, q) : pageNeedLogin();
    else if (key === "runner") ME ? await pageDash(Number(parts[1]), q) : pageNeedLogin();
    else if (key === "admin") ME && ME.is_admin ? await pageAdmin() : pageNeedLogin();
    else await pageHome(q);
  } catch (e) {
    if (e.status === 401) { ME = null; pageNeedLogin(); }
    else $app.innerHTML = `<div class="note err">Gagal memuat: ${esc(e.message)}</div>`;
  }
  $app.focus({ preventScroll: true });
}
addEventListener("hashchange", () => { scrollTo(0, 0); route(); });

// ── home ────────────────────────────────────────────────────────────────────
async function pageHome(q) {
  const b = await api("/api/leaderboard?period=month");
  const top = b.rows.slice(0, 3);
  const msg = { denied: "Lu batal ngasih akses di intervals.icu.", expired: "Login kelamaan, coba lagi ya.", failed: "Login ke intervals.icu gagal. Coba lagi bentar lagi." }[q.get("login")]
    ?.concat(q.get("why") ? ` (kode: ${q.get("why")})` : "");
  $app.innerHTML = `
    ${msg ? `<div class="note err" role="alert">${esc(msg)}</div>` : ""}
    <section class="hero">
      <div>
        <p class="eyebrow">Bekasi · Harapan Indah</p>
        <h1>${esc(CLUB)}<span class="hand">lari bareng, ngopi bareng.</span></h1>
        <p class="lead">Connect jam lu sekali. Tiap habis lari, kilometer lu otomatis masuk leaderboard klub dan dashboard pribadi lu.</p>
        <div class="row-actions">${ME ? `<a class="btn" href="#/me">Dashboard gue →</a>` : (DEMO ? `<a class="btn" href="/auth/demo">Coba demo →</a>` : loginButton())}
          <a class="btn ghost" href="#/leaderboard">Lihat leaderboard</a></div>
      </div>
      <div class="poster" aria-label="Total klub bulan ini">
        <span class="hand">bulan ini kita udah lari</span>
        <div class="big num">${km(b.club.km)} km</div>
        <div class="row"><span>${b.rows.length} runner aktif</span><span>${b.club.runs} sesi</span><span>${esc(b.label)}</span></div>
      </div>
    </section>
    ${top.length ? `<section class="section"><h2>Top 3 <span class="hand">${esc(b.label)}</span></h2>${podium(top, "distance")}</section>` : ""}
    <section class="section steps">
      <h2>Cara gabung</h2>
      <div class="grid g4" style="grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin-top:28px">
        <div class="card"><h3>Bikin akun intervals.icu</h3><p>Gratis. Daftar di intervals.icu, lalu connect jam lu: Garmin, COROS, Polar, Suunto, atau Wahoo.</p></div>
        <div class="card"><h3>Connect ke HiGround</h3><p>Klik tombol connect di sini, login intervals.icu, terus pilih <b>Allow</b>. Cukup sekali.</p></div>
        <div class="card"><h3>Lari aja</h3><p>Habis sync jam, run lu otomatis masuk dalam ±30 menit. Nggak perlu upload apa-apa.</p></div>
      </div>
      <p class="muted" style="margin-top:16px">Catatan: run yang masuk ke intervals.icu lewat Strava nggak bisa dibaca (aturan Strava). Connect jam lu langsung ke intervals.icu ya.</p>
    </section>`;
}

function podium(top, metric) {
  return `<div class="podium">${top.map((r, i) => `
    <a class="pod p${i + 1}" href="#/runner/${r.member_id}">
      <span class="place">#${i + 1}</span>
      <span class="who">${esc(r.name)}</span>
      <span class="val num">${metricVal(r, metric)}</span>
      <span class="sub">${r.runs} run · Lv ${r.level}</span>
    </a>`).join("")}</div>`;
}

function metricVal(r, metric) {
  if (metric === "days") return `${r.days} hari`;
  if (metric === "elevation") return `${km(r.elevation)} m`;
  return `${km(r.km)} km`;
}

// ── leaderboard ─────────────────────────────────────────────────────────────
async function pageBoard() {
  const b = await api(`/api/leaderboard?period=${state.period}&gender=${state.gender}&metric=${state.metric}`);
  const rest = b.rows.slice(3);
  const metricLabel = { distance: "Jarak total", days: "Hari aktif (konsistensi)", elevation: "Elevasi" }[state.metric];
  $app.innerHTML = `
    <p class="eyebrow">Komunitas</p>
    <h1>Leaderboard</h1>
    <div class="filters">
      ${seg("gender", [["all", "Semua"], ["F", "Perempuan"], ["M", "Laki-laki"]], state.gender)}
      ${seg("period", [["week", "Minggu"], ["month", "Bulan"], ["all", "All time"]], state.period)}
    </div>
    <div class="filters" style="margin-top:0">
      ${seg("metric", [["distance", "Jarak"], ["days", "Konsistensi"], ["elevation", "Elevasi"]], state.metric)}
      <div class="clubbar"><span class="muted">${esc(b.label)} · klub</span><b class="num">${km(b.club.km)} km</b><span class="muted">${b.club.runs} sesi</span></div>
    </div>
    <p class="muted" style="margin:4px 0 12px">Diurutkan berdasarkan ${metricLabel.toLowerCase()}.</p>
    ${b.rows.length ? podium(b.rows.slice(0, 3), state.metric) : `<div class="idle">Belum ada yang lari di periode ini. Jadi yang pertama!</div>`}
    ${rest.length ? `<ol class="board" start="4">${rest.map((r) => `
      <li class="${r.member_id === b.me ? "me" : ""}"><a href="#/runner/${r.member_id}">
        <span class="rk num">${r.rank}</span>
        <span><div class="nm">${esc(r.name)}${r.member_id === b.me ? " (lu)" : ""}</div>
          <div class="meta"><span class="lvl">LV ${r.level}</span>${r.days} hari aktif · pace ${pace(r.pace)}/km</div></span>
        <span class="val num">${metricVal(r, state.metric)}<small>${r.runs} run</small></span>
        <span class="chev" aria-hidden="true">›</span>
      </a></li>`).join("")}</ol>` : ""}
    ${b.idle ? `<p class="idle">${b.idle} member belum lari di periode ini. Yuk ajak di grup, Sunday Long Run berangkat dari HiGround ☕</p>` : ""}`;
  $app.querySelectorAll("[data-seg]").forEach((btn) => btn.addEventListener("click", () => {
    state[btn.dataset.seg] = btn.dataset.v; pageBoard();
  }));
}

// ── dashboard ───────────────────────────────────────────────────────────────
async function pageDash(memberId, q) {
  const d = await api(`/api/dashboard?period=${state.period}${memberId ? `&member=${memberId}` : ""}`);
  const own = d.member.own;
  const t = d.totals, lv = d.level;
  const first = d.member.name.split(" ")[0];
  const pct = Math.round((lv.into / lv.next) * 100);
  document.getElementById("garmin").hidden = !d.garmin;
  $app.innerHTML = `
    ${own && q.get("welcome") ? `<div class="note" role="status">Selamat datang! Run lu lagi ditarik dari intervals.icu. Ini bisa makan satu–dua menit. Refresh halaman ini sebentar lagi.</div>` : ""}
    ${own && ME.sync_error ? `<div class="note err" role="alert">Sync terakhir gagal: ${esc(ME.sync_error)}${ME.connected ? "" : ` · ${loginButton("Connect ulang")}`}</div>` : ""}
    <div class="hello" style="margin-top:16px">
      <div>
        <p class="eyebrow">${own ? "Dashboard gue" : "Profil runner"}</p>
        <h1>${own ? `Hai, ${esc(first)}.` : esc(d.member.name)}</h1>
        <div class="row-actions" style="margin-top:12px">
          ${d.rank ? `<span class="chip rank">#${d.rank.rank} dari ${d.rank.of} bulan ini</span>` : ""}
          <span class="chip">🔥 ${d.streak} minggu beruntun</span>
        </div>
      </div>
      <div class="card levelbox" aria-label="Level">
        <div style="display:flex;justify-content:space-between"><b>Level ${lv.level}</b><span class="muted num">${lv.xp.toLocaleString("id-ID")} XP</span></div>
        <div class="bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><i style="width:${pct}%"></i></div>
        <div class="muted" style="font-size:12px;margin-top:6px">${(lv.next - lv.into).toLocaleString("id-ID")} XP lagi ke level ${lv.level + 1}. XP dari hari aktif, km, dan minggu dengan ≥3 hari lari.</div>
      </div>
    </div>

    <div class="filters">${seg("period", [["week", "Minggu ini"], ["month", "Bulan ini"], ["all", "All time"]], state.period)}<span class="muted">${esc(d.label)}</span></div>
    <div class="grid g4">
      ${stat(km(t.km), "km", "Jarak")}${stat(t.runs, "", "Run")}${stat(dur(t.hours * 3600), "", "Waktu bergerak")}${stat(pace(t.pace), "/km", "Pace rata-rata")}
    </div>

    <div class="grid g2" style="margin-top:16px">
      <div class="card"><h3>Kilometer per minggu</h3><p class="muted" style="margin:4px 0 12px;font-size:13px">16 minggu terakhir</p>${weekBars(d.weeks)}</div>
      <div class="card"><h3>Tren pace</h3><p class="muted" style="margin:4px 0 12px;font-size:13px">Rata-rata per minggu, makin atas makin cepat</p>${paceLine(d.weeks)}</div>
    </div>

    <div class="grid g2" style="margin-top:16px">
      <div class="card load ${d.load.state}">
        <h3>Beban latihan</h3>
        <p style="margin:8px 0"><span class="status-label">${{ good: "✓ Aman", high: "⚠ Terlalu tinggi", low: "↓ Lebih ringan", unknown: "… Belum cukup data" }[d.load.state]}</span></p>
        <p style="margin:0 0 8px">${esc(d.load.text)}</p>
        <p class="muted num" style="margin:0;font-size:13px">7 hari: ${km(d.load.acute)} km · rata-rata mingguan 4 minggu: ${km(d.load.chronic)} km${d.load.ratio ? ` · rasio ${d.load.ratio}` : ""}</p>
      </div>
      <div class="card">
        <h3>Rekor</h3>
        <div class="grid" style="grid-template-columns:1fr 1fr;margin-top:12px">
          ${best("Lari terjauh", d.longest, (b) => `${km(b.km)} km`)}
          ${best("Pace tercepat ≥5 km", d.fastest5k, (b) => `${pace(b.pace)}/km`)}
        </div>
        <p class="muted" style="margin:12px 0 0;font-size:13px">All time: ${km(d.all_time.km)} km · ${d.all_time.runs} run · ${km(d.all_time.elevation)} m naik</p>
      </div>
    </div>

    <div class="card" style="margin-top:16px"><h3>Kalender konsistensi</h3>${heat(d.calendar, d.calendar_start)}</div>

    <div class="grid g2" style="margin-top:16px;align-items:start">
      <div class="card"><h3>Aktivitas terakhir</h3><ul class="acts">${d.recent.slice(0, 10).map(actRow).join("") || `<li class="muted">Belum ada run.</li>`}</ul></div>
      ${own ? settingsCard() : ""}
    </div>`;
  bindTips($app);
  $app.querySelectorAll("[data-seg]").forEach((btn) => btn.addEventListener("click", () => { state.period = btn.dataset.v; pageDash(memberId, new URLSearchParams()); }));
  $app.querySelectorAll("[data-table]").forEach((btn) => btn.addEventListener("click", () => {
    const tbl = document.getElementById(btn.dataset.table); tbl.hidden = !tbl.hidden; btn.textContent = tbl.hidden ? "Lihat sebagai tabel" : "Sembunyikan tabel";
  }));
  if (own) bindSettings();
}

const stat = (v, unit, label) => `<div class="card stat"><div class="v num">${v}${unit ? `<small> ${unit}</small>` : ""}</div><div class="l">${label}</div></div>`;
const best = (label, b, f) => `<div><div class="muted" style="font-size:13px">${label}</div>${b ? `<div style="font:22px/1.1 var(--display)" class="num">${f(b)}</div><div class="muted" style="font-size:12px">${dshort(b.day)}${b.name ? ` · ${esc(b.name)}` : ""}</div>` : `<div class="muted">–</div>`}</div>`;

function actRow(a) {
  const badge = a.status === "ok" ? "" : `<span class="badge ${a.status}">${{ flagged: "direview admin", rejected: "ditolak", approved: "disetujui", skipped: "nggak dihitung" }[a.status]}</span>`;
  const garmin = a.device && /garmin/i.test(a.device);
  return `<li><div><div class="t">${esc(a.name || a.type)}${badge}</div>
    <div class="s">${dlong(a.start)}${a.km ? ` · ${dur(a.secs)} · ${pace(a.pace)}/km` : ""}${a.hr ? ` · ♥ ${a.hr}` : ""}</div>
    ${a.device ? `<div class="s">${garmin ? "Garmin® " + esc(a.device.replace(/^garmin\s*/i, "")) : esc(a.device)}</div>` : ""}
    ${a.reason ? `<div class="s">${esc(a.reason)}</div>` : ""}</div>
    <div class="k num">${a.km ? km(a.km) + " km" : "–"}</div></li>`;
}

function weekBars(weeks) {
  const W = 480, H = 180, L = 32, B = 22, T = 8, max = Math.max(10, ...weeks.map((w) => w.km));
  const step = niceStep(max), top = Math.ceil(max / step) * step;
  const bw = (W - L) / weeks.length, y = (v) => T + (H - T - B) * (1 - v / top);
  let g = "";
  for (let v = 0; v <= top; v += step) g += `<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text>`;
  const bars = weeks.map((w, i) => {
    const x = L + i * bw + 2, h = Math.max(0, y(0) - y(w.km)), cur = i === weeks.length - 1;
    const r = Math.min(4, h / 2, (bw - 4) / 2);
    const path = h ? `M${x},${y(0)} V${y(w.km) + r} Q${x},${y(w.km)} ${x + r},${y(w.km)} H${x + bw - 4 - r} Q${x + bw - 4},${y(w.km)} ${x + bw - 4},${y(w.km) + r} V${y(0)} Z` : "";
    const tip = `<b>${km(w.km)} km</b><br>Minggu ${dshort(w.week)}<br>${w.runs} run${w.pace ? ` · ${pace(w.pace)}/km` : ""}`;
    return `<rect class="hit" x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(`Minggu ${dshort(w.week)}: ${km(w.km)} km`)}"/>${path ? `<path class="bar${cur ? "" : ""}" d="${path}"/>` : ""}` +
      (i % 4 === 0 ? `<text x="${x + (bw - 4) / 2}" y="${H - 6}" text-anchor="middle">${dshort(w.week)}</text>` : "");
  }).join("");
  const id = "t" + Math.random().toString(36).slice(2, 8);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Kilometer per minggu, 16 minggu terakhir"><g class="grid">${g}</g>${bars}</svg>
    <button class="table-toggle" data-table="${id}">Lihat sebagai tabel</button>
    <table class="data" id="${id}" hidden><thead><tr><th>Minggu mulai</th><th class="n">Km</th><th class="n">Run</th><th class="n">Pace</th></tr></thead>
    <tbody>${weeks.map((w) => `<tr><td>${dshort(w.week)}</td><td class="n">${km(w.km)}</td><td class="n">${w.runs}</td><td class="n">${pace(w.pace)}</td></tr>`).join("")}</tbody></table>`;
}

function paceLine(weeks) {
  const pts = weeks.map((w, i) => ({ i, w })).filter((p) => p.w.pace);
  if (pts.length < 2) return `<p class="muted">Butuh minimal 2 minggu dengan lari.</p>`;
  const W = 480, H = 180, L = 40, B = 22, T = 12;
  const lo = Math.min(...pts.map((p) => p.w.pace)), hi = Math.max(...pts.map((p) => p.w.pace));
  const pad = Math.max(10, (hi - lo) * 0.15), a = lo - pad, b = hi + pad;
  const x = (i) => L + ((W - L - 10) * i) / (weeks.length - 1), y = (s) => T + (H - T - B) * ((s - a) / (b - a)); // faster = higher
  let g = "";
  for (let k = 0; k <= 3; k++) { const s = a + ((b - a) * k) / 3; g += `<line x1="${L}" x2="${W}" y1="${y(s)}" y2="${y(s)}"/><text x="${L - 6}" y="${y(s) + 4}" text-anchor="end">${pace(s)}</text>`; }
  const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i).toFixed(1)},${y(p.w.pace).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const dots = pts.map((p) => `<circle class="dot" cx="${x(p.i)}" cy="${y(p.w.pace)}" r="${p === last ? 5 : 3.5}"/>` +
    `<circle class="hit" cx="${x(p.i)}" cy="${y(p.w.pace)}" r="12" tabindex="0" data-tip="${esc(`<b>${pace(p.w.pace)}/km</b><br>Minggu ${dshort(p.w.week)} · ${km(p.w.km)} km`)}" aria-label="${esc(`Minggu ${dshort(p.w.week)}: ${pace(p.w.pace)} per km`)}"/>`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Tren pace mingguan"><g class="grid">${g}</g><path class="line" d="${d}"/>${dots}
    <text x="${x(last.i)}" y="${y(last.w.pace) - 10}" text-anchor="end" style="fill:var(--ink);font-weight:700">${pace(last.w.pace)}</text></svg>`;
}

function niceStep(max) { const raw = max / 4, p = 10 ** Math.floor(Math.log10(raw)), n = raw / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }

function heat(cal, startIso) {
  const start = new Date(startIso + "T00:00:00"), today = new Date(); today.setHours(0, 0, 0, 0);
  let cells = "";
  for (let d = new Date(start); d <= today || d.getDay() !== 1; d.setDate(d.getDate() + 1)) {
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const v = cal[iso] || 0, lvl = v === 0 ? 0 : v < 5 ? 1 : v < 10 ? 2 : v < 16 ? 3 : 4;
    const future = d > today;
    cells += `<i class="l${lvl}${future ? " out" : ""}" ${future ? "" : `tabindex="0" data-tip="${esc(`<b>${v ? km(v) + " km" : "Rest"}</b><br>${dlong(iso)}`)}" aria-label="${esc(`${dlong(iso)}: ${v ? km(v) + " km" : "rest"}`)}"`}></i>`;
    if (d > today && d.getDay() === 0) break;
  }
  return `<div class="heat" style="margin-top:12px">${cells}</div>
    <div class="legend">Rest <i style="background:var(--heat-0)"></i><i style="background:var(--heat-1)"></i><i style="background:var(--heat-2)"></i><i style="background:var(--heat-3)"></i><i style="background:var(--heat-4)"></i> 16+ km <span style="margin-left:8px">(&lt;5 · 5–10 · 10–16 · 16+)</span></div>`;
}

function settingsCard() {
  return `<div class="card"><h3>Pengaturan</h3>
    <form class="form" id="prof" style="margin-top:12px">
      <label>Nama di leaderboard<input type="text" name="name" maxlength="60" value="${esc(ME.name)}" required></label>
      <label>Kategori leaderboard
        <select name="gender"><option value="" ${ME.gender === "" ? "selected" : ""}>Pilih… (cuma tampil di "Semua")</option>
          <option value="F" ${ME.gender === "F" ? "selected" : ""}>Perempuan</option><option value="M" ${ME.gender === "M" ? "selected" : ""}>Laki-laki</option></select></label>
      <label class="check"><input type="checkbox" name="public" ${ME.public ? "checked" : ""}><span>Tampilkan gue di leaderboard dan profil gue bisa dilihat member lain</span></label>
      <div class="row-actions"><button class="btn small" type="submit">Simpan</button><span id="profmsg" class="muted" role="status"></span></div>
    </form>
    <hr style="border:0;border-top:1px solid var(--line);margin:20px 0">
    <p class="muted" style="margin:0 0 10px;font-size:14px">Sync otomatis tiap ±30 menit. Terakhir: ${ago(ME.last_sync)}.</p>
    <div class="row-actions">
      <button class="btn small ghost" id="syncnow" type="button">Sync sekarang</button>
      <button class="btn small danger" id="disc" type="button">Putuskan & hapus data gue</button>
      <button class="btn small ghost" id="logout" type="button">Keluar</button>
    </div><p id="syncmsg" class="muted" role="status" style="font-size:14px"></p></div>`;
}

function bindSettings() {
  const f = document.getElementById("prof");
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("profmsg");
    try {
      const r = await api("/api/profile", { name: f.name.value, gender: f.gender.value, public: f.public.checked });
      ME = r.me; msg.textContent = "Tersimpan ✓";
    } catch (err) { msg.textContent = err.message; }
  });
  document.getElementById("syncnow").addEventListener("click", async (e) => {
    const msg = document.getElementById("syncmsg"); e.target.disabled = true; msg.textContent = "Lagi narik dari intervals.icu…";
    try {
      const r = await api("/api/sync", {});
      msg.textContent = r.error ? `Gagal: ${r.error}` : `Beres: ${r.new} run baru.`;
      if (!r.error && r.new) setTimeout(route, 800);
    } catch (err) { msg.textContent = err.message; }
    e.target.disabled = false;
  });
  document.getElementById("disc").addEventListener("click", async () => {
    if (!confirm("Putuskan intervals.icu dan hapus semua data lari lu dari HiGround? Ini nggak bisa dibatalin.")) return;
    await api("/api/disconnect", {}); ME = null; location.hash = "#/";
  });
  document.getElementById("logout").addEventListener("click", async () => { await api("/auth/logout", {}); ME = null; location.hash = "#/"; });
}

// ── admin ───────────────────────────────────────────────────────────────────
async function pageAdmin() {
  const a = await api("/api/admin");
  $app.innerHTML = `
    <p class="eyebrow">Khusus admin</p><h1>Admin</h1>
    <div class="card" style="margin-top:24px"><h2>Perlu direview (${a.flagged.length})</h2>
      <p class="muted" style="margin-top:0">Run yang kena aturan anti-cheat. Nggak masuk leaderboard sampai disetujui.</p>
      ${a.flagged.map((f) => `<div class="adm-row"><div><b>${esc(f.member_name)}</b> · ${esc(f.name || f.type)}<div class="muted" style="font-size:13px">${dlong(f.start_local)} · ${km(f.distance_m / 1000)} km · ${pace((f.moving_s || f.elapsed_s) / (f.distance_m / 1000))}/km · ${esc(f.device || f.source || "")}</div><div style="font-size:13px;color:var(--warn)">${esc(f.reason)}</div></div>
        <div class="row-actions"><button class="btn small" data-review="${esc(f.id)}" data-d="approved">Setujui</button><button class="btn small danger" data-review="${esc(f.id)}" data-d="rejected">Tolak</button></div></div>`).join("") || `<p>Aman, nggak ada yang perlu direview.</p>`}
    </div>
    <div class="card" style="margin-top:16px"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap"><h2 style="margin:0">Member (${a.members.length})</h2><button class="btn small" id="syncall" ${a.syncing ? "disabled" : ""}>${a.syncing ? "Lagi sync…" : "Sync semua sekarang"}</button></div>
      ${a.members.map((m) => `<div class="adm-row"><div><b>${esc(m.name)}</b>${m.is_admin ? ` <span class="lvl">ADMIN</span>` : ""}<div class="muted" style="font-size:13px">${esc(m.athlete_id)} · gabung ${esc(m.joined)} · sync ${ago(m.last_sync)}${m.connected ? "" : " · <b style='color:var(--bad)'>terputus</b>"}</div>${m.sync_error ? `<div style="font-size:13px;color:var(--bad)">${esc(m.sync_error)}</div>` : ""}</div>
        <label class="row-actions" style="font-size:13px"><input type="checkbox" data-pub="${m.id}" ${m.public ? "checked" : ""}> di leaderboard</label></div>`).join("")}
    </div>`;
  $app.querySelectorAll("[data-review]").forEach((b) => b.addEventListener("click", async () => { await api("/api/admin/review", { id: b.dataset.review, decision: b.dataset.d }); pageAdmin(); }));
  $app.querySelectorAll("[data-pub]").forEach((c) => c.addEventListener("change", () => api("/api/admin/member", { id: Number(c.dataset.pub), public: c.checked })));
  const s = document.getElementById("syncall");
  s.addEventListener("click", async () => { s.disabled = true; s.textContent = "Lagi sync…"; await api("/api/admin/sync-all", {}); });
}

function pageNeedLogin() {
  renderNav("");
  $app.innerHTML = `<div class="card" style="max-width:560px;margin:40px auto;text-align:center">
    <h2>Masuk dulu</h2><p>Halaman ini buat member HiGround Run Club. Masuk pakai akun intervals.icu lu.</p>
    ${DEMO ? `<a class="btn" href="/auth/demo">Masuk demo →</a>` : loginButton("Masuk dengan intervals.icu")}</div>`;
}

// ── boot ────────────────────────────────────────────────────────────────────
(async function boot() {
  try {
    const m = await api("/api/me");
    ME = m.me; CLUB = m.club; DEMO = m.demo; CAN_LOGIN = m.can_login;
  } catch (e) { /* page still renders public bits */ }
  route();
})();
