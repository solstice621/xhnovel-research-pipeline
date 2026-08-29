#!/usr/bin/env python3
"""Attack-fixture tests: invalid evidence must not get a green light."""
from __future__ import annotations

import hashlib
import pathlib
import sys
import tempfile
import textwrap
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_evidence_yaml import (  # noqa: E402
    CheckError,
    KINDS_WITH_NO_TIER_CAP,
    SNIPPET_KINDS,
    check_tree,
)


def write_pack(research: pathlib.Path, rel: str = "packs/scene.md", text: str = "bundle\n") -> tuple[str, str]:
    path = research / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    repo_rel = path.relative_to(research.parent).as_posix()
    return repo_rel, hashlib.sha256(data).hexdigest()


def write_retrieval_file(research: pathlib.Path, scene: str, name: str, text: str) -> tuple[str, str]:
    path = research / "scenes" / scene / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(data)
    rel = path.relative_to(research.parent).as_posix()
    return rel, hashlib.sha256(data).hexdigest()


def write_run_pair(research: pathlib.Path, scene: str) -> dict[str, str]:
    d = research / "scenes" / scene
    d.mkdir(parents=True, exist_ok=True)
    repo = research.parent
    out: dict[str, str] = {}
    for key, name, body in (
        ("run_a", "run-a.txt", "RUN-A isolation transcript\n"),
        ("run_b", "run-b.txt", "RUN-B adversarial transcript\n"),
    ):
        path = d / name
        path.write_text(body, encoding="utf-8")
        out[key] = path.relative_to(repo).as_posix()
        out[f"{key}_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def write_scene(scenes: pathlib.Path, name: str, evidence: str, claims: str | None = None) -> None:
    d = scenes / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "evidence.yaml").write_text(textwrap.dedent(evidence), encoding="utf-8")
    if claims is not None:
        (d / "claims.yaml").write_text(textwrap.dedent(claims), encoding="utf-8")


def expect_ok(label: str, fn) -> None:
    try:
        fn()
    except CheckError:
        traceback.print_exc()
        raise SystemExit(f"FAIL test {label}: expected OK")


def expect_fail(label: str, fn, needle: str) -> None:
    try:
        fn()
    except CheckError as e:
        if needle not in str(e):
            raise SystemExit(f"FAIL test {label}: expected {needle!r} in {e}")
        return
    raise SystemExit(f"FAIL test {label}: expected CheckError")


UNQUAL_HEADER = """
            schema_version: "0.2"
            qualification_eligible: false
            qualification_credit: NONE
            adversarial_fixture: FAIL
            reproducibility: INCONCLUSIVE
            isolation_status: CURRENT
"""


def test_fake_qualified_empty_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-FAKE-QUAL",
            """
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: build-x
            adversarial_fixture: FAIL
            reproducibility: INCONCLUSIVE
            scene:
              scene_id: SCENE-FAKE-QUAL
            sources: []
            """,
            """
            scene_id: SCENE-FAKE-QUAL
            confirmed_count: 0
            live_original_fact_count: 0
            claims: []
            """,
        )
        expect_fail(
            "fake-qualified",
            lambda: check_tree(scenes, research_dir=research),
            "eligible_build_ids",
        )


def test_confirmed_unknown_retrieval_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-MISSING-RID",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            scene:
              scene_id: SCENE-MISSING-RID
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-MISSING-RID
            confirmed_count: 1
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: CONFIRMED
                text: forged
                retrieval_ids: [DOES-NOT-EXIST]
            """,
        )
        expect_fail(
            "missing-rid",
            lambda: check_tree(scenes, research_dir=research),
            "unknown retrieval_id",
        )


def test_confirmed_single_tier_d_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-TIER-D",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-SNIPPET]
            scene:
              scene_id: SCENE-TIER-D
            sources:
              - source_id: SRC-A
                platform: Search
                retrievals:
                  - retrieval_id: SRC-A-SNIPPET
                    access_kind: search_snippet
                    tier: D
                    excerpt: hello
            """,
            """
            scene_id: SCENE-TIER-D
            confirmed_count: 1
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: CONFIRMED
                text: from snippet
                retrieval_ids: [SRC-A-SNIPPET]
            """,
        )
        expect_fail(
            "tier-d-confirmed",
            lambda: check_tree(scenes, research_dir=research),
            "CONFIRMED does not meet",
        )


