// node dlmm_terminal/executor/test_ladder.mjs — the RH ladder plan (no RPC).
import assert from "node:assert/strict";
import { planLadder } from "./ladder.mjs";

const p = planLadder({ activeId: -466, binStep: 80, solIsX: false, lamports: 8_000_000_000n, slices: 5, top: 0, range: 70 });
assert.equal(p.slices.length, 5);
assert.equal(p.slices.reduce((s, x) => s + x.lamports, 0n), 8_000_000_000n, "every lamport placed");
const sol = p.slices.map(s => Number(s.lamports) / 1e9);
for (let i = 0; i < 5; i++) assert.ok(Math.abs(sol[i] - 8 * (i + 1) / 15) < 1e-6, `slice ${i + 1} holds ${i + 1}/15`);
assert.equal(p.slices[0].hi, -466, "slice 1 starts at the active bin");
for (let i = 1; i < 5; i++) assert.equal(p.slices[i].hi, p.slices[i - 1].lo - 1, "slices touch, no gap");
const widths = p.slices.map(s => s.width);
assert.ok(Math.max(...widths) - Math.min(...widths) <= 1, "equal widths");
assert.ok(Math.abs(p.realEnd - 70) < 0.5, "reaches -70%");
// SOL as X: the ladder sits above the active bin
const x = planLadder({ activeId: 100, binStep: 100, solIsX: true, lamports: 1_000_000_000n, slices: 5, top: 0, range: 70 });
assert.equal(x.slices[0].lo, 100);
assert.ok(x.slices.every((s, i) => i === 0 || s.lo === x.slices[i - 1].hi + 1));
// a band too wide for 5 x 70 bins gets more slices
const w = planLadder({ activeId: 0, binStep: 10, solIsX: false, lamports: 1_000_000_000n, slices: 5, top: 0, range: 70 });
assert.ok(w.raised > 5 && w.slices.every(s => s.width <= 70), "raised slices, 70 bins max each");
console.log("ladder tests ok");
