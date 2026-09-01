"""Novel Spec validation primitives and composed direct-research preflight.

P0-C1 extracted the original pure checks without changing their call sites. P0-C2
composes those primitives into a side-effect-free preflight for callers that need a
fully resolved description of a direct research run. The production workflow is not
rerouted through this composer: its historical validation order and I/O boundaries
remain unchanged.
"""

from __future__ import annotations

import copy
import pathlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .errors import ValidationError
from .hashing import object_hash

# Re-export the already-pure rights/source-quality validators so callers can reach
# the whole Novel Spec validation surface through one module. Their definitions stay
# in novel_assessment (which does not import this module); this is import-only sugar.
from .novel_assessment import declared_rights, declared_source_quality

__all__ = [
    "SpecValidationPurpose",
    "ValidatedDirectResearchSpec",
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
    "validate_direct_research_spec",
    "load_validated_direct_research_spec",
]

_SCENE_SCOUT_OPTION_KEYS = {
    "window_chars",
    "overlap_chars",
    "max_input_chars",
    "max_request_bytes",
    "max_workers",
}
_DEFAULT_DISCOVERY_BRIEF = "提取小说中的关键情节、参与者、条件与状态变化。"
_DEFAULT_SCENE_SCOUT_CONFIG = {
    "window_chars": 10_000,
    "overlap_chars": 1_800,
    "max_input_chars": 20_000,
    "max_request_bytes": 2_000_000,
    "max_workers": 8,
}
_SUPPORTED_SOURCE_KINDS = {
    "txt": "txt",
    "epub": "epub",
    "directory": "directory",
    "chapter-directory": "directory",
    "site": "site",
    "static-site": "site",
}
_PHASE0_ONLY_KEYS = {
    "location_hints",
    "motivating_lead_ids",
    "research_leads",
    "chapter_scope",
    "selected_chapter_ids",
}


class SpecValidationPurpose(str, Enum):
    """Select compatibility or strict Evidence-Handoff policy."""

    RUNTIME_COMPAT = "RUNTIME_COMPAT"
    EVIDENCE_HANDOFF = "EVIDENCE_HANDOFF"


@dataclass(frozen=True)
class ValidatedDirectResearchSpec:
    """Resolved, copied view of a direct Novel Spec.

    ``resolved_spec_hash`` is deliberately core-neutral. Phase 0 maps it to
    ``EvidenceHandoff.novel_spec.expected_input_spec_hash``.
    """

    effective_spec: dict[str, Any]
    resolved_spec_hash: str
    source_kind: str
    normalized_source_spec: dict[str, Any]
    rights: dict[str, Any]
    source_quality: dict[str, str]
    source_quality_tier: str
    discovery_brief: str
    scene_scout_config: dict[str, int]
    limits: dict[str, int]
    strict_order: bool
    execution_scope: str = "FULL_WORK"


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
    """Scene Scout byte/char budgets must be positive ints (not bool)."""
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


def _quality_tier(source_quality: dict[str, str]) -> str:
    edition = source_quality["edition_status"]
    completeness = source_quality["textual_completeness"]
    if completeness == "COMPLETE" and edition == "OFFICIAL":
        return "A"
    if completeness == "COMPLETE" and edition in {"PUBLISHED_EDITION", "USER_VERIFIED_COPY"}:
        return "B"
    return "D"


def _normalized_source(
    source: dict[str, Any],
    *,
    purpose: SpecValidationPurpose,
) -> tuple[str, dict[str, Any]]:
    from .novel_adapters import adapter_from_spec

    source_copy = copy.deepcopy(source)
    adapter_from_spec(source_copy)  # validates adapter kind/options without fetching
    raw_kind = str(source_copy["kind"]).casefold()
    source_kind = _SUPPORTED_SOURCE_KINDS[raw_kind]
    source_copy["kind"] = source_kind

    if purpose is SpecValidationPurpose.EVIDENCE_HANDOFF:
        title = source_copy.get("title")
        language = source_copy.get("language")
        author = source_copy.get("author")
        if not isinstance(title, str) or not title.strip():
            raise ValidationError(
                "E-HANDOFF-SOURCE",
                "evidence handoff source requires an explicit non-empty title",
            )
        if not isinstance(language, str) or not language.strip():
            raise ValidationError(
                "E-HANDOFF-SOURCE",
                "evidence handoff source requires an explicit non-empty language",
            )
        if author is not None and (not isinstance(author, str) or not author.strip()):
            raise ValidationError(
                "E-HANDOFF-SOURCE",
                "evidence handoff source author must be null or a non-empty string",
            )
        source_copy["title"] = title.strip()
        source_copy["language"] = language.strip()
        if isinstance(author, str):
            source_copy["author"] = author.strip()

    if source_kind in {"txt", "epub", "directory"}:
        path_value = source_copy.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValidationError("E-NOVEL-SPEC", "local novel source requires a non-empty path")
        path = pathlib.Path(path_value).expanduser()
        if purpose is SpecValidationPurpose.EVIDENCE_HANDOFF and not path.is_absolute():
            raise ValidationError(
                "E-HANDOFF-SOURCE",
                "evidence handoff local source path must already be resolved",
            )
        path = path.resolve()
        source_copy["path"] = str(path)
        if source_kind == "directory":
            if not path.is_dir():
                raise ValidationError("E-NOVEL-SOURCE", f"missing chapter directory {path}")
        elif source_kind == "epub":
            if not path.is_file():
                raise ValidationError("E-NOVEL-SOURCE", f"missing EPUB {path}")
        elif not path.is_file():
            raise ValidationError("E-NOVEL-SOURCE", f"missing text file {path}")

    return source_kind, source_copy


