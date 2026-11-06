// MET Portfolio's exit rules, run outside Scriptable: node dlmm_terminal/scriptable/test_rules.mjs
// (The entry side, "DLMM Rules", is tested against Python in tests/test_dlmm_rules.mjs.)
import fs from "node:fs";
const port = fs.readFileSync(new URL("./MET Portfolio.js", import.meta.url), "utf8");
const grab = (src, start, end) => src.slice(src.indexOf(start), src.indexOf(end, src.indexOf(start)));
const exitSrc = grab(port, "function exitFrom(", "const EXIT = exitFrom(") + grab(port, "function exitCheck(", "// Every row's positions");
const m = new Function("SECRETS", exitSrc + "; const EXIT = exitFrom(null); return { EXIT, exitCheck, exitFrom };")({});
const assert = (c, msg) => { if (!c) { console.error("FAIL", msg); process.exitCode = 1; } else console.log("ok  ", msg); };
// exits (SOL is Y: range below price)
const P = { solIsX: false, lower: -370, upper: -301, active: -320, depositSol: 8, pnlSol: 0.1, depositTokenUi: 0 };
let st = {}; let e = m.exitCheck(P, st, 0);
assert(!e.exit && e.alerts.length === 0, "in range, no alerts");
e = m.exitCheck({ ...P, active: -360 }, st, 60e3);
assert(e.alerts.some(a => a.key === "third" && a.fresh), "bottom third alert");
e = m.exitCheck({ ...P, active: -380 }, st, 120e3);
assert(e.alerts.some(a => a.key === "out") && !e.exit, "below range alert, no exit yet");
e = m.exitCheck({ ...P, active: -380 }, st, 120e3 + 10 * 60e3);
assert(e.exit && e.exit.rule === "belowRange", "exit after 10 min below range");
e = m.exitCheck({ ...P, active: -380, depositTokenUi: 5 }, {}, 0); const st2 = {};
m.exitCheck({ ...P, active: -380, depositTokenUi: 5 }, st2, 0); e = m.exitCheck({ ...P, active: -380, depositTokenUi: 5 }, st2, 30 * 60e3);
assert(!e.exit && e.alerts.length === 0, "token-side position: range rules skipped");
e = m.exitCheck({ ...P, pnlSol: -0.9 }, {}, 0);
assert(e.alerts.some(a => a.key === "pnl"), "PnL -11% alert");
// SOL is X: SOL-only sits above, token falling moves active up
const X = { ...P, solIsX: true, lower: 301, upper: 370, active: 290 };
st = {}; m.exitCheck({ ...X, active: 380 }, st, 0); e = m.exitCheck({ ...X, active: 380 }, st, 11 * 60e3);
assert(e.exit && e.exit.rule === "belowRange", "SOL-as-X orientation");
assert(m.EXIT.takeProfitPct === null && m.EXIT.stopLossPct === null && m.EXIT.mode === "alert", "TP/SL off, alert mode by default");

// ⚙ Settings: the exit rules from DLMM Rules' config (MET Secrets + the saved overlay) equal the Secrets-only ones by default.
import { createRequire } from "node:module";
const R = createRequire(import.meta.url)("./DLMM Rules.js");
const fromCfg = m.exitFrom(R.makeConfig({}));
assert(JSON.stringify(fromCfg) === JSON.stringify(m.EXIT), "exit rules: config defaults == MET Secrets defaults");
const ov = m.exitFrom(R.applySettings(R.makeConfig({ EXIT_ALERT_PNL_PCT: -8 }), { "exits.closeBelowRangeAfterMin": null, "exits.takeProfitPct": 5 }));
assert(ov.alertAtPnlPct === -8 && ov.closeBelowRangeAfterMin === null && ov.takeProfitPct === 5, "exit rules: Secrets then overlay");
assert(R.cleanSetting("position.ladderSlices", "3").value === 3 && R.cleanSetting("position.ladderSlices", "30").error && R.cleanSetting("trade.maxLossPct", "2,5").value === 2.5, "settings: clean");
assert(R.cleanSetting("exits.stopLossPct", "").value === null && R.cleanSetting("nope.x", 1).error, "settings: empty = off, unknown refused");
const c2 = R.applySettings(R.makeConfig({}), { "flags.nightEntryLocalHours.1": 7, "signal.blastTop": 5, "bogus.key": 1 });
assert(c2.flags.nightEntryLocalHours[1] === 7 && c2.signal.blastTop === 5 && !c2.bogus, "settings: apply (list index, unknown ignored)");
const mm = R.matchBenchmark({ binStep: 80, tokenAgeHours: 5, mcapUsd: 500000, volume24hUsd: 60000, totalFeesSol: 70 }, R.DEFAULT_PROFILE,
  R.DEFAULTS.signal.bandOverrides, R.DEFAULTS.signal.matchDims, false);
assert(mm.score === 1 && mm.dims === 4, "PING: GMGN bands only (no bin-step check)");
assert(R.pingFilterText(R.makeConfig({}), 10, 12, 1) === "Filtered by: SOL pair, bin 20-250, pool age ≥ 60m, fee APR 24h ≥ 1,000%, token age 1h–250h, mcap $400.0k–$10.00M, vol 24h ≥ $50.0k, total fees ≥ 50 SOL. Excluded 1 dead→alive pool(s). Top 10 of 12, sorted by 1h Vol/TVL.", "PING: filter text = the Mac's");
