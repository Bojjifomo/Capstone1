// icon-color: deep-purple; icon-glyph: filter;
// ============================================================================
//  DLMM Rules : shared logic for "MET Pool" (and anything else on the phone)
//
//  Name this script exactly "DLMM Rules" in Scriptable. It has no UI and holds no
//  key; MET Pool imports it. It is the JavaScript twin of the Mac terminal's
//  filters.py / web.py verdict / benchmark.py / activity.py, and test_rules.mjs
//  checks it against Python's own output, so the phone and the Mac judge a pool
//  the same way.
//
//  What is in here
//    config      defaults = dlmm_terminal/config.json (stamped by stamp_rules.py),
//                overridable from "MET Secrets"
//    layer 1     v1 gates + flags -> OPEN / CAREFUL / WAIT / SKIP (+ reasons)
//    layer 2     benchmark wallet "habitat" match + the dead-then-alive (revival) gate
//    benchmark   build the habitat profile from a wallet's closed positions
// ============================================================================
"use strict";

const SOL_MINT = "So11111111111111111111111111111111111111112";
const DATAPI = "https://dlmm.datapi.meteora.ag";

// ---------------------------------------------------------------- defaults ---
// Stamped from dlmm_terminal/config.json by stamp_rules.py. Do not edit by hand.
//__DEFAULTS_BEGIN__
const DEFAULTS = {
  "universe": {
    "protocol": "meteora-dlmm",
    "quote": "SOL",
    "binStepMin": 20,
    "binStepMax": 250
  },
  "gates": {
    "minPoolAgeMinutes": 60,
    "minFeeApr24hPct": 1000,
    "maxPositionPctOfTvl": null
  },
  "position": {
    "strategy": "Spot",
    "side": "SOL-only",
    "rangeBottomPct": -70,
    "bankrollSol": 160,
    "lossBudgetPct": 5,
    "defaultSizeSol": 8,
    "ladderSlices": 5
  },
  "flags": {
    "pump1hPct": 25,
    "drop6hPct": -17,
    "minTvlUsd": 25000,
    "nightEntryLocalHours": [
      0,
      9
    ],
    "utcOffsetHours": 7
  },
  "scan": {
    "pages": 2,
    "pageSize": 100,
    "prefilterMinTvlUsd": 1000
  },
  "signal": {
    "benchmarkWallet": "WKX8JSQ7K4XNPHN24MjrDCi8fRVTr4KFkh4aa3TZmpt",
    "benchmarkDays": 20,
    "refreshBenchmarkHours": 24,
    "mode": "blast",
    "blastEveryMin": 30,
    "blastTop": 10,
    "blastSortBy": "volTvl1h",
    "pollEveryMin": 5,
    "minMatchScore": 1.0,
    "cooldownHours": 6,
    "pingWalletEntries": true,
    "revival": {
      "mode": "gate",
      "minDeadHours": 6,
      "minTop3Share": 0.7,
      "deadHourPct": 0.5
    },
    "bandOverrides": {
      "mcapUsd": {
        "lo": 300000,
        "hi": 10000000
      },
      "fees24hUsd": {
        "lo": 200,
        "hi": null
      },
      "fees1hUsd": {
        "lo": 20,
        "hi": null
      }
    }
  },
  "exits": {
    "mode": "alert",
    "pollEveryMin": 5,
    "alertAtPnlPct": -10,
    "alertBottomThirdOfRange": true,
    "closeBelowRangeAfterMin": 10,
    "priceStopBelowEntryPct": null,
    "takeProfitPct": null,
    "stopLossPct": null,
    "trailingPts": null,
    "trailingArmPct": 1,
    "maxHoldMin": null
  }
};
//__DEFAULTS_END__

