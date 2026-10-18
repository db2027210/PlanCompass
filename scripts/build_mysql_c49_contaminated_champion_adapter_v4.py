#!/usr/bin/env python3
"""Freeze current C49 measurement sources for contaminated-champion replay."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260829_v3/algorithm_c_measurement_adapter_contract.json"
OUTPUT = ROOT / "config/formal/frozen_mysql_c49_contaminated_champion_adapter_20260829_v4/algorithm_c_measurement_adapter_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if OUTPUT.parent.exists() or OUTPUT.parent.is_symlink():
        raise RuntimeError(f"fresh adapter contract required: {OUTPUT.parent}")
    document: dict[str, Any] = json.loads(SOURCE.read_text(encoding="utf-8"))
    bindings = document.get("source_bindings")
    if not isinstance(bindings, Mapping) or not bindings:
        raise RuntimeError("predecessor source bindings are absent")
    refreshed: dict[str, dict[str, str]] = {}
    for name, raw in sorted(bindings.items()):
        path = ROOT / str(raw["path"])
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"adapter source is absent: {path}")
        refreshed[str(name)] = {
            "path": str(path.relative_to(ROOT)), "sha256": sha(path),
        }
    document["created_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    document["source_bindings"] = refreshed
    document["algorithm_a_measurement_adapter_successor"] = {
        "release_version": "v4",
        "predecessor_adapter_contract_path": str(SOURCE.relative_to(ROOT)),
        "predecessor_adapter_contract_sha256": sha(SOURCE),
        "algorithm_result_or_search_state_changed": False,
        "measurement_adapter_source_bindings_refreshed": True,
        "refreshed_binding_count": len(refreshed),
        "consumer": "four_scene_zero_io_contaminated_champions_only",
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=False)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(
        f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii",
    )
    print(json.dumps({"status": "PASS", "contract": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
