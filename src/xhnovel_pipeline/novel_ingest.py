from __future__ import annotations

import fcntl
import json
import os
import pathlib
import re
import tempfile
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlparse

from .catalog import Catalog
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import artifact_id_for, object_hash, sorted_ids
from .ids import derived_id
from .novel_adapters import ChapterRef, WorkMetadata, adapter_from_spec
from .parse import parse_artifact, parser_build_id_for
from .schema import validate_schema
from .store import ArtifactStore


CHECKPOINT_INTEGRITY_FIELD = "integrity_hash"
WORK_DIR_LOCK_NAME = ".novel-ingest.lock"
MAX_REPORTED_MISSING_CHAPTERS = 10_000
_DISCOVERY_STATE_FIELDS = (
    "schema_version",
    "input_spec_hash",
    "adapter_build_id",
    "discovery_complete",
    "work",
    "chapter_refs",
    "provenance",
    "started_at",
)
_WORK_FIELDS = {"title", "author", "language", "source_kind", "source_locator"}
_CHAPTER_REF_FIELDS = {
    "chapter_key",
    "ordinal",
    "title",
    "source_locator",
    "media_type",
    "declared_number",
    "adapter_data",
    "derived_from_provenance",
}
_PROVENANCE_FIELDS = {"artifact_id", "locator", "media_type", "byte_length", "created_at"}
_COMPLETION_FIELDS = {
    "artifact_id",
    "byte_length",
    "media_type",
    "final_locator",
    "http_status",
    "retrieved_at",
    "receipt_artifact_id",
}
_SITE_ATTEMPT_FIELDS = {
    "schema_version",
    "retrieval_id",
    "attempt_ordinal",
    "stage",
    "requested_url",
    "final_url",
    "http_status",
    "content_type",
    "status",
    "error_code",
    "error_message",
    "raw_artifact_id",
    "raw_byte_length",
    "attempted_at",
    "retry_of",
    "adapter_build_id",
}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


@contextmanager
def _exclusive_work_dir(work_dir: pathlib.Path):
    work_dir.mkdir(parents=True, exist_ok=True)
    lock_path = work_dir / WORK_DIR_LOCK_NAME
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ValidationError("E-NOVEL-WORKDIR-LOCK", f"cannot open work-dir lock {lock_path}") from exc
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValidationError(
                "E-NOVEL-WORKDIR-LOCKED",
                f"another novel ingestion is already using {work_dir}",
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _checkpoint_integrity_hash(state: dict[str, Any]) -> str:
    return object_hash(state, omit=(CHECKPOINT_INTEGRITY_FIELD,))


def _write_checkpoint(path: pathlib.Path, state: dict[str, Any]) -> None:
    state[CHECKPOINT_INTEGRITY_FIELD] = _checkpoint_integrity_hash(state)
    _atomic_write(path, _json_bytes(state))


def _write_immutable(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValidationError("E-IMMUTABLE-OUTPUT", f"refusing to overwrite {path}")


def _artifact_record(
    artifact_id: str,
    *,
    media_type: str,
    byte_length: int,
    created_at: str,
    parent_artifact_id: str | None = None,
    transform_build_id: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "media_type": media_type,
        "byte_length": byte_length,
        "retention_policy": "retention-v1",
        "durability_status": "LOCAL",
        "created_at": created_at,
    }
    if parent_artifact_id:
        record["parent_artifact_id"] = parent_artifact_id
        record["transform_build_id"] = transform_build_id or "novel-adapter-v1"
    return record


def _add_artifact_once(catalog: Catalog, record: dict[str, Any]) -> None:
    if not any(item["artifact_id"] == record["artifact_id"] for item in catalog.all("Artifact")):
        catalog.add("Artifact", record)


def _load_checkpoint(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-CHECKPOINT-CORRUPT", f"cannot read {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError("E-CHECKPOINT-CORRUPT", "checkpoint must be an object")
    integrity_hash = value.get(CHECKPOINT_INTEGRITY_FIELD)
    if not isinstance(integrity_hash, str) or integrity_hash != _checkpoint_integrity_hash(value):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint content does not match its integrity hash")
    return value


def _checkpoint_refs(state: dict[str, Any]) -> list[ChapterRef]:
    try:
        values = state["chapter_refs"]
        if not isinstance(values, list) or not values:
            raise TypeError("chapter_refs must be a non-empty array")
        if any(not isinstance(item, dict) or set(item) != _CHAPTER_REF_FIELDS for item in values):
            raise TypeError("chapter_refs have an invalid shape")
        refs = [ChapterRef.from_checkpoint(item) for item in values]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("E-CHECKPOINT-CORRUPT", "invalid chapter refs") from exc
    if (
        any(not ref.chapter_key or ref.ordinal < 1 for ref in refs)
        or len({ref.chapter_key for ref in refs}) != len(refs)
        or [ref.ordinal for ref in refs] != list(range(1, len(refs) + 1))
    ):
        raise ValidationError("E-CHECKPOINT-CORRUPT", "chapter refs are not uniquely and consecutively ordered")
    return refs


def _work_from_checkpoint(state: dict[str, Any]) -> WorkMetadata:
    try:
        value = state["work"]
        if not isinstance(value, dict) or set(value) != _WORK_FIELDS:
            raise TypeError("work metadata has an invalid shape")
        return WorkMetadata(
            title=value["title"],
            author=value.get("author"),
            language=value["language"],
            source_kind=value["source_kind"],
            source_locator=value["source_locator"],
        )
    except (KeyError, TypeError) as exc:
        raise ValidationError("E-CHECKPOINT-CORRUPT", "invalid work metadata") from exc


def _discovery_payload(state: dict[str, Any]) -> dict[str, Any]:
    try:
        return {field: state[field] for field in _DISCOVERY_STATE_FIELDS}
    except KeyError as exc:
        raise ValidationError("E-CHECKPOINT-CORRUPT", f"checkpoint lacks discovery field {exc.args[0]}") from exc


def _read_canonical_json_artifact(
    store: ArtifactStore,
    artifact_id: str,
    *,
    label: str,
) -> dict[str, Any]:
    try:
        raw = store.get(artifact_id)
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("E-CHECKPOINT-INTEGRITY", f"{label} artifact is not valid JSON") from exc
    if not isinstance(value, dict) or raw != _json_bytes(value):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", f"{label} artifact is not canonical JSON")
    return value


def _completion_payload(chapter_key: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "chapter_key": chapter_key,
        "artifact_id": result["artifact_id"],
        "byte_length": result["byte_length"],
        "media_type": result["media_type"],
        "final_locator": result["final_locator"],
        "http_status": result["http_status"],
        "retrieved_at": result["retrieved_at"],
    }


def _completion_marker_path(work_dir: pathlib.Path, chapter_key: str, receipt_artifact_id: str) -> pathlib.Path:
    chapter_digest = artifact_id_for(chapter_key.encode("utf-8")).removeprefix("sha256:")
    receipt_digest = receipt_artifact_id.removeprefix("sha256:")
    return work_dir / "completion-receipts" / chapter_digest / f"{receipt_digest}.json"


def _write_completion_marker(
    work_dir: pathlib.Path,
    chapter_key: str,
    receipt_artifact_id: str,
) -> None:
    marker = {"chapter_key": chapter_key, "receipt_artifact_id": receipt_artifact_id}
    _write_immutable(_completion_marker_path(work_dir, chapter_key, receipt_artifact_id), _json_bytes(marker))


def _site_attempt_marker_path(work_dir: pathlib.Path, receipt_artifact_id: str) -> pathlib.Path:
    digest = receipt_artifact_id.removeprefix("sha256:")
    return work_dir / "site-attempts" / f"{digest}.json"


def _read_site_attempt_marker_ids(work_dir: pathlib.Path) -> list[str]:
    marker_root = work_dir / "site-attempts"
    receipt_ids: list[str] = []
    if marker_root.exists():
        for path in sorted(marker_root.glob("*.json")):
            try:
                raw = path.read_bytes()
                marker = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", f"invalid site attempt marker {path}") from exc
            if (
                not isinstance(marker, dict)
                or set(marker) != {"attempt_receipt_artifact_id"}
                or raw != _json_bytes(marker)
                or not isinstance(marker["attempt_receipt_artifact_id"], str)
                or _site_attempt_marker_path(work_dir, marker["attempt_receipt_artifact_id"]) != path
                or marker["attempt_receipt_artifact_id"] in receipt_ids
            ):
                raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", f"invalid site attempt marker {path}")
            receipt_ids.append(marker["attempt_receipt_artifact_id"])
    return receipt_ids


def _read_site_attempt_receipts(
    store: ArtifactStore,
    receipt_ids: list[str],
) -> list[dict[str, Any]]:
    receipts = [
        _read_canonical_json_artifact(store, receipt_id, label="site attempt receipt")
        for receipt_id in receipt_ids
    ]
    if any(set(receipt) != _SITE_ATTEMPT_FIELDS for receipt in receipts):
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "site attempt receipt has an invalid shape")
    by_retrieval_id: dict[str, dict[str, Any]] = {}
    ordinals: set[int] = set()
    for receipt in receipts:
        ordinal = receipt.get("attempt_ordinal")
        raw_artifact_id = receipt.get("raw_artifact_id")
        raw_byte_length = receipt.get("raw_byte_length")
        identity = {key: value for key, value in receipt.items() if key not in {"schema_version", "retrieval_id"}}
        if (
            receipt.get("schema_version") != SCHEMA_VERSION
            or receipt.get("retrieval_id") != derived_id("Retrieval", identity)
            or not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 1
            or ordinal in ordinals
            or receipt.get("stage") not in {"INDEX", "CHAPTER"}
            or not isinstance(receipt.get("requested_url"), str)
            or not receipt["requested_url"]
            or not isinstance(receipt.get("final_url"), str)
            or not receipt["final_url"]
            or (
                receipt.get("http_status") is not None
                and (
                    not isinstance(receipt["http_status"], int)
                    or isinstance(receipt["http_status"], bool)
                )
            )
            or not isinstance(receipt.get("content_type"), str)
            or receipt.get("status") not in {"FETCHED", "FAILED"}
            or (receipt["status"] == "FAILED" and not isinstance(receipt.get("error_code"), str))
            or (receipt["status"] == "FETCHED" and receipt.get("error_code") is not None)
            or not isinstance(receipt.get("attempted_at"), str)
            or not isinstance(receipt.get("adapter_build_id"), str)
            or (raw_artifact_id is None) != (raw_byte_length is None)
        ):
            raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "site attempt receipt is invalid")
        if raw_artifact_id is not None and (
            not isinstance(raw_artifact_id, str)
            or not isinstance(raw_byte_length, int)
            or isinstance(raw_byte_length, bool)
            or raw_byte_length < 0
            or len(store.get(raw_artifact_id)) != raw_byte_length
        ):
            raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "site attempt raw artifact changed")
        if receipt["retrieval_id"] in by_retrieval_id:
            raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "duplicate site retrieval identity")
        ordinals.add(ordinal)
        by_retrieval_id[receipt["retrieval_id"]] = receipt
    if ordinals and ordinals != set(range(1, len(ordinals) + 1)):
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "site attempt ordinals are not consecutive")
    for receipt in receipts:
        retry_of = receipt.get("retry_of")
        if retry_of is None:
            continue
        previous = by_retrieval_id.get(retry_of)
        if (
            previous is None
            or previous["status"] != "FAILED"
            or previous["stage"] != receipt["stage"]
            or previous["requested_url"] != receipt["requested_url"]
            or previous["attempt_ordinal"] >= receipt["attempt_ordinal"]
        ):
            raise ValidationError(
                "E-SITE-ATTEMPT-INTEGRITY",
                "site retry does not target the prior failed chapter/index attempt",
            )
    for receipt, receipt_artifact_id in zip(receipts, receipt_ids):
        receipt["receipt_artifact_id"] = receipt_artifact_id
    return sorted(receipts, key=lambda receipt: receipt["attempt_ordinal"])