def _validated_request(
    spec: dict[str, Any],
    *,
    purpose: SpecValidationPurpose,
) -> str:
    raw_request = spec.get("request")
    if raw_request is None:
        request: dict[str, Any] = {}
    elif isinstance(raw_request, dict):
        request = raw_request
    else:
        raise ValidationError("E-NOVEL-SPEC", "request must be an object")

    if purpose is SpecValidationPurpose.EVIDENCE_HANDOFF:
        for key in _PHASE0_ONLY_KEYS:
            if key in spec or key in request:
                raise ValidationError(
                    "E-HANDOFF-SCOPE",
                    "exploration hints or narrowed chapter scope cannot enter a direct research spec",
                )

    raw_brief = request.get("discovery_brief")
    if purpose is SpecValidationPurpose.EVIDENCE_HANDOFF:
        if not isinstance(raw_brief, str) or not raw_brief.strip():
            raise ValidationError(
                "E-HANDOFF-BRIEF",
                "evidence handoff requires an explicit non-empty discovery_brief",
            )
        return raw_brief

    if raw_brief is None:
        return _DEFAULT_DISCOVERY_BRIEF
    return str(raw_brief)


def validate_direct_research_spec(
    spec: dict[str, Any],
    *,
    purpose: SpecValidationPurpose,
) -> ValidatedDirectResearchSpec:
    """Compose a direct-research preflight without mutating ``spec`` or fetching.

    This function is intentionally not inserted into the production workflow. It
    exposes the same validation primitives for Phase 0 and other preflight callers
    without changing the runtime's historical validation timing.
    """

    if not isinstance(purpose, SpecValidationPurpose):
        raise ValidationError("E-NOVEL-SPEC", "spec validation purpose is not recognized")
    check_spec_object(spec)
    effective_spec = copy.deepcopy(spec)

    source = effective_spec.get("source")
    check_source_object(source)
    source_catalog = effective_spec.get("source_catalog")
    check_source_catalog(source_catalog)
    if purpose is SpecValidationPurpose.EVIDENCE_HANDOFF and source_catalog:
        raise ValidationError(
            "E-HANDOFF-SOURCE",
            "evidence handoff requires one resolved direct source",
        )

    limits_raw = effective_spec.get("limits", {})
    check_limits_object(limits_raw)
    limits = {
        "max_chapters": input_limit(limits_raw, "max_chapters", 100_000, minimum=1),
        "max_bytes": input_limit(limits_raw, "max_bytes", 500_000_000, minimum=0),
    }
    strict_order = effective_spec.get("strict_order", False)
    check_strict_order(strict_order)

    # A direct research run always stores source text and sends native Scene Scout
    # tasks to an external semantic executor. Both purposes therefore share the
    # production rights gate; the Handoff purpose is stricter only in other areas.
    rights = declared_rights(
        effective_spec,
        require_storage=True,
        require_external_model=True,
    )
    source_quality = declared_source_quality(effective_spec)
    tier = _quality_tier(source_quality)
    if purpose is SpecValidationPurpose.EVIDENCE_HANDOFF and tier not in {"A", "B"}:
        raise ValidationError(
            "E-HANDOFF-QUALITY",
            "evidence handoff requires Tier A or Tier B source quality",
        )

    discovery_brief = _validated_request(effective_spec, purpose=purpose)
    source_kind, normalized_source_spec = _normalized_source(source, purpose=purpose)

    raw_scene_options = effective_spec.get("scene_scout")
    scene_options = raw_scene_options or {}
    check_scene_scout_options(scene_options)
    scene_config = {**_DEFAULT_SCENE_SCOUT_CONFIG, **scene_options}
    check_scene_window_params(
        window_chars=scene_config["window_chars"],
        overlap_chars=scene_config["overlap_chars"],
    )
    check_scene_concurrency(scene_config["max_workers"])
    check_scene_config_values(
        max_input_chars=scene_config["max_input_chars"],
        max_request_bytes=scene_config["max_request_bytes"],
    )

    return ValidatedDirectResearchSpec(
        effective_spec=effective_spec,
        resolved_spec_hash=object_hash(effective_spec, omit=()),
        source_kind=source_kind,
        normalized_source_spec=normalized_source_spec,
        rights=rights,
        source_quality=source_quality,
        source_quality_tier=tier,
        discovery_brief=discovery_brief,
        scene_scout_config=scene_config,
        limits=limits,
        strict_order=strict_order,
    )


def load_validated_direct_research_spec(
    path: pathlib.Path,
    *,
    purpose: SpecValidationPurpose,
) -> ValidatedDirectResearchSpec:
    """Load path-relative fields through the production loader, then preflight."""

    from .novel_ingest import load_novel_spec

    return validate_direct_research_spec(load_novel_spec(path), purpose=purpose)
