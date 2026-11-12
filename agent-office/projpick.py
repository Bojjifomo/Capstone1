"""Which project folder a task belongs in: Haiku picks from the list; keyword scoring when it cannot."""
import json
import os

import haiku
from team import words

PICK_SYSTEM = (
    "You pick which project folder a task belongs in. Answer ONLY a JSON object: "
    '{"id": "<id from the list>"}. Use exactly one id from the project list, or {"id": null} when no project fits.')


def _base(path):
    return os.path.basename(str(path).rstrip("/"))


def score(task_words, project, hints):
    """Keyword overlap: label and folder name count most, hints (intern names, teammate folders) less."""
    own = set(words(project["label"].replace("/", " ") + " " + _base(project["path"]).replace("-", " ").replace("_", " ")))
    extra = set()
    for text in hints:
        extra.update(words(text.replace("-", " ").replace("_", " ")))
    total = 0.0
    for w in set(task_words):
        if w in own:
            total += 3.0
        elif w in extra:
            total += 1.5
    return total


def hints_for(path, interns, staff):
    """Extra words that point at this folder: interns whose project is in it, teammates whose folder is."""
    out = []
    for i in interns or []:
        if i.get("project") and (i["project"] == path or i["project"].startswith(path + "/")):
            out.append(str(i.get("name") or ""))
    for s in staff or []:
        folder = s.get("folder")
        if folder and (folder == path or folder.startswith(path + "/")):
            out.append(_base(folder))
            out.append(str(s.get("name") or ""))
    return out


def suggest_project(cfg, task, projects, interns=(), staff=()):
    """Returns {id, label, source: 'ai'|'keywords'} or {id: None}. `projects` items carry id, path, label."""
    task = " ".join(str(task).split())[:2000]
    none = {"id": None, "label": "", "source": "none"}
    if not task or not projects:
        return none
    by_id = {p["id"]: p for p in projects}
    try:
        listing = "\n".join("- %s: %s (folder %s)" % (p["id"], p["label"], _base(p["path"])) for p in projects)
        raw = haiku.ask(cfg, PICK_SYSTEM, "Task:\n%s\n\nProjects:\n%s" % (task, listing), timeout=40)
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        pid = data.get("id")
        if isinstance(pid, str) and pid in by_id:
            return {"id": pid, "label": by_id[pid]["label"], "source": "ai"}
    except Exception:
        pass
    want = words(task)
    best, best_score = None, 0.0
    for p in projects:  # projects arrive most-recent first, so ties go to the one used lately
        s = score(want, p, hints_for(p["path"], interns, staff))
        if s > best_score:
            best, best_score = p, s
    if not best:
        return none
    return {"id": best["id"], "label": best["label"], "source": "keywords"}