def _site_source_record(receipt: dict[str, Any]) -> dict[str, Any]:
    source_id = derived_id(
        "Source",
        {
            "platform_id": "novel:site",
            "stage": receipt["stage"],
            "requested_url": receipt["requested_url"],
        },
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "canonical_url": receipt["requested_url"],
        "platform_id": "novel:site",
        "title": "",
        "author": "",
        "work": "",
        "document_location": receipt["stage"].casefold(),
        "same_platform_as": None,
    }


def _site_retrieval_record(receipt: dict[str, Any]) -> dict[str, Any]:
    content_type = receipt["content_type"]
    if receipt["status"] == "FETCHED":
        content_type = content_type.split(";", 1)[0].strip().casefold()
    return {
        "schema_version": SCHEMA_VERSION,
        "retrieval_id": receipt["retrieval_id"],
        "source_id": _site_source_record(receipt)["source_id"],
        "requested_url": receipt["requested_url"],
        "final_url": receipt["final_url"],
        "access_kind": "index_page" if receipt["stage"] == "INDEX" else "full_text_chapter",
        "retrieved_at": receipt["attempted_at"],
        "http_status": receipt["http_status"],
        "content_type": content_type,
        "fetcher_build_id": receipt["adapter_build_id"],
        "status": receipt["status"],
        "retry_of": receipt["retry_of"],
    }


class _SiteAttemptJournal:
    def __init__(self, work_dir: pathlib.Path, store: ArtifactStore, *, now: str) -> None:
        self.work_dir = work_dir
        self.store = store
        self.now = now
        self.adapter_build_id = ""
        marker_ids = _read_site_attempt_marker_ids(work_dir)
        self.receipts = _read_site_attempt_receipts(store, marker_ids)

    @property
    def receipt_ids(self) -> list[str]:
        return [receipt["receipt_artifact_id"] for receipt in self.receipts]

    def record(self, event: dict[str, Any]) -> None:
        raw = event.pop("raw_response_bytes", None)
        if raw is not None and not isinstance(raw, bytes):
            raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "site attempt raw response is not bytes")
        raw_artifact_id = self.store.put(raw) if raw is not None else None
        previous = next(
            (
                receipt
                for receipt in reversed(self.receipts)
                if receipt["stage"] == event["stage"]
                and receipt["requested_url"] == event["requested_url"]
            ),
            None,
        )
        retry_of = previous["retrieval_id"] if previous and previous["status"] == "FAILED" else None
        identity = {
            "attempt_ordinal": len(self.receipts) + 1,
            "stage": event["stage"],
            "requested_url": event["requested_url"],
            "final_url": event["final_url"],
            "http_status": event["http_status"],
            "content_type": event["content_type"],
            "status": event["status"],
            "error_code": event["error_code"],
            "error_message": event["error_message"],
            "raw_artifact_id": raw_artifact_id,
            "raw_byte_length": len(raw) if raw is not None else None,
            "attempted_at": self.now,
            "retry_of": retry_of,
            "adapter_build_id": self.adapter_build_id,
        }
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "retrieval_id": derived_id("Retrieval", identity),
            **identity,
        }
        receipt_artifact_id = self.store.put(_json_bytes(receipt))
        marker = {"attempt_receipt_artifact_id": receipt_artifact_id}
        _write_immutable(
            _site_attempt_marker_path(self.work_dir, receipt_artifact_id),
            _json_bytes(marker),
        )
        receipt["receipt_artifact_id"] = receipt_artifact_id
        self.receipts.append(receipt)
        if receipt["status"] == "FAILED":
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "attempt_receipt_artifact_id": receipt_artifact_id,
                "attempt": {key: value for key, value in receipt.items() if key != "receipt_artifact_id"},
                "source": _site_source_record(receipt),
                "retrieval": _site_retrieval_record(receipt),
                "raw_artifact_id": raw_artifact_id,
            }
            _write_immutable(
                self.work_dir / "failures" / receipt["retrieval_id"] / "failure-manifest.json",
                _json_bytes(manifest),
            )


def _materialize_site_attempts(
    catalog: Catalog,
    store: ArtifactStore,
    receipts: list[dict[str, Any]],
) -> None:
    for receipt in receipts:
        source = _site_source_record(receipt)
        if not any(item["source_id"] == source["source_id"] for item in catalog.all("Source")):
            catalog.add("Source", source)
        raw_artifact_id = receipt["raw_artifact_id"]
        if raw_artifact_id is not None:
            media_type = (
                receipt["content_type"].split(";", 1)[0].strip().casefold()
                if receipt["status"] == "FETCHED" and receipt["content_type"]
                else "application/octet-stream"
            )
            _add_artifact_once(
                catalog,
                _artifact_record(
                    raw_artifact_id,
                    media_type=media_type or "application/octet-stream",
                    byte_length=receipt["raw_byte_length"],
                    created_at=receipt["attempted_at"],
                ),
            )
        retrieval = _site_retrieval_record(receipt)
        catalog.add("Retrieval", retrieval)
        if raw_artifact_id is not None:
            catalog.add(
                "RetrievalArtifact",
                {
                    "schema_version": SCHEMA_VERSION,
                    "retrieval_id": retrieval["retrieval_id"],
                    "artifact_id": raw_artifact_id,
                    "role": "RAW_RESPONSE",
                },
            )


