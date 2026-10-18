#!/usr/bin/env python3
"""Publish runtime v15 with fail-closed completed-rank decision recovery."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v14"
    / "optimization_contract.json"
)
OLD_COORD = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v21"
    / "coordination_contract.json"
)
NEW_COORD = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v22"
    / "coordination_contract.json"
)
NEW = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v15"
    / "optimization_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def main() -> int:
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD),
        "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "bind completed-rank decision recovery controller without changing "
        "lease, quiet-window, or interference semantics"
    )
    for key, path_value in list(coordination["source_bindings"].items()):
        if not key.endswith("_path"):
            continue
        sha_key = key[:-5] + "_sha256"
        if sha_key in coordination["source_bindings"]:
            coordination["source_bindings"][sha_key] = sha(
                (ROOT / str(path_value)).resolve()
            )
    NEW_COORD.parent.mkdir(parents=True, exist_ok=True)
    NEW_COORD.write_text(
        json.dumps(coordination, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    NEW_COORD.with_name(NEW_COORD.name + ".sha256").write_text(
        f"{sha(NEW_COORD)}  {NEW_COORD.name}\n", encoding="ascii"
    )

    value = json.loads(OLD.read_text(encoding="utf-8"))
    value["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v15"
    value["predecessor"] = {"path": rel(OLD), "sha256": sha(OLD)}
    value["successor_reason"] = (
        "restore checkpoint plan replay state and reuse a SHA-bound completed "
        "rank only when its full workload and no-interference evidence pass"
    )
    value["completed_rank_decision_recovery"] = {
        "supported_rank": 5,
        "required_source_failure": "KeyError: 'full_plan_replay_history_v2'",
        "required_batch_status": "PASS",
        "required_full_scene_coverage": True,
        "required_hard_gates": True,
        "required_watchdog_interference_detected": False,
        "measurement_rerun_required": False,
        "decision_replay_uses_original_algorithm": True,
        "candidate_score_changed": False,
        "acceptance_or_convergence_contract_changed": False,
    }
    bindings = value["bindings"]
    builder = Path(__file__).resolve()
    bindings["state_restore_release_builder_path"] = rel(builder)
    bindings["state_restore_release_builder_sha256"] = sha(builder)
    for key, path_value in list(bindings.items()):
        if not key.endswith("_path"):
            continue
        sha_key = key[:-5] + "_sha256"
        if sha_key not in bindings:
            continue
        path = (ROOT / str(path_value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release binding differs: {path}")
        bindings[sha_key] = sha(path)
    NEW.parent.mkdir(parents=True, exist_ok=True)
    NEW.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    NEW.with_name(NEW.name + ".sha256").write_text(
        f"{sha(NEW)}  {NEW.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "coordination_path": rel(NEW_COORD),
        "coordination_sha256": sha(NEW_COORD),
        "optimization_path": rel(NEW),
        "optimization_sha256": sha(NEW),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
