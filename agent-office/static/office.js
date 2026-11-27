// The office map: a top-down pixel office. One room per division, a lounge for people on a break, a server
// room for the programs. People sit at desks when they work, stand with the phone when they need you, take a
// coffee in the lounge when idle, and walk between rooms along the corridors when that changes. Everything is
// drawn on a canvas at device resolution so motion is smooth and the pixels stay crisp.
"use strict";

const Office = (() => {
  const BASE_SCALE = 2;
  const CELL_W = 18, CELL_H = 21;          // one desk with its chair
  const PAD = 5, WALL = 7, SPINE = 10, HALL = 9, EDGE = 3;
  const WALK_SPEED = 30;                   // virtual pixels per second
  const ROOM_FLOORS = {
    Executive: ["#e9dcff", "#e1d2fa"], Management: ["#ffe6c7", "#f8dcb8"], Operations: ["#d9e6f7", "#cfdcf0"],
    Engineering: ["#dce6ff", "#d1dcf8"], Finance: ["#dcf5e2", "#cfeed7"], Research: ["#def1f8", "#d2e8f1"],
    Writing: ["#fff3cf", "#f8ebc0"], Design: ["#ffdbea", "#f8cfe0"], Business: ["#ffe3c9", "#f6d8bb"],
    Science: ["#d9f3ec", "#cdeae2"], Legal: ["#e6e7ee", "#dcdde6"], Creative: ["#f8dfff", "#efd3f8"],
    General: ["#ececec", "#e2e2e2"], Office: ["#dfe8f7", "#d4def0"], Lounge: ["#f7e8d3", "#efdfc6"],
    "Server room": ["#d7dce9", "#cbd1e0"],
  };
  const DEFAULT_FLOOR = [["#e7e3d8", "#dcd8cc"], ["#dfe9e2", "#d3ded6"], ["#eee3df", "#e4d8d3"]];
  const ORDER = ["Executive", "Management", "Operations", "Engineering", "Finance", "Research", "Writing", "Design",
    "Business", "Science", "Legal", "Creative", "General", "Office"];
  const reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  const el = {};
  const T = {
    W: 0, H: 0, S: BASE_SCALE, dpr: 1, k: 2, layout: null, bg: null, state: null, people: new Map(),
    time: 0, last: 0, built: false, particles: [], chatAt: 4, lite: false, hover: null,
  };

  // ---- pixel helpers -----------------------------------------------------------------------
  const rect = (g, x, y, w, h, c) => { g.fillStyle = c; g.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); };
  const px = (g, x, y, c) => rect(g, x, y, 1, 1, c);
  function offscreen(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.ceil(w));
    c.height = Math.max(1, Math.ceil(h));
    return c;
  }
  function rng(seed) {
    let a = People.hash(String(seed)) || 1;
    return () => {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function floorOf(name) {
    return ROOM_FLOORS[name] || DEFAULT_FLOOR[People.hash(name) % DEFAULT_FLOOR.length];
  }

  // ---- time of day (the Mac is in Jakarta) ---------------------------------------------------
  function jakartaHour() {
    try {
      const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date());
      return Number(parts.find((p) => p.type === "hour").value) + Number(parts.find((p) => p.type === "minute").value) / 60;
    } catch (e) { return ((Date.now() / 3600000) + 7) % 24; }
  }
  function daylight() {
    const h = jakartaHour();
    if (h >= 21 || h < 5) return { name: "night", k: 0.5, night: true, tint: "#5663a8" };
    if (h >= 18) return { name: "evening", k: 0.25, night: true, tint: "#b58ab0" };
    if (h < 7) return { name: "early", k: 0.12, night: false, tint: "#ffd7b0" };
    return { name: "day", k: 0, night: false, tint: "#ffffff" };
  }
  function greeting() {
    const h = jakartaHour();
    return h < 11 ? "Morning" : h < 15 ? "Afternoon" : h < 18.5 ? "Late afternoon" : "Evening";
  }

  // ---- who is where -----------------------------------------------------------------------------
  function activity(o, now) {
    if (o.status === "needs") return "needs";
    if (o.status === "working") return "work";
    const quiet = now - (o.active || 0);
    return quiet > 90 * 60 ? "off" : "break";
  }

  function residents(state) {
    const now = Date.now() / 1000;
    const projects = {};
    const leads = (state.sessions || []).filter((s) => !s.staff && !s.helper && ["working", "needs", "idle"].includes(s.status))
      .map((s) => {
        projects[s.project] = (projects[s.project] || 0) + 1;
        return { id: s.id, sid: s.id, name: s.name, label: s.persona ? s.persona.name.split(" ")[0] : s.name,
          title: s.title && s.title !== s.name ? s.title : (s.project ? s.project : ""), seed: s.id, kind: null, status: s.status,
          note: s.note, act: activity(s, now), division: s.division || "Office", project: s.project, kindLabel: "Your chat",
          remote: s.desktop ? "Claude app" : (s.live ? "live" : "background"), model: s.model, cost: s.pay ? s.pay.today : 0, active: s.active };
      });
    const team = state.team || { staff: [], divisions: [] };
    const staff = team.staff.filter((m) => !m.builtin || m.status === "working").map((m) => ({
      id: m.id, staff: m.id, sid: m.sid, name: m.name, label: m.name, title: m.role || m.title, seed: m.seed, kind: m.kind,
      status: m.status, note: m.note, act: activity(m, now), division: m.division || "General", project: "",
      kindLabel: m.chain ? m.chain : "Specialist", specialist: true, builtin: !!m.builtin, seat: m.chain || "",
      remote: m.sid ? "live" : "", model: m.model, cost: m.pay ? m.pay.today : 0, active: m.active,
    }));
    const everyone = leads.concat(staff);
    const divisions = new Map();
    everyone.forEach((p) => {
      if (!divisions.has(p.division)) divisions.set(p.division, { name: p.division, people: [], working: 0, needs: 0, cost: 0 });
      const d = divisions.get(p.division);
      d.people.push(p);
      if (p.act === "work") d.working++;
      if (p.act === "needs") d.needs++;
      d.cost += p.cost || 0;
    });
    const list = [...divisions.values()].sort((a, b) => {
      const ia = ORDER.indexOf(a.name), ib = ORDER.indexOf(b.name);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.name.localeCompare(b.name);
    });
    return { everyone, divisions: list, projects, onBreak: everyone.filter((p) => p.act === "break"), off: everyone.filter((p) => p.act === "off") };
  }

  // ---- layout -----------------------------------------------------------------------------------
  function layout(res, W) {
    const colW = Math.floor((W - 2 * EDGE - SPINE) / 2);
    const cols = Math.max(2, Math.floor((colW - 2 * PAD) / CELL_W));
    const rooms = [];
    // reception across the top
    const reception = { id: "reception", name: "Reception", x: EDGE, y: EDGE + 4, w: W - 2 * EDGE, h: 32, kind: "reception", people: [] };
    let y = reception.y + reception.h + HALL;
    const divisions = res.divisions.filter((d) => d.people.some((p) => p.act !== "break") || d.people.length);
    const cells = divisions.map((d) => {
      const desks = Math.max(cols, d.people.length);
      const rows = Math.ceil(desks / cols);
      return { id: "d:" + d.name, name: d.name, kind: "division", division: d, desks, rows, h: WALL + PAD + rows * CELL_H + PAD };
    });
    const racks = Math.max(3, Math.min(cols * 2, (res.programs && res.programs.length) || 3));
    cells.push({ id: "lounge", name: "Lounge", kind: "lounge", h: WALL + PAD + 2 * CELL_H + 6 });
    cells.push({ id: "servers", name: "Server room", kind: "servers", racks, h: WALL + PAD + 2 * CELL_H + 6 });
    for (let i = 0; i < cells.length; i += 2) {
      const pair = cells.slice(i, i + 2);
      const h = Math.max(...pair.map((c) => c.h));
      pair.forEach((c, j) => rooms.push(Object.assign(c, { x: EDGE + j * (colW + SPINE), y, w: colW, h, hall: y + h + Math.floor(HALL / 2) })));
      y += h + HALL;
    }
    const H = y - HALL + EDGE + 4;
    const spineX = EDGE + colW + Math.floor(SPINE / 2);
    rooms.forEach((r) => { r.door = { x: r.x + Math.floor(r.w / 2), y: r.y + r.h }; r.spots = spotsOf(r, cols); });
    reception.door = { x: spineX, y: reception.y + reception.h };
    reception.hall = reception.y + reception.h + Math.floor(HALL / 2);
    return { W, H, rooms, reception, spineX, cols, colW };
  }

  function spotsOf(r, cols) {
    const spots = [];
    const top = r.y + WALL + PAD;
    if (r.kind === "division") {
      const span = (r.w - 2 * PAD) / cols;
      for (let i = 0; i < r.desks; i++) {
        const c = i % cols, row = Math.floor(i / cols);
        spots.push({ x: Math.round(r.x + PAD + span * c + span / 2), y: top + row * CELL_H + 7, kind: "desk" });
      }
    } else if (r.kind === "lounge") {
      // two sofas facing a table, plus the coffee machine corner
      const cx = r.x + Math.floor(r.w / 2);
      const sofaY = top + 2, sofa2Y = top + CELL_H + 6;
      [-16, -5, 6].forEach((dx) => spots.push({ x: cx + dx - 6, y: sofaY, kind: "sofa" }));
      [-16, -5, 6].forEach((dx) => spots.push({ x: cx + dx - 6, y: sofa2Y, kind: "sofa", flip: true }));
      spots.push({ x: r.x + r.w - 12, y: top + 4, kind: "stand" });
      spots.push({ x: r.x + r.w - 24, y: top + 10, kind: "stand" });
    }
    return spots;
  }

  // ---- the static background: floors, walls, furniture -------------------------------------------
  function drawRoomShell(g, r, floor, rnd) {
    // carpet tiles
    for (let yy = r.y + WALL; yy < r.y + r.h; yy++) {
      for (let xx = r.x; xx < r.x + r.w; xx++) {
        const tile = ((xx >> 3) + (yy >> 3)) & 1;
        g.fillStyle = tile ? floor[0] : floor[1];
        if (rnd() < 0.015) g.fillStyle = People.shade(floor[1], -8);
        g.fillRect(xx, yy, 1, 1);
      }
    }
    // back wall with a skirting line, a window or a whiteboard
    rect(g, r.x, r.y, r.w, WALL, "#f1efe8");
    rect(g, r.x, r.y, r.w, 1, "#cfcbc0");
    rect(g, r.x, r.y + WALL - 1, r.w, 1, "#bdb8aa");
    // outer walls
    rect(g, r.x - 1, r.y - 1, r.w + 2, 1, "#6b6f80");
    rect(g, r.x - 1, r.y + r.h, r.w + 2, 1, "#6b6f80");
    rect(g, r.x - 1, r.y - 1, 1, r.h + 2, "#6b6f80");
    rect(g, r.x + r.w, r.y - 1, 1, r.h + 2, "#6b6f80");
    // door gap in the bottom wall
    rect(g, r.door.x - 4, r.y + r.h, 8, 1, floor[0]);
    rect(g, r.door.x - 4, r.y + r.h, 1, 1, "#8a7a5a"); rect(g, r.door.x + 3, r.y + r.h, 1, 1, "#8a7a5a");
  }

  function drawDesk(g, x, y, t) {
    // a desk seen from above: top, monitor, keyboard, mug; the chair sits below it
    rect(g, x - 7, y - 7, 14, 7, "#d9c3a0");
    rect(g, x - 7, y - 7, 14, 1, "#efe0c8");
    rect(g, x - 7, y - 1, 14, 1, "#b59a72");
    rect(g, x - 3, y - 7, 6, 4, "#2c3141");           // monitor
    rect(g, x - 2, y - 6, 4, 2, "#394a6a");
    rect(g, x - 1, y - 3, 2, 1, "#2c3141");
    rect(g, x - 2, y - 2, 5, 1, "#8b8f9c");           // keyboard
    px(g, x + 5, y - 5, "#f1f1f1"); px(g, x + 5, y - 4, "#e65c5c");   // mug
  }

  function drawChair(g, x, y, c) {
    rect(g, x - 3, y, 6, 3, c || "#4a5068");
    rect(g, x - 3, y + 3, 6, 1, "#2c3141");
  }

  function drawPlant(g, x, y, big) {
    rect(g, x - 2, y - 2, 4, 3, "#b86b4a");
    rect(g, x - 2, y - 3, 4, 1, "#d08a66");
    rect(g, x - 3, y - 7, 6, 4, "#3f9d5a");
    rect(g, x - 2, y - 9, 4, 2, "#55b86e");
    px(g, x - 4, y - 5, "#3f9d5a"); px(g, x + 3, y - 6, "#3f9d5a");
    if (big) { rect(g, x - 1, y - 11, 2, 2, "#6fcf84"); px(g, x + 4, y - 8, "#55b86e"); px(g, x - 5, y - 8, "#55b86e"); }
  }

  function drawCooler(g, x, y) {
    rect(g, x - 2, y - 10, 5, 6, "#8ec5f5");
    rect(g, x - 2, y - 10, 5, 1, "#c9e6ff");
    rect(g, x - 3, y - 4, 7, 5, "#eef1f5");
    rect(g, x - 3, y - 4, 7, 1, "#c7ccd4");
    px(g, x, y - 2, "#3a7bd5");
  }

  function drawPrinter(g, x, y) {
    rect(g, x - 5, y - 5, 10, 6, "#d9dbe0");
    rect(g, x - 5, y - 5, 10, 1, "#f3f4f6");
    rect(g, x - 3, y - 7, 6, 2, "#fdfdfd");
    rect(g, x - 4, y - 2, 8, 1, "#8a8f9a");
    px(g, x + 3, y - 4, "#4fcf7a");
  }

  function drawWhiteboard(g, x, y, w) {
    rect(g, x, y, w, 4, "#ffffff");
    rect(g, x, y, w, 4, "rgba(0,0,0,0)");
    rect(g, x, y + 4, w, 1, "#c5c7cc");
    for (let i = 2; i < w - 2; i += 3) px(g, x + i, y + 1 + (i % 2), ["#2d7ef7", "#e05454", "#2ca86a"][i % 3]);
  }

  function drawWindow(g, x, y, w, night) {
    rect(g, x, y, w, 5, night ? "#30406a" : "#a9d4f5");
    if (!night) { rect(g, x + 1, y + 1, Math.max(1, w - 2), 1, "#d8edff"); }
    else { px(g, x + 2, y + 1, "#ffe9a0"); px(g, x + w - 3, y + 2, "#ffffff"); }
    rect(g, x + Math.floor(w / 2), y, 1, 5, "#e8e4dc");
    rect(g, x - 1, y, 1, 5, "#bfb9ad"); rect(g, x + w, y, 1, 5, "#bfb9ad");
  }

  function drawSofa(g, x, y, c, flip) {
    rect(g, x, y, 30, 8, c);
    rect(g, x, flip ? y + 6 : y, 30, 2, People.shade(c, -30));
    rect(g, x - 1, y, 1, 8, People.shade(c, -30)); rect(g, x + 30, y, 1, 8, People.shade(c, -30));
    for (let i = 10; i < 30; i += 10) rect(g, x + i, y + 2, 1, 4, People.shade(c, -18));
  }

  function drawCoffee(g, x, y) {
    rect(g, x - 4, y - 11, 8, 11, "#3a3f4b");
    rect(g, x - 4, y - 11, 8, 1, "#5b6170");
    rect(g, x - 2, y - 8, 4, 2, "#e9eef5");
    px(g, x, y - 4, "#f4b942");
    rect(g, x - 3, y - 2, 6, 1, "#20242c");
  }

  function drawRack(g, x, y) {
    rect(g, x, y - 20, 8, 20, "#30353f");
    rect(g, x, y - 20, 8, 1, "#4b515d");
    for (let i = 2; i < 19; i += 3) rect(g, x + 1, y - 20 + i, 6, 1, "#1d2027");
    rect(g, x, y, 8, 1, "#171a20");
  }

  function drawClock(g, x, y) {
    rect(g, x - 3, y - 3, 7, 7, "#f7f7f7");
    rect(g, x - 2, y - 4, 5, 9, "#f7f7f7");
    rect(g, x - 4, y - 2, 9, 5, "#f7f7f7");
    px(g, x, y, "#333");
  }

  function buildBackground(L, res) {
    const c = offscreen(L.W, L.H);
    const g = c.getContext("2d");
    const rnd = rng("office" + L.W);
    const night = daylight().night;
    // corridors: dark grey-blue carpet
    for (let yy = 0; yy < L.H; yy++) for (let xx = 0; xx < L.W; xx++) {
      const tile = ((xx >> 2) + (yy >> 2)) & 1;
      g.fillStyle = tile ? "#8f98ab" : "#8791a4";
      g.fillRect(xx, yy, 1, 1);
    }
    // reception
    const R = L.reception;
    drawRoomShell(g, R, ["#e9eef7", "#dfe6f2"], rnd);
    rect(g, R.x + 8, R.y + 2, 30, 4, "#1d4ed8");
    for (let i = 0; i < 11; i++) px(g, R.x + 10 + i * 2 + (i % 2), R.y + 3 + (i % 2), "#ffffff");
    drawWindow(g, R.x + Math.floor(R.w * 0.55), R.y + 1, Math.floor(R.w * 0.3), night);
    drawClock(g, R.x + R.w - 10, R.y + 3);
    rect(g, R.x + 42, R.y + WALL + 10, 26, 9, "#cfd8e6");   // the front desk
    rect(g, R.x + 42, R.y + WALL + 10, 26, 1, "#eaf0f8");
    rect(g, R.x + 42, R.y + WALL + 18, 26, 1, "#9aa8bd");
    drawPlant(g, R.x + 14, R.y + R.h - 4, true);
    drawPlant(g, R.x + R.w - 14, R.y + R.h - 4, true);
    drawPrinter(g, R.x + R.w - 36, R.y + R.h - 5);
    L.receptionSeat = { x: R.x + 55, y: R.y + WALL + 9 };
    // rooms
    L.rooms.forEach((r) => {
      const floor = r.kind === "lounge" ? ROOM_FLOORS.Lounge : r.kind === "servers" ? ROOM_FLOORS["Server room"] : floorOf(r.name);
      drawRoomShell(g, r, floor, rnd);
      if (r.kind === "division") {
        drawWhiteboard(g, r.x + 4, r.y + 1, Math.max(10, Math.floor(r.w * 0.35)));
        drawWindow(g, r.x + Math.floor(r.w * 0.55), r.y + 1, Math.max(8, Math.floor(r.w * 0.32)), night);
        r.spots.forEach((s) => { drawDesk(g, s.x, s.y, 0); drawChair(g, s.x, s.y + 1); });
        drawPlant(g, r.x + r.w - 5, r.y + r.h - 3, false);
        if (r.w > 70) drawCooler(g, r.x + 5, r.y + r.h - 2);
      } else if (r.kind === "lounge") {
        drawWindow(g, r.x + 6, r.y + 1, Math.max(10, Math.floor(r.w * 0.4)), night);
        const cx = r.x + Math.floor(r.w / 2);
        const top = r.y + WALL + PAD;
        drawSofa(g, cx - 24, top + 2, "#c86b5a", false);
        rect(g, cx - 12, top + 13, 24, 7, "#9a7b55"); rect(g, cx - 12, top + 13, 24, 1, "#c1a07a");
        px(g, cx - 6, top + 15, "#f1f1f1"); px(g, cx + 3, top + 16, "#f1f1f1");
        drawSofa(g, cx - 24, top + CELL_H + 7, "#5a7fc8", true);
        drawCoffee(g, r.x + r.w - 8, top + 1);
        drawPlant(g, r.x + 6, r.y + r.h - 3, true);
        drawPlant(g, r.x + r.w - 6, r.y + r.h - 3, false);
      } else if (r.kind === "servers") {
        const n = r.racks;
        const gap = Math.max(10, Math.floor((r.w - 2 * PAD) / n));
        r.rackAt = [];
        for (let i = 0; i < n; i++) {
          const x = r.x + PAD + i * gap + Math.floor((gap - 8) / 2), y = r.y + WALL + PAD + 20;
          drawRack(g, x, y);
          r.rackAt.push({ x: x + 6, y: y - 17 });
        }
        for (let yy = r.y + r.h - 14; yy < r.y + r.h; yy += 3) rect(g, r.x + 2, yy, r.w - 4, 1, "#b9c0d2");
        rect(g, r.x + r.w - 14, r.y + 1, 10, 4, "#58b0ff"); // a cold AC vent
      }
    });
    return c;
  }

  // ---- people: spots, paths, motion -----------------------------------------------------------------
  function assignSpots(res, L) {
    const used = new Set();
    const where = new Map();   // person id -> {room, spot, act}
    const roomOf = {};
    L.rooms.forEach((r) => { roomOf[r.id] = r; });
    const lounge = roomOf.lounge;
    const loungeFree = lounge ? lounge.spots.slice() : [];
    res.everyone.forEach((p) => {
      const room = roomOf["d:" + p.division];
      if (p.act === "break") {
        const spot = loungeFree.shift();
        if (spot) { where.set(p.id, { room: lounge, spot, pose: spot.kind === "sofa" ? "coffee" : "stand", flip: !!spot.flip }); return; }
        // the lounge is full: they take their break at their desk
      }
      if (!room) return;
      const spot = room.spots.find((s) => !used.has(s));
      if (!spot) return;
      used.add(spot);
      const pose = p.act === "work" ? "type" : p.act === "needs" ? "phone" : p.act === "break" ? "coffee" : "off";
      where.set(p.id, { room, spot, pose });
    });
    return where;
  }

  function pathBetween(a, roomA, b, roomB, L) {
    // leave through the door, along the hall to the spine, along the spine to the other hall, in through the door
    const path = [];
    const out = roomA ? [{ x: a.x, y: roomA.door.y - 2 }, { x: roomA.door.x, y: roomA.door.y - 2 }, { x: roomA.door.x, y: roomA.hall }] : [{ x: a.x, y: a.y }];
    const inn = roomB ? [{ x: roomB.door.x, y: roomB.hall }, { x: roomB.door.x, y: roomB.door.y - 2 }, { x: b.x, y: roomB.door.y - 2 }, { x: b.x, y: b.y }] : [{ x: b.x, y: b.y }];
    path.push(...out);
    const hallA = roomA ? roomA.hall : a.y, hallB = roomB ? roomB.hall : b.y;
    if (hallA !== hallB) path.push({ x: L.spineX, y: hallA }, { x: L.spineX, y: hallB });
    path.push(...inn);
    return path;
  }

  function placePeople(res, L) {
    const where = assignSpots(res, L);
    const seen = new Set();
    res.everyone.forEach((p) => {
      const w = where.get(p.id);
      if (!w) return;
      seen.add(p.id);
      let person = T.people.get(p.id);
      const target = { x: w.spot.x, y: w.spot.y + (w.spot.kind === "desk" ? 4 : w.spot.kind === "sofa" ? 7 : 0) };
      if (!person) {
        // new arrivals walk in from reception
        const start = L.receptionSeat ? { x: L.reception.door.x, y: L.reception.hall } : target;
        person = { id: p.id, x: start.x, y: start.y, path: [], pose: "walk", flip: false, dist: 0, sitFlip: !!w.flip };
        T.people.set(p.id, person);
        person.path = reduced ? [target] : pathBetween(start, null, target, w.room, L);
        if (reduced) { person.x = target.x; person.y = target.y; }
      } else if (person.room !== w.room || person.tx !== target.x || person.ty !== target.y) {
        person.path = reduced ? [target] : pathBetween({ x: person.x, y: person.y }, person.room, target, w.room, L);
        if (reduced) { person.x = target.x; person.y = target.y; person.path = []; }
      }
      person.room = w.room; person.tx = target.x; person.ty = target.y; person.spot = w.spot;
      person.restPose = w.pose; person.sitFlip = !!w.flip; person.data = p;
    });
    [...T.people.keys()].forEach((id) => { if (!seen.has(id)) T.people.delete(id); });
  }

  function step(dt) {
    T.people.forEach((m) => {
      if (m.path.length) {
        let left = dt * WALK_SPEED;
        while (left > 0 && m.path.length) {
          const a = m.path[0];
          const dx = a.x - m.x, dy = a.y - m.y, d = Math.hypot(dx, dy);
          if (d <= left) { m.x = a.x; m.y = a.y; m.path.shift(); left -= d; }
          else { m.x += (dx / d) * left; m.y += (dy / d) * left; if (Math.abs(dx) > 0.01) m.flip = dx < 0; left = 0; }
          m.dist += Math.min(d, left + d);
        }
        m.pose = m.path.length ? "walk" : m.restPose;
        if (!m.path.length) { m.flip = m.sitFlip; if (m.data.act === "work") puff(m.x, m.y - 14, "type"); }
      } else {
        m.pose = m.restPose;
      }
    });
    T.particles.forEach((p) => { p.age += dt; p.x += p.vx * dt; p.y += p.vy * dt; });
    T.particles = T.particles.filter((p) => p.age < p.life);
    chatter();
  }

  function puff(x, y, kind) {
    if (T.particles.length > 24) return;
    T.particles.push({ x, y, vx: (Math.random() - 0.5) * 2, vy: -4 - Math.random() * 3, life: 1.6, age: 0, kind });
  }

  // ---- chatter --------------------------------------------------------------------------------------------
  const LINES = {
    work: ["Almost there…", "One more test", "Who broke main?", "Reading the logs", "Hmm, curious", "The RPC is grumpy today",
      "Double-checking the decimals", "This bug is sneaky", "Pipeline's still warming up", "Compiling, brb"],
    break: ["Coffee first", "Tokens are pricey", "Standup at ten?", "Anyone seen the charger?", "Quick stretch", "Nothing urgent yet",
      "Patiently waiting", "Steeping, nothing urgent yet", "Did the deploy go out?"],
    needs: ["Boss? A word?", "Waiting on your call", "Need your OK on this", "Psst, over here", "Remember, a webhook is slow… needs you!"],
    off: [],
  };
  const REPLIES = { "Who broke main?": "Not me!", "Tokens are pricey": "Worth it!", "Standup at ten?": "Ten sharp", "Did the deploy go out?": "Green across",
    "Almost there…": "You got this", "This bug is sneaky": "Squash it!", "Coffee first": "Always" };
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

  function chatter() {
    if (T.time < T.chatAt || !T.layout) return;
    T.chatAt = T.time + 6 + Math.random() * 6;
    const people = [...T.people.values()].filter((m) => m.data && m.data.act !== "off" && !m.path.length);
    if (!people.length) return;
    const m = pick(people);
    const lines = LINES[m.data.act] || LINES.break;
    if (!lines.length) return;
    let line = pick(lines);
    if (m.data.act === "break" && T.state && T.state.cost_today > 20 && Math.random() < 0.5) line = "Tokens are pricey";
    say(m.data, line);
    const answer = REPLIES[line];
    if (answer) {
      const others = people.filter((o) => o !== m);
      if (others.length) say(pick(others).data, answer, 1700);
    }
  }

  function anchorOf(p) {
    const m = T.people.get(p.id);
    return m ? { x: m.x, y: m.y - 18 } : null;
  }

  function bubbleAt(at, text) {
    if (!at || !el.hits) return;
    const b = document.createElement("div");
    b.className = "chat";
    b.textContent = text;
    b.style.left = (at.x / T.W * 100).toFixed(2) + "%";
    b.style.top = (at.y / T.H * 100).toFixed(2) + "%";
    el.hits.appendChild(b);
    setTimeout(() => b.classList.add("gone"), 3400);
    setTimeout(() => b.remove(), 3900);
  }

  function say(p, text, delay) {
    setTimeout(() => {
      if (typeof ChatLog !== "undefined") ChatLog.add("say", p.label || p.name, text, p.sid, null, "", { local: true });
      bubbleAt(anchorOf(p), text);
    }, delay || 0);
  }

  function speak(line) {
    if (!line || !line.text || !T.layout) return;
    const who = String(line.who || "");
    if (line.ch === "external" && /^(GET|POST|PUT|DELETE)\b/.test(line.text)) return;
    const boss = line.ch === "external" || /office|guild hall|guildmaster|bojjifomo/i.test(who);
    if (boss) { bubbleAt({ x: T.layout.receptionSeat.x, y: T.layout.receptionSeat.y - 10 }, line.text); return; }
    if (line.ch !== "say" && line.ch !== "guild" && line.ch !== "orders") return;
    const m = [...T.people.values()].find((x) => x.data && ((x.data.label || x.data.name) === who || String(x.data.name || "").indexOf(who) === 0));
    if (m) bubbleAt(anchorOf(m.data), line.text);
  }

  // ---- drawing a frame ------------------------------------------------------------------------------------
  function sprite(g, seed, kind, pose, frame, x, y, flip, alpha) {
    const img = People.canvas(seed, kind, pose, frame);
    const k = T.k;
    const dx = Math.round(x * k - (img.width * k) / 2), dy = Math.round(y * k - img.height * k);
    g.globalAlpha = alpha == null ? 1 : alpha;
    if (flip) {
      g.save(); g.translate(dx + img.width * k, dy); g.scale(-1, 1); g.drawImage(img, 0, 0, img.width * k, img.height * k); g.restore();
    } else g.drawImage(img, dx, dy, img.width * k, img.height * k);
    g.globalAlpha = 1;
  }

  function paint() {
    if (!T.layout || !T.bg) return;
    const L = T.layout, k = T.k, t = T.time, day = daylight();
    const out = el.canvas.getContext("2d");
    out.imageSmoothingEnabled = false;
    out.drawImage(T.bg, 0, 0, el.canvas.width, el.canvas.height);
    const lights = [];
    // monitors on / off, typing dots, sitting people, racks
    const items = [];
    L.rooms.forEach((r) => {
      if (r.kind === "servers") {
        const rows = (T.programs || []);
        r.rackAt.forEach((p, i) => {
          const prog = rows[i];
          const on = prog ? (prog.ok ? (Math.floor(t * 6 + i) % 7 !== 0) : (Math.floor(t * 3) % 2 === 0)) : Math.floor(t * 2 + i) % 5 !== 0;
          const col = !prog ? "#7a8296" : prog.ok ? "#39e07a" : "#ff5a5a";
          if (on) { rect2(out, p.x, p.y, 1, 1, col); if (prog) lights.push({ x: p.x, y: p.y, r: 4, c: prog.ok ? "60,220,120" : "255,80,80" }); }
          rect2(out, p.x, p.y + 3, 1, 1, Math.floor(t * 4 + i * 2) % 3 ? "#ffd166" : "#7a8296");
        });
      }
    });
    T.people.forEach((m) => {
      const p = m.data;
      if (!p) return;
      items.push({ y: m.y, draw: () => {
        const seat = m.spot && !m.path.length;
        const desk = seat && m.spot.kind === "desk";
        if (desk && m.restPose !== "off") {
          // the monitor glows while someone sits at it
          rect2(out, m.spot.x - 2, m.spot.y - 6, 4, 2, p.act === "work" ? "#7ab8ff" : "#5577aa");
          lights.push({ x: m.spot.x, y: m.spot.y - 5, r: 7, c: p.act === "work" ? "120,180,255" : "90,120,180" });
        }
        if (m.restPose === "off" && seat) return;   // went home: an empty chair
        const pose = m.path.length ? "walk" : m.restPose === "off" ? "sit" : m.restPose;
        const frame = m.path.length ? Math.floor(m.dist / 6) % 2 : pose === "type" ? Math.floor(t * 6) % 2 : 0;
        const standing = pose === "phone" || pose === "stand" || pose === "walk";
        const yy = standing ? m.y : m.y - 1;
        const bob = pose === "coffee" ? Math.sin(t * 2 + m.x) * 0.3 : 0;
        if (pose === "phone") sprite(out, p.seed, p.kind, "phone", 0, m.x + 9, m.y + 8, false);
        else sprite(out, p.seed, p.kind, pose, frame, m.x, yy + bob, m.flip);
        if (desk && !m.path.length && pose !== "phone") drawChairBack(out, m.spot.x, m.spot.y + 5);
        if (pose === "phone") {
          const hop = Math.round(Math.sin(t * 5) * 1.5);
          mark(out, m.x + 9, m.y - 13 + hop);
        }
        if (pose === "coffee" && Math.floor(t * 2 + m.x) % 5 === 0) puff(m.x + (m.flip ? -3 : 3), m.y - 12, "steam");
      } });
    });
    // the receptionist: bojjifomo's seat, with a hand up when something needs you
    const R = L.receptionSeat;
    const needs = T.state && T.state.sessions && T.state.sessions.some((s) => s.status === "needs");
    items.push({ y: R.y + 6, draw: () => { sprite(out, "bojjifomo-reception", null, needs ? "stand" : "sit", 0, R.x, R.y + 6, false); drawChairBack(out, R.x, R.y + 7); } });
    items.sort((a, b) => a.y - b.y).forEach((it) => it.draw());
    T.particles.forEach((pt) => {
      const a = 1 - pt.age / pt.life;
      out.globalAlpha = (pt.kind === "type" ? 0.9 : 0.5) * a;
      rect2(out, pt.x, pt.y, pt.kind === "type" ? 1 : 1.5, pt.kind === "type" ? 1 : 1.5, pt.kind === "type" ? "#2d3b5a" : "#ffffff");
      out.globalAlpha = 1;
    });
    // evening tint, then the lights
    if (day.k > 0) {
      out.globalCompositeOperation = "multiply";
      out.globalAlpha = day.k;
      out.fillStyle = day.tint;
      out.fillRect(0, 0, el.canvas.width, el.canvas.height);
      out.globalAlpha = 1;
      out.globalCompositeOperation = "source-over";
    }
    const gl = el.glow.getContext("2d");
    gl.clearRect(0, 0, el.glow.width, el.glow.height);
    if (lights.length) {
      gl.globalCompositeOperation = "lighter";
      const kk = el.glow.width / T.W;
      lights.forEach((l) => {
        const grad = gl.createRadialGradient(l.x * kk, l.y * kk, 0, l.x * kk, l.y * kk, l.r * kk);
        const a = (day.night ? 0.75 : 0.35);
        grad.addColorStop(0, "rgba(" + l.c + "," + a + ")");
        grad.addColorStop(1, "rgba(" + l.c + ",0)");
        gl.fillStyle = grad;
        gl.fillRect((l.x - l.r) * kk, (l.y - l.r) * kk, l.r * 2 * kk, l.r * 2 * kk);
      });
      gl.globalCompositeOperation = "source-over";
    }
  }
  function rect2(g, x, y, w, h, c) { g.fillStyle = c; g.fillRect(Math.round(x * T.k), Math.round(y * T.k), Math.max(1, Math.round(w * T.k)), Math.max(1, Math.round(h * T.k))); }
  function drawChairBack(g, x, y) { rect2(g, x - 3, y, 6, 1, "#2c3141"); }
  function mark(g, x, y) {
    rect2(g, x - 3, y - 4, 7, 7, "#ffffff"); rect2(g, x - 2, y - 5, 5, 9, "#ffffff"); rect2(g, x - 1, y + 4, 2, 1, "#ffffff");
    rect2(g, x, y - 3, 1, 4, "#e03131"); rect2(g, x, y + 2, 1, 1, "#e03131");
  }

  // ---- DOM: size, plates, taps ------------------------------------------------------------------------
  function availableWidth() {
    return el.stage.parentElement.clientWidth || document.documentElement.clientWidth || 0;
  }

  function sizeStage() {
    T.avail = availableWidth();
    const cssW = Math.min(T.avail, 760);
    T.S = cssW >= 600 ? 3 : BASE_SCALE;
    T.W = Math.floor(cssW / T.S);
    T.lite = cssW < 520 || reduced;
    T.dpr = Math.min(T.lite ? 2 : 3, window.devicePixelRatio || 1);
    T.k = T.S * T.dpr;
  }

  function applySize() {
    const cssW = T.W * T.S, cssH = T.H * T.S;
    el.stage.style.width = cssW + "px";
    el.stage.style.height = cssH + "px";
    el.canvas.width = Math.round(T.W * T.k); el.canvas.height = Math.round(T.H * T.k);
    el.glow.width = Math.round(cssW / 2); el.glow.height = Math.round(cssH / 2);
  }

  const pct = (v, total) => (v / total * 100).toFixed(3) + "%";
  function plates(L, res) {
    el.hits.textContent = "";
    const add = (r, label, sub, alert, division) => {
      const plate = document.createElement("button");
      plate.type = "button";
      plate.className = "plate-label" + (alert ? " alert" : "");
      plate.innerHTML = `<b>${esc(label)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}`;
      plate.style.left = pct(r.x + 2, T.W);
      plate.style.top = pct(r.y + 1, T.H);
      if (division) plate.dataset.division = division;
      plate.setAttribute("aria-label", label + (sub ? ": " + sub : ""));
      el.hits.appendChild(plate);
    };
    L.rooms.forEach((r) => {
      if (r.kind === "division") {
        const d = r.division;
        const bits = [d.working ? d.working + " working" : "", d.needs ? d.needs + " need you" : ""].filter(Boolean).join(" · ");
        add(r, d.name, bits || (d.people.length ? d.people.length + " people" : "quiet"), d.needs > 0, d.name);
      } else if (r.kind === "lounge") {
        add(r, "Lounge", res.onBreak.length ? res.onBreak.length + " on break" : "empty", false, "Lounge");
      } else if (r.kind === "servers") {
        const pr = (T.state && T.state.programs) || {};
        add(r, "Server room", pr.total ? `${pr.up || 0} up${pr.trouble ? " · " + pr.trouble + " in trouble" : ""}` : "no programs yet", (pr.trouble || 0) > 0, "Server room");
      }
    });
    const rp = document.createElement("div");
    rp.className = "plate-label sign";
    rp.innerHTML = `<b>Agent Office</b><small>${esc(greeting())} · ${esc(T.state && T.state.name || "")}</small>`;
    rp.style.left = pct(L.reception.x + 2, T.W);
    rp.style.top = pct(L.reception.y + 1, T.H);
    el.hits.appendChild(rp);
  }

  function personAt(cssX, cssY) {
    const x = cssX / T.S, y = cssY / T.S;
    let best = null, bd = 10;
    T.people.forEach((m) => {
      if (m.restPose === "off" && !m.path.length) return;
      const phone = m.restPose === "phone" && !m.path.length;
      const cx = phone ? m.x + 9 : m.x, cy = phone ? m.y - 1 : m.y - 9;
      const d = Math.hypot(cx - x, cy - y);
      if (d < bd) { bd = d; best = m; }
    });
    return best;
  }

  // ---- build + loop ----------------------------------------------------------------------------------
  let lastKey = "";
  function build() {
    sizeStage();
    if (T.W < 40) { clearTimeout(T.retry); T.retry = setTimeout(rebuild, 250); return; }
    const res = residents(T.state || { sessions: [], team: { staff: [] } });
    res.programs = T.programs || [];
    const L = layout(res, T.W);
    T.layout = L; T.H = L.H;
    T.bg = buildBackground(L, res);
    applySize();
    placePeople(res, L);
    plates(L, res);
    paint();
  }
  function rebuild() {
    try { build(); } catch (e) { if (typeof reportError === "function") reportError(e, "office"); }
  }

  function update(state) {
    T.state = state;
    if (!T.built) return;
    const res = residents(state);
    const key = JSON.stringify([daylight().name, res.divisions.map((d) => [d.name, d.people.map((p) => [p.id, p.act])]), res.onBreak.length,
      state.programs && [state.programs.up, state.programs.trouble, state.programs.total]]);
    if (key !== lastKey || Math.abs(availableWidth() - (T.avail || 0)) > 2) { lastKey = key; rebuild(); }
    if (typeof Directory !== "undefined") Directory.update(res, state);
  }

  async function loadPrograms() {
    try {
      const r = await api("/api/programs");
      T.programs = r.rows || [];
      if (T.built) rebuild();
    } catch (e) { /* the racks blink grey until the server room answers */ }
  }

  function loop(now) {
    requestAnimationFrame(loop);
    const visible = !document.hidden && !document.body.classList.contains("sheet-open");
    if (!visible) return;
    const dt = Math.min(0.1, (now - (T.last || now)) / 1000);
    T.last = now;
    if (reduced && now - (T.lastPaint || 0) < 500) return;
    T.lastPaint = now;
    T.time += dt;
    try { step(dt); paint(); } catch (e) { if (typeof reportError === "function") reportError(e, "office frame"); }
  }

  function init() {
    el.stage = document.getElementById("office-stage");
    el.canvas = document.getElementById("office-canvas");
    el.glow = document.getElementById("office-glow");
    el.hits = document.getElementById("office-hits");
    if (!el.stage) return;
    T.built = true;
    el.canvas.addEventListener("click", (ev) => {
      const r = el.canvas.getBoundingClientRect();
      const m = personAt(ev.clientX - r.left, ev.clientY - r.top);
      if (!m) return;
      if (m.data.staff) openPanel("staff", true, m.data.staff); else openSheet(m.data.sid);
    });
    el.hits.addEventListener("click", (ev) => {
      const plate = ev.target.closest("[data-division]");
      if (plate && typeof Directory !== "undefined") Directory.focus(plate.dataset.division);
    });
    let resizeTimer = null;
    const later = () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(rebuild, 150); };
    window.addEventListener("resize", later);
    window.addEventListener("pageshow", later);
    if (window.ResizeObserver) new ResizeObserver(() => { if (Math.abs(availableWidth() - (T.avail || 0)) > 2) later(); }).observe(el.stage.parentElement);
    rebuild();
    if (T.state) update(T.state);
    loadPrograms();
    setInterval(loadPrograms, 60000);
    requestAnimationFrame(loop);
  }

  return { init, update, speak, residents, greeting, jakartaHour };
})();

const Town = Office;   // older scripts talk to the town
Hooks.header.push((state) => Office.update(state));
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => Office.init());
else { Office.init(); if (UI.state) Office.update(UI.state); }
