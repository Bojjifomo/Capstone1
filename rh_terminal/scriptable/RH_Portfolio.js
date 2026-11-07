// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: red; icon-glyph: door-closed;
// ============================================================================
//  RH Portfolio : Uniswap V4 positions and closer : Robinhood Chain (4663)
//
//  *** THIS SCRIPT HOLDS A PRIVATE KEY. DO NOT SHARE OR EXPORT IT. ***
//
//  Exporting a Scriptable script sends the whole file, key included, and the
//  file also syncs to iCloud in plain text. Share the read-only copy from
//  apex-hood.vercel.app instead; never send this one to anyone.
//
//  Same table as the portfolio tracker, plus a Close button on every row and a
//  Close All at the top. Closing withdraws liquidity and claims fees, leaving
//  the underlying tokens in your wallet. It never swaps, so there is no
//  slippage or sandwich risk; selling stays a separate decision.
//
//  A row is one token, so closing it closes every position in it (five ranges
//  become one transaction: one signature, one fee). Positions are grouped by
//  pool so each pool settles with a single TAKE_PAIR.
//
//  Every close is simulated with eth_call first and abandoned if it reverts, so
//  a failing close costs nothing and never reaches the chain.
//
//  Data sources:
//   - Alchemy Robinhood mainnet RPC when ALCHEMY_API_KEY is configured
//   - Robinhood public RPC fallback when it is not configured
//   - On-chain reads: PositionManager, StateView, Multicall3
//   - Chain ID 4663 (Arbitrum Orbit L2, gas token = ETH)
//   - Blockscout explorer, only to list which position NFTs a wallet owns.
//     Position NFTs are not enumerable and no free RPC here serves eth_getLogs,
//     so scanning ownerOf over the id space is the only pure-RPC alternative and
//     it is far too slow once a wallet's positions span a wide id range. Every
//     number displayed is still read from chain state, never from the explorer.
//
//  PNL is unrealised: current value + unclaimed fees - amount deposited. The
//  deposit is recovered from chain state without event logs, because a position
//  minted single-sided needs an amount of that token that depends only on its
//  liquidity and tick range, not on the price at mint. See initialUsd().
//  Pairs with no stablecoin side fall back to drift since first sighting.
//
//  SETUP:
//   1. Paste an Alchemy key into ALCHEMY_API_KEY. Required here, unlike the
//      read-only tracker: the public RPCs time out on eth_estimateGas, and
//      broadcasting a transaction whose gas failed to estimate is unsafe.
//   2. Paste the private key of the wallet holding the positions.
//   3. Run it from the Scriptable app. This is not a home-screen widget.
//
//  Leave DRY_RUN = true for the first run: it simulates and reports the gas it
//  would use without sending anything.
// ============================================================================

// =============================================================================
//  SETTINGS - the only part of this file you need to edit.
// =============================================================================

// Leave any of these blank to read them from a ".env" file in your Scriptable
// folder instead, which keeps them out of this script entirely. See loadDotEnv
// below. With neither source set, this stays a read-only tracker.
//
// -- 1. Your Alchemy key. Required. -------------------------------------------
// Paste ONLY the key, not the whole https:// link.
// Get one free at alchemy.com, make an app, pick the Robinhood chain.
// A separate "RH Secrets" script holds every credential and personal setting,
// so updating this file never overwrites them: paste a new build over the top
// and the keys and presets survive. A .js is used rather than a .env because
// Scriptable's file list hides dotfiles, leaving no way to create or edit one
// on the phone. Both spellings are accepted since earlier builds looked for
// "RHSecrets". Declared before the config block below so those constants can
// read from it without tripping over the temporal dead zone.
const SECRETS = (() => {
  if (typeof importModule !== "function") return {};
  for (const name of ["RH Secrets", "RHSecrets"]) {
    try {
      const m = importModule(name);
      if (m && typeof m === "object") return m;
    } catch (e) {}
  }
  return {};
})();

const ALCHEMY_API_KEY = "";

// -- 2. Your private key. Required. -------------------------------------------
// The wallet holding the positions. 64 characters, "0x" at the front optional.
// WARNING: anyone who reads this file can take everything in this wallet.
// Never share or export this script once you have filled this in.
const PRIVATE_KEY = "";

// -- 3. Practice mode. --------------------------------------------------------
// true  = pretend. Works out what it would do and tells you, sends nothing.
// false = real. Buttons actually close, claim, swap and re-enter.
// Leave this true until you have run it once and the numbers look right.
const DOTENV = loadDotEnv();
const secret = (inline, name) => {
  const v = String(inline || "").trim();
  if (v) return v;
  // The secrets script wins over .env so a friend who has both is not confused
  // by an invisible file quietly taking precedence over the one they can see.
  const s = SECRETS[name];
  if (s != null && String(s).trim()) return String(s).trim();
  return String(DOTENV[name] || "").trim();
};

const PRACTICE_MODE = typeof SECRETS.PRACTICE_MODE === "boolean" ? SECRETS.PRACTICE_MODE
  : /^(true|1|yes)$/i.test(String(DOTENV.PRACTICE_MODE || "").trim()) ? true
  : false;

// -- Blockscout API key. Optional, but history needs it. -----------------------
// The free tier throttles hard: position history, the cleanup token list and
// position ids all read from Blockscout, and without a key those requests time
// out or return 403 under any real use. Measured: no key -> timeout; key -> 200
// in ~3s with a visible budget of ~180 requests. Free from blockscout.com.
// Leave blank to read BLOCKSCOUT_API_KEY from .env instead.
const BLOCKSCOUT_API_KEY = "";

// -- 3b. Uniswap API key, for the swap options. -------------------------------
// Free from hub.uniswap.org. Without it, Close and Claim still work; only the
// "swap to USDG" options and the gas top-up need it, because the API is what
// knows which pool actually holds liquidity on this chain.
const UNISWAP_API_KEY = "";   // or UNISWAP_API_KEY in .env

// -- 4. Worst price you will accept when swapping. ----------------------------
// Only used by the "swap to USDG" options and the gas top-up.
// 5 means: if a swap would give you more than 5% less than the screen price,
// cancel it instead of taking the bad price. Your tokens stay untouched.
//
//   1  = very strict, safest price, swaps often refuse on small pools
//   5  = balanced (recommended)
//   10 = permissive, accepts a worse price to get the swap through
//
// Your pools are small, so selling everything can move the price a few percent
// by itself. If a swap keeps refusing, that is this setting doing its job:
// raise the number only if you accept the worse price.
// The most a sale may lose against the market, all in. This is the number the
// confirmation dialogs promise and the only one worth reasoning about.
//
// Five, and this time measured rather than inherited. Quoting a $800 sale of
// each token this wallet trades, on 2026-09-06, against the independent
// reference below:
//
//   MEME 1.4%   ZZZ 1.9%   FATCOIN 1.9%   SHROOM 3.4%   MOO 4.2%
//   NUDES 5.9%  ORBIO 6.0%   GRASS 38.7%   par 39.2%
//
// The two at 38% are bad routes, not cost: GRASS's deepest pool holds $294,000
// and an $800 sale there should be under 1%. Refusing those is the point. But a
// 3% limit would have refused six of the nine, including SHROOM and MOO, and a
// refused sale is not free: the tokens stay in the volatile position the user
// was trying to leave. Five clears the honest ones and still refuses the bad
// routes by a wide margin.
//
// This is a true five. Before the compounding fix it sat beside an equal router
// tolerance and the two multiplied, permitting 9.75%.
const MAX_TOTAL_LOSS_PERCENT = 5;
// What the router is allowed to slip between quoting and filling. Subtracted
// from the total rather than sitting alongside it: the two used to compound, so
// a quote 5% below market that then filled 5% below the quote landed 9.75% down
// while the dialog still said 5%.
const ROUTER_SLIPPAGE_PERCENT = 1;
// A price that authorises a sale must be this fresh. Display prices are cached
// for minutes, which is right for a table and wrong for a trade: during a
// multi-token close, approvals and earlier swaps can put minutes between the
// price and the send.
const TRADE_PRICE_MAX_AGE_MS = 30 * 1000;

// -- 5. Rarely need changing. -------------------------------------------------
const VERSION           = "close-v120 (range from one pool, steady across refreshes)";
const MINUTES_TO_CONFIRM = 20;   // a transaction unclaimed this long expires
const GAS_LIMIT_BUFFER = 1.30;   // ask for 30% more gas than estimated
const MAX_FEE_MULT     = 2.0;    // allow the fee to double before failing

// =============================================================================
//  Nothing below here needs editing.
// =============================================================================
const DEFAULT_WALLET = "";
const DEFAULT_NAME   = "";
// Percent is friendlier to edit; the maths below wants basis points, where
// 1% = 100. Converting once here keeps both honest.
const DRY_RUN      = PRACTICE_MODE;
const DEADLINE_MIN = MINUTES_TO_CONFIRM;

// =============================================================================
//  VENDORED CRYPTO - generated by build-closer.mjs, do not hand-edit.
//    js-sha256          HMAC-SHA256 for RFC6979   sha256:2db6c8e554fbee14
//    noble-secp256k1 1.7.1  ECDSA signing         sha256:a6a95cbca6b6d980
//  keccak256 is NOT vendored: this script already carries one, verified against
//  NIST vectors and against js-sha3 over 200 random inputs.
//  Verified in tests/: NIST vectors, 200-key OpenSSL cross-check, RFC6979
//  determinism, low-s (EIP-2), and signSync == sign over 100 random cases.
// =============================================================================
const CRYPTO = (function () {
  function load(factory) {
    const module = { exports: {} };
    // noble calls require("crypto") at load time for its async path only; the
    // sync path below never touches it, so an empty stub is safe here.
    const require = function () { return {}; };
    factory(module, module.exports, require);
    return module.exports;
  }
  const _sha256 = load(function (module, exports, require) {
/**
 * [js-sha256]{@link https://github.com/emn178/js-sha256}
 *
 * @version 0.11.1
 * @author Chen, Yi-Cyuan [emn178@gmail.com]
 * @copyright Chen, Yi-Cyuan 2014-2025
 * @license MIT
 */
/*jslint bitwise: true */
(function () {
  'use strict';

  var ERROR = 'input is invalid type';
  var WINDOW = typeof window === 'object';
  var root = WINDOW ? window : {};
  if (root.JS_SHA256_NO_WINDOW) {
    WINDOW = false;
  }
  var WEB_WORKER = !WINDOW && typeof self === 'object';
  var NODE_JS = !root.JS_SHA256_NO_NODE_JS && typeof process === 'object' && process.versions && process.versions.node && process.type != 'renderer';
  if (NODE_JS) {
    root = global;
  } else if (WEB_WORKER) {
    root = self;
  }
  var COMMON_JS = !root.JS_SHA256_NO_COMMON_JS && typeof module === 'object' && module.exports;
  var AMD = typeof define === 'function' && define.amd;
  var ARRAY_BUFFER = !root.JS_SHA256_NO_ARRAY_BUFFER && typeof ArrayBuffer !== 'undefined';
  var HEX_CHARS = '0123456789abcdef'.split('');
  var EXTRA = [-2147483648, 8388608, 32768, 128];
  var SHIFT = [24, 16, 8, 0];
  var K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];
  var OUTPUT_TYPES = ['hex', 'array', 'digest', 'arrayBuffer'];

  var blocks = [];

  if (root.JS_SHA256_NO_NODE_JS || !Array.isArray) {
    Array.isArray = function (obj) {
      return Object.prototype.toString.call(obj) === '[object Array]';
    };
  }

  if (ARRAY_BUFFER && (root.JS_SHA256_NO_ARRAY_BUFFER_IS_VIEW || !ArrayBuffer.isView)) {
    ArrayBuffer.isView = function (obj) {
      return typeof obj === 'object' && obj.buffer && obj.buffer.constructor === ArrayBuffer;
    };
  }

  var createOutputMethod = function (outputType, is224) {
    return function (message) {
      return new Sha256(is224, true).update(message)[outputType]();
    };
  };

  var createMethod = function (is224) {
    var method = createOutputMethod('hex', is224);
    if (NODE_JS) {
      method = nodeWrap(method, is224);
    }
    method.create = function () {
      return new Sha256(is224);
    };
    method.update = function (message) {
      return method.create().update(message);
    };
    for (var i = 0; i < OUTPUT_TYPES.length; ++i) {
      var type = OUTPUT_TYPES[i];
      method[type] = createOutputMethod(type, is224);
    }
    return method;
  };

  var nodeWrap = function (method, is224) {
    var crypto = require('crypto')
    var Buffer = require('buffer').Buffer;
    var algorithm = is224 ? 'sha224' : 'sha256';
    var bufferFrom;
    if (Buffer.from && !root.JS_SHA256_NO_BUFFER_FROM) {
      bufferFrom = Buffer.from;
    } else {
      bufferFrom = function (message) {
        return new Buffer(message);
      };
    }
    var nodeMethod = function (message) {
      if (typeof message === 'string') {
        return crypto.createHash(algorithm).update(message, 'utf8').digest('hex');
      } else {
        if (message === null || message === undefined) {
          throw new Error(ERROR);
        } else if (message.constructor === ArrayBuffer) {
          message = new Uint8Array(message);
        }
      }
      if (Array.isArray(message) || ArrayBuffer.isView(message) ||
        message.constructor === Buffer) {
        return crypto.createHash(algorithm).update(bufferFrom(message)).digest('hex');
      } else {
        return method(message);
      }
    };
    return nodeMethod;
  };

  var createHmacOutputMethod = function (outputType, is224) {
    return function (key, message) {
      return new HmacSha256(key, is224, true).update(message)[outputType]();
    };
  };

  var createHmacMethod = function (is224) {
    var method = createHmacOutputMethod('hex', is224);
    method.create = function (key) {
      return new HmacSha256(key, is224);
    };
    method.update = function (key, message) {
      return method.create(key).update(message);
    };
    for (var i = 0; i < OUTPUT_TYPES.length; ++i) {
      var type = OUTPUT_TYPES[i];
      method[type] = createHmacOutputMethod(type, is224);
    }
    return method;
  };

  function Sha256(is224, sharedMemory) {
    if (sharedMemory) {
      blocks[0] = blocks[16] = blocks[1] = blocks[2] = blocks[3] =
        blocks[4] = blocks[5] = blocks[6] = blocks[7] =
        blocks[8] = blocks[9] = blocks[10] = blocks[11] =
        blocks[12] = blocks[13] = blocks[14] = blocks[15] = 0;
      this.blocks = blocks;
    } else {
      this.blocks = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    }

    if (is224) {
      this.h0 = 0xc1059ed8;
      this.h1 = 0x367cd507;
      this.h2 = 0x3070dd17;
      this.h3 = 0xf70e5939;
      this.h4 = 0xffc00b31;
      this.h5 = 0x68581511;
      this.h6 = 0x64f98fa7;
      this.h7 = 0xbefa4fa4;
    } else { // 256
      this.h0 = 0x6a09e667;
      this.h1 = 0xbb67ae85;
      this.h2 = 0x3c6ef372;
      this.h3 = 0xa54ff53a;
      this.h4 = 0x510e527f;
      this.h5 = 0x9b05688c;
      this.h6 = 0x1f83d9ab;
      this.h7 = 0x5be0cd19;
    }

    this.block = this.start = this.bytes = this.hBytes = 0;
    this.finalized = this.hashed = false;
    this.first = true;
    this.is224 = is224;
  }

  Sha256.prototype.update = function (message) {
    if (this.finalized) {
      return;
    }
    var notString, type = typeof message;
    if (type !== 'string') {
      if (type === 'object') {
        if (message === null) {
          throw new Error(ERROR);
        } else if (ARRAY_BUFFER && message.constructor === ArrayBuffer) {
          message = new Uint8Array(message);
        } else if (!Array.isArray(message)) {
          if (!ARRAY_BUFFER || !ArrayBuffer.isView(message)) {
            throw new Error(ERROR);
          }
        }
      } else {
        throw new Error(ERROR);
      }
      notString = true;
    }
    var code, index = 0, i, length = message.length, blocks = this.blocks;
    while (index < length) {
      if (this.hashed) {
        this.hashed = false;
        blocks[0] = this.block;
        this.block = blocks[16] = blocks[1] = blocks[2] = blocks[3] =
          blocks[4] = blocks[5] = blocks[6] = blocks[7] =
          blocks[8] = blocks[9] = blocks[10] = blocks[11] =
          blocks[12] = blocks[13] = blocks[14] = blocks[15] = 0;
      }

      if (notString) {
        for (i = this.start; index < length && i < 64; ++index) {
          blocks[i >>> 2] |= message[index] << SHIFT[i++ & 3];
        }
      } else {
        for (i = this.start; index < length && i < 64; ++index) {
          code = message.charCodeAt(index);
          if (code < 0x80) {
            blocks[i >>> 2] |= code << SHIFT[i++ & 3];
          } else if (code < 0x800) {
            blocks[i >>> 2] |= (0xc0 | (code >>> 6)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | (code & 0x3f)) << SHIFT[i++ & 3];
          } else if (code < 0xd800 || code >= 0xe000) {
            blocks[i >>> 2] |= (0xe0 | (code >>> 12)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | ((code >>> 6) & 0x3f)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | (code & 0x3f)) << SHIFT[i++ & 3];
          } else {
            code = 0x10000 + (((code & 0x3ff) << 10) | (message.charCodeAt(++index) & 0x3ff));
            blocks[i >>> 2] |= (0xf0 | (code >>> 18)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | ((code >>> 12) & 0x3f)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | ((code >>> 6) & 0x3f)) << SHIFT[i++ & 3];
            blocks[i >>> 2] |= (0x80 | (code & 0x3f)) << SHIFT[i++ & 3];
          }
        }
      }

      this.lastByteIndex = i;
      this.bytes += i - this.start;
      if (i >= 64) {
        this.block = blocks[16];
        this.start = i - 64;
        this.hash();
        this.hashed = true;
      } else {
        this.start = i;
      }
    }
    if (this.bytes > 4294967295) {
      this.hBytes += this.bytes / 4294967296 << 0;
      this.bytes = this.bytes % 4294967296;
    }
    return this;
  };

  Sha256.prototype.finalize = function () {
    if (this.finalized) {
      return;
    }
    this.finalized = true;
    var blocks = this.blocks, i = this.lastByteIndex;
    blocks[16] = this.block;
    blocks[i >>> 2] |= EXTRA[i & 3];
    this.block = blocks[16];
    if (i >= 56) {
      if (!this.hashed) {
        this.hash();
      }
      blocks[0] = this.block;
      blocks[16] = blocks[1] = blocks[2] = blocks[3] =
        blocks[4] = blocks[5] = blocks[6] = blocks[7] =
        blocks[8] = blocks[9] = blocks[10] = blocks[11] =
        blocks[12] = blocks[13] = blocks[14] = blocks[15] = 0;
    }
    blocks[14] = this.hBytes << 3 | this.bytes >>> 29;
    blocks[15] = this.bytes << 3;
    this.hash();
  };

  Sha256.prototype.hash = function () {
    var a = this.h0, b = this.h1, c = this.h2, d = this.h3, e = this.h4, f = this.h5, g = this.h6,
      h = this.h7, blocks = this.blocks, j, s0, s1, maj, t1, t2, ch, ab, da, cd, bc;

    for (j = 16; j < 64; ++j) {
      // rightrotate
      t1 = blocks[j - 15];
      s0 = ((t1 >>> 7) | (t1 << 25)) ^ ((t1 >>> 18) | (t1 << 14)) ^ (t1 >>> 3);
      t1 = blocks[j - 2];
      s1 = ((t1 >>> 17) | (t1 << 15)) ^ ((t1 >>> 19) | (t1 << 13)) ^ (t1 >>> 10);
      blocks[j] = blocks[j - 16] + s0 + blocks[j - 7] + s1 << 0;
    }

    bc = b & c;
    for (j = 0; j < 64; j += 4) {
      if (this.first) {
        if (this.is224) {
          ab = 300032;
          t1 = blocks[0] - 1413257819;
          h = t1 - 150054599 << 0;
          d = t1 + 24177077 << 0;
        } else {
          ab = 704751109;
          t1 = blocks[0] - 210244248;
          h = t1 - 1521486534 << 0;
          d = t1 + 143694565 << 0;
        }
        this.first = false;
      } else {
        s0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
        s1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
        ab = a & b;
        maj = ab ^ (a & c) ^ bc;
        ch = (e & f) ^ (~e & g);
        t1 = h + s1 + ch + K[j] + blocks[j];
        t2 = s0 + maj;
        h = d + t1 << 0;
        d = t1 + t2 << 0;
      }
      s0 = ((d >>> 2) | (d << 30)) ^ ((d >>> 13) | (d << 19)) ^ ((d >>> 22) | (d << 10));
      s1 = ((h >>> 6) | (h << 26)) ^ ((h >>> 11) | (h << 21)) ^ ((h >>> 25) | (h << 7));
      da = d & a;
      maj = da ^ (d & b) ^ ab;
      ch = (h & e) ^ (~h & f);
      t1 = g + s1 + ch + K[j + 1] + blocks[j + 1];
      t2 = s0 + maj;
      g = c + t1 << 0;
      c = t1 + t2 << 0;
      s0 = ((c >>> 2) | (c << 30)) ^ ((c >>> 13) | (c << 19)) ^ ((c >>> 22) | (c << 10));
      s1 = ((g >>> 6) | (g << 26)) ^ ((g >>> 11) | (g << 21)) ^ ((g >>> 25) | (g << 7));
      cd = c & d;
      maj = cd ^ (c & a) ^ da;
      ch = (g & h) ^ (~g & e);
      t1 = f + s1 + ch + K[j + 2] + blocks[j + 2];
      t2 = s0 + maj;
      f = b + t1 << 0;
      b = t1 + t2 << 0;
      s0 = ((b >>> 2) | (b << 30)) ^ ((b >>> 13) | (b << 19)) ^ ((b >>> 22) | (b << 10));
      s1 = ((f >>> 6) | (f << 26)) ^ ((f >>> 11) | (f << 21)) ^ ((f >>> 25) | (f << 7));
      bc = b & c;
      maj = bc ^ (b & d) ^ cd;
      ch = (f & g) ^ (~f & h);
      t1 = e + s1 + ch + K[j + 3] + blocks[j + 3];
      t2 = s0 + maj;
      e = a + t1 << 0;
      a = t1 + t2 << 0;
      this.chromeBugWorkAround = true;
    }

    this.h0 = this.h0 + a << 0;
    this.h1 = this.h1 + b << 0;
    this.h2 = this.h2 + c << 0;
    this.h3 = this.h3 + d << 0;
    this.h4 = this.h4 + e << 0;
    this.h5 = this.h5 + f << 0;
    this.h6 = this.h6 + g << 0;
    this.h7 = this.h7 + h << 0;
  };

  Sha256.prototype.hex = function () {
    this.finalize();

    var h0 = this.h0, h1 = this.h1, h2 = this.h2, h3 = this.h3, h4 = this.h4, h5 = this.h5,
      h6 = this.h6, h7 = this.h7;

    var hex = HEX_CHARS[(h0 >>> 28) & 0x0F] + HEX_CHARS[(h0 >>> 24) & 0x0F] +
      HEX_CHARS[(h0 >>> 20) & 0x0F] + HEX_CHARS[(h0 >>> 16) & 0x0F] +
      HEX_CHARS[(h0 >>> 12) & 0x0F] + HEX_CHARS[(h0 >>> 8) & 0x0F] +
      HEX_CHARS[(h0 >>> 4) & 0x0F] + HEX_CHARS[h0 & 0x0F] +
      HEX_CHARS[(h1 >>> 28) & 0x0F] + HEX_CHARS[(h1 >>> 24) & 0x0F] +
      HEX_CHARS[(h1 >>> 20) & 0x0F] + HEX_CHARS[(h1 >>> 16) & 0x0F] +
      HEX_CHARS[(h1 >>> 12) & 0x0F] + HEX_CHARS[(h1 >>> 8) & 0x0F] +
      HEX_CHARS[(h1 >>> 4) & 0x0F] + HEX_CHARS[h1 & 0x0F] +
      HEX_CHARS[(h2 >>> 28) & 0x0F] + HEX_CHARS[(h2 >>> 24) & 0x0F] +
      HEX_CHARS[(h2 >>> 20) & 0x0F] + HEX_CHARS[(h2 >>> 16) & 0x0F] +
      HEX_CHARS[(h2 >>> 12) & 0x0F] + HEX_CHARS[(h2 >>> 8) & 0x0F] +
      HEX_CHARS[(h2 >>> 4) & 0x0F] + HEX_CHARS[h2 & 0x0F] +
      HEX_CHARS[(h3 >>> 28) & 0x0F] + HEX_CHARS[(h3 >>> 24) & 0x0F] +
      HEX_CHARS[(h3 >>> 20) & 0x0F] + HEX_CHARS[(h3 >>> 16) & 0x0F] +
      HEX_CHARS[(h3 >>> 12) & 0x0F] + HEX_CHARS[(h3 >>> 8) & 0x0F] +
      HEX_CHARS[(h3 >>> 4) & 0x0F] + HEX_CHARS[h3 & 0x0F] +
      HEX_CHARS[(h4 >>> 28) & 0x0F] + HEX_CHARS[(h4 >>> 24) & 0x0F] +
      HEX_CHARS[(h4 >>> 20) & 0x0F] + HEX_CHARS[(h4 >>> 16) & 0x0F] +
      HEX_CHARS[(h4 >>> 12) & 0x0F] + HEX_CHARS[(h4 >>> 8) & 0x0F] +
      HEX_CHARS[(h4 >>> 4) & 0x0F] + HEX_CHARS[h4 & 0x0F] +
      HEX_CHARS[(h5 >>> 28) & 0x0F] + HEX_CHARS[(h5 >>> 24) & 0x0F] +
      HEX_CHARS[(h5 >>> 20) & 0x0F] + HEX_CHARS[(h5 >>> 16) & 0x0F] +
      HEX_CHARS[(h5 >>> 12) & 0x0F] + HEX_CHARS[(h5 >>> 8) & 0x0F] +
      HEX_CHARS[(h5 >>> 4) & 0x0F] + HEX_CHARS[h5 & 0x0F] +
      HEX_CHARS[(h6 >>> 28) & 0x0F] + HEX_CHARS[(h6 >>> 24) & 0x0F] +
      HEX_CHARS[(h6 >>> 20) & 0x0F] + HEX_CHARS[(h6 >>> 16) & 0x0F] +
      HEX_CHARS[(h6 >>> 12) & 0x0F] + HEX_CHARS[(h6 >>> 8) & 0x0F] +
      HEX_CHARS[(h6 >>> 4) & 0x0F] + HEX_CHARS[h6 & 0x0F];
    if (!this.is224) {
      hex += HEX_CHARS[(h7 >>> 28) & 0x0F] + HEX_CHARS[(h7 >>> 24) & 0x0F] +
        HEX_CHARS[(h7 >>> 20) & 0x0F] + HEX_CHARS[(h7 >>> 16) & 0x0F] +
        HEX_CHARS[(h7 >>> 12) & 0x0F] + HEX_CHARS[(h7 >>> 8) & 0x0F] +
        HEX_CHARS[(h7 >>> 4) & 0x0F] + HEX_CHARS[h7 & 0x0F];
    }
    return hex;
  };

  Sha256.prototype.toString = Sha256.prototype.hex;

  Sha256.prototype.digest = function () {
    this.finalize();

    var h0 = this.h0, h1 = this.h1, h2 = this.h2, h3 = this.h3, h4 = this.h4, h5 = this.h5,
      h6 = this.h6, h7 = this.h7;

    var arr = [
      (h0 >>> 24) & 0xFF, (h0 >>> 16) & 0xFF, (h0 >>> 8) & 0xFF, h0 & 0xFF,
      (h1 >>> 24) & 0xFF, (h1 >>> 16) & 0xFF, (h1 >>> 8) & 0xFF, h1 & 0xFF,
      (h2 >>> 24) & 0xFF, (h2 >>> 16) & 0xFF, (h2 >>> 8) & 0xFF, h2 & 0xFF,
      (h3 >>> 24) & 0xFF, (h3 >>> 16) & 0xFF, (h3 >>> 8) & 0xFF, h3 & 0xFF,
      (h4 >>> 24) & 0xFF, (h4 >>> 16) & 0xFF, (h4 >>> 8) & 0xFF, h4 & 0xFF,
      (h5 >>> 24) & 0xFF, (h5 >>> 16) & 0xFF, (h5 >>> 8) & 0xFF, h5 & 0xFF,
      (h6 >>> 24) & 0xFF, (h6 >>> 16) & 0xFF, (h6 >>> 8) & 0xFF, h6 & 0xFF
    ];
    if (!this.is224) {
      arr.push((h7 >>> 24) & 0xFF, (h7 >>> 16) & 0xFF, (h7 >>> 8) & 0xFF, h7 & 0xFF);
    }
    return arr;
  };

  Sha256.prototype.array = Sha256.prototype.digest;

  Sha256.prototype.arrayBuffer = function () {
    this.finalize();

    var buffer = new ArrayBuffer(this.is224 ? 28 : 32);
    var dataView = new DataView(buffer);
    dataView.setUint32(0, this.h0);
    dataView.setUint32(4, this.h1);
    dataView.setUint32(8, this.h2);
    dataView.setUint32(12, this.h3);
    dataView.setUint32(16, this.h4);
    dataView.setUint32(20, this.h5);
    dataView.setUint32(24, this.h6);
    if (!this.is224) {
      dataView.setUint32(28, this.h7);
    }
    return buffer;
  };

  function HmacSha256(key, is224, sharedMemory) {
    var i, type = typeof key;
    if (type === 'string') {
      var bytes = [], length = key.length, index = 0, code;
      for (i = 0; i < length; ++i) {
        code = key.charCodeAt(i);
        if (code < 0x80) {
          bytes[index++] = code;
        } else if (code < 0x800) {
          bytes[index++] = (0xc0 | (code >>> 6));
          bytes[index++] = (0x80 | (code & 0x3f));
        } else if (code < 0xd800 || code >= 0xe000) {
          bytes[index++] = (0xe0 | (code >>> 12));
          bytes[index++] = (0x80 | ((code >>> 6) & 0x3f));
          bytes[index++] = (0x80 | (code & 0x3f));
        } else {
          code = 0x10000 + (((code & 0x3ff) << 10) | (key.charCodeAt(++i) & 0x3ff));
          bytes[index++] = (0xf0 | (code >>> 18));
          bytes[index++] = (0x80 | ((code >>> 12) & 0x3f));
          bytes[index++] = (0x80 | ((code >>> 6) & 0x3f));
          bytes[index++] = (0x80 | (code & 0x3f));
        }
      }
      key = bytes;
    } else {
      if (type === 'object') {
        if (key === null) {
          throw new Error(ERROR);
        } else if (ARRAY_BUFFER && key.constructor === ArrayBuffer) {
          key = new Uint8Array(key);
        } else if (!Array.isArray(key)) {
          if (!ARRAY_BUFFER || !ArrayBuffer.isView(key)) {
            throw new Error(ERROR);
          }
        }
      } else {
        throw new Error(ERROR);
      }
    }

    if (key.length > 64) {
      key = (new Sha256(is224, true)).update(key).array();
    }

    var oKeyPad = [], iKeyPad = [];
    for (i = 0; i < 64; ++i) {
      var b = key[i] || 0;
      oKeyPad[i] = 0x5c ^ b;
      iKeyPad[i] = 0x36 ^ b;
    }

    Sha256.call(this, is224, sharedMemory);

    this.update(iKeyPad);
    this.oKeyPad = oKeyPad;
    this.inner = true;
    this.sharedMemory = sharedMemory;
  }
  HmacSha256.prototype = new Sha256();

  HmacSha256.prototype.finalize = function () {
    Sha256.prototype.finalize.call(this);
    if (this.inner) {
      this.inner = false;
      var innerHash = this.array();
      Sha256.call(this, this.is224, this.sharedMemory);
      this.update(this.oKeyPad);
      this.update(innerHash);
      Sha256.prototype.finalize.call(this);
    }
  };

  var exports = createMethod();
  exports.sha256 = exports;
  exports.sha224 = createMethod(true);
  exports.sha256.hmac = createHmacMethod();
  exports.sha224.hmac = createHmacMethod(true);

  if (COMMON_JS) {
    module.exports = exports;
  } else {
    root.sha256 = exports.sha256;
    root.sha224 = exports.sha224;
    if (AMD) {
      define(function () {
        return exports;
      });
    }
  }
})();

  });
  const _secp = load(function (module, exports, require) {
"use strict";
/*! noble-secp256k1 - MIT License (c) 2019 Paul Miller (paulmillr.com) */
Object.defineProperty(exports, "__esModule", { value: true });
exports.utils = exports.schnorr = exports.verify = exports.signSync = exports.sign = exports.getSharedSecret = exports.recoverPublicKey = exports.getPublicKey = exports.Signature = exports.Point = exports.CURVE = void 0;
const nodeCrypto = require("crypto");
const _0n = BigInt(0);
const _1n = BigInt(1);
const _2n = BigInt(2);
const _3n = BigInt(3);
const _8n = BigInt(8);
const CURVE = Object.freeze({
    a: _0n,
    b: BigInt(7),
    P: BigInt('0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffefffffc2f'),
    n: BigInt('0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141'),
    h: _1n,
    Gx: BigInt('55066263022277343669578718895168534326250603453777594175500187360389116729240'),
    Gy: BigInt('32670510020758816978083085130507043184471273380659243275938904335757337482424'),
    beta: BigInt('0x7ae96a2b657c07106e64479eac3434e99cf0497512f58995c1396c28719501ee'),
});
exports.CURVE = CURVE;
const divNearest = (a, b) => (a + b / _2n) / b;
const endo = {
    beta: BigInt('0x7ae96a2b657c07106e64479eac3434e99cf0497512f58995c1396c28719501ee'),
    splitScalar(k) {
        const { n } = CURVE;
        const a1 = BigInt('0x3086d221a7d46bcde86c90e49284eb15');
        const b1 = -_1n * BigInt('0xe4437ed6010e88286f547fa90abfe4c3');
        const a2 = BigInt('0x114ca50f7a8e2f3f657c1108d9d44cfd8');
        const b2 = a1;
        const POW_2_128 = BigInt('0x100000000000000000000000000000000');
        const c1 = divNearest(b2 * k, n);
        const c2 = divNearest(-b1 * k, n);
        let k1 = mod(k - c1 * a1 - c2 * a2, n);
        let k2 = mod(-c1 * b1 - c2 * b2, n);
        const k1neg = k1 > POW_2_128;
        const k2neg = k2 > POW_2_128;
        if (k1neg)
            k1 = n - k1;
        if (k2neg)
            k2 = n - k2;
        if (k1 > POW_2_128 || k2 > POW_2_128) {
            throw new Error('splitScalarEndo: Endomorphism failed, k=' + k);
        }
        return { k1neg, k1, k2neg, k2 };
    },
};
const fieldLen = 32;
const groupLen = 32;
const hashLen = 32;
const compressedLen = fieldLen + 1;
const uncompressedLen = 2 * fieldLen + 1;
function weierstrass(x) {
    const { a, b } = CURVE;
    const x2 = mod(x * x);
    const x3 = mod(x2 * x);
    return mod(x3 + a * x + b);
}
const USE_ENDOMORPHISM = CURVE.a === _0n;
class ShaError extends Error {
    constructor(message) {
        super(message);
    }
}
function assertJacPoint(other) {
    if (!(other instanceof JacobianPoint))
        throw new TypeError('JacobianPoint expected');
}
class JacobianPoint {
    constructor(x, y, z) {
        this.x = x;
        this.y = y;
        this.z = z;
    }
    static fromAffine(p) {
        if (!(p instanceof Point)) {
            throw new TypeError('JacobianPoint#fromAffine: expected Point');
        }
        if (p.equals(Point.ZERO))
            return JacobianPoint.ZERO;
        return new JacobianPoint(p.x, p.y, _1n);
    }
    static toAffineBatch(points) {
        const toInv = invertBatch(points.map((p) => p.z));
        return points.map((p, i) => p.toAffine(toInv[i]));
    }
    static normalizeZ(points) {
        return JacobianPoint.toAffineBatch(points).map(JacobianPoint.fromAffine);
    }
    equals(other) {
        assertJacPoint(other);
        const { x: X1, y: Y1, z: Z1 } = this;
        const { x: X2, y: Y2, z: Z2 } = other;
        const Z1Z1 = mod(Z1 * Z1);
        const Z2Z2 = mod(Z2 * Z2);
        const U1 = mod(X1 * Z2Z2);
        const U2 = mod(X2 * Z1Z1);
        const S1 = mod(mod(Y1 * Z2) * Z2Z2);
        const S2 = mod(mod(Y2 * Z1) * Z1Z1);
        return U1 === U2 && S1 === S2;
    }
    negate() {
        return new JacobianPoint(this.x, mod(-this.y), this.z);
    }
    double() {
        const { x: X1, y: Y1, z: Z1 } = this;
        const A = mod(X1 * X1);
        const B = mod(Y1 * Y1);
        const C = mod(B * B);
        const x1b = X1 + B;
        const D = mod(_2n * (mod(x1b * x1b) - A - C));
        const E = mod(_3n * A);
        const F = mod(E * E);
        const X3 = mod(F - _2n * D);
        const Y3 = mod(E * (D - X3) - _8n * C);
        const Z3 = mod(_2n * Y1 * Z1);
        return new JacobianPoint(X3, Y3, Z3);
    }
    add(other) {
        assertJacPoint(other);
        const { x: X1, y: Y1, z: Z1 } = this;
        const { x: X2, y: Y2, z: Z2 } = other;
        if (X2 === _0n || Y2 === _0n)
            return this;
        if (X1 === _0n || Y1 === _0n)
            return other;
        const Z1Z1 = mod(Z1 * Z1);
        const Z2Z2 = mod(Z2 * Z2);
        const U1 = mod(X1 * Z2Z2);
        const U2 = mod(X2 * Z1Z1);
        const S1 = mod(mod(Y1 * Z2) * Z2Z2);
        const S2 = mod(mod(Y2 * Z1) * Z1Z1);
        const H = mod(U2 - U1);
        const r = mod(S2 - S1);
        if (H === _0n) {
            if (r === _0n) {
                return this.double();
            }
            else {
                return JacobianPoint.ZERO;
            }
        }
        const HH = mod(H * H);
        const HHH = mod(H * HH);
        const V = mod(U1 * HH);
        const X3 = mod(r * r - HHH - _2n * V);
        const Y3 = mod(r * (V - X3) - S1 * HHH);
        const Z3 = mod(Z1 * Z2 * H);
        return new JacobianPoint(X3, Y3, Z3);
    }
    subtract(other) {
        return this.add(other.negate());
    }
    multiplyUnsafe(scalar) {
        const P0 = JacobianPoint.ZERO;
        if (typeof scalar === 'bigint' && scalar === _0n)
            return P0;
        let n = normalizeScalar(scalar);
        if (n === _1n)
            return this;
        if (!USE_ENDOMORPHISM) {
            let p = P0;
            let d = this;
            while (n > _0n) {
                if (n & _1n)
                    p = p.add(d);
                d = d.double();
                n >>= _1n;
            }
            return p;
        }
        let { k1neg, k1, k2neg, k2 } = endo.splitScalar(n);
        let k1p = P0;
        let k2p = P0;
        let d = this;
        while (k1 > _0n || k2 > _0n) {
            if (k1 & _1n)
                k1p = k1p.add(d);
            if (k2 & _1n)
                k2p = k2p.add(d);
            d = d.double();
            k1 >>= _1n;
            k2 >>= _1n;
        }
        if (k1neg)
            k1p = k1p.negate();
        if (k2neg)
            k2p = k2p.negate();
        k2p = new JacobianPoint(mod(k2p.x * endo.beta), k2p.y, k2p.z);
        return k1p.add(k2p);
    }
    precomputeWindow(W) {
        const windows = USE_ENDOMORPHISM ? 128 / W + 1 : 256 / W + 1;
        const points = [];
        let p = this;
        let base = p;
        for (let window = 0; window < windows; window++) {
            base = p;
            points.push(base);
            for (let i = 1; i < 2 ** (W - 1); i++) {
                base = base.add(p);
                points.push(base);
            }
            p = base.double();
        }
        return points;
    }
    wNAF(n, affinePoint) {
        if (!affinePoint && this.equals(JacobianPoint.BASE))
            affinePoint = Point.BASE;
        const W = (affinePoint && affinePoint._WINDOW_SIZE) || 1;
        if (256 % W) {
            throw new Error('Point#wNAF: Invalid precomputation window, must be power of 2');
        }
        let precomputes = affinePoint && pointPrecomputes.get(affinePoint);
        if (!precomputes) {
            precomputes = this.precomputeWindow(W);
            if (affinePoint && W !== 1) {
                precomputes = JacobianPoint.normalizeZ(precomputes);
                pointPrecomputes.set(affinePoint, precomputes);
            }
        }
        let p = JacobianPoint.ZERO;
        let f = JacobianPoint.BASE;
        const windows = 1 + (USE_ENDOMORPHISM ? 128 / W : 256 / W);
        const windowSize = 2 ** (W - 1);
        const mask = BigInt(2 ** W - 1);
        const maxNumber = 2 ** W;
        const shiftBy = BigInt(W);
        for (let window = 0; window < windows; window++) {
            const offset = window * windowSize;
            let wbits = Number(n & mask);
            n >>= shiftBy;
            if (wbits > windowSize) {
                wbits -= maxNumber;
                n += _1n;
            }
            const offset1 = offset;
            const offset2 = offset + Math.abs(wbits) - 1;
            const cond1 = window % 2 !== 0;
            const cond2 = wbits < 0;
            if (wbits === 0) {
                f = f.add(constTimeNegate(cond1, precomputes[offset1]));
            }
            else {
                p = p.add(constTimeNegate(cond2, precomputes[offset2]));
            }
        }
        return { p, f };
    }
    multiply(scalar, affinePoint) {
        let n = normalizeScalar(scalar);
        let point;
        let fake;
        if (USE_ENDOMORPHISM) {
            const { k1neg, k1, k2neg, k2 } = endo.splitScalar(n);
            let { p: k1p, f: f1p } = this.wNAF(k1, affinePoint);
            let { p: k2p, f: f2p } = this.wNAF(k2, affinePoint);
            k1p = constTimeNegate(k1neg, k1p);
            k2p = constTimeNegate(k2neg, k2p);
            k2p = new JacobianPoint(mod(k2p.x * endo.beta), k2p.y, k2p.z);
            point = k1p.add(k2p);
            fake = f1p.add(f2p);
        }
        else {
            const { p, f } = this.wNAF(n, affinePoint);
            point = p;
            fake = f;
        }
        return JacobianPoint.normalizeZ([point, fake])[0];
    }
    toAffine(invZ) {
        const { x, y, z } = this;
        const is0 = this.equals(JacobianPoint.ZERO);
        if (invZ == null)
            invZ = is0 ? _8n : invert(z);
        const iz1 = invZ;
        const iz2 = mod(iz1 * iz1);
        const iz3 = mod(iz2 * iz1);
        const ax = mod(x * iz2);
        const ay = mod(y * iz3);
        const zz = mod(z * iz1);
        if (is0)
            return Point.ZERO;
        if (zz !== _1n)
            throw new Error('invZ was invalid');
        return new Point(ax, ay);
    }
}
JacobianPoint.BASE = new JacobianPoint(CURVE.Gx, CURVE.Gy, _1n);
JacobianPoint.ZERO = new JacobianPoint(_0n, _1n, _0n);
function constTimeNegate(condition, item) {
    const neg = item.negate();
    return condition ? neg : item;
}
const pointPrecomputes = new WeakMap();
class Point {
    constructor(x, y) {
        this.x = x;
        this.y = y;
    }
    _setWindowSize(windowSize) {
        this._WINDOW_SIZE = windowSize;
        pointPrecomputes.delete(this);
    }
    hasEvenY() {
        return this.y % _2n === _0n;
    }
    static fromCompressedHex(bytes) {
        const isShort = bytes.length === 32;
        const x = bytesToNumber(isShort ? bytes : bytes.subarray(1));
        if (!isValidFieldElement(x))
            throw new Error('Point is not on curve');
        const y2 = weierstrass(x);
        let y = sqrtMod(y2);
        const isYOdd = (y & _1n) === _1n;
        if (isShort) {
            if (isYOdd)
                y = mod(-y);
        }
        else {
            const isFirstByteOdd = (bytes[0] & 1) === 1;
            if (isFirstByteOdd !== isYOdd)
                y = mod(-y);
        }
        const point = new Point(x, y);
        point.assertValidity();
        return point;
    }
    static fromUncompressedHex(bytes) {
        const x = bytesToNumber(bytes.subarray(1, fieldLen + 1));
        const y = bytesToNumber(bytes.subarray(fieldLen + 1, fieldLen * 2 + 1));
        const point = new Point(x, y);
        point.assertValidity();
        return point;
    }
    static fromHex(hex) {
        const bytes = ensureBytes(hex);
        const len = bytes.length;
        const header = bytes[0];
        if (len === fieldLen)
            return this.fromCompressedHex(bytes);
        if (len === compressedLen && (header === 0x02 || header === 0x03)) {
            return this.fromCompressedHex(bytes);
        }
        if (len === uncompressedLen && header === 0x04)
            return this.fromUncompressedHex(bytes);
        throw new Error(`Point.fromHex: received invalid point. Expected 32-${compressedLen} compressed bytes or ${uncompressedLen} uncompressed bytes, not ${len}`);
    }
    static fromPrivateKey(privateKey) {
        return Point.BASE.multiply(normalizePrivateKey(privateKey));
    }
    static fromSignature(msgHash, signature, recovery) {
        const { r, s } = normalizeSignature(signature);
        if (![0, 1, 2, 3].includes(recovery))
            throw new Error('Cannot recover: invalid recovery bit');
        const h = truncateHash(ensureBytes(msgHash));
        const { n } = CURVE;
        const radj = recovery === 2 || recovery === 3 ? r + n : r;
        const rinv = invert(radj, n);
        const u1 = mod(-h * rinv, n);
        const u2 = mod(s * rinv, n);
        const prefix = recovery & 1 ? '03' : '02';
        const R = Point.fromHex(prefix + numTo32bStr(radj));
        const Q = Point.BASE.multiplyAndAddUnsafe(R, u1, u2);
        if (!Q)
            throw new Error('Cannot recover signature: point at infinify');
        Q.assertValidity();
        return Q;
    }
    toRawBytes(isCompressed = false) {
        return hexToBytes(this.toHex(isCompressed));
    }
    toHex(isCompressed = false) {
        const x = numTo32bStr(this.x);
        if (isCompressed) {
            const prefix = this.hasEvenY() ? '02' : '03';
            return `${prefix}${x}`;
        }
        else {
            return `04${x}${numTo32bStr(this.y)}`;
        }
    }
    toHexX() {
        return this.toHex(true).slice(2);
    }
    toRawX() {
        return this.toRawBytes(true).slice(1);
    }
    assertValidity() {
        const msg = 'Point is not on elliptic curve';
        const { x, y } = this;
        if (!isValidFieldElement(x) || !isValidFieldElement(y))
            throw new Error(msg);
        const left = mod(y * y);
        const right = weierstrass(x);
        if (mod(left - right) !== _0n)
            throw new Error(msg);
    }
    equals(other) {
        return this.x === other.x && this.y === other.y;
    }
    negate() {
        return new Point(this.x, mod(-this.y));
    }
    double() {
        return JacobianPoint.fromAffine(this).double().toAffine();
    }
    add(other) {
        return JacobianPoint.fromAffine(this).add(JacobianPoint.fromAffine(other)).toAffine();
    }
    subtract(other) {
        return this.add(other.negate());
    }
    multiply(scalar) {
        return JacobianPoint.fromAffine(this).multiply(scalar, this).toAffine();
    }
    multiplyAndAddUnsafe(Q, a, b) {
        const P = JacobianPoint.fromAffine(this);
        const aP = a === _0n || a === _1n || this !== Point.BASE ? P.multiplyUnsafe(a) : P.multiply(a);
        const bQ = JacobianPoint.fromAffine(Q).multiplyUnsafe(b);
        const sum = aP.add(bQ);
        return sum.equals(JacobianPoint.ZERO) ? undefined : sum.toAffine();
    }
}
exports.Point = Point;
Point.BASE = new Point(CURVE.Gx, CURVE.Gy);
Point.ZERO = new Point(_0n, _0n);
function sliceDER(s) {
    return Number.parseInt(s[0], 16) >= 8 ? '00' + s : s;
}
function parseDERInt(data) {
    if (data.length < 2 || data[0] !== 0x02) {
        throw new Error(`Invalid signature integer tag: ${bytesToHex(data)}`);
    }
    const len = data[1];
    const res = data.subarray(2, len + 2);
    if (!len || res.length !== len) {
        throw new Error(`Invalid signature integer: wrong length`);
    }
    if (res[0] === 0x00 && res[1] <= 0x7f) {
        throw new Error('Invalid signature integer: trailing length');
    }
    return { data: bytesToNumber(res), left: data.subarray(len + 2) };
}
function parseDERSignature(data) {
    if (data.length < 2 || data[0] != 0x30) {
        throw new Error(`Invalid signature tag: ${bytesToHex(data)}`);
    }
    if (data[1] !== data.length - 2) {
        throw new Error('Invalid signature: incorrect length');
    }
    const { data: r, left: sBytes } = parseDERInt(data.subarray(2));
    const { data: s, left: rBytesLeft } = parseDERInt(sBytes);
    if (rBytesLeft.length) {
        throw new Error(`Invalid signature: left bytes after parsing: ${bytesToHex(rBytesLeft)}`);
    }
    return { r, s };
}
class Signature {
    constructor(r, s) {
        this.r = r;
        this.s = s;
        this.assertValidity();
    }
    static fromCompact(hex) {
        const arr = hex instanceof Uint8Array;
        const name = 'Signature.fromCompact';
        if (typeof hex !== 'string' && !arr)
            throw new TypeError(`${name}: Expected string or Uint8Array`);
        const str = arr ? bytesToHex(hex) : hex;
        if (str.length !== 128)
            throw new Error(`${name}: Expected 64-byte hex`);
        return new Signature(hexToNumber(str.slice(0, 64)), hexToNumber(str.slice(64, 128)));
    }
    static fromDER(hex) {
        const arr = hex instanceof Uint8Array;
        if (typeof hex !== 'string' && !arr)
            throw new TypeError(`Signature.fromDER: Expected string or Uint8Array`);
        const { r, s } = parseDERSignature(arr ? hex : hexToBytes(hex));
        return new Signature(r, s);
    }
    static fromHex(hex) {
        return this.fromDER(hex);
    }
    assertValidity() {
        const { r, s } = this;
        if (!isWithinCurveOrder(r))
            throw new Error('Invalid Signature: r must be 0 < r < n');
        if (!isWithinCurveOrder(s))
            throw new Error('Invalid Signature: s must be 0 < s < n');
    }
    hasHighS() {
        const HALF = CURVE.n >> _1n;
        return this.s > HALF;
    }
    normalizeS() {
        return this.hasHighS() ? new Signature(this.r, mod(-this.s, CURVE.n)) : this;
    }
    toDERRawBytes() {
        return hexToBytes(this.toDERHex());
    }
    toDERHex() {
        const sHex = sliceDER(numberToHexUnpadded(this.s));
        const rHex = sliceDER(numberToHexUnpadded(this.r));
        const sHexL = sHex.length / 2;
        const rHexL = rHex.length / 2;
        const sLen = numberToHexUnpadded(sHexL);
        const rLen = numberToHexUnpadded(rHexL);
        const length = numberToHexUnpadded(rHexL + sHexL + 4);
        return `30${length}02${rLen}${rHex}02${sLen}${sHex}`;
    }
    toRawBytes() {
        return this.toDERRawBytes();
    }
    toHex() {
        return this.toDERHex();
    }
    toCompactRawBytes() {
        return hexToBytes(this.toCompactHex());
    }
    toCompactHex() {
        return numTo32bStr(this.r) + numTo32bStr(this.s);
    }
}
exports.Signature = Signature;
function concatBytes(...arrays) {
    if (!arrays.every((b) => b instanceof Uint8Array))
        throw new Error('Uint8Array list expected');
    if (arrays.length === 1)
        return arrays[0];
    const length = arrays.reduce((a, arr) => a + arr.length, 0);
    const result = new Uint8Array(length);
    for (let i = 0, pad = 0; i < arrays.length; i++) {
        const arr = arrays[i];
        result.set(arr, pad);
        pad += arr.length;
    }
    return result;
}
const hexes = Array.from({ length: 256 }, (v, i) => i.toString(16).padStart(2, '0'));
function bytesToHex(uint8a) {
    if (!(uint8a instanceof Uint8Array))
        throw new Error('Expected Uint8Array');
    let hex = '';
    for (let i = 0; i < uint8a.length; i++) {
        hex += hexes[uint8a[i]];
    }
    return hex;
}
const POW_2_256 = BigInt('0x10000000000000000000000000000000000000000000000000000000000000000');
function numTo32bStr(num) {
    if (typeof num !== 'bigint')
        throw new Error('Expected bigint');
    if (!(_0n <= num && num < POW_2_256))
        throw new Error('Expected number 0 <= n < 2^256');
    return num.toString(16).padStart(64, '0');
}
function numTo32b(num) {
    const b = hexToBytes(numTo32bStr(num));
    if (b.length !== 32)
        throw new Error('Error: expected 32 bytes');
    return b;
}
function numberToHexUnpadded(num) {
    const hex = num.toString(16);
    return hex.length & 1 ? `0${hex}` : hex;
}
function hexToNumber(hex) {
    if (typeof hex !== 'string') {
        throw new TypeError('hexToNumber: expected string, got ' + typeof hex);
    }
    return BigInt(`0x${hex}`);
}
function hexToBytes(hex) {
    if (typeof hex !== 'string') {
        throw new TypeError('hexToBytes: expected string, got ' + typeof hex);
    }
    if (hex.length % 2)
        throw new Error('hexToBytes: received invalid unpadded hex' + hex.length);
    const array = new Uint8Array(hex.length / 2);
    for (let i = 0; i < array.length; i++) {
        const j = i * 2;
        const hexByte = hex.slice(j, j + 2);
        const byte = Number.parseInt(hexByte, 16);
        if (Number.isNaN(byte) || byte < 0)
            throw new Error('Invalid byte sequence');
        array[i] = byte;
    }
    return array;
}
function bytesToNumber(bytes) {
    return hexToNumber(bytesToHex(bytes));
}
function ensureBytes(hex) {
    return hex instanceof Uint8Array ? Uint8Array.from(hex) : hexToBytes(hex);
}
function normalizeScalar(num) {
    if (typeof num === 'number' && Number.isSafeInteger(num) && num > 0)
        return BigInt(num);
    if (typeof num === 'bigint' && isWithinCurveOrder(num))
        return num;
    throw new TypeError('Expected valid private scalar: 0 < scalar < curve.n');
}
function mod(a, b = CURVE.P) {
    const result = a % b;
    return result >= _0n ? result : b + result;
}
function pow2(x, power) {
    const { P } = CURVE;
    let res = x;
    while (power-- > _0n) {
        res *= res;
        res %= P;
    }
    return res;
}
function sqrtMod(x) {
    const { P } = CURVE;
    const _6n = BigInt(6);
    const _11n = BigInt(11);
    const _22n = BigInt(22);
    const _23n = BigInt(23);
    const _44n = BigInt(44);
    const _88n = BigInt(88);
    const b2 = (x * x * x) % P;
    const b3 = (b2 * b2 * x) % P;
    const b6 = (pow2(b3, _3n) * b3) % P;
    const b9 = (pow2(b6, _3n) * b3) % P;
    const b11 = (pow2(b9, _2n) * b2) % P;
    const b22 = (pow2(b11, _11n) * b11) % P;
    const b44 = (pow2(b22, _22n) * b22) % P;
    const b88 = (pow2(b44, _44n) * b44) % P;
    const b176 = (pow2(b88, _88n) * b88) % P;
    const b220 = (pow2(b176, _44n) * b44) % P;
    const b223 = (pow2(b220, _3n) * b3) % P;
    const t1 = (pow2(b223, _23n) * b22) % P;
    const t2 = (pow2(t1, _6n) * b2) % P;
    const rt = pow2(t2, _2n);
    const xc = (rt * rt) % P;
    if (xc !== x)
        throw new Error('Cannot find square root');
    return rt;
}
function invert(number, modulo = CURVE.P) {
    if (number === _0n || modulo <= _0n) {
        throw new Error(`invert: expected positive integers, got n=${number} mod=${modulo}`);
    }
    let a = mod(number, modulo);
    let b = modulo;
    let x = _0n, y = _1n, u = _1n, v = _0n;
    while (a !== _0n) {
        const q = b / a;
        const r = b % a;
        const m = x - u * q;
        const n = y - v * q;
        b = a, a = r, x = u, y = v, u = m, v = n;
    }
    const gcd = b;
    if (gcd !== _1n)
        throw new Error('invert: does not exist');
    return mod(x, modulo);
}
function invertBatch(nums, p = CURVE.P) {
    const scratch = new Array(nums.length);
    const lastMultiplied = nums.reduce((acc, num, i) => {
        if (num === _0n)
            return acc;
        scratch[i] = acc;
        return mod(acc * num, p);
    }, _1n);
    const inverted = invert(lastMultiplied, p);
    nums.reduceRight((acc, num, i) => {
        if (num === _0n)
            return acc;
        scratch[i] = mod(acc * scratch[i], p);
        return mod(acc * num, p);
    }, inverted);
    return scratch;
}
function bits2int_2(bytes) {
    const delta = bytes.length * 8 - groupLen * 8;
    const num = bytesToNumber(bytes);
    return delta > 0 ? num >> BigInt(delta) : num;
}
function truncateHash(hash, truncateOnly = false) {
    const h = bits2int_2(hash);
    if (truncateOnly)
        return h;
    const { n } = CURVE;
    return h >= n ? h - n : h;
}
let _sha256Sync;
let _hmacSha256Sync;
class HmacDrbg {
    constructor(hashLen, qByteLen) {
        this.hashLen = hashLen;
        this.qByteLen = qByteLen;
        if (typeof hashLen !== 'number' || hashLen < 2)
            throw new Error('hashLen must be a number');
        if (typeof qByteLen !== 'number' || qByteLen < 2)
            throw new Error('qByteLen must be a number');
        this.v = new Uint8Array(hashLen).fill(1);
        this.k = new Uint8Array(hashLen).fill(0);
        this.counter = 0;
    }
    hmac(...values) {
        return exports.utils.hmacSha256(this.k, ...values);
    }
    hmacSync(...values) {
        return _hmacSha256Sync(this.k, ...values);
    }
    checkSync() {
        if (typeof _hmacSha256Sync !== 'function')
            throw new ShaError('hmacSha256Sync needs to be set');
    }
    incr() {
        if (this.counter >= 1000)
            throw new Error('Tried 1,000 k values for sign(), all were invalid');
        this.counter += 1;
    }
    async reseed(seed = new Uint8Array()) {
        this.k = await this.hmac(this.v, Uint8Array.from([0x00]), seed);
        this.v = await this.hmac(this.v);
        if (seed.length === 0)
            return;
        this.k = await this.hmac(this.v, Uint8Array.from([0x01]), seed);
        this.v = await this.hmac(this.v);
    }
    reseedSync(seed = new Uint8Array()) {
        this.checkSync();
        this.k = this.hmacSync(this.v, Uint8Array.from([0x00]), seed);
        this.v = this.hmacSync(this.v);
        if (seed.length === 0)
            return;
        this.k = this.hmacSync(this.v, Uint8Array.from([0x01]), seed);
        this.v = this.hmacSync(this.v);
    }
    async generate() {
        this.incr();
        let len = 0;
        const out = [];
        while (len < this.qByteLen) {
            this.v = await this.hmac(this.v);
            const sl = this.v.slice();
            out.push(sl);
            len += this.v.length;
        }
        return concatBytes(...out);
    }
    generateSync() {
        this.checkSync();
        this.incr();
        let len = 0;
        const out = [];
        while (len < this.qByteLen) {
            this.v = this.hmacSync(this.v);
            const sl = this.v.slice();
            out.push(sl);
            len += this.v.length;
        }
        return concatBytes(...out);
    }
}
function isWithinCurveOrder(num) {
    return _0n < num && num < CURVE.n;
}
function isValidFieldElement(num) {
    return _0n < num && num < CURVE.P;
}
function kmdToSig(kBytes, m, d, lowS = true) {
    const { n } = CURVE;
    const k = truncateHash(kBytes, true);
    if (!isWithinCurveOrder(k))
        return;
    const kinv = invert(k, n);
    const q = Point.BASE.multiply(k);
    const r = mod(q.x, n);
    if (r === _0n)
        return;
    const s = mod(kinv * mod(m + d * r, n), n);
    if (s === _0n)
        return;
    let sig = new Signature(r, s);
    let recovery = (q.x === sig.r ? 0 : 2) | Number(q.y & _1n);
    if (lowS && sig.hasHighS()) {
        sig = sig.normalizeS();
        recovery ^= 1;
    }
    return { sig, recovery };
}
function normalizePrivateKey(key) {
    let num;
    if (typeof key === 'bigint') {
        num = key;
    }
    else if (typeof key === 'number' && Number.isSafeInteger(key) && key > 0) {
        num = BigInt(key);
    }
    else if (typeof key === 'string') {
        if (key.length !== 2 * groupLen)
            throw new Error('Expected 32 bytes of private key');
        num = hexToNumber(key);
    }
    else if (key instanceof Uint8Array) {
        if (key.length !== groupLen)
            throw new Error('Expected 32 bytes of private key');
        num = bytesToNumber(key);
    }
    else {
        throw new TypeError('Expected valid private key');
    }
    if (!isWithinCurveOrder(num))
        throw new Error('Expected private key: 0 < key < n');
    return num;
}
function normalizePublicKey(publicKey) {
    if (publicKey instanceof Point) {
        publicKey.assertValidity();
        return publicKey;
    }
    else {
        return Point.fromHex(publicKey);
    }
}
function normalizeSignature(signature) {
    if (signature instanceof Signature) {
        signature.assertValidity();
        return signature;
    }
    try {
        return Signature.fromDER(signature);
    }
    catch (error) {
        return Signature.fromCompact(signature);
    }
}
function getPublicKey(privateKey, isCompressed = false) {
    return Point.fromPrivateKey(privateKey).toRawBytes(isCompressed);
}
exports.getPublicKey = getPublicKey;
function recoverPublicKey(msgHash, signature, recovery, isCompressed = false) {
    return Point.fromSignature(msgHash, signature, recovery).toRawBytes(isCompressed);
}
exports.recoverPublicKey = recoverPublicKey;
function isProbPub(item) {
    const arr = item instanceof Uint8Array;
    const str = typeof item === 'string';
    const len = (arr || str) && item.length;
    if (arr)
        return len === compressedLen || len === uncompressedLen;
    if (str)
        return len === compressedLen * 2 || len === uncompressedLen * 2;
    if (item instanceof Point)
        return true;
    return false;
}
function getSharedSecret(privateA, publicB, isCompressed = false) {
    if (isProbPub(privateA))
        throw new TypeError('getSharedSecret: first arg must be private key');
    if (!isProbPub(publicB))
        throw new TypeError('getSharedSecret: second arg must be public key');
    const b = normalizePublicKey(publicB);
    b.assertValidity();
    return b.multiply(normalizePrivateKey(privateA)).toRawBytes(isCompressed);
}
exports.getSharedSecret = getSharedSecret;
function bits2int(bytes) {
    const slice = bytes.length > fieldLen ? bytes.slice(0, fieldLen) : bytes;
    return bytesToNumber(slice);
}
function bits2octets(bytes) {
    const z1 = bits2int(bytes);
    const z2 = mod(z1, CURVE.n);
    return int2octets(z2 < _0n ? z1 : z2);
}
function int2octets(num) {
    return numTo32b(num);
}
function initSigArgs(msgHash, privateKey, extraEntropy) {
    if (msgHash == null)
        throw new Error(`sign: expected valid message hash, not "${msgHash}"`);
    const h1 = ensureBytes(msgHash);
    const d = normalizePrivateKey(privateKey);
    const seedArgs = [int2octets(d), bits2octets(h1)];
    if (extraEntropy != null) {
        if (extraEntropy === true)
            extraEntropy = exports.utils.randomBytes(fieldLen);
        const e = ensureBytes(extraEntropy);
        if (e.length !== fieldLen)
            throw new Error(`sign: Expected ${fieldLen} bytes of extra data`);
        seedArgs.push(e);
    }
    const seed = concatBytes(...seedArgs);
    const m = bits2int(h1);
    return { seed, m, d };
}
function finalizeSig(recSig, opts) {
    const { sig, recovery } = recSig;
    const { der, recovered } = Object.assign({ canonical: true, der: true }, opts);
    const hashed = der ? sig.toDERRawBytes() : sig.toCompactRawBytes();
    return recovered ? [hashed, recovery] : hashed;
}
async function sign(msgHash, privKey, opts = {}) {
    const { seed, m, d } = initSigArgs(msgHash, privKey, opts.extraEntropy);
    const drbg = new HmacDrbg(hashLen, groupLen);
    await drbg.reseed(seed);
    let sig;
    while (!(sig = kmdToSig(await drbg.generate(), m, d, opts.canonical)))
        await drbg.reseed();
    return finalizeSig(sig, opts);
}
exports.sign = sign;
function signSync(msgHash, privKey, opts = {}) {
    const { seed, m, d } = initSigArgs(msgHash, privKey, opts.extraEntropy);
    const drbg = new HmacDrbg(hashLen, groupLen);
    drbg.reseedSync(seed);
    let sig;
    while (!(sig = kmdToSig(drbg.generateSync(), m, d, opts.canonical)))
        drbg.reseedSync();
    return finalizeSig(sig, opts);
}
exports.signSync = signSync;
const vopts = { strict: true };
function verify(signature, msgHash, publicKey, opts = vopts) {
    let sig;
    try {
        sig = normalizeSignature(signature);
        msgHash = ensureBytes(msgHash);
    }
    catch (error) {
        return false;
    }
    const { r, s } = sig;
    if (opts.strict && sig.hasHighS())
        return false;
    const h = truncateHash(msgHash);
    let P;
    try {
        P = normalizePublicKey(publicKey);
    }
    catch (error) {
        return false;
    }
    const { n } = CURVE;
    const sinv = invert(s, n);
    const u1 = mod(h * sinv, n);
    const u2 = mod(r * sinv, n);
    const R = Point.BASE.multiplyAndAddUnsafe(P, u1, u2);
    if (!R)
        return false;
    const v = mod(R.x, n);
    return v === r;
}
exports.verify = verify;
function schnorrChallengeFinalize(ch) {
    return mod(bytesToNumber(ch), CURVE.n);
}
class SchnorrSignature {
    constructor(r, s) {
        this.r = r;
        this.s = s;
        this.assertValidity();
    }
    static fromHex(hex) {
        const bytes = ensureBytes(hex);
        if (bytes.length !== 64)
            throw new TypeError(`SchnorrSignature.fromHex: expected 64 bytes, not ${bytes.length}`);
        const r = bytesToNumber(bytes.subarray(0, 32));
        const s = bytesToNumber(bytes.subarray(32, 64));
        return new SchnorrSignature(r, s);
    }
    assertValidity() {
        const { r, s } = this;
        if (!isValidFieldElement(r) || !isWithinCurveOrder(s))
            throw new Error('Invalid signature');
    }
    toHex() {
        return numTo32bStr(this.r) + numTo32bStr(this.s);
    }
    toRawBytes() {
        return hexToBytes(this.toHex());
    }
}
function schnorrGetPublicKey(privateKey) {
    return Point.fromPrivateKey(privateKey).toRawX();
}
class InternalSchnorrSignature {
    constructor(message, privateKey, auxRand = exports.utils.randomBytes()) {
        if (message == null)
            throw new TypeError(`sign: Expected valid message, not "${message}"`);
        this.m = ensureBytes(message);
        const { x, scalar } = this.getScalar(normalizePrivateKey(privateKey));
        this.px = x;
        this.d = scalar;
        this.rand = ensureBytes(auxRand);
        if (this.rand.length !== 32)
            throw new TypeError('sign: Expected 32 bytes of aux randomness');
    }
    getScalar(priv) {
        const point = Point.fromPrivateKey(priv);
        const scalar = point.hasEvenY() ? priv : CURVE.n - priv;
        return { point, scalar, x: point.toRawX() };
    }
    initNonce(d, t0h) {
        return numTo32b(d ^ bytesToNumber(t0h));
    }
    finalizeNonce(k0h) {
        const k0 = mod(bytesToNumber(k0h), CURVE.n);
        if (k0 === _0n)
            throw new Error('sign: Creation of signature failed. k is zero');
        const { point: R, x: rx, scalar: k } = this.getScalar(k0);
        return { R, rx, k };
    }
    finalizeSig(R, k, e, d) {
        return new SchnorrSignature(R.x, mod(k + e * d, CURVE.n)).toRawBytes();
    }
    error() {
        throw new Error('sign: Invalid signature produced');
    }
    async calc() {
        const { m, d, px, rand } = this;
        const tag = exports.utils.taggedHash;
        const t = this.initNonce(d, await tag(TAGS.aux, rand));
        const { R, rx, k } = this.finalizeNonce(await tag(TAGS.nonce, t, px, m));
        const e = schnorrChallengeFinalize(await tag(TAGS.challenge, rx, px, m));
        const sig = this.finalizeSig(R, k, e, d);
        if (!(await schnorrVerify(sig, m, px)))
            this.error();
        return sig;
    }
    calcSync() {
        const { m, d, px, rand } = this;
        const tag = exports.utils.taggedHashSync;
        const t = this.initNonce(d, tag(TAGS.aux, rand));
        const { R, rx, k } = this.finalizeNonce(tag(TAGS.nonce, t, px, m));
        const e = schnorrChallengeFinalize(tag(TAGS.challenge, rx, px, m));
        const sig = this.finalizeSig(R, k, e, d);
        if (!schnorrVerifySync(sig, m, px))
            this.error();
        return sig;
    }
}
async function schnorrSign(msg, privKey, auxRand) {
    return new InternalSchnorrSignature(msg, privKey, auxRand).calc();
}
function schnorrSignSync(msg, privKey, auxRand) {
    return new InternalSchnorrSignature(msg, privKey, auxRand).calcSync();
}
function initSchnorrVerify(signature, message, publicKey) {
    const raw = signature instanceof SchnorrSignature;
    const sig = raw ? signature : SchnorrSignature.fromHex(signature);
    if (raw)
        sig.assertValidity();
    return {
        ...sig,
        m: ensureBytes(message),
        P: normalizePublicKey(publicKey),
    };
}
function finalizeSchnorrVerify(r, P, s, e) {
    const R = Point.BASE.multiplyAndAddUnsafe(P, normalizePrivateKey(s), mod(-e, CURVE.n));
    if (!R || !R.hasEvenY() || R.x !== r)
        return false;
    return true;
}
async function schnorrVerify(signature, message, publicKey) {
    try {
        const { r, s, m, P } = initSchnorrVerify(signature, message, publicKey);
        const e = schnorrChallengeFinalize(await exports.utils.taggedHash(TAGS.challenge, numTo32b(r), P.toRawX(), m));
        return finalizeSchnorrVerify(r, P, s, e);
    }
    catch (error) {
        return false;
    }
}
function schnorrVerifySync(signature, message, publicKey) {
    try {
        const { r, s, m, P } = initSchnorrVerify(signature, message, publicKey);
        const e = schnorrChallengeFinalize(exports.utils.taggedHashSync(TAGS.challenge, numTo32b(r), P.toRawX(), m));
        return finalizeSchnorrVerify(r, P, s, e);
    }
    catch (error) {
        if (error instanceof ShaError)
            throw error;
        return false;
    }
}
exports.schnorr = {
    Signature: SchnorrSignature,
    getPublicKey: schnorrGetPublicKey,
    sign: schnorrSign,
    verify: schnorrVerify,
    signSync: schnorrSignSync,
    verifySync: schnorrVerifySync,
};
Point.BASE._setWindowSize(8);
const crypto = {
    node: nodeCrypto,
    web: typeof self === 'object' && 'crypto' in self ? self.crypto : undefined,
};
const TAGS = {
    challenge: 'BIP0340/challenge',
    aux: 'BIP0340/aux',
    nonce: 'BIP0340/nonce',
};
const TAGGED_HASH_PREFIXES = {};
exports.utils = {
    bytesToHex,
    hexToBytes,
    concatBytes,
    mod,
    invert,
    isValidPrivateKey(privateKey) {
        try {
            normalizePrivateKey(privateKey);
            return true;
        }
        catch (error) {
            return false;
        }
    },
    _bigintTo32Bytes: numTo32b,
    _normalizePrivateKey: normalizePrivateKey,
    hashToPrivateKey: (hash) => {
        hash = ensureBytes(hash);
        const minLen = groupLen + 8;
        if (hash.length < minLen || hash.length > 1024) {
            throw new Error(`Expected valid bytes of private key as per FIPS 186`);
        }
        const num = mod(bytesToNumber(hash), CURVE.n - _1n) + _1n;
        return numTo32b(num);
    },
    randomBytes: (bytesLength = 32) => {
        if (crypto.web) {
            return crypto.web.getRandomValues(new Uint8Array(bytesLength));
        }
        else if (crypto.node) {
            const { randomBytes } = crypto.node;
            return Uint8Array.from(randomBytes(bytesLength));
        }
        else {
            throw new Error("The environment doesn't have randomBytes function");
        }
    },
    randomPrivateKey: () => exports.utils.hashToPrivateKey(exports.utils.randomBytes(groupLen + 8)),
    precompute(windowSize = 8, point = Point.BASE) {
        const cached = point === Point.BASE ? point : new Point(point.x, point.y);
        cached._setWindowSize(windowSize);
        cached.multiply(_3n);
        return cached;
    },
    sha256: async (...messages) => {
        if (crypto.web) {
            const buffer = await crypto.web.subtle.digest('SHA-256', concatBytes(...messages));
            return new Uint8Array(buffer);
        }
        else if (crypto.node) {
            const { createHash } = crypto.node;
            const hash = createHash('sha256');
            messages.forEach((m) => hash.update(m));
            return Uint8Array.from(hash.digest());
        }
        else {
            throw new Error("The environment doesn't have sha256 function");
        }
    },
    hmacSha256: async (key, ...messages) => {
        if (crypto.web) {
            const ckey = await crypto.web.subtle.importKey('raw', key, { name: 'HMAC', hash: { name: 'SHA-256' } }, false, ['sign']);
            const message = concatBytes(...messages);
            const buffer = await crypto.web.subtle.sign('HMAC', ckey, message);
            return new Uint8Array(buffer);
        }
        else if (crypto.node) {
            const { createHmac } = crypto.node;
            const hash = createHmac('sha256', key);
            messages.forEach((m) => hash.update(m));
            return Uint8Array.from(hash.digest());
        }
        else {
            throw new Error("The environment doesn't have hmac-sha256 function");
        }
    },
    sha256Sync: undefined,
    hmacSha256Sync: undefined,
    taggedHash: async (tag, ...messages) => {
        let tagP = TAGGED_HASH_PREFIXES[tag];
        if (tagP === undefined) {
            const tagH = await exports.utils.sha256(Uint8Array.from(tag, (c) => c.charCodeAt(0)));
            tagP = concatBytes(tagH, tagH);
            TAGGED_HASH_PREFIXES[tag] = tagP;
        }
        return exports.utils.sha256(tagP, ...messages);
    },
    taggedHashSync: (tag, ...messages) => {
        if (typeof _sha256Sync !== 'function')
            throw new ShaError('sha256Sync is undefined, you need to set it');
        let tagP = TAGGED_HASH_PREFIXES[tag];
        if (tagP === undefined) {
            const tagH = _sha256Sync(Uint8Array.from(tag, (c) => c.charCodeAt(0)));
            tagP = concatBytes(tagH, tagH);
            TAGGED_HASH_PREFIXES[tag] = tagP;
        }
        return _sha256Sync(tagP, ...messages);
    },
    _JacobianPoint: JacobianPoint,
};
Object.defineProperties(exports.utils, {
    sha256Sync: {
        configurable: false,
        get() {
            return _sha256Sync;
        },
        set(val) {
            if (!_sha256Sync)
                _sha256Sync = val;
        },
    },
    hmacSha256Sync: {
        configurable: false,
        get() {
            return _hmacSha256Sync;
        },
        set(val) {
            if (!_hmacSha256Sync)
                _hmacSha256Sync = val;
        },
    },
});

  });
  const sha256fn = _sha256.sha256 || _sha256;
  // RFC6979 needs synchronous HMAC-SHA256; JavaScriptCore has no WebCrypto.
  _secp.utils.hmacSha256Sync = function (key) {
    const h = sha256fn.hmac.create(key);
    for (let i = 1; i < arguments.length; i++) h.update(arguments[i]);
    return new Uint8Array(h.arrayBuffer());
  };
  _secp.utils.sha256Sync = function () {
    const h = sha256fn.create();
    for (let i = 0; i < arguments.length; i++) h.update(arguments[i]);
    return new Uint8Array(h.arrayBuffer());
  };
  // keccak256 is bound later, once the script's own implementation is defined.
  return { sha256: sha256fn, secp: _secp, keccak256: null };
})();


// ----------------------------- secrets ---------------------------------------
// Keys may live either in the constants above or in a ".env" file beside this
// script in the Scriptable folder. The file keeps credentials out of the script
// itself, so the script can be edited, shared or re-deployed without carrying
// them along. Constants win when both are set.
//
// Format is one KEY=value per line; blank lines and # comments are ignored:
//
//     ALCHEMY_API_KEY=your_key_here
//     PRIVATE_KEY=0xabc123...
//
// With neither source set, this stays a plain read-only widget.
function loadDotEnv() {
  const out = {};
  try {
    // iCloud first, since that is where Scriptable keeps its files; a local
    // folder is the fallback when iCloud Drive is not enabled.
    const dirs = [];
    try { const f = FileManager.iCloud(); dirs.push([f, f.documentsDirectory()]); } catch (e) {}
    try { const f = FileManager.local();  dirs.push([f, f.documentsDirectory()]); } catch (e) {}
    for (const [f, dir] of dirs) {
      const path = f.joinPath(dir, ".env");
      if (!f.fileExists(path)) continue;
      // Read first: on the normal path the file is already on the device and
      // this just works. downloadFileFromiCloud is async and this function has
      // to stay synchronous (top-level await is not available here, and the
      // constants below are evaluated at load), so an evicted file cannot be
      // waited for. Kick the download anyway so the NEXT run finds it local,
      // which is what matters for a widget that wakes on a timer.
      let text = "";
      try { text = f.readString(path) || ""; } catch (e) {}
      if (!text) {
        try {
          if (f.isFileStoredIniCloud && f.isFileStoredIniCloud(path) && !f.isFileDownloaded(path)) {
            f.downloadFileFromiCloud(path);   // deliberately not awaited
          }
        } catch (e) {}
        try { text = f.readString(path) || ""; } catch (e) {}
      }
      if (!text) continue;
      for (const line of text.split("\n")) {
        const s = line.trim();
        if (!s || s[0] === "#") continue;
        const eq = s.indexOf("=");
        if (eq < 1) continue;
        const key = s.slice(0, eq).trim();
        let val = s.slice(eq + 1).trim();
        // Tolerate quoted values.
        if ((val[0] === '"' && val.slice(-1) === '"') || (val[0] === "'" && val.slice(-1) === "'")) {
          val = val.slice(1, -1);
        }
        if (key && !(key in out)) out[key] = val;
      }
      if (Object.keys(out).length) break;
    }
  } catch (e) {}
  return out;
}




// --- Keccak-256 (Ethereum variant, pre-FIPS padding 0x01) ---
const KECCAK_RC = [
  0x0000000000000001n, 0x0000000000008082n, 0x800000000000808An,
  0x8000000080008000n, 0x000000000000808Bn, 0x0000000080000001n,
  0x8000000080008081n, 0x8000000000008009n, 0x000000000000008An,
  0x0000000000000088n, 0x0000000080008009n, 0x000000008000000An,
  0x000000008000808Bn, 0x800000000000008Bn, 0x8000000000008089n,
  0x8000000000008003n, 0x8000000000008002n, 0x8000000000000080n,
  0x000000000000800An, 0x800000008000000An, 0x8000000080008081n,
  0x8000000000008080n, 0x0000000080000001n, 0x8000000080008008n,
];
const KECCAK_RHO = [1,3,6,10,15,21,28,36,45,55,2,14,27,41,56,8,25,43,62,18,39,61,20,44];
const KECCAK_PI  = [10,7,11,17,18,3,5,16,8,21,24,4,15,23,19,13,12,2,20,14,22,9,6,1];
function keccak256(hexInput) {
  const input = hexInput.replace("0x", "");
  const bytes = [];
  for (let i = 0; i < input.length; i += 2) bytes.push(parseInt(input.substr(i, 2), 16));
  const rate = 136;
  const padLen = rate - (bytes.length % rate);
  if (padLen === 1) { bytes.push(0x81); }
  else { bytes.push(0x01); for (let i = 1; i < padLen - 1; i++) bytes.push(0); bytes.push(0x80); }

  const state = new Array(25).fill(0n);
  const mask64 = (1n << 64n) - 1n;
  const rotl64 = (x, n) => ((x << n) | (x >> (64n - n))) & mask64;

  for (let off = 0; off < bytes.length; off += rate) {
    for (let i = 0; i < 17; i++) {
      let lane = 0n;
      for (let b = 0; b < 8; b++) lane |= BigInt(bytes[off + i * 8 + b]) << BigInt(b * 8);
      state[i] ^= lane;
    }
    for (let round = 0; round < 24; round++) {
      // theta
      const C = new Array(5);
      for (let x = 0; x < 5; x++) C[x] = state[x] ^ state[x+5] ^ state[x+10] ^ state[x+15] ^ state[x+20];
      for (let x = 0; x < 5; x++) {
        const D = C[(x+4)%5] ^ rotl64(C[(x+1)%5], 1n);
        for (let y = 0; y < 25; y += 5) state[x+y] = (state[x+y] ^ D) & mask64;
      }
      // rho + pi
      let current = state[1];
      for (let t = 0; t < 24; t++) {
        const j = KECCAK_PI[t];
        const tmp = state[j];
        state[j] = rotl64(current, BigInt(KECCAK_RHO[t]));
        current = tmp;
      }
      // chi
      for (let y = 0; y < 25; y += 5) {
        const t0=state[y], t1=state[y+1], t2=state[y+2], t3=state[y+3], t4=state[y+4];
        state[y]   = (t0 ^ ((~t1 & mask64) & t2)) & mask64;
        state[y+1] = (t1 ^ ((~t2 & mask64) & t3)) & mask64;
        state[y+2] = (t2 ^ ((~t3 & mask64) & t4)) & mask64;
        state[y+3] = (t3 ^ ((~t4 & mask64) & t0)) & mask64;
        state[y+4] = (t4 ^ ((~t0 & mask64) & t1)) & mask64;
      }
      // iota
      state[0] = (state[0] ^ KECCAK_RC[round]) & mask64;
    }
  }
  let out = "";
  for (let i = 0; i < 4; i++) {
    let lane = state[i];
    for (let b = 0; b < 8; b++) { out += (Number(lane & 0xFFn)).toString(16).padStart(2, "0"); lane >>= 8n; }
  }
  return out;
}
// keccak256 lives here rather than in the vendored bundle: the script already
// had a correct implementation, so binding it saves shipping a second one.
CRYPTO.keccak256 = function (input) {
  const hex = typeof input === "string" ? input
    : Array.from(input, b => b.toString(16).padStart(2, "0")).join("");
  return keccak256(hex).replace(/^0x/, "");
};

const RPC_URL    = "https://rpc.mainnet.chain.robinhood.com";
const RPC_PUBLIC = "https://robinhood-rpc.publicnode.com";
const REFRESH_MIN = 15;
const WIDGET_BUDGET_MS = 20_000;

// Blockscout instance for chain 4663. Used only to look up which position NFTs a
// wallet owns; every value shown is still read from chain state. Keyless.
// Blockscout has two endpoints for this chain and they are not equivalent.
//
//   hosted  api.blockscout.com/4663/api/v2   metered, needs BLOCKSCOUT_API_KEY
//   self    robinhoodchain.blockscout.com    community instance, no auth
//
// Measured head to head on the same requests: bulk transfers 4.0s hosted while
// the self-hosted one timed out; wallet balances 0.7s; a history page that
// returned 504 self-hosted returned 200 hosted. The self-hosted instance also
// ignores API keys entirely, so a key there does nothing.
//
// With a key, requests go to the hosted API. Without one, the community
// instance still works, just slower and less reliably.
const EXPLORER_SELF = "https://robinhoodchain.blockscout.com";
const EXPLORER_HOSTED = "https://api.blockscout.com/4663";
function explorerBase() {
  return activeBlockscoutKey() ? EXPLORER_HOSTED : EXPLORER_SELF;
}

// The keyed API can stop answering while the key itself is fine: on
// 2026-09-26 it returned 402 "Out of credits" for every call, and positions
// opened after that had no age, no %/d and no claimed fees, because their mint
// could not be looked up. Checked once per run (and remembered for 30 minutes,
// so widget runs pay nothing): if it refuses with 401/402/403, every explorer
// call goes to the public instance instead until the next check.
let _hostedExplorerDown = false;
// The key in use: the first of BLOCKSCOUT_API_KEY, _2 ... _9 (all read from
// RH Secrets) that the keyed API still accepts. Backups are optional lines in
// RH Secrets; with none, this is simply BLOCKSCOUT_API_KEY.
let _explorerKey = null;
function blockscoutKeys() {
  const keys = [secret(BLOCKSCOUT_API_KEY, "BLOCKSCOUT_API_KEY")];
  for (let i = 2; i <= 9; i++) keys.push(secret("", `BLOCKSCOUT_API_KEY_${i}`));
  return keys.filter(Boolean);
}
// For the footer: which explorer answered this run, so a missing figure can be
// traced to a refused key or a rate-limited public instance.
let _mintLookupNote = "";
function explorerNote() {
  const keys = blockscoutKeys();
  if (!keys.length) return "explorer public (no key)";
  if (_hostedExplorerDown) return `explorer public (${keys.length} key${keys.length > 1 ? "s" : ""} refused)`;
  const i = _explorerKey != null ? keys.indexOf(_explorerKey) : 0;
  return `explorer key ${i + 1}/${keys.length}`;
}
function activeBlockscoutKey() {
  if (_hostedExplorerDown) return "";
  return _explorerKey != null ? _explorerKey : (blockscoutKeys()[0] || "");
}
async function checkHostedExplorer() {
  const keys = blockscoutKeys();
  if (!keys.length) return;
  const c = loadJson("explorer_health");
  if (c && Date.now() - (c.at || 0) < 30 * 60 * 1000 && c.n === keys.length) {
    _hostedExplorerDown = !!c.down;
    _explorerKey = c.down ? null : (keys[c.idx || 0] || keys[0]);
    return;
  }
  for (let i = 0; i < keys.length; i++) {
    try {
      const req = new Request(EXPLORER_HOSTED + "/api/v2/stats?apikey=" + encodeURIComponent(keys[i]));
      req.headers = { "User-Agent": EXPLORER_UA, "Accept": "application/json" };
      req.timeoutInterval = 8;
      await req.loadString();
      const code = req.response && req.response.statusCode;
      if (code === 401 || code === 402 || code === 403) continue;
      _explorerKey = keys[i]; _hostedExplorerDown = false;
      saveJson("explorer_health", { at: Date.now(), down: false, idx: i, n: keys.length, code });
      return;
    } catch (e) { return; /* offline: decide on the next run */ }
  }
  _explorerKey = null; _hostedExplorerDown = true;
  saveJson("explorer_health", { at: Date.now(), down: true, n: keys.length });
}
// The hosted API takes the key as a query parameter; the header form is not
// accepted there. Callers build "<base>/api/v2/<path>" and pass it through here.
// Whether the keyed host is in use, so the footer can say which one answered
// rather than leaving a slow load ambiguous.
const EXPLORER_KEYED = !!secret(BLOCKSCOUT_API_KEY, "BLOCKSCOUT_API_KEY");

function explorerUrl(path) {
  const k = activeBlockscoutKey();
  if (!k) return EXPLORER_SELF + path;
  return EXPLORER_HOSTED + path + (path.includes("?") ? "&" : "?") + "apikey=" + encodeURIComponent(k);
}
const EXPLORER_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
                  + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const EXPLORER_MAX_PAGES = config.runsInWidget ? 3 : 6;
const MINT_TX_PAGE = 200;   // transfers pulled when refreshing mint timestamps
const ZERO_ADDR = "0x0000000000000000000000000000000000000000";
const MINT_RETRY_MS = 10 * 60 * 1000;   // backoff between mint-timestamp attempts

// DexScreener: keyless, ~300 req/min. Used for 1h volume and the ETH price that
// values wallet ETH/WETH. Market cap is computed on-chain instead: see
// fetchTotalSupplies for why DexScreener's cannot be trusted per pool.
const DEX_PAIRS  = "https://api.dexscreener.com/latest/dex/pairs/robinhood/";
const DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/robinhood/";

// Wallet cash counted toward net worth alongside the LP positions.
const USDG_ADDR = "5fc5360d0400a0fd4f2af552add042d716f1d168";
const WETH_ADDR = "0bd7d308f8e1639fab988df18a8011f41eacad73";
const ZERO_TOKEN = "0000000000000000000000000000000000000000";  // native ETH in a PoolKey

// Uniswap V4 on Robinhood Chain (4663)
const POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7";
// The singleton that holds every V4 pool. Its ModifyLiquidity events name the
// pool each position belongs to, which is how a batch close is split exactly.
const POOL_MANAGER = "0x8366a39CC670B4001A1121B8F6A443A643e40951";
const STATE_VIEW       = "0xf3334192d15450cdd385c8b70e03f9a6bd9e673b";
const MULTICALL3       = "0xcA11bde05977b3631167028862bE2a173976CA11";

// Stablecoins and native assets with fixed USD prices
const STABLES = {
  "5fc5360d0400a0fd4f2af552add042d716f1d168": { symbol: "USDG", decimals: 6, usd: 1.0 },
  "0000000000000000000000000000000000000000": { symbol: "ETH",  decimals: 18, usd: null },
};
// Dynamic token registry: populated from cache + on-chain discovery
const KNOWN_TOKENS = { ...STABLES };

const SCAN_BATCH = 1000;

// Function selectors (keccak256 first 4 bytes)
const SEL = {
  ownerOf:                "6352211e",
  balanceOf:              "70a08231",
  nextTokenId:            "75794a3c",
  getPoolAndPositionInfo: "7ba03aad",
  getPositionLiquidity:   "1efeed33",
  getSlot0:               "c815641c",
  symbol:                 "95d89b41",
  decimals:               "313ce567",
  getPositionInfo:        "97fd7b42",
  // A different overload from getPositionInfo above, addressed by pool, owner,
  // tick range and salt. Both are needed; neither replaces the other.
  getPositionInfoAtRange: "dacf1d2f",   // by (poolId, owner, ticks, salt)
  getFeeGrowthInside:     "53e9c1fb",
  getEthBalance:          "4d2301cc",   // on Multicall3, not the token
  totalSupply:            "18160ddd",
};

const Q128 = 1n << 128n;
const U256 = 1n << 256n;

function isAddress(s) { return /^0x[0-9a-fA-F]{40}$/.test(s); }

// The wallet is derived from the private key rather than taken from a parameter.
// Showing one wallet's positions while holding another's key would offer Close
// buttons that can only ever revert, so the two cannot be allowed to disagree.
function normPriv(k) {
  const s = String(k || "").replace(/^0x/, "").trim();
  if (!/^[0-9a-fA-F]{64}$/.test(s)) return null;
  const n = BigInt("0x" + s);
  // Valid keys are 1..n-1 over the secp256k1 curve order.
  const ORDER = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141n;
  if (n === 0n || n >= ORDER) return null;
  return s;
}
const PRIV = normPriv(secret(PRIVATE_KEY, "PRIVATE_KEY"));
let WALLET = PRIV
  ? "0x" + CRYPTO.keccak256(CRYPTO.secp.getPublicKey(PRIV, false).slice(1)).slice(-40)
  : "";
let NAME = DEFAULT_NAME;

// Showing positions needs only the address, never the key. Accept a wallet from
// the widget Parameter ("0x…" or "Name|0x…") and from the ?wallet= on the run
// URL, so a widget still renders when the key is not readable: .env lives in
// iCloud and can be evicted from the device, which used to leave the widget
// with no wallet and a permanent "Set a wallet". The error message had promised
// this fallback for a while without it existing.
const _param = (args.widgetParameter || "").trim();
if (!WALLET && _param) {
  if (_param.includes("|")) {
    const i = _param.indexOf("|");
    NAME = _param.slice(0, i).trim();
    WALLET = _param.slice(i + 1).trim();
  } else {
    WALLET = _param;
  }
}
const _qp = args.queryParameters || {};
if (!WALLET && _qp.wallet) WALLET = _qp.wallet;
if (!NAME && _qp.name) NAME = _qp.name;
// A pasted key or pool id is 32 bytes; an address is 20. Reject the rest so a
// mistake reads as a mistake rather than an RPC revert. A private key pasted
// here would also be exposed, since widget parameters are not secret.
if (WALLET && !/^0x[0-9a-fA-F]{40}$/.test(WALLET)) WALLET = "";
WALLET = WALLET.toLowerCase();

const APP_URL = `https://app.lpagent.io/portfolio?address=${WALLET}&chain=ROBINHOOD`;

// ----------------------------- palette (Uniswap) -----------------------------
const COL = {
  text:   new Color("#F5F5FF"),
  muted:  new Color("#9595B2"),
  faint:  new Color("#6C6C89"),
  accent: new Color("#FF007A"),   // Uniswap pink
  blue:   new Color("#0A84FF"),   // iOS system blue, as used by addButton
  yellow: new Color("#F5BD00"),
  green:  new Color("#30D158"),
  red:    new Color("#FF453A"),
  bgTop:  new Color("#1B1B2F"),
  bgBot:  new Color("#0D0D1A"),
};

// ----------------------------- ABI helpers ------------------------------------
function hexU256(n) {
  if (typeof n === "bigint") return n.toString(16).padStart(64, "0");
  return Math.floor(n).toString(16).padStart(64, "0");
}
function hexAddr(a) {
  return a.replace("0x", "").toLowerCase().padStart(64, "0");
}
function decU256(hex) {
  return BigInt("0x" + (hex || "0"));
}
function decAddr(hex64) {
  return hex64.slice(24);
}
function decInt24(raw) {
  return raw >= 0x800000 ? raw - 0x1000000 : raw;
}
function padCalldata(hex) {
  const bytes = hex.length / 2;
  const padded = Math.ceil(bytes / 32) * 32;
  return hex + "0".repeat((padded - bytes) * 2);
}

// ----------------------------- Multicall3 encoder -----------------------------
// tryAggregate(bool requireSuccess, (address target, bytes callData)[] calls)
// returns (bool success, bytes returnData)[]
function encodeTryAggregate(calls) {
  const sel = "bce38bd7";
  let head = sel;
  head += hexU256(0n);    // requireSuccess = false
  head += hexU256(64n);   // offset to calls array

  let arr = hexU256(BigInt(calls.length)); // array length

  // compute tuple sizes and offsets
  const tuples = [];
  for (const c of calls) {
    const cd = c.data; // hex without 0x prefix
    const cdBytes = cd.length / 2;
    const cdPadded = padCalldata(cd);
    const tuple = hexAddr(c.target)       // address (32 bytes)
                + hexU256(64n)            // offset to bytes within tuple
                + hexU256(BigInt(cdBytes)) // bytes length
                + cdPadded;               // bytes data
    tuples.push(tuple);
  }

  let offsets = "";
  let currentOffset = calls.length * 32;
  for (const t of tuples) {
    offsets += hexU256(BigInt(currentOffset));
    currentOffset += t.length / 2; // add tuple byte size
  }

  return "0x" + head + arr + offsets + tuples.join("");
}

function decodeTryAggregateResult(hex) {
  const r = hex.startsWith("0x") ? hex.slice(2) : hex;
  // returns (Result[]) where Result = (bool success, bytes data)
  // ABI: offset to array, then array data
  const arrOffset = Number(decU256(r.slice(0, 64))) * 2;
  const arrLen = Number(decU256(r.slice(arrOffset, arrOffset + 64)));
  const elemsStart = arrOffset + 64;

  const results = [];
  for (let i = 0; i < arrLen; i++) {
    const elemOffset = Number(decU256(r.slice(elemsStart + i * 64, elemsStart + (i + 1) * 64))) * 2;
    const absOffset = elemsStart + elemOffset;
    const success = Number(decU256(r.slice(absOffset, absOffset + 64))) !== 0;
    const dataOffset = Number(decU256(r.slice(absOffset + 64, absOffset + 128))) * 2;
    const dataAbsOffset = absOffset + dataOffset;
    const dataLen = Number(decU256(r.slice(dataAbsOffset, dataAbsOffset + 64)));
    const data = r.slice(dataAbsOffset + 64, dataAbsOffset + 64 + dataLen * 2);
    results.push({ success, data });
  }
  return results;
}

// ----------------------------- RPC -------------------------------------------
const ALCHEMY_RPC_BASE = "https://robinhood-mainnet.g.alchemy.com/v2/";

function alchemyRpcFrom(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw.startsWith(ALCHEMY_RPC_BASE)) return raw;
  // Alchemy keys currently contain letters, numbers, underscores and hyphens.
  // Reject other input instead of producing a malformed or unsafe URL.
  return /^[A-Za-z0-9_-]+$/.test(raw) ? ALCHEMY_RPC_BASE + raw : "";
}

// Precedence: key pasted in CONFIG, then optional iCloud-only RHSecrets.js.
// Keeping CONFIG blank lets this distributed script remain credential-free.
function loadPrivateRpc() {
  const configured = alchemyRpcFrom(secret(ALCHEMY_API_KEY, "ALCHEMY_API_KEY"));
  if (configured) return configured;
  try {
    if (typeof importModule !== "function") return "";
    const secrets = importModule("RHSecrets");
    const url = secrets && secrets.ROBINHOOD_RPC_URL;
    if (typeof url === "string" && /^https:\/\//.test(url.trim())) {
      return url.trim();
    }
    return alchemyRpcFrom(secrets && secrets.ALCHEMY_API_KEY);
  } catch (_) {
    return "";
  }
}
const RPC_KEYED = loadPrivateRpc();
const RPC_URLS = RPC_KEYED ? [RPC_KEYED] : [RPC_URL, RPC_PUBLIC];
const RPC_TIMEOUT_SEC = 5;
// Short, and in-memory only: long enough to stop a burst of calls all hammering
// a failing endpoint, short enough that it never becomes the reason nothing
// works. It is cleared by the first success.
const RPC_FAIL_COOLDOWN_MS = 8 * 1000;

// Alchemy rejects anything over its per-second compute limit at the edge: HTTP
// 403 with an empty body, which never reaches the account logs. A burst across
// dozens of pools passes that limit easily, so treat it as a wait, not a fault.
const RPC_RETRIES = 5;
const RPC_BACKOFF_MS = 400;

let _rpcBlockedUntil = null;

async function rpcEthCall(url, to, data) {
  const req = new Request(url);
  req.method = "POST";
  req.headers = { "Content-Type": "application/json" };
  req.body = JSON.stringify({
    jsonrpc: "2.0", method: "eth_call",
    params: [{ to, data }, "latest"], id: 1,
  });
  req.timeoutInterval = RPC_TIMEOUT_SEC;
  // Read-only, so retrying is always safe.
  let lastErr = null;
  for (let attempt = 0; attempt < RPC_RETRIES; attempt++) {
    if (attempt) await pause(RPC_BACKOFF_MS * Math.pow(2, attempt - 1));
    let body;
    try {
      body = await req.loadString();
    } catch (e) { lastErr = e; continue; }
    if (!body || body[0] !== "{") {
      lastErr = new Error("rate limited by the RPC provider");
      continue;
    }
    const j = JSON.parse(body);
    if (j && j.result != null) return j.result;
    throw new Error((j && j.error && j.error.message) || "RPC error");
  }
  throw new Error(String((lastErr && lastErr.message) || lastErr || "RPC unavailable"));
}

async function ethCall(to, data) {
  

// Deliberately NOT restored from disk. A cooldown written by an earlier run
  // used to survive into the next launch, so opening the app after a blip meant
  // it refused to even try and just showed stale data. In-memory only: every
  // fresh run gets a real attempt, and the backoff only damps a burst inside one
  // run against an endpoint that is currently failing.
  if (_rpcBlockedUntil == null) _rpcBlockedUntil = 0;
  if (Date.now() < _rpcBlockedUntil) {
    const wait = Math.max(1, Math.ceil((_rpcBlockedUntil - Date.now()) / 1000));
    throw new Error(`RPC busy, retrying in ${wait}s.`);
  }
  try {
    const out = await new Promise((resolve, reject) => {
      let left = RPC_URLS.length, last = null, settled = false;
      for (const url of RPC_URLS) {
        rpcEthCall(url, to, data).then(value => {
          if (!settled) { settled = true; resolve(value); }
        }).catch(e => {
          last = e;
          if (--left === 0 && !settled) reject(last || new Error("RPC error"));
        });
      }
    });
    _rpcBlockedUntil = 0;
    return out;
  } catch (e) {
    _rpcBlockedUntil = Date.now() + RPC_FAIL_COOLDOWN_MS;
    throw new Error(`RPC unavailable. ${String((e && e.message) || e)}`);
  }
}

// Multicalls issued in the same tick (the parallel read batch in main) are
// Reads issued in the same tick (the parallel batch in main) are merged into one
// tryAggregate and the results split back out, so a burst of five reads costs
// one round trip instead of five. The node serves concurrent requests serially,
// so unmerged parallel calls pay their latency one after another. One giant
// call does not complete either, so the merged batch goes out in chunks that
// run in parallel: latency of a single round trip, payloads the node can serve.
const MC_CHUNK = 30;
let _mcQueue = null;
function multicall(calls) {
  if (!calls.length) return Promise.resolve([]);
  if (!_mcQueue) {
    _mcQueue = [];
    Promise.resolve().then(flushMulticalls);
  }
  return new Promise((resolve, reject) => _mcQueue.push({ calls, resolve, reject }));
}
async function flushMulticalls() {
  const q = _mcQueue;
  _mcQueue = null;
  const all = [];
  for (const e of q) for (const c of e.calls) all.push(c);
  try {
    const chunks = [];
    for (let i = 0; i < all.length; i += MC_CHUNK) chunks.push(all.slice(i, i + MC_CHUNK));
    const parts = await Promise.all(chunks.map(ch =>
      ethCall(MULTICALL3, encodeTryAggregate(ch)).then(decodeTryAggregateResult)));
    const res = [].concat(...parts);
    let i = 0;
    for (const e of q) { e.resolve(res.slice(i, i + e.calls.length)); i += e.calls.length; }
  } catch (err) {
    for (const e of q) e.reject(err);
  }
}
function pause(ms) { return new Promise(r => Timer.schedule(ms, false, r)); }

// ============================ CLOSING POSITIONS ==============================
// Everything below is what separates this script from the read-only tracker.
// Encoding here was verified byte-for-byte against real closes mined on this
// chain, and the whole path is simulated with eth_call before anything is sent.

const CHAIN_ID = 4663;
// V4 periphery action ids, confirmed by decoding live PositionManager traffic.
const ACT_DECREASE_LIQUIDITY = "01";
const ACT_MINT_POSITION = "02";
const ACT_BURN_POSITION = "03";
const ACT_SWAP_EXACT_IN_SINGLE = "06";
const ACT_SETTLE_ALL    = "0c";
const ACT_SETTLE_PAIR   = "0d";
const ACT_TAKE_ALL      = "0f";
const ACT_TAKE_PAIR     = "11";
const ACT_CLOSE_CURRENCY = "12";

const UNIVERSAL_ROUTER = "0x8876789976dEcBfCbBbe364623C63652db8C0904";
const PERMIT2          = "0x000000000022D473030F116dDEE9F6B43aC78BA3";
const UR_V4_SWAP       = "10";   // UniversalRouter command byte
const ETH_ADDR         = "0x0000000000000000000000000000000000000000";
const WETH_FULL        = "0x" + WETH_ADDR;

// Re-entry re-deposits fractionally less than the close released, so the mint
// can never need more than the burn produced. That keeps the net delta positive
// and lets CLOSE_CURRENCY refund the remainder instead of pulling from the
// wallet, which would need a Permit2 approval and a second transaction.
//
// The full amount is redeposited. Safety comes from trimming the liquidity by a
// billionth instead (see liquidityForAmount), which costs a few wei rather than
// a slice of the deposit. A percentage margin here compounded on every
// re-entry: 0.5% took a $700 ladder to $693 after two.
const REENTRY_KEEP_BPS = 10000;  // 100% of recovered amount

const SEL_BALANCE_OF     = "70a08231";  // ERC20 balanceOf(address)
const SEL_ALLOWANCE      = "dd62ed3e";  // ERC20 allowance(address,address)
const SEL_APPROVE        = "095ea7b3";  // ERC20 approve(address,uint256)
const SEL_P2_ALLOWANCE   = "927da105";  // Permit2 allowance(address,address,address)
const SEL_P2_APPROVE     = "87517c45";  // Permit2 approve(address,address,uint160,uint48)

const _hexToBytes = h => {
  const s = String(h || "").replace(/^0x/, "");
  const p = s.length % 2 ? "0" + s : s;
  const o = new Uint8Array(p.length / 2);
  for (let i = 0; i < o.length; i++) o[i] = parseInt(p.slice(i * 2, i * 2 + 2), 16);
  return o;
};
const _bytesToHex = b => Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
const _concat = arrs => {
  let n = 0; for (const a of arrs) n += a.length;
  const o = new Uint8Array(n); let k = 0;
  for (const a of arrs) { o.set(a, k); k += a.length; }
  return o;
};
// RLP encodes quantities minimally: no leading zero bytes, and zero is empty.
const _numBytes = v => {
  const n = BigInt(v);
  if (n === 0n) return new Uint8Array(0);
  let s = n.toString(16); if (s.length % 2) s = "0" + s;
  return _hexToBytes(s);
};
function _rlpLen(len, offset) {
  if (len < 56) return Uint8Array.of(offset + len);
  const lb = _numBytes(len);
  return _concat([Uint8Array.of(offset + 55 + lb.length), lb]);
}
function _rlp(item) {
  if (Array.isArray(item)) {
    const body = _concat(item.map(_rlp));
    return _concat([_rlpLen(body.length, 0xc0), body]);
  }
  const b = item instanceof Uint8Array ? item : _hexToBytes(item);
  if (b.length === 1 && b[0] < 0x80) return b;
  return _concat([_rlpLen(b.length, 0x80), b]);
}

const _u = v => BigInt(v).toString(16).padStart(64, "0");
const _a = x => String(x || "").replace(/^0x/, "").toLowerCase().padStart(64, "0");
const _encBytes = h => {
  const s = String(h || "").replace(/^0x/, "");
  return _u(s.length / 2) + s.padEnd(Math.ceil(s.length / 64) * 64, "0");
};
// abi.encode(bytes actions, bytes[] params)
function _unlockData(actionsHex, paramsHex) {
  const ab = _encBytes(actionsHex);
  let offs = "", blobs = "", cursor = 32 * paramsHex.length;
  for (const p of paramsHex) {
    offs += _u(cursor);
    const b = _encBytes(p);
    blobs += b;
    cursor += b.length / 2;
  }
  return _u(64) + _u(64 + ab.length / 2) + ab + _u(paramsHex.length) + offs + blobs;
}
// modifyLiquidities(bytes unlockData, uint256 deadline)
function encodeModifyLiquidities(actionsHex, paramsHex, deadline) {
  const unlock = _unlockData(actionsHex, paramsHex);
  return "0xdd46508f" + _u(64) + _u(deadline) + _u(unlock.length / 2) + unlock;
}
// BURN_POSITION(uint256 tokenId, uint128 amount0Min, uint128 amount1Min, bytes hookData)
const burnParam = (tokenId, min0, min1) =>
  _u(tokenId) + _u(min0 || 0) + _u(min1 || 0) + _u(128) + _u(0);
// TAKE_PAIR(Currency currency0, Currency currency1, address recipient)
const takePairParam = (c0, c1, to) => _a(c0) + _a(c1) + _a(to);
// DECREASE_LIQUIDITY(uint256 tokenId, uint256 liquidity, uint128 amount0Min,
//                    uint128 amount1Min, bytes hookData)
// Decreasing by zero collects the accrued fees and leaves the position intact,
// which is how a fee claim is expressed in V4.
const decreaseParam = (tokenId, liquidity, min0, min1) =>
  _u(tokenId) + _u(liquidity || 0) + _u(min0 || 0) + _u(min1 || 0) + _u(160) + _u(0);

// SETTLE_PAIR(Currency currency0, Currency currency1)
const settlePairParam = (c0, c1) => _a(c0) + _a(c1);
// CLOSE_CURRENCY(Currency currency): settles or takes whichever way the net
// delta points, so it refunds dust without ever needing an allowance.
const closeCurrencyParam = c => _a(c);

// Two's-complement encoding for the signed tick fields.
const _i = v => {
  const n = BigInt(v);
  return (n < 0n ? n + (1n << 256n) : n).toString(16).padStart(64, "0");
};

// MINT_POSITION(PoolKey, int24 tickLower, int24 tickUpper, uint256 liquidity,
//               uint128 amount0Max, uint128 amount1Max, address owner, bytes hookData)
const mintParam = (pk, tickLower, tickUpper, liquidity, max0, max1, owner) =>
  _a(pk.currency0) + _a(pk.currency1) + _u(pk.fee) + _i(pk.tickSpacing) + _a(pk.hooks)
  + _i(tickLower) + _i(tickUpper) + _u(liquidity) + _u(max0) + _u(max1) + _a(owner)
  + _u(384) + _u(0);

// Swaps are routed by the Uniswap API, not encoded here. Hand-built V4 swap
// calldata was correct by construction (it reproduced a mined swap byte for
// byte) yet reverted every time, because this chain uses fee tiers such as
// 9000/90 that no standard list contains: the guessed pool keys resolved to
// initialised-but-empty pools. See swapOutTo.

// ----------------------------- reentry math ----------------------------------
// A single-sided position sits wholly on one side of the current price. Re-entering
// means keeping the tick width and sliding the range so it starts at the current
// price, still on the same side, so the same token funds it and no swap is needed.
function reentryRange(pos, currentTick) {
  const width = pos.tickUpper - pos.tickLower;
  const sp = pos.tickSpacing;
  const snapUp   = t => Math.ceil(t / sp) * sp;
  const snapDown = t => Math.floor(t / sp) * sp;
  // Above the current price the range holds only currency0; below, only currency1.
  // A range is single-sided in currency0 only while tick < tickLower strictly, so
  // the lower bound is pushed past the current tick; landing exactly on it would
  // make the position in-range and require both tokens to mint. The currency1
  // case is inclusive (tick >= tickUpper), so it needs no such nudge.
  if (currentTick < pos.tickLower) {
    const lower = snapUp(currentTick + 1);
    return { tickLower: lower, tickUpper: lower + width, side: 0 };
  }
  if (currentTick >= pos.tickUpper) {
    const upper = snapDown(currentTick);
    return { tickLower: upper - width, tickUpper: upper, side: 1 };
  }
  return null;   // still in range: nothing to re-enter
}

// Liquidity that a given single-sided amount buys over a tick range.
const _sqrtAt = t => Math.pow(1.0001, t / 2);
function liquidityForAmount(amount, tickLower, tickUpper, side) {
  const sa = _sqrtAt(tickLower), sb = _sqrtAt(tickUpper);
  if (!(sb > sa) || !(amount > 0)) return 0n;
  const L = side === 0 ? amount / (1 / sa - 1 / sb) : amount / (sb - sa);
  if (!Number.isFinite(L) || !(L > 0)) return 0n;
  // These ticks are evaluated in floating point while the pool uses exact
  // integer maths, so L can come out a hair high and the mint would then ask
  // for more than the burn released. Trimming a billionth off the liquidity
  // covers that: a few wei on a $700 ladder, invisible in the balance, where
  // taking the same safety out of the deposit was not. If it ever still falls
  // short the whole transaction reverts, costing a fee but never funds.
  return BigInt(Math.floor(L * (1 - 1e-9)));
}

// Plan re-entry for a whole group of positions at once.
//
// A slope ladder is several slices at stepped tick ranges with deliberately
// different sizes. Shifting each slice independently would collapse them all
// onto the same starting tick and destroy that shape, so the group is moved as
// a rigid body: one shift is computed for the group and applied to every slice.
// Relative spacing, individual widths and per-slice liquidity all survive.
//
// Restricted deliberately: the pool must be paired with USDG or ETH, and the
// side being re-deposited must be that cash side. A memecoin balance is never
// re-deposited; it stays in the wallet for a separate decision.
function planReentryGroup(positions) {
  const isCash = a => a === USDG_ADDR || a === ZERO_TOKEN || a === WETH_ADDR;
  const live = (positions || []).filter(p => p.liquidity > 0n && p.currentTick != null);
  if (!live.length) return positions;

  // Only slices sharing one pool can move together.
  const byPool = new Map();
  for (const p of live) {
    if (!byPool.has(p.poolKeyStr)) byPool.set(p.poolKeyStr, []);
    byPool.get(p.poolKeyStr).push(p);
  }

  for (const group of byPool.values()) {
    const tick = group[0].currentTick;
    const sp = group[0].tickSpacing;
    const c0 = group[0].currency0, c1 = group[0].currency1;
    if (!isCash(c0) && !isCash(c1)) continue;

    // Every slice must sit on the same side of the price for one shift to work.
    const allAbove = group.every(p => tick < p.tickLower);
    const allBelow = group.every(p => tick >= p.tickUpper);
    if (!allAbove && !allBelow) {
      for (const p of group) p.reentryWhy = group.some(q => tick >= q.tickLower && tick < q.tickUpper)
        ? "some slices are still in range" : "slices sit on both sides of the price";
      continue;
    }

    const side = allAbove ? 0 : 1;
    if (!isCash(side === 0 ? c0 : c1)) {
      for (const p of group) p.reentryWhy = "closing this would return the token, not USDG or ETH";
      continue;
    }

    // Move the nearest edge of the group adjacent to the current price, then
    // shift every slice by that same amount. Single-sided in currency0 needs
    // tick < tickLower strictly; the currency1 case is inclusive.
    const groupLo = Math.min(...group.map(p => p.tickLower));
    const groupHi = Math.max(...group.map(p => p.tickUpper));
    const shift = side === 0
      ? Math.ceil((tick + 1) / sp) * sp - groupLo
      : Math.floor(tick / sp) * sp - groupHi;
    // shift === 0 is allowed: the ladder is already on the nearest legal tick,
    // but re-entering rebuilds it fresh (useful as a "reset" and because the
    // user asked for it not to be blocked at tiny drifts). Costs one round-trip
    // of gas for no placement change, so the confirmation dialog says so.

    for (const p of group) {
      const L = Number(p.liquidity);
      const sa = _sqrtAt(p.tickLower), sb = _sqrtAt(p.tickUpper);
      const recovered = side === 0 ? L * (1 / sa - 1 / sb) : L * (sb - sa);
      if (!(recovered > 0) || !Number.isFinite(recovered)) continue;

      const tickLower = p.tickLower + shift;
      const tickUpper = p.tickUpper + shift;
      const amount = Math.floor(recovered * REENTRY_KEEP_BPS / 10000);
      if (!(amount > 0)) continue;
      const liquidity = liquidityForAmount(amount, tickLower, tickUpper, side);
      if (!(liquidity > 0n)) continue;

      p.reentry = { tickLower, tickUpper, side, amount: BigInt(amount), liquidity, shift };
    }
  }
  return positions;
}

// Close every position in the rows and immediately re-open each one at the
// current price with its original width, in a single transaction. Burning and
// minting inside one unlock lets the released tokens fund the new positions
// directly, so nothing is pulled from the wallet and no approval is needed.
function buildReentryCalldata(rows, recipient, deadline, fee) {
  let actions = "";
  const params = [];
  const currencies = new Set();
  const plan = [];

  for (const r of rows) {
    for (const p of r.positions || []) {
      if (!p.reentry) continue;
      actions += ACT_BURN_POSITION;
      params.push(burnParam(p.tokenId, 0, 0));
      currencies.add(p.currency0);
      currencies.add(p.currency1);
      plan.push(p);
    }
  }
  if (!plan.length) return null;

  for (const p of plan) {
    const rr = p.reentry;
    actions += ACT_MINT_POSITION;
    params.push(mintParam(
      p.poolKey, rr.tickLower, rr.tickUpper, rr.liquidity,
      rr.side === 0 ? rr.amount : 0, rr.side === 1 ? rr.amount : 0,
      recipient));
  }
  // The service fee, when there is one, is taken before the net-out, so the
  // CLOSE_CURRENCY that follows covers any shortfall from the wallet.
  if (fee) {
    const f = serviceFeeActions(fee);
    actions += f.actions;
    params.push(...f.params);
    for (const c of f.currencies) currencies.add(c);
  }
  // Net out each currency last: dust comes back, nothing is ever pulled in.
  for (const c of currencies) {
    actions += ACT_CLOSE_CURRENCY;
    params.push(closeCurrencyParam(c));
  }
  return { calldata: encodeModifyLiquidities(actions, params, deadline), count: plan.length,
           ladders: new Set(plan.map(p => p.poolKeyStr)).size };
}

// ----------------------------- roll math -------------------------------------
// A roll is close -> sell -> re-open, for a ladder that has FILLED.
//
// Re-entry above slides an UNFILLED cash ladder along to follow the price, all
// in one transaction, and needs no sale: the tokens the burn releases are
// exactly the tokens the mint wants. Once a ladder has filled that stops being
// true. It now holds the memecoin, so re-opening in cash means selling first,
// and a sale cannot happen inside the PositionManager's unlock. So a roll is
// three transactions where a re-entry is one: burn, sell, mint. Each is
// separately recoverable, and a failure at any step leaves value in the wallet
// rather than in a half-built position.
//
// What is carried across, which is exactly what was asked for:
//   - the tick WIDTH of every slice. Ticks are log-price, so equal tick width is
//     the same percentage width at any price: this is what makes the new range
//     the same -x% as the old one, without ever computing a percentage.
//   - the slice COUNT and the relative spacing between slices, moved as a rigid
//     body, so a stepped ladder keeps its shape instead of collapsing onto one
//     tick.
//   - the INITIAL capital, not the proceeds. Anything the position made above
//     its original cost stays in the wallet as USDG. That is the "take the
//     profit now" half; re-opening at the same size is the other half.
//
// The edge of the ladder adjacent to the current price is pinned AT the current
// price, so the new range runs from spot down by the width it always had. When
// the cash token is currency0 the arithmetic mirrors -- a cash-only range then
// sits above the current tick, and its LOWER tick is the edge nearest spot and
// therefore the highest price -- so the rule is stated as "the edge adjacent to
// spot" rather than "the upper tick", and holds in both orientations.
const ROLL_MIN_DEPLOY_RAW = 1_000_000n;   // $1 in USDG's 6 decimals

// Which side of this pool is the cash we sell back into, or null if neither is.
function rollCashSide(p) {
  if (p.currency0 === USDG_ADDR) return 0;
  if (p.currency1 === USDG_ADDR) return 1;
  return null;
}

// What a slice originally cost, in cash, derived from liquidity and its own tick
// range. Liquidity does not change as the price moves through a range, and
// neither expression below contains the price at mint, so this is still the
// original deposit even for a ladder that has completely filled. That is what
// makes "same initial capital" readable from chain state alone, with no archive
// node and no record of what was paid.
function rollSliceCost(p, side) {
  const L = Number(p.liquidity);
  const sa = _sqrtAt(p.tickLower), sb = _sqrtAt(p.tickUpper);
  if (!(sb > sa) || !(L > 0)) return 0;
  const v = side === 0 ? L * (1 / sa - 1 / sb) : L * (sb - sa);
  return Number.isFinite(v) && v > 0 ? v : 0;
}

// The shape half of the plan, knowable before anything is closed: how much the
// ladder originally cost and where it would be re-placed at the given tick.
// Split out from the funding half so the confirmation dialog can show the user
// real numbers before they commit to a burn.
function planRollShape(positions, currentTick) {
  const live = (positions || []).filter(p => p.liquidity > 0n && p.tickUpper > p.tickLower);
  if (!live.length) return { bad: "no live positions to roll" };

  const keys = new Set(live.map(p => p.poolKeyStr));
  if (keys.size !== 1) return { bad: "a roll re-opens one ladder in one pool; close these separately" };

  const side = rollCashSide(live[0]);
  if (side == null) return { bad: "this pool is not paired with USDG, so there is nothing to re-enter with" };
  if (!Number.isFinite(currentTick)) return { bad: "the pool price could not be read" };

  const sp = live[0].tickSpacing;
  if (!Number.isFinite(sp) || sp <= 0) return { bad: "the pool tick spacing could not be read" };

  const groupLo = Math.min(...live.map(p => p.tickLower));
  const groupHi = Math.max(...live.map(p => p.tickUpper));
  // Pin the edge adjacent to spot at spot. A cash-only range in currency0 needs
  // tick < tickLower strictly, hence the +1; the currency1 case is inclusive of
  // tick >= tickUpper and needs no nudge.
  const shift = side === 0
    ? Math.ceil((currentTick + 1) / sp) * sp - groupLo
    : Math.floor(currentTick / sp) * sp - groupHi;

  const costs = live.map(p => rollSliceCost(p, side));
  const initialRaw = costs.reduce((a, b) => a + b, 0);
  if (!(initialRaw > 0)) return { bad: "the original deposit could not be derived from liquidity" };

  return {
    live, side, shift, sp,
    poolKey: live[0].poolKey,
    costs,
    initialRaw: BigInt(Math.floor(initialRaw)),
    width: groupHi - groupLo,
    newLo: groupLo + shift,
    newHi: groupHi + shift,
  };
}

// The funding half: given the cash actually recovered, size each slice.
//
// Capped at the original cost deliberately. If the sale returned more than the
// ladder cost, the excess is profit and stays in the wallet -- re-deploying it
// would quietly compound the position instead of taking the gain, which is the
// opposite of what a roll is for. If it returned less, the ladder is rebuilt at
// whatever is actually there rather than reverting on a shortfall.
function planRollFunding(shape, recoveredRaw) {
  if (shape.bad) return shape;
  const budget = recoveredRaw < shape.initialRaw ? recoveredRaw : shape.initialRaw;
  if (budget < ROLL_MIN_DEPLOY_RAW) {
    return { bad: `only ${fmtUsd(Number(budget) / 1e6)} came back, too little to re-open` };
  }

  const total = shape.costs.reduce((a, b) => a + b, 0);
  const slices = [];
  let assigned = 0n;
  for (let i = 0; i < shape.live.length; i++) {
    if (!(shape.costs[i] > 0)) continue;
    const p = shape.live[i];
    // Proportional to what that slice originally held, so an uneven ladder
    // stays uneven in the same way.
    const amount = budget * BigInt(Math.round(shape.costs[i] / total * 1e9)) / 1_000_000_000n;
    if (!(amount > 0n)) continue;
    const tickLower = p.tickLower + shape.shift;
    const tickUpper = p.tickUpper + shape.shift;
    const liquidity = liquidityForAmount(Number(amount), tickLower, tickUpper, shape.side);
    if (!(liquidity > 0n)) continue;
    assigned += amount;
    slices.push({ tickLower, tickUpper, amount, liquidity });
  }
  if (!slices.length) return { bad: "no slice came out large enough to mint" };
  return { ...shape, slices, deployRaw: assigned, budgetRaw: budget,
           surplusRaw: recoveredRaw > assigned ? recoveredRaw - assigned : 0n };
}

// Mint the planned ladder, funded from the wallet.
//
// SETTLE_PAIR rather than the CLOSE_CURRENCY that re-entry uses: re-entry is
// funded by a burn in the same unlock and must never pull from the wallet,
// whereas this is funded by the wallet and must. TAKE_PAIR afterwards returns
// whatever rounding left behind, so nothing is stranded inside the manager.
function buildRollMintCalldata(poolKey, slices, side, recipient, deadline, openFeeRaw) {
  let actions = "";
  const params = [];
  for (const s of slices) {
    actions += ACT_MINT_POSITION;
    params.push(mintParam(poolKey, s.tickLower, s.tickUpper, s.liquidity,
      side === 0 ? s.amount : 0, side === 1 ? s.amount : 0, recipient));
  }
  // The open fee deepens the USDG debt by exactly its amount, and SETTLE_PAIR
  // settles the full debt, so it is paid by the same pull that funds the mint.
  if (openFeeRaw > 0n) {
    actions += ACT_TAKE;
    params.push(takeParam(USDG_ADDR, SERVICE_FEE_WALLET, openFeeRaw));
  }
  actions += ACT_SETTLE_PAIR;
  params.push(settlePairParam(poolKey.currency0, poolKey.currency1));
  actions += ACT_TAKE_PAIR;
  params.push(takePairParam(poolKey.currency0, poolKey.currency1, recipient));
  return encodeModifyLiquidities(actions, params, deadline);
}

// One modifyLiquidities that closes every position in the given rows. Positions
// are regrouped by pool so each pool contributes its burns plus the single
// TAKE_PAIR that settles that pair's balances.
function buildCloseCalldata(rows, recipient, deadline, fee) {
  const byPool = new Map();
  for (const r of rows) {
    for (const p of r.positions || []) {
      const k = p.poolKeyStr;
      if (!byPool.has(k)) byPool.set(k, { c0: p.currency0, c1: p.currency1, ps: [] });
      byPool.get(k).ps.push(p);
    }
  }
  let actions = "";
  const params = [];
  if (fee) return withServiceFee(byPool, g => g.ps.map(p => [ACT_BURN_POSITION,
    burnParam(p.tokenId, p.min0, p.min1)]), fee, deadline);
  for (const g of byPool.values()) {
    for (const p of g.ps) {
      actions += ACT_BURN_POSITION;
      params.push(burnParam(p.tokenId, p.min0, p.min1));
    }
    actions += ACT_TAKE_PAIR;
    params.push(takePairParam(g.c0, g.c1, recipient));
  }
  return { calldata: encodeModifyLiquidities(actions, params, deadline), pools: byPool.size };
}

// Collect fees from every position in the rows without touching liquidity.
function buildClaimCalldata(rows, recipient, deadline, fee) {
  const byPool = new Map();
  for (const r of rows) {
    for (const p of r.positions || []) {
      const k = p.poolKeyStr;
      if (!byPool.has(k)) byPool.set(k, { c0: p.currency0, c1: p.currency1, ps: [] });
      byPool.get(k).ps.push(p);
    }
  }
  let actions = "";
  const params = [];
  if (fee) return withServiceFee(byPool, g => g.ps.map(p => [ACT_DECREASE_LIQUIDITY,
    decreaseParam(p.tokenId, 0, 0, 0)]), fee, deadline);
  for (const g of byPool.values()) {
    for (const p of g.ps) {
      actions += ACT_DECREASE_LIQUIDITY;
      params.push(decreaseParam(p.tokenId, 0, 0, 0));
    }
    actions += ACT_TAKE_PAIR;
    params.push(takePairParam(g.c0, g.c1, recipient));
  }
  return { calldata: encodeModifyLiquidities(actions, params, deadline), pools: byPool.size };
}

// ----------------------------- service fee -----------------------------------
// The public build of this script charges a service fee, disclosed on the page
// it is downloaded from:
//
//   claim or close   1.5% of the LP fees collected, paid in USDG
//   open             0.3 USDG per ladder opened (re-entry and roll re-open one)
//
// SERVICE_FEE_WALLET is empty in the source and set only by the public build
// (tools/build-scripts.mjs), so the owner's own copy charges nothing and every
// path below is skipped.
//
// The fee is never a separate transfer. It is a TAKE inside the same
// modifyLiquidities unlock that collects the fees, so it is paid exactly when
// the claim or close succeeds and not at all when it reverts. The net-out that
// follows is CLOSE_CURRENCY per currency: if the position's own USDG does not
// cover the fee, the remainder is pulled from the wallet in that same
// transaction.
const SERVICE_FEE_WALLET = "";
const SERVICE_FEE_BPS = 150n;             // 1.5% of LP fees collected
const SERVICE_OPEN_FEE_RAW = 300_000n;    // 0.3 USDG, 6 decimals
const ACT_TAKE = "0e";
// TAKE(Currency currency, address recipient, uint256 amount)
const takeParam = (c, to, amount) => _a(c) + _a(to) + _u(amount);
const Q192 = 1n << 192n;

function serviceFeeOn() {
  const w = String(SERVICE_FEE_WALLET).replace(/^0x/, "").toLowerCase();
  if (!/^[0-9a-f]{40}$/.test(w)) return false;
  // The fee wallet paying itself is a TAKE to itself: harmless but pointless gas.
  return ("0x" + w) !== String(WALLET || "").toLowerCase();
}

// Rounded up, so rounding never shaves the cut below 1.5%.
const _cut = v => (v * SERVICE_FEE_BPS + 9_999n) / 10_000n;

// What `raw` of `token` is worth in USDG raw units, at the pool's live price.
//
// A USDG pool prices its own token exactly: sqrtPriceX96^2 / 2^192 is currency1
// per currency0 in raw units, so no decimals and no float are involved. Any
// other pool goes through the same USD price the table shows. Returns null when
// the token has no price, and the caller takes that part in kind instead.
function usdgRawOf(token, raw, pos, pp) {
  if (!(raw > 0n)) return 0n;
  if (token === USDG_ADDR) return raw;
  const S2 = pp.sqrtPriceX96 * pp.sqrtPriceX96;
  if (!(S2 > 0n)) return null;
  if (pos.currency1 === USDG_ADDR && token === pos.currency0) return raw * S2 / Q192;
  if (pos.currency0 === USDG_ADDR && token === pos.currency1) return raw * Q192 / S2;
  const px = tokenUsdPrice(token, pos, pp);
  if (!(px > 0)) return null;
  const dec = (KNOWN_TOKENS[token] || { decimals: 18 }).decimals;
  const usd = Number(raw) / Math.pow(10, dec) * px;
  return Number.isFinite(usd) ? BigInt(Math.floor(usd * 1e6)) : null;
}

// Read every position's uncollected fees fresh, at the latest block, and work
// out the cut. Not from the table: a table loaded ten minutes ago undercounts.
// Fees only ever grow, so what the transaction actually collects a few seconds
// later is at least what is read here.
//
// Fails closed. A fee that cannot be read is not assumed to be zero.
async function quoteServiceFee(positions, opens) {
  const poolKeys = new Map();
  for (const p of positions) poolKeys.set(p.poolKeyStr, p.poolKey);
  const [raw, prices] = await Promise.all([fetchFees(positions, poolKeys), fetchPoolPrices(poolKeys)]);

  let valueRaw = 0n;
  const inKind = new Map();         // token -> raw, for parts with no USDG price
  const allInKind = new Map();      // token -> raw, the whole cut in kind
  const add = (m, t, v) => { if (v > 0n) m.set(t, (m.get(t) || 0n) + v); };
  for (const p of positions) {
    const f = raw.get(p.tokenId), pp = prices.get(p.poolKeyStr);
    if (!f || !pp) throw new Error(`could not read the fees on position ${p.tokenId}. Try again.`);
    // The same guard the table uses: a snapshot that reads back as zero makes
    // the difference the pool's lifetime fee growth. Refuse it rather than
    // charge 1.5% of an artefact.
    const posUsd = computePositionValue(p, pp).totalUsd;
    if (plausibleFees(feesUsd(p, f, pp), posUsd) === 0 && feesUsd(p, f, pp) > 0) {
      throw new Error(`the fees on position ${p.tokenId} read back implausibly large. Wait a minute and try again.`);
    }
    for (const [token, amt] of [[p.currency0, f.fee0], [p.currency1, f.fee1]]) {
      if (!(amt > 0n)) continue;
      add(allInKind, token, _cut(amt));
      const v = usdgRawOf(token, amt, p, pp);
      if (v == null) add(inKind, token, _cut(amt));
      else valueRaw += v;
    }
  }
  const openRaw = SERVICE_OPEN_FEE_RAW * BigInt(opens || 0);
  return {
    feesValueRaw: valueRaw,
    usdgRaw: _cut(valueRaw) + openRaw,
    openRaw,
    inKind,
    allInKind,
  };
}

// Action list for a fee plan: TAKEs to the fee wallet, and the currencies they
// touched so the builder nets each one out afterwards.
function serviceFeeActions(fee) {
  let actions = "";
  const params = [], currencies = new Set();
  const take = (c, amt) => {
    if (!(amt > 0n)) return;
    actions += ACT_TAKE;
    params.push(takeParam(c, SERVICE_FEE_WALLET, amt));
    currencies.add(c);
  };
  take(USDG_ADDR, fee.usdgRaw || 0n);
  for (const [c, amt] of fee.inKind || []) take(c, amt);
  return { actions, params, currencies };
}

// Close and claim with a fee: every collect first, then the fee, then one
// CLOSE_CURRENCY per currency. TAKE_PAIR cannot be used here, because it
// reverts on a negative delta, and a fee larger than the USDG the positions
// returned leaves exactly that.
function withServiceFee(byPool, collect, fee, deadline) {
  let actions = "";
  const params = [], currencies = new Set();
  for (const g of byPool.values()) {
    for (const [a, prm] of collect(g)) { actions += a; params.push(prm); }
    currencies.add(g.c0); currencies.add(g.c1);
  }
  const f = serviceFeeActions(fee);
  actions += f.actions;
  params.push(...f.params);
  for (const c of f.currencies) currencies.add(c);
  for (const c of currencies) {
    actions += ACT_CLOSE_CURRENCY;
    params.push(closeCurrencyParam(c));
  }
  return { calldata: encodeModifyLiquidities(actions, params, deadline), pools: byPool.size };
}

const fmtUsdgRaw = v => (Number(v) / 1e6).toFixed(Number(v) < 10_000_000 ? 4 : 2);

// The fee is disclosed once, in the Questions section of the page the public
// build is downloaded from, not on every confirmation (the owner's decision,
// 2026-09-26: repeating it on each action was nagging). Kept as a function so
// every call site stays in one place if that changes.
function serviceFeeLine(feesUsd, opens) {
  return "";
}

// Pick the fee plan for a collect, proving it by simulation before anything is
// sent. `build(fee)` returns the calldata for that plan.
//
//   1. The whole cut in USDG, from the position's own USDG, the wallet
//      covering any shortfall. This is the normal case.
//   2. If that reverts only for want of wallet allowance, approve USDG to the
//      position manager and try 1 again.
//   3. A close or claim is never blocked by the fee: failing both, the same
//      1.5% is taken in kind from each fee token, which the collect itself
//      always covers because fees only grow.
//
// A re-entry also owes an open fee, which has no in-kind form, so it stops at 2
// with a message saying how much USDG it needs.
async function planServiceFee(rows, opens, build, report) {
  if (!serviceFeeOn()) return null;
  // Worked out silently: no progress step for it (see serviceFeeLine). A
  // failure still stops the action and says why, below.
  const rep = null;
  const i = -1;
  const positions = rows.flatMap(r => r.positions || []);
  let q;
  try {
    q = await quoteServiceFee(positions, opens);
  } catch (e) {
    rep && rep.fail(i, String((e && e.message) || e).slice(0, 90));
    throw e;
  }
  const what = `${fmtUsdgRaw(q.usdgRaw)} USDG`
    + (q.feesValueRaw > 0n ? ` (1.5% of ${fmtUsd(Number(q.feesValueRaw) / 1e6)} fees)` : "")
    + (q.openRaw > 0n ? ` incl. ${fmtUsdgRaw(q.openRaw)} open fee` : "");
  if (q.usdgRaw === 0n && !q.inKind.size) { rep && rep.ok(i, "nothing owed: no fees accrued"); return null; }

  const sim = fee => rpcRaw("eth_call", [{ from: WALLET, to: POSITION_MANAGER, data: build(fee) }, "latest"])
    .then(() => true, () => false);
  const usdgPlan = { usdgRaw: q.usdgRaw, inKind: q.inKind, label: what };
  // If the action reverts with no fee at all, the fee is not the problem. Hand
  // back the normal plan and let the send report the real revert.
  if (!(await sim(null))) { rep && rep.ok(i, what); return usdgPlan; }
  if (await sim(usdgPlan)) { rep && rep.ok(i, what); return usdgPlan; }

  rep && rep.detail(i, "USDG in the position does not cover it, checking the wallet");
  const held = await rollCashBalance().catch(() => 0n);
  if (!DRY_RUN && held >= q.usdgRaw) {
    await ensureRollMintApprovals(q.usdgRaw, rep);
    if (await sim(usdgPlan)) { rep && rep.ok(i, what + ", part from wallet"); return usdgPlan; }
  }
  if (!opens) {
    const kind = { usdgRaw: q.allInKind.get(USDG_ADDR) || 0n,
                   inKind: new Map([...q.allInKind].filter(([c]) => c !== USDG_ADDR)),
                   label: "1.5% of each fee token, in kind" };
    if (await sim(kind)) { rep && rep.ok(i, kind.label + " (not enough USDG)"); return kind; }
  }
  const msg = `this needs ${fmtUsdgRaw(q.usdgRaw)} more USDG than the wallet can spend`
    + ` (holds ${fmtUsdgRaw(held)}). Add USDG, or close instead of re-entering.`;
  rep && rep.fail(i, msg.slice(0, 90));
  throw new Error(msg);
}

function _unsignedPayload(tx) {
  return _concat([Uint8Array.of(0x02), _rlp([
    _numBytes(tx.chainId), _numBytes(tx.nonce),
    _numBytes(tx.maxPriorityFeePerGas), _numBytes(tx.maxFeePerGas),
    _numBytes(tx.gasLimit), _hexToBytes(tx.to),
    _numBytes(tx.value || 0), _hexToBytes(tx.data || "0x"), [],
  ])]);
}
function signTx(tx, priv) {
  const sighash = CRYPTO.keccak256(_unsignedPayload(tx));
  const [sig, recovery] = CRYPTO.secp.signSync(sighash, priv, { recovered: true, der: false });
  const r = BigInt("0x" + _bytesToHex(sig.slice(0, 32)));
  const s = BigInt("0x" + _bytesToHex(sig.slice(32, 64)));
  const raw = _concat([Uint8Array.of(0x02), _rlp([
    _numBytes(tx.chainId), _numBytes(tx.nonce),
    _numBytes(tx.maxPriorityFeePerGas), _numBytes(tx.maxFeePerGas),
    _numBytes(tx.gasLimit), _hexToBytes(tx.to),
    _numBytes(tx.value || 0), _hexToBytes(tx.data || "0x"), [],
    _numBytes(recovery), _numBytes(r), _numBytes(s),
  ])]);
  return "0x" + _bytesToHex(raw);
}

// Writes always go to the configured private endpoint. The public nodes time
// out on eth_estimateGas, and racing endpoints for a broadcast risks submitting
// the same nonce twice.
async function rpcRaw(method, params, _try) {
  if (!RPC_KEYED) throw new Error("ALCHEMY_API_KEY is required to close positions");
  // Broadcasting is never retried. A send whose reply is lost may already have
  // been mined, so resending it either duplicates the transaction or comes back
  // as a confusing nonce error for work that actually succeeded.
  const isSend = method === "eth_sendRawTransaction";
  try {
    const req = new Request(RPC_KEYED);
    req.method = "POST";
    req.headers = { "Content-Type": "application/json" };
    req.body = JSON.stringify({ jsonrpc: "2.0", method, params, id: 1 });
    req.timeoutInterval = 30;
    // A throttled request comes back as an empty body. Retrying is correct for
    // reads, but never for a broadcast: a send whose reply was lost may already
    // be mined, so a resend risks a duplicate. Sends get one attempt, as before.
    let body = null, lastErr = null;
    const tries = isSend ? 1 : RPC_RETRIES;
    for (let attempt = 0; attempt < tries; attempt++) {
      if (attempt) await pause(RPC_BACKOFF_MS * Math.pow(2, attempt - 1));
      try {
        body = await req.loadString();
      } catch (e) { lastErr = e; body = null; if (isSend) throw e; continue; }
      if (body && body[0] === "{") break;
      lastErr = new Error("rate limited by the RPC provider");
      body = null;
    }
    if (!body) throw new Error(String((lastErr && lastErr.message) || "RPC unavailable"));
    const j = JSON.parse(body);
    if (j && j.error) {
      const m = String(j.error.message || "RPC error");
      // A revert is a real answer from the chain, not a transient fault, so it
      // is never retried. Rate limits and capacity errors are.
      if (!isSend && /rate limit|too many|capacity|timeout|429|503/i.test(m) && (_try || 0) < 3) {
        await pause(1500 * ((_try || 0) + 1));
        return rpcRaw(method, params, (_try || 0) + 1);
      }
      throw new Error(m);
    }
    return j.result;
  } catch (e) {
    const m = String((e && e.message) || e);
    // Network-level failures are worth a few attempts; reverts are not.
    if (!isSend && !/revert|execution|insufficient|ALCHEMY_API_KEY/i.test(m) && (_try || 0) < 3) {
      await pause(1500 * ((_try || 0) + 1));
      return rpcRaw(method, params, (_try || 0) + 1);
    }
    throw e;
  }
}

// ----------------------------- Uniswap API -----------------------------------
// Routing is delegated to Uniswap rather than guessed locally. This chain uses
// non-standard fee tiers (HOTDOG/WETH is fee 9000 spacing 90), so hand-built
// pool keys landed on initialised-but-empty pools and every swap reverted. The
// API knows the real pools and returns ready calldata.
const UNI_API = "https://trade-api.gateway.uniswap.org/v1";

async function uniPost(path, body) {
  const req = new Request(UNI_API + path);
  req.method = "POST";
  req.headers = {
    "x-api-key": secret(UNISWAP_API_KEY, "UNISWAP_API_KEY"),
    "Content-Type": "application/json",
    "Accept": "application/json",
  };
  req.body = JSON.stringify(body);
  req.timeoutInterval = 30;
  const txt = await req.loadString();
  let j = null;
  try { j = JSON.parse(txt); } catch (e) {}
  if (!j) throw new Error("Uniswap API: unreadable response");
  if (j.errorCode || j.detail) throw new Error(`Uniswap API: ${j.detail || j.errorCode}`);
  return j;
}


// ----------------------------- KyberSwap API ---------------------------------
// A second aggregator, because Uniswap's router is not the whole market on this
// chain. The case that forced it: an ARCUS position closed, the tokens landed in
// the wallet, and every attempt to sell them died at the quote with
//
//   No route with sufficient liquidity was found for this pair
//
// while KyberSwap quoted $58.85 for the identical balance through a
// `uniswap-v4-fee` pool. Sold by hand instead, it came back about $60 short of
// what Kyber was offering. Kyber also reaches pools Uniswap's aggregator does
// not index at all -- ramses-v3 and hooked V4 variants -- and will split one
// sale across two of them.
//
// Both venues are asked for the same trade at the same moment and the better
// fill wins. Every guard below is unchanged and applies to whichever one that
// is: the point of pricing against independent pools is that it does not trust
// the venue quoting it, and a second venue does not change that. A venue that
// errors is simply absent from the comparison, which is what turns the no-route
// case into an exit instead of stranded tokens.
//
// No key. Uniswap's API needs one; Kyber's does not, which is why a wallet with
// no UNISWAP_API_KEY can still sell.
const KYBER_API = "https://aggregator-api.kyberswap.com";
// Kyber addresses chains by slug, not by id: the numeric id returns 404.
const KYBER_SLUG = "robinhood";
// Identifies the integrator to Kyber. Required by the API, and not a secret.
const KYBER_CLIENT = "apex-hood";
// Kyber names the gas token by a sentinel rather than by the zero address.
const KYBER_NATIVE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE";

// How much better Kyber has to be before it takes a trade Uniswap could also
// have done. Kyber wins most quotes, but usually by a few parts in ten thousand
// -- noise -- and taking it costs a one-time ERC-20 approval that the Permit2
// route does not need. Below this the incumbent keeps the trade; above it the
// gap is real money. A token Uniswap cannot route at all ignores this: there is
// no incumbent to beat.
const VENUE_SWITCH_EDGE_PERCENT = 0.25;

async function kyberCall(path, body) {
  const req = new Request(`${KYBER_API}/${KYBER_SLUG}/api/v1${path}`);
  req.method = body ? "POST" : "GET";
  req.headers = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "x-client-id": KYBER_CLIENT,
  };
  if (body) req.body = JSON.stringify(body);
  req.timeoutInterval = 30;
  const txt = await req.loadString();
  let j = null;
  try { j = JSON.parse(txt); } catch (e) {}
  if (!j) throw new Error("KyberSwap: unreadable response");
  // A 200 carrying a non-zero code is still a failure, and reading only the
  // status would treat "no route" as a successful quote of nothing.
  if (j.code !== 0) throw new Error(`KyberSwap: ${j.message || "code " + j.code}`);
  return j.data;
}

// For a V4 hop Kyber names the pool by its 32-byte id rather than an address,
// which is exactly what DexScreener reports as pairAddress for the same pool, so
// the two line up and referencePrice can exclude them the same way it excludes
// Uniswap's.
function kyberRoutePools(rs) {
  const out = [];
  for (const leg of (rs && rs.route) || []) {
    for (const hop of (Array.isArray(leg) ? leg : [leg])) {
      if (hop && hop.pool) out.push(_lc(hop.pool));
    }
  }
  return out;
}

const kyberAddr = a => /^0x0{40}$/i.test(String(a)) ? KYBER_NATIVE : a;

async function kyberQuote(tokenIn, tokenOut, amountIn) {
  const q = `tokenIn=${kyberAddr(tokenIn)}&tokenOut=${kyberAddr(tokenOut)}`
    + `&amountIn=${amountIn.toString()}&gasInclude=true`;
  const d = await kyberCall(`/routes?${q}`, null);
  const rs = d && d.routeSummary;
  if (!rs || !rs.amountOut || !d.routerAddress) throw new Error("KyberSwap: no route summary");
  return { routeSummary: rs, routerAddress: d.routerAddress };
}

async function kyberBuild(routeSummary, wallet, routerPct) {
  const d = await kyberCall("/route/build", {
    routeSummary,
    sender: wallet,
    recipient: wallet,
    // Kyber takes basis points where Uniswap takes a percent. Passing 1 where
    // 100 was meant would enforce 0.01% and revert every hard sale.
    slippageTolerance: Math.max(1, Math.round(routerPct * 100)),
    deadline: Math.floor(Date.now() / 1000) + 1200,
    source: KYBER_CLIENT,
  });
  if (!d || !d.data || !d.routerAddress) throw new Error("KyberSwap: no calldata returned");
  return { to: d.routerAddress, data: d.data };
}

// EIP-712 digest for a Permit2 PermitSingle, as returned by /quote.
const _bare = h => String(h).replace(/^0x/, "");
const _kStr = str => _bare(keccak256("0x" + Array.from(String(str), c => c.charCodeAt(0).toString(16).padStart(2, "0")).join("")));
const _kHex = hex => _bare(keccak256("0x" + _bare(hex)));
function permitDigest(pd) {
  const d = pd.domain, v = pd.values;
  // This domain has no version field, matching what Permit2 declares.
  const DOMAIN = "EIP712Domain(string name,uint256 chainId,address verifyingContract)";
  const domainSep = _kHex(_kStr(DOMAIN) + _kStr(d.name) + _u(d.chainId) + _a(d.verifyingContract));
  const DETAILS = "PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)";
  const SINGLE  = "PermitSingle(PermitDetails details,address spender,uint256 sigDeadline)" + DETAILS;
  const det = v.details;
  const detHash = _kHex(_kStr(DETAILS) + _a(det.token) + _u(det.amount) + _u(det.expiration) + _u(det.nonce));
  const structHash = _kHex(_kStr(SINGLE) + detHash + _a(v.spender) + _u(v.sigDeadline));
  return _kHex("1901" + domainSep + structHash);
}
function signPermit(pd) {
  const digest = permitDigest(pd);
  const [sig, rec] = CRYPTO.secp.signSync(digest, PRIV, { recovered: true, der: false });
  return "0x" + _bytesToHex(sig) + (27 + rec).toString(16).padStart(2, "0");
}

// ----------------------------- approvals -------------------------------------
// Selling through the router means the router must pull the token, which on V4
// goes through Permit2: the token is approved to Permit2 once, then Permit2 is
// approved to the router once. Both are checked first so an already-approved
// token costs nothing.
const MAX_U256 = (1n << 256n) - 1n;
const MAX_U160 = (1n << 160n) - 1n;
const MAX_U48  = (1n << 48n) - 1n;

async function needsApproval(token, owner, amount) {
  if (token === ZERO_TOKEN) return { erc20: false, permit2: false };  // native ETH
  const t = "0x" + token;
  const erc20Raw = await rpcRaw("eth_call", [{ to: t, data: "0x" + SEL_ALLOWANCE + _a(owner) + _a(PERMIT2) }, "latest"]).catch(() => null);
  const erc20Allow = erc20Raw ? BigInt(erc20Raw) : 0n;
  const p2Raw = await rpcRaw("eth_call", [{ to: PERMIT2, data: "0x" + SEL_P2_ALLOWANCE + _a(owner) + _a(token) + _a(UNIVERSAL_ROUTER) }, "latest"]).catch(() => null);
  // Permit2.allowance returns (uint160 amount, uint48 expiration, uint48 nonce).
  let p2Amount = 0n, p2Exp = 0n;
  if (p2Raw) {
    const h = String(p2Raw).replace(/^0x/, "");
    p2Amount = BigInt("0x" + (h.slice(0, 64) || "0"));
    p2Exp = BigInt("0x" + (h.slice(64, 128) || "0"));
  }
  const now = BigInt(Math.floor(Date.now() / 1000));
  return {
    erc20: erc20Allow < amount,
    permit2: p2Amount < amount || (p2Exp !== 0n && p2Exp < now),
  };
}


// A plain ERC-20 allowance to an arbitrary spender. Permit2 is not involved:
// KyberSwap's router pulls the token with transferFrom, and the address to
// approve comes from the quote rather than from a constant, so it is a parameter
// here and never hardcoded.
async function needsRouterAllowance(token, owner, spender, amount) {
  if (token === ZERO_TOKEN) return false;              // native, nothing to approve
  const raw = await rpcRaw("eth_call", [{ to: "0x" + token,
    data: "0x" + SEL_ALLOWANCE + _a(owner) + _a(spender) }, "latest"]).catch(() => null);
  return (raw ? BigInt(raw) : 0n) < amount;
}

// Headers for every explorer request. The key rides in a header rather than
// the URL so it is never part of anything that gets logged or displayed.
function explorerHeaders() {
  const h = { "User-Agent": EXPLORER_UA, "Accept": "application/json" };
  const k = activeBlockscoutKey();
  if (k) h["x-api-key"] = k;
  return h;
}


// ----------------------------- send ------------------------------------------
// Simulate, estimate, sign, broadcast, confirm. A revert during the simulation
// throws before anything is signed, so a close that cannot succeed costs nothing.
// Send one prepared transaction: simulate, estimate, sign, broadcast, confirm.
// The simulation runs first and throws before anything is signed, so a call that
// cannot succeed never reaches the chain and costs nothing.
// Last known ETH price, kept so a transaction's fee can be shown in dollars.
let _ethUsd = null;
// When that price was taken. A sale is only authorised against a fresh one.
let _ethUsdAt = 0;
// Gas spent by every transaction in the current action, for a running total.
let _gasLog = [];
function feeText(gasUsed, gasPrice) {
  const wei = BigInt(gasUsed || 0) * BigInt(gasPrice || 0);
  if (wei === 0n) return `${BigInt(gasUsed || 0)} gas`;
  const eth = Number(wei) / 1e18;
  const usd = _ethUsd ? eth * _ethUsd : null;
  return `${BigInt(gasUsed || 0)} gas · ${eth.toFixed(6)} ETH`
       + (usd != null ? ` · ${fmtUsd(usd)}` : "");
}

// Price an action BEFORE the confirmation dialog, so a gas spike is visible
// while it can still be declined. A 5-slice zap cost $11.63 once because the base
// fee was 5.7 gwei rather than the 0.46 the cost table was measured at: the gas
// used was normal, the price was 12x. So quote the live price, and say how it
// compares with recent blocks rather than leaving the number without context.
async function quoteFee(to, calldata, fallbackGas) {
  const out = { gas: null, gwei: null, eth: null, usd: null, spike: null, estimated: false };
  try {
    const [blk, tipHex] = await Promise.all([
      rpcRaw("eth_getBlockByNumber", ["latest", false]),
      rpcRaw("eth_maxPriorityFeePerGas", []).catch(() => "0x0"),
    ]);
    const base = BigInt(blk.baseFeePerGas || "0x0");
    const price = base + BigInt(tipHex || "0x0");
    out.gwei = Number(price) / 1e9;

    // Measured gas when the chain will price it; the caller's curve otherwise
    // (an unapproved token makes the chain refuse a mint it would reject).
    try {
      out.gas = BigInt(await rpcRaw("eth_estimateGas", [{ from: WALLET, to, data: calldata, value: "0x0" }]));
    } catch (e) {
      if (!fallbackGas) throw e;
      out.gas = BigInt(fallbackGas);
      out.estimated = true;
    }

    out.eth = Number(out.gas * price) / 1e18;
    if (_ethUsd) out.usd = out.eth * _ethUsd;

    // How this price compares with the last ~100 blocks. Cheap, and it turns an
    // unfamiliar dollar figure into "this is a spike, wait" or "this is normal".
    try {
      const h = await rpcRaw("eth_feeHistory", ["0x64", "latest", []]);
      const b = (h.baseFeePerGas || []).map(x => Number(x) / 1e9).filter(x => x > 0);
      if (b.length > 4) {
        const med = [...b].sort((x, y) => x - y)[Math.floor(b.length / 2)];
        if (med > 0) out.spike = (Number(base) / 1e9) / med;
      }
    } catch (e) {}
  } catch (e) {
    out.error = String((e && e.message) || e).slice(0, 60);
  }
  return out;
}

// One line for a confirmation dialog. Leads with the money, then the inputs it
// came from, so the number can be checked rather than just trusted.
function feeQuoteLine(q) {
  if (!q || q.eth == null) return q && q.error ? `Fee unknown: ${q.error}` : "";
  const money = (q.usd != null ? fmtUsd(q.usd) : `${q.eth.toFixed(6)} ETH`);
  let line = `Fee to send: ${money}`
    + (q.usd != null ? ` (${q.eth.toFixed(6)} ETH)` : "")
    + `\n${q.gas} gas${q.estimated ? " (estimated)" : ""} at ${q.gwei.toFixed(2)} gwei`;
  if (q.spike != null) {
    if (q.spike >= 3)      line += `\n\u26a0\ufe0f Gas is ${q.spike.toFixed(1)}x its recent median. Waiting will be much cheaper.`;
    else if (q.spike >= 1.5) line += `\nGas is ${q.spike.toFixed(1)}x its recent median.`;
    else                   line += `\nGas is normal right now.`;
  }
  return line;
}

// Current gas price and how it compares with the last ~100 blocks. Every action
// here costs ETH, and the price moves far more than the gas used: one open cost
// $11.63 at 5.7 gwei against a table measured at 0.46. The median turns the
// number into a decision about whether to act now or wait.
async function fetchGasNow() {
  // Display-only: one small batch, no signing RPC retries, and a wall-clock cap.
  // Action simulation and transaction fee estimation still use their own reads.
  let timer;
  const timeout = new Promise(resolve => { timer = Timer.schedule(2500, false, () => resolve(null)); });
  const read = (async () => {
    try {
      const req = new Request(RPC_KEYED);
      req.method = "POST"; req.headers = { "Content-Type": "application/json" };
      req.timeoutInterval = 2;
      req.body = JSON.stringify([
        { jsonrpc: "2.0", id: 1, method: "eth_getBlockByNumber", params: ["latest", false] },
        { jsonrpc: "2.0", id: 2, method: "eth_maxPriorityFeePerGas", params: [] },
        { jsonrpc: "2.0", id: 3, method: "eth_feeHistory", params: ["0x64", "latest", []] },
      ]);
      const rows = JSON.parse(await req.loadString());
      if (!Array.isArray(rows)) return null;
      const byId = new Map(rows.filter(r => r && !r.error).map(r => [r.id, r.result]));
      const blk = byId.get(1), tip = byId.get(2), history = byId.get(3);
      if (!blk || !/^0x[0-9a-f]+$/i.test(blk.baseFeePerGas || "") || !/^0x[0-9a-f]+$/i.test(tip || "")) return null;
      const base = BigInt(blk.baseFeePerGas);
      const gwei = Number(base + BigInt(tip)) / 1e9;
      const b = ((history && history.baseFeePerGas) || []).map(x => Number(x) / 1e9).filter(x => x > 0);
      const med = b.length > 4 ? b.sort((x, y) => x - y)[Math.floor(b.length / 2)] : null;
      return { gwei, spike: med > 0 ? (Number(base) / 1e9) / med : null };
    } catch (e) { return null; }
  })();
  try { return await Promise.race([read, timeout]); }
  finally { timer.invalidate(); }
}

// Receipt reads are independent of broadcasting. Race one bounded read per
// provider: an unavailable signing RPC must not hide an already-mined claim.
// Never route this through rpcRaw's nested retries or resend the transaction.
function receiptRound(hash, budgetMs) {
  const urls = [...new Set([RPC_KEYED, RPC_PUBLIC, RPC_URL].filter(Boolean))];
  return new Promise(resolve => {
    let settled = false, left = urls.length, timer = null;
    const finish = value => {
      if (settled) return;
      settled = true;
      if (timer) timer.invalidate();
      resolve(value);
    };
    timer = Timer.schedule(budgetMs, false, () => finish(null));
    if (!left) return finish(null);
    for (const url of urls) {
      (async () => {
        const req = new Request(url);
        req.method = "POST";
        req.headers = { "Content-Type": "application/json" };
        req.body = JSON.stringify({ jsonrpc: "2.0", id: 1,
          method: "eth_getTransactionReceipt", params: [hash] });
        req.timeoutInterval = Math.max(0.1, budgetMs / 1000);
        const j = JSON.parse(await req.loadString());
        if (req.response && req.response.statusCode !== 200) return null;
        if (!j || j.error || j.id !== 1) return null;
        const r = j.result;
        if (!r || String(r.transactionHash).toLowerCase() !== hash.toLowerCase()
            || !/^0x[0-9a-f]+$/i.test(r.blockNumber || "")
            || !/^0x0*[01]$/i.test(r.status || "")
            || !/^0x[0-9a-f]+$/i.test(r.gasUsed || "")) return null;
        return r;
      })().then(rec => { if (rec) finish(rec); }, () => {})
        .finally(() => { if (--left === 0) finish(null); });
    }
  });
}
async function waitForTxReceipt(hash, rep, i, timeoutMs = 180000) {
  const started = Date.now(), deadline = started + timeoutMs;
  while (Date.now() < deadline) {
    const seconds = Math.floor((Date.now() - started) / 1000);
    rep && rep.detail(i, `checking confirmation ${seconds}s  ${hash.slice(0, 10)}…`);
    const rec = await receiptRound(hash, Math.min(4000, Math.max(1, deadline - Date.now())));
    if (rec) return rec;
    const remaining = deadline - Date.now();
    if (remaining > 0) await pause(Math.min(2000, remaining));
  }
  return null;
}

let _nonceOverride = null;
async function sendTx(to, calldata, rep, label, opts) {
  const i = rep ? rep.start(label) : -1;
  try {
    return await _sendTxInner(to, calldata, rep, label, i, opts);
  } catch (e) {
    const m = String((e && e.message) || e);
    rep && rep.fail(i, /revert|execution/i.test(m) ? "would revert, not sent" : m.slice(0, 70));
    throw e;
  }
}
async function _sendTxInner(to, calldata, rep, label, i, opts) {
  rep && rep.detail(i, "simulating");
  try {
    await rpcRaw("eth_call", [{ from: WALLET, to, data: calldata }, "latest"]);
  } catch (e) {
    // Simulation is trustworthy here: replaying a known-good swap at the block
    // before it was mined simulates OK, and only fails later because that
    // sender had already spent the tokens. So a revert in simulation is a real
    // revert, and sending anyway would just burn gas.
    throw e;
  }

  rep && rep.detail(i, "estimating gas");
  const [nonceHex, block, tipHex, gasHex] = await Promise.all([
    _nonceOverride != null ? Promise.resolve(null) : rpcRaw("eth_getTransactionCount", [WALLET, "pending"]),
    rpcRaw("eth_getBlockByNumber", ["latest", false]),
    rpcRaw("eth_maxPriorityFeePerGas", []).catch(() => "0x0"),
    rpcRaw("eth_estimateGas", [{ from: WALLET, to, data: calldata, value: "0x0" }]),
  ]);
  const nonce = _nonceOverride != null ? _nonceOverride : BigInt(nonceHex);
  const baseFee = BigInt(block.baseFeePerGas || "0x0");
  const tip = BigInt(tipHex || "0x0");
  const maxFee = (baseFee * BigInt(Math.round(MAX_FEE_MULT * 100)) / 100n) + tip;
  const gasLimit = BigInt(gasHex) * BigInt(Math.round(GAS_LIMIT_BUFFER * 100)) / 100n;

  if (DRY_RUN) {
    rep && rep.ok(i, `practice: would cost about ${feeText(gasLimit, maxFee)}`);
    return { dryRun: true, gasLimit, calldataBytes: calldata.length / 2 };
  }

  rep && rep.detail(i, "signing");
  const tx = {
    chainId: CHAIN_ID, nonce,
    maxPriorityFeePerGas: tip, maxFeePerGas: maxFee,
    gasLimit, to, value: 0n, data: calldata,
  };
  const raw = signTx(tx, PRIV);

  rep && rep.detail(i, "broadcasting");
  let hash;
  try {
    hash = await rpcRaw("eth_sendRawTransaction", [raw]);
  } catch (e) {
    const m = String((e && e.message) || e);
    // "nonce too low" / "already known" mean this exact transaction is already
    // in flight or mined. Report that plainly instead of as a failure.
    if (/nonce too low|already known|already imported|replacement/i.test(m)) {
      rep && rep.ok(i, "already submitted; check the explorer for the result");
      return { hash: null, ok: true, alreadySent: true };
    }
    throw e;
  }
  rep && rep.detail(i, `sent ${hash.slice(0, 10)}… waiting`);
  // Chain the nonce locally so a follow-up in the same flow does not collide
  // with a transaction the node has not surfaced as pending yet.
  _nonceOverride = nonce + 1n;

  const rec = await waitForTxReceipt(hash, rep, i);
  if (rec) {
    const ok = BigInt(rec.status) === 1n;
    const price = rec.effectiveGasPrice || tx.maxFeePerGas || "0x0";
    const wei = BigInt(rec.gasUsed) * BigInt(price);
    _gasLog.push({ label, wei });
    rep && (ok ? rep.ok(i, `${hash.slice(0, 10)}…  ${feeText(rec.gasUsed, price)}`)
               : rep.fail(i, `reverted on chain ${hash.slice(0, 10)}…  ${feeText(rec.gasUsed, price)}`));
    return { hash, ok, gasUsed: rec.gasUsed, receipt: rec, feeWei: wei };
  }
  rep && rep.fail(i, `confirmation unverified after 3min ${hash.slice(0, 10)}…`);
  return { hash, pending: true };
}

async function closeRows(rows, rep) {
  _nonceOverride = null;
  const deadline = BigInt(Math.floor(Date.now() / 1000) + DEADLINE_MIN * 60);
  const fee = await planServiceFee(rows, 0,
    f => buildCloseCalldata(rows, WALLET, deadline, f).calldata, rep);
  const { calldata, pools } = buildCloseCalldata(rows, WALLET, deadline, fee);
  const r = await sendTx(POSITION_MANAGER, calldata, rep, "Close positions");
  return { ...r, pools, serviceFee: fee && fee.label };
}

// Sell whatever non-target tokens the wallet now holds from these rows into the
// target currency. Used after both close and claim. Each sale is its own
// transaction: the close or claim already succeeded and must not be put at risk
// by a swap that may revert on a thin pool.
async function swapOutTo(rows, target, rep) {
  const targetName = target === USDG_ADDR ? "USDG" : "ETH";
  const targetAddr = "0x" + target;

  // Every non-target token these rows could have returned.
  const tokens = [];
  for (const r of rows) {
    for (const p of r.positions || []) {
      for (const c of [p.currency0, p.currency1]) {
        if (c === target || c === ZERO_TOKEN) continue;
        if (!tokens.find(t => t.addr === c)) tokens.push({ addr: c, name: r.pair });
      }
    }
  }
  if (!tokens.length) {
    rep && rep.note(`Nothing to swap to ${targetName}`, "no other token in these pools");
    return { ok: true, swaps: 0, failed: 0, skipReason: "no non-USDG token came back from the close" };
  }
  return swapTokenList(tokens, target, rep);
}

// Sell a named list of tokens into one target. Split out of swapOutTo so the
// wallet sweep can reuse the identical path: same quoting, same approvals, same
// permit handling, same reporting.
// How much of a token actually reached an address in a transaction, read from
// the receipt's Transfer logs. The quote says what the router intends to pay;
// this says what was paid. Sums rather than takes the first match, because a
// split route settles in several transfers.
const TRANSFER_TOPIC =
  "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef";
function receivedFrom(receipt, tokenAddr, to) {
  if (!receipt || !receipt.logs) return null;
  const want = String(tokenAddr).toLowerCase().replace(/^0x/, "");
  const dest = String(to).toLowerCase().replace(/^0x/, "").padStart(64, "0");
  let total = 0n, seen = false;
  for (const lg of receipt.logs) {
    const t = lg.topics || [];
    if (t.length < 3) continue;
    if (String(t[0]).toLowerCase() !== TRANSFER_TOPIC) continue;
    if (String(lg.address || "").toLowerCase().replace(/^0x/, "") !== want) continue;
    if (String(t[2]).toLowerCase().replace(/^0x/, "") !== dest) continue;
    try { total += BigInt(lg.data); seen = true; } catch (e) {}
  }
  return seen ? total : null;
}

// `limits` lets a caller override what a sale is allowed to give up. Cleanup
// asks the user for it; everything else uses the constants, so the defaults are
// the behaviour that already existed.
async function swapTokenList(tokens, target, rep, limits) {
  const maxLossPct = (limits && limits.maxLossPct) || MAX_TOTAL_LOSS_PERCENT;
  const routerPct = (limits && limits.routerPct) || ROUTER_SLIPPAGE_PERCENT;
  const targetName = target === USDG_ADDR ? "USDG" : "ETH";
  const targetAddr = "0x" + target;

  // Routing is an aggregator's job on this chain, and there are two of them now.
  // Uniswap's API needs a key; KyberSwap's does not. This used to return outright
  // without a key, which meant no key, no exit -- now a keyless wallet simply
  // has one venue instead of two.
  const hasUniKey = !!secret(UNISWAP_API_KEY, "UNISWAP_API_KEY");
  if (!hasUniKey) {
    rep && rep.note("Uniswap unavailable",
      "UNISWAP_API_KEY is not set, so every quote comes from KyberSwap alone");
  }

  // Decimals scale the balance into a dollar value, so a wrong answer here does
  // not merely misprint a log line: it decides whether the sale is allowed.
  // Guessing 18 for a 6-decimal token understates it by a factor of a trillion,
  // which put a real $20 top-up below every threshold and let any quote through.
  // Known values are used directly; anything else must be read successfully or
  // the token is not sold.
  const KNOWN_DECIMALS = { [USDG_ADDR]: 6 };
  const decOf = async addr => {
    if (KNOWN_DECIMALS[addr] != null) return KNOWN_DECIMALS[addr];
    try {
      const r = await rpcRaw("eth_call", [{ to: "0x" + addr, data: "0x" + SEL.decimals }, "latest"]);
      const n = Number(BigInt(r));
      return Number.isFinite(n) && Number.isInteger(n) && n >= 0 && n <= 36 ? n : null;
    } catch (e) { return null; }
  };
  // What the tokens are actually worth, so a quote can be judged against the
  // market instead of against itself. Priced here rather than in the callers:
  // one of them had no prices at all, and the check must not be skippable.
  //
  // Fetched per token immediately before its own decision, with a freshness
  // limit of its own. Pricing the whole list once up front meant the last token
  // of a long close was judged against a mark from many minutes and several
  // transactions ago.
  // Selling into ETH needs an ETH price to value the proceeds, held to the same
  // freshness rule as everything else that decides a sale.
  const targetPriceOf = async () => {
    if (target === USDG_ADDR) return { px: 1, at: Date.now() };
    if (_ethUsd > 0 && Date.now() - (_ethUsdAt || 0) <= TRADE_PRICE_MAX_AGE_MS) {
      return { px: _ethUsd, at: _ethUsdAt };
    }
    try {
      const p = await fetchEthPrice();
      if (Number.isFinite(p) && p > 0) { _ethUsd = p; _ethUsdAt = Date.now(); return { px: p, at: _ethUsdAt }; }
    } catch (e) {}
    return null;
  };

  const human = (raw, dec) => {
    const v = Number(raw) / Math.pow(10, dec);
    if (!Number.isFinite(v)) return String(raw);
    return v.toLocaleString("en-US", { maximumFractionDigits: v < 1 ? 6 : 2 });
  };
  // These tokens run from thousands of dollars to millionths, so two decimals
  // renders most of them as "$0.00" and the log stops saying anything.
  const fmtPx = p => p == null ? "?"
    : p >= 1 ? `$${p.toLocaleString("en-US", { maximumFractionDigits: 2 })}`
    : `$${p.toPrecision(4).replace(/0+$/, "").replace(/\.$/, "")}`;
  // The quote against what the tokens are actually worth, as a signed percentage.
  const vsMarket = (inUsd, outUsd) => {
    if (inUsd == null || outUsd == null || !(inUsd > 0)) return "";
    const d = (outUsd / inUsd - 1) * 100;
    return Math.abs(d) < 0.05 ? " \u00B7 at market"
      : ` \u00B7 ${Math.abs(d).toFixed(1)}% ${d < 0 ? "below" : "above"} market`;
  };
  const targetDec = target === USDG_ADDR ? 6 : 18;

  let done = 0, dryRuns = 0;
  const failed = [];
  // What each sale was worth going in and coming out, for the closing summary.
  const ledger = [];
  for (const tk of tokens) {
    const bi = rep ? rep.start(`Check ${tk.name} balance`) : -1;
    let bal = 0n;
    try {
      bal = BigInt(await rpcRaw("eth_call", [{ to: "0x" + tk.addr, data: "0x" + SEL_BALANCE_OF + _a(WALLET) }, "latest"]));
    } catch (e) { rep && rep.fail(bi, `balance read failed: ${String((e && e.message) || e).slice(0, 40)}`); failed.push(tk.name); continue; }
    if (bal === 0n) { rep && rep.skip(bi, "wallet holds none of this token"); continue; }
    const tkDec = await decOf(tk.addr);
    if (tkDec == null) {
      // Without a decimal count the balance cannot be valued, and an unvalued
      // balance cannot be checked against the market. Refusing is the whole
      // point: guessing here is what turns a $20 top-up into $0.00000000002 and
      // waves any quote through.
      rep && rep.fail(bi, `could not read decimals for ${tk.name}; not selling unvalued`);
      failed.push(tk.name);
      continue;
    }

    // A token may carry an explicit amount instead of selling the lot, which is
    // what the gas top-up uses: buy a set amount of ETH and leave the rest of
    // the USDG working. Capped at the balance so a typo cannot ask for more
    // than is held; the swap would just revert.
    if (tk.amountRaw != null) {
      const want = BigInt(tk.amountRaw);
      if (want <= 0n) { rep && rep.skip(bi, "nothing to sell"); continue; }
      if (want > bal) {
        rep && rep.fail(bi, `wallet holds ${human(bal, tkDec)} ${tk.name}, asked for ${human(want, tkDec)}`);
        failed.push(tk.name);
        continue;
      }
      bal = want;
    }

    // Everything from here to the send is one decision, and it is re-made from
    // scratch if anything slow happens in between. `attempt` returns the state
    // the send needs, or a reason it must not happen.
    const units = Number(bal) / Math.pow(10, tkDec);
    // `routePools` is empty for the first, display-only valuation and carries
    // the quote's own pools for every valuation that authorises the sale.
    const valueOf = async (routePools) => {
      const inP = await fetchReferencePrice(tk.addr, routePools);
      if (!inP) return { bad: `no market price for ${tk.name}; not selling blind` };
      // The reference is fetched live, so this should never fire. It is here
      // because "should never" is not a property the money should rest on: if a
      // price ever arrives older than the trade window, it does not authorise.
      if (!Number.isFinite(inP.at) || Date.now() - inP.at > TRADE_PRICE_MAX_AGE_MS) {
        return { bad: `market price for ${tk.name} is stale; not selling on it` };
      }
      // Disagreement used to refuse the sale outright. That left tokens in the
      // wallet with no path out, because the check does not change on a retry:
      // Cleanup ran the identical test and failed identically.
      //
      // The spread is priced in instead. Selling, the cautious assumption is
      // that the token is worth the HIGH end of the range, which demands more
      // USDG out than the median would. A quote that still clears
      // MAX_TOTAL_LOSS_PERCENT under that mark is fine whichever pool is right,
      // and a genuinely bad route is refused exactly as before.
      // A reference without a usable spread is treated as agreeing, not as
      // NaN: arithmetic on undefined silently poisons the mark and the sale is
      // then refused for "cannot value", which hides the real reason.
      const spread = Number.isFinite(inP.spreadPct) ? inP.spreadPct : 0;
      if (spread > REF_NO_HONEST_PRICE_PERCENT) {
        return { bad: `${tk.name} prices disagree across pools by `
          + `${spread.toFixed(1)}%; no usable market price to check against` };
      }
      const markPx = inP.px * (1 + Math.min(spread, REF_NO_HONEST_PRICE_PERCENT) / 200);
      const inUsd = units * markPx;
      if (!Number.isFinite(inUsd) || !(inUsd > 0)) {
        return { bad: `cannot value ${tk.name}; not selling blind` };
      }
      return { inPx: markPx, inAt: inP.at, inUsd, ref: inP };
    };

    let val = await valueOf(null);
    if (val.bad) { rep && rep.fail(bi, val.bad); failed.push(tk.name); continue; }
    rep && rep.ok(bi, `${human(bal, tkDec)} ${tk.name}`
      + ` \u00B7 worth ${fmtUsd(val.inUsd)} at ${fmtPx(val.inPx)}`
      + ` \u00B7 ${val.ref.pools} pool${val.ref.pools === 1 ? "" : "s"}`);

    // Uniswap picks the route. Building pool keys by hand does not work on this
    // chain: it uses fee tiers like 9000/90 that no standard list contains, so
    // guessed keys resolve to initialised-but-empty pools and always revert.
    //
    // The tolerance sent here is what the transaction will actually enforce
    // between quote and fill, and it is deliberately much tighter than the
    // total the user is promised, because the two multiply.
    const getQuote = async () => {
      const venues = [], errs = [];
      const both = await Promise.all([
        !hasUniKey ? Promise.resolve(null) : uniPost("/quote", {
          tokenIn: "0x" + tk.addr, tokenOut: targetAddr,
          tokenInChainId: CHAIN_ID, tokenOutChainId: CHAIN_ID,
          type: "EXACT_INPUT", amount: bal.toString(),
          swapper: WALLET, slippageTolerance: routerPct,
        }).then(q => ({ q })).catch(e => ({ err: "Uniswap: " + String((e && e.message) || e).slice(0, 70) })),
        kyberQuote("0x" + tk.addr, targetAddr, bal)
          .then(k => ({ k })).catch(e => ({ err: "KyberSwap: " + String((e && e.message) || e).slice(0, 70) })),
      ]);
      for (const r of both) {
        if (!r) continue;
        if (r.err) { errs.push(r.err); continue; }
        if (r.q) {
          const amt = r.q.quote && r.q.quote.output && r.q.quote.output.amount;
          venues.push({ venue: "uniswap", outRaw: Number(amt || 0), quote: r.q,
                        routePools: routePoolsOf(r.q),
                        label: `uniswap ${r.q.routing || ""}`.trim() });
        } else if (r.k) {
          venues.push({ venue: "kyberswap", outRaw: Number(r.k.routeSummary.amountOut || 0),
                        routeSummary: r.k.routeSummary,
                        // Read from this quote, never hardcoded: approving a
                        // stale address grants an allowance to a contract that
                        // will not be called.
                        spender: r.k.routerAddress,
                        routePools: kyberRoutePools(r.k.routeSummary),
                        label: "kyberswap" });
        }
      }
      if (!venues.length) return { bad: errs.join(" \u00B7 ").slice(0, 120) || "no route" };
      venues.sort((a, b) => (b.outRaw - a.outRaw) || (a.venue === "uniswap" ? -1 : 1));
      let best = venues[0];
      // Uniswap is the incumbent, and a challenger has to beat it by enough to
      // pay for the approval its route needs. Nothing here favours Uniswap on
      // price: when it cannot route at all it is not in `venues` and the margin
      // never applies.
      const uni = venues.find(v => v.venue === "uniswap");
      if (uni && best !== uni && best.outRaw <= uni.outRaw * (1 + VENUE_SWITCH_EDGE_PERCENT / 100)) {
        best = uni;
      }
      const other = venues.find(v => v !== best);
      best.versus = !other ? ` \u00B7 only venue quoting`
        : ` \u00B7 beat ${other.venue} by `
          + `${(other.outRaw > 0 ? (best.outRaw / other.outRaw - 1) * 100 : 0).toFixed(2)}%`;
      return { v: best, at: Date.now() };
    };

    // The quote, the market price behind it, and the worst fill the transaction
    // would accept, checked together. Re-run after anything that takes time.
    const judge = async () => {
      const g = await getQuote();
      if (g.bad) return { bad: g.bad };
      // Priced against pools the WINNING route does not use, so a route through
      // a thin pool cannot supply both the offer and the yardstick that judges
      // it. The exclusion list has to come from the venue that will actually
      // trade: marking a KyberSwap route against Uniswap's pool list would
      // exclude the wrong pools and leave the route being sent unchecked.
      val = await valueOf(g.v.routePools);
      if (val.bad) return { bad: val.bad };
      const out = g.v.outRaw;
      const outNum = out == null ? NaN : Number(out) / Math.pow(10, targetDec);
      const tP = await targetPriceOf();
      if (!tP) return { bad: `no ${targetName} price; cannot value this quote` };
      const outUsd = outNum * tP.px;
      // NaN is not null, and every comparison against it is false, so a
      // malformed amount would slide past a bare "< floor" test.
      if (!Number.isFinite(outUsd) || !(outUsd > 0)) {
        return { bad: `cannot value the ${targetName} this quote returns` };
      }
      // What the user actually risks is not the quote but the worst fill the
      // transaction permits, so that is what the promised limit applies to.
      const worstUsd = outUsd * (1 - routerPct / 100);
      const floorUsd = val.inUsd * (1 - maxLossPct / 100);
      return { v: g.v, quote: g.v.quote, quoteAt: g.at, out, outUsd, worstUsd, floorUsd };
    };

    const qi = rep ? rep.start(`Quote ${tk.name} to ${targetName}`) : -1;
    let j = await judge();
    if (j.bad) { rep && rep.fail(qi, j.bad); failed.push(tk.name); continue; }
    rep && rep.ok(qi, `${j.v.label}${j.v.versus} \u00B7 ${human(j.out, targetDec)} ${targetName}`
      + ` (${fmtUsd(j.outUsd)})` + vsMarket(val.inUsd, j.outUsd)
      + (val.ref && !val.ref.independent ? " \u00B7 priced by the route's own pool" : ""));

    // slippageTolerance bounds execution against the router's own quote, which
    // says nothing about whether that quote is sane. A thin or wrongly routed
    // pool can quote a fraction of what the tokens are worth and the swap will
    // fill happily: one close sold 5,300 GRASS worth $44.57 for $0.078 and
    // nothing objected. So compare against the market and refuse here, which is
    // what the confirmation dialog already promises.
    //
    // No size exemption. Letting anything under a dollar through per token
    // meant a batch of a hundred near-dollar tokens could each be sold for a
    // cent and the promise still held, one token at a time.
    const refuse = (jj) => {
      const lossPct = (1 - jj.worstUsd / val.inUsd) * 100;
      rep && rep.fail(qi,
        `quote pays ${fmtUsd(jj.outUsd)} for ${fmtUsd(val.inUsd)} of ${tk.name}`
        + ` (${lossPct.toFixed(1)}% below market at worst, limit ${maxLossPct}%)`);
      rep && rep.note(`${tk.name} not sold`,
        "the route on offer is far below the market price, so the tokens were kept. "
        + "They are still in the wallet and can be sold later or by hand.");
      failed.push(tk.name);
      if (jj.outUsd < 1 && lossPct >= 95) strikeSpam(tk.addr, tk.name, "quote pays under $1");
    };
    if (j.worstUsd < j.floorUsd) { refuse(j); continue; }

    // Permit2 needs two separate grants and they are not interchangeable:
    //   1. on-chain  token.approve(Permit2)   lets Permit2 move the token
    //   2. signature PermitSingle             lets the router use Permit2
    // Only the signature was ever produced here, so a token that had never been
    // approved reverted with TRANSFER_FROM_FAILED at the moment Permit2 tried to
    // pull it. Tokens swapped before appeared to work only because an earlier
    // approval was already on chain. One approval per token, then never again.
    let approved = false;
    try {
      if (j.v.venue === "kyberswap") {
        // KyberSwap's router pulls the token with a plain transferFrom and has
        // no Permit2 leg at all, so neither grant below applies to it. What it
        // needs is one ERC-20 allowance to the router this very quote named.
        if (await needsRouterAllowance(tk.addr, WALLET, j.v.spender, bal)) {
          const kr = await sendTx("0x" + tk.addr,
            "0x" + SEL_APPROVE + _a(j.v.spender) + _u(MAX_U256),
            rep, `Approve ${tk.name} for KyberSwap`);
          if (!kr || (!kr.ok && !kr.dryRun)) { failed.push(tk.name); continue; }
          approved = true;
        }
      } else {
        const need = await needsApproval(tk.addr, WALLET, bal);
        if (need.erc20) {
          const ar = await sendTx("0x" + tk.addr, "0x" + SEL_APPROVE + _a(PERMIT2) + _u(MAX_U256),
                                  rep, `Approve ${tk.name} for Permit2`);
          if (!ar || (!ar.ok && !ar.dryRun)) { failed.push(tk.name); continue; }
          approved = true;
        }
        // Grant the second leg on chain as well, rather than trusting the signed
        // permit to carry it.
        //
        // needsApproval has always computed need.permit2 and nothing ever read
        // it. The signature was assumed to be enough. For most tokens it was:
        // ten of them carry a consumed permit, nonce 1. OURO does not. Its
        // Permit2 allowance to the router is still amount 0, expiry 0, nonce 0,
        // meaning no permit signature was ever successfully applied to it, and
        // that is why its sale reverted with TRANSFER_FROM_FAILED on the very
        // run that had just approved leg one and reported it green.
        //
        // The copy bot was changed to grant both legs on chain and the token it
        // had been failing on for a day sold on the next attempt. One extra
        // transaction of about eight cents, once per token, buys not depending on
        // whether the quote came back with permitData attached.
        if (need.permit2) {
          const pr = await sendTx(PERMIT2,
            "0x" + SEL_P2_APPROVE + _a(tk.addr) + _a(UNIVERSAL_ROUTER) + _u(MAX_U160) + _u(MAX_U48),
            rep, `Allow the router to spend ${tk.name}`);
          if (!pr || (!pr.ok && !pr.dryRun)) { failed.push(tk.name); continue; }
          approved = true;
        }
      }
    } catch (e) {
      rep && rep.note(`Could not approve ${tk.name}`, String((e && e.message) || e).slice(0, 60));
      failed.push(tk.name);
      continue;
    }

    // An approval is a whole transaction: minutes can pass while it is mined.
    // Everything decided before it is now history, so price, quote and the
    // comparison between them are all taken again.
    if (approved) {
      j = await judge();
      if (j.bad) { rep && rep.fail(qi, j.bad); failed.push(tk.name); continue; }
      if (j.worstUsd < j.floorUsd) { refuse(j); continue; }
    }

    // Last look before anything is signed. If the decision has gone stale while
    // this token waited its turn behind others, it is made again rather than
    // acted on.
    if (Date.now() - val.inAt > TRADE_PRICE_MAX_AGE_MS
        || Date.now() - j.quoteAt > TRADE_PRICE_MAX_AGE_MS) {
      j = await judge();
      if (j.bad) { rep && rep.fail(qi, j.bad); failed.push(tk.name); continue; }
      if (j.worstUsd < j.floorUsd) { refuse(j); continue; }
    }

    // A permit is signed locally; the key never leaves this script. Only the
    // Uniswap route has a permit to sign -- Kyber's allowance is already on
    // chain by this point.
    const body = { quote: j.v.venue === "uniswap" ? j.quote.quote : null };
    if (j.v.venue === "uniswap" && j.quote.permitData) {
      const pi = rep ? rep.start(`Sign permit for ${tk.name}`) : -1;
      try {
        body.signature = signPermit(j.quote.permitData);
        body.permitData = j.quote.permitData;
        rep && rep.ok(pi, "signed locally");
      } catch (e) { rep && rep.fail(pi, String((e && e.message) || e).slice(0, 60)); failed.push(tk.name); continue; }
    }

    const si = rep ? rep.start(`Build swap for ${tk.name}`) : -1;
    let swapTx;
    try {
      if (j.v.venue === "kyberswap") {
        swapTx = await kyberBuild(j.v.routeSummary, WALLET, routerPct);
      } else {
        const r = await uniPost("/swap", body);
        swapTx = r && r.swap;
      }
      if (!swapTx || !swapTx.data) throw new Error("no transaction returned");
      rep && rep.ok(si, `${j.v.venue} \u00B7 to ${String(swapTx.to).slice(0, 10)}…`);
    } catch (e) { rep && rep.fail(si, String((e && e.message) || e).slice(0, 60)); failed.push(tk.name); continue; }

    // sendTx rethrows, and every other step in this loop already continues on
    // failure. Without this catch one bad token ended the whole run: a close-all
    // that swapped STRATTON, hit TRANSFER_FROM_FAILED on HOTDOG, and left every
    // remaining token untouched. One token's problem is not the others'.
    try {
      const res = await sendTx(swapTx.to, swapTx.data, rep, `Swap ${tk.name} to ${targetName}`);
      if (res && res.dryRun) { dryRuns++; done++; ledger.push({ name: tk.name, inUsd: val.inUsd, outUsd: j.outUsd, dry: true }); }
      else if (res && res.ok) {
        done++;
        // The quote is a promise; the receipt is what happened. Read the amount
        // that actually reached the wallet so the two can be compared. Native
        // ETH moves without a Transfer log, so the gas top-up reports the quote.
        const got = target === USDG_ADDR
          ? receivedFrom(res.receipt, targetAddr, WALLET) : null;
        const tPx = (await targetPriceOf());
        const gotUsd = got == null || !tPx
          ? null : (Number(got) / Math.pow(10, targetDec)) * tPx.px;
        rep && rep.note(`${tk.name} sold`,
          `${human(bal, tkDec)} ${tk.name} worth ${fmtUsd(val.inUsd)}`
          + ` \u2192 ` + (got == null
              ? `${human(j.out, targetDec)} ${targetName} quoted`
              : `${human(got, targetDec)} ${targetName} received`)
          + ` (${fmtUsd(gotUsd == null ? j.outUsd : gotUsd)})`
          + vsMarket(val.inUsd, gotUsd == null ? j.outUsd : gotUsd));
        ledger.push({ name: tk.name, inUsd: val.inUsd, outUsd: gotUsd == null ? j.outUsd : gotUsd });
      }
      else failed.push(tk.name);
    } catch (e) {
      failed.push(tk.name);
      rep && rep.note(`${tk.name} not sold`, String((e && e.message) || e).slice(0, 90));
    }
  }
  if (ledger.length) {
    const tot = k => ledger.reduce((a, r) => a + (r[k] || 0), 0);
    const totIn = tot("inUsd"), totOut = tot("outUsd");
    // Headline carries the totals and how the run did against the market; the
    // detail breaks it down per token. Separated by a bullet, because three
    // spaces between "$41.33" and the next token name read as one number.
    rep && rep.note(
      `Sold ${fmtUsd(totIn)} for ${fmtUsd(totOut)} of ${targetName}${vsMarket(totIn, totOut)}`
        + (ledger.some(r => r.dry) ? " (practice)" : ""),
      ledger.map(r => `${r.name} ${fmtUsd(r.inUsd)} \u2192 ${fmtUsd(r.outUsd)}`).join("  \u00B7  "));
  }
  if (failed.length) {
    rep && rep.note(
      `${done} sold, ${failed.length} failed`,
      `could not sell ${failed.join(", ")}. They are still in the wallet; Cleanup can retry them.`);
  }
  // Shaped like every other action's result. Returning a plain count meant
  // run() saw no .ok on it and announced a revert over a swap that had just
  // succeeded on chain.
  return { ok: done > 0 && failed.length === 0, swaps: done, failed: failed.length,
           dryRun: dryRuns > 0 && dryRuns === done, partial: done > 0 && failed.length > 0 };
}

// Smallest position worth selling. Below this the gas costs more than the
// token is worth, and an airdropped token with no market cannot be sold at all.
const MIN_SWEEP_USD = 1;

// Sell every leftover token in the wallet worth more than MIN_SWEEP_USD. Left
// behind by a swap that failed, a close whose swap was declined, or a manual
// trade. Spam is excluded by value rather than by any list: an airdropped token
// with no liquidity has no price, and one worth cents is not worth the gas.
// Tokens seen in any recent position, remembered on disk. Blockscout's balance
// list is indexed and lags the chain by seconds, so a token that a close just
// returned is briefly invisible to it: Cleanup reported an empty wallet while
// $200 of STRATTON was sitting in it, then worked a few seconds later. Anything
// a position ever held is a candidate regardless of what the indexer says.
function rememberTokens(addrs) {
  try {
    const prev = loadJson("known_tokens") || [];
    const set = new Set(prev);
    for (const a of addrs) if (a && a !== ZERO_TOKEN) set.add(a);
    // Bounded: this only exists to cover the indexer's lag.
    saveJson("known_tokens", [...set].slice(-200));
  } catch (e) {}
}
// Token decimals, read from chain. 18 is the safe default: it is what almost
// every token here uses, and a wrong guess only misstates a display amount.
async function decOfAddr(addr) {
  try {
    const r = await rpcRaw("eth_call", [{ to: "0x" + addr, data: "0x" + SEL.decimals }, "latest"]);
    const n = Number(BigInt(r));
    return Number.isFinite(n) && n >= 0 && n <= 36 ? n : 18;
  } catch (e) { return 18; }
}

function knownTokens() {
  try { return loadJson("known_tokens") || []; } catch (e) { return []; }
}

// Spam tokens. Airdropped fakes (a 63 "ETH" priced like ETH off a $2k pool,
// a "USDT" nobody buys) used to be priced, quoted and refused on every cleanup,
// costing a minute each run. They are remembered here and skipped before any
// quote, with four guards against flagging a real token:
//   1. Two strikes. One bad answer (a broken route quoted $44 of GRASS at
//      $0.08) is a strike, not a flag; a second strike on a cleanup at least
//      SPAM_STRIKE_GAP_MS later flags it.
//   2. Expiry. A flag lasts SPAM_TTL_MS, then the token is priced afresh.
//   3. LP leftovers are never flagged: a token held by one of our open
//      positions in the last LP_EXEMPT_MS is what Cleanup exists to sell, even
//      when we were most of its pool and the pool is now thin.
//   4. Un-flagging by hand (Cleanup > Spam tokens) also shields the token from
//      new strikes for SPAM_TTL_MS, so it is not flagged again next run.
const SPAM_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const SPAM_STRIKE_GAP_MS = 60 * 60 * 1000;
const LP_EXEMPT_MS = 7 * 24 * 60 * 60 * 1000;
function liveEntries(suffix, ttl) {
  let all = {};
  try { all = loadJson(suffix) || {}; } catch (e) {}
  const now = Date.now(), live = {};
  for (const a in all) if (all[a] && now - all[a].at < ttl) live[a] = all[a];
  return live;
}
// Tokens held by an open position, stamped each time the table sees one.
function stampLpTokens(addrs) {
  try {
    const all = liveEntries("lp_tokens", LP_EXEMPT_MS);
    for (const a of addrs) if (a && a !== ZERO_TOKEN) all[a] = { at: Date.now() };
    saveJson("lp_tokens", all);
  } catch (e) {}
}
function spamExempt(addr) {
  return !!(liveEntries("lp_tokens", LP_EXEMPT_MS)[addr] || liveEntries("spam_trusted", SPAM_TTL_MS)[addr]);
}
function spamTokens() {
  const live = liveEntries("spam_tokens", SPAM_TTL_MS);
  for (const a in live) if (spamExempt(a)) delete live[a];
  return live;
}
// Returns true when this strike flagged the token.
function strikeSpam(addr, name, why) {
  if (!addr || spamExempt(addr)) return false;
  const now = Date.now();
  const strikes = liveEntries("spam_strikes", SPAM_TTL_MS);
  const prev = strikes[addr];
  if (!prev || now - prev.at < SPAM_STRIKE_GAP_MS) {
    // First strike, or a repeat within the hour: keep the first time, so two
    // runs a minute apart never add up to a flag.
    strikes[addr] = { name, why, at: prev ? prev.at : now };
    saveJson("spam_strikes", strikes);
    return false;
  }
  delete strikes[addr];
  saveJson("spam_strikes", strikes);
  const all = liveEntries("spam_tokens", SPAM_TTL_MS);
  all[addr] = { name, why, at: now };
  saveJson("spam_tokens", all);
  return true;
}
// By hand from the Spam tokens list: the owner can see a fake for what it is,
// so a token with one strike can be flagged without waiting for the second.
function flagSpamNow(addr) {
  const strikes = liveEntries("spam_strikes", SPAM_TTL_MS);
  const trusted = liveEntries("spam_trusted", SPAM_TTL_MS);
  const all = liveEntries("spam_tokens", SPAM_TTL_MS);
  const st = strikes[addr] || {};
  all[addr] = { name: st.name || addr.slice(0, 8), why: `flagged by hand (${st.why || "one strike"})`, at: Date.now() };
  delete strikes[addr]; delete trusted[addr];
  saveJson("spam_tokens", all);
  saveJson("spam_strikes", strikes);
  saveJson("spam_trusted", trusted);
}
function unflagSpam(addrs) {
  const all = liveEntries("spam_tokens", SPAM_TTL_MS);
  const strikes = liveEntries("spam_strikes", SPAM_TTL_MS);
  const trusted = liveEntries("spam_trusted", SPAM_TTL_MS);
  for (const a of addrs) {
    trusted[a] = { name: (all[a] || strikes[a] || {}).name || a.slice(0, 8), at: Date.now() };
    delete all[a]; delete strikes[a];
  }
  saveJson("spam_tokens", all);
  saveJson("spam_strikes", strikes);
  saveJson("spam_trusted", trusted);
}

async function sweepWallet(target, rep, limits) {
  const targetName = target === USDG_ADDR ? "USDG" : "ETH";

  const si = rep ? rep.start("Read wallet tokens") : -1;
  let held = [];
  try {
    const req = new Request(explorerUrl(`/api/v2/addresses/${WALLET}/tokens?type=ERC-20`));
    req.headers = explorerHeaders();
    req.timeoutInterval = 15;
    const j = await req.loadJSON();
    for (const it of (j && j.items) || []) {
      const tk = it.token || {};
      const addr = String(tk.address || tk.address_hash || "").replace(/^0x/, "").toLowerCase();
      const raw = BigInt(it.value || "0");
      // Never sell what counts as cash, whichever direction this is going: USDG,
      // native ETH and WETH all stay. Matched by address, never by symbol, since
      // this wallet already holds a second token calling itself USDG that is
      // worth about a cent. A symbol is not an identity.
      if (!addr || raw === 0n) continue;
      if (addr === USDG_ADDR || addr === WETH_ADDR || addr === ZERO_TOKEN) continue;
      if (addr === target) continue;
      const dec = Number(tk.decimals != null ? tk.decimals : 18);
      held.push({ addr, name: tk.symbol || addr.slice(0, 8), amt: Number(raw) / Math.pow(10, dec) });
    }
    // Anything a position held recently, whether or not the indexer lists it.
    // Balances come from the chain, so a stale extra costs one call and a
    // missing one is what this is here to prevent.
    const seen = new Set(held.map(h => h.addr));
    const extra = knownTokens().filter(a =>
      a && a !== target && a !== USDG_ADDR && a !== WETH_ADDR && a !== ZERO_TOKEN && !seen.has(a));
    if (extra.length) {
      const checked = await Promise.all(extra.map(async addr => {
        try {
          const raw = BigInt(await rpcRaw("eth_call", [
            { to: "0x" + addr, data: "0x" + SEL_BALANCE_OF + _a(WALLET) }, "latest"]));
          if (raw === 0n) return null;
          const dec = await decOfAddr(addr);
          return { addr, name: addr.slice(0, 8), amt: Number(raw) / Math.pow(10, dec) };
        } catch (e) { return null; }
      }));
      for (const c of checked) if (c) held.push(c);
    }
    rep && rep.ok(si, `${held.length} token${held.length === 1 ? "" : "s"} with a balance`
      + (extra.length ? ` (${extra.length} checked beyond the indexer)` : ""));
  } catch (e) {
    rep && rep.fail(si, String((e && e.message) || e).slice(0, 60));
    return { ok: false, swaps: 0, failed: 0 };
  }
  // Nothing to do is a success, not a revert: the wallet is already clean.
  if (!held.length) {
    rep && rep.note("Nothing to clean up", "no other tokens in the wallet");
    return { ok: true, swaps: 0, failed: 0, nothingToDo: true };
  }

  // Price them, so dust and unsellable spam are dropped before any gas is spent.
  const pi = rep ? rep.start("Price them") : -1;
  const priced = [];
  try {
    // One token per request. This endpoint returns at most 30 pairs however
    // many tokens are asked for, so a batch of 25 answered for only a few and
    // the rest looked priceless: they would have been skipped as spam and left
    // in the wallet, which is the opposite of what Cleanup is for.
    const spam = spamTokens();
    const known = held.filter(h => spam[h.addr]);
    held = held.filter(h => !spam[h.addr]);
    if (known.length) rep && rep.note(`Skipping ${known.length} spam token${known.length === 1 ? "" : "s"}`,
      known.map(h => h.name).join(", ") + " \u00B7 no buyer at the last try, rechecked weekly");
    const results = await Promise.all(held.map(async h => {
      try {
        const req = new Request("https://api.dexscreener.com/latest/dex/tokens/0x" + h.addr);
        req.headers = { Accept: "application/json" };
        req.timeoutInterval = 12;
        const j = await req.loadJSON();
        let best = null;
        for (const p of (j && j.pairs) || []) {
          if (p.chainId !== "robinhood") continue;
          const b = (p.baseToken && p.baseToken.address || "").replace(/^0x/, "").toLowerCase();
          if (b !== h.addr) continue;   // priceUsd describes the base token
          // A pair with no liquidity figure (common on brand-new pools) is
          // unknown, not empty: null, so it is never read as $0 of liquidity.
          const px = Number(p.priceUsd || 0);
          const lr = p.liquidity && p.liquidity.usd;
          const liq = lr != null && Number.isFinite(Number(lr)) ? Number(lr) : null;
          if (!(px > 0)) continue;
          if (!best || (liq != null && (best.liq == null || liq > best.liq))) best = { px, liq };
        }
        if (!best) return null;
        // A holding cannot sell for more than its whole pool holds. Above that,
        // the price is a thin pool's and the tokens are likely spam (the fake
        // "ETH": $153k "worth" against $2.2k of liquidity). It is not sold
        // either way; strikeSpam decides whether it is skipped next time.
        const usd = h.amt * best.px;
        if (best.liq != null && usd > best.liq) {
          strikeSpam(h.addr, h.name, "worth more than its pool");
          return null;
        }
        return { ...h, usd };
      } catch (e) { return null; }
    }));
    for (const r of results) if (r) priced.push(r);
    priced.sort((a, b) => b.usd - a.usd);
    rep && rep.ok(pi, `${priced.length} of ${held.length} have a market`);
  } catch (e) {
    rep && rep.fail(pi, String((e && e.message) || e).slice(0, 60));
    return { ok: false, swaps: 0, failed: 0 };
  }

  const worth = priced.filter(p => p.usd >= MIN_SWEEP_USD);
  const skipped = held.length - worth.length;
  if (!worth.length) {
    rep && rep.note("Nothing worth selling",
      `${skipped} token${skipped === 1 ? "" : "s"} below ${fmtUsd(MIN_SWEEP_USD)} or with no market`);
    return { ok: true, swaps: 0, failed: 0, nothingToDo: true };
  }
  const total = worth.reduce((s, p) => s + p.usd, 0);
  rep && rep.note(`Selling ${worth.length} token${worth.length === 1 ? "" : "s"}`,
    `about ${fmtUsd(total)} total${skipped ? `, skipping ${skipped} below ${fmtUsd(MIN_SWEEP_USD)} or unsellable` : ""}`);

  return swapTokenList(worth.map(p => ({ addr: p.addr, name: p.name })), target, rep, limits);
}

async function closeAndSwapRows(rows, target, rep) {
  _nonceOverride = null;
  const closed = await closeRows(rows, rep);
  // In a dry run the tokens are never actually received, so the swap cannot be
  // simulated against a real balance; the close result is reported alone.
  if (closed.dryRun) return { ...closed, swapSkipped: "swap not previewed in practice mode" };
  if (!closed.ok) return closed;

  // The close and the swap are two outcomes, and only one of them used to be
  // reported. Spreading the close result meant ok:true survived even when the
  // swap sold nothing, so a close-and-swap that never swapped announced plain
  // success and the tokens sat in the wallet unnoticed. Carry both.
  const sw = await swapOutTo(rows, target, rep);
  const swaps = (sw && sw.swaps) || 0;
  const failedSwaps = (sw && sw.failed) || 0;
  return {
    ...closed,
    swaps,
    swapFailed: failedSwaps,
    // Closing worked either way; the caller needs to know the swap did not, and
    // that the tokens are still sitting in the wallet.
    swapIncomplete: swaps === 0 || failedSwaps > 0,
    swapNote: swaps === 0 && failedSwaps === 0
      ? (sw && sw.skipReason) || "nothing was sold"
      : null,
  };
}

// Collect fees only. Liquidity and the position NFT are left untouched.
async function claimRows(rows, rep) {
  _nonceOverride = null;
  const deadline = BigInt(Math.floor(Date.now() / 1000) + DEADLINE_MIN * 60);
  const fee = await planServiceFee(rows, 0,
    f => buildClaimCalldata(rows, WALLET, deadline, f).calldata, rep);
  const { calldata, pools } = buildClaimCalldata(rows, WALLET, deadline, fee);
  const r = await sendTx(POSITION_MANAGER, calldata, rep, "Claim fees");
  return { ...r, pools, serviceFee: fee && fee.label };
}

async function claimAndSwapRows(rows, target, rep) {
  _nonceOverride = null;
  const claimed = await claimRows(rows, rep);
  if (claimed.dryRun) return { ...claimed, swapSkipped: "swap not previewed in practice mode" };
  if (!claimed.ok) return claimed;
  // The swap result is a shape, not a count: {ok, swaps, failed, partial}.
  // Assigning the whole object to `swaps` printed "[object Object] swap done",
  // and worse, left `ok` as the CLAIM's success, so a claim whose sale failed
  // was announced as "Claimed and swapped" while the tokens sat in the wallet.
  // The two outcomes are merged explicitly now, the same way the close path
  // does it.
  const swap = await swapOutTo(rows, target, rep);
  return {
    ...claimed,
    swaps: swap.swaps, failed: swap.failed, swapFailed: swap.failed,
    ok: claimed.ok && swap.ok,
    partial: swap.partial,
    swapIncomplete: swap.swaps === 0 && swap.failed > 0,
    swapNote: swap.skipReason,
  };
}

// Position ids minted to us in a transaction, read from its Transfer logs.
// The explorer lags badly after a position changes, and the widget finds
// positions through it, so a fresh re-entry looks like "no positions" for as
// long as indexing takes. Reading the ids out of the receipt makes the table
// correct immediately and independently of the explorer.
function mintedIdsFrom(receipt) {
  const TRANSFER = "0x" + keccak256("0x" + Array.from("Transfer(address,address,uint256)", c => c.charCodeAt(0).toString(16).padStart(2, "0")).join(""));
  const ZERO32 = "0x" + "0".repeat(64);
  const out = [];
  for (const lg of (receipt && receipt.logs) || []) {
    if (String(lg.address || "").toLowerCase() !== POSITION_MANAGER) continue;
    const t = lg.topics || [];
    if (t[0] !== TRANSFER || t[1] !== ZERO32) continue;
    // topic[2] is the recipient, topic[3] the token id.
    if (t[2] && ("0x" + String(t[2]).slice(26).toLowerCase()) !== WALLET) continue;
    try { out.push(BigInt(t[3]).toString()); } catch (e) {}
  }
  return out;
}

// Close and immediately re-open, atomically, in one transaction.
async function reentryRows(rows, rep) {
  _nonceOverride = null;
  const deadline = BigInt(Math.floor(Date.now() / 1000) + DEADLINE_MIN * 60);
  const probe = buildReentryCalldata(rows, WALLET, deadline);
  if (!probe) throw new Error("No out-of-range USDG or ETH positions to re-enter");
  // Only the positions that are actually re-entered are charged, and each
  // ladder re-opened owes one open fee.
  const planned = rows.map(r => ({ ...r, positions: (r.positions || []).filter(p => p.reentry) }));
  const fee = await planServiceFee(planned, probe.ladders,
    f => buildReentryCalldata(rows, WALLET, deadline, f).calldata, rep);
  const built = buildReentryCalldata(rows, WALLET, deadline, fee);
  const r = await sendTx(POSITION_MANAGER, built.calldata, rep, "Close and re-open");
  if (r && r.ok && r.receipt) {
    const ids = mintedIdsFrom(r.receipt);
    if (ids.length) {
      // Merge, never replace. Saving only the minted ids wiped every position
      // in the other pools from the cache: re-entering one token left the
      // table showing just that token, and with the explorer lagging a fresh
      // mint there was nothing to restore the rest from. Drop the ids this
      // transaction burned, keep the others, add the new ones.
      const norm = v => { try { return BigInt(v).toString(); } catch (e) { return null; } };
      const burned = new Set();
      for (const row of rows) {
        for (const p of row.positions || []) {
          const k = norm(p.tokenId);
          if (k) burned.add(k);
        }
      }
      const seen = new Set();
      const keep = [];
      const add = (raw, skipBurned) => {
        const k = norm(raw);
        if (!k || seen.has(k) || (skipBurned && burned.has(k))) return;
        seen.add(k);
        keep.push(k);
      };
      for (const raw of loadActiveIds() || []) add(raw, true);
      for (const id of ids) add(id, false);
      saveActiveIds(keep);
      rep && rep.note(`${ids.length} new position${ids.length > 1 ? "s" : ""} recorded`,
                      `read from the receipt, so the table does not wait on the explorer`
                      + (keep.length > ids.length ? `; ${keep.length - ids.length} other position${keep.length - ids.length > 1 ? "s" : ""} kept` : ""));
    }
  }
  return { ...r, count: built.count };
}

// How much USDG this wallet holds right now, in raw units.
//
// Read before the burn and again after the sale, because the difference is the
// only honest measure of what this ladder returned. Reading the balance once,
// afterwards, would hand the roll every spare dollar already sitting in the
// wallet and re-open a far larger position than the one that was closed.
async function rollCashBalance() {
  const [r] = await multicall([{ target: "0x" + USDG_ADDR, data: SEL.balanceOf + hexAddr(WALLET) }]);
  if (!r || !r.success || r.data.length < 64) throw new Error("USDG balance unreadable");
  return decU256(r.data.slice(0, 64));
}

// The current tick of one pool, read fresh.
//
// Not the tick the table was built with: a close and a sale both move the price,
// and the sale is the larger of the two. Anchoring the new ladder to a tick
// measured before either would place its top edge away from the actual price by
// however far our own selling pushed it.
async function rollCurrentTick(poolKey, poolKeyStr) {
  const prices = await fetchPoolPrices(new Map([[poolKeyStr, poolKey]]));
  const p = prices.get(poolKeyStr);
  if (!p || !Number.isFinite(p.tick)) throw new Error("pool price unreadable");
  return p.tick;
}

// Minting from the wallet pulls cash through Permit2, the same two legs a swap
// needs, but with the PositionManager as the spender instead of the router.
//
// The closer has never needed this before: every mint it did was a re-entry,
// funded by a burn inside the same unlock, which owes the wallet nothing. A roll
// is funded by the wallet, so both legs have to be in place or the mint reverts
// with TRANSFER_FROM_FAILED -- the same failure, for the same reason, that the
// swap path hit the first time it sold a new token.
async function ensureRollMintApprovals(amount, rep) {
  const t = "0x" + USDG_ADDR;
  const ercRaw = await rpcRaw("eth_call", [{ to: t,
    data: "0x" + SEL_ALLOWANCE + _a(WALLET) + _a(PERMIT2) }, "latest"]).catch(() => null);
  if ((ercRaw ? BigInt(ercRaw) : 0n) < amount) {
    const r = await sendTx(t, "0x" + SEL_APPROVE + _a(PERMIT2) + _u(MAX_U256),
      rep, "Approve USDG for Permit2");
    if (!r || (!r.ok && !r.dryRun)) throw new Error("USDG could not be approved to Permit2");
  }
  const p2Raw = await rpcRaw("eth_call", [{ to: PERMIT2,
    data: "0x" + SEL_P2_ALLOWANCE + _a(WALLET) + _a(USDG_ADDR) + _a(POSITION_MANAGER) }, "latest"]).catch(() => null);
  let p2Amount = 0n, p2Exp = 0n;
  if (p2Raw) {
    const h = String(p2Raw).replace(/^0x/, "");
    p2Amount = BigInt("0x" + (h.slice(0, 64) || "0"));
    p2Exp = BigInt("0x" + (h.slice(64, 128) || "0"));
  }
  const now = BigInt(Math.floor(Date.now() / 1000));
  if (p2Amount < amount || (p2Exp !== 0n && p2Exp < now)) {
    const r = await sendTx(PERMIT2,
      "0x" + SEL_P2_APPROVE + _a(USDG_ADDR) + _a(POSITION_MANAGER) + _u(MAX_U160) + _u(MAX_U48),
      rep, "Allow the position manager to spend USDG");
    if (!r || (!r.ok && !r.dryRun)) throw new Error("the position manager could not be approved");
  }
}

// Close the ladder, sell what it returns, and re-open it at the current price
// with its original shape and its original capital.
//
// Three transactions, and the order matters more than the count: every step
// leaves value somewhere recoverable. A failed sale leaves tokens in the wallet.
// A failed mint leaves USDG in the wallet. Nothing is ever left half-placed,
// which is why this is not attempted as one clever transaction.
async function rollRows(rows, rep) {
  _nonceOverride = null;
  const positions = rows.flatMap(r => r.positions || []);

  // Shape first, against the tick we already have, so an impossible roll is
  // refused before anything is burned rather than after.
  const pre = planRollShape(positions, (positions.find(p => p.currentTick != null) || {}).currentTick);
  if (pre.bad) throw new Error(pre.bad);

  const before = await rollCashBalance();
  rep && rep.note("Ladder captured",
    `${pre.live.length} slice${pre.live.length > 1 ? "s" : ""}, `
    + `${pre.width} ticks wide, original cost ${fmtUsd(Number(pre.initialRaw) / 1e6)}`);

  const closed = await closeRows(rows, rep);
  if (closed.dryRun) return { ...closed, rollSkipped: "a roll is not previewed in practice mode" };
  if (!closed.ok) return closed;

  const sw = await swapOutTo(rows, USDG_ADDR, rep);

  const after = await rollCashBalance();
  const recovered = after > before ? after - before : 0n;
  // The re-open owes the open fee, paid from what came back, so the ladder is
  // sized from what is left after it rather than failing 0.3 USDG short.
  const openFee = serviceFeeOn() ? SERVICE_OPEN_FEE_RAW : 0n;
  rep && rep.note("Recovered", `${fmtUsd(Number(recovered) / 1e6)} of USDG came back`
    + (recovered < pre.initialRaw
        ? ` (under the ${fmtUsd(Number(pre.initialRaw) / 1e6)} it cost, so the ladder re-opens smaller)`
        : ` (cost ${fmtUsd(Number(pre.initialRaw) / 1e6)}, so ${fmtUsd(Number(recovered - pre.initialRaw) / 1e6)} stays as profit)`));

  // Re-anchor to the price as it is now, after our own sale moved it.
  const tick = await rollCurrentTick(pre.poolKey, pre.live[0].poolKeyStr);
  const plan = planRollFunding(planRollShape(positions, tick),
                               recovered > openFee ? recovered - openFee : 0n);
  if (plan.bad) {
    return { ...closed, ...sw, rollSkipped: plan.bad,
             recoveredUsd: Number(recovered) / 1e6 };
  }

  await ensureRollMintApprovals(plan.deployRaw + openFee, rep);

  const deadline = BigInt(Math.floor(Date.now() / 1000) + DEADLINE_MIN * 60);
  const calldata = buildRollMintCalldata(plan.poolKey, plan.slices, plan.side, WALLET, deadline, openFee);
  const minted = await sendTx(POSITION_MANAGER, calldata, rep,
    `Re-open ${plan.slices.length} slice${plan.slices.length > 1 ? "s" : ""}`);
  if (minted && minted.ok && minted.receipt) {
    const ids = mintedIdsFrom(minted.receipt);
    if (ids.length) {
      // Same merge rule as re-entry: drop the ids this roll burned, keep every
      // other pool's, add the new ones. Replacing outright would empty the table
      // of everything except the ladder just rebuilt.
      const norm = v => { try { return BigInt(v).toString(); } catch (e) { return null; } };
      const burned = new Set();
      for (const p of positions) { const k = norm(p.tokenId); if (k) burned.add(k); }
      const seen = new Set(), keep = [];
      const add = (raw, skipBurned) => {
        const k = norm(raw);
        if (!k || seen.has(k) || (skipBurned && burned.has(k))) return;
        seen.add(k); keep.push(k);
      };
      for (const raw of loadActiveIds() || []) add(raw, true);
      for (const id of ids) add(id, false);
      saveActiveIds(keep);
      rep && rep.note(`${ids.length} new position${ids.length > 1 ? "s" : ""} recorded`,
        "read from the receipt, so the table does not wait on the explorer");
    }
  }
  return {
    ...closed,
    swaps: (sw && sw.swaps) || 0,
    swapFailed: (sw && sw.failed) || 0,
    ok: closed.ok && !!(minted && minted.ok),
    rolled: plan.slices.length,
    deployedUsd: Number(plan.deployRaw) / 1e6,
    recoveredUsd: Number(recovered) / 1e6,
    profitUsd: Number(plan.surplusRaw) / 1e6,
    hash: minted && minted.hash,
  };
}


// ----------------------------- token discovery --------------------------------
function decodeAbiString(hex) {
  if (!hex || hex.length < 128) return null;
  const offset = Number(decU256(hex.slice(0, 64))) * 2;
  const len = Number(decU256(hex.slice(offset, offset + 64)));
  const raw = hex.slice(offset + 64, offset + 64 + len * 2);
  let s = "";
  for (let i = 0; i < raw.length; i += 2) s += String.fromCharCode(parseInt(raw.substr(i, 2), 16));
  return s;
}

async function discoverTokens(addresses) {
  const unknown = addresses.filter(a => !KNOWN_TOKENS[a] && a !== "0000000000000000000000000000000000000000");
  if (!unknown.length) return;

  const cached = loadJson("tokens");
  if (cached) {
    for (const addr in cached) {
      if (!KNOWN_TOKENS[addr]) KNOWN_TOKENS[addr] = cached[addr];
    }
  }
  const still = unknown.filter(a => !KNOWN_TOKENS[a]);
  if (!still.length) return;

  const calls = [];
  for (const a of still) {
    calls.push({ target: "0x" + a, data: SEL.symbol });
    calls.push({ target: "0x" + a, data: SEL.decimals });
  }
  const results = await multicall(calls);

  for (let i = 0; i < still.length; i++) {
    const symR = results[i * 2];
    const decR = results[i * 2 + 1];
    const sym = symR.success ? (decodeAbiString(symR.data) || still[i].slice(0, 6)) : still[i].slice(0, 6);
    const dec = decR.success ? Number(decU256(decR.data.slice(0, 64))) : 18;
    KNOWN_TOKENS[still[i]] = { symbol: sym, decimals: dec, usd: null };
  }

  const toCache = {};
  for (const addr in KNOWN_TOKENS) {
    if (!STABLES[addr]) toCache[addr] = { symbol: KNOWN_TOKENS[addr].symbol, decimals: KNOWN_TOKENS[addr].decimals, usd: null };
  }
  saveJson("tokens", toCache);
}

// ----------------------------- position discovery -----------------------------
// Position NFTs are not enumerable and no free RPC for this chain serves
// eth_getLogs, so the only on-chain discovery is scanning ownerOf over the token
// id space. That is ~1500 ids/sec and a wallet's positions can span 200k+ ids,
// which no widget has time for. The explorer indexes ownership already, so ask it
// for the ids and keep the scan only as a fallback.
async function fetchOwnedIdsFromExplorer(wallet) {
  let url = explorerUrl(`/api/v2/addresses/${wallet}/nft?type=ERC-721`);
  const ids = [];
  for (let page = 0; page < EXPLORER_MAX_PAGES; page++) {
    let j = null;
    for (let attempt = 0; attempt < 2; attempt++) {
      const req = new Request(url);
      // Default Scriptable UA trips the explorer's bot challenge; a browser UA passes.
      req.headers = explorerHeaders();
      req.timeoutInterval = 8;
      const body = await req.loadString();
      const status = req.response ? req.response.statusCode : 200;
      // The explorer throttles bursts with an empty 429; a short wait clears it.
      if (status === 429) { if (!attempt) await pause(3000); continue; }
      if (status !== 200) throw new Error(`explorer HTTP ${status}`);
      j = JSON.parse(body);
      break;
    }
    if (!j || !j.items) throw new Error("explorer: unexpected response");
    for (const it of j.items) {
      const a = ((it.token && (it.token.address_hash || it.token.address)) || "").toLowerCase();
      if (a === POSITION_MANAGER) ids.push(Number(it.id));
    }
    const n = j.next_page_params;
    if (!n) break;
    url = explorerUrl(`/api/v2/addresses/${wallet}/nft?type=ERC-721`)
        + `&token_type=${encodeURIComponent(n.token_type)}`
        + `&token_contract_address_hash=${encodeURIComponent(n.token_contract_address_hash)}`
        + `&token_id=${encodeURIComponent(n.token_id)}`
        + `&items_count=${encodeURIComponent(n.items_count)}`;
  }
  return [...new Set(ids)].filter(n => Number.isFinite(n)).sort((a, b) => a - b);
}

// The explorer index can lag a burn or transfer, so confirm ownership on-chain
// before trusting the ids. One multicall, and it keeps someone else's closed
// position from ever showing up as yours.
async function verifyOwned(ids, wallet) {
  if (!ids.length) return [];
  const walletClean = wallet.replace("0x", "").toLowerCase();
  const results = await multicall(ids.map(id => ({
    target: POSITION_MANAGER,
    data: SEL.ownerOf + hexU256(BigInt(id)),
  })));
  return ids.filter((id, i) =>
    results[i] && results[i].success && results[i].data.length >= 64 &&
    decAddr(results[i].data.slice(0, 64)) === walletClean);
}

async function getExpectedCount(wallet) {
  // Through multicall so it merges with the ownership check issued alongside it.
  const [r] = await multicall([{ target: POSITION_MANAGER, data: SEL.balanceOf + hexAddr(wallet) }]);
  if (!r || !r.success || r.data.length < 64) throw new Error("balanceOf failed");
  return Number(decU256(r.data.slice(0, 64)));
}

async function getNextTokenId() {
  const raw = await ethCall(POSITION_MANAGER, "0x" + SEL.nextTokenId);
  return Number(decU256(raw.replace("0x", "")));
}

async function scanBatch(startId, count, wallet) {
  const walletClean = wallet.replace("0x", "").toLowerCase();
  const ids = [];
  for (let i = 0; i < count; i++) ids.push(startId + i);
  const calls = ids.map(id => ({
    target: POSITION_MANAGER,
    data: SEL.ownerOf + hexU256(BigInt(id)),
  }));
  const results = await multicall(calls);
  const found = [];
  for (let i = 0; i < results.length; i++) {
    if (results[i].success && results[i].data.length >= 64) {
      const owner = decAddr(results[i].data.slice(0, 64));
      if (owner === walletClean) found.push(ids[i]);
    }
  }
  return found;
}

// A widget extension is killed if it runs too long, so the backward scan gets a
// wall-clock budget. Running out returns whatever was found instead of dying:
// a partial list still renders, and the next run resumes with a warm cache.
const DISCOVER_BUDGET_MS = 12000;

async function discoverActiveIds(wallet, expected) {
  if (expected == null) expected = await getExpectedCount(wallet);
  if (expected === 0) return [];

  const nextId = await getNextTokenId();
  const found = new Set();
  const deadline = Date.now() + DISCOVER_BUDGET_MS;

  let cursor = nextId;
  // Scan up to 50K IDs back (50 batches of 1000). Covers any realistic gap.
  const minScan = Math.max(1, nextId - 50000);
  while (found.size < expected && cursor > minScan) {
    const start = Math.max(1, cursor - SCAN_BATCH);
    const batch = await scanBatch(start, cursor - start, wallet);
    batch.forEach(id => found.add(id));
    cursor = start;
    if (found.size >= expected) break;
    if (Date.now() > deadline) break;
  }
  return [...found].sort((a, b) => a - b);
}

// ----------------------------- position data ----------------------------------
function decodePoolAndPositionInfo(hex) {
  // returns { currency0, currency1, fee, tickSpacing, hooks, tickLower, tickUpper }
  const currency0 = decAddr(hex.slice(0, 64));
  const currency1 = decAddr(hex.slice(64, 128));
  const fee = Number(decU256(hex.slice(128, 192)));
  const tsRaw = Number(decU256(hex.slice(192, 256)));
  const tickSpacing = tsRaw >= 0x800000 ? tsRaw - 0x1000000 : tsRaw;
  const hooks = decAddr(hex.slice(256, 320));
  const info = decU256(hex.slice(320, 384));

  const tickLowerRaw = Number((info >> 8n) & 0xFFFFFFn);
  const tickUpperRaw = Number((info >> 32n) & 0xFFFFFFn);

  return {
    currency0, currency1, fee, tickSpacing, hooks,
    tickLower: decInt24(tickLowerRaw),
    tickUpper: decInt24(tickUpperRaw),
  };
}

async function fetchPositionData(activeIds) {
  const calls = [];
  // getPoolAndPositionInfo for each
  for (const id of activeIds) {
    calls.push({
      target: POSITION_MANAGER,
      data: SEL.getPoolAndPositionInfo + hexU256(BigInt(id)),
    });
  }
  // getPositionLiquidity for each
  for (const id of activeIds) {
    calls.push({
      target: POSITION_MANAGER,
      data: SEL.getPositionLiquidity + hexU256(BigInt(id)),
    });
  }

  const results = await multicall(calls);
  const n = activeIds.length;
  const positions = [];
  const poolKeys = new Map(); // poolKeyHash -> {currency0, currency1, fee, tickSpacing, hooks}

  for (let i = 0; i < n; i++) {
    const infoRes = results[i];
    const liqRes = results[n + i];
    if (!infoRes.success || !liqRes.success) continue;

    const info = decodePoolAndPositionInfo(infoRes.data);
    const liquidity = decU256(liqRes.data.slice(0, 64));

    // A closed position keeps its NFT, so the wallet still owns ids worth nothing.
    // Skip them or the open count reports every position ever opened.
    if (liquidity === 0n) continue;

    // Pool key hash for deduplication (use sorted address pair + fee + tickSpacing)
    const poolKeyStr = `${info.currency0}_${info.currency1}_${info.fee}_${info.tickSpacing}_${info.hooks}`;
    poolKeys.set(poolKeyStr, info);

    positions.push({
      tokenId: activeIds[i],
      ...info,
      liquidity,
      poolKeyStr,
    });
  }

  return { positions, poolKeys };
}

// PoolId = keccak256(abi.encode(PoolKey))



function computePoolId(poolKey) {
  // int24 tickSpacing: ABI-encode as int256 (sign-extended to 32 bytes)
  const ts = poolKey.tickSpacing >= 0
    ? BigInt(poolKey.tickSpacing)
    : (1n << 256n) + BigInt(poolKey.tickSpacing); // two's complement
  const encoded = hexAddr(poolKey.currency0)
                + hexAddr(poolKey.currency1)
                + hexU256(BigInt(poolKey.fee))
                + hexU256(ts)
                + hexAddr(poolKey.hooks);
  return keccak256(encoded);
}

async function fetchPoolPrices(poolKeys) {
  const entries = Array.from(poolKeys.entries());
  const calls = entries.map(([, pk]) => ({
    target: STATE_VIEW,
    data: SEL.getSlot0 + computePoolId(pk),
  }));

  const results = await multicall(calls);
  const prices = new Map();

  for (let i = 0; i < entries.length; i++) {
    if (!results[i].success) continue;
    const d = results[i].data;
    const sqrtPriceX96 = decU256(d.slice(0, 64));
    const tick = decInt24(Number(decU256(d.slice(64, 128)) & 0xFFFFFFn));
    prices.set(entries[i][0], { sqrtPriceX96, tick });
  }
  return prices;
}

// ----------------------------- fees -------------------------------------------
// V4 stores a position under keccak256(owner ++ tickLower ++ tickUpper ++ salt),
// packed tightly: 20-byte owner, two 3-byte ticks, 32-byte salt (58 bytes total).
// For PositionManager-held positions the owner is PositionManager and the salt is
// the token id. Not abi.encode - the ticks are 3 bytes here, not padded to 32.
function positionKey(tokenId, tickLower, tickUpper) {
  const t = v => (v & 0xFFFFFF).toString(16).padStart(6, "0");
  return keccak256(
    POSITION_MANAGER.replace("0x", "").toLowerCase()
    + t(tickLower) + t(tickUpper)
    + hexU256(BigInt(tokenId))
  );
}

function hexInt24(v) {
  return hexU256(v >= 0 ? BigInt(v) : U256 + BigInt(v));
}

// Unclaimed fees = liquidity * (feeGrowthInside_now - feeGrowthInside_atLastTouch)
// / 2^128, per token. The subtraction is deliberately allowed to wrap: V4 lets fee
// growth overflow and relies on the difference being correct in modular arithmetic.
async function fetchFees(positions, poolKeys) {
  const calls = [];
  for (const p of positions) {
    const pid = computePoolId(poolKeys.get(p.poolKeyStr));
    calls.push({ target: STATE_VIEW,
      data: SEL.getPositionInfo + pid + positionKey(p.tokenId, p.tickLower, p.tickUpper) });
    calls.push({ target: STATE_VIEW,
      data: SEL.getFeeGrowthInside + pid + hexInt24(p.tickLower) + hexInt24(p.tickUpper) });
  }
  const res = await multicall(calls);
  const out = new Map();
  for (let i = 0; i < positions.length; i++) {
    const info = res[2 * i], grow = res[2 * i + 1];
    if (!info || !grow || !info.success || !grow.success) continue;
    if (info.data.length < 192 || grow.data.length < 128) continue;

    const liq   = decU256(info.data.slice(0, 64));
    const last0 = decU256(info.data.slice(64, 128));
    const last1 = decU256(info.data.slice(128, 192));
    const now0  = decU256(grow.data.slice(0, 64));
    const now1  = decU256(grow.data.slice(64, 128));

    const fee0 = (liq * ((now0 - last0 + U256) % U256)) / Q128;
    const fee1 = (liq * ((now1 - last1 + U256) % U256)) / Q128;
    // The checkpoint itself is kept too: a claim moves it, which is how the
    // claim ledger below sees claims made anywhere, not only from this script.
    out.set(positions[i].tokenId, { fee0, fee1, liq, last0, last1 });
  }
  return out;
}

function feesUsd(pos, raw, poolPrice) {
  if (!raw) return 0;
  const dec0 = (KNOWN_TOKENS[pos.currency0] || { decimals: 18 }).decimals;
  const dec1 = (KNOWN_TOKENS[pos.currency1] || { decimals: 18 }).decimals;
  const p0 = tokenUsdPrice(pos.currency0, pos, poolPrice);
  const p1 = tokenUsdPrice(pos.currency1, pos, poolPrice);
  const u0 = p0 !== null ? (Number(raw.fee0) / Math.pow(10, dec0)) * p0 : 0;
  const u1 = p1 !== null ? (Number(raw.fee1) / Math.pow(10, dec1)) * p1 : 0;
  return u0 + u1;
}

// Unclaimed fees that dwarf the position itself are not fees.
//
// feesUsd is liquidity x (feeGrowthInside_now - feeGrowthInside_atLastTouch) /
// 2^128. The subtraction is modular on purpose, because V4 lets fee growth
// overflow. But if the "last touch" snapshot reads back as ZERO -- a position
// minted moments ago whose info has not settled, or a read that missed -- then
// the difference is no longer a small increment: it is the pool's ENTIRE
// lifetime fee growth, and the result is astronomical.
//
// Seen live on Robinhood Chain: a $1,000 MUSEPAD ladder five minutes old
// reported $60,155,463,546,120,710,... of unclaimed fees and an ROI of 1.5e51%.
// The same numbers had settled to $7.91 and $8.59 twenty-seven minutes later, so
// the state was transient -- but while it lasted it propagated through PnL, the
// ROI and the header totals, making every figure on the screen wrong from one
// bad read.
//
// A real position's unclaimed fees are a fraction of its value; the largest
// measured across this book is about 18%. A hundredfold is therefore nowhere
// near what honest data reaches, and past it the number is an artefact. Reported
// as zero rather than rendered, on the same principle as everything else here:
// prefer showing nothing to showing a confidently wrong number.
const FEES_IMPLAUSIBLE_MULTIPLE = 100;
function plausibleFees(fees, positionUsd) {
  if (!Number.isFinite(fees) || fees < 0) return 0;
  const ceiling = Math.max(1000, (Number(positionUsd) || 0) * FEES_IMPLAUSIBLE_MULTIPLE);
  return fees > ceiling ? 0 : fees;
}

// ----------------------------- claimed fees ledger ----------------------------
// Unrealised PnL counts unclaimed fees, so every claim used to take its fees
// out of the position's PnL even though the money was in the wallet. A wallet
// that claims every few minutes (a copy bot does) showed a PnL near zero.
//
// Any claim, from this script, a bot or another app, moves the position's
// feeGrowthInsideLast checkpoint, and fetchFees already reads it every refresh.
// liquidity x (new - old) / 2^128 is exactly what was collected. The ledger
// keeps, per position, the checkpoint at mint (read once at the mint block),
// the checkpoint last seen, the raw amounts collected between them and their
// dollar value at the price when the claim was first seen. No log or archive
// search: one historical read per position, ever.
//
// Claims made before the position was first seen here are valued at the price
// when they are first seen, not when they happened. A claim between two
// refreshes is valued at the next refresh's price. Unpriceable tokens count
// as zero, like unclaimed fees do.
const CLAIMS_KEY = "claims";
const CLAIMS_KEEP_MS = 90 * 86400 * 1000;   // closed positions stay for realised
function loadClaims() {
  const c = loadJson(CLAIMS_KEY);
  return c && c.v === 1 && c.pos ? c : { v: 1, pos: {} };
}
function saveClaims(c) { saveJson(CLAIMS_KEY, c); }

function _claimWords(hex) {
  const h = String(hex || "").replace(/^0x/, "");
  return h.length >= 192 ? [0, 1, 2].map(i => BigInt("0x" + h.slice(i * 64, i * 64 + 64))) : null;
}

// Mint-block checkpoints for positions the ledger has not seen, one batch.
async function fetchMintCheckpoints(items) {
  const out = new Map();
  if (!items.length) return out;
  const values = await histMany(items.map(it => _histCall(STATE_VIEW,
    "0x" + SEL.getPositionInfo + it.pid + positionKey(it.pos.tokenId, it.pos.tickLower, it.pos.tickUpper),
    it.block)));
  items.forEach((it, i) => {
    const w = _claimWords(values[i]);
    // A position that did not exist yet, or a missed read, reads as zeros.
    if (w && w[0] > 0n) out.set(String(it.pos.tokenId), { liq: w[0], last0: w[1], last1: w[2] });
  });
  return out;
}

// A position met for the first time may already have been claimed from and
// resized many times (a copy bot compounds every few minutes). Its ledger entry
// is then built by replaying its history instead of from the mint checkpoint
// alone: the wallet's PositionManager transactions since the mint (explorer),
// the ModifyLiquidity events of this position in their receipts, and at each
// event block the position's exact size and fee checkpoint. Every touch
// collects the fees accrued since the previous one, on the size held before it,
// valued at that block's price; every size change is capital added or returned
// at that block's price. Exact rather than estimated; paid once per position,
// in the app only. Returns null when the history cannot be read completely.
async function replayPositionHistory(pos, pid, mintBlock) {
  const url = explorerUrl(`/api?module=account&action=txlist&address=${WALLET}`
    + `&startblock=${mintBlock}&endblock=99999999999&page=1&offset=1000&sort=asc`);
  const req = new Request(url);
  req.headers = explorerHeaders();
  req.timeoutInterval = 20;
  const list = ((await req.loadJSON()) || {}).result;
  if (!Array.isArray(list) || list.length >= 1000) return null;
  const hashes = [...new Set(list.filter(t => _lc(t.to) === _lc(POSITION_MANAGER) && String(t.isError) !== "1")
    .map(t => t.hash))];
  const receipts = await histMany(hashes.map(h => ({ method: "eth_getTransactionReceipt", params: [h] })));
  const blocks = new Set();
  for (const rc of receipts) {
    for (const log of (rc && rc.logs) || []) {
      if (_lc(log.address) !== _lc(POOL_MANAGER) || _lc(log.topics && log.topics[0]) !== MODIFY_LIQUIDITY_TOPIC) continue;
      const d = _wordsOf(log.data);
      if (d.length >= 4 && BigInt("0x" + d[3]).toString() === String(pos.tokenId)) blocks.add(Number(rc.blockNumber));
    }
  }
  const order = [...blocks].sort((a, b) => a - b);
  if (!order.length || order[0] !== mintBlock) return null;
  const key = positionKey(pos.tokenId, pos.tickLower, pos.tickUpper);
  const vals = await histMany(order.flatMap(b => [
    _histCall(STATE_VIEW, "0x" + SEL.getPositionInfo + pid + key, b),
    _histCall(STATE_VIEW, "0x" + SEL.getSlot0 + pid, b),
  ]));
  const st = order.map((b, k) => {
    const w = _wordsOf(vals[k * 2]).map(x => BigInt("0x" + x));
    const s0 = _wordsOf(vals[k * 2 + 1]);
    if (w.length < 3 || s0.length < 2) throw new Error("short history read");
    return { L: w[0], last0: w[1], last1: w[2],
             pp: { sqrtPriceX96: BigInt("0x" + s0[0]), tick: decInt24(Number(BigInt("0x" + s0[1]) & 0xFFFFFFn)) } };
  });
  if (!(st[0].L > 0n)) return null;
  const e = { b0: String(st[0].last0), b1: String(st[0].last1), L0: String(st[0].L),
              raw0: "0", raw1: "0", usd: 0, addedUsd: 0, removedUsd: 0, replayed: order.length };
  let raw0 = 0n, raw1 = 0n;
  for (let k = 1; k < st.length; k++) {
    const a = st[k - 1], b = st[k];
    const f0 = a.L * _sub256(b.last0, a.last0) / Q128, f1 = a.L * _sub256(b.last1, a.last1) / Q128;
    raw0 += f0; raw1 += f1;
    e.usd += feesUsd(pos, { fee0: f0, fee1: f1 }, b.pp);
    if (b.L !== a.L) {
      const dL = b.L > a.L ? b.L - a.L : a.L - b.L;
      const v = computePositionValue({ ...pos, liquidity: dL }, b.pp).totalUsd;
      if (b.L > a.L) e.addedUsd += v; else e.removedUsd += v;
    }
  }
  const last = st[st.length - 1];
  return { ...e, raw0: String(raw0), raw1: String(raw1), cp0: String(last.last0), cp1: String(last.last1),
           L: String(last.L), seen: Date.now() };
}

// Runs fn over items, n at a time, starting nothing new once budgetMs has
// passed. Replays used to run one after another: with a copy bot opening
// positions all day that was 90s to 7 min per app open (2026-10-04, 92 NFTs).
async function eachLimited(items, n, budgetMs, fn) {
  const until = Date.now() + budgetMs;
  let next = 0;
  const worker = async () => {
    while (next < items.length && Date.now() < until) {
      const i = next++;
      try { await fn(items[i], i); } catch (e) {}
    }
  };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
}
const REPLAY_PARALLEL = 4, REPLAY_BUDGET_MS = 90 * 1000;

// Brings the ledger up to date and returns tokenId -> ledger entry (usd is the
// claimed total; addedUsd/removedUsd are size changes; L0 the minted size). Never
// throws: a failure leaves the ledger as it was and PnL without claims.
// quick: no replays and no new entries, only what the fee reads already in hand
// can book. The table shows PnL from that first; the full pass runs after it.
// The returned map's .pending counts positions the full pass still has work for.
async function updateClaims(positions, poolKeys, feeRaw, poolPrices, mintBlocks, valueUsdOf, quick) {
  const claimed = new Map();
  claimed.pending = 0;
  try {
    const ledger = loadClaims();
    const light = config.runsInWidget || !!quick;
    let changed = false;
    const missing = [];
    for (const pos of positions) {
      const id = String(pos.tokenId);
      if (!ledger.pos[id] && feeRaw.get(pos.tokenId) && mintBlocks[id]) {
        missing.push({ pos, pid: computePoolId(poolKeys.get(pos.poolKeyStr)), block: mintBlocks[id] });
      }
    }
    // New entries are made in the app only: the replay below is too much for a
    // widget's time budget, and a simple entry made there would stop the app
    // from ever replaying that position.
    if (missing.length && light) missing.length = 0;
    // Entries made before replay existed (close-v103 and earlier) counted
    // pre-sighting claims on the minted size at one price. Each open one is
    // replayed once, in the app, and replaced if the replay succeeds.
    // A replay that failed (explorer refusing, rate-limited) is tried again on
    // later app runs, up to three times, rather than given up after one.
    const redo = positions.filter(pos => {
      const id = String(pos.tokenId), e = ledger.pos[id];
      return e && !e.replayed && (e.replayTries || 0) < 3 && mintBlocks[id];
    });
    claimed.pending = redo.length + positions.filter(pos => {
      const id = String(pos.tokenId);
      return !ledger.pos[id] && feeRaw.get(pos.tokenId) && mintBlocks[id];
    }).length;
    if (!light) {
      await eachLimited(redo, REPLAY_PARALLEL, REPLAY_BUDGET_MS, async pos => {
        const id = String(pos.tokenId), e = ledger.pos[id];
        e.replayTries = (e.replayTries || 0) + 1; changed = true;
        const r = await replayPositionHistory(pos, computePoolId(poolKeys.get(pos.poolKeyStr)), mintBlocks[id])
          .catch(() => null);
        if (r) ledger.pos[id] = r;
      });
    }
    // A new position whose replay did not run (out of budget) or failed gets a
    // mint-checkpoint entry below, which is replayed on a later full pass.
    const replayedNew = new Set();
    await eachLimited(missing, REPLAY_PARALLEL, REPLAY_BUDGET_MS, async m => {
      const e = await replayPositionHistory(m.pos, m.pid, m.block).catch(() => null);
      if (e) { ledger.pos[String(m.pos.tokenId)] = e; changed = true; replayedNew.add(m); }
    });
    for (let i = missing.length - 1; i >= 0; i--) if (replayedNew.has(missing[i])) missing.splice(i, 1);
    if (missing.length) {
      const bases = await fetchMintCheckpoints(missing).catch(() => new Map());
      for (const [id, b] of bases) {
        ledger.pos[id] = { b0: String(b.last0), b1: String(b.last1), cp0: String(b.last0), cp1: String(b.last1),
                           L: String(b.liq), L0: String(b.liq), raw0: "0", raw1: "0", usd: 0,
                           addedUsd: 0, removedUsd: 0, seen: Date.now(), replayTries: 1 };
        changed = true;
      }
    }
    for (const pos of positions) {
      const id = String(pos.tokenId);
      const e = ledger.pos[id], info = feeRaw.get(pos.tokenId), pp = poolPrices.get(pos.poolKeyStr);
      if (!e) continue;
      e.seen = Date.now();
      if (info && pp) {
        // Fee growth is a uint256 that wraps by design, and V4 positions often
        // start just below 2^256, so the difference is taken modulo 2^256. A
        // plain subtraction read every wrapped checkpoint as negative and
        // recorded nothing for that position, ever (CASHED, 2026-09-26). A
        // transient bad read (the MUSEPAD zero above) now shows up as an
        // astronomically large difference instead, which the plausibility
        // check below refuses, so the next refresh retries.
        const d0 = _sub256(info.last0, BigInt(e.cp0)), d1 = _sub256(info.last1, BigInt(e.cp1));
        if (d0 > 0n || d1 > 0n) {
          // Fees accrue on the liquidity held before the touch, which is the
          // liquidity the ledger last saw.
          const L = BigInt(e.L);
          const amt = { fee0: L * d0 / Q128, fee1: L * d1 / Q128 };
          const usd = feesUsd(pos, amt, pp);
          if (plausibleFees(usd, valueUsdOf(pos)) === usd) {
            e.raw0 = String(BigInt(e.raw0) + amt.fee0);
            e.raw1 = String(BigInt(e.raw1) + amt.fee1);
            e.usd += usd;
            e.cp0 = String(info.last0); e.cp1 = String(info.last1);
            changed = true;
          }
        }
        // A size change after the fees above were booked on the old size.
        // Money added is capital at that moment's value; money taken out is a
        // return. Seen between two refreshes, so valued at the second one.
        if (String(info.liq) !== e.L && info.liq > 0n) {
          const dL = info.liq - BigInt(e.L);
          const abs = dL < 0n ? -dL : dL;
          const usd = computePositionValue({ ...pos, liquidity: abs }, pp).totalUsd;
          if (Number.isFinite(usd) && plausibleFees(usd, valueUsdOf(pos)) === usd) {
            if (dL > 0n) e.addedUsd = (e.addedUsd || 0) + usd;
            else e.removedUsd = (e.removedUsd || 0) + usd;
            e.L = String(info.liq);
            changed = true;
          }
        }
      }
      claimed.set(pos.tokenId, e);
    }
    const cutoff = Date.now() - CLAIMS_KEEP_MS;
    for (const [id, e] of Object.entries(ledger.pos)) {
      if ((e.seen || 0) < cutoff) { delete ledger.pos[id]; changed = true; }
    }
    if (changed) saveClaims(ledger);
  } catch (e) { /* PnL without claims rather than no PnL */ }
  return claimed;
}


// Supply of each position token, read on-chain, so market cap is supply times an
// address-level reference price rather than any one pool's. DexScreener's
// marketCap cannot be used as is either: it describes
// whichever token DexScreener calls "base", and for some pools here that is USDG,
// which put a stablecoin's cap in a token's column. Supply equals what DexScreener
// implies where it is oriented correctly (checked: identical to 4 significant digits).
// Raw supplies, scaled by supplyUnits once token metadata is in. This runs in
// parallel with discoverTokens, so reading decimals here used whatever the
// registry held at that instant: 18 for a token seen for the first time, which
// put a 6- or 9-decimal token's cap, and its range ends, off by a factor of a
// billion until the next refresh.
async function fetchTotalSupplies(addrs) {
  const out = new Map();
  if (!addrs.length) return out;
  const res = await multicall(addrs.map(a => ({ target: "0x" + a, data: SEL.totalSupply })));
  for (let i = 0; i < addrs.length; i++) {
    const r = res[i];
    if (!r || !r.success || r.data.length < 64) continue;
    out.set(addrs[i], decU256(r.data.slice(0, 64)));
  }
  return out;
}

// Whole-token supplies, with the last good figure standing in for a read that
// failed this time. Supply hardly moves, but a missing one switched the range
// ends from supply x pool price to the cross-pool cap and back again, so the
// same range read as different numbers from one refresh to the next.
function supplyUnits(raw, addrs) {
  const last = loadJson("supplies") || {};
  const out = new Map(), keep = {};
  for (const a of addrs) {
    const dec = (KNOWN_TOKENS[a] || { decimals: 18 }).decimals;
    const v = raw.has(a) ? Number(raw.get(a)) / Math.pow(10, dec) : null;
    if (v != null && Number.isFinite(v) && v > 0) { out.set(a, v); keep[a] = v; }
    else if (last[a] > 0) { out.set(a, last[a]); keep[a] = last[a]; }
  }
  saveJson("supplies", keep);
  return out;
}

// Which side of a pair is the money it is quoted in: USDG first, then ETH or
// WETH. Fixed by address. The grouping used to ask whether that side had a USD
// price this refresh, and ETH only has one when its price loads, so a refresh
// where it did not moved every ETH pair into another row, read upside down.
function quoteRank(a) {
  return a === USDG_ADDR ? 2 : (a === ZERO_TOKEN || a === WETH_ADDR) ? 1 : 0;
}

// The pool a row's range strip is drawn against: the one holding most of the
// row's value, ties to the lower key so the choice never depends on order.
// Each pool has its own price, and pools quoted in different money do not even
// share a tick scale, so the strip cannot mix them. Taking the first position
// seen made the bar, the % and the cap ends follow whichever pool happened to
// come back first, against a range stretched over every pool.
function rangeRefPool(spans) {
  let ref = null;
  for (const sp of spans) {
    if (!ref || sp.usd > ref.usd + 1e-9 || (Math.abs(sp.usd - ref.usd) <= 1e-9 && sp.key < ref.key)) ref = sp;
  }
  return ref;
}

// ----------------------------- wallet + market --------------------------------
// Native ETH, WETH and USDG sitting in the wallet, in one multicall. Multicall3
// exposes getEthBalance so the native balance batches with the ERC20 reads instead
// of needing its own eth_getBalance round trip.

// Tokens sitting loose in the wallet, valued.
//
// A close that could not sell, a swap refused on price, a claim of a token with
// no market: all of them leave real value in the wallet that no line on this
// screen counted, so net worth silently understated what was held. The list is
// whatever the positions have touched, which is exactly where leftovers come
// from, and balances are one multicall. Only the few with a balance are priced,
// so the usual cost is one round trip and nothing else.
async function fetchLeftoverTokens(wallet) {
  const addrs = knownTokens().filter(a =>
    a && a !== USDG_ADDR && a !== WETH_ADDR && a !== ZERO_TOKEN);
  if (!addrs.length) return { usd: 0, items: [] };

  let held = [];
  try {
    const r = await multicall(addrs.map(a => (
      { target: "0x" + a, data: SEL.balanceOf + hexAddr(wallet) })));
    for (let i = 0; i < addrs.length; i++) {
      if (!r[i] || !r[i].success || r[i].data.length < 64) continue;
      const raw = decU256(r[i].data.slice(0, 64));
      if (raw === 0n || Number(raw) === 0) continue;
      held.push({ addr: addrs[i], raw });
    }
  } catch (e) { return { usd: 0, items: [], failed: true }; }
  if (!held.length) return { usd: 0, items: [] };

  const items = [];
  await Promise.all(held.map(async h => {
    try {
      const dec = await decOfAddr(h.addr);
      const amt = Number(h.raw) / Math.pow(10, dec);
      // The same multi-pool reference the sell guard uses, so a token is valued
      // the way it would actually be sold rather than by whichever pool shouts
      // loudest.
      const ref = await fetchReferencePrice(h.addr, null);
      if (!ref || !(ref.px > 0)) return;
      items.push({ addr: h.addr, amt, px: ref.px, usd: amt * ref.px,
                   name: (KNOWN_TOKENS[h.addr] || {}).symbol || h.addr.slice(0, 6) });
    } catch (e) { /* an unpriceable leftover is not counted, not guessed */ }
  }));
  items.sort((a, b) => b.usd - a.usd);
  return { usd: items.reduce((s, i) => s + i.usd, 0), items };
}

async function fetchWalletBalances(wallet) {
  const calls = [
    { target: MULTICALL3,      data: SEL.getEthBalance + hexAddr(wallet) },
    { target: "0x" + USDG_ADDR, data: SEL.balanceOf    + hexAddr(wallet) },
    { target: "0x" + WETH_ADDR, data: SEL.balanceOf    + hexAddr(wallet) },
  ];
  const r = await multicall(calls);
  const read = (i, dec) => (r[i] && r[i].success && r[i].data.length >= 64)
    ? Number(decU256(r[i].data.slice(0, 64))) / Math.pow(10, dec) : 0;
  const base = { eth: read(0, 18), usdg: read(1, 6), weth: read(2, 18) };
  // Loose tokens are part of what the wallet is worth, so they are read here
  // rather than bolted on at one call site and forgotten at the others.
  const left = await fetchLeftoverTokens(wallet).catch(() => ({ usd: 0, items: [] }));
  return { ...base, shitters: left.usd, shitterList: left.items };
}

// Market data for the exact pools the positions sit in.
//
// DexScreener uses the V4 poolId as its pair address, so the pools can be looked
// up by the same id computed for the on-chain reads. That matters because volume
// is per pool, not per token: a token's deepest pool is often not the one holding
// the position, and quoting its volume describes a market the position is not in.
async function fetchPoolMarketData(poolIds) {
  const out = new Map();
  if (!poolIds.length) return out;
  const req = new Request(DEX_PAIRS + poolIds.map(id => "0x" + id).join(","));
  req.headers = { "Accept": "application/json" };
  req.timeoutInterval = 8;
  const j = await req.loadJSON();
  const pairs = (j && j.pairs) || [];
  for (const p of pairs) {
    if (!p || !p.pairAddress) continue;
    out.set(p.pairAddress.replace("0x", "").toLowerCase(), {
      liq: (p.liquidity && Number(p.liquidity.usd)) || 0,
      priceUsd: Number(p.priceUsd) || null,
      priceNative: Number(p.priceNative) || null,
      quoteSymbol: (p.quoteToken && p.quoteToken.symbol) || "",
      baseAddr: ((p.baseToken && p.baseToken.address) || "").replace("0x", "").toLowerCase(),
      mcap: Number(p.marketCap || p.fdv) || null,
      volH1: (p.volume && Number(p.volume.h1)) || 0,
    });
  }
  return out;
}

// USD price of native ETH. An ETH-quoted pool already implies it (priceUsd is the
// base token in dollars, priceNative the same token in ETH), so derive it for free
// when such a pool is present and only fall back to a WETH lookup otherwise.
function ethPriceFromMarket(market) {
  for (const [, m] of market) {
    if (!/^W?ETH$/i.test(m.quoteSymbol)) continue;
    if (m.priceUsd && m.priceNative) return m.priceUsd / m.priceNative;
  }
  return null;
}
async function fetchEthPrice() {
  const req = new Request(DEX_TOKENS + "0x" + WETH_ADDR);
  req.headers = { "Accept": "application/json" };
  req.timeoutInterval = 8;
  const j = await req.loadJSON();
  const pairs = Array.isArray(j) ? j : (j && j.pairs) || [];
  let best = null;
  for (const p of pairs) {
    const liq = (p.liquidity && Number(p.liquidity.usd)) || 0;
    if (p.baseToken && p.baseToken.address.replace("0x", "").toLowerCase() === WETH_ADDR
        && Number(p.priceUsd) && (!best || liq > best.liq)) best = { liq, price: Number(p.priceUsd) };
  }
  return best ? best.price : null;
}

// ----------------------------- age --------------------------------------------
// Nothing on chain records when a position was minted, so read the mint timestamp
// from the explorer's transfer list. Cached against the id set: it only needs
// refetching when positions change, and age is derived locally from then on.
async function fetchMintTimes(wallet, blocksOut) {
  const url = explorerUrl(`/api?module=account&action=tokennfttx`)
            + `&address=${wallet}&contractaddress=${POSITION_MANAGER}`
            + `&page=1&offset=${MINT_TX_PAGE}&sort=desc`;
  const req = new Request(url);
  req.headers = explorerHeaders();
  req.timeoutInterval = 8;
  const j = await req.loadJSON();
  // "Out of credits" and friends come back as JSON without a list. That is a
  // failure, not a wallet with no mints.
  if (!Array.isArray(j && j.result)) throw new Error("explorer returned no transfer list");
  const rows = j.result;
  const times = {};
  for (const t of rows) {
    if ((t.from || "").toLowerCase() !== ZERO_ADDR) continue;   // mints only
    const id = Number(t.tokenID), ts = Number(t.timeStamp);
    if (Number.isFinite(id) && Number.isFinite(ts)) times[id] = ts;
    // Same row, so the claim ledger's one historical read costs no extra call.
    const bn = Number(t.blockNumber);
    if (blocksOut && Number.isFinite(id) && Number.isSafeInteger(bn) && bn > 0) blocksOut[id] = bn;
  }
  return times;
}

// ----------------------------- V4 math ---------------------------------------
function sqrtPriceFromTick(tick) {
  return Math.sqrt(Math.pow(1.0001, tick));
}

function getAmounts(liquidity, tickLower, tickUpper, tick, sqrtPriceX96) {
  const L = Number(liquidity);
  const sqrtCurrent = Number(sqrtPriceX96) / (2 ** 96);
  const sqrtLower = sqrtPriceFromTick(tickLower);
  const sqrtUpper = sqrtPriceFromTick(tickUpper);

  let amount0 = 0, amount1 = 0;
  if (tick < tickLower) {
    amount0 = L * (1 / sqrtLower - 1 / sqrtUpper);
  } else if (tick < tickUpper) {
    amount0 = L * (1 / sqrtCurrent - 1 / sqrtUpper);
    amount1 = L * (sqrtCurrent - sqrtLower);
  } else {
    amount1 = L * (sqrtUpper - sqrtLower);
  }
  return { amount0: Math.max(0, amount0), amount1: Math.max(0, amount1) };
}

// The token's price from its deepest pool. Every pool has its own tick and so
// its own price; the deepest is the one arbitrage keeps honest, and it is the
// only sane basis for a market cap. Falls back to whatever was recorded first.
// The token's price from its deepest pool anywhere, not just the pools a
// position happens to sit in. Valuing a token by the pool you are in is wrong
// when that pool is thin: CINEMA's thinnest pool above the $10K floor, which the
// zap will happily offer, prices it 1.55x its deepest pool. Positions are often
// opened in exactly those pools, so the market price has to come from outside
// the wallet's own holdings.
// How long a token price is reused before refetching.
const TOKEN_PRICE_TTL_MS = 3 * 60 * 1000;

// A reference price for a sale, built from every pool that quotes the token
// rather than from whichever single pool reports the largest liquidity.
//
// Measured on chain 2026-09-06: GRASS is quoted by 22 pools spanning 18.8%.
// Nearly all of that spread is dust; the four deepest agree within 2.6%. In the
// same measurement the router put 100% of a GRASS sale through a pool holding
// $170, priced 6% above the deep ones, while a $294,000 pool sat beside it. A
// reference taken from the pool the router is already using cannot see that.
//
// So: keep only pools with real depth, drop the ones the route passes through,
// and take the median of what is left. Refuse when the survivors disagree
// materially, because then the market is not telling one story and no single
// number honestly represents it.
const REF_MIN_POOL_LIQUIDITY_USD = 1000;
// Also relative, so one deep pool is not outvoted by a crowd of shallow ones.
const REF_MIN_LIQUIDITY_SHARE = 0.05;
// A pool has to trade to be a witness to the price. Liquidity alone let in
// pools abandoned with a little money left in them: on 2026-09-09 two RSTR/WETH
// pools with $16k and $9.6k of liquidity, doing $5k and $17k of volume against
// $4.1M in the real pools, sat at +54% and +88%, and a STRATTON/DELTA pool with
// $8.6k liquidity and $1.8k volume sat at +50%. Each pushed the spread over the
// limit and refused a sale the Robinhood app filled at the median. Two percent
// of the busiest pool's 24h volume is far below any pool that is part of the
// market.
const REF_MIN_VOLUME_SHARE = 0.02;
// A pool has to be able to PAY for a sale to witness the sell price. DexScreener
// values a pool by both sides at the pool's own price, so a pool holding only
// the token still reports "liquidity". On 2026-09-10 an AA/ETH pool created
// that morning held 8,931,916 AA and 0.0000169 ETH: $38k on paper, four cents
// to sell into, priced 67% above the market. A pool must hold at least this
// share of its value in the paying asset. Healthy pools sit near 50%.
const REF_MIN_QUOTE_SHARE = 0.10;
const REF_MAX_DISAGREEMENT_PERCENT = 5;
// Above this the reference data is not usable at all and no mark can be drawn
// from it. Below it, disagreement is uncertainty rather than absence. Measured
// across every token this wallet has traded, the median spread is 3.5% and the
// upper quartile 4.5%, so refusing at 5% sat inside the normal range and
// declined honest sales on a coin flip: HOTDOG read 5.9% and refused, then
// 1.4% minutes later.
const REF_NO_HONEST_PRICE_PERCENT = 20;

async function fetchReferencePrice(addr, routePools) {
  if (addr === USDG_ADDR) {
    return { px: 1, at: Date.now(), pools: 1, independent: true, spreadPct: 0 };
  }
  let pairs = [];
  try {
    const req = new Request("https://api.dexscreener.com/latest/dex/tokens/0x" + addr);
    req.headers = { "Accept": "application/json" };
    req.timeoutInterval = 10;
    const j = await req.loadJSON();
    pairs = (j && j.pairs) || [];
  } catch (e) { return null; }

  const want = _lc("0x" + addr);
  const cands = [];
  for (const p of pairs) {
    if (p.chainId !== "robinhood") continue;
    const liq = Number((p.liquidity && p.liquidity.usd) || 0);
    if (!(liq > 0)) continue;
    // priceUsd describes the base token; a token that is always the quote side
    // is priced from priceUsd / priceNative instead.
    const usd = Number(p.priceUsd || 0);
    const native = Number(p.priceNative || 0);
    const isBase = _lc(p.baseToken && p.baseToken.address) === want;
    const px = isBase ? usd : (native > 0 ? usd / native : 0);
    if (!Number.isFinite(px) || !(px > 0)) continue;
    // How much of the pool is the asset that would pay us; null when reserves
    // are not reported, and the rule below then stands aside.
    let quoteShare = null;
    const base = Number(p.liquidity && p.liquidity.base), quote = Number(p.liquidity && p.liquidity.quote);
    if (Number.isFinite(base) && Number.isFinite(quote) && native > 0) {
      const tokAmt = isBase ? base : quote, payAmt = isBase ? quote : base;
      const payPx = isBase ? usd / native : usd;
      const tokUsd = tokAmt * px, payUsd = payAmt * payPx;
      if (tokUsd + payUsd > 0) quoteShare = payUsd / (tokUsd + payUsd);
    }
    cands.push({ px, liq, vol: Number((p.volume && p.volume.h24) || 0), quoteShare, pair: _lc(p.pairAddress) });
  }
  if (!cands.length) return null;

  const deepest = cands.reduce((a, c) => Math.max(a, c.liq), 0);
  const floor = Math.max(REF_MIN_POOL_LIQUIDITY_USD, deepest * REF_MIN_LIQUIDITY_SHARE);
  const byDepth = cands.filter(c => c.liq >= floor);
  // ...and by volume, when volume is reported at all. If every pool shows zero
  // the rule cannot say anything and is skipped rather than emptying the set;
  // if it would empty the set on its own, depth alone stands.
  const busiest = cands.reduce((a, c) => Math.max(a, c.vol), 0);
  const trading = busiest > 0 ? byDepth.filter(c => c.vol >= busiest * REF_MIN_VOLUME_SHARE) : byDepth;
  const traded = trading.length ? trading : byDepth;
  // ...and it has to be able to pay. Skipped for a pool that does not report
  // reserves, and never allowed to empty the set on its own.
  const paying = traded.filter(c => c.quoteShare == null || c.quoteShare >= REF_MIN_QUOTE_SHARE);
  const deep = paying.length ? paying : traded;
  const route = new Set((routePools || []).map(_lc));
  const outside = deep.filter(c => !route.has(c.pair));

  // Prefer pools the route does not touch. Falling back to the routed pool is
  // still better than no reference at all, but it is not independent and the
  // log says so rather than implying a check that did not happen.
  const use = outside.length ? outside : deep.length ? deep : cands;
  const independent = outside.length > 0;

  // Every pool votes with the money sitting in it, not with one vote each.
  //
  // On 2026-09-10 AA had three USDG pools holding $239k that agreed within 2%,
  // and one AA/ETH pool created that day holding $38k at +67%. The mark was
  // right; the disagreement measure was not. With four pools it took the full
  // range, and one pool made that 69%, so a sale the market priced within 2%
  // was refused. RSTR and STRATTON the day before were the same shape with
  // staler pools. Quartiles over the pool COUNT needed five or more pools to
  // mean anything and were still one vote per pool; quartiles over the
  // LIQUIDITY mean the same thing at any count: a $38k pool can move the price
  // of the top 14% of the money and nothing else. Two equal pools that
  // genuinely disagree by 22% still read as 22% and are still refused.
  const byPx = use.slice().sort((a, b) => a.px - b.px);
  const totalLiq = byPx.reduce((s, c) => s + c.liq, 0);
  // The lowest price at which at least this share of the liquidity sits.
  const wq = (p) => {
    let acc = 0;
    for (const c of byPx) { acc += c.liq; if (acc / totalLiq >= p) return c.px; }
    return byPx[byPx.length - 1].px;
  };
  const px = wq(0.5);
  const spreadPct = byPx.length < 2 ? 0 : (wq(0.75) - wq(0.25)) / px * 100;
  // The unweighted full range, kept for the log line: it is the number a
  // person would compute by eye, and seeing both is what makes a refusal
  // explicable.
  const rangePct = byPx.length > 1 ? (byPx[byPx.length - 1].px - byPx[0].px) / px * 100 : 0;

  return { px, at: Date.now(), pools: byPx.length, independent, spreadPct, rangePct,
           disagrees: byPx.length > 1 && spreadPct > REF_MAX_DISAGREEMENT_PERCENT };
}

// Pool identifiers the quote says it will trade through. They match the pair
// addresses the price source reports, which is what makes exclusion possible.
function routePoolsOf(quote) {
  const out = [];
  const route = quote && quote.quote && quote.quote.route;
  for (const leg of route || []) {
    for (const hop of (Array.isArray(leg) ? leg : [leg])) {
      if (hop && hop.address) out.push(_lc(hop.address));
    }
  }
  return out;
}

// ---------------------- market cap: one answer per address --------------------
// A market cap belongs to a token contract, never to a symbol or to one pool.
// Every row for one address must show the same figure, including when the chain
// reads fail.
//
// Pools of a single token disagree badly. Measured 2026-09-06: one PEZ contract's
// pools spanned 137%, one UNIPCS contract's 215%, STRATTON's 7,613%. Taking the
// single deepest pool by reported liquidity inherits all of that, and reported
// liquidity is not executable depth near a concentrated tick.
//
// So admit pools with real depth, take the unweighted median of the survivors,
// and report the spread rather than hiding it. Liquidity admits a pool; it never
// weights one, because weighting would give an imperfect measurement more
// authority than it has.
//
// Unlike the swap guard this never refuses: a table has to show something. It
// shows the midpoint and marks it uncertain.
const MCAP_MIN_POOL_LIQUIDITY_USD = 1000;
const MCAP_MIN_LIQUIDITY_SHARE = 0.05;
const MCAP_MAX_DISAGREEMENT_PERCENT = 5;

// candidates: [{ pairId, tokenAddr, px, liq }] -> { px, lowPx, highPx, pools, spreadPct, disagrees }
// Pure, and independent of the order candidates arrive in.
function mcapReference(candidates) {
  const ok = [];
  for (const c of candidates || []) {
    const px = Number(c && c.px);
    const liq = Number(c && c.liq);
    if (!Number.isFinite(px) || !(px > 0)) continue;
    if (!Number.isFinite(liq) || !(liq > 0)) continue;
    ok.push({ px, liq });
  }
  if (!ok.length) return null;
  const deepest = ok.reduce((a, c) => (c.liq > a ? c.liq : a), 0);
  const floor = Math.max(MCAP_MIN_POOL_LIQUIDITY_USD, deepest * MCAP_MIN_LIQUIDITY_SHARE);
  const kept = ok.filter(c => c.liq >= floor);
  // Nothing clears the floor only when every pool is dust; then the dust is all
  // there is, and saying so with a wide spread beats saying nothing.
  const use = kept.length ? kept : ok;
  const s = use.map(c => c.px).sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  const px = s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  const lowPx = s[0];
  const highPx = s[s.length - 1];
  const spreadPct = lowPx > 0 ? (highPx / lowPx - 1) * 100 : 0;
  return {
    px, lowPx, highPx, pools: s.length, spreadPct,
    disagrees: s.length > 1 && spreadPct > MCAP_MAX_DISAGREEMENT_PERCENT,
  };
}

// The last six hex characters of a contract address. A symbol is not an
// identity: UNIPCS is at least four separate contracts on this chain, and the
// public search caps at 30 pairs so colliding contracts can be invisible. Shown
// on every non-major token rather than only when a collision is detected,
// because a collision that is hidden cannot be detected.
function addrTag(addr) {
  const a = String(addr == null ? "" : addr).replace(/^0x/, "");
  return a.length >= 6 ? "…" + a.slice(-6) : "";
}

async function fetchTokenMarketPrices(tokenAddrs, maxAgeMs) {
  const out = new Map();
  // One token per request: the /tokens/ endpoint returns at most 30 pairs
  // whatever it is asked for, so batching answered for only a handful of them.
  //
  // That made every table refresh cost one request per held token, which is
  // what turned an 0.3s load into 18s. Prices are cached on disk for
  // TOKEN_PRICE_TTL_MS, so a refresh a minute later spends nothing: a market
  // cap does not move enough in two minutes to be worth the wait, and a stale
  // price is still far better than the per-pool figure it replaced.
  // The table is happy with a price from a few minutes ago. A trade is not, and
  // asks for a much shorter window; the entries carry their age so the caller
  // can check it again later, after approvals have taken their time.
  const ttl = Number.isFinite(maxAgeMs) ? maxAgeMs : TOKEN_PRICE_TTL_MS;
  const now = Date.now();
  let store = {};
  try { store = loadJson("token_prices") || {}; } catch (e) {}
  const list = [];
  for (const a of new Set(tokenAddrs)) {
    if (!a || a === ZERO_TOKEN) continue;
    const hit = store[a];
    if (hit && now - hit.t < ttl) out.set(a, { px: hit.px, liq: hit.liq, t: hit.t, ref: hit.ref || null });
    else list.push(a);
  }
  if (!list.length) return out;
  await Promise.all(list.map(async addr => {
    try {
      const req = new Request("https://api.dexscreener.com/latest/dex/tokens/0x" + addr);
      req.headers = { "Accept": "application/json" };
      req.timeoutInterval = 10;
      const j = await req.loadJSON();
      // Every qualified pool of this token, so a market cap can be a median over
      // them rather than whatever the single deepest one happens to say.
      const cands = [];
      for (const p of (j && j.pairs) || []) {
        if (p.chainId !== "robinhood") continue;
        // priceUsd describes the BASE token. A token that is always the quote
        // side (SPY is, since USDG sorts as base against it) gets no entry at
        // all on a base-only lookup, and then falls back to a per-pool figure.
        // Derive the quote side from priceNative, the base's price in quote
        // units. Addresses here are stored without the 0x prefix.
        const liq = Number((p.liquidity && p.liquidity.usd) || 0);
        if (!(liq > 0)) continue;
        const usd = Number(p.priceUsd || 0);
        const native = Number(p.priceNative || 0);

        const base = (p.baseToken && p.baseToken.address || "").replace(/^0x/, "").toLowerCase();
        if (base && usd > 0) {
          const prev = out.get(base);
          if (!prev || liq > prev.liq) out.set(base, { px: usd, liq, t: Date.now() });
          cands.push({ pairId: _lc(p.pairAddress), tokenAddr: base, px: usd, liq });
        }
        const quote = (p.quoteToken && p.quoteToken.address || "").replace(/^0x/, "").toLowerCase();
        if (quote && usd > 0 && native > 0) {
          const qpx = usd / native;
          if (Number.isFinite(qpx) && qpx > 0) {
            const prev = out.get(quote);
            if (!prev || liq > prev.liq) out.set(quote, { px: qpx, liq, t: Date.now() });
            cands.push({ pairId: _lc(p.pairAddress), tokenAddr: quote, px: qpx, liq });
          }
        }
      }
      // Attach the address-level reference to the token this lookup was for.
      // Only its own candidates: a pair also names a counterparty token, whose
      // other pools this request never saw.
      const mine = cands.filter(c => c.tokenAddr === addr);
      const ref = mcapReference(mine);
      const cur = out.get(addr);
      if (cur && ref) out.set(addr, Object.assign({}, cur, { ref }));
    } catch (e) {}
  }));
  // Persist whatever was just fetched, keeping earlier entries.
  try {
    for (const a of list) {
      const v = out.get(a);
      if (v && v.px > 0) store[a] = { px: v.px, liq: v.liq, t: v.t || now, ref: v.ref || null };
    }
    saveJson("token_prices", store);
  } catch (e) {}
  return out;
}

function pickDeepestPrice(g) {
  if (!g.poolPrices || !g.poolPrices.size) return g.tokenPrice;
  let best = null, bestLiq = -1;
  for (const [pid, px] of g.poolPrices) {
    const liq = (g.pools.get(pid) || {}).liq || 0;
    if (liq > bestLiq) { bestLiq = liq; best = px; }
  }
  return best != null ? best : g.tokenPrice;
}

function tokenUsdPrice(addr, poolKey, poolPrice) {
  const known = KNOWN_TOKENS[addr];
  if (known && known.usd !== null) return known.usd;

  // Derive price from pool: price = (sqrtPriceX96/2^96)^2 adjusted for decimals
  const c0 = KNOWN_TOKENS[poolKey.currency0] || { decimals: 18, usd: null };
  const c1 = KNOWN_TOKENS[poolKey.currency1] || { decimals: 18, usd: null };
  const rawPrice = (Number(poolPrice.sqrtPriceX96) / (2 ** 96)) ** 2;
  // rawPrice = token1/token0 in raw units
  const humanPrice = rawPrice * Math.pow(10, c0.decimals - c1.decimals);
  // humanPrice = how many token1 per 1 token0 in human units

  if (addr === poolKey.currency0) {
    // We want USD price of token0
    // If token1 has a known USD price: price_token0_usd = humanPrice * price_token1_usd
    if (c1.usd !== null) return humanPrice * c1.usd;
    return null;
  } else {
    // We want USD price of token1
    // price_token1_usd = price_token0_usd / humanPrice
    if (c0.usd !== null) return c0.usd / humanPrice;
    return null;
  }
}

// Cost basis without archive access or event logs.
//
// A ladder position is minted single-sided: the range sits entirely to one side of
// the spot price, so the deposit is one token only. The amount of that token needed
// for liquidity L over [lower, upper] depends only on L and the range, not on the
// price at mint, which makes the basis exactly recoverable from current chain state:
//
//   all token0 (price below range):  amount0 = L * (1/sqrt(Pa) - 1/sqrt(Pb))
//   all token1 (price above range):  amount1 = L * (sqrt(Pb) - sqrt(Pa))
//
// Verified against a real 5-position ladder mint: the derived amounts summed to
// 503.593627 USDG against 503.593627 actually spent.
//
// This holds only while the deposit really was single-sided in a token we can price.
// Pairs with no stablecoin side return null so the row shows "--" instead of a
// confidently wrong number.
function initialUsd(pos) {
  const st0 = STABLES[pos.currency0], st1 = STABLES[pos.currency1];
  const stableIs0 = st0 && st0.usd !== null;
  const stableIs1 = st1 && st1.usd !== null;
  if (!stableIs0 && !stableIs1) return null;

  const L = Number(pos.liquidity);
  const sa = sqrtPriceFromTick(pos.tickLower);
  const sb = sqrtPriceFromTick(pos.tickUpper);
  if (!isFinite(L) || !isFinite(sa) || !isFinite(sb) || sa <= 0 || sb <= 0) return null;

  const stable = stableIs0 ? st0 : st1;
  const raw = stableIs0 ? L * (1 / sa - 1 / sb) : L * (sb - sa);
  const amount = raw / Math.pow(10, stable.decimals);
  if (!isFinite(amount) || amount <= 0) return null;
  return amount * stable.usd;
}

function computePositionValue(pos, poolPrice) {
  const { amount0, amount1 } = getAmounts(
    pos.liquidity, pos.tickLower, pos.tickUpper,
    poolPrice.tick, poolPrice.sqrtPriceX96
  );

  const dec0 = (KNOWN_TOKENS[pos.currency0] || { decimals: 18 }).decimals;
  const dec1 = (KNOWN_TOKENS[pos.currency1] || { decimals: 18 }).decimals;
  const human0 = amount0 / Math.pow(10, dec0);
  const human1 = amount1 / Math.pow(10, dec1);

  const price0 = tokenUsdPrice(pos.currency0, pos, poolPrice);
  const price1 = tokenUsdPrice(pos.currency1, pos, poolPrice);

  const usd0 = price0 !== null ? human0 * price0 : 0;
  const usd1 = price1 !== null ? human1 * price1 : 0;

  return {
    amount0: human0, amount1: human1,
    usd0, usd1,
    totalUsd: usd0 + usd1,
    inRange: poolPrice.tick >= pos.tickLower && poolPrice.tick < pos.tickUpper,
  };
}

// ----------------------------- cache -----------------------------------------
const fm = FileManager.local();
// libraryDirectory, not cacheDirectory: the widget runs in its own extension
// process with its own cache directory, and only the library directory is
// shared with the app. With separate caches the widget never sees what an
// in-app run fetched, and the app never sees the widget's.
const cacheDir = fm.libraryDirectory();
function cachePath(suffix) { return fm.joinPath(cacheDir, `rh_uni_v4_${WALLET}_${suffix}.json`); }

function saveJson(suffix, data) {
  try { fm.writeString(cachePath(suffix), JSON.stringify(data)); } catch (e) {}
}
function loadJson(suffix) {
  try {
    const p = cachePath(suffix);
    if (fm.fileExists(p)) return JSON.parse(fm.readString(p));
  } catch (e) {}
  return null;
}

function saveCache(data) { saveJson("data", { t: Date.now(), data }); }
function loadCache() { return loadJson("data"); }

// One-time carry-over of the per-wallet caches earlier versions wrote to
// cacheDirectory, which the widget could not see. Copied where the shared
// directory has no file yet. In-app only: a widget's own old cache is the
// stale one this fixes.
function migrateOldCache() {
  if (config.runsInWidget) return;
  const flag = fm.joinPath(cacheDir, "rh_uni_v4_migrated");
  if (fm.fileExists(flag)) return;
  try {
    const oldDir = fm.cacheDirectory();
    for (const name of fm.listContents(oldDir)) {
      if (!name.startsWith("rh_uni_v4_") || name === "rh_uni_v4_migrated") continue;
      const to = fm.joinPath(cacheDir, name);
      if (!fm.fileExists(to)) fm.copy(fm.joinPath(oldDir, name), to);
    }
    fm.writeString(flag, "1");
  } catch (e) {}
}

function saveActiveIds(ids) { saveJson("ids", { t: Date.now(), ids }); }
function loadActiveIds() {
  const d = loadJson("ids");
  return d ? d.ids : null;
}

function saveMintTimes(times) { saveJson("mints", times); }
function loadMintBlocks() { return loadJson("mint_blocks") || {}; }
function loadMintTimes() { return loadJson("mints") || {}; }

function savePnlBaseline(baseline) { saveJson("pnl_baseline", baseline); }
function loadPnlBaseline() { return loadJson("pnl_baseline") || {}; }

// ----------------------------- formatting ------------------------------------
function fmtUsd(v, signed) {
  if (v == null || isNaN(v)) return "$ --";
  const sign = signed && v > 0 ? "+" : "";
  const abs = Math.abs(v);
  let s;
  if (abs >= 1000) s = abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  else s = abs.toFixed(2);
  return `$${sign}${v < 0 ? "-" : ""}${s}`;
}
// One decimal, for the small value under a row's PnL, matching the other small
// figures in the row. Same sign and grouping rules as fmtUsd.
function fmtUsd1(v, signed) {
  if (v == null || isNaN(v)) return "$ --";
  const sign = signed && v > 0 ? "+" : "";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  return `$${sign}${v < 0 ? "-" : ""}${s}`;
}
// Capital put into realised ladders, compact enough for a narrow subtitle:
// $532, $1.1k, $12k. Unknown or zero capital has no figure at all, never "$0".
function fmtInvested(v) {
  // Under 50 cents would round to "$0": that is no figure, not a zero.
  if (!(Number(v) > 0) || Math.round(v) < 1) return null;
  if (Math.round(v) < 1000) return "$" + Math.round(v);
  if (v < 9950) return "$" + (v / 1000).toFixed(1) + "k";
  return "$" + Math.round(v / 1000) + "k";
}
// Compact dollars for the claimed figure: $24.7, $0.4, $1.2K.
function fmtClaimed(v) {
  if (v >= 1000) return "$" + (v / 1000).toFixed(1) + "K";
  return "$" + v.toFixed(1);
}
function signColor(v) {
  if (v == null || isNaN(v)) return COL.muted;
  return v >= 0 ? COL.green : COL.red;
}

// Cash beside the headline, in one format on every surface: both widgets, both
// in-app tables, both scripts. Net worth counts LP + fees + USDG only, so ETH is
// reported as "+x ETH" rather than folded into the dollar figure: it is gas, not
// capital, and burning it is the cost of acting rather than a change in what the
// book is worth. USDG comes first because it answers "can I open another
// position"; the ETH figure only says whether gas is covered.
// Falls back to the nested wallet object so a cache written by an older build
// still renders instead of showing nothing.
function walletTag(d) {
  const usdg = d.walletUsdg != null ? d.walletUsdg : (d.wallet ? d.wallet.usdg : 0);
  const eth = d.walletEth != null ? d.walletEth
    : (d.wallet ? (d.wallet.eth || 0) + (d.wallet.weth || 0) : 0);
  // Loose tokens are counted in net worth, so the line that breaks net worth
  // down has to name them or the figures do not add up on screen: the header
  // read $4,527.28 while LP + fees + USDG came to $4,495.62, and the missing
  // $31.66 was sitting in the wallet unmentioned.
  const shitters = d.walletShitters != null ? d.walletShitters
    : (d.wallet ? (d.wallet.shitters || 0) : 0);
  const parts = [];
  if (usdg > 0) parts.push(`${fmtUsd(usdg)} USDG`);
  if (shitters > 0) parts.push(`${fmtUsd(shitters)} shitters`);
  if (eth > 0) parts.push(`+${eth.toFixed(4)} ETH`);
  return parts.join("  \u00B7  ");
}
// Market cap and volume need to fit a narrow cell, so 3 significant digits and a
// magnitude suffix: "12.2M", "3.17K".
// Days here are the user's local days, never UTC ones: "today" has to mean the
// day they are actually having. Scriptable runs in the device timezone, so Date
// already reports local values and midnight is the only boundary needed.
function startOfLocalDay(ms) {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}
// Counted between midnights rather than by dividing elapsed milliseconds, so a
// DST change cannot make a day 23 or 25 hours long and file a close under the
// wrong date.
function localDaysAgo(ms, now) {
  return Math.round(
    (startOfLocalDay(now == null ? Date.now() : now) - startOfLocalDay(ms)) / 86400000);
}
function dayLabel(ms, now) {
  const n = localDaysAgo(ms, now);
  if (n === 0) return "TODAY";
  if (n === 1) return "YESTERDAY";
  return new Date(ms)
    .toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })
    .toUpperCase();
}
// Clock time in the device's own format, plus how long ago that was.
function fmtWhen(ms, now) {
  return `${fmtTime(ms)}  \u00B7  ${fmtAge(((now == null ? Date.now() : now) - ms) / 86400000)} ago`;
}

// ---------------------------- realised PnL ------------------------------------
// Unrealised PnL above is what open liquidity is worth right now. This is the
// other half: what closed ladders actually returned.
//
//   capital = value deposited at mint
//   revenue = principal removed at close + every fee the position earned
//   pnl     = revenue - capital
//
// A closed position no longer exists on chain, so every read here carries an
// explicit block number. Ported from tools/realized-pnl-reference.mjs, whose
// figures for this wallet are the regression target.
const REALISED_SCHEMA = 5;
// Robinhood Chain produces a block roughly every 0.102s. Only used to date a
// snapshot written before mint timestamps were stored, so that an existing
// history shows how long its positions were open without being rebuilt.
const APPROX_BLOCK_MS = 102;
// USDG_ADDR is stored without a 0x prefix; addresses decoded from call data
// carry one. Comparing the two forms silently matched nothing.
// Lowercased on the way in: every address this module compares it against is
// decoded from a call result and therefore lowercase, and the host scripts do
// not agree on how they case their own address constants. Written out rather
// than calling _lc, which is declared below this line and would be in its
// temporal dead zone at module load.
const USDG_0X = ("0x" + USDG_ADDR).toLowerCase();
const MODIFY_LIQUIDITY_TOPIC =
  "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec";
const Q96 = 1n << 96n;
// Historical reconstruction is far heavier than a price refresh, so it runs
// only in the app and only against the keyed endpoint.
const HIST_BATCH = 12;
const HIST_PACE_MS = 150;
const HIST_TIMEOUT_SEC = 30;

const _lc = v => String(v == null ? "" : v).toLowerCase();
const _wordsOf = v => String(v == null ? "" : v).replace(/^0x/, "").match(/.{64}/g) || [];
const _padWord = v => String(v).replace(/^0x/, "").padStart(64, "0");
const _intWord = v => {
  const n = BigInt(v);
  return _padWord((n < 0n ? U256 + n : n).toString(16));
};
const _addrWord = w => ("0x" + String(w).slice(24)).toLowerCase();
// Fee growth is a uint256 counter that wraps. A plain subtraction on a wrapped
// pair yields an astronomically large fee instead of a small one.
const _sub256 = (now, before) => (now >= before ? now - before : U256 - before + now);

function _groupBy(items, keyOf) {
  const out = new Map();
  for (const item of items) {
    const k = keyOf(item);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(item);
  }
  return out;
}

// A batched, block-tagged eth_call. Archive reads are the whole basis of this
// calculation, so they go to the keyed endpoint; the public nodes do not serve
// them reliably.
async function histRpc(payloads) {
  if (!payloads.length) return [];
  if (!RPC_KEYED) throw new Error("ALCHEMY_API_KEY is required for realised PnL");
  const body = payloads.map((p, i) => ({ jsonrpc: "2.0", id: i + 1, method: p.method, params: p.params }));
  let lastErr = null;
  for (let attempt = 0; attempt < 8; attempt++) {
    if (attempt) await pause(Math.min(5000, RPC_BACKOFF_MS * Math.pow(2, attempt - 1)));
    try {
      const req = new Request(RPC_KEYED);
      req.method = "POST";
      req.headers = { "Content-Type": "application/json" };
      req.body = JSON.stringify(body);
      req.timeoutInterval = HIST_TIMEOUT_SEC;
      const text = await req.loadString();
      if (!text || text[0] !== "[" && text[0] !== "{") {
        lastErr = new Error("rate limited");
        await pause(1200 * (attempt + 1));
        continue;
      }
      const rows = JSON.parse(text);
      const list = Array.isArray(rows) ? rows : [rows];
      const byId = new Map(list.map(r => [r.id, r]));
      return body.map(p => {
        const r = byId.get(p.id);
        if (!r) throw new Error("missing response in batch");
        if (r.error) throw new Error(String(r.error.message || "RPC error"));
        return r.result;
      });
    } catch (e) { lastErr = e; }
  }
  throw new Error(String((lastErr && lastErr.message) || "RPC unavailable"));
}

async function histMany(payloads, onProgress) {
  const out = [];
  for (let i = 0; i < payloads.length; i += HIST_BATCH) {
    if (i) await pause(HIST_PACE_MS);
    out.push(...await histRpc(payloads.slice(i, i + HIST_BATCH)));
    if (onProgress) onProgress(Math.min(payloads.length, i + HIST_BATCH), payloads.length);
  }
  return out;
}

const _histCall = (to, data, block) => ({
  method: "eth_call", params: [{ to, data }, "0x" + Number(block).toString(16)],
});

// Every position NFT this wallet has ever held. A page that fails is an error,
// never an empty page: silently dropping one understates capital, which looks
// like a plausible number rather than a fault.
// Walk the explorer's ERC-721 transfers, newest first, stopping as soon as the
// caller says it has what it needs.
//
// Reading to the end of history was the whole cost of a first run: ten pages at
// several seconds each. Reading only as far as today would be wrong in the other
// direction, because valuing a position closed today means reading chain state
// at the block it was MINTED, which can be weeks earlier, and a lifecycle whose
// mint was never seen is silently dropped rather than reported. So the stopping
// point is neither "the end" nor "today": it is the moment every position that
// closed in the day being built has had its own mint found.
//
// `startParams` resumes from where a previous scan stopped, so extending
// coverage backwards does not re-read the pages already held.
async function fetchPositionNftTransfers(wallet, asOfBlock, onProgress, opts) {
  const o = opts || {};
  const all = [];
  let next = o.startParams && Object.keys(o.startParams).length ? o.startParams : {};
  let page = 0;
  let exhausted = false;
  let stopped = false;
  let reachedCache = false;
  do {
    page++;
    const extra = Object.keys(next).map(k => `&${encodeURIComponent(k)}=${encodeURIComponent(next[k])}`).join("");
    const url = explorerUrl(`/api/v2/addresses/${wallet}/token-transfers?type=ERC-721`) + extra;
    let body = null;
    for (let attempt = 0; attempt < 9 && body == null; attempt++) {
      if (attempt) await pause(Math.min(6000, 500 * Math.pow(2, attempt - 1)));
      try {
        const req = new Request(url);
        req.headers = explorerHeaders();
        req.timeoutInterval = 45;
        const text = await req.loadString();
        const status = req.response ? req.response.statusCode : 200;
        if (status !== 200 || !text) continue;
        const parsed = JSON.parse(text);
        if (Array.isArray(parsed.items)) body = parsed;
      } catch (e) { /* retry */ }
    }
    if (body == null) throw new Error(`explorer would not serve NFT page ${page}`);
    for (const it of body.items) {
      if (Number(it.block_number) <= asOfBlock) all.push(it);
    }
    next = body.next_page_params || {};
    if (onProgress) onProgress(page, all.length);
    exhausted = Object.keys(next).length === 0;
    if (exhausted) break;
    if (o.enough && o.enough(all.filter(isPositionTransfer))) { stopped = true; break; }
    // Reached ground the cache already covers. Everything below this point is
    // held, so carrying on would re-read pages only to merge them away; the
    // caller resumes from the cache's own oldest edge instead.
    if (o.stopAtBlock != null && body.items.length
        && Number(body.items[body.items.length - 1].block_number) < o.stopAtBlock) {
      reachedCache = true;
      break;
    }
  } while (page < 60);
  const included = all.filter(isPositionTransfer);
  const oldestBlock = all.reduce((a, it) => Math.min(a, Number(it.block_number)), Infinity);
  return { total: all.length, included, pages: page, exhausted, stopped, reachedCache,
           oldestBlock, nextParams: next };
}

function isPositionTransfer(it) {
  return _lc(it.token && it.token.address_hash) === _lc(POSITION_MANAGER);
}

// True once every position that closed inside [dayStart, dayEnd) has had its own
// mint found, and the scan has reached back past the start of the day so no
// further closes from it can appear. Deliberately conservative: returning true
// early does not raise an error, it drops a position and quietly understates the
// day.
// `scanned` is what this scan actually walked, `records` is that merged with
// the cache. The two are not interchangeable and mixing them was a real bug.
//
// Whether the scan may stop is a question about contiguity: has it walked back
// past the start of the day, so no further closes from that day can appear
// below? Only the live scan can answer it. Asking the merged set instead let a
// single unrelated cache entry from weeks ago satisfy "oldest < dayStart"
// immediately, so a scan that had covered the last two hours of a day declared
// the day complete. Yesterday reported two closes out of six.
//
// Content is a different question, and there the cache is welcome: a mint from
// months ago is exactly what it is for.
function haveEnoughFor(scanned, records, wallet, dayStart, dayEnd) {
  let oldest = Infinity;
  const burned = new Set(), minted = new Set();
  for (const it of scanned) {
    const t = Date.parse(it.timestamp) || 0;
    if (t > 0 && t < oldest) oldest = t;
  }
  for (const it of records) {
    const t = Date.parse(it.timestamp) || 0;
    const id = String(it.total && it.total.token_id);
    if (it.type === "token_minting" && _lc(it.to && it.to.hash) === _lc(wallet)) minted.add(id);
    else if (it.type === "token_burning" && _lc(it.from && it.from.hash) === _lc(wallet)
             && t >= dayStart && t < dayEnd) burned.add(id);
  }
  // Past the start of the day, so no further closes from it can turn up.
  if (oldest >= dayStart) return false;
  // Every close in the day has its own mint, without which it cannot be valued
  // and would be dropped rather than reported.
  for (const id of burned) if (!minted.has(id)) return false;
  return true;
}

// Reading deeper only to name the next day is its own request, and only made
// when the user asks for older history. On a quiet wallet the nearest older
// close can be weeks back, and paying for that on every ordinary refresh was
// the whole cost of looking at a dormant wallet.
function haveOlderCloseThan(records, wallet, before) {
  for (const it of records) {
    if (it.type !== "token_burning") continue;
    if (_lc(it.from && it.from.hash) !== _lc(wallet)) continue;
    const t = Date.parse(it.timestamp) || 0;
    if (t > 0 && t < before) return true;
  }
  return false;
}

// The discovery cache. Only the fields buildLifecycles reads are kept, so the
// file stays small enough to load on every run.
// Bumped to 3 on 2026-09-08. Every cache written before then was built by a
// scan that could stop before it had covered the day it was reading, so any of
// them may be short in the middle. There is no way to tell a truncated cache
// from a complete one after the fact, so they are all discarded once: the next
// build walks the history again and everything after it is incremental.
const TRANSFER_CACHE_SCHEMA = 3;
function packTransfer(it) {
  return {
    b: Number(it.block_number), t: it.type, h: _lc(it.transaction_hash),
    i: Number(it.log_index), s: it.timestamp,
    d: String(it.total && it.total.token_id),
    to: _lc(it.to && it.to.hash), f: _lc(it.from && it.from.hash),
  };
}
function unpackTransfer(p) {
  return {
    block_number: p.b, type: p.t, transaction_hash: p.h, log_index: p.i,
    timestamp: p.s, total: { token_id: p.d },
    to: { hash: p.to }, from: { hash: p.f },
  };
}
// Forget the cached transfer scan, so the next build re-walks history from the
// top. The cache is what makes an ordinary refresh cheap, and also what makes a
// gap in it permanent: discovery only sees the transfers it holds, so a mint it
// never reached is a close that can never be valued, on every later refresh. The
// full rebuild exists precisely for that case.
function clearTransferCache() {
  try { saveJson("realised_transfers", null); } catch (e) {}
}
function loadTransferCache(wallet) {
  let c = null;
  try { c = loadJson("realised_transfers"); } catch (e) {}
  if (!c || c.schema !== TRANSFER_CACHE_SCHEMA) return null;
  if (_lc(c.wallet) !== _lc(wallet) || !Array.isArray(c.items)) return null;
  return c;
}
// The cache covers a contiguous range of history, newest first. `complete` says
// whether it reaches all the way back; when it does not, `nextParams` is the
// cursor that continues from its oldest edge, so extending coverage never
// re-reads a page already held.
function saveTransferCache(wallet, packed, complete, nextParams, coveredToBlock, coveredPages) {
  const newestBlock = packed.reduce((a, p) => (p.b > a ? p.b : a), 0);
  const oldestBlock = packed.reduce((a, p) => (p.b < a ? p.b : a), Infinity);
  try {
    saveJson("realised_transfers", {
      schema: TRANSFER_CACHE_SCHEMA, wallet: _lc(wallet),
      newestBlock, oldestBlock: Number.isFinite(oldestBlock) ? oldestBlock : 0,
      // How deep the scan has actually been, over every transfer rather than
      // only the position ones: it is what the cursor continues from.
      coveredToBlock: Number.isFinite(coveredToBlock) ? coveredToBlock : 0,
      // And how many pages that depth is, which is what lets a resumed scan
      // report the page it is really on. Optional: a cache written before this
      // existed still loads, and fills the field in on its next save, so the
      // schema does not move for it.
      coveredPages: Number.isFinite(coveredPages) ? coveredPages : null,
      complete: !!complete, nextParams: nextParams || null, items: packed,
    });
  } catch (e) { /* the cache is an optimisation; losing it costs time, not truth */ }
}
// Newest-first, deduped by transaction and log index. A page fetched again
// because it straddles the cache boundary must not produce a second copy.
function mergeTransfers(fresh, cached) {
  const byKey = new Map();
  for (const p of cached) byKey.set(`${p.h}:${p.i}`, p);
  for (const p of fresh) byKey.set(`${p.h}:${p.i}`, p);
  return [...byKey.values()].sort((a, b) => b.b - a.b || b.i - a.i);
}

// Mint and burn per token id. An NFT with no burn is still open.
function buildLifecycles(transfers, wallet) {
  const byId = _groupBy(transfers, it => String(it.total && it.total.token_id));
  const out = [];
  for (const [positionId, history] of byId) {
    if (!positionId || positionId === "undefined") continue;
    const mint = history.find(it => it.type === "token_minting" && _lc(it.to && it.to.hash) === wallet);
    if (!mint) continue;
    const burn = history.find(it => it.type === "token_burning" && _lc(it.from && it.from.hash) === wallet);
    out.push({
      positionId,
      mintBlock: Number(mint.block_number),
      mintTx: _lc(mint.transaction_hash),
      burnBlock: burn ? Number(burn.block_number) : null,
      burnTx: burn ? _lc(burn.transaction_hash) : null,
      // The explorer already stamps every transfer, so the close time costs no
      // extra call. Without it, dating a close would mean fetching each burn
      // block's header.
      mintAt: Date.parse(mint.timestamp) || null,
      burnAt: burn ? (Date.parse(burn.timestamp) || null) : null,
    });
  }
  return out;
}

// Pool key, range and liquidity, each read at the block the NFT was minted.
async function hydratePositionDefinitions(lifecycles, onProgress) {
  const calls = [];
  for (const row of lifecycles) {
    const idWord = _padWord(BigInt(row.positionId).toString(16));
    calls.push(_histCall(POSITION_MANAGER, "0x" + SEL.getPoolAndPositionInfo + idWord, row.mintBlock));
    calls.push(_histCall(POSITION_MANAGER, "0x" + SEL.getPositionLiquidity + idWord, row.mintBlock));
  }
  const values = await histMany(calls, onProgress);
  const kept = [];
  for (let i = 0; i < lifecycles.length; i++) {
    const row = lifecycles[i];
    const info = _wordsOf(values[i * 2]);
    if (info.length < 6) continue;
    const packed = BigInt("0x" + info[5]);
    row.key = {
      currency0: _addrWord(info[0]),
      currency1: _addrWord(info[1]),
      fee: Number(BigInt("0x" + info[2])),
      tickSpacing: Number(BigInt.asIntN(24, BigInt("0x" + info[3]))),
      hooks: _addrWord(info[4]),
    };
    // A non-USDG pool would need its own historical USD source. Excluded rather
    // than valued at zero, which would quietly flatter the total.
    if (row.key.currency0 !== USDG_0X && row.key.currency1 !== USDG_0X) continue;
    row.tickLower = Number(BigInt.asIntN(24, (packed >> 8n) & 0xffffffn));
    row.tickUpper = Number(BigInt.asIntN(24, (packed >> 32n) & 0xffffffn));
    row.liquidity = BigInt(values[i * 2 + 1] || "0x0");
    row.poolId = computePoolId(row.key);
    kept.push(row);
  }
  return kept;
}

// Token address is identity; the symbol is display only and can change. One
// token here traded as MEME and later as AMC.
async function fetchHistTokenMetadata(lifecycles) {
  const firstSeen = new Map();
  for (const row of lifecycles) {
    const tok = row.key.currency0 === USDG_0X ? row.key.currency1 : row.key.currency0;
    if (!firstSeen.has(tok)) firstSeen.set(tok, row.mintBlock);
  }
  const entries = [...firstSeen.entries()];
  const calls = [];
  for (const [tok, blk] of entries) {
    calls.push(_histCall(tok, "0x" + SEL.decimals, blk));
    calls.push(_histCall(tok, "0x" + SEL.symbol, blk));
  }
  const values = await histMany(calls);
  const out = new Map();
  entries.forEach(([tok], i) => {
    let dec = 18;
    try { dec = Number(BigInt(values[i * 2] || "0x12")); } catch (e) {}
    out.set(tok, { decimals: dec, symbol: decodeSymbolHex(values[i * 2 + 1], tok.slice(0, 8)) });
  });
  return out;
}

function decodeSymbolHex(raw, fallback) {
  const data = _wordsOf(raw);
  if (!data.length) return fallback;
  try {
    if (BigInt("0x" + data[0]) === 32n && data.length >= 2) {
      const len = Number(BigInt("0x" + data[1]));
      const hex = data.slice(2).join("").slice(0, len * 2);
      let s = "";
      for (let i = 0; i < hex.length; i += 2) s += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
      return s || fallback;
    }
    let s = "";
    const hex = data[0].replace(/(00)+$/, "");
    for (let i = 0; i < hex.length; i += 2) s += String.fromCharCode(parseInt(hex.substr(i, 2), 16));
    return s || fallback;
  } catch (e) { return fallback; }
}

// Exact sqrtPriceX96, not a sqrt rebuilt from the rounded tick: the tick is a
// grid position and rounding it back into a price loses real value.
function decodeSlot0Exact(raw, key, tokenDecimals) {
  const data = _wordsOf(raw);
  if (data.length < 2) throw new Error("short getSlot0");
  const sqrtPriceX96 = BigInt("0x" + data[0]);
  const sqrt = Number(sqrtPriceX96) / Number(Q96);
  const tick = Number(BigInt.asIntN(24, BigInt("0x" + data[1])));
  const tokenIsC1 = key.currency1 !== USDG_0X;
  const tokenUsd = (tokenIsC1 ? 1 / (sqrt * sqrt) : sqrt * sqrt) * Math.pow(10, tokenDecimals - 6);
  return { sqrt, tick, tokenUsd };
}

function rawAmountsAt(liquidity, tickLower, tickUpper, state) {
  const sqrtLower = Math.pow(1.0001, tickLower / 2);
  const sqrtUpper = Math.pow(1.0001, tickUpper / 2);
  const L = Number(liquidity);
  if (state.tick < tickLower) return { amount0: L * (1 / sqrtLower - 1 / sqrtUpper), amount1: 0 };
  if (state.tick >= tickUpper) return { amount0: 0, amount1: L * (sqrtUpper - sqrtLower) };
  return {
    amount0: L * (1 / state.sqrt - 1 / sqrtUpper),
    amount1: L * (state.sqrt - sqrtLower),
  };
}

function amountsUsd(amount0, amount1, key, tokenDecimals, tokenUsd) {
  const dec0 = key.currency0 === USDG_0X ? 6 : tokenDecimals;
  const dec1 = key.currency1 === USDG_0X ? 6 : tokenDecimals;
  const usd0 = amount0 / Math.pow(10, dec0) * (key.currency0 === USDG_0X ? 1 : tokenUsd);
  const usd1 = amount1 / Math.pow(10, dec1) * (key.currency1 === USDG_0X ? 1 : tokenUsd);
  return usd0 + usd1;
}

async function calculateClosedNfts(closed, metadata, onProgress) {
  const calls = [];
  for (const row of closed) {
    const ticks = _intWord(row.tickLower) + _intWord(row.tickUpper);
    const positionInfoCall = "0x" + SEL.getPositionInfoAtRange
      + _padWord(row.poolId) + _padWord(POSITION_MANAGER) + ticks
      + _padWord(BigInt(row.positionId).toString(16));
    calls.push(_histCall(STATE_VIEW, "0x" + SEL.getSlot0 + _padWord(row.poolId), row.mintBlock));
    // The burn block can contain swaps after the close, and standard RPC cannot
    // select pre-transaction state inside a block. burnBlock - 1 avoids those,
    // at the cost of missing swaps earlier in the burn block itself.
    calls.push(_histCall(STATE_VIEW, "0x" + SEL.getSlot0 + _padWord(row.poolId), row.burnBlock - 1));
    calls.push(_histCall(STATE_VIEW, positionInfoCall, row.mintBlock));
    calls.push(_histCall(STATE_VIEW, "0x" + SEL.getFeeGrowthInside + _padWord(row.poolId) + ticks, row.burnBlock - 1));
  }
  const values = await histMany(calls, onProgress);
  const ledger = loadClaims();
  const out = [];
  for (let i = 0; i < closed.length; i++) {
    const row = closed[i];
    const tokenAddress = row.key.currency0 === USDG_0X ? row.key.currency1 : row.key.currency0;
    const token = metadata.get(tokenAddress) || { decimals: 18, symbol: tokenAddress.slice(0, 8) };
    const warnings = [];
    let rec = null;
    try {
      const mintState = decodeSlot0Exact(values[i * 4], row.key, token.decimals);
      const burnState = decodeSlot0Exact(values[i * 4 + 1], row.key, token.decimals);
      const initial = _wordsOf(values[i * 4 + 2]).map(w => BigInt("0x" + w));
      const growth = _wordsOf(values[i * 4 + 3]).map(w => BigInt("0x" + w));
      if (initial.length < 3 || growth.length < 2) throw new Error("short fee response");
      if (initial[0] !== row.liquidity) warnings.push("mint liquidity mismatch");
      const capRaw = rawAmountsAt(row.liquidity, row.tickLower, row.tickUpper, mintState);
      const prinRaw = rawAmountsAt(row.liquidity, row.tickLower, row.tickUpper, burnState);
      const fee0 = _sub256(growth[0], initial[1]) * row.liquidity / Q128;
      const fee1 = _sub256(growth[1], initial[2]) * row.liquidity / Q128;
      const capitalUsd = amountsUsd(capRaw.amount0, capRaw.amount1, row.key, token.decimals, mintState.tokenUsd);
      const principalUsd = amountsUsd(prinRaw.amount0, prinRaw.amount1, row.key, token.decimals, burnState.tokenUsd);
      // fee0/fee1 are every fee since mint, claimed or not, so realised already
      // counts claims; priced at the close, though, a claim sold long before
      // is repriced at whatever the token is worth by then. Where the claim
      // ledger followed this position from the same mint checkpoint, the part
      // it saw claimed keeps the value it had when claimed, and only the rest
      // is priced at the close.
      let feesUsd = amountsUsd(Number(fee0), Number(fee1), row.key, token.decimals, burnState.tokenUsd);
      const led = ledger.pos[String(row.positionId)];
      const sameMint = led && BigInt(led.b0) === initial[1] && BigInt(led.b1) === initial[2];
      if (sameMint) {
        const c0 = BigInt(led.raw0), c1 = BigInt(led.raw1);
        if (c0 <= fee0 && c1 <= fee1 && Number.isFinite(led.usd)) {
          feesUsd = led.usd + amountsUsd(Number(fee0 - c0), Number(fee1 - c1), row.key, token.decimals, burnState.tokenUsd);
        }
      }
      // Resized during its life. The single-size formulas above no longer
      // hold, so where the ledger followed every change the ladder is valued
      // from it: capital is the mint plus each add at its value when added,
      // principal is the size last seen at the close's price, fees are every
      // claim as booked plus what accrued since the last checkpoint seen, and
      // anything taken out along the way is a return. validateFullBurns then
      // checks the close removed exactly the size the ledger last saw; if not,
      // a change went unseen and the ladder stays under review.
      const resized = sameMint && led.L0 && ((led.addedUsd || 0) > 0 || (led.removedUsd || 0) > 0);
      let capitalOut = capitalUsd, principalOut = principalUsd, liquidityAtBurn = row.liquidity, extraReturn = 0;
      if (resized) {
        const Lb = BigInt(led.L);
        liquidityAtBurn = Lb;
        const endRaw = rawAmountsAt(Lb, row.tickLower, row.tickUpper, burnState);
        principalOut = amountsUsd(endRaw.amount0, endRaw.amount1, row.key, token.decimals, burnState.tokenUsd);
        capitalOut = capitalUsd + (led.addedUsd || 0);
        extraReturn = led.removedUsd || 0;
        const u0 = _sub256(growth[0], BigInt(led.cp0)) * Lb / Q128;
        const u1 = _sub256(growth[1], BigInt(led.cp1)) * Lb / Q128;
        feesUsd = led.usd + amountsUsd(Number(u0), Number(u1), row.key, token.decimals, burnState.tokenUsd);
      }
      rec = {
        positionId: row.positionId, token: token.symbol, tokenAddress, poolId: row.poolId,
        mintTx: row.mintTx, mintBlock: row.mintBlock, burnTx: row.burnTx, burnBlock: row.burnBlock,
        liquidity: liquidityAtBurn.toString(),
        capitalUsd: capitalOut, principalUsd: principalOut, feesUsd,
        revenueUsd: principalOut + feesUsd + extraReturn,
        pnlUsd: principalOut + feesUsd + extraReturn - capitalOut,
        warnings,
      };
    } catch (e) {
      rec = {
        positionId: row.positionId, token: token.symbol, tokenAddress, poolId: row.poolId,
        mintTx: row.mintTx, mintBlock: row.mintBlock, burnTx: row.burnTx, burnBlock: row.burnBlock,
        liquidity: row.liquidity.toString(),
        capitalUsd: 0, principalUsd: 0, feesUsd: 0, revenueUsd: 0, pnlUsd: 0,
        warnings: warnings.concat([String((e && e.message) || e)]),
      };
    }
    out.push(rec);
  }
  return out;
}

const RESIZED_WARNING = "liquidity changed during the position's life";

// A position that changed size (the copy bot adds to positions; so can a hand
// compound) cannot be valued from its mint and its close alone. Its history is
// rebuilt from the chain instead: the wallet's PositionManager transactions
// between the mint and the close, from the explorer, and from each receipt the
// ModifyLiquidity events whose salt is this position. Then, segment by segment:
// fees are the liquidity held times the fee growth across the segment, each add
// is capital at its block's price, each removal (the close included) is money
// out at its block's price. Fees are priced at the close, as for any position.
// If the events do not start with the mint and net to zero at the close, the
// history is incomplete and the position stays under review.
async function reconstructResized(nftRows, closed, metadata) {
  const rowOf = new Map(closed.map(r => [String(r.positionId), r]));
  for (const rec of nftRows) {
    if (!rec.warnings.includes(RESIZED_WARNING)) continue;
    const row = rowOf.get(String(rec.positionId));
    if (!row || row.burnBlock == null) continue;
    try {
      const url = explorerUrl(`/api?module=account&action=txlist&address=${WALLET}`
        + `&startblock=${row.mintBlock}&endblock=${row.burnBlock}&page=1&offset=1000&sort=asc`);
      const req = new Request(url);
      req.headers = explorerHeaders();
      req.timeoutInterval = 20;
      const list = ((await req.loadJSON()) || {}).result;
      if (!Array.isArray(list) || list.length >= 1000) continue;
      const hashes = [...new Set(list.filter(t => _lc(t.to) === _lc(POSITION_MANAGER) && String(t.isError) !== "1")
        .map(t => t.hash))];
      const receipts = await histMany(hashes.map(h => ({ method: "eth_getTransactionReceipt", params: [h] })));
      const events = [];
      for (const rc of receipts) {
        for (const log of (rc && rc.logs) || []) {
          if (_lc(log.address) !== _lc(POOL_MANAGER) || _lc(log.topics && log.topics[0]) !== MODIFY_LIQUIDITY_TOPIC) continue;
          const d = _wordsOf(log.data);
          if (d.length < 4 || BigInt("0x" + d[3]).toString() !== String(rec.positionId)) continue;
          const delta = BigInt.asIntN(256, BigInt("0x" + d[2]));
          if (delta !== 0n) events.push({ block: Number(rc.blockNumber), index: Number(log.logIndex), delta });
        }
      }
      events.sort((a, b) => a.block - b.block || a.index - b.index);
      const net = events.reduce((a, e) => a + e.delta, 0n);
      if (!events.length || events[0].block !== row.mintBlock || events[0].delta !== row.liquidity
          || net !== 0n || events[events.length - 1].block !== row.burnBlock) continue;

      // State at each event. The close is read at burnBlock - 1, like every
      // other close here; the rest at the end of their own block.
      const at = e => (e.block === row.burnBlock ? row.burnBlock - 1 : e.block);
      const ticks = _intWord(row.tickLower) + _intWord(row.tickUpper);
      const vals = await histMany(events.flatMap(e => [
        _histCall(STATE_VIEW, "0x" + SEL.getSlot0 + _padWord(row.poolId), at(e)),
        _histCall(STATE_VIEW, "0x" + SEL.getFeeGrowthInside + _padWord(row.poolId) + ticks, at(e)),
      ]));
      const tokenAddress = row.key.currency0 === USDG_0X ? row.key.currency1 : row.key.currency0;
      const token = metadata.get(tokenAddress) || { decimals: 18 };
      let L = 0n, fee0 = 0n, fee1 = 0n, capital = 0, out = 0, principal = 0, prevG = null, burnState = null;
      events.forEach((e, k) => {
        const state = decodeSlot0Exact(vals[k * 2], row.key, token.decimals);
        const g = _wordsOf(vals[k * 2 + 1]).map(w => BigInt("0x" + w));
        if (g.length < 2) throw new Error("short fee growth");
        if (prevG) {
          fee0 += _sub256(g[0], prevG[0]) * L / Q128;
          fee1 += _sub256(g[1], prevG[1]) * L / Q128;
        }
        prevG = g;
        const abs = e.delta < 0n ? -e.delta : e.delta;
        const a = rawAmountsAt(abs, row.tickLower, row.tickUpper, state);
        const usdValue = amountsUsd(a.amount0, a.amount1, row.key, token.decimals, state.tokenUsd);
        if (e.delta > 0n) capital += usdValue;
        else if (k === events.length - 1) { principal = usdValue; burnState = state; }
        else out += usdValue;
        L += e.delta;
      });
      const feesUsd = amountsUsd(Number(fee0), Number(fee1), row.key, token.decimals, burnState.tokenUsd);
      Object.assign(rec, {
        capitalUsd: capital, principalUsd: principal, feesUsd,
        revenueUsd: principal + feesUsd + out,
        pnlUsd: principal + feesUsd + out - capital,
        resizedEvents: events.length,
        warnings: rec.warnings.filter(w => w !== RESIZED_WARNING),
      });
    } catch (e) { /* stays under review */ }
  }
}

// The close receipt must remove exactly the liquidity the mint created. If it
// does not, the position changed size during its life and the single-segment
// fee formula no longer describes it, so it is excluded rather than guessed at.
async function validateFullBurns(nftRows) {
  const hashes = [...new Set(nftRows.map(r => r.burnTx).filter(Boolean))];
  if (!hashes.length) return;
  const receipts = await histMany(hashes.map(h => ({ method: "eth_getTransactionReceipt", params: [h] })));
  const removed = new Map();
  for (const rc of receipts) {
    for (const log of (rc && rc.logs) || []) {
      if (_lc(log.address) !== _lc(POOL_MANAGER)) continue;
      if (_lc(log.topics && log.topics[0]) !== MODIFY_LIQUIDITY_TOPIC) continue;
      const data = _wordsOf(log.data);
      if (data.length < 4) continue;
      const delta = BigInt.asIntN(256, BigInt("0x" + data[2]));
      if (delta >= 0n) continue;
      const id = BigInt("0x" + data[3]).toString();
      removed.set(id, (removed.get(id) || 0n) - delta);
    }
  }
  for (const row of nftRows) {
    if (removed.get(row.positionId) !== BigInt(row.liquidity)) {
      row.warnings.push(RESIZED_WARNING);
    }
  }
}

// One user-visible position is every slice minted by the same transaction into
// the same pool. The slice count is whatever that group contains; it is never
// assumed, and adjacent token ids are not evidence of anything.
function makeLadders(lifecycles, nftRows, metadata) {
  const byId = new Map(nftRows.map(r => [r.positionId, r]));
  const groups = _groupBy(lifecycles, r => `${r.mintTx}:${r.poolId}`);
  const out = [];
  for (const members of groups.values()) {
    const tokenAddress = members[0].key.currency0 === USDG_0X
      ? members[0].key.currency1 : members[0].key.currency0;
    const meta = metadata.get(tokenAddress) || { symbol: tokenAddress.slice(0, 8) };
    const calculated = members.map(m => byId.get(m.positionId)).filter(Boolean);
    const fullyClosed = calculated.length === members.length && members.every(m => m.burnBlock != null);
    const warnings = [...new Set(calculated.reduce((a, r) => a.concat(r.warnings), []))];
    const usable = fullyClosed && warnings.length === 0;
    const sum = f => calculated.reduce((t, r) => t + r[f], 0);
    const ids = members.map(m => m.positionId).sort((a, b) => (BigInt(a) < BigInt(b) ? -1 : BigInt(a) > BigInt(b) ? 1 : 0));
    out.push({
      key: `${members[0].mintTx}:${members[0].poolId}`,
      token: meta.symbol, tokenAddress, poolId: members[0].poolId,
      mintTx: members[0].mintTx, mintBlock: members[0].mintBlock,
      burnAt: fullyClosed
        ? (members.reduce((a, m) => Math.max(a, m.burnAt || 0), 0) || null) : null,
      mintAt: (() => {
        const t = members.reduce((a, m) => Math.min(a, m.mintAt || Infinity), Infinity);
        return Number.isFinite(t) ? t : null;
      })(),
      burnBlock: fullyClosed && new Set(calculated.map(r => r.burnBlock)).size === 1 ? calculated[0].burnBlock : null,
      positionIds: ids, sliceCount: ids.length,
      status: usable ? "closed" : fullyClosed ? "needs_review" : calculated.length ? "partially_closed" : "open",
      capitalUsd: usable ? sum("capitalUsd") : null,
      principalUsd: usable ? sum("principalUsd") : null,
      feesUsd: usable ? sum("feesUsd") : null,
      revenueUsd: usable ? sum("revenueUsd") : null,
      pnlUsd: usable ? sum("pnlUsd") : null,
      warnings,
    });
  }
  // Most recently closed first: that is the order the list is read in, and the
  // day headings depend on it. Anything still open sorts by mint instead.
  return out.sort((a, b) => (b.burnAt || 0) - (a.burnAt || 0) || b.mintBlock - a.mintBlock);
}

// Split one day's NFTs into the groups an earlier snapshot already valued and
// the ones that still have to be read. A valued group cannot change: its
// positions are closed and every block it read is final, so refreshing a day
// should pay only for what closed since.
//
// Pure on purpose. This decides how much work a "load more" costs, and getting
// it wrong is invisible: the numbers stay correct and the wait just never
// improves, which is exactly how it shipped the first time.
function partitionForDay(wanted, previous) {
  const settled = new Map();
  for (const l of (previous && previous.ladders) || []) {
    // Only a clean close is final. A ladder under review is valued again, so a
    // fix to how it is valued (reconstructResized) reaches days already built.
    if (l.status !== "closed") continue;
    if (!settled.has(l.mintTx)) settled.set(l.mintTx, []);
    settled.get(l.mintTx).push(l);
  }
  const reused = [], todo = [];
  for (const [mintTx, members] of _groupBy(wanted, r => r.mintTx)) {
    const had = settled.get(mintTx);
    const covered = had
      ? new Set(had.reduce((a, l) => a.concat(l.positionIds), []).map(String)) : null;
    // Only reuse when the stored groups cover exactly the NFTs discovery now
    // reports for that mint. A slice closed since the snapshot was written would
    // otherwise be dropped without a trace.
    if (covered && covered.size === members.length
        && members.every(m => covered.has(String(m.positionId)))) {
      reused.push(...had);
    } else {
      todo.push(...members);
    }
  }
  return { reused, todo };
}

// Builds a complete snapshot or throws. A partial result is never returned, so
// a caller cannot accidentally persist one over a good snapshot.
async function buildRealisedSnapshot(wallet, report, opts) {
  const say = (m) => { if (report) report(m); };
  const previous = (opts && opts.previous) || null;
  const now = Date.now();
  // One local day per build. Days are loaded independently rather than as a span
  // reaching back from today: with a span, a user who skipped a week and then
  // tapped refresh was made to value every close in that week before seeing
  // anything. A day is the unit the user asks for, so it is the unit of work.
  let day = startOfLocalDay(
    Number.isFinite(opts && opts.day) ? opts.day : now);
  let dayEnd = day + 86400000;

  say("Reading head block");
  const headHex = (await histRpc([{ method: "eth_blockNumber", params: [] }]))[0];
  const asOfBlock = Number(BigInt(headHex));

  // Discovery has to cover all of history, because a position closed today may
  // have been minted days ago and its mint block is needed to value it. That is
  // nine explorer pages on this wallet and it dominated every run. History does
  // not change, so only the part newer than the cache is fetched now.
  const tCache = loadTransferCache(wallet);
  const cached = tCache ? tCache.items : [];
  const label = tCache ? "Checking" : "Finding";

  // The cursor must always describe the page after the OLDEST page held. Phase
  // one normally stops the moment it reaches ground the cache covers, and its
  // own cursor then points just past page one, nowhere near the oldest edge.
  // Saving that made the next deeper load resume from the top and re-read
  // everything it already had. So the cursor is only adopted from a scan that
  // actually went deeper than anything held.
  let complete = tCache ? !!tCache.complete : false;
  let nextParams = tCache ? tCache.nextParams : null;
  let coveredTo = tCache && Number.isFinite(tCache.coveredToBlock)
    ? tCache.coveredToBlock : Infinity;
  // How many pages lie between the newest transfer and that depth. A resumed
  // scan counts its own pages from one, so without this the progress line reads
  // exactly like a scan starting from the top, and the resume looks like a
  // restart. Null when the cache predates the field: the line then says how many
  // pages were added rather than inventing a position. It stays null there: the
  // only way to learn the real depth is a read from the top, and forcing one by
  // moving the schema would re-read the very pages this exists to stop re-reading.
  let coveredPages = tCache && Number.isFinite(tCache.coveredPages)
    ? tCache.coveredPages : null;
  let pages = 0;
  const adopt = (res, resumed) => {
    pages += res.pages;
    // A scan that began at a cursor extends the coverage. One that began at the
    // top replaces it, because its pages are the same pages counted again.
    const depth = resumed ? (coveredPages == null ? null : coveredPages + res.pages) : res.pages;
    if (res.exhausted) {
      complete = true; nextParams = null; coveredTo = 0; coveredPages = depth; return;
    }
    if (res.oldestBlock < coveredTo) {
      coveredTo = res.oldestBlock; nextParams = res.nextParams; coveredPages = depth;
    }
  };

  // Selecting a day older than anything loaded is its own request: it needs the
  // scan to reach a close older than that point, which on a quiet wallet can be
  // far back. Ordinary refreshes never pay for it.
  const olderThan = Number.isFinite(opts && opts.olderThan) ? opts.olderThan : null;
  const enough = olderThan != null
    ? (fetched) => haveOlderCloseThan(
        mergeTransfers(fetched.map(packTransfer), cached).map(unpackTransfer), wallet, olderThan)
    : (fetched) => haveEnoughFor(
        fetched,
        mergeTransfers(fetched.map(packTransfer), cached).map(unpackTransfer),
        wallet, day, dayEnd);

  say(`${label} position NFTs`);
  const head = await fetchPositionNftTransfers(wallet, asOfBlock,
    (page, n) => say(`${label} position NFTs \u00B7 page ${page} \u00B7 ${n} transfers`),
    { enough, stopAtBlock: tCache ? tCache.newestBlock : null });
  let packed = mergeTransfers(head.included.map(packTransfer), cached);
  adopt(head, false);

  // Phase two: what is wanted lies deeper than anything held, so continue from
  // the oldest edge rather than walking down from the newest page again.
  if (!head.stopped && !complete && nextParams) {
    // Numbered from the pages already held, not from one. The scan resumes at
    // the cursor either way; printing "page 1 - 50 transfers" for it is what
    // made a correct resume look like a re-read of the whole history.
    const held = coveredPages;
    say(`${label} older position NFTs`);
    const tail = await fetchPositionNftTransfers(wallet, asOfBlock,
      (page, n) => say(`${label} older position NFTs \u00B7 ${held != null
        ? `page ${held + page}`
        : `${page} page${page > 1 ? "s" : ""} past the cache`} \u00B7 ${n} transfers`),
      { enough, startParams: nextParams });
    packed = mergeTransfers(tail.included.map(packTransfer), packed);
    adopt(tail, true);
  }

  saveTransferCache(wallet, packed, complete, nextParams, coveredTo, coveredPages);
  const discovered = buildLifecycles(packed.map(unpackTransfer), wallet);

  // When the caller asked for "whatever is older", the day is only known once
  // discovery has found it. Nothing older exists if the scan ran out first.
  if (olderThan != null) {
    let found = null;
    for (const r of discovered) {
      if (r.burnAt == null) continue;
      const d = startOfLocalDay(r.burnAt);
      if (d < olderThan && (found == null || d > found)) found = d;
    }
    if (found == null) {
      // Nothing older to load. Hand back the previous snapshot with the search
      // recorded, so the button stops offering what does not exist.
      const base = previous || {};
      return Object.assign({}, base, {
        schema: REALISED_SCHEMA, wallet, builtAt: Date.now(),
        nextDay: null, hasMore: false,
        counts: Object.assign({}, base.counts, { historyComplete: complete }),
      });
    }
    day = found;
    dayEnd = day + 86400000;
  }

  // A ladder closes as a unit, so select by close time and then pull in every
  // sibling of a selected NFT. Selecting NFTs alone could take four slices of a
  // five-slice ladder and report four fifths of its PnL as the whole.
  const dayTx = new Set();
  for (const r of discovered) {
    if (r.burnAt != null && r.burnAt >= day && r.burnAt < dayEnd) dayTx.add(r.mintTx);
  }
  const wanted = discovered.filter(r => dayTx.has(r.mintTx));

  const { reused, todo } = partitionForDay(wanted, previous);

  let fresh = [];
  if (todo.length) {
    say(`Reading ${todo.length} position definitions`);
    const lifecycles = await hydratePositionDefinitions(todo,
      (done, total) => say(`Reading definitions \u00B7 ${done}/${total}`));
    const metadata = await fetchHistTokenMetadata(lifecycles);
    const closed = lifecycles.filter(r => r.burnBlock != null);
    say(`Valuing ${closed.length} closed slices`);
    const nftRows = await calculateClosedNfts(closed, metadata,
      (done, total) => say(`Valuing closed slices \u00B7 ${done}/${total}`));
    say("Checking each close removed all of its liquidity");
    await validateFullBurns(nftRows);
    if (nftRows.some(r => r.warnings.includes(RESIZED_WARNING))) {
      say("Rebuilding positions that changed size");
      await reconstructResized(nftRows, closed, metadata).catch(() => {});
    }
    fresh = makeLadders(lifecycles, nftRows, metadata);
  }

  // Everything already valued for other days is carried across untouched. Only
  // the day being built is recomputed, and within it only what was not already
  // known, which is what makes a refresh cost the new closes and nothing else.
  const byKey = new Map();
  for (const l of (previous && previous.ladders) || []) {
    if (l.burnAt == null || startOfLocalDay(l.burnAt) !== day) {
      byKey.set(l.key || `${l.mintTx}:${l.poolId}`, l);
    }
  }
  for (const l of reused.concat(fresh)) byKey.set(l.key || `${l.mintTx}:${l.poolId}`, l);
  const ladders = [...byKey.values()]
    .sort((a, b) => (b.burnAt || 0) - (a.burnAt || 0) || b.mintBlock - a.mintBlock);

  const accepted = ladders.filter(l => l.status === "closed");
  const total = f => accepted.reduce((s, l) => s + l[f], 0);

  const dayMap = new Map();
  for (const l of accepted) {
    if (l.burnAt == null) continue;
    const at = startOfLocalDay(l.burnAt);
    if (!dayMap.has(at)) dayMap.set(at, { at, count: 0, pnlUsd: 0, capitalUsd: 0, feesUsd: 0 });
    const e = dayMap.get(at);
    e.count++; e.pnlUsd += l.pnlUsd; e.capitalUsd += l.capitalUsd; e.feesUsd += l.feesUsd;
  }
  const byDay = [...dayMap.values()].sort((a, b) => b.at - a.at);

  // Which days have actually been looked at. A day with no closes still counts
  // as loaded: the answer "nothing closed" is an answer, and asking again would
  // be the same work for the same result.
  const loadedDays = [...new Set([...((previous && previous.loadedDays) || []), day])]
    .sort((a, b) => b - a);
  const oldestLoaded = loadedDays[loadedDays.length - 1];

  // The next day worth offering is the most recent one older than anything
  // loaded that actually has a close in it. Stepping back one calendar day at a
  // time would offer empty days and make the user tap through them.
  let nextDay = null;
  for (const r of discovered) {
    if (r.burnAt == null) continue;
    const d = startOfLocalDay(r.burnAt);
    if (d < oldestLoaded && (nextDay == null || d > nextDay)) nextDay = d;
  }
  // A day may exist below what has been scanned so far. The button then offers
  // "older" rather than a date, and asking for it is what pays to find out.
  const moreUnscanned = nextDay == null && !complete;

  return {
    schema: REALISED_SCHEMA,
    wallet,
    asOfBlock,
    builtAt: Date.now(),
    builtDay: day,
    loadedDays,
    nextDay,
    oldestLoaded,
    hasMore: nextDay != null || moreUnscanned,
    counts: {
      transfers: packed.length,
      explorerPages: pages,
      historyComplete: complete,
      discoveredNfts: discovered.length,
      openNfts: discovered.filter(r => r.burnBlock == null).length,
      groups: ladders.length,
      closedGroups: accepted.length,
      needsReview: ladders.filter(l => l.status === "needs_review").length,
      olderClosedNfts: discovered.filter(r =>
        r.burnAt != null && startOfLocalDay(r.burnAt) < oldestLoaded).length,
    },
    totals: {
      capitalUsd: total("capitalUsd"),
      revenueUsd: total("revenueUsd"),
      feesUsd: total("feesUsd"),
      pnlUsd: total("pnlUsd"),
    },
    byDay,
    ladders,
  };
}

// Day keys are local midnights stored as instants. When the phone's time zone
// changes (travel), local midnight moves and no stored key matches a day any
// more: every day read "not read yet" at $0.00 while the snapshot still held
// its 455 closes (owner, 2026-10-12, UTC+7 to UTC+8). So days are re-anchored
// on load: a stored midnight plus 12h falls in the same calendar day in any
// zone within 12h of the one it was saved in, and day totals are recounted
// from the closes themselves in the current zone. No figure changes, only
// which day it is filed under.
function rekeyRealisedDays(snap) {
  const H12 = 43200000;
  const day = d => (Number.isFinite(d) ? startOfLocalDay(d + H12) : d);
  const dayMap = new Map();
  for (const l of snap.ladders || []) {
    if (l.status !== "closed" || l.burnAt == null) continue;
    const at = startOfLocalDay(l.burnAt);
    if (!dayMap.has(at)) dayMap.set(at, { at, count: 0, pnlUsd: 0, capitalUsd: 0, feesUsd: 0 });
    const e = dayMap.get(at);
    e.count++;
    e.pnlUsd += Number(l.pnlUsd) || 0;
    e.capitalUsd += Number(l.capitalUsd) || 0;
    e.feesUsd += Number(l.feesUsd) || 0;
  }
  return Object.assign({}, snap, {
    loadedDays: [...new Set((snap.loadedDays || []).map(day))].sort((a, b) => b - a),
    builtDay: day(snap.builtDay),
    oldestLoaded: day(snap.oldestLoaded),
    nextDay: snap.nextDay == null ? snap.nextDay : day(snap.nextDay),
    byDay: [...dayMap.values()].sort((a, b) => b.at - a.at),
  });
}

// The Mac's hourly realised bot (tools/realised-bot.mjs) builds this same
// snapshot and drops it in the Scriptable iCloud folder (owner, 2026-10-12:
// "everytime i open my rh portfolio its updated"). Whichever of that copy and
// the phone's own was built last is the one shown; a tap still builds on the
// phone, and that build starts from whatever was shown, so nothing is lost.
function sharedRealisedPath() {
  const f = FileManager.iCloud();
  return [f, f.joinPath(f.documentsDirectory(), `rh_realised_${_lc(WALLET)}.json`)];
}
function loadSharedRealised() {
  try {
    const [f, p] = sharedRealisedPath();
    if (!f.fileExists(p)) return null;
    // Evicted: not worth a stall in a widget. Kick the download so the next
    // run has it; the app waits for it up front (fetchSharedRealised).
    if (f.isFileDownloaded && !f.isFileDownloaded(p)) { f.downloadFileFromiCloud(p); return null; }
    return JSON.parse(f.readString(p));
  } catch (e) { return null; }
}
async function fetchSharedRealised(ms) {
  try {
    const [f, p] = sharedRealisedPath();
    if (f.fileExists(p) && f.isFileDownloaded && !f.isFileDownloaded(p)) {
      await Promise.race([f.downloadFileFromiCloud(p),
        new Promise(r => Timer.schedule(ms, false, r))]);
    }
  } catch (e) {}
}

function loadRealised() {
  const usable = s => s && s.schema === REALISED_SCHEMA && _lc(s.wallet) === _lc(WALLET);
  const snap = [loadJson("realised"), loadSharedRealised()].filter(usable)
    .sort((a, b) => (b.builtAt || 0) - (a.builtAt || 0))[0];
  if (!snap) return null;
  // Nothing to reinterpret when a snapshot outlives the day it was built on.
  // Each day stands on its own, so a snapshot from last week is simply a
  // snapshot that does not have this week's days in it yet. A change of time
  // zone is the one thing that does need reinterpreting; see rekeyRealisedDays.
  // Pruned on load too, not only on save: the headline is the sum of what is
  // stored, so a day that fell out of the window overnight stayed in the 7-day
  // total while its row was gone (owner, 2026-10-12: $+375 over rows summing
  // $+1,011, the gap being Mon 5 Oct).
  return pruneRealised(rekeyRealisedDays(snap));
}

// Only ever called with a snapshot that completed. A throw upstream leaves the
// previous good snapshot in place.
// The cache holds the last week of closes, and nothing older.
//
// A wallet accumulates closed positions without limit, and after a year the
// snapshot is mostly history nobody reads. Positions are kept by when they
// CLOSED, not when they opened: a ladder opened in June and closed yesterday is
// this week's business.
//
// Note this only prunes results. The transfer cache still holds all history and
// has to: a position closed today may have been minted months ago, and without
// that mint it cannot be valued at all.
//
// Totals are recomputed from what survives, so the headline is always the sum
// of the rows underneath it rather than a number describing data that is no
// longer there. Older days can still be loaded on demand; they show, and then
// fall outside the window on the next build.
const REALISED_RETAIN_DAYS = 7;

function pruneRealised(snap) {
  if (!snap || !Array.isArray(snap.ladders)) return snap;
  const cutoff = startOfLocalDay(Date.now()) - (REALISED_RETAIN_DAYS - 1) * 86400000;
  const kept = snap.ladders.filter(l => l.burnAt == null || l.burnAt >= cutoff);
  if (kept.length === snap.ladders.length) return snap;

  const accepted = kept.filter(l => l.status === "closed");
  const total = f => accepted.reduce((s, l) => s + (Number(l[f]) || 0), 0);
  const dayMap = new Map();
  for (const l of accepted) {
    if (l.burnAt == null) continue;
    const at = startOfLocalDay(l.burnAt);
    if (!dayMap.has(at)) dayMap.set(at, { at, count: 0, pnlUsd: 0, capitalUsd: 0, feesUsd: 0 });
    const e = dayMap.get(at);
    e.count++; e.pnlUsd += l.pnlUsd; e.capitalUsd += l.capitalUsd; e.feesUsd += l.feesUsd;
  }
  return Object.assign({}, snap, {
    ladders: kept,
    byDay: [...dayMap.values()].sort((a, b) => b.at - a.at),
    loadedDays: (snap.loadedDays || []).filter(d => d >= cutoff),
    oldestLoaded: cutoff,
    // There is older history by construction, since something was just dropped.
    hasMore: true,
    retainDays: REALISED_RETAIN_DAYS,
    totals: {
      capitalUsd: total("capitalUsd"),
      revenueUsd: total("revenueUsd"),
      feesUsd: total("feesUsd"),
      pnlUsd: total("pnlUsd"),
    },
  });
}

function saveRealised(snap) {
  if (!snap || snap.schema !== REALISED_SCHEMA || !snap.totals) return false;
  saveJson("realised", pruneRealised(snap));
  return true;
}

function fmtCompact(v) {
  // Distinguish "no data" from a real zero: a pool that genuinely traded nothing in
  // the last hour is worth seeing, and "--" would hide it as unknown.
  if (v == null || isNaN(v)) return "--";
  if (v <= 0) return "0";
  const u = [[1e9, "B"], [1e6, "M"], [1e3, "K"]];
  for (const [n, sfx] of u) {
    if (v >= n) { const x = v / n; return (x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(2)) + sfx; }
  }
  return v.toFixed(0);
}

// Daily fee yield, banded so the column is scannable instead of uniformly green.
// Below FEE_RATE_OK goes red: the range is barely being traded and the position is
// a candidate to rebalance, which is the whole point of watching this column.
// Colour thresholds for a fee rate, in %/day. Set against the measured spread
// of live pools on this chain (median %/day by fee tier: 3.3, 10.4, 62.5, 109
// for <1%, 1-3%, 3-5%, 5-10% tiers; the ranked table's top rows sit at 45-290):
//   green  >= 75   the top tier, worth entering now
//   yellow 25-75   decent, most working pools
//   red    <  25   not worth the gas to enter
const FEE_RATE_STRONG = 75;
const FEE_RATE_OK = 25;

// Entry distance thresholds. An Alert renders plain text only, so the state has
// to be carried by a coloured circle rather than by colouring the number.
// Absolute rather than relative to the grid: a 3% wait is a 3% wait whatever
// the pool's spacing, and waiting is what the reader is deciding about.
const ENTRY_NEAR_PCT = 1;   // green below this
const ENTRY_FAR_PCT  = 3;   // red above this
function entryDot(pct) {
  if (!(pct > 0)) return "\u{1F7E2}";
  if (pct < ENTRY_NEAR_PCT) return "\u{1F7E2}";
  return pct <= ENTRY_FAR_PCT ? "\u{1F7E1}" : "\u{1F534}";
}

function feeRateColor(pct) {
  if (pct == null || isNaN(pct)) return COL.faint;
  if (pct >= FEE_RATE_STRONG) return COL.green;
  if (pct >= FEE_RATE_OK) return COL.yellow;
  return COL.red;
}

// Age has to read at a glance in a narrow column, so one unit and no decimals
// past a day: "42m", "6.4h", "2.3d", "3w".
function fmtAge(days) {
  if (days == null || isNaN(days)) return "--";
  if (days < 1 / 24) return `${Math.max(1, Math.round(days * 1440))}m`;
  if (days < 1) return `${(days * 24).toFixed(1)}h`;
  if (days < 14) return `${days.toFixed(1)}d`;
  return `${Math.round(days / 7)}w`;
}
function shortWallet(w) {
  return w.length > 10 ? `${w.slice(0, 6)}...${w.slice(-4)}` : w;
}
function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
// Table timestamp carries seconds: the table refetches on every open, so a
// changing second hand is the proof that what is on screen is actually live.
function fmtTimeSec(ts) {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function tokenSymbol(addr) {
  const k = KNOWN_TOKENS[addr];
  return k ? k.symbol : addr.slice(0, 6);
}

// ----------------------------- widget UI -------------------------------------
function cell(parent, label, value, color) {
  const s = parent.addStack();
  s.layoutVertically();
  const l = s.addText(label.toUpperCase());
  l.font = Font.semiboldSystemFont(9);
  l.textColor = COL.faint;
  s.addSpacer(3);
  const v = s.addText(value);
  v.font = Font.semiboldSystemFont(15);
  v.textColor = color;
  v.lineLimit = 1;
  v.minimumScaleFactor = 0.7;
}

// The same shape as cell() but quieter, for a second row of figures that should
// read as supporting detail rather than compete with the headline grid.
function smallCell(parent, label, value, color) {
  const s = parent.addStack();
  s.layoutVertically();
  const l = s.addText(label);
  l.font = Font.mediumSystemFont(8);
  l.textColor = COL.faint;
  s.addSpacer(2);
  const v = s.addText(value);
  v.font = Font.semiboldSystemFont(12);
  v.textColor = color;
  v.lineLimit = 1;
  v.minimumScaleFactor = 0.7;
}

function baseWidget() {
  const w = new ListWidget();
  // ListWidget inserts a default gap between every child on top of any explicit
  // addSpacer(n). Zero it so the explicit spacers below fully define the layout.
  w.spacing = 0;
  const g = new LinearGradient();
  g.colors = [COL.bgTop, COL.bgBot];
  g.locations = [0, 1];
  w.backgroundGradient = g;
  return w;
}

function col(stack, text, width, font, color, right) {
  const s = stack.addStack();
  s.size = new Size(width, 0);
  s.spacing = 0;
  const t = s.addText(text);
  t.font = font;
  t.textColor = color;
  t.lineLimit = 1;
  // Shrink rather than ellipsize: a value one point too wide for its column
  // should lose a little size, never its last digit.
  t.minimumScaleFactor = 0.75;
  if (right) t.rightAlignText(); else t.leftAlignText();
}

function rangeBar(inRange, tickLower, tickUpper, currentTick, w, h, flip) {
  w = w || 60; h = h || 12;
  const dc = new DrawContext();
  dc.size = new Size(w, h);
  dc.opaque = false;
  dc.respectScreenScale = true;
  const midY = h / 2, trackH = 3;
  dc.setFillColor(new Color("#FFFFFF", 0.18));
  dc.fillRect(new Rect(0, midY - trackH / 2, w, trackH));
  if (tickUpper <= tickLower) return dc.getImage();

  let f = (currentTick - tickLower) / (tickUpper - tickLower);
  if (!isFinite(f)) f = 0.5;
  if (flip) f = 1 - f;
  const above = f >= 1;
  const hex = inRange ? "#30D158" : (above ? "#F5BD00" : "#FF453A");
  dc.setFillColor(new Color(hex, 0.35));
  dc.fillRect(new Rect(0, midY - trackH / 2, w, trackH));
  f = Math.max(0, Math.min(1, f));
  const mw = 3, x = Math.max(0, Math.min(w - mw, f * w - mw / 2));
  dc.setFillColor(new Color(hex));
  dc.fillRect(new Rect(x, 0, mw, h));
  return dc.getImage();
}

// Where the current price sits across a row's range, as the same fraction the
// bar draws (linear in ticks, so log price), in percent. Not clamped: 112% says
// price has run 12% of the range width past the top, -8% that it fell below.
function rangePct(r) {
  if (r.tickUpper <= r.tickLower) return null;
  let f = (r.currentTick - r.tickLower) / (r.tickUpper - r.tickLower);
  if (!isFinite(f)) return null;
  if (r.flipRange) f = 1 - f;
  return f * 100;
}
function fmtRangePct(p) {
  if (p == null) return "--";
  return Math.round(Math.max(-999, Math.min(999, p))) + "%";
}
// Same colour rule as the bar: green while any position is in range, yellow when
// price is above the whole range, red when below.
function rangeColor(r, p) {
  if (r.barInRange != null ? r.barInRange : r.inRange) return COL.green;
  return (p != null && p >= 100) ? COL.yellow : COL.red;
}

// Market cap the token would have at a tick of this pool, scaled from the current
// cap by the price ratio. A tick is log price, so the ratio between two ticks is
// 1.0001^(difference), inverted when the token is currency1 because the pool price
// is then quoted the other way round. Decimals cancel in the ratio.
function mcapAtTick(r, tick) {
  if (r.currentTick == null) return null;
  // The base has to come from the SAME pool the tick does.
  //
  // This scaled from r.mcap, which is supply times a reference price taken as
  // the median across every pool holding the token. That reference and this
  // pool's tick move at different rates, so a range whose ticks never change
  // drifted every time the price did: one HOTDOG position read 1.65M-4.47M,
  // then 1.67M-4.55M, then 1.73M-4.71M within twenty minutes.
  //
  // supply x the pool's own implied price is self-consistent with currentTick,
  // so the ratio below cancels exactly and a fixed range reads as a fixed
  // number. r.mcap stays as the fallback for a row with no supply.
  const base = (r.supply > 0 && r.poolTokenPrice > 0)
    ? r.supply * r.poolTokenPrice : r.mcap;
  if (base == null) return null;
  const d = tick - r.currentTick;
  return base * Math.pow(1.0001, r.flipRange ? -d : d);
}


const WIDGET_BOX = {
  956: [382, 170], 932: [382, 170], 926: [364, 162], 896: [360, 159],
  874: [356, 158], 852: [345, 158], 844: [338, 155], 812: [329, 148],
  736: [338, 148], 667: [311, 148], 568: [282, 141],
};
function widgetSize() {
  const h = Math.round(Device.screenSize().height);
  const box = WIDGET_BOX[h] || [329, 148];
  return config.widgetFamily === "medium"
    ? { w: box[0], h: box[1] }
    : { w: box[0], h: box[0] };
}

function buildWidget(d, stale, dataTime) {
  // Realised PnL comes from the snapshot the app last completed. A widget never
  // rebuilds it: that is hundreds of archive reads and would blow the render
  // budget. No snapshot simply means the line is not shown.
  let _realised = null;
  try { _realised = loadRealised(); } catch (e) {}
  const small = config.widgetFamily === "small";
  const w = baseWidget();
  // Tap opens this script in Scriptable, which renders the interactive table.
  // A widget has a single tap target, so the table is what makes per-token links
  // reachable. The wallet rides along because a scriptable:// launch has no
  // widgetParameter.
  let runUrl = "scriptable:///run/" + encodeURIComponent(Script.name()) + "?wallet=" + WALLET;
  if (NAME) runUrl += "&name=" + encodeURIComponent(NAME);
  w.url = runUrl;
  w.setPadding(12, 17, 8, 17);
  w.refreshAfterDate = new Date(Date.now() + REFRESH_MIN * 60 * 1000);

  // Header
  const head = w.addStack();
  head.spacing = 0;
  head.centerAlignContent();
  const icon = head.addText("\u{1F984}"); // unicorn emoji for Uniswap
  icon.font = Font.systemFont(16);
  head.addSpacer(6);
  const t = head.addText(NAME || shortWallet(WALLET));
  t.font = Font.boldSystemFont(13);
  t.textColor = COL.text;
  t.lineLimit = 1;
  t.minimumScaleFactor = 0.8;
  head.addSpacer();
  // Status lives in the header: red for a cached result, green for a live one.
  const tag = head.addText(stale ? `STALE ${fmtTime(dataTime)}` : `Updated ${fmtTime(Date.now())}`);
  tag.font = Font.semiboldSystemFont(10);
  tag.textColor = stale ? COL.red : COL.green;

  w.addSpacer(6);

  // Hero: Total Value
  const nwRow = w.addStack();
  nwRow.spacing = 0;
  const nwLabel = nwRow.addText("NET WORTH");
  nwLabel.font = Font.semiboldSystemFont(10);
  nwLabel.textColor = COL.accent;
  const cashTag = walletTag(d);
  if (cashTag) {
    nwRow.addSpacer();
    const ethInfo = nwRow.addText(cashTag);
    ethInfo.font = Font.mediumSystemFont(10);
    ethInfo.textColor = COL.muted;
  }
  w.addSpacer(3);
  const nw = w.addText(fmtUsd(d.netWorth != null ? d.netWorth : d.totalValue));
  nw.font = Font.boldSystemFont(small ? 24 : 26);
  nw.textColor = COL.text;
  nw.minimumScaleFactor = 0.6;
  nw.lineLimit = 1;

  w.addSpacer(6);

  // Bottom grid
  const grid = w.addStack();
  grid.spacing = 0;
  grid.centerAlignContent();
  if (small) {
    cell(grid, `LP · ${d.openCount}`, fmtUsd(d.totalValue), COL.text);
    grid.addSpacer();
    cell(grid, "PnL", fmtUsd(d.totalPnl, true) + (() => {
      // Total return on total invested, beside the money. The per-row figure
      // says how each trade is doing; this one says how the book is doing.
      const b = d.totalBasis || 0;
      if (!(b > 0) || !Number.isFinite(d.totalPnl)) return "";
      const p = (d.totalPnl / b) * 100;
      return `  ${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
    })(), signColor(d.totalPnl));
  } else {
    cell(grid, `LP · ${d.openCount}`, fmtUsd(d.totalValue), COL.text);
    grid.addSpacer();
    cell(grid, "Fees", fmtUsd(d.totalFees), d.totalFees > 0 ? COL.green : COL.muted);
    grid.addSpacer();
    cell(grid, "PnL", fmtUsd(d.totalPnl, true) + (() => {
      // Total return on total invested, beside the money. The per-row figure
      // says how each trade is doing; this one says how the book is doing.
      const b = d.totalBasis || 0;
      if (!(b > 0) || !Number.isFinite(d.totalPnl)) return "";
      const p = (d.totalPnl / b) * 100;
      return `  ${p >= 0 ? "+" : ""}${p.toFixed(1)}%`;
    })(), signColor(d.totalPnl));
    grid.addSpacer();
    const tir = d.tokensInRange != null ? d.tokensInRange : d.inRangeCount;
    const tc  = d.tokenCount != null ? d.tokenCount : d.openCount;
    cell(grid, "In Range", `${tir}/${tc}`, tir === tc ? COL.green : COL.yellow);
  }

  // Realised PnL, one column per day, laid out the same way as the row above so
  // the eye reads them the same. Each label names its own day, so the row needs
  // no heading of its own; RPNL rather than PNL because the row above already
  // has a PNL column showing the return on still-open positions.
  // Days are never added together.
  //
  // Four columns: today and the three before it. A widget is glanced at, so this
  // is bounded by what fits rather than by how much history exists.
  const REALISED_COLS = 4;
  if (!small && _realised && Array.isArray(_realised.byDay) && _realised.byDay.length) {
    const today = startOfLocalDay(Date.now());
    const cols = _realised.byDay
      .filter(e => e.at <= today)
      .sort((a, b) => b.at - a.at)
      .slice(0, REALISED_COLS);
    // Today always has a column. Without one, a quiet day and a snapshot that
    // has not been refreshed since midnight both just show yesterday first.
    if (!cols.some(e => e.at === today)) {
      cols.unshift({ at: today, pnlUsd: null, count: 0 });
      if (cols.length > REALISED_COLS) cols.pop();
    }
    if (cols.length) {
      w.addSpacer(5);
      const rg = w.addStack();
      rg.spacing = 0;
      cols.forEach((e, i) => {
        if (i) rg.addSpacer();
        const n = localDaysAgo(e.at, Date.now());
        smallCell(rg, n === 0 ? "TODAY RPNL" : `${n}d AGO RPNL`,
          e.pnlUsd == null ? "--" : fmtUsd(e.pnlUsd, true),
          e.pnlUsd == null ? COL.muted : e.pnlUsd >= 0 ? COL.green : COL.red);
      });
      rg.addSpacer();
    }
  }

  // Position rows (medium/large only).
  //
  // Stale data is suppressed rather than drawn. A cached row is a position that
  // was open when the cache was written, so after closing everything the widget
  // kept showing holdings that no longer existed, with the leftover height as a
  // large blank gap. Header totals still say what they are dated, which is
  // honest; individual rows cannot, so they wait for a real refresh.
  const list = stale ? [] : (d.rows || []);
  if (!list.length && !small) {
    w.addSpacer(10);
    const note = w.addText(stale
      ? "Ladders hidden until this refreshes"
      : "No open ladders");
    note.font = Font.semiboldSystemFont(11);
    note.textColor = COL.muted;
    note.centerAlignText();
    if (stale) {
      const sub = w.addText("tap to open and reload");
      sub.font = Font.systemFont(9);
      sub.textColor = COL.faint;
      sub.centerAlignText();
    }
  }
  if (list.length && !small) {
    const usable = widgetSize().w - 34;
    // Seven columns plus the bar is dense, so the gap and font shrink relative to
    // the older five-column layout to keep every value un-truncated.
    const GAP = 3;
    const PT = usable >= 320 ? 9.5 : 9;
    let barW = Math.round(Math.max(22, Math.min(32, usable * 0.085)));
    // Every row appends a "% through range" label after the bar; reserve its
    // width so the row does not overflow and clip the label off the right edge.
    const LBLW = 28;
    const rem = usable - barW - LBLW - GAP * 6;
    const WT = { pair: 2.6, value: 2.4, pnl: 2.0, fees: 2.0, feePct: 1.9, age: 1.3 };
    let tot = 0; for (const k in WT) tot += WT[k];
    const W = {}; for (const k in WT) W[k] = Math.floor(rem * WT[k] / tot);

    const LBL = Font.semiboldSystemFont(7.5);
    const DAT = Font.semiboldSystemFont(PT);

    w.addSpacer(6);
    const hdr = w.addStack();
    hdr.spacing = 0;
    hdr.centerAlignContent();
    col(hdr, "", W.pair, LBL, COL.faint);
    hdr.addSpacer(GAP);
    col(hdr, "VALUE", W.value, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "PNL", W.pnl, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "FEES", W.fees, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "FEE%/D", W.feePct, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "AGE", W.age, LBL, COL.faint, true);
    hdr.addSpacer(GAP);
    col(hdr, "RANGE", barW, LBL, COL.faint);
    w.addSpacer(2);

    for (let i = 0; i < list.length; i++) {
      const r = list[i];
      const row = w.addStack();
      row.spacing = 0;
      row.centerAlignContent();
      const rp = rangePct(r);
      const feePctStr = r.feePctDay != null ? r.feePctDay.toFixed(2) + "%" : "--";
      col(row, r.pair, W.pair, DAT, COL.text);
      row.addSpacer(GAP);
      col(row, fmtUsd(r.totalUsd), W.value, DAT, COL.text, true);
      row.addSpacer(GAP);
      col(row, fmtUsd(r.pnl, true), W.pnl, DAT, signColor(r.pnl), true);
      row.addSpacer(GAP);
      col(row, fmtUsd(r.fees), W.fees, DAT, r.fees > 0 ? COL.green : COL.muted, true);
      row.addSpacer(GAP);
      col(row, feePctStr, W.feePct, DAT, feeRateColor(r.feePctDay), true);
      row.addSpacer(GAP);
      col(row, fmtAge(r.ageDays), W.age, DAT, COL.muted, true);
      row.addSpacer(GAP);
      const bar = row.addImage(rangeBar(r.barInRange != null ? r.barInRange : r.inRange, r.tickLower, r.tickUpper, r.currentTick, barW, 12, r.flipRange));
      bar.imageSize = new Size(barW, 12);
      col(row, fmtRangePct(rp), LBLW, Font.semiboldSystemFont(7.5), rangeColor(r, rp), true);
      if (i < list.length - 1) w.addSpacer(5);
    }
  }

  w.addSpacer();
  const foot = w.addText(`All values USD · ${VERSION}`);
  foot.font = Font.systemFont(9);
  foot.textColor = COL.faint;
  return w;
}

function errorWidget(msg) {
  const w = baseWidget();
  w.setPadding(16, 17, 16, 17);
  const head = w.addStack();
  head.centerAlignContent();
  const icon = head.addText("\u{1F984}");
  icon.font = Font.systemFont(16);
  head.addSpacer(6);
  const t = head.addText("Uniswap V4");
  t.font = Font.boldSystemFont(14);
  t.textColor = COL.text;
  w.addSpacer(6);
  const m = w.addText(msg);
  m.font = Font.systemFont(11);
  m.textColor = COL.muted;
  return w;
}

// -------------------------------- main ---------------------------------------
// quick: everything needed for value and PnL, nothing that waits on the
// explorer per position (mint lookups, claimed-fee replays). The app shows a
// quick pass first and runs the full one behind it; result.pending says whether
// the full pass has anything left to do.
async function main(opts) {
  const quick = !!(opts && opts.quick);
  const startedAt = Date.now();
  const timings = {};
  migrateOldCache();
  await checkHostedExplorer();
  if (!WALLET) {
    const msg = "Set a wallet: widget Parameter = wallet address (or \"Name|wallet\"), or DEFAULT_WALLET in the script.";
    return { widget: errorWidget(msg), data: null, message: msg };
  }
  if (!isAddress(WALLET)) {
    const n = WALLET.replace(/^0x/, "").length;
    const msg = `Not a wallet address. Got ${n} hex chars, need 40 (20 bytes). `
      + (n === 64 ? "That length is a tx hash, pool id, or private key." : "Check the widget Parameter.");
    return { widget: errorWidget(msg), data: null, message: msg };
  }

  let data = null, stale = false, dataTime = Date.now(), error = null;
  const gasPromise = RPC_KEYED ? fetchGasNow() : Promise.resolve(null);
  try {
    // 1. Resolve active position IDs.
    // balanceOf is one cheap call and tells us whether the cached ID list is still
    // correct, so the expensive backward scan only runs when the count actually
    // changed. A blind TTL here would periodically force a ~50-call rescan and
    // blow the widget's execution budget.
    let activeIds = loadActiveIds();
    // balanceOf and the ownership check of the cached ids do not depend on each
    // other, so they go out together; the check is only wasted when the count
    // turns out to have changed, and it saves a round trip every other run.
    // Position definitions are prefetched with the ownership checks. Multicall's
    // same-tick queue combines all three into one RPC request on the normal warm-
    // cache path, removing an entire sequential round trip.
    const prefetchedPositions = activeIds && activeIds.length
      ? fetchPositionData(activeIds).catch(() => null) : null;
    const [expected, stillOwned, cachedPositionData] = await Promise.all([
      getExpectedCount(WALLET),
      activeIds && activeIds.length ? verifyOwned(activeIds, WALLET).catch(() => null) : null,
      prefetchedPositions,
    ]);

    // Matching the cached count against balanceOf is not enough on its own: close
    // 10 positions and open 10 more and the count is unchanged while every id
    // differs. But if all cached ids are still owned AND there are exactly
    // balanceOf of them, the cached set must be the owned set. So confirm both,
    // and only then skip the explorer lookup.
    let cacheOk = false;
    if (activeIds && activeIds.length === expected && expected > 0) {
      cacheOk = !!stillOwned && stillOwned.length === expected;
      if (cacheOk) activeIds = stillOwned;
    } else if (activeIds && expected === 0) {
      cacheOk = activeIds.length === 0;
    }

    // balanceOf is authoritative and cheap, so the script always knows how many
    // positions the wallet holds. Rendering fewer than that without saying so
    // is the worst failure here: the table looks complete and simply omits
    // money. Surfaced as a first-class field rather than a silent shortfall.
    let idsDiag = cacheOk ? "cache" : "";
    let positionData = cacheOk ? cachedPositionData : null;
    if (!cacheOk) {
      if (config.runsInWidget) {
        // Discovery can take many paginated explorer requests. Keep that work in
        // the app, where the interactive table is already showing cached rows.
        if (stillOwned && stillOwned.length) {
          activeIds = stillOwned;
          if (cachedPositionData) {
            const owned = new Set(stillOwned);
            positionData = {
              positions: cachedPositionData.positions.filter(p => owned.has(p.tokenId)),
              poolKeys: cachedPositionData.poolKeys,
            };
          }
        } else {
          throw new Error("Position list changed. Tap widget to refresh in app.");
        }
      } else {
        // Union the explorer's list with what is already known, then let the
        // chain decide. The explorer is an index and lags: a position minted
        // minutes ago is missing from it, and replacing the cached list with
        // its answer dropped a token that had just been opened. ownerOf is
        // authoritative, so a stale extra costs one call and a missing one
        // costs a whole row.
        let fetched = null;
        try {
          const exT0 = Date.now();
          const fromExplorer = await fetchOwnedIdsFromExplorer(WALLET);
          idsDiag = `explorer ${EXPLORER_KEYED ? "hosted" : "public"} ${(Date.now() - exT0) / 1000 | 0}s -> ${(fromExplorer || []).length}`;
          // Normalise before deduping. The explorer yields Number ids and a
          // receipt yields decimal strings, so a plain Set kept both: one
          // re-entered ladder counted twice, showing 10 positions and $1,600
          // where there were 5 and $800.
          const seenIds = new Set();
          const merged = [];
          for (const raw of [...(fromExplorer || []), ...(activeIds || [])]) {
            let key;
            try { key = BigInt(raw).toString(); } catch (e) { continue; }
            if (seenIds.has(key)) continue;
            seenIds.add(key);
            merged.push(key);
          }
          fetched = await verifyOwned(merged, WALLET);
        } catch (e) {
          fetched = null;
          idsDiag = `explorer ${EXPLORER_KEYED ? "hosted" : "public"} FAILED: ${String((e && e.message) || e).slice(0, 40)}`;
        }

        if (fetched && fetched.length) {
          activeIds = fetched;
          idsDiag += ` verified ${fetched.length}`;
          saveActiveIds(activeIds);
        } else if (activeIds && activeIds.length) {
          // Explorer down or rate limiting us. Keep the cached ids, re-verified
          // on chain instead of starting a very wide ownerOf scan.
          try { activeIds = await verifyOwned(activeIds, WALLET); } catch (e) {}
          // Closing or re-entering burns the cached ids, and the explorer needs
          // time to index the replacements. Chain state already says how many
          // positions exist, so trust that over a short list and go scan.
          // Firing only on an empty list left a half-filled table stuck: five
          // cached ids out of twenty rendered five rows and gave up, even
          // though balanceOf already said fifteen were missing.
          if (activeIds.length < expected && expected > 0) {
            try {
              const scanned = await discoverActiveIds(WALLET, expected);
              // Union, not replace. The scan walks back from the newest id on a
              // 12s budget, so it can stop before reaching an older position
              // that the cache still knows about.
              const seen = new Set();
              const union = [];
              for (const raw of [...(activeIds || []), ...(scanned || [])]) {
                let key;
                try { key = BigInt(raw).toString(); } catch (e) { continue; }
                if (seen.has(key)) continue;
                seen.add(key);
                union.push(key);
              }
              if (union.length > activeIds.length) {
                activeIds = await verifyOwned(union, WALLET);
                if (activeIds.length) saveActiveIds(activeIds);
              }
            } catch (e) {}
          }
        } else {
          // Nothing cached and no explorer: scanning is the only option left.
          activeIds = await discoverActiveIds(WALLET, expected);
          saveActiveIds(activeIds);
        }
      }
    }

    // Counted after resolution, not before: reading it from the pre-refresh
    // cache reported a shortfall that the explorer had already filled in.
    const missingCount = (expected || 0) - (activeIds ? activeIds.length : 0);

    if (!activeIds.length) {
      // Having no positions is a normal state, not an error. Bailing out here
      // dropped the whole table, so there was no way to see net worth, the
      // wallet, the gas price, or to reach Cleanup and Gas Topup at exactly the
      // moment those are most wanted: right after closing everything. Build the
      // same table with no rows instead.
      // Own timer: the main path's liveStartedAt is declared further down, and
      // reading it from here threw before initialisation, which killed the whole
      // fetch and left the table showing cache.
      const emptyLiveAt = Date.now();
      const [wallet, fallbackEthPrice] = await Promise.all([
        fetchWalletBalances(WALLET).catch(() => ({ eth: 0, weth: 0, usdg: 0 })),
        fetchEthPrice().catch(() => null),
      ]);
      const ethPrice = fallbackEthPrice;
      if (ethPrice) _ethUsd = ethPrice;
      const walletUsd = wallet.usdg + (wallet.shitters || 0)
      + (ethPrice ? (wallet.eth + wallet.weth) * ethPrice : 0);
      timings.live = Date.now() - emptyLiveAt;
      timings.total = Date.now() - startedAt;
      timings.positions = Date.now() - startedAt;
      timings.idsDiag = idsDiag;
      const data = {
        rows: [], rowBudget: 0,
        totalValue: 0, totalFees: 0, totalClaimed: 0, totalPnl: 0, totalBasis: 0, posCount: 0,
        // The footer reads openCount, not posCount; omitting it printed
        // "undefined open positions · IDs/positions NaNs".
        openCount: 0,
        inRangeCount: 0, tokenCount: 0, tokensInRange: 0,
        wallet, walletUsd, ethPrice,
        walletEth: wallet.eth + wallet.weth,
        walletUsdg: wallet.usdg,
      walletShitters: wallet.shitters || 0,
        walletShitters: wallet.shitters || 0,
        // Same definition as the main path: LP + fees + USDG, with no positions
        // making the first two zero. This used to be walletUsd, which counts ETH,
        // so closing everything quietly inflated net worth by the gas balance and
        // the figure dropped again on the next refresh with positions open.
        netWorth: wallet.usdg + (wallet.shitters || 0),
        gas: await gasPromise,
        timings,
        noPositions: true,
      };
      timings.total = Date.now() - startedAt;
      saveCache(data);
      return { widget: buildWidget(data, false, Date.now()), data, stale: false, dataTime: Date.now() };
    }

    // 2. Fetch position data. Usually already included in the first multicall.
    const { positions, poolKeys } = positionData || await fetchPositionData(activeIds);
    timings.positions = Date.now() - startedAt;
    timings.idsDiag = idsDiag;

    // 3. Everything after position data is independent: fire them all at once.
    // Token metadata, market data, supplies, pool prices, fees, and wallet
    // balances overlap into one big parallel batch so the widget pays one
    // round-trip's latency instead of seven serial ones.
    const allTokens = new Set();
    for (const [, pk] of poolKeys) { allTokens.add(pk.currency0); allTokens.add(pk.currency1); }
    const poolIdOf = new Map();
    for (const [key, pk] of poolKeys) poolIdOf.set(key, computePoolId(pk));

    const liveStartedAt = Date.now();
    const supplyAddrs = [...allTokens].filter(a => !STABLES[a]);
    const [, market, rawSupplies, poolPrices, feeRaw, wallet, fallbackEthPrice, tokenMarket] = await Promise.all([
      discoverTokens([...allTokens]),
      fetchPoolMarketData([...new Set(poolIdOf.values())]).catch(() => new Map()),
      fetchTotalSupplies(supplyAddrs).catch(() => new Map()),
      fetchPoolPrices(poolKeys),
      fetchFees(positions, poolKeys).catch(() => new Map()),
      fetchWalletBalances(WALLET).catch(() => ({ eth: 0, weth: 0, usdg: 0 })),
      fetchEthPrice().catch(() => null),
      fetchTokenMarketPrices([...allTokens]).catch(() => new Map()),
    ]);
    timings.live = Date.now() - liveStartedAt;
    const supplies = supplyUnits(rawSupplies, supplyAddrs);

    const ethPrice = ethPriceFromMarket(market) || fallbackEthPrice;
    if (ethPrice) _ethUsd = ethPrice;   // used to price gas in the progress log
    if (ethPrice) {
      STABLES[ZERO_TOKEN].usd = ethPrice;
      if (KNOWN_TOKENS[ZERO_TOKEN]) KNOWN_TOKENS[ZERO_TOKEN].usd = ethPrice;
    }

    // Mint timestamps for age. Only refetched when a position appears that we
    // have no time for, so the usual refresh pays nothing for this.
    let mintTimes = loadMintTimes();
    let mintBlocks = loadMintBlocks();
    // Blocks arrived after times did, so a wallet with every time cached asks
    // once more for its blocks, but at most daily: the page holds the newest
    // transfers only, and an old position it will never contain must not cost
    // an explorer call on every open.
    const blocksMeta = loadJson("mint_blocks_meta") || {};
    const wantBlocks = positions.some(p => !mintBlocks[p.tokenId])
      && Date.now() - (blocksMeta.lastAttempt || 0) > 86400 * 1000;
    const mintsPending = positions.some(p => !mintTimes[p.tokenId] || !mintBlocks[p.tokenId]);
    if (!config.runsInWidget && !quick && (positions.some(p => !mintTimes[p.tokenId]) || wantBlocks)) {
      const meta = loadJson("mints_meta") || {};
      if (Date.now() - (meta.lastAttempt || 0) > MINT_RETRY_MS) {
        try {
          const blocks = {};
          mintTimes = { ...mintTimes, ...(await fetchMintTimes(WALLET, blocks)) };
          saveMintTimes(mintTimes);
          mintBlocks = { ...mintBlocks, ...blocks };
          saveJson("mint_blocks", mintBlocks);
          // Only an answer counts as the day's attempt; a failed call must not
          // hold the blocks back for 24 hours.
          saveJson("mint_blocks_meta", { lastAttempt: Date.now() });
        } catch (e) {}
        saveJson("mints_meta", { lastAttempt: Date.now() });
      }
    }
    // The page above holds only the wallet's newest transfers. A wallet a copy
    // bot churns can push a live position's mint out of it within an hour or
    // two, leaving it with no age, no %/d and no claimed fees. Those are looked
    // up one NFT at a time instead, in the app, a dozen at most per run.
    if (!config.runsInWidget && !quick) {
      const miss = positions.filter(p => !mintTimes[p.tokenId] || !mintBlocks[p.tokenId]).slice(0, 12);
      const found = await Promise.all(miss.map(async p => {
        try {
          const r = new Request(explorerUrl(`/api/v2/tokens/${POSITION_MANAGER}/instances/${p.tokenId}/transfers`));
          r.headers = explorerHeaders();
          r.timeoutInterval = 10;
          const j = await r.loadJSON();
          const m = ((j && j.items) || []).find(t => t.type === "token_minting" && _lc(t.to && t.to.hash) === _lc(WALLET));
          return m ? { id: p.tokenId, block: Number(m.block_number), at: Math.floor(Date.parse(m.timestamp) / 1000) } : null;
        } catch (e) { return null; }
      }));
      let got = 0;
      for (const f of found) {
        if (!f || !Number.isSafeInteger(f.block) || !Number.isFinite(f.at)) continue;
        mintTimes[f.id] = f.at; mintBlocks[f.id] = f.block; got++;
      }
      if (miss.length) _mintLookupNote = `mints ${got}/${miss.length} found`;
      if (got) { saveMintTimes(mintTimes); saveJson("mint_blocks", mintBlocks); }
    }
    const claimedUsd = await updateClaims(positions, poolKeys, feeRaw, poolPrices, mintBlocks,
      pos => { const pp = poolPrices.get(pos.poolKeyStr); return pp ? computePositionValue(pos, pp).totalUsd : 0; },
      quick);
    const pending = quick && (mintsPending || claimedUsd.pending > 0);
    const nowSec = Date.now() / 1000;

    // 5. Compute values and merge by pair
    let totalValue = 0, totalPnl = 0, totalFees = 0, totalClaimed = 0, totalBasis = 0, inRangeCount = 0;
    const baseline = loadPnlBaseline();
    const newBaseline = { ...baseline };
    const pairMap = new Map();

    // Fixed order, so nothing below depends on the order the ids arrived in.
    const byId = (a, b) => { try { const x = BigInt(a.tokenId), y = BigInt(b.tokenId); return x < y ? -1 : x > y ? 1 : 0; }
                             catch (e) { return String(a.tokenId) < String(b.tokenId) ? -1 : 1; } };
    for (const pos of [...positions].sort(byId)) {
      const pp = poolPrices.get(pos.poolKeyStr);
      if (!pp) continue;

      const val = computePositionValue(pos, pp);
      totalValue += val.totalUsd;
      if (val.inRange) inRangeCount++;

      const fees = plausibleFees(feesUsd(pos, feeRaw.get(pos.tokenId), pp), val.totalUsd);
      totalFees += fees;
      const mintedAt = mintTimes[pos.tokenId] || null;

      // Unrealised PnL includes current unclaimed fees. Falls back to the
      // old first-sighting baseline
      // only when the basis is not derivable (a pair with no stablecoin side).
      const basis = initialUsd(pos);
      const key = String(pos.tokenId);
      let pnl, basisUsd;
      // Fees already claimed are income of this position too; see updateClaims.
      const led = claimedUsd.get(pos.tokenId);
      const claimed = led ? led.usd : 0;
      // A position that changed size since its mint: the deposit is the mint
      // (derived from the minted liquidity, single-sided) plus every add, and
      // anything taken out is a return. Deriving the basis from today's
      // liquidity would count compounded fees as money put in.
      const resized = led && led.L0 && ((led.addedUsd || 0) > 0 || (led.removedUsd || 0) > 0);
      const mintBasis = resized ? initialUsd({ ...pos, liquidity: BigInt(led.L0) }) : null;
      if (resized && mintBasis != null) {
        basisUsd = mintBasis + (led.addedUsd || 0);
        pnl = val.totalUsd + fees + claimed + (led.removedUsd || 0) - basisUsd;
      } else if (basis != null) {
        basisUsd = basis;
        pnl = val.totalUsd + fees + claimed - basis;
      } else {
        if (!(key in newBaseline)) newBaseline[key] = val.totalUsd;
        basisUsd = newBaseline[key] || val.totalUsd;
        pnl = val.totalUsd - basisUsd;
      }
      totalPnl += pnl;
      totalBasis += basisUsd;

      const sym0 = tokenSymbol(pos.currency0);
      const sym1 = tokenSymbol(pos.currency1);
      const q0 = quoteRank(pos.currency0), q1 = quoteRank(pos.currency1);
      const isStable0 = q0 > q1, isStable1 = q1 > q0;
      const pair = isStable0 ? sym1 : isStable1 ? sym0 : `${sym0}/${sym1}`;
      // Keyed by contract address, never by the rendered symbol. UNIPCS is at
      // least four separate contracts on this chain; keying by symbol merged the
      // second into the first's row, which then carried the first's address,
      // supply, orientation and current tick while accruing the second's value
      // and pools. The symbol stays as a label.
      const groupKey = isStable0 ? pos.currency1
                     : isStable1 ? pos.currency0
                     : `${pos.currency0}/${pos.currency1}`;

      if (!pairMap.has(groupKey)) {
        const tokenAddr = isStable0 ? pos.currency1 : pos.currency0;
        pairMap.set(groupKey, {
          pair, groupKey, totalUsd: 0, pnl: 0, fees: 0, baselineSum: 0, inRange: 0, posCount: 0, claimed: 0,
          tickMin: pos.tickLower, tickMax: pos.tickUpper, currentTick: pp.tick,
          flipRange: isStable0, tokenAddr, mintedAt: null,
          tokenPrice: tokenUsdPrice(tokenAddr, pos, pp),
          supply: supplies.get(tokenAddr) || null,
          pools: new Map(),
          // Identifies what this row would burn if its Close button is tapped.
          positions: [],
          // Every position's range and pool, for the range strip; see rangeRefPool.
          spans: [], poolSpans: new Map(),
        });
      }
      const g = pairMap.get(groupKey);
      {
        const tokPx = tokenUsdPrice(g.tokenAddr, pos, pp);
        const ps = g.poolSpans.get(pos.poolKeyStr)
          || { key: pos.poolKeyStr, tick: pp.tick, usd: 0,
               price: tokPx != null && Number.isFinite(tokPx) && tokPx > 0 ? tokPx : null };
        ps.usd += val.totalUsd || 0;
        g.poolSpans.set(pos.poolKeyStr, ps);
        g.spans.push({ key: pos.poolKeyStr, tickLower: pos.tickLower,
                       tickUpper: pos.tickUpper, inRange: !!val.inRange });
      }
      // Remembered for Cleanup, which cannot rely on the indexer alone.
      rememberTokens([pos.currency0, pos.currency1]);
      // Open positions' tokens are never flagged as spam (strikeSpam).
      if (pos.liquidity > 0n) stampLpTokens([pos.currency0, pos.currency1]);
      // Only positions that still hold liquidity are closable; a burnt-out NFT
      // would make the whole batch revert.
      if (pos.liquidity > 0n) {
        // Reentry needs the pool key and tick geometry, not just the id, so it
        // can rebuild the same shaped range around the current price.
        const entry = {
          tokenId: pos.tokenId,
          currency0: pos.currency0,
          currency1: pos.currency1,
          poolKeyStr: pos.poolKeyStr,
          poolKey: {
            currency0: pos.currency0, currency1: pos.currency1,
            fee: pos.fee, tickSpacing: pos.tickSpacing, hooks: pos.hooks,
          },
          tickLower: pos.tickLower, tickUpper: pos.tickUpper,
          tickSpacing: pos.tickSpacing,
          liquidity: pos.liquidity,
          currentTick: pp ? pp.tick : null,
          amount0: val.amount0, amount1: val.amount1,
        };
        g.positions.push(entry);
      }
      g.totalUsd += val.totalUsd;
      g.pnl += pnl;
      g.fees += fees;
      g.claimed += claimed;
      totalClaimed += claimed;
      g.baselineSum += basisUsd;
      g.posCount++;
      if (val.inRange) g.inRange++;
      g.tickMin = Math.min(g.tickMin, pos.tickLower);
      g.tickMax = Math.max(g.tickMax, pos.tickUpper);
      // Age of a merged row is the oldest position in it: that is when this pair
      // started earning, which is what the fee rate should be measured against.
      if (mintedAt && (!g.mintedAt || mintedAt < g.mintedAt)) g.mintedAt = mintedAt;
      // Volume is per pool, so a pair spread over several pools sums them; mcap is
      // a token-level figure, so it comes from whichever of them is deepest.
      const pid = poolIdOf.get(pos.poolKeyStr);
      if (pid && market.has(pid)) g.pools.set(pid, market.get(pid));
      // Price per pool, kept with its id. tokenPrice used to be whichever
      // position happened to be seen first, so holding the same token in two
      // pools valued it at an arbitrary one of them. Thin pools drift hard:
      // MEME quoted a 6.6x spread across its pools at one moment.
      if (pid && pp) {
        if (!g.poolPrices) g.poolPrices = new Map();
        const px = tokenUsdPrice(g.tokenAddr, pos, pp);
        if (px != null && Number.isFinite(px) && px > 0) g.poolPrices.set(pid, px);
      }
    }

    savePnlBaseline(newBaseline);

    const rows = [...pairMap.values()].map(g => {
      const ageDays = g.mintedAt ? Math.max((nowSec - g.mintedAt) / 86400, 1 / 1440) : null;
      // The strip is the reference pool's ladder against that pool's own price,
      // so the bar, the % and the cap ends always describe one consistent thing.
      // Positions in the token's other pools still count in the value and in
      // "n/m in range".
      const ref = rangeRefPool([...g.poolSpans.values()]);
      const strip = ref ? g.spans.filter(sp => sp.key === ref.key) : [];
      const stripLo = strip.length ? Math.min(...strip.map(sp => sp.tickLower)) : g.tickMin;
      const stripHi = strip.length ? Math.max(...strip.map(sp => sp.tickUpper)) : g.tickMax;
      return {
      pair: g.pair,
      groupKey: g.groupKey,
      tokenTag: g.tokenAddr ? addrTag(g.tokenAddr) : "",
      totalUsd: g.totalUsd,
      pnl: g.pnl,
      fees: g.fees,
      claimed: g.claimed,
      ageDays,
      // Fee yield per day, as a share of position value. Under about 15 minutes a
      // single swap dominates and scaling it to a day produces a wild number, so
      // show nothing rather than a rate that looks authoritative and is not.
      // Everything earned counts, claimed and unclaimed: a position that is
      // claimed often would otherwise read as earning almost nothing.
      feePctDay: (ageDays && ageDays >= 15 / 1440 && g.totalUsd > 0)
        ? ((g.fees + g.claimed) / g.totalUsd) * 100 / ageDays : null,
      // Market cap = on-chain supply x an address-level reference price: the
      // median of this token's pools that clear a depth floor, not whichever
      // single pool reports the most liquidity. Reported liquidity is not
      // executable depth, and one token's pools were measured disagreeing by
      // 137% to 7,613%.
      //
      // pickDeepestPrice only sees pools this wallet is in, so it is the last
      // resort rather than the basis. A DexScreener figure is used only from a
      // pool that lists this token as its base, since on a USDG pair the base is
      // often the stablecoin and its cap is not this token's.
      ...(() => {
        const tm = tokenMarket.get(g.tokenAddr) || {};
        const ref = tm.ref || null;
        const px = (ref && ref.px) || tm.px || pickDeepestPrice(g);
        if (g.supply && px > 0) {
          return {
            mcap: g.supply * px,
            mcapLow: g.supply * ((ref && ref.lowPx) || px),
            mcapHigh: g.supply * ((ref && ref.highPx) || px),
            mcapUncertain: !!(ref && ref.disagrees),
          };
        }
        const caps = [];
        for (const [, m] of g.pools) {
          if (m.baseAddr === g.tokenAddr && m.mcap != null && m.liq > 0) {
            caps.push({ px: Number(m.mcap), liq: m.liq });
          }
        }
        const q = mcapReference(caps);
        return q
          ? { mcap: q.px, mcapLow: q.lowPx, mcapHigh: q.highPx, mcapUncertain: q.disagrees }
          : { mcap: null, mcapLow: null, mcapHigh: null, mcapUncertain: true };
      })(),
      volH1: g.pools.size ? [...g.pools.values()].reduce((t, m) => t + (m.volH1 || 0), 0) : null,
      // What the pool is paying right now, on the last hour's volume scaled to a
      // day. Same metric and same window as the pool finder's "1h pace", so the
      // two tables can be read against each other. Distinct from the FEES
      // column, which is what THIS position has earned over its lifetime: a
      // position can look healthy there while the pool has already gone quiet.
      poolFeePctDay1h: (() => {
        let vol1h = 0, liq = 0, fee = 0;
        for (const [, m] of g.pools) { vol1h += m.volH1 || 0; liq += m.liq || 0; }
        if (!(liq > 0)) return null;
        for (const p of g.positions || []) {
          const f = p.poolKey && p.poolKey.fee;
          if (f > 0) { fee = f; break; }
        }
        if (!(fee > 0)) return null;
        return (vol1h * 24 * (fee / 1e6) / liq) * 100;
      })(),
      pnlPct: Number.isFinite(g.pnl) && g.baselineSum > 0 ? (g.pnl / g.baselineSum) * 100 : null,
      inRange: g.inRange > 0,
      inRangeCount: g.inRange,
      tickLower: stripLo,
      tickUpper: stripHi,
      currentTick: ref ? ref.tick : g.currentTick,
      // What the strip's colour follows, so a green % is never drawn against a
      // marker outside the bar: in range in the pool the strip is drawn for.
      barInRange: strip.length ? strip.some(sp => sp.inRange) : g.inRange > 0,
      // Needed by mcapAtTick: the range must be priced off this pool, not off
      // the cross-pool reference that r.mcap uses.
      supply: g.supply,
      // The reference pool's own price or none: borrowing another pool's here
      // would scale it by this pool's tick. With none, mcapAtTick uses r.mcap.
      poolTokenPrice: ref ? ref.price : g.tokenPrice,
      posCount: g.posCount,
      positions: planReentryGroup(g.positions),
      flipRange: g.flipRange,
      tokenAddr: g.tokenAddr,
      poolId: (() => { let b = null, bp = null; for (const [pid, m] of g.pools) if (!b || m.liq > b.liq) { b = m; bp = pid; } return bp; })(),
    };});
    // "In range" at the token level: a token counts when any of its positions is
    // in range, since that is when it is earning at all.
    const tokenCount = rows.length;
    const tokensInRange = rows.filter(r => r.inRange).length;
    // Largest PnL either way first (owner, 2026-10-06): a -$80 row is as much
    // worth a look as a +$80 one. Rows with no usable PnL (no priceable side,
    // so no cost basis) sink below everything rather than sorting as if flat.
    // Net worth = LP positions + unclaimed fees + USDG in the wallet. Fees are
    // claimable at any time so they count, even though they sit outside the
    // position value. ETH/WETH is shown alongside rather than counted.
    const walletUsd = wallet.usdg + (wallet.shitters || 0)
      + (ethPrice ? (wallet.eth + wallet.weth) * ethPrice : 0);
    // Loose tokens are value the wallet holds, so they count. ETH still does
    // not: it is gas, reported on its own line, and counting it here is the
    // split that made two screens disagree by the gas balance.
    const netWorth = totalValue + totalFees + wallet.usdg + (wallet.shitters || 0);
    const walletEth = wallet.eth + wallet.weth;

    rows.sort((a, b) => {
      const av = (a.pnl == null || isNaN(a.pnl)) ? -Infinity : Math.abs(a.pnl);
      const bv = (b.pnl == null || isNaN(b.pnl)) ? -Infinity : Math.abs(b.pnl);
      if (av !== bv) return bv - av;
      return b.totalUsd - a.totalUsd;
    });

    // Row budget for widget
    const CHROME = 194, PITCH = 24;
    const rowBudget = config.widgetFamily === "small" ? 0
      : Math.max(0, Math.min(10, Math.floor((widgetSize().h - CHROME + 11) / PITCH)));

    timings.total = Date.now() - startedAt;
    data = {
      pending,
      totalValue,
      netWorth,
      walletUsd,
      walletEth,
      walletUsdg: wallet.usdg,
      wallet,
      ethPrice,
      totalPnl,
      totalFees,
      totalClaimed,
      totalBasis,
      openCount: positions.length,
      inRangeCount,
      tokenCount,
      tokensInRange,
      rows,
      rowBudget,
      timings,
      missingCount: missingCount > 0 ? missingCount : 0,
      expectedCount: expected || 0,
      // Only with a key: without one the RPC is the public endpoint, and a
      // failed read here must not cost the whole table.
      gas: await gasPromise,
    };
    timings.total = Date.now() - startedAt;
    saveCache(data);
  } catch (e) {
    const c = loadCache();
    if (c) { data = c.data; stale = true; dataTime = c.t; error = String((e && e.message) || e).slice(0, 80); }
    else { const msg = "Couldn't load data: " + String((e && e.message) || e).slice(0, 100); return { widget: errorWidget(msg), data: null, message: msg }; }
  }

  return { widget: buildWidget({ ...data, rows: data.rows.slice(0, data.rowBudget || 6) }, stale, dataTime), data, stale, dataTime, error };
}

// ----------------------------- interactive table ------------------------------
// Only reachable when the script is run in the Scriptable app (including via the
// widget's tap URL). The widget rendering path above is untouched by any of this,
// and a failure here falls back to just showing the widget.
// One line under the header saying how current the rows are: yellow while a
// cached result is shown and the fetch is in flight, red when the fetch failed
// and the cached result is all there is, green when the rows are live. A tap
// on it fetches again.
function statusRow(t, kind, text, onTap, gas) {
  const r = new UITableRow();
  r.height = 22;
  const c = r.addText(`●  ${text}`);
  c.titleFont = Font.semiboldSystemFont(11);
  c.titleColor = kind === "refreshing" ? COL.yellow : kind === "fresh" ? COL.green : COL.red;
  if (gas && gas.gwei != null) {
    c.widthWeight = 68;
    // Own cell, right-aligned: the status text varies in length and would
    // otherwise truncate this off the end of the row.
    const g = r.addText(`⛽️ ${gas.gwei.toFixed(2)} gwei`);
    g.widthWeight = 32;
    g.rightAligned();
    g.titleFont = Font.semiboldSystemFont(11);
    g.titleColor = gas.spike == null ? COL.muted
                 : gas.spike >= 3   ? COL.red
                 : gas.spike >= 1.5 ? COL.yellow
                                    : COL.green;
  }
  if (onTap) { r.onSelect = onTap; r.dismissOnSelect = false; }
  t.addRow(r);
}
function statusText(stale, dataTime, refreshing, error) {
  if (refreshing === "claims") return `Updated ${fmtTimeSec(dataTime)}   ·   loading claimed fees and ages…`;
  if (refreshing) return `Refreshing…   ·   showing data from ${fmtTimeSec(dataTime)}`;
  if (stale) return `Showing cached data from ${fmtTimeSec(dataTime)} — refresh failed${error ? ": " + error : ""}   ·   tap to retry`;
  return `Updated ${fmtTimeSec(Date.now())}   ·   tap to refresh`;
}

// Fills (or refills) a table in place, so the same one can open on cached rows
// and be reloaded with fresh ones. "refreshing" is that first, cached state.

function fillTable(t, d, stale, dataTime, refreshing, error, onRefresh, onClose, busy, onReentry, onClaim, progress, onSweep, onGas, realised, onRealised, realisedOpen, onRealisedToggle) {
  t.removeAllRows();
  // Separators are table-wide in Scriptable, and the built-in ones would cut each
  // position off from its own range strip. Draw them by hand instead: a 1pt row
  // with a background colour, placed only where two groups meet.
  t.showSeparators = false;
  const divider = (gapAbove) => {
    if (gapAbove) { const sp = new UITableRow(); sp.height = gapAbove; sp.addText(""); t.addRow(sp); }
    const dv = new UITableRow();
    dv.height = 1;
    dv.backgroundColor = new Color("#8E8E93", 0.35);
    dv.addText("");
    t.addRow(dv);
  };
  const tir = d.tokensInRange != null ? d.tokensInRange : d.inRangeCount;
  const tc  = d.tokenCount != null ? d.tokenCount : d.openCount;

  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const cashTag = walletTag(d);
  const ethTag = cashTag ? `   ${cashTag}` : "";
  const hCell = header.addText(
    fmtUsd(d.netWorth != null ? d.netWorth : d.totalValue),
    `${NAME || shortWallet(WALLET)}   ·   LP ${fmtUsd(d.totalValue)}${ethTag}`
  );
  hCell.widthWeight = 55;
  hCell.titleFont = Font.boldSystemFont(20);
  hCell.subtitleFont = Font.systemFont(12);
  hCell.subtitleColor = COL.muted;

  // Total return on total invested. The per-row figure says how each trade is
  // doing; this one says how the book is doing. It sits under the money in the
  // smaller line rather than beside it, so the headline stays one number.
  const totalRoi = Number.isFinite(d.totalPnl) && (d.totalBasis || 0) > 0 ? (d.totalPnl / d.totalBasis) * 100 : null;
  const hPnl = header.addText(
    fmtUsd(d.totalPnl, true),
    (totalRoi != null ? `ROI ${totalRoi >= 0 ? "+" : ""}${totalRoi.toFixed(1)}%  \u2022  ` : "")
      + `fees ${fmtUsd(d.totalFees)}`
  );
  hPnl.widthWeight = 45;
  hPnl.rightAligned();
  hPnl.titleFont = Font.boldSystemFont(20);
  hPnl.titleColor = signColor(d.totalPnl);
  hPnl.subtitleFont = Font.systemFont(12);
  // The subtitle carries ROI and fees. Fees are never negative, so the ROI's
  // sign is the judgement and sets the colour of the line.
  hPnl.subtitleColor = totalRoi != null ? signColor(totalRoi) : COL.muted;
  header.onSelect = () => Safari.open(APP_URL);
  header.dismissOnSelect = false;
  t.addRow(header);

  // Range on its own line with its own colour. A table cell has one title and
  // one subtitle, each a single colour, so three independently coloured lines
  // need a second row: green when everything is in range, yellow when some is,
  // red when nothing is earning.
  const rangeRow = new UITableRow();
  rangeRow.height = 22;
  rangeRow.isHeader = true;
  const rl = rangeRow.addText(""); rl.widthWeight = 55;
  const rr = rangeRow.addText(`${tir}/${tc} in range`);
  rr.widthWeight = 45;
  rr.rightAligned();
  rr.titleFont = Font.systemFont(12);
  rr.titleColor = tc === 0 ? COL.muted : tir === tc ? COL.green : tir > 0 ? COL.yellow : COL.red;
  rangeRow.onSelect = () => Safari.open(APP_URL);
  rangeRow.dismissOnSelect = false;
  t.addRow(rangeRow);
  statusRow(t, refreshing ? "refreshing" : stale ? "stale" : "fresh", statusText(stale, dataTime, refreshing, error), () => onRefresh(false, true), d.gas);
  divider(0);

  drawProgress(t, progress, divider);

  if (onClose) {
    const closable = (d.rows || []).filter(r => (r.positions || []).length);
    const nPos = closable.reduce((s, r) => s + r.positions.length, 0);
    const totalFees = (d.rows || []).reduce((s, r) => s + (r.fees || 0), 0);
    const withFees = (d.rows || []).filter(r => (r.fees || 0) > 0);

    const ar = new UITableRow();
    ar.height = 42;
    const mkAll = (title, enabled, color, tap) => {
      const b = ar.addButton(busy ? "…" : title);
      b.widthWeight = 33;
      b.centerAligned();
      b.titleFont = Font.boldSystemFont(14);
      b.titleColor = busy || !enabled ? COL.faint : color;
      if (!busy && enabled) b.onTap = tap;
    };
    mkAll(totalFees > 0 ? `CLAIM · ${fmtUsd(totalFees)}` : "NO FEES",
          withFees.length > 0, COL.green, () => onClaim(withFees, "all fees"));
    mkAll(nPos ? `${DRY_RUN ? "PRACTICE" : "CLOSE"} · ${nPos}` : "NOTHING",
          nPos > 0, DRY_RUN ? COL.yellow : COL.accent, () => onClose(closable, "all positions"));
    // Always offered: it recovers tokens the position table no longer knows
    // about, which is exactly the case where there is nothing else to act on.
    mkAll("CLEANUP", true, COL.text, () => onSweep());
    t.addRow(ar);
    divider(0);
  }

  // An incomplete table must announce itself. This is the one failure mode that
  // silently omits money: the rows look ordinary and a position is simply gone.
  if (d.missingCount > 0) {
    const warn = new UITableRow();
    warn.height = 44;
    const wc = warn.addText(
      `\u26A0\uFE0F ${d.missingCount} position${d.missingCount === 1 ? "" : "s"} not loaded`,
      `The chain reports ${d.expectedCount}, this table has ${d.expectedCount - d.missingCount}. `
      + `The explorer is behind or unreachable. Tap to retry.`);
    wc.titleFont = Font.semiboldSystemFont(14);
    wc.titleColor = COL.yellow;
    wc.subtitleFont = Font.systemFont(11);
    wc.subtitleColor = COL.muted;
    warn.onSelect = () => onRefresh(false, true);
    warn.dismissOnSelect = false;
    t.addRow(warn);
    divider(0);
  }

  // With nothing to list, column headings over empty space read as a failure.
  // Say the state plainly instead; everything else on the table still works.
  if (!(d.rows || []).length) {
    const none = new UITableRow();
    none.height = 44;
    const nc = none.addText("No open positions",
      "Wallet, gas price, Cleanup and Gas Topup all still work here.");
    nc.centerAligned();
    nc.titleFont = Font.semiboldSystemFont(15);
    nc.titleColor = COL.muted;
    nc.subtitleFont = Font.systemFont(11);
    nc.subtitleColor = COL.faint;
    t.addRow(none);
    divider(0);
  }

  // Two-line labels, one line per line of the cells under them, so none of
  // them is cut off in a quarter-width column on a phone. Only worth drawing
  // when there is something underneath them.
  if ((d.rows || []).length) {
    const labels = new UITableRow();
    labels.height = 34;
    labels.isHeader = false;
    const l1 = labels.addText("POSITION", "in range · age");  l1.widthWeight = 28;
    const l2 = labels.addText("PnL", "roi · value");                l2.widthWeight = 25; l2.rightAligned();
    const l3 = labels.addText("FEES", "%/d · claimed");             l3.widthWeight = 25; l3.rightAligned();
    const l4 = labels.addText("MCAP", "1h pace");             l4.widthWeight = 22; l4.rightAligned();
    for (const c of [l1, l2, l3, l4]) {
      c.titleFont = Font.semiboldSystemFont(10); c.titleColor = COL.faint;
      c.subtitleFont = Font.systemFont(9);       c.subtitleColor = COL.faint;
    }
    t.addRow(labels);
    divider(0);
  }



  for (const r of d.rows || []) {
    const row = new UITableRow();
    row.height = 54;

    // No contract suffix on the row. A symbol is not an identity here either,
    // but the grouping is keyed by address now, so two contracts with one name
    // can no longer merge into a single row; the suffix only reported a hazard
    // that the keying already removes. The row still carries tokenTag for
    // anything that needs to tell them apart.
    const name = row.addText(
      r.pair,
      `${r.inRangeCount}/${r.posCount} in range   ${fmtAge(r.ageDays)}`
    );
    name.widthWeight = 28;
    name.titleFont = Font.boldSystemFont(16);
    name.subtitleFont = Font.systemFont(11);
    name.subtitleColor = r.inRangeCount > 0 ? COL.green : COL.red;

    const val = row.addText(
      // Title stays one line. ROI goes in the subtitle beside value: putting it
      // on the title wrapped the cell to three lines and broke row alignment.
      fmtUsd(r.pnl, true),
      (r.pnlPct != null ? `${r.pnlPct >= 0 ? "+" : ""}${r.pnlPct.toFixed(1)}% · ` : "") + fmtUsd1(r.totalUsd));
    val.widthWeight = 25;
    val.rightAligned();
    val.titleFont = Font.semiboldSystemFont(16);
    // PnL is the headline now, so the sign colour belongs on the title. The
    // subtitle is position value, which has no sign to convey.
    val.titleColor = signColor(r.pnl);
    val.subtitleFont = Font.systemFont(11);
    val.subtitleColor = r.pnlPct != null ? signColor(r.pnlPct) : COL.muted;

    const fee = row.addText(
      fmtUsd(r.fees),
      // Short on every row so a row with claims fits on one line and all rows
      // read alike; the column header says which figure is which.
      (r.feePctDay != null ? `${r.feePctDay.toFixed(1)}%/d` : "--")
        + ((r.claimed || 0) >= 0.005 ? ` · ${fmtClaimed(r.claimed)}` : "")
    );
    fee.widthWeight = 25;
    fee.rightAligned();
    fee.titleFont = Font.semiboldSystemFont(16);
    fee.titleColor = r.fees > 0 ? COL.green : COL.muted;
    fee.subtitleFont = Font.systemFont(11);
    fee.subtitleColor = feeRateColor(r.feePctDay);

    const mkt = row.addText(fmtCompact(r.mcap),
      r.poolFeePctDay1h != null
        ? `1h ${r.poolFeePctDay1h.toFixed(r.poolFeePctDay1h >= 100 ? 0 : 1)}%/d`
        : "--");
    mkt.widthWeight = 22;
    mkt.rightAligned();
    mkt.titleFont = Font.semiboldSystemFont(15);
    mkt.titleColor = COL.text;
    mkt.subtitleFont = Font.systemFont(11);
    mkt.subtitleColor = r.poolFeePctDay1h != null ? feeRateColor(r.poolFeePctDay1h) : COL.muted;


    // Per-token DexScreener link: the whole reason the table exists, since a
    // widget cannot carry a separate tap target per row.
    row.onSelect = () => Safari.open(`https://dexscreener.com/robinhood/0x${r.poolId || r.tokenAddr}`);
    row.dismissOnSelect = false;
    t.addRow(row);

    // Range strip: the market cap at each end of the range with the price marker
    // between them, so the row reads "covers X to Y of cap, price is here".
    // Market cap at each end of the range: the token's cap scaled by the price
    // ratio between the current tick and each bound.
    //
    // Every figure in this table is derived, and the range bar's own ends are no
    // less certain than the cap in the MCAP column they come from, so they carry
    // no marker of their own. A dash means the cap or the current tick is not
    // known, never that a known figure is being withheld.
    const mA = mcapAtTick(r, r.tickLower), mB = mcapAtTick(r, r.tickUpper);
    const capLo = (mA != null && mB != null) ? Math.min(mA, mB) : null;
    const capHi = (mA != null && mB != null) ? Math.max(mA, mB) : null;
    const showCap = v => v == null ? "--" : fmtCompact(v);
    const rp = rangePct(r);
    const strip = new UITableRow();
    strip.height = 22;
    const sLo = strip.addText(showCap(capLo));
    sLo.widthWeight = 16;
    sLo.titleFont = Font.systemFont(11);
    sLo.titleColor = COL.muted;
    const sBar = strip.addImage(rangeBar(r.barInRange != null ? r.barInRange : r.inRange, r.tickLower, r.tickUpper, r.currentTick, 200, 12, r.flipRange));
    sBar.widthWeight = 48;
    sBar.centerAligned();
    const sHi = strip.addText(showCap(capHi));
    sHi.widthWeight = 16;
    sHi.titleFont = Font.systemFont(11);
    sHi.titleColor = COL.muted;
    sHi.rightAligned();
    const sPct = strip.addText(fmtRangePct(rp));
    sPct.widthWeight = 20;
    sPct.titleFont = Font.semiboldSystemFont(12);
    sPct.titleColor = rangeColor(r, rp);
    sPct.rightAligned();

    strip.onSelect = row.onSelect;
    strip.dismissOnSelect = false;
    t.addRow(strip);

    // Actions get a row of their own. On the data or range rows a near-miss hit
    // their onSelect and opened DexScreener instead; this row has no onSelect,
    // so missing a button does nothing at all.
    if (onClose) {
      const nPos = (r.positions || []).length;
      const nRe  = (r.positions || []).filter(p => p.reentry).length;
      const hasFees = (r.fees || 0) > 0;

      const acts = new UITableRow();
      acts.height = 38;

      const pad = acts.addText("");
      pad.widthWeight = 10;

      // A table button always renders in the system tint, so a greyed-out one is
      // indistinguishable from a live one. Every button stays tappable and the
      // handler says why it cannot act, instead of the tap doing nothing at all.
      const mk = (title, tap) => {
        const b = acts.addButton(busy ? "…" : title);
        b.widthWeight = 30;
        b.centerAligned();
        b.titleFont = Font.semiboldSystemFont(14);
        if (!busy) b.onTap = tap;
      };
      mk("Re-enter", () => onReentry([r], r.pair));
      mk("Claim", () => onClaim([r], r.pair));
      mk("Close", () => onClose([r], r.pair));
      t.addRow(acts);
    }
    divider(6);
  }

  // Wallet cash, so the net worth figure in the header is broken down rather than
  // just asserted.
  if (d.wallet) {
    const wr = new UITableRow();
    wr.height = 56;
    const parts = [];
    if (d.wallet.eth  > 0) parts.push(`${d.wallet.eth.toFixed(4)} ETH`);
    if (d.wallet.weth > 0) parts.push(`${d.wallet.weth.toFixed(4)} WETH`);
    if (d.wallet.usdg > 0) parts.push(`${d.wallet.usdg.toFixed(2)} USDG`);
    // Leftovers from a close that could not sell, a refused swap, or a claim of
    // something with no market. Counted, named, and countable at a glance.
    if (d.wallet.shitters > 0) {
      const n = (d.wallet.shitterList || []).length;
      parts.push(`${fmtUsd(d.wallet.shitters)} shitters${n > 1 ? ` (${n})` : ""}`);
    }
    const wc = wr.addText("Wallet", parts.length ? parts.join("   ") : "empty");
    wc.widthWeight = 53;
    wc.titleFont = Font.semiboldSystemFont(15);
    wc.subtitleFont = Font.systemFont(11);
    wc.subtitleColor = COL.muted;
    const wv = wr.addText(fmtUsd(d.walletUsd || 0), d.ethPrice ? `ETH ${fmtUsd(d.ethPrice)}` : "no ETH price");
    wv.widthWeight = 47;
    wv.rightAligned();
    wv.titleFont = Font.semiboldSystemFont(15);
    wv.subtitleFont = Font.systemFont(11);
    wv.subtitleColor = d.ethPrice ? COL.muted : COL.red;
    t.addRow(wr);

    // Gas runs out quietly: every action here costs ETH while everything else
    // in the wallet is USDG, so the balance above can look healthy while
    // nothing can actually be sent. Sits under the wallet row because that is
    // where the ETH figure is read.
    if (onGas) {
      const gr = new UITableRow();
      gr.height = 36;
      const gb = gr.addButton(busy ? "…" : "\u26FD\uFE0F Gas Topup");
      gb.centerAligned();
      gb.titleFont = Font.semiboldSystemFont(13);
      if (!busy) gb.onTap = () => onGas();
      t.addRow(gr);
    }
    divider(0);
  }

  divider(10);
  const nowMs = Date.now();
  const building = realised && realised.note != null;
  const rc = !building && realised && realised.counts ? realised.counts : null;
  const loaded = !building && realised && Array.isArray(realised.loadedDays)
    ? realised.loadedDays : null;
  const rLoaded = !building && realised && realised.totals ? realised.totals.pnlUsd : null;

  // One line, four things: what it is, what it covers, what it made, what a tap
  // does. Everything else about the snapshot lives further down where it is
  // wanted, not in the header where it has to be read past.
  const rHead = new UITableRow();
  rHead.height = 44;
  // Nothing here is computed while the table renders: it is read back from the
  // last stored snapshot, and it is only brought up to date when asked. Since
  // nothing refreshes on its own, the only thing standing between a stale figure
  // and a wrong read of it is saying how old it is.
  const todayLoaded = loaded ? loaded.indexOf(startOfLocalDay(nowMs)) >= 0 : false;
  const ageDays = realised && realised.builtAt ? (nowMs - realised.builtAt) / 86400000 : null;
  const rt = rHead.addText("REALISED"
      + (loaded ? (loaded.length === 1 ? "  \u00B7  TODAY" : `  \u00B7  ${loaded.length} DAYS`) : ""),
    building ? String(realised.note)
      : rc
      ? `${rc.closedGroups} closed \u00B7 `
        + (todayLoaded ? `${fmtAge(ageDays)} old` : "today not loaded")
      : "closed positions");
  rt.widthWeight = 50;
  rt.titleFont = Font.semiboldSystemFont(13);
  rt.titleColor = COL.muted;
  rt.subtitleFont = Font.systemFont(11);
  // Amber once the stored figures have stopped being current: today missing, or
  // more than an hour since the last look.
  rt.subtitleColor = building || !rc ? COL.faint
    : (!todayLoaded || ageDays > 1 / 24) ? COL.yellow : COL.faint;
  const rv = rHead.addText(
    building ? "\u2026" : rc ? fmtUsd(rLoaded, true) : "Calculate",
    building ? "working" : rc ? `tap to refresh ${REALISED_RETAIN_DAYS} days` : "");
  rv.widthWeight = 50;
  rv.rightAligned();
  rv.titleFont = Font.boldSystemFont(18);
  // Blue where the cell is a control rather than a result. Grey read as dead
  // text and the whole feature went unnoticed sitting on screen.
  rv.titleColor = building ? COL.muted : !rc ? COL.blue
    : rLoaded >= 0 ? COL.green : COL.red;
  rv.subtitleFont = Font.systemFont(11);
  rv.subtitleColor = building ? COL.faint : COL.blue;
  if (onRealised) {
    rHead.onSelect = () => onRealised({ week: true });
    rHead.dismissOnSelect = false;
  }
  t.addRow(rHead);

  // What went in, beside what came out. A sum of deposits, not cash: the same
  // dollars reopen many times a day, and each opening counts, so this is the
  // money put to work, not the money in the wallet.
  // It covers the same snapshot as the headline and the list: the days the
  // header counts. One line of at most ~330pt at 11pt, inside a 390pt phone.
  const deployed = rc && realised.totals ? fmtInvested(realised.totals.capitalUsd) : null;
  if (deployed) {
    const dr0 = new UITableRow();
    dr0.height = 22;
    const nDays = loaded ? loaded.length : null;
    const dc = dr0.addText(`${deployed} deployed`
      + (nDays ? ` in ${nDays} day${nDays === 1 ? "" : "s"}` : "")
      + " (summed, reused money counts again)");
    dc.titleFont = Font.systemFont(11);
    dc.titleColor = COL.faint;
    t.addRow(dr0);
  }

  if (!building && realised && realised.ladders) {
    const closed = realised.ladders
      .filter(l => l.status === "closed" && l.burnAt != null)
      .sort((a, b) => b.burnAt - a.burnAt);

    const days = new Set(closed.map(l => startOfLocalDay(l.burnAt)));
    // With one day loaded the header already names it and carries its total, so
    // a heading would only repeat itself.
    const showDayHeadings = days.size > 1;

    // Two running totals, both built oldest first, the order the money was
    // actually made in, then read off as the list renders newest first. The
    // overall one puts everything loaded on the top row; the per-day one resets
    // at each day so a day can be read on its own without the days below it
    // moving the number.
    const runningByKey = new Map();
    const dayRunningByKey = new Map();
    let acc = 0, dayAcc = 0, accDay = null;
    for (let k = closed.length - 1; k >= 0; k--) {
      const l = closed[k];
      const d = startOfLocalDay(l.burnAt);
      if (d !== accDay) { accDay = d; dayAcc = 0; }
      acc += l.pnlUsd;
      dayAcc += l.pnlUsd;
      runningByKey.set(l.key, acc);
      dayRunningByKey.set(l.key, dayAcc);
    }

    // Each heading refreshes its own day. A day loaded while it was still
    // running holds only what had closed by then, and until now the only way to
    // complete it was to rebuild today and hope, or reload the whole history.
    const dayRow = (label, count, total, dim, dayAt, capital) => {
      const dr = new UITableRow();
      const tappable = onRealised && dayAt != null && !building;
      const dayIn = fmtInvested(capital);
      // Two lines in either cell (a deployed figure, or "tap to refresh") take
      // the 38pt of the other two-line rows; one line keeps 28pt.
      dr.height = tappable || dayIn ? 38 : 28;
      const dl = dr.addText(`${label}   ${count}`, dayIn ? `${dayIn} deployed` : "");
      dl.widthWeight = 55;
      dl.titleFont = Font.semiboldSystemFont(11);
      dl.titleColor = COL.muted;
      dl.subtitleFont = Font.systemFont(9);
      dl.subtitleColor = COL.faint;
      const dv = dr.addText(fmtUsd(total, true), tappable ? "tap to refresh" : "");
      dv.widthWeight = 45;
      dv.rightAligned();
      dv.titleFont = Font.semiboldSystemFont(13);
      dv.titleColor = dim ? COL.muted : total >= 0 ? COL.green : COL.red;
      dv.subtitleFont = Font.systemFont(9);
      // Blue for the same reason the header is: a grey control reads as dead text.
      dv.subtitleColor = COL.blue;
      if (tappable) {
        dr.onSelect = () => onRealised({ day: dayAt });
        dr.dismissOnSelect = false;
      }
      t.addRow(dr);
    };

    // Every recent day gets a row, loaded or not.
    //
    // Headings used to be emitted only while walking the closes, so a day with
    // nothing cached produced no row at all: there was nothing to tap and no
    // way to ask for it. Yesterday was simply missing from the list, and the
    // only route to it was rebuilding today and hoping. A day that has not been
    // read is a different thing from a day that was read and was quiet, and now
    // it says which.
    const todayAt = startOfLocalDay(nowMs);
    const RECENT_DAYS = 7;
    const recent = [];
    for (let i = 0; i < RECENT_DAYS; i++) recent.push(todayAt - i * 86400000);
    const oldestRecent = recent[recent.length - 1];
    const emitted = new Set();
    const emitDay = (day) => {
      if (emitted.has(day)) return;
      emitted.add(day);
      const tot = (realised.byDay || []).find(e => e.at === day);
      const isLoaded = loaded && loaded.indexOf(day) >= 0;
      dayRow(dayLabel(day, nowMs),
             tot ? String(tot.count) : (isLoaded ? "\u2014" : "not read yet"),
             tot ? tot.pnlUsd : 0, !tot, day, tot ? tot.capitalUsd : null);
    };

    let currentDay = null;
    for (const l of closed) {
      const day = startOfLocalDay(l.burnAt);
      // Collapsed, the list stops at a week: after a year of trading a heading
      // per day is a screen of nothing. Expanding shows the lot.
      if (!realisedOpen && day < oldestRecent) continue;
      if (showDayHeadings && day !== currentDay) {
        currentDay = day;
        // Recent days newer than this one that had nothing in them, so the
        // sequence stays in date order with no silent gaps.
        for (const w of recent) if (w > day) emitDay(w);
        // Expanded, history reaches past the week. Everything still owed from
        // inside the week has to be laid down before crossing that line, or the
        // list jumps back to recent dates after showing older ones.
        if (day < oldestRecent) for (const w of recent) emitDay(w);
        emitDay(day);
      }
      if (!realisedOpen) continue;

      const row = new UITableRow();
      row.height = 38;
      // The slice count is whatever the group actually held.
      // How long the position was open, beside the slice count: both describe
      // the shape of the position rather than its result. Falls back to the
      // block span for snapshots written before mint times were kept, so an
      // existing history is not blank until it is rebuilt.
      const heldMs = l.mintAt != null && l.burnAt != null && l.burnAt > l.mintAt
        ? l.burnAt - l.mintAt
        : (l.mintBlock && l.burnBlock && l.burnBlock > l.mintBlock)
          ? (l.burnBlock - l.mintBlock) * APPROX_BLOCK_MS
          : null;
      // What went in sits with the shape of the position, last, so the PnL
      // column keeps its width; single-spaced so "5 slices · 2.3h · $1.1k in"
      // fits an iPhone's 130pt cell.
      // Past 25 characters the slices are written "10×", which keeps the worst
      // case ("10× · 12.3h · $148k in", ~110pt) inside the cell.
      const ladderIn = fmtInvested(l.capitalUsd);
      const shapeTail = (heldMs == null ? "" : ` \u00B7 ${fmtAge(heldMs / 86400000)}`)
        + (ladderIn ? ` \u00B7 ${ladderIn} in` : "");
      let shape = `${l.sliceCount} slice${l.sliceCount === 1 ? "" : "s"}` + shapeTail;
      if (shape.length > 25) shape = `${l.sliceCount}\u00D7` + shapeTail;
      const nm = row.addText(l.token, shape);
      nm.widthWeight = 36;
      nm.titleFont = Font.semiboldSystemFont(14);
      nm.subtitleFont = Font.systemFont(11);
      nm.subtitleColor = COL.muted;

      const roi = l.capitalUsd > 0 ? (l.pnlUsd / l.capitalUsd) * 100 : null;
      const pv = row.addText(fmtUsd(l.pnlUsd, true),
        (roi == null ? "" : `${roi >= 0 ? "+" : ""}${roi.toFixed(1)}%  \u00B7  `)
          + `fees ${fmtUsd(l.feesUsd)}`);
      pv.widthWeight = 32;
      pv.rightAligned();
      pv.titleFont = Font.semiboldSystemFont(15);
      pv.titleColor = l.pnlUsd >= 0 ? COL.green : COL.red;
      pv.subtitleFont = Font.systemFont(11);
      pv.subtitleColor = COL.faint;

      // The running total. The top row equals the headline above and is blue so
      // the two can be checked against each other; the rest stay quiet so the
      // column reads as a progression, not a second set of results.
      const cv = row.addText(fmtUsd(runningByKey.get(l.key), true),
        `${fmtUsd(dayRunningByKey.get(l.key), true)} \u00B7 ${fmtTime(l.burnAt)}`);
      cv.widthWeight = 32;
      cv.rightAligned();
      cv.titleFont = l === closed[0] ? Font.boldSystemFont(15) : Font.systemFont(14);
      cv.titleColor = l === closed[0] ? COL.blue : COL.muted;
      cv.subtitleFont = Font.systemFont(11);
      cv.subtitleColor = COL.faint;
      t.addRow(row);
    }

    if (!closed.length) {
      const er = new UITableRow();
      er.height = 30;
      const c = er.addText("", "nothing closed yet");
      c.subtitleFont = Font.systemFont(11);
      c.subtitleColor = COL.faint;
      c.centerAligned();
      t.addRow(er);
    }
    // Any of the last seven days older than the oldest close held nothing and
    // has not been read; either way it is tappable rather than absent.
    if (showDayHeadings) for (const w of recent) emitDay(w);

    // The day totals above are the summary. The positions behind them are the
    // detail, and a wallet accumulates those without limit, so they fold away.
    if (closed.length) {
      const tgr = new UITableRow();
      tgr.height = 36;
      const tgb = tgr.addButton(realisedOpen ? "Hide" : `Show ${closed.length}`);
      tgb.centerAligned();
      if (onRealisedToggle) tgb.onTap = () => onRealisedToggle();
      tgr.dismissOnSelect = false;
      t.addRow(tgr);
    }

    // Groups excluded from the total are shown, not hidden: a silently dropped
    // position is indistinguishable from one that made nothing.
    const review = realised.ladders.filter(l => l.status === "needs_review");
    if (review.length) {
      const rr = new UITableRow();
      rr.height = 30;
      const c = rr.addText(`\u26A0\uFE0F ${review.length} excluded`,
        review[0].warnings[0] || "liquidity changed during their life");
      c.titleFont = Font.systemFont(12);
      c.titleColor = COL.yellow;
      c.subtitleFont = Font.systemFont(10);
      c.subtitleColor = COL.faint;
      t.addRow(rr);
    }


    // No "load older".
    //
    // The cache keeps a week of closes, so a day loaded from beyond it showed
    // once and then fell outside the window on the next build: the button
    // offered work whose result could not be kept. Older history is still on
    // chain and still reachable by raising REALISED_RETAIN_DAYS; it is simply
    // not something to invite a tap for when the answer will be discarded.
  }

  const foot = new UITableRow();
  foot.height = 56;
  const timing = d.timings
    ? ` · IDs/positions ${(d.timings.positions / 1000).toFixed(1)}s · live ${(d.timings.live / 1000).toFixed(1)}s · total ${(d.timings.total / 1000).toFixed(1)}s`
      + (d.timings.idsDiag ? ` · ${d.timings.idsDiag}` : "")
    : "";
  const diag = ` · ${explorerNote()}${_mintLookupNote ? ` · ${_mintLookupNote}` : ""}`;
  const fCell = foot.addText(
    "Open LP Agent Portfolio",
    `${d.openCount} open slices${timing}${diag} · ${VERSION}`
  );
  fCell.titleFont = Font.semiboldSystemFont(15);
  fCell.titleColor = COL.blue;
  fCell.subtitleFont = Font.systemFont(11);
  fCell.subtitleColor = COL.faint;
  fCell.centerAligned();
  foot.onSelect = () => Safari.open(APP_URL);
  foot.dismissOnSelect = false;
  t.addRow(foot);
  divider(0);
}

// Table for when there is nothing to show: no cache and the fetch failed, or
// no positions. The status line carries the reason and a tap on it tries again.
// Progress log. Drawn by both the populated and the empty table so the outcome
// of an action stays on screen even when the refresh that follows finds nothing
// to list — which is exactly when the result matters most.
// Table cells truncate instead of wrapping, so long text is broken into lines
// here and each one gets its own row. Errors are the reason: they are the
// longest strings shown and the part you most need to read in full.
function wrapLines(text, width) {
  const words = String(text == null ? "" : text).split(/\s+/).filter(Boolean);
  const out = [];
  let cur = "";
  for (const w of words) {
    if (!cur) { cur = w; }
    else if ((cur + " " + w).length <= width) { cur += " " + w; }
    else { out.push(cur); cur = w; }
    while (cur.length > width) { out.push(cur.slice(0, width)); cur = cur.slice(width); }
  }
  if (cur) out.push(cur);
  return out.length ? out : [""];
}

function drawProgress(t, progress, divider) {
  if (!progress) return;
  // Progress log. Stays on screen after the work finishes so the outcome can
  // actually be read, and warns while running that the script must stay open.

    const head = new UITableRow();
    head.height = 38;
    const hpad = head.addText("");
    hpad.widthWeight = 6;
    const hc = head.addText(
      progress.running ? `Working: ${progress.title}` : (progress.outcome && progress.outcome.ok ? `Done: ${progress.title}` : `Problem: ${progress.title}`));
    hc.titleFont = Font.boldSystemFont(13);
    hc.widthWeight = 94;
    hc.titleColor = progress.running ? COL.yellow
                  : (progress.outcome && progress.outcome.ok) ? COL.green : COL.red;
    t.addRow(head);

    if (progress.running) {
      const warn = new UITableRow();
      warn.height = 26;
      const wpad = warn.addText("");
      wpad.widthWeight = 6;
      const wc = warn.addText("Keep this script open until it finishes.");
      wc.widthWeight = 94;
      wc.titleFont = Font.semiboldSystemFont(11);
      wc.titleColor = COL.yellow;
      t.addRow(warn);
    }

    for (const st of progress.steps) {
      const detail = wrapLines(st.detail, 44);
      const sr = new UITableRow();
      sr.height = st.detail ? 40 : 28;
      const spad = sr.addText("");
      spad.widthWeight = 6;
      const mark = st.state === "ok" ? "✓" : st.state === "fail" ? "✗"
                 : st.state === "skip" ? "–" : st.state === "info" ? "·" : "…";
      const c = sr.addText(`${mark}   ${st.label}`, detail[0] || "");
      c.widthWeight = 94;
      c.titleFont = Font.systemFont(12);
      c.titleColor = st.state === "ok" ? COL.green : st.state === "fail" ? COL.red
                   : st.state === "run" ? COL.yellow : COL.muted;
      c.subtitleFont = Font.systemFont(10);
      c.subtitleColor = COL.faint;
      t.addRow(sr);
      for (const extra of detail.slice(1)) {
        const er = new UITableRow();
        er.height = 20;
        const ep = er.addText(""); ep.widthWeight = 10;
        const ec = er.addText(extra); ec.widthWeight = 90;
        ec.titleFont = Font.systemFont(10);
        ec.titleColor = COL.faint;
        t.addRow(er);
      }
    }

    if (progress.outcome) {
      const gap = new UITableRow();
      gap.height = 8;
      gap.addText("");
      t.addRow(gap);

      const olines = wrapLines(progress.outcome.text, 42);
      const orow = new UITableRow();
      orow.height = 44;
      const opad = orow.addText("");
      opad.widthWeight = 6;
      const oc = orow.addText(olines[0], olines[1] || progress.outcome.gas || "");
      oc.widthWeight = 94;
      oc.titleFont = Font.semiboldSystemFont(12);
      oc.titleColor = progress.outcome.ok ? COL.green : COL.red;
      oc.subtitleFont = Font.systemFont(11);
      oc.subtitleColor = COL.muted;
      t.addRow(orow);
      for (const extra of olines.slice(2)) {
        const er = new UITableRow();
        er.height = 20;
        const ep = er.addText(""); ep.widthWeight = 6;
        const ec = er.addText(extra); ec.widthWeight = 94;
        ec.titleFont = Font.systemFont(11);
        ec.titleColor = progress.outcome.ok ? COL.green : COL.red;
        t.addRow(er);
      }
      if (progress.outcome.gas && olines.length > 1) {
        const gr = new UITableRow();
        gr.height = 22;
        const gp = gr.addText(""); gp.widthWeight = 6;
        const gc = gr.addText(progress.outcome.gas); gc.widthWeight = 94;
        gc.titleFont = Font.systemFont(11);
        gc.titleColor = COL.muted;
        t.addRow(gr);
      }
      // Retry, but only where nothing can already be in flight. progress.retry
      // is set exclusively for failures that stopped BEFORE broadcasting; a
      // transaction that was sent and lost its reply may well be mined, and
      // re-running it would close the same position twice.
      if (progress.retry && !progress.running) {
        const rr = new UITableRow();
        rr.height = 38;
        const rb = rr.addButton("\u21BB  Retry");
        rb.centerAligned();
        rb.titleFont = Font.semiboldSystemFont(14);
        rb.onTap = progress.retry;
        t.addRow(rr);
      }
    }
    divider(0);
  
  if (divider) divider(0);
}

function fillEmptyTable(t, message, refreshing, onRefresh, progress) {
  t.removeAllRows();
  const header = new UITableRow();
  header.isHeader = true;
  header.height = 72;
  const c = header.addText(WALLET ? (NAME || shortWallet(WALLET)) : "Uniswap V4 LP", "Uniswap V4 · Robinhood Chain");
  c.titleFont = Font.boldSystemFont(20);
  c.subtitleFont = Font.systemFont(12);
  c.subtitleColor = COL.muted;
  t.addRow(header);
  statusRow(t, refreshing ? "refreshing" : "stale",
            refreshing ? (message || "Refreshing…") : `${message}   ·   tap to retry`, onRefresh);
  drawProgress(t, progress, null);
}

// Widgets render the same view as the tracker. Close buttons are UITable-only,
// so nothing tappable can reach the home screen; closing needs the app.
if (config.runsInWidget) {
  let w;
  try {
    const fallback = new Promise(resolve => Timer.schedule(WIDGET_BUDGET_MS, false, () => {
      const c = loadCache();
      if (c) resolve({ widget: buildWidget({ ...c.data, rows: c.data.rows.slice(0, c.data.rowBudget || 6) }, true, c.t) });
      else resolve({ widget: errorWidget("Refresh timed out. Tap to open in app.") });
    }));
    w = (await Promise.race([main(), fallback])).widget;
  } catch (e) {
    const c = loadCache();
    w = c && c.data
      ? buildWidget({ ...c.data, rows: (c.data.rows || []).slice(0, 6) }, true, c.t)
      : errorWidget(String((e && e.message) || e).slice(0, 80));
  }
  Script.setWidget(w);
} else if (!PRIV) {
  const t = new UITable();
  fillEmptyTable(t, "Set PRIVATE_KEY at the top of the script (64 hex characters)", false, null, null);
  await t.present(true);
} else if (!RPC_KEYED) {
  const t = new UITable();
  fillEmptyTable(t, "Set ALCHEMY_API_KEY at the top of the script", false, null, null);
  await t.present(true);
} else {
  const t = new UITable();
  let shown = null, current = null, busy = false, notice = null, progress = null;
  // Realised PnL is a stored snapshot, never computed during a render.
  let realised = null, realisedNote = null;
  // Folded away on open. A wallet accumulates closed positions without limit and
  // the list is the tallest thing in this table.
  let realisedOpen = false;
  await fetchSharedRealised(3000);
  try { realised = loadRealised(); } catch (e) {}

  const show = (r, refreshing) => {
    if (r.data && r.data.rows) {
      fillTable(t, r.data, r.stale, r.dataTime, refreshing,
                notice || r.error, refresh, onClose, busy, onReentry, onClaim, progress, onSweep, onGas,
                realisedNote ? { note: realisedNote } : realised, onRealised,
                realisedOpen, onRealisedToggle);
    } else {
      fillEmptyTable(t, r.message || "Nothing loaded", refreshing, refresh, progress);
    }
    if (shown) t.reload(); else shown = t.present(false);
  };

  const onRealisedToggle = () => {
    realisedOpen = !realisedOpen;
    if (current) show(current, false);
  };

  const onRealised = async (opts) => {
    if (busy) return;
    // A whole week, newest first, each day built the same way a single tap
    // builds it. Newest first because that is the order they are read in, so
    // the numbers the user is looking at settle before the ones further down.
    if (opts && opts.week) {
      // A rebuild, not a refresh.
      //
      // This used to call the per-day build seven times, each one handed the
      // stored snapshot and the cached transfer scan. Both are reuse
      // mechanisms: a day whose ladders were already valued is kept verbatim,
      // and discovery only sees transfers the cache holds. So the one control
      // a user reaches for when a day looks wrong was the one that could not
      // fix it -- it rebuilt nothing it already believed, including whatever
      // was missing. Everything is dropped first and the seven days are built
      // from chain state.
      const a = new Alert();
      a.title = `Rebuild ${REALISED_RETAIN_DAYS} days?`;
      a.message = "Throws away the stored history and the cached transfer scan, "
        + "then rebuilds every day from chain state.\n\nThis is the slow one: it "
        + "re-walks your whole transfer history and re-values every close. "
        + "Minutes, not seconds. Use it when a day looks wrong or something is "
        + "missing; the per-day taps are the cheap way to pick up new closes.";
      a.addAction("Rebuild");
      a.addCancelAction("Cancel");
      if ((await a.presentAlert()) !== 0) return;
      realised = null;
      realisedNote = "Rebuilding " + REALISED_RETAIN_DAYS + " days\u2026";
      clearTransferCache();
      if (current) show(current, false);
      const todayAt = startOfLocalDay(Date.now());
      for (let i = 0; i < REALISED_RETAIN_DAYS; i++) {
        // rebuild: no per-day confirmation, and the first day starts from
        // nothing rather than from the snapshot just discarded.
        await onRealised({ day: todayAt - i * 86400000, rebuild: true });
        // A failed day leaves its reason on screen. Carrying on would spend
        // minutes reproducing the same failure six more times.
        if (realisedNote) break;
      }
      return;
    }
    // Either a specific day, or "whatever is older than this", which the build
    // resolves for itself once it has scanned far enough to know.
    const olderThan = Number.isFinite(opts && opts.olderThan) ? opts.olderThan : null;
    const day = olderThan != null ? null : startOfLocalDay(
      Number.isFinite(opts && opts.day) ? opts.day : Date.now());
    // Refreshing never rebuilds a span. A day that has already ended cannot gain
    // a close, so only today can differ, and everything already valued is
    // reused: asking to "recalculate three days" would have been three days of
    // reading to confirm two of them had not moved.
    busy = true;
    // The very first calculation is the only expensive one: it walks the whole
    // explorer history to find where positions were minted. Everything after
    // that is a check, and a check does not deserve a dialog.
    if (!realised && !(opts && opts.rebuild)) {
      const a = new Alert();
      a.title = "Calculate realised PnL?";
      a.message = "Reads every position you closed today back from chain state, "
        + "and walks your transfer history once to find where they were opened.\n\n"
        + "It makes a few hundred read-only calls. Nothing is sent and nothing is spent.";
      a.addAction("Calculate");
      a.addCancelAction("Cancel");
      if ((await a.presentAlert()) !== 0) { busy = false; return; }
    }
    try {
      const snap = await buildRealisedSnapshot(WALLET, (msg) => {
        realisedNote = msg;
        if (current) show(current, false);
      }, olderThan != null ? { olderThan, previous: realised } : { day, previous: realised });
      saveRealised(snap);
      realised = snap;
      realisedNote = null;
    } catch (e) {
      realisedNote = "Could not finish: " + String((e && e.message) || e).slice(0, 90);
    }
    busy = false;
    show(current || { data: null, message: "Loading…" }, false);
  };
  // Closing is destructive and irreversible, so it always goes through an
  // explicit choice naming exactly what will happen.
  // Recover tokens the position table no longer tracks. Confirmed first, with
  // what will actually be sold, because it moves real balances.
  // Buy ETH with USDG so there is gas to keep acting. Amount is in dollars
  // because that is how the balance above reads, and USDG is a dollar stable.
  async function onGas() {
    if (busy) return;
    const held = (current && current.data && current.data.wallet && current.data.wallet.usdg) || 0;
    const ask = new Alert();
    ask.title = `${DRY_RUN ? "Practice: gas topup" : "Gas Topup"}`;
    ask.message = `How much USDG to spend on ETH?\n\n`
      + `Wallet holds ${fmtUsd(held)} USDG`
      + (current && current.data && current.data.wallet
          ? ` and ${current.data.wallet.eth.toFixed(4)} ETH` : "")
      + `.\n\nSold at no worse than ${MAX_TOTAL_LOSS_PERCENT}% below the market price, `
      + `counting the ${ROUTER_SLIPPAGE_PERCENT}% the router may slip after quoting.`
      + (DRY_RUN ? "\n\nPRACTICE MODE: nothing will be sent." : "");
    ask.addTextField("USDG amount, e.g. 20", "20");
    if (DRY_RUN) ask.addAction("Practice run"); else ask.addDestructiveAction("Buy ETH");
    ask.addCancelAction("Cancel");
    if ((await ask.presentAlert()) !== 0) return;

    const usd = Number(String(ask.textFieldValue(0)).replace(/[^0-9.]/g, ""));
    if (!(usd > 0)) {
      progress = { title: "Gas topup", steps: [], running: false,
        outcome: { ok: false, text: "Enter an amount greater than zero." } };
      return show(current, false);
    }
    if (held > 0 && usd > held) {
      progress = { title: "Gas topup", steps: [], running: false,
        outcome: { ok: false, text: `Wallet holds ${fmtUsd(held)} USDG, which is less than ${fmtUsd(usd)}.` } };
      return show(current, false);
    }
    // USDG is 6 decimals on this chain.
    const raw = BigInt(Math.round(usd * 1e6));
    await run(step => swapTokenList([{ addr: USDG_ADDR, name: "USDG", amountRaw: raw }], ZERO_TOKEN, step),
              `gas topup ${fmtUsd(usd)}`, "Topped up gas with");
  }

  // Both limits are asked for rather than fixed.
  //
  // A leftover refused at 5.4% against a flat 5% stayed in the wallet decaying
  // while a 5.4% exit was on the table, and there was no way to say "that is
  // fine, take it" short of editing the script. The guard is still a guard: the
  // number is just the operator's to set, once, at the moment of selling.
  //
  // The two are not the same thing and are asked for separately. Max loss is
  // measured against an independent market price and is what actually protects
  // against a bad route. Slippage is only the gap the router may open between
  // its own quote and the fill, and raising it protects nothing.
  // What Cleanup skips as spam, and a way to take a token off the list. An
  // un-flagged token is priced and quoted again on the next cleanup and cannot
  // be flagged again for a week (strikeSpam).
  async function onSpamList() {
    const list = Object.entries(spamTokens()).sort((x, y) => y[1].at - x[1].at);
    const strikes = liveEntries("spam_strikes", SPAM_TTL_MS);
    const day = ms => new Date(ms).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    const a = new Alert();
    a.title = "Spam tokens";
    const lines = list.map(([addr, t]) =>
      `${t.name}: ${t.why}\nflagged ${day(t.at)}, rechecked ${day(t.at + SPAM_TTL_MS)}\n0x${addr.slice(0, 6)}\u2026${addr.slice(-4)}`);
    const struck = Object.entries(strikes).filter(([addr]) => !spamExempt(addr))
      .sort((x, y) => x[1].at - y[1].at);
    a.message = (lines.length ? lines.join("\n\n")
        : "Nothing flagged. Cleanup quotes every token with a market.")
      + (struck.length ? `\n\nOne strike so far (flagged on a second failed cleanup an hour or more later, `
        + `or now by hand): ` + struck.map(([addr, t]) =>
          `${t.name} 0x${addr.slice(0, 6)}\u2026${addr.slice(-4)} (${t.why}, ${day(t.at)})`).join(", ") : "")
      + "\n\nTokens from your open positions in the last 7 days are never flagged.";
    for (const [, t] of list) a.addAction(`Un-flag ${t.name}`);
    const allIdx = list.length > 1 ? list.length : -2;
    if (list.length > 1) a.addDestructiveAction("Un-flag all");
    const flagBase = list.length + (list.length > 1 ? 1 : 0);
    for (const [, t] of struck) a.addAction(`Flag ${t.name} now`);
    a.addCancelAction("Done");
    const i = await a.presentAlert();
    if (i < 0) return;
    let text;
    if (i >= flagBase) {
      const [addr, t] = struck[i - flagBase];
      flagSpamNow(addr);
      text = `Flagged ${t.name}: cleanup skips it without quoting until ${day(Date.now() + SPAM_TTL_MS)}.`;
    } else {
      const picked = i === allIdx ? list.map(([addr]) => addr) : [list[i][0]];
      unflagSpam(picked);
      text = `Un-flagged ${picked.length === 1 ? list[i][1].name : picked.length + " tokens"}: `
        + "the next cleanup prices and quotes it again.";
    }
    progress = { title: "Spam tokens", steps: [], running: false, outcome: { ok: true, text } };
    show(current, false);
  }

  async function onSweep() {
    if (busy) return;
    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: cleanup" : "Cleanup"}`;
    a.message = `Sells every leftover token worth ${fmtUsd(MIN_SWEEP_USD)} or more.\n\n`
      + `Max loss is measured against the market price from pools the trade does `
      + `not route through. Slippage is only what the router may give up between `
      + `quoting and filling.\n\n`
      + `USDG, ETH and WETH are never sold. Dust and tokens with no market are `
      + `left alone: they cost more in gas than they are worth.`
      + (DRY_RUN ? "\n\nPRACTICE MODE: nothing will be sent." : "");
    a.addTextField("Max loss %", String(MAX_TOTAL_LOSS_PERCENT));
    a.addTextField("Slippage %", String(ROUTER_SLIPPAGE_PERCENT));
    // USDG only. Every sale in this script comes back to USDG; the "to ETH"
    // variant was never used and doubled every dialog's choices for nothing.
    // Buying ETH for gas is its own button and is unaffected.
    if (DRY_RUN) a.addAction("Sell to USDG"); else a.addDestructiveAction("Sell to USDG");
    const nSpam = Object.keys(spamTokens()).length;
    a.addAction(`Spam tokens (${nSpam})`);
    a.addCancelAction("Cancel");
    const choice = await a.presentAlert();
    if (choice === 1) return onSpamList();
    if (choice !== 0) return;

    // Typed input, so it is checked rather than trusted. A blank field means
    // the default; anything unreadable stops the sale instead of quietly
    // becoming a number nobody chose.
    const readPct = (raw, name, dflt, hi) => {
      const t = String(raw == null ? "" : raw).trim().replace(/%$/, "");
      if (!t) return dflt;
      const v = Number(t);
      if (!Number.isFinite(v) || v <= 0) return { bad: `${name} "${t}" is not a number` };
      if (v > hi) return { bad: `${name} ${v}% is above the ${hi}% this will accept` };
      return v;
    };
    const maxLossPct = readPct(a.textFieldValue(0), "Max loss", MAX_TOTAL_LOSS_PERCENT, 50);
    const routerPct = readPct(a.textFieldValue(1), "Slippage", ROUTER_SLIPPAGE_PERCENT, 10);
    for (const v of [maxLossPct, routerPct]) {
      if (v && v.bad) {
        const e = new Alert();
        e.title = "Nothing was sold";
        e.message = v.bad;
        e.addCancelAction("OK");
        await e.presentAlert();
        return;
      }
    }

    const target = USDG_ADDR;
    await run(step => sweepWallet(target, step, { maxLossPct, routerPct }), "wallet cleanup",
              `Cleaned up into USDG `
              + `at up to ${maxLossPct}% loss, ${routerPct}% slippage`);
  }

  async function onClose(rows, label) {
    if (busy) return;
    const nPos = rows.reduce((s, r) => s + (r.positions || []).length, 0);
    if (!nPos) {
      progress = { title: label, steps: [], running: false,
        outcome: { ok: false, text: "Nothing to close: this row has no liquidity left." } };
      return show(current, false);
    }
    const usd  = rows.reduce((s, r) => s + (r.totalUsd || 0), 0);
    const fees = rows.reduce((s, r) => s + (r.fees || 0), 0);

    // Price it before asking. The dialog is the last point this can be declined.
    const qDeadline = BigInt(Math.floor(Date.now() / 1000) + MINUTES_TO_CONFIRM * 60);
    const qClose = await quoteFee(POSITION_MANAGER,
      buildCloseCalldata(rows, WALLET, qDeadline).calldata).catch(() => null);

    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: close" : "Close"} ${label}?`;
    a.message = `${nPos} position${nPos > 1 ? "s" : ""} across ${rows.length} token${rows.length > 1 ? "s" : ""}, `
      + `about ${fmtUsd(usd)} plus ${fmtUsd(fees)} of fees.\n\n`
      + `Swapping sells the tokens the close returns. It refuses anything that could end up more than ${MAX_TOTAL_LOSS_PERCENT}% below the market price. `
      + `Pools here are thin, so a swap that would move the price further than that reverts rather than filling badly.`
      + serviceFeeLine(fees, 0)
      + (feeQuoteLine(qClose) ? `\n\n${feeQuoteLine(qClose)}` : "")
      + (qClose && qClose.eth != null ? "\nA swap option adds a second transaction." : "")
      + "\n\nRe-entering closes the ladder, sells what it returns, and re-opens it "
      + "at the same width and slice count with its ORIGINAL capital, its top edge "
      + "at the current price. Anything it made above that cost stays in the wallet."
      + (DRY_RUN ? "\n\nPRACTICE MODE: nothing will be sent." : "");
    a.addAction("Close only");
    if (DRY_RUN) a.addAction("Close and swap to USDG");
    else a.addDestructiveAction("Close and swap to USDG");
    // A roll only makes sense for one ladder at a time: the new range is
    // anchored to one pool's price and funded by that pool's sale, so it is
    // offered only when the selection is a single pool.
    const rollable = rows.length === 1 && !planRollShape(
      rows.flatMap(r => r.positions || []),
      (rows.flatMap(r => r.positions || []).find(p => p.currentTick != null) || {}).currentTick).bad;
    if (rollable) {
      if (DRY_RUN) a.addAction("Close, swap and re-enter");
      else a.addDestructiveAction("Close, swap and re-enter");
    }
    a.addCancelAction("Cancel");

    const choice = await a.presentAlert();
    if (choice < 0 || choice > (rollable ? 2 : 1)) return;

    await run(async step => {
      if (choice === 0) return await closeRows(rows, step);
      if (choice === 1) return await closeAndSwapRows(rows, USDG_ADDR, step);
      return await rollRows(rows, step);
    }, label, choice === 0 ? "Closed" : choice === 1 ? "Closed and swapped" : "Rolled");
  }

  // Claiming takes only the accrued fees: liquidity and the position NFT are
  // left exactly as they are, so it is safe to do at any time.
  async function onClaim(rows, label) {
    if (busy) return;
    const fees = rows.reduce((s, r) => s + (r.fees || 0), 0);
    const nPos = rows.reduce((s, r) => s + (r.positions || []).length, 0);
    if (!(fees > 0)) {
      progress = { title: label, steps: [], running: false,
        outcome: { ok: false, text: "No fees have accrued on this position yet." } };
      return show(current, false);
    }

    const qDeadline = BigInt(Math.floor(Date.now() / 1000) + MINUTES_TO_CONFIRM * 60);
    const qClaim = await quoteFee(POSITION_MANAGER,
      buildClaimCalldata(rows, WALLET, qDeadline).calldata).catch(() => null);

    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: claim" : "Claim"} ${label}?`;
    a.message = `About ${fmtUsd(fees)} of fees from ${nPos} position${nPos > 1 ? "s" : ""}.\n\n`
      + `Your liquidity stays exactly where it is; only the earned fees are collected. `
      + `Swapping sells what is collected into one currency, refusing anything that could end up more than ${MAX_TOTAL_LOSS_PERCENT}% below the market price.`
      + serviceFeeLine(fees, 0)
      + (feeQuoteLine(qClaim) ? `\n\n${feeQuoteLine(qClaim)}` : "")
      + (qClaim && qClaim.eth != null && fees > 0 && qClaim.usd != null
          ? `\nThat is ${(qClaim.usd / fees * 100).toFixed(0)}% of the ${fmtUsd(fees)} being claimed.` : "")
      + (DRY_RUN ? "\n\nPRACTICE MODE: nothing will be sent." : "");
    a.addAction("Claim only");
    a.addAction("Claim and swap to USDG");
    a.addCancelAction("Cancel");

    const choice = await a.presentAlert();
    if (choice < 0 || choice > 1) return;

    await run(async step => {
      if (choice === 0) return await claimRows(rows, step);
      return await claimAndSwapRows(rows, USDG_ADDR, step);
    }, label, choice === 0 ? "Claimed fees on" : "Claimed");
  }

  // Re-entering keeps the range width and slides it to the current price, on the
  // same side, so the tokens the close returns fund the new position directly.
  async function onReentry(rows, label) {
    if (busy) return;
    const eligible = rows.map(r => ({ ...r, positions: (r.positions || []).filter(p => p.reentry) }))
                         .filter(r => r.positions.length);
    const n = eligible.reduce((s, r) => s + r.positions.length, 0);
    if (!n) {
      // Say precisely why, rather than leaving a tap with no visible effect.
      const why = rows.flatMap(r => (r.positions || []).map(p => p.reentryWhy)).filter(Boolean)[0];
      progress = { title: label, steps: [], running: false,
        outcome: { ok: false, text: why
          ? `Nothing to re-enter: ${why}.`
          : "Nothing to re-enter: positions must be out of range, on the USDG or ETH side, and not already at the current price." } };
      return show(current, false);
    }
    const sample = eligible[0].positions[0];
    const width = sample.tickUpper - sample.tickLower;

    // A single-sided deposit cannot straddle the price: USDG-only liquidity has
    // to sit entirely on one side, so the ladder always starts out of range and
    // begins earning only once price reaches it. How far that is depends on the
    // pool's tick grid, which can be coarse (a 401 spacing is 4.1% per step), so
    // say the distance rather than let it come as a surprise after the gas.
    const reTick = sample.currentTick;
    const spacingPct = (Math.pow(1.0001, sample.tickSpacing) - 1) * 100;
    let gapTicks = null;
    for (const r of eligible) {
      for (const p of r.positions) {
        const g = p.reentry.side === 1
          ? reTick - p.reentry.tickUpper
          : p.reentry.tickLower - reTick;
        if (gapTicks == null || g < gapTicks) gapTicks = g;
      }
    }
    const gapPct = gapTicks == null ? null : (Math.pow(1.0001, Math.max(0, gapTicks)) - 1) * 100;
    const gapLine = gapPct == null ? "" :
      `\n\n${entryDot(gapPct)} After this it earns nothing until the price falls ${gapPct.toFixed(2)}% to reach the range. `
      + `This pool's tick grid steps ${spacingPct.toFixed(2)}%, which is the closest it can be placed.`;

    const qDeadline = BigInt(Math.floor(Date.now() / 1000) + MINUTES_TO_CONFIRM * 60);
    const qRe = await quoteFee(POSITION_MANAGER,
      buildReentryCalldata(eligible, WALLET, qDeadline).calldata).catch(() => null);

    const a = new Alert();
    a.title = `${DRY_RUN ? "Practice: re-enter" : "Re-enter"} ${label}?`;
    a.message = `${n} out-of-range position${n > 1 ? "s" : ""} will be closed and immediately re-opened `
      + `at the current price, keeping the same range width (${width} ticks) and the same side.\n\n`
      + `It happens in one transaction, funded by what the close returns, so nothing is swapped and `
      + `no approval is needed. Any non-USDG tokens stay in your wallet.`
      + gapLine
      + serviceFeeLine(eligible.reduce((s, r) => s + (r.fees || 0), 0),
                       new Set(eligible.flatMap(r => r.positions.map(p => p.poolKeyStr))).size)
      + (feeQuoteLine(qRe) ? `\n\n${feeQuoteLine(qRe)}` : "")
      + (DRY_RUN ? "\n\nPRACTICE MODE: nothing will be sent." : "");
    if (DRY_RUN) a.addAction("Practice run"); else a.addDestructiveAction("Re-enter");
    a.addCancelAction("Cancel");
    if ((await a.presentAlert()) !== 0) return;

    // Retry restarts from onReentry rather than resending this attempt's plan.
    // It refreshes first and then looks the rows up again by pair, because the
    // captured `rows` describe positions as they were when this attempt began:
    // re-entry derives its ladder from the live tick and from which positions
    // still hold liquidity, and after a refresh those may both have changed.
    const keys = rows.map(r => r.groupKey);
    await run(step => reentryRows(eligible, step), label, "Re-entered", async () => {
      await refresh();
      const fresh = ((current && current.data && current.data.rows) || [])
        .filter(r => keys.includes(r.groupKey));
      if (!fresh.length) {
        progress = { title: label, steps: [], running: false, outcome: { ok: false,
          text: "Nothing left to re-enter: those positions are no longer open." } };
        return show(current, false);
      }
      onReentry(fresh, label);
    });
  }

  // Shared execution wrapper. Every stage is recorded in `progress` so the table
  // shows what is happening while it runs, and the finished log stays on screen
  // afterwards. The result used to be written to a notice that the follow-up
  // refresh immediately cleared, so outcomes were never actually readable.
  // onRetry, when given, is invoked from the Retry button. It must restart the
  // action from the top so the plan is rebuilt against current chain state: a
  // re-entry's ladder is derived from the live tick, and resending a stale plan
  // after the price moved would place the range in the wrong spot.
  async function run(action, label, doneVerb, onRetry) {
    progress = { title: label, steps: [], running: true, outcome: null };
    const rep = {
      start(l)        { progress.steps.push({ label: l, state: "run", detail: "" }); show(current, false); return progress.steps.length - 1; },
      detail(i, d)    { if (progress.steps[i]) { progress.steps[i].detail = d; show(current, false); } },
      ok(i, d)        { if (progress.steps[i]) { progress.steps[i].state = "ok";   progress.steps[i].detail = d || ""; show(current, false); } },
      fail(i, d)      { if (progress.steps[i]) { progress.steps[i].state = "fail"; progress.steps[i].detail = d || ""; show(current, false); } },
      skip(i, d)      { if (progress.steps[i]) { progress.steps[i].state = "skip"; progress.steps[i].detail = d || ""; show(current, false); } },
      note(l, d)      { progress.steps.push({ label: l, state: "info", detail: d || "" }); show(current, false); },
    };

    busy = true;
    _gasLog = [];
    show(current, false);
    try {
      const res = await action(rep);
      if (res.dryRun) {
        progress.outcome = { ok: true, text: `Practice run finished. Nothing was sent.` };
      } else if (res.pending) {
        progress.outcome = { ok: false, text: `Sent; confirmation could not be verified within 3 minutes. It may already have succeeded. Check the explorer before retrying.` };
      } else if (res.nothingToDo) {
        // Not a failure: there was simply nothing that needed doing.
        progress.outcome = { ok: true, text: `Nothing to do. Wallet is already clean.` };
      } else if (res.swapIncomplete) {
        // Closing succeeded, selling did not. Saying "closed" alone reads as
        // total success and the leftover tokens go unnoticed.
        progress.outcome = { ok: false, text:
          `${doneVerb} ${label}, but the swap did not finish`
          + (res.swaps ? `: ${res.swaps} sold, ${res.swapFailed} failed. ` : `: ${res.swapNote || "nothing was sold"}. `)
          + `The tokens are still in your wallet. Use CLEANUP to sell them.` };
      } else if (res.partial) {
        // Some sold, some did not. Saying "reverted" here would be wrong in
        // both directions: work did happen, and more is still outstanding.
        progress.outcome = { ok: true, text:
          `${doneVerb} ${label}, partly: ${res.swaps} sold, ${res.failed} could not be. `
          + `The rest are still in the wallet.` };
      } else if (res.ok) {
        progress.outcome = { ok: true, text: `${doneVerb} ${label}.` + (res.swaps ? ` ${res.swaps} swap${res.swaps > 1 ? "s" : ""} done.` : "") };
      } else if (!_gasLog.length) {
        // Nothing was broadcast: every sale was refused at the quote or price
        // check. "Reverted on chain" here sent the owner looking for a failed
        // transaction that never existed (fake USDT quoted at $0.00).
        progress.outcome = { ok: false, text: res.failed
          ? `Nothing sent: ${res.failed} token${res.failed === 1 ? " was" : "s were"} refused before any transaction. `
            + `Still in the wallet; details above.`
          : `Nothing sent. Nothing changed.` };
      } else {
        progress.outcome = { ok: false, text: `Reverted on chain. Nothing changed.` };
      }
    } catch (e) {
      const m = String((e && e.message) || e);
      // A throw here means the action stopped before broadcasting: simulation
      // reverted, a quote failed, the RPC was unreachable. Nothing is on chain,
      // so starting over is safe. Anything already sent reports through
      // res.pending instead and deliberately gets no Retry.
      if (onRetry) progress.retry = () => { if (!busy) onRetry(); };
      // NOT_MINTED means the position NFT no longer exists: it was already
      // closed, usually from another device or an earlier run whose result the
      // cached table had not caught up with. Nothing is wrong with the wallet.
      progress.outcome = { ok: false, text: /NOT_MINTED/i.test(m)
        ? `Already closed. That position no longer exists on chain, so there was `
          + `nothing to close. The table was showing a cached row; tap to refresh.`
        : /revert|execution/i.test(m)
        ? `Stopped before sending: ${m.slice(0, 100)}`
        : `Failed: ${m.slice(0, 110)}` };
    }
    // A close-and-swap can be four transactions; show what the whole thing cost.
    if (_gasLog.length && progress.outcome) {
      const total = _gasLog.reduce((t, g) => t + g.wei, 0n);
      const eth = Number(total) / 1e18;
      const usd = _ethUsd ? eth * _ethUsd : null;
      progress.outcome.gas = `gas ${eth.toFixed(6)} ETH`
        + (usd != null ? ` · ${fmtUsd(usd)}` : "")
        + (_gasLog.length > 1 ? ` · ${_gasLog.length} transactions` : "");
    }
    progress.running = false;
    busy = false;
    show(current, false);
    // Refresh the numbers but keep the log on screen.
    if (!DRY_RUN) await refresh(true);
  }

  // hard: the user tapped "tap to refresh". That is an explicit request for
  // current data, so the short-lived caches are cleared first. Without this a
  // refresh could be answered entirely from a price cache written seconds ago,
  // which looks exactly like the refresh doing nothing.
  const refresh = async (keepProgress, hard) => {
    if (busy) return;
    busy = true;
    notice = null;
    if (hard) { try { saveJson("token_prices", {}); } catch (e) {} }
    if (!keepProgress) progress = null;
    show(current || { data: null, message: "Loading…" }, true);
    const myGen = ++gen;
    // Quick pass first: value and PnL in seconds. Mint lookups and claimed-fee
    // replays wait on the explorer one position at a time, and with a copy bot
    // churning ~90 positions they held the whole table back for 1.5 to 7
    // minutes (2026-10-04). They run behind the table instead.
    try { current = await main({ quick: true }); }
    catch (e) { current = { data: null, message: "Couldn't load: " + String((e && e.message) || e).slice(0, 100) }; }
    busy = false;
    const more = !!(current && current.data && current.data.pending);
    show(current, more ? "claims" : false);
    if (more) fullPass(myGen);
  };
  // The full pass, behind the table. Buttons stay live meanwhile; if one is in
  // use when the pass finishes, its own refresh will show the new figures, so
  // the pass does not redraw over it.
  // gen counts refreshes: a pass that started before a later refresh (say, a
  // close) would otherwise land after it and put the older figures back.
  let fullRunning = false, gen = 0;
  const fullPass = async (myGen) => {
    if (fullRunning) return;
    fullRunning = true;
    let r = null;
    try { r = await main(); } catch (e) {}
    fullRunning = false;
    if (busy) return;
    if (myGen !== gen) {
      // Superseded. Its ledger and mint work is saved, so the next pass is short.
      if (current && current.data && current.data.pending) fullPass(gen);
      return;
    }
    if (r && r.data && r.data.rows) current = r;
    if (current) show(current, false);
  };

  const cached = loadCache();
  if (cached && cached.data && cached.data.rows) current = { data: cached.data, stale: false, dataTime: cached.t };
  await refresh();

  // No automatic refresh.
  //
  // Refreshing today on open sounded cheap: only one day, only new closes. In
  // practice the build is slow enough to be felt every single time the table is
  // opened, including the many times nothing has changed, so the cost landed on
  // every glance rather than on the moments the figures actually mattered. The
  // header refreshes the whole week on request instead, and each day heading
  // refreshes itself, so which work happens is the reader's choice.

  await shown;
}
Script.complete();
