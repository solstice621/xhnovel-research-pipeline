from __future__ import annotations

import re
from typing import Any

from .errors import ValidationError
from .hashing import object_hash

PREFIXES = {
    "ResearchRequest": "REQ-",
    "SearchCampaign": "CAM-",
    "QuerySpec": "QRY-",
    "SearchRun": "SRUN-",
    "DiscoveryHit": "HIT-",
    "Source": "SRC-",
    "Retrieval": "RET-",
    "TriageAssessment": "TRI-",
    "OriginAssessment": "ORI-",
    "CollectionDecision": "CDEC-",
    "CollectionReview": "CRV-",
    "ParseRun": "PRUN-",
    "ParsedDocument": "DOC-",
    "Segment": "SEG-",
    "CollectionSnapshot": "SNP-",
    "EvidenceBundle": "BND-",
    "ExtractionRun": "ERUN-",
    "Claim": "CLM-",
    "ExtractorBuild": "BLD-",
    "QualificationRun": "QRUN-",
    "AssuranceRecord": "ASR-",
    "EvidenceExport": "EXP-",
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