// Bootstrap habitat so Signal works before the first benchmark build on the phone.
// Stamped from dlmm_terminal/data/benchmark.json; MET Pool rebuilds it every
// signal.refreshBenchmarkHours.
//__PROFILE_BEGIN__
const DEFAULT_PROFILE = {
 "wallet": "WKX8JSQ7K4XNPHN24MjrDCi8fRVTr4KFkh4aa3TZmpt",
 "days": 20,
 "builtAt": "2026-11-05T09:34:08+00:00",
 "entries": 90,
 "tokens": 28,
 "wins": 79,
 "winRatePct": 87.8,
 "pnlSol": 16.3277,
 "capitalSolMedian": 10.456,
 "binSteps": {
  "100": {
   "entries": 48,
   "wins": 44,
   "pnlSol": 9.3713
  },
  "125": {
   "entries": 10,
   "wins": 7,
   "pnlSol": 1.4964
  },
  "250": {
   "entries": 4,
   "wins": 3,
   "pnlSol": 0.5718
  },
  "200": {
   "entries": 24,
   "wins": 22,
   "pnlSol": 4.0594
  },
  "80": {
   "entries": 3,
   "wins": 3,
   "pnlSol": 0.8288
  },
  "20": {
   "entries": 1,
   "wins": 0,
   "pnlSol": 0.0
  }
 },
 "bands": {
  "poolAgeHours": {
   "lo": 1.32,
   "hi": 192.52,
   "median": 21.39,
   "winMedian": 20.78
  },
  "mcapUsd": {
   "lo": 210144.1,
   "hi": 2152733.5,
   "median": 704285.5,
   "winMedian": 714140
  },
  "fees24hUsd": {
   "lo": 231.27,
   "hi": 24585.39,
   "median": 6679.46,
   "winMedian": 6742.51
  },
  "fees1hUsd": {
   "lo": 19.08,
   "hi": 2594.12,
   "median": 269.07,
   "winMedian": 290.87
  },
  "priceChg1h": {
   "lo": -13.79,
   "hi": 55.7,
   "median": 5.1,
   "winMedian": 5.61
  },
  "priceChg6h": {
   "lo": -21.53,
   "hi": 108.82,
   "median": 29.53,
   "winMedian": 29.53
  },
  "holdMin": {
   "lo": 25.99,
   "hi": 476.81,
   "median": 66.6,
   "winMedian": 75.4
  },
  "pnlPct": {
   "lo": 0.0,
   "hi": 6.04,
   "median": 0.73,
   "winMedian": 0.91
  }
 },
 "quantiles": [
  0.1,
  0.9
 ]
};
//__PROFILE_END__

// ----------------------------------------------- Python-compatible numbers ---
// Python rounds half to even on exact ties (round(2.5) = 2, f"{2.5:.0f}" = "2");
// JavaScript's toFixed rounds them up. Same text on both sides matters for the golden tests.
// A true tie is when x * 10^n is exactly a half-integer in binary, so it is decided with BigInt, not
// with float multiplication (269.075 is not a tie: its binary value is a hair under).
function tieQuotient(ax, n) {
  const dv = new DataView(new ArrayBuffer(8));
  dv.setFloat64(0, ax);
  const hi = dv.getUint32(0), lo = dv.getUint32(4), exp = (hi >>> 20) & 0x7ff;
  let mant = (BigInt(hi & 0xfffff) << 32n) | BigInt(lo), e;
  if (exp === 0) e = -1074; else { mant |= 1n << 52n; e = exp - 1075; }
  let two = mant * 2n * 10n ** BigInt(n);                 // 2 * x * 10^n = two * 2^e
  if (e >= 0) two <<= BigInt(e);
  else { const sh = BigInt(-e); if (two % (1n << sh) !== 0n) return null; two >>= sh; }
  return two % 2n === 1n ? (two - 1n) / 2n : null;        // the integer just below the half-way point
}
function pyFixed(x, n) {
  if (x == null || !Number.isFinite(x)) return String(x);
  const q = tieQuotient(Math.abs(x), n);
  if (q !== null) {
    const r = q % 2n === 0n ? q : q + 1n;                 // half to even
    const s = r.toString().padStart(n + 1, "0");
    return (x < 0 ? "-" : "") + (n ? s.slice(0, s.length - n) + "." + s.slice(s.length - n) : s);
  }
  return x.toFixed(n);
}
const pyRound = (x, n) => (x == null ? null : Number(pyFixed(x, n)));
const commas = s => String(s).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
// Python's str() of a float / None, for the few places a number lands in a reason text.
const pyStr = x => (x == null ? "None" : Number.isInteger(x) ? x.toFixed(1) : String(x));
const num = v => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));

// ------------------------------------------------------------------ config ---
const clone = o => JSON.parse(JSON.stringify(o));

