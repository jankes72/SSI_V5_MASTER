from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


CHECKPOINT_SCHEMA_VERSION = 1


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def source_roots(sports_root: Path, data_root: Path) -> Tuple[Path, ...]:
    """Only raw-source roots. Never include dane/ssi_canonical itself."""
    candidates = (
        Path(sports_root),
        Path(data_root) / "market_world",
        Path(data_root) / "stock_world",
        Path(data_root) / "bond_world",
        Path(data_root) / "bond_world_v1",
    )
    seen = set()
    out: List[Path] = []
    for p in candidates:
        rp = p.expanduser().resolve()
        key = str(rp)
        if key not in seen:
            seen.add(key)
            out.append(rp)
    return tuple(out)


def _iter_source_files(roots: Sequence[Path]) -> Iterable[Tuple[str, os.stat_result]]:
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            st = root.stat()
            yield str(root), st
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", ".git", ".cache"}]
            for name in sorted(filenames):
                path = Path(dirpath) / name
                try:
                    st = path.stat()
                except (FileNotFoundError, PermissionError):
                    continue
                if not path.is_file():
                    continue
                yield str(path), st


def build_source_watermark(sports_root: Path, data_root: Path) -> Dict[str, Any]:
    roots = source_roots(sports_root, data_root)
    h = hashlib.sha256()
    count = 0
    total_size = 0
    newest_mtime_ns = 0
    per_root: Dict[str, Dict[str, int]] = {str(r): {"file_count": 0, "total_size": 0, "newest_mtime_ns": 0} for r in roots}
    root_strings = sorted(per_root, key=len, reverse=True)
    for filename, st in _iter_source_files(roots):
        count += 1
        total_size += int(st.st_size)
        newest_mtime_ns = max(newest_mtime_ns, int(st.st_mtime_ns))
        h.update(filename.encode("utf-8", errors="surrogateescape"))
        h.update(b"\0")
        h.update(str(int(st.st_size)).encode())
        h.update(b"\0")
        h.update(str(int(st.st_mtime_ns)).encode())
        h.update(b"\n")
        for rs in root_strings:
            if filename == rs or filename.startswith(rs + os.sep):
                info = per_root[rs]
                info["file_count"] += 1
                info["total_size"] += int(st.st_size)
                info["newest_mtime_ns"] = max(info["newest_mtime_ns"], int(st.st_mtime_ns))
                break
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "fingerprint": h.hexdigest(),
        "file_count": count,
        "total_size": total_size,
        "newest_mtime_ns": newest_mtime_ns,
        "roots": per_root,
    }


@dataclass(frozen=True)
class IncrementalDecision:
    action: str
    reason: str
    current: Dict[str, Any]
    previous: Optional[Dict[str, Any]]

    @property
    def should_ingest(self) -> bool:
        return self.action == "RUN_INGESTION"


class SourceCheckpoint:
    def __init__(self, root: Path):
        self.path = Path(root) / "runtime" / "source_checkpoint.json"

    def load(self) -> Optional[Dict[str, Any]]:
        if not self.path.is_file():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    def save(self, watermark: Mapping[str, Any], *, cycle_id: str, mode: str) -> None:
        payload = dict(watermark)
        payload.update({"cycle_id": cycle_id, "mode": mode})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def decide(self, sports_root: Path, data_root: Path, *, canonical_state_exists: bool) -> IncrementalDecision:
        current = build_source_watermark(sports_root, data_root)
        previous = self.load()
        if previous is None:
            # Migration safety: existing canonical state is treated as already ingested.
            # Create a baseline without rereading all historical RAW immediately.
            if canonical_state_exists:
                return IncrementalDecision("BOOTSTRAP_CHECKPOINT", "EXISTING_CANONICAL_STATE", current, None)
            return IncrementalDecision("RUN_INGESTION", "NO_CHECKPOINT_AND_NO_CANONICAL_STATE", current, None)
        if previous.get("fingerprint") == current.get("fingerprint"):
            return IncrementalDecision("SKIP_INGESTION", "RAW_SOURCES_UNCHANGED", current, previous)
        return IncrementalDecision("RUN_INGESTION", "RAW_SOURCES_CHANGED", current, previous)
