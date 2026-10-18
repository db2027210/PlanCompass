#!/usr/bin/env python3
"""Run one four-scene Top4-B scene on mixed-SSD w1-w4 lanes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_fresh_parallel_4x_scheduler_v26 as scheduler_4x
from scripts import mysql_cross_project_wave_coordination_v14 as gate_v14
from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v2 as rejection_cycle
from scripts import run_mysql_algorithm_b_xp_top4_async_v3 as xp_adapter_v3
from scripts import run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5 as independent_v5
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v1 as reset_mixed
from scripts import run_mysql_algorithm_b_xp_top4_async_v30 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v32 as settle_v32
from scripts import run_mysql_four_scene_mixed_four_worker_authority_v1 as authority


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3"
    / "four_scene_effective_resource_successor.json"
)
FREEZE_SCHEMA = "mysql_four_scene_mixed_disk_four_worker_successor_v3"
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v14"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
BOOTSTRAP_CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v42"
)
BASE_RESET_XP = settle_v32.ORIGINAL_RESET_XP
TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}


def authority_root() -> Path:
    raw = os.environ.get("MYSQL_MIXED_FOUR_WORKER_AUTHORITY_ROOT", "")
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
        raise RuntimeError("current mixed four-worker authority root differs")
    return path


def scene_scoped_reset(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    output: Path, token: str, *, worker_ids: set[str] | None = None,
) -> None:
    requested = set(worker_ids or ())
    if not requested or not requested.issubset(workers):
        raise RuntimeError("mixed four-worker B reset subset differs")
    scenes = {str(workers[worker_id]["scene"]) for worker_id in requested}
    if len(requested) > 4 or len(scenes) != 1:
        raise RuntimeError("mixed four-worker B reset crossed a scene boundary")
    scoped = {worker_id: workers[worker_id] for worker_id in requested}
    BASE_RESET_XP(freeze, scoped, output, token, worker_ids=requested)
    workers.update(scoped)

    # A device-wide quiet wait would make independently advancing scenes wait
    # on siblings sharing an SSD.  Flush each newly created data plane instead
    # and record completion without introducing that prohibited dependency.
    stage = output / "xp_resets" / token
    sync_rows: list[dict[str, Any]] = []
    for worker_id in sorted(requested):
        datadir = Path(str(workers[worker_id]["datadir"])).resolve()
        # The MySQL image changes datadir ownership to its in-container UID.
        # Opening that mode-0750 directory from the unprivileged controller is
        # therefore denied even though its worker root is controller-owned.
        # sync(2) on the worker root issues syncfs for the same backing
        # filesystem and keeps the settle scene-scoped (not device-wide).
        syncfs_anchor = datadir.parent
        completed = subprocess.run(
            ["sync", "-f", str(syncfs_anchor)], capture_output=True, text=True,
            check=False, timeout=300,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"{worker_id} post-reset syncfs failed")
        sync_rows.append({
            "worker_id": worker_id, "datadir": str(datadir),
            "syncfs_anchor": str(syncfs_anchor),
            "syncfs_returncode": completed.returncode,
        })
    evidence = {
        "schema_version": "mysql_mixed_disk_scene_scoped_post_reset_sync_v1",
        "status": "PASS", "created_at": settle_v32.runtime.alg.now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "workers": sync_rows, "scene_scoped": True,
        "device_wide_quiet_wait": False,
        "sibling_scene_completion_dependency": False,
        "shared_nvme_mutual_exclusion": False,
    }
    settle_v32.runtime.alg.atomic(stage / "post_reset_storage_settle.json", evidence)


def validate_contract(alg: Any) -> dict[str, Any]:
    contract = alg.load(CONTRACT)
    if (
        contract.get("schema_version")
        != "mysql_algorithm_b_four_scene_top4_mixed_four_worker_contract_v1"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("candidate_batches") != [[1, 2, 3, 4], [5]]
        or contract.get("physical_replay_parallelism") != 4
        or contract.get("outer_acceptance_policy") != qps_policy.policy_contract()
        or contract.get("outer_rejection_cycle_policy") != rejection_cycle.policy_contract()
        or contract.get("four_scene_barrier") is not False
        or contract.get("sibling_scene_wait") is not False
        or contract.get("lease_mode") != "none"
    ):
        raise RuntimeError("Top4-B mixed four-worker contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        if not isinstance(binding, Mapping):
            raise RuntimeError("Top4-B mixed four-worker binding is not an object")
        path = ROOT / str(binding.get("path") or "")
        if not path.is_file() or path.is_symlink() or alg.sha(path) != binding.get("sha256"):
            raise RuntimeError(f"Top4-B mixed four-worker binding differs: {path}")
    return contract


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = BOOTSTRAP_CONTRACT_ROOT
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    runtime = impl["runtime"]
    no_lease = impl["no_lease_runtime"]
    impl["TOP4_TABLES"] = dict(TABLES)
    runtime_globals = core._implementation_main.__globals__
    runtime_globals["rejection_cycle"] = rejection_cycle
    impl["rejection_cycle"] = rejection_cycle
    original_state_document = impl["state_document"]

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document.update({
            "formal_candidate_batches": [[1, 2, 3, 4], [5]],
            "candidate_parallelism": 4, "physical_replay_parallelism": 4,
            "sibling_scene_wait": False, "other_scene_completion_required": False,
            "four_scene_wave_barrier": False,
        })
        document["xp_top4_parameter_release"].update({
            "contract_path": str(CONTRACT), "mixed_four_worker_successor": True,
        })
        document["freeze_path"] = str(FREEZE)
        document["freeze_sha256"] = alg.sha(FREEZE)
        document["runtime_preflight_path"] = str(authority.PREFLIGHT)
        document["runtime_preflight_sha256"] = alg.sha(authority.PREFLIGHT)
        document["prepared_binding_path"] = str(authority.PREPARED)
        document["prepared_binding_sha256"] = alg.sha(authority.PREPARED)
        document["benchmark_accounts_path"] = str(authority.BENCHMARK_ACCOUNTS)
        document["benchmark_accounts_sha256"] = alg.sha(authority.BENCHMARK_ACCOUNTS)
        return document

    def configure_runtime() -> Mapping[str, Any]:
        root = authority_root()
        contract = alg.load(COORDINATION)
        if (
            contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
            or contract.get("schema_version")
            != "mysql_algorithm_b_top4_mixed_four_worker_no_lease_coordination_v1"
            or contract.get("lease_mode") != "none"
            or contract.get("sibling_scene_wait") is not False
            or contract.get("maximum_workers_per_scene") != 4
            or float(contract.get("maximum_idle_load1", 0.0)) != 512.0
        ):
            raise RuntimeError("Top4-B mixed four-worker coordination differs")
        reset_mixed.configure(freeze=FREEZE, prepared=root / "prepared_binding.json")
        alg.FREEZE = FREEZE
        alg.reset = reset_mixed
        alg.r0mod = authority
        alg.WORKER_IDS = tuple(
            f"{prefix}_w{index}"
            for prefix in ("job", "tpch", "xp_cpu", "xp_io")
            for index in range(1, 5)
        )
        alg.XP_IDS = tuple(
            f"{prefix}_w{index}"
            for prefix in ("xp_cpu", "xp_io")
            for index in range(1, 5)
        )
        alg.XP_RESET_MAX_WORKERS = 4
        # The predecessor requests a 300-second graceful-quiescence value,
        # while the sealed adapter authority accepts at most 120 seconds.
        # The original four-lane runtime installed this compatibility adapter
        # in its configure hook; the mixed-SSD hook replaces that hook and
        # therefore must install the same adapter explicitly.
        alg.run_hybench = xp_adapter_v3.run_hybench_adapter_compatible
        alg.measure_batch = independent_v5.measure_batch
        authority.configure(
            freeze=FREEZE, freeze_schema=FREEZE_SCHEMA,
            authority_root=root, scene=impl["SCENES"][0],
        )
        runtime.alg.reset_xp = scene_scoped_reset
        no_lease.alg = alg
        no_lease.coordination = gate_v14
        runtime.WAVE_COORDINATION_CONTRACT = COORDINATION
        return contract

    def validate_release(scene: str) -> dict[str, Any]:
        contract = validate_contract(alg)
        expected = list(alg.load(impl["TOP4_ROOT"] / TABLES[scene])[
            "parameter_space"
        ]["active_params"])
        if (contract.get("active_parameters_by_scene") or {}).get(scene) != expected:
            raise RuntimeError("Top4-B mixed four-worker active parameter set differs")
        return contract

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["FREEZE"] = FREEZE
    impl["PHYSICAL_REPLAY_PARALLELISM"] = 4
    impl["scheduler"] = scheduler_4x
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
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