// secrets: the object "MET Secrets" exports ({} is fine). Every key is optional.
function makeConfig(secrets) {
  const cfg = clone(DEFAULTS);
  const s = secrets || {};
  const n = k => (s[k] === undefined || s[k] === null || s[k] === "" ? undefined : num(s[k]));
  const set = (obj, key, v) => { if (v !== undefined && v !== null) obj[key] = v; };
  set(cfg.universe, "binStepMin", n("SPEC_BIN_STEP_MIN"));
  set(cfg.universe, "binStepMax", n("SPEC_BIN_STEP_MAX"));
  set(cfg.gates, "minPoolAgeMinutes", n("SPEC_MIN_POOL_AGE_MIN"));
  set(cfg.gates, "minFeeApr24hPct", n("SPEC_MIN_FEE_APR_PCT"));
  set(cfg.gates, "maxPositionPctOfTvl", n("SPEC_MAX_PCT_OF_TVL"));   // a number turns the size-vs-TVL gate on
  set(cfg.flags, "pump1hPct", n("SPEC_FLAG_PUMP_1H_PCT"));
  set(cfg.flags, "drop6hPct", n("SPEC_FLAG_DROP_6H_PCT"));
  set(cfg.flags, "minTvlUsd", n("SPEC_FLAG_MIN_TVL_USD"));
  const nf = n("SPEC_FLAG_NIGHT_FROM"), nt = n("SPEC_FLAG_NIGHT_TO");
  if (nf !== undefined || nt !== undefined) cfg.flags.nightEntryLocalHours = [nf !== undefined ? nf : cfg.flags.nightEntryLocalHours[0], nt !== undefined ? nt : cfg.flags.nightEntryLocalHours[1]];
  set(cfg.flags, "utcOffsetHours", n("SPEC_UTC_OFFSET"));
  const rb = n("SPEC_RANGE_BOTTOM_PCT");                              // written positive in Secrets (70)
  if (rb !== undefined) cfg.position.rangeBottomPct = -Math.abs(rb);
  set(cfg.position, "bankrollSol", n("BANKROLL_SOL"));
  set(cfg.position, "lossBudgetPct", n("LOSS_BUDGET_PCT"));
  set(cfg.position, "defaultSizeSol", n("DEFAULT_SIZE_SOL"));
  set(cfg.position, "ladderSlices", n("LADDER_SLICES"));
  set(cfg.scan, "minTvlUsd", n("SCAN_MIN_TVL_USD"));
  const sig = cfg.signal;
  if (typeof s.BENCHMARK_WALLET === "string" && s.BENCHMARK_WALLET.trim()) sig.benchmarkWallet = s.BENCHMARK_WALLET.trim();
  set(sig, "benchmarkDays", n("BENCHMARK_DAYS"));
  set(sig, "minMatchScore", n("SIGNAL_MIN_MATCH_SCORE"));
  set(sig, "blastTop", n("SIGNAL_TOP"));
  if (typeof s.SIGNAL_SORT_BY === "string" && s.SIGNAL_SORT_BY.trim()) sig.blastSortBy = s.SIGNAL_SORT_BY.trim();
  if (["gate", "flag", "off"].includes(String(s.SIGNAL_REVIVAL_MODE || "").toLowerCase())) sig.revival.mode = String(s.SIGNAL_REVIVAL_MODE).toLowerCase();
  if (cfg.scan.minTvlUsd == null) cfg.scan.minTvlUsd = 2000;         // the web's default TVL floor
  return cfg;
}

function maxPositionSol(cfg) {
  // One bad position fell 93% (spec), so one position may hold at most loss budget / 0.9.
  const p = cfg.position;
  return p.bankrollSol * p.lossBudgetPct / 100 / 0.9;
}
function plannedSizeSol(cfg, requested) {
  const want = requested != null ? requested : (cfg.position.defaultSizeSol != null ? cfg.position.defaultSizeSol : maxPositionSol(cfg));
  return pyRound(Math.min(want, maxPositionSol(cfg)), 4);
}

// ----------------------------------------------------------- layer 1: v1 ---
function solSide(p) {
  if (p.token_y && p.token_y.address === SOL_MINT) return "y";
  if (p.token_x && p.token_x.address === SOL_MINT) return "x";
  return null;
}
// Pool price is token X priced in token Y. Return the memecoin priced in SOL.
function toTokenInSol(poolPrice, side) {
  if (!poolPrice) return 0;
  return side === "y" ? poolPrice : 1 / poolPrice;
}
function feeApr(feesUsd, tvl, hours) {
  if (tvl <= 0 || hours <= 0) return 0;
  return feesUsd / tvl * 8760 / hours * 100;
}
function windowFeeApr(p, windowH, ageH) {
  const fees = ((p.fees || {})[windowH + "h"]) || 0;
  const hours = ageH > 0 ? Math.min(windowH, ageH) : windowH;   // a younger pool is judged on the hours it has
  return feeApr(fees, p.tvl || 0, Math.max(hours, 1 / 60));
}

// p: one raw row of the Meteora pools API. changes: {"1h","6h","24h"} price moves in % (DexScreener).
function features(p, sizeSol, nowMs, changes) {
  const side = solSide(p);
  const tok = side ? (side === "y" ? p.token_x : p.token_y) : (p.token_x || {});
  const solTok = side ? (side === "y" ? p.token_y : p.token_x) : null;
  const solUsd = solTok ? (solTok.price || 0) : 0;
  const tvl = p.tvl || 0;
  const ageH = (nowMs - (p.created_at || 0)) / 3600000;
  const cfgp = p.pool_config || {};
  const ch = changes || {};
  const pick = k => (ch[k] == null ? null : pyRound(Number(ch[k]), 2));
  return {
    pool: p.address,
    name: p.name,
    tokenMint: tok.address,
    symbol: tok.symbol,
    solSide: side,
    poolAgeHours: pyRound(ageH, 2),
    tvlUsd: pyRound(tvl, 2),
    feeApr1h: pyRound(windowFeeApr(p, 1, ageH), 1),
    feeApr24h: pyRound(windowFeeApr(p, 24, ageH), 1),
    volTvl24h: tvl ? pyRound((((p.volume || {})["24h"]) || 0) / tvl, 2) : null,
    mcapUsd: tok.market_cap == null ? null : tok.market_cap,
    priceChg1h: pick("1h"),
    priceChg6h: pick("6h"),
    priceChg24h: pick("24h"),
    range24hPct: null,
    binStep: cfgp.bin_step == null ? null : cfgp.bin_step,
    baseFeePct: cfgp.base_fee_pct == null ? null : cfgp.base_fee_pct,
    solUsd,
    sizeSol,
    sizeVsTvlPct: tvl && solUsd ? pyRound(sizeSol * solUsd / tvl * 100, 2) : null,
    entryHourLocal: null,
    holders: tok.holders == null ? null : tok.holders,
    priceSol: toTokenInSol(p.current_price || 0, side),
  };
}

