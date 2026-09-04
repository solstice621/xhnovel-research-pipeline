from __future__ import annotations

import os
import pathlib
import tempfile

from .errors import ValidationError
from .hashing import artifact_id_for, sha256_bytes, strip_sha_prefix


class ArtifactStore:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = pathlib.Path(root)
        (self.root / "sha256").mkdir(parents=True, exist_ok=True)

    def _path(self, artifact_id: str) -> pathlib.Path:
        digest = strip_sha_prefix(artifact_id)
        return self.root / "sha256" / digest[:2] / digest

    @staticmethod
    def _verify_published(dest: pathlib.Path, artifact_id: str, data: bytes) -> None:
        try:
            existing = dest.read_bytes()
        except OSError as exc:
            raise ValidationError("E-CAS-VERIFY", f"verify failed for {artifact_id}") from exc
        if existing != data:
            raise ValidationError("E-CAS-COLLISION", f"hash collision at {artifact_id}")
        if sha256_bytes(existing) != strip_sha_prefix(artifact_id):
            raise ValidationError("E-CAS-VERIFY", f"verify failed for {artifact_id}")

    def put(self, data: bytes) -> str:
        artifact_id = artifact_id_for(data)
        dest = self._path(artifact_id)
        if dest.exists():
            self._verify_published(dest, artifact_id, data)
            return artifact_id
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                # Publish without replacing an existing CAS object. Concurrent
                # writers of the same bytes race only on this atomic link: the
                # winner publishes, and every loser verifies the winner below.
                # This avoids Windows sharing violations caused by os.replace
                # while another writer is reading the destination.
                os.link(tmp_name, dest)
            except FileExistsError:
                pass
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        self._verify_published(dest, artifact_id, data)
        return artifact_id

    def get(self, artifact_id: str) -> bytes:
        path = self._path(artifact_id)
        if not path.is_file():
            raise ValidationError("E-ARTIFACT-MISSING", f"missing {artifact_id}")
        data = path.read_bytes()
        if artifact_id_for(data) != artifact_id:
            raise ValidationError("E-ARTIFACT-CORRUPT", f"corrupt {artifact_id}")
        return data

    def exists(self, artifact_id: str) -> bool:
        return self._path(artifact_id).is_file()

    def verify(self, artifact_id: str) -> None:
        self.get(artifact_id)

    def delete_for_test(self, artifact_id: str) -> None:
        path = self._path(artifact_id)
        path.unlink(missing_ok=True)
