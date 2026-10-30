// Usage dashboard: where the gold goes, who could run on a cheaper model, what the CEO changed.
"use strict";

const KIND_NAME = { you: "You", chain: "Chain", guild: "Guild" };
const FAMILY_NAME = { opus: "Opus", sonnet: "Sonnet", haiku: "Haiku", other: "Other" };
const IDEA_TAG = { likely: "likely", try: "worth a try", setting: "setting", habit: "habit" };

function segBar(items) {
  return `<span class="seg-bar">` + items.filter((x) => x.cost > 0).map((x) =>
    `<i class="seg s-${esc(x.cls)}" data-w="${(x.share * 100).toFixed(1)}" title="${esc(x.label)}"></i>`).join("") + `</span>`;
}

function legend(items) {
  return `<div class="legend">` + items.map((x) =>
    `<span><i class="sw s-${esc(x.cls)}"></i>${esc(x.label)} <b>${money(x.cost)}</b> <small>${Math.round(x.share * 100)}%</small></span>`).join("") + `</div>`;
}

function modelForm(a) {
  if (!a.staff) return "";
  const current = ["haiku", "sonnet", "opus"].includes(a.model) ? a.model : "";
  return `<form class="form inline" data-form="report-model" data-name="${esc(a.name)}">` +
    `<select name="model" aria-label="Model for ${esc(a.name)}">` +
    ["haiku", "sonnet", "opus"].filter((m) => !UI.options || UI.options.models.includes(m) || m === current)
      .map((m) => `<option value="${m}"${m === current ? " selected" : ""}>${FAMILY_NAME[m]}</option>`).join("") +
    (current ? "" : `<option value="" selected>Default</option>`) +
    `</select><button type="submit" class="btn small">Set</button></form>`;
}

function actorHTML(a, top) {
  const fam = Object.entries(a.fam).sort((x, y) => y[1] - x[1]).map(([f, c]) => `${FAMILY_NAME[f] || f} ${money(c)}`).join(" · ");
  return `<article class="spender k-${esc(a.kind)}"><header>` +
    `<b>${esc(a.name)}</b><span class="tag">${esc(KIND_NAME[a.kind])}</span><span class="v">${money(a.cost)}</span></header>` +
    bar(a.cost / top, a.kind === "chain" ? "" : a.kind === "guild" ? "red" : "gold") +
    `<p class="sub">${Math.round(a.share * 100)}% · ${esc(tokens(a.tokens))} tokens · ${esc(a.sessions)} session${a.sessions === 1 ? "" : "s"}` +
    `${a.today ? " · " + money(a.today) + " today" : ""}${a.role ? " · " + esc(a.role) : ""}</p>` +
    `<p class="sub">${esc(fam)}</p>` +
    `<div class="btn-row">${a.sid ? `<button type="button" class="btn small" data-sid="${esc(a.sid)}">Open latest</button>` : ""}${modelForm(a)}</div></article>`;
}

function ideaHTML(i) {
  const act = i.action;
  const button = !act ? (i.hint ? `<p class="sub">${esc(i.hint)}</p>` : "")
    : act.staff ? `<button type="button" class="btn small primary" data-panel-act="idea-model" data-name="${esc(i.who)}" data-model="${esc(act.model)}">Move to ${esc(FAMILY_NAME[act.model])}</button>`
    : `<button type="button" class="btn small primary" data-panel-act="idea-setting" data-setting="${esc(JSON.stringify(act.setting))}">Apply</button>`;
  return `<article class="idea i-${esc(i.kind)}"><header><b>${esc(i.title)}</b><span class="tag">${esc(IDEA_TAG[i.kind] || i.kind)}</span></header>` +
    `<p>${esc(i.detail)}</p><p class="sub">Saves about ${money(i.saving)} a week.</p>${button}</article>`;
}

