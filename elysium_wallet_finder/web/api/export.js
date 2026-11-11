// A board as raw data, for spreadsheets and scripts:
//   /api/export?board=multiday|degen|day|all&real=1&format=csv|json
// CSV by default (Google Sheets: =IMPORTDATA("https://<site>/api/export?board=degen")).
// Plain numbers, one column per figure (see ../export.js); every ranked wallet
// of the board, ranked as on the page, before any of the page's filters.
const { readSnapshot, send, sendBig } = require("./_blob");
const X = require("../export.js");

module.exports = async (req, res) => {
  const q = new URL(req.url || "/", "http://x").searchParams;
  const board = (q.get("board") || "all").toLowerCase();
  const format = (q.get("format") || "csv").toLowerCase();
  const real = ["1", "true", "yes"].includes((q.get("real") || "").toLowerCase());
  if (!X.BOARDS.includes(board) || !["csv", "json"].includes(format)) {
    res.setHeader("content-type", "application/json; charset=utf-8");
    return send(res, 400, { error: `board is one of ${X.BOARDS.join(", ")}; format is csv or json`,
                            example: "/api/export?board=degen&format=csv" });
  }
  const got = await readSnapshot(req, process.env);
  if (got.status !== 200) {
    res.setHeader("content-type", "application/json; charset=utf-8");
    return send(res, got.status, { error: got.error });
  }
  const snap = JSON.parse(got.body);
  const rows = X.boardRows(snap, board, real);
  const days = X.windowDays(snap, board);
  res.setHeader("cache-control", "public, max-age=0, s-maxage=60, stale-while-revalidate=300");
  res.setHeader("access-control-allow-origin", "*");
  res.setHeader("content-disposition", `${q.get("download") === "0" ? "inline" : "attachment"}; ` +
                `filename="${X.fileName(board, real, snap.generated, format)}"`);
  if (format === "json") {
    res.setHeader("content-type", "application/json; charset=utf-8");
    return sendBig(req, res, 200, JSON.stringify({ board, real, window_days: days,
      generated_utc: new Date(snap.generated * 1000).toISOString(), rows: X.records(rows, days) }));
  }
  res.setHeader("content-type", "text/csv; charset=utf-8");
  sendBig(req, res, 200, X.toCSV(rows, days));
};