def _read_completion_markers(work_dir: pathlib.Path) -> dict[str, str]:
    marker_root = work_dir / "completion-receipts"
    observed: dict[str, str] = {}
    if marker_root.exists():
        for path in sorted(marker_root.glob("*/*.json")):
            try:
                raw = path.read_bytes()
                marker = json.loads(raw.decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValidationError("E-CHECKPOINT-INTEGRITY", f"invalid completion marker {path}") from exc
            if (
                not isinstance(marker, dict)
                or set(marker) != {"chapter_key", "receipt_artifact_id"}
                or raw != _json_bytes(marker)
                or not isinstance(marker["chapter_key"], str)
                or not isinstance(marker["receipt_artifact_id"], str)
                or _completion_marker_path(
                    work_dir,
                    marker["chapter_key"],
                    marker["receipt_artifact_id"],
                )
                != path
                or marker["chapter_key"] in observed
            ):
                raise ValidationError("E-CHECKPOINT-INTEGRITY", f"invalid completion marker {path}")
            observed[marker["chapter_key"]] = marker["receipt_artifact_id"]
    return observed


def _validate_completion_markers(
    work_dir: pathlib.Path,
    completed: dict[str, dict[str, Any]],
) -> None:
    observed = _read_completion_markers(work_dir)
    expected = {
        chapter_key: result["receipt_artifact_id"] for chapter_key, result in completed.items()
    }
    if observed != expected:
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "completed chapters differ from immutable markers")


def _reconcile_orphan_completion_markers(
    state: dict[str, Any],
    refs: list[ChapterRef],
    store: ArtifactStore,
    *,
    work_dir: pathlib.Path,
) -> bool:
    completed = state.get("completed")
    if not isinstance(completed, dict):
        raise ValidationError("E-CHECKPOINT-CORRUPT", "checkpoint completed must be an object")
    markers = _read_completion_markers(work_dir)
    refs_by_key = {ref.chapter_key: ref for ref in refs}
    ref_keys = set(refs_by_key)
    if set(markers) - ref_keys:
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "completion marker is outside frozen chapter refs")
    for chapter_key, result in completed.items():
        if not isinstance(result, dict) or markers.get(chapter_key) != result.get("receipt_artifact_id"):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint completion marker is missing or changed")
    reconciled = False
    source_kind = str(state.get("work", {}).get("source_kind", ""))
    for chapter_key in sorted(set(markers) - set(completed)):
        receipt_artifact_id = markers[chapter_key]
        receipt = _read_canonical_json_artifact(store, receipt_artifact_id, label="orphan completion receipt")
        if set(receipt) != {
            "chapter_key",
            "artifact_id",
            "byte_length",
            "media_type",
            "final_locator",
            "http_status",
            "retrieved_at",
        } or receipt.get("chapter_key") != chapter_key:
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "orphan completion receipt has an invalid shape")
        artifact_id = receipt.get("artifact_id")
        byte_length = receipt.get("byte_length")
        http_status = receipt.get("http_status")
        if (
            not isinstance(artifact_id, str)
            or not isinstance(byte_length, int)
            or isinstance(byte_length, bool)
            or byte_length < 0
            or not isinstance(receipt.get("media_type"), str)
            or not receipt["media_type"]
            or not isinstance(receipt.get("final_locator"), str)
            or not receipt["final_locator"]
            or not isinstance(receipt.get("retrieved_at"), str)
            or (source_kind == "SITE" and http_status != 200)
            or (source_kind != "SITE" and http_status is not None)
            or (
                source_kind != "SITE"
                and (
                    receipt.get("final_locator") != refs_by_key[chapter_key].source_locator
                    or receipt.get("media_type") != refs_by_key[chapter_key].media_type
                )
            )
        ):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "orphan completion receipt is invalid")
        if len(store.get(artifact_id)) != byte_length:
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "orphan completion artifact length changed")
        completed[chapter_key] = {
            key: value for key, value in receipt.items() if key != "chapter_key"
        }
        completed[chapter_key]["receipt_artifact_id"] = receipt_artifact_id
        reconciled = True
    return reconciled


def _validate_provenance(state: dict[str, Any], store: ArtifactStore) -> None:
    provenance = state.get("provenance")
    if not isinstance(provenance, list):
        raise ValidationError("E-CHECKPOINT-CORRUPT", "checkpoint provenance must be an array")
    for item in provenance:
        if not isinstance(item, dict) or set(item) != _PROVENANCE_FIELDS:
            raise ValidationError("E-CHECKPOINT-CORRUPT", "invalid checkpoint provenance record")
        artifact_id = item.get("artifact_id")
        byte_length = item.get("byte_length")
        if not isinstance(artifact_id, str) or not isinstance(byte_length, int) or byte_length < 0:
            raise ValidationError("E-CHECKPOINT-CORRUPT", "invalid checkpoint provenance identity")
        if len(store.get(artifact_id)) != byte_length:
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint provenance length changed")


def _validate_completed(
    state: dict[str, Any],
    refs: list[ChapterRef],
    store: ArtifactStore,
    *,
    work_dir: pathlib.Path,
) -> None:
    completed = state.get("completed")
    if not isinstance(completed, dict):
        raise ValidationError("E-CHECKPOINT-CORRUPT", "checkpoint completed must be an object")
    refs_by_key = {ref.chapter_key: ref for ref in refs}
    ref_keys = set(refs_by_key)
    source_kind = str(state.get("work", {}).get("source_kind", ""))
    if any(not isinstance(key, str) or key not in ref_keys for key in completed):
        raise ValidationError("E-CHECKPOINT-CORRUPT", "completed chapter is not present in frozen discovery")
    _validate_completion_markers(work_dir, completed)
    for chapter_key, result in completed.items():
        if not isinstance(result, dict) or set(result) != _COMPLETION_FIELDS:
            raise ValidationError("E-CHECKPOINT-CORRUPT", "invalid completed chapter record")
        artifact_id = result.get("artifact_id")
        byte_length = result.get("byte_length")
        receipt_artifact_id = result.get("receipt_artifact_id")
        if (
            not isinstance(artifact_id, str)
            or not isinstance(byte_length, int)
            or byte_length < 0
            or not isinstance(receipt_artifact_id, str)
            or not isinstance(result.get("media_type"), str)
            or not isinstance(result.get("final_locator"), str)
            or (
                result.get("http_status") is not None
                and (
                    not isinstance(result.get("http_status"), int)
                    or isinstance(result.get("http_status"), bool)
                )
            )
            or not isinstance(result.get("retrieved_at"), str)
            or (
                source_kind != "SITE"
                and (
                    result.get("final_locator") != refs_by_key[chapter_key].source_locator
                    or result.get("media_type") != refs_by_key[chapter_key].media_type
                )
            )
        ):
            raise ValidationError("E-CHECKPOINT-CORRUPT", "invalid completed chapter identity")
        if len(store.get(artifact_id)) != byte_length:
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "completed chapter length changed")
        receipt = _read_canonical_json_artifact(store, receipt_artifact_id, label="completion receipt")
        if receipt != _completion_payload(chapter_key, result):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "completed chapter differs from its receipt")


def _validate_checkpoint_identity(
    state: dict[str, Any],
    *,
    input_spec_hash: str,
    adapter_build_id: str,
    store: ArtifactStore,
    work_dir: pathlib.Path,
) -> bool:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("E-CHECKPOINT-VERSION", "checkpoint schema version changed")
    if state.get("input_spec_hash") != input_spec_hash:
        raise ValidationError("E-CHECKPOINT-INPUT", "checkpoint belongs to another ingestion spec")
    if state.get("adapter_build_id") != adapter_build_id:
        raise ValidationError("E-CHECKPOINT-BUILD", "checkpoint belongs to another adapter build")
    discovery_artifact_id = state.get("discovery_artifact_id")
    if not isinstance(discovery_artifact_id, str):
        raise ValidationError("E-CHECKPOINT-CORRUPT", "checkpoint has no frozen discovery artifact")
    frozen_discovery = _read_canonical_json_artifact(store, discovery_artifact_id, label="discovery")
    if frozen_discovery != _discovery_payload(state):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint discovery differs from its frozen artifact")
    if state.get("discovery_complete") is not True:
        raise ValidationError("E-CHECKPOINT-CORRUPT", "checkpoint discovery is not complete")
    refs = _checkpoint_refs(state)
    _work_from_checkpoint(state)
    _validate_provenance(state, store)
    reconciled = _reconcile_orphan_completion_markers(state, refs, store, work_dir=work_dir)
    _validate_completed(state, refs, store, work_dir=work_dir)
    return reconciled


