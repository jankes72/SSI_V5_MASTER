#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SSI V5 Compute Fabric V2.

Durable, world-aware compute queue on the i7 control node plus an SSH transport
for Node-01. This module does not contain world logic and does not execute
arbitrary code received from a job. It transports typed job bundles and imports
typed result bundles.

Control-plane model:
    SSI/i7 -> local SQLite queue -> SSH/LAN -> Node-01 inbox
    Node-01 results -> SSH/LAN -> i7 result collector -> local queue/artifacts

The SQLite queue stays on the i7. It must not be opened over SSHFS by Node-01.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


def now_unix() -> int:
    return int(time.time())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def safe_segment(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    clean = clean.strip("._")
    if not clean:
        raise ValueError("Path/identity segment cannot be empty")
    return clean[:160]


PRIORITY: Dict[str, int] = {
    "P0_SYSTEM_CRITICAL": 1000,
    "P1_LIVE_PREDICTION": 900,
    "P2_OUTCOME_OBSERVATION": 800,
    "P3_FEATURE_PREDICTION": 700,
    "P4_REQUIRED_RETRAIN": 600,
    "P5_AGENT_ACTIVE_LAB": 500,
    "P6_CHALLENGER_TRAINING": 400,
    "P7_FEATURE_MICRONET_LEARNING": 300,
    "P8_DISCOVERY_EXPERIMENT": 200,
    "P9_MAINTENANCE": 100,
}


@dataclass(frozen=True)
class ResourceRequest:
    cpu_cores: int = 1
    ram_mb: int = 1024
    gpu_mode: str = "AUTO"  # AUTO / CPU / GPU / NONE
    vram_mb: int = 0
    exclusive_gpu: bool = False
    expected_seconds: Optional[int] = None

    def validate(self) -> None:
        if self.cpu_cores < 0 or self.ram_mb < 0 or self.vram_mb < 0:
            raise ValueError("Resource values must be non-negative")
        if self.gpu_mode not in {"AUTO", "CPU", "GPU", "NONE"}:
            raise ValueError(f"Unsupported gpu_mode: {self.gpu_mode}")


@dataclass(frozen=True)
class ComputeJob:
    job_id: str
    job_type: str
    priority_class: str
    world_key: str
    domain_key: str
    discipline: str
    owner_type: str
    owner_id: str
    route_key: str
    network_id: Optional[str]
    generation: Optional[int]
    payload: Dict[str, Any]
    resources: ResourceRequest
    artifact_inputs: List[str] = field(default_factory=list)
    artifact_outputs: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    created_unix: int = field(default_factory=now_unix)
    deadline_unix: Optional[int] = None
    idempotency_key: Optional[str] = None

    @property
    def base_priority(self) -> int:
        try:
            return PRIORITY[self.priority_class]
        except KeyError as exc:
            raise ValueError(f"Unknown priority class: {self.priority_class}") from exc

    def validate(self) -> None:
        safe_segment(self.job_id)
        safe_segment(self.world_key)
        safe_segment(self.domain_key)
        safe_segment(self.discipline)
        self.resources.validate()
        _ = self.base_priority

    @classmethod
    def create(cls, **kwargs: Any) -> "ComputeJob":
        kwargs.setdefault("job_id", uuid.uuid4().hex)
        job = cls(**kwargs)
        job.validate()
        return job


@dataclass(frozen=True)
class WorkerProfile:
    worker_id: str
    hostname: str
    role: str
    cpu_cores: int
    ram_mb: int
    gpu_name: Optional[str]
    vram_mb: int
    workspace: str
    address: str
    ssh_user: str
    labels: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NodeConnection:
    host: str = "192.168.1.7"
    user: str = "pawwe-jankiewicz"
    workspace: str = "/home/pawwe-jankiewicz/ssi_compute"
    connect_timeout_seconds: int = 5
    ssh_port: int = 22

    @property
    def target(self) -> str:
        return f"{self.user}@{self.host}"


class ComputeQueueStore:
    """Durable control-plane queue. Keep this database local to the i7."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._schema()

    def _schema(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS compute_jobs(
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                priority_class TEXT NOT NULL,
                base_priority INTEGER NOT NULL,
                world_key TEXT NOT NULL,
                domain_key TEXT NOT NULL,
                discipline TEXT NOT NULL,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                route_key TEXT NOT NULL,
                network_id TEXT,
                generation INTEGER,
                payload_json TEXT NOT NULL,
                resources_json TEXT NOT NULL,
                artifact_inputs_json TEXT NOT NULL,
                artifact_outputs_json TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                idempotency_key TEXT,
                status TEXT NOT NULL,
                dispatch_status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_unix INTEGER NOT NULL,
                deadline_unix INTEGER,
                queued_unix INTEGER NOT NULL,
                dispatched_unix INTEGER,
                started_unix INTEGER,
                finished_unix INTEGER,
                worker_id TEXT,
                lease_until_unix INTEGER,
                local_bundle_path TEXT,
                remote_bundle_path TEXT,
                result_json TEXT,
                error_text TEXT
            );
            CREATE TABLE IF NOT EXISTS compute_workers(
                worker_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                status TEXT NOT NULL,
                last_heartbeat_unix INTEGER NOT NULL,
                current_job_id TEXT,
                updated_unix INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifact_claims(
                claim_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                owner_type TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                parent_artifact_id TEXT,
                status TEXT NOT NULL,
                created_unix INTEGER NOT NULL,
                UNIQUE(artifact_id,owner_type,owner_id,claim_type)
            );

            CREATE TABLE IF NOT EXISTS compute_events(
                event_id TEXT PRIMARY KEY,
                job_id TEXT,
                worker_id TEXT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_unix INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_compute_events_job
                ON compute_events(job_id,created_unix);
            """
        )
        # Upgrade queues created by the earlier V1 contract without discarding jobs.
        existing = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(compute_jobs)").fetchall()}
        migrations = {
            "world_key": "TEXT NOT NULL DEFAULT 'LEGACY'",
            "discipline": "TEXT NOT NULL DEFAULT 'LEGACY'",
            "idempotency_key": "TEXT",
            "dispatch_status": "TEXT NOT NULL DEFAULT 'LOCAL_ONLY'",
            "dispatched_unix": "INTEGER",
            "local_bundle_path": "TEXT",
            "remote_bundle_path": "TEXT",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE compute_jobs ADD COLUMN {column} {ddl}")
        self.conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_compute_jobs_idempotency
                ON compute_jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_compute_jobs_sched
                ON compute_jobs(status,dispatch_status,base_priority,created_unix);
            CREATE INDEX IF NOT EXISTS idx_compute_jobs_world
                ON compute_jobs(world_key,domain_key,status);
            CREATE INDEX IF NOT EXISTS idx_compute_jobs_owner
                ON compute_jobs(owner_type,owner_id,status);
            """
        )
        self.conn.commit()

    def event(self, event_type: str, payload: Dict[str, Any], *, job_id: Optional[str] = None,
              worker_id: Optional[str] = None) -> None:
        event_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO compute_events(event_id,job_id,worker_id,event_type,payload_json,created_unix) "
            "VALUES(?,?,?,?,?,?)",
            (event_id, job_id, worker_id, event_type, canonical_json(payload), now_unix()),
        )
        self.conn.commit()

    def enqueue(self, job: ComputeJob, local_bundle_path: Optional[str] = None) -> str:
        job.validate()
        try:
            self.conn.execute(
                """
                INSERT INTO compute_jobs(
                    job_id,job_type,priority_class,base_priority,world_key,domain_key,discipline,
                    owner_type,owner_id,route_key,network_id,generation,payload_json,resources_json,
                    artifact_inputs_json,artifact_outputs_json,dependencies_json,idempotency_key,
                    status,dispatch_status,created_unix,deadline_unix,queued_unix,local_bundle_path
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job.job_id, job.job_type, job.priority_class, job.base_priority,
                    job.world_key, job.domain_key, job.discipline, job.owner_type, job.owner_id,
                    job.route_key, job.network_id, job.generation, canonical_json(job.payload),
                    canonical_json(asdict(job.resources)), canonical_json(job.artifact_inputs),
                    canonical_json(job.artifact_outputs), canonical_json(job.dependencies),
                    job.idempotency_key, "QUEUED", "LOCAL_ONLY", job.created_unix,
                    job.deadline_unix, now_unix(), local_bundle_path,
                ),
            )
            self.conn.commit()
            self.event("JOB_ENQUEUED", {"world": job.world_key, "domain": job.domain_key}, job_id=job.job_id)
            return job.job_id
        except sqlite3.IntegrityError:
            if job.idempotency_key:
                row = self.conn.execute(
                    "SELECT job_id FROM compute_jobs WHERE idempotency_key=?",
                    (job.idempotency_key,),
                ).fetchone()
                if row:
                    return str(row["job_id"])
            raise

    def register_worker(self, profile: WorkerProfile, status: str = "REGISTERED_NOT_HEARTBEATING") -> None:
        ts = now_unix()
        self.conn.execute(
            """
            INSERT INTO compute_workers(worker_id,profile_json,status,last_heartbeat_unix,updated_unix)
            VALUES(?,?,?,?,?)
            ON CONFLICT(worker_id) DO UPDATE SET
                profile_json=excluded.profile_json,
                updated_unix=excluded.updated_unix
            """,
            (profile.worker_id, canonical_json(asdict(profile)), status, ts, ts),
        )
        self.conn.commit()

    def mark_dispatched(self, job_id: str, remote_path: str) -> None:
        ts = now_unix()
        self.conn.execute(
            "UPDATE compute_jobs SET dispatch_status='DISPATCHED',status='REMOTE_QUEUED',"
            "dispatched_unix=?,remote_bundle_path=?,error_text=NULL WHERE job_id=?",
            (ts, remote_path, job_id),
        )
        self.conn.commit()
        self.event("JOB_DISPATCHED", {"remote_path": remote_path}, job_id=job_id, worker_id="node-01")

    def mark_dispatch_failed(self, job_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE compute_jobs SET dispatch_status='DISPATCH_FAILED',error_text=?,attempts=attempts+1 "
            "WHERE job_id=?",
            (str(error)[-8000:], job_id),
        )
        self.conn.commit()
        self.event("JOB_DISPATCH_FAILED", {"error": str(error)}, job_id=job_id, worker_id="node-01")

    def mark_result(self, job_id: str, result: Dict[str, Any], *, worker_id: str = "node-01") -> None:
        status = str(result.get("status") or "COMPLETED")
        terminal = {
            "COMPLETED": "COMPLETED",
            "FAILED": "FAILED",
            "WAITING_DEPENDENCY": "WAITING_DEPENDENCY",
            "WAITING_RESOURCES": "WAITING_RESOURCES",
            "WAITING_IMPLEMENTATION": "WAITING_IMPLEMENTATION",
        }.get(status, status)
        ts = now_unix()
        self.conn.execute(
            "UPDATE compute_jobs SET status=?,dispatch_status='RESULT_COLLECTED',finished_unix=?,"
            "worker_id=?,result_json=?,error_text=? WHERE job_id=?",
            (
                terminal, ts, worker_id, canonical_json(result),
                str(result.get("error") or "")[-8000:] or None, job_id,
            ),
        )
        self.conn.execute(
            "UPDATE compute_workers SET status='ONLINE',last_heartbeat_unix=?,current_job_id=NULL,updated_unix=? "
            "WHERE worker_id=?",
            (ts, ts, worker_id),
        )
        self.conn.commit()
        self.event("JOB_RESULT_COLLECTED", result, job_id=job_id, worker_id=worker_id)

    def rows_for_dispatch(self, limit: int = 100) -> List[Dict[str, Any]]:
        now = now_unix()
        rows = self.conn.execute(
            """
            SELECT *,
              (base_priority + MIN(120, CAST((? - queued_unix) / 300 AS INTEGER))) AS effective_priority
            FROM compute_jobs
            WHERE status='QUEUED' AND dispatch_status IN ('LOCAL_ONLY','DISPATCH_FAILED')
            ORDER BY effective_priority DESC,
                     CASE WHEN deadline_unix IS NULL THEN 1 ELSE 0 END,
                     deadline_unix ASC,
                     queued_unix ASC
            LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        now = now_unix()
        rows = self.conn.execute(
            """
            SELECT *,
              (base_priority + MIN(120, CAST((? - queued_unix) / 300 AS INTEGER))) AS effective_priority
            FROM compute_jobs
            WHERE status NOT IN ('COMPLETED','FAILED')
            ORDER BY effective_priority DESC, queued_unix ASC
            LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> Dict[str, Any]:
        rows = self.conn.execute(
            "SELECT status,dispatch_status,COUNT(*) n FROM compute_jobs "
            "GROUP BY status,dispatch_status ORDER BY status,dispatch_status"
        ).fetchall()
        worlds = self.conn.execute(
            "SELECT world_key,domain_key,status,COUNT(*) n FROM compute_jobs "
            "GROUP BY world_key,domain_key,status ORDER BY world_key,domain_key,status"
        ).fetchall()
        workers = self.conn.execute(
            "SELECT worker_id,status,last_heartbeat_unix,current_job_id FROM compute_workers ORDER BY worker_id"
        ).fetchall()
        return {
            "jobs": [dict(r) for r in rows],
            "by_world": [dict(r) for r in worlds],
            "workers": [dict(r) for r in workers],
            "database": str(self.path),
        }


class JobBundleBuilder:
    """Build an immutable, checksum-protected bundle for Node-01."""

    def __init__(self, outbox_root: Path):
        self.outbox_root = Path(outbox_root)
        self.outbox_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            for row in rows:
                fh.write(canonical_json(row) + "\n")
        os.replace(tmp, path)

    def build(self, job: ComputeJob) -> Path:
        target = self.outbox_root / safe_segment(job.world_key) / safe_segment(job.domain_key) / safe_segment(job.job_id)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        payload = dict(job.payload)
        train_records = list(payload.pop("train_records", []) or [])
        observation_records = list(payload.pop("observation_records", []) or [])
        if train_records:
            self._write_jsonl(target / "data" / "train.jsonl", train_records)
        if observation_records:
            self._write_jsonl(target / "data" / "observation.jsonl", observation_records)

        governance = payload.pop("governance_snapshot", None)
        if governance is not None:
            atomic_json_write(target / "governance" / "snapshot.json", governance)

        job_meta = asdict(job)
        job_meta["payload"] = payload
        job_meta["resources"] = asdict(job.resources)
        job_meta["bundle_layout"] = {
            "train": "data/train.jsonl" if train_records else None,
            "observation": "data/observation.jsonl" if observation_records else None,
            "governance": "governance/snapshot.json" if governance is not None else None,
        }
        atomic_json_write(target / "job.json", job_meta)

        files = sorted(p for p in target.rglob("*") if p.is_file())
        manifest = {
            "job_id": job.job_id,
            "world_key": job.world_key,
            "domain_key": job.domain_key,
            "created_unix": now_unix(),
            "files": [
                {
                    "path": str(p.relative_to(target)),
                    "size": p.stat().st_size,
                    "sha256": sha256_file(p),
                }
                for p in files
            ],
        }
        atomic_json_write(target / "manifest.json", manifest)
        return target


class SshNodeTransport:
    """One-way dispatch and result collection over the existing passwordless SSH link."""

    def __init__(self, connection: NodeConnection):
        self.connection = connection

    def _ssh_base(self) -> List[str]:
        return [
            "ssh",
            "-p", str(self.connection.ssh_port),
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.connection.connect_timeout_seconds}",
            "-o", "ServerAliveInterval=5",
            "-o", "ServerAliveCountMax=2",
            self.connection.target,
        ]

    def check_connection(self) -> Dict[str, Any]:
        cmd = self._ssh_base() + ["printf 'SSI_NODE01_OK\\n'"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.connection.connect_timeout_seconds + 5)
        return {
            "ok": proc.returncode == 0 and "SSI_NODE01_OK" in proc.stdout,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "target": self.connection.target,
        }

    def ensure_workspace(self) -> None:
        ws = shlex.quote(self.connection.workspace)
        remote = (
            f"set -eu; mkdir -p {ws}/inbox {ws}/running {ws}/results {ws}/failed "
            f"{ws}/archive {ws}/artifacts {ws}/checkpoints {ws}/logs {ws}/tmp {ws}/.incoming"
        )
        proc = subprocess.run(self._ssh_base() + [remote], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "Could not prepare Node-01 workspace")

    def dispatch_bundle(self, job: ComputeJob, bundle_dir: Path) -> str:
        job_id = safe_segment(job.job_id)
        self.ensure_workspace()
        ws = self.connection.workspace.rstrip("/")
        incoming = f"{ws}/.incoming/{job_id}"
        ready = f"{ws}/inbox/{job_id}.ready"
        remote = (
            "set -eu; "
            f"rm -rf {shlex.quote(incoming)}; mkdir -p {shlex.quote(incoming)}; "
            f"tar -xf - -C {shlex.quote(incoming)}; "
            f"rm -rf {shlex.quote(ready)}; mv {shlex.quote(incoming)} {shlex.quote(ready)}"
        )
        tar_proc = subprocess.Popen(
            ["tar", "-C", str(bundle_dir), "-cf", "-", "."],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert tar_proc.stdout is not None
        ssh_proc = subprocess.Popen(
            self._ssh_base() + [remote],
            stdin=tar_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tar_proc.stdout.close()
        ssh_out, ssh_err = ssh_proc.communicate()
        tar_err = tar_proc.stderr.read() if tar_proc.stderr else b""
        tar_rc = tar_proc.wait()
        if tar_rc != 0:
            raise RuntimeError(f"tar failed: {tar_err.decode(errors='replace')}")
        if ssh_proc.returncode != 0:
            raise RuntimeError(f"SSH dispatch failed: {ssh_err.decode(errors='replace')}")
        return ready

    def list_result_names(self) -> List[str]:
        result_root = f"{self.connection.workspace.rstrip('/')}/results"
        remote = (
            f"if [ -d {shlex.quote(result_root)} ]; then "
            f"find {shlex.quote(result_root)} -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' | sort; fi"
        )
        proc = subprocess.run(self._ssh_base() + [remote], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "Could not list Node-01 results")
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def fetch_result(self, result_name: str, local_result_root: Path) -> Path:
        name = safe_segment(result_name)
        local_result_root = Path(local_result_root)
        local_result_root.mkdir(parents=True, exist_ok=True)
        local_path = local_result_root / name
        if local_path.exists():
            shutil.rmtree(local_path)
        remote_path = f"{self.connection.target}:{self.connection.workspace.rstrip('/')}/results/{name}"
        cmd = [
            "scp", "-P", str(self.connection.ssh_port), "-r",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.connection.connect_timeout_seconds}",
            remote_path, str(local_result_root),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"Could not fetch {name}")
        return local_path

    def acknowledge_result(self, result_name: str) -> None:
        name = safe_segment(result_name)
        path = f"{self.connection.workspace.rstrip('/')}/results/{name}"
        archive = f"{self.connection.workspace.rstrip('/')}/archive/result_{name}"
        remote = f"set -eu; rm -rf {shlex.quote(archive)}; mv {shlex.quote(path)} {shlex.quote(archive)}"
        proc = subprocess.run(self._ssh_base() + [remote], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"Could not acknowledge {name}")


class ComputeBackend:
    """Interface used by SSI worlds. Backends execute jobs, never world logic."""

    def submit(self, job: ComputeJob) -> str:
        raise NotImplementedError


class QueueComputeBackend(ComputeBackend):
    def __init__(self, queue: ComputeQueueStore):
        self.queue = queue

    def submit(self, job: ComputeJob) -> str:
        return self.queue.enqueue(job)


class RemoteQueueComputeBackend(ComputeBackend):
    """Build a job bundle, persist it locally, and dispatch it to Node-01 by SSH."""

    def __init__(self, queue: ComputeQueueStore, bundle_builder: JobBundleBuilder,
                 transport: SshNodeTransport, *, strict_dispatch: bool = False):
        self.queue = queue
        self.bundle_builder = bundle_builder
        self.transport = transport
        self.strict_dispatch = strict_dispatch

    def submit(self, job: ComputeJob) -> str:
        bundle = self.bundle_builder.build(job)
        job_id = self.queue.enqueue(job, local_bundle_path=str(bundle))
        if job_id != job.job_id:
            return job_id
        try:
            remote_path = self.transport.dispatch_bundle(job, bundle)
            self.queue.mark_dispatched(job.job_id, remote_path)
        except Exception as exc:
            self.queue.mark_dispatch_failed(job.job_id, str(exc))
            if self.strict_dispatch:
                raise
        return job.job_id


def dispatch_pending(queue: ComputeQueueStore, transport: SshNodeTransport, *, limit: int = 100) -> Dict[str, Any]:
    dispatched: List[str] = []
    errors: List[Dict[str, str]] = []
    for row in queue.rows_for_dispatch(limit=limit):
        job_id = str(row["job_id"])
        try:
            bundle_text = row.get("local_bundle_path")
            if not bundle_text:
                raise RuntimeError("Missing local_bundle_path")
            bundle = Path(str(bundle_text))
            if not bundle.is_dir():
                raise RuntimeError(f"Bundle directory not found: {bundle}")
            job = ComputeJob(
                job_id=job_id,
                job_type=str(row["job_type"]),
                priority_class=str(row["priority_class"]),
                world_key=str(row["world_key"]),
                domain_key=str(row["domain_key"]),
                discipline=str(row["discipline"]),
                owner_type=str(row["owner_type"]),
                owner_id=str(row["owner_id"]),
                route_key=str(row["route_key"]),
                network_id=row.get("network_id"),
                generation=row.get("generation"),
                payload=json.loads(str(row["payload_json"])),
                resources=ResourceRequest(**json.loads(str(row["resources_json"]))),
                artifact_inputs=list(json.loads(str(row["artifact_inputs_json"]))),
                artifact_outputs=list(json.loads(str(row["artifact_outputs_json"]))),
                dependencies=list(json.loads(str(row["dependencies_json"]))),
                created_unix=int(row["created_unix"]),
                deadline_unix=row.get("deadline_unix"),
                idempotency_key=row.get("idempotency_key"),
            )
            remote_path = transport.dispatch_bundle(job, bundle)
            queue.mark_dispatched(job_id, remote_path)
            dispatched.append(job_id)
        except Exception as exc:
            queue.mark_dispatch_failed(job_id, str(exc))
            errors.append({"job_id": job_id, "error": str(exc)})
    return {"dispatched": dispatched, "errors": errors, "count": len(dispatched)}


def collect_remote_results(queue: ComputeQueueStore, transport: SshNodeTransport,
                           local_result_root: Path, *, acknowledge: bool = True) -> Dict[str, Any]:
    from ssi_v5.compute.result_verifier import ResultBundleVerifier

    collected: List[str] = []
    rejected: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    verifier = ResultBundleVerifier(queue)
    for result_name in transport.list_result_names():
        try:
            local_dir = transport.fetch_result(result_name, local_result_root)
            verification = verifier.verify(local_dir)
            if not verification.ok:
                rejected.append({"result": result_name, "verification": verification.to_dict()})
                continue
            result_path = local_dir / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            job_id = str(verification.job_id)
            result["collector_verification"] = verification.to_dict()
            queue.mark_result(job_id, result, worker_id=str(verification.worker_id or "node-01"))
            if acknowledge:
                transport.acknowledge_result(result_name)
            collected.append(job_id)
        except Exception as exc:
            errors.append({"result": result_name, "error": str(exc)})
    return {
        "collected": collected,
        "rejected": rejected,
        "errors": errors,
        "count": len(collected),
        "rejected_count": len(rejected),
    }


NODE01_DEFAULT_CONNECTION = NodeConnection(
    host=os.environ.get("SSI_NODE01_HOST", "192.168.1.7"),
    user=os.environ.get("SSI_NODE01_USER", "pawwe-jankiewicz"),
    workspace=os.environ.get("SSI_NODE01_WORKSPACE", "/home/pawwe-jankiewicz/ssi_compute"),
    ssh_port=int(os.environ.get("SSI_NODE01_SSH_PORT", "22")),
)

NODE01_DEFAULT_PROFILE = WorkerProfile(
    worker_id="node-01",
    hostname="pawwe-jankiewicz-MS-7A15",
    role="LOCAL_COMPUTE_WORKER",
    cpu_cores=4,
    ram_mb=15 * 1024,
    gpu_name="NVIDIA GeForce GTX 970",
    vram_mb=4096,
    workspace=NODE01_DEFAULT_CONNECTION.workspace,
    address=NODE01_DEFAULT_CONNECTION.host,
    ssh_user=NODE01_DEFAULT_CONNECTION.user,
    labels={
        "arch": "x86_64",
        "gpu_generation": "Maxwell",
        "gpu_policy": "AUTO_BENCHMARK_OR_CPU_FALLBACK",
        "max_parallel_gpu_jobs": 1,
        "reserved_cpu_cores": 1,
        "soft_training_ram_mb": 10 * 1024,
        "transport": "SSH_LAN",
    },
)


def bootstrap_default_store(root: Path) -> ComputeQueueStore:
    store = ComputeQueueStore(Path(root) / "compute" / "compute_queue.sqlite3")
    store.register_worker(NODE01_DEFAULT_PROFILE, status="REGISTERED_NOT_HEARTBEATING")
    return store


def build_remote_backend(root: Path, *, strict_dispatch: bool = False) -> RemoteQueueComputeBackend:
    root = Path(root)
    queue = bootstrap_default_store(root)
    builder = JobBundleBuilder(root / "compute" / "outbox")
    transport = SshNodeTransport(NODE01_DEFAULT_CONNECTION)
    return RemoteQueueComputeBackend(queue, builder, transport, strict_dispatch=strict_dispatch)



def sync_node01_worker_status(
    root: Path,
    *,
    stale_after_seconds: int = 30,
    ssh_timeout_seconds: int = 5,
) -> Dict[str, Any]:
    """Synchronize Node-01 worker_status.json into the local i7 compute queue."""
    root = Path(root)
    store = bootstrap_default_store(root)
    target = NODE01_DEFAULT_CONNECTION.target
    remote_status_path = f"{NODE01_DEFAULT_CONNECTION.workspace.rstrip('/')}/worker_status.json"
    remote = f"cat {shlex.quote(remote_status_path)}"
    try:
        proc = subprocess.run(
            SshNodeTransport(NODE01_DEFAULT_CONNECTION)._ssh_base() + [remote],
            capture_output=True,
            text=True,
            timeout=int(ssh_timeout_seconds) + 3,
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "worker_id": NODE01_DEFAULT_PROFILE.worker_id, "status": "SYNC_FAILED", "error": str(exc)}
    if proc.returncode != 0:
        return {
            "ok": False,
            "worker_id": NODE01_DEFAULT_PROFILE.worker_id,
            "status": "SYNC_FAILED",
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip(),
        }
    try:
        payload = json.loads(proc.stdout)
    except Exception as exc:
        return {"ok": False, "worker_id": NODE01_DEFAULT_PROFILE.worker_id, "status": "INVALID_REMOTE_STATUS", "error": str(exc)}
    worker_id = str(payload.get("worker_id") or NODE01_DEFAULT_PROFILE.worker_id)
    remote_status = str(payload.get("status") or "UNKNOWN")
    heartbeat_unix = int(payload.get("updated_unix") or 0)
    current_job_id = payload.get("current_job_id")
    now = now_unix()
    age = max(0, now - heartbeat_unix) if heartbeat_unix else None
    effective = remote_status if age is not None and age <= int(stale_after_seconds) else "STALE"
    store.conn.execute(
        "UPDATE compute_workers SET status=?,last_heartbeat_unix=?,current_job_id=?,updated_unix=? WHERE worker_id=?",
        (effective, heartbeat_unix, current_job_id, now, worker_id),
    )
    store.conn.commit()
    return {
        "ok": True,
        "worker_id": worker_id,
        "remote_status": remote_status,
        "status": effective,
        "last_heartbeat_unix": heartbeat_unix,
        "heartbeat_age_seconds": age,
        "current_job_id": current_job_id,
        "target": target,
    }

def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="SSI V5 Compute Fabric V2")
    p.add_argument("--root", default="./dane/ssi_canonical")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--check-node", action="store_true")
    p.add_argument("--collect", action="store_true")
    p.add_argument("--dispatch-pending", action="store_true")
    args = p.parse_args()

    root = Path(args.root)
    store = bootstrap_default_store(root)
    transport = SshNodeTransport(NODE01_DEFAULT_CONNECTION)
    out: Dict[str, Any] = {"queue": store.summary()}
    if args.check_node:
        out["node01"] = transport.check_connection()
    if args.dispatch_pending:
        out["dispatch"] = dispatch_pending(store, transport)
    if args.collect:
        out["collection"] = collect_remote_results(
            store, transport, root / "compute" / "results_collected"
        )
    if args.summary or args.check_node or args.collect:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
