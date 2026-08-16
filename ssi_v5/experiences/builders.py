from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any, Dict, Iterable, List, Optional


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(payload: Dict[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ExperienceRecord:
    experience_id: str
    world_id: str
    domain_id: str
    entity_id: str
    route_key: str
    source_id: str
    event_unix: int
    period_key: str
    features: Dict[str, Any]
    target: Any
    target_complete: bool
    verified: bool
    provenance: Dict[str, Any]
    quality: Dict[str, Any]
    experience_kind: str
    schema_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BaseExperienceBuilder:
    world_id: str = ""

    @staticmethod
    def _require_fields(raw: Dict[str, Any], fields: Iterable[str]) -> None:
        missing = [f for f in fields if f not in raw]
        if missing:
            raise ValueError(f"missing required fields: {', '.join(missing)}")

    @staticmethod
    def _make_id(payload: Dict[str, Any]) -> str:
        return "EXP__" + _content_hash(payload)[:24].upper()

    @staticmethod
    def _base_provenance(raw: Dict[str, Any], builder_name: str) -> Dict[str, Any]:
        return {
            "builder": builder_name,
            "raw_source_id": raw["source_id"],
            "raw_content_hash": _content_hash(raw),
            "raw_record_preserved": True,
        }


class SportExperienceBuilder(BaseExperienceBuilder):
    world_id = "WORLD__SPORT"

    def build(self, raw: Dict[str, Any]) -> ExperienceRecord:
        self._require_fields(raw, [
            "source_id", "domain_id", "entity_id", "event_unix", "period_key",
            "features", "target", "target_complete", "verified",
        ])
        if not str(raw["domain_id"]).startswith("DOMAIN__"):
            raise ValueError("sport domain_id must use canonical DOMAIN__ namespace")
        payload = {
            "world_id": self.world_id,
            "domain_id": raw["domain_id"],
            "entity_id": raw["entity_id"],
            "source_id": raw["source_id"],
            "event_unix": int(raw["event_unix"]),
            "period_key": str(raw["period_key"]),
            "features": dict(raw["features"]),
            "target": raw["target"],
        }
        return ExperienceRecord(
            experience_id=self._make_id(payload),
            world_id=self.world_id,
            domain_id=raw["domain_id"],
            entity_id=raw["entity_id"],
            route_key=f"{self.world_id}/{raw['domain_id']}/{raw['entity_id']}",
            source_id=raw["source_id"],
            event_unix=int(raw["event_unix"]),
            period_key=str(raw["period_key"]),
            features=dict(raw["features"]),
            target=raw["target"],
            target_complete=bool(raw["target_complete"]),
            verified=bool(raw["verified"]),
            provenance=self._base_provenance(raw, self.__class__.__name__),
            quality={"conflict_state": raw.get("conflict_state", "NONE")},
            experience_kind="SPORT_EVENT",
        )


class PairExperienceBuilder(BaseExperienceBuilder):
    world_id = "WORLD__FOREX"

    def build(self, raw: Dict[str, Any]) -> ExperienceRecord:
        self._require_fields(raw, [
            "source_id", "domain_id", "base_asset", "quote_asset", "event_unix",
            "period_key", "features", "target", "target_complete", "verified", "target_time",
        ])
        if raw["domain_id"] not in {"DOMAIN__CURRENCY_PAIRS", "DOMAIN__CRYPTO_PAIRS"}:
            raise ValueError("PairExperienceBuilder accepts only pair domains")
        base = str(raw["base_asset"]).upper()
        quote = str(raw["quote_asset"]).upper()
        if base == quote:
            raise ValueError("base_asset and quote_asset must differ")
        pair = f"{base}/{quote}"
        features = dict(raw["features"])
        features.setdefault("base_asset", base)
        features.setdefault("quote_asset", quote)
        features.setdefault("pair", pair)
        features.setdefault("target_time", raw["target_time"])
        payload = {
            "world_id": self.world_id,
            "domain_id": raw["domain_id"],
            "entity_id": pair,
            "source_id": raw["source_id"],
            "event_unix": int(raw["event_unix"]),
            "period_key": str(raw["period_key"]),
            "features": features,
            "target": raw["target"],
        }
        return ExperienceRecord(
            experience_id=self._make_id(payload),
            world_id=self.world_id,
            domain_id=raw["domain_id"],
            entity_id=pair,
            route_key=f"{self.world_id}/{raw['domain_id']}/{base}__{quote}",
            source_id=raw["source_id"],
            event_unix=int(raw["event_unix"]),
            period_key=str(raw["period_key"]),
            features=features,
            target=raw["target"],
            target_complete=bool(raw["target_complete"]),
            verified=bool(raw["verified"]),
            provenance=self._base_provenance(raw, self.__class__.__name__),
            quality={"pair_semantics": "EXPLICIT", "target_time": raw["target_time"]},
            experience_kind="FOREX_PAIR",
        )


class CapitalAssetExperienceBuilder(BaseExperienceBuilder):
    world_id = "WORLD__CAPITAL"
    _ALLOWED = {
        "DOMAIN__STOCK", "DOMAIN__BOND", "DOMAIN__CURRENCY_ASSET", "DOMAIN__CRYPTO_ASSET",
        "DOMAIN__PORTFOLIO", "DOMAIN__ALLOCATION", "DOMAIN__RISK",
    }

    def build(self, raw: Dict[str, Any]) -> ExperienceRecord:
        self._require_fields(raw, [
            "source_id", "domain_id", "entity_id", "event_unix", "period_key",
            "features", "target", "target_complete", "verified", "target_definition",
        ])
        if raw["domain_id"] not in self._ALLOWED:
            raise ValueError("unsupported capital domain")
        features = dict(raw["features"])
        features.setdefault("target_definition", raw["target_definition"])
        payload = {
            "world_id": self.world_id,
            "domain_id": raw["domain_id"],
            "entity_id": raw["entity_id"],
            "source_id": raw["source_id"],
            "event_unix": int(raw["event_unix"]),
            "period_key": str(raw["period_key"]),
            "features": features,
            "target": raw["target"],
        }
        return ExperienceRecord(
            experience_id=self._make_id(payload),
            world_id=self.world_id,
            domain_id=raw["domain_id"],
            entity_id=str(raw["entity_id"]),
            route_key=f"{self.world_id}/{raw['domain_id']}/{raw['entity_id']}",
            source_id=raw["source_id"],
            event_unix=int(raw["event_unix"]),
            period_key=str(raw["period_key"]),
            features=features,
            target=raw["target"],
            target_complete=bool(raw["target_complete"]),
            verified=bool(raw["verified"]),
            provenance=self._base_provenance(raw, self.__class__.__name__),
            quality={"target_definition": raw["target_definition"]},
            experience_kind="CAPITAL_ASSET",
        )
