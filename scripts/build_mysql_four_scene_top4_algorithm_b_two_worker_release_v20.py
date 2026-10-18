#!/usr/bin/env python3
"""Freeze v63 TPC-H relational runtime continuation."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v63"
PREDECESSOR = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v62"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v20.py",
    "outer6_continuation_controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v18.py",
    "relational_runtime_predecessor": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v11.py",
    "two_worker_controller": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_two_worker_v1.py",
    "algorithm_b_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v12.py",
    "dynamic_noninterference_gate_predecessor": ROOT / "scripts/mysql_cross_project_wave_coordination_v11.py",
    "xp_reset_implementation": ROOT / "scripts/reset_mysql_xp_data_planes_v6.py",
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
        "release_version": "v63-tpch-relational-runtime-outer6-continuation",
        "coordination_contract_path": str(OUTPUT / "coordination_contract.json"),
        "relational_runtime_chain_successor": {
            "scene": "tpch_sf4_memory",
            "xp_only_docker_start_compatibility_layer_used": False,
            "existing_60_second_transition_preflight_preserved": True,
            "per_candidate_dynamic_gate_release": "v68",
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    })
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract["coordination_contract_sha256"] = sha(
        OUTPUT / "coordination_contract.json"
    )
    atomic(OUTPUT / "algorithm_b_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_b_top4_contract.json"),
        "sha256": sha(OUTPUT / "algorithm_b_top4_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
