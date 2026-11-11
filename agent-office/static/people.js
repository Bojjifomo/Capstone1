// Office workers as pixel art. Same seed -> same person, everywhere on the page.
// 12 x 18 pixels: hair, face, shirt, trousers, shoes. Poses: stand, walk (2 frames), sit, type (2 frames),
// coffee (on a break), phone (needs you). Drawn once per pose and cached as a canvas.
"use strict";

const People = (() => {
  const HAIR = {
    short:  ["....hhhh....", "...hhhhhh...", "..hhhhhhhh..", "..hh....hh.."],
    long:   ["....hhhh....", "...hhhhhh...", "..hhhhhhhh..", "..hhhhhhhh..", "..hh....hh.."],
    bun:    ["......hh....", "....hhhhh...", "...hhhhhh...", "..hhhhhhhh..", "..hh....hh.."],
    curly:  ["...hhhhhh...", "..hhhhhhhh..", ".hhhhhhhhhh.", ".hh.hhhh.hh.", "..hh....hh.."],
    cap:    ["....cccc....", "...cccccc...", "..cccccccc..", ".cccccccccc.", "..hh....hh.."],
    bald:   ["............", "....SSSS....", "...SSSSSS...", "............"],
    side:   ["....hhhh....", "...hhhhhh...", "..hhhhhhhh..", "..hhh...hh.."],
  };
  const FACE = ["..SSSSSSSS..", "..SESSSSES..", "..SSSSSSSS..", "...SSSSSS..."];
  const GLASSES = "..GEGSSGEG..";
  const BODY = {
    stand:  ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTTTK.", ".KTTTTTTTTK.", "..TTTTTTTT..", "..PPPPPPPP..", "..PPP..PPP..", "..PPP..PPP..", "..ooo..ooo.."],
    walk0:  ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTTTK.", "..TTTTTTTT..", "..TTTTTTTT..", "..PPPPPPPP..", "..PPP...PPP.", ".PPP.....PP.", ".ooo.....oo."],
    walk1:  ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTTTK.", "..TTTTTTTT..", "..TTTTTTTT..", "..PPPPPPPP..", "...PPP.PPP..", "...PPP.PPP..", "...ooo.ooo.."],
    sit:    ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTTTK.", "..TTTTTTTT..", "..PPPPPPPP.."],
    type0:  ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTTTK.", "..TTKTTKTT..", "..PPPPPPPP.."],
    type1:  ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTTTK.", "..TKTTTTKT..", "..PPPPPPPP.."],
    coffee: ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTmmK.", "..TTTTTTmmK.", "..PPPPPPPP.."],
    phone:  ["...TTTTTT...", "..TTTTTTTT..", ".KTTTTTTTTK.", ".KTTTTTTTTK.", "..TTTTTTTT..", "..PPPPPPPP..", "..PPP..PPP..", "..PPP..PPP..", "..ooo..ooo.."],
  };
  const SHIRTS = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#6366f1", "#84cc16", "#e2e8f0", "#0ea5e9"];
  const TROUSERS = ["#1e293b", "#334155", "#3f3f46", "#1f2937", "#4b5563", "#2b2d42", "#5c4033"];
  const SKINS = ["#f8d9b4", "#efc29c", "#d9a06b", "#b4753f", "#8d5524", "#ffe0c2"];
  const HAIRS = ["#1f1a17", "#4a2e1a", "#8b5a2b", "#d4a04a", "#b7b7b7", "#2b1d0e", "#c0392b", "#e0e0e0"];
  const STYLES = Object.keys(HAIR);

  function hash(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }

  function shade(hex, amt) {
    const n = parseInt(hex.slice(1), 16);
    const c = (v) => Math.max(0, Math.min(255, v + amt));
    return "#" + [c(n >> 16), c((n >> 8) & 255), c(n & 255)].map((v) => v.toString(16).padStart(2, "0")).join("");
  }

  // kind: an optional hint from the role ("knight", "wizard"...): kept so old seeds keep their look
  function look(seed, kind) {
    const h = hash(String(seed) + (kind || ""));
    return {
      hair: STYLES[h % STYLES.length], hairColor: HAIRS[(h >>> 4) % HAIRS.length],
      shirt: SHIRTS[(h >>> 8) % SHIRTS.length], trousers: TROUSERS[(h >>> 13) % TROUSERS.length],
      skin: SKINS[(h >>> 17) % SKINS.length], glasses: ((h >>> 21) & 3) === 0, cap: HAIR_CAP[(h >>> 23) % HAIR_CAP.length],
    };
  }
  const HAIR_CAP = ["#1d4ed8", "#111827", "#b91c1c", "#065f46"];

  function colorsOf(l) {
    return {
      h: l.hairColor, c: l.cap, S: l.skin, E: "#1a1a2e", G: "#2a2a3a", T: l.shirt, K: l.skin, P: l.trousers,
      o: "#1a1a1a", m: "#f3f3f3",
    };
  }

  function rows(l, pose) {
    const hair = HAIR[l.hair].slice();
    const face = FACE.slice();
    if (l.glasses) face[1] = GLASSES;
    const body = (BODY[pose] || BODY.stand).slice();
    return hair.concat(face, body);
  }

  const cache = new Map();
  function canvas(seed, kind, pose, frame) {
    const key = seed + "|" + (kind || "") + "|" + pose + "|" + (frame || 0);
    if (cache.has(key)) return cache.get(key);
    const l = look(seed, kind);
    const key2 = pose === "walk" ? "walk" + (frame ? 1 : 0) : pose === "type" ? "type" + (frame ? 1 : 0) : pose;
    const art = rows(l, key2);
    const colors = colorsOf(l);
    const c = document.createElement("canvas");
    c.width = 12;
    c.height = art.length;
    const g = c.getContext("2d");
    art.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const col = colors[row[x]];
        if (col) { g.fillStyle = col; g.fillRect(x, y, 1, 1); }
      }
    });
    // a darker shirt edge and a collar
    const top = hair_rows(l) + 4;
    g.fillStyle = shade(l.shirt, -40);
    g.fillRect(2, top + 1, 1, 2); g.fillRect(9, top + 1, 1, 2);
    g.fillStyle = "#ffffff";
    g.fillRect(5, top, 2, 1);
    cache.set(key, c);
    return c;
  }
  function hair_rows(l) { return HAIR[l.hair].length; }

  // Inline SVG portrait for sheets and lists (same drawing, scaled, crisp).
  function svg(seed, kind, size) {
    const l = look(seed, kind);
    const art = rows(l, "stand");
    const colors = colorsOf(l);
    let out = "";
    art.forEach((row, y) => {
      let x = 0;
      while (x < row.length) {
        const ch = row[x];
        let w = 1;
        while (x + w < row.length && row[x + w] === ch) w++;
        if (colors[ch]) out += `<rect x="${x}" y="${y}" width="${w}" height="1" fill="${colors[ch]}"/>`;
        x += w;
      }
    });
    const px = size || 48;
    return `<svg class="sprite" viewBox="0 0 12 ${art.length}" width="${px}" height="${Math.round(px * art.length / 12)}" ` +
      `shape-rendering="crispEdges" aria-hidden="true">${out}</svg>`;
  }

  return { canvas, svg, look, hash, shade, KINDS: ["wizard", "knight", "ranger", "bard", "cleric"] };
})();

// The rest of the page still asks Sprites for portraits; office people answer.
const Sprites = People;
