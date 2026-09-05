from __future__ import annotations

import re
from typing import Any

from .errors import ValidationError
from .hashing import object_hash

PREFIXES = {
    "ResearchRequest": "REQ-",
    "NovelRankingHit": "NRH-",
    "Source": "SRC-",
    "Retrieval": "RET-",
    "TriageAssessment": "TRI-",
    "CollectionDecision": "CDEC-",
    "CollectionReview": "CRV-",
    "NovelWork": "NWK-",
    "NovelChapter": "CHP-",
    "NovelIngestionRun": "NING-",
    "NovelRankingRun": "NRNK-",
    "NovelSourceResolution": "NSR-",
    "ParseRun": "PRUN-",
    "ParsedDocument": "DOC-",
    "Segment": "SEG-",
    "CollectionSnapshot": "SNP-",
    "EvidenceBundle": "BND-",
    "SceneWindow": "SWIN-",
    "SceneScoutRun": "SSRUN-",
    "SceneMergeRun": "SMRUN-",
    "SceneCandidate": "SCN-",
    "ModelAttempt": "MAT-",
    "ExtractorBuild": "BLD-",
    "EvidenceExport": "EXP-",
    # Standalone Phase 0 records. Adding prefixes does not add these kinds to
    # Catalog.ID_FIELDS; they remain outside the core evidence catalog.
    "ExplorationBrief": "XBR-",
    "ResearchLead": "RLD-",
    "LeadSource": "LDS-",
    "WorkRef": "WREF-",
    "SourceRef": "SREF-",
    "HandoffBuildRequest": "HBR-",
    "SourceDeclaration": "SDL-",
    "OperatorAttestation": "OPA-",
    "EvidenceHandoff": "EHO-",
    "HandoffAttempt": "HAT-",
    "HandoffAttemptEvent": "HEV-",
    "EvidenceHandoffExecutionReceipt": "HER-",
    # Standalone Phase -1 records; still excluded from Catalog.ID_FIELDS.
    "ResearchIntake": "RIN-",
    "NeutralPlanningInput": "NPI-",
    "NeutralPlanningExecution": "NPE-",
    "NeutralResearchFrame": "NRF-",
    "ExplorationPlan": "XPL-",
    "PlanningCompilationReceipt": "PCR-",
    "Seed": "SD-",
    "PlanningCompilerBuild": "PCB-",
    # Standalone observation research records; deliberately excluded from Catalog.
    "ObservationDefinition": "ODEF-",
    "ObservationRequirement": "OREQ-",
    "ProfileResolution": "PRES-",
    "ObservationWorkLead": "OWL-",
    "GenericHandoffBuildRequest": "GHB-",
    "GenericExtractionHandoff": "GEH-",
    "GenericHandoffAttempt": "GAT-",
    "GenericHandoffAttemptEvent": "GEV-",
    "GenericExtractionExecutionReceipt": "GER-",
    "ObservationResearchRun": "ORUN-",
    "ObservationResearchEvent": "OREV-",
}

ID_RE = re.compile(r"^[A-Z]{2,5}-[A-Z0-9][A-Z0-9._:-]{1,}$")


def check_id(kind: str, value: str) -> None:
    prefix = PREFIXES[kind]
    if not isinstance(value, str) or not value.startswith(prefix) or not ID_RE.match(value):
        raise ValidationError("E-ID", f"{kind} id {value!r} must match {prefix}*")


def derived_id(kind: str, payload: dict[str, Any], *, length: int = 20) -> str:
    """Return a stable opaque ID for the complete logical-object input."""
    digest = object_hash(payload, omit=()).removeprefix("sha256:")
    return f"{PREFIXES[kind]}{digest[:length].upper()}"
