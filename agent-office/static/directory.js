// Under the map: the division chips, and Everyone (every session and specialist in one list) with filters,
// the search box from the header, and a Remove mode.
"use strict";

const Directory = (() => {
  const ORDER = { needs: 0, work: 1, break: 2, off: 3 };
  const DOING = { work: "Working", needs: "Needs you", break: "On break", off: "Off" };
  const FILTERS = [["all", "All"], ["needs", "Needs you"], ["work", "Working"], ["break", "On break"], ["off", "Off"]];
  const D = { filter: "all", division: "", project: "", q: "", edit: false, res: null, state: null };

  function chipsHTML(res, state) {
    const divs = res.divisions.map((d) =>
      `<button type="button" class="div-chip${D.division === d.name ? " on" : ""}${d.needs ? " needs" : ""}" data-division="${esc(d.name)}">` +
      `<i class="dot d-${esc(d.name.toLowerCase().replace(/[^a-z]/g, ""))}"></i>${esc(d.name)} <b>${d.people.length}</b>` +
      `<small>${d.working} working${d.needs ? ` · <em>${d.needs} need you</em>` : ""}${d.cost >= 0.01 ? " · " + money(d.cost) : ""}</small></button>`).join("");
    const pr = state.programs || {};
    return divs +
      `<button type="button" class="div-chip${D.division === "Lounge" ? " on" : ""}" data-division="Lounge"><i class="dot d-lounge"></i>Lounge <b>${res.onBreak.length}</b><small>on break</small></button>` +
      `<button type="button" class="div-chip" data-open="bots"><i class="dot d-servers"></i>Server room <b>${pr.up || 0}</b><small>${pr.total ? `running${pr.trouble ? ` · <em>${pr.trouble} in trouble</em>` : ""}` : "no programs yet"}</small></button>` +
      (res.off.length ? `<button type="button" class="div-chip${D.division === "Off" ? " on" : ""}" data-division="Off"><i class="dot d-off"></i>Off <b>${res.off.length}</b><small>gone home</small></button>` : "");
  }

  function matches(p) {
    if (D.filter !== "all" && p.act !== D.filter) return false;
    if (D.division === "Lounge") { if (p.act !== "break") return false; }
    else if (D.division === "Off") { if (p.act !== "off") return false; }
    else if (D.division && p.division !== D.division) return false;
    if (D.project && p.project !== D.project && !(D.project === "Specialists" && p.specialist)) return false;
    if (D.q) {
      const hay = [p.label, p.name, p.title, p.division, p.project, p.kindLabel].join(" ").toLowerCase();
      if (!hay.includes(D.q)) return false;
    }
    return true;
  }

  function rowHTML(p) {
    const tag = p.project ? `<span class="ptag">${esc(p.project)}</span>` : p.seat ? `<span class="ptag seat">${esc(p.seat)}</span>` : "";
    const row = `<button type="button" class="roster-row a-${esc(p.act)}" ${p.staff ? `data-staff="${esc(p.staff)}"` : `data-sid="${esc(p.sid)}"`}>` +
      `${People.svg(p.seed, p.kind, 22)}<span><b>${esc(p.label || p.name)}${p.label && p.name && p.label !== p.name && !p.specialist ? ` <small class="full">· ${esc(p.name)}</small>` : ""}</b>` +
      `<small>${esc(p.title || "")}${p.title ? " · " : ""}${esc(p.kindLabel)}${p.division ? " · " + esc(p.division) : ""} ${tag}</small></span>` +
      `<em>${esc(DOING[p.act] || "")}<small>${esc(p.remote || "")}</small></em></button>`;
    if (!D.edit || p.builtin) return row;
    const who = p.staff ? `data-remove-staff="${esc(p.staff)}"` : `data-remove-session="${esc(p.sid)}"`;
    return `<div class="roster-wrap">${row}<button type="button" class="roster-x" ${who} data-name="${esc(p.label || p.name)}" aria-label="Remove ${esc(p.label || p.name)}">✕</button></div>`;
  }

  function render() {
    const res = D.res, state = D.state;
    const chips = $("#division-chips");
    const list = $("#everyone");
    if (!res || !chips || !list) return;
    chips.innerHTML = chipsHTML(res, state);
    const all = res.everyone.slice().sort((a, b) => (ORDER[a.act] ?? 9) - (ORDER[b.act] ?? 9) || String(a.label || a.name).localeCompare(String(b.label || b.name)));
    const shown = all.filter(matches);
    const count = (f) => all.filter((p) => f === "all" || p.act === f).length;
    const where = D.division ? ` in ${D.division}` : D.project ? ` in ${D.project}` : "";
    list.innerHTML = `<header><b>Everyone${where}</b><small>${shown.length} of ${all.length}` +
      ` · ${D.edit ? "tap ✕ to remove someone" : "tap someone to open them"}</small>` +
      (D.division || D.project ? `<button type="button" class="chip-btn" data-dir-clear>Show all</button>` : "") +
      `<button type="button" class="chip-btn roster-edit${D.edit ? " on" : ""}" data-dir-edit>${D.edit ? "Done" : "✎ Remove"}</button></header>` +
      `<div class="roster-filters" role="tablist">` + FILTERS.filter(([f]) => f === "all" || count(f)).map(([f, label]) =>
        `<button type="button" class="chip-btn${f === D.filter ? " on" : ""}${f === "needs" && count("needs") ? " needs" : ""}" data-dir-filter="${f}">${esc(label)} <b>${count(f)}</b></button>`).join("") + `</div>` +
      (shown.length ? shown.map(rowHTML).join("") : `<p class="sub">${D.q ? "Nobody matches." : D.filter === "needs" ? "Nobody is waiting on you." : "Nobody here right now."}</p>`);
  }

  function update(res, state) { D.res = res; D.state = state; render(); renderProjects(res, state); }

  function renderProjects(res, state) {
    const box = $("#project-chips");
    if (!box) return;
    const names = Object.entries(res.projects).sort((a, b) => b[1] - a[1]);
    const specialists = res.everyone.filter((p) => p.specialist).length;
    box.innerHTML = `<button type="button" class="chip-btn${D.project ? "" : " on"}" data-project="">All <b>${res.everyone.length}</b></button>` +
      (specialists ? `<button type="button" class="chip-btn${D.project === "Specialists" ? " on" : ""}" data-project="Specialists">Specialists <b>${specialists}</b></button>` : "") +
      names.slice(0, 12).map(([n, c]) => `<button type="button" class="chip-btn${D.project === n ? " on" : ""}" data-project="${esc(n)}">${esc(n)} <b>${c}</b></button>`).join("");
  }

  function focus(division) {
    D.division = D.division === division ? "" : division;
    D.filter = "all";
    render();
    const list = $("#everyone");
    if (list && D.division) list.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function search(q) { D.q = String(q || "").trim().toLowerCase(); render(); }

  document.addEventListener("click", async (ev) => {
    const chip = ev.target.closest("#division-chips [data-division]");
    if (chip) { focus(chip.dataset.division); return; }
    const proj = ev.target.closest("#project-chips [data-project]");
    if (proj) { D.project = proj.dataset.project; D.division = ""; render(); renderProjects(D.res, D.state); return; }
    const f = ev.target.closest("[data-dir-filter]");
    if (f) { D.filter = f.dataset.dirFilter; render(); return; }
    if (ev.target.closest("[data-dir-clear]")) { D.division = ""; D.project = ""; render(); renderProjects(D.res, D.state); return; }
    if (ev.target.closest("[data-dir-edit]")) { D.edit = !D.edit; render(); return; }
    const x = ev.target.closest("[data-remove-staff], [data-remove-session]");
    if (!x) return;
    const name = x.dataset.name;
    try {
      if (x.dataset.removeStaff) {
        const m = ((D.state && D.state.team && D.state.team.staff) || []).find((s) => s.id === x.dataset.removeStaff);
        if (!confirm(m ? dismissQuestion(m) : `Let ${name} go?`)) return;
        await api(`/api/staff/${x.dataset.removeStaff}/dismiss`, { force: true });
        toast(`${name} has left the office.`);
      } else {
        if (!confirm(`Archive ${name}'s session?\n\nThey leave the map. You can bring them back from Archived.`)) return;
        await api(`/api/session/${x.dataset.removeSession}/archive`, { archived: true });
        toast(`${name} was archived.`);
      }
      await poll();
    } catch (e) { toast(e.message); }
  });

  document.addEventListener("input", (ev) => {
    if (ev.target.id === "find-input") search(ev.target.value);
  });

  return { update, focus, search };
})();