def test_legacy_allowlist_rejects_new_scene() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-LEGACY",
            """
            schema_version: "0.1-legacy"
            qualification_eligible: false
            qualification_credit: NONE
            scene:
              scene_id: SCENE-LEGACY
            sources:
              - source_id: SRC-02
                tier: B
                access_method: 搜索摘录
            """,
        )
        expect_fail(
            "legacy-impersonation",
            lambda: check_tree(scenes, research_dir=research),
            "allowlisted only",
        )


def test_legacy_allowlisted_scene001_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-2026-08-29-001",
            """
            schema_version: "0.1-legacy"
            qualification_eligible: false
            qualification_credit: NONE
            scene:
              scene_id: SCENE-2026-08-29-001
            sources:
              - source_id: SRC-02
                tier: B
                access_method: 搜索摘录
            """,
        )
        checked, eligible = check_tree(scenes, research_dir=research)
        assert checked == 1
        assert eligible == 0


def test_legal_two_independent_b_confirmed_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-LEGAL-CONFIRMED",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-PAGE, SRC-B-PAGE]
            scene:
              scene_id: SCENE-LEGAL-CONFIRMED
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
              - source_id: SRC-B
                platform: WikiB
                retrievals:
                  - retrieval_id: SRC-B-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-LEGAL-CONFIRMED
            confirmed_count: 1
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: CONFIRMED
                text: independent two-B confirmation
                retrieval_ids: [SRC-A-PAGE, SRC-B-PAGE]
            """,
        )
        checked, eligible = check_tree(scenes, research_dir=research)
        assert checked == 2
        assert eligible == 0


def test_same_platform_b_confirmed_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-SAME-B",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-01-PAGE, SRC-04-PAGE]
            scene:
              scene_id: SCENE-SAME-B
            sources:
              - source_id: SRC-01
                platform: 百度百科
                retrievals:
                  - retrieval_id: SRC-01-PAGE
                    access_kind: full_page
                    tier: B
              - source_id: SRC-04
                platform: 百度百科
                same_platform_as: SRC-01
                retrievals:
                  - retrieval_id: SRC-04-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-SAME-B
            confirmed_count: 1
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: CONFIRMED
                text: same platform
                retrieval_ids: [SRC-01-PAGE, SRC-04-PAGE]
            """,
        )
        expect_fail(
            "same-platform-b",
            lambda: check_tree(scenes, research_dir=research),
            "CONFIRMED does not meet",
        )


def test_scene_id_mismatch_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-ID-A",
            UNQUAL_HEADER
            + """
            scene:
              scene_id: SCENE-ID-A
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-ID-B
            confirmed_count: 0
            live_original_fact_count: 0
            claims: []
            """,
        )
        expect_fail(
            "scene-id-mismatch",
            lambda: check_tree(scenes, research_dir=research),
            "!= evidence scene_id",
        )


def test_live_original_fact_count_ignores_reception() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-RECEPTION",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            scene:
              scene_id: SCENE-RECEPTION
            sources:
              - source_id: SRC-A
                platform: Forum
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: C
            """,
            """
            scene_id: SCENE-RECEPTION
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: R-1
                effective_status: ACTIVE
                kind: RECEPTION
                grade: INFERRED
                text: readers remember a scene
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "reception-not-original-fact",
            lambda: check_tree(scenes, research_dir=research),
            "live_original_fact_count 1 != ACTIVE ORIGINAL_FACT count 0",
        )


def test_canonical_exclusion_generic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-EXCL",
            UNQUAL_HEADER
            + """
            canonical_exclusions:
              participants: ["墨阑"]
            scene:
              scene_id: SCENE-EXCL
              participants: 青鳞、墨阑
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-EXCL
            confirmed_count: 0
            live_original_fact_count: 0
            claims: []
            """,
        )
        expect_fail(
            "canonical-exclusion",
            lambda: check_tree(scenes, research_dir=research),
            "must not contain '墨阑'",
        )


