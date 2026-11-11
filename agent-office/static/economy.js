// The spent-budget banner, the raid banner, and the Budget section of the usage sheet.
"use strict";

function goldHTML(g) {
  const width = g.max ? Math.max(0, Math.min(100, (g.gold / g.max) * 100)) : 0;
  const queue = (g.queue || []).map((item) =>
    `<div class="row"><span class="k">${esc(item.label || item.kind)}</span>` +
    `<button type="button" class="btn" data-panel-act="release-gold" data-qid="${esc(item.id)}">Let it through</button></div>`).join("");
  return `<h3>Daily budget</h3>` +
    `<p><b>${goldLabel(g.gold)}</b> / ${goldLabel(g.max)}` +
    (g.metered ? ` · budget ${money(g.budget)} · spent ${money(g.spent)}` : " · not rationed (set daily_budget_usd)") +
    `</p><span class="bar gold"><i data-w="${width.toFixed(1)}"></i></span>` +
    (g.paused ? `<p class="bot-error">The daily budget is spent. New sessions wait here until tomorrow, or until you let one through.</p>` : "") +
    (queue ? `<div class="rows">${queue}</div>` : "");
}

function renderGold(state) {
  const banner = $("#gold-banner");
  const g = state.gold;
  if (banner) {
    if (g && g.paused) {
      banner.hidden = false;
      const n = (g.queue || []).length;
      banner.textContent = n ? `The daily budget is spent. ${n} session${n === 1 ? "" : "s"} waiting.` : "The daily budget is spent. New sessions will wait.";
    } else if (banner) banner.hidden = true;
  }
  const raidEl = $("#raid");
  const raid = state.raid;
  if (!raidEl) return;
  const fresh = raid && (raid.status === "fighting" || (raid.ended && Date.now() / 1000 - raid.ended < 90));
  if (!fresh) { raidEl.hidden = true; return; }
  raidEl.hidden = false;
  raidEl.classList.toggle("victory", raid.status === "victory");
  raidEl.classList.toggle("fled", raid.status === "fled");
  const pct = raid.max ? Math.max(0, Math.min(100, (raid.hp / raid.max) * 100)) : 0;
  const party = (raid.party || []).map((name) => `<span class="attacker">${esc(name)}</span>`).join("");
  raidEl.innerHTML = `<div class="boss"><b>${esc(raid.title)}</b><span>${raid.hp}/${raid.max}</span></div>` +
    `<span class="hp"><i data-w="${pct.toFixed(1)}"></i></span>` +
    (party ? `<div class="party">${party}</div>` : `<p class="sub">The party is forming.</p>`) +
    (raid.status === "victory" ? `<p class="loot">Victory. Loot: ${raid.loot || 0} XP each.</p>` : "") +
    (raid.status === "fled" ? `<p class="loot">The raid broke.</p>` : "");
  if (typeof applyBars === "function") applyBars(raidEl);
}

Hooks.header.push(renderGold);

if (Hooks.jobExtras) {
  const baseExtras = Hooks.jobExtras;
  Hooks.jobExtras = () => baseExtras() + ((UI.state && UI.state.gold && UI.state.gold.paused)
    ? `<label class="check"><input type="checkbox" name="override"><span>Send it anyway<small>The daily budget is spent. This sends the session now, from this phone only.</small></span></label>` : "");
}

if (Hooks.panels.usage) {
  const baseUsage = Hooks.panels.usage.render;
  Hooks.panels.usage.render = async function (body) {
    await baseUsage.apply(this, arguments);
    const g = UI.state && UI.state.gold;
    if (!g) return;
    const sec = document.createElement("section");
    sec.className = "panel-sec";
    sec.innerHTML = goldHTML(g);
    body.prepend(sec);
    if (typeof applyBars === "function") applyBars(sec);
  };
}

Object.assign(Hooks.panelActions, {
  async "release-gold"(btn) {
    await api(`/api/gold/release/${btn.dataset.qid}`, {});
    toast("Sent.");
    await poll();
  },
});
