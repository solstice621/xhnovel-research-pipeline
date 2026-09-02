"""Project canonical host-agent Skills to every host-discovery directory.

`.agents/skills/*/SKILL.md` files are the editable sources discovered by Codex and
Cursor. `.claude/skills/*/SKILL.md` files are byte-identical generated mirrors
discovered by Claude Code. Every host must see the same operating contracts — the
projection is a deterministic byte copy, never a template.

Usage:
    python scripts/sync_skills.py            # write mirrors from canonical
    python scripts/sync_skills.py --check     # verify mirrors match; exit 1 on drift
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CANONICAL_ROOT = REPO / ".agents" / "skills"
MIRROR_ROOTS = [REPO / ".claude" / "skills"]


def _canonical_skills() -> list[Path]:
    skills = sorted(CANONICAL_ROOT.glob("*/SKILL.md"))
    if not skills:
        raise SystemExit(f"no canonical Skills found under {CANONICAL_ROOT}")
    return skills


def check() -> int:
    drift = []
    canonicals = _canonical_skills()
    expected_relatives = {path.relative_to(CANONICAL_ROOT) for path in canonicals}
    for canonical in canonicals:
        relative = canonical.relative_to(CANONICAL_ROOT)
        for mirror_root in MIRROR_ROOTS:
            mirror = mirror_root / relative
            if not mirror.is_file():
                drift.append(f"missing mirror: {mirror}")
            elif mirror.read_bytes() != canonical.read_bytes():
                drift.append(f"out of sync: {mirror}")
    for mirror_root in MIRROR_ROOTS:
        for mirror in sorted(mirror_root.glob("*/SKILL.md")):
            if mirror.relative_to(mirror_root) not in expected_relatives:
                drift.append(f"mirror has no canonical Skill: {mirror}")
    if drift:
        for line in drift:
            print(line, file=sys.stderr)
        print("run: python scripts/sync_skills.py", file=sys.stderr)
        return 1
    return 0


def sync() -> int:
    for canonical in _canonical_skills():
        relative = canonical.relative_to(CANONICAL_ROOT)
        for mirror_root in MIRROR_ROOTS:
            mirror = mirror_root / relative
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror.write_bytes(canonical.read_bytes())
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
