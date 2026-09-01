"""Pure Novel Spec validation primitives.

Extracted (verbatim) from the previously-scattered validation sites in
``novel_ingest``, ``scene_scout`` and ``novel_workflow`` so a single reusable
surface exists. Every function here is pure: it depends only on its arguments,
raises the exact same ``ValidationError`` code and message the original inline
check raised, and performs no I/O — no store, no catalog, no filesystem, no lock.

This module deliberately has no Phase 0 knowledge. It is the shared foundation a
later strict ``EVIDENCE_HANDOFF`` preflight composes; it does not itself compose a
spec or change any runtime behavior. Its only dependencies are ``.errors`` (for
``ValidationError``) and a re-export line for the already-pure rights/quality
validators, so it cannot introduce an import cycle.
"""

from __future__ import annotations

from typing import Any

from .errors import ValidationError

# Re-export the already-pure rights/source-quality validators so callers can reach
# the whole Novel Spec validation surface through one module. Their definitions stay
# in novel_assessment (which does not import this module); this is import-only sugar.
from .novel_assessment import declared_rights, declared_source_quality

__all__ = [
    "check_spec_object",
    "check_source_object",
    "check_source_catalog",
    "check_limits_object",
    "check_strict_order",
    "input_limit",
    "check_scene_window_params",
    "check_scene_concurrency",
    "check_scene_config_values",
    "check_scene_scout_options",
    "declared_rights",
    "declared_source_quality",
]

# The exact key set accepted under spec["scene_scout"]; unknown keys are rejected.
_SCENE_SCOUT_OPTION_KEYS = {
    "window_chars",
    "overlap_chars",
    "max_input_chars",
    "max_request_bytes",
    "max_workers",
}


def check_spec_object(spec: Any) -> None:
    """The top-level Novel Spec must be a JSON object."""
    if not isinstance(spec, dict):
        raise ValidationError("E-NOVEL-SPEC", "novel spec must be an object")


def check_source_object(source: Any) -> None:
    """spec["source"] must be a JSON object."""
    if not isinstance(source, dict):
        raise ValidationError("E-NOVEL-SPEC", "source must be an object")


def check_source_catalog(source_catalog: Any) -> None:
    """spec["source_catalog"], when present, must be an array (None is allowed)."""
    if source_catalog is not None and not isinstance(source_catalog, list):
        raise ValidationError(
            "E-NOVEL-SOURCE-CATALOG",
            "source_catalog must be an array",
        )


def check_limits_object(limits: Any) -> None:
    """spec["limits"] must be a JSON object."""
    if not isinstance(limits, dict):
        raise ValidationError("E-NOVEL-LIMIT", "limits must be an object")


def check_strict_order(value: Any) -> None:
    """The resolved spec["strict_order"] value must be a boolean."""
    if not isinstance(value, bool):
        raise ValidationError("E-NOVEL-SPEC", "strict_order must be a boolean")


def input_limit(
    limits: dict[str, Any],
    field: str,
    default: int,
    *,
    minimum: int,
) -> int:
    """Return an integer limit field, defaulted, validated int (not bool) >= minimum."""
    value = limits.get(field, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValidationError(
            "E-NOVEL-LIMIT",
            f"limits.{field} must be an integer of at least {minimum}",
        )
    return value


def check_scene_window_params(*, window_chars: int, overlap_chars: int) -> None:
    """Scene window size/overlap bounds (window_chars checked first)."""
    if not 8_000 <= window_chars <= 12_000:
        raise ValidationError("E-SCENE-WINDOW", "window_chars must be between 8000 and 12000")
    if not 0.15 <= overlap_chars / window_chars <= 0.20:
        raise ValidationError("E-SCENE-WINDOW", "overlap must be between 15% and 20%")


def check_scene_concurrency(max_workers: Any) -> None:
    """Scene Scout max_workers must be an int (not bool) in [1, 64]."""
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or not 1 <= max_workers <= 64:
        raise ValidationError("E-SCENE-CONCURRENCY", "max_workers must be between 1 and 64")


def check_scene_config_values(*, max_input_chars: Any, max_request_bytes: Any) -> None:
    """Scene Scout byte/char budgets must be positive ints (not bool).

    The tuple order (max_input_chars first) is preserved so the ``field`` reported
    in the error message matches the original inline check.
    """
    for field, value in (
        ("max_input_chars", max_input_chars),
        ("max_request_bytes", max_request_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationError("E-SCENE-CONFIG", f"{field} must be a positive integer")


def check_scene_scout_options(options: Any) -> None:
    """spec["scene_scout"] must be an object carrying only the known option keys."""
    if not isinstance(options, dict) or set(options) - _SCENE_SCOUT_OPTION_KEYS:
        raise ValidationError("E-SCENE-CONFIG", "scene_scout options are invalid")
