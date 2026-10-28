// The guild town: an HD-2D style pixel diorama. Each division is a building, the Guild Hall
// sits on the plaza, and agents live inside: writing at desks, reading by the fire, asleep in
// bed, or walking the road with a scroll when they carry an errand. Everything is drawn here.
"use strict";

const Town = (() => {
  const ROOM_SCALE = 3;
  const TOWN_SCALE = 2;
  const STYLES = {
    "Guild Hall": { roof: "#7a2230", wall: "#e6d5b0", sign: "crest" },
    "The Forge": { roof: "#8e3b2a", wall: "#cdb48c", sign: "anvil", sparks: true },
    "The Counting House": { roof: "#2f5d8e", wall: "#e3d2b0", sign: "coin" },
    "The Watchtower": { roof: "#4a4f6b", wall: "#b9b2a6", sign: "eye", tower: true },
    "The Scriptorium": { roof: "#5b3a8e", wall: "#e8dcc0", sign: "book" },
    "The Observatory": { roof: "#2c6b6b", wall: "#d5cdbd", sign: "star", dome: true },
    "The Atelier": { roof: "#a0523d", wall: "#efe0c8", sign: "brush" },
    "The War Room": { roof: "#6d1f2c", wall: "#d8c7a8", sign: "sword" },
    "The Common Room": { roof: "#7a5a2a", wall: "#e0cfaa", sign: "mug" },
    "The Tavern": { roof: "#8b5a2b", wall: "#ead7ad", sign: "mug", tavern: true },
  };
  const ROOFS = ["#7a5a2a", "#2f5d8e", "#5b3a8e", "#2c6b6b", "#8e3b2a", "#4a4f6b"];
  const ICONS = {
    anvil: [".....", "XXXXX", ".XXX.", "..X..", ".XXX."],
    coin: [".XXX.", "XX.XX", "X.X.X", "XX.XX", ".XXX."],
    eye: [".....", ".XXX.", "XX.XX", ".XXX.", "....."],
    book: ["XXXX.", "X..XX", "X..XX", "X..XX", "XXXX."],
    star: ["..X..", ".XXX.", "XXXXX", ".XXX.", "X...X"],
    brush: ["....X", "...XX", "..XX.", ".XX..", "X...."],
    sword: ["....X", "...X.", "X.X..", ".X...", "X.X.."],
    mug: ["XXXX.", "X..XX", "X..X.", "XXXX.", "....."],
    crest: ["XXXXX", "X.X.X", "XXXXX", ".XXX.", "..X.."],
    plus: ["..X..", "..X..", "XXXXX", "..X..", "..X.."],
  };
  const NIGHT_WINDOW = "#2c3552";
  const LIT_WINDOW = "#ffd27a";

  const el = {};
  const T = {
    view: "town", room: null, W: 0, H: 0, S: TOWN_SCALE, dpr: 1,
    buf: null, g: null, bg: null, layout: null, roomLayout: null,
    walkers: [], wanderers: [], villagers: [], particles: [], flies: [],
    prev: new Map(), state: null, time: 0, last: 0, list: false, built: false,
  };
  const reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- tiny pixel helpers ------------------------------------------------------

  function rng(seed) {
    let a = Sprites.hash(String(seed)) || 1;
    return () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const rect = (g, x, y, w, h, c) => { g.fillStyle = c; g.fillRect(Math.round(x), Math.round(y), w, h); };
  const px = (g, x, y, c) => rect(g, x, y, 1, 1, c);
  function disc(g, cx, cy, r, c) {
    g.fillStyle = c;
    for (let dy = -r; dy <= r; dy++) {
      const w = Math.round(Math.sqrt(r * r - dy * dy));
      g.fillRect(Math.round(cx - w), Math.round(cy + dy), w * 2 + 1, 1);
    }
  }
  function oval(g, cx, cy, rx, ry, c) {
    g.fillStyle = c;
    for (let dy = -ry; dy <= ry; dy++) {
      const w = Math.round(rx * Math.sqrt(1 - (dy * dy) / (ry * ry)));
      g.fillRect(Math.round(cx - w), Math.round(cy + dy), w * 2 + 1, 1);
    }
  }
  function icon(g, name, x, y, c) {
    (ICONS[name] || ICONS.plus).forEach((row, dy) => {
      for (let dx = 0; dx < row.length; dx++) if (row[dx] === "X") px(g, x + dx, y + dy, c);
    });
  }
  function offscreen(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.ceil(w));
    c.height = Math.max(1, Math.ceil(h));
    return c;
  }
  function sprite(g, seed, kind, pose, frame, x, y, flip) {
    const img = Sprites.canvas(seed, kind, pose, frame);
    const dx = Math.round(x - img.width / 2);
    const dy = Math.round(y - img.height);
    if (flip) {
      g.save();
      g.translate(dx + img.width, dy);
      g.scale(-1, 1);
      g.drawImage(img, 0, 0);
      g.restore();
    } else {
      g.drawImage(img, dx, dy);
    }
  }
  function styleOf(name) {
    if (STYLES[name]) return STYLES[name];
    const h = Sprites.hash(name);
    return { roof: ROOFS[h % ROOFS.length], wall: "#e0cfaa", sign: ["book", "star", "coin", "eye"][h % 4] };
  }

  // ---- time of day ----------------------------------------------------------------

  function jakartaHour() {
    try {
      const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      }).formatToParts(new Date());
      const hour = Number(parts.find((p) => p.type === "hour").value);
      const minute = Number(parts.find((p) => p.type === "minute").value);
      return hour + minute / 60;
    } catch (e) {
      return ((Date.now() / 3600000) + 7) % 24;
    }
  }

  function daylight() {
    // The owner's clock is Asia/Jakarta, even when the phone is set somewhere else.
    const h = jakartaHour();
    if (h >= 20 || h < 5) return { name: "night", tint: "#3b4a8c", k: 0.62, glow: 1, night: true };
    if (h >= 18.5) return { name: "dusk", tint: "#8a6ab0", k: 0.42, glow: 0.85, night: true };
    if (h >= 16.5) return { name: "golden", tint: "#ffb070", k: 0.22, glow: 0.55, night: false };
    if (h < 7) return { name: "dawn", tint: "#ffc8a0", k: 0.25, glow: 0.6, night: false };
    return { name: "day", tint: "#fff4e0", k: 0.05, glow: 0.35, night: false };
  }

  function skyName() {
    return (T.state && T.state.weather && T.state.weather.sky) || "sunny";
  }

  // ---- who lives where ----------------------------------------------------------

  function activity(o, now, night) {
    if (o.status === "needs") return "needs";
    if (o.status === "working") return "work";
    const quiet = now - (o.active || 0);
    return night || quiet > 45 * 60 ? "sleep" : "read";
  }

  function residents(state) {
    const now = Date.now() / 1000;
    const night = daylight().night;
    const leads = state.sessions.filter((s) => !s.staff && !s.helper && ["working", "needs", "idle"].includes(s.status))
      .map((s) => ({ id: s.id, sid: s.id, name: s.name, label: s.persona ? s.persona.name.split(" ")[0] : s.name,
        title: s.persona && s.persona.title, seed: s.id, kind: null, status: s.status, note: s.note,
        act: activity(s, now, night), home: s.division ? "d:" + s.division : "hall" }));
    const team = state.team || { staff: [], divisions: [] };
    const staff = team.staff.map((m) => ({
      id: m.id, staff: m.id, sid: m.sid, name: m.name, label: m.name, title: m.title, seed: m.seed, kind: m.kind,
      status: m.status, note: m.note, act: activity(m, now, night), home: "d:" + m.division, specialist: true,
    }));
    const everyone = leads.concat(staff);
    const divisions = [];
    everyone.forEach((p) => { if (p.home !== "hall" && !divisions.includes(p.home)) divisions.push(p.home); });
    // awake and idle: everyone takes their break at the tavern
    const at = (id) => everyone.filter((p) => p.home === id && p.act !== "read");
    const homes = [{ id: "hall", name: "Guild Hall", hall: true, people: at("hall") },
      { id: "tavern", name: "The Tavern", tavern: true, people: everyone.filter((p) => p.act === "read") }];
    divisions.forEach((id) => homes.push({ id, name: id.slice(2), people: at(id) }));
    return homes;
  }

  // ---- the town layout -------------------------------------------------------------

  function townLayout(homes, W) {
    const extra = homes.slice(1);
    const rows = Math.max(1, Math.ceil(Math.max(0, extra.length - 1) / 2));
    const H = 150 + rows * 78 + 40;
    const hall = { ...homes[0], x: Math.round(W / 2), y: 132, w: 66, hw: 26, hr: 24 };
    const roadY = hall.y + 12;
    const slots = [];
    slots.push({ x: Math.round(W * 0.22), y: 88 });
    slots.push({ x: Math.round(W * 0.78), y: 88 });
    for (let r = 0; r < rows + 1; r++) {
      slots.push({ x: Math.round(W * 0.22), y: roadY + 62 + r * 78 });
      slots.push({ x: Math.round(W * 0.78), y: roadY + 62 + r * 78 });
    }
    const buildings = [hall];
    extra.forEach((home, i) => {
      const s = slots[i];
      const st = styleOf(home.name);
      buildings.push({ ...home, x: s.x, y: s.y, w: st.tower ? 26 : 46, hw: st.tower ? 34 : 22, hr: st.tower ? 18 : 20, below: s.y > roadY });
    });
    const free = slots.slice(extra.length);
    const plot = free.shift();
    if (plot) buildings.push({ id: "plot", name: "Empty plot", plot: true, people: [], x: plot.x, y: plot.y, w: 40, hw: 0, hr: 0, below: plot.y > roadY });
    const lowest = Math.max(...buildings.map((b) => b.y));
    const gardens = free.slice(0, 2).filter((s) => s.y <= lowest);
    return { W, H: Math.min(H, lowest + 36), hall, roadY, buildings, gardens };
  }

  function routeOf(b, L) {
    if (b.hall) return [{ x: b.x, y: b.y }, { x: b.x, y: L.roadY }];
    if (!b.below) return [{ x: b.x, y: b.y }, { x: b.x, y: L.roadY }];
    const side = b.x < L.W / 2 ? b.x + b.w / 2 + 6 : b.x - b.w / 2 - 6;
    return [{ x: b.x, y: b.y }, { x: b.x, y: b.y + 5 }, { x: side, y: b.y + 5 }, { x: side, y: L.roadY }];
  }

  // ---- static background: grass, roads, plaza ----------------------------------

  function cobble(g, x, y, w, h, r) {
    for (let yy = 0; yy < h; yy++) {
      for (let xx = 0; xx < w; xx++) {
        const X = x + xx, Y = y + yy;
        const k = (X * 7 + Y * 13 + ((X >> 2) * 3)) % 11;
        g.fillStyle = k === 0 ? "#8c7552" : k < 3 ? "#c9b48c" : r() < 0.06 ? "#a58c66" : "#b8a27a";
        g.fillRect(X, Y, 1, 1);
      }
    }
  }

  function drawPath(g, a, b, r, width) {
    const w = width || 5;
    if (a.x === b.x) cobble(g, Math.round(a.x - w / 2), Math.min(a.y, b.y), w, Math.abs(b.y - a.y) + 1, r);
    else cobble(g, Math.min(a.x, b.x) - Math.floor(w / 2), Math.round(a.y - w / 2), Math.abs(b.x - a.x) + w, w, r);
  }

  function buildBackground(L) {
    const c = offscreen(L.W, L.H);
    const g = c.getContext("2d");
    const r = rng("town-ground");
    for (let y = 0; y < L.H; y++) {
      for (let x = 0; x < L.W; x++) {
        const n = r();
        g.fillStyle = n < 0.05 ? "#4f7d33" : n < 0.1 ? "#77a64b" : n < 0.11 ? "#8fbf5a" : "#62913c";
        g.fillRect(x, y, 1, 1);
      }
    }
    for (let i = 0; i < 26; i++) {
      const fx = Math.floor(r() * L.W), fy = Math.floor(r() * L.H);
      const col = ["#f6e27a", "#f08aa0", "#ffffff", "#b99cf0"][i % 4];
      for (let k = 0; k < 6; k++) px(g, fx + Math.floor(r() * 7), fy + Math.floor(r() * 5), col);
    }
    drawPath(g, { x: 4, y: L.roadY }, { x: L.W - 4, y: L.roadY }, r, 7);
    L.buildings.forEach((b) => {
      const route = routeOf(b, L);
      for (let i = 0; i < route.length - 1; i++) drawPath(g, route[i], route[i + 1], r, 5);
    });
    oval(g, L.hall.x, L.roadY + 16, 36, 13, "#8c7552");
    for (let y = -12; y <= 12; y++) {
      for (let x = -35; x <= 35; x++) {
        if ((x * x) / (35 * 35) + (y * y) / (12 * 12) <= 1) {
          const X = L.hall.x + x, Y = L.roadY + 16 + y;
          const ring = Math.round(Math.sqrt(x * x / 9 + y * y));
          g.fillStyle = (ring + (X >> 1)) % 4 === 0 ? "#a8936c" : "#c4ae86";
          g.fillRect(X, Y, 1, 1);
        }
      }
    }
    L.buildings.forEach((b) => oval(g, b.x, b.y + 1, Math.round(b.w / 2) + 3, 3, "rgba(20,30,10,.35)"));
    L.gardens.forEach((s) => {
      for (let y = -6; y < 6; y += 3) for (let x = -14; x < 14; x++) px(g, s.x + x, s.y - 4 + y, x % 2 ? "#6b4a2e" : "#5a3c24");
      for (let i = 0; i < 18; i++) px(g, s.x - 13 + Math.floor(r() * 26), s.y - 10 + Math.floor(r() * 12), ["#e86a5a", "#f6e27a", "#7fd06b"][i % 3]);
    });
    return c;
  }

  // ---- buildings ---------------------------------------------------------------------

  function renderBuilding(b, lit) {
    const st = b.hall ? STYLES["Guild Hall"] : styleOf(b.name);
    const cw = b.w + 10, ch = b.hw + b.hr + 16;
    const c = offscreen(cw, ch);
    const g = c.getContext("2d");
    const cx = Math.floor(cw / 2), left = cx - Math.floor(b.w / 2), bottom = ch - 2, top = bottom - b.hw;
    const lights = [];
    if (b.plot) {
      for (let x = left; x < left + b.w; x++) for (let y = bottom - 14; y < bottom; y++) px(g, x, y, (x + y) % 3 ? "#7a5a3a" : "#6b4a2e");
      for (let x = left; x < left + b.w; x += 5) { rect(g, x, bottom - 18, 1, 6, "#5a3c24"); }
      rect(g, left, bottom - 16, b.w, 1, "#6b4423");
      rect(g, cx - 1, bottom - 22, 2, 10, "#5a3c24");
      rect(g, cx - 6, bottom - 28, 12, 8, "#c9a46a");
      icon(g, "plus", cx - 2, bottom - 27, "#5a3c24");
      return { canvas: c, ox: cx, oy: bottom, lights, chimney: null };
    }
    // walls with a stone footing and timber frame
    rect(g, left, top, b.w, b.hw, st.wall);
    rect(g, left, bottom - 3, b.w, 3, "#7d7466");
    for (let x = left; x < left + b.w; x += 3) px(g, x, bottom - 2, "#6a6256");
    const beam = "#4a2e1a";
    rect(g, left, top, 1, b.hw, beam);
    rect(g, left + b.w - 1, top, 1, b.hw, beam);
    if (b.w > 30) rect(g, cx, top, 1, b.hw - 3, beam);
    if (b.hw > 20) rect(g, left, top + Math.floor(b.hw / 2) - 1, b.w, 1, beam);
    rect(g, left, top, b.w, 1, beam);
    // roof: gable, cone or dome
    const rw = Math.floor(b.w / 2) + 4;
    if (st.dome) {
      for (let i = 0; i < b.hr; i++) {
        const half = Math.round(rw * Math.sqrt(1 - (i * i) / (b.hr * b.hr)));
        rect(g, cx - half, top - 1 - i, half * 2 + 1, 1, i % 4 === 0 ? Sprites.shade(st.roof, -25) : st.roof);
      }
      rect(g, cx - 1, top - b.hr - 4, 2, 4, "#c8c0b0");
    } else {
      for (let i = 0; i < b.hr; i++) {
        const half = Math.round(rw * (1 - i / b.hr));
        const shade = i % 3 === 0 ? Sprites.shade(st.roof, -28) : st.roof;
        rect(g, cx - half, top - 1 - i, half * 2 + 1, 1, shade);
        px(g, cx - half, top - 1 - i, Sprites.shade(st.roof, -50));
        px(g, cx + half, top - 1 - i, Sprites.shade(st.roof, -50));
        if (i < b.hr - 3) px(g, cx - half + 2, top - 1 - i, Sprites.shade(st.roof, 25));
      }
      rect(g, cx - rw, top - 1, rw * 2 + 1, 1, Sprites.shade(st.roof, -45));
    }
    // chimney
    let chimney = null;
    if (!st.tower && !st.dome) {
      const chx = cx + Math.floor(rw / 2), chy = top - Math.floor(b.hr * 0.55) - 6;
      rect(g, chx, chy, 4, 8, "#7d7466");
      rect(g, chx - 1, chy, 6, 1, "#5f584e");
      chimney = { x: chx + 2, y: chy };
    }
    // door
    const dw = b.hall ? 10 : 6, dh = b.hall ? 13 : 9;
    rect(g, cx - Math.floor(dw / 2) - 1, bottom - dh - 1, dw + 2, dh + 1, "#2a1a0e");
    rect(g, cx - Math.floor(dw / 2), bottom - dh, dw, dh, "#5a3820");
    for (let y = bottom - dh + 2; y < bottom; y += 3) rect(g, cx - Math.floor(dw / 2), y, dw, 1, "#4a2e1a");
    px(g, cx + Math.floor(dw / 2) - 2, bottom - Math.floor(dh / 2), "#e0b04c");
    // windows
    const wins = [];
    const wy = top + (b.hw > 20 ? 4 : 5);
    if (st.tower) wins.push([cx - 2, top + 6], [cx - 2, top + 18]);
    else if (b.w >= 60) wins.push([left + 5, wy], [left + 15, wy], [left + b.w - 20, wy], [left + b.w - 10, wy]);
    else wins.push([left + 5, wy], [left + b.w - 10, wy]);
    wins.forEach(([x, y]) => {
      rect(g, x - 1, y - 1, 7, 7, "#3b2414");
      rect(g, x, y, 5, 5, lit ? LIT_WINDOW : NIGHT_WINDOW);
      rect(g, x + 2, y, 1, 5, lit ? "#b07a2a" : "#1c2238");
      rect(g, x, y + 2, 5, 1, lit ? "#b07a2a" : "#1c2238");
      if (lit) lights.push({ x: x + 2.5 - cx, y: y + 2.5 - bottom, r: 9, c: "255,200,110", rect: [x - cx, y - bottom, 5, 5] });
      rect(g, x - 1, y + 5, 7, 1, "#6b4423");
    });
    // hanging sign with the division's emblem
    const sx = cx + Math.floor(dw / 2) + 3, sy = bottom - dh - 1;
    rect(g, sx, sy - 1, 7, 1, "#3b2414");
    rect(g, sx + 1, sy, 1, 1, "#3b2414");
    rect(g, sx, sy + 1, 7, 7, "#6b4423");
    icon(g, st.sign, sx + 1, sy + 2, "#f0c860");
    if (b.hall) {
      [left + 3, left + b.w - 7].forEach((x) => {
        rect(g, x, top + 2, 4, 11, "#8e2a3a");
        px(g, x + 1, top + 13, "#8e2a3a"); px(g, x + 2, top + 13, "#8e2a3a");
        px(g, x + 1, top + 6, "#e0b04c"); px(g, x + 2, top + 6, "#e0b04c");
      });
      rect(g, cx - 3, top - 10, 7, 7, "#e0b04c");
      icon(g, "crest", cx - 2, top - 9, "#7a2230");
    }
    return { canvas: c, ox: cx, oy: bottom, lights, chimney };
  }

  // ---- scenery sprites ----------------------------------------------------------------

  function tree(seed) {
    const r = rng(seed);
    const c = offscreen(22, 28);
    const g = c.getContext("2d");
    rect(g, 10, 18, 3, 9, "#5a3c24");
    rect(g, 10, 18, 1, 9, "#7a5232");
    const R = 7 + Math.floor(r() * 3);
    disc(g, 11, 12, R, "#2f5e2a");
    disc(g, 9, 10, R - 2, "#3f7a34");
    disc(g, 8, 8, R - 4, "#58993f");
    for (let i = 0; i < 10; i++) px(g, 4 + Math.floor(r() * 12), 4 + Math.floor(r() * 10), "#7fbf55");
    return c;
  }

  function windmillBody() {
    const c = offscreen(16, 34);
    const g = c.getContext("2d");
    for (let y = 0; y < 26; y++) {
      const half = 4 + Math.floor(y / 6);
      rect(g, 8 - half, 8 + y, half * 2, 1, y % 5 === 0 ? "#b9a27a" : "#d6c39a");
    }
    for (let i = 0; i < 7; i++) rect(g, 8 - (6 - i), 8 - i, (6 - i) * 2, 1, "#7a3a2a");
    rect(g, 6, 27, 4, 7, "#5a3820");
    rect(g, 7, 15, 2, 3, "#2c3552");
    return c;
  }

  // ---- layout + caches when the residents change ------------------------------

  function buildTown(homes) {
    const L = townLayout(homes, T.W);
    T.H = L.H;
    const night = daylight().night;
    L.buildings.forEach((b) => {
      const busy = b.people.some((p) => p.act === "work" || p.act === "needs");
      const awake = b.people.some((p) => p.act !== "sleep");
      b.lit = busy;
      b.dim = !b.plot && b.people.length === 0;
      b.art = renderBuilding(b, b.lit || (night && awake));
      b.busy = busy;
      b.sleepy = b.people.length > 0 && !awake;
    });
    const r = rng("town-decor" + T.W);
    L.trees = [];
    const tryTree = (x, y) => {
      const clash = L.buildings.some((b) => Math.abs(b.x - x) < b.w / 2 + 10 && y > b.y - b.hw - b.hr - 6 && y < b.y + 12) ||
        Math.abs(y - L.roadY) < 10 || (Math.abs(x - L.hall.x) < 44 && Math.abs(y - (L.roadY + 16)) < 22) ||
        L.buildings.some((b) => routeOf(b, L).some((p, i, arr) => i && Math.abs(p.x - x) < 8 && y > Math.min(p.y, arr[i - 1].y) - 4 && y < Math.max(p.y, arr[i - 1].y) + 10));
      if (!clash) L.trees.push({ x, y, art: tree("t" + x + "," + y) });
    };
    for (let i = 0; i < 40; i++) tryTree(Math.floor(r() * T.W), 18 + Math.floor(r() * (L.H - 18)));
    for (let x = 6; x < T.W; x += 16) tryTree(x + Math.floor(r() * 6), 22 + Math.floor(r() * 6));
    L.mill = { x: Math.max(14, Math.round(T.W * 0.08)), y: L.roadY - 8, body: windmillBody() };
    L.trees = L.trees.filter((t) => Math.abs(t.x - L.mill.x) > 16 || Math.abs(t.y - L.mill.y) > 26);
    L.lamps = [];
    for (let x = 16; x < T.W - 8; x += 34) if (Math.abs(x - L.hall.x) > 20) L.lamps.push({ x, y: L.roadY - 5 });
    L.fountain = { x: L.hall.x, y: L.roadY + 18 };
    T.layout = L;
    T.bg = buildBackground(L);
    if (!T.villagers.length) {
      T.villagers = ["villager-baker", "villager-bard", "villager-smith"].map((seed, i) => ({
        seed, kind: null, x: 20 + i * 50, tx: 60, wait: i * 2, y: L.roadY + 1,
      }));
    }
  }

  // ---- the room inside a building ----------------------------------------------------

  function tavernLayout(home, W) {
    const wallH = 46;
    const cols = Math.max(1, Math.floor((W - 8) / 46));
    const tables = [];
    home.people.forEach((p, i) => {
      const t = Math.floor(i / 2), c = t % cols, r = Math.floor(t / cols);
      const span = (W - 8) / cols;
      tables.push({ p, side: i % 2, x: Math.round(4 + span * c + span / 2), y: wallH + 40 + r * 30 });
    });
    const rows = Math.max(1, Math.ceil(Math.ceil(home.people.length / 2) / cols));
    return { W, H: Math.max(wallH + 40 + rows * 30 + 4, 116), wallH, home, chairs: [], desks: [], beds: [], tables };
  }

  function roomLayout(home, W) {
    if (home.tavern) return tavernLayout(home, W);
    const wallH = 46;
    const people = home.people.slice();
    const readers = people.filter((p) => p.act === "read");
    const sleepers = people.filter((p) => p.act === "sleep");
    const chairs = readers.slice(0, 2);
    const beds = sleepers.slice(0, Math.max(1, Math.floor((W - 20) / 18)));
    const deskPeople = people.filter((p) => !chairs.includes(p) && !beds.includes(p));
    const cols = Math.max(1, Math.floor((W - 8) / 42));
    const rows = Math.max(1, Math.ceil(deskPeople.length / cols));
    const deskTop = wallH + 40;
    const desks = deskPeople.map((p, i) => {
      const c = i % cols, r = Math.floor(i / cols);
      const span = (W - 8) / cols;
      return { p, x: Math.round(4 + span * c + span / 2 + (p.act === "needs" ? 6 : 0)), y: deskTop + r * 32 };
    });
    const bedY = deskTop + rows * 32 - 6;
    const H = beds.length ? bedY + 30 : deskTop + rows * 32;
    return {
      W, H: Math.max(H, 116), wallH, home,
      chairs: chairs.map((p, i) => ({ p, x: 14 + i * 20, y: wallH + 24 })),
      desks,
      beds: beds.map((p, i) => ({ p, x: W - 12 - i * 18, y: bedY })),
    };
  }

  function buildRoom(home) {
    const R = roomLayout(home, T.W);
    T.H = R.H;
    const st = home.hall ? STYLES["Guild Hall"] : styleOf(home.name);
    const c = offscreen(R.W, R.H);
    const g = c.getContext("2d");
    const r = rng("room" + home.id);
    // floor
    for (let y = R.wallH; y < R.H; y++) {
      for (let x = 0; x < R.W; x++) {
        if (home.hall) {
          const dark = ((x >> 3) + (y - R.wallH >> 3)) % 2;
          g.fillStyle = dark ? "#2f2a36" : "#8c8270";
          if (!dark && r() < 0.08) g.fillStyle = "#9a907c";
        } else {
          const plank = Math.floor((y - R.wallH) / 4);
          g.fillStyle = (y - R.wallH) % 4 === 0 ? "#4e3220" : ((x + plank * 11) % 23 === 0 ? "#5a3a24" : plank % 2 ? "#7a5232" : "#6f4a2c");
        }
        g.fillRect(x, y, 1, 1);
      }
    }
    if (home.hall) {
      rect(g, 10, R.wallH + 30, R.W - 20, R.H - R.wallH - 40, "#7a2230");
      rect(g, 12, R.wallH + 32, R.W - 24, R.H - R.wallH - 44, "#8e2a3a");
      g.strokeStyle = "#e0b04c";
      g.strokeRect(13.5, R.wallH + 33.5, R.W - 27, R.H - R.wallH - 47);
    }
    // back wall: panels, molding, window, shelves, prop
    for (let y = 0; y < R.wallH; y++) {
      for (let x = 0; x < R.W; x++) {
        g.fillStyle = x % 7 === 0 ? "#3e2716" : y < 3 ? "#2d1b10" : y > R.wallH - 4 ? "#2d1b10" : (x * 3 + y) % 17 === 0 ? "#553621" : "#4b301c";
        g.fillRect(x, y, 1, 1);
      }
    }
    const wx = Math.round(R.W * 0.56);
    rect(g, wx - 1, 7, 18, 20, "#2d1b10");
    R.window = { x: wx, y: 8, w: 16, h: 18 };
    const sx = Math.round(R.W * 0.3);
    if (sx + 24 < wx && !st.sparks && !st.tavern) {
      for (let s = 0; s < 3; s++) {
        const y = 8 + s * 11;
        rect(g, sx, y + 9, 22, 2, "#6b4423");
        for (let x = sx + 1; x < sx + 21;) {
          const bw = 1 + Math.floor(r() * 2), bh = 6 + Math.floor(r() * 3);
          rect(g, x, y + 9 - bh, bw, bh, ["#8e2a3a", "#2f5d8e", "#2e7d4f", "#b7791f", "#5b3a8e", "#c9b48c"][Math.floor(r() * 6)]);
          x += bw + (r() < 0.2 ? 1 : 0);
        }
      }
    }
    // fireplace
    rect(g, 3, R.wallH - 24, 24, 27, "#6a6256");
    for (let y = R.wallH - 24; y < R.wallH + 3; y += 4) for (let x = 3 + ((y >> 2) % 2) * 3; x < 27; x += 6) rect(g, x, y, 1, 4, "#57504a");
    rect(g, 1, R.wallH - 26, 28, 3, "#4a443e");
    rect(g, 8, R.wallH - 14, 14, 17, "#1a1210");
    R.fire = { x: 15, y: R.wallH + 2, forge: !!st.sparks };
    if (st.tavern) {
      const bx = Math.round(R.W * 0.3);
      for (let i = 0; i < 4; i++) { disc(g, bx + 4 + i * 9, R.wallH - 30, 4, "#6b4423"); rect(g, bx + 1 + i * 9, R.wallH - 31, 7, 1, "#4a2e1a"); }
      rect(g, bx - 2, R.wallH - 12, R.W - bx - 26, 12, "#6b4423");
      rect(g, bx - 2, R.wallH - 12, R.W - bx - 26, 2, "#a8743e");
      for (let x = bx + 2; x < R.W - 30; x += 8) { rect(g, x, R.wallH - 17, 3, 4, "#e8d8b4"); rect(g, x, R.wallH - 17, 3, 1, "#ffffff"); }
    }
    if (st.sparks) {
      // a weapon rack where the bookshelf would be
      for (let i = 0; i < 5; i++) {
        rect(g, sx + 2 + i * 4, 10, 1, 22, "#9aa4b0");
        rect(g, sx + 1 + i * 4, 30, 3, 2, "#5a3820");
      }
      rect(g, sx, 32, 22, 2, "#6b4423");
      rect(g, R.W - 30, R.wallH + 10, 20, 5, "#4a4f5a");
      rect(g, R.W - 27, R.wallH + 15, 14, 4, "#3a3f48");
      rect(g, R.W - 25, R.wallH + 19, 10, 6, "#5a3820");
    }
    // the division's signature prop on the right wall
    prop(g, st.sign, R.W - 22, R.wallH);
    if (home.hall) {
      [R.W * 0.12 + 30, R.W - 34].forEach((x) => { rect(g, Math.round(x), 6, 6, 16, "#8e2a3a"); rect(g, Math.round(x) + 1, 12, 4, 2, "#e0b04c"); });
    }
    R.lamps = [{ x: Math.round(R.W * 0.25), y: 9 }, { x: Math.round(R.W * 0.8), y: 9 }];
    R.lamps.forEach((l) => { rect(g, l.x, 0, 1, l.y - 2, "#2a1a0e"); });
    // furniture under the people: chair backs, beds, desks are drawn per frame (they overlap people)
    R.bg = c;
    T.roomLayout = R;
  }

  function prop(g, kind, x, wallH) {
    if (kind === "anvil") {
      rect(g, x + 2, wallH + 2, 12, 3, "#4a4f5a"); rect(g, x + 5, wallH + 5, 6, 3, "#3a3f48"); rect(g, x + 4, wallH + 8, 8, 4, "#5a3820");
    } else if (kind === "coin") {
      rect(g, x, wallH - 2, 16, 10, "#6b4423"); rect(g, x, wallH - 2, 16, 2, "#8b5a2b"); rect(g, x + 7, wallH + 2, 2, 2, "#e0b04c");
      for (let i = 0; i < 9; i++) px(g, x + 2 + i, wallH - 4 + (i % 3), "#f0c860");
    } else if (kind === "eye" || kind === "sword" || kind === "crest") {
      rect(g, x - 2, 8, 20, 16, "#c9b48c"); rect(g, x - 1, 9, 18, 14, "#e8d8b4");
      for (let i = 0; i < 5; i++) rect(g, x + 1 + i * 3, 12 + (i % 2) * 4, 2, 1, "#8e2a3a");
      rect(g, x + 2, 18, 12, 1, "#6b4423");
    } else if (kind === "star") {
      rect(g, x + 4, wallH - 8, 1, 14, "#4a2e1a"); rect(g, x + 9, wallH - 8, 1, 14, "#4a2e1a");
      for (let i = 0; i < 10; i++) rect(g, x + 2 + i, wallH - 14 + Math.floor(i / 2), 3, 2, "#b7791f");
    } else if (kind === "brush") {
      rect(g, x + 3, wallH - 18, 1, 22, "#5a3820"); rect(g, x + 12, wallH - 18, 1, 22, "#5a3820");
      rect(g, x + 2, wallH - 18, 12, 12, "#f3e4c3"); rect(g, x + 4, wallH - 15, 3, 3, "#2f5d8e"); rect(g, x + 8, wallH - 13, 4, 4, "#c05621");
    } else {
      rect(g, x, wallH - 4, 7, 10, "#6b4423"); rect(g, x + 8, wallH - 2, 7, 8, "#6b4423");
      rect(g, x, wallH - 1, 7, 1, "#3b2414"); rect(g, x + 8, wallH + 1, 7, 1, "#3b2414");
    }
  }

  // ---- particles, walkers, villagers ---------------------------------------------------

  function puff(x, y, kind) {
    if (T.lite && T.particles.length > 12) return;
    T.particles.push({ x, y, vx: (Math.random() - 0.5) * 3, vy: kind === "spark" ? -14 - Math.random() * 6 : -5 - Math.random() * 3,
      life: kind === "spark" ? 0.7 : 2.6, age: 0, kind });
  }

  function sendWalker(m, from, to, flags) {
    if (!from || !to || !from.length || !to.length) return;
    flags = flags || {};
    T.walkers.push({
      id: m.id, seed: m.seed, kind: m.kind, path: [from[0]].concat(from.slice(1), to.slice().reverse()),
      i: 0, x: from[0].x, y: from[0].y, scroll: !flags.loot, loot: !!flags.loot,
    });
  }

  function noticeChanges(homes) {
    const L = T.layout;
    if (!L) return;
    const routeTo = (id) => { const b = L.buildings.find((x) => x.id === id); return b ? routeOf(b, L) : null; };
    const hallRoute = routeOf(L.hall, L);
    homes.forEach((home) => home.people.forEach((m) => {
      const before = T.prev.get(m.id);
      T.prev.set(m.id, { status: m.status, act: m.act, home: m.home });
      if (!before) return;
      const homeRoute = routeTo(m.home), tavernRoute = routeTo("tavern");
      const fromRoute = routeTo(before.act === "read" ? "tavern" : (before.home || m.home)) || hallRoute;
      if (!homeRoute) return;
      if (m.status === "working" && before.status !== "working") {
        sendWalker(m, fromRoute, homeRoute);  // errand or quest: walk to their building
        say(m, "On my way!");
      } else if (before.status === "working" && m.status !== "working") {
        const back = m.act === "read" && tavernRoute ? tavernRoute : hallRoute;
        sendWalker(m, homeRoute, back, { loot: true });  // home, carrying the prize
        say(m, "Done! Here you go");
      } else if (m.act === "read" && before.act !== "read" && tavernRoute) {
        sendWalker(m, homeRoute, tavernRoute);
        if (before.act === "work") say(m, "Quick break, brb");
      } else if (before.act === "read" && m.act !== "read" && tavernRoute) {
        sendWalker(m, tavernRoute, homeRoute);
        if (m.act === "work") say(m, "Back to it!");
      }
    }));
    wander(homes);
  }

  function wander(homes) {
    if (reduced || !T.layout) { T.wanderers = []; return; }
    const resting = [];
    homes.forEach((h) => h.people.forEach((p) => { if (p.act === "read") resting.push(p); }));
    const cap = T.lite ? 2 : 3;
    const walking = new Set(T.walkers.map((w) => w.id));
    const live = new Set(resting.map((p) => p.id));
    T.wanderers = T.wanderers.filter((w) => live.has(w.id) && !walking.has(w.id));
    resting.forEach((p) => {
      if (T.wanderers.length >= cap || walking.has(p.id) || T.wanderers.some((w) => w.id === p.id)) return;
      const road = T.layout.roadY;
      T.wanderers.push({ id: p.id, seed: p.seed, kind: p.kind, x: 16 + Math.random() * 40, y: road + 1, tx: T.W - 24, wait: Math.random() * 2, flip: false });
    });
  }

  // ---- chatter: little speech bubbles, the way a busy hall sounds ---------------------

  const LINES = {
    work: ["Almost there…", "One more test", "Who broke main?", "Reading the logs", "Hmm, curious", "Sharpening the quill",
      "The RPC is grumpy today", "Double-checking the decimals", "Squeezing into a rack, very cosy", "This bug is sneaky"],
    read: ["Tokens are pricey", "Fossils can wait, tea cannot", "Quick hop, then back to decimals", "Sunbathing on warm pixels, brb",
      "Another round?", "Heard the Forge shipped", "Moon rocks can wait, probably", "All good?", "Who's paying this round?"],
    sleep: ["Zzz…", "Zzz… merge… zzz", "Five more minutes", "Zzz… green tests… zzz"],
    needs: ["Guildmaster? A word?", "Waiting on your call", "Need your seal on this!", "Psst, over here!"],
  };
  const REPLIES = { "Who broke main?": "Not me!", "Tokens are pricey": "Worth it!", "Another round?": "Just one!",
    "Heard the Forge shipped": "Nice deploy!", "Almost there…": "You got this", "All good?": "All good",
    "Who's paying this round?": "The treasury, sadly", "This bug is sneaky": "Squash it!" };
  const pickOf = (arr) => arr[Math.floor(Math.random() * arr.length)];

  function anchorOf(p) {
    if (T.view === "room") {
      const R = T.roomLayout;
      const spot = R && R.chairs.concat(R.desks, R.beds, R.tables || []).find((s) => s.p === p || (s.p && s.p.id === p.id));
      return spot && spot.hit ? { x: spot.hit[0] + spot.hit[2] / 2, y: spot.hit[1] } : null;
    }
    const L = T.layout;
    const id = p.act === "read" ? "tavern" : p.home;
    const b = L && L.buildings.find((x) => x.id === id);
    return b ? { x: b.x + (Math.random() - 0.5) * 16, y: b.y - b.hw - b.hr - 8 } : null;
  }

  function bubbleAt(at, text) {
    if (!at || !el.hits) return;
    const bubble = document.createElement("div");
    bubble.className = "chat";
    bubble.textContent = text;
    bubble.style.left = pct(at.x, T.W);
    bubble.style.top = pct(at.y, T.H);
    el.hits.appendChild(bubble);
    setTimeout(() => bubble.classList.add("gone"), 3400);
    setTimeout(() => bubble.remove(), 3900);
  }

  function say(p, text, delay) {
    setTimeout(() => {
      if (!el.hits) return;
      if (typeof ChatLog !== "undefined") ChatLog.add("say", p.label || p.name, text, p.sid, null, "", { local: true });
      bubbleAt(anchorOf(p), text);
    }, delay || 0);
  }

  function speak(line) {
    if (!line || !line.text || !T.layout || T.view !== "town") return;
    const who = String(line.who || "");
    const audit = line.ch === "external" && /^(GET|POST|PUT|DELETE)\b/.test(line.text);
    if (audit) return;
    const guildmaster = line.ch === "external" || /guild hall|guildmaster/i.test(who);
    if (guildmaster) {
      bubbleAt({ x: guildmasterAt(T.layout).x, y: guildmasterAt(T.layout).y - 28 }, line.text);
      return;
    }
    if (line.ch !== "say" && line.ch !== "guild") return;
    const person = (T.people || []).find((p) => who && ((p.label || p.name) === who || String(p.name || "").indexOf(who) === 0));
    if (person) bubbleAt(anchorOf(person), line.text);
  }

  function reply(p, first, answer) {
    const others = (T.people || []).filter((o) => o.id !== p.id && o.act !== "sleep");
    if (!others.length) return;
    const other = pickOf(others);
    say(other, first, 1500);
    if (answer) say(p, answer, 3000);
  }

  let nextChat = 4;
  function chatter() {
    if (T.time < nextChat) return;
    nextChat = T.time + 5 + Math.random() * 5;
    const people = T.view === "room" ? (T.roomHome ? T.roomHome.people : []) : (T.people || []);
    if (!people.length) return;
    const p = pickOf(people);
    let line = pickOf(LINES[p.act] || LINES.read);
    if (p.act === "read" && T.state && T.state.cost_today > 20 && Math.random() < 0.5) line = "Tokens are pricey";
    say(p, line);
    const answer = REPLIES[line];
    if (answer) {
      const others = people.filter((o) => o.id !== p.id && o.act !== "sleep");
      if (others.length) say(pickOf(others), answer, 1700);
    }
  }

  function step(dt) {
    const L = T.layout;
    chatter();
    T.walkers.forEach((w) => {
      let left = dt * 16;
      while (left > 0 && w.i < w.path.length - 1) {
        const a = w.path[w.i + 1];
        const dx = a.x - w.x, dy = a.y - w.y, d = Math.hypot(dx, dy);
        if (d <= left) { w.x = a.x; w.y = a.y; w.i++; left -= d; } else { w.x += (dx / d) * left; w.y += (dy / d) * left; w.flip = dx < -0.01 ? true : dx > 0.01 ? false : w.flip; left = 0; }
      }
      if (w.i >= w.path.length - 1) { w.done = true; puff(w.x, w.y - 6, "smoke"); }
    });
    T.walkers = T.walkers.filter((w) => !w.done);
    if (L && !reduced) {
      T.wanderers.forEach((v) => {
        if (v.wait > 0) { v.wait -= dt; return; }
        const d = v.tx - v.x;
        if (Math.abs(d) < 1) { v.wait = 1.5 + Math.random() * 3; v.tx = 12 + Math.random() * (T.W - 24); return; }
        v.flip = d < 0;
        v.x += Math.sign(d) * Math.min(Math.abs(d), dt * (T.lite ? 7 : 9));
      });
    }
    if (L) {
      T.villagers.forEach((v) => {
        if (v.wait > 0) { v.wait -= dt; return; }
        const d = v.tx - v.x;
        if (Math.abs(d) < 1) { v.wait = 2 + Math.random() * 5; v.tx = 10 + Math.random() * (T.W - 20); return; }
        v.flip = d < 0;
        v.x += Math.sign(d) * Math.min(Math.abs(d), dt * 9);
      });
    }
    T.particles.forEach((p) => { p.age += dt; p.x += p.vx * dt; p.y += p.vy * dt; p.vx += (Math.random() - 0.5) * dt * 4; });
    T.particles = T.particles.filter((p) => p.age < p.life);
  }

  // ---- drawing a frame ---------------------------------------------------------------

  function drawTown(g, t, day, lights) {
    const L = T.layout;
    g.drawImage(T.bg, 0, 0);
    const items = [];
    L.buildings.forEach((b) => items.push({ y: b.y, draw: () => {
      if (b.dim) g.globalAlpha = 0.55;
      g.drawImage(b.art.canvas, Math.round(b.x - b.art.ox), Math.round(b.y - b.art.oy));
      g.globalAlpha = 1;
      if (!b.dim) b.art.lights.forEach((l) => lights.push({ x: b.x + l.x, y: b.y + l.y, r: l.r, c: l.c, rect: [b.x + l.rect[0], b.y + l.rect[1], l.rect[2], l.rect[3]] }));
      if (b.art.chimney && b.busy) {
        const cx = b.x - b.art.ox + b.art.chimney.x, cy = b.y - b.art.oy + b.art.chimney.y;
        if (Math.random() < (T.lite ? 0.04 : 0.08)) puff(cx, cy, "smoke");
        if (b.busy && styleOf(b.name).sparks && Math.random() < (T.lite ? 0.06 : 0.15)) puff(cx, cy, "spark");
      }
    } }));
    L.trees.forEach((tr) => items.push({ y: tr.y, draw: () => g.drawImage(tr.art, tr.x - 11, tr.y - 27) }));
    items.push({ y: L.mill.y, draw: () => drawMill(g, L.mill, t) });
    L.lamps.forEach((l) => items.push({ y: l.y, draw: () => {
      rect(g, l.x, l.y - 11, 1, 11, "#2a2420");
      rect(g, l.x - 1, l.y - 14, 3, 3, day.night ? "#ffe39a" : "#c8b890");
      if (day.night) lights.push({ x: l.x, y: l.y - 13, r: 14, c: "255,210,130" });
    } }));
    items.push({ y: L.fountain.y + 4, draw: () => drawFountain(g, L.fountain, t, lights, day) });
    T.walkers.forEach((w) => items.push({ y: w.y, draw: () => {
      sprite(g, w.seed, w.kind, "walk", Math.floor(t * 6) % 2, w.x, w.y, w.flip);
      if (w.loot) {
        const lx = Math.round(w.x + (w.flip ? -8 : 4));
        rect(g, lx, Math.round(w.y - 10), 5, 4, "#c48a2a");
        rect(g, lx + 1, Math.round(w.y - 12), 3, 2, "#f6e27a");
      } else if (w.scroll) rect(g, Math.round(w.x + (w.flip ? -6 : 4)), Math.round(w.y - 7), 2, 3, "#f3e4c3");
      if (day.night) lights.push({ x: w.x, y: w.y - 6, r: 8, c: "255,220,150" });
    } }));
    T.wanderers.forEach((w) => items.push({ y: w.y, draw: () => sprite(g, w.seed, w.kind, w.wait > 0 ? "stand" : "walk", Math.floor(t * 5) % 2, w.x, w.y, w.flip) }));
    const gm = guildmasterAt(L);
    items.push({ y: gm.y, draw: () => sprite(g, "guildmaster", "cleric", "stand", 0, gm.x, gm.y, false) });
    if (!day.night || day.name === "dusk") {
      T.villagers.forEach((v) => items.push({ y: v.y, draw: () => sprite(g, v.seed, v.kind, v.wait > 0 ? "stand" : "walk", Math.floor(t * 5) % 2, v.x, v.y, v.flip) }));
    }
    items.sort((a, b) => a.y - b.y).forEach((it) => it.draw());
    T.particles.forEach((p) => {
      const k = 1 - p.age / p.life;
      if (p.kind === "spark") { px(g, p.x, p.y, "#ffb347"); lights.push({ x: p.x, y: p.y, r: 3, c: "255,150,60", a: k }); }
      else { g.globalAlpha = 0.5 * k; rect(g, p.x, p.y, 2 + Math.round((1 - k) * 2), 2 + Math.round((1 - k) * 2), "#d8d4cc"); g.globalAlpha = 1; }
    });
    // speech marks over buildings
    L.buildings.forEach((b) => {
      const topY = b.y - b.hw - b.hr - 10;
      if (b.people.some((p) => p.act === "needs")) bubble(g, b.x, topY + Math.round(Math.sin(t * 5) * 1.5), "!");
      else if (b.sleepy) {
        const k = (t * 0.6) % 1;
        g.globalAlpha = 1 - k;
        zz(g, b.x + 8 + k * 4, topY + 6 - k * 8);
        g.globalAlpha = 1;
      }
    });
  }

  function drawMill(g, m, t) {
    g.drawImage(m.body, m.x - 8, m.y - 34);
    const hx = m.x, hy = m.y - 25;
    const a0 = reduced ? 0.4 : t * 0.9;
    for (let k = 0; k < 4; k++) {
      const a = a0 + (k * Math.PI) / 2;
      const ca = Math.cos(a), sa = Math.sin(a);
      for (let r = 1; r <= 13; r++) {
        px(g, hx + ca * r, hy + sa * r, "#4a2e1a");
        if (r > 3) for (let s = 1; s <= 3; s++) px(g, hx + ca * r - sa * s, hy + sa * r + ca * s, (r + s) % 3 ? "#efe3c6" : "#d6c39a");
      }
    }
    rect(g, hx - 1, hy - 1, 3, 3, "#3b2414");
  }

  function drawFountain(g, f, t, lights, day) {
    oval(g, f.x, f.y, 13, 5, "#7d7466");
    oval(g, f.x, f.y - 1, 11, 4, "#4f8fc0");
    oval(g, f.x, f.y - 1, 8, 3, "#6fb0e0");
    rect(g, f.x - 1, f.y - 9, 3, 8, "#9a9284");
    for (let i = 0; i < 6; i++) {
      const k = ((t * 1.2 + i / 6) % 1);
      const side = i % 2 ? 1 : -1;
      px(g, f.x + side * (1 + k * 6), f.y - 10 + k * k * 9, "#bfe6ff");
    }
    if (Math.floor(t * 3) % 2) px(g, f.x - 4, f.y - 2, "#ffffff");
    if (day.night) lights.push({ x: f.x, y: f.y - 2, r: 10, c: "120,180,255", a: 0.4 });
  }

  function bubble(g, x, y, mark) {
    rect(g, x - 4, y - 4, 9, 8, "#f3e4c3");
    rect(g, x - 3, y - 5, 7, 10, "#f3e4c3");
    px(g, x - 1, y + 5, "#f3e4c3");
    if (mark === "!") { rect(g, x, y - 3, 1, 4, "#b42318"); px(g, x, y + 2, "#b42318"); }
  }

  function zz(g, x, y) {
    rect(g, x, y, 3, 1, "#c8d0f0"); px(g, x + 1, y + 1, "#c8d0f0"); rect(g, x, y + 2, 3, 1, "#c8d0f0");
  }

  function drawRoom(g, t, day, lights) {
    const R = T.roomLayout;
    g.drawImage(R.bg, 0, 0);
    // window: sky by time of day, plus a beam of light in daytime
    const w = R.window;
    const sky = day.night ? "#26305a" : day.name === "golden" || day.name === "dusk" ? "#f0b080" : "#9fc6e8";
    rect(g, w.x, w.y, w.w, w.h, sky);
    rect(g, w.x + 7, w.y, 2, w.h, "#2d1b10");
    rect(g, w.x, w.y + 8, w.w, 2, "#2d1b10");
    if (day.night) { px(g, w.x + 3, w.y + 3, "#ffffff"); px(g, w.x + 12, w.y + 5, "#ffe39a"); }
    else lights.push({ beam: true, x: w.x, y: w.y, w: w.w, h: w.h });
    // fire
    const f = R.fire;
    for (let i = 0; i < 5; i++) {
      const h = 5 + Math.round(Math.abs(Math.sin(t * 7 + i * 1.7)) * 6);
      rect(g, f.x - 5 + i * 2, f.y - 2 - h, 2, h, i % 2 ? "#ff8a3d" : "#ffc35a");
    }
    rect(g, f.x - 6, f.y - 2, 12, 2, "#5a3820");
    lights.push({ x: f.x, y: f.y - 6, r: f.forge ? 44 : 30, c: f.forge ? "255,110,40" : "255,140,60", a: 0.85 + Math.sin(t * 9) * 0.1 });
    if (f.forge && Math.random() < 0.3) T.particles.push({ x: f.x + (Math.random() - 0.5) * 8, y: f.y - 8, vx: (Math.random() - 0.5) * 6, vy: -10 - Math.random() * 8, life: 0.8, age: 0, kind: "spark" });
    R.lamps.forEach((l) => { rect(g, l.x - 1, l.y - 2, 3, 4, "#ffd27a"); lights.push({ x: l.x, y: l.y, r: 22, c: "255,200,120" }); });
    const items = [];
    R.chairs.forEach((c) => items.push({ y: c.y, draw: () => {
      rect(g, c.x - 6, c.y - 13, 12, 9, "#7a2230");
      rect(g, c.x - 6, c.y - 13, 12, 1, "#5c1823");
      sprite(g, c.p.seed, c.p.kind, "read", Math.floor(t / 3) % 2, c.x, c.y - 2);
      rect(g, c.x - 7, c.y - 5, 2, 6, "#5c1823");
      rect(g, c.x + 5, c.y - 5, 2, 6, "#5c1823");
      rect(g, c.x - 6, c.y - 2, 12, 3, "#8e2a3a");
      c.hit = [c.x - 7, c.y - 16, 14, 18];
    } }));
    R.desks.forEach((d) => items.push({ y: d.y, draw: () => {
      const p = d.p, deskX = d.x - (p.act === "needs" ? 6 : 0);
      if (p.act === "work") sprite(g, p.seed, p.kind, "write", Math.floor(t * 5) % 2, deskX, d.y - 2);
      if (p.act === "read" || p.act === "sleep") sprite(g, p.seed, p.kind, p.act === "read" ? "read" : "sit", 0, deskX, d.y - 2);
      rect(g, deskX - 12, d.y - 5, 24, 5, "#8b5a2b");
      rect(g, deskX - 12, d.y - 5, 24, 1, "#a8743e");
      rect(g, deskX - 12, d.y, 24, 4, "#6b4423");
      rect(g, deskX - 11, d.y + 4, 2, 3, "#4a2e1a");
      rect(g, deskX + 9, d.y + 4, 2, 3, "#4a2e1a");
      rect(g, deskX - 6, d.y - 5, 5, 3, "#f3e4c3");
      if (p.act === "work") {
        rect(g, deskX + 6, d.y - 8, 1, 3, "#f6e7c1");
        px(g, deskX + 6, d.y - 9 - (Math.floor(t * 8) % 2), "#ffb347");
        lights.push({ x: deskX + 6, y: d.y - 9, r: 12, c: "255,190,100" });
        if (Math.floor(t * 2) % 3 === 0) px(g, deskX - 3, d.y - 4, "#2b1d0e");
      }
      if (p.act === "sleep") {
        const k = (t * 0.5 + d.x * 0.01) % 1;
        g.globalAlpha = 1 - k; zz(g, deskX + 4 + k * 3, d.y - 18 - k * 8); g.globalAlpha = 1;
      }
      if (p.act === "needs") {
        sprite(g, p.seed, p.kind, "stand", 0, deskX + 16, d.y + 4);
        bubble(g, deskX + 16, d.y - 18 + Math.round(Math.sin(t * 5) * 1.5), "!");
      }
      d.hit = p.act === "needs" ? [deskX + 9, d.y - 24, 14, 30] : [deskX - 12, d.y - 16, 24, 22];
    } }));
    (R.tables || []).forEach((s) => items.push({ y: s.y, draw: () => {
      const p = s.p, sx = s.x + (s.side ? 10 : -10);
      sprite(g, p.seed, p.kind, "sit", 0, sx, s.y - 1, !!s.side);
      if (!s.side) {
        oval(g, s.x, s.y - 3, 7, 3, "#6b4423");
        oval(g, s.x, s.y - 4, 6, 2, "#8b5a2b");
        rect(g, s.x - 1, s.y - 1, 2, 4, "#4a2e1a");
        rect(g, s.x - 4, s.y - 7, 2, 3, "#e8d8b4");
        rect(g, s.x + 2, s.y - 7, 2, 3, "#e8d8b4");
      }
      s.hit = [sx - 6, s.y - 15, 12, 16];
    } }));
    R.beds.forEach((b) => items.push({ y: b.y + 20, draw: () => {
      const p = b.p;
      rect(g, b.x - 7, b.y, 14, 23, "#5a3820");
      rect(g, b.x - 6, b.y + 1, 12, 21, "#e8dcc0");
      rect(g, b.x - 5, b.y + 2, 10, 4, "#f7efdc");
      sprite(g, p.seed, p.kind, "sleep", 0, b.x, b.y + 10);
      const look = Sprites.look(p.seed, p.kind);
      rect(g, b.x - 6, b.y + 9, 12, 13, look.robe);
      for (let y = b.y + 11; y < b.y + 22; y += 3) rect(g, b.x - 6, y, 12, 1, Sprites.shade(look.robe, -30));
      rect(g, b.x - 6, b.y + 9, 12, 1, "#f7efdc");
      const k = (t * 0.5 + b.x * 0.02) % 1;
      g.globalAlpha = 1 - k; zz(g, b.x + 5 + k * 3, b.y - 2 - k * 8); g.globalAlpha = 1;
      b.hit = [b.x - 7, b.y, 14, 23];
    } }));
    items.sort((a, b) => a.y - b.y).forEach((it) => it.draw());
    T.particles.forEach((p) => { if (p.kind === "spark") { px(g, p.x, p.y, "#ffb347"); lights.push({ x: p.x, y: p.y, r: 3, c: "255,150,60", a: 1 - p.age / p.life }); } });
  }

  function paint() {
    if (!T.buf || (T.view === "town" ? !T.layout : !T.roomLayout)) return;
    const g = T.g;
    const t = T.time;
    const day = daylight();
    const lights = [];
    g.imageSmoothingEnabled = false;
    if (T.view === "town") drawTown(g, t, day, lights);
    else drawRoom(g, t, day, lights);
    // time-of-day grade, then lit windows punched back in so they stay bright
    if (day.k > 0.06) {
      g.globalCompositeOperation = "multiply";
      g.globalAlpha = T.view === "room" ? day.k * 0.6 : day.k;
      rect(g, 0, 0, T.W, T.H, day.tint);
      g.globalAlpha = 1;
      g.globalCompositeOperation = "source-over";
    }
    lights.forEach((l) => { if (l.rect) rect(g, l.rect[0], l.rect[1], l.rect[2], l.rect[3], LIT_WINDOW); });
    if (T.view === "town") drawSky(g, t);
    if (T.view === "town" && day.night) {
      T.flies.forEach((f, i) => {
        const x = f.x + Math.sin(t * f.s + i) * 6, y = f.y + Math.cos(t * f.s * 0.8 + i) * 4;
        if (Math.sin(t * 3 + i * 2) > -0.2) { px(g, x, y, "#fff3a0"); lights.push({ x, y, r: 5, c: "255,240,140", a: 0.8 }); }
      });
    }
    const out = el.canvas.getContext("2d");
    out.imageSmoothingEnabled = false;
    out.drawImage(T.buf, 0, 0, el.canvas.width, el.canvas.height);
    // bloom
    const gl = el.glow.getContext("2d");
    gl.clearRect(0, 0, el.glow.width, el.glow.height);
    gl.globalCompositeOperation = "lighter";
    const k = el.glow.width / T.W;
    lights.forEach((l) => {
      if (l.beam) {
        gl.fillStyle = "rgba(255,230,170,.10)";
        gl.beginPath();
        gl.moveTo(l.x * k, l.y * k);
        gl.lineTo((l.x + l.w) * k, l.y * k);
        gl.lineTo((l.x + l.w - 30) * k, (T.H - 4) * k);
        gl.lineTo((l.x - 40) * k, (T.H - 4) * k);
        gl.fill();
        return;
      }
      const grad = gl.createRadialGradient(l.x * k, l.y * k, 0, l.x * k, l.y * k, l.r * k);
      const a = (l.a == null ? 0.7 : l.a) * day.glow;
      grad.addColorStop(0, "rgba(" + l.c + "," + a + ")");
      grad.addColorStop(1, "rgba(" + l.c + ",0)");
      gl.fillStyle = grad;
      gl.fillRect((l.x - l.r) * k, (l.y - l.r) * k, l.r * 2 * k, l.r * 2 * k);
    });
    gl.globalCompositeOperation = "source-over";
    // tilt-shift is the expensive pass; phones skip it
    if (!T.lite) {
      const bl = el.blur.getContext("2d");
      bl.imageSmoothingEnabled = false;
      bl.clearRect(0, 0, el.blur.width, el.blur.height);
      bl.drawImage(el.canvas, 0, 0, el.blur.width, el.blur.height);
    }
  }

  function drawSky(g, t) {
    const sky = skyName();
    if (sky === "sunny") return;
    if (sky === "cloudy") {
      g.globalAlpha = 0.16;
      rect(g, 0, 0, T.W, T.H, "#8d97a6");
      g.globalAlpha = 1;
      return;
    }
    g.globalAlpha = 0.28;
    rect(g, 0, 0, T.W, T.H, "#5d6b7c");
    g.globalAlpha = 0.9;
    const drops = T.lite ? 28 : 48;
    for (let i = 0; i < drops; i++) {
      const x = (i * 29 + t * 48) % T.W;
      const y = (i * 47 + t * 90) % T.H;
      rect(g, x, y, 1, 4, "#e7eef8");
    }
    g.globalAlpha = 1;
  }

  // ---- DOM: stage size, hit areas, labels ----------------------------------------------

  function availableWidth() {
    return el.stage.parentElement.clientWidth || el.town.clientWidth || document.documentElement.clientWidth || 0;
  }

  function sizeStage() {
    T.avail = availableWidth();
    const cssW = Math.min(T.avail, 760);
    T.S = T.view === "town" ? TOWN_SCALE : ROOM_SCALE;
    T.W = Math.floor(cssW / T.S);
    const narrow = cssW < 520;
    T.lite = narrow || reduced;
    const dpr = window.devicePixelRatio || 1;
    T.dpr = T.lite ? 1 : Math.min(2, dpr);
  }

  function applySize() {
    const cssW = T.W * T.S, cssH = T.H * T.S;
    el.stage.style.width = cssW + "px";
    el.stage.style.height = cssH + "px";
    [el.canvas].forEach((c) => { c.width = T.W * T.S * T.dpr; c.height = T.H * T.S * T.dpr; });
    el.glow.width = Math.round(cssW / 2); el.glow.height = Math.round(cssH / 2);
    el.blur.width = Math.round(cssW / 2); el.blur.height = Math.round(cssH / 2);
    T.buf = offscreen(T.W, T.H);
    T.g = T.buf.getContext("2d");
    T.flies = Array.from({ length: T.lite ? 4 : 14 }, () => ({ x: Math.random() * T.W, y: 20 + Math.random() * (T.H - 30), s: 0.5 + Math.random() }));
  }

  function guildmasterAt(L) {
    const hall = L.hall || L.buildings.find((b) => b.hall) || { x: L.W / 2, y: 40, w: 40 };
    return { x: hall.x + Math.round((hall.w || 40) / 2) + 10, y: hall.y + 1 };
  }

  const pct = (v, total) => (v / total * 100).toFixed(3) + "%";

  function place(node, x, y, w, h) {
    node.style.left = pct(x, T.W);
    node.style.top = pct(y, T.H);
    if (w != null) { node.style.width = pct(w, T.W); node.style.height = pct(h, T.H); }
  }

  function counts(people) {
    const n = (a) => people.filter((p) => p.act === a).length;
    return [n("needs") && "! " + n("needs"), n("work") && "⚒ " + n("work"), n("read") && "🍺 " + n("read"), n("sleep") && "☾ " + n("sleep")].filter(Boolean).join("  ");
  }

  const DOING = { work: "Working", needs: "Awaits you", read: "On break", sleep: "Asleep" };

  function questBoard() {
    const rows = (T.state && T.state.board) || [];
    const body = rows.length ? rows.map((q) =>
      `<button type="button" class="quest rarity-${esc(q.rarity || "Common")}" data-open="petitions" data-arg="${esc(q.id)}">` +
      `<b>${esc(q.title)}</b><small>${esc(q.rarity)} · ${q.xp} XP · ${esc(q.by || "")}</small></button>`).join("")
      : `<p class="sub">No open petitions. The board is clear.</p>`;
    return `<div class="quest-board"><h3>Quest board</h3>${body}</div>`;
  }

  function roster(home) {
    const people = home.people;
    const working = people.filter((p) => p.act === "work").length;
    const specialists = people.filter((p) => p.specialist).length;
    el.roster.hidden = false;
    const board = home.hall ? questBoard() : "";
    el.roster.innerHTML = board + `<header><b>${esc(home.name)}</b><small>${people.length} ${people.length === 1 ? "person" : "people"} · ${working} working` +
      (specialists ? ` · ${specialists} specialist${specialists === 1 ? "" : "s"}` : "") + `</small></header>` +
      (people.length ? people.map((p) =>
        `<button type="button" class="roster-row a-${esc(p.act)}" ${p.staff ? `data-staff="${esc(p.staff)}"` : `data-sid="${esc(p.sid)}"`}>` +
        `${Sprites.svg(p.seed, p.kind, 22)}<span><b>${esc(p.label || p.name)}</b><small>${esc(p.title || "")}${p.title ? " · " : ""}${esc(p.specialist ? "Specialist" : p.name)}</small></span>` +
        `<em>${esc(DOING[p.act] || "")}</em></button>`).join("") : `<p class="sub">Nobody here right now.</p>`);
  }

  function townHits() {
    const L = T.layout;
    el.hits.textContent = "";
    L.buildings.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "hit bld";
      if (b.plot) {
        btn.dataset.open = "hire";
        btn.setAttribute("aria-label", "Empty plot: recruit a specialist to build here");
      } else {
        btn.dataset.bld = b.id;
        btn.setAttribute("aria-label", `${b.name}: ${b.people.length} inside`);
      }
      place(btn, b.x - b.w / 2 - 3, b.y - b.hw - b.hr - 4, b.w + 6, b.hw + b.hr + 8);
      el.hits.appendChild(btn);
      const plate = document.createElement("div");
      plate.className = "plate-label" + (b.people.some((p) => p.act === "needs") ? " alert" : "");
      plate.innerHTML = `<b>${esc(b.plot ? "＋ Recruit" : b.name.replace(/^The /, ""))}</b>` +
        (b.people.length ? `<small>${esc(counts(b.people))}</small>` : b.plot ? "" : "<small>quiet</small>");
      place(plate, b.x, b.y + 4);
      el.hits.appendChild(plate);
    });
  }

  function roomHits() {
    el.hits.textContent = "";
    const R = T.roomLayout;
    paint();  // fills in the hit rects
    const spots = R.chairs.concat(R.desks, R.beds, R.tables || []);
    spots.forEach((s) => {
      if (!s.hit) return;
      const p = s.p;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "hit person";
      if (p.staff) btn.dataset.staff = p.staff; else btn.dataset.sid = p.sid;
      const doing = { work: "at work", needs: "awaits your word", read: "on a break", sleep: "asleep" }[p.act];
      btn.setAttribute("aria-label", `${p.name}: ${doing}`);
      place(btn, s.hit[0], s.hit[1], s.hit[2], s.hit[3]);
      const tag = document.createElement("span");
      tag.className = "name-tag" + (p.act === "needs" ? " alert" : "");
      const label = p.label || p.name;
      tag.textContent = label.length > 14 ? label.slice(0, 13) + "…" : label;
      btn.appendChild(tag);
      el.hits.appendChild(btn);
    });
    if (!spots.length) {
      const empty = document.createElement("p");
      empty.className = "room-empty";
      empty.textContent = T.room.hall ? "The hall is quiet. Post a quest to wake it up." : "Nobody home right now.";
      el.hits.appendChild(empty);
    }
  }

  // ---- views ----------------------------------------------------------------------------

  function homesNow() {
    return T.state ? residents(T.state) : [{ id: "hall", name: "Guild Hall", hall: true, people: [] }];
  }

  function rebuild() {
    try { build(); } catch (e) { if (typeof reportError === "function") reportError(e, "town"); }
  }

  function build() {
    sizeStage();
    // a home-screen app on iOS can report a width of 0 right after launch; measure again shortly
    if (T.W < 40) { clearTimeout(T.retry); T.retry = setTimeout(rebuild, 250); return; }
    const homes = homesNow();
    if (T.view === "town") {
      buildTown(homes);
      applySize();
      townHits();
      el.title.textContent = "Guild Town";
      const day = daylight();
      const sky = skyName();
      el.sub.textContent = (day.night ? "Night in Jakarta" : day.name + " in Jakarta") + (sky === "rain" ? " · rain" : sky === "cloudy" ? " · cloudy" : "");
      el.back.hidden = true;
      el.roster.hidden = true;
    } else {
      const home = homes.find((h) => h.id === T.room) || homes[0];
      T.room = home.id;
      buildRoom(home);
      applySize();
      el.title.textContent = home.name;
      el.sub.textContent = counts(home.people) || "nobody home";
      el.back.hidden = false;
      T.roomHome = home;
      roomHits();
      roster(home);
    }
    paint();
  }

  function enter(id, push) {
    if (T.view === "room" && T.room === id) return;
    el.stage.classList.add("fade");
    setTimeout(() => {
      T.view = "room";
      T.room = id;
      rebuild();
      el.stage.classList.remove("fade");
    }, reduced ? 0 : 160);
    if (push !== false) history.pushState({ town: id }, "");
  }

  function leave(pop) {
    if (T.view === "town") return;
    el.stage.classList.add("fade");
    setTimeout(() => {
      T.view = "town";
      T.room = null;
      rebuild();
      el.stage.classList.remove("fade");
    }, reduced ? 0 : 160);
    if (!pop && history.state && history.state.town) history.back();
  }

  let lastKey = "";
  function update(state) {
    T.state = state;
    if (!T.built) return;
    const homes = residents(state);
    T.people = homes.flatMap((h) => h.people);
    const key = JSON.stringify([T.view, T.room, daylight().name, homes.map((h) => [h.id, h.people.map((p) => [p.id, p.name, p.act])])]);
    noticeChanges(homes);
    if (key !== lastKey || Math.abs(availableWidth() - (T.avail || 0)) > 2) { lastKey = key; rebuild(); }
  }

  function loop(now) {
    requestAnimationFrame(loop);
    const visible = !document.hidden && !document.body.classList.contains("sheet-open");
    if (!visible || reduced) return;
    if (now - T.last < 33) return;
    const dt = Math.min(0.1, (now - (T.last || now)) / 1000);
    T.last = now;
    T.time += dt;
    try { step(dt); paint(); } catch (e) { if (typeof reportError === "function") reportError(e, "town frame"); }
  }

  function init() {
    el.town = document.getElementById("town");
    el.stage = document.getElementById("town-stage");
    el.canvas = document.getElementById("town-canvas");
    el.glow = document.getElementById("town-glow");
    el.blur = document.getElementById("town-blur");
    el.hits = document.getElementById("town-hits");
    el.title = document.getElementById("town-title");
    el.sub = document.getElementById("town-sub");
    el.back = document.getElementById("town-back");
    el.roster = document.getElementById("town-roster");
    T.built = true;
    el.back.addEventListener("click", () => leave(false));
    el.hits.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-bld]");
      if (b) enter(b.dataset.bld);
    });
    window.addEventListener("popstate", (ev) => {
      if (ev.state && ev.state.town) enter(ev.state.town, false);
      else if (!ev.state || !(ev.state.sheet || ev.state.panel)) leave(true);
    });
    let resizeTimer = null;
    const later = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(rebuild, 150); };
    window.addEventListener("resize", later);
    window.addEventListener("pageshow", later);
    if (window.ResizeObserver) {
      new ResizeObserver(() => { if (Math.abs(availableWidth() - (T.avail || 0)) > 2) later(); }).observe(el.stage.parentElement);
    }
    rebuild();
    if (T.state) update(T.state);
    requestAnimationFrame(loop);
  }

  return { init, update, enter, leave, speak };
})();

Hooks.header.push((state) => Town.update(state));
// a retried download can arrive after DOMContentLoaded
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => Town.init());
else { Town.init(); if (UI.state) Town.update(UI.state); }
