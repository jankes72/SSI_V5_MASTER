from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class DomainIdentity:
    world_id: str
    domain_id: str
    legacy_key: Optional[str]
    status: str = "ACTIVE"
    notes: str = ""

    @property
    def route_prefix(self) -> str:
        return f"{self.world_id}/{self.domain_id}"


WORLD__SPORT = "WORLD__SPORT"
WORLD__FOREX = "WORLD__FOREX"
WORLD__CAPITAL = "WORLD__CAPITAL"

CANONICAL_WORLD_REGISTRY: Mapping[str, Tuple[str, ...]] = {
    WORLD__SPORT: (
        "DOMAIN__TENNIS",
        "DOMAIN__SOCCER",
        "DOMAIN__BASEBALL",
        "DOMAIN__BASKETBALL",
        "DOMAIN__E_SPORTS",
        "DOMAIN__GOLF",
    ),
    WORLD__FOREX: (
        "DOMAIN__CURRENCY_PAIRS",
        "DOMAIN__CRYPTO_PAIRS",
        "DOMAIN__CROSS_STRENGTH",
        "DOMAIN__PAIR_RELATIONS",
        "DOMAIN__MARKET_REGIMES",
    ),
    WORLD__CAPITAL: (
        "DOMAIN__STOCK",
        "DOMAIN__BOND",
        "DOMAIN__CURRENCY_ASSET",
        "DOMAIN__CRYPTO_ASSET",
        "DOMAIN__PORTFOLIO",
        "DOMAIN__ALLOCATION",
        "DOMAIN__RISK",
    ),
}

LEGACY_DOMAIN_ALIASES: Dict[str, DomainIdentity] = {
    "SPORTS.TENNIS": DomainIdentity(WORLD__SPORT, "DOMAIN__TENNIS", "SPORTS.TENNIS"),
    "SPORTS.SOCCER": DomainIdentity(WORLD__SPORT, "DOMAIN__SOCCER", "SPORTS.SOCCER"),
    "SPORTS.BASEBALL": DomainIdentity(WORLD__SPORT, "DOMAIN__BASEBALL", "SPORTS.BASEBALL"),
    "SPORTS.BASKETBALL": DomainIdentity(WORLD__SPORT, "DOMAIN__BASKETBALL", "SPORTS.BASKETBALL"),
    "SPORTS.E_SPORTS": DomainIdentity(WORLD__SPORT, "DOMAIN__E_SPORTS", "SPORTS.E_SPORTS"),
    "SPORTS.GOLF": DomainIdentity(WORLD__SPORT, "DOMAIN__GOLF", "SPORTS.GOLF"),
    # Existing market series are preserved as migration inputs. They are NOT
    # relabelled as pair Experiences before PairExperienceBuilder exists.
    "MARKETS.CURRENCY": DomainIdentity(
        WORLD__FOREX,
        "DOMAIN__CURRENCY_SERIES_LEGACY",
        "MARKETS.CURRENCY",
        status="LEGACY_INPUT_ONLY",
        notes="Raw/derived single-series compatibility route; pair semantics come later.",
    ),
    "MARKETS.CRYPTO": DomainIdentity(
        WORLD__FOREX,
        "DOMAIN__CRYPTO_SERIES_LEGACY",
        "MARKETS.CRYPTO",
        status="LEGACY_INPUT_ONLY",
        notes="Raw/derived single-series compatibility route; pair semantics come later.",
    ),
    "CAPITAL.STOCK": DomainIdentity(WORLD__CAPITAL, "DOMAIN__STOCK", "CAPITAL.STOCK"),
    "CAPITAL.BOND": DomainIdentity(WORLD__CAPITAL, "DOMAIN__BOND", "CAPITAL.BOND"),
}


def identity_for_legacy(legacy_key: str) -> DomainIdentity:
    try:
        return LEGACY_DOMAIN_ALIASES[legacy_key]
    except KeyError as exc:
        raise KeyError(f"No SSI V5 canonical namespace mapping for legacy domain {legacy_key!r}") from exc


def canonical_namespace(legacy_key: str, *, entity_id: Optional[str] = None, route_key: Optional[str] = None) -> Dict[str, Optional[str]]:
    ident = identity_for_legacy(legacy_key)
    return {
        "world_id": ident.world_id,
        "domain_id": ident.domain_id,
        "entity_id": entity_id,
        "route_key": route_key,
        "legacy_domain_key": legacy_key,
        "migration_status": ident.status,
    }


def validate_registry() -> None:
    if set(CANONICAL_WORLD_REGISTRY) != {WORLD__SPORT, WORLD__FOREX, WORLD__CAPITAL}:
        raise ValueError("Canonical registry must contain exactly SPORT, FOREX and CAPITAL worlds")
    seen = set()
    for world_id, domain_ids in CANONICAL_WORLD_REGISTRY.items():
        if not world_id.startswith("WORLD__"):
            raise ValueError(f"Invalid world id: {world_id}")
        for domain_id in domain_ids:
            if not domain_id.startswith("DOMAIN__"):
                raise ValueError(f"Invalid domain id: {domain_id}")
            pair = (world_id, domain_id)
            if pair in seen:
                raise ValueError(f"Duplicate canonical domain: {pair}")
            seen.add(pair)
