#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SSI V5 Node-01 worker.

Runs on the i5/GTX 970 host. It watches atomic job bundles delivered by the i7
through SSH, chooses the highest-priority eligible job, executes only known job
types, and writes an immutable result bundle. It never opens the i7 SQLite
queue and never executes arbitrary Python from a job bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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

ALLOWED_JOB_TYPES = {
    "HEALTHCHECK",
    "MODEL_TRAIN_AND_OBSERVE",
}


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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"JSONL row is not an object at {path}:{line_no}")
            rows.append(value)
    return rows


def read_mem_available_mb() -> int:
    try:
        with Path("/proc/meminfo").open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def gpu_snapshot() -> Dict[str, Any]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=name,memory.total,memory.free,temperature.gpu,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0 or not proc.stdout.strip():
            return {"available": False, "error": proc.stderr.strip()}
        row = [part.strip() for part in proc.stdout.splitlines()[0].split(",")]
        return {
            "available": True,
            "name": row[0],
            "memory_total_mb": int(float(row[1])),
            "memory_free_mb": int(float(row[2])),
            "temperature_c": int(float(row[3])),
            "utilization_pct": int(float(row[4])),
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def system_snapshot() -> Dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "cpu_count": int(os.cpu_count() or 1),
        "memory_available_mb": read_mem_available_mb(),
        "gpu": gpu_snapshot(),
        "unix": now_unix(),
    }


@dataclass(frozen=True)
class CandidateJob:
    path: Path
    job: Dict[str, Any]
    effective_priority: int


class NodeWorkspace:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.inbox = self.root / "inbox"
        self.running = self.root / "running"
        self.results = self.root / "results"
        self.failed = self.root / "failed"
        self.archive = self.root / "archive"
        self.logs = self.root / "logs"
        self.tmp = self.root / "tmp"
        for path in (self.inbox, self.running, self.results, self.failed, self.archive, self.logs, self.tmp):
            path.mkdir(parents=True, exist_ok=True)

    def heartbeat(self, current_job_id: Optional[str], status: str) -> None:
        atomic_json_write(
            self.root / "worker_status.json",
            {
                "worker_id": "node-01",
                "hostname": socket.gethostname(),
                "status": status,
                "current_job_id": current_job_id,
                "resources": system_snapshot(),
                "updated_unix": now_unix(),
            },
        )


