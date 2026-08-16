from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ssi_v5.compute.fabric import canonical_json, sha256_file


@dataclass(frozen=True)
class ResultVerification:
    ok: bool
    status: str
    job_id: Optional[str]
    worker_id: Optional[str]
    envelope_id: Optional[str]
    checks: Dict[str, bool]
    errors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "job_id": self.job_id,
            "worker_id": self.worker_id,
            "envelope_id": self.envelope_id,
            "checks": dict(self.checks),
            "errors": list(self.errors),
        }


class ResultBundleVerifier:
    """Verify a Node-01 result bundle before it is accepted into the i7 queue."""

    REQUIRED_FILES = {"result.json", "job.json", "input_manifest.json", "result_manifest.json"}

    def __init__(self, queue: Any) -> None:
        self.queue = queue

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object: {path.name}")
        return value

    def verify(self, result_dir: Path) -> ResultVerification:
        result_dir = Path(result_dir)
        checks: Dict[str, bool] = {}
        errors: List[str] = []
        job_id: Optional[str] = None
        worker_id: Optional[str] = None
        envelope_id: Optional[str] = None

        try:
            present = {p.name for p in result_dir.iterdir() if p.is_file()}
            missing = self.REQUIRED_FILES - present
            checks["required_files_present"] = not missing
            if missing:
                raise ValueError("Missing required result files: " + ",".join(sorted(missing)))

            result = self._load_json(result_dir / "result.json")
            job = self._load_json(result_dir / "job.json")
            input_manifest = self._load_json(result_dir / "input_manifest.json")
            result_manifest = self._load_json(result_dir / "result_manifest.json")

            job_id = str(result.get("job_id") or "")
            worker_id = str(result.get("worker_id") or "")
            if not job_id:
                raise ValueError("result.json has no job_id")

            row = self.queue.conn.execute(
                "SELECT * FROM compute_jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            checks["job_exists_in_queue"] = row is not None
            if row is None:
                raise ValueError(f"Unknown job_id: {job_id}")
            row = dict(row)

            ids = {
                str(result.get("job_id") or ""),
                str(job.get("job_id") or ""),
                str(input_manifest.get("job_id") or ""),
                str(result_manifest.get("job_id") or ""),
                str(row.get("job_id") or ""),
            }
            checks["job_id_consistent"] = len(ids) == 1 and job_id in ids
            if not checks["job_id_consistent"]:
                errors.append("job_id mismatch across result/job/manifests/queue")

            checks["worker_identity"] = worker_id == "node-01" and str(result_manifest.get("worker_id")) == "node-01"
            if not checks["worker_identity"]:
                errors.append("unexpected worker identity")

            manifest_entries = result_manifest.get("files") or []
            expected_hashes: Dict[str, Dict[str, Any]] = {}
            for item in manifest_entries:
                if not isinstance(item, dict) or not item.get("path"):
                    errors.append("invalid result manifest entry")
                    continue
                expected_hashes[str(item["path"])] = item

            actual_files = sorted(
                str(p.relative_to(result_dir))
                for p in result_dir.rglob("*")
                if p.is_file() and p.name != "result_manifest.json"
            )
            checks["result_manifest_file_set"] = set(actual_files) == set(expected_hashes)
            if not checks["result_manifest_file_set"]:
                errors.append("result manifest file set mismatch")

            hash_ok = True
            for rel, item in expected_hashes.items():
                p = result_dir / rel
                if not p.is_file():
                    hash_ok = False
                    continue
                if int(item.get("size", -1)) != p.stat().st_size:
                    hash_ok = False
                if str(item.get("sha256") or "") != sha256_file(p):
                    hash_ok = False
            checks["result_file_hashes"] = hash_ok
            if not hash_ok:
                errors.append("result file hash mismatch")

            local_bundle_path = row.get("local_bundle_path")
            local_manifest_path = Path(str(local_bundle_path)) / "manifest.json" if local_bundle_path else None
            local_job_path = Path(str(local_bundle_path)) / "job.json" if local_bundle_path else None
            local_manifest = self._load_json(local_manifest_path) if local_manifest_path and local_manifest_path.is_file() else None
            checks["trusted_local_bundle_present"] = local_manifest is not None and local_job_path is not None and local_job_path.is_file()
            if not checks["trusted_local_bundle_present"]:
                errors.append("trusted local bundle is unavailable")
            else:
                checks["input_manifest_matches_i7"] = canonical_json(input_manifest) == canonical_json(local_manifest)
                if not checks["input_manifest_matches_i7"]:
                    errors.append("input manifest differs from i7 bundle manifest")
                checks["job_json_matches_i7"] = sha256_file(result_dir / "job.json") == sha256_file(local_job_path)
                if not checks["job_json_matches_i7"]:
                    errors.append("job.json differs from i7 submitted job")

            lineage_ok = (
                str(result.get("world_key")) == str(row.get("world_key"))
                and str(result.get("domain_key")) == str(row.get("domain_key"))
                and str(job.get("world_key")) == str(row.get("world_key"))
                and str(job.get("domain_key")) == str(row.get("domain_key"))
                and job.get("network_id") == row.get("network_id")
                and job.get("generation") == row.get("generation")
            )
            checks["job_lineage"] = lineage_ok
            if not lineage_ok:
                errors.append("world/domain/network/generation lineage mismatch")

            queued_payload = json.loads(str(row.get("payload_json") or "{}"))
            queued_env = queued_payload.get("job_envelope") if isinstance(queued_payload, dict) else None
            result_env = (job.get("payload") or {}).get("job_envelope") if isinstance(job.get("payload"), dict) else None
            if queued_env is not None:
                envelope_id = str(queued_env.get("envelope_id") or "")
                checks["envelope_lineage"] = bool(envelope_id) and canonical_json(queued_env) == canonical_json(result_env)
                if not checks["envelope_lineage"]:
                    errors.append("JobEnvelope lineage mismatch")
            else:
                checks["envelope_lineage"] = True

        except Exception as exc:
            errors.append(str(exc))

        ok = bool(checks) and all(checks.values()) and not errors
        return ResultVerification(
            ok=ok,
            status="VERIFIED" if ok else "REJECTED",
            job_id=job_id,
            worker_id=worker_id,
            envelope_id=envelope_id,
            checks=checks,
            errors=errors,
        )


def result_verifier_status() -> Dict[str, Any]:
    return {
        "status": "READY",
        "accept_before_verification": False,
        "unknown_job_result_allowed": False,
        "worker_identity_required": "node-01",
        "input_manifest_must_match_i7_bundle": True,
        "result_manifest_hashes_required": True,
        "job_lineage_required": True,
        "envelope_lineage_required_when_present": True,
        "acknowledge_rejected_result": False,
    }
