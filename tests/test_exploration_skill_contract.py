"""Contract guards for the Phase 0 open-world exploration Skill.

These tests bind the host-facing workflow to the shipped CLI and repository trust
boundaries.  They deliberately avoid testing headings or cosmetic prose.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from xhnovel_pipeline.catalog import ID_FIELDS
from xhnovel_pipeline.paths import repo_root


ROOT = repo_root()
CANONICAL_ROOT = ROOT / ".agents" / "skills"
MIRROR_ROOT = ROOT / ".claude" / "skills"
SKILL = CANONICAL_ROOT / "xhnovel-explore" / "SKILL.md"
PLAN_SKILL = CANONICAL_ROOT / "xhnovel-plan" / "SKILL.md"
DOC = ROOT / "docs" / "PHASE0_EXPLORATION.md"

PHASE0_KINDS = {
    "ExplorationBrief",
    "ResearchLead",
    "HandoffBuildRequest",
    "SourceDeclaration",
    "OperatorAttestation",
    "EvidenceHandoff",
    "HandoffAttemptEvent",
    "EvidenceHandoffExecutionReceipt",
}
PHASE_MINUS1_KINDS = {
    "ResearchIntake",
    "NeutralPlanningInput",
    "NeutralPlanningExecution",
    "NeutralResearchFrame",
    "ExplorationPlan",
    "ExplorationPlanCompileRequest",
    "PlanningCompilationReceipt",
}


def _cli_source() -> str:
    return (ROOT / "src" / "xhnovel_pipeline" / "cli.py").read_text(encoding="utf-8")


def _real_flags() -> set[str]:
    return set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', _cli_source()))


def _real_subcommands() -> set[str]:
    return set(re.findall(r'add_parser\(\s*"([a-z0-9-]+)"', _cli_source()))


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no YAML frontmatter"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def test_every_repository_skill_has_one_byte_identical_mirror():
    canonicals = sorted(CANONICAL_ROOT.glob("*/SKILL.md"))
    mirrors = sorted(MIRROR_ROOT.glob("*/SKILL.md"))
    assert canonicals
    assert {path.parent.name for path in mirrors} == {
        path.parent.name for path in canonicals
    }
    for canonical in canonicals:
        mirror = MIRROR_ROOT / canonical.parent.name / "SKILL.md"
        assert mirror.read_bytes() == canonical.read_bytes(), (
            f"Skill mirror drifted: {mirror}; run python scripts/sync_skills.py"
        )


def test_exploration_skill_has_discriminating_frontmatter():
    fields = _frontmatter(SKILL)
    description = fields.get("description", "")
    assert fields.get("name") == SKILL.parent.name == "xhnovel-explore"
    assert "Phase 0" in description
    assert "open-world exploration" in description
    assert "already-generated Scene Scout tasks" in description

    plan_fields = _frontmatter(PLAN_SKILL)
    plan_description = plan_fields.get("description", "")
    assert plan_fields.get("name") == PLAN_SKILL.parent.name == "xhnovel-plan"
    assert "Phase -1" in plan_description
    assert "ExplorationPlan" in plan_description
    assert "search for scenes" in plan_description


def test_sync_skills_check_covers_the_new_skill():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_skills.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_skill_links_resolve_to_repository_documents():
    for skill in (SKILL, PLAN_SKILL):
        links = re.findall(
            r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)",
            skill.read_text(encoding="utf-8"),
        )
        assert links
        for target in links:
            if "://" not in target:
                assert (skill.parent / target).resolve().is_file(), (
                    f"broken Skill link: {target}"
                )


def test_exploration_docs_reference_only_shipped_cli_surface():
    real_flags = _real_flags()
    real_subcommands = _real_subcommands()
    for path in (SKILL, PLAN_SKILL, DOC):
        text = path.read_text(encoding="utf-8")
        used_flags = set(re.findall(r"(--[a-z][a-z0-9-]+)", text))
        used_subcommands = set(
            re.findall(r"xhnovel-pipeline\s+([a-z][a-z0-9-]+)", text)
        )
        assert used_flags <= real_flags, (
            f"{path.name} references unknown flags: {sorted(used_flags - real_flags)}"
        )
        assert used_subcommands <= real_subcommands, (
            f"{path.name} references unknown subcommands: "
            f"{sorted(used_subcommands - real_subcommands)}"
        )


def test_product_path_prepares_before_receipt_managed_execution():
    for path in (SKILL, DOC):
        text = path.read_text(encoding="utf-8")
        prepare = text.index("xhnovel-pipeline prepare-handoff")
        execute = text.index("xhnovel-pipeline execute-handoff")
        assert prepare < execute
        assert "--executor agent-files" in text[execute : execute + 300]
        assert not re.search(r"xhnovel-pipeline\s+research-novel\b", text)
        assert "WAITING_FOR_AGENT" in text
        assert "--retry" in text


def test_phase_minus1_compile_then_explore_then_handoff_flow_is_explicit():
    plan_text = PLAN_SKILL.read_text(encoding="utf-8")
    explore_text = SKILL.read_text(encoding="utf-8")
    assert plan_text.index("xhnovel-pipeline seal-intake") < plan_text.index(
        "xhnovel-pipeline seal-neutral-frame"
    ) < plan_text.index("xhnovel-pipeline compile-exploration-plan")
    assert "neutral-planning-input.json" in plan_text
    assert "never give it" in plan_text.lower()
    assert "user_goal_verbatim" in plan_text
    assert "selection_budget" in plan_text
    assert "strategy worker must not" in plan_text.lower()
    assert "HOST_ISOLATED_ATTESTED" in plan_text
    assert "NOT_PROVEN" in plan_text
    assert "does not replace" in plan_text

    assert "0. **Consume the sealed planning outputs.**" in explore_text
    assert "exploration-brief.json" in explore_text
    assert "exploration-plan.json" in explore_text
    assert "exploration_seeds" in explore_text and "diversity" in explore_text
    assert "hard host exploration exclusion" in explore_text
    assert "not machine-verified" in explore_text
    assert "formal Brief" in explore_text
    assert "host-audited strategy execution" in explore_text
    assert explore_text.index("xhnovel-pipeline prepare-handoff") < explore_text.index(
        "xhnovel-pipeline validate-planning-handoff"
    ) < explore_text.index("xhnovel-pipeline execute-handoff")


def test_phase0_records_remain_outside_the_core_catalog():
    assert (PHASE0_KINDS | PHASE_MINUS1_KINDS).isdisjoint(ID_FIELDS)
    for path in (SKILL, DOC):
        text = path.read_text(encoding="utf-8")
        assert re.search(
            r"Phase 0 (?:records|objects) stay outside (?:the )?core `Catalog`",
            text,
        )


def test_agent_files_flow_does_not_require_a_model_api_key():
    requirement = re.compile(
        r"(export|set|provide|need|needs|requires?|must set|must provide)"
        r"(?:(?!\b(?:no|not|without|never)\b)[^\n])*"
        r"(OPENAI_API_KEY|model API key)"
        r"|(OPENAI_API_KEY|model API key)[^\n]*(is required|needed|must be set)",
        re.IGNORECASE,
    )
    for path in (SKILL, DOC):
        match = requirement.search(path.read_text(encoding="utf-8"))
        assert match is None, (
            f"{path.name} presents an API key as required: {match.group(0)!r}"
        )


def test_exploration_is_host_managed_not_a_new_runtime():
    assert not ({"explore", "search", "discover"} & _real_subcommands())
    for path in (SKILL, DOC):
        text = path.read_text(encoding="utf-8").lower()
        assert "scheduler" in text and "worker registry" in text
        assert re.search(r"must not gain|does not manage|do not.+add", text, re.DOTALL)


def test_unproven_official_status_is_not_an_eligibility_filter():
    skill = SKILL.read_text(encoding="utf-8")
    docs = DOC.read_text(encoding="utf-8")
    step_four = skill.split("5. **Prepare through the product boundary.**", 1)[0]
    assert "4. **Resolve a source.**" in step_four
    assert "edition_status=UNKNOWN" in step_four
    assert "UNOFFICIAL_COPY" in step_four
    assert "positively" in step_four.casefold()
    assert "FAIR_USE_RESEARCH" in step_four
    assert "declare `unofficial_copy` merely because official or licensed status is unproven" in skill.casefold()
    assert "edition_status=UNKNOWN" in docs
    assert "UNOFFICIAL_COPY" in docs
    assert "positively established" in docs


def test_new_work_roots_seed_the_canonical_standing_attestation():
    skill = SKILL.read_text(encoding="utf-8")
    assert "attestations/operator-attestation.json" in skill
    assert "content-identical" in skill
    assert re.search(r"same\s+`attestation_id`", skill)
    assert "never author, edit, or re-sign an attestation per run" in skill.casefold()


def test_real_pilot_targets_are_preserved_as_lead_quality_gates():
    text = DOC.read_text(encoding="utf-8")
    assert re.search(r"at least 12 qualified ResearchLeads", text)
    assert re.search(r"at\s+least four works and three interaction families", text)
    assert re.search(r"no more than two first-round\s+Leads per work", text)
    assert re.search(r"Each Pilot Lead needs a concrete scene hint", text)
    assert re.search(r"at least one\s+location hint", text)
    assert "do not require any Lead to become executable" in text
