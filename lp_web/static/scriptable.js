// =============================================================================
//  Scriptable compatibility layer for the browser.
//
//  Implements the part of Scriptable's API that the RH scripts use (UITable,
//  Alert, Request, FileManager, Timer, DrawContext, ...) so the scripts run in a
//  Mac browser unchanged. Requests and files go through the local server
//  (server.py); nothing here talks to the outside world directly.
// =============================================================================
(function () {
  "use strict";

  const TOKEN = window.LP_TOKEN;
  const nativeFetch = window.fetch.bind(window);

  // ---------------------------------------------------------------- server I/O
  function apiFetch(path, body) {
    return nativeFetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "X-LP-Token": TOKEN, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(async (r) => {
      const j = await r.json().catch(() => ({ error: "bad response from local server" }));
      if (!r.ok) throw new Error(j.error || `local server ${r.status}`);
      return j;
    });
  }
  // Scriptable's FileManager and importModule are synchronous, so these are too.
  function apiSync(path) {
    const x = new XMLHttpRequest();
    x.open("GET", path, false);
    x.setRequestHeader("X-LP-Token", TOKEN);
    x.send(null);
    let j;
    try { j = JSON.parse(x.responseText); } catch (e) { j = { error: "bad response from local server" }; }
    if (x.status !== 200) throw new Error(j.error || `local server ${x.status}`);
    return j;
  }
  const qs = (o) => Object.entries(o).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");

  // ------------------------------------------------------------------- log UI
  const logLines = [];
  function log(kind, parts) {
    const text = parts.map((p) => {
      if (typeof p === "string") return p;
      if (p instanceof Error) return p.stack || p.message;
      try { return JSON.stringify(p, (k, v) => (typeof v === "bigint" ? v.toString() + "n" : v)); }
      catch (e) { return String(p); }
    }).join(" ");
    logLines.push({ kind, text, t: new Date() });
    if (logLines.length > 500) logLines.shift();
    const el = document.getElementById("lp-log-body");
    if (el) {
      const d = document.createElement("div");
      d.className = "lp-log-" + kind;
      d.textContent = `${new Date().toLocaleTimeString()}  ${text}`;
      el.appendChild(d);
      while (el.childNodes.length > 500) el.removeChild(el.firstChild);
      el.scrollTop = el.scrollHeight;
    }
  }
  const sConsole = {
    log: (...a) => { console.log(...a); log("log", a); },
    warn: (...a) => { console.warn(...a); log("warn", a); },
    error: (...a) => { console.error(...a); log("error", a); },
    logError: (...a) => { console.error(...a); log("error", a); },
    info: (...a) => { console.info(...a); log("log", a); },
    debug: (...a) => { console.debug(...a); },
  };

  // ---------------------------------------------------------- basic value types
  class Color {
    constructor(hex, alpha) {
      let h = String(hex || "000000").replace(/^#/, "");
      if (h.length === 3) h = h.split("").map((c) => c + c).join("");
      let a = 1;
      if (h.length === 8) { a = parseInt(h.slice(6, 8), 16) / 255; h = h.slice(0, 6); }
      this.hex = h.toUpperCase();
      this.red = parseInt(h.slice(0, 2), 16) / 255;
      this.green = parseInt(h.slice(2, 4), 16) / 255;
      this.blue = parseInt(h.slice(4, 6), 16) / 255;
      this.alpha = alpha == null ? a : Number(alpha);
    }
    get _css() {
      return `rgba(${Math.round(this.red * 255)},${Math.round(this.green * 255)},${Math.round(this.blue * 255)},${this.alpha})`;
    }
    static dynamic(light, dark) { return dark || light; }
    static black() { return new Color("000000"); }
    static white() { return new Color("FFFFFF"); }
    static clear() { return new Color("000000", 0); }
    static red() { return new Color("FF3B30"); }
    static green() { return new Color("34C759"); }
    static blue() { return new Color("007AFF"); }
    static orange() { return new Color("FF9500"); }
    static yellow() { return new Color("FFCC00"); }
    static purple() { return new Color("AF52DE"); }
    static gray() { return new Color("8E8E93"); }
    static darkGray() { return new Color("555555"); }
    static lightGray() { return new Color("AAAAAA"); }
    static brown() { return new Color("A2845E"); }
    static cyan() { return new Color("32ADE6"); }
    static magenta() { return new Color("FF00FF"); }
  }

  class Font {
    constructor(name, size, weight, mono, italic) {
      this.name = name || "system"; this.size = Number(size) || 17;
      this.weight = weight || 400; this.mono = !!mono; this.italic = !!italic;
    }
    get _css() {
      const fam = this.mono
        ? `ui-monospace, "SF Mono", Menlo, monospace`
        : `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif`;
      return `${this.italic ? "italic " : ""}${this.weight} ${this.size}px ${fam}`;
    }
    static systemFont(s) { return new Font("system", s, 400); }
    static ultraLightSystemFont(s) { return new Font("system", s, 100); }
    static thinSystemFont(s) { return new Font("system", s, 200); }
    static lightSystemFont(s) { return new Font("system", s, 300); }
    static regularSystemFont(s) { return new Font("system", s, 400); }
    static mediumSystemFont(s) { return new Font("system", s, 500); }
    static semiboldSystemFont(s) { return new Font("system", s, 600); }
    static boldSystemFont(s) { return new Font("system", s, 700); }
    static heavySystemFont(s) { return new Font("system", s, 800); }
    static blackSystemFont(s) { return new Font("system", s, 900); }
    static italicSystemFont(s) { return new Font("system", s, 400, false, true); }
    static regularMonospacedSystemFont(s) { return new Font("mono", s, 400, true); }
    static mediumMonospacedSystemFont(s) { return new Font("mono", s, 500, true); }
    static semiboldMonospacedSystemFont(s) { return new Font("mono", s, 600, true); }
    static boldMonospacedSystemFont(s) { return new Font("mono", s, 700, true); }
    static largeTitle() { return new Font("system", 34, 400); }
    static title1() { return new Font("system", 28, 400); }
    static title2() { return new Font("system", 22, 400); }
    static title3() { return new Font("system", 20, 400); }
    static headline() { return new Font("system", 17, 600); }
    static subheadline() { return new Font("system", 15, 400); }
    static body() { return new Font("system", 17, 400); }
    static callout() { return new Font("system", 16, 400); }
    static footnote() { return new Font("system", 13, 400); }
    static caption1() { return new Font("system", 12, 400); }
    static caption2() { return new Font("system", 11, 400); }
  }

  class Point { constructor(x, y) { this.x = Number(x) || 0; this.y = Number(y) || 0; } }
  class Size { constructor(w, h) { this.width = Number(w) || 0; this.height = Number(h) || 0; } }
  class Rect {
    constructor(x, y, w, h) { this.x = +x || 0; this.y = +y || 0; this.width = +w || 0; this.height = +h || 0; }
    get minX() { return this.x; } get minY() { return this.y; }
    get maxX() { return this.x + this.width; } get maxY() { return this.y + this.height; }
    get origin() { return new Point(this.x, this.y); } get size() { return new Size(this.width, this.height); }
  }

  class Image {
    constructor(src, w, h) { this._src = src; this.size = new Size(w, h); }
    static fromData() { return null; }
    static fromFile() { return null; }
  }
  class Data {
    constructor(str) { this._s = str || ""; }
    static fromString(s) { return new Data(String(s)); }
    static fromBase64String(b) { try { return new Data(atob(b)); } catch (e) { return null; } }
    toRawString() { return this._s; }
    toBase64String() { return btoa(this._s); }
    getBytes() { return Array.from(this._s, (c) => c.charCodeAt(0) & 255); }
  }

  // --------------------------------------------------------------- DrawContext
  class Path {
    constructor() { this._ops = []; }
    move(p) { this._ops.push(["m", p.x, p.y]); }
    addLine(p) { this._ops.push(["l", p.x, p.y]); }
    addCurve(p, c1, c2) { this._ops.push(["c", c1.x, c1.y, c2.x, c2.y, p.x, p.y]); }
    addQuadCurve(p, c) { this._ops.push(["q", c.x, c.y, p.x, p.y]); }
    addRect(r) { this._ops.push(["r", r.x, r.y, r.width, r.height]); }
    addEllipse(r) { this._ops.push(["e", r.x, r.y, r.width, r.height]); }
    addRoundedRect(r, cw, ch) { this._ops.push(["rr", r.x, r.y, r.width, r.height, cw]); }
    addLines(pts) { pts.forEach((p, i) => (i ? this.addLine(p) : this.move(p))); }
    addRects(rs) { rs.forEach((r) => this.addRect(r)); }
    closeSubpath() { this._ops.push(["z"]); }
  }
  function tracePath(ctx, path) {
    ctx.beginPath();
    for (const o of path._ops) {
      if (o[0] === "m") ctx.moveTo(o[1], o[2]);
      else if (o[0] === "l") ctx.lineTo(o[1], o[2]);
      else if (o[0] === "c") ctx.bezierCurveTo(o[1], o[2], o[3], o[4], o[5], o[6]);
      else if (o[0] === "q") ctx.quadraticCurveTo(o[1], o[2], o[3], o[4]);
      else if (o[0] === "r") ctx.rect(o[1], o[2], o[3], o[4]);
      else if (o[0] === "e") ctx.ellipse(o[1] + o[3] / 2, o[2] + o[4] / 2, o[3] / 2, o[4] / 2, 0, 0, Math.PI * 2);
      else if (o[0] === "rr") { if (ctx.roundRect) ctx.roundRect(o[1], o[2], o[3], o[4], o[5]); else ctx.rect(o[1], o[2], o[3], o[4]); }
      else if (o[0] === "z") ctx.closePath();
    }
  }
  class DrawContext {
    constructor() {
      this.size = new Size(200, 200); this.respectScreenScale = false; this.opaque = true;
      this._canvas = null; this._ctx = null; this._path = null;
      this._fill = "#000"; this._stroke = "#000"; this._lw = 1;
      this._font = new Font("system", 17); this._text = "#000"; this._align = "left";
    }
    get ctx() {
      if (!this._ctx) {
        const s = this.respectScreenScale ? (window.devicePixelRatio || 2) : 1;
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(this.size.width * s));
        c.height = Math.max(1, Math.round(this.size.height * s));
        const x = c.getContext("2d");
        x.scale(s, s);
        if (this.opaque) { x.fillStyle = "#000"; x.fillRect(0, 0, this.size.width, this.size.height); }
        this._canvas = c; this._ctx = x;
      }
      return this._ctx;
    }
    setFillColor(c) { this._fill = c._css; }
    setStrokeColor(c) { this._stroke = c._css; }
    setLineWidth(w) { this._lw = w; }
    setFont(f) { this._font = f; }
    setTextColor(c) { this._text = c._css; }
    setTextAlignedLeft() { this._align = "left"; }
    setTextAlignedCenter() { this._align = "center"; }
    setTextAlignedRight() { this._align = "right"; }
    fill(r) { this.fillRect(r); }
    fillRect(r) { const x = this.ctx; x.fillStyle = this._fill; x.fillRect(r.x, r.y, r.width, r.height); }
    fillEllipse(r) { const p = new Path(); p.addEllipse(r); this._path = p; this.fillPath(); }
    stroke(r) { this.strokeRect(r); }
    strokeRect(r) { const x = this.ctx; x.strokeStyle = this._stroke; x.lineWidth = this._lw; x.strokeRect(r.x, r.y, r.width, r.height); }
    strokeEllipse(r) { const p = new Path(); p.addEllipse(r); this._path = p; this.strokePath(); }
    addPath(p) { this._path = p; }
    fillPath() { if (!this._path) return; const x = this.ctx; tracePath(x, this._path); x.fillStyle = this._fill; x.fill(); }
    strokePath() { if (!this._path) return; const x = this.ctx; tracePath(x, this._path); x.strokeStyle = this._stroke; x.lineWidth = this._lw; x.stroke(); }
    drawText(t, p) { const x = this.ctx; x.font = this._font._css; x.fillStyle = this._text; x.textBaseline = "top"; x.textAlign = "left"; x.fillText(String(t), p.x, p.y); }
    drawTextInRect(t, r) {
      const x = this.ctx; x.font = this._font._css; x.fillStyle = this._text; x.textBaseline = "top";
      x.textAlign = this._align;
      const px = this._align === "center" ? r.x + r.width / 2 : this._align === "right" ? r.x + r.width : r.x;
      x.fillText(String(t), px, r.y, r.width);
    }
    drawImageInRect(img, r) {
      if (!img || !img._src) return;
      const el = new window.Image(); el.src = img._src;
      try { this.ctx.drawImage(el, r.x, r.y, r.width, r.height); } catch (e) {}
    }
    drawImageAtPoint(img, p) { if (img) this.drawImageInRect(img, new Rect(p.x, p.y, img.size.width, img.size.height)); }
    getImage() { this.ctx; return new Image(this._canvas.toDataURL("image/png"), this.size.width, this.size.height); }
  }

  // --------------------------------------------------------------- widget stubs
  // The scripts only build widgets when config.runsInWidget is true, which is
  // never the case here. These exist so a stray reference cannot throw.
  function stub() {
    const fn = function () {};
    return new Proxy(fn, {
      get: (t, k) => (k === "then" ? undefined : k === Symbol.toPrimitive ? () => "" : stub()),
      set: () => true,
      apply: () => stub(),
      construct: () => stub(),
    });
  }
  const ListWidget = function () { return stub(); };
  const LinearGradient = function () { return stub(); };
  const WidgetStack = function () { return stub(); };

  // -------------------------------------------------------------------- Timer
  class Timer {
    constructor() { this.timeInterval = 0; this.repeats = false; this._h = null; }
    schedule(fn) {
      this.invalidate();
      const run = () => { try { const r = fn(this); if (r && r.catch) r.catch(reportError); } catch (e) { reportError(e); } };
      this._h = this.repeats ? setInterval(run, this.timeInterval) : setTimeout(run, this.timeInterval);
      this._rep = this.repeats;
    }
    invalidate() {
      if (this._h != null) { (this._rep ? clearInterval : clearTimeout)(this._h); this._h = null; }
    }
    static schedule(ms, repeats, fn) {
      const t = new Timer(); t.timeInterval = Number(ms) || 0; t.repeats = !!repeats; t.schedule(fn); return t;
    }
  }

  // ------------------------------------------------------------------ Request
  class Request {
    constructor(url) {
      this.url = url; this.method = "GET"; this.headers = {}; this.body = null;
      this.timeoutInterval = 60; this.allowInsecureRequest = false; this.response = null;
      this.onRedirect = null;
    }
    async _go() {
      const body = this.body == null ? null
        : typeof this.body === "string" ? this.body
        : this.body instanceof Data ? this.body._s
        : JSON.stringify(this.body);
      const timeout = Number(this.timeoutInterval) || 60;
      const ctl = new AbortController();
      const h = setTimeout(() => ctl.abort(), (timeout + 5) * 1000);
      let r;
      try {
        r = await nativeFetch("/api/proxy", {
          method: "POST", signal: ctl.signal,
          headers: { "X-LP-Token": TOKEN, "Content-Type": "application/json" },
          body: JSON.stringify({ url: String(this.url), method: this.method, headers: this.headers || {}, body, timeout }),
        });
      } catch (e) {
        throw new Error(ctl.signal.aborted ? "The request timed out." : "Could not connect to the local server. Is server.py still running?");
      } finally { clearTimeout(h); }
      const j = await r.json().catch(() => ({ error: "bad response from local server" }));
      if (j.error) throw new Error(j.error);
      const ct = (j.headers && (j.headers["Content-Type"] || j.headers["content-type"])) || "";
      this.response = { statusCode: j.status, url: j.url, headers: j.headers || {}, mimeType: ct.split(";")[0], cookies: [] };
      return j.body;
    }
    async loadString() { return this._go(); }
    async loadJSON() {
      const s = await this._go();
      try { return JSON.parse(s); }
      catch (e) { throw new Error(`The data couldn't be read because it isn't in the correct format. (${String(s).slice(0, 80)})`); }
    }
    async load() { return new Data(await this._go()); }
    async loadImage() { return null; }
    addParameterToMultipart() {}
  }

  // -------------------------------------------------------------- FileManager
  // Paths are virtual: /icloud/... is the Scriptable iCloud folder on this Mac,
  // /local/... is lp_web/local/files. Reads are cached in memory; writes update
  // the cache at once and are sent to the server in order.
  const fsCache = new Map();   // path -> { exists, dir, content }
  let writeChain = Promise.resolve();
  function fsRead(p) {
    if (fsCache.has(p)) return fsCache.get(p);
    let r;
    try { r = apiSync("/api/fs?" + qs({ op: "read", path: p })); }
    catch (e) { r = { exists: false, content: null }; }
    fsCache.set(p, r);
    return r;
  }
  function fsWrite(op, payload) {
    writeChain = writeChain.then(() => apiFetch("/api/fs", { op, ...payload })).catch((e) => sConsole.warn("file write failed:", e.message));
    return writeChain;
  }
  const norm = (p) => "/" + String(p).split("/").filter(Boolean).join("/");
  class FileManager {
    constructor(kind) { this._kind = kind; }
    static local() { return new FileManager("local"); }
    static iCloud() { return new FileManager("icloud"); }
    documentsDirectory() { return this._kind === "icloud" ? "/icloud" : "/local/Documents"; }
    libraryDirectory() { return "/local/Library"; }
    cacheDirectory() { return "/local/Caches"; }
    temporaryDirectory() { return "/local/tmp"; }
    joinPath(a, b) { return norm(String(a) + "/" + String(b)); }
    fileExists(p) { return !!fsRead(norm(p)).exists; }
    isDirectory(p) { return !!fsRead(norm(p)).dir; }
    readString(p) { const r = fsRead(norm(p)); return r.content == null ? null : r.content; }
    read(p) { const s = this.readString(p); return s == null ? null : new Data(s); }
    readImage() { return null; }
    writeString(p, s) {
      p = norm(p); const content = String(s);
      fsCache.set(p, { exists: true, dir: false, content, mtime: Date.now() });
      fsWrite("write", { path: p, content });
    }
    write(p, d) { this.writeString(p, d instanceof Data ? d._s : String(d)); }
    writeImage() {}
    remove(p) { p = norm(p); fsCache.set(p, { exists: false, content: null }); fsWrite("remove", { path: p }); }
    createDirectory(p) { p = norm(p); fsCache.set(p, { exists: true, dir: true, content: null }); fsWrite("mkdir", { path: p }); }
    copy(a, b) { a = norm(a); b = norm(b); const r = fsRead(a); if (!r.exists) throw new Error("No such file: " + a); if (r.content != null) fsCache.set(b, { ...r }); else fsCache.delete(b); fsWrite("copy", { src: a, dst: b }); }
    move(a, b) { a = norm(a); b = norm(b); const r = fsRead(a); if (!r.exists) throw new Error("No such file: " + a); fsCache.set(b, { ...r }); fsCache.set(a, { exists: false, content: null }); fsWrite("move", { src: a, dst: b }); }
    listContents(p) {
      p = norm(p);
      let names = [];
      try { names = apiSync("/api/fs?" + qs({ op: "list", path: p })).names || []; } catch (e) {}
      const set = new Set(names);
      for (const [k, v] of fsCache) {
        if (k.startsWith(p + "/") && !k.slice(p.length + 1).includes("/")) {
          const n = k.slice(p.length + 1);
          if (v.exists) set.add(n); else set.delete(n);
        }
      }
      return [...set].sort();
    }
    fileName(p, ext) { const n = String(p).split("/").pop(); return ext ? n : n.replace(/\.[^.]*$/, ""); }
    fileExtension(p) { const m = String(p).match(/\.([^./]+)$/); return m ? m[1] : ""; }
    modificationDate(p) { const r = fsRead(norm(p)); return r.exists ? new Date(r.mtime || Date.now()) : null; }
    creationDate(p) { return this.modificationDate(p); }
    fileSize(p) { const r = fsRead(norm(p)); return r.content == null ? 0 : r.content.length / 1000; }
    isFileStoredIniCloud(p) { return norm(p).startsWith("/icloud"); }
    isFileDownloaded() { return true; }
    downloadFileFromiCloud() { return Promise.resolve(); }
    evictFromiCloud() {}
    getUTI() { return "public.data"; }
    allTags() { return []; } addTag() {} removeTag() {}
    bookmarkedPath() { throw new Error("Bookmarks are not available on the web."); }
    bookmarkExists() { return false; }
    allFileBookmarks() { return []; }
  }

  // ------------------------------------------------------------------- Keychain
  const kcKey = (k) => "lpweb.keychain." + k;
  const Keychain = {
    contains: (k) => { try { return localStorage.getItem(kcKey(k)) != null; } catch (e) { return false; } },
    get: (k) => { let v = null; try { v = localStorage.getItem(kcKey(k)); } catch (e) {} if (v == null) throw new Error("No item in keychain: " + k); return v; },
    set: (k, v) => { try { localStorage.setItem(kcKey(k), String(v)); } catch (e) {} },
    remove: (k) => { try { localStorage.removeItem(kcKey(k)); } catch (e) {} },
  };

  // --------------------------------------------------------------------- Alert
  let alertChain = Promise.resolve();
  class Alert {
    constructor() { this.title = ""; this.message = ""; this._actions = []; this._fields = []; this._values = []; }
    addAction(t) { this._actions.push({ title: String(t), kind: "normal" }); }
    addDestructiveAction(t) { this._actions.push({ title: String(t), kind: "destructive" }); }
    addCancelAction(t) { this._cancel = String(t); }
    addTextField(placeholder, text) { this._fields.push({ placeholder: placeholder || "", text: text || "", secure: false }); }
    addSecureTextField(placeholder, text) { this._fields.push({ placeholder: placeholder || "", text: text || "", secure: true }); }
    textFieldValue(i) { return this._values[i] != null ? this._values[i] : (this._fields[i] ? this._fields[i].text : ""); }
    setTextFieldFont() {} setTextFieldTextColor() {}
    setTextFieldLeftAligned() {} setTextFieldCenterAligned() {} setTextFieldRightAligned() {}
    setTextFieldKeyboardType() {} setTextFieldAutocorrectionType() {}
    presentAlert() { return this._present(false); }
    presentSheet() { return this._present(true); }
    _present(sheet) {
      const p = alertChain.then(() => new Promise((resolve) => this._show(sheet, resolve)));
      alertChain = p.catch(() => {});
      return p;
    }
    _show(sheet, resolve) {
      const overlay = document.createElement("div");
      overlay.className = "lp-alert-overlay";
      const box = document.createElement("div");
      box.className = "lp-alert" + (sheet ? " lp-sheet" : "");
      box.setAttribute("role", "dialog");
      box.setAttribute("aria-modal", "true");
      if (this.title) { const h = document.createElement("div"); h.className = "lp-alert-title"; h.textContent = this.title; box.appendChild(h); }
      if (this.message) { const m = document.createElement("div"); m.className = "lp-alert-msg"; m.textContent = this.message; box.appendChild(m); }
      const inputs = this._fields.map((f) => {
        // iOS hides a field's placeholder once it has a value; on a big screen
        // a label above each field is clearer, so show it there too.
        if (f.placeholder && this._fields.length > 1) {
          const l = document.createElement("div");
          l.className = "lp-alert-label"; l.textContent = f.placeholder;
          box.appendChild(l);
        }
        const i = document.createElement("input");
        i.type = f.secure ? "password" : "text"; i.placeholder = f.placeholder; i.value = f.text;
        i.className = "lp-alert-input"; i.autocomplete = "off"; i.spellcheck = false;
        box.appendChild(i);
        return i;
      });
      const btns = document.createElement("div");
      const total = this._actions.length + (this._cancel != null ? 1 : 0);
      btns.className = "lp-alert-btns" + (total === 2 && !sheet ? " lp-two" : "");
      const done = (idx) => {
        this._values = inputs.map((i) => i.value);
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        resolve(idx);
      };
      const mk = (title, kind, idx) => {
        const b = document.createElement("button");
        b.type = "button"; b.textContent = title; b.className = "lp-alert-btn lp-" + kind;
        b.onclick = () => done(idx);
        return b;
      };
      const cancelBtn = this._cancel != null ? mk(this._cancel, "cancel", -1) : null;
      if (cancelBtn && total === 2 && !sheet) btns.appendChild(cancelBtn);
      this._actions.forEach((a, i) => btns.appendChild(mk(a.title, a.kind, i)));
      if (cancelBtn && !(total === 2 && !sheet)) btns.appendChild(cancelBtn);
      box.appendChild(btns);
      overlay.appendChild(box);
      document.body.appendChild(overlay);
      // Enter only confirms a plain action. A destructive one (a real send)
      // always needs a click.
      const onKey = (e) => {
        if (e.key === "Escape" && this._cancel != null) { e.preventDefault(); done(-1); }
        else if (e.key === "Enter" && this._actions.length && this._actions[0].kind === "normal") { e.preventDefault(); done(0); }
      };
      document.addEventListener("keydown", onKey, true);
      setTimeout(() => (inputs[0] || btns.querySelector(".lp-normal, .lp-cancel") || box).focus(), 0);
    }
  }

  // ------------------------------------------------------------------- UITable
  const ALIGN = { left: "flex-start", center: "center", right: "flex-end" };
  class UITableCell {
    constructor(type) {
      this._type = type; this.widthWeight = 100; this.align = "left";
      this.title = ""; this.subtitle = ""; this.titleColor = null; this.subtitleColor = null;
      this.titleFont = null; this.subtitleFont = null; this.onTap = null; this.dismissOnTap = false;
      this.image = null; this.imageURL = null;
    }
    static text(t, s) { const c = new UITableCell("text"); c.title = t == null ? "" : String(t); c.subtitle = s == null ? "" : String(s); return c; }
    static button(t) { const c = new UITableCell("button"); c.title = t == null ? "" : String(t); return c; }
    static image(img) { const c = new UITableCell("image"); c.image = img; return c; }
    static imageAtURL(u) { const c = new UITableCell("image"); c.imageURL = u; return c; }
    leftAligned() { this.align = "left"; }
    centerAligned() { this.align = "center"; }
    rightAligned() { this.align = "right"; }
  }
  class UITableRow {
    constructor() {
      this._cells = []; this.height = 44; this.cellSpacing = 0; this.backgroundColor = null;
      this.isHeader = false; this.dismissOnSelect = true; this.onSelect = null;
    }
    addCell(c) { this._cells.push(c); }
    addText(t, s) { const c = UITableCell.text(t, s); this._cells.push(c); return c; }
    addButton(t) { const c = UITableCell.button(t); this._cells.push(c); return c; }
    addImage(img) { const c = UITableCell.image(img); this._cells.push(c); return c; }
    addImageAtURL(u) { const c = UITableCell.imageAtURL(u); this._cells.push(c); return c; }
  }
  class UITable {
    constructor() { this._rows = []; this.showSeparators = false; this._el = null; }
    addRow(r) { this._rows.push(r); }
    removeRow(r) { this._rows = this._rows.filter((x) => x !== r); }
    removeAllRows() { this._rows = []; }
    reload() { if (this._el) renderTable(this); }
    present() {
      const host = document.getElementById("lp-app");
      if (!this._el) {
        this._el = document.createElement("div");
        this._el.className = "lp-table";
        host.innerHTML = "";
        host.appendChild(this._el);
      }
      renderTable(this);
      // A Scriptable table resolves when it is dismissed; this page never closes it.
      return new Promise(() => {});
    }
  }
  function cellNode(c) {
    const el = document.createElement("div");
    el.className = "lp-cell lp-cell-" + c._type;
    const w = Number(c.widthWeight);
    el.style.flex = `${w > 0 ? w : 0} 1 0`;
    el.style.alignItems = ALIGN[c.align] || ALIGN.left;
    el.style.textAlign = c.align;
    if (c._type === "image") {
      const src = c.image ? c.image._src : c.imageURL;
      if (src) {
        const img = document.createElement("img");
        img.src = src;
        if (c.image) { img.style.width = c.image.size.width + "px"; img.style.height = c.image.size.height + "px"; }
        img.style.maxWidth = "100%";
        el.appendChild(img);
      }
      return el;
    }
    if (c._type === "button") {
      const b = document.createElement("button");
      b.type = "button"; b.className = "lp-btn"; b.textContent = c.title;
      if (c.titleFont) b.style.font = c.titleFont._css;
      if (c.titleColor) b.style.color = c.titleColor._css;
      b.disabled = !c.onTap;
      b.onclick = (e) => { e.stopPropagation(); if (c.onTap) guard(() => c.onTap()); };
      el.appendChild(b);
      return el;
    }
    const t = document.createElement("div");
    t.className = "lp-title"; t.textContent = c.title;
    if (c.titleFont) t.style.font = c.titleFont._css;
    if (c.titleColor) t.style.color = c.titleColor._css;
    el.appendChild(t);
    if (c.subtitle) {
      const s = document.createElement("div");
      s.className = "lp-subtitle"; s.textContent = c.subtitle;
      if (c.subtitleFont) s.style.font = c.subtitleFont._css;
      if (c.subtitleColor) s.style.color = c.subtitleColor._css;
      el.appendChild(s);
    }
    return el;
  }
  function renderTable(t) {
    const frag = document.createDocumentFragment();
    t._rows.forEach((r, i) => {
      const row = document.createElement("div");
      row.className = "lp-row" + (r.isHeader ? " lp-header" : "") + (t.showSeparators ? " lp-sep" : "") + (r.onSelect ? " lp-selectable" : "");
      const h = Number(r.height) || 44;
      row.style.minHeight = h + "px";
      if (h <= 2) { row.style.height = h + "px"; row.style.minHeight = "0"; row.style.padding = "0"; }
      if (r.cellSpacing) row.style.gap = r.cellSpacing + "px";
      if (r.backgroundColor) row.style.background = r.backgroundColor._css;
      for (const c of r._cells) row.appendChild(cellNode(c));
      if (r.onSelect) row.onclick = () => guard(() => r.onSelect(i));
      frag.appendChild(row);
    });
    const y = window.scrollY;
    t._el.replaceChildren(frag);
    window.scrollTo(0, y);
  }

  function reportError(e) {
    sConsole.error(e);
    showToast(String((e && e.message) || e));
  }
  function guard(fn) {
    try { const r = fn(); if (r && typeof r.catch === "function") r.catch(reportError); }
    catch (e) { reportError(e); }
  }
  function showToast(text) {
    const t = document.createElement("div");
    t.className = "lp-toast"; t.textContent = text;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 8000);
  }

  // --------------------------------------------------- Safari, Device, others
  function scriptableRunUrl(url) {
    // scriptable:///run/<name>?query  ->  /run/<name>?query on this server
    const m = String(url).match(/^scriptable:\/\/\/?run\/([^?]*)(\?.*)?$/i);
    if (!m) return null;
    let name = m[1];
    try { name = decodeURIComponent(name); } catch (e) {}
    return "/run/" + encodeURIComponent(name) + (m[2] || "");
  }
  const Safari = {
    open(url) {
      const local = scriptableRunUrl(url);
      if (local) { window.location.href = local; return; }
      if (/^scriptable:/i.test(url)) { showToast("This link only works in the Scriptable app."); return; }
      window.open(url, "_blank", "noopener");
    },
    openInApp(url) { Safari.open(url); return Promise.resolve(); },
  };
  const Device = {
    screenSize: () => new Size(430, 932),
    screenResolution: () => new Size(1290, 2796),
    screenScale: () => window.devicePixelRatio || 2,
    screenBrightness: () => 1,
    isInPortrait: () => true, isInLandscapeLeft: () => false, isInLandscapeRight: () => false,
    isInPortraitUpsideDown: () => false, isFaceUp: () => false, isFaceDown: () => false,
    isUsingDarkAppearance: () => true,
    isPhone: () => false, isPad: () => false,
    name: () => "Mac", model: () => "Mac", systemName: () => "macOS", systemVersion: () => "",
    batteryLevel: () => 1, isDischarging: () => false, isCharging: () => false, isFullyCharged: () => true,
    preferredLanguages: () => navigator.languages || ["en"], locale: () => navigator.language || "en_US",
    language: () => (navigator.language || "en").slice(0, 2),
    volume: () => 1, setScreenBrightness() {},
  };
  const Pasteboard = {
    copy: (s) => { try { navigator.clipboard.writeText(String(s)); } catch (e) {} },
    copyString: (s) => Pasteboard.copy(s),
    paste: () => "", pasteString: () => "",
    copyImage() {}, pasteImage: () => null,
  };
  class Notification {
    constructor() { this.title = ""; this.body = ""; this.subtitle = ""; }
    async schedule() {
      showToast([this.title, this.body].filter(Boolean).join(": "));
      try {
        if ("Notification" in window && window.Notification.permission === "granted")
          new window.Notification(this.title || "LP", { body: this.body });
      } catch (e) {}
    }
    remove() {} setTriggerDate() {} addAction() {}
    static current() { return null; } static async allPending() { return []; } static async allDelivered() { return []; }
    static removeAllPending() {} static removeAllDelivered() {} static removePending() {} static removeDelivered() {} static resetCurrent() {}
  }
  const QuickLook = { present: async (x) => { const a = new Alert(); a.message = typeof x === "string" ? x : JSON.stringify(x, null, 2); a.addAction("OK"); await a.presentAlert(); } };
  const UUID = { string: () => (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2)).toUpperCase() };
  const CallbackURL = function () { return stub(); };
  const WebView = { loadURL: async (u) => Safari.open(u), loadHTML: async () => {} };

  // ------------------------------------------------------------- run a script
  const moduleCache = new Map();
  let currentName = "";
  const API_NAMES = ["Request", "Timer", "Alert", "UITable", "UITableRow", "UITableCell", "FileManager",
    "Color", "Font", "Size", "Rect", "Point", "Path", "DrawContext", "Image", "Data", "Safari", "Script",
    "config", "args", "Device", "Pasteboard", "Notification", "ListWidget", "LinearGradient", "WidgetStack",
    "Keychain", "QuickLook", "UUID", "CallbackURL", "WebView", "importModule", "console", "module", "exports"];

  function makeScope(name, query) {
    const Script = {
      name: () => name,
      complete: () => {},
      setWidget: () => {},
      setShortcutOutput: () => {},
    };
    const config = {
      runsInApp: true, runsInWidget: false, runsInActionExtension: false, runsWithSiri: false,
      runsInNotification: false, runsFromHomeScreen: false, widgetFamily: null,
    };
    const args = {
      queryParameters: query, widgetParameter: null, plainTexts: [], urls: [], fileURLs: [],
      images: [], notification: null, shortcutParameter: null, siriShortcutArguments: {}, all: [],
    };
    const module = { filename: name + ".js", exports: {} };
    const scope = {
      Request, Timer, Alert, UITable, UITableRow, UITableCell, FileManager, Color, Font, Size, Rect,
      Point, Path, DrawContext, Image, Data, Safari, Script, config, args, Device, Pasteboard,
      Notification, ListWidget, LinearGradient, WidgetStack, Keychain, QuickLook, UUID, CallbackURL,
      WebView, console: sConsole, module, exports: module.exports,
    };
    scope.importModule = (n) => importModule(n, scope);
    return scope;
  }
  function compile(src, name) {
    // The body sits in an inner function so a script may declare a name that
    // is also an API name (both RH scripts vendor their own `class Point`).
    return new Function(...API_NAMES,
      `return (async () => {\n${src}\n})();\n//# sourceURL=${encodeURIComponent(name)}.js`);
  }
  function importModule(n, parentScope) {
    n = String(n).replace(/\.js$/i, "").split("/").pop();
    if (moduleCache.has(n.toLowerCase())) return moduleCache.get(n.toLowerCase());
    const j = apiSync("/api/script?" + qs({ name: n }));
    const scope = { ...parentScope, module: { filename: j.name + ".js", exports: {} } };
    scope.exports = scope.module.exports;
    // A module runs synchronously in Scriptable, so only its synchronous part
    // counts; a top-level await in one would not have worked there either.
    const fn = new Function(...API_NAMES, `${j.source}\n//# sourceURL=${encodeURIComponent(j.name)}.js`);
    fn(...API_NAMES.map((k) => scope[k]));
    moduleCache.set(n.toLowerCase(), scope.module.exports);
    return scope.module.exports;
  }

  async function runScript(name, query) {
    currentName = name;
    const j = await apiFetch("/api/script?" + qs({ name }));
    document.title = j.name;
    const scope = makeScope(j.name, query);
    const fn = compile(j.source, j.name);
    return fn(...API_NAMES.map((k) => scope[k]));
  }

  window.LPWeb = { runScript, apiFetch, showToast, reportError, logLines };
})();