function gates(f, cfg) {
  const u = cfg.universe, g = cfg.gates;
  const checks = [
    ["solQuoted", f.solSide != null],
    ["binStep", f.binStep != null && u.binStepMin <= f.binStep && f.binStep <= u.binStepMax],
    ["poolAge", f.poolAgeHours * 60 >= g.minPoolAgeMinutes],
    ["feeApr24h", f.feeApr24h >= g.minFeeApr24hPct],
  ];
  // Size vs TVL is a gate only when configured (the owner turned it off; it stays visible as info).
  if (g.maxPositionPctOfTvl != null) checks.push(["sizeVsTvl", f.sizeVsTvlPct != null && f.sizeVsTvlPct <= g.maxPositionPctOfTvl]);
  const why = {
    solQuoted: () => "solQuoted: SOL not in pair",
    binStep: () => `binStep: ${f.binStep == null ? "None" : f.binStep} outside ${u.binStepMin}-${u.binStepMax}`,
    poolAge: () => `poolAge: ${pyFixed(f.poolAgeHours * 60, 0)}m < ${g.minPoolAgeMinutes}m`,
    feeApr24h: () => `feeApr24h: ${pyFixed(f.feeApr24h, 0)}% < ${g.minFeeApr24hPct}%`,
    sizeVsTvl: () => `sizeVsTvl: ${pyStr(f.sizeVsTvlPct)}% > ${g.maxPositionPctOfTvl}%`,
  };
  return { passed: checks.filter(c => c[1]).map(c => c[0]), failed: checks.filter(c => !c[1]).map(c => why[c[0]]()) };
}

function flags(f, cfg) {
  const fl = cfg.flags, out = [];
  if (f.priceChg1h != null && f.priceChg1h >= fl.pump1hPct) out.push(`pump1h +${pyFixed(f.priceChg1h, 0)}%`);
  if (f.priceChg6h != null && f.priceChg6h <= fl.drop6hPct) out.push(`drop6h ${pyFixed(f.priceChg6h, 0)}%`);
  if (f.tvlUsd < fl.minTvlUsd) out.push(`tvl $${commas(pyFixed(f.tvlUsd, 0))}`);
  const [start, end] = fl.nightEntryLocalHours;
  if (f.entryHourLocal != null && start <= f.entryHourLocal && f.entryHourLocal < end) out.push(`night entry ${String(f.entryHourLocal).padStart(2, "0")}h`);
  return out;
}

function isoSeconds(ms) { return new Date(ms).toISOString().slice(0, 19) + "+00:00"; }

// One candidate row, in the same shape as Python's filters.evaluate.
function evaluate(p, cfg, nowMs, opts) {
  const o = opts || {};
  const size = plannedSizeSol(cfg, o.sizeSol);
  const f = features(p, size, nowMs, o.changes);
  f.entryHourLocal = new Date(nowMs + (cfg.flags.utcOffsetHours == null ? 7 : cfg.flags.utcOffsetHours) * 3600000).getUTCHours();
  const g = gates(f, cfg);
  const fl = flags(f, cfg);
  const bottom = cfg.position.rangeBottomPct;
  return Object.assign({ ts: isoSeconds(nowMs) }, f, {
    gatesPassed: g.passed,
    gatesFailed: g.failed,
    flags: fl,
    decision: g.failed.length ? "skip" : "propose",
    decisionReason: g.failed.length ? g.failed.join("; ") : "all gates pass (assist mode: you confirm)",
    rangeType: bottom <= -65 ? "wide" : "standard",
    plan: {
      strategy: cfg.position.strategy, side: cfg.position.side, sizeSol: size, rangeBottomPct: bottom,
      rangeBottomPriceSol: f.priceSol ? f.priceSol * (1 + bottom / 100) : null,
    },
  });
}

// Same candidate, a different size or a later moment (the zap sheet lets you change the size).
function recheck(c, cfg, sizeSol, nowMs) {
  const f = Object.assign({}, c, { sizeSol, sizeVsTvlPct: c.tvlUsd && c.solUsd ? pyRound(sizeSol * c.solUsd / c.tvlUsd * 100, 2) : null });
  f.entryHourLocal = new Date(nowMs + (cfg.flags.utcOffsetHours == null ? 7 : cfg.flags.utcOffsetHours) * 3600000).getUTCHours();
  const g = gates(f, cfg);
  return Object.assign(f, { gatesPassed: g.passed, gatesFailed: g.failed, flags: flags(f, cfg) });
}

