"""Non-blocking host coordination over the existing bounded source runner.

No queue or background owner: an OS lock lives only for this synchronous call.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import copy
from uuid import uuid4

import jsonschema
import research_library as lib
from xhnovel_pipeline.phase0_handoff import work_ref_from_declaration
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.file_io import write_immutable

acq = lib.acq
FORMAT = "host-shared-acquisition-v1"


def checked_work(work):
    defs = lib.read_json(lib.ROOT / "contracts/phase0-defs.schema.json")
    schema = {"$defs": defs["$defs"], "$ref": "#/$defs/work_ref"}
    try:
        jsonschema.Draft202012Validator(schema).validate(work)
    except jsonschema.ValidationError as exc:
        lib.fail(exc.message, "E-LIBRARY-WORK")
    if work_ref_from_declaration({"work": work}) != work:
        lib.fail("WorkRef differs from native canonical identity", "E-LIBRARY-WORK")
    return work


def acquisition_key(work, cfg, catalog):
    # Research IDs, paths, retry limits, audit prose and arbitrary source labels
    # must not create a second writer for the same concrete source request.
    source = {k: cfg["source"][k] for k in ("channel", "scope_url", "edition_label", "extractor")}
    return lib.digest(lib.encoded({"work_identity": work["identity"], "source": source,
                                   "entries": catalog["entries"], "chapters": catalog["chapters"]}))


class SharedAcquisition:
    def __init__(self, library, research_id):
        self.library = library
        self.research_id = research_id
        self.research = library.load(research_id, "research")
        library.validate(research_id)

    def directory(self, key):
        # Reuse the strict full-digest parser, without creating a host record.
        self.library._record_path(key)
        return self.library.path(f"acquisition/shared/{key}")

    @contextmanager
    def claim(self, key):
        directory = self.directory(key)
        lock = lib.checked_path(directory / "coordination")
        lib.checked_path(lock / ".novel-ingest.lock")
        manager = acq._exclusive_work_dir(lock)
        try:
            manager.__enter__()
        except ValidationError as exc:
            if exc.code != "E-NOVEL-WORKDIR-LOCKED":
                raise
            yield False
            return
        try:
            yield True
        finally:
            manager.__exit__(None, None, None)

    def observe(self, key, status, **extra):
        result = {"format_version": FORMAT, "library_id": self.library.config["library_id"],
                  "research_record_id": self.research_id, "acquisition_id": key,
                  "run_dir": str(self.directory(key) / "run"), "status": status,
                  "next_action": "RETRY_ACQUIRE" if status == "INITIALIZATION_REQUIRED" else
                                 "TRY_OTHER_WORK" if status in {"BUSY_SKIPPED", "PARTIAL"} else
                                 "PREPARE_HANDOFF" if status == "SEALED" else "REVIEW_SOURCE",
                  "observed_at": datetime.now(timezone.utc).isoformat(), **extra}
        lib.schema_check("shared-acquisition-observation", result)
        path = self.library.path(f"research/{self.research['research_id']}/acquisition-observations/{uuid4().hex}.json")
        write_immutable(path, lib.encoded(result))
        return {**result, "observation_path": str(path)}

    def _snapshot_config(self, directory, cfg, catalog, base):
        cfg, catalog = copy.deepcopy(cfg), copy.deepcopy(catalog)

        def snapshot(ref):
            _, raw = acq.checked_ref(ref, base)
            target = lib.checked_path(directory / "inputs" / (lib.digest(raw) + ".bin"))
            write_immutable(target, raw)
            return acq.ref(target, raw)

        cfg["attestation"] = snapshot(cfg["attestation"])
        for assessment in catalog["assessments"].values():
            assessment["evidence"] = [snapshot(r) for r in assessment["evidence"]]
        if cfg["source"]["browser_authorization"] is not None:
            cfg["source"]["browser_authorization"] = snapshot(cfg["source"]["browser_authorization"])
        cat_data = lib.encoded(catalog)
        cat_path = lib.checked_path(directory / "inputs" / (lib.digest(cat_data) + ".json"))
        write_immutable(cat_path, cat_data)
        cfg["catalog"] = acq.ref(cat_path, cat_data)
        cfg["run_dir"] = str(directory / "run")
        cfg["source"]["id"] = "shared"
        cfg["limits"] = {**acq.DEFAULT_LIMITS, **cfg.get("limits", {})}
        return cfg

    def acquire(self, config, *, work_ref, input=None):
        config = lib.checked_path(config)
        cfg = acq.read_json(config)
        catalog, _, _ = acq.validate_config(cfg, config.parent)
        work = checked_work(lib.read_json(work_ref))
        if cfg["work"] != {"title": work["canonical_title"], "author": work["author"], "language": work["language"]}:
            lib.fail("acquisition config does not match WorkRef", "E-LIBRARY-WORK")
        if (input is None) != (cfg["source"]["channel"] == "C1"):
            lib.fail("shared acquisition supports C1, or C4 with --input; C2 is not implemented")
        if cfg["source"]["channel"] not in {"C1", "C4"}:
            lib.fail("unsupported shared acquisition channel")
        key = acquisition_key(work, cfg, catalog)
        with self.claim(key) as acquired:
            if not acquired:
                return self.observe(key, "BUSY_SKIPPED")
            directory = self.directory(key)
            frozen = self._snapshot_config(directory, cfg, catalog, config.parent)
            cfg_path = lib.checked_path(directory / "config.json")
            write_immutable(cfg_path, lib.encoded(frozen), code="E-LIBRARY-ACQUISITION-CONFLICT",
                            message="shared source already has different frozen policy or inputs; inspect its reference, do not start another run")
            plan = {"format_version": FORMAT, "library_id": self.library.config["library_id"],
                    "acquisition_id": key, "work_ref": work, "config": lib.file_ref(cfg_path),
                    "acquisition_script_sha256": lib.digest(lib.read_bytes(acq.__file__))}
            lib.schema_check("shared-acquisition", plan)
            write_immutable(lib.checked_path(directory / "binding.json"), lib.encoded(plan),
                            code="E-LIBRARY-ACQUISITION-CONFLICT")
            return self._run(key, input=input)

    def _load(self, key):
        directory = self.directory(key)
        plan = lib.read_json(directory / "binding.json")
        lib.schema_check("shared-acquisition", plan)
        if plan["library_id"] != self.library.config["library_id"] or plan["acquisition_id"] != key:
            lib.fail("shared acquisition ownership differs", "E-LIBRARY-BINDING")
        if plan["config"]["path"] != str(directory / "config.json"):
            lib.fail("shared configuration moved", "E-LIBRARY-BINDING")
        lib.ref_bytes(plan["config"])
        if plan["acquisition_script_sha256"] != lib.digest(lib.read_bytes(acq.__file__)):
            lib.fail("shared acquisition implementation changed", "E-LIBRARY-INTEGRITY")
        cfg = acq.read_json(directory / "config.json")
        catalog, _, _ = acq.validate_config(cfg, directory)
        work = checked_work(plan["work_ref"])
        if cfg["run_dir"] != str(directory / "run") or acquisition_key(work, cfg, catalog) != key:
            lib.fail("shared acquisition key or run path differs", "E-LIBRARY-BINDING")
        if cfg["work"] != {"title": work["canonical_title"], "author": work["author"], "language": work["language"]}:
            lib.fail("shared acquisition work differs", "E-LIBRARY-WORK")
        return plan, cfg

    def _sealed(self, key):
        path = self.directory(key) / "sealed.json"
        if not path.exists():
            return None
        ref = lib.read_json(path)
        acq.fields(ref, {"path", "sha256"}, label="shared seal reference")
        sealed = lib.checked_path(ref["path"])
        if sealed.parent != self.library.path("sources/sealed"):
            lib.fail("shared seal outside library", "E-LIBRARY-BINDING")
        if lib.digest(lib.read_bytes(sealed / "source-manifest.json")) != ref["sha256"]:
            lib.fail("shared seal reference digest differs", "E-LIBRARY-INTEGRITY")
        _, frozen_run = acq.validate_sealed(sealed)
        run = acq.Run(self.directory(key) / "run")
        if frozen_run.binding != run.binding:
            lib.fail("shared seal belongs to another acquisition", "E-LIBRARY-BINDING")
        return str(sealed)

    def _run(self, key, *, input=None, inspect=False):
        _, cfg = self._load(key)
        sealed = self._sealed(key)
        if sealed:
            return self.observe(key, "SEALED", sealed_path=sealed)
        # Run.initialize is idempotent, including recovery after interruption
        # before the native binding finished publishing. Native code owns recovery.
        run = acq.Run.initialize(self.directory(key) / "config.json")
        state = run.status()
        if state["acquisition"] == "ENTRIES_ACQUIRED":
            return self.observe(key, "READY_FOR_REVIEW", native_status=state)
        if not inspect:
            if cfg["source"]["channel"] == "C1" and input is None:
                state = run.acquire()
            elif cfg["source"]["channel"] == "C4" and input is not None:
                state = run.import_local(lib.checked_path(input))
            else:
                lib.fail("resume must use frozen channel; C4 requires --input")
        return self.observe(key, "READY_FOR_REVIEW" if state["acquisition"] == "ENTRIES_ACQUIRED" else "PARTIAL",
                            native_status=state)

    def resume(self, acquisition_id, *, input=None, inspect=False):
        with self.claim(acquisition_id) as acquired:
            if not acquired:
                return self.observe(acquisition_id, "BUSY_SKIPPED")
            if not (self.directory(acquisition_id) / "binding.json").exists():
                return self.observe(acquisition_id, "INITIALIZATION_REQUIRED")
            return self._run(acquisition_id, input=input, inspect=inspect)

    def seal(self, acquisition_id, *, review):
        with self.claim(acquisition_id) as acquired:
            if not acquired:
                return self.observe(acquisition_id, "BUSY_SKIPPED")
            self._load(acquisition_id)
            sealed = self._sealed(acquisition_id)
            if not sealed:
                sealed = str(acq.seal(acq.Run(self.directory(acquisition_id) / "run"),
                                      self.library.path("sources/sealed"), lib.checked_path(review)))
                # A crash before publication can leave a validated orphan seal;
                # it never makes an unvalidated path available to followers.
                write_immutable(lib.checked_path(self.directory(acquisition_id) / "sealed.json"),
                                lib.encoded({"path": sealed, "sha256": lib.digest(lib.read_bytes(Path(sealed) / "source-manifest.json"))}))
            return self.observe(acquisition_id, "SEALED", sealed_path=sealed)
