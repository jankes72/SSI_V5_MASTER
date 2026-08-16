#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSI V5 - CANONICAL WORLD FACTORY
================================
Nowy rdzeń światów SSI budowany od zera na podstawie istniejących, sprawdzonych
wzorców projektu:

- DATA / READINESS GATE
- COMPLEXITY GATE
- MODEL ROUTER / MODEL LAB
- chronologiczne 60% TRAIN / 40% OBSERVATION
- generacje modeli, bez nadpisywania historii
- retraining dopiero po wzroście danych (domyślnie +20%)
- parent -> child intelligence inheritance
- Network / Prediction / Strategy / Behavior Artifacts
- provenance, lineage, checkpointy
- WAIT / ABSTAIN zamiast wymuszania treningu

Ten plik jest rdzeniem wszystkich zarejestrowanych światów. Planowanie pozostaje
na i7, a ciężkie zadania są pakowane i wysyłane przez SSH/LAN do Node-01.
"""

from __future__ import annotations

import dataclasses
import csv
import argparse
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ssi_v5.compute.fabric import (
    ComputeBackend, ComputeJob, ResourceRequest, build_remote_backend,
    bootstrap_default_store, SshNodeTransport, NODE01_DEFAULT_CONNECTION,
    collect_remote_results
)

CSV_SEPARATOR = ";"
CSV_ENCODING = "utf-8"
DEFAULT_TRAIN_FRACTION = 0.60
DEFAULT_OBSERVATION_FRACTION = 0.40
DEFAULT_RETRAIN_GROWTH = 0.20
SCHEMA_VERSION = 4


# ============================================================
# HELPERS
# ============================================================

def now_unix() -> int:
    return int(time.time())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def hash_id(*parts: Any, size: int = 32) -> str:
    raw = "|".join(canonical_json(p) if isinstance(p, (dict, list, tuple)) else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:size]


def atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# ============================================================
# CANONICAL POLICY OBJECTS
# ============================================================

@dataclass(frozen=True)
class SplitPolicy:
    train_fraction: float = DEFAULT_TRAIN_FRACTION
    observation_fraction: float = DEFAULT_OBSERVATION_FRACTION
    mode: str = "CHRONOLOGICAL"
    shuffle: bool = False
    observation_leakage: str = "FORBIDDEN"

    def validate(self) -> None:
        if self.mode != "CHRONOLOGICAL":
            raise ValueError("SSI requires chronological split for canonical observation.")
        if self.shuffle:
            raise ValueError("Shuffle is forbidden for canonical SSI observation.")
        if abs(self.train_fraction + self.observation_fraction - 1.0) > 1e-9:
            raise ValueError("Train + observation fractions must equal 1.0")


@dataclass(frozen=True)
class RetrainingPolicy:
    min_growth_fraction: float = DEFAULT_RETRAIN_GROWTH
    min_absolute_new_samples: int = 1
    preserve_generations: bool = True


@dataclass(frozen=True)
class ReadinessPolicy:
    min_samples: int
    min_periods: int
    min_target_diversity: int = 2
    max_missing_fraction: float = 0.10
    min_identity_match_fraction: float = 0.95
    require_verified_outcomes: bool = True


@dataclass(frozen=True)
class ComplexityTier:
    tier: int
    min_samples: int
    min_periods: int
    max_features: int
    mlp_architectures: Tuple[Tuple[int, ...], ...]
    sequence_windows: Tuple[int, ...] = ()
    advanced_sequence_models: Tuple[str, ...] = ()


DEFAULT_COMPLEXITY_TIERS: Tuple[ComplexityTier, ...] = (
    ComplexityTier(1, 24, 2, 24, ((16,),)),
    ComplexityTier(2, 80, 4, 48, ((24,), (32, 16)), (3,)),
    ComplexityTier(3, 240, 8, 80, ((32, 16), (64, 32, 16)), (3, 6)),
    ComplexityTier(4, 800, 20, 128, ((64, 32, 16), (128, 64, 32)), (6, 12), ("GRU", "LSTM", "TRANSFORMER")),
)


@dataclass(frozen=True)
class GovernanceBundle:
    constitution: Dict[str, Any]
    policies: Dict[str, Any]
    rules: Dict[str, Any]


@dataclass(frozen=True)
class TargetSpec:
    target_id: str
    prediction_type: str
    outputs: Tuple[str, ...]
    description: str
    partial_targets_allowed: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DomainSpec:
    world: str
    discipline: str
    target: TargetSpec
    readiness: ReadinessPolicy
    governance: GovernanceBundle
    parent_domains: Tuple[str, ...] = ()
    dynamic_membership: bool = False
    notes: str = ""


# ============================================================
# CANONICAL DOMAINS
# ============================================================

def _gov(world: str, discipline: str) -> GovernanceBundle:
    return GovernanceBundle(
        constitution={
            "world": world,
            "discipline": discipline,
            "principles": [
                "EVIDENCE_BEFORE_CONFIDENCE",
                "UNKNOWN_WAIT_ABSTAIN_ALLOWED",
                "NO_OBSERVATION_LEAKAGE",
                "PRESERVE_PROVENANCE",
                "KNOWLEDGE_IS_NOT_AUTHORITY",
                "NO_DIRECT_NETWORK_TO_EXECUTION",
            ],
        },
        policies={
            "csv_separator": CSV_SEPARATOR,
            "csv_encoding": CSV_ENCODING,
            "split": asdict(SplitPolicy()),
            "retraining": asdict(RetrainingPolicy()),
            "generations_are_immutable": True,
            "teacher_publishes_artifacts_not_authority": True,
        },
        rules={
            "event_identity_is_primary": True,
            "conflicting_verified_results": "QUARANTINE",
            "missing_exact_target": "ALLOW_ONLY_COMPATIBLE_PARTIAL_TARGETS",
        },
    )


DOMAIN_REGISTRY: Dict[str, DomainSpec] = {
    "SPORTS.TENNIS": DomainSpec(
        "SPORTS", "TENNIS",
        TargetSpec("tennis_winner", "TWO_WAY_OUTCOME", ("winner_side",), "Winner of the tennis event."),
        ReadinessPolicy(80, 7), _gov("SPORTS", "TENNIS"),
    ),
    "SPORTS.SOCCER": DomainSpec(
        "SPORTS", "SOCCER",
        TargetSpec("soccer_exact_score", "EXACT_SCORE", ("home_goals", "away_goals"), "Exact final score; 1/X/2 is derived.", ("result_1x2",)),
        ReadinessPolicy(120, 10, min_target_diversity=5), _gov("SPORTS", "SOCCER"),
    ),
    "SPORTS.BASEBALL": DomainSpec(
        "SPORTS", "BASEBALL",
        TargetSpec("baseball_exact_score", "EXACT_SCORE", ("home_runs", "away_runs"), "Exact final run score; winner/margin/total are derived.", ("winner_side",)),
        ReadinessPolicy(120, 10, min_target_diversity=5), _gov("SPORTS", "BASEBALL"),
    ),
    "SPORTS.BASKETBALL": DomainSpec(
        "SPORTS", "BASKETBALL",
        TargetSpec("basketball_exact_score", "EXACT_SCORE", ("home_points", "away_points"), "Exact final point score; winner/margin/total are derived.", ("winner_side",)),
        ReadinessPolicy(120, 10, min_target_diversity=8), _gov("SPORTS", "BASKETBALL"),
    ),
    "SPORTS.E_SPORTS": DomainSpec(
        "SPORTS", "E_SPORTS",
        TargetSpec("esports_exact_series_score", "EXACT_SCORE", ("side_a_score", "side_b_score"), "Exact series/map score, with winner as compatible partial target.", ("winner_side",)),
        ReadinessPolicy(100, 8, min_target_diversity=4), _gov("SPORTS", "E_SPORTS"),
    ),
    "SPORTS.GOLF": DomainSpec(
        "SPORTS", "GOLF",
        TargetSpec("golf_top_n", "BINARY_MARKET", ("target_hit",), "Whether player finishes inside the market threshold Top-N."),
        ReadinessPolicy(100, 3), _gov("SPORTS", "GOLF"),
        notes="Collect odds immediately; training waits for verified tournament outcomes.",
    ),
    "MARKETS.CURRENCY": DomainSpec(
        "MARKETS", "CURRENCY",
        TargetSpec("currency_relative_move", "RELATIVE_PRICE_MOVE", ("future_return_pct",), "Future relative currency movement across a defined horizon."),
        ReadinessPolicy(120, 5), _gov("MARKETS", "CURRENCY"),
    ),
    "MARKETS.CRYPTO": DomainSpec(
        "MARKETS", "CRYPTO",
        TargetSpec("crypto_relative_move", "RELATIVE_PRICE_MOVE", ("future_return_pct",), "Future crypto movement/cross strength across a defined horizon."),
        ReadinessPolicy(120, 3), _gov("MARKETS", "CRYPTO"),
        dynamic_membership=True,
        notes="Primary assets should be selected dynamically (e.g. Top-N current market activity), historical artifacts remain preserved.",
    ),
    "CAPITAL.STOCK": DomainSpec(
        "CAPITAL", "STOCK",
        TargetSpec("stock_future_move", "PRICE_MOVE", ("future_return_pct",), "Future stock percentage price move."),
        ReadinessPolicy(120, 10), _gov("CAPITAL", "STOCK"),
        parent_domains=("MARKETS.CURRENCY", "MARKETS.CRYPTO"),
    ),
    "CAPITAL.BOND": DomainSpec(
        "CAPITAL", "BOND",
        TargetSpec("bond_future_bp", "BASIS_POINT_MOVE", ("future_move_bp",), "Future bond/yield movement in basis points."),
        ReadinessPolicy(120, 10), _gov("CAPITAL", "BOND"),
        parent_domains=("MARKETS.CURRENCY", "MARKETS.CRYPTO"),
    ),
}


WORLD_REGISTRY: Dict[str, Tuple[str, ...]] = {
    "SPORTS": tuple(key for key in DOMAIN_REGISTRY if key.startswith("SPORTS.")),
    "MARKETS": tuple(key for key in DOMAIN_REGISTRY if key.startswith("MARKETS.")),
    "CAPITAL": tuple(key for key in DOMAIN_REGISTRY if key.startswith("CAPITAL.")),
}

def world_for_domain(domain_key: str) -> str:
    return DOMAIN_REGISTRY[domain_key].world


# ============================================================
# CANONICAL RECORDS / ARTIFACTS
# ============================================================

@dataclass
class ExperienceRecord:
    experience_id: str
    domain_key: str
    source_id: str
    event_unix: int
    period_key: str
    features: Dict[str, float]
    target: Dict[str, Any]
    target_complete: bool
    verified: bool
    provenance: Dict[str, Any]
    quality: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReadinessDecision:
    ready: bool
    status: str
    reasons: List[str]
    sample_count: int
    period_count: int
    missing_fraction: float
    target_diversity: int


@dataclass
class TrainingDecision:
    action: str  # WAIT / TRAIN / RETRAIN / SKIP / QUARANTINE
    reason: str
    current_samples: int
    last_train_samples: int
    growth_fraction: float


@dataclass
class TrialResult:
    trial_id: str
    model_kind: str
    architecture: Dict[str, Any]
    complexity_tier: int
    train_metrics: Dict[str, Any]
    observation_metrics: Dict[str, Any]
    score: float
    passed: bool
    model_path: Optional[str] = None


@dataclass
class GenerationArtifact:
    network_id: str
    domain_key: str
    generation: int
    target_spec: Dict[str, Any]
    training_count: int
    observation_count: int
    training_from_unix: int
    training_to_unix: int
    observation_from_unix: int
    observation_to_unix: int
    complexity_tier: int
    champion_trial_id: str
    champion_model_kind: str
    champion_score: float
    trials: List[Dict[str, Any]]
    parent_lineage: List[Dict[str, Any]]
    provenance: Dict[str, Any]
    created_unix: int


# ============================================================
# SQLITE STATE STORE
# ============================================================

class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._schema()

    def _schema(self) -> None:
        c = self.conn
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS experiences(
            experience_id TEXT PRIMARY KEY,
            domain_key TEXT NOT NULL,
            source_id TEXT NOT NULL,
            event_unix INTEGER NOT NULL,
            period_key TEXT NOT NULL,
            features_json TEXT NOT NULL,
            target_json TEXT NOT NULL,
            target_complete INTEGER NOT NULL,
            verified INTEGER NOT NULL,
            provenance_json TEXT NOT NULL,
            quality_json TEXT NOT NULL,
            created_unix INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_exp_domain_time ON experiences(domain_key,event_unix);

        CREATE TABLE IF NOT EXISTS networks(
            network_id TEXT PRIMARY KEY,
            domain_key TEXT NOT NULL,
            status TEXT NOT NULL,
            current_generation INTEGER NOT NULL DEFAULT 0,
            last_train_samples INTEGER NOT NULL DEFAULT 0,
            last_train_unix INTEGER,
            champion_trial_id TEXT,
            champion_model_kind TEXT,
            champion_score REAL,
            updated_unix INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_network_domain ON networks(domain_key,status);

        CREATE TABLE IF NOT EXISTS generations(
            generation_id TEXT PRIMARY KEY,
            network_id TEXT NOT NULL,
            domain_key TEXT NOT NULL,
            generation INTEGER NOT NULL,
            artifact_json TEXT NOT NULL,
            created_unix INTEGER NOT NULL,
            UNIQUE(network_id,generation)
        );

        CREATE TABLE IF NOT EXISTS model_trials(
            trial_id TEXT PRIMARY KEY,
            network_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            model_kind TEXT NOT NULL,
            complexity_tier INTEGER NOT NULL,
            architecture_json TEXT NOT NULL,
            train_metrics_json TEXT NOT NULL,
            observation_metrics_json TEXT NOT NULL,
            score REAL NOT NULL,
            passed INTEGER NOT NULL,
            model_path TEXT,
            created_unix INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS parent_snapshots(
            snapshot_id TEXT PRIMARY KEY,
            child_domain_key TEXT NOT NULL,
            parent_domain_key TEXT NOT NULL,
            parent_generation_ref TEXT,
            snapshot_json TEXT NOT NULL,
            source_unix INTEGER NOT NULL,
            created_unix INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifacts(
            artifact_id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            domain_key TEXT NOT NULL,
            network_id TEXT,
            generation INTEGER,
            payload_json TEXT NOT NULL,
            created_unix INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_domain_type ON artifacts(domain_key,artifact_type,created_unix);

        CREATE TABLE IF NOT EXISTS ingestion_records(
            ingestion_id TEXT PRIMARY KEY,
            domain_key TEXT NOT NULL,
            source_id TEXT,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_files_json TEXT NOT NULL,
            raw_identity_json TEXT NOT NULL,
            quality_json TEXT NOT NULL,
            created_unix INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ingestion_domain_status ON ingestion_records(domain_key,status,created_unix);

        CREATE TABLE IF NOT EXISTS sports_markets(
            market_id TEXT PRIMARY KEY,
            domain_key TEXT NOT NULL,
            source_id TEXT NOT NULL,
            league TEXT,
            scheduled_text TEXT,
            scheduled_unix INTEGER,
            participant_a TEXT,
            participant_b TEXT,
            quote_count INTEGER NOT NULL,
            first_quote_unix INTEGER,
            last_quote_unix INTEGER,
            market_json TEXT NOT NULL,
            provenance_json TEXT NOT NULL,
            created_unix INTEGER NOT NULL,
            updated_unix INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sports_market_domain_time ON sports_markets(domain_key,scheduled_unix);
        """)
        c.commit()

    def upsert_experience(self, x: ExperienceRecord) -> bool:
        cur = self.conn.execute("""
        INSERT OR IGNORE INTO experiences(
            experience_id,domain_key,source_id,event_unix,period_key,features_json,target_json,
            target_complete,verified,provenance_json,quality_json,created_unix
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            x.experience_id, x.domain_key, x.source_id, int(x.event_unix), x.period_key,
            canonical_json(x.features), canonical_json(x.target), int(x.target_complete), int(x.verified),
            canonical_json(x.provenance), canonical_json(x.quality), now_unix(),
        ))
        self.conn.commit()
        return cur.rowcount > 0

    def load_verified_experiences(self, domain_key: str, complete_only: bool = True, route_key: Optional[str] = None) -> List[ExperienceRecord]:
        sql = "SELECT * FROM experiences WHERE domain_key=? AND verified=1"
        args: List[Any] = [domain_key]
        if complete_only:
            sql += " AND target_complete=1"
        sql += " ORDER BY event_unix, experience_id"
        rows = self.conn.execute(sql, args).fetchall()
        result = [ExperienceRecord(
            experience_id=r["experience_id"], domain_key=r["domain_key"], source_id=r["source_id"],
            event_unix=int(r["event_unix"]), period_key=r["period_key"],
            features=json.loads(r["features_json"]), target=json.loads(r["target_json"]),
            target_complete=bool(r["target_complete"]), verified=bool(r["verified"]),
            provenance=json.loads(r["provenance_json"]), quality=json.loads(r["quality_json"]),
        ) for r in rows]
        if route_key and not route_key.startswith("PRIMARY::"):
            result = [r for r in result if r.provenance.get("route_key") == route_key]
        return result

    def save_ingestion_record(self, domain_key: str, source_id: str, status: str, reason: str,
                              source_files: Sequence[str], raw_identity: Dict[str, Any], quality: Dict[str, Any]) -> str:
        iid = hash_id("INGESTION", domain_key, source_id, status, reason, raw_identity, quality)
        self.conn.execute("""
        INSERT OR REPLACE INTO ingestion_records(
            ingestion_id,domain_key,source_id,status,reason,source_files_json,raw_identity_json,quality_json,created_unix
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """, (iid, domain_key, source_id, status, reason, canonical_json(list(source_files)),
              canonical_json(raw_identity), canonical_json(quality), now_unix()))
        self.conn.commit()
        return iid

    def upsert_sports_market(self, *, market_id: str, domain_key: str, source_id: str, league: str,
                             scheduled_text: str, scheduled_unix: int, participant_a: str, participant_b: str,
                             quotes: Sequence[Dict[str, Any]], provenance: Dict[str, Any]) -> None:
        qtimes = [int(q["unix"]) for q in quotes if q.get("unix") is not None]
        self.conn.execute("""
        INSERT INTO sports_markets(
            market_id,domain_key,source_id,league,scheduled_text,scheduled_unix,participant_a,participant_b,
            quote_count,first_quote_unix,last_quote_unix,market_json,provenance_json,created_unix,updated_unix
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(market_id) DO UPDATE SET
            quote_count=excluded.quote_count, first_quote_unix=excluded.first_quote_unix,
            last_quote_unix=excluded.last_quote_unix, market_json=excluded.market_json,
            provenance_json=excluded.provenance_json, updated_unix=excluded.updated_unix
        """, (market_id,domain_key,source_id,league,scheduled_text,int(scheduled_unix),participant_a,participant_b,
              len(quotes), min(qtimes) if qtimes else None, max(qtimes) if qtimes else None,
              canonical_json(list(quotes)), canonical_json(provenance), now_unix(), now_unix()))
        self.conn.commit()

    def ingestion_summary(self) -> Dict[str, Any]:
        rows = self.conn.execute("""
            SELECT domain_key,status,COUNT(*) AS n FROM ingestion_records
            GROUP BY domain_key,status ORDER BY domain_key,status
        """).fetchall()
        out: Dict[str, Dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["domain_key"], {})[r["status"]] = int(r["n"])
        exp = self.conn.execute("""
            SELECT domain_key,COUNT(*) AS n FROM experiences GROUP BY domain_key ORDER BY domain_key
        """).fetchall()
        experiences = {r["domain_key"]: int(r["n"]) for r in exp}
        return {"quality": out, "experiences": experiences}

    def network_row(self, network_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM networks WHERE network_id=?", (network_id,)).fetchone()

    def ensure_network(self, network_id: str, domain_key: str) -> sqlite3.Row:
        self.conn.execute("""
        INSERT OR IGNORE INTO networks(network_id,domain_key,status,current_generation,last_train_samples,updated_unix)
        VALUES(?,?, 'NEW', 0, 0, ?)
        """, (network_id, domain_key, now_unix()))
        self.conn.commit()
        return self.network_row(network_id)

    def save_trial(self, network_id: str, generation: int, trial: TrialResult) -> None:
        self.conn.execute("""
        INSERT OR REPLACE INTO model_trials(
            trial_id,network_id,generation,model_kind,complexity_tier,architecture_json,
            train_metrics_json,observation_metrics_json,score,passed,model_path,created_unix
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trial.trial_id, network_id, generation, trial.model_kind, trial.complexity_tier,
            canonical_json(trial.architecture), canonical_json(trial.train_metrics),
            canonical_json(trial.observation_metrics), float(trial.score), int(trial.passed),
            trial.model_path, now_unix(),
        ))
        self.conn.commit()

    def commit_generation(self, artifact: GenerationArtifact) -> None:
        gid = hash_id("GENERATION", artifact.network_id, artifact.generation)
        self.conn.execute("""
        INSERT OR REPLACE INTO generations(generation_id,network_id,domain_key,generation,artifact_json,created_unix)
        VALUES(?,?,?,?,?,?)
        """, (gid, artifact.network_id, artifact.domain_key, artifact.generation, canonical_json(asdict(artifact)), artifact.created_unix))
        self.conn.execute("""
        UPDATE networks SET status='ACTIVE',current_generation=?,last_train_samples=?,last_train_unix=?,
          champion_trial_id=?,champion_model_kind=?,champion_score=?,updated_unix=? WHERE network_id=?
        """, (
            artifact.generation, artifact.training_count + artifact.observation_count, artifact.created_unix,
            artifact.champion_trial_id, artifact.champion_model_kind, artifact.champion_score,
            artifact.created_unix, artifact.network_id,
        ))
        self.conn.commit()

    def publish_artifact(self, artifact_type: str, domain_key: str, payload: Dict[str, Any], network_id: Optional[str] = None, generation: Optional[int] = None) -> str:
        aid = hash_id("ARTIFACT", artifact_type, domain_key, network_id, generation, payload, now_unix())
        self.conn.execute("""
        INSERT INTO artifacts(artifact_id,artifact_type,domain_key,network_id,generation,payload_json,created_unix)
        VALUES(?,?,?,?,?,?,?)
        """, (aid, artifact_type, domain_key, network_id, generation, canonical_json(payload), now_unix()))
        self.conn.commit()
        return aid

    def save_parent_snapshot(self, child_domain_key: str, parent_domain_key: str, snapshot: Dict[str, Any], source_unix: int, parent_generation_ref: Optional[str] = None) -> str:
        sid = hash_id("PARENT_SNAPSHOT", child_domain_key, parent_domain_key, source_unix, snapshot)
        self.conn.execute("""
        INSERT OR IGNORE INTO parent_snapshots(snapshot_id,child_domain_key,parent_domain_key,parent_generation_ref,snapshot_json,source_unix,created_unix)
        VALUES(?,?,?,?,?,?,?)
        """, (sid, child_domain_key, parent_domain_key, parent_generation_ref, canonical_json(snapshot), int(source_unix), now_unix()))
        self.conn.commit()
        return sid


# ============================================================
# GATES
# ============================================================

class ReadinessGate:
    @staticmethod
    def evaluate(spec: DomainSpec, rows: Sequence[ExperienceRecord]) -> ReadinessDecision:
        policy = spec.readiness
        verified = [r for r in rows if r.verified and r.target_complete]
        samples = len(verified)
        periods = len({r.period_key for r in verified})
        if samples:
            missing_values = 0
            total_values = 0
            for r in verified:
                for v in r.features.values():
                    total_values += 1
                    if v is None or (isinstance(v, float) and not math.isfinite(v)):
                        missing_values += 1
            missing_fraction = missing_values / max(1, total_values)
            target_signatures = {canonical_json(r.target) for r in verified}
            target_diversity = len(target_signatures)
        else:
            missing_fraction = 1.0
            target_diversity = 0

        reasons: List[str] = []
        if samples < policy.min_samples:
            reasons.append(f"SAMPLES {samples}/{policy.min_samples}")
        if periods < policy.min_periods:
            reasons.append(f"PERIODS {periods}/{policy.min_periods}")
        if target_diversity < policy.min_target_diversity:
            reasons.append(f"TARGET_DIVERSITY {target_diversity}/{policy.min_target_diversity}")
        if missing_fraction > policy.max_missing_fraction:
            reasons.append(f"MISSING_FRACTION {missing_fraction:.4f}>{policy.max_missing_fraction:.4f}")

        return ReadinessDecision(
            ready=not reasons,
            status="READY" if not reasons else "WAITING_DATA",
            reasons=reasons,
            sample_count=samples,
            period_count=periods,
            missing_fraction=missing_fraction,
            target_diversity=target_diversity,
        )


class RetrainingGate:
    @staticmethod
    def decide(current_samples: int, last_train_samples: int, policy: RetrainingPolicy) -> TrainingDecision:
        if current_samples <= 0:
            return TrainingDecision("WAIT", "NO_VERIFIED_EXPERIENCE", current_samples, last_train_samples, 0.0)
        if last_train_samples <= 0:
            return TrainingDecision("TRAIN", "FIRST_GENERATION", current_samples, last_train_samples, 1.0)
        new_samples = max(0, current_samples - last_train_samples)
        growth = new_samples / max(1, last_train_samples)
        required_abs = max(policy.min_absolute_new_samples, math.ceil(last_train_samples * policy.min_growth_fraction))
        if new_samples < required_abs:
            return TrainingDecision("WAIT", f"GROWTH_TOO_SMALL {new_samples}/{required_abs}", current_samples, last_train_samples, growth)
        return TrainingDecision("RETRAIN", f"GROWTH_READY {growth:.3f}", current_samples, last_train_samples, growth)


class ComplexityGate:
    def __init__(self, tiers: Sequence[ComplexityTier] = DEFAULT_COMPLEXITY_TIERS):
        self.tiers = sorted(tiers, key=lambda x: x.tier)

    def choose(self, sample_count: int, period_count: int) -> Optional[ComplexityTier]:
        allowed = [t for t in self.tiers if sample_count >= t.min_samples and period_count >= t.min_periods]
        return allowed[-1] if allowed else None


# ============================================================
# SPLIT / PARENT INHERITANCE
# ============================================================

def chronological_split(rows: Sequence[ExperienceRecord], policy: SplitPolicy = SplitPolicy()) -> Tuple[List[ExperienceRecord], List[ExperienceRecord]]:
    policy.validate()
    ordered = sorted(rows, key=lambda r: (r.event_unix, r.experience_id))
    if len(ordered) < 2:
        return list(ordered), []
    cut = int(math.floor(len(ordered) * policy.train_fraction))
    cut = min(max(1, cut), len(ordered) - 1)
    return ordered[:cut], ordered[cut:]


class ParentIntelligenceBridge:
    """Child domains inherit canonical parent artifacts/snapshots, never parent RAW."""

    def __init__(self, store: StateStore):
        self.store = store

    def inherit(self, child_domain_key: str, parent_domain_key: str, snapshot: Dict[str, Any], source_unix: int, parent_generation_ref: Optional[str] = None) -> str:
        if child_domain_key == parent_domain_key:
            raise ValueError("A domain cannot inherit itself")
        return self.store.save_parent_snapshot(child_domain_key, parent_domain_key, snapshot, source_unix, parent_generation_ref)


# ============================================================
# SPORTS DATA ADAPTERS / QUALITY / EXPERIENCE BUILDER
# ============================================================

SPORT_ALIASES = {
    "TENNIS": "SPORTS.TENNIS",
    "SOCCER": "SPORTS.SOCCER",
    "BASEBALL": "SPORTS.BASEBALL",
    "BASKETBALL": "SPORTS.BASKETBALL",
    "E SPORTS": "SPORTS.E_SPORTS",
    "E_SPORTS": "SPORTS.E_SPORTS",
    "ESPORTS": "SPORTS.E_SPORTS",
    "GOLF": "SPORTS.GOLF",
}

SPORT_DIRECTORY_NAMES = {
    "SPORTS.TENNIS": "Tennis",
    "SPORTS.SOCCER": "Soccer",
    "SPORTS.BASEBALL": "Baseball",
    "SPORTS.BASKETBALL": "Basketball",
    "SPORTS.E_SPORTS": "E_Sports",
    "SPORTS.GOLF": "Golf",
}

SPORTS_TIMEZONE = ZoneInfo("Europe/Warsaw")


def _sport_domain(value: str) -> Optional[str]:
    key = str(value or "").strip().replace("-", " ").replace("_", " ").upper()
    key = " ".join(key.split())
    return SPORT_ALIASES.get(key)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        v = float(str(value).strip().replace(",", "."))
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _scheduled_unix(text: str) -> int:
    for fmt in ("%Y-%m-%d,%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text.strip(), fmt).replace(tzinfo=SPORTS_TIMEZONE)
            return int(dt.timestamp())
        except Exception:
            pass
    return 0


def _split_participants(text: str) -> Tuple[str, str]:
    # Dataset convention uses "A - B". Split only once to preserve names containing hyphens.
    if " - " in text:
        a, b = text.split(" - ", 1)
        return a.strip(), b.strip()
    return text.strip(), ""


def _movement_features(quotes: Sequence[Dict[str, Any]]) -> Dict[str, float]:
    ordered = sorted([q for q in quotes if q.get("unix") is not None], key=lambda q: int(q["unix"]))
    out: Dict[str, float] = {"quote_count": float(len(ordered))}
    if not ordered:
        return out
    t0, t1 = int(ordered[0]["unix"]), int(ordered[-1]["unix"])
    duration = max(0, t1 - t0)
    out["quote_duration_seconds"] = float(duration)
    for side in ("a", "b"):
        vals = [float(q[f"odds_{side}"]) for q in ordered if q.get(f"odds_{side}") not in (None, 0)]
        if not vals:
            continue
        opening, closing = vals[0], vals[-1]
        diffs = [vals[i] - vals[i-1] for i in range(1, len(vals))]
        reversals = 0
        prev_sign = 0
        for d in diffs:
            sign = 1 if d > 0 else (-1 if d < 0 else 0)
            if sign and prev_sign and sign != prev_sign:
                reversals += 1
            if sign:
                prev_sign = sign
        implied = [1.0 / v for v in vals if v > 0]
        out.update({
            f"odds_{side}_opening": opening,
            f"odds_{side}_closing": closing,
            f"odds_{side}_min": min(vals),
            f"odds_{side}_max": max(vals),
            f"odds_{side}_amplitude": max(vals) - min(vals),
            f"odds_{side}_relative_amplitude": (max(vals)-min(vals))/max(1e-12, abs(opening)),
            f"odds_{side}_delta": closing-opening,
            f"odds_{side}_delta_pct": (closing-opening)/max(1e-12, abs(opening)),
            f"odds_{side}_mean": statistics.fmean(vals),
            f"odds_{side}_stdev": statistics.pstdev(vals) if len(vals) > 1 else 0.0,
            f"odds_{side}_max_jump": max([abs(x) for x in diffs], default=0.0),
            f"odds_{side}_reversals": float(reversals),
            f"odds_{side}_velocity_per_hour": ((closing-opening)/(duration/3600.0)) if duration > 0 else 0.0,
            f"prob_{side}_opening": implied[0] if implied else 0.0,
            f"prob_{side}_closing": implied[-1] if implied else 0.0,
            f"prob_{side}_delta": (implied[-1]-implied[0]) if implied else 0.0,
        })
    # Normalize the 2-way market to reduce bookmaker margin; useful even when real outcome can be draw.
    if out.get("prob_a_closing", 0.0) > 0 and out.get("prob_b_closing", 0.0) > 0:
        den = out["prob_a_closing"] + out["prob_b_closing"]
        out["market_strength_a_closing"] = out["prob_a_closing"] / den
        out["market_strength_b_closing"] = out["prob_b_closing"] / den
    if out.get("prob_a_opening", 0.0) > 0 and out.get("prob_b_opening", 0.0) > 0:
        den = out["prob_a_opening"] + out["prob_b_opening"]
        out["market_strength_a_opening"] = out["prob_a_opening"] / den
        out["market_strength_b_opening"] = out["prob_b_opening"] / den
    return out


@dataclass
class SportsMarketRecord:
    domain_key: str
    league: str
    scheduled_text: str
    scheduled_unix: int
    participants_text: str
    participant_a: str
    participant_b: str
    source_id: str
    quotes: List[Dict[str, Any]]
    source_file: str


@dataclass
class SportsResultRecord:
    domain_key: str
    league: str
    scheduled_text: str
    scheduled_unix: int
    participants_text: str
    participant_a: str
    participant_b: str
    score_a: Optional[int]
    score_b: Optional[int]
    result_code: str
    source_id: str
    source_file: str


class SportsCsvAdapter:
    def read_markets(self, path: Path) -> List[SportsMarketRecord]:
        out: List[SportsMarketRecord] = []
        with Path(path).open("r", encoding=CSV_ENCODING, newline="") as fh:
            for row_no, row in enumerate(csv.reader(fh, delimiter=CSV_SEPARATOR), start=1):
                if not row or len(row) < 8:
                    continue
                domain = _sport_domain(row[0])
                if not domain:
                    continue
                league, scheduled, participants, source_id = row[1].strip(), row[2].strip(), row[3].strip(), row[4].strip()
                a, b = _split_participants(participants)
                quotes: List[Dict[str, Any]] = []
                tail = row[5:]
                for i in range(0, len(tail)-2, 3):
                    oa, ob, ts = _float_or_none(tail[i]), _float_or_none(tail[i+1]), _int_or_none(tail[i+2])
                    if oa is None or ob is None or ts is None or oa <= 0 or ob <= 0:
                        continue
                    quotes.append({"odds_a": oa, "odds_b": ob, "unix": ts})
                # Deduplicate identical quote timestamps/values and keep chronology.
                uq = {(q["unix"], q["odds_a"], q["odds_b"]): q for q in quotes}
                quotes = sorted(uq.values(), key=lambda q: q["unix"])
                out.append(SportsMarketRecord(domain, league, scheduled, _scheduled_unix(scheduled), participants, a, b,
                                              source_id, quotes, f"{path}:{row_no}"))
        return out

    def read_results(self, path: Path) -> List[SportsResultRecord]:
        out: List[SportsResultRecord] = []
        with Path(path).open("r", encoding=CSV_ENCODING, newline="") as fh:
            for row_no, row in enumerate(csv.reader(fh, delimiter=CSV_SEPARATOR), start=1):
                if not row:
                    continue
                # Canonical rows: sport;league;scheduled;participants;scoreA;scoreB;result;event_id
                if len(row) >= 8 and _sport_domain(row[0]):
                    domain = _sport_domain(row[0])
                    league, scheduled, participants = row[1].strip(), row[2].strip(), row[3].strip()
                    score_a, score_b = _int_or_none(row[4]), _int_or_none(row[5])
                    result_code, source_id = row[6].strip(), row[7].strip()
                else:
                    # Legacy duplicate without sport prefix: cannot safely infer domain from league alone.
                    continue
                a, b = _split_participants(participants)
                out.append(SportsResultRecord(domain, league, scheduled, _scheduled_unix(scheduled), participants, a, b,
                                              score_a, score_b, result_code, source_id, f"{path}:{row_no}"))
        return out


class SportsExperienceBuilder:
    def __init__(self, store: StateStore):
        self.store = store

    @staticmethod
    def _result_signature(r: SportsResultRecord) -> Tuple[Any, ...]:
        return (r.score_a, r.score_b, r.result_code)

    @staticmethod
    def _target_for(domain_key: str, result: SportsResultRecord, market: SportsMarketRecord) -> Tuple[Dict[str, Any], bool]:
        if domain_key == "SPORTS.TENNIS":
            winner = result.result_code if result.result_code in {"1", "2"} else None
            return ({"winner_side": winner, "score_a": result.score_a, "score_b": result.score_b}, winner is not None)
        if domain_key == "SPORTS.SOCCER":
            complete = result.score_a is not None and result.score_b is not None
            return ({"home_goals": result.score_a, "away_goals": result.score_b, "result_1x2": result.result_code}, complete)
        if domain_key == "SPORTS.BASEBALL":
            complete = result.score_a is not None and result.score_b is not None
            return ({"home_runs": result.score_a, "away_runs": result.score_b, "winner_side": result.result_code}, complete)
        if domain_key == "SPORTS.BASKETBALL":
            complete = result.score_a is not None and result.score_b is not None
            return ({"home_points": result.score_a, "away_points": result.score_b, "winner_side": result.result_code}, complete)
        if domain_key == "SPORTS.E_SPORTS":
            complete = result.score_a is not None and result.score_b is not None
            return ({"side_a_score": result.score_a, "side_b_score": result.score_b, "winner_side": result.result_code}, complete)
        if domain_key == "SPORTS.GOLF":
            # Actual Golf result contract will be confirmed when first result files arrive.
            return ({}, False)
        return ({}, False)

    def ingest_pair(self, market_file: Path, result_file: Optional[Path]) -> Dict[str, Any]:
        adapter = SportsCsvAdapter()
        markets = adapter.read_markets(market_file)
        results = adapter.read_results(result_file) if result_file and Path(result_file).exists() else []
        result_map: Dict[Tuple[str, str], List[SportsResultRecord]] = {}
        for r in results:
            result_map.setdefault((r.domain_key, r.source_id), []).append(r)

        counts = {"markets": 0, "verified": 0, "partial": 0, "conflict": 0, "waiting_result": 0, "invalid": 0,
                  "experiences_inserted": 0}
        for m in markets:
            counts["markets"] += 1
            market_id = hash_id("SPORTS_MARKET", m.domain_key, m.source_id)
            features = _movement_features(m.quotes)
            # Time to scheduled event is informative but can be negative for stale snapshots; preserve raw sign.
            if m.quotes and m.scheduled_unix:
                features["last_quote_to_event_seconds"] = float(m.scheduled_unix - int(m.quotes[-1]["unix"]))
                features["first_quote_to_event_seconds"] = float(m.scheduled_unix - int(m.quotes[0]["unix"]))
            provenance = {"market_source": m.source_file, "result_source": str(result_file) if result_file else None,
                          "csv_separator": CSV_SEPARATOR, "csv_encoding": CSV_ENCODING}
            self.store.upsert_sports_market(market_id=market_id, domain_key=m.domain_key, source_id=m.source_id,
                league=m.league, scheduled_text=m.scheduled_text, scheduled_unix=m.scheduled_unix,
                participant_a=m.participant_a, participant_b=m.participant_b, quotes=m.quotes, provenance=provenance)

            identity = {"event_id": m.source_id, "league": m.league, "scheduled": m.scheduled_text,
                        "participant_a": m.participant_a, "participant_b": m.participant_b}
            if not m.source_id or not m.quotes:
                counts["invalid"] += 1
                self.store.save_ingestion_record(m.domain_key, m.source_id, "INVALID", "MISSING_EVENT_ID_OR_QUOTES",
                                                  [m.source_file], identity, {"quote_count": len(m.quotes)})
                continue

            versions = result_map.get((m.domain_key, m.source_id), [])
            if not versions:
                counts["waiting_result"] += 1
                self.store.save_ingestion_record(m.domain_key, m.source_id, "WAITING_RESULT", "NO_MATCHING_RESULT_YET",
                                                  [m.source_file], identity, {"quote_count": len(m.quotes)})
                continue
            signatures = {self._result_signature(r) for r in versions}
            if len(signatures) > 1:
                counts["conflict"] += 1
                self.store.save_ingestion_record(m.domain_key, m.source_id, "CONFLICT", "RESULT_VERSIONS_DISAGREE",
                    [m.source_file] + [r.source_file for r in versions], identity,
                    {"signatures": [list(x) for x in sorted(signatures, key=str)], "quote_count": len(m.quotes)})
                continue

            result = versions[0]
            target, complete = self._target_for(m.domain_key, result, m)
            if not target:
                counts["partial"] += 1
                self.store.save_ingestion_record(m.domain_key, m.source_id, "PARTIAL", "TARGET_CONTRACT_NOT_COMPLETE",
                    [m.source_file, result.source_file], identity, {"quote_count": len(m.quotes)})
                continue
            status = "VERIFIED" if complete else "PARTIAL"
            counts["verified" if complete else "partial"] += 1
            quality = {
                "status": status,
                "quote_count": len(m.quotes),
                "target_complete": complete,
                "result_versions": len(versions),
                "identity_match": True,
            }
            self.store.save_ingestion_record(m.domain_key, m.source_id, status, "MATCHED_MARKET_AND_RESULT",
                [m.source_file, result.source_file], identity, quality)
            x = ExperienceRecord(
                experience_id=hash_id("SPORTS_EXPERIENCE", m.domain_key, m.source_id, target),
                domain_key=m.domain_key,
                source_id=m.source_id,
                event_unix=int(result.scheduled_unix or m.scheduled_unix or (m.quotes[-1]["unix"] if m.quotes else 0)),
                period_key=(result.scheduled_text or m.scheduled_text).split(",", 1)[0],
                features=features,
                target=target,
                target_complete=bool(complete),
                verified=True,
                provenance={**provenance, "result_record": result.source_file, "league": m.league,
                            "participants": [m.participant_a, m.participant_b]},
                quality=quality,
            )
            if self.store.upsert_experience(x):
                counts["experiences_inserted"] += 1
        return counts


class SportsWorldIngestor:
    """Scans ./dane/sport_2way and builds canonical Sports Experience without training models."""
    def __init__(self, store: StateStore, sports_root: Path):
        self.store = store
        self.sports_root = Path(sports_root)
        self.builder = SportsExperienceBuilder(store)

    def discover_pairs(self) -> List[Tuple[Path, Optional[Path]]]:
        pairs: List[Tuple[Path, Optional[Path]]] = []
        odds_root = self.sports_root / "kursy"
        results_root = self.sports_root / "wyniki"
        if not odds_root.exists():
            return pairs
        for domain_key, dirname in SPORT_DIRECTORY_NAMES.items():
            odir = odds_root / dirname
            if not odir.exists():
                continue
            rdir = results_root / dirname
            for ofile in sorted(odir.glob("kursy_*.csv")):
                date_part = ofile.stem.replace("kursy_", "", 1)
                rfile = rdir / f"wyniki_{date_part}.csv"
                pairs.append((ofile, rfile if rfile.exists() else None))
        return pairs

    def run(self) -> Dict[str, Any]:
        aggregate: Dict[str, int] = {"files": 0, "markets": 0, "verified": 0, "partial": 0, "conflict": 0,
                                    "waiting_result": 0, "invalid": 0, "experiences_inserted": 0}
        details: List[Dict[str, Any]] = []
        for market_file, result_file in self.discover_pairs():
            stats = self.builder.ingest_pair(market_file, result_file)
            aggregate["files"] += 1
            for k, v in stats.items():
                aggregate[k] = aggregate.get(k, 0) + int(v)
            details.append({"market_file": str(market_file), "result_file": str(result_file) if result_file else None, **stats})
        return {"aggregate": aggregate, "details": details, "database": self.store.ingestion_summary()}


# ============================================================
# MODEL LAB CONTRACT
# ============================================================

class ModelLab:
    """
    Domain-independent contract. Concrete ML backend implements candidate trials.
    It must train only on TRAIN and score primarily on OBSERVATION.
    """

    def run_trials(
        self,
        spec: DomainSpec,
        network_id: str,
        generation: int,
        tier: ComplexityTier,
        train_rows: Sequence[ExperienceRecord],
        observation_rows: Sequence[ExperienceRecord],
        parent_context: Optional[Dict[str, Any]] = None,
    ) -> List[TrialResult]:
        raise NotImplementedError


class DryRunModelLab(ModelLab):
    """Safe local lab used until domain ML adapters are connected."""

    def run_trials(self, spec, network_id, generation, tier, train_rows, observation_rows, parent_context=None):
        results: List[TrialResult] = []
        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for arch in tier.mlp_architectures:
            candidates.append(("MLP", {"layers": list(arch)}))
        for w in tier.sequence_windows:
            candidates.append(("TIME_SERIES_MLP", {"window": w, "layers": list(tier.mlp_architectures[-1])}))
        for kind in tier.advanced_sequence_models:
            candidates.append((kind, {"window": max(tier.sequence_windows or (12,))}))
        for idx, (kind, arch) in enumerate(candidates):
            tid = hash_id("TRIAL", network_id, generation, kind, arch)
            # No fake accuracy. Dry-run records eligibility only and never passes a model.
            results.append(TrialResult(
                trial_id=tid,
                model_kind=kind,
                architecture=arch,
                complexity_tier=tier.tier,
                train_metrics={"eligible": True, "samples": len(train_rows)},
                observation_metrics={"samples": len(observation_rows), "status": "NOT_EVALUATED"},
                score=float("-inf"),
                passed=False,
            ))
        return results


# ============================================================
# CANONICAL NETWORK FACTORY
# ============================================================

class CanonicalNetworkFactory:
    def __init__(
        self,
        root: Path,
        store: StateStore,
        model_lab: Optional[ModelLab] = None,
        split_policy: SplitPolicy = SplitPolicy(),
        retraining_policy: RetrainingPolicy = RetrainingPolicy(),
        complexity_gate: Optional[ComplexityGate] = None,
        compute_backend: Optional[ComputeBackend] = None,
    ):
        self.root = Path(root)
        self.store = store
        self.model_lab = model_lab or DryRunModelLab()
        self.split_policy = split_policy
        self.retraining_policy = retraining_policy
        self.complexity_gate = complexity_gate or ComplexityGate()
        self.compute_backend = compute_backend

    def network_id_for(self, domain_key: str, route_key: str) -> str:
        return hash_id("NETWORK", domain_key, route_key)

    @staticmethod
    def _resource_request_for_trial(trial: TrialResult) -> ResourceRequest:
        kind = trial.model_kind.upper()
        if kind in {"GRU", "LSTM", "TRANSFORMER", "GRU_REGRESSION", "LSTM_REGRESSION", "TRANSFORMER_REGRESSION"}:
            return ResourceRequest(cpu_cores=2, ram_mb=4096, gpu_mode="AUTO", vram_mb=2800, exclusive_gpu=True)
        if kind in {"TIME_SERIES_MLP", "MLP", "REGRESSION_MLP", "CLASSIFICATION_MLP"}:
            return ResourceRequest(cpu_cores=2, ram_mb=2048, gpu_mode="AUTO", vram_mb=1200, exclusive_gpu=False)
        return ResourceRequest(cpu_cores=2, ram_mb=2048, gpu_mode="CPU", vram_mb=0, exclusive_gpu=False)

    def _enqueue_candidate_jobs(self, *, spec: DomainSpec, domain_key: str, route_key: str, network_id: str, generation: int,
                                trials: Sequence[TrialResult], train_rows: Sequence[ExperienceRecord],
                                observation_rows: Sequence[ExperienceRecord], training: TrainingDecision,
                                parent_context: Optional[Dict[str, Any]]) -> List[str]:
        if self.compute_backend is None:
            return []
        priority = "P4_REQUIRED_RETRAIN" if training.action == "RETRAIN" else "P6_CHALLENGER_TRAINING"
        job_ids: List[str] = []
        for trial in trials:
            job = ComputeJob.create(
                job_type="MODEL_TRAIN_AND_OBSERVE",
                priority_class=priority,
                world_key=spec.world,
                discipline=spec.discipline,
                owner_type="WORLD",
                owner_id=domain_key,
                domain_key=domain_key,
                route_key=route_key,
                network_id=network_id,
                generation=generation,
                payload={
                    "namespace": canonical_namespace(domain_key, entity_id=route_key, route_key=route_key),
                    "trial_id": trial.trial_id,
                    "model_kind": trial.model_kind,
                    "target_spec": asdict(spec.target),
                    "architecture": trial.architecture,
                    "complexity_tier": trial.complexity_tier,
                    "train_experience_ids": [x.experience_id for x in train_rows],
                    "observation_experience_ids": [x.experience_id for x in observation_rows],
                    "train_records": [asdict(x) for x in train_rows],
                    "observation_records": [asdict(x) for x in observation_rows],
                    "parent_context": parent_context or {},
                    "governance_snapshot": asdict(spec.governance),
                    "rules": {
                        "observation_is_read_only": True,
                        "no_observation_training_leakage": True,
                        "publish_executable_artifact_bundle": True,
                    },
                },
                resources=self._resource_request_for_trial(trial),
                artifact_outputs=["NETWORK_ARTIFACT", "BEHAVIOR_ARTIFACT", "PREDICTOR_ARTIFACT", "PREDICTION_SUMMARY_ARTIFACT"],
                idempotency_key=hash_id("REMOTE_JOB", spec.world, domain_key, route_key, network_id, generation, trial.trial_id),
            )
            job_ids.append(self.compute_backend.submit(job))
        return job_ids

    def evaluate_route(self, domain_key: str, route_key: str, parent_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if domain_key not in DOMAIN_REGISTRY:
            raise KeyError(f"Unknown domain: {domain_key}")
        spec = DOMAIN_REGISTRY[domain_key]
        network_id = self.network_id_for(domain_key, route_key)
        nrow = self.store.ensure_network(network_id, domain_key)
        rows = self.store.load_verified_experiences(domain_key, complete_only=True, route_key=route_key)

        ready = ReadinessGate.evaluate(spec, rows)
        if not ready.ready:
            self.store.conn.execute("UPDATE networks SET status='WAITING_DATA',updated_unix=? WHERE network_id=?", (now_unix(), network_id))
            self.store.conn.commit()
            return {"network_id": network_id, "status": "WAITING_DATA", "readiness": asdict(ready)}

        last_train_samples = int(nrow["last_train_samples"] or 0)
        training = RetrainingGate.decide(ready.sample_count, last_train_samples, self.retraining_policy)
        if training.action == "WAIT":
            self.store.conn.execute("UPDATE networks SET status='WAITING_GROWTH',updated_unix=? WHERE network_id=?", (now_unix(), network_id))
            self.store.conn.commit()
            return {"network_id": network_id, "status": "WAITING_GROWTH", "readiness": asdict(ready), "training": asdict(training)}

        tier = self.complexity_gate.choose(ready.sample_count, ready.period_count)
        if tier is None:
            return {"network_id": network_id, "status": "WAITING_COMPLEXITY", "readiness": asdict(ready), "training": asdict(training)}

        train_rows, observation_rows = chronological_split(rows, self.split_policy)
        if not observation_rows:
            return {"network_id": network_id, "status": "WAITING_OBSERVATION", "readiness": asdict(ready)}

        generation = int(nrow["current_generation"] or 0) + 1
        trials = self.model_lab.run_trials(spec, network_id, generation, tier, train_rows, observation_rows, parent_context)
        for t in trials:
            self.store.save_trial(network_id, generation, t)

        # Dry-run jest ostatnia bramka tej wersji: przygotowujemy prawdziwy plan
        # kandydatow, ale NIE udajemy metryk ani championa.
        if isinstance(self.model_lab, DryRunModelLab):
            queued_job_ids = self._enqueue_candidate_jobs(
                spec=spec, domain_key=domain_key, route_key=route_key, network_id=network_id, generation=generation,
                trials=trials, train_rows=train_rows, observation_rows=observation_rows,
                training=training, parent_context=parent_context,
            )
            candidate_payload = {
                "network_id": network_id,
                "domain_key": domain_key,
                "route_key": route_key,
                "generation_candidate": generation,
                "target": asdict(spec.target),
                "readiness": asdict(ready),
                "training_decision": asdict(training),
                "split": {
                    "train_fraction": self.split_policy.train_fraction,
                    "observation_fraction": self.split_policy.observation_fraction,
                    "train_count": len(train_rows),
                    "observation_count": len(observation_rows),
                    "train_from_unix": train_rows[0].event_unix,
                    "train_to_unix": train_rows[-1].event_unix,
                    "observation_from_unix": observation_rows[0].event_unix,
                    "observation_to_unix": observation_rows[-1].event_unix,
                },
                "complexity_tier": asdict(tier),
                "candidates": [
                    {
                        "trial_id": t.trial_id,
                        "model_kind": t.model_kind,
                        "architecture": t.architecture,
                        "complexity_tier": t.complexity_tier,
                    }
                    for t in trials
                ],
                "parent_context": parent_context or {},
                "status": "MODEL_CANDIDATES_READY",
                "heavy_training_started": False,
                "compute_queue": {
                    "enabled": self.compute_backend is not None,
                    "queued_job_ids": queued_job_ids,
                    "job_count": len(queued_job_ids),
                },
            }
            self.store.publish_artifact(
                "MODEL_CANDIDATE_PLAN", domain_key, candidate_payload, network_id, generation
            )
            self.store.conn.execute(
                "UPDATE networks SET status='MODEL_CANDIDATES_READY',updated_unix=? WHERE network_id=?",
                (now_unix(), network_id),
            )
            self.store.conn.commit()
            return candidate_payload

        passing = [t for t in trials if t.passed and math.isfinite(t.score)]
        if not passing:
            self.store.conn.execute("UPDATE networks SET status='NO_CHAMPION',updated_unix=? WHERE network_id=?", (now_unix(), network_id))
            self.store.conn.commit()
            return {
                "network_id": network_id,
                "status": "NO_CHAMPION",
                "generation_candidate": generation,
                "complexity_tier": tier.tier,
                "trial_count": len(trials),
            }

        champion = max(passing, key=lambda x: x.score)
        artifact = GenerationArtifact(
            network_id=network_id,
            domain_key=domain_key,
            generation=generation,
            target_spec=asdict(spec.target),
            training_count=len(train_rows),
            observation_count=len(observation_rows),
            training_from_unix=train_rows[0].event_unix,
            training_to_unix=train_rows[-1].event_unix,
            observation_from_unix=observation_rows[0].event_unix,
            observation_to_unix=observation_rows[-1].event_unix,
            complexity_tier=tier.tier,
            champion_trial_id=champion.trial_id,
            champion_model_kind=champion.model_kind,
            champion_score=champion.score,
            trials=[asdict(t) for t in trials],
            parent_lineage=[parent_context] if parent_context else [],
            provenance={
                "schema_version": SCHEMA_VERSION,
                "split_policy": asdict(self.split_policy),
                "retraining_policy": asdict(self.retraining_policy),
                "readiness": asdict(ready),
                "training_decision": asdict(training),
            },
            created_unix=now_unix(),
        )
        self.store.commit_generation(artifact)
        self._publish_generation_artifacts(spec, artifact, champion)
        self._write_artifact_bundle(spec, route_key, artifact)
        return {"network_id": network_id, "status": "ACTIVE", "generation": generation, "champion": champion.trial_id}

    def _publish_generation_artifacts(self, spec: DomainSpec, artifact: GenerationArtifact, champion: TrialResult) -> None:
        network_payload = {
            "identity": {"network_id": artifact.network_id, "world": spec.world, "discipline": spec.discipline, "generation": artifact.generation},
            "purpose": asdict(spec.target),
            "data": {
                "training_count": artifact.training_count,
                "observation_count": artifact.observation_count,
                "training_range": [artifact.training_from_unix, artifact.training_to_unix],
                "observation_range": [artifact.observation_from_unix, artifact.observation_to_unix],
            },
            "model": {"kind": champion.model_kind, "trial_id": champion.trial_id, "model_path": champion.model_path},
            "behavior": champion.observation_metrics,
            "prediction_interface": {"prediction_type": spec.target.prediction_type, "outputs": list(spec.target.outputs)},
            "governance": asdict(spec.governance),
            "provenance": artifact.provenance,
        }
        self.store.publish_artifact("NETWORK_ARTIFACT", artifact.domain_key, network_payload, artifact.network_id, artifact.generation)
        self.store.publish_artifact("BEHAVIOR_ARTIFACT", artifact.domain_key, {
            "network_id": artifact.network_id,
            "generation": artifact.generation,
            "observation": champion.observation_metrics,
            "trial_comparison": [{"trial_id": t["trial_id"], "kind": t["model_kind"], "score": t["score"], "passed": t["passed"]} for t in artifact.trials],
        }, artifact.network_id, artifact.generation)

    def _write_artifact_bundle(self, spec: DomainSpec, route_key: str, artifact: GenerationArtifact) -> None:
        base = self.root / "worlds" / f"WORLD__{spec.world}" / f"DOMAIN__{spec.discipline}" / "network_artifacts" / artifact.network_id / f"generation_{artifact.generation:04d}"
        atomic_json_write(base / "generation.json", asdict(artifact))
        atomic_json_write(base / "constitution.json", spec.governance.constitution)
        atomic_json_write(base / "policies.json", spec.governance.policies)
        atomic_json_write(base / "rules.json", spec.governance.rules)
        atomic_json_write(base / "target_spec.json", asdict(spec.target))
        atomic_json_write(base / "route.json", {"route_key": route_key, "domain_key": artifact.domain_key})



# ============================================================
# MARKET / CAPITAL DATA ADAPTERS
# ============================================================

MARKET_SCALES = {
    "HOUR": 3600,
    "DAY": 86400,
    "WEEK": 7 * 86400,
    "MONTH": 30 * 86400,
}

STABLE_REFERENCE_ASSETS = {
    "USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "AEUR"
}


def _period_start(ts: int, scale: str) -> int:
    ts = int(ts)
    if scale == "HOUR":
        return ts - ts % 3600
    dt = datetime.fromtimestamp(ts, tz=ZoneInfo("UTC"))
    if scale == "DAY":
        return int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if scale == "WEEK":
        d = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return int((d.timestamp() - d.weekday() * 86400))
    if scale == "MONTH":
        return int(dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    return ts


def _extract_value_time_pairs(row: Sequence[str], start: int = 3) -> List[Tuple[int, float]]:
    out: List[Tuple[int, float]] = []
    i = start
    while i + 1 < len(row):
        value = _float_or_none(row[i])
        ts = _int_or_none(row[i + 1])
        if value is not None and value > 0 and ts is not None and ts > 100_000_000:
            out.append((int(ts), float(value)))
        i += 2
    return out


def _find_market_files(current_dir: Path, archive_root: Path, domain_folder: str) -> List[Path]:
    files: List[Path] = []
    if current_dir.is_dir():
        files.extend(sorted(current_dir.glob("*.csv")))
    if archive_root.is_dir():
        for path in archive_root.rglob("*.csv"):
            if domain_folder.upper() in {part.upper() for part in path.parts}:
                files.append(path)
    return sorted(set(files))


def _load_simple_market_series(files: Sequence[Path], allowed_kind: str, *, exclude_symbols: Optional[set] = None) -> Tuple[Dict[str, Dict[int, float]], Dict[str, set], int]:
    series: Dict[str, Dict[int, float]] = {}
    sources: Dict[str, set] = {}
    rows_seen = 0
    for path in files:
        try:
            with path.open("r", encoding=CSV_ENCODING, newline="") as f:
                for row in csv.reader(f, delimiter=CSV_SEPARATOR):
                    if len(row) < 5:
                        continue
                    kind = str(row[0]).strip().upper()
                    if kind != allowed_kind.upper():
                        continue
                    symbol = str(row[2]).strip().upper()
                    if not symbol or (exclude_symbols and symbol in exclude_symbols):
                        continue
                    pairs = _extract_value_time_pairs(row, 3)
                    if not pairs:
                        continue
                    dst = series.setdefault(symbol, {})
                    sources.setdefault(symbol, set()).add(str(path))
                    for ts, value in pairs:
                        dst[ts] = value
                    rows_seen += 1
        except Exception:
            continue
    return series, sources, rows_seen


def _load_stock_series(data_root: Path) -> Tuple[Dict[str, Dict[int, float]], Dict[str, set], int]:
    base = data_root / "stock_world" / "YAHOO"
    series: Dict[str, Dict[int, float]] = {}
    sources: Dict[str, set] = {}
    rows_seen = 0
    if not base.is_dir():
        return series, sources, rows_seen
    for path in base.rglob("*.csv"):
        try:
            with path.open("r", encoding=CSV_ENCODING, newline="") as f:
                for row in csv.reader(f, delimiter=CSV_SEPARATOR):
                    if len(row) < 5:
                        continue
                    kind = str(row[0]).strip().upper()
                    if "AKCJ" not in kind and kind not in {"STOCK", "EQUITY"}:
                        continue
                    symbol = str(row[2]).strip().upper()
                    pairs = _extract_value_time_pairs(row, 3)
                    if not symbol or not pairs:
                        continue
                    dst = series.setdefault(symbol, {})
                    sources.setdefault(symbol, set()).add(str(path))
                    for ts, value in pairs:
                        dst[ts] = value
                    rows_seen += 1
        except Exception:
            continue
    return series, sources, rows_seen


def _load_bond_series(data_root: Path) -> Tuple[Dict[str, Dict[int, float]], Dict[str, set], int]:
    # bond_world_v1 has priority only for the same timestamp; original evidence remains in provenance.
    series: Dict[str, Dict[int, Tuple[int, float]]] = {}
    sources: Dict[str, set] = {}
    rows_seen = 0
    for priority, base_name in ((1, "bond_world"), (2, "bond_world_v1")):
        base = data_root / base_name
        if not base.is_dir():
            continue
        for path in base.rglob("*.csv"):
            try:
                with path.open("r", encoding=CSV_ENCODING, newline="") as f:
                    for row in csv.reader(f, delimiter=CSV_SEPARATOR):
                        if len(row) < 5:
                            continue
                        kind = str(row[0]).strip().upper()
                        if "OBLIG" not in kind and kind != "BOND":
                            continue
                        symbol = str(row[2]).strip().upper()
                        pairs = _extract_value_time_pairs(row, 3)
                        if not symbol or not pairs:
                            continue
                        dst = series.setdefault(symbol, {})
                        sources.setdefault(symbol, set()).add(str(path))
                        for ts, value in pairs:
                            prev = dst.get(ts)
                            if prev is None or priority >= prev[0]:
                                dst[ts] = (priority, value)
                        rows_seen += 1
            except Exception:
                continue
    flat = {symbol: {ts: pv[1] for ts, pv in pts.items()} for symbol, pts in series.items()}
    return flat, sources, rows_seen


def _aggregate_periods(points: Dict[int, float], scale: str) -> List[Dict[str, Any]]:
    grouped: Dict[int, List[Tuple[int, float]]] = {}
    for ts, value in sorted(points.items()):
        grouped.setdefault(_period_start(ts, scale), []).append((int(ts), float(value)))
    out: List[Dict[str, Any]] = []
    for start, vals in sorted(grouped.items()):
        vals.sort()
        vv = [v for _, v in vals]
        open_v, close_v = vv[0], vv[-1]
        min_v, max_v = min(vv), max(vv)
        ret = (close_v / open_v - 1.0) * 100.0 if open_v else 0.0
        out.append({
            "start": int(start), "last_unix": int(vals[-1][0]), "open": open_v, "close": close_v,
            "min": min_v, "max": max_v, "return_pct": ret,
            "amplitude_pct": ((max_v / min_v - 1.0) * 100.0) if min_v else 0.0,
            "observations": len(vals),
        })
    return out


def _market_features(periods: Sequence[Dict[str, Any]], i: int) -> Dict[str, float]:
    cur = periods[i]
    prev = periods[i - 1] if i > 0 else cur
    return {
        "open": float(cur["open"]), "close": float(cur["close"]),
        "min": float(cur["min"]), "max": float(cur["max"]),
        "return_pct": float(cur["return_pct"]), "amplitude_pct": float(cur["amplitude_pct"]),
        "observations": float(cur["observations"]),
        "previous_return_pct": float(prev["return_pct"]),
        "return_acceleration": float(cur["return_pct"] - prev["return_pct"]),
        "close_vs_previous_close_pct": ((float(cur["close"]) / float(prev["close"]) - 1.0) * 100.0) if prev["close"] else 0.0,
    }


class MarketCapitalWorldIngestor:
    """Canonical Level-1 adapter for Currency, Crypto, Stock and Bond raw evidence."""

    def __init__(self, store: StateStore, data_root: Path):
        self.store = store
        self.data_root = Path(data_root)

    def _domain_series(self, domain_key: str):
        market_root = self.data_root / "market_world"
        if domain_key == "MARKETS.CURRENCY":
            files = _find_market_files(market_root / "biezace" / "WALUTA", market_root / "archiwum", "WALUTA")
            return (*_load_simple_market_series(files, "WALUTA"), {"files": len(files)})
        if domain_key == "MARKETS.CRYPTO":
            files = _find_market_files(market_root / "biezace" / "CRYPTO", market_root / "archiwum", "CRYPTO")
            loaded = _load_simple_market_series(files, "CRYPTO", exclude_symbols=STABLE_REFERENCE_ASSETS)
            return (*loaded, {"files": len(files)})
        if domain_key == "CAPITAL.STOCK":
            loaded = _load_stock_series(self.data_root)
            return (*loaded, {"root": str(self.data_root / "stock_world" / "YAHOO")})
        if domain_key == "CAPITAL.BOND":
            loaded = _load_bond_series(self.data_root)
            return (*loaded, {"roots": [str(self.data_root / "bond_world"), str(self.data_root / "bond_world_v1")]})
        return {}, {}, 0, {}

    def run_domain(self, domain_key: str) -> Dict[str, Any]:
        series, sources, rows_seen, source_meta = self._domain_series(domain_key)
        inserted = 0
        route_keys: List[str] = []
        per_symbol: Dict[str, Any] = {}
        if not series:
            self.store.save_ingestion_record(domain_key, "DOMAIN", "WAITING_DATA", "NO_RAW_SERIES", [], {}, source_meta)
            return {"status": "WAITING_DATA", "series": 0, "rows": rows_seen, "experiences_inserted": 0, "routes": []}

        # Crypto Level-1 keeps all observed non-stable evidence, while top activity is published
        # for later Level-2 child selection. This prevents deleting historical competence.
        activity = sorted(((len(pts), sym) for sym, pts in series.items()), reverse=True)
        active_top = [sym for _, sym in activity[:10]] if domain_key == "MARKETS.CRYPTO" else []

        for symbol, points in sorted(series.items()):
            symbol_inserted = 0
            for scale in MARKET_SCALES:
                periods = _aggregate_periods(points, scale)
                if len(periods) < 2:
                    continue
                route_key = f"{symbol}::{scale}::H1"
                route_keys.append(route_key)
                for i in range(len(periods) - 1):
                    cur, nxt = periods[i], periods[i + 1]
                    features = _market_features(periods, i)
                    if domain_key == "CAPITAL.BOND":
                        target = {"future_move_bp": (float(nxt["close"]) - float(cur["close"])) * 100.0}
                    else:
                        target = {"future_return_pct": (float(nxt["close"]) / float(cur["close"]) - 1.0) * 100.0 if cur["close"] else 0.0}
                    exp = ExperienceRecord(
                        experience_id=hash_id("MARKET_EXP", domain_key, route_key, cur["start"], nxt["start"]),
                        domain_key=domain_key,
                        source_id=symbol,
                        event_unix=int(cur["last_unix"]),
                        period_key=f"{scale}:{cur['start']}",
                        features=features,
                        target=target,
                        target_complete=True,
                        verified=True,
                        provenance={
                            "route_key": route_key, "symbol": symbol, "scale": scale,
                            "source_files": sorted(sources.get(symbol, set())),
                            "source_period_start": int(cur["start"]), "target_period_start": int(nxt["start"]),
                            "parent_input_policy": "RAW_ONLY_FOR_OWN_DOMAIN",
                        },
                        quality={"status": "VERIFIED", "observations": int(cur["observations"])},
                    )
                    if self.store.upsert_experience(exp):
                        inserted += 1
                        symbol_inserted += 1
            per_symbol[symbol] = {"raw_points": len(points), "experiences_inserted": symbol_inserted}

        route_keys = sorted(set(route_keys))
        payload = {
            "status": "INGESTED", "series": len(series), "rows": rows_seen,
            "experiences_inserted": inserted, "routes": route_keys,
            "symbols": per_symbol, "source": source_meta,
        }
        if domain_key == "MARKETS.CRYPTO":
            payload["dynamic_top10_local_activity"] = active_top
            self.store.publish_artifact("DYNAMIC_MEMBERSHIP_SNAPSHOT", domain_key, {
                "selection_basis": "LOCAL_OBSERVATION_ACTIVITY_UNTIL_LIVE_BINANCE_RANKER_CONNECTED",
                "top10": active_top, "created_unix": now_unix(),
            })
        self.store.save_ingestion_record(domain_key, "DOMAIN", "VERIFIED", "RAW_SERIES_INGESTED", [], {"series": len(series)}, payload)
        return payload

    def run(self) -> Dict[str, Any]:
        return {key: self.run_domain(key) for key in ("MARKETS.CURRENCY", "MARKETS.CRYPTO", "CAPITAL.STOCK", "CAPITAL.BOND")}


def build_parent_contexts(store: StateStore, market_ingestion: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Publish parent intelligence snapshots for Capital without rereading parent RAW."""
    contexts: Dict[str, Dict[str, Any]] = {}
    for parent in ("MARKETS.CURRENCY", "MARKETS.CRYPTO"):
        info = market_ingestion.get(parent, {})
        rows = store.load_verified_experiences(parent, complete_only=True)
        latest = max((r.event_unix for r in rows), default=0)
        ctx = {
            "domain_key": parent,
            "experience_count": len(rows),
            "latest_experience_unix": latest,
            "ingestion": {k: v for k, v in info.items() if k not in {"symbols", "routes"}},
            "artifact_rule": "CHILD_INHERITS_CANONICAL_PARENT_INTELLIGENCE_NOT_PARENT_RAW",
        }
        contexts[parent] = ctx
    return contexts


# ============================================================
# WORLD ORCHESTRATOR
# ============================================================

class WorldOrchestrator:
    """Runs all parent Level-1 domains before child Level-1 domains."""

    PARENT_FIRST_ORDER = (
        "SPORTS.TENNIS",
        "SPORTS.SOCCER",
        "SPORTS.BASEBALL",
        "SPORTS.BASKETBALL",
        "SPORTS.E_SPORTS",
        "SPORTS.GOLF",
        "MARKETS.CURRENCY",
        "MARKETS.CRYPTO",
        "CAPITAL.STOCK",
        "CAPITAL.BOND",
    )

    def __init__(self, factory: CanonicalNetworkFactory, bridge: ParentIntelligenceBridge):
        self.factory = factory
        self.bridge = bridge

    def run_level1(self, route_map: Dict[str, Sequence[str]], parent_contexts: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        parent_contexts = parent_contexts or {}
        result: Dict[str, Any] = {}
        for domain_key in self.PARENT_FIRST_ORDER:
            if domain_key not in route_map:
                continue
            spec = DOMAIN_REGISTRY[domain_key]
            if spec.parent_domains:
                missing = [p for p in spec.parent_domains if p not in parent_contexts]
                if missing:
                    result[domain_key] = {"status": "WAITING_FOR_PARENT_INTELLIGENCE", "missing": missing}
                    continue
            rows = []
            for route_key in route_map[domain_key]:
                parent_context = {p: parent_contexts[p] for p in spec.parent_domains} if spec.parent_domains else None
                rows.append(self.factory.evaluate_route(domain_key, route_key, parent_context))
            result[domain_key] = rows
        return result


# ============================================================
# MANIFEST / BOOTSTRAP
# ============================================================

def write_canonical_manifests(root: Path) -> None:
    root = Path(root)
    for key, spec in DOMAIN_REGISTRY.items():
        base = root / "worlds" / f"WORLD__{spec.world}" / f"DOMAIN__{spec.discipline}"
        atomic_json_write(base / "constitution.json", spec.governance.constitution)
        atomic_json_write(base / "policies.json", spec.governance.policies)
        atomic_json_write(base / "rules.json", spec.governance.rules)
        atomic_json_write(base / "domain_spec.json", {
            "domain_key": key,
            "world": spec.world,
            "discipline": spec.discipline,
            "target": asdict(spec.target),
            "readiness": asdict(spec.readiness),
            "parent_domains": list(spec.parent_domains),
            "dynamic_membership": spec.dynamic_membership,
            "notes": spec.notes,
        })


def current_stage_route_map(market_ingestion: Optional[Dict[str, Any]] = None) -> Dict[str, Sequence[str]]:
    """All connected canonical Level-1 routes at the current executable frontier."""
    routes: Dict[str, Sequence[str]] = {
        key: (f"PRIMARY::{spec.target.target_id}",)
        for key, spec in DOMAIN_REGISTRY.items()
        if key.startswith("SPORTS.")
    }
    for domain_key, info in (market_ingestion or {}).items():
        discovered = tuple(info.get("routes") or ())
        if discovered:
            routes[domain_key] = discovered
        elif domain_key in DOMAIN_REGISTRY:
            routes[domain_key] = (f"PRIMARY::{DOMAIN_REGISTRY[domain_key].target.target_id}",)
    return routes


def run_current_pipeline(root: Path, sports_root: Path, data_root: Path, db: StateStore) -> Dict[str, Any]:
    """Run every implemented Level-1 gate for every canonical world.

    Executable frontier:
      manifests -> all-domain ingestion -> quality/Experience -> parent inheritance ->
      readiness -> +20% retraining gate -> chronological 60/40 -> complexity -> candidates.

    Heavy fitting is dispatched to Node-01 over the internal LAN through SSH job bundles.
    """
    sports_ingestion = SportsWorldIngestor(db, sports_root).run()
    market_ingestion = MarketCapitalWorldIngestor(db, data_root).run()

    compute_backend = build_remote_backend(root, strict_dispatch=False)
    compute_queue = compute_backend.queue
    factory = CanonicalNetworkFactory(
        root=root, store=db, model_lab=DryRunModelLab(), compute_backend=compute_backend
    )
    bridge = ParentIntelligenceBridge(db)
    orchestrator = WorldOrchestrator(factory, bridge)
    route_map = current_stage_route_map(market_ingestion)

    parent_contexts = build_parent_contexts(db, market_ingestion)
    # Persist lineage snapshots for both Capital domains. A snapshot is knowledge metadata,
    # never a copy of the parent's raw market files.
    for child in ("CAPITAL.STOCK", "CAPITAL.BOND"):
        for parent, ctx in parent_contexts.items():
            bridge.inherit(child, parent, ctx, int(ctx.get("latest_experience_unix") or now_unix()))

    level1 = orchestrator.run_level1(route_map, parent_contexts=parent_contexts)
    return {
        "sports_ingestion": sports_ingestion,
        "market_capital_ingestion": market_ingestion,
        "level1_gate_all_worlds": level1,
        "parent_intelligence": parent_contexts,
        "compute_queue": compute_queue.summary(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SSI V5 Canonical World Factory")
    parser.add_argument("--root", default=os.environ.get("SSI_CANONICAL_ROOT", "./dane/ssi_canonical"))
    parser.add_argument("--sports-root", default=os.environ.get("SSI_SPORTS_ROOT", "./dane/sport_2way"))
    parser.add_argument("--data-root", default=os.environ.get("SSI_DATA_ROOT", "./dane"))
    parser.add_argument("--summary", action="store_true", help="Also print persisted ingestion summary")
    parser.add_argument("--summary-only", action="store_true", help="Do not run pipeline; print persisted state only")
    # Kept only for compatibility with the previous build. Ingestion is now part
    # of the normal executable workflow and no flag is required.
    parser.add_argument("--ingest-sports", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    write_canonical_manifests(root)
    db = StateStore(root / "canonical_worlds.sqlite3")

    summary: Dict[str, Any] = {
        "status": "READY",
        "schema_version": SCHEMA_VERSION,
        "root": str(root),
        "database": str(db.path),
        "worlds": {k: list(v) for k, v in WORLD_REGISTRY.items()},
        "domains": sorted(DOMAIN_REGISTRY.keys()),
        "train_fraction": DEFAULT_TRAIN_FRACTION,
        "observation_fraction": DEFAULT_OBSERVATION_FRACTION,
        "retrain_growth": DEFAULT_RETRAIN_GROWTH,
        "workflow": [
            "CANONICAL_MANIFESTS",
            "SPORTS_INGESTION",
            "CURRENCY_INGESTION",
            "CRYPTO_INGESTION",
            "STOCK_INGESTION",
            "BOND_INGESTION",
            "DATA_QUALITY_GATE",
            "EXPERIENCE_BUILDER",
            "PARENT_INTELLIGENCE_INHERITANCE",
            "READINESS_GATE",
            "RETRAINING_GATE_20_PERCENT",
            "CHRONOLOGICAL_60_40_SPLIT",
            "COMPLEXITY_GATE",
            "MODEL_CANDIDATE_PLAN",
            "PRIORITY_COMPUTE_QUEUE",
            "NODE01_RESOURCE_ROUTING",
        ],
        "model_lab": "CANDIDATE_PLANNING_ONLY_NO_HEAVY_TRAINING",
        "remote_compute": "SSH_LAN_NODE01_QUEUE_ENABLED",
    }

    if args.summary_only:
        summary["persisted_ingestion"] = db.ingestion_summary()
    else:
        summary["pipeline"] = run_current_pipeline(root, Path(args.sports_root), Path(args.data_root), db)
        if args.summary:
            summary["persisted_ingestion"] = db.ingestion_summary()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