// OPEN / CAREFUL / WAIT / SKIP, plus the reasons.
//   SKIP     a gate that won't change (SOL not in pair, bin step outside the range)
//   WAIT     a gate that can change (pool too young, fee APR too low)
//   CAREFUL  gates pass, some flag shows
//   OPEN     gates pass, no flags
// m: market context from DexScreener {vol1h, vol24, buys24, sells24}. It is context only: the spec found no
// entry signal that held up on unseen tokens, so it never changes the level.
function verdict(c, m) {
  const hard = c.gatesFailed.filter(g => g.startsWith("solQuoted") || g.startsWith("binStep"));
  const level = hard.length ? "SKIP" : c.gatesFailed.length ? "WAIT" : c.flags.length ? "CAREFUL" : "OPEN";
  const bad = c.gatesFailed.map(g => ({ ok: false, text: g })).concat(c.flags.map(f => ({ ok: false, text: f, flag: true })));
  const good = [];
  if (!c.gatesFailed.length) good.push({ ok: true, text: `Gates pass: fee APR ${commas(pyFixed(c.feeApr24h, 0))}% · ${pyStr(c.sizeVsTvlPct)}% of TVL · ${pyFixed(c.poolAgeHours, 1)}h old` });
  const info = [];
  if (m) {
    const v1 = m.vol1h || 0, v24 = m.vol24 || 0;
    if (v24 && v1 * 24 >= 1.5 * v24) info.push({ ok: true, text: `Volume picking up: last hour runs ${pyFixed(v1 * 24 / v24, 1)}x the 24h pace` });
    else if (v24 && v1 * 24 <= 0.4 * v24) info.push({ ok: false, text: `Quiet: last hour runs ${pyFixed(v1 * 24 / v24, 1)}x the 24h pace` });
    const b = m.buys24 || 0, s = m.sells24 || 0;
    if (b + s >= 50) {
      const share = b / (b + s) * 100;
      if (share >= 55) info.push({ ok: true, text: `Buyers lead: ${pyFixed(share, 0)}% of 24h trades are buys` });
      else if (share <= 45) info.push({ ok: false, text: `Sellers lead: ${pyFixed(100 - share, 0)}% of 24h trades are sells` });
    }
  }
  const score = good.length + info.filter(x => x.ok).length - bad.length - info.filter(x => !x.ok).length;
  return { level, score, reasons: bad.concat(good), info };
}

// Terse reason -> something readable on a phone.
function nice(t) {
  let m;
  if ((m = /^sizeVsTvl: (.*)% > (.*)%/.exec(t))) return `Size ${(+m[1]).toFixed(1)}% of TVL (max ${m[2]}%)`;
  if ((m = /^poolAge: (\d+)m < (\d+)m/.exec(t))) return `Pool ${m[1]}m old (min ${m[2]}m)`;
  if ((m = /^feeApr24h: (\d+)% < (\d+)%/.exec(t))) return `Fees thin: APR ${commas(m[1])}% (min ${commas(m[2])}%)`;
  if ((m = /^binStep: (.*) outside (.*)/.exec(t))) return `Bin step ${m[1]} (only ${m[2]})`;
  if (/^solQuoted/.test(t)) return "Not a SOL pair";
  if ((m = /^pump1h (.*)/.exec(t))) return `Pump 1h ${m[1]}`;
  if ((m = /^drop6h (.*)/.exec(t))) return `Drop 6h ${m[1]}`;
  if ((m = /^tvl (.*)/.exec(t))) return `Thin pool: TVL ${m[1]}`;
  if ((m = /^night entry (.*)/.exec(t))) return `Night entry ${m[1]} WIB`;
  if ((m = /^revival: (.*)/.exec(t))) return `Dead→alive: ${m[1].replace(/^dead→alive: /, "")}`;
  return t;
}

// ----------------------------------------------------- layer 2: benchmark ---
const MATCH_DIMS = ["poolAgeHours", "mcapUsd", "fees24hUsd", "fees1hUsd"];
const INFO_DIMS = ["priceChg1h", "priceChg6h", "holdMin", "pnlPct"];

function fmtDim(d, v) {
  if (d === "poolAgeHours") return v >= 1 ? pyFixed(v, 0) + "h" : pyFixed(v * 60, 0) + "m";
  if (d.endsWith("Usd")) return v >= 1e6 ? `$${pyFixed(v / 1e6, 2)}M` : v >= 1e3 ? `$${pyFixed(v / 1e3, 1)}k` : `$${pyFixed(v, 0)}`;
  return String(v);
}
function bandTxt(d, b) {
  const lo = b.lo, hi = b.hi;
  if (lo == null && hi == null) return "any";
  if (hi == null) return `> ${fmtDim(d, lo)}`;
  if (lo == null) return `< ${fmtDim(d, hi)}`;
  return `${fmtDim(d, lo)}–${fmtDim(d, hi)}`;
}
// Habitat bands from the benchmark, with the owner's fixed bands on top (signal.bandOverrides; null = no limit).
function bandsFor(prof, overrides) {
  const out = {};
  Object.keys(prof.bands || {}).forEach(d => { out[d] = Object.assign({}, prof.bands[d]); });
  Object.keys(overrides || {}).forEach(d => {
    const o = overrides[d];
    if (d.startsWith("_") || !o || typeof o !== "object") return;
    out[d] = Object.assign({}, out[d] || {}, { lo: o.lo == null ? null : o.lo, hi: o.hi == null ? null : o.hi, fixed: true });
  });
  return out;
}
const candidateDims = p => ({ fees1hUsd: ((p.fees || {})["1h"]) || 0, fees24hUsd: ((p.fees || {})["24h"]) || 0 });

