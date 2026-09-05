#!/usr/bin/env python3
"""Run the production CLI from this checkout with the selected Python interpreter."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def doctor():
    modules, issues = {}, []
    for name in ("xhnovel_pipeline", "jsonschema", "yaml", "pypdf"):
        try:
            module = importlib.import_module(name)
            modules[name] = str(Path(module.__file__).resolve())
        except ImportError as exc:
            issues.append({"module": name, "error": str(exc)})
    expected = ROOT / "src/xhnovel_pipeline/__init__.py"
    if modules.get("xhnovel_pipeline") != str(expected):
        issues.append({"module": "xhnovel_pipeline", "error": "module is not from this checkout"})
    result = {"checkout": str(ROOT), "python": sys.executable,
              "python_version": sys.version.split()[0], "modules": modules, "issues": issues}
    if not issues:
        from xhnovel_pipeline.build_identity import build_source_hash
        result["source_tree_hash"] = build_source_hash(ROOT)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if issues else 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["doctor"]:
        return doctor()
    try:
        from xhnovel_pipeline.cli import main as native_main
    except ImportError as exc:
        print(f"E-HOST-ENVIRONMENT: {exc}; run {sys.executable} {__file__} doctor",
              file=sys.stderr)
        return 2
    return native_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
