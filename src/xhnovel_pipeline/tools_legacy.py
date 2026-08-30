from __future__ import annotations

import pathlib

import yaml

from .errors import ValidationError


def check_legacy_scene_001(root: pathlib.Path) -> None:
    evidence = yaml.safe_load((root / "fixtures/legacy/scene-001/evidence.yaml").read_text(encoding="utf-8"))
    if str(evidence.get("schema_version")) != "0.1-legacy":
        raise ValidationError("E-LEGACY", "SCENE-001 must remain 0.1-legacy")
    if evidence.get("qualification_eligible"):
        raise ValidationError("E-LEGACY", "SCENE-001 must never auto-qualify")
    if (root / "fixtures/legacy/scene-001/claims.yaml").exists():
        raise ValidationError("E-LEGACY", "SCENE-001 must not present a 0.2 claims table")


def check_scene_002_tombstone(root: pathlib.Path) -> None:
    claims = yaml.safe_load((root / "fixtures/legacy/scene-002-tombstone/claims.yaml").read_text(encoding="utf-8"))
    evidence = yaml.safe_load((root / "fixtures/legacy/scene-002-tombstone/evidence.yaml").read_text(encoding="utf-8"))
    if claims.get("claims"):
        raise ValidationError("E-TOMBSTONE", "SCENE-002 tombstone must have zero live claims")
    if claims.get("live_original_fact_count") not in {0, None}:
        raise ValidationError("E-TOMBSTONE", "SCENE-002 live_original_fact_count must be 0")
    if evidence.get("qualification_eligible"):
        raise ValidationError("E-TOMBSTONE", "SCENE-002 must not be qualification_eligible")
