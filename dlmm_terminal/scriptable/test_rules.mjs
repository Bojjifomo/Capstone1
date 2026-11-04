// Spec v1 rules inside MET Pool / MET Portfolio, run outside Scriptable: node dlmm_terminal/scriptable/test_rules.mjs
import fs from "node:fs";
const pool = fs.readFileSync(new URL("./MET Pool.js", import.meta.url), "utf8"), port = fs.readFileSync(new URL("./MET Portfolio.js", import.meta.url), "utf8");
const grab = (src, start, end) => src.slice(src.indexOf(start), src.indexOf(end, src.indexOf(start)));
const fmtCompact = v => String(Math.round(v)), SECRETS = { BANKROLL_SOL: 160 }, ZAP_PRESETS = [{ amount: 8 }, { amount: 20 }];
const specSrc = grab(pool, "const SPEC = (() =>", "// Every candidate the app ranks");
const exitSrc = grab(port, "const EXIT = (() =>", "function exitStatePath()") + grab(port, "function exitCheck(", "// Every row's positions");
const f = new Function("SECRETS", "fmtCompact", "ZAP_PRESETS", specSrc + exitSrc + "; return { SPEC, SPEC_MAX_POSITION_SOL, specCheck, specRowSize, EXIT, exitCheck };");
const m = f(SECRETS, fmtCompact, ZAP_PRESETS);
const assert = (c, msg) => { if (!c) { console.error("FAIL", msg); process.exitCode = 1; } else console.log("ok  ", msg); };
assert(Math.abs(m.SPEC_MAX_POSITION_SOL - 8.8889) < 1e-3, "loss budget cap 8.89");
assert(m.specRowSize() === m.SPEC_MAX_POSITION_SOL, "row size capped by budget");
const now = Date.UTC(2026, 10, 4, 8); // 15:00 WIB
const good = { binStep: 100, ageDays: 2, tvl: 100000, fees24: 4000, solUsd: 93, chg1h: 3, chg6h: 2 };
let c = m.specCheck(good, 8, now);
assert(c.ok && c.flags.length === 0 && Math.round(c.feeApr) === 1460, "good pool passes, APR 1460%");
c = m.specCheck({ ...good, ageDays: 0.5 / 24, binStep: 10, tvl: 20000, fees24: 10, chg1h: 30 }, 8, Date.UTC(2026, 10, 4, 20));
assert(!c.ok && c.gates.length === 4 && c.flags.length === 3, "bad pool: 4 gates + pump/tvl/night flags: " + c.gates.join("|") + " / " + c.flags.join("|"));
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