def _reconcile_site_attempt_ids(
    state: dict[str, Any],
    journal: _SiteAttemptJournal | None,
) -> bool:
    stored = state.get("site_attempt_receipt_ids", [])
    if not isinstance(stored, list) or any(not isinstance(item, str) for item in stored):
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "checkpoint site attempts are invalid")
    if journal is None:
        if stored:
            raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "local checkpoint contains site attempts")
        return False
    observed = journal.receipt_ids
    if stored != observed[: len(stored)]:
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "checkpoint site attempt history changed")
    if stored == observed:
        return False
    state["site_attempt_receipt_ids"] = observed
    return True


def _verify_current_derived_source(adapter: Any, state: dict[str, Any]) -> None:
    refs = _checkpoint_refs(state)
    source_kind = (state.get("work") or {}).get("source_kind")
    if source_kind != "DIRECTORY" and not any(ref.derived_from_provenance for ref in refs):
        return
    current = adapter.discover()
    expected_provenance = [
        {
            "artifact_id": item["artifact_id"],
            "locator": item["locator"],
            "media_type": item["media_type"],
            "byte_length": item["byte_length"],
        }
        for item in state["provenance"]
    ]
    current_provenance = [
        {
            "artifact_id": artifact_id_for(blob.data),
            "locator": blob.locator,
            "media_type": blob.media_type,
            "byte_length": len(blob.data),
        }
        for blob in current.provenance
    ]
    if current_provenance != expected_provenance:
        raise ValidationError(
            "E-NOVEL-SOURCE-CHANGED",
            "current complete source bytes differ from the frozen provenance",
        )
    current_work = {
        "title": current.work.title,
        "author": current.work.author,
        "language": current.work.language,
        "source_kind": current.work.source_kind,
        "source_locator": current.work.source_locator,
    }
    if current_work != state["work"] or [ref.to_checkpoint() for ref in current.chapters] != state["chapter_refs"]:
        raise ValidationError(
            "E-NOVEL-SOURCE-CHANGED",
            "current source discovery differs from the frozen checkpoint",
        )


def _order_validation(chapters: list[dict[str, Any]], *, strict: bool) -> dict[str, Any]:
    ready = [item for item in chapters if item["status"] == "READY"]
    out_of_order: list[str] = []
    missing_numbers: list[int] = []
    numbered = [(item["chapter_id"], item.get("declared_number")) for item in ready]
    present = [(chapter_id, number) for chapter_id, number in numbered if number is not None]
    # Discovery order cannot prove declared chapter order when any number is
    # unknown. Strict validation must fail closed instead of filtering those
    # chapters out and reporting a false PASS.
    has_unknown_numbers = len(present) != len(ready)
    for index in range(1, len(present)):
        if int(present[index][1]) <= int(present[index - 1][1]):
            out_of_order.append(present[index][0])
    if present and len(present) == len(ready):
        numbers = [int(number) for _, number in present]
        unique_numbers = sorted(set(numbers))
        missing_count = sum(
            max(0, upper - lower - 1) for lower, upper in zip(unique_numbers, unique_numbers[1:])
        )
        if missing_count > MAX_REPORTED_MISSING_CHAPTERS:
            raise ValidationError(
                "E-CHAPTER-ORDER",
                f"declared chapter numbers imply {missing_count} missing chapters",
            )
        for lower, upper in zip(unique_numbers, unique_numbers[1:]):
            missing_numbers.extend(range(lower + 1, upper))
    duplicate_ids = [item["chapter_id"] for item in chapters if item["status"] == "DUPLICATE"]
    has_issue = bool(has_unknown_numbers or out_of_order or missing_numbers or duplicate_ids)
    return {
        "status": "FAIL" if strict and has_issue else "WARNING" if has_issue else "PASS",
        "out_of_order_chapter_ids": out_of_order,
        "missing_declared_numbers": missing_numbers,
        "duplicate_chapter_ids": duplicate_ids,
    }


def _catalog_json(catalog: Catalog) -> dict[str, list[dict[str, Any]]]:
    return {kind: values for kind, values in catalog.by_type.items() if values}


def novel_ingestion_artifact_ids(
    catalog: Catalog,
    store: ArtifactStore,
    ingestion: dict[str, Any],
) -> list[str]:
    """Return the complete, verified CAS closure needed to audit an ingestion."""
    checkpoint_artifact_id = ingestion.get("checkpoint_artifact_id")
    if not isinstance(checkpoint_artifact_id, str):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "ingestion lacks a checkpoint artifact")
    checkpoint = _read_canonical_json_artifact(
        store,
        checkpoint_artifact_id,
        label="checkpoint",
    )
    if (
        checkpoint.get(CHECKPOINT_INTEGRITY_FIELD) != _checkpoint_integrity_hash(checkpoint)
        or object_hash(checkpoint, omit=()) != ingestion.get("checkpoint_hash")
    ):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "stored checkpoint differs from ingestion run")

    artifact_ids = {
        ingestion.get("input_spec_artifact_id"),
        checkpoint_artifact_id,
        *(ingestion.get("provenance_artifact_ids") or []),
    }
    discovery_artifact_id = checkpoint.get("discovery_artifact_id")
    if not isinstance(discovery_artifact_id, str):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint lacks frozen discovery")
    artifact_ids.add(discovery_artifact_id)
    if _read_canonical_json_artifact(
        store,
        discovery_artifact_id,
        label="discovery",
    ) != _discovery_payload(checkpoint):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint discovery differs from frozen artifact")

    completed = checkpoint.get("completed")
    if not isinstance(completed, dict):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint completions are invalid")
    for chapter_key, result in completed.items():
        if not isinstance(chapter_key, str) or not isinstance(result, dict):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint completion is invalid")
        artifact_id = result.get("artifact_id")
        receipt_artifact_id = result.get("receipt_artifact_id")
        if not isinstance(artifact_id, str) or not isinstance(receipt_artifact_id, str):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint completion lacks artifacts")
        receipt = _read_canonical_json_artifact(
            store,
            receipt_artifact_id,
            label="completion receipt",
        )
        if receipt != _completion_payload(chapter_key, result):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "completion differs from frozen receipt")
        artifact_ids.update((artifact_id, receipt_artifact_id))

    attempt_receipt_ids = checkpoint.get("site_attempt_receipt_ids", [])
    if not isinstance(attempt_receipt_ids, list) or any(
        not isinstance(receipt_id, str) for receipt_id in attempt_receipt_ids
    ):
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "checkpoint site attempt list is invalid")
    for receipt in _read_site_attempt_receipts(store, attempt_receipt_ids):
        artifact_ids.add(receipt["receipt_artifact_id"])
        if receipt["raw_artifact_id"] is not None:
            artifact_ids.add(receipt["raw_artifact_id"])

    if None in artifact_ids:
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "ingestion artifact closure is incomplete")
    for artifact_id in artifact_ids:
        catalog.get("Artifact", artifact_id)
        store.verify(artifact_id)
    return sorted_ids(artifact_ids)


def _url_origin(url: str) -> tuple[str, str, int | None]:
    try:
        parsed = urlparse(url)
        return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port
    except ValueError as exc:
        raise ValidationError("E-NOVEL-SCOPE", f"invalid URL {url!r}") from exc


def _source_kind_for_spec(source_spec: dict[str, Any]) -> str:
    kind = str(source_spec.get("kind", "")).casefold()
    mapping = {
        "txt": "TXT",
        "epub": "EPUB",
        "directory": "DIRECTORY",
        "chapter-directory": "DIRECTORY",
        "site": "SITE",
        "static-site": "SITE",
    }
    try:
        return mapping[kind]
    except KeyError as exc:
        raise ValidationError("E-NOVEL-SPEC", f"unsupported stored source kind {kind!r}") from exc


