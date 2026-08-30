from __future__ import annotations

import json
import pathlib
from typing import Any

from .constants import PROFILE_ID, SCHEMA_VERSION
from .errors import ValidationError
from .extraction import mock_extract
from .hashing import artifact_id_for, digest_prefix, object_hash, sha256_bytes
from .ids import derived_id
from .parse import parse_html

SUITE_FILES = (
    "fixtures/positive/minimal-local/run-a.json",
    "fixtures/positive/minimal-local/run-b.json",
    "fixtures/positive/minimal-local/pages/wiki-alpha-clean.html",
    "fixtures/positive/minimal-local/pages/wiki-alpha.html",
    "profiles/xuanhuan-gameplay-scene-v1/neutral-prompt.md",
    "profiles/xuanhuan-gameplay-scene-v1/profile.schema.json",
)

BUILD_IDENTITY_FIELDS = (
    "repository_commit",
    "source_tree_hash",
    "model",
    "prompt_template_hash",
    "parameters",
    "profile_version",
    "executor_build_id",
    "tool_policy_hash",
)

def fixture_suite_hash(root: pathlib.Path) -> str:
    payload = []
    for rel in SUITE_FILES:
        path = root / rel
        payload.append({"path": rel, "sha256": digest_prefix(sha256_bytes(path.read_bytes()))})
    return object_hash({"suite": payload}, omit=())


def build_source_hash(root: pathlib.Path) -> str:
    paths = [root / "pyproject.toml", root / "requirements.lock"]
    paths.extend(sorted((root / "src" / "xhnovel_pipeline").glob("*.py")))
    paths.extend(sorted((root / "profiles" / "xuanhuan-gameplay-scene-v1").glob("*")))
    payload = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        if not path.is_file():
            raise ValidationError("E-BUILD-BIND", f"missing build source {rel}")
        payload.append({"path": rel, "sha256": digest_prefix(sha256_bytes(path.read_bytes()))})
    return object_hash({"build_sources": payload}, omit=())