class NodeWorker:
    def __init__(self, workspace: NodeWorkspace):
        self.workspace = workspace

    @staticmethod
    def _load_job(path: Path) -> Dict[str, Any]:
        job_path = path / "job.json"
        if not job_path.is_file():
            raise ValueError(f"Missing job.json in {path}")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        if not isinstance(job, dict):
            raise ValueError("job.json must contain an object")
        job_type = str(job.get("job_type") or "")
        if job_type not in ALLOWED_JOB_TYPES:
            raise ValueError(f"Job type not allowed: {job_type}")
        return job

    @staticmethod
    def _eligible(job: Dict[str, Any], snapshot: Dict[str, Any]) -> Tuple[bool, str]:
        resources = dict(job.get("resources") or {})
        cpu = int(resources.get("cpu_cores") or 0)
        ram = int(resources.get("ram_mb") or 0)
        gpu_mode = str(resources.get("gpu_mode") or "AUTO").upper()
        vram = int(resources.get("vram_mb") or 0)
        if cpu > int(snapshot.get("cpu_count") or 1):
            return False, "CPU_REQUEST_EXCEEDS_NODE"
        if ram > int(snapshot.get("memory_available_mb") or 0):
            return False, "WAITING_RAM"
        gpu = dict(snapshot.get("gpu") or {})
        if gpu_mode == "GPU":
            if not gpu.get("available"):
                return False, "GPU_REQUIRED_NOT_AVAILABLE"
            if vram > int(gpu.get("memory_free_mb") or 0):
                return False, "WAITING_VRAM"
        return True, "ELIGIBLE"

    def _candidates(self) -> List[CandidateJob]:
        snapshot = system_snapshot()
        now = now_unix()
        candidates: List[CandidateJob] = []
        for path in sorted(self.workspace.inbox.glob("*.ready")):
            if not path.is_dir():
                continue
            try:
                job = self._load_job(path)
                eligible, _ = self._eligible(job, snapshot)
                if not eligible:
                    continue
                priority_name = str(job.get("priority_class") or "P9_MAINTENANCE")
                base = PRIORITY.get(priority_name, 0)
                age = max(0, now - int(job.get("created_unix") or now))
                effective = base + min(120, age // 300)
                candidates.append(CandidateJob(path, job, effective))
            except Exception as exc:
                self._quarantine_invalid(path, str(exc))
        candidates.sort(
            key=lambda c: (
                -c.effective_priority,
                int(c.job.get("deadline_unix") or 2**62),
                int(c.job.get("created_unix") or 0),
            )
        )
        return candidates

    def _quarantine_invalid(self, path: Path, error: str) -> None:
        target = self.workspace.failed / f"{path.name}.invalid_{now_unix()}"
        if target.exists():
            shutil.rmtree(target)
        try:
            os.replace(path, target)
        except OSError:
            shutil.move(str(path), str(target))
        atomic_json_write(target / "result.json", {
            "status": "FAILED",
            "error": error,
            "worker_id": "node-01",
            "finished_unix": now_unix(),
        })

    def claim_next(self) -> Optional[Tuple[Path, Dict[str, Any]]]:
        candidates = self._candidates()
        if not candidates:
            return None
        chosen = candidates[0]
        job_id = str(chosen.job.get("job_id") or chosen.path.name.split(".", 1)[0])
        target = self.workspace.running / job_id
        if target.exists():
            shutil.rmtree(target)
        try:
            os.replace(chosen.path, target)
        except FileNotFoundError:
            return None
        return target, chosen.job

    @staticmethod
    def _feature_names(rows: Sequence[Dict[str, Any]]) -> List[str]:
        names = set()
        for row in rows:
            features = row.get("features") or {}
            if isinstance(features, dict):
                names.update(str(k) for k in features)
        return sorted(names)

    @staticmethod
    def _matrix(rows: Sequence[Dict[str, Any]], feature_names: Sequence[str]):
        import numpy as np

        return np.asarray([
            [float((row.get("features") or {}).get(name) or 0.0) for name in feature_names]
            for row in rows
        ], dtype=float)

    @staticmethod
    def _target_values(rows: Sequence[Dict[str, Any]], outputs: Sequence[str]):
        values: List[Any] = []
        for row in rows:
            target = row.get("target") or {}
            selected = [target.get(name) for name in outputs]
            values.append(selected[0] if len(selected) == 1 else selected)
        return values

    @staticmethod
    def _all_numeric(values: Sequence[Any]) -> bool:
        def numeric(v: Any) -> bool:
            if isinstance(v, bool) or v is None:
                return False
            if isinstance(v, (int, float)):
                return math.isfinite(float(v))
            if isinstance(v, list):
                return bool(v) and all(numeric(x) for x in v)
            return False
        return bool(values) and all(numeric(v) for v in values)

    @staticmethod
    def _sequence_data(train_rows: Sequence[Dict[str, Any]], observation_rows: Sequence[Dict[str, Any]],
                       feature_names: Sequence[str], window: int):
        import numpy as np

        if window < 2:
            raise ValueError("Sequence window must be >= 2")

        def feature_vector(row: Dict[str, Any]) -> List[float]:
            f = row.get("features") or {}
            return [float(f.get(name) or 0.0) for name in feature_names]

        train_vecs = [feature_vector(r) for r in train_rows]
        X_train: List[List[float]] = []
        train_indices: List[int] = []
        for i in range(window - 1, len(train_rows)):
            flattened: List[float] = []
            for vec in train_vecs[i - window + 1:i + 1]:
                flattened.extend(vec)
            X_train.append(flattened)
            train_indices.append(i)

        combined_rows = list(train_rows[-(window - 1):]) + list(observation_rows)
        combined_vecs = [feature_vector(r) for r in combined_rows]
        X_obs: List[List[float]] = []
        obs_indices: List[int] = []
        offset = window - 1
        for i in range(offset, len(combined_rows)):
            flattened = []
            for vec in combined_vecs[i - window + 1:i + 1]:
                flattened.extend(vec)
            X_obs.append(flattened)
            obs_indices.append(i - offset)

        expanded_names = [f"LAG_{lag}__{name}" for lag in reversed(range(window)) for name in feature_names]
        return (
            np.asarray(X_train, dtype=float), train_indices,
            np.asarray(X_obs, dtype=float), obs_indices, expanded_names,
        )

    @staticmethod
    def _regression_metrics(y_true, y_pred) -> Dict[str, Any]:
        import numpy as np
        from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

        mae = float(mean_absolute_error(y_true, y_pred))
        rmse = float(math.sqrt(mean_squared_error(y_true, y_pred)))
        try:
            r2 = float(r2_score(y_true, y_pred))
        except Exception:
            r2 = float("nan")
        return {
            "task": "REGRESSION",
            "mae": mae,
            "rmse": rmse,
            "r2": r2 if math.isfinite(r2) else None,
            "score": -mae,
            "samples": int(len(y_true)),
        }

    @staticmethod
    def _classification_metrics(y_true, y_pred, model, X) -> Dict[str, Any]:
        from sklearn.metrics import accuracy_score, balanced_accuracy_score, log_loss

        accuracy = float(accuracy_score(y_true, y_pred))
        balanced = float(balanced_accuracy_score(y_true, y_pred))
        out: Dict[str, Any] = {
            "task": "CLASSIFICATION",
            "accuracy": accuracy,
            "balanced_accuracy": balanced,
            "score": accuracy,
            "samples": int(len(y_true)),
        }
        try:
            proba = model.predict_proba(X)
            out["log_loss"] = float(log_loss(y_true, proba, labels=getattr(model, "classes_", None)))
        except Exception:
            out["log_loss"] = None
        return out

    def _train_sklearn(self, job_dir: Path, job: Dict[str, Any]) -> Dict[str, Any]:
        try:
            import joblib
            import numpy as np
            from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
            from sklearn.linear_model import Ridge
            from sklearn.neural_network import MLPClassifier, MLPRegressor
            from sklearn.pipeline import Pipeline
            from sklearn.preprocessing import StandardScaler
        except Exception as exc:
            return {
                "status": "WAITING_DEPENDENCY",
                "error": f"scikit-learn stack unavailable: {exc}",
                "required": ["numpy", "scikit-learn", "joblib"],
            }

        layout = job.get("bundle_layout") or {}
        train_path = job_dir / str(layout.get("train") or "data/train.jsonl")
        observation_path = job_dir / str(layout.get("observation") or "data/observation.jsonl")
        train_rows = load_jsonl(train_path)
        observation_rows = load_jsonl(observation_path)
        if not train_rows or not observation_rows:
            return {
                "status": "FAILED",
                "error": "Training and observation data are both required",
                "train_count": len(train_rows),
                "observation_count": len(observation_rows),
            }

        payload = dict(job.get("payload") or {})
        target_spec = payload.get("target_spec") or {}
        outputs = list(target_spec.get("outputs") or [])
        if not outputs:
            output_names = set()
            for row in train_rows:
                output_names.update((row.get("target") or {}).keys())
            outputs = sorted(output_names)
        if not outputs:
            return {"status": "FAILED", "error": "No target outputs in job"}

        model_kind = str(payload.get("model_kind") or "MLP").upper()
        architecture = dict(payload.get("architecture") or {})
        advanced = {"GRU", "LSTM", "TRANSFORMER", "GRU_REGRESSION", "LSTM_REGRESSION", "TRANSFORMER_REGRESSION"}
        if model_kind in advanced:
            return {
                "status": "WAITING_IMPLEMENTATION",
                "error": f"Advanced runner {model_kind} is not enabled in this worker build",
                "device_probe": self._resolve_device(job),
            }

        feature_names = self._feature_names(train_rows)
        if not feature_names:
            return {"status": "FAILED", "error": "No numeric features found"}

        y_train_all = self._target_values(train_rows, outputs)
        y_obs_all = self._target_values(observation_rows, outputs)
        regression = self._all_numeric(y_train_all) and self._all_numeric(y_obs_all)

        if model_kind == "TIME_SERIES_MLP":
            window = int(architecture.get("window") or 3)
            X_train, train_indices, X_obs, obs_indices, model_feature_names = self._sequence_data(
                train_rows, observation_rows, feature_names, window
            )
            y_train_values = [y_train_all[i] for i in train_indices]
            y_obs_values = [y_obs_all[i] for i in obs_indices]
        else:
            X_train = self._matrix(train_rows, feature_names)
            X_obs = self._matrix(observation_rows, feature_names)
            y_train_values = y_train_all
            y_obs_values = y_obs_all
            model_feature_names = feature_names
            window = 1

        if len(X_train) < 2 or len(X_obs) < 1:
            return {
                "status": "FAILED",
                "error": "Insufficient rows after preprocessing",
                "train_rows": int(len(X_train)),
                "observation_rows": int(len(X_obs)),
            }

        if regression:
            y_train = np.asarray(y_train_values, dtype=float)
            y_obs = np.asarray(y_obs_values, dtype=float)
            if model_kind in {"LINEAR_REGRESSION", "RIDGE"}:
                model = Pipeline([("scale", StandardScaler()), ("model", Ridge(alpha=1.0))])
            elif model_kind in {"RANDOM_FOREST", "RANDOM_FOREST_REGRESSION"}:
                model = RandomForestRegressor(
                    n_estimators=int(architecture.get("n_estimators") or 200),
                    max_depth=architecture.get("max_depth"),
                    random_state=42,
                    n_jobs=max(1, min(3, (os.cpu_count() or 2) - 1)),
                )
            else:
                layers = tuple(int(x) for x in (architecture.get("layers") or [32, 16]))
                model = Pipeline([
                    ("scale", StandardScaler()),
                    ("model", MLPRegressor(
                        hidden_layer_sizes=layers,
                        random_state=42,
                        max_iter=700,
                        early_stopping=True,
                        validation_fraction=0.15,
                    )),
                ])
        else:
            def label(v: Any) -> str:
                return canonical_json(v) if isinstance(v, list) else str(v)
            y_train = np.asarray([label(v) for v in y_train_values], dtype=object)
            y_obs = np.asarray([label(v) for v in y_obs_values], dtype=object)
            if model_kind in {"RANDOM_FOREST", "RANDOM_FOREST_CLASSIFICATION"}:
                model = RandomForestClassifier(
                    n_estimators=int(architecture.get("n_estimators") or 200),
                    max_depth=architecture.get("max_depth"),
                    random_state=42,
                    n_jobs=max(1, min(3, (os.cpu_count() or 2) - 1)),
                    class_weight="balanced_subsample",
                )
            else:
                layers = tuple(int(x) for x in (architecture.get("layers") or [32, 16]))
                model = Pipeline([
                    ("scale", StandardScaler()),
                    ("model", MLPClassifier(
                        hidden_layer_sizes=layers,
                        random_state=42,
                        max_iter=700,
                        early_stopping=True,
                        validation_fraction=0.15,
                    )),
                ])

        started = time.time()
        model.fit(X_train, y_train)
        train_pred = model.predict(X_train)
        obs_pred = model.predict(X_obs)
        elapsed = time.time() - started
        if regression:
            train_metrics = self._regression_metrics(y_train, train_pred)
            observation_metrics = self._regression_metrics(y_obs, obs_pred)
        else:
            train_metrics = self._classification_metrics(y_train, train_pred, model, X_train)
            observation_metrics = self._classification_metrics(y_obs, obs_pred, model, X_obs)

        artifact_rel = Path("artifacts") / str(job.get("world_key")) / str(job.get("discipline")) / str(job.get("network_id") or "network") / f"generation_{int(job.get('generation') or 0):04d}" / str(payload.get("trial_id") or "trial")
        artifact_dir = job_dir / "output" / artifact_rel
        artifact_dir.mkdir(parents=True, exist_ok=True)
        model_path = artifact_dir / "model.joblib"
        joblib.dump(model, model_path)
        atomic_json_write(artifact_dir / "feature_schema.json", {
            "base_feature_names": feature_names,
            "model_feature_names": model_feature_names,
            "sequence_window": window,
            "target_outputs": outputs,
            "task": "REGRESSION" if regression else "CLASSIFICATION",
        })
        atomic_json_write(artifact_dir / "metrics.json", {
            "train": train_metrics,
            "observation": observation_metrics,
            "fit_seconds": elapsed,
        })
        atomic_json_write(artifact_dir / "predictor_manifest.json", {
            "runner": "SSI_V5_NODE01_WORKER_SKLEARN_V1",
            "model_kind": model_kind,
            "model_file": "model.joblib",
            "feature_schema": "feature_schema.json",
            "prediction_type": target_spec.get("prediction_type"),
            "outputs": outputs,
            "device": "CPU",
        })

        return {
            "status": "COMPLETED",
            "model_kind": model_kind,
            "trial_id": payload.get("trial_id"),
            "train_metrics": train_metrics,
            "observation_metrics": observation_metrics,
            "score": observation_metrics.get("score"),
            "passed": True,
            "fit_seconds": elapsed,
            "device": "CPU",
            "artifact_relative_path": str(artifact_rel),
            "model_relative_path": str(artifact_rel / "model.joblib"),
            "train_count": int(len(X_train)),
            "observation_count": int(len(X_obs)),
        }

    @staticmethod
    def _resolve_device(job: Dict[str, Any]) -> Dict[str, Any]:
        resources = dict(job.get("resources") or {})
        mode = str(resources.get("gpu_mode") or "AUTO").upper()
        gpu = gpu_snapshot()
        result = {"requested_mode": mode, "gpu": gpu, "selected": "CPU"}
        if mode in {"GPU", "AUTO"} and gpu.get("available"):
            if int(gpu.get("memory_free_mb") or 0) >= int(resources.get("vram_mb") or 0):
                result["selected"] = "GPU_CANDIDATE"
        return result

    def execute(self, job_dir: Path, job: Dict[str, Any]) -> Dict[str, Any]:
        job_type = str(job.get("job_type") or "")
        if job_type == "HEALTHCHECK":
            return {"status": "COMPLETED", "health": system_snapshot()}
        if job_type == "MODEL_TRAIN_AND_OBSERVE":
            return self._train_sklearn(job_dir, job)
        return {"status": "FAILED", "error": f"Unsupported job type: {job_type}"}

    def process_once(self) -> bool:
        claimed = self.claim_next()
        if claimed is None:
            self.workspace.heartbeat(None, "IDLE")
            return False
        job_dir, job = claimed
        job_id = str(job.get("job_id") or job_dir.name)
        self.workspace.heartbeat(job_id, "RUNNING")
        started = now_unix()
        result: Dict[str, Any]
        try:
            result = self.execute(job_dir, job)
        except Exception as exc:
            result = {
                "status": "FAILED",
                "error": str(exc),
                "traceback": traceback.format_exc(limit=25),
            }
        result.update({
            "job_id": job_id,
            "worker_id": "node-01",
            "world_key": job.get("world_key"),
            "domain_key": job.get("domain_key"),
            "discipline": job.get("discipline"),
            "network_id": job.get("network_id"),
            "generation": job.get("generation"),
            "started_unix": started,
            "finished_unix": now_unix(),
            "resources_after": system_snapshot(),
        })

        suffix = "done" if result.get("status") == "COMPLETED" else "failed"
        result_dir = self.workspace.results / f"{job_id}.{suffix}"
        if result_dir.exists():
            shutil.rmtree(result_dir)
        result_dir.mkdir(parents=True, exist_ok=True)
        output_dir = job_dir / "output"
        if output_dir.is_dir():
            shutil.copytree(output_dir, result_dir / "output", dirs_exist_ok=True)
        atomic_json_write(result_dir / "result.json", result)
        shutil.copy2(job_dir / "job.json", result_dir / "job.json")
        manifest = job_dir / "manifest.json"
        if manifest.is_file():
            shutil.copy2(manifest, result_dir / "input_manifest.json")

        result_files = sorted(
            p for p in result_dir.rglob("*")
            if p.is_file() and p.name != "result_manifest.json"
        )
        atomic_json_write(result_dir / "result_manifest.json", {
            "job_id": job_id,
            "worker_id": "node-01",
            "created_unix": now_unix(),
            "files": [
                {
                    "path": str(p.relative_to(result_dir)),
                    "size": p.stat().st_size,
                    "sha256": sha256_file(p),
                }
                for p in result_files
            ],
        })

        archive_dir = self.workspace.archive / f"{job_id}.{now_unix()}"
        if archive_dir.exists():
            shutil.rmtree(archive_dir)
        os.replace(job_dir, archive_dir)
        self.workspace.heartbeat(None, "IDLE")
        return True


def main() -> int:
    p = argparse.ArgumentParser(description="SSI V5 Node-01 Worker")
    p.add_argument("--workspace", default=os.environ.get("SSI_NODE01_WORKSPACE", "~/ssi_compute"))
    p.add_argument("--once", action="store_true")
    p.add_argument("--poll-seconds", type=float, default=2.0)
    args = p.parse_args()

    workspace = NodeWorkspace(Path(args.workspace))
    worker = NodeWorker(workspace)
    workspace.heartbeat(None, "STARTING")
    if args.once:
        worker.process_once()
        return 0

    while True:
        try:
            processed = worker.process_once()
            if not processed:
                time.sleep(max(0.2, args.poll_seconds))
        except KeyboardInterrupt:
            workspace.heartbeat(None, "STOPPED")
            return 0
        except Exception:
            workspace.heartbeat(None, "ERROR")
            with (workspace.logs / "worker_errors.log").open("a", encoding="utf-8") as fh:
                fh.write(f"\n[{now_unix()}]\n{traceback.format_exc()}\n")
            time.sleep(max(1.0, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
