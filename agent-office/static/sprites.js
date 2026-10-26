// Pixel-art adventurers drawn as inline SVG. Same seed -> same character.
"use strict";

const Sprites = (() => {
  const HATS = {
    wizard: ["......H.....", ".....HH.....", "....HHHH....", "...HHHHHH...", "..BBBBBBBB.."],
    knight: ["............", "....MMMM....", "...MMMMMM...", "..MMMMMMMM..", "..MVVVVVVM.."],
    ranger: ["............", "....HHHH....", "...HHHHHH...", "..HHHHHHHH..", "..HHHHHHHH.."],
    bard:   ["........F...", ".......FF...", "...CCCCCC...", "..CCCCCCCC..", "...hhhhhh..."],
    cleric: ["............", "....WWWW....", "...WWGGWW...", "..WWWGGWWW..", "..WWWWWWWW.."],
  };
  const HEAD = ["...SSSSSS...", "...SESSES...", "...SSSSSS...", "....SSSS...."];
  const BODY = ["..RRRRRRRR..", ".RRRRRRRRRR.", ".RRRRRRRRRR.", "..RRRRRRRR..", "..RRLLLLRR..", "..RRRRRRRR..", "..DD....DD.."];
  const HANDS = [[1, 11], [10, 11]];

  const ROBES = ["#6b3fa0", "#2f6f9f", "#2e7d4f", "#a33b3b", "#b7791f", "#4a5568", "#9c4221", "#2c7a7b", "#805ad5", "#c05621"];
  const SKINS = ["#f1c27d", "#e0ac69", "#c68642", "#8d5524", "#ffdbac"];
  const HAIR = ["#2d1b0e", "#6b4226", "#d69e2e", "#a0aec0", "#1a202c", "#9b2c2c"];
  const KINDS = Object.keys(HATS);

  function hash(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
  }

  function look(seed, kind) {
    const h = hash(String(seed));
    return {
      kind: kind && HATS[kind] ? kind : KINDS[h % KINDS.length],
      robe: ROBES[(h >>> 4) % ROBES.length],
      hat: ROBES[(h >>> 9) % ROBES.length],
      skin: SKINS[(h >>> 14) % SKINS.length],
      hair: HAIR[(h >>> 18) % HAIR.length],
      beard: ((h >>> 22) & 3) === 0,
    };
  }

  function rects(rows, y0, colors) {
    let out = "";
    rows.forEach((row, dy) => {
      let x = 0;
      while (x < row.length) {
        const c = row[x];
        let w = 1;
        while (x + w < row.length && row[x + w] === c) w++;
        if (colors[c]) out += `<rect x="${x}" y="${y0 + dy}" width="${w}" height="1" fill="${colors[c]}"/>`;
        x += w;
      }
    });
    return out;
  }

  // kind: optional fixed class (staff pick one from their role)
  function svg(seed, kind, size) {
    const l = look(seed, kind);
    const colors = {
      H: l.hat, B: l.hat, M: "#a0aec0", V: "#2d3748", C: l.hat, F: "#e53e3e", W: "#edf2f7", G: "#d69e2e",
      h: l.hair, S: l.skin, E: "#1a1a2e", R: l.robe, L: "#5c3d1e", D: "#3b2a1a",
    };
    let body = rects(HATS[l.kind], 0, colors) + rects(HEAD, 5, colors) + rects(BODY, 9, colors);
    if (l.kind === "ranger") body += rects(["..H......H..", "..H......H.."], 5, colors);
    if (l.beard && l.kind === "wizard") body += rects(["...WWWWWW...", "....WWWW...."], 8, { W: "#e2e8f0" });
    const [left, right] = HANDS.map(([x, y]) => `<rect x="${x}" y="${y}" width="1" height="1" fill="${l.skin}"/>`);
    const px = size || 48;
    return `<svg class="sprite" viewBox="0 0 12 16" width="${px}" height="${Math.round(px * 16 / 12)}" ` +
      `shape-rendering="crispEdges" aria-hidden="true">${body}` +
      `<g class="hand hl">${left}</g><g class="hand hr">${right}</g></svg>`;
  }

  // Canvas versions for the town, cached per character + pose + frame.
  // pose: stand | walk | write | read | sit | sleep (head on a pillow)
  const cache = new Map();

  function colorsOf(l) {
    return {
      H: l.hat, B: l.hat, M: "#a0aec0", V: "#2d3748", C: l.hat, F: "#e53e3e", W: "#edf2f7", G: "#d69e2e",
      h: l.hair, S: l.skin, E: "#1a1a2e", e: shade(l.skin, -40), R: l.robe, L: "#5c3d1e", D: "#3b2a1a",
      K: l.skin, P: "#f3e4c3", O: "#8b5a2b",
    };
  }

  function shade(hex, amt) {
    const n = parseInt(hex.slice(1), 16);
    const c = (v) => Math.max(0, Math.min(255, v + amt));
    return "#" + [c(n >> 16), c((n >> 8) & 255), c(n & 255)].map((v) => v.toString(16).padStart(2, "0")).join("");
  }

  function rows(l, pose, frame) {
    const hat = HATS[l.kind].slice();
    let head = HEAD.slice();
    let body = BODY.slice();
    if (pose === "sleep") {
      head = ["...SSSSSS...", "...SeSSeS...", "...SSSSSS...", "....SSSS...."];
      return hat.concat(head);
    }
    if (pose === "walk") {
      body[6] = frame ? "...DD..DD..." : "..DD....DD..";
      body[2] = frame ? "KRRRRRRRRRR." : ".RRRRRRRRRRK";
    } else if (pose === "write") {
      body[2] = frame ? ".RRRKRRRRRR." : ".RRRRRRKRRR.";
    } else if (pose === "read") {
      body[1] = ".RRROOOORRR.";
      body[2] = ".RKOPPPPOKR.";
      if (frame) body[2] = ".RKOPPOPOKR.";
    } else {
      body[2] = ".KRRRRRRRRK.";
    }
    if (pose === "sit" || pose === "read" || pose === "write") body = body.slice(0, 5);
    if (l.beard && l.kind === "wizard") head = [head[0], head[1], "...WWWWWW...", "....WWWW...."];
    return hat.concat(head, body);
  }

  function canvas(seed, kind, pose, frame) {
    const key = seed + "|" + (kind || "") + "|" + pose + "|" + (frame || 0);
    if (cache.has(key)) return cache.get(key);
    const l = look(seed, kind);
    const colors = colorsOf(l);
    const art = rows(l, pose, frame || 0);
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
    if (l.kind === "ranger" && pose !== "sleep") {
      g.fillStyle = l.hat;
      g.fillRect(2, 5, 1, 2);
      g.fillRect(9, 5, 1, 2);
    }
    cache.set(key, c);
    return c;
  }

  return { svg, canvas, hash, look, shade, KINDS };
})();
