#!/usr/bin/env python3
"""Freeze fresh four-scene Algorithm C with v20 client-primary semantics."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PARENT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260826_v25/"
    "algorithm_c_async_contract.json"
)
SOURCE_OPTIMIZATION = SOURCE_PARENT.with_name("outer_runtime_optimization.json")
SOURCE_GLOBAL = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_qps_20260827_v26/"
    "global_monotonic_incumbent_contract.json"
)
OUT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260903_v90"
)
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v1.py",
    "global_monotonic_predecessor": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v74.py",
    "two_reject_and_empty_wave_predecessor": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v73.py",
    "two_worker_no_lease_installer": ROOT / "scripts/run_mysql_algorithm_c_async_scene_v62.py",
    "algorithm_core": ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py",
    "client_primary_measurement": ROOT / "scripts/mysql_algorithm_c_measurement_client_primary_v1.py",
    "xp_reset": ROOT / "scripts/reset_mysql_xp_data_planes_client_primary_v3.py",
    "run_scoped_reset_token": ROOT / "scripts/mysql_xp_reset_run_scoped_token_v1.py",
    "strict_qps_outer": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "two_reject_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "global_chain_auditor": ROOT / "scripts/audit_mysql_algorithm_c_global_incumbent_chain_v1.py",
    "resource_freeze": FREEZE,
    "fresh_bootstrap_builder": ROOT / "scripts/build_mysql_algorithm_c_client_primary_bootstrap_v1.py",
    "fresh_checkpoint": ROOT / "history/mysql_four_scene_20260809/v1185_to_c_client_primary_bootstrap_20260903/checkpoint/prepared_binding.json",
    "empty_replay_quarantine": ROOT / "history/mysql_four_scene_20260809/v1185_to_c_client_primary_bootstrap_20260903/replay_quarantine/replay_quarantine.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def bindings() -> dict[str, dict[str, str]]:
    return {
        name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
        for name, path in SOURCES.items()
    }


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh v90 release required: {OUT}")
    parent = json.loads(SOURCE_PARENT.read_text(encoding="utf-8"))
    optimization = json.loads(SOURCE_OPTIMIZATION.read_text(encoding="utf-8"))
    successor = json.loads(SOURCE_GLOBAL.read_text(encoding="utf-8"))
    created = datetime.now().astimezone().isoformat()
    parent.update({
        "created_at": created,
        "release_version": "v90-client-primary",
        "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
        "xp_server_questions_qps_role": "audit_only",
        "xp_metric_policy": "mysql_xp_client_xap_qps_outer_primary_v1",
        "job_tpch_metric_semantics_changed": False,
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "all_four_scenes_started_together": True,
        "source_bindings": bindings(),
    })
    optimization["created_at"] = created
    optimization["release_version"] = "v90-client-primary"
    optimization["bindings"].update({
        "reset_source_path": str(SOURCES["xp_reset"].relative_to(ROOT)),
        "reset_source_sha256": sha(SOURCES["xp_reset"]),
        "resource_freeze_path": str(FREEZE.relative_to(ROOT)),
        "resource_freeze_sha256": sha(FREEZE),
    })
    optimization["xp_outer_primary_metric"] = (
        "native_hybench_xp_client_xap_qps"
    )
    optimization["xp_server_questions_qps_role"] = "audit_only"
    optimization["job_tpch_metric_semantics_changed"] = False
    OUT.mkdir(parents=True, mode=0o700)
    atomic(OUT / "algorithm_c_async_contract.json", parent)
    atomic(OUT / "outer_runtime_optimization.json", optimization)

    successor.update({
        "created_at": created,
        "release_version": "v90-client-primary",
        "parent_contract": {
            "path": str((OUT / "algorithm_c_async_contract.json").relative_to(ROOT)),
            "sha256": sha(OUT / "algorithm_c_async_contract.json"),
        },
        "outer_runtime_optimization": {
            "path": str((OUT / "outer_runtime_optimization.json").relative_to(ROOT)),
            "sha256": sha(OUT / "outer_runtime_optimization.json"),
        },
        "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
        "xp_server_questions_qps_role": "audit_only",
        "xp_metric_policy": "mysql_xp_client_xap_qps_outer_primary_v1",
        "job_tpch_metric_semantics_changed": False,
        "source_bindings": bindings(),
        "full_parallel_launch": {
            "all_four_scenes_started_together": True,
            "physical_workers_per_scene": 2,
            "maximum_concurrent_mysql_workers": 8,
            "four_scene_wave_barrier": False,
            "sibling_scene_wait": False,
            "lease_mode": "none",
        },
        "algorithm_a_prior_policy": {
            "algorithm_a_parameters_or_results_used_for_generation": False,
            "algorithm_a_used_only_after_completion_for_result_check": True,
        },
    })
    atomic(OUT / "global_monotonic_incumbent_contract.json", successor)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "root": str(OUT),
        "parent_sha256": sha(OUT / "algorithm_c_async_contract.json"),
        "optimization_sha256": sha(OUT / "outer_runtime_optimization.json"),
        "contract_sha256": sha(OUT / "global_monotonic_incumbent_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
