#!/usr/bin/env python3
"""Publish runtime v19 / coordination v26 for target-journal D-state handling."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v18/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v19/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v25/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v26/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        sha_key = key[:-5] + "_sha256" if key.endswith("_path") else None
        if sha_key is None or sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[sha_key] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "record target nvme2 journal D-state without treating the kernel "
        "journal worker itself as external user-space interference proof"
    )
    coordination["target_journal_d_state_policy"] = {
        "recognized_name_regex": r"jbd2/nvme2n1-\d+",
        "record_in_every_pressure_sample": True,
        "kernel_worker_alone_blocks_formal_wave": False,
        "user_space_or_unknown_d_state_remains_fail_closed": True,
        "external_physical_workload_remains_fail_closed": True,
        "cpu_numa_process_and_target_storage_overlap_gates_unchanged": True,
    }
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v19"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "remove target-journal kernel-thread false positive while preserving "
        "all user-space and external-workload interference gates"
    )
    optimization["target_journal_d_state_policy"] = dict(
        coordination["target_journal_d_state_policy"]
    )
    builder = Path(__file__).resolve()
    optimization["bindings"]["kernel_journal_gate_release_builder_path"] = rel(builder)
    optimization["bindings"]["kernel_journal_gate_release_builder_sha256"] = sha(builder)
    rebind(optimization["bindings"])
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
