from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from uuid import uuid4

SCHEMA_VERSION = 1
ALLOWED_KINDS = {
    "RAW_EVIDENCE",
    "EXPERIENCE_SET",
    "GOVERNANCE_SNAPSHOT",
    "NETWORK_GENERATION",
    "PREDICTION_SET",
    "OUTCOME_SET",
    "EVALUATION",
    "BEHAVIOR",
    "HEALTH",
    "PARENT_SNAPSHOT",
    "COMPUTE_RESULT",
    "DIRECTOR_OUTPUT_ARTIFACT",
    "RUNTIME_INVOCATION_ARTIFACT",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_component(value: str) -> str:
    value = value.strip()
    if not value or value in {".", ".."}:
        raise ValueError("Empty/unsafe storage component")
    if "/" in value or "\\" in value or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
        raise ValueError(f"Unsafe storage component: {value}")
    return value


@dataclass(frozen=True)
class ArtifactRecord:
    artifact_id: str
    artifact_kind: str
    world_id: str
    domain_id: Optional[str]
    entity_id: Optional[str]
    route_key: Optional[str]
    generation: Optional[int]
    storage_relpath: str
    content_sha256: str
    byte_size: int
    governance_refs: Sequence[str]
    parent_artifact_refs: Sequence[str]
    metadata: Mapping[str, Any]
    created_unix: int
    immutable: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind,
            "world_id": self.world_id,
            "domain_id": self.domain_id,
            "entity_id": self.entity_id,
            "route_key": self.route_key,
            "generation": self.generation,
            "storage_relpath": self.storage_relpath,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "governance_refs": list(self.governance_refs),
            "parent_artifact_refs": list(self.parent_artifact_refs),
            "metadata": dict(self.metadata),
            "created_unix": self.created_unix,
            "immutable": self.immutable,
        }