def _validate_run_checkpoint_bindings(
    catalog: Catalog,
    store: ArtifactStore,
    run: dict[str, Any],
    work: dict[str, Any],
    chapters: list[dict[str, Any]],
    input_spec: dict[str, Any],
    checkpoint: dict[str, Any],
) -> None:
    if checkpoint.get(CHECKPOINT_INTEGRITY_FIELD) != _checkpoint_integrity_hash(checkpoint):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "stored checkpoint integrity hash changed")
    if (
        checkpoint.get("input_spec_hash") != run["input_spec_hash"]
        or checkpoint.get("adapter_build_id") != run["adapter_build_id"]
    ):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint identity differs from ingestion run")
    discovery_artifact_id = checkpoint.get("discovery_artifact_id")
    if not isinstance(discovery_artifact_id, str):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint lacks frozen discovery")
    if _read_canonical_json_artifact(store, discovery_artifact_id, label="discovery") != _discovery_payload(
        checkpoint
    ):
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint discovery differs from frozen artifact")
    refs = _checkpoint_refs(checkpoint)
    checkpoint_work = _work_from_checkpoint(checkpoint)
    if {
        "title": checkpoint_work.title,
        "author": checkpoint_work.author,
        "language": checkpoint_work.language,
        "source_kind": checkpoint_work.source_kind,
        "source_locator": checkpoint_work.source_locator,
    } != {
        "title": work["title"],
        "author": work.get("author"),
        "language": work["language"],
        "source_kind": work["source_kind"],
        "source_locator": work["source_locator"],
    } or work["adapter_build_id"] != run["adapter_build_id"]:
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint work differs from catalog work")
    _validate_provenance(checkpoint, store)
    provenance_ids = [item["artifact_id"] for item in checkpoint["provenance"]]
    if provenance_ids != run["provenance_artifact_ids"] or provenance_ids != work["source_artifact_ids"]:
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "checkpoint provenance differs from run lineage")

    completed = checkpoint.get("completed")
    if not isinstance(completed, dict) or set(completed) != {ref.chapter_key for ref in refs}:
        raise ValidationError("E-CHECKPOINT-INTEGRITY", "final checkpoint has an incomplete chapter partition")
    if [ref.chapter_key for ref in refs] != [chapter["chapter_key"] for chapter in chapters]:
        raise ValidationError("E-CHAPTER-ORDER", "checkpoint refs differ from catalog chapter order")

    source_spec = input_spec.get("source")
    if not isinstance(source_spec, dict):
        raise ValidationError("E-NOVEL-SPEC", "stored ingestion spec has no source")
    source_kind = _source_kind_for_spec(source_spec)
    if source_kind != work["source_kind"]:
        raise ValidationError("E-NOVEL-SPEC", "stored source kind differs from catalog work")
    attempt_receipt_ids = checkpoint.get("site_attempt_receipt_ids", [])
    if not isinstance(attempt_receipt_ids, list) or any(
        not isinstance(receipt_id, str) for receipt_id in attempt_receipt_ids
    ):
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "checkpoint site attempt list is invalid")
    site_attempt_receipts = _read_site_attempt_receipts(store, attempt_receipt_ids)
    if source_kind != "SITE" and site_attempt_receipts:
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "local ingestion contains site attempts")
    if source_kind == "SITE" and not site_attempt_receipts:
        raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "site ingestion has no recorded attempts")
    for attempt in site_attempt_receipts:
        expected_source = _site_source_record(attempt)
        source_record = catalog.get("Source", expected_source["source_id"])
        if any(
            source_record.get(key) != expected_source[key]
            for key in ("schema_version", "source_id", "canonical_url", "platform_id", "same_platform_as")
        ):
            raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "site attempt source changed")
        retrieval_record = catalog.get("Retrieval", attempt["retrieval_id"])
        expected_retrieval = _site_retrieval_record(attempt)
        if any(retrieval_record.get(key) != value for key, value in expected_retrieval.items()):
            raise ValidationError("E-NOVEL-RETRIEVAL-BIND", "site attempt retrieval changed")
        raw_edges = [
            edge
            for edge in catalog.all("RetrievalArtifact")
            if edge["retrieval_id"] == attempt["retrieval_id"] and edge["role"] == "RAW_RESPONSE"
        ]
        raw_artifact_id = attempt["raw_artifact_id"]
        if raw_artifact_id is None:
            if raw_edges:
                raise ValidationError("E-SITE-ATTEMPT-INTEGRITY", "bodyless attempt gained an artifact")
        elif len(raw_edges) != 1 or raw_edges[0]["artifact_id"] != raw_artifact_id:
            raise ValidationError("E-NOVEL-RETRIEVAL-BIND", "site attempt raw artifact changed")
    chapter_pattern = None
    index_origin = None
    allow_external = False
    if source_kind == "SITE":
        pattern_text = source_spec.get("chapter_url_pattern")
        index_url = source_spec.get("index_url")
        if not isinstance(pattern_text, str) or not pattern_text or not isinstance(index_url, str):
            raise ValidationError("E-NOVEL-SPEC", "stored site source lacks URL policy")
        if "allow_external_chapters" in source_spec and not isinstance(
            source_spec["allow_external_chapters"], bool
        ):
            raise ValidationError("E-NOVEL-SPEC", "stored external chapter policy is not boolean")
        try:
            chapter_pattern = re.compile(pattern_text)
        except re.error as exc:
            raise ValidationError("E-NOVEL-SPEC", f"stored chapter URL pattern is invalid: {exc}") from exc
        index_origin = _url_origin(index_url)
        allow_external = source_spec.get("allow_external_chapters", False)

    for ref, chapter in zip(refs, chapters):
        result = completed[ref.chapter_key]
        if not isinstance(result, dict) or set(result) != _COMPLETION_FIELDS:
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "completed chapter record has an invalid shape")
        receipt_artifact_id = result.get("receipt_artifact_id")
        if not isinstance(receipt_artifact_id, str):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "completed chapter lacks a receipt")
        receipt = _read_canonical_json_artifact(store, receipt_artifact_id, label="completion receipt")
        if receipt != _completion_payload(ref.chapter_key, result):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "completed chapter differs from its receipt")
        artifact_id = result.get("artifact_id")
        if not isinstance(artifact_id, str) or len(store.get(artifact_id)) != result.get("byte_length"):
            raise ValidationError("E-CHECKPOINT-INTEGRITY", "completed chapter artifact changed")

        retrieval = catalog.get("Retrieval", chapter["retrieval_id"])
        source = catalog.get("Source", chapter["source_id"])
        if source_kind != "SITE" and (result.get("http_status") is not None or retrieval.get("http_status") is not None):
            raise ValidationError("E-NOVEL-HTTP-BIND", "local novel retrieval cannot have an HTTP status")
        if source_kind != "SITE" and (
            result.get("final_locator") != ref.source_locator
            or result.get("media_type") != ref.media_type
        ):
            raise ValidationError(
                "E-NOVEL-RETRIEVAL-BIND",
                "local completion differs from its frozen chapter ref",
            )
        if (
            retrieval["source_id"] != chapter["source_id"]
            or retrieval.get("requested_url") != ref.source_locator
            or retrieval.get("final_url") != result.get("final_locator")
            or retrieval.get("http_status") != result.get("http_status")
            or retrieval.get("content_type") != result.get("media_type")
            or retrieval.get("status") != "FETCHED"
            or retrieval.get("fetcher_build_id") != run["adapter_build_id"]
            or chapter["artifact_id"] != artifact_id
            or chapter["source_locator"] != result.get("final_locator")
            or chapter["media_type"] != result.get("media_type")
            or source.get("canonical_url")
            != (ref.source_locator if source_kind == "SITE" else result.get("final_locator"))
        ):
            raise ValidationError("E-NOVEL-RETRIEVAL-BIND", "retrieval differs from frozen chapter completion")
        raw_links = [
            link
            for link in catalog.all("RetrievalArtifact")
            if link["retrieval_id"] == retrieval["retrieval_id"] and link["role"] == "RAW_RESPONSE"
        ]
        if len(raw_links) != 1 or raw_links[0]["artifact_id"] != artifact_id:
            raise ValidationError("E-NOVEL-RETRIEVAL-BIND", "retrieval artifact differs from completed chapter")
        artifact = catalog.get("Artifact", artifact_id)
        if artifact["media_type"] != result["media_type"] or artifact["byte_length"] != result["byte_length"]:
            raise ValidationError("E-NOVEL-RETRIEVAL-BIND", "artifact metadata differs from completed chapter")

        if source_kind == "SITE":
            requested_url = ref.source_locator
            final_url = str(result["final_locator"])
            if result.get("http_status") != 200:
                raise ValidationError("E-NOVEL-HTTP-BIND", "site chapter retrieval must bind HTTP 200")
            assert chapter_pattern is not None and index_origin is not None
            if not chapter_pattern.search(requested_url) or not chapter_pattern.search(final_url):
                raise ValidationError("E-NOVEL-SCOPE", "site chapter URL does not match frozen pattern")
            if not allow_external and (
                _url_origin(requested_url) != index_origin or _url_origin(final_url) != index_origin
            ):
                raise ValidationError("E-NOVEL-SCOPE", "site chapter URL leaves frozen origin policy")


