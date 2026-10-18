#!/usr/bin/env python3
"""Freeze the fresh matched XP baseline replay contract."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / (
    "config/formal/frozen_mysql_matched_baseline_replay_20260829_v7/"
    "baseline_replay_contract.json"
)
AUTHORITY = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1102_matched_xp_live_authority_20260829"
)
OUTPUT = ROOT / (
    "config/formal/frozen_mysql_matched_xp_baseline_replay_20260829_v2/"
    "baseline_replay_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def bind(path: str) -> dict[str, str]:
    target = ROOT / path
    return {"path": path, "sha256": sha(target)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh contract required: {OUTPUT}")
    source = load(SOURCE)
    contract = copy.deepcopy(source)
    contract.update({
        "schema_version": "mysql_matched_xp_baseline_replay_contract_v2",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "baseline_replayed": True,
        "champion_replayed": False,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "historical_artifacts_mutated": False,
        "assignments_by_scene": {
            "hybench_xp_cpu": {"xp_cpu_w2": "b_cpu_baseline"},
            "hybench_xp_io": {
                "xp_io_w1": "b_io_baseline",
                "xp_io_w2": "c_io_baseline",
            },
        },
        "points": {
            key: copy.deepcopy(source["points"][key])
            for key in ("b_cpu_baseline", "b_io_baseline", "c_io_baseline")
        },
        "resource_freeze_by_scene": {
            key: copy.deepcopy(source["resource_freeze_by_scene"][key])
            for key in ("hybench_xp_cpu", "hybench_xp_io")
        },
        "source_result_evidence": {
            key: copy.deepcopy(source["source_result_evidence"][key])
            for key in ("xp_cpu", "xp_io")
        },
        "comparison_contract": {
            "pairing": "same_worker_same_device_same_corrected_sampler",
            "baseline_then_champion_order_required": True,
            "cross_worker_averaging_prohibited": True,
            "zero_from_unobserved_sibling_device_prohibited": True,
            "xp_cpu_worker_substitution": {
                "from": "xp_cpu_w1",
                "to": "xp_cpu_w2",
                "reason": (
                    "xp_cpu_w1 was removed by failed reset; both fresh baseline "
                    "and fresh champion use xp_cpu_w2"
                ),
            },
        },
    })
    authority_files = {
        "manifest": AUTHORITY / "live_authority.json",
        "prepared": AUTHORITY / "prepared_binding.json",
        "preflight": AUTHORITY / "transition_preflight.json",
        "accounts": AUTHORITY / "benchmark_accounts_binding.json",
    }
    contract["authority"] = {
        f"{key}_path": str(path) for key, path in authority_files.items()
    }
    contract["authority"].update({
        f"{key}_sha256": sha(path) for key, path in authority_files.items()
    })
    contract["source_bindings"] = {
        "authority_sealer": bind("scripts/seal_mysql_matched_xp_replay_authority_v1.py"),
        "builder": bind("scripts/build_mysql_matched_xp_baseline_contract_v1.py"),
        "runner": bind("scripts/run_mysql_matched_baseline_replay_v8.py"),
        "runner_implementation": bind("scripts/run_mysql_matched_baseline_replay_v7.py"),
        "coordination_contract": bind(
            "config/formal/frozen_mysql_cross_project_wave_coordination_20260829_v76/"
            "coordination_contract.json"
        ),
        "dynamic_gate": bind("scripts/mysql_cross_project_wave_coordination_v24.py"),
        "parallel_xp_reset": bind("scripts/mysql_async_xp_post_reset_settle_v2.py"),
        "single_scene_measurement": bind("scripts/mysql_single_scene_measurement_v2.py"),
        "c49_adapter": bind(
            "config/formal/frozen_mysql_c49_contaminated_champion_adapter_20260829_v4/"
            "algorithm_c_measurement_adapter_contract.json"
        ),
    }
    OUTPUT.parent.mkdir(parents=True, mode=0o700)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, OUTPUT)
    print(json.dumps({"status": "PASS", "contract": str(OUTPUT), "sha256": sha(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
