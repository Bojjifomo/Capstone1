"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtW = (v) => (v == null ? "–" : v < 10 ? v.toFixed(2) + " W" : v.toFixed(1) + " W");
  const fmtWh = (wh) => (wh >= 1000 ? (wh / 1000).toFixed(2) + " kWh" : wh.toFixed(1) + " Wh");
  const money = (cur, v) => cur + " " + Math.round(v).toLocaleString("id-ID");
  const STATE = { busy: "Lagi kerja", idle: "Santai", offline: "Nggak ada data" };
  let range = "live600";

  async function get(path) {
    const r = await fetch(path, { cache: "no-store", credentials: "same-origin" });
    if (!r.ok) throw new Error(r.status + "");
    return r.json();
  }

  async function tickNow() {
    try {
      const d = await get("/api/now");
      const s = d.sample;
      $("state").textContent = STATE[d.state] || d.state;
      $("state").className = "pill " + d.state;
      $("watts").textContent = s && d.state !== "offline" ? (s.watts < 10 ? s.watts.toFixed(1) : Math.round(s.watts)) : "–";
      document.title = (s && d.state !== "offline" ? Math.round(s.watts) + " W · " : "") + "Watt Mac mini";
      $("cpu").textContent = fmtW(s && s.cpu_w);
      $("gpu").textContent = fmtW(s && s.gpu_w);
      $("ane").textContent = fmtW(s && s.ane_w);
      $("temp").textContent = s && s.cpu_temp != null ? Math.round(s.cpu_temp) + "°C" : "–";
      let sub = d.error ? "Error: " + d.error : s ? "update " + new Date(s.ts * 1000).toLocaleTimeString("id-ID") : "nunggu data…";
      if (s && s.system_w == null && !d.error) sub += " · cuma chip (CPU+GPU+ANE)";
      $("sub").textContent = sub;
      $("today").textContent = fmtWh(d.today_wh);
      $("todayCost").textContent = "≈ " + money(d.currency, d.today_cost);
      $("avg").textContent = fmtW(d.avg_1h_w);
      $("proj").textContent = d.month_projection_kwh != null
        ? "≈ " + d.month_projection_kwh + " kWh/bln · " + money(d.currency, d.month_projection_kwh * d.price_per_kwh)
        : "";
      $("foot").textContent = "Sumber: " + (d.source || "–") + " · kerja ≥ " + d.busy_watts + " W · " + money(d.currency, d.price_per_kwh) + "/kWh";
    } catch (e) {
      $("state").textContent = "Mac nggak kejangkau";
      $("state").className = "pill offline";
      $("sub").textContent = "Cek Tailscale di HP & Mac (" + e.message + ")";
    }
  }

  function draw(series, xMin, xMax) {
    const c = $("chart");
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = 220;
    c.width = w * dpr; c.height = h * dpr; c.style.height = h + "px";
    const g = c.getContext("2d");
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    const padL = 34, padB = 20, padT = 8, padR = 6;
    let yMax = 10;
    series.forEach((s) => s.pts.forEach((p) => { if (p[1] > yMax) yMax = p[1]; }));
    const step = [1, 2, 2.5, 5, 10, 15, 20, 25, 50, 100].find((k) => k * 4 >= yMax) || Math.ceil(yMax / 4);
    yMax = step * 4;
    const X = (t) => padL + ((t - xMin) / (xMax - xMin || 1)) * (w - padL - padR);
    const Y = (v) => padT + (1 - v / yMax) * (h - padT - padB);
    g.font = "11px -apple-system, sans-serif";
    g.fillStyle = css("--muted");
    g.strokeStyle = css("--line");
    g.lineWidth = 1;
    for (let i = 0; i <= 4; i++) {
      const v = (yMax / 4) * i, y = Y(v);
      g.beginPath(); g.moveTo(padL, y); g.lineTo(w - padR, y); g.stroke();
      g.fillText(Math.round(v) + "W", 2, y + 4);
    }
    const span = xMax - xMin;
    for (let i = 0; i <= 4; i++) {
      const t = xMin + (span / 4) * i;
      const d = new Date(t * 1000);
      const lab = span > 2 * 86400 ? d.toLocaleDateString("id-ID", { day: "numeric", month: "short" })
        : d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
      const tw = g.measureText(lab).width;
      g.fillText(lab, Math.min(Math.max(X(t) - tw / 2, padL), w - padR - tw), h - 4);
    }
    series.forEach((s) => {
      if (!s.pts.length) return;
      g.strokeStyle = css(s.color);
      g.lineWidth = s.width;
      g.beginPath();
      let prev = null;
      s.pts.forEach((p) => {
        const x = X(p[0]), y = Y(p[1]);
        if (prev == null || p[0] - prev > s.gap) g.moveTo(x, y); else g.lineTo(x, y);
        prev = p[0];
      });
      g.stroke();
      if (s.fill) {
        g.globalAlpha = 0.12; g.fillStyle = css(s.color);
        g.lineTo(X(s.pts[s.pts.length - 1][0]), Y(0)); g.lineTo(X(s.pts[0][0]), Y(0));
        g.closePath(); g.fill(); g.globalAlpha = 1;
      }
    });
  }

  async function tickChart() {
    const now = Date.now() / 1000;
    try {
      if (range.startsWith("live")) {
        const secs = +range.slice(4);
        const d = await get("/api/live?seconds=" + secs);
        const p = d.points;
        draw([
          { pts: p.map((x) => [x[0], x[2]]), color: "--cpu", width: 1.2, gap: 15 },
          { pts: p.map((x) => [x[0], x[3]]), color: "--gpu", width: 1.2, gap: 15 },
          { pts: p.map((x) => [x[0], x[1]]), color: "--tot", width: 2, gap: 15, fill: true },
        ], now - secs, now);
      } else {
        const hours = +range.slice(1);
        const d = await get("/api/history?hours=" + hours);
        const p = d.points;
        draw([
          { pts: p.map((x) => [x[0], x[2]]), color: "--cpu", width: 1, gap: d.bucket * 3 },
          { pts: p.map((x) => [x[0], x[1]]), color: "--tot", width: 2, gap: d.bucket * 3, fill: true },
        ], now - hours * 3600, now);
      }
    } catch (e) { /* tickNow already shows the problem */ }
  }

  async function tickDays() {
    try {
      const d = await get("/api/daily?days=14");
      const tb = document.querySelector("#days tbody");
      tb.textContent = "";
      d.days.slice().reverse().filter((r, i) => i === 0 || r[2] != null).forEach((r) => {
        const tr = document.createElement("tr");
        const dt = new Date(r[0] + "T00:00:00");
        [dt.toLocaleDateString("id-ID", { weekday: "short", day: "numeric", month: "short" }),
          (r[1] / 1000).toFixed(3), r[2] == null ? "–" : r[2].toFixed(1) + " W",
          r[3] == null ? "–" : r[3].toFixed(0) + " W", money(d.currency, r[4])]
          .forEach((v) => { const td = document.createElement("td"); td.textContent = v; tr.appendChild(td); });
        tb.appendChild(tr);
      });
    } catch (e) { /* ignore */ }
  }

  $("range").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    range = b.dataset.r;
    document.querySelectorAll("#range button").forEach((x) => x.classList.toggle("on", x === b));
    tickChart();
  });
  window.addEventListener("resize", tickChart);

  tickNow(); tickChart(); tickDays();
  setInterval(tickNow, 2000);
  setInterval(() => { if (range.startsWith("live")) tickChart(); }, 2000);
  setInterval(() => { if (!range.startsWith("live")) tickChart(); }, 60000);
  setInterval(tickDays, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { tickNow(); tickChart(); } });
})();
