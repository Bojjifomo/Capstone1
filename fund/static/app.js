"use strict";
// Fund: the page. Plain JS, no build step. Every number comes from /api/*.

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 });
const usdf = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const rp = (v) => (v == null ? "–" : (v < 0 ? "−Rp " : "Rp ") + nf.format(Math.abs(v)));
const short = (v) => {
  if (v == null) return "–";
  const a = Math.abs(v), s = v < 0 ? "−" : "";
  if (a >= 1e12) return s + "Rp " + (a / 1e12).toFixed(2).replace(".", ",") + " T";
  if (a >= 1e9) return s + "Rp " + (a / 1e9).toFixed(2).replace(".", ",") + " M";
  if (a >= 1e6) return s + "Rp " + (a / 1e6).toFixed(1).replace(".", ",") + " jt";
  return s + "Rp " + nf.format(a);
};
const usd = (v) => (v == null ? "–" : usdf.format(v));
const num = (s) => {
  // "1.500.000", "1,5jt", "2.5m", "16450" → number
  let t = String(s || "").trim().toLowerCase().replace(/\s|rp/g, "");
  let mult = 1;
  const m = t.match(/(jt|juta|m|rb|k|t)$/);
  if (m) { mult = { jt: 1e6, juta: 1e6, m: 1e9, rb: 1e3, k: 1e3, t: 1e12 }[m[1]]; t = t.slice(0, -m[1].length); }
  if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(t)) t = t.replace(/\./g, "").replace(",", ".");
  else t = t.replace(",", ".");
  const n = parseFloat(t);
  return isNaN(n) ? null : n * mult;
};

async function api(path, body) {
  const opt = body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}
const fail = (e) => alert(e.message || e);
// CSP forbids inline style attributes, so bar widths ride in data-w and are set here
const widths = (el) => el.querySelectorAll("[data-w]").forEach((x) => (x.style.width = x.dataset.w + "%"));
const localDay = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

let meta = { sleeves: {}, kinds: [], categories: [], lp: false };
let summary = null;

// ── tabs ─────────────────────────────────────────────────────────────────────────
function show(tab) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  document.querySelectorAll("main > section").forEach((s) => (s.hidden = s.id !== tab));
  try { localStorage.setItem("fund_tab", tab); } catch (_) {}
  ({ nw: loadSummary, spend: loadMonth, recs: loadRecs, lp: loadLP })[tab]?.();
}
$("#tabs").addEventListener("click", (e) => e.target.dataset.tab && show(e.target.dataset.tab));

// ── a small prompt dialog ────────────────────────────────────────────────────────
function ask(title, fields) {
  const dlg = $("#dlg"), f = $("#dlg-form");
  f.innerHTML = `<h2>${esc(title)}</h2>` + fields.map((x) =>
    x.area ? `<textarea name="${x.name}" rows="3" placeholder="${esc(x.label)}">${esc(x.value)}</textarea>`
           : `<input name="${x.name}" ${x.type ? `type="${x.type}"` : 'inputmode="decimal"'} placeholder="${esc(x.label)}" value="${esc(x.value)}">`
  ).join("") + `<div class="row"><button value="cancel" formnovalidate>Batal</button><button value="ok">Simpan</button></div>`;
  return new Promise((res) => {
    dlg.onclose = () => res(dlg.returnValue === "ok" ? Object.fromEntries(new FormData(f)) : null);
    dlg.showModal();
  });
}

