"""Stage 4 doc/skill provenance guards.

Static assertions that the operating docs and the host-agent Skill stay honest
about the real CLI: no invented flags, no API-key requirement for agent-files, no
claim that famous-novel supports agent-files, and the experiment protocol treats
agent-files as a native executor while still forbidding native-contract bypass.
No network, no model API — these read repository text and the argparse surface.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

from xhnovel_pipeline.paths import repo_root

ROOT = repo_root()
SKILL_REL = ("skills", "xhnovel-agent-files", "SKILL.md")
# Canonical source (Codex/Cursor discover .agents/skills); the .claude mirror is a
# byte-identical projection produced by scripts/sync_skills.py.
SKILL = ROOT.joinpath(".agents", *SKILL_REL)
SKILL_MIRROR = ROOT.joinpath(".claude", *SKILL_REL)
OLD_TOP_LEVEL_SKILL = ROOT.joinpath(*SKILL_REL)
README = ROOT / "README.md"
PROTOCOL = ROOT / "docs" / "EXPERIMENT_PROTOCOL.md"
AGENT_EXEC = ROOT / "docs" / "AGENT_EXECUTION.md"
EXAMPLE = ROOT / "examples" / "novel-direct.json"


def _real_flags() -> set[str]:
    """Every long option accepted by the real CLI parsers, harvested from source."""
    text = (ROOT / "src" / "xhnovel_pipeline" / "cli.py").read_text(encoding="utf-8")
    return set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', text))


def _real_subcommands() -> set[str]:
    text = (ROOT / "src" / "xhnovel_pipeline" / "cli.py").read_text(encoding="utf-8")
    return set(re.findall(r'add_parser\(\s*"([a-z0-9-]+)"', text))


def _frontmatter(path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, f"{path} has no YAML frontmatter"
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


# ---------------------------------------------------------------------------
# Skill discovery layout: canonical in .agents, byte-identical mirror in .claude,
# no stale top-level copy, frontmatter name matches the skill directory.
# ---------------------------------------------------------------------------
def test_skill_lives_in_host_discovery_dirs_and_mirror_is_identical():
    assert SKILL.is_file(), f"canonical Skill missing: {SKILL}"
    assert SKILL_MIRROR.is_file(), f"Claude mirror missing: {SKILL_MIRROR}"
    assert SKILL.read_bytes() == SKILL_MIRROR.read_bytes(), (
        "Skill mirror drifted from canonical; run: python scripts/sync_skills.py"
    )
    assert not OLD_TOP_LEVEL_SKILL.exists(), (
        f"stale non-discoverable top-level Skill still present: {OLD_TOP_LEVEL_SKILL}"
    )


def test_skill_frontmatter_name_matches_directory():
    fm = _frontmatter(SKILL)
    assert fm.get("name") == "xhnovel-agent-files" == SKILL.parent.name
    assert fm.get("description", "").strip()


def test_sync_skills_check_passes():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_skills.py"), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_skill_and_readme_reference_only_real_flags():
    real = _real_flags()
    for doc in (SKILL, README):
        used = set(re.findall(r"(--[a-z][a-z0-9-]+)", doc.read_text(encoding="utf-8")))
        unknown = used - real
        assert not unknown, f"{doc.name} references non-existent flags: {sorted(unknown)}"


def test_skill_references_only_real_subcommands():
    real = _real_subcommands()
    used = set(re.findall(r"xhnovel-pipeline\s+([a-z][a-z0-9-]+)", SKILL.read_text(encoding="utf-8")))
    unknown = used - real
    assert not unknown, f"SKILL references non-existent subcommands: {sorted(unknown)}"


def test_agent_files_docs_do_not_require_openai_api_key():
    # An API key must never be presented as a *requirement* of the agent-files flow.
    # A prohibition ("do not look for an OPENAI_API_KEY") is fine; a requirement
    # ("export OPENAI_API_KEY", "requires a model API key") is not.
    require = re.compile(
        r"(export|set|provide|need|needs|requires?|must set|must provide)"
        r"(?:(?!\b(?:no|not|without|never)\b)[^\n])*"
        r"(OPENAI_API_KEY|model API key)"
        r"|(OPENAI_API_KEY|model API key)[^\n]*(is required|needed|must be set)",
        re.IGNORECASE,
    )
    for doc in (SKILL, README):
        text = doc.read_text(encoding="utf-8")
        m = require.search(text)
        assert m is None, f"{doc.name} presents an API key as required for agent-files: {m.group(0)!r}"


def test_no_doc_claims_famous_novel_supports_agent_files():
    pattern = re.compile(r"research-famous-novel[^\n]*--executor\s+agent-files")
    for doc in (SKILL, README, PROTOCOL, AGENT_EXEC):
        text = doc.read_text(encoding="utf-8")
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - 200) : m.end() + 200]
            assert "E-AGENT-EXECUTOR-UNSUPPORTED" in window or "reject" in window.lower() or "not" in window.lower(), (
                f"{doc.name} mentions research-famous-novel --executor agent-files "
                "without marking it unsupported"
            )


def test_protocol_names_agent_files_as_native_and_still_bans_bypass():
    text = PROTOCOL.read_text(encoding="utf-8")
    assert "AGENT_FILES_EXECUTOR" in text
    assert "API_EXECUTOR" in text
    # native execution is defined for both; bypass of the native task contract stays invalid
    assert "bypass" in text.lower()
    assert "native executor/task contract" in text


def test_example_spec_contains_explicit_discovery_brief_and_parses():
    spec = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    brief = spec.get("request", {}).get("discovery_brief")
    assert isinstance(brief, str) and brief.strip(), "example must set request.discovery_brief"


@pytest.mark.parametrize("doc", [SKILL, README, PROTOCOL, AGENT_EXEC])
def test_operating_docs_exist_and_are_nonempty(doc):
    assert doc.is_file(), f"missing {doc}"
    assert doc.read_text(encoding="utf-8").strip(), f"empty {doc}"


def test_readme_states_exit_3_is_not_failure():
    text = README.read_text(encoding="utf-8")
    assert "WAITING_FOR_AGENT" in text
    assert re.search(r"exit\D{0,4}3|code\D{0,4}3|\b3\b", text)
    # explicit "not a failure" framing near the exit-3 mention
    assert "not a failure" in text.lower() or "not failure" in text.lower()