def load_novel_spec(path: pathlib.Path) -> dict[str, Any]:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-NOVEL-SPEC", f"cannot read novel spec {path}") from exc
    if not isinstance(spec, dict):
        raise ValidationError("E-NOVEL-SPEC", "novel spec must be an object")
    source_catalog = spec.get("source_catalog")
    if source_catalog is not None and not isinstance(source_catalog, list):
        raise ValidationError(
            "E-NOVEL-SOURCE-CATALOG",
            "source_catalog must be an array",
        )
    sources = [spec.get("source")]
    for entry in source_catalog or []:
        if isinstance(entry, dict):
            sources.append(entry.get("source"))
    for source in sources:
        if isinstance(source, dict) and isinstance(source.get("path"), str):
            source_path = pathlib.Path(source["path"]).expanduser()
            if not source_path.is_absolute():
                source["path"] = str((path.parent / source_path).resolve())
    return spec


def _input_limit(
    limits: dict[str, Any],
    field: str,
    default: int,
    *,
    minimum: int,
) -> int:
    value = limits.get(field, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValidationError(
            "E-NOVEL-LIMIT",
            f"limits.{field} must be an integer of at least {minimum}",
        )
    return value


def run_novel_ingestion(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    repo_root: pathlib.Path,
    fetcher: Any | None = None,
    now: str,
    catalog: Catalog | None = None,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    work_dir = pathlib.Path(work_dir)
    with _exclusive_work_dir(work_dir):
        return _run_novel_ingestion_unlocked(
            spec,
            work_dir,
            repo_root=repo_root,
            fetcher=fetcher,
            now=now,
            catalog=catalog,
            store=store,
        )


def _run_novel_ingestion_unlocked(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    repo_root: pathlib.Path,
    fetcher: Any | None = None,
    now: str,
    catalog: Catalog | None = None,
    store: ArtifactStore | None = None,
) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise ValidationError("E-NOVEL-SPEC", "novel spec must be an object")
    source_spec = spec.get("source")
    if not isinstance(source_spec, dict):
        raise ValidationError("E-NOVEL-SPEC", "source must be an object")
    limits = spec.get("limits", {})
    if not isinstance(limits, dict):
        raise ValidationError("E-NOVEL-LIMIT", "limits must be an object")
    max_chapters = _input_limit(limits, "max_chapters", 100_000, minimum=1)
    max_bytes = _input_limit(limits, "max_bytes", 500_000_000, minimum=0)
    strict_order = spec.get("strict_order", False)
    if not isinstance(strict_order, bool):
        raise ValidationError("E-NOVEL-SPEC", "strict_order must be a boolean")
    work_dir = pathlib.Path(work_dir)
    store = store or ArtifactStore(work_dir / "objects")
    source_kind = str(source_spec.get("kind", "")).casefold()
    is_site = source_kind in {"site", "static-site"}
    attempt_journal = _SiteAttemptJournal(work_dir, store, now=now) if is_site else None
    adapter_spec = dict(source_spec)
    adapter_spec["_ingestion_max_bytes"] = max_bytes
    adapter_spec["_ingestion_max_chapters"] = max_chapters
    adapter = adapter_from_spec(
        adapter_spec,
        fetcher=fetcher,
        attempt_recorder=attempt_journal.record if attempt_journal else None,
    )
    adapter_build_id = adapter.adapter_id
    if attempt_journal is not None:
        attempt_journal.adapter_build_id = adapter_build_id
    input_spec_bytes = _json_bytes(spec)
    input_spec_hash = object_hash(spec, omit=())
    input_spec_artifact_id = store.put(input_spec_bytes)
    checkpoint_path = work_dir / "ingestion-checkpoint.json"
    state = _load_checkpoint(checkpoint_path)
    resumed = state is not None
    if state is not None:
        reconciled = _validate_checkpoint_identity(
            state,
            input_spec_hash=input_spec_hash,
            adapter_build_id=adapter_build_id,
            store=store,
            work_dir=work_dir,
        )
        attempts_reconciled = _reconcile_site_attempt_ids(state, attempt_journal)
        if reconciled or attempts_reconciled:
            _write_checkpoint(checkpoint_path, state)
        refs = _checkpoint_refs(state)
        work = _work_from_checkpoint(state)
        _verify_current_derived_source(adapter, state)
    else:
        discovery = adapter.discover()
        work = discovery.work
        provenance = []
        for blob in discovery.provenance:
            artifact_id = store.put(blob.data)
            provenance.append(
                {
                    "artifact_id": artifact_id,
                    "locator": blob.locator,
                    "media_type": blob.media_type,
                    "byte_length": len(blob.data),
                    "created_at": now,
                }
            )
        refs = discovery.chapters
        state = {
            "schema_version": SCHEMA_VERSION,
            "input_spec_hash": input_spec_hash,
            "adapter_build_id": adapter_build_id,
            "discovery_complete": True,
            "work": {
                "title": work.title,
                "author": work.author,
                "language": work.language,
                "source_kind": work.source_kind,
                "source_locator": work.source_locator,
            },
            "chapter_refs": [ref.to_checkpoint() for ref in refs],
            "provenance": provenance,
            "completed": {},
            "site_attempt_receipt_ids": attempt_journal.receipt_ids if attempt_journal else [],
            "started_at": now,
        }
        state["discovery_artifact_id"] = store.put(_json_bytes(_discovery_payload(state)))
        _write_checkpoint(checkpoint_path, state)

    if len(refs) > max_chapters:
        raise ValidationError("E-NOVEL-LIMIT", f"chapter count exceeds {max_chapters}")
    completed: dict[str, dict[str, Any]] = state.setdefault("completed", {})
    total_bytes = sum(int(item["byte_length"]) for item in completed.values())
    for ref in refs:
        if ref.chapter_key in completed:
            continue
        try:
            data, media_type, final_locator, http_status = adapter.fetch_chapter(ref)
            total_bytes += len(data)
            if total_bytes > max_bytes:
                raise ValidationError("E-NOVEL-LIMIT", f"ingestion exceeds {max_bytes} bytes")
            artifact_id = store.put(data)
            result = {
                "artifact_id": artifact_id,
                "byte_length": len(data),
                "media_type": media_type,
                "final_locator": final_locator,
                "http_status": http_status,
                "retrieved_at": now,
            }
            result["receipt_artifact_id"] = store.put(
                _json_bytes(_completion_payload(ref.chapter_key, result))
            )
            _write_completion_marker(work_dir, ref.chapter_key, result["receipt_artifact_id"])
            completed[ref.chapter_key] = result
            state["site_attempt_receipt_ids"] = attempt_journal.receipt_ids if attempt_journal else []
            state.pop("last_error", None)
            _write_checkpoint(checkpoint_path, state)
        except Exception as exc:
            state["site_attempt_receipt_ids"] = attempt_journal.receipt_ids if attempt_journal else []
            state["last_error"] = {
                "chapter_key": ref.chapter_key,
                "error": str(exc),
                "recorded_at": now,
            }
            _write_checkpoint(checkpoint_path, state)
            raise

    state["finished_at"] = now
    state["site_attempt_receipt_ids"] = attempt_journal.receipt_ids if attempt_journal else []
    state.pop("last_error", None)
    _write_checkpoint(checkpoint_path, state)
    checkpoint_bytes = _json_bytes(state)
    checkpoint_hash = object_hash(state, omit=())
    checkpoint_artifact_id = store.put(checkpoint_bytes)

    catalog = catalog or Catalog()
    _add_artifact_once(
        catalog,
        _artifact_record(
            input_spec_artifact_id,
            media_type="application/json",
            byte_length=len(input_spec_bytes),
            created_at=now,
        ),
    )
    provenance_ids = []
    for item in state.get("provenance") or []:
        provenance_ids.append(item["artifact_id"])
        _add_artifact_once(
            catalog,
            _artifact_record(
                item["artifact_id"],
                media_type=item["media_type"],
                byte_length=int(item["byte_length"]),
                created_at=item["created_at"],
            ),
        )
    _add_artifact_once(
        catalog,
        _artifact_record(
            checkpoint_artifact_id,
            media_type="application/json",
            byte_length=len(checkpoint_bytes),
            created_at=now,
        ),
    )
    discovery_artifact_id = state["discovery_artifact_id"]
    discovery_bytes = store.get(discovery_artifact_id)
    _add_artifact_once(
        catalog,
        _artifact_record(
            discovery_artifact_id,
            media_type="application/json",
            byte_length=len(discovery_bytes),
            created_at=state["started_at"],
        ),
    )
    for completion in completed.values():
        completion_receipt_id = completion["receipt_artifact_id"]
        completion_receipt_bytes = store.get(completion_receipt_id)
        _add_artifact_once(
            catalog,
            _artifact_record(
                completion_receipt_id,
                media_type="application/json",
                byte_length=len(completion_receipt_bytes),
                created_at=completion["retrieved_at"],
            ),
        )

    site_attempt_receipts: list[dict[str, Any]] = []
    if is_site:
        site_attempt_receipts = _read_site_attempt_receipts(
            store,
            state.get("site_attempt_receipt_ids", []),
        )
        for attempt in site_attempt_receipts:
            receipt_artifact_id = attempt["receipt_artifact_id"]
            receipt_bytes = store.get(receipt_artifact_id)
            _add_artifact_once(
                catalog,
                _artifact_record(
                    receipt_artifact_id,
                    media_type="application/json",
                    byte_length=len(receipt_bytes),
                    created_at=attempt["attempted_at"],
                ),
            )
        _materialize_site_attempts(catalog, store, site_attempt_receipts)

    work_identity = {
        "title": work.title,
        "author": work.author,
        "language": work.language,
        "source_kind": work.source_kind,
        "source_locator": work.source_locator,
        "adapter_build_id": adapter_build_id,
    }
    work_record = {
        "schema_version": SCHEMA_VERSION,
        "work_id": derived_id("NovelWork", work_identity),
        **work_identity,
        "source_artifact_ids": provenance_ids,
        "created_at": state["started_at"],
    }
    catalog.add("NovelWork", work_record)

    normalized_seen: dict[str, str] = {}
    chapters: list[dict[str, Any]] = []
    parent_artifact_id = provenance_ids[0] if len(provenance_ids) == 1 else None
    for ref in refs:
        result = completed[ref.chapter_key]
        artifact_id = result["artifact_id"]
        _add_artifact_once(
            catalog,
            _artifact_record(
                artifact_id,
                media_type=result["media_type"],
                byte_length=int(result["byte_length"]),
                created_at=result["retrieved_at"],
                parent_artifact_id=parent_artifact_id if ref.derived_from_provenance else None,
                transform_build_id=adapter_build_id,
            ),
        )
        if is_site:
            candidates = [
                receipt
                for receipt in site_attempt_receipts
                if receipt["stage"] == "CHAPTER"
                and receipt["requested_url"] == ref.source_locator
                and receipt["status"] == "FETCHED"
                and receipt["raw_artifact_id"] == artifact_id
                and receipt["attempted_at"] == result["retrieved_at"]
            ]
            if not candidates:
                raise ValidationError(
                    "E-SITE-ATTEMPT-INTEGRITY",
                    f"completed chapter {ref.chapter_key} has no successful site attempt",
                )
            site_attempt = max(candidates, key=lambda receipt: receipt["attempt_ordinal"])
            source_id = _site_source_record(site_attempt)["source_id"]
            retrieval_id = site_attempt["retrieval_id"]
            catalog.get("Source", source_id).update(
                {
                    "title": ref.title,
                    "author": work.author or "",
                    "work": work.title,
                    "document_location": ref.chapter_key,
                }
            )
        else:
            source_id = derived_id(
                "Source",
                {
                    "work_id": work_record["work_id"],
                    "chapter_key": ref.chapter_key,
                    "locator": result["final_locator"],
                },
            )
            source_record = {
                "schema_version": SCHEMA_VERSION,
                "source_id": source_id,
                "canonical_url": result["final_locator"],
                "platform_id": f"novel:{work.source_kind.casefold()}",
                "title": ref.title,
                "author": work.author or "",
                "work": work.title,
                "document_location": ref.chapter_key,
                "same_platform_as": None,
            }
            catalog.add("Source", source_record)
            retrieval_id = derived_id(
                "Retrieval",
                {"source_id": source_id, "artifact_id": artifact_id, "adapter_build_id": adapter_build_id},
            )
            retrieval = {
                "schema_version": SCHEMA_VERSION,
                "retrieval_id": retrieval_id,
                "source_id": source_id,
                "requested_url": ref.source_locator,
                "final_url": result["final_locator"],
                "access_kind": "full_text_chapter",
                "retrieved_at": result["retrieved_at"],
                "http_status": result["http_status"],
                "content_type": result["media_type"],
                "fetcher_build_id": adapter_build_id,
                "status": "FETCHED",
                "retry_of": None,
            }
            catalog.add("Retrieval", retrieval)
            catalog.add(
                "RetrievalArtifact",
                {
                    "schema_version": SCHEMA_VERSION,
                    "retrieval_id": retrieval_id,
                    "artifact_id": artifact_id,
                    "role": "RAW_RESPONSE",
                    **(
                        {"parent_artifact_id": parent_artifact_id, "transform_build_id": adapter_build_id}
                        if ref.derived_from_provenance and parent_artifact_id
                        else {}
                    ),
                },
            )
        data = store.get(artifact_id)
        parser_build_id = parser_build_id_for(result["media_type"], data)
        document_id = derived_id(
            "ParsedDocument",
            {"artifact_id": artifact_id, "parser_build_id": parser_build_id, "media_type": result["media_type"]},
        )
        parsed = parse_artifact(artifact_id, data, result["media_type"], document_id)
        if not parsed["segments"]:
            raise ValidationError("E-NOVEL-EMPTY", f"chapter {ref.chapter_key} has no parseable text")
        normalized_content_hash = object_hash(
            {"segment_hashes": [segment["normalized_text_hash"] for segment in parsed["segments"]]},
            omit=(),
        )
        previous_chapter_id = normalized_seen.get(normalized_content_hash)
        chapter_identity = {
            "work_id": work_record["work_id"],
            "chapter_key": ref.chapter_key,
            "ordinal": ref.ordinal,
            "artifact_id": artifact_id,
        }
        chapter_id = derived_id("NovelChapter", chapter_identity)
        status = "DUPLICATE" if previous_chapter_id else "READY"
        if not previous_chapter_id:
            normalized_seen[normalized_content_hash] = chapter_id
        if not any(item["document_id"] == document_id for item in catalog.all("ParsedDocument")):
            catalog.add("ParsedDocument", parsed["document"])
            output_hash = object_hash({"document": parsed["document"], "segments": parsed["segments"]})
            parse_identity = {
                "input_artifact_id": artifact_id,
                "parser_build_id": parser_build_id,
                "parameters": {"media_type": result["media_type"]},
                "status": "SUCCEEDED",
                "output_hash": output_hash,
                "retry_of": None,
                "supersedes": None,
            }
            catalog.add(
                "ParseRun",
                {
                    "schema_version": SCHEMA_VERSION,
                    "parse_run_id": derived_id("ParseRun", parse_identity),
                    **parse_identity,
                    "output_document_id": document_id,
                },
            )
            for segment in parsed["segments"]:
                catalog.add("Segment", segment)
        chapter_record = {
            "schema_version": SCHEMA_VERSION,
            "chapter_id": chapter_id,
            "work_id": work_record["work_id"],
            "chapter_key": ref.chapter_key,
            "ordinal": ref.ordinal,
            "declared_number": ref.declared_number,
            "title": ref.title,
            "source_locator": result["final_locator"],
            "source_id": source_id,
            "retrieval_id": retrieval_id,
            "artifact_id": artifact_id,
            "document_id": document_id,
            "segment_ids": [segment["segment_id"] for segment in parsed["segments"]],
            "normalized_content_hash": normalized_content_hash,
            "media_type": result["media_type"],
            "status": status,
            "duplicate_of": previous_chapter_id,
        }
        catalog.add("NovelChapter", chapter_record)
        chapters.append(chapter_record)

    order_validation = _order_validation(chapters, strict=strict_order)
    run_status = "FAILED" if order_validation["status"] == "FAIL" else (
        "PARTIAL" if order_validation["status"] == "WARNING" else "SUCCEEDED"
    )
    chapter_ids = [item["chapter_id"] for item in chapters]
    ready_ids = [item["chapter_id"] for item in chapters if item["status"] == "READY"]
    duplicate_ids = [item["chapter_id"] for item in chapters if item["status"] == "DUPLICATE"]
    run_identity = {
        "work_id": work_record["work_id"],
        "input_spec_hash": input_spec_hash,
        "adapter_build_id": adapter_build_id,
        "chapter_ids": chapter_ids,
        "checkpoint_hash": checkpoint_hash,
        "strict_order": strict_order,
        "resumed_from_checkpoint": resumed,
    }
    ingestion_run = {
        "schema_version": SCHEMA_VERSION,
        "ingestion_run_id": derived_id("NovelIngestionRun", run_identity),
        "work_id": work_record["work_id"],
        "adapter_build_id": adapter_build_id,
        "input_spec_hash": input_spec_hash,
        "input_spec_artifact_id": input_spec_artifact_id,
        "chapter_ids": chapter_ids,
        "ready_chapter_ids": ready_ids,
        "duplicate_chapter_ids": duplicate_ids,
        "provenance_artifact_ids": provenance_ids,
        "checkpoint_hash": checkpoint_hash,
        "checkpoint_artifact_id": checkpoint_artifact_id,
        "order_validation": order_validation,
        "strict_order": strict_order,
        "resumed_from_checkpoint": resumed,
        "status": run_status,
        "started_at": state["started_at"],
        "finished_at": now,
        "retry_of": None,
    }
    catalog.add("NovelIngestionRun", ingestion_run)
    validate_novel_ingestion(catalog, store)

    output_dir = work_dir / "ingestions" / ingestion_run["ingestion_run_id"]
    _write_immutable(output_dir / "catalog.json", _json_bytes(_catalog_json(catalog)))
    _write_immutable(output_dir / "novel-ingestion.json", _json_bytes(ingestion_run))
    return {
        "catalog": catalog,
        "store": store,
        "work": work_record,
        "chapters": chapters,
        "ingestion": ingestion_run,
        "work_dir": output_dir,
        "root_work_dir": work_dir,
    }


def validate_novel_ingestion(catalog: Catalog, store: ArtifactStore) -> None:
    for kind in (
        "NovelWork",
        "NovelChapter",
        "NovelIngestionRun",
        "Source",
        "Retrieval",
        "RetrievalArtifact",
        "Artifact",
        "TriageAssessment",
        "ParseRun",
        "ParsedDocument",
        "Segment",
    ):
        for record in catalog.all(kind):
            validate_schema(kind, record)
    for artifact in catalog.all("Artifact"):
        data = store.get(artifact["artifact_id"])
        if len(data) != artifact["byte_length"]:
            raise ValidationError("E-HASH-MISMATCH", f"{artifact['artifact_id']} length mismatch")
    for work in catalog.all("NovelWork"):
        identity = {
            "title": work["title"],
            "author": work.get("author"),
            "language": work["language"],
            "source_kind": work["source_kind"],
            "source_locator": work["source_locator"],
            "adapter_build_id": work["adapter_build_id"],
        }
        if work["work_id"] != derived_id("NovelWork", identity):
            raise ValidationError("E-ID-BIND", f"{work['work_id']} does not match work content")
        for artifact_id in work["source_artifact_ids"]:
            catalog.get("Artifact", artifact_id)
            store.verify(artifact_id)
    for chapter in catalog.all("NovelChapter"):
        catalog.get("NovelWork", chapter["work_id"])
        catalog.get("Source", chapter["source_id"])
        retrieval = catalog.get("Retrieval", chapter["retrieval_id"])
        document = catalog.get("ParsedDocument", chapter["document_id"])
        if retrieval["source_id"] != chapter["source_id"]:
            raise ValidationError("E-LINEAGE", f"{chapter['chapter_id']} source/retrieval mismatch")
        if document["input_artifact_id"] != chapter["artifact_id"]:
            raise ValidationError("E-LINEAGE", f"{chapter['chapter_id']} artifact/document mismatch")
        chapter_identity = {
            "work_id": chapter["work_id"],
            "chapter_key": chapter["chapter_key"],
            "ordinal": chapter["ordinal"],
            "artifact_id": chapter["artifact_id"],
        }
        if chapter["chapter_id"] != derived_id("NovelChapter", chapter_identity):
            raise ValidationError("E-ID-BIND", f"{chapter['chapter_id']} does not match chapter content")
        if not chapter["segment_ids"]:
            raise ValidationError("E-NOVEL-EMPTY", f"{chapter['chapter_id']} has no parseable segments")
        segments = [catalog.get("Segment", segment_id) for segment_id in chapter["segment_ids"]]
        if any(segment["document_id"] != document["document_id"] for segment in segments):
            raise ValidationError("E-LINEAGE", f"{chapter['chapter_id']} segment/document mismatch")
        expected = object_hash(
            {"segment_hashes": [segment["normalized_text_hash"] for segment in segments]}, omit=()
        )
        if expected != chapter["normalized_content_hash"]:
            raise ValidationError("E-HASH-MISMATCH", f"{chapter['chapter_id']} normalized content changed")
        if chapter["status"] == "DUPLICATE":
            original = catalog.get("NovelChapter", chapter["duplicate_of"])
            if original["normalized_content_hash"] != chapter["normalized_content_hash"]:
                raise ValidationError("E-DUPLICATE", f"{chapter['chapter_id']} duplicate hash mismatch")
    for run in catalog.all("NovelIngestionRun"):
        work = catalog.get("NovelWork", run["work_id"])
        work_chapters = [
            item for item in catalog.all("NovelChapter") if item["work_id"] == run["work_id"]
        ]
        if run["chapter_ids"] != [item["chapter_id"] for item in work_chapters]:
            raise ValidationError("E-CHAPTER-ORDER", f"{run['ingestion_run_id']} chapter order changed")
        try:
            input_spec = json.loads(store.get(run["input_spec_artifact_id"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("E-NOVEL-SPEC", "stored ingestion spec is invalid") from exc
        if (
            object_hash(input_spec, omit=()) != run["input_spec_hash"]
            or artifact_id_for(_json_bytes(input_spec)) != run["input_spec_artifact_id"]
        ):
            raise ValidationError("E-HASH-MISMATCH", f"{run['ingestion_run_id']} input spec changed")
        checkpoint = json.loads(store.get(run["checkpoint_artifact_id"]).decode("utf-8"))
        if object_hash(checkpoint, omit=()) != run["checkpoint_hash"]:
            raise ValidationError("E-HASH-MISMATCH", f"{run['ingestion_run_id']} checkpoint changed")
        if artifact_id_for(_json_bytes(checkpoint)) != run["checkpoint_artifact_id"]:
            raise ValidationError("E-HASH-MISMATCH", f"{run['ingestion_run_id']} checkpoint bytes changed")
        _validate_run_checkpoint_bindings(
            catalog,
            store,
            run,
            work,
            work_chapters,
            input_spec,
            checkpoint,
        )
        if sorted_ids(run["ready_chapter_ids"] + run["duplicate_chapter_ids"]) != sorted_ids(run["chapter_ids"]):
            raise ValidationError("E-LINEAGE", f"{run['ingestion_run_id']} chapter partition mismatch")
        if run["ready_chapter_ids"] != [
            item["chapter_id"] for item in work_chapters if item["status"] == "READY"
        ] or run["duplicate_chapter_ids"] != [
            item["chapter_id"] for item in work_chapters if item["status"] == "DUPLICATE"
        ]:
            raise ValidationError("E-LINEAGE", f"{run['ingestion_run_id']} chapter status partition changed")
        expected_order = _order_validation(work_chapters, strict=run["strict_order"])
        expected_status = "FAILED" if expected_order["status"] == "FAIL" else (
            "PARTIAL" if expected_order["status"] == "WARNING" else "SUCCEEDED"
        )
        if run["order_validation"] != expected_order or run["status"] != expected_status:
            raise ValidationError("E-CHAPTER-ORDER", f"{run['ingestion_run_id']} order result changed")
        identity = {
            "work_id": run["work_id"],
            "input_spec_hash": run["input_spec_hash"],
            "adapter_build_id": run["adapter_build_id"],
            "chapter_ids": run["chapter_ids"],
            "checkpoint_hash": run["checkpoint_hash"],
            "strict_order": run["strict_order"],
            "resumed_from_checkpoint": run["resumed_from_checkpoint"],
        }
        if run["ingestion_run_id"] != derived_id("NovelIngestionRun", identity):
            raise ValidationError("E-ID-BIND", f"{run['ingestion_run_id']} does not match ingestion content")
