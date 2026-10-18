#!/usr/bin/env python3
"""Run one independent Algorithm C XP scene on four physical workers."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_v7 as preflight_v7
from scripts import reset_mysql_xp_data_planes_four_worker_v1 as reset4
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v1 as runtime
from scripts import run_mysql_xp_four_worker_authority_v1 as authority


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v8"
)
ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_xp4_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "optimization_contract.json"
COORDINATION_CONTRACT = CONTRACT_ROOT / "coordination_contract.json"
XP_WORKER_IDS = (
    "xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4",
    "xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4",
)


def validate_contract() -> dict[str, Any]:
    contract = runtime.alg.load(ASYNC_CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_c_xp4_async_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2, 3, 4], [5]]
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("semantic_top_k") != 5
        or contract.get("algorithm_c_candidate_or_outer_flow_changed") is not False
        or contract.get("native_xp_workload_unchanged") is not True
        or contract.get("accepted_history_preserved") is not True
        or contract.get("completed_candidate_replay_due_to_transition") != 0
        or contract.get("recalibration_performed") is not False
        or contract.get("lease_mode") != "none"
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
    ):
        raise RuntimeError("Algorithm C XP four-worker async contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("XP four-worker source binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if (
            not path.is_file() or path.is_symlink()
            or runtime.alg.sha(path) != binding.get("sha256")
        ):
            raise RuntimeError(f"XP four-worker source binding differs: {path}")
    return contract


def install() -> None:
    reset4.install()
    runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    runtime.WAVE_COORDINATION_CONTRACT = COORDINATION_CONTRACT
    runtime.alg.reset = reset4
    runtime.alg.r0mod = authority
    runtime.alg.WORKER_IDS = XP_WORKER_IDS
    runtime.alg.XP_IDS = XP_WORKER_IDS
    runtime.alg.XP_RESET_MAX_WORKERS = 4
    authority.PREFLIGHT_SOURCE = Path(preflight_v7.__file__).resolve()


def main() -> int:
    validate_contract()
    install()
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
