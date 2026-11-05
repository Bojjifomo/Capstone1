// "DLMM Rules" (phone) against Python's own answers. Run: node dlmm_terminal/scriptable/tests/test_dlmm_rules.mjs
// Re-record the Python side with: python3 dlmm_terminal/scriptable/tests/make_golden.py
import { createRequire } from "node:module";
import fs from "node:fs";
import assert from "node:assert/strict";

const require = createRequire(import.meta.url);
const R = require("../DLMM Rules.js");
const here = p => new URL(p, import.meta.url);
const G = JSON.parse(fs.readFileSync(here("./golden.json"), "utf8"));

let failures = 0, passed = 0;
function check(name, fn) {
  try { fn(); passed++; console.log("ok  ", name); } catch (e) { failures++; console.log("FAIL", name, "\n     ", String(e.message).split("\n").slice(0, 6).join("\n      ")); }
}
// Deep equality; numbers within 1e-9 (relative), everything else exact.
function same(a, b, path = "") {
  if (typeof a === "number" && typeof b === "number") {
    if (Math.abs(a - b) > 1e-9 * Math.max(1, Math.abs(a), Math.abs(b))) throw new Error(`${path}: ${a} != ${b}`);
    return;
  }
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") {
    if (a !== b) throw new Error(`${path}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`);
    return;
  }
  if (Array.isArray(a) !== Array.isArray(b)) throw new Error(`${path}: array vs object`);
  const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
  if (ka.join() !== kb.join()) throw new Error(`${path}: keys ${ka.join()} != ${kb.join()}`);
  ka.forEach(k => same(a[k], b[k], `${path}.${k}`));
}

// ---- defaults are config.json -----------------------------------------------------------------
check("DEFAULTS == config.json (notes stripped)", () => {
  const strip = o => (o && typeof o === "object" && !Array.isArray(o) ? Object.fromEntries(Object.entries(o).filter(([k]) => !k.startsWith("_")).map(([k, v]) => [k, strip(v)])) : o);
  same(R.DEFAULTS, strip(JSON.parse(fs.readFileSync(here("../../config.json"), "utf8"))));
});
check("DEFAULT_PROFILE is a usable bootstrap habitat for the configured wallet", () => {
  const p = R.DEFAULT_PROFILE;
  assert.ok(p, "no profile stamped: run python3 dlmm_terminal/scriptable/stamp_rules.py after `python3 -m dlmm_terminal benchmark`");
  assert.equal(p.wallet, R.DEFAULTS.signal.benchmarkWallet);
  assert.equal(p.days, R.DEFAULTS.signal.benchmarkDays);
  assert.ok(Object.keys(p.binSteps).length > 0);
  R.MATCH_DIMS.forEach(d => assert.ok(p.bands[d], "band " + d));
});

// ---- Secrets -> config ---------------------------------------------------------------------------
check("makeConfig: empty secrets keep the defaults, minTvl 2000", () => {
  const c = R.makeConfig({});
  same(c.gates, R.DEFAULTS.gates);
  assert.equal(c.scan.minTvlUsd, 2000);
});
check("makeConfig: overrides", () => {
  const c = R.makeConfig({ BANKROLL_SOL: 20, SPEC_RANGE_BOTTOM_PCT: 70, SPEC_MAX_PCT_OF_TVL: 3, SPEC_FLAG_NIGHT_TO: 6, SIGNAL_REVIVAL_MODE: "flag", SPEC_MIN_POOL_AGE_MIN: "" });
  assert.equal(c.position.bankrollSol, 20);
  assert.equal(c.position.rangeBottomPct, -70);
  assert.equal(c.gates.maxPositionPctOfTvl, 3);
  assert.equal(c.gates.minPoolAgeMinutes, 60);
  assert.deepEqual(c.flags.nightEntryLocalHours, [0, 6]);
  assert.equal(c.signal.revival.mode, "flag");
  assert.ok(Math.abs(R.maxPositionSol(c) - 20 * 5 / 100 / 0.9) < 1e-12);
});
check("plannedSizeSol caps at the loss budget", () => {
  const c = R.makeConfig({ BANKROLL_SOL: 160 });
  assert.equal(R.plannedSizeSol(c, 20), 8.8889);
  assert.equal(R.plannedSizeSol(c, null), 8);
});