Hooks.panels.report = {
  title: "Where the gold goes",
  subtitle: "Usage by person, kind of work, model and project, and what could run cheaper.",
  icon: '<span class="panel-icon">📊</span>',
  async render(body) {
    const [r, c] = await Promise.all([api("/api/usage/report?days=7"), api("/api/chain").catch(() => null)]);
    MD.reset();
    const topActor = Math.max(...r.actors.map((a) => a.cost), 0.0001);
    const kinds = r.kinds.map((k) => ({ cost: k.cost, share: k.share, cls: k.kind, label: KIND_NAME[k.kind] }));
    const models = r.models.map((m) => ({ cost: m.cost, share: m.share, cls: m.family, label: FAMILY_NAME[m.family] || m.family }));
    const topProject = Math.max(...r.projects.map((p) => p.cost), 0.0001);
    const g = r.guard || {};
    const review = c && c.people.ceo.review;
    const ceo = c && c.people.ceo;
    body.innerHTML =
      `<section class="panel-sec stats"><div><b>${money(r.today)}</b><small>today</small></div>` +
      `<div><b>${money(r.total)}</b><small>last ${esc(r.days)} days</small></div>` +
      `<div><b>${money(r.projected_month)}</b><small>a month at this rate</small></div></section>` +
      (g.metered ? `<section class="panel-sec"><p class="sub">Daily budget ${Math.round(g.used)}% used. Automatic rounds stop at ${esc(g.pct)}%.${g.on ? " <b>Rationing now.</b>" : ""}</p>` +
        bar((g.used || 0) / 100, g.on ? "red" : "gold") + `</section>` : "") +
      `<section class="panel-sec"><h3>What costs the most</h3>${segBar(kinds)}${legend(kinds)}</section>` +
      `<section class="panel-sec"><h3>Which model</h3>${segBar(models)}${legend(models)}` +
      `<p class="sub">Opus seats: ${esc(r.seats.used)} of ${esc(r.seats.max)} in use.</p></section>` +
      (r.ideas.length ? `<section class="panel-sec"><h3>You could save about ${money(r.potential)} a week</h3>${r.ideas.map(ideaHTML).join("")}</section>` : "") +
      `<section class="panel-sec"><h3>Biggest spenders</h3>${r.actors.length ? r.actors.map((a) => actorHTML(a, topActor)).join("") : '<p class="loading">No usage in this window.</p>'}</section>` +
      (r.projects.length ? `<section class="panel-sec"><h3>By project</h3><div class="rows">` + r.projects.map((p) =>
        `<div class="row"><span class="k">${esc(p.name)}</span>${bar(p.cost / topProject)}<span class="v">${money(p.cost)}</span></div>`).join("") + `</div></section>` : "") +
      (ceo ? `<section class="panel-sec"><h3>The CEO's budget review</h3>` +
        (ceo.name ? `<p class="sub">${esc(ceo.name)} reads this report, moves people between models and writes down why.` +
          `${review && review.started ? ` Last review ${esc(ago(review.ended || review.started))}${review.running ? " (running)" : review.ok === false ? " (failed)" : ""}.` : " No review yet."}</p>` +
          (review && review.summary ? `<details><summary>Their last review</summary><div class="md">${MD.render(review.summary)}</div></details>` : "") +
          `<div class="btn-row"><button type="button" class="btn primary" data-panel-act="chain-round" data-kind="budget">Ask the CEO to review now</button>` +
          (review && review.sid ? `<button type="button" class="btn" data-sid="${esc(review.sid)}">Open the review</button>` : "") + `</div>`
          : `<p class="sub">Recruit the CEO in ⚜️ Chain first.</p>`) + `</section>` : "") +
      `<p class="hint">${esc(r.note)}</p>`;
    applyBars(body);
  },
};

Object.assign(Hooks.forms, {
  async "report-model"(form) {
    const r = await api("/api/team/model", { name: form.dataset.name, model: form.model.value });
    toast(`${r.name}: ${r.was} → ${r.model}`);
    Hooks.panels.report.render($("#sheet-body"));
  },
});

Object.assign(Hooks.panelActions, {
  async "idea-model"(btn) {
    const r = await api("/api/team/model", { name: btn.dataset.name, model: btn.dataset.model });
    toast(`${r.name}: ${r.was} → ${r.model}`);
  },
  async "idea-setting"(btn) {
    await api("/api/chain/settings", JSON.parse(btn.dataset.setting));
    toast("Saved.");
  },
});
