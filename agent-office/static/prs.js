// PR page: open pull requests in every Bojjifomo repo. Review here, merge with a phone seal.
"use strict";

const CI_LABEL = { green: "CI green", red: "CI failing", pending: "CI running", none: "no CI checks", unknown: "CI unknown" };
const VERDICT_LABEL = {
  approve: "Thorne: approve", changes: "Thorne: changes needed", "needs-you": "Thorne: needs your decision",
  failed: "Thorne: review failed", stale: "Thorne: approved an older commit, review again", pending: "Thorne: reviewing…", none: "Thorne: not reviewed",
};

function prCard(p) {
  const key = `data-repo="${esc(p.repo)}" data-number="${p.number}"`;
  const why = p.can_merge ? "" : p.draft ? "Draft PR." : p.ci !== "green" ? "Merge is off until CI is green." : "Merge is off until Thorne approves.";
  return `<article class="petition pr-card" ${key}><header><b>${esc(p.repo)}#${p.number} · ${esc(p.title)}</b></header>` +
    `<p class="sub">${p.author ? "by " + esc(p.author) + " · " : ""}${esc(p.created.slice(0, 10))}${p.draft ? " · draft" : ""}` +
    ` · <a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer">GitHub</a></p>` +
    `<p><span class="tag pr-ci-${esc(p.ci)}">${esc(CI_LABEL[p.ci] || p.ci)}</span> ` +
    `<span class="tag pr-v-${esc(p.verdict)}">${esc(VERDICT_LABEL[p.verdict] || p.verdict)}</span></p>` +
    (p.verdict_note ? `<p class="sub">${esc(p.verdict_note)}</p>` : "") +
    (p.lp_bot ? `<p class="run-error">Careful: this bot talks to real followers. A bad merge reaches them.</p>` : "") +
    `<details class="pr-diff"><summary>Diff</summary><pre class="pr-diff-body">Open to load…</pre></details>` +
    `<div class="btn-row"><button type="button" class="btn primary" data-panel-act="pr-merge" ${key}${p.can_merge ? "" : " disabled"}>Merge (squash)</button>` +
    `<button type="button" class="btn" data-panel-act="pr-changes" ${key}>Minta perbaikan</button></div>` +
    (why ? `<p class="sub">${esc(why)}</p>` : "") + `</article>`;
}

Hooks.panels.prs = {
  title: "Pull requests",
  subtitle: "Open PRs in every Bojjifomo repo. Merge needs green CI, Thorne's approval and a phone seal.",
  icon: '<span class="panel-icon">🔀</span>',
  async render(body) {
    const { prs } = await api("/api/prs");
    body.innerHTML = prs.length ? prs.map(prCard).join("") : `<p class="loading">No open pull requests.</p>`;
    body.querySelectorAll(".pr-diff").forEach((d) => d.addEventListener("toggle", async () => {
      const pre = $(".pr-diff-body", d);
      if (!d.open || pre.dataset.loaded) return;
      pre.dataset.loaded = "1";
      const card = d.closest(".pr-card");
      try {
        const r = await fetch(`/api/prs/diff?repo=${encodeURIComponent(card.dataset.repo)}&number=${card.dataset.number}`);
        const data = await r.json();
        pre.textContent = r.ok ? data.diff || "(empty diff)" : data.error || "Could not load the diff.";
      } catch (e) {
        pre.textContent = e.message;
      }
    }));
  },
};

Object.assign(Hooks.panelActions, {
  async "pr-merge"(btn) {
    const { repo, number } = btn.dataset;
    const warn = /^bojjifomo\/lp[-_]call[-_]bot$/i.test(repo) ? "\n\nThis bot talks to real followers." : "";
    if (!confirm(`Squash-merge ${repo}#${number} and delete the branch? A code will be sent to your phone.${warn}`)) return false;
    await withSeal(`merge:${repo}#${number}`, true, (seal) => api("/api/prs/merge", Object.assign({ repo, number: Number(number) }, seal)));
    toast(`Merged ${repo}#${number}.`);
  },
  async "pr-changes"(btn) {
    const { repo, number } = btn.dataset;
    const text = prompt("What should change? This is posted on the PR and sent to the agent who opened it.");
    if (!text || !text.trim()) return false;
    const r = await api("/api/prs/changes", { repo, number: Number(number), text: text.trim() });
    toast(r.sent_to ? `Commented and sent to ${r.sent_to}.` : "Commented. No agent found for this PR.");
    return false;
  },
});