// ── net worth ────────────────────────────────────────────────────────────────────
async function loadSummary() {
  summary = await api("/api/summary");
  const s = summary;
  $("#nw-idr").textContent = rp(s.net_worth_idr);
  $("#nw-usd").textContent = "≈ " + usd(s.net_worth_usd);
  const c = s.change_30d_idr;
  $("#nw-chg").innerHTML = `<span class="${c >= 0 ? "good" : "bad"}">${c >= 0 ? "+" : ""}${esc(short(c))}</span> 30 hari`;
  $("#fx-edit").textContent = nf.format(s.usd_idr);
  $("#t-runway").textContent = s.runway_months != null ? s.runway_months + " bln" : "–";
  $("#t-save").textContent = s.savings_rate_pct != null ? s.savings_rate_pct + "%" : "–";
  $("#t-spend").textContent = short(s.avg_monthly_spend);
  drawChart(s.history);
  const pos = s.net_worth_idr > 0;
  $("#sleeves").innerHTML = s.sleeves.length ? s.sleeves.map((x) => `
    <div class="bar"><div class="top"><span>${esc(x.label)}</span><span>${esc(short(x.idr))}${x.pct != null ? " · " + x.pct + "%" : ""}</span></div>
    <div class="track"><div class="fill" data-w="${pos ? Math.max(0, Math.min(100, x.pct || 0)) : 0}"></div></div></div>`).join("")
    : `<div class="empty">Belum ada akun. Tambah di bawah.</div>`;
  widths($("#sleeves"));
  const stale = s.stale_accounts;
  $("#stale").hidden = !stale.length;
  $("#stale").textContent = stale.length ? `Saldo lebih dari 35 hari / belum diisi: ${stale.join(", ")}` : "";
  let html = "", last = null;
  for (const a of s.accounts) {
    if (a.sleeve !== last) { html += `<div class="group">${esc(meta.sleeves[a.sleeve])}</div>`; last = a.sleeve; }
    const native = a.currency === "USD" ? usd(a.amount) : rp(a.amount);
    html += `<div class="acct" data-id="${a.id}" data-auto="${a.auto ? 1 : ""}">
      <div><div class="name">${esc(a.name)}</div><div class="hint">${esc(a.kind)}${a.auto ? " · otomatis" : ""} · ${a.as_of ? esc(a.as_of) : "belum diisi"}</div></div>
      <div class="amt"><div>${esc(short(a.idr))}</div>${a.currency === "USD" ? `<div class="hint">${esc(native)}</div>` : ""}</div></div>`;
  }
  $("#accounts").innerHTML = html;
}

$("#accounts").addEventListener("click", async (e) => {
  const row = e.target.closest(".acct");
  if (!row) return;
  const a = summary.accounts.find((x) => String(x.id) === row.dataset.id);
  if (row.dataset.auto) return alert("Akun ini diisi otomatis dari tab LP.");
  const v = await ask(`Saldo ${a.name} (${a.currency})`, [
    { name: "amount", label: "Saldo sekarang", value: a.amount ? String(a.amount) : "" },
    { name: "day", label: "Tanggal", type: "date", value: localDay() },
    { name: "note", label: "Catatan (opsional)", type: "text", value: "" },
  ]);
  if (!v) return;
  const amount = num(v.amount);
  if (amount == null) return alert("Angkanya nggak kebaca");
  api("/api/balances", { account: a.id, amount, day: v.day, note: v.note }).then(loadSummary).catch(fail);
});

$("#fx-edit").addEventListener("click", async () => {
  const v = await ask("Kurs USD/IDR hari ini", [{ name: "usd_idr", label: "mis. 16450", value: String(summary.usd_idr) }]);
  if (v && num(v.usd_idr)) api("/api/fx", { usd_idr: num(v.usd_idr) }).then(loadSummary).catch(fail);
});

$("#f-account").addEventListener("submit", async (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  try {
    const { id } = await api("/api/accounts", { name: d.name, kind: d.kind, sleeve: +d.sleeve, currency: d.currency });
    if (d.amount && num(d.amount) != null) await api("/api/balances", { account: id, amount: num(d.amount) });
    e.target.reset();
    loadSummary();
  } catch (err) { fail(err); }
});

