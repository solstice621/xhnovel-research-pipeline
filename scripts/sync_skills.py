"""Project the canonical host-agent Skill to every host-discovery directory.

`.agents/skills/xhnovel-agent-files/SKILL.md` is the single editable source
(discovered by Codex and Cursor). `.claude/skills/xhnovel-agent-files/SKILL.md`
is a byte-identical generated mirror (discovered by Claude Code). Both hosts must
see the exact same operating contract — the projection is a deterministic byte
copy, never a template.

Usage:
    python scripts/sync_skills.py            # write mirrors from canonical
    python scripts/sync_skills.py --check     # verify mirrors match; exit 1 on drift
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL_REL = Path("skills") / "xhnovel-agent-files" / "SKILL.md"
CANONICAL = REPO / ".agents" / SKILL_REL
MIRRORS = [REPO / ".claude" / SKILL_REL]


def _canonical_bytes() -> bytes:
    if not CANONICAL.is_file():
        raise SystemExit(f"canonical Skill missing: {CANONICAL}")
    return CANONICAL.read_bytes()


def check() -> int:
    canonical = _canonical_bytes()
    drift = []
    for mirror in MIRRORS:
        if not mirror.is_file():
            drift.append(f"missing mirror: {mirror}")
        elif mirror.read_bytes() != canonical:
            drift.append(f"out of sync: {mirror}")
    if drift:
        for line in drift:
            print(line, file=sys.stderr)
        print("run: python scripts/sync_skills.py", file=sys.stderr)
        return 1
    return 0


def sync() -> int:
    canonical = _canonical_bytes()
    for mirror in MIRRORS:
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_bytes(canonical)
        print(f"wrote {mirror.relative_to(REPO)}")
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--check":
        return check()
    if argv:
        raise SystemExit(f"unknown argument: {argv[0]}")
    return sync()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
