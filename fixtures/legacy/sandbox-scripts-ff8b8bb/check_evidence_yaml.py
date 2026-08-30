#!/usr/bin/env python3
"""Enforce research evidence contracts. Green means the contract held, not that YAML parsed.

Install: pip install -r research/scripts/requirements.txt
Run from repo root: python3 research/scripts/check_evidence_yaml.py
Self-test: python3 research/scripts/test_check_evidence_yaml.py
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENES = ROOT / "research" / "scenes"
RESEARCH_DIR = ROOT / "research"

CURRENT_SCHEMA = "0.2"
LEGACY_SCHEMA = "0.1-legacy"
# Frozen. New scenes may not opt into 0.1-legacy.
LEGACY_ALLOWLIST = {
    "SCENE-2026-08-29-001": "SCENE-2026-08-29-001",
}
LIVE_STATUSES = {"ACTIVE"}
DEAD_STATUSES = {"LEGACY_UNRESOLVED", "ARCHIVED", "SUPERSEDED"}
ACCESS_KIND_ALIASES = {
    "full_page": "full_page",
    "fullpage": "full_page",
    "全文": "full_page",
    "search_snippet": "search_snippet",
    "searchsnippet": "search_snippet",
    "search_excerpt": "search_excerpt",
    "searchexcerpt": "search_excerpt",
    "搜索摘录": "search_snippet",
    "搜索摘要": "search_snippet",
    "licensed_teaser": "licensed_teaser",
    "licensedteaser": "licensed_teaser",
    "catalog_page": "catalog_page",
    "catalogpage": "catalog_page",
    "unauthorized_reprint": "unauthorized_reprint",
    "unauthorizedreprint": "unauthorized_reprint",
}
ALLOWED_ACCESS_KINDS = set(ACCESS_KIND_ALIASES.values())
# User decision 2026-08-29: no authorization-based tier cap. Only search
# snippets/excerpts are forced to D. unauthorized_reprint and catalog_page
# must not be forced to D or banned from A.
SNIPPET_KINDS = {"search_snippet", "search_excerpt"}
KINDS_WITH_NO_TIER_CAP = ALLOWED_ACCESS_KINDS - SNIPPET_KINDS
SEARCH_ACCESS_MARKERS = ("搜索摘录", "搜索摘要", "search_snippet", "search_excerpt")
MANIFEST_KEYS = ("model", "prompt", "parameters", "run_a", "run_b", "run_a_hash", "run_b_hash")
CREDIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CheckError(Exception):
    """Contract violation. Distinct from YAML parse errors."""


def fail(msg: str) -> None:
    raise CheckError(msg)


def load_yaml(path: pathlib.Path):
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        fail(f"{path}: YAML parse error: {e}")


def schema_of(data: dict, path: pathlib.Path) -> str:
    v = data.get("schema_version")
    if v is None:
        fail(
            f"{path}: missing schema_version. "
            f"Current contract is {CURRENT_SCHEMA!r}; unmigrated PILOT files must set {LEGACY_SCHEMA!r}."
        )
    return str(v)


def qualification_eligible(data: dict) -> bool:
    if "qualification_eligible" not in data:
        fail("must declare qualification_eligible at document root")
    return bool(data.get("qualification_eligible"))


def qualification_credit(data: dict):
    if data.get("qualification_credit") is not None:
        return data.get("qualification_credit")
    corr = data.get("correction_2026_08_29") or {}
    return corr.get("qualification_credit")


def fixture_fields(data: dict) -> dict:
    corr = data.get("correction_2026_08_29") or {}
    return {
        "adversarial_fixture": data.get("adversarial_fixture", corr.get("adversarial_fixture")),
        "reproducibility": data.get("reproducibility", corr.get("reproducibility")),
        "qualification_credit": qualification_credit(data),
    }


def normalize_access_kind(kind: object) -> str:
    text = str(kind or "").strip()
    if not text:
        return ""
    underscored = re.sub(r"[\s.\-]+", "_", text)
    compact = underscored.replace("_", "")
    for key in (text, underscored, underscored.casefold(), compact.casefold(), text.casefold()):
        if key in ACCESS_KIND_ALIASES:
            return ACCESS_KIND_ALIASES[key]
    return underscored.casefold()


def normalize_platform(platform: object) -> str:
    return str(platform or "").strip().casefold()


def is_snippet_retrieval(ret: dict) -> bool:
    kind = normalize_access_kind(ret.get("access_kind") or ret.get("access_method") or "")
    if kind in SNIPPET_KINDS:
        return True
    raw = str(ret.get("access_kind") or ret.get("access_method") or "")
    return any(m in raw for m in SEARCH_ACCESS_MARKERS)


def platform_equivalence(path: pathlib.Path, sources: list) -> dict[str, str]:
    """source_id → equivalence-class root. same_platform_as is transitive; platform names are case-insensitive."""
    by_id: dict[str, dict] = {}
    for src in sources:
        if not isinstance(src, dict):
            continue
        sid = src.get("source_id")
        if not sid:
            fail(f"{path}: source missing source_id")
        if sid in by_id:
            fail(f"{path}: duplicate source_id {sid}")
        by_id[sid] = src
    parent = {sid: sid for sid in by_id}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for sid, src in by_id.items():
        alias = src.get("same_platform_as")
        if alias in (None, ""):
            continue
        if not isinstance(alias, str):
            fail(f"{path}: {sid} same_platform_as must be a source_id string")
        if alias not in by_id:
            fail(f"{path}: {sid} same_platform_as {alias!r} does not exist")
        union(sid, alias)
    seen_platform: dict[str, str] = {}
    for sid, src in by_id.items():
        plat = normalize_platform(src.get("platform"))
        if not plat:
            continue
        if plat in seen_platform:
            union(sid, seen_platform[plat])
        else:
            seen_platform[plat] = sid
    return {sid: find(sid) for sid in parent}


def iter_retrieval_entries(data: dict):
    for src in data.get("sources") or []:
        if not isinstance(src, dict):
            continue
        for ret in src.get("retrievals") or []:
            if isinstance(ret, dict):
                yield src, ret


def retrieval_index(data: dict, classes: dict[str, str] | None = None) -> dict[str, dict]:
    index = {}
    classes = classes or {}
    for src, ret in iter_retrieval_entries(data):
        rid = ret.get("retrieval_id")
        if not rid:
            continue
        if rid in index:
            fail(f"duplicate retrieval_id {rid}")
        sid = src.get("source_id")
        index[rid] = {
            "retrieval": ret,
            "source": src,
            "tier": ret.get("tier"),
            "platform": src.get("platform"),
            "source_id": sid,
            "same_platform_as": src.get("same_platform_as"),
            "platform_class": classes.get(sid),
            "post_isolation": bool(src.get("post_isolation") or ret.get("post_isolation")),
            "hash": ret.get("hash"),
            "file": ret.get("file"),
            "access_kind": normalize_access_kind(ret.get("access_kind") or ret.get("access_method") or ""),
        }
    return index


def load_eligible_build_ids(research_dir: pathlib.Path) -> list:
    path = research_dir / "qualification.md"
    if not path.exists():
        return []
    loaded = load_yaml(path)
    if not isinstance(loaded, dict):
        fail(f"{path}: must be a YAML mapping with eligible_build_ids")
    ids = loaded.get("eligible_build_ids")
    if ids is None:
        fail(f"{path}: missing eligible_build_ids")
    if not isinstance(ids, list):
        fail(f"{path}: eligible_build_ids must be a list")
    return ids


def check_legacy_scene(path: pathlib.Path, data: dict) -> None:
    scene_id = (data.get("scene") or {}).get("scene_id")
    allowed_dir = LEGACY_ALLOWLIST.get(scene_id)
    if allowed_dir is None:
        fail(
            f"{path}: schema {LEGACY_SCHEMA} is allowlisted only for "
            f"{sorted(LEGACY_ALLOWLIST)}; got scene_id={scene_id!r}"
        )
    if path.parent.name != allowed_dir:
        fail(f"{path}: legacy scene_id {scene_id} must live in directory {allowed_dir}")
    if qualification_eligible(data):
        fail(f"{path}: schema {LEGACY_SCHEMA} cannot be qualification_eligible")
    credit = qualification_credit(data)
    if credit not in (None, "NONE"):
        fail(f"{path}: legacy scene qualification_credit must be NONE, got {credit!r}")
    claims_path = path.parent / "claims.yaml"
    if claims_path.exists():
        fail(
            f"{path}: {LEGACY_SCHEMA} must not present claims.yaml as a 0.2 live table; "
            "migrate to 0.2 or keep unqualified without a live claims contract"
        )
    sources = data.get("sources") or []
    if not isinstance(sources, list):
        fail(f"{path}: sources must be a list")
    for src in sources:
        if not isinstance(src, dict):
            fail(f"{path}: source entries must be mappings")
        if src.get("retrievals"):
            fail(
                f"{path}: {src.get('source_id')} has retrievals[] but schema is {LEGACY_SCHEMA}; "
                f"set schema_version: {CURRENT_SCHEMA!r} if this bundle has been migrated"
            )


def check_current_sources(path: pathlib.Path, data: dict) -> dict[str, dict]:
    sources = data.get("sources")
    if sources is None or not isinstance(sources, list):
        fail(f"{path}: 0.2 sources must be a list")
    classes = platform_equivalence(path, sources)
    for src in sources:
        if not isinstance(src, dict):
            fail(f"{path}: source entries must be mappings")
        sid = src.get("source_id")
        retrievals = src.get("retrievals")
        if retrievals is None:
            fail(
                f"{path}: {sid} missing retrievals[] "
                f"(0.2 contract; unmigrated files must set schema_version: {LEGACY_SCHEMA!r})"
            )
        if not retrievals:
            fail(f"{path}: {sid} retrievals[] is empty")
        access = str(src.get("access_method") or src.get("access_kind") or "")
        if "+" in access or ("全文" in access and "摘录" in access):
            fail(f"{path}: {sid} still mixes full page and snippet on the parent source")
        parent_kind = normalize_access_kind(src.get("access_method") or "")
        if parent_kind in SNIPPET_KINDS and src.get("tier") in {"A", "B", "C"}:
            fail(f"{path}: {sid} parent still tags search excerpt as tier {src.get('tier')} (must be retrieval-level D)")
        for ret in retrievals:
            if not isinstance(ret, dict):
                fail(f"{path}: {sid} retrievals[] entries must be mappings")
            rid = ret.get("retrieval_id")
            if not rid:
                fail(f"{path}: {sid} retrieval missing retrieval_id")
            raw_kind = ret.get("access_kind")
            if not raw_kind:
                fail(f"{path}: {rid} missing access_kind")
            kind = normalize_access_kind(raw_kind)
            if kind not in ALLOWED_ACCESS_KINDS:
                fail(f"{path}: {rid} unknown access_kind {raw_kind!r} (normalized {kind!r})")
            tier = ret.get("tier")
            # Kind-level tier force is snippet-only. Do not add pirate-site or
            # catalog_page caps here (2026-08-29: 不对盗版网站做任何限制).
            if kind in SNIPPET_KINDS and tier != "D":
                fail(f"{path}: {rid} is a search snippet but tier={tier!r} (must be D)")
    return retrieval_index(data, classes)


def check_split_verdict(path: pathlib.Path, data: dict) -> None:
    if data.get("verdict") in {"FAIL/INCONCLUSIVE", "FAIL / INCONCLUSIVE"}:
        fail(f"{path}: split verdict into adversarial_fixture / reproducibility / qualification_credit")
    fields = fixture_fields(data)
    for key in ("adversarial_fixture", "reproducibility", "qualification_credit"):
        if fields[key] is None:
            fail(f"{path}: 0.2 scene missing {key}")


def check_canonical_contract(path: pathlib.Path, scene: dict, data: dict) -> None:
    timeline = scene.get("timeline")
    if isinstance(timeline, dict) and timeline.get("status") == "CONFLICTING":
        branches = timeline.get("branches")
        if not isinstance(branches, dict) or len(branches) < 2:
            fail(f"{path}: CONFLICTING timeline must declare at least two scene.timeline.branches")
        canon = f"{scene.get('participants') or ''}{scene.get('key_decision') or ''}"
        for _name, branch in branches.items():
            if not isinstance(branch, dict):
                continue
            if branch.get("not_canonical_scene_definition") or branch.get("role") == "branch_lead_only":
                for token in branch.get("canonical_forbidden_tokens") or []:
                    if token and token in canon:
                        fail(f"{path}: D-lead token {token!r} must not appear in participants/key_decision")
    exclusions = data.get("canonical_exclusions") or scene.get("canonical_exclusions") or {}
    if not isinstance(exclusions, dict):
        fail(f"{path}: canonical_exclusions must be a mapping")
    for field, needles in exclusions.items():
        text = str(scene.get(field) or "")
        for needle in needles or []:
            if needle and needle in text:
                fail(f"{path}: canonical field {field} must not contain {needle!r}")


def usable_original_metas(metas: list[dict]) -> list[dict]:
    return [m for m in metas if not is_snippet_retrieval(m["retrieval"]) and m["tier"] != "D"]


def supported_original_fact_ok(metas: list[dict]) -> bool:
    return any(m["tier"] in {"A", "B"} for m in usable_original_metas(metas))


def supported_reception_ok(metas: list[dict]) -> bool:
    return any(m["tier"] == "C" and not is_snippet_retrieval(m["retrieval"]) for m in metas)


def file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_repo_relative_file(file_str: object, repo_root: pathlib.Path, *, field: str) -> pathlib.Path:
    """Resolve a path that must stay inside repo_root. Absolute paths and '..' are rejected."""
    if not isinstance(file_str, str) or not file_str.strip():
        fail(f"{field} must be a repository-relative path inside the repo (no absolute paths or '..')")
    text = file_str.strip().replace("\\", "/")
    posix = pathlib.PurePosixPath(text)
    if posix.is_absolute() or pathlib.Path(text).is_absolute() or text.startswith(("/", "~")):
        fail(f"{field} {file_str!r} must be a repository-relative path inside the repo (no absolute paths or '..')")
    first = posix.parts[0] if posix.parts else ""
    if ".." in posix.parts or ":" in first:
        fail(f"{field} {file_str!r} must be a repository-relative path inside the repo (no absolute paths or '..')")
    full = (repo_root / text).resolve()
    root = repo_root.resolve()
    try:
        full.relative_to(root)
    except ValueError:
        fail(f"{field} {file_str!r} resolves outside the repository")
    if not full.is_file():
        fail(f"{field} {file_str!r} does not exist on disk")
    return full


def check_qualification_gate(
    path: pathlib.Path,
    data: dict,
    claims: dict,
    research_dir: pathlib.Path,
    index: dict[str, dict],
) -> None:
    if not qualification_eligible(data):
        return
    fields = fixture_fields(data)
    credit = fields["qualification_credit"]
    if not credit or credit == "NONE":
        fail(f"{path}: qualification_eligible true requires a precise build id, not NONE")
    if not isinstance(credit, str) or not CREDIT_RE.match(credit):
        fail(f"{path}: qualification_credit {credit!r} is not a precise build id")
    eligible_ids = load_eligible_build_ids(research_dir)
    if credit not in eligible_ids:
        fail(
            f"{path}: qualification_credit {credit!r} is not in "
            f"{research_dir / 'qualification.md'} eligible_build_ids "
            "(missing file means no qualified builds)"
        )
    if fields["adversarial_fixture"] != "PASS":
        fail(f"{path}: qualification_eligible true requires adversarial_fixture: PASS")
    if fields["reproducibility"] != "PASS":
        fail(f"{path}: qualification_eligible true requires reproducibility: PASS (hash/manifest complete)")
    sources = data.get("sources") or []
    if not sources:
        fail(f"{path}: qualification_eligible true requires non-empty sources")
    if not index:
        fail(f"{path}: qualification_eligible true requires at least one retrieval")
    if not (claims.get("claims") or []):
        fail(f"{path}: qualification_eligible true requires a non-empty claims list")
    materials = data.get("materials") or {}
    material_file = materials.get("file")
    declared_hash = str(materials.get("sha256") or "").strip().lower()
    if not declared_hash:
        fail(f"{path}: qualification_eligible true requires materials.sha256")
    repo_root = research_dir.resolve().parent
    pack = resolve_repo_relative_file(material_file, repo_root, field=f"{path}: materials.file")
    actual_hash = file_sha256(pack)
    if actual_hash != declared_hash:
        fail(
            f"{path}: materials.sha256 {declared_hash} does not match "
            f"{pack} ({actual_hash})"
        )
    manifest = data.get("run_manifest") or {}
    if not isinstance(manifest, dict):
        fail(f"{path}: qualification_eligible true requires run_manifest mapping")
    for key in MANIFEST_KEYS:
        if key not in manifest or manifest[key] in (None, ""):
            fail(f"{path}: run_manifest missing {key}")
    run_a_path = resolve_repo_relative_file(
        manifest.get("run_a"), repo_root, field=f"{path}: run_manifest.run_a"
    )
    run_b_path = resolve_repo_relative_file(
        manifest.get("run_b"), repo_root, field=f"{path}: run_manifest.run_b"
    )
    if run_a_path == run_b_path:
        fail(f"{path}: run_manifest.run_a and run_b must be distinct files")
    declared_a = str(manifest.get("run_a_hash") or "").strip().lower()
    declared_b = str(manifest.get("run_b_hash") or "").strip().lower()
    actual_a = file_sha256(run_a_path)
    actual_b = file_sha256(run_b_path)
    if actual_a != declared_a:
        fail(
            f"{path}: run_manifest.run_a_hash {declared_a} does not match "
            f"{run_a_path} ({actual_a})"
        )
    if actual_b != declared_b:
        fail(
            f"{path}: run_manifest.run_b_hash {declared_b} does not match "
            f"{run_b_path} ({actual_b})"
        )
    for rid, meta in index.items():
        ret = meta["retrieval"]
        declared = str(ret.get("hash") or "").strip().lower()
        if not declared:
            fail(f"{path}: retrieval {rid} missing hash (required for qualified reproducibility)")
        if not SHA256_RE.fullmatch(declared):
            fail(
                f"{path}: retrieval {rid} hash must be a 64-character hex SHA-256 of a saved page file, "
                f"not {declared!r}"
            )
        page = resolve_repo_relative_file(
            ret.get("file"), repo_root, field=f"{path}: retrieval {rid} file"
        )
        actual = file_sha256(page)
        if actual != declared:
            fail(
                f"{path}: retrieval {rid} hash {declared} does not match "
                f"{page} ({actual})"
            )


def independent_tier_cross_platform(metas: list[dict], tier: str) -> bool:
    """True iff two non-snippet retrievals of `tier` belong to distinct platform equivalence classes."""
    items = [m for m in metas if m["tier"] == tier and not is_snippet_retrieval(m["retrieval"])]
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            if a.get("source_id") == b.get("source_id"):
                continue
            if not normalize_platform(a.get("platform")) or not normalize_platform(b.get("platform")):
                continue
            if a.get("platform_class") is None or b.get("platform_class") is None:
                continue
            if a.get("platform_class") == b.get("platform_class"):
                continue
            return True
    return False


def independent_original_fact_bs(metas: list[dict]) -> bool:
    return independent_tier_cross_platform(metas, "B")


def confirmed_ok(kind: str, metas: list[dict]) -> bool:
    if any(is_snippet_retrieval(m["retrieval"]) or m["tier"] == "D" for m in metas) and kind == "ORIGINAL_FACT":
        # D/snippet may sit beside stronger retrievals; they do not count. Strip them.
        metas = [m for m in metas if not is_snippet_retrieval(m["retrieval"]) and m["tier"] != "D"]
    if not metas:
        return False
    if kind == "ORIGINAL_FACT":
        if any(m["tier"] == "A" and not is_snippet_retrieval(m["retrieval"]) for m in metas):
            return True
        return independent_original_fact_bs(metas)
    if kind == "RECEPTION":
        return independent_tier_cross_platform(metas, "C")
    return False


def check_claims(
    path: pathlib.Path,
    data: dict,
    evidence: dict,
    index: dict[str, dict],
) -> tuple[int, int]:
    """Return (live ORIGINAL_FACT count, live CONFIRMED count)."""
    if not isinstance(data, dict):
        fail(f"{path}: root must be a mapping")
    ev_scene_id = (evidence.get("scene") or {}).get("scene_id")
    if data.get("scene_id") != ev_scene_id:
        fail(f"{path}: scene_id {data.get('scene_id')!r} != evidence scene_id {ev_scene_id!r}")
    isolation = evidence.get("isolation_status")
    live_rows = [
        c
        for c in (data.get("claims") or [])
        if isinstance(c, dict) and c.get("effective_status") in LIVE_STATUSES
    ]
    if live_rows and isolation == "SUPERSEDED":
        fail(
            f"{path}: ACTIVE FactClaims are forbidden while isolation_status is SUPERSEDED; "
            "re-run isolation before promoting rows"
        )
    consumed: set[str] = set()
    if live_rows:
        consumed_raw = evidence.get("isolation_consumed_retrieval_ids")
        if not isinstance(consumed_raw, list) or not consumed_raw:
            fail(
                f"{path}: ACTIVE FactClaims require non-empty isolation_consumed_retrieval_ids "
                "(omitting the field is not a skip)"
            )
        consumed = {str(x) for x in consumed_raw}
        for rid in consumed:
            if rid not in index:
                fail(f"{path}: isolation_consumed_retrieval_ids cites unknown retrieval_id {rid!r}")
            if index[rid]["post_isolation"]:
                fail(f"{path}: isolation_consumed_retrieval_ids includes post_isolation {rid}")
    live_facts = 0
    live_receptions = 0
    live_confirmed = 0

    for claim in data.get("claims") or []:
        if not isinstance(claim, dict):
            fail(f"{path}: claim entries must be mappings")
        cid = claim.get("id")
        status = claim.get("effective_status")
        if status not in LIVE_STATUSES | DEAD_STATUSES:
            fail(f"{path}: claim {cid} unknown effective_status {status!r}")
        kind = claim.get("kind")
        grade = claim.get("grade")
        binding = claim.get("retrieval_binding")
        rids = list(claim.get("retrieval_ids") or [])

        if status in LIVE_STATUSES:
            if binding == "POST_HOC_UNVERIFIED":
                fail(f"{path}: claim {cid} is ACTIVE with POST_HOC_UNVERIFIED retrieval binding")
            if not rids:
                fail(f"{path}: claim {cid} missing retrieval_ids")
            metas = []
            for rid in rids:
                if rid not in index:
                    fail(f"{path}: claim {cid} cites unknown retrieval_id {rid!r}")
                meta = index[rid]
                if meta["post_isolation"]:
                    fail(f"{path}: claim {cid} cites post_isolation retrieval {rid}")
                if rid not in consumed:
                    fail(f"{path}: claim {cid} cites {rid} which isolation_consumed_retrieval_ids did not consume")
                metas.append(meta)
            if kind == "ORIGINAL_FACT":
                live_facts += 1
                if grade == "SUPPORTED" and not supported_original_fact_ok(metas):
                    fail(
                        f"{path}: claim {cid} SUPPORTED ORIGINAL_FACT requires a non-snippet Tier A or B; "
                        "Tier D / search snippets are not enough for convergence"
                    )
            elif kind == "RECEPTION":
                live_receptions += 1
                if grade == "SUPPORTED" and not supported_reception_ok(metas):
                    fail(f"{path}: claim {cid} SUPPORTED RECEPTION requires a non-snippet Tier C")
            if grade == "CONFIRMED":
                if kind not in {"ORIGINAL_FACT", "RECEPTION"}:
                    fail(f"{path}: claim {cid} CONFIRMED with non-fact kind {kind!r}")
                if not confirmed_ok(kind, metas):
                    fail(
                        f"{path}: claim {cid} CONFIRMED does not meet Tier A or "
                        ">=2 independent Tier B (ORIGINAL_FACT) / independent Tier C (RECEPTION)"
                    )
                live_confirmed += 1
        else:
            if grade == "CONFIRMED":
                fail(
                    f"{path}: claim {cid} is {status} and still CONFIRMED; "
                    "revoke CONFIRMED on unresolved/archived historical rows"
                )

    declared_live = data.get("live_original_fact_count")
    if declared_live is not None and declared_live != live_facts:
        fail(f"{path}: live_original_fact_count {declared_live} != ACTIVE ORIGINAL_FACT count {live_facts}")
    declared_reception = data.get("live_reception_count")
    if declared_reception is not None and declared_reception != live_receptions:
        fail(f"{path}: live_reception_count {declared_reception} != ACTIVE RECEPTION count {live_receptions}")
    declared_confirmed = data.get("confirmed_count")
    if declared_confirmed is not None and declared_confirmed != live_confirmed:
        fail(f"{path}: confirmed_count {declared_confirmed} != live CONFIRMED {live_confirmed}")

    for note in data.get("researcher_notes") or []:
        if not isinstance(note, dict):
            fail(f"{path}: researcher_notes entries must be mappings")
        if note.get("kind") in {"ORIGINAL_FACT", "RECEPTION"}:
            fail(f"{path}: researcher_note {note.get('id')} must not be a FactClaim kind")
        if note.get("grade") == "CONFIRMED":
            fail(f"{path}: researcher_note {note.get('id')} must not be CONFIRMED")

    return live_facts, live_confirmed


def check_generated_scene_facts(scene_dir: pathlib.Path, evidence: dict, claims: dict) -> None:
    from generate_scene_facts import render_scene_facts

    facts_path = scene_dir / "scene-facts.md"
    if not facts_path.exists():
        fail(f"{facts_path}: schema {CURRENT_SCHEMA} requires generated scene-facts.md")
    expected = render_scene_facts(evidence, claims)
    actual = facts_path.read_text(encoding="utf-8")
    if actual != expected:
        fail(
            f"{facts_path}: does not match generate_scene_facts.py output. "
            "Run python3 research/scripts/generate_scene_facts.py"
        )


def check_tree(
    scenes: pathlib.Path,
    *,
    research_dir: pathlib.Path | None = None,
    require_generated_facts: bool = False,
) -> tuple[int, int]:
    """Return (files_checked, qualification_eligible_count). Raises CheckError."""
    research_dir = research_dir or scenes.parent
    if not scenes.exists():
        fail(f"missing {scenes}")
    checked = 0
    eligible_count = 0
    for evidence_path in sorted(scenes.glob("*/evidence.yaml")):
        data = load_yaml(evidence_path)
        schema = schema_of(data, evidence_path)
        scene = data.get("scene") or {}
        scene_id = scene.get("scene_id")
        if scene_id and evidence_path.parent.name != scene_id:
            fail(f"{evidence_path}: directory name {evidence_path.parent.name} != scene_id {scene_id}")
        if schema == LEGACY_SCHEMA:
            check_legacy_scene(evidence_path, data)
            checked += 1
            continue
        if schema != CURRENT_SCHEMA:
            fail(f"{evidence_path}: unknown schema_version {schema!r}")
        qualification_eligible(data)
        check_split_verdict(evidence_path, data)
        if not isinstance(scene, dict):
            fail(f"{evidence_path}: scene must be a mapping")
        check_canonical_contract(evidence_path, scene, data)
        index = check_current_sources(evidence_path, data)
        claims_path = evidence_path.parent / "claims.yaml"
        if not claims_path.exists():
            fail(f"{evidence_path}: schema {CURRENT_SCHEMA} requires claims.yaml")
        claims = load_yaml(claims_path)
        check_claims(claims_path, claims, data, index)
        check_qualification_gate(evidence_path, data, claims, research_dir, index)
        checked += 2
        if qualification_eligible(data):
            eligible_count += 1
        facts_path = evidence_path.parent / "scene-facts.md"
        if require_generated_facts or facts_path.exists():
            check_generated_scene_facts(evidence_path.parent, data, claims)
    if checked == 0:
        fail("no evidence.yaml found")
    return checked, eligible_count


def main() -> None:
    try:
        checked, eligible_count = check_tree(SCENES, research_dir=RESEARCH_DIR, require_generated_facts=True)
    except CheckError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    print(
        f"OK: parsed and checked {checked} YAML files; "
        f"qualification_eligible={eligible_count}"
    )


if __name__ == "__main__":
    main()
