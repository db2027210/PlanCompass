#!/usr/bin/env python3
"""Freeze v57 handoff authority validation compatibility."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v57"
PREDECESSOR = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v56"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v14.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v11.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v14.py",
    "scene_authority_refresh": ROOT / "scripts/refresh_mysql_two_worker_scene_authority_v5.py",
    "chain_monitor": ROOT / "scripts/monitor_mysql_four_scene_top4_abc_chain_v14.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v10.py",
}

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value

def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")

def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination = load(PREDECESSOR / "coordination_contract.json")
    contract = load(PREDECESSOR / "algorithm_b_top4_contract.json")
    coordination["created_at"] = created
    contract.update({
        "created_at": created,
        "release_version": "v57-handoff-authority-validator",
        "coordination_contract_path": str(OUTPUT / "coordination_contract.json"),
        "handoff_authority_validator_successor": {
            "minimum_observed_seconds": 15,
            "formal_candidate_wave_equivalence_required": True,
            "resource_checks_and_fail_closed_required": True,
            "authority_artifact_mutation_prohibited": True,
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    })
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    atomic(OUTPUT / "algorithm_b_top4_contract.json", contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "contract": str(OUTPUT / "algorithm_b_top4_contract.json")}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