def test_superseded_isolation_blocks_active_not_scene_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-ANY-SUPERSEDED",
            """
            schema_version: "0.2"
            qualification_eligible: false
            qualification_credit: NONE
            adversarial_fixture: FAIL
            reproducibility: INCONCLUSIVE
            isolation_status: SUPERSEDED
            scene:
              scene_id: SCENE-ANY-SUPERSEDED
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-ANY-SUPERSEDED
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: leftover
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "generic-superseded",
            lambda: check_tree(scenes, research_dir=research),
            "isolation_status is SUPERSEDED",
        )


def test_real_qualification_requires_manifest_hash_and_registry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        pack_rel, pack_hash = write_pack(research)
        runs = write_run_pair(research, "SCENE-QUAL-PASS")
        page_a, hash_a = write_retrieval_file(research, "SCENE-QUAL-PASS", "page-a.txt", "wiki A page\n")
        page_b, hash_b = write_retrieval_file(research, "SCENE-QUAL-PASS", "page-b.txt", "wiki B page\n")
        write_scene(
            scenes,
            "SCENE-QUAL-PASS",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE, SRC-B-PAGE]
            materials:
              file: {pack_rel}
              sha256: {pack_hash}
            run_manifest:
              model: test-model
              prompt: isolate
              parameters: {{temp: 0}}
              run_a: {runs["run_a"]}
              run_b: {runs["run_b"]}
              run_a_hash: {runs["run_a_hash"]}
              run_b_hash: {runs["run_b_hash"]}
            scene:
              scene_id: SCENE-QUAL-PASS
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    file: {page_a}
                    hash: {hash_a}
              - source_id: SRC-B
                platform: WikiB
                retrievals:
                  - retrieval_id: SRC-B-PAGE
                    access_kind: full_page
                    tier: B
                    file: {page_b}
                    hash: {hash_b}
            """,
            """
            scene_id: SCENE-QUAL-PASS
            confirmed_count: 1
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: CONFIRMED
                text: qualified two-B
                retrieval_ids: [SRC-A-PAGE, SRC-B-PAGE]
            """,
        )
        checked, eligible = check_tree(scenes, research_dir=research)
        assert checked == 2
        assert eligible == 1


def test_qualified_without_hashes_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        pack_rel, pack_hash = write_pack(research)
        runs = write_run_pair(research, "SCENE-QUAL-NOHASH")
        write_scene(
            scenes,
            "SCENE-QUAL-NOHASH",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: {pack_rel}
              sha256: {pack_hash}
            run_manifest:
              model: test-model
              prompt: isolate
              parameters: {{temp: 0}}
              run_a: {runs["run_a"]}
              run_b: {runs["run_b"]}
              run_a_hash: {runs["run_a_hash"]}
              run_b_hash: {runs["run_b_hash"]}
            scene:
              scene_id: SCENE-QUAL-NOHASH
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-QUAL-NOHASH
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: no hash
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "qual-no-hash",
            lambda: check_tree(scenes, research_dir=research),
            "missing hash",
        )


def test_qualified_empty_bundle_listed_in_registry_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- build-x\n",
            encoding="utf-8",
        )
        write_scene(
            scenes,
            "SCENE-EMPTY-QUAL",
            """
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: build-x
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            materials:
              file: /data/pack.md
            run_manifest:
              model: x
              prompt: y
              parameters: {}
              run_a_hash: a
              run_b_hash: b
            scene:
              scene_id: SCENE-EMPTY-QUAL
            sources: []
            """,
            """
            scene_id: SCENE-EMPTY-QUAL
            confirmed_count: 0
            live_original_fact_count: 0
            claims: []
            """,
        )
        expect_fail(
            "empty-qualified",
            lambda: check_tree(scenes, research_dir=research),
            "non-empty sources",
        )