function drawChart(points) {
  const el = $("#chart");
  if (!points || points.length < 2) { el.innerHTML = `<div class="empty">Grafik muncul setelah ada saldo di dua tanggal berbeda.</div>`; return; }
  const W = 600, H = 160, L = 46, B = 18, T = 8;
  const ys = points.map((p) => p[1]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = (i) => L + (i * (W - L - 6)) / (points.length - 1);
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  const area = d + `L${x(points.length - 1)},${H - B}L${x(0)},${H - B}Z`;
  const ticks = [lo, (lo + hi) / 2, hi];
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Net worth history">
    ${ticks.map((t) => `<line class="grid-line" x1="${L}" x2="${W}" y1="${y(t)}" y2="${y(t)}"/><text x="0" y="${y(t) + 3}">${esc(short(t).replace("Rp ", ""))}</text>`).join("")}
    <path class="area" d="${area}"/><path class="line" d="${d}"/>
    <text x="${L}" y="${H - 4}">${esc(points[0][0].slice(0, 7))}</text>
    <text x="${W - 60}" y="${H - 4}">${esc(points[points.length - 1][0])}</text></svg>`;
}

// ── money in / out ───────────────────────────────────────────────────────────────
let month = localDay().slice(0, 7);
let flowKind = "expense";
$("#flow-kind").addEventListener("click", (e) => {
  if (!e.target.dataset.kind) return;
  flowKind = e.target.dataset.kind;
  document.querySelectorAll("#flow-kind button").forEach((b) => b.classList.toggle("on", b === e.target));
});
const shiftMonth = (n) => {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + n, 1);
  month = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  loadMonth();
};
$("#m-prev").onclick = () => shiftMonth(-1);
$("#m-next").onclick = () => shiftMonth(1);

async function loadMonth() {
  const [{ flows }, { budgets }] = await Promise.all([api(`/api/flows?month=${month}`), api("/api/budgets")]);
  $("#m-label").textContent = new Date(month + "-01T00:00:00").toLocaleDateString("id-ID", { month: "long", year: "numeric" });
  const fx = summary?.usd_idr || 16500;
  const idr = (f) => f.amount * (f.currency === "USD" ? fx : 1);
  const spent = flows.filter((f) => f.kind === "expense").reduce((a, f) => a + idr(f), 0);
  const income = flows.filter((f) => f.kind === "income").reduce((a, f) => a + idr(f), 0);
  const btotal = budgets.reduce((a, b) => a + b.monthly, 0);
  $("#m-spent").textContent = short(spent);
  $("#m-income").textContent = short(income);
  $("#m-left").innerHTML = `<span class="${income - spent >= 0 ? "good" : "bad"}">${esc(short(income - spent))}</span>`;
  $("#m-budget").textContent = btotal ? `budget ${short(btotal)}` : "";
  const cats = {};
  flows.filter((f) => f.kind === "expense").forEach((f) => (cats[f.category] = (cats[f.category] || 0) + idr(f)));
  budgets.forEach((b) => (cats[b.category] = cats[b.category] || 0));
  const bmap = Object.fromEntries(budgets.map((b) => [b.category, b.monthly]));
  const top = Math.max(1, ...Object.values(cats));
  $("#cats-bars").innerHTML = Object.entries(cats).sort((a, b) => b[1] - a[1]).map(([c, v]) => {
    const b = bmap[c], pct = b ? (100 * v) / b : (100 * v) / top;
    return `<div class="bar"><div class="top"><span>${esc(c)}</span><span>${esc(short(v))}${b ? " / " + esc(short(b)) : ""}</span></div>
      <div class="track"><div class="fill ${b && v > b ? "over" : ""}" data-w="${Math.min(100, pct)}"></div></div></div>`;
  }).join("") || `<div class="empty">Belum ada pengeluaran bulan ini.</div>`;
  widths($("#cats-bars"));
  $("#flows").innerHTML = flows.map((f) => `<div class="flow">
      <div><div>${esc(f.category)}${f.note ? ` <span class="muted">· ${esc(f.note)}</span>` : ""}</div><div class="hint">${esc(f.day)}</div></div>
      <div class="row"><span class="${f.kind === "income" ? "good" : ""}">${f.kind === "income" ? "+" : "−"}${esc(f.currency === "USD" ? usd(f.amount) : rp(f.amount))}</span>
      <button class="x" data-del="${f.id}" aria-label="Hapus">×</button></div></div>`).join("") || `<div class="empty">Kosong.</div>`;
  $("#budgets").innerHTML = budgets.map((b) => `<div class="flow"><span>${esc(b.category)}</span><span>${esc(rp(b.monthly))}</span></div>`).join("");
  const all = new Set([...meta.categories, ...Object.keys(cats)]);
  $("#cats").innerHTML = [...all].sort().map((c) => `<option value="${esc(c)}">`).join("");
}

$("#flows").addEventListener("click", (e) => {
  const id = e.target.dataset.del;
  if (id && confirm("Hapus catatan ini?")) api("/api/flows/delete", { id: +id }).then(loadMonth).catch(fail);
});
$("#f-flow").addEventListener("submit", (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  const amount = num(d.amount);
  if (!amount) return alert("Jumlahnya nggak kebaca");
  api("/api/flows", { kind: flowKind, amount, category: d.category, note: d.note, day: d.day }).then(() => {
    e.target.amount.value = ""; e.target.note.value = "";
    if (!meta.categories.includes(d.category.toLowerCase())) meta.categories.push(d.category.toLowerCase());
    loadMonth();
  }).catch(fail);
});
$("#f-budget").addEventListener("submit", (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  api("/api/budgets", { category: d.category, monthly: num(d.monthly) ?? 0 }).then(() => { e.target.reset(); loadMonth(); }).catch(fail);
});

// ── recommendations ──────────────────────────────────────────────────────────────
const STATUS = { proposed: "usulan", accepted: "dijalanin", done: "selesai", rejected: "ditolak" };
async function loadRecs() {
  const [{ recs }, s] = await Promise.all([api("/api/recs"), api("/api/summary")]);
  summary = s;
  $("#r-open").textContent = s.recs.open;
  $("#r-done").textContent = s.recs.done;
  $("#r-actual").textContent = short(s.recs.actual_idr_month_done);
  $("#r-expected").textContent = s.recs.expected_idr_month_done ? `target ${short(s.recs.expected_idr_month_done)}` : "";
  $("#rec-list").innerHTML = recs.map((r) => `<div class="card rec" data-id="${r.id}">
      <div class="row between"><span class="badge ${r.status}">${STATUS[r.status]}</span>
        <span class="hint">${r.author === "me" ? "lu" : "fund manager"} · ${esc(r.created)}${r.sleeve != null ? " · " + esc(meta.sleeves[r.sleeve]) : ""}</span></div>
      <h3>${esc(r.title)}</h3>
      ${r.body ? `<p>${esc(r.body)}</p>` : ""}
      ${r.expected_idr_month ? `<div class="hint">Target dampak: ${esc(short(r.expected_idr_month))} / bulan</div>` : ""}
      ${r.status === "done" ? `<div class="hint">Selesai ${esc(r.done)} · realisasi <b>${esc(short(r.actual_idr_month))}</b> / bulan</div>${r.result ? `<p class="muted">${esc(r.result)}</p>` : ""}` : ""}
      <div class="actions">${{
        proposed: `<button data-act="rejected">Tolak</button><button class="primary" data-act="accepted">Jalanin</button>`,
        accepted: `<button data-act="proposed">Balikin</button><button class="primary" data-act="done">Udah selesai</button>`,
        done: `<button data-act="done">Edit hasil</button>`,
        rejected: `<button data-act="proposed">Buka lagi</button>`,
      }[r.status]}</div></div>`).join("") || `<div class="card empty">Belum ada rekomendasi. Minta fund manager: "fund-manager, review bulanan".</div>`;
}

$("#rec-list").addEventListener("click", async (e) => {
  const act = e.target.dataset.act, card = e.target.closest(".rec");
  if (!act || !card) return;
  const id = +card.dataset.id;
  if (act === "done") {
    const v = await ask("Hasilnya gimana?", [
      { name: "actual", label: "Realisasi dampak per bulan (Rp)", value: "" },
      { name: "result", label: "Catatan hasil", area: true, value: "" },
    ]);
    if (!v) return;
    return api("/api/recs", { id, status: "done", result: v.result, actual_idr_month: num(v.actual) }).then(loadRecs).catch(fail);
  }
  api("/api/recs", { id, status: act }).then(loadRecs).catch(fail);
});
$("#f-rec").addEventListener("submit", (e) => {
  e.preventDefault();
  const d = Object.fromEntries(new FormData(e.target));
  api("/api/recs", { title: d.title, body: d.body, expected_idr_month: num(d.expected_idr_month) }).then(() => { e.target.reset(); loadRecs(); }).catch(fail);
});

// ── LP ───────────────────────────────────────────────────────────────────────────
let lpDays = 30;
$("#lp-days").addEventListener("click", (e) => {
  if (!e.target.dataset.days) return;
  lpDays = +e.target.dataset.days;
  document.querySelectorAll("#lp-days button").forEach((b) => b.classList.toggle("on", b === e.target));
  loadLP();
});
$("#lp-refresh").onclick = () => api("/api/lp/refresh", {}).then(loadLP).catch(fail);

async function loadLP() {
  const s = await api(`/api/lp?days=${lpDays}`);
  $("#lp-error").hidden = !s.error;
  $("#lp-error").textContent = s.error || "";
  const n = s.snapshot, f = s.flows;
  $("#lp-open").textContent = n ? usd(n.open_usd) : "–";
  $("#lp-npos").textContent = n ? `${n.open_positions} posisi${n.unpriced_open ? ` · ${n.unpriced_open} belum ada harga` : ""}` : "";
  $("#lp-idle").textContent = n ? usd(n.idle_usd) : "–";
  $("#lp-total").textContent = n ? usd(n.total_usd) : "–";
  $("#lp-asof").textContent = n?.balance_as_of ? "per " + n.balance_as_of.replace("T", " ") : "";
  if (!f) return;
  $("#lp-dep").textContent = usd(f.deposit_usd);
  $("#lp-wd").textContent = usd(f.withdraw_usd);
  $("#lp-fee").innerHTML = `<span class="good">${esc(usd(f.fees_usd))}</span>`;
  $("#lp-pnl").innerHTML = `<span class="${f.realized_pnl_usd >= 0 ? "good" : "bad"}">${esc(usd(f.realized_pnl_usd))}</span>`;
  $("#lp-closed").textContent = `${f.closed_positions} ditutup${f.unpriced_events ? ` · ${f.unpriced_events} event nunggu harga` : ""}`;
  $("#lp-daily").innerHTML = f.daily.slice().reverse().map((d) => `<div class="flow"><span>${esc(d.day)}</span>
    <span class="hint">masuk ${esc(usd(d.deposit))} · tarik ${esc(usd(d.withdraw))} · fee <span class="good">${esc(usd(d.fees))}</span></span></div>`).join("")
    || `<div class="empty">Nggak ada aktivitas LP di rentang ini.</div>`;
}

// ── start ────────────────────────────────────────────────────────────────────────
(async () => {
  try {
    meta = await api("/api/meta");
  } catch (e) { return fail(e); }
  $("#kinds").innerHTML = meta.kinds.map((k) => `<option>${esc(k)}</option>`).join("");
  $("#sleeve-select").innerHTML = Object.entries(meta.sleeves).map(([k, v]) => `<option value="${k}">${k} · ${esc(v)}</option>`).join("");
  $("#f-flow").day.value = localDay();
  $('#tabs [data-tab="lp"]').hidden = !meta.lp;
  let tab = "nw";
  try { tab = localStorage.getItem("fund_tab") || "nw"; } catch (_) {}
  if (tab === "lp" && !meta.lp) tab = "nw";
  await loadSummary().catch(fail);
  show(tab);
})();