// ---- the golden pools -----------------------------------------------------------------------------
const base = G.cfg, sized = JSON.parse(JSON.stringify(G.cfg));
sized.gates.maxPositionPctOfTvl = 3;
G.pools.forEach((k, i) => {
  const name = `pool ${i} ${k.snap.name} [${k.cfg}]`;
  const cfg = k.cfg === "sized" ? sized : base;
  check(`${name}: candidate`, () => {
    const c = R.evaluate(k.snap, cfg, G.now, { sizeSol: k.size, changes: k.changes });
    Object.assign(c, R.candidateDims(k.snap));
    same(c, k.expected.c);
  });
  check(`${name}: benchmark match`, () => {
    const c = Object.assign(R.evaluate(k.snap, cfg, G.now, { sizeSol: k.size, changes: k.changes }), R.candidateDims(k.snap));
    same(R.matchBenchmark(c, G.profile, cfg.signal.bandOverrides), k.expected.match);
  });
  check(`${name}: verdict ${k.expected.verdict.level}`, () => {
    const c = R.evaluate(k.snap, cfg, G.now, { sizeSol: k.size, changes: k.changes });
    same(R.verdict(c, k.market), k.expected.verdict);
  });
});
check("the golden set covers every verdict level and both pool orientations", () => {
  const levels = new Set(G.pools.map(k => k.expected.verdict.level));
  ["OPEN", "CAREFUL", "WAIT", "SKIP"].forEach(l => assert.ok(levels.has(l), "missing " + l));
  const sides = new Set(G.pools.map(k => k.expected.c.solSide));
  assert.ok(sides.has("y") && sides.has("x") && sides.has(null), [...sides].join());
});

// ---- dead-then-alive --------------------------------------------------------------------------------
G.activity.forEach(a => {
  check(`activity ${a.name} age ${a.ageH}`, () => {
    const f = R.activityFeatures(a.hourly, a.ageH, G.cfg.signal.revival.deadHourPct);
    same(f, a.expected);
    assert.equal(R.revived(f, G.cfg.signal.revival), a.revived);
    if (f) assert.equal(R.describeActivity(f), a.text);
  });
});
check("applyRevival: gate drops, flag keeps with a warning, off ignores", () => {
  const a = G.activity.find(x => x.revived);
  assert.ok(a, "golden has no revived series");
  const mk = () => ({ poolAgeHours: a.ageH, gatesFailed: [], flags: [], decision: "propose" });
  for (const [mode, keep] of [["gate", false], ["flag", true], ["off", true]]) {
    const cfg = JSON.parse(JSON.stringify(G.cfg)); cfg.signal.revival.mode = mode;
    const c = mk();
    assert.equal(R.applyRevival(c, a.hourly, cfg), keep, mode);
    if (mode === "gate") { assert.equal(c.decision, "skip"); assert.ok(c.gatesFailed[0].startsWith("revival: dead→alive")); }
    if (mode === "flag") assert.ok(c.flags[0].startsWith("dead→alive"));
    if (mode === "off") assert.equal(c.activity, undefined);
  }
});

// ---- benchmark ---------------------------------------------------------------------------------------
check("profile(entries) == Python's profile", () => {
  const e = G.profileCase.expected;
  same(R.profile(G.profileCase.entries, e.wallet, e.days, Date.parse(e.builtAt)), e);
});
check("groupEntries + entryFeatures == Python's", () => {
  const c = G.entryCase;
  const groups = R.groupEntries(c.positions);
  same(groups.map(g => g.map(p => p.positionAddress)), c.groups);
  same(groups.map(g => R.entryFeatures(g, c.snap, c.vol, c.candles, c.side)), c.expected);
});

// ---- the builder, with a fake network that replays the fixture --------------------------------------
check("buildBenchmark walks portfolio -> positions -> history and returns the same entries", async () => {});
await (async () => {
  const c = G.entryCase, addr = c.snap.address, wallet = "W";
  const since = Math.min(...c.positions.map(p => p.createdAt)) + 40 * 86400 - 1;   // `days`=40 from a fake "now"
  const now = Math.min(...c.positions.map(p => p.createdAt)) + 39 * 86400;
  const calls = [];
  const net = { async getJson(url) {
    calls.push(url);
    const u = new URL(url), p = u.pathname;
    if (p === "/portfolio") return { pools: [{ poolAddress: addr, tokenXMint: c.side === "y" ? "T" : R.SOL_MINT, tokenYMint: c.side === "y" ? R.SOL_MINT : "T", tokenX: "X", tokenY: "Y", lastClosedAt: now }], hasNext: false };
    if (p === `/positions/${addr}/pnl`) return { positions: c.positions, hasNext: false };
    if (p === `/pools/${addr}`) return c.snap;
    if (p === `/pools/${addr}/volume/history`) return { data: c.vol.filter(r => r.timestamp >= +u.searchParams.get("start_time") && r.timestamp <= +u.searchParams.get("end_time")) };
    if (p === `/pools/${addr}/ohlcv`) return { data: c.candles.filter(r => r.timestamp >= +u.searchParams.get("start_time") && r.timestamp <= +u.searchParams.get("end_time")) };
    throw new Error("unexpected " + url);
  } };
  try {
    const out = await R.buildBenchmark(net, wallet, 40, now);
    same(out.entries, c.expected);
    assert.equal(out.profile.entries, c.expected.length);
    assert.ok(calls.some(u => u.includes("status=closed")));
    passed++; console.log("ok   buildBenchmark (fake network replaying the fixture)");
  } catch (e) { failures++; console.log("FAIL buildBenchmark", String(e.message).split("\n").slice(0, 6).join("\n      ")); }
})();

console.log(`\n${passed} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
