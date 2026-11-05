// MET Portfolio's exit rules, run outside Scriptable: node dlmm_terminal/scriptable/test_rules.mjs
// (The entry side, "DLMM Rules", is tested against Python in tests/test_dlmm_rules.mjs.)
import fs from "node:fs";
const port = fs.readFileSync(new URL("./MET Portfolio.js", import.meta.url), "utf8");
const grab = (src, start, end) => src.slice(src.indexOf(start), src.indexOf(end, src.indexOf(start)));
const exitSrc = grab(port, "const EXIT = (() =>", "function exitStatePath()") + grab(port, "function exitCheck(", "// Every row's positions");
const m = new Function("SECRETS", exitSrc + "; return { EXIT, exitCheck };")({});
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
