from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .catalog import Catalog
from .engine import run_local_slice
from .errors import PipelineError
from .paths import repo_root
from .store import ArtifactStore
from .validate import validate_all, validate_collection, validate_evidence, validate_export, validate_qualification
from . import audit
from .tools_legacy import check_legacy_scene_001, check_scene_002_tombstone
from .hashing import object_hash


def _catalog_from_json(path: pathlib.Path) -> tuple[Catalog, ArtifactStore | None]:
    data = json.loads(path.read_text(encoding="utf-8"))
    catalog = Catalog()
    for kind, objs in data.items():
        for obj in objs:
            catalog.add(kind, obj)
    store = None
    runtime = path.parent / "objects"
    if runtime.exists():
        store = ArtifactStore(runtime)
    return catalog, store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xhnovel-pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_val = sub.add_parser("validate")
    p_val.add_argument("target", choices=["collection", "evidence", "qualification", "export", "all"])
    p_val.add_argument("catalog", type=pathlib.Path)

    p_run = sub.add_parser("run")
    p_run.add_argument("mode", choices=["local-slice"])
    p_run.add_argument("fixture", type=pathlib.Path)
    p_run.add_argument("--work-dir", type=pathlib.Path, default=None)

    p_ver = sub.add_parser("verify-export")
    p_ver.add_argument("export", type=pathlib.Path)

    p_exp = sub.add_parser("explain-claim")
    p_exp.add_argument("catalog", type=pathlib.Path)
    p_exp.add_argument("claim_id")

    p_tr = sub.add_parser("trace-request")
    p_tr.add_argument("catalog", type=pathlib.Path)
    p_tr.add_argument("request_id")

    p_art = sub.add_parser("check-artifact")
    p_art.add_argument("store", type=pathlib.Path)
    p_art.add_argument("artifact_id")

    p_scan = sub.add_parser("scan-artifacts")
    p_scan.add_argument("catalog", type=pathlib.Path)
    p_scan.add_argument("store", type=pathlib.Path)

    p_diff = sub.add_parser("diff-bundle")
    p_diff.add_argument("a", type=pathlib.Path)
    p_diff.add_argument("b", type=pathlib.Path)

    p_leg = sub.add_parser("legacy-check")

    args = parser.parse_args(argv)
    root = repo_root()
    try:
        if args.cmd == "validate":
            catalog, store = _catalog_from_json(args.catalog)
            if args.target == "collection":
                validate_collection(catalog, store)
            elif args.target == "evidence":
                validate_evidence(catalog, store)
            elif args.target == "qualification":
                validate_qualification(catalog)
            elif args.target == "export":
                validate_export(catalog, store)
            else:
                validate_all(catalog, store)
            print(f"OK: validate {args.target}")
            return 0
        if args.cmd == "run":
            work = args.work_dir or (root / ".runtime" / "slices" / args.fixture.name)
            result = run_local_slice(args.fixture, work, repo_root=root)
            print(f"OK: verified export {result['export']['export_id']}")
            print(result["work_dir"] / "export.json")
            return 0
        if args.cmd == "verify-export":
            audit.verify_export_bytes(args.export.read_bytes())
            print("OK: export hash")
            return 0
        if args.cmd == "explain-claim":
            catalog, _ = _catalog_from_json(args.catalog)
            print(json.dumps(audit.explain_claim(catalog, args.claim_id), ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "trace-request":
            catalog, _ = _catalog_from_json(args.catalog)
            print(json.dumps(audit.trace_request(catalog, args.request_id), ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "check-artifact":
            store = ArtifactStore(args.store)
            print(audit.check_artifact(store, args.artifact_id))
            return 0
        if args.cmd == "scan-artifacts":
            catalog, _ = _catalog_from_json(args.catalog)
            store = ArtifactStore(args.store)
            ids = [a["artifact_id"] for a in catalog.all("Artifact")]
            print(json.dumps(audit.scan_artifacts(store, ids), indent=2))
            return 0
        if args.cmd == "diff-bundle":
            a = json.loads(args.a.read_text(encoding="utf-8"))
            b = json.loads(args.b.read_text(encoding="utf-8"))
            print(json.dumps(audit.diff_bundle(a, b), indent=2))
            return 0
        if args.cmd == "legacy-check":
            check_legacy_scene_001(root)
            check_scene_002_tombstone(root)
            print("OK: legacy fixtures remain unqualified; SCENE-002 live claims = 0")
            return 0
    except PipelineError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
