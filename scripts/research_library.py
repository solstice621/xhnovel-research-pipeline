#!/usr/bin/env python3
"""Host-side chapter library. Native compilers remain the evidence authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import jsonschema
import source_acquisition as acq
from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.build_identity import build_source_hash
from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.errors import PipelineError, ValidationError
from xhnovel_pipeline.file_io import write_immutable
from xhnovel_pipeline.generic_handoff import resolve_generic_handoff
from xhnovel_pipeline.generic_extraction import validate_selected_generic_corpus
from xhnovel_pipeline.generic_handoff_execution import (
    validate_generic_execution, validate_generic_execution_history,
)
from xhnovel_pipeline.hashing import artifact_id_for
from xhnovel_pipeline.observation_common import get_record
from xhnovel_pipeline.phase0_builder import resolve_validated_handoff_input
from xhnovel_pipeline.phase0_execution import validate_handoff_execution_history, verify_handoff_execution
from xhnovel_pipeline.phase0_handoff import attestation_rights
from xhnovel_pipeline.store import ArtifactStore

FORMAT = "host-research-library-v1"
DEFAULT_ROOT = Path.home() / "Documents" / "xhnovel-library"
MAX_RECORD_BYTES = 8_000_000
MAX_NATIVE_CATALOG_BYTES = 500_000_000


def fail(message: str, code: str = "E-LIBRARY"):
    raise ValidationError(code, message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return canonical_dumps(value) + b"\n"


def checked_path(path) -> Path:
    return acq.no_symlinks(Path(path).expanduser()).resolve()


def read_bytes(path, limit=MAX_RECORD_BYTES) -> bytes:
    return acq.read_bytes(checked_path(path), limit)


def read_json(path, limit=MAX_RECORD_BYTES):
    return json.loads(read_bytes(path, limit))


def file_ref(path) -> dict:
    path = checked_path(path)
    return {"path": str(path), "sha256": digest(read_bytes(path))}


def ref_bytes(ref) -> bytes:
    data = read_bytes(ref["path"])
    if digest(data) != ref["sha256"]:
        fail("registered file changed", "E-LIBRARY-INTEGRITY")
    return data


def schema_check(name, value):
    schema = read_json(ROOT / "contracts/host_library" / (name + ".schema.json"))
    try:
        jsonschema.Draft202012Validator(schema).validate(value)
    except jsonschema.ValidationError as exc:
        fail(exc.message, "E-LIBRARY-SCHEMA")


def resolve_binding(protocol, handoff, native_root):
    ref_bytes(handoff)
    native_root = checked_path(native_root)
    if not native_root.is_dir():
        fail("native root is missing", "E-LIBRARY-MISSING-PATH")
    if protocol == "SCENE":
        result = resolve_validated_handoff_input(Path(handoff["path"]), phase0_root=native_root)
    elif protocol == "GENERIC":
        result = resolve_generic_handoff(Path(handoff["path"]), native_root, root=ROOT)
    else:
        fail("unsupported protocol")
    return result


class Library:
    def __init__(self, root):
        self.root = checked_path(root)
        self.config = read_json(self.root / "library.json")
        schema_check("library", self.config)
        if self.config["root"] != str(self.root):
            fail("library root moved; restore its recorded absolute path", "E-LIBRARY-ROOT")

    @classmethod
    def initialize(cls, root):
        root = checked_path(root)
        config_path = root / "library.json"
        if config_path.exists():
            return cls(root)
        # Never adopt arbitrary existing files as a managed library.
        if root.exists() and any(root.iterdir()):
            fail("initial library root must be empty", "E-LIBRARY-ROOT")
        config = {"format_version": FORMAT, "library_id": str(uuid4()), "root": str(root)}
        schema_check("library", config)
        write_immutable(config_path, encoded(config))
        return cls(root)

    def path(self, relative):
        return acq.child(self.root, relative)

    def _body(self, kind, **fields):
        return {"format_version": FORMAT, "library_id": self.config["library_id"], "kind": kind, **fields}

    def _record_path(self, record_id):
        if not isinstance(record_id, str) or not re.fullmatch(r"[0-9a-f]{64}", record_id):
            fail("record ID must be a full host SHA-256", "E-LIBRARY-ID")
        return self.path(f"records/sha256/{record_id[:2]}/{record_id}.json")

    def load(self, record_id, kind=None):
        data = read_bytes(self._record_path(record_id))
        value = json.loads(data)
        schema_check("record", value)
        if digest(data) != record_id or data != encoded(value):
            fail("host record digest or canonical bytes differ", "E-LIBRARY-INTEGRITY")
        if value["library_id"] != self.config["library_id"] or (kind and value["kind"] != kind):
            fail("record kind or library ownership differs", "E-LIBRARY-BINDING")
        return value

    def _publish(self, value):
        schema_check("record", value)
        data = encoded(value)
        record_id = digest(data)
        write_immutable(self._record_path(record_id), data)
        # The index is rebuilt from records on query; an interrupted index write
        # never necessitates native model execution or loses a published record.
        return {"record_id": record_id, "record_path": str(self._record_path(record_id)), "record": value}

    def new_research(self, request, *, key, name):
        raw = read_bytes(request)
        value = json.loads(raw)
        if not isinstance(value, dict):
            fail("research request must be a JSON object")
        fields = {"key": key, "name": name, "request_sha256": digest(raw)}
        research_id = "R-" + digest(encoded(fields))[:24]
        record = self._body("research", research_id=research_id, **fields)
        schema_check("record", record)
        directory = self.path(f"research/{research_id}")
        write_immutable(checked_path(directory / "request.json"), raw)
        result = self._publish(record)
        result["paths"] = self.research_paths(record)
        return result

    def research_paths(self, record):
        directory = self.path(f"research/{record['research_id']}")
        return {"research_dir": str(directory), "phase0_base": str(directory / "phase0"),
                "campaign_root": str(directory / "campaign"), "reports_dir": str(directory / "reports"),
                "sealed_output": str(self.root / "sources/sealed"),
                "acquisition_root": str(self.root / "acquisition")}

    def _execution_binding_path(self, record):
        key = digest(encoded({k: record[k] for k in ("protocol", "native_root", "handoff")}))
        return self.path(f"bindings/executions/{key}.json")

    def _load_execution(self, record_id):
        record = self.load(record_id)
        if record["kind"] not in {"execution", "external-execution"}:
            fail("expected an execution registration", "E-LIBRARY-BINDING")
        return record

    def _external_execution_body(self, research_id, protocol, handoff, native_root, work_dir):
        self.load(research_id, "research")
        self.validate(research_id)
        native = checked_path(work_dir)
        if not native.is_dir() or native.is_relative_to(self.root):
            fail("historical native execution must exist outside this library", "E-LIBRARY-PATH")
        resolved = resolve_binding(protocol, handoff, native_root)
        return self._body("external-execution", research_record_id=research_id, protocol=protocol,
                          handoff=handoff, native_root=str(checked_path(native_root)), work_dir=str(native),
                          work_ref=resolved.handoff["work_ref"], work_ref_id=resolved.handoff["work_ref"]["work_ref_id"],
                          source_ref_id=resolved.handoff["source_ref"]["source_ref_id"], mode="EXTERNAL_REFERENCE",
                          input_spec_hash=resolved.handoff["novel_spec"]["expected_input_spec_hash"]), resolved

    def register_external_execution(self, research_id, *, protocol, handoff, native_root, work_dir):
        record, _ = self._external_execution_body(research_id, protocol, file_ref(handoff), native_root, work_dir)
        if self._native_status(record) == "HANDOFF_READY":
            fail("no native execution history at the supplied old work-dir", "E-LIBRARY-NOT-READY")
        write_immutable(self._execution_binding_path(record), encoded({"record_id": digest(encoded(record))}),
                        code="E-LIBRARY-BINDING")
        return self._publish(record)

    def _source_context(self, sealed, protocol, handoff, native_root):
        sealed = checked_path(sealed)
        manifest, run = acq.validate_sealed(sealed)
        resolved = resolve_binding(protocol, handoff, native_root)
        spec = resolved.execution_spec
        # Host cross-binding only: all source, attestation and Handoff validation
        # above is performed by the existing production validators.
        expected_source = {"kind": "directory", "path": str(sealed / "chapters"), **run.cfg["work"]}
        if spec["source"] != expected_source or spec["rights"] != attestation_rights(
            acq.validate_operator_attestation(read_json(run.root / "operator-attestation.json"))
        ) or spec["source_quality"] != {"edition_status": manifest["source"]["edition_status"],
                                     "textual_completeness": "COMPLETE"}:
            fail("native Handoff does not bind this sealed source and declaration", "E-LIBRARY-BINDING")
        work = resolved.handoff["work_ref"]
        mode = "EXTERNAL_REFERENCE"
        if sealed.is_relative_to(self.root):
            if sealed != self.path(f"sources/sealed/{sealed.name}"):
                fail("managed source must be sealed at its stable source path", "E-LIBRARY-PATH")
            mode = "MANAGED"
        record = self._body("source", protocol=protocol, handoff=handoff, native_root=str(checked_path(native_root)),
                            input_spec_hash=resolved.handoff["novel_spec"]["expected_input_spec_hash"],
                            work_ref_id=work["work_ref_id"], work_ref=work,
                            sealed_path=str(sealed), source_revision=sealed.name, mode=mode,
                            acquisition_script_sha256=digest(read_bytes(Path(acq.__file__))))
        return record, resolved, run

    def register_source(self, sealed, *, protocol, handoff, native_root):
        record, _, _ = self._source_context(sealed, protocol, file_ref(handoff), native_root)
        return self._publish(record)

    def _validate_source(self, record):
        rebuilt, resolved, run = self._source_context(record["sealed_path"], record["protocol"],
                                                     record["handoff"], record["native_root"])
        if rebuilt != record:
            fail("source registration differs from replay", "E-LIBRARY-INTEGRITY")
        return resolved, run

    def allocate_execution(self, research_id, source_id, *, handoff, native_root, key, work_dir=None):
        research = self.load(research_id, "research")
        self.validate(research_id)
        source = self.load(source_id, "source")
        self._validate_source(source)
        bound, _, _ = self._source_context(source["sealed_path"], source["protocol"], file_ref(handoff), native_root)
        if bound["work_ref_id"] != source["work_ref_id"]:
            fail("execution work identity differs from source registration", "E-LIBRARY-BINDING")
        fields = {"research_record_id": research_id, "source_record_id": source_id,
                  "source_revision": source["source_revision"], "execution_key": key,
                  **{k: bound[k] for k in ("protocol", "handoff", "native_root", "input_spec_hash", "work_ref_id")}}
        execution_id = "E-" + digest(encoded(fields))[:24]
        directory = self.path(f"research/{research['research_id']}/works/{source['work_ref_id']}/"
                              f"{source['source_revision']}/{execution_id}")
        native = directory / "native"
        mode = "MANAGED"
        if work_dir is not None:
            native = checked_path(work_dir)
            if native != directory / "native":
                if native.is_relative_to(self.root) or not native.is_dir():
                    fail("external execution must reference an existing directory outside this library")
                mode = "EXTERNAL_REFERENCE"
        # Native roots may never land in source/record trees or overwrite other runs.
        record = self._body("execution", **fields, execution_id=execution_id, work_dir=str(native), mode=mode)
        schema_check("record", record)
        existing, issues = self._inventory()
        if issues:
            fail("repair corrupt registrations before allocating another execution", "E-LIBRARY-INTEGRITY")
        for other in existing.values():
            if (other["kind"] == "execution" and other["protocol"] == record["protocol"]
                    and other["native_root"] == record["native_root"] and other["handoff"] == record["handoff"]
                    and other != record):
                fail("this native Handoff already has a fixed execution registration", "E-LIBRARY-BINDING")
        # Atomic no-replace binding also closes the concurrent allocation race.
        # A crash here is resumed by repeating these same inputs; no lease/queue.
        write_immutable(self._execution_binding_path(record), encoded({"record_id": digest(encoded(record))}),
                        code="E-LIBRARY-BINDING", message="native Handoff already bound to another execution")
        if mode == "MANAGED":
            checked_path(native).mkdir(parents=True, exist_ok=True)
            checked_path(directory / "reports").mkdir(parents=True, exist_ok=True)
        result = self._publish(record)
        result["paths"] = {"work_dir": str(native), "reports_dir": str(directory / "reports")}
        return result

    def _validate_execution(self, record):
        if record["kind"] == "external-execution":
            rebuilt, resolved = self._external_execution_body(record["research_record_id"], record["protocol"],
                record["handoff"], record["native_root"], record["work_dir"])
            if rebuilt != record or self._native_status(record) == "HANDOFF_READY":
                fail("historical execution does not reproduce native lineage", "E-LIBRARY-INTEGRITY")
            if read_bytes(self._execution_binding_path(record)) != encoded({"record_id": digest(encoded(record))}):
                fail("historical execution allocation binding differs", "E-LIBRARY-BINDING")
            return resolved
        self.validate(record["research_record_id"])
        source = self.load(record["source_record_id"], "source")
        self._validate_source(source)
        rebuilt, resolved, _ = self._source_context(source["sealed_path"], record["protocol"],
                                                   record["handoff"], record["native_root"])
        if any(record[k] != rebuilt[k] for k in ("work_ref_id", "input_spec_hash", "source_revision")):
            fail("execution source binding differs", "E-LIBRARY-BINDING")
        if record["protocol"] != source["protocol"] or record["work_ref_id"] != source["work_ref_id"]:
            fail("execution protocol or work identity differs", "E-LIBRARY-BINDING")
        fields = {k: record[k] for k in ("research_record_id", "source_record_id", "source_revision", "execution_key",
                                       "protocol", "handoff", "native_root", "input_spec_hash", "work_ref_id")}
        expected_id = "E-" + digest(encoded(fields))[:24]
        research = self.load(record["research_record_id"], "research")
        expected_path = self.path(f"research/{research['research_id']}/works/{record['work_ref_id']}/"
                                  f"{record['source_revision']}/{expected_id}/native")
        native = checked_path(record["work_dir"])
        if record["execution_id"] != expected_id or not native.is_dir():
            fail("execution ID differs or native directory is missing", "E-LIBRARY-BINDING")
        if ((record["mode"] == "MANAGED" and native != expected_path) or
            (record["mode"] == "EXTERNAL_REFERENCE" and native.is_relative_to(self.root))):
            fail("execution directory ownership differs", "E-LIBRARY-PATH")
        if read_bytes(self._execution_binding_path(record)) != encoded({"record_id": digest(encoded(record))}):
            fail("native Handoff allocation binding differs", "E-LIBRARY-BINDING")
        return resolved

    def execution_status(self, record):
        self._validate_execution(record)
        return self._native_status(record)

    def _native_status(self, record):
        if record["protocol"] == "SCENE":
            history = validate_handoff_execution_history(Path(record["handoff"]["path"]), phase0_root=Path(record["native_root"]))
            matches = [a for a in history if a.work_dir == Path(record["work_dir"])]
            return matches[-1].state if matches else "HANDOFF_READY"
        history = validate_generic_execution_history(Path(record["handoff"]["path"]), Path(record["native_root"]), root=ROOT)
        matches = [e for _, e in history if e["work_dir"] == record["work_dir"]]
        if matches and matches[-1]["state"] in {"SUCCEEDED", "FAILED"}:
            validate_generic_execution(get_record(Path(record["native_root"]), matches[-1]["detail"]["receipt_artifact_id"]),
                                       Path(record["native_root"]), root=ROOT, work_dir=Path(record["work_dir"]))
        return matches[-1]["state"] if matches else "HANDOFF_READY"

    def _product_context(self, execution_id, receipt_ref):
        execution = self._load_execution(execution_id)
        resolved = self._validate_execution(execution)
        receipt = json.loads(ref_bytes(receipt_ref))
        native = Path(execution["work_dir"])
        if execution["protocol"] == "SCENE":
            history = validate_handoff_execution_history(Path(execution["handoff"]["path"]),
                                                        phase0_root=Path(execution["native_root"]))
            matches = [a for a in history if a.work_dir == native and a.receipt == receipt
                       and checked_path(a.receipt_path) == Path(receipt_ref["path"])]
            if len(matches) != 1 or receipt["status"] != "SUCCEEDED":
                fail("product requires an authoritative successful native receipt", "E-LIBRARY-NOT-READY")
            store = ArtifactStore(native / "ingestion/objects")
            catalog = Catalog.from_mapping(read_json(native / "research" / receipt["scene_scout_run_id"] / "catalog.json", MAX_NATIVE_CATALOG_BYTES))
            # Validate the exact captured catalog used for display, rather than
            # trusting a second disk read after historical receipt validation.
            if verify_handoff_execution(resolved.handoff, catalog, store, attempt_id=receipt["attempt_id"],
                                        attempt_ordinal=receipt["attempt_ordinal"], executor=receipt["executor"],
                                        recorded_at=receipt["recorded_at"]) != receipt:
                fail("captured Scene catalog differs from receipt", "E-LIBRARY-INTEGRITY")
            merge = next(x for x in catalog.all("SceneMergeRun") if x["merge_run_id"] == receipt["merge_run_id"])
            candidates = {c["scene_candidate_id"]: c for c in catalog.all("SceneCandidate")}
            records = [candidates[k] for k in merge["output_candidate_ids"]]
            family, native_id = "SCENE_CANDIDATES", receipt["scene_scout_run_id"]
        else:
            receipt = validate_generic_execution(receipt, Path(execution["native_root"]), root=ROOT, work_dir=native)
            if receipt["handoff_id"] != resolved.handoff["handoff_id"] or receipt["status"] != "SUCCEEDED":
                fail("product requires this Handoff's successful receipt", "E-LIBRARY-NOT-READY")
            target = receipt["result"]
            selected = validate_selected_generic_corpus(resolved.spec, native, profile_ref=resolved.profile_ref,
                extraction_run_id=target["extraction_run_id"], reduction_run_id=target["reduction_run_id"],
                corpus_snapshot_id=target["corpus_snapshot_id"], root=ROOT)
            if selected.corpus_snapshot["corpus_snapshot_hash"] != target["corpus_snapshot_hash"]:
                fail("captured corpus differs from receipt", "E-LIBRARY-INTEGRITY")
            records, catalog, store = selected.corpus_records, selected.extraction.catalog, selected.extraction.store
            family, native_id = "GENERIC_CORPUS", target["extraction_run_id"]
        record = self._body("product", execution_record_id=execution_id, receipt=receipt_ref, family=family,
                            native_run_id=native_id, content_sha256=digest(encoded(records)), record_count=len(records),
                            profile_ref=getattr(resolved, "profile_ref", "xuanhuan-gameplay-scene-v1"))
        return record, records, catalog, store, resolved

    def register_product(self, execution_id, receipt):
        record, *_ = self._product_context(execution_id, file_ref(receipt))
        return self._publish(record)

    def register_report(self, research_id, report, *, executions, products=()):
        record = self._body("report", research_record_id=research_id, report=file_ref(report),
                            execution_record_ids=sorted(set(executions)), product_record_ids=sorted(set(products)),
                            assurance="HOST_AUTHORED")
        schema_check("record", record)
        self._validate_report(record)
        return self._publish(record)

    def _validate_report(self, record):
        self.validate(record["research_record_id"])
        ref_bytes(record["report"])
        for eid in record["execution_record_ids"]:
            execution = self._load_execution(eid)
            if execution["research_record_id"] != record["research_record_id"]:
                fail("report references another research", "E-LIBRARY-BINDING")
            self.execution_status(execution)
        for pid in record["product_record_ids"]:
            product = self.load(pid, "product")
            if product["execution_record_id"] not in record["execution_record_ids"]:
                fail("report product is not bound to a referenced execution", "E-LIBRARY-BINDING")
            self.validate(pid)

    def validate(self, record_id):
        record = self.load(record_id)
        kind = record["kind"]
        if kind == "research":
            fields = {k: record[k] for k in ("key", "name", "request_sha256")}
            if record["research_id"] != "R-" + digest(encoded(fields))[:24]:
                fail("research identity differs", "E-LIBRARY-INTEGRITY")
            if digest(read_bytes(self.path(f"research/{record['research_id']}/request.json"))) != record["request_sha256"]:
                fail("frozen research request changed", "E-LIBRARY-INTEGRITY")
        elif kind == "source":
            self._validate_source(record)
        elif kind in {"execution", "external-execution"}:
            return {"status": self.execution_status(record)}
        elif kind == "product":
            rebuilt, *_ = self._product_context(record["execution_record_id"], record["receipt"])
            if rebuilt != record:
                fail("product differs from native replay", "E-LIBRARY-INTEGRITY")
        elif kind == "report":
            self._validate_report(record)
        elif kind == "validation":
            self.load(record["target_record_id"])
            return {"status": "HISTORICAL_VALIDATION_ONLY"}
        return {"status": "VALIDATED"}

    def verify(self, record_id):
        self.load(record_id)
        try:
            result = self.validate(record_id)
            outcome, error = "PASS", None
        except (PipelineError, OSError, ValueError) as exc:
            result, outcome, error = {}, "FAIL", str(exc)
        validation = self._body("validation", target_record_id=record_id,
                                checked_at=datetime.now(timezone.utc).isoformat(),
                                validator_sha256=digest(encoded({"library": digest(read_bytes(__file__)),
                                    "acquisition": digest(read_bytes(acq.__file__)), "native": build_source_hash(ROOT),
                                    "schemas": {p.name: digest(read_bytes(p)) for p in sorted((ROOT / "contracts/host_library").glob("*.json"))}})),
                                outcome=outcome, error=error)
        return {**self._publish(validation), "outcome": outcome, "validation": result}

    def research_status(self, research_id, **kwargs):
        from research_status import research_status
        return research_status(self, research_id, **kwargs)

    def _inventory(self):
        result, issues = {}, []
        directory = self.path("records/sha256")
        if directory.exists() and not directory.is_dir():
            fail("record directory replaced by a file", "E-LIBRARY-INTEGRITY")
        for path in sorted(directory.glob("*/*.json")):
            try:
                record = self.load(path.stem)
                if path != self._record_path(path.stem):
                    fail("record is misplaced")
                result[path.stem] = record
            except (PipelineError, OSError, ValueError) as exc:
                issues.append({"path": str(path), "error": str(exc)})
        return result, issues

    def reindex(self, *, validate=True):
        records, issues = self._inventory()
        index = self.path("index/library.sqlite")
        index.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".library-", suffix=".sqlite", dir=index.parent)
        os.close(fd)
        rows = []
        try:
            with closing(sqlite3.connect(temporary)) as db, db:
                db.execute("CREATE TABLE entries (record_id TEXT PRIMARY KEY, kind TEXT, payload TEXT, status TEXT)")
                for rid, record in records.items():
                    status = "NOT_CHECKED"
                    if validate:
                        try:
                            status = self.validate(rid)["status"]
                        except (PipelineError, OSError, ValueError) as exc:
                            status = "UNAVAILABLE"
                            issues.append({"record_id": rid, "error": str(exc)})
                    rows.append((rid, record["kind"], encoded(record).decode("utf-8"), status))
                db.executemany("INSERT INTO entries VALUES (?, ?, ?, ?)", rows)
            with open(temporary, "rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, index)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"indexed_records": len(rows), "issues": issues, "index_path": str(index)}

    def list_records(self, kind=None, *, query=None, work_ref_id=None, source_revision=None, research_id=None):
        # Always derive the metadata snapshot from immutable records. SQLite is
        # never a fallback for missing source bytes or a cached eligibility claim.
        snapshot = self.reindex(validate=False)
        with closing(sqlite3.connect(self.path("index/library.sqlite"))) as db:
            rows = db.execute("SELECT record_id FROM entries WHERE (? IS NULL OR kind=? OR (?='execution' AND kind='external-execution')) ORDER BY record_id", (kind, kind, kind)).fetchall()
        result = []
        for (rid,) in rows:
            record = self.load(rid)
            base = record
            if record["kind"] == "product":
                base = self._load_execution(record["execution_record_id"])
            if work_ref_id and base.get("work_ref_id") != work_ref_id:
                continue
            if source_revision and base.get("source_revision") != source_revision:
                continue
            if research_id and base.get("research_record_id", rid if record["kind"] == "research" else None) != research_id:
                continue
            metadata = [record]
            if base["kind"] in {"execution", "external-execution"}:
                metadata.extend([base, self.load(base["research_record_id"], "research")])
                if base["kind"] == "execution":
                    metadata.append(self.load(base["source_record_id"], "source"))
            if query and query.casefold() not in json.dumps(metadata, ensure_ascii=False).casefold():
                continue
            result.append({"record_id": rid, "record": record, "validation": "NOT_CHECKED"})
        return {"records": result, "issues": snapshot["issues"], "validation": "NOT_CHECKED"}

    @staticmethod
    def _text_permission(spec, include_text):
        rights = spec["rights"]
        if rights["basis"] == "UNKNOWN" or not rights["may_store_full_text"]:
            fail("source storage permission is missing", "E-LIBRARY-RIGHTS")
        if include_text and (not rights["may_send_to_external_model"] or not rights["may_export_excerpts"]):
            fail("text output requires explicit model and excerpt permission", "E-LIBRARY-RIGHTS")

    def search_text(self, source_id, query, *, limit=20, offset=0, include_text=False):
        if not query or len(query) > 1000 or not 1 <= limit <= 1000 or offset < 0 or offset > 100_000:
            fail("invalid bounded search parameters")
        source = self.load(source_id, "source")
        resolved, run = self._validate_source(source)
        self._text_permission(resolved.execution_spec, include_text)
        view, _ = acq.chapter_view(run)
        matches, seen, truncated = [], 0, False
        for chapter in view["chapters"]:
            path = acq.child(Path(source["sealed_path"]) / "chapters", chapter["file_name"])
            raw = read_bytes(path, run.limits["max_input_bytes"])
            if "sha256:" + digest(raw) != chapter["sha256"]:
                fail("chapter changed during search", "E-LIBRARY-INTEGRITY")
            text = raw.decode("utf-8")
            pos = 0
            while (start := text.find(query, pos)) >= 0:
                pos = start + 1
                seen += 1
                if seen <= offset:
                    continue
                if len(matches) == limit:
                    truncated = True
                    break
                end = start + len(query)
                match = {"kind": "TEXT_MATCH", "source_revision": source["source_revision"],
                         "chapter_key": chapter["key"], "ordinal": chapter["ordinal"], "chapter_path": str(path),
                         "chapter_sha256": chapter["sha256"], "codepoint_start": start, "codepoint_end": end,
                         "byte_start": len(text[:start].encode("utf-8")), "byte_end": len(text[:end].encode("utf-8"))}
                if include_text:
                    match["text"] = text[start:end]
                matches.append(match)
            if truncated:
                break
        return {"matches": matches, "truncated": truncated, "next_offset": offset + len(matches) if truncated else None,
                "evidence_status": "TEXT_MATCH_ONLY", "scope": "CHAPTER_FILES"}

    def read_product(self, product_id, *, query=None, offset=0, limit=20, include_text=False):
        if not 0 <= offset <= 100_000 or not 1 <= limit <= 1000 or (query is not None and len(query) > 1000):
            fail("invalid bounded product query")
        product = self.load(product_id, "product")
        rebuilt, records, _, _, resolved = self._product_context(product["execution_record_id"], product["receipt"])
        if rebuilt != product:
            fail("product differs from native replay", "E-LIBRARY-INTEGRITY")
        self._text_permission(resolved.execution_spec, include_text)
        id_field = "scene_candidate_id" if product["family"] == "SCENE_CANDIDATES" else "record_id"
        filtered = [r for r in records if not query or query.casefold() in json.dumps(r, ensure_ascii=False).casefold()]
        page = filtered[offset:offset + limit]
        return {"product_record_id": product_id, "profile_ref": product["profile_ref"], "matched_records": len(filtered),
                "next_offset": offset + len(page) if offset + len(page) < len(filtered) else None,
                "records": [{"native_record_id": r[id_field], "source_spans": r["source_spans"],
                             **({"content": r} if include_text else {})} for r in page],
                "content_policy": "EXCERPTS_PERMITTED" if include_text else "OFFSETS_ONLY_NO_EXCERPTS"}

    def show_evidence(self, product_id, native_record_id, *, include_text=False):
        product = self.load(product_id, "product")
        rebuilt, records, catalog, store, resolved = self._product_context(product["execution_record_id"], product["receipt"])
        if rebuilt != product:
            fail("product differs from replay", "E-LIBRARY-INTEGRITY")
        self._text_permission(resolved.execution_spec, include_text)
        id_field = "scene_candidate_id" if product["family"] == "SCENE_CANDIDATES" else "record_id"
        matches = [r for r in records if r[id_field] == native_record_id]
        if len(matches) != 1:
            fail("native record is not a member of this product", "E-LIBRARY-MEMBER")
        execution = self._load_execution(product["execution_record_id"])
        source, view = None, None
        if execution["kind"] == "execution":
            source = self.load(execution["source_record_id"], "source")
            view = read_json(Path(source["sealed_path"]) / "chapter-view.json")
        chapters = {sid: chapter for chapter in catalog.all("NovelChapter") for sid in chapter["segment_ids"]}
        segments = {s["segment_id"]: s for s in catalog.all("Segment")}
        spans = []

        def collect(value, path=""):
            if isinstance(value, dict):
                for key, child in value.items():
                    current = path + "/" + key
                    if key in {"source_spans", "support_spans"}:
                        for i, span in enumerate(child):
                            spans.append((current + f"/{i}", span))
                    else:
                        collect(child, current)
            elif isinstance(value, list):
                for i, child in enumerate(value):
                    collect(child, path + f"/{i}")

        collect(matches[0])
        result = []
        for pointer, span in spans:
            segment = segments[span["segment_id"]]
            chapter = chapters[span["segment_id"]]
            chapter_path, chapter_key = None, chapter["chapter_id"]
            if view is not None:
                row = view["chapters"][chapter["ordinal"] - 1]
                chapter_path = str(Path(source["sealed_path"]) / "chapters" / row["file_name"])
                chapter_key = row["key"]
                raw = read_bytes(chapter_path, row["byte_length"])
                if artifact_id_for(raw) != chapter["artifact_id"] or store.get(chapter["artifact_id"]) != raw:
                    fail("native chapter does not bind the sealed chapter", "E-LIBRARY-BINDING")
            else:
                store.get(chapter["artifact_id"])
            text = segment["normalized_text"]
            if not 0 <= span["start"] < span["end"] <= len(text):
                fail("native evidence bounds differ", "E-LIBRARY-INTEGRITY")
            result.append({"field_pointer": pointer, "segment_id": span["segment_id"],
                           "normalized_start": span["start"], "normalized_end": span["end"],
                           "normalized_text_hash": segment["normalized_text_hash"], "chapter_id": chapter["chapter_id"],
                           "chapter_key": chapter_key, "chapter_path": chapter_path,
                           "chapter_locator": chapter["source_locator"], "store_root": str(store.root),
                           "source_artifact_id": chapter["artifact_id"],
                           **({"text": text[span["start"]:span["end"]]} if include_text else {})})
        return {"native_record_id": native_record_id, "family": product["family"],
                "evidence": result, "assurance": "DRAFT_UNVERIFIED" if product["family"] == "SCENE_CANDIDATES" else "UNQUALIFIED"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library-root", default=os.environ.get("XHNOVEL_LIBRARY_ROOT", str(DEFAULT_ROOT)))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    p = commands.add_parser("new-research")
    p.add_argument("request"); p.add_argument("--key", required=True); p.add_argument("--name", required=True)
    p = commands.add_parser("register-source")
    p.add_argument("sealed"); p.add_argument("--protocol", choices=["SCENE", "GENERIC"], required=True)
    p.add_argument("--handoff", required=True); p.add_argument("--native-root", required=True)
    p = commands.add_parser("allocate-execution")
    p.add_argument("research_id"); p.add_argument("source_id"); p.add_argument("--handoff", required=True)
    p.add_argument("--native-root", required=True); p.add_argument("--key", required=True); p.add_argument("--work-dir")
    p = commands.add_parser("register-product")
    p.add_argument("execution_id"); p.add_argument("--receipt", required=True)
    p = commands.add_parser("register-external-execution")
    p.add_argument("research_id"); p.add_argument("--protocol", choices=["SCENE", "GENERIC"], required=True)
    p.add_argument("--handoff", required=True); p.add_argument("--native-root", required=True); p.add_argument("--work-dir", required=True)
    p = commands.add_parser("register-report")
    p.add_argument("research_id"); p.add_argument("report"); p.add_argument("--executions", nargs="+", required=True)
    p.add_argument("--products", nargs="*", default=[])
    for name in ("verify", "show"):
        commands.add_parser(name).add_argument("record_id")
    commands.add_parser("reindex")
    p = commands.add_parser("research-status")
    p.add_argument("research_id")
    p.add_argument("--planning-root")
    p.add_argument("--attestation-root")
    p.add_argument("--legacy-root", action="append", default=[])
    p.add_argument("--acquisition-root", action="append", default=[])
    p.add_argument("--work-ref-id", action="append", default=[])
    for name in ("list-works", "list-sources", "list-research", "list-executions", "list-products", "list-reports"):
        p = commands.add_parser(name)
        p.add_argument("--query"); p.add_argument("--work-ref-id"); p.add_argument("--source-revision"); p.add_argument("--research-id")
    p = commands.add_parser("search-text")
    p.add_argument("source_id"); p.add_argument("--query", required=True); p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0); p.add_argument("--include-text", action="store_true")
    p = commands.add_parser("show-evidence")
    p.add_argument("product_id"); p.add_argument("--record-id", required=True); p.add_argument("--include-text", action="store_true")
    p = commands.add_parser("read-product")
    p.add_argument("product_id"); p.add_argument("--query"); p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0); p.add_argument("--include-text", action="store_true")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    root = args.pop("library_root")
    try:
        library = Library.initialize(root) if command == "init" else Library(root)
        if command == "init":
            result = library.config
        elif command.startswith("list-"):
            kinds = {"works": "source", "sources": "source", "research": "research", "executions": "execution", "products": "product", "reports": "report"}
            result = library.list_records(kinds[command[5:]], **args)
            if command == "list-works":
                result["works"] = list({r["record"]["work_ref_id"]: r["record"]["work_ref"] for r in result.pop("records")}.values())
        elif command == "show":
            result = {"record": library.load(args["record_id"]), "validation": library.validate(args["record_id"])}
        elif command == "show-evidence":
            args["native_record_id"] = args.pop("record_id")
            result = library.show_evidence(**args)
        else:
            result = getattr(library, command.replace("-", "_"))(**args)
        print(json.dumps({"format_version": FORMAT, "command": command, "result": result}, ensure_ascii=False, indent=2))
        return 1 if result.get("outcome") == "FAIL" or result.get("issues") else 0
    except (PipelineError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"format_version": FORMAT, "command": command, "error": str(exc),
                          "code": getattr(exc, "code", "E-LIBRARY-IO")}, ensure_ascii=False))
        return 4 if getattr(exc, "code", None) == "E-LIBRARY-NOT-READY" else 1 if isinstance(exc, (PipelineError, ValueError)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
