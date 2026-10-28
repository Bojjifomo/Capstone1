// Petitions: updates the guild proposes, waiting for your yes or no.
"use strict";

const PETITION_STATUS = {
  pending: "awaits you", approved: "approved", building: "being built", rejected: "rejected",
  merged: "merged", closed: "closed", failed: "failed", expired: "expired",
};

Hooks.header.push((state) => {
  const badge = $("#petitions-btn .badge");
  badge.hidden = !state.petitions;
  badge.textContent = state.petitions || "";
});

function petitionForm(p, projects, o) {
  const home = projects.find((x) => /Capstone1$/i.test(x.label)) || projects[0] || {};
  return `<form class="form petition-form" data-form="petition" data-pid="${esc(p.id)}">` +
    `<label>Build in<select name="project">${projects.map((x) => `<option value="${esc(x.id)}"${x.id === home.id ? " selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>` +
    `<div class="two"><label>Permissions<select name="mode">${options(o.modes, W.modes, o.default_mode)}</select></label>` +
    `<label>Model<select name="model">${options(o.models, W.models, "")}</select></label></div>` +
    `<label>Note for the builder <small>(optional)</small><textarea name="note" rows="2" maxlength="1000" placeholder="e.g. keep the old column too"></textarea></label>` +
    `<div class="btn-row">` +
    (p.status === "pending"
      ? `<button type="submit" class="btn primary" name="act" value="build">Approve &amp; build</button>` +
        `<button type="submit" class="btn" name="act" value="approve">Approve, build later</button>` +
        `<button type="submit" class="btn danger" name="act" value="reject">Reject</button>`
      : `<button type="submit" class="btn primary" name="act" value="build-later">Build it now</button>`) +
    `</div></form>`;
}

function petitionCard(p, open, projects, o) {
  const tags = [p.app, p.impact && "impact " + p.impact, p.effort && "effort " + p.effort].filter(Boolean);
  return `<article class="petition st-${esc(p.status)}"><header><b>${esc(p.title)}</b><span class="tag">${esc(PETITION_STATUS[p.status] || p.status)}</span></header>` +
    `<p class="sub">from ${esc(p.by)} · ${esc(ago(p.created))}${tags.length ? " · " + tags.map(esc).join(" · ") : ""}</p>` +
    (p.summary ? `<p>${esc(p.summary)}</p>` : "") +
    `<details${open ? " open" : ""}><summary>The plan</summary><div class="md">${MD.render(p.plan)}</div></details>` +
    (p.note ? `<p class="petition-note"><b>Your note:</b> ${esc(p.note)}</p>` : "") +
    (p.pr_url ? `<p class="pr-link"><a href="${esc(p.pr_url)}" target="_blank" rel="noopener noreferrer">Pull request</a>` +
      (p.status === "building" ? " · open, waiting on review" : "") + `</p>` : "") +
    (p.status === "building" && p.quest_ok && !p.pr_url ? `<p class="sub">The build quest finished. No pull request link yet.</p>` : "") +
    (p.status === "failed" ? `<p class="sub">The build quest failed before it opened a pull request.</p>` : "") +
    (p.status === "pending" || p.status === "approved" ? petitionForm(p, projects, o) : "") +
    (p.sid ? `<button type="button" class="btn" data-sid="${esc(p.sid)}">Open the build quest</button>` : "") +
    `</article>`;
}

Hooks.panels.petitions = {
  title: "Petitions",
  subtitle: "Updates the guild wants to make. Nothing is built until you say yes.",
  icon: '<span class="panel-icon">📜</span>',
  async render(body) {
    const [{ petitions }, { projects }, o] = await Promise.all([api("/api/petitions"), api("/api/projects"), getOptions()]);
    MD.reset();
    const onlyOne = petitions.filter((p) => p.status === "pending").length === 1;
    body.innerHTML = petitions.length
      ? petitions.map((p) => petitionCard(p, p.id === UI.arg || (onlyOne && p.status === "pending"), projects, o)).join("")
      : `<p class="loading">No petitions yet. Ask a specialist, e.g. Orin: "propose the 3 most useful updates to the Wallet Finder as petitions".</p>`;
  },
};

Hooks.forms.petition = async (form, submitter) => {
  const pid = form.dataset.pid;
  const act = submitter ? submitter.value : "build";
  const note = form.note.value.trim();
  const build = { project: form.project.value, mode: form.mode.value, model: form.model.value };
  let r;
  if (act === "reject") {
    if (!confirm("Reject this petition?")) return;
    await api(`/api/petitions/${pid}/decide`, { decision: "reject", note });
    toast("Petition rejected.");
  } else if (act === "approve") {
    await api(`/api/petitions/${pid}/decide`, { decision: "approve", note });
    toast("Approved. Build it whenever you like.");
  } else {
    const path = act === "build" ? `/api/petitions/${pid}/decide` : `/api/petitions/${pid}/build`;
    r = await withSeal("bypass:new", isRisky(build.mode), (seal) =>
      api(path, Object.assign({ decision: "approve", build: true, note }, build, seal)));
    UI.run = { id: r.run, sid: r.sid, events: [], text: "Building the approved petition", done: false, v: -1 };
    history.replaceState({ sheet: r.sid }, "");
    openSheet(r.sid, false);
    followRun(r.run);
    toast("Approved. The build has started.");
    return;
  }
  Hooks.panels.petitions.render($("#sheet-body"));
};