class ArtifactRegistry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.storage_root = self.root / "artifacts" / "storage"
        self.db_path = self.root / "artifacts" / "artifact_registry.sqlite3"
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    artifact_kind TEXT NOT NULL,
                    world_id TEXT NOT NULL,
                    domain_id TEXT,
                    entity_id TEXT,
                    route_key TEXT,
                    generation INTEGER,
                    storage_relpath TEXT NOT NULL UNIQUE,
                    content_sha256 TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    governance_refs_json TEXT NOT NULL,
                    parent_artifact_refs_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_unix INTEGER NOT NULL,
                    immutable INTEGER NOT NULL CHECK(immutable = 1)
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(world_id, domain_id, entity_id, route_key)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(artifact_kind)")

    def _scope_dir(
        self,
        *,
        world_id: str,
        domain_id: Optional[str],
        entity_id: Optional[str],
        route_key: Optional[str],
        generation: Optional[int],
    ) -> Path:
        parts = [_safe_component(world_id)]
        if domain_id:
            parts.append(_safe_component(domain_id))
        if entity_id:
            parts.extend(["entities", _safe_component(entity_id)])
        if route_key:
            parts.extend(["routes", _safe_component(route_key)])
        if generation is not None:
            if int(generation) < 1:
                raise ValueError("generation must be >= 1")
            parts.extend(["generations", f"GEN__{int(generation):06d}"])
        path = self.storage_root
        for part in parts:
            path = path / part
        return path

    def register_bytes(
        self,
        data: bytes,
        *,
        artifact_kind: str,
        world_id: str,
        domain_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        route_key: Optional[str] = None,
        generation: Optional[int] = None,
        governance_refs: Optional[Iterable[str]] = None,
        parent_artifact_refs: Optional[Iterable[str]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        suffix: str = ".bin",
        artifact_id: Optional[str] = None,
        created_unix: Optional[int] = None,
    ) -> ArtifactRecord:
        if artifact_kind not in ALLOWED_KINDS:
            raise ValueError(f"Unsupported artifact_kind: {artifact_kind}")
        if not world_id.startswith("WORLD__"):
            raise ValueError(f"Invalid world_id: {world_id}")
        if domain_id is not None and not domain_id.startswith("DOMAIN__"):
            raise ValueError(f"Invalid domain_id: {domain_id}")
        refs = list(governance_refs or [])
        parents = list(parent_artifact_refs or [])
        content_hash = sha256(data).hexdigest()
        aid = artifact_id or f"ART__{uuid4().hex.upper()}"
        created = int(time.time()) if created_unix is None else int(created_unix)
        directory = self._scope_dir(
            world_id=world_id,
            domain_id=domain_id,
            entity_id=entity_id,
            route_key=route_key,
            generation=generation,
        ) / artifact_kind
        directory.mkdir(parents=True, exist_ok=True)
        clean_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        filename = f"{_safe_component(aid)}__{content_hash[:16]}{clean_suffix}"
        path = directory / filename
        if path.exists():
            existing = sha256(path.read_bytes()).hexdigest()
            if existing != content_hash:
                raise ValueError(f"Immutable artifact path collision: {path}")
        else:
            path.write_bytes(data)
        relpath = str(path.relative_to(self.root))
        record = ArtifactRecord(
            artifact_id=aid,
            artifact_kind=artifact_kind,
            world_id=world_id,
            domain_id=domain_id,
            entity_id=entity_id,
            route_key=route_key,
            generation=generation,
            storage_relpath=relpath,
            content_sha256=content_hash,
            byte_size=len(data),
            governance_refs=refs,
            parent_artifact_refs=parents,
            metadata=dict(metadata or {}),
            created_unix=created,
            immutable=True,
        )
        with self._connect() as con:
            try:
                con.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_id,artifact_kind,world_id,domain_id,entity_id,route_key,generation,
                        storage_relpath,content_sha256,byte_size,governance_refs_json,parent_artifact_refs_json,
                        metadata_json,created_unix,immutable
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                    """,
                    (
                        aid, artifact_kind, world_id, domain_id, entity_id, route_key, generation,
                        relpath, content_hash, len(data), _canonical_json(refs), _canonical_json(parents),
                        _canonical_json(record.metadata), created,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Artifact already registered or storage path reused: {aid}") from exc
        return record

    def register_json(self, value: Any, **kwargs: Any) -> ArtifactRecord:
        return self.register_bytes(
            (_canonical_json(value) + "\n").encode("utf-8"), suffix=".json", **kwargs
        )

    def list_records(self) -> List[ArtifactRecord]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM artifacts ORDER BY created_unix, artifact_id").fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"], artifact_kind=row["artifact_kind"],
            world_id=row["world_id"], domain_id=row["domain_id"], entity_id=row["entity_id"],
            route_key=row["route_key"], generation=row["generation"], storage_relpath=row["storage_relpath"],
            content_sha256=row["content_sha256"], byte_size=int(row["byte_size"]),
            governance_refs=json.loads(row["governance_refs_json"]),
            parent_artifact_refs=json.loads(row["parent_artifact_refs_json"]),
            metadata=json.loads(row["metadata_json"]), created_unix=int(row["created_unix"]),
            immutable=bool(row["immutable"]),
        )

    def verify_integrity(self) -> Dict[str, Any]:
        records = self.list_records()
        errors: List[str] = []
        for record in records:
            path = self.root / record.storage_relpath
            if not path.is_file():
                errors.append(f"missing file: {record.artifact_id}")
                continue
            data = path.read_bytes()
            if len(data) != record.byte_size:
                errors.append(f"byte_size mismatch: {record.artifact_id}")
            if sha256(data).hexdigest() != record.content_sha256:
                errors.append(f"sha256 mismatch: {record.artifact_id}")
            if not record.immutable:
                errors.append(f"artifact not immutable: {record.artifact_id}")
        by_kind: Dict[str, int] = {}
        for record in records:
            by_kind[record.artifact_kind] = by_kind.get(record.artifact_kind, 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "VALID" if not errors else "INVALID",
            "artifact_count": len(records),
            "by_kind": dict(sorted(by_kind.items())),
            "errors": errors,
            "database": str(self.db_path),
            "storage_root": str(self.storage_root),
        }


def artifact_registry_status(root: Path) -> Dict[str, Any]:
    return ArtifactRegistry(root).verify_integrity()