def test_omitting_consumed_list_with_active_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-NO-CONSUMED",
            UNQUAL_HEADER
            + """
            scene:
              scene_id: SCENE-NO-CONSUMED
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
            """,
            """
            scene_id: SCENE-NO-CONSUMED
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: post-hoc bind
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "omit-consumed",
            lambda: check_tree(scenes, research_dir=research),
            "isolation_consumed_retrieval_ids",
        )


def test_supported_original_fact_tier_d_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-SUPPORTED-D",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-SNIPPET]
            scene:
              scene_id: SCENE-SUPPORTED-D
            sources:
              - source_id: SRC-A
                platform: Search
                retrievals:
                  - retrieval_id: SRC-A-SNIPPET
                    access_kind: search_snippet
                    tier: D
                    excerpt: hello
            """,
            """
            scene_id: SCENE-SUPPORTED-D
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: d-only supported
                retrieval_ids: [SRC-A-SNIPPET]
            """,
        )
        expect_fail(
            "supported-d",
            lambda: check_tree(scenes, research_dir=research),
            "SUPPORTED ORIGINAL_FACT requires a non-snippet Tier A or B",
        )


def test_qualified_missing_material_file_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        write_scene(
            scenes,
            "SCENE-QUAL-MISSING-PACK",
            """
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: packs/does-not-exist.md
              sha256: deadbeef
            run_manifest:
              model: x
              prompt: y
              parameters: {}
              run_a_hash: a
              run_b_hash: b
            scene:
              scene_id: SCENE-QUAL-MISSING-PACK
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    hash: hash-a
            """,
            """
            scene_id: SCENE-QUAL-MISSING-PACK
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: no pack
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "missing-pack",
            lambda: check_tree(scenes, research_dir=research),
            "does not exist on disk",
        )


def test_qualified_wrong_material_hash_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        pack_rel, _pack_hash = write_pack(research)
        write_scene(
            scenes,
            "SCENE-QUAL-BADHASH",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: {pack_rel}
              sha256: "{'0' * 64}"
            run_manifest:
              model: x
              prompt: y
              parameters: {{}}
              run_a_hash: a
              run_b_hash: b
            scene:
              scene_id: SCENE-QUAL-BADHASH
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    hash: hash-a
            """,
            """
            scene_id: SCENE-QUAL-BADHASH
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: bad hash
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "bad-pack-hash",
            lambda: check_tree(scenes, research_dir=research),
            "does not match",
        )


def test_qualified_absolute_hosts_material_fails() -> None:
    hosts = pathlib.Path("/etc/hosts")
    if not hosts.is_file():
        raise SystemExit("FAIL test hosts-material: /etc/hosts missing")
    hosts_hash = hashlib.sha256(hosts.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        runs = write_run_pair(research, "SCENE-QUAL-HOSTS")
        write_scene(
            scenes,
            "SCENE-QUAL-HOSTS",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: /etc/hosts
              sha256: {hosts_hash}
            run_manifest:
              model: test-model
              prompt: isolate
              parameters: {{temp: 0}}
              run_a: {runs["run_a"]}
              run_b: {runs["run_b"]}
              run_a_hash: {runs["run_a_hash"]}
              run_b_hash: {runs["run_b_hash"]}
            scene:
              scene_id: SCENE-QUAL-HOSTS
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    hash: hash-a
            """,
            """
            scene_id: SCENE-QUAL-HOSTS
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: hosts pack
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "hosts-material",
            lambda: check_tree(scenes, research_dir=research),
            "repository-relative path",
        )


def test_qualified_forged_run_hashes_fail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        pack_rel, pack_hash = write_pack(research)
        runs = write_run_pair(research, "SCENE-QUAL-FAKE-RUN")
        write_scene(
            scenes,
            "SCENE-QUAL-FAKE-RUN",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: {pack_rel}
              sha256: {pack_hash}
            run_manifest:
              model: test-model
              prompt: isolate
              parameters: {{temp: 0}}
              run_a: {runs["run_a"]}
              run_b: {runs["run_b"]}
              run_a_hash: aaaa
              run_b_hash: bbbb
            scene:
              scene_id: SCENE-QUAL-FAKE-RUN
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    hash: hash-a
            """,
            """
            scene_id: SCENE-QUAL-FAKE-RUN
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: fake run hashes
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "fake-run-hashes",
            lambda: check_tree(scenes, research_dir=research),
            "does not match",
        )


def test_qualified_absolute_run_a_fails() -> None:
    hosts = pathlib.Path("/etc/hosts")
    if not hosts.is_file():
        raise SystemExit("FAIL test abs-run-a: /etc/hosts missing")
    hosts_hash = hashlib.sha256(hosts.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        pack_rel, pack_hash = write_pack(research)
        runs = write_run_pair(research, "SCENE-QUAL-ABS-RUN")
        write_scene(
            scenes,
            "SCENE-QUAL-ABS-RUN",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: {pack_rel}
              sha256: {pack_hash}
            run_manifest:
              model: test-model
              prompt: isolate
              parameters: {{temp: 0}}
              run_a: /etc/hosts
              run_b: {runs["run_b"]}
              run_a_hash: {hosts_hash}
              run_b_hash: {runs["run_b_hash"]}
            scene:
              scene_id: SCENE-QUAL-ABS-RUN
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    hash: hash-a
            """,
            """
            scene_id: SCENE-QUAL-ABS-RUN
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: abs run_a
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "abs-run-a",
            lambda: check_tree(scenes, research_dir=research),
            "repository-relative path",
        )


def test_confirmed_reception_same_reddit_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-REDDIT-RECEPTION",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-R1-PAGE, SRC-R2-PAGE]
            scene:
              scene_id: SCENE-REDDIT-RECEPTION
            sources:
              - source_id: SRC-R1
                platform: Reddit
                retrievals:
                  - retrieval_id: SRC-R1-PAGE
                    access_kind: full_page
                    tier: C
              - source_id: SRC-R2
                platform: Reddit
                retrievals:
                  - retrieval_id: SRC-R2-PAGE
                    access_kind: full_page
                    tier: C
            """,
            """
            scene_id: SCENE-REDDIT-RECEPTION
            confirmed_count: 1
            live_original_fact_count: 0
            claims:
              - id: R-1
                effective_status: ACTIVE
                kind: RECEPTION
                grade: CONFIRMED
                text: two reddit threads are not two platforms
                retrieval_ids: [SRC-R1-PAGE, SRC-R2-PAGE]
            """,
        )
        expect_fail(
            "same-reddit-reception",
            lambda: check_tree(scenes, research_dir=research),
            "CONFIRMED does not meet",
        )