// Which habitat dims the pool sits inside, and a 0..1 score. c needs fees1hUsd / fees24hUsd (candidateDims).
function matchBenchmark(c, prof, overrides) {
  const hits = [], misses = [];
  const steps = prof.binSteps || {};
  let good = Object.keys(steps).filter(s => steps[s].pnlSol > 0);
  if (!good.length) good = Object.keys(steps);
  const bs = c.binStep == null ? "None" : String(c.binStep);
  if (good.includes(bs)) hits.push(`binStep ${bs}`);
  else misses.push(`binStep ${bs} ∉ [${good.slice().sort((a, b) => parseFloat(a) - parseFloat(b)).map(s => `'${s}'`).join(", ")}]`);
  const bands = bandsFor(prof, overrides);
  MATCH_DIMS.forEach(d => {
    const b = bands[d], v = c[d];
    if (!b) return;
    if (v == null) misses.push(`${d} n/a`);
    else if ((b.lo == null || v >= b.lo) && (b.hi == null || v <= b.hi)) hits.push(`${d} ${fmtDim(d, v)}`);
    else misses.push(`${d} ${fmtDim(d, v)} ∉ ${bandTxt(d, b)}`);
  });
  const n = hits.length + misses.length;
  return { score: n ? pyRound(hits.length / n, 2) : 0, hits, misses, dims: n };
}

// ------------------------------------------- dead-then-alive (revival) gate ---
// hourly: Meteora volume/history 1h rows, oldest first, ending at the current hour.
function activityFeatures(hourly, ageH, deadHourPct) {
  const dhp = deadHourPct == null ? 0.5 : deadHourPct;
  const n = Math.max(1, Math.min(24, Math.trunc(ageH)));
  const fees = hourly.slice(-n).map(r => r.fees || 0);
  const total = fees.reduce((a, b) => a + b, 0);
  if (total <= 0 || !fees.length) return null;
  let streak = 0, best = 0;
  fees.forEach(f => { streak = f < dhp / 100 * total ? streak + 1 : 0; best = Math.max(best, streak); });
  const top = fees.slice().sort((a, b) => b - a);
  return {
    hours: fees.length,
    activeHours: fees.filter(f => f >= 0.01 * total).length,
    top3Share: pyRound(top.slice(0, 3).reduce((a, b) => a + b, 0) / total, 3),
    deadStreak: best,
    last4Share: pyRound(fees.slice(-4).reduce((a, b) => a + b, 0) / total, 3),
  };
}
function revived(f, rv) {
  if (!f) return false;
  return f.hours >= rv.minDeadHours + 3 && f.deadStreak >= rv.minDeadHours && f.top3Share >= rv.minTop3Share;
}
function describeActivity(f) {
  return `dead→alive: ${f.deadStreak}h dead, top 3 hours = ${pyFixed(f.top3Share * 100, 0)}% of 24h fees, ${f.activeHours}/${f.hours}h active`;
}
// Mutates c like Python's _revival_check. Returns true when the pool stays in the signal list.
function applyRevival(c, hourly, cfg) {
  const rv = cfg.signal.revival || {};
  const mode = rv.mode || "gate";
  if (mode === "off") return true;
  const f = activityFeatures(hourly || [], c.poolAgeHours, rv.deadHourPct);
  c.activity = f;
  if (!revived(f, rv)) return true;
  const why = describeActivity(f);
  if (mode === "flag") { c.flags.push(why); return true; }
  c.gatesFailed.push("revival: " + why);
  c.decision = "skip";
  c.decisionReason = c.gatesFailed.join("; ");
  return false;
}

// The signal test without the revival request: gates pass AND the pool sits inside the habitat.
function signalCandidate(p, cfg, prof, nowMs, opts) {
  const c = evaluate(p, cfg, nowMs, opts);
  Object.assign(c, candidateDims(p));
  const m = matchBenchmark(c, prof, cfg.signal.bandOverrides);
  return { c, m, hit: c.gatesFailed.length === 0 && m.score >= cfg.signal.minMatchScore };
}

