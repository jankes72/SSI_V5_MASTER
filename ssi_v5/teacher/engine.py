from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from ssi_v5.governance import build_default_registry


@dataclass(frozen=True)
class ComplexityTier:
    tier: int
    min_samples: int
    min_periods: int
    max_features: int
    mlp_architectures: Tuple[Tuple[int, ...], ...]
    sequence_windows: Tuple[int, ...] = ()
    advanced_models: Tuple[str, ...] = ()


DEFAULT_COMPLEXITY_TIERS: Tuple[ComplexityTier, ...] = (
    ComplexityTier(1, 24, 2, 24, ((16,),)),
    ComplexityTier(2, 80, 4, 48, ((24,), (32, 16)), (3,)),
    ComplexityTier(3, 240, 8, 80, ((32, 16), (64, 32, 16)), (3, 6)),
    ComplexityTier(4, 800, 20, 128, ((64, 32, 16), (128, 64, 32)), (6, 12), ("GRU", "LSTM", "TRANSFORMER")),
)


@dataclass(frozen=True)
class ReadinessPolicy:
    min_samples: int
    min_periods: int
    min_target_diversity: int = 2
    max_missing_fraction: float = 0.10


READINESS_BY_DOMAIN: Dict[str, ReadinessPolicy] = {
    "DOMAIN__TENNIS": ReadinessPolicy(80, 7),
    "DOMAIN__SOCCER": ReadinessPolicy(120, 10, 5),
    "DOMAIN__BASEBALL": ReadinessPolicy(120, 10, 5),
    "DOMAIN__BASKETBALL": ReadinessPolicy(120, 10, 8),
    "DOMAIN__E_SPORTS": ReadinessPolicy(100, 8, 4),
    "DOMAIN__GOLF": ReadinessPolicy(100, 3),
    "DOMAIN__CURRENCY_PAIRS": ReadinessPolicy(120, 5),
    "DOMAIN__CRYPTO_PAIRS": ReadinessPolicy(120, 3),
    "DOMAIN__CROSS_STRENGTH": ReadinessPolicy(120, 5),
    "DOMAIN__PAIR_RELATIONS": ReadinessPolicy(120, 5),
    "DOMAIN__MARKET_REGIMES": ReadinessPolicy(120, 5),
    "DOMAIN__STOCK": ReadinessPolicy(120, 10),
    "DOMAIN__BOND": ReadinessPolicy(120, 10),
    "DOMAIN__CURRENCY_ASSET": ReadinessPolicy(120, 5),
    "DOMAIN__CRYPTO_ASSET": ReadinessPolicy(120, 3),
    "DOMAIN__PORTFOLIO": ReadinessPolicy(120, 10),
    "DOMAIN__ALLOCATION": ReadinessPolicy(120, 10),
    "DOMAIN__RISK": ReadinessPolicy(120, 10),
}