def test_confirmed_reception_same_platform_as_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-RECEPTION-ALIAS",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-R1-PAGE, SRC-OLD-PAGE]
            scene:
              scene_id: SCENE-RECEPTION-ALIAS
            sources:
              - source_id: SRC-R1
                platform: Reddit
                retrievals:
                  - retrieval_id: SRC-R1-PAGE
                    access_kind: full_page
                    tier: C
              - source_id: SRC-OLD
                platform: old.reddit.com
                same_platform_as: SRC-R1
                retrievals:
                  - retrieval_id: SRC-OLD-PAGE
                    access_kind: full_page
                    tier: C
            """,
            """
            scene_id: SCENE-RECEPTION-ALIAS
            confirmed_count: 1
            live_original_fact_count: 0
            claims:
              - id: R-1
                effective_status: ACTIVE
                kind: RECEPTION
                grade: CONFIRMED
                text: aliased reddit is not independent
                retrieval_ids: [SRC-R1-PAGE, SRC-OLD-PAGE]
            """,
        )
        expect_fail(
            "reception-same-platform-as",
            lambda: check_tree(scenes, research_dir=research),
            "CONFIRMED does not meet",
        )


def test_confirmed_reception_two_platforms_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-RECEPTION-OK",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-R1-PAGE, SRC-Z-PAGE]
            scene:
              scene_id: SCENE-RECEPTION-OK
            sources:
              - source_id: SRC-R1
                platform: Reddit
                retrievals:
                  - retrieval_id: SRC-R1-PAGE
                    access_kind: full_page
                    tier: C
              - source_id: SRC-Z
                platform: 知乎
                retrievals:
                  - retrieval_id: SRC-Z-PAGE
                    access_kind: full_page
                    tier: C
            """,
            """
            scene_id: SCENE-RECEPTION-OK
            confirmed_count: 1
            live_original_fact_count: 0
            claims:
              - id: R-1
                effective_status: ACTIVE
                kind: RECEPTION
                grade: CONFIRMED
                text: reddit plus zhihu
                retrieval_ids: [SRC-R1-PAGE, SRC-Z-PAGE]
            """,
        )
        checked, eligible = check_tree(scenes, research_dir=research)
        assert checked == 2
        assert eligible == 0


