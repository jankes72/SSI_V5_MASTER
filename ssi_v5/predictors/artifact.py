from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable

from ssi_v5.lifecycle.generation_registry import GenerationLifecycleRegistry, LifecycleError

REQUIRED_FILES = ("model.joblib", "feature_schema.json", "metrics.json", "predictor_manifest.json")
FORBIDDEN_GENERATION_STATES = {"PLANNED", "QUEUED", "FAILED", "QUARANTINED", "RETIRED"}


class PredictorArtifactError(RuntimeError):
    pass


def _now_unix() -> int:
    return int(time.time())


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_segment(value: str) -> str:
    value = str(value).strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise PredictorArtifactError(f"unsafe path segment: {value!r}")
    return value


@dataclass(frozen=True)
class PredictorArtifactRecord:
    predictor_artifact_id: str
    generation_id: str
    world_id: str
    domain_id: str
    network_id: str
    generation: int
    governance_id: str
    governance_hash: str
    source_job_id: str
    source_envelope_id: str | None
    storage_path: str
    manifest_hash: str
    created_unix: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PredictorArtifactRegistry:
    """Immutable predictor artifacts bound to an existing lifecycle generation.

    This registry validates and stores predictor material. It does not execute a
    predictor, evaluate outcomes, select a champion, or promote a generation.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.storage_root = self.root / "predictors" / "storage"
        self.database = self.root / "predictors" / "predictor_artifacts.sqlite3"
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.database)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS predictor_artifacts(
                predictor_artifact_id TEXT PRIMARY KEY,
                generation_id TEXT NOT NULL UNIQUE,
                world_id TEXT NOT NULL,
                domain_id TEXT NOT NULL,
                network_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                governance_id TEXT NOT NULL,
                governance_hash TEXT NOT NULL,
                source_job_id TEXT NOT NULL,
                source_envelope_id TEXT,
                storage_path TEXT NOT NULL UNIQUE,
                manifest_hash TEXT NOT NULL,
                created_unix INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _load_generation(self, generation_id: str):
        lifecycle = GenerationLifecycleRegistry(self.root / "lifecycle" / "generation_lifecycle.sqlite3")
        try:
            return lifecycle.get(generation_id)
        except LifecycleError as exc:
            raise PredictorArtifactError(str(exc)) from exc
        finally:
            lifecycle.close()

    def register_from_directory(self, source_dir: Path, *, generation_id: str) -> PredictorArtifactRecord:
        source_dir = Path(source_dir).resolve()
        if not source_dir.is_dir():
            raise PredictorArtifactError("source predictor directory does not exist")
        for name in REQUIRED_FILES:
            if not (source_dir / name).is_file():
                raise PredictorArtifactError(f"required predictor file missing: {name}")

        generation_record = self._load_generation(generation_id)
        if generation_record.state in FORBIDDEN_GENERATION_STATES:
            raise PredictorArtifactError(
                f"generation state does not allow predictor material: {generation_record.state}"
            )

        try:
            manifest = json.loads((source_dir / "predictor_manifest.json").read_text(encoding="utf-8"))
        except Exception as exc:
            raise PredictorArtifactError("invalid predictor_manifest.json") from exc

        expected = {
            "world_id": generation_record.world_id,
            "domain_id": generation_record.domain_id,
            "network_id": generation_record.network_id,
            "generation": generation_record.generation,
            "generation_id": generation_record.generation_id,
            "governance_id": generation_record.governance_id,
            "governance_hash": generation_record.governance_hash,
        }
        for key, expected_value in expected.items():
            if manifest.get(key) != expected_value:
                raise PredictorArtifactError(
                    f"predictor lineage mismatch for {key}: {manifest.get(key)!r} != {expected_value!r}"
                )

        source_job_id = str(manifest.get("source_job_id") or manifest.get("job_id") or "").strip()
        if not source_job_id:
            raise PredictorArtifactError("predictor manifest requires source_job_id/job_id")
        source_envelope_id = manifest.get("source_envelope_id") or manifest.get("envelope_id")
        if source_envelope_id is not None:
            source_envelope_id = str(source_envelope_id)

        declared_hashes = manifest.get("file_hashes")
        if not isinstance(declared_hashes, dict):
            raise PredictorArtifactError("predictor manifest requires file_hashes")
        for name in ("model.joblib", "feature_schema.json", "metrics.json"):
            actual = _sha256_file(source_dir / name)
            if declared_hashes.get(name) != actual:
                raise PredictorArtifactError(f"predictor file hash mismatch: {name}")

        artifact_id = f"PREDART__{uuid.uuid4().hex.upper()}"
        destination = (
            self.storage_root
            / _safe_segment(generation_record.world_id)
            / _safe_segment(generation_record.domain_id)
            / _safe_segment(generation_record.network_id)
            / f"GEN_{generation_record.generation:06d}__{_safe_segment(generation_record.generation_id)}"
        )
        if destination.exists():
            raise PredictorArtifactError("predictor artifact destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)

        temp_destination = destination.with_name(destination.name + f".tmp.{uuid.uuid4().hex}")
        temp_destination.mkdir(parents=False, exist_ok=False)
        try:
            for name in REQUIRED_FILES:
                shutil.copy2(source_dir / name, temp_destination / name)

            material_hashes = {name: _sha256_file(temp_destination / name) for name in REQUIRED_FILES}
            artifact_manifest = {
                "schema_version": 1,
                "predictor_artifact_id": artifact_id,
                "generation_id": generation_record.generation_id,
                "world_id": generation_record.world_id,
                "domain_id": generation_record.domain_id,
                "network_id": generation_record.network_id,
                "generation": generation_record.generation,
                "generation_state_at_registration": generation_record.state,
                "governance_id": generation_record.governance_id,
                "governance_hash": generation_record.governance_hash,
                "source_job_id": source_job_id,
                "source_envelope_id": source_envelope_id,
                "material_hashes": material_hashes,
                "execution_authority": False,
                "promotion_authority": False,
            }
            manifest_text = _canonical_json(artifact_manifest)
            manifest_hash = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
            (temp_destination / "predictor_artifact.json").write_text(
                json.dumps({**artifact_manifest, "manifest_hash": manifest_hash}, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temp_destination, destination)
        except Exception:
            if temp_destination.exists():
                shutil.rmtree(temp_destination, ignore_errors=True)
            raise

        ts = _now_unix()
        try:
            self.conn.execute(
                """
                INSERT INTO predictor_artifacts(
                    predictor_artifact_id,generation_id,world_id,domain_id,network_id,generation,
                    governance_id,governance_hash,source_job_id,source_envelope_id,
                    storage_path,manifest_hash,created_unix
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    artifact_id, generation_record.generation_id, generation_record.world_id,
                    generation_record.domain_id, generation_record.network_id, generation_record.generation,
                    generation_record.governance_id, generation_record.governance_hash, source_job_id,
                    source_envelope_id, str(destination), manifest_hash, ts,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise PredictorArtifactError("predictor artifact already registered for generation") from exc

        return self.get(artifact_id)

    def get(self, predictor_artifact_id: str) -> PredictorArtifactRecord:
        row = self.conn.execute(
            "SELECT * FROM predictor_artifacts WHERE predictor_artifact_id=?", (predictor_artifact_id,)
        ).fetchone()
        if row is None:
            raise PredictorArtifactError(f"unknown predictor_artifact_id: {predictor_artifact_id}")
        return PredictorArtifactRecord(**dict(row))

    def verify(self, predictor_artifact_id: str) -> Dict[str, Any]:
        record = self.get(predictor_artifact_id)
        directory = Path(record.storage_path)
        errors = []
        manifest_path = directory / "predictor_artifact.json"
        if not manifest_path.is_file():
            errors.append("predictor_artifact.json missing")
            return {"status": "INVALID", "predictor_artifact_id": predictor_artifact_id, "errors": errors}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            errors.append("predictor_artifact.json invalid")
            return {"status": "INVALID", "predictor_artifact_id": predictor_artifact_id, "errors": errors}

        stored_manifest_hash = manifest.pop("manifest_hash", None)
        actual_manifest_hash = hashlib.sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
        if stored_manifest_hash != actual_manifest_hash or record.manifest_hash != actual_manifest_hash:
            errors.append("artifact manifest hash mismatch")
        for name, expected_hash in manifest.get("material_hashes", {}).items():
            path = directory / name
            if not path.is_file():
                errors.append(f"material missing: {name}")
            elif _sha256_file(path) != expected_hash:
                errors.append(f"material hash mismatch: {name}")
        return {
            "status": "VALID" if not errors else "INVALID",
            "predictor_artifact_id": predictor_artifact_id,
            "generation_id": record.generation_id,
            "errors": errors,
        }

    def summary(self) -> Dict[str, Any]:
        count = int(self.conn.execute("SELECT COUNT(*) FROM predictor_artifacts").fetchone()[0])
        return {
            "status": "READY",
            "schema_version": 1,
            "database": str(self.database),
            "storage_root": str(self.storage_root),
            "artifact_count": count,
        }


def predictor_artifact_status(root: Path | None = None) -> Dict[str, Any]:
    base = Path(root or "./dane/ssi_canonical").expanduser().resolve()
    registry = PredictorArtifactRegistry(base)
    try:
        summary = registry.summary()
    finally:
        registry.close()
    return {
        **summary,
        "required_material": list(REQUIRED_FILES),
        "immutable_after_registration": True,
        "generation_lineage_required": True,
        "governance_lineage_required": True,
        "source_job_lineage_required": True,
        "artifact_can_execute_itself": False,
        "artifact_can_promote_generation": False,
    }