def claim_set_fingerprint(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live = [c for c in claims if c.get("status") == "ACTIVE"]
    normalized = [
        {
            "kind": claim["kind"],
            "grade": claim["grade"],
            "statement": claim["statement"],
            "profile_schema": claim["profile_schema"],
            "profile_payload": claim["profile_payload"],
        }
        for claim in live
    ]
    return sorted(normalized, key=lambda claim: object_hash(claim, omit=()))


def qualification_result(*, execution_id: str, input_hash: str, claims: list[dict[str, Any]]) -> dict[str, Any]:
    claim_set = claim_set_fingerprint(claims)
    result = {
        "execution_id": execution_id,
        "input_hash": input_hash,
        "claims": claim_set,
        "claim_set_hash": object_hash({"claims": claim_set}, omit=()),
        "result_hash": "sha256:" + "0" * 64,
    }
    result["result_hash"] = object_hash(result, omit=("result_hash",))
    return result


def compare_claim_sets(run_a_claims: list[dict[str, Any]], run_b_claims: list[dict[str, Any]]) -> bool:
    return claim_set_fingerprint(run_a_claims) == claim_set_fingerprint(run_b_claims)


def extractor_build_hash(build: dict[str, Any]) -> str:
    return object_hash({key: build.get(key) for key in BUILD_IDENTITY_FIELDS}, omit=())


def replay_mock_qualification(
    root: pathlib.Path,
    build: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if build.get("model") != "mock-deterministic-v1" or build.get("profile_version") != PROFILE_ID:
        raise ValidationError("E-QUALIFICATION-RUNNER", "no qualification runner for extractor build")
    source_path = root / "fixtures/positive/minimal-local/pages/wiki-alpha-clean.html"
    source_bytes = source_path.read_bytes()
    artifact_id = artifact_id_for(source_bytes)
    parsed = parse_html(artifact_id, source_bytes, document_id="DOC-QUALIFICATION-FIXTURE")
    retrievals = {
        parsed["document"]["document_id"]: {
            "retrieval_id": "RET-QUALIFICATION-FIXTURE",
            "artifact_id": artifact_id,
        }
    }

    results = []
    for label, rel in zip(("A", "B"), SUITE_FILES[:2]):
        input_hash = file_sha(root / rel)
        context = json.loads((root / rel).read_text(encoding="utf-8"))
        execution_id = derived_id(
            "ExtractionRun",
            {
                "qualification": True,
                "extractor_build_hash": extractor_build_hash(build),
                "fixture_suite_hash": fixture_suite_hash(root),
                "run": label,
                "input_hash": input_hash,
            },
        ).replace("ERUN-", "QEXEC-", 1)
        claims = mock_extract(
            parsed["segments"],
            retrievals,
            extraction_run_id=execution_id,
            project_context=context,
        )
        results.append(qualification_result(execution_id=execution_id, input_hash=input_hash, claims=claims))
    return results[0], results[1]


def replay_mock_source_injection(
    root: pathlib.Path,
    build: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if build.get("model") != "mock-deterministic-v1" or build.get("profile_version") != PROFILE_ID:
        raise ValidationError("E-QUALIFICATION-RUNNER", "no qualification runner for extractor build")
    context_path = root / SUITE_FILES[0]
    context = json.loads(context_path.read_text(encoding="utf-8"))
    results = []
    for label, rel in (
        ("SOURCE-CLEAN", "fixtures/positive/minimal-local/pages/wiki-alpha-clean.html"),
        ("SOURCE-INJECTED", "fixtures/positive/minimal-local/pages/wiki-alpha.html"),
    ):
        source_bytes = (root / rel).read_bytes()
        artifact_id = artifact_id_for(source_bytes)
        document_id = f"DOC-QUALIFICATION-{label}"
        parsed = parse_html(artifact_id, source_bytes, document_id=document_id)
        claims = mock_extract(
            parsed["segments"],
            {document_id: {"retrieval_id": f"RET-QUALIFICATION-{label}", "artifact_id": artifact_id}},
            extraction_run_id=f"QEXEC-{label}",
            project_context=context,
        )
        results.append(
            qualification_result(
                execution_id=f"QEXEC-{label}",
                input_hash=file_sha(root / rel),
                claims=claims,
            )
        )
    return results[0], results[1]


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
        "repository_commit": build["repository_commit"],
        "source_tree_hash": build["source_tree_hash"],
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
    qualified_at: str,
    build: dict[str, Any],
) -> dict[str, Any]:
    run_a = root / "fixtures/positive/minimal-local/run-a.json"
    run_b = root / "fixtures/positive/minimal-local/run-b.json"
    if run_a.resolve() == run_b.resolve():
        raise ValidationError("E-RUN-PAIR", "run_a and run_b must be distinct")
    run_a_result, run_b_result = replay_mock_qualification(root, build)
    injection_ok = run_a_result["claim_set_hash"] == run_b_result["claim_set_hash"]
    source_clean_result, source_injected_result = replay_mock_source_injection(root, build)
    source_ok = source_clean_result["claim_set_hash"] == source_injected_result["claim_set_hash"]
    repro = "PASS" if run_a_result["claims"] and run_b_result["claims"] else "INCONCLUSIVE"
    adv = "PASS" if injection_ok else "FAIL"
    src = "PASS" if source_ok else "FAIL"
    result = "PASS" if adv == "PASS" and src == "PASS" and repro == "PASS" else ("INCONCLUSIVE" if repro != "PASS" else "FAIL")
    run_a_hash = file_sha(run_a)
    run_b_hash = file_sha(run_b)
    qualification = {
        "schema_version": SCHEMA_VERSION,
        "extractor_build_id": build["extractor_build_id"],
        "extractor_build_hash": extractor_build_hash(build),
        "fixture_suite_hash": fixture_suite_hash(root),
        "run_a": "fixtures/positive/minimal-local/run-a.json",
        "run_b": "fixtures/positive/minimal-local/run-b.json",
        "run_a_hash": run_a_hash,
        "run_b_hash": run_b_hash,
        "run_a_result": run_a_result,
        "run_b_result": run_b_result,
        "adversarial_project_expectation": adv,
        "source_content_injection": src,
        "reproducibility": repro,
        "result": result,
        "qualified_at": qualified_at,
    }
    qualification["qualification_run_id"] = derived_id("QualificationRun", qualification)
    return qualification