def test_qualified_placeholder_retrieval_hash_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        pack_rel, pack_hash = write_pack(research)
        runs = write_run_pair(research, "SCENE-QUAL-HASH-A")
        page_a, _hash_a = write_retrieval_file(research, "SCENE-QUAL-HASH-A", "page-a.txt", "wiki A page\n")
        write_scene(
            scenes,
            "SCENE-QUAL-HASH-A",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: {pack_rel}
              sha256: {pack_hash}
            run_manifest:
              model: test-model
              prompt: isolate
              parameters: {{temp: 0}}
              run_a: {runs["run_a"]}
              run_b: {runs["run_b"]}
              run_a_hash: {runs["run_a_hash"]}
              run_b_hash: {runs["run_b_hash"]}
            scene:
              scene_id: SCENE-QUAL-HASH-A
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    file: {page_a}
                    hash: hash-a
            """,
            """
            scene_id: SCENE-QUAL-HASH-A
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: placeholder hash
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "placeholder-retrieval-hash",
            lambda: check_tree(scenes, research_dir=research),
            "64-character hex SHA-256",
        )


def test_qualified_retrieval_hash_mismatch_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        research.mkdir(parents=True)
        (research / "qualification.md").write_text(
            "eligible_build_ids:\n- wf-test-001\n",
            encoding="utf-8",
        )
        pack_rel, pack_hash = write_pack(research)
        runs = write_run_pair(research, "SCENE-QUAL-PAGE-MISMATCH")
        page_a, _hash_a = write_retrieval_file(
            research, "SCENE-QUAL-PAGE-MISMATCH", "page-a.txt", "wiki A page\n"
        )
        bad_hash = "0" * 64
        write_scene(
            scenes,
            "SCENE-QUAL-PAGE-MISMATCH",
            f"""
            schema_version: "0.2"
            qualification_eligible: true
            qualification_credit: wf-test-001
            adversarial_fixture: PASS
            reproducibility: PASS
            isolation_status: CURRENT
            isolation_consumed_retrieval_ids: [SRC-A-PAGE]
            materials:
              file: {pack_rel}
              sha256: {pack_hash}
            run_manifest:
              model: test-model
              prompt: isolate
              parameters: {{temp: 0}}
              run_a: {runs["run_a"]}
              run_b: {runs["run_b"]}
              run_a_hash: {runs["run_a_hash"]}
              run_b_hash: {runs["run_b_hash"]}
            scene:
              scene_id: SCENE-QUAL-PAGE-MISMATCH
            sources:
              - source_id: SRC-A
                platform: WikiA
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: B
                    file: {page_a}
                    hash: "{bad_hash}"
            """,
            """
            scene_id: SCENE-QUAL-PAGE-MISMATCH
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: wrong page hash
                retrieval_ids: [SRC-A-PAGE]
            """,
        )
        expect_fail(
            "retrieval-hash-mismatch",
            lambda: check_tree(scenes, research_dir=research),
            "does not match",
        )


