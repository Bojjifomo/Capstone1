// Runs "MET Pool.js" (app mode) in Node against the live Meteora API, with Scriptable's UI classes stubbed,
// then prints the table it would draw. A smoke test for runtime errors and layout logic, not a replacement for
// opening the script on the phone.
//   NODE_USE_ENV_PROXY=1 node dlmm_terminal/scriptable/tests/harness_pool.mjs [--mode=all|v1|signal] [--win=1h|4h|24h] [--rows=12] [--stale]
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = p => fileURLToPath(new URL(p, import.meta.url));
const opt = (k, d) => { const a = process.argv.find(x => x.startsWith(`--${k}=`)); return a ? a.split("=")[1] : d; };
const MODE = opt("mode", null), WIN = opt("win", null), ROWS = +opt("rows", 12), STALE = process.argv.includes("--stale");

const lib = fs.mkdtempSync(path.join(os.tmpdir(), "scriptable-lib-"));
if (MODE || WIN) fs.writeFileSync(path.join(lib, "met_view.json"), JSON.stringify({ mode: MODE || "v1", win: WIN || "24h" }));
if (STALE) { const realNow = Date.now; Date.now = () => realNow() + 30 * 3600 * 1000; }

// --- Scriptable stubs ---
const noop = () => {};
const loose = () => new Proxy(function () {}, { get: (t, k) => (k === "getImage" ? () => ({}) : k === Symbol.toPrimitive ? () => 0 : loose()), set: () => true, apply: () => loose(), construct: () => loose() });
class Cell { constructor(title, subtitle) { this.title = title; this.subtitle = subtitle; } rightAligned() { return this; } centerAligned() { return this; } leftAligned() { return this; } }
class Row {
  constructor() { this.cells = []; }
  addText(t, s) { const c = new Cell(t, s); c.kind = "text"; this.cells.push(c); return c; }
  addButton(t) { const c = new Cell(t); c.kind = "button"; this.cells.push(c); return c; }
  addImage(i) { const c = new Cell(""); c.kind = "image"; this.cells.push(c); return c; }
}
const tables = [];
class Table { constructor() { this.rows = []; tables.push(this); } addRow(r) { this.rows.push(r); } removeAllRows() { this.rows = []; } reload() {} present() { return Promise.resolve(); } }
class Alert { constructor() { this.actions = []; } addAction(a) { this.actions.push(a); } addDestructiveAction(a) { this.actions.push(a); } addCancelAction() {} addTextField() {} textFieldValue() { return ""; } async presentAlert() { return -1; } }
const FM = { local: () => ({
  libraryDirectory: () => lib, documentsDirectory: () => lib, joinPath: (a, b) => path.join(a, b),
  fileExists: p => fs.existsSync(p), readString: p => fs.readFileSync(p, "utf8"), writeString: (p, s) => fs.writeFileSync(p, s),
  remove: p => fs.rmSync(p, { force: true }),
}) };
const key = (await import("node:child_process")).execFileSync("node", ["-e", "const {Keypair}=require('@solana/web3.js');const b=require('bs58');console.log((b.default||b).encode(Keypair.generate().secretKey))"], { cwd: here("../../executor"), encoding: "utf8" }).trim();
const secrets = { ...require(here("../MET Secrets.example.js")), SOLANA_PRIVATE_KEY: key, HELIUS_API_KEY: "00000000-0000-4000-8000-000000000000", PRACTICE_MODE: true, BANKROLL_SOL: 20 };
const importModule = name => {
  if (name === "MET Secrets" || name === "METSecrets") return secrets;
  if (name === "DLMM Rules" || name === "DLMMRules") return require(here("../DLMM Rules.js"));
  throw new Error("no module " + name);
};
const G = {
  config: { runsInWidget: false, widgetFamily: "large" }, args: { queryParameters: {}, widgetParameter: null },
  importModule, FileManager: FM, UITable: Table, UITableRow: Row, Alert,
  Font: loose(), Color: class { constructor(h) { this.hex = h; } static white() { return new this("#fff"); } }, Point: class { constructor(x, y) { this.x = x; this.y = y; } },
  Rect: class { constructor(...a) { this.a = a; } }, Size: class { constructor(...a) { this.a = a; } }, DrawContext: class { constructor() { return loose(); } },
  Path: class { constructor() { return loose(); } }, LinearGradient: class { constructor() { return loose(); } }, ListWidget: class { constructor() { return loose(); } },
  Script: { complete: noop, name: () => "MET Pool", setWidget: noop }, Safari: { open: u => console.log("  [Safari]", u) },
  Device: { screenSize: () => ({ width: 390, height: 844 }), isPhone: () => true }, Keychain: loose(), Pasteboard: loose(), Timer: { schedule: (ms, rep, cb) => setTimeout(cb, ms) },
};

const code = fs.readFileSync(here("../MET Pool.js"), "utf8");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const t0 = Date.now();
const run = async () => {
  const names = Object.keys(G);
  await new AsyncFunction(...names, code)(...names.map(n => G[n]));
};
try { await run(); } catch (e) { console.log("SCRIPT THREW:", e.stack || e); process.exit(1); }
if (process.argv.includes("--widget")) {
  // second run, same library: what the home-screen widget does with the cache the app just wrote
  G.config = { runsInWidget: true, widgetFamily: "large" };
  let set = 0;
  G.Script = { complete: noop, name: () => "MET Pool", setWidget: () => { set++; } };
  const w0 = Date.now();
  try { await run(); console.log(`WIDGET run ok: setWidget x${set}, ${((Date.now() - w0) / 1000).toFixed(1)}s`); } catch (e) { console.log("WIDGET THREW:", e.stack || e); process.exit(1); }
}

const T = tables[tables.length - 1];
const txt = r => r.cells.map(c => c.kind === "button" ? `[${c.title}]` : c.kind === "image" ? "~" : [c.title, c.subtitle].filter(x => x != null && x !== "").join(" / ")).join("  |  ");
const lines = T.rows.map(txt);
const head = lines.findIndex(l => /POOL/.test(l) && /TVL/.test(l));
console.log(lines.slice(0, head >= 0 ? head + 2 : 8).join("\n"));
console.log("   ...");
console.log(lines.slice(head + 2, head + 2 + ROWS * 4).join("\n"));
if (process.argv.includes("--dump")) {
  const cache = JSON.parse(fs.readFileSync(path.join(lib, "met_pools_data.json"), "utf8")).data.rows;
  console.log("DUMP " + JSON.stringify({ all: cache.length, v1: cache.filter(r => r.v && ["OPEN", "CAREFUL"].includes(r.v.level)).map(r => r.id).sort(), signal: cache.filter(r => r.sig && r.sig.hit).map(r => r.id).sort(), excluded: cache.filter(r => r.sig && r.sig.excluded).map(r => `${r.symbol}:${r.sig.why}`) }));
}
console.log(`\n${T.rows.length} UI rows, ${((Date.now() - t0) / 1000).toFixed(1)}s, library: ${fs.readdirSync(lib).join(", ")}`);
fs.rmSync(lib, { recursive: true, force: true });
process.exit(0);