// ------------------------------------------------------ benchmark builder ---
// Port of benchmark.py. net.getJson(url) -> parsed JSON (the caller owns retries/timeouts).
const qs = o => Object.keys(o).filter(k => o[k] != null).map(k => `${k}=${encodeURIComponent(o[k])}`).join("&");
const sideOfRow = r => (r.tokenYMint === SOL_MINT ? "y" : r.tokenXMint === SOL_MINT ? "x" : null);

async function apiPortfolio(net, wallet, sinceSec) {
  const pools = [];
  for (let page = 1; ; page++) {
    const d = await net.getJson(`${DATAPI}/portfolio?${qs({ user: wallet, page, page_size: 100 })}`);
    const rows = d.pools || [];
    pools.push(...rows);
    if (!d.hasNext || (sinceSec && rows.length && (rows[rows.length - 1].lastClosedAt || 0) < sinceSec)) return pools;
  }
}
async function apiPositions(net, pool, wallet, status) {
  const out = [];
  for (let page = 1; ; page++) {
    const d = await net.getJson(`${DATAPI}/positions/${pool}/pnl?${qs({ user: wallet, status, page, page_size: 100 })}`);
    out.push(...(d.positions || []));
    if (!d.hasNext) return out;
  }
}
// The 1h history endpoints cover <= 72h per request; walk the window in chunks.
async function apiChunked(net, path, pool, startSec, endSec, spanH) {
  const span = (spanH || 72) * 3600, out = [];
  for (let t = startSec; t < endSec; t += span) {
    const d = await net.getJson(`${DATAPI}/pools/${pool}/${path}?${qs({ timeframe: "1h", start_time: t, end_time: Math.min(endSec, t + span) })}`);
    out.push(...(d.data || []));
  }
  const seen = new Set(), uniq = [];
  out.sort((a, b) => a.timestamp - b.timestamp).forEach(r => { if (!seen.has(r.timestamp)) { seen.add(r.timestamp); uniq.push(r); } });
  return uniq;
}

// Slices of one ladder are opened minutes apart: positions created within gapS of the first one count as one entry.
function groupEntries(positions, gapS) {
  const gap = gapS == null ? 1800 : gapS, out = [];
  positions.slice().sort((a, b) => a.createdAt - b.createdAt).forEach(p => {
    const last = out[out.length - 1];
    if (last && p.createdAt - last[0].createdAt <= gap) last.push(p); else out.push([p]);
  });
  return out;
}
const bucketSum = (rows, key, start, end) => rows.filter(r => start <= r.timestamp && r.timestamp < end).reduce((a, r) => a + (r[key] || 0), 0);
function closeAt(candles, ts, side) {
  let last = null;
  candles.forEach(c => { if (c.timestamp <= ts) last = c; });   // candles are sorted
  return last ? toTokenInSol(last.close, side) : null;
}

function entryFeatures(group, snap, vol, candles, side, utcOffsetH) {
  const off = utcOffsetH == null ? 7 : utcOffsetH;
  const t = Math.min(...group.map(p => p.createdAt));
  const hour = t - t % 3600;
  const sol = p => Number((((p.allTimeDeposits || {}).total) || {}).sol || 0);
  const dep = group.reduce((a, p) => a + sol(p), 0);
  const pnl = group.reduce((a, p) => a + Number(p.pnlSol || 0), 0);
  const price = closeAt(candles, t, side);
  const p1 = closeAt(candles, t - 3600, side), p6 = closeAt(candles, t - 6 * 3600, side);
  const tok = side === "y" ? snap.token_x : snap.token_y;
  const nowPrice = toTokenInSol(snap.current_price || 0, side);
  const mcap = tok.market_cap;
  const closed = Math.max(...group.map(p => p.closedAt || t));
  return {
    pool: snap.address,
    symbol: tok.symbol,
    entryTs: isoSeconds(t * 1000),
    slices: group.length,
    binStep: (snap.pool_config || {}).bin_step,
    poolAgeHours: pyRound((t - (snap.created_at || 0) / 1000) / 3600, 2),
    // Fees of the last full hour / 24 full hours before the entry (no TVL history exists).
    fees1hUsd: pyRound(bucketSum(vol, "fees", hour - 3600, hour), 2),
    fees24hUsd: pyRound(bucketSum(vol, "fees", hour - 24 * 3600, hour), 2),
    // Approx: today's mcap scaled by the token's SOL price then vs now.
    mcapUsd: mcap && price && nowPrice ? Math.round(mcap * price / nowPrice) : null,
    priceChg1h: price && p1 ? pyRound((price / p1 - 1) * 100, 2) : null,
    priceChg6h: price && p6 ? pyRound((price / p6 - 1) * 100, 2) : null,
    entryHourLocal: (new Date(t * 1000).getUTCHours() + off) % 24,
    capitalSol: pyRound(dep, 4),
    pnlSol: pyRound(pnl, 4),
    pnlPct: dep ? pyRound(pnl / dep * 100, 2) : null,
    holdMin: pyRound((closed - t) / 60, 1),
    win: pnl > 0,
  };
}