def test_confirmed_reception_case_variant_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-REDDIT-CASE",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-R1-PAGE, SRC-R2-PAGE]
            scene:
              scene_id: SCENE-REDDIT-CASE
            sources:
              - source_id: SRC-R1
                platform: Reddit
                retrievals:
                  - retrieval_id: SRC-R1-PAGE
                    access_kind: full_page
                    tier: C
              - source_id: SRC-R2
                platform: reddit
                retrievals:
                  - retrieval_id: SRC-R2-PAGE
                    access_kind: full_page
                    tier: C
            """,
            """
            scene_id: SCENE-REDDIT-CASE
            confirmed_count: 1
            live_original_fact_count: 0
            claims:
              - id: R-1
                effective_status: ACTIVE
                kind: RECEPTION
                grade: CONFIRMED
                text: Reddit vs reddit
                retrieval_ids: [SRC-R1-PAGE, SRC-R2-PAGE]
            """,
        )
        expect_fail(
            "reddit-case-variant",
            lambda: check_tree(scenes, research_dir=research),
            "CONFIRMED does not meet",
        )


def test_confirmed_reception_alias_chain_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-REDDIT-CHAIN",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-PAGE, SRC-C-PAGE]
            scene:
              scene_id: SCENE-REDDIT-CHAIN
            sources:
              - source_id: SRC-A
                platform: Reddit
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: C
              - source_id: SRC-B
                platform: old.reddit.com
                same_platform_as: SRC-A
                retrievals:
                  - retrieval_id: SRC-B-PAGE
                    access_kind: full_page
                    tier: C
              - source_id: SRC-C
                platform: www.reddit.com
                same_platform_as: SRC-B
                retrievals:
                  - retrieval_id: SRC-C-PAGE
                    access_kind: full_page
                    tier: C
            """,
            """
            scene_id: SCENE-REDDIT-CHAIN
            confirmed_count: 1
            live_original_fact_count: 0
            claims:
              - id: R-1
                effective_status: ACTIVE
                kind: RECEPTION
                grade: CONFIRMED
                text: A and C via B
                retrieval_ids: [SRC-A-PAGE, SRC-C-PAGE]
            """,
        )
        expect_fail(
            "reddit-alias-chain",
            lambda: check_tree(scenes, research_dir=research),
            "CONFIRMED does not meet",
        )


def test_same_platform_as_missing_source_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-ALIAS-MISSING",
            UNQUAL_HEADER
            + """
            scene:
              scene_id: SCENE-ALIAS-MISSING
            sources:
              - source_id: SRC-A
                platform: Reddit
                same_platform_as: SRC-MISSING
                retrievals:
                  - retrieval_id: SRC-A-PAGE
                    access_kind: full_page
                    tier: C
            """,
            """
            scene_id: SCENE-ALIAS-MISSING
            confirmed_count: 0
            live_original_fact_count: 0
            claims: []
            """,
        )
        expect_fail(
            "alias-missing",
            lambda: check_tree(scenes, research_dir=research),
            "does not exist",
        )


def test_search_snippet_case_variant_must_be_d() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-SNIPPET-VARIANT",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-SNIPPET]
            scene:
              scene_id: SCENE-SNIPPET-VARIANT
            sources:
              - source_id: SRC-A
                platform: Search
                retrievals:
                  - retrieval_id: SRC-A-SNIPPET
                    access_kind: Search_Snippet
                    tier: B
            """,
            """
            scene_id: SCENE-SNIPPET-VARIANT
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: cased snippet
                retrieval_ids: [SRC-A-SNIPPET]
            """,
        )
        expect_fail(
            "snippet-case-variant",
            lambda: check_tree(scenes, research_dir=research),
            "is a search snippet but tier='B'",
        )


def test_no_authorization_tier_cap_on_reprint_or_catalog() -> None:
    assert "unauthorized_reprint" in KINDS_WITH_NO_TIER_CAP
    assert "catalog_page" in KINDS_WITH_NO_TIER_CAP
    assert "unauthorized_reprint" not in SNIPPET_KINDS
    assert "catalog_page" not in SNIPPET_KINDS


def test_unauthorized_reprint_tier_a_confirmed_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-REPRINT-A",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-PIRATE-PAGE]
            scene:
              scene_id: SCENE-REPRINT-A
            sources:
              - source_id: SRC-PIRATE
                platform: 天涯书库
                retrievals:
                  - retrieval_id: SRC-PIRATE-PAGE
                    access_kind: unauthorized_reprint
                    tier: A
            """,
            """
            scene_id: SCENE-REPRINT-A
            confirmed_count: 1
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: CONFIRMED
                text: original chapter text on an unauthorized reprint site
                retrieval_ids: [SRC-PIRATE-PAGE]
            """,
        )
        checked, eligible = check_tree(scenes, research_dir=research)
        assert checked == 2
        assert eligible == 0


