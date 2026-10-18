#!/usr/bin/env python3
"""Run XP Top4-B with two workers on the mixed CPU-A-slot successor."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_fresh_parallel_221_scheduler_v25 as scheduler_221
from scripts import mysql_cross_project_wave_coordination_cpu_a_slots_v12 as gate_v8
from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v2 as reset_v7
from scripts import run_mysql_algorithm_b_xp_top4_async_v30 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as settle_v32
from scripts import run_mysql_four_scene_r0_v5 as two_worker_authority


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_20260828_v44"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
V40_CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v42"
)
BASE_RESET_XP = settle_v32.runtime.alg.reset_xp


def authority_root() -> Path:
    raw = os.environ.get("MYSQL_TWO_WORKER_AUTHORITY_ROOT", "")
    path = Path(raw).expanduser().resolve()
    required = (
        path / "transition_preflight.json",
        path / "prepared_binding.json",
        path / "benchmark_accounts_binding.json",
    )
    if (
        not raw or HISTORY.resolve() not in path.parents or path.is_symlink()
        or any(not item.is_file() or item.is_symlink() for item in required)
    ):
        raise RuntimeError("current two-worker authority root differs")
    return path


def scene_scoped_reset(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    requested = set(worker_ids or ())
    if not requested or not requested.issubset(workers):
        raise RuntimeError("two-worker B reset subset differs")
    scenes = {str(workers[worker_id]["scene"]) for worker_id in requested}
    if len(requested) > 2 or len(scenes) != 1:
        raise RuntimeError("two-worker B reset crossed a scene boundary")
    scoped = {worker_id: workers[worker_id] for worker_id in requested}
    BASE_RESET_XP(
        freeze, scoped, output, token, worker_ids=requested,
    )
    workers.update(scoped)


def validate_contract(alg: Any) -> dict[str, Any]:
    contract = alg.load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_b_xp_top4_cpu_a_slots_two_worker_contract_v2"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("physical_replay_parallelism") != 2
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy")
        != rejection_cycle.policy_contract()
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
    ):
        raise RuntimeError("Top4-B two-worker contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("Top4-B two-worker binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"Top4-B two-worker binding differs: {path}")
    return contract


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = V40_CONTRACT_ROOT
    # This successor freezes the complete inherited runtime chain itself.
    # The predecessor's historical v42 hash set intentionally no longer
    # validates after the shared C reset namespace fix.
    inherited_alg = (
        predecessor.predecessor.predecessor.predecessor.successor.predecessor._impl["alg"]
    )
    validate_contract(inherited_alg)
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    runtime = impl["runtime"]
    no_lease = impl["no_lease_runtime"]
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "formal_candidate_batches": [[1, 2], [3, 4], [5]],
            "candidate_parallelism": 2,
            "physical_replay_parallelism": 2,
            "sibling_scene_wait": False,
            "other_scene_completion_required": False,
        })
        document["xp_top4_parameter_release"].update({
            "contract_path": str(CONTRACT),
            "two_worker_successor": True,
        })
        document["freeze_path"] = str(FREEZE)
        document["freeze_sha256"] = alg.sha(FREEZE)
        document["runtime_preflight_path"] = str(two_worker_authority.PREFLIGHT)
        document["runtime_preflight_sha256"] = alg.sha(two_worker_authority.PREFLIGHT)
        document["prepared_binding_path"] = str(two_worker_authority.PREPARED)
        document["prepared_binding_sha256"] = alg.sha(two_worker_authority.PREPARED)
        document["benchmark_accounts_path"] = str(two_worker_authority.BENCHMARK_ACCOUNTS)
        document["benchmark_accounts_sha256"] = alg.sha(two_worker_authority.BENCHMARK_ACCOUNTS)
        return document

    def configure_runtime() -> Mapping[str, Any]:
        root = authority_root()
        contract = alg.load(COORDINATION)
        if (
            contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or contract.get("schema_version")
            != "mysql_algorithm_b_top4_cpu_a_slots_no_lease_coordination_v2"
            or contract.get("lease_mode") != "none"
            or contract.get("sibling_scene_wait") is not False
            or contract.get("maximum_workers_per_scene") != 2
        ):
            raise RuntimeError("Top4-B two-worker coordination differs")
        reset_values = {
            "FREEZE": FREEZE,
            "PREPARED": root / "prepared_binding.json",
            "RESET_SAME_FREEZE": True,
            "XP_IDS": {
                "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
            },
            "XP_CONTAINER_ALLOWLIST": {
            str(row["worker_id"]): str(row["container"])
            for row in alg.load(FREEZE)["workers"]
            if str(row["worker_id"]).startswith(("xp_cpu_", "xp_io_"))
            },
            "DOCKER_CONTROL_TIMEOUT_SECONDS": 600,
        }
        for name, value in reset_values.items():
            setattr(reset_v7, name, copy.deepcopy(value))
            reset_v7.install.__globals__[name] = copy.deepcopy(value)
        reset_v7._impl.update(copy.deepcopy(reset_values))
        reset_v7.install()
        alg.FREEZE = FREEZE
        alg.reset = reset_v7
        alg.r0mod = two_worker_authority
        alg.WORKER_IDS = (
            "job_w1", "job_w2", "tpch_w1", "tpch_w2",
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        )
        alg.XP_IDS = (
            "xp_cpu_w1", "xp_cpu_w2", "xp_io_w1", "xp_io_w2",
        )
        alg.XP_RESET_MAX_WORKERS = 2
        two_worker_authority.FREEZE = FREEZE
        two_worker_authority.FREEZE_SCHEMA = (
            "mysql_four_scene_cpu_a_slots_two_worker_successor_v2"
        )
        two_worker_authority.PREFLIGHT = root / "transition_preflight.json"
        two_worker_authority.RUNTIME_PREFLIGHT = two_worker_authority.PREFLIGHT
        two_worker_authority.PREPARED = root / "prepared_binding.json"
        two_worker_authority.BENCHMARK_ACCOUNTS = (
            root / "benchmark_accounts_binding.json"
        )
        two_worker_authority.PREFLIGHT_SOURCE = (
            ROOT / "scripts/preflight_mysql_four_scene_cpu_a_slots_v10.py"
        )
        two_worker_authority.AUTHORITY_SCENE_SCOPE = impl["SCENES"][0]
        two_worker_authority.dynamic_gate = gate_v8
        settle_v32.ORIGINAL_RESET_XP = scene_scoped_reset
        BASE_RESET_XP.__globals__["reset"] = reset_v7
        runtime.alg.reset_xp = settle_v32.reset_xp
        no_lease.alg = alg
        no_lease.coordination = gate_v8
        runtime.WAVE_COORDINATION_CONTRACT = COORDINATION
        return contract

    def validate_release(scene: str) -> dict[str, Any]:
        contract = validate_contract(alg)
        expected = list(alg.load(impl["TOP4_ROOT"] / impl["TOP4_TABLES"][scene])[
            "parameter_space"
        ]["active_params"])
        if (contract.get("active_parameters_by_scene") or {}).get(scene) != expected:
            raise RuntimeError("Top4-B two-worker active parameter set differs")
        return contract

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["FREEZE"] = FREEZE
    impl["PHYSICAL_REPLAY_PARALLELISM"] = 2
    impl["scheduler"] = scheduler_221
    impl["configure_runtime"] = configure_runtime
    impl["validate_release"] = validate_release
    impl["state_document"] = state_document
    impl["ACCEPTANCE_AWARE_SELECTION_EFFECTIVE_OUTER"] = 1
    impl["SUBSET_ONLY_SHRINK_EFFECTIVE_OUTER"] = 1
    impl["CANDIDATE_DIVERSITY_EFFECTIVE_OUTER"] = 1
    impl["ONLINE_RESOURCE_MODEL_UPDATE_EFFECTIVE_OUTER"] = 1
    impl["decide_validation_acceptance"] = qps_policy.decide_qps_strict_progress
    impl["advance_acceptance_tier"] = qps_policy.keep_qps_only_mode
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