function quantile(vals, q) {
  const v = vals.slice().sort((a, b) => a - b);
  if (v.length === 1) return v[0];
  const k = (v.length - 1) * q, lo = Math.floor(k);
  return v[lo] + (v[Math.min(lo + 1, v.length - 1)] - v[lo]) * (k - lo);
}
const median = vals => { const v = vals.slice().sort((a, b) => a - b), n = v.length; return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2; };

function profile(entries, wallet, days, builtAtMs, loQ, hiQ) {
  const lo = loQ == null ? 0.1 : loQ, hi = hiQ == null ? 0.9 : hiQ;
  const wins = entries.filter(e => e.win);
  const bands = {};
  MATCH_DIMS.concat(INFO_DIMS).forEach(d => {
    const vals = entries.map(e => e[d]).filter(v => v != null);
    if (!vals.length) return;
    const wv = wins.map(e => e[d]).filter(v => v != null);
    bands[d] = { lo: pyRound(quantile(vals, lo), 2), hi: pyRound(quantile(vals, hi), 2), median: pyRound(median(vals), 2), winMedian: wv.length ? pyRound(median(wv), 2) : null };
  });
  const steps = {};
  entries.forEach(e => {
    const s = steps[String(e.binStep)] = steps[String(e.binStep)] || { entries: 0, wins: 0, pnlSol: 0 };
    s.entries += 1;
    s.wins += e.win ? 1 : 0;
    s.pnlSol = pyRound(s.pnlSol + e.pnlSol, 4);
  });
  return {
    wallet, days,
    builtAt: isoSeconds(builtAtMs == null ? Date.now() : builtAtMs),
    entries: entries.length,
    tokens: new Set(entries.map(e => e.pool)).size,
    wins: wins.length,
    winRatePct: entries.length ? pyRound(wins.length / entries.length * 100, 1) : null,
    pnlSol: pyRound(entries.reduce((a, e) => a + e.pnlSol, 0), 4),
    capitalSolMedian: entries.length ? pyRound(median(entries.map(e => e.capitalSol)), 3) : null,
    binSteps: steps, bands, quantiles: [lo, hi],
  };
}

// Pull a wallet's closed DLMM positions of the last `days` days and rebuild what each entry looked like.
// log(msg) is optional progress. Pools are read with a small concurrency, one pool = ~5 requests.
async function buildBenchmark(net, wallet, days, nowSec, log, concurrency) {
  const say = log || (() => {});
  const now = nowSec == null ? Date.now() / 1000 : nowSec;
  const since = now - days * 86400;
  const pools = (await apiPortfolio(net, wallet, since)).filter(p => (p.lastClosedAt || 0) >= since && sideOfRow(p));
  const entries = [];
  let done = 0;
  const one = async row => {
    const addr = row.poolAddress, side = sideOfRow(row);
    try {
      const pos = (await apiPositions(net, addr, wallet, "closed")).filter(p => p.createdAt >= since);
      if (!pos.length) return [];
      const groups = groupEntries(pos);
      const snap = await net.getJson(`${DATAPI}/pools/${addr}`);
      const start = Math.trunc(Math.min(...pos.map(p => p.createdAt)) - 25 * 3600);
      const end = Math.trunc(Math.max(...pos.map(p => p.createdAt)) + 3600);
      const vol = await apiChunked(net, "volume/history", addr, start, end);
      const candles = await apiChunked(net, "ohlcv", addr, start, end);
      return groups.map(g => entryFeatures(g, snap, vol, candles, side));
    } catch (e) {
      say(`  skip ${row.tokenX}/${row.tokenY}: ${e && e.message ? e.message : e}`);
      return [];
    } finally {
      done += 1;
      say(`  [${done}/${pools.length}] ${row.tokenX}/${row.tokenY}`);
    }
  };
  const results = new Array(pools.length);
  let next = 0;
  const worker = async () => { while (next < pools.length) { const i = next++; results[i] = await one(pools[i]); } };
  await Promise.all(Array.from({ length: Math.min(concurrency || 4, pools.length) }, worker));
  results.forEach(r => entries.push(...(r || [])));
  entries.sort((a, b) => (a.entryTs < b.entryTs ? -1 : a.entryTs > b.entryTs ? 1 : 0));
  if (!entries.length) throw new Error(`no closed SOL DLMM positions for ${wallet} in the last ${days} days`);
  return { entries, profile: profile(entries, wallet, days, now * 1000) };
}

module.exports = {
  SOL_MINT, DATAPI, DEFAULTS, DEFAULT_PROFILE, MATCH_DIMS, INFO_DIMS,
  pyFixed, pyRound, commas, pyStr,
  makeConfig, maxPositionSol, plannedSizeSol,
  solSide, toTokenInSol, features, gates, flags, evaluate, recheck, verdict, nice,
  fmtDim, bandTxt, bandsFor, candidateDims, matchBenchmark,
  activityFeatures, revived, describeActivity, applyRevival, signalCandidate,
  groupEntries, entryFeatures, profile, buildBenchmark,
};
