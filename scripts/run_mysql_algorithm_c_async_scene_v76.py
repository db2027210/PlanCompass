#!/usr/bin/env python3
"""Resume monotonic C with four XP workers on mixed idle CPU-A slots."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_async_xp_multi_ssd_post_reset_settle_v2 as settle
from scripts import mysql_cross_project_wave_coordination_cpu_a_slots_v13 as slot_gate
from scripts import mysql_single_scene_measurement_v1 as single_measurement
from scripts import preflight_mysql_four_scene_cpu_a_slots_v10 as slot_preflight
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v2 as mixed_reset
from scripts import run_mysql_four_scene_r0_cpu_a_slots_four_worker_v1 as r0_four
from scripts import run_mysql_algorithm_c_async_scene_v74 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v34 as boundary_v34


FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v28"
)
PARENT_CONTRACT = CONTRACT_ROOT / "algorithm_c_async_contract.json"
OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "outer_runtime_optimization.json"
CONTRACT = CONTRACT_ROOT / "global_monotonic_incumbent_contract.json"


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def cli_value(name: str) -> str:
    try:
        index = sys.argv.index(name)
        value = sys.argv[index + 1]
    except (ValueError, IndexError) as error:
        raise RuntimeError(f"required controller argument absent: {name}") from error
    if not value or value.startswith("--"):
        raise RuntimeError(f"required controller argument differs: {name}")
    return value


def validate_contract() -> dict[str, Any]:
    alg = predecessor.predecessor.alg
    parent = alg.load(PARENT_CONTRACT)
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    contract = alg.load(CONTRACT)
    freeze = alg.load(FREEZE)
    if (
        parent.get("release_version") != "v73"
        or parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
        or parent.get("candidate_batches") != [[1, 2, 3, 4], [5]]
        or parent.get("four_scene_wave_barrier") is not False
        or parent.get("sibling_scene_wait") is not False
        or parent.get("lease_mode") != "none"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or mapping(optimization.get("bindings"), "optimization bindings").get(
            "resource_freeze_sha256"
        ) != alg.sha(FREEZE)
        or contract.get("schema_version")
        != "mysql_algorithm_c_global_monotonic_incumbent_contract_v1"
        or contract.get("release_version") != "v76"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("parent_contract", {}).get("sha256") != alg.sha(PARENT_CONTRACT)
        or contract.get("resume_invariant", {}).get(
            "next_reference_equals_previous_selected_incumbent"
        ) is not True
        or freeze.get("schema_version")
        != "mysql_four_scene_cpu_a_slots_four_worker_successor_v3"
        or len(freeze.get("workers") or []) != 12
        or (freeze.get("xp_capacity_successor") or {}).get(
            "physical_workers_per_xp_scene_after"
        ) != 4
        or (freeze.get("xp_capacity_successor") or {}).get("four_scene_barrier") is not False
    ):
        raise RuntimeError("Algorithm C v76 CPU-A slot contract differs")
    for document, label in (
        (parent, "parent"), (contract, "successor"),
    ):
        for name, raw in mapping(document.get("source_bindings"), f"{label} bindings").items():
            row = mapping(raw, f"{label} {name}")
            path = (ROOT / str(row.get("path") or "")).resolve()
            if not path.is_file() or path.is_symlink() or alg.sha(path) != row.get("sha256"):
                raise RuntimeError(f"Algorithm C v76 source binding differs: {name}")
    return {"parent": parent, "optimization": optimization, "successor": contract}


def install_mixed_runtime() -> None:
    freeze = predecessor.predecessor.alg.load(FREEZE)
    all_worker_ids = tuple(
        str(row["worker_id"]) for row in freeze.get("workers") or []
    )
    xp = {
        str(row["worker_id"]): dict(row)
        for row in freeze.get("workers") or []
        if str(row.get("scene")) in {"hybench_xp_cpu", "hybench_xp_io"}
    }
    expected = {
        "xp_cpu_w1", "xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4",
        "xp_io_w1", "xp_io_w2", "xp_io_w3", "xp_io_w4",
    }
    if set(xp) != expected:
        raise RuntimeError("Algorithm C v76 XP worker set differs")
    values = {
        "AUTHORITY_VERSION": "algorithm_c_v76_cpu_a_slots_mixed_reset",
        "OUTPUT_SCHEMA": "mysql_algorithm_c_v76_cpu_a_slots_reset_binding_v1",
        "XP_IDS": set(expected),
        "XP_CONTAINER_ALLOWLIST": {
            worker_id: str(row["container"]) for worker_id, row in xp.items()
        },
        "RESET_SAME_FREEZE": True,
    }
    for name, value in values.items():
        setattr(mixed_reset, name, value)
        mixed_reset.install.__globals__[name] = copy.deepcopy(value)
    mixed_reset._impl.update(copy.deepcopy(values))
    mixed_reset.install()
    # The daemon can serialize inspect behind concurrent physical resets for
    # several minutes.  Preserve fail-closed behavior but do not misclassify
    # a delayed read-only inspect as a reset failure at the old 240 s bound.
    mixed_reset._impl["DOCKER_CONTROL_TIMEOUT_SECONDS"] = 600
    predecessor.predecessor.alg.reset = mixed_reset
    predecessor.predecessor.runtime.alg.reset = mixed_reset
    predecessor.predecessor.alg.WORKER_IDS = all_worker_ids
    predecessor.predecessor.alg.XP_IDS = tuple(sorted(expected))
    single_measurement.async_xp_settle = settle


def main() -> int:
    validate_contract()
    c74 = predecessor
    v73 = predecessor.predecessor
    v62 = v73.predecessor.predecessor
    old = {
        "c74_contract": c74.CONTRACT,
        "c74_parent": c74.PARENT_CONTRACT,
        "c74_validate": c74.validate_contract,
        "v73_async": v73.ASYNC_CONTRACT,
        "v73_optimization": v73.OPTIMIZATION_CONTRACT,
        "r0_preflight_source": v73.alg.r0mod.PREFLIGHT_SOURCE,
        "v62_preflight_module_file": v73.predecessor.predecessor.preflight_v8.__file__,
        "v62_install": v62.install,
        "v73_gate": v73.gate_v11,
        "boundary_v34_reset": boundary_v34.reset_v7,
        "single_settle": single_measurement.async_xp_settle,
        "formal_candidate_batches": v73.alg.FORMAL_CANDIDATE_BATCHES,
        "worker_ids": v73.alg.WORKER_IDS,
        "xp_ids": v73.alg.XP_IDS,
        "r0mod": v73.alg.r0mod,
        "boundary_worker_count": v73.runtime.INDEPENDENT_SCENE_WORKER_COUNT,
        "boundary_candidate_batches": v73.runtime.FORMAL_CANDIDATE_BATCHES,
        "checkpoint_incumbent_baseline_reuse": (
            v73.runtime.REUSE_CHECKPOINT_INCUMBENT_BASELINE
        ),
        "resume_workers_per_scene": (
            v73.runtime.predecessor.FROZEN_WORKERS_PER_SCENE
        ),
    }
    c74.CONTRACT = CONTRACT
    c74.PARENT_CONTRACT = PARENT_CONTRACT
    c74.validate_contract = validate_contract
    v73.ASYNC_CONTRACT = PARENT_CONTRACT
    v73.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v73.alg.r0mod = r0_four
    r0_four.configure_runtime_authority(
        freeze=FREEZE,
        freeze_schema="mysql_four_scene_cpu_a_slots_four_worker_successor_v3",
        runtime_preflight=Path(cli_value("--runtime-preflight")),
        prepared=Path(cli_value("--prepared-binding")),
        benchmark_accounts=Path(cli_value("--benchmark-accounts")),
        preflight_source=Path(slot_preflight.__file__),
        scene=cli_value("--scene"),
    )
    v73.alg.r0mod.PREFLIGHT_SOURCE = Path(slot_preflight.__file__).resolve()
    v73.predecessor.predecessor.preflight_v8.__file__ = slot_preflight.__file__
    def install_v75_preflight_after_v62() -> None:
        old["v62_install"]()
        v73.alg.r0mod.PREFLIGHT_SOURCE = Path(slot_preflight.__file__).resolve()
    v62.install = install_v75_preflight_after_v62
    v73.gate_v11 = slot_gate
    boundary_v34.reset_v7 = mixed_reset
    install_mixed_runtime()
    v73.alg.FORMAL_CANDIDATE_BATCHES = ((1, 2, 3, 4), (5,))
    v73.runtime.INDEPENDENT_SCENE_WORKER_COUNT = 4
    v73.runtime.FORMAL_CANDIDATE_BATCHES = ((1, 2, 3, 4), (5,))
    v73.runtime.REUSE_CHECKPOINT_INCUMBENT_BASELINE = True
    v73.runtime.predecessor.FROZEN_WORKERS_PER_SCENE = 4
    try:
        return int(c74.main())
    finally:
        c74.CONTRACT = old["c74_contract"]
        c74.PARENT_CONTRACT = old["c74_parent"]
        c74.validate_contract = old["c74_validate"]
        v73.ASYNC_CONTRACT = old["v73_async"]
        v73.OPTIMIZATION_CONTRACT = old["v73_optimization"]
        v73.alg.r0mod.PREFLIGHT_SOURCE = old["r0_preflight_source"]
        v73.predecessor.predecessor.preflight_v8.__file__ = old["v62_preflight_module_file"]
        v62.install = old["v62_install"]
        v73.gate_v11 = old["v73_gate"]
        boundary_v34.reset_v7 = old["boundary_v34_reset"]
        single_measurement.async_xp_settle = old["single_settle"]
        v73.alg.FORMAL_CANDIDATE_BATCHES = old["formal_candidate_batches"]
        v73.alg.WORKER_IDS = old["worker_ids"]
        v73.alg.XP_IDS = old["xp_ids"]
        v73.alg.r0mod = old["r0mod"]
        v73.runtime.INDEPENDENT_SCENE_WORKER_COUNT = old["boundary_worker_count"]
        v73.runtime.FORMAL_CANDIDATE_BATCHES = old["boundary_candidate_batches"]
        v73.runtime.REUSE_CHECKPOINT_INCUMBENT_BASELINE = old[
            "checkpoint_incumbent_baseline_reuse"
        ]
        v73.runtime.predecessor.FROZEN_WORKERS_PER_SCENE = old[
            "resume_workers_per_scene"
        ]


if __name__ == "__main__":
    raise SystemExit(main())
