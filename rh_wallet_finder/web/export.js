// Raw data out of a board snapshot: plain numbers (no "$83.4K"), one column per
// figure. Shared by the page (its Export buttons) and api/export.js (a link a
// spreadsheet can read: =IMPORTDATA("https://<site>/api/export?board=degen")).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.RHWF_EXPORT = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const BOARDS = ["multiday", "degen", "day", "all"];
  const FOLLOW = { "✓": "yes", "?": "maybe", "✗": "bot" };
  const iso = v => (v ? new Date(v * 1000).toISOString().replace(".000Z", "Z") : null);
  const hours = v => (v === null || v === undefined ? null : v / 3600);

  // [header, value of a row]; {d} in a header = the board's window in days
  const COLUMNS = [
    ["rank", r => r.rank],
    ["address", r => r.addr],
    ["class", r => r.kind],
    ["score", r => r.score],
    ["tier", r => r.verdict],
    ["net_worth_usd", r => r.worth],
    ["pnl_{d}d_usd", r => r.net],
    ["roi_{d}d_pct", r => r.roi_nw],
    ["pnl_7d_usd", r => r.net_7d],
    ["pnl_7d_share_of_{d}d", r => r.momentum],
    ["pnl_24h_usd", r => r.net_1d],
    ["avg_month_usd", r => r.avg_month],
    ["pnl_3m_usd", r => r.pnl_3m],
    ["pnl_all_usd", r => r.pnl_all],
    ["roi_all_pct", r => r.roi_all],
    ["pnl_all_since_utc", r => iso(r.hist_from)],
    ["open_pnl_usd", r => r.float],
    ["total_{d}d_usd", r => r.total],
    ["win_pct", r => r.win_pos],
    ["trade_win_pct", r => r.win],
    ["profit_factor", r => r.pf],
    ["capital_roi_{d}d_pct", r => r.roi_win],
    ["roi_per_month_pct", r => r.roi],
    ["green_days_pct", r => r.daily],
    ["fees_{d}d_usd", r => r.fees],
    ["max_drawdown_pct", r => r.dd],
    ["recovery_hours", r => hours(r.recover)],
    ["recovery_ongoing", r => (r.recover_open ? 1 : 0)],
    ["usdg_pct", r => r.usdg],
    ["avg_size_usd", r => r.size],
    ["hold_hours", r => hours(r.duration)],
    ["trades", r => r.trades],
    ["closed_trades", r => r.closed_trades],
    ["closed_positions", r => r.closed],
    ["open_positions", r => r.open],
    ["flat_trades_pct", r => r.flat],
    ["style", r => r.style],
    ["style_fit_pct", r => r.style_conf],
    ["range_pct", r => r.range],
    ["entry_mcap_usd", r => r.mcap],
    ["parallel_trades", r => r.concur],
    ["followable", r => FOLLOW[r.copy] || null],
    ["copiers", r => r.copiers],
    ["crowding_pct", r => (r.exposure === null || r.exposure === undefined ? null : r.exposure * 100)],
    ["deployed_usd", r => r.open_now],
    ["class_fit_pct", r => r.kind_fit],
    ["like_reference_pct", r => (r.sim_ref ? r.sim_pct : null)],
    ["reference_wallet", r => (r.ref ? 1 : 0)],
    ["first_seen_utc", r => iso(r.first_seen)],
    ["last_seen_utc", r => iso(r.last_seen)],
    ["active_days", r => r.span_days],
    ["lpagent_url", r => `https://app.lpagent.io/portfolio?address=${r.addr}&chain=ROBINHOOD`],
    ["explorer_url", r => `https://robinhoodchain.blockscout.com/address/${r.addr}`],
  ];

  // a board's rows, ranked as the page ranks them: multiday / degen = that class
  // of the window view, day = the 24 h view, all = both classes of the window
  function boardRows(snap, board, real) {
    const view = (board === "day" ? "d1" : "") + (real ? "real" : "raw");
    let rows = ((snap.views || {})[view] || []).slice();
    if (board === "multiday" || board === "degen") rows = rows.filter(r => r.kind === board);
    return rows.map((r, i) => ({ ...r, rank: i + 1 }));
  }

  function windowDays(snap, board) {
    return board === "day" ? 1 : ((snap.status || {}).metric_days || 30);
  }

  function headers(days) {
    return COLUMNS.map(([h]) => h.replace(/\{d\}/g, days));
  }

  function records(rows, days) {
    const hs = headers(days);
    return rows.map(r => Object.fromEntries(COLUMNS.map(([, f], i) => {
      const v = f(r);
      return [hs[i], v === undefined || (typeof v === "number" && !Number.isFinite(v)) ? null : v];
    })));
  }

  function csvCell(v) {
    if (v === null || v === undefined) return "";
    const s = String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function toCSV(rows, days) {
    const hs = headers(days);
    const lines = [hs.join(",")];
    for (const rec of records(rows, days)) lines.push(hs.map(h => csvCell(rec[h])).join(","));
    return lines.join("\r\n") + "\r\n";
  }

  function fileName(board, real, generated, ext) {
    const day = new Date((generated || Date.now() / 1000) * 1000).toISOString().slice(0, 10);
    return `rh-wallet-finder-${board}${real ? "-real" : ""}-${day}.${ext}`;
  }

  return { BOARDS, COLUMNS, boardRows, windowDays, headers, records, toCSV, fileName };
});
