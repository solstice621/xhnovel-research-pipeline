from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .catalog import Catalog
from .engine import NOW, run_local_slice
from .errors import PipelineError
from .paths import repo_root
from .store import ArtifactStore
from .validate import validate_all, validate_collection, validate_evidence, validate_export, validate_qualification
from . import audit
from .tools_legacy import check_legacy_scene_001, check_scene_002_tombstone
from .bundle_ops import refuse_inplace_member_edit
from .hardening import apply_gc, backup_export, live_artifact_ids, restore_from_backup, write_revocation
from .parse import diff_segments
from .qualification import invalidate_build, upsert_build_record


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
    p_run.add_argument("mode", choices=["local-slice", "wikipedia"])
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

    p_pd = sub.add_parser("parse-diff")
    p_pd.add_argument("a", type=pathlib.Path)
    p_pd.add_argument("b", type=pathlib.Path)

    p_fr = sub.add_parser("freeze-bundle")
    p_fr.add_argument("catalog", type=pathlib.Path)
    p_fr.add_argument("bundle_id")
    p_fr.add_argument("--set", action="append", default=[])

    p_qual = sub.add_parser("qualify")
    p_qual.add_argument("fixture", type=pathlib.Path)
    p_qual.add_argument("--work-dir", type=pathlib.Path, default=None)

    p_inv = sub.add_parser("invalidate-build")
    p_inv.add_argument("build_id")
    p_inv.add_argument("--reason", default="prompt/model/profile change")

    p_bak = sub.add_parser("backup")
    p_bak.add_argument("export", type=pathlib.Path)
    p_bak.add_argument("store", type=pathlib.Path)
    p_bak.add_argument("dest", type=pathlib.Path)

    p_rst = sub.add_parser("restore")
    p_rst.add_argument("backup", type=pathlib.Path)
    p_rst.add_argument("store", type=pathlib.Path)

    p_gc = sub.add_parser("gc")
    p_gc.add_argument("catalog", type=pathlib.Path)
    p_gc.add_argument("store", type=pathlib.Path)
    p_gc.add_argument("--apply", action="store_true")

    p_rev = sub.add_parser("revoke-export")
    p_rev.add_argument("export", type=pathlib.Path)
    p_rev.add_argument("--reason", required=True)

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
            if args.mode == "wikipedia":
                from .http_fetch import HttpFetcher
                from .wikipedia import WikipediaOpenSearchProvider

                result = run_local_slice(
                    args.fixture,
                    work,
                    repo_root=root,
                    provider=WikipediaOpenSearchProvider(),
                    fetcher=HttpFetcher(),
                )
            else:
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
        if args.cmd == "parse-diff":
            a = json.loads(args.a.read_text(encoding="utf-8"))
            b = json.loads(args.b.read_text(encoding="utf-8"))
            print(json.dumps(diff_segments(a, b), indent=2))
            return 0
        if args.cmd == "freeze-bundle":
            catalog, _ = _catalog_from_json(args.catalog)
            bundle = catalog.get("EvidenceBundle", args.bundle_id)
            if args.set:
                fields = {}
                for item in args.set:
                    key, _, val = item.partition("=")
                    fields[key] = val
                refuse_inplace_member_edit(catalog, bundle, **fields)
            from .bundle_ops import assert_frozen_intact, freeze_bundle

            freeze_bundle(catalog, bundle)
            assert_frozen_intact(catalog, bundle)
            print(f"OK: frozen {bundle['bundle_id']} {bundle['bundle_hash']}")
            return 0
        if args.cmd == "qualify":
            work = args.work_dir or (root / ".runtime" / "qualify" / args.fixture.name)
            result = run_local_slice(args.fixture, work, repo_root=root)
            build = result["catalog"].all("ExtractorBuild")[0]
            qrun = result["catalog"].all("QualificationRun")[0]
            upsert_build_record(root, build, qualification=qrun)
            print(
                json.dumps(
                    {"result": qrun["result"], "build": build["extractor_build_id"], "status": build["status"]},
                    indent=2,
                )
            )
            return 0 if qrun["result"] == "PASS" else 1
        if args.cmd == "invalidate-build":
            print(json.dumps(invalidate_build(root, args.build_id, reason=args.reason), indent=2))
            return 0
        if args.cmd == "backup":
            store = ArtifactStore(args.store)
            print(json.dumps(backup_export(args.export, store, args.dest), indent=2))
            return 0
        if args.cmd == "restore":
            store = ArtifactStore(args.store)
            print(json.dumps(restore_from_backup(args.backup, store), indent=2))
            return 0
        if args.cmd == "gc":
            data = json.loads(args.catalog.read_text(encoding="utf-8"))
            live = live_artifact_ids(data)
            store = ArtifactStore(args.store)
            removed = apply_gc(store, live) if args.apply else audit.gc_cas(store, live)
            print(json.dumps({"apply": args.apply, "candidates_or_removed": removed}, indent=2))
            return 0
        if args.cmd == "revoke-export":
            print(json.dumps(write_revocation(args.export, reason=args.reason, created_at=NOW), indent=2))
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
