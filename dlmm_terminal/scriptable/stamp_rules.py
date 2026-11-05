"""Stamp dlmm_terminal/config.json and data/benchmark.json into "DLMM Rules.js".

  python3 dlmm_terminal/scriptable/stamp_rules.py

The phone module has to judge a pool exactly like the Mac terminal, so its defaults are not
typed twice: this copies them in. test_rules.mjs fails if the stamped copy drifts from config.json.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
TARGET = HERE / "DLMM Rules.js"


def strip_notes(o):
    if isinstance(o, dict):
        return {k: strip_notes(v) for k, v in o.items() if not k.startswith("_")}
    return o


def stamp(text: str, name: str, body: str) -> str:
    pat = re.compile(rf"(//__{name}_BEGIN__\n).*?(//__{name}_END__)", re.S)
    if not pat.search(text):
        raise SystemExit(f"marker {name} missing in {TARGET.name}")
    return pat.sub(lambda m: m.group(1) + body + "\n" + m.group(2), text)


def main():
    cfg = strip_notes(json.loads((PKG / "config.json").read_text()))
    text = TARGET.read_text()
    text = stamp(text, "DEFAULTS", "const DEFAULTS = " + json.dumps(cfg, indent=2, ensure_ascii=False) + ";")
    bench = PKG / "data" / "benchmark.json"
    if bench.exists():
        text = stamp(text, "PROFILE", "const DEFAULT_PROFILE = " + json.dumps(json.loads(bench.read_text()), indent=1) + ";")
        print("profile:", json.loads(bench.read_text())["builtAt"])
    TARGET.write_text(text)
    print("stamped", TARGET.name)


if __name__ == "__main__":
    main()
