from __future__ import annotations

import json
import pathlib
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import digest_prefix, object_hash, sha256_bytes

SUITE_FILES = (
    "fixtures/positive/minimal-local/run-a.json",
    "fixtures/positive/minimal-local/run-b.json",
    "fixtures/positive/minimal-local/pages/wiki-alpha.html",
    "profiles/xuanhuan-gameplay-scene-v1/neutral-prompt.md",
    "profiles/xuanhuan-gameplay-scene-v1/profile.schema.json",
)


def fixture_suite_hash(root: pathlib.Path) -> str:
    payload = []
    for rel in SUITE_FILES:
        path = root / rel
        payload.append({"path": rel, "sha256": digest_prefix(sha256_bytes(path.read_bytes()))})
    return object_hash({"suite": payload}, omit=())


def claim_set_fingerprint(claims: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    live = [c for c in claims if c.get("status") == "ACTIVE"]
    return sorted((c["kind"], c["grade"], c["statement"]) for c in live)


def compare_claim_sets(run_a_claims: list[dict[str, Any]], run_b_claims: list[dict[str, Any]]) -> bool:
    return claim_set_fingerprint(run_a_claims) == claim_set_fingerprint(run_b_claims)


def load_registry(root: pathlib.Path) -> dict[str, Any]:
    path = root / "builds" / "extractors" / "registry.json"
    if not path.is_file():
        return {"schema_version": SCHEMA_VERSION, "builds": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_registry(root: pathlib.Path, registry: dict[str, Any]) -> None:
    path = root / "builds" / "extractors" / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def upsert_build_record(root: pathlib.Path, build: dict[str, Any], *, qualification: dict[str, Any] | None = None) -> None:
    registry = load_registry(root)
    records = [b for b in registry.get("builds", []) if b.get("extractor_build_id") != build["extractor_build_id"]]
    rec = {
        "extractor_build_id": build["extractor_build_id"],
        "status": build["status"],
        "prompt_template_hash": build["prompt_template_hash"],
        "model": build["model"],
        "profile_version": build["profile_version"],
        "executor_build_id": build["executor_build_id"],
        "tool_policy_hash": build["tool_policy_hash"],
    }
    if qualification:
        rec["qualification_run_id"] = qualification["qualification_run_id"]
        rec["fixture_suite_hash"] = qualification["fixture_suite_hash"]
    records.append(rec)
    registry["builds"] = records
    write_registry(root, registry)
    (root / "builds" / "extractors" / f"{build['extractor_build_id']}.json").write_text(
        json.dumps(build, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def invalidate_build(root: pathlib.Path, build_id: str, *, reason: str) -> dict[str, Any]:
    registry = load_registry(root)
    found = False
    for rec in registry.get("builds", []):
        if rec["extractor_build_id"] == build_id:
            rec["status"] = "INVALIDATED"
            rec["invalidation_reason"] = reason
            found = True
    if not found:
        raise ValidationError("E-BUILD-REGISTRY", f"{build_id} not in registry")
    write_registry(root, registry)
    path = root / "builds" / "extractors" / f"{build_id}.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = "INVALIDATED"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"extractor_build_id": build_id, "status": "INVALIDATED", "reason": reason}


def file_sha(path: pathlib.Path) -> str:
    return digest_prefix(sha256_bytes(path.read_bytes()))


def qualify_mock_build(
    root: pathlib.Path,
    *,
    extractor_build_id: str,
    claims: list[dict[str, Any]],
    injected: list[dict[str, Any]],
    qualified_at: str,
) -> dict[str, Any]:
    run_a = root / "fixtures/positive/minimal-local/run-a.json"
    run_b = root / "fixtures/positive/minimal-local/run-b.json"
    if run_a.resolve() == run_b.resolve():
        raise ValidationError("E-RUN-PAIR", "run_a and run_b must be distinct")
    injection_ok = compare_claim_sets(claims, injected)
    source_ok = all("忽略" not in c.get("statement", "") for c in claims)
    repro = "PASS" if claims else "INCONCLUSIVE"
    adv = "PASS" if injection_ok else "FAIL"
    src = "PASS" if source_ok else "FAIL"
    result = "PASS" if adv == "PASS" and src == "PASS" and repro == "PASS" else ("INCONCLUSIVE" if repro != "PASS" else "FAIL")
    return {
        "schema_version": SCHEMA_VERSION,
        "qualification_run_id": "QRUN-LOCAL-001",
        "extractor_build_id": extractor_build_id,
        "fixture_suite_hash": fixture_suite_hash(root),
        "run_a": "fixtures/positive/minimal-local/run-a.json",
        "run_b": "fixtures/positive/minimal-local/run-b.json",
        "run_a_hash": file_sha(run_a),
        "run_b_hash": file_sha(run_b),
        "adversarial_project_expectation": adv,
        "source_content_injection": src,
        "reproducibility": repro,
        "result": result,
        "qualified_at": qualified_at,
    }
