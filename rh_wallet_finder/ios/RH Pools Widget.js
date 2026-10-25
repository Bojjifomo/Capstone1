// RH Pools: iPhone home-screen widget for the Wallet Finder's web showcase.
// Runs in the Scriptable app (free, App Store). Shows the pools paying LPs the most
// fee per $ of liquidity (APR over 24h, and over the last hour), and on the large
// size the top wallets per class.
// Data: https://<your site>/api/widget (the snapshot your Mac publishes), so the
// widget works with the Mac asleep; it just shows how old the numbers are.
//
// Setup: Scriptable → + → paste this → set SITE below → ▶ to test →
// home screen: hold → + → Scriptable → pick a size → Edit widget → Script: this one.

const SITE = "https://YOUR-PROJECT.vercel.app"; // your web showcase, no trailing slash
const POOLS = { small: 3, medium: 4, large: 6 };
const WALLETS = 3;

const C = {
  bg: Color.dynamic(new Color("#f4f6fb"), new Color("#0b0f17")),
  text: Color.dynamic(new Color("#111827"), new Color("#e6e9ef")),
  dim: Color.dynamic(new Color("#6b7280"), new Color("#8b93a3")),
  gold: Color.dynamic(new Color("#a87400"), new Color("#e3b341")),
  up: Color.dynamic(new Color("#0a7d3b"), new Color("#3fb950")),
  down: Color.dynamic(new Color("#c62828"), new Color("#ff7b7b")),
  warn: Color.dynamic(new Color("#b45309"), new Color("#f0a020")),
};

const pct = v => (v === null || v === undefined) ? "–" : `${v >= 0 ? "" : "-"}${Math.abs(v) >= 1000
  ? Math.round(Math.abs(v) / 100) / 10 + "k" : Math.round(Math.abs(v))}%`;
const usd = v => (v === null || v === undefined) ? "–" : `${v < 0 ? "-" : ""}$${Math.abs(v) >= 1000
  ? (Math.abs(v) / 1000).toFixed(1) + "k" : Math.round(Math.abs(v))}`;
const short = a => a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "";
const age = m => m < 60 ? `${m}m ago` : m < 2880 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`;

function line(w, parts, size) {
  const s = w.addStack();
  s.centerAlignContent();
  for (const [text, color, opts] of parts) {
    if (text === null) { s.addSpacer(); continue; }
    const t = s.addText(String(text));
    t.font = (opts && opts.bold) ? Font.semiboldMonospacedSystemFont(size) : Font.monospacedSystemFont(size);
    t.textColor = color;
    t.lineLimit = 1;
  }
  return s;
}

async function load(family) {
  const req = new Request(`${SITE}/api/widget?pools=${POOLS[family] || 4}&wallets=${WALLETS}`);
  req.timeoutInterval = 20;
  const data = await req.loadJSON();
  if (req.response && req.response.statusCode !== 200) throw new Error(data.error || `HTTP ${req.response.statusCode}`);
  return data;
}

function build(data, family, err) {
  const w = new ListWidget();
  w.backgroundColor = C.bg;
  w.setPadding(12, 14, 12, 14);
  w.url = SITE;
  w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
  const fs = family === "small" ? 11 : 12;

  const head = line(w, [["RH POOLS", C.gold, { bold: true }], [null], [data ? age(data.age_min) : "offline",
    data && data.age_min <= 45 ? C.dim : C.warn]], fs - 1);
  head.url = SITE;
  w.addSpacer(6);
  if (!data) {
    const t = w.addText(err || "No data");
    t.font = Font.systemFont(fs);
    t.textColor = C.down;
    return w;
  }
  if (!data.pools.length) {
    const t = w.addText("No pool scan yet: open the Pools tab or run finder.py pools on the Mac.");
    t.font = Font.systemFont(fs);
    t.textColor = C.dim;
  }
  if (family !== "small") line(w, [["pool", C.dim], [null], ["APR 24h", C.dim], ["    1h", C.dim]], fs - 2);
  for (const p of data.pools) {
    // last hour vs the day: green = paying more than its 24h rate right now, red = cooling off
    const hot = [[`  ${pct(p.apr_1h).padStart(5)}`, (p.apr_1h || 0) >= (p.apr_24h || 0) ? C.up : C.down]];
    line(w, [[(p.pair || short(p.pool)).replace(/ \/ /g, "/").slice(0, family === "small" ? 9 : 14), C.text,
      { bold: true }], [null], [pct(p.apr_24h).padStart(6), C.gold], ...(family === "small" ? [] : hot)], fs);
  }
  if (family === "large") {
    for (const kind of ["multiday", "degen"]) {
      w.addSpacer(8);
      line(w, [[kind.toUpperCase(), C.dim, { bold: true }], [null], ["score  30D", C.dim]], fs - 2);
      for (const r of data[kind] || []) {
        line(w, [[short(r.addr), C.text], [null], [`${Math.round(r.score)}`.padStart(5), C.gold],
          [`  ${usd(r.net).padStart(6)}`, (r.net || 0) >= 0 ? C.up : C.down]], fs);
      }
    }
  }
  w.addSpacer();
  return w;
}

const family = config.widgetFamily || "large";
let data = null, err = null;
try {
  data = await load(family);
} catch (e) {
  err = SITE.includes("YOUR-PROJECT") ? "Set SITE at the top of the script." : `Can't load: ${e.message}`;
}
const widget = build(data, family, err);
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentLarge();
Script.complete();