@dataclass(frozen=True)
class TeacherDecision:
    action: str
    reason: str
    world_id: str
    domain_id: str
    sample_count: int
    period_count: int
    target_diversity: int
    missing_fraction: float
    last_train_samples: int
    growth_fraction: float
    complexity_tier: Optional[int]
    governance_ids: Tuple[str, ...]
    train_fraction: float
    observation_fraction: float
    observation_trains_same_generation: bool

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WorldTeacherEngine:
    """Policy-bound planner. It may recommend training, never promote or execute it."""

    def __init__(self, complexity_tiers: Sequence[ComplexityTier] = DEFAULT_COMPLEXITY_TIERS):
        self.complexity_tiers = tuple(sorted(complexity_tiers, key=lambda t: t.tier))
        self.governance = build_default_registry()

    def _policy_values(self, world_id: str) -> Tuple[float, float, float, Tuple[str, ...]]:
        chain = self.governance.chain_for(world_id)
        root, world_const, world_policy = chain
        train = float(world_policy.rules["training_fraction"])
        observation = float(world_policy.rules["observation_fraction"])
        retrain_growth = float(world_policy.rules["retrain_growth_trigger"])
        if abs((train + observation) - 1.0) > 1e-9:
            raise ValueError("Governance train/observation fractions must sum to 1.0")
        ids = (root.governance_id, world_const.governance_id, world_policy.governance_id)
        return train, observation, retrain_growth, ids

    def _choose_tier(self, sample_count: int, period_count: int) -> Optional[ComplexityTier]:
        allowed = [t for t in self.complexity_tiers if sample_count >= t.min_samples and period_count >= t.min_periods]
        return allowed[-1] if allowed else None

    def decide(
        self,
        *,
        world_id: str,
        domain_id: str,
        sample_count: int,
        period_count: int,
        target_diversity: int,
        missing_fraction: float,
        last_train_samples: int,
    ) -> TeacherDecision:
        policy = READINESS_BY_DOMAIN.get(domain_id)
        if policy is None:
            raise ValueError(f"No readiness policy for {domain_id}")

        chain = self.governance.chain_for(world_id, domain_id)
        domain_policy = chain[-1]
        if domain_policy.scope.get("domain_id") != domain_id:
            raise ValueError("Domain governance mismatch")

        train_fraction, observation_fraction, retrain_growth, governance_ids = self._policy_values(world_id)
        governance_ids = governance_ids + (domain_policy.governance_id,)

        reasons = []
        if sample_count < policy.min_samples:
            reasons.append(f"SAMPLES {sample_count}/{policy.min_samples}")
        if period_count < policy.min_periods:
            reasons.append(f"PERIODS {period_count}/{policy.min_periods}")
        if target_diversity < policy.min_target_diversity:
            reasons.append(f"TARGET_DIVERSITY {target_diversity}/{policy.min_target_diversity}")
        if missing_fraction > policy.max_missing_fraction:
            reasons.append(f"MISSING_FRACTION {missing_fraction:.4f}>{policy.max_missing_fraction:.4f}")

        tier = self._choose_tier(sample_count, period_count)
        if tier is None:
            reasons.append("NO_ALLOWED_COMPLEXITY_TIER")

        growth = 0.0 if last_train_samples <= 0 else max(0, sample_count - last_train_samples) / max(1, last_train_samples)

        if reasons:
            action = "ABSTAIN"
            reason = "; ".join(reasons)
        elif last_train_samples <= 0:
            action = "TRAIN"
            reason = "FIRST_GENERATION_READY"
        else:
            new_samples = max(0, sample_count - last_train_samples)
            required = max(1, int((last_train_samples * retrain_growth) + 0.999999999))
            if new_samples < required:
                action = "WAIT"
                reason = f"RETRAIN_GROWTH_TOO_SMALL {new_samples}/{required}"
            else:
                action = "RETRAIN"
                reason = f"RETRAIN_GROWTH_READY {growth:.3f}"

        return TeacherDecision(
            action=action,
            reason=reason,
            world_id=world_id,
            domain_id=domain_id,
            sample_count=sample_count,
            period_count=period_count,
            target_diversity=target_diversity,
            missing_fraction=missing_fraction,
            last_train_samples=last_train_samples,
            growth_fraction=growth,
            complexity_tier=tier.tier if tier else None,
            governance_ids=tuple(governance_ids),
            train_fraction=train_fraction,
            observation_fraction=observation_fraction,
            observation_trains_same_generation=False,
        )


def teacher_status() -> Dict[str, Any]:
    engine = WorldTeacherEngine()
    return {
        "status": "READY",
        "schema_version": 1,
        "role": "PLAN_ONLY_NO_EXECUTION_NO_PROMOTION",
        "train_fraction": 0.60,
        "observation_fraction": 0.40,
        "retrain_growth_trigger": 0.20,
        "observation_trains_same_generation": False,
        "complexity_tiers": [asdict(t) for t in engine.complexity_tiers],
        "readiness_policy_count": len(READINESS_BY_DOMAIN),
    }
