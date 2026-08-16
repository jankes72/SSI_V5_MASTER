from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from ssi_v5.worlds.registry import CANONICAL_WORLD_REGISTRY

SCHEMA_VERSION = 1
ALLOWED_KINDS = {
    "ROOT_CONSTITUTION",
    "WORLD_CONSTITUTION",
    "WORLD_POLICY",
    "DOMAIN_POLICY",
    "WORLD_RULE",
    "ROOM_RULE",
    "LOCAL_RELATIONAL_RULE",
    "AGENT_INTERNAL_RULE",
    "STRATEGY_LOCAL_RULE",
}
ALLOWED_STATUSES = {"ACTIVE", "SUPERSEDED", "DRAFT", "SUSPENDED", "RETIRED"}


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(payload: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GovernanceDocument:
    governance_id: str
    kind: str
    version: int
    status: str
    scope: Mapping[str, Optional[str]]
    parent_governance_id: Optional[str]
    rules: Mapping[str, Any]
    description: str
    content_hash: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "GovernanceDocument":
        body = dict(raw)
        declared_hash = str(body.pop("content_hash", ""))
        calculated_hash = _content_hash(body)
        if declared_hash and declared_hash != calculated_hash:
            raise ValueError(
                f"Governance hash mismatch for {raw.get('governance_id')}: "
                f"declared={declared_hash} calculated={calculated_hash}"
            )
        doc = cls(
            governance_id=str(body["governance_id"]),
            kind=str(body["kind"]),
            version=int(body["version"]),
            status=str(body["status"]),
            scope=dict(body.get("scope", {})),
            parent_governance_id=body.get("parent_governance_id"),
            rules=dict(body.get("rules", {})),
            description=str(body.get("description", "")),
            content_hash=calculated_hash,
        )
        doc.validate()
        return doc

    def validate(self) -> None:
        if self.kind not in ALLOWED_KINDS:
            raise ValueError(f"Unsupported governance kind: {self.kind}")
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"Unsupported governance status: {self.status}")
        if self.version < 1:
            raise ValueError("Governance version must be >= 1")
        if not self.governance_id.endswith(f"__V{self.version}"):
            raise ValueError(
                f"governance_id must end in __V{self.version}: {self.governance_id}"
            )
        world_id = self.scope.get("world_id")
        domain_id = self.scope.get("domain_id")
        if world_id is not None and not str(world_id).startswith("WORLD__"):
            raise ValueError(f"Invalid world_id in governance scope: {world_id}")
        if domain_id is not None and not str(domain_id).startswith("DOMAIN__"):
            raise ValueError(f"Invalid domain_id in governance scope: {domain_id}")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "governance_id": self.governance_id,
            "kind": self.kind,
            "version": self.version,
            "status": self.status,
            "scope": dict(self.scope),
            "parent_governance_id": self.parent_governance_id,
            "rules": dict(self.rules),
            "description": self.description,
            "content_hash": self.content_hash,
        }


class GovernanceRegistry:
    def __init__(self, documents: Iterable[GovernanceDocument]):
        docs = list(documents)
        self._by_id: Dict[str, GovernanceDocument] = {}
        for doc in docs:
            if doc.governance_id in self._by_id:
                raise ValueError(f"Duplicate governance_id: {doc.governance_id}")
            self._by_id[doc.governance_id] = doc
        self.validate()

    @property
    def documents(self) -> Tuple[GovernanceDocument, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def get(self, governance_id: str) -> GovernanceDocument:
        try:
            return self._by_id[governance_id]
        except KeyError as exc:
            raise KeyError(f"Unknown governance document: {governance_id}") from exc

    def active(self, kind: str, *, world_id: Optional[str] = None, domain_id: Optional[str] = None) -> GovernanceDocument:
        matches = [
            doc
            for doc in self._by_id.values()
            if doc.kind == kind
            and doc.status == "ACTIVE"
            and doc.scope.get("world_id") == world_id
            and doc.scope.get("domain_id") == domain_id
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one ACTIVE {kind} for world={world_id} domain={domain_id}; got {len(matches)}"
            )
        return matches[0]

    def chain_for(self, world_id: str, domain_id: Optional[str] = None) -> List[GovernanceDocument]:
        chain = [self.active("ROOT_CONSTITUTION")]
        world_const = self.active("WORLD_CONSTITUTION", world_id=world_id)
        world_policy = self.active("WORLD_POLICY", world_id=world_id)
        chain.extend([world_const, world_policy])
        if domain_id is not None:
            chain.append(self.active("DOMAIN_POLICY", world_id=world_id, domain_id=domain_id))
        return chain

    def validate(self) -> None:
        roots = [d for d in self._by_id.values() if d.kind == "ROOT_CONSTITUTION" and d.status == "ACTIVE"]
        if len(roots) != 1:
            raise ValueError(f"Exactly one ACTIVE Root Constitution is required; got {len(roots)}")

        for doc in self._by_id.values():
            if doc.parent_governance_id is not None and doc.parent_governance_id not in self._by_id:
                raise ValueError(
                    f"Missing parent {doc.parent_governance_id} for {doc.governance_id}"
                )

        for world_id, domain_ids in CANONICAL_WORLD_REGISTRY.items():
            world_const = self.active("WORLD_CONSTITUTION", world_id=world_id)
            world_policy = self.active("WORLD_POLICY", world_id=world_id)
            if world_const.parent_governance_id != roots[0].governance_id:
                raise ValueError(f"{world_const.governance_id} must inherit Root Constitution")
            if world_policy.parent_governance_id != world_const.governance_id:
                raise ValueError(f"{world_policy.governance_id} must inherit World Constitution")
            for domain_id in domain_ids:
                domain_policy = self.active("DOMAIN_POLICY", world_id=world_id, domain_id=domain_id)
                if domain_policy.parent_governance_id != world_policy.governance_id:
                    raise ValueError(f"{domain_policy.governance_id} must inherit World Policy")

    def summary(self) -> Dict[str, Any]:
        active = [d for d in self._by_id.values() if d.status == "ACTIVE"]
        by_kind: Dict[str, int] = {}
        for doc in active:
            by_kind[doc.kind] = by_kind.get(doc.kind, 0) + 1
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "VALID",
            "document_count": len(self._by_id),
            "active_document_count": len(active),
            "active_by_kind": dict(sorted(by_kind.items())),
            "root_constitution": self.active("ROOT_CONSTITUTION").as_dict(),
            "worlds": {
                world_id: {
                    "constitution": self.active("WORLD_CONSTITUTION", world_id=world_id).as_dict(),
                    "policy": self.active("WORLD_POLICY", world_id=world_id).as_dict(),
                    "domains": {
                        domain_id: self.active(
                            "DOMAIN_POLICY", world_id=world_id, domain_id=domain_id
                        ).as_dict()
                        for domain_id in domain_ids
                    },
                }
                for world_id, domain_ids in CANONICAL_WORLD_REGISTRY.items()
            },
        }


def load_documents(directory: Path) -> List[GovernanceDocument]:
    docs: List[GovernanceDocument] = []
    for path in sorted(directory.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        docs.append(GovernanceDocument.from_mapping(raw))
    if not docs:
        raise ValueError(f"No governance documents found in {directory}")
    return docs


def build_default_registry() -> GovernanceRegistry:
    definitions = Path(__file__).resolve().parent / "definitions"
    return GovernanceRegistry(load_documents(definitions))


def governance_status() -> Dict[str, Any]:
    return build_default_registry().summary()
