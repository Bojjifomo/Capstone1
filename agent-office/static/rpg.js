// Ranks, the character sheet, the quest board badges.
"use strict";

function pips(into, span) {
  const n = 10;
  const filled = span ? Math.round(n * (into || 0) / span) : n;
  let html = '<span class="pips" aria-hidden="true">';
  for (let i = 0; i < n; i++) html += `<i${i < filled ? ' class="on"' : ""}></i>`;
  return html + "</span>";
}

function progressInner(p) {
  if (!p) return "";
  const next = p.next_rank ? `${Math.max(0, (p.next_at || 0) - p.xp)} XP to ${p.next_rank}` : "Master of the hall";
  const stat = (k, v) => `<div><b>${v == null ? "—" : v}</b><small>${k}</small></div>`;
  const medals = (p.badges || []).map((b) => `<span class="medal">${esc(b.label)}</span>`).join("");
  const history = (p.history || []).map((h) =>
    `<li><span class="rarity-${esc(h.rarity || "Common")}">${esc(h.rarity || "")}</span> ${esc(h.title || "")}` +
    `${h.ok ? ` · +${h.xp} XP` : " · fell short"}</li>`).join("");
  return `<h3>Level ${p.level} · ${esc(p.rank)}</h3>` +
    pips(p.into, p.span) +
    `<p class="sub">${p.xp} XP · ${esc(next)}</p>` +
    `<div class="xp-stats">${stat("Success", p.success == null ? null : p.success + "%")}` +
    `${stat("Thrift", p.efficiency == null ? null : p.efficiency)}${stat("Speed", p.speed)}</div>` +
    (medals ? `<div class="medals">${medals}</div>` : "") +
    (history ? `<ul class="xp-history">${history}</ul>` : `<p class="sub">No quests written down yet.</p>`);
}

function toastUnlocks(state) {
  const fresh = state.unlocks || [];
  if (!fresh.length) return;
  let seen = [];
  try { seen = JSON.parse(localStorage.getItem("guild-badges") || "[]"); } catch (e) { seen = []; }
  let changed = false;
  fresh.forEach((u) => {
    const id = (u.key || "") + ":" + u.badge;
    if (seen.indexOf(id) >= 0) return;
    seen.push(id);
    changed = true;
    toast(`${u.name} unlocked ${u.label}`);
  });
  if (changed) {
    try { localStorage.setItem("guild-badges", JSON.stringify(seen.slice(-80))); } catch (e) { /* private mode */ }
  }
}

Hooks.header.push(toastUnlocks);

if (Hooks.sheetTop) {
  const baseTop = Hooks.sheetTop;
  Hooks.sheetTop = (d) => baseTop(d) + (d.progress ? `<section class="xp-card">${progressInner(d.progress)}</section>` : "");
}

if (Hooks.panels.staff) {
  const baseStaff = Hooks.panels.staff.render;
  Hooks.panels.staff.render = function (body) {
    const result = baseStaff.apply(this, arguments);
    const m = memberById(UI.arg);
    if (m && m.progress) {
      const card = document.createElement("section");
      card.className = "panel-sec xp-card";
      card.innerHTML = progressInner(m.progress);
      const profile = body.querySelector(".profile-card");
      if (profile) profile.after(card);
      else body.prepend(card);
    }
    return result;
  };
}
