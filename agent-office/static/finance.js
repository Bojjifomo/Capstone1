// Under the map: Money (what the office earns and costs) and the Payroll ranking (who costs the most, by person
// and by division), each with Today / 7 days / 30 days.
"use strict";

const Finance = (() => {
  const F = { window: "today", payWindow: "today", money: null, report: null, at: 0 };
  const WINDOWS = [["today", "Today"], ["week", "7 days"], ["month", "30 days"]];
  const DAYS = { today: 1, week: 7, month: 30 };
  const signed = (v) => v == null ? "—" : (v < 0 ? "−" : "") + money(Math.abs(v));

  function tabs(current, attr) {
    return `<div class="seg">` + WINDOWS.map(([k, label]) => `<button type="button" class="${k === current ? "on" : ""}" ${attr}="${k}">${label}</button>`).join("") + `</div>`;
  }

  function moneyHTML() {
    const m = F.money;
    const w = F.window;
    if (!m) return `<h3>Money</h3><p class="sub">Loading…</p>`;
    const label = { today: "today", week: "7 days", month: "30 days" }[w];
    const sources = m.sources.map((s) => {
      const v = s[w];
      return `<div class="mrow"><span class="ml"><b>${s.link ? `<a href="${esc(s.link)}" target="_blank" rel="noopener noreferrer">${esc(s.name)} ↗</a>` : esc(s.name)}</b>` +
        `${s.note ? `<small>${esc(s.note)}</small>` : ""}${s.error ? `<small class="err">Couldn't refresh (${esc(s.error)})${s.updated ? ", showing last values" : ""}</small>` : ""}</span>` +
        `<span class="mv ${v > 0 ? "pos" : v < 0 ? "neg" : ""}">${v == null ? "—" : signed(v)}</span></div>`;
    }).join("");
    const empty = !m.sources.length ? `<p class="sub">No income sources yet. On the Mac, add them to <code>money.json</code> next to server.py (a URL, a command or a file that answers JSON with today/week/month). The README shows the format.</p>` : "";
    return `<h3>Money</h3>${tabs(w, "data-money-window")}` + sources + empty +
      `<div class="mrow total"><span class="ml"><b>Earned ${label}</b></span><span class="mv pos">${signed(m.earned[w])}</span></div>` +
      `<div class="mrow"><span class="ml"><b>Fixed costs</b><small>${money(m.fixed_monthly)}/month from money.json</small></span><span class="mv neg">−${money(m.fixed[w])}</span></div>` +
      `<div class="mrow total"><span class="ml"><b>Net ${label}</b></span><span class="mv ${m.net[w] >= 0 ? "pos" : "neg"}">${signed(m.net[w])}</span></div>` +
      `<div class="mrow"><span class="ml"><b>Claude at API prices</b><small>what the work would cost without your subscription</small></span><span class="mv">${money(m.api_prices[w])}</span></div>` +
      `<p class="sub">${m.checked ? `Money checked ${esc(ago(m.checked))}` : "Not checked yet"} · rechecked every ${m.every_minutes} min` +
      ` <button type="button" class="chip-btn" data-money-refresh${m.refreshing ? " disabled" : ""}>${m.refreshing ? "Refreshing…" : "Refresh now"}</button></p>` +
      `<p class="hint">${esc(m.day_note)}</p>`;
  }

  function payrollHTML() {
    const r = F.report;
    const w = F.payWindow;
    if (!r) return `<h3>Payroll ranking</h3><p class="sub">Loading…</p>`;
    const rows = r.actors.slice(0, 12).map((a, i) =>
      `<button type="button" class="prow" ${a.staff ? `data-staff="${esc(a.staff)}"` : a.sid ? `data-sid="${esc(a.sid)}"` : ""}>` +
      `<span class="rank">#${i + 1}</span><i class="dot d-${esc(String(a.division || "").toLowerCase().replace(/[^a-z]/g, ""))}"></i>` +
      `<span class="who"><b>${esc(a.name)}${a.title ? " · " + esc(a.title) : ""}</b><small>${esc(a.kind === "you" ? "Your chat" : a.kind === "chain" ? a.role || "Chain" : "Specialist")}${a.model ? " · " + esc(a.model) : ""}</small></span>` +
      `<span class="mv">${money(a.cost)}</span></button>`).join("");
    const top = Math.max(...(r.divisions || []).map((d) => d.cost), 0.0001);
    const divs = (r.divisions || []).map((d) =>
      `<div class="drow"><span class="dl">${esc(d.name)}</span><span class="bar"><i class="d-${esc(d.name.toLowerCase().replace(/[^a-z]/g, ""))}" data-w="${(d.cost / top * 100).toFixed(1)}"></i></span><span class="mv">${money(d.cost)}</span></div>`).join("");
    return `<h3>Payroll ranking</h3>${tabs(w, "data-pay-window")}` +
      (rows || `<p class="sub">No usage in this window.</p>`) +
      (divs ? `<div class="divbars">${divs}</div>` : "") +
      `<p class="hint">What each would cost at API prices (${money(r.total)} total). You're on a subscription, so this measures size, not your bill.</p>`;
  }

  function render() {
    const m = $("#money"), p = $("#payroll");
    if (m) m.innerHTML = moneyHTML();
    if (p) { p.innerHTML = payrollHTML(); if (typeof applyBars === "function") applyBars(p); }
  }

  async function loadMoney() {
    try {
      F.money = await api("/api/money");
      if (UI.state) UI.state.money_summary = { net_today: F.money.net.today, earned_today: F.money.earned.today };
      const b = $("#earned");
      if (b) { const v = F.money.net.today; b.hidden = !F.money.sources.length; b.className = "earned " + (v >= 0 ? "pos" : "neg"); b.textContent = (v >= 0 ? "+" : "−") + money(Math.abs(v)) + " today"; }
    } catch (e) { /* the panel says loading */ }
    render();
  }

  async function loadReport() {
    try { F.report = await api("/api/usage/report?days=" + DAYS[F.payWindow]); } catch (e) { /* keep the last one */ }
    render();
  }

  let polls = 0;
  Hooks.header.push(() => {
    if (polls++ % 15 === 0) { loadMoney(); loadReport(); }
  });

  document.addEventListener("click", async (ev) => {
    const mw = ev.target.closest("[data-money-window]");
    if (mw) { F.window = mw.dataset.moneyWindow; render(); return; }
    const pw = ev.target.closest("[data-pay-window]");
    if (pw) { F.payWindow = pw.dataset.payWindow; render(); loadReport(); return; }
    if (ev.target.closest("[data-money-refresh]")) {
      try { await api("/api/money/refresh", {}); toast("Refreshing the money sources…"); setTimeout(loadMoney, 4000); } catch (e) { toast(e.message); }
    }
  });

  render();
  return { loadMoney, loadReport };
})();
