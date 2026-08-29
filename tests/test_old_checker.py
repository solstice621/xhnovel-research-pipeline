from __future__ import annotations

import sys
from pathlib import Path

from xhnovel_pipeline.paths import repo_root


def test_frozen_checker_self_tests():
    root = repo_root()
    scripts = root / "fixtures/legacy/sandbox-scripts-ff8b8bb"
    sys.path.insert(0, str(scripts))
    import test_check_evidence_yaml as t

    t.main()


def test_frozen_scenes_check_tree():
    root = repo_root()
    scripts = root / "fixtures/legacy/sandbox-scripts-ff8b8bb"
    sys.path.insert(0, str(scripts))
    from check_evidence_yaml import check_tree

    research = root / "fixtures/legacy/sandbox-research-ff8b8bb"
    checked, eligible = check_tree(research / "scenes", research_dir=research, require_generated_facts=True)
    assert checked == 3
    assert eligible == 0
