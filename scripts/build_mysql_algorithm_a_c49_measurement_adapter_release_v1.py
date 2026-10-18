#!/usr/bin/env python3
"""Freeze current c49 measurement-adapter sources for XP Algorithm A."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260819_v37"
    / "algorithm_c_async_contract.json"
)
OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260827_v1"
)
CONTRACT = OUTPUT / "algorithm_c_measurement_adapter_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh adapter release required: {OUTPUT}")
    contract = load(SOURCE)
    bindings = contract.get("source_bindings")
    if not isinstance(bindings, Mapping) or not bindings:
        raise RuntimeError("c49 predecessor source bindings are absent")
    refreshed: dict[str, dict[str, str]] = {}
    for name, raw in sorted(bindings.items()):
        if not isinstance(raw, Mapping):
            raise RuntimeError(f"c49 source binding is not an object: {name}")
        path = Path(str(raw.get("path") or ""))
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"current c49 source is absent: {path}")
        refreshed[str(name)] = {
            "path": str(path.relative_to(ROOT)), "sha256": sha(path)
        }
    contract.update({
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "algorithm_a_measurement_adapter_successor": {
            "release_version": "v1",
            "predecessor_contract_path": str(SOURCE.relative_to(ROOT)),
            "predecessor_contract_sha256": sha(SOURCE),
            "algorithm_c_result_or_search_state_changed": False,
            "measurement_adapter_source_bindings_refreshed": True,
        },
        "source_bindings": refreshed,
    })
    atomic(CONTRACT, contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "contract": str(CONTRACT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
