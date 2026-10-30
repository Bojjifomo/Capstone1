// The guild chat under the map: ledger entries, status changes and town chatter, MMORPG style.
"use strict";

const ChatLog = (() => {
  const MAX = 150;
  const lines = [];
  const seen = new Set();
  const last = new Map();   // sid -> status, to announce changes
  let channel = "all";
  let polls = 0;
  let primed = false;

  const hhmm = (ts) => {
    const d = new Date(ts * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  };

  function lineHTML(l) {
    return `<div class="chat-line c-${l.ch}"${l.sid ? ` data-sid="${esc(l.sid)}" role="button" tabindex="0"` : ""}>` +
      `<time>[${hhmm(l.ts)}]</time> <span class="who">[${esc(l.who)}]</span> <span class="msg">${esc(l.text)}</span></div>`;
  }

  function render() {
    const box = $("#chat-lines");
    if (!box) return;
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    // Raw token-call audits stay on the External tab. All still shows what they said and did.
    const shown = lines.filter((l) => channel === "all" ? l.ch !== "external" : (l.ch === channel || l.also === channel));
    box.innerHTML = shown.length ? shown.map(lineHTML).join("") : '<div class="chat-line c-system"><span class="msg">The hall is quiet.</span></div>';
    if (near || !primed) box.scrollTop = box.scrollHeight;
  }

  function add(ch, who, text, sid, ts, also, opts) {
    if (!text) return;
    ts = ts || Date.now() / 1000;
    const key = ch + "|" + (also || "") + "|" + Math.round(ts) + "|" + who + "|" + text;
    if (seen.has(key)) return;
    seen.add(key);
    let i = lines.length;
    while (i > 0 && lines[i - 1].ts > ts) i--;
    lines.splice(i, 0, { ch, also: also || "", who: String(who || "Guild"), text: String(text).slice(0, 300), sid: sid || null, ts });
    while (lines.length > MAX) lines.shift();
    render();
    if (!(opts && opts.local) && typeof Town !== "undefined" && Town.speak) {
      try { Town.speak({ ch, who: String(who || "Guild"), text: String(text).slice(0, 140), sid: sid || null }); } catch (e) { /* the map can live without a bubble */ }
    }
  }

  let names = [];
  let namesAt = 0;
  let waitingUntil = 0;   // after an order, read the ledger on every poll so the answer shows up quickly

  const LEDGER_WHO = { send: "You", queue: "You", job: "You", seal: "Seal", hire: "Guild", dismiss: "Guild", errand: "Errand", bot: "Familiar", clean: "Vault", guild: "Guild Hall", chatter: "Guild Hall", external: "External" };
  const LEDGER_VERB = { queue: "queued: ", job: "posted a quest: ", hire: "welcomed ", dismiss: "dismissed ", errand: "sent ", bot: "", clean: "" };

  function ingestLedger(e) {
    if (e.kind === "external") {
      add("external", e.origin || "External", e.text, e.sid, e.ts);
      return;
    }
    const who = e.by || e.origin || LEDGER_WHO[e.kind] || e.kind;
    const text = (LEDGER_VERB[e.kind] || "") + e.text;
    if (e.kind === "order" || e.kind === "reply") {
      add("orders", who, e.text, e.sid, e.ts);
      return;
    }
    const ch = e.kind === "chatter" ? "say" : "guild";
    add(ch, who, text, e.sid, e.ts, e.origin ? "external" : "");
  }

  async function loadLedger() {
    try {
      const { entries } = await api("/api/ledger");
      entries.slice().reverse().forEach(ingestLedger);
    } catch (e) { /* the chat is a nice-to-have */ }
  }

  function nameOf(s) { return s.persona ? s.persona.name.split(" ")[0] : s.name; }

  function onState(state) {
    (state.sessions || []).forEach((s) => {
      const was = last.get(s.id);
      last.set(s.id, s.status);
      if (!primed || was === s.status) return;
      const who = nameOf(s);
      if (s.status === "needs") add("system", who, "awaits your word" + (s.note ? ": " + s.note : "."), s.id);
      else if (s.status === "working" && was) add("system", who, "set to work" + (s.note && s.note !== "thinking" ? ": " + s.note : "."), s.id);
      else if (s.status === "finished") add("system", who, "returned from their quest.", s.id);
      else if (s.status === "working") add("system", who, "joined the guild and set out.", s.id);
    });
    if (!primed) {
      const waiting = (state.sessions || []).filter((s) => s.status === "needs");
      const busy = (state.sessions || []).filter((s) => s.status === "working");
      add("system", "Guild", `Welcome back, bojjifomo. ${busy.length} on a quest, ${waiting.length} awaiting your word.`);
      waiting.forEach((s) => add("system", nameOf(s), "awaits your word" + (s.note ? ": " + s.note : "."), s.id));
      primed = true;
      loadLedger();
    } else if (++polls % 4 === 0 || Date.now() < waitingUntil) {
      loadLedger();
    }
  }

  document.addEventListener("click", (ev) => {
    const tab = ev.target.closest(".chat-tabs [data-ch]");
    if (!tab) return;
    channel = tab.dataset.ch;
    document.querySelectorAll(".chat-tabs [data-ch]").forEach((b) => b.classList.toggle("on", b === tab));
    const box = $("#chat-lines");
    render();
    if (box) box.scrollTop = box.scrollHeight;
  });
  document.addEventListener("keydown", (ev) => {
    const line = ev.target.closest && ev.target.closest(".chat-line[data-sid]");
    if (line && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); openSheet(line.dataset.sid); }
  });

  // ---- giving orders from the chat ----------------------------------------------------
  async function loadNames(force) {
    if (!force && Date.now() - namesAt < 30000) return names;
    try { names = (await api("/api/chat/names")).names; namesAt = Date.now(); } catch (e) { /* suggestions are optional */ }
    return names;
  }

  async function suggest(input) {
    const box = $("#chat-names");
    const m = input.value.match(/^@([^\s]*)$/);
    if (!m) { box.hidden = true; return; }
    const q = m[1].toLowerCase();
    const list = (await loadNames()).filter((n) => n.name.toLowerCase().startsWith(q)).slice(0, 8);
    box.hidden = !list.length;
    box.innerHTML = list.map((n) =>
      `<button type="button" class="chip-btn${n.needs ? " needs" : ""}" data-chat-name="${esc(n.name)}">@${esc(n.name)}` +
      `<small>${esc(n.kind === "staff" ? n.title : (n.needs ? "awaits you" : "quest"))}</small></button>`).join("");
  }

  document.addEventListener("input", (ev) => {
    if (ev.target.closest && ev.target.closest("#chat-form")) suggest(ev.target);
  });
  document.addEventListener("focusin", (ev) => {
    if (ev.target.closest && ev.target.closest("#chat-form")) {
      document.body.classList.add("chatting");   // the Post-a-quest button would cover the input
      loadNames(true);
      suggest(ev.target);
    }
  });
  document.addEventListener("focusout", (ev) => {
    if (ev.target.closest && ev.target.closest("#chat-form")) setTimeout(() => {
      if (!document.activeElement || !document.activeElement.closest("#chat-form")) document.body.classList.remove("chatting");
    }, 200);
  });
  document.addEventListener("click", (ev) => {
    const pick = ev.target.closest("[data-chat-name]");
    if (!pick) return;
    const input = $("#chat-form input");
    input.value = "@" + pick.dataset.chatName + " ";
    $("#chat-names").hidden = true;
    input.focus();
  });
  document.addEventListener("submit", async (ev) => {
    if (ev.target.id !== "chat-form") return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const input = ev.target.text;
    const btn = ev.target.querySelector("[type=submit]");
    const text = input.value.trim();
    if (!text || btn.disabled) return;
    btn.disabled = true;
    try {
      const r = await api("/api/chat", { text });
      input.value = "";
      $("#chat-names").hidden = true;
      if (r.reply) {
        add("orders", "bojjifomo", text, null, null, "", { local: true });
        add("orders", r.by || "Guild", r.reply, null, null, "", { local: true });
      } else {
        await loadLedger();   // your order, as the guild wrote it down
        waitingUntil = Date.now() + 10 * 60 * 1000;
        add("orders", "Guild", r.held ? `${r.to} will start when the treasury refills.`
          : `${r.to} ${r.queued ? "is busy; your message is queued" : "is on it"}. The answer lands here.`, r.sid, null, "", { local: true });
      }
    } catch (e) {
      add("orders", "bojjifomo", text, null, null, "", { local: true });
      add("orders", "Guild", e.message, null, null, "", { local: true });
    } finally {
      btn.disabled = false;
    }
  }, true);

  Hooks.header.push(onState);
  render();
  if (UI.state) onState(UI.state);   // the first poll can land before this script runs
  return { add };
})();