def test_catalog_page_not_forced_off_tier_a() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-CATALOG-A",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-CAT-PAGE]
            scene:
              scene_id: SCENE-CATALOG-A
            sources:
              - source_id: SRC-CAT
                platform: WebNovel
                retrievals:
                  - retrieval_id: SRC-CAT-PAGE
                    access_kind: catalog_page
                    tier: A
            """,
            """
            scene_id: SCENE-CATALOG-A
            confirmed_count: 1
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: CONFIRMED
                text: catalog kind is not a piracy gate
                retrieval_ids: [SRC-CAT-PAGE]
            """,
        )
        checked, eligible = check_tree(scenes, research_dir=research)
        assert checked == 2
        assert eligible == 0


def test_search_snippet_hyphen_variant_must_be_d() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        research = pathlib.Path(tmp) / "research"
        scenes = research / "scenes"
        write_scene(
            scenes,
            "SCENE-SNIPPET-HYPHEN",
            UNQUAL_HEADER
            + """
            isolation_consumed_retrieval_ids: [SRC-A-SNIPPET]
            scene:
              scene_id: SCENE-SNIPPET-HYPHEN
            sources:
              - source_id: SRC-A
                platform: Search
                retrievals:
                  - retrieval_id: SRC-A-SNIPPET
                    access_kind: search-snippet
                    tier: B
            """,
            """
            scene_id: SCENE-SNIPPET-HYPHEN
            confirmed_count: 0
            live_original_fact_count: 1
            claims:
              - id: F-1
                effective_status: ACTIVE
                kind: ORIGINAL_FACT
                grade: SUPPORTED
                text: hyphen snippet
                retrieval_ids: [SRC-A-SNIPPET]
            """,
        )
        expect_fail(
            "snippet-hyphen-variant",
            lambda: check_tree(scenes, research_dir=research),
            "is a search snippet but tier='B'",
        )


def main() -> None:
    test_fake_qualified_empty_fails()
    test_confirmed_unknown_retrieval_fails()
    test_confirmed_single_tier_d_fails()
    test_legacy_allowlist_rejects_new_scene()
    expect_ok("legacy-001", test_legacy_allowlisted_scene001_ok)
    expect_ok("legal-confirmed", test_legal_two_independent_b_confirmed_ok)
    test_same_platform_b_confirmed_fails()
    test_scene_id_mismatch_fails()
    test_live_original_fact_count_ignores_reception()
    test_canonical_exclusion_generic()
    test_superseded_isolation_blocks_active_not_scene_id()
    expect_ok("qual-pass", test_real_qualification_requires_manifest_hash_and_registry)
    test_qualified_without_hashes_fails()
    test_qualified_empty_bundle_listed_in_registry_fails()
    test_omitting_consumed_list_with_active_fails()
    test_supported_original_fact_tier_d_fails()
    test_qualified_missing_material_file_fails()
    test_qualified_wrong_material_hash_fails()
    test_qualified_absolute_hosts_material_fails()
    test_qualified_forged_run_hashes_fail()
    test_qualified_absolute_run_a_fails()
    test_confirmed_reception_same_reddit_fails()
    test_confirmed_reception_same_platform_as_fails()
    expect_ok("reception-two-platforms", test_confirmed_reception_two_platforms_ok)
    test_qualified_placeholder_retrieval_hash_fails()
    test_qualified_retrieval_hash_mismatch_fails()
    test_confirmed_reception_case_variant_fails()
    test_confirmed_reception_alias_chain_fails()
    test_same_platform_as_missing_source_fails()
    test_search_snippet_case_variant_must_be_d()
    test_search_snippet_hyphen_variant_must_be_d()
    test_no_authorization_tier_cap_on_reprint_or_catalog()
    expect_ok("reprint-tier-a", test_unauthorized_reprint_tier_a_confirmed_ok)
    expect_ok("catalog-tier-a", test_catalog_page_not_forced_off_tier_a)
    print("OK: check_evidence_yaml self-tests passed")


if __name__ == "__main__":
    main()
