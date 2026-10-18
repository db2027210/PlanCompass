#!/usr/bin/env python3
"""Resume only CPU-w1 and IO-w3 from two interrupted C coverage resets."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v3 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
OUTPUT = HISTORY / "v1015_c_xp_dual_partial_coverage_reset_recovery_20260828"
CONFIG = {
    "hybench_xp_cpu": {
        "prefix": "xp_cpu",
        "missing": "xp_cpu_w1",
        "outer": 17,
        "accepted": 6,
        "error": (
            "TimeoutExpired: Command '['docker', 'start', "
            "'ra_mysql_disk2_algc_xp_cpu_w1_20260809']' timed out after 120 seconds"
        ),
        "failed_state": HISTORY / "v1013_to_c_cpu_outer17_stale_recovery_resume_20260828_xp_cpu/run_state.json",
        "checkpoint": HISTORY / "v992_to_c_cpu_outer17_docker_timeout_checkpoint_20260828/prepared_binding.json",
        "prior": HISTORY / "v1011_c_cpu_outer17_stale_triple_authority_recovery_20260828/authority/prepared_binding.json",
        "partial_stage": HISTORY / "v1013_to_c_cpu_outer17_stale_recovery_resume_20260828_xp_cpu/outer_018/current_interval_plan_coverage/xp_resets/outer_018_current_interval_plan_coverage_1",
        "token": "outer_018_current_interval_plan_coverage_1",
    },
    "hybench_xp_io": {
        "prefix": "xp_io",
        "missing": "xp_io_w3",
        "outer": 12,
        "accepted": 5,
        "error": (
            "TimeoutExpired: Command '['sudo', '-n', 'chown', '999:999', "
            "'/disk2/resource_adaptive_mysql_algorithm_c_cpu_a_slots_v3/"
            "xp_io_w3/credentials/reset-init.sql']' timed out after 30 seconds"
        ),
        "failed_state": HISTORY / "v1009_to_c_io_outer12_docker_timeout_resume_20260828_xp_io/run_state.json",
        "checkpoint": HISTORY / "v1006_to_c_io_outer12_docker_timeout_checkpoint_20260828/prepared_binding.json",
        "prior": HISTORY / "v903_to_c_io_four_worker_transition_20260828/live_authority_v6/prepared_binding.json",
        "partial_stage": HISTORY / "v1009_to_c_io_outer12_docker_timeout_resume_20260828_xp_io/outer_013/current_interval_plan_coverage/xp_resets/outer_013_current_interval_plan_coverage_1",
        "token": "outer_013_current_interval_plan_coverage_1",
    },
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return handoff.sha(path)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
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


def validate_live(
    *, freeze: Mapping[str, Any], frozen: Mapping[str, Mapping[str, Any]],
    rows: dict[str, dict[str, Any]],
) -> None:
    for worker_id, worker in sorted(frozen.items()):
        row = rows[worker_id]
        inspected = reset._impl["_docker_json"]("inspect", str(worker["container"]))
        state = inspected.get("State") or {}
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        credential_root = Path(mounts.get("/run/ra-credentials") or "")
        defaults = credential_root / "selected.cnf"
        benchmark = credential_root / "benchmark.cnf"
        if (
            state.get("Running") is not True
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or mounts.get("/var/lib/mysql") != str(worker["runtime_root"] + "/datadir")
            or not defaults.is_file()
            or not benchmark.is_file()
        ):
            raise RuntimeError(f"{worker_id} recovered live identity differs")
        sessions = reset._impl["_host_mysql"](
            dict(worker), defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'", timeout=30,
        ).stdout.strip()
        identity = reset._impl["_host_mysql"](
            dict(worker), benchmark,
            "SELECT CURRENT_USER(),DATABASE(),"
            "(SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x');", timeout=30,
        ).stdout.strip().split("\t")
        if (
            sessions != "0" or len(identity) != 3
            or not identity[0].startswith("ra_benchmark@") or identity[2] != "8"
        ):
            raise RuntimeError(f"{worker_id} recovered account identity differs")
        row.update({
            **copy.deepcopy(dict(worker)), "ready": True,
            "container_id": str(inspected["Id"]),
            "container_pid": int(state.get("Pid") or 0),
            "datadir": mounts["/var/lib/mysql"],
            "credential_defaults": str(defaults),
            "benchmark_defaults": str(benchmark),
            "benchmark_user": "ra_benchmark", "external_current_user": identity[0],
            "external_database": "hybench_sf10x", "database": "hybench_sf10x",
            "table_count": 8,
            "kernel_cgroup_resources": reset._impl["prep"]._kernel_resource_readback(
                inspected, dict(worker)
            ),
            "blkio_authority": reset._impl["prep"]._enforce_blkio(
                inspected, freeze, dict(worker)
            ),
            "passwords_emitted": False,
        })


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh recovery output required: {OUTPUT}")
    freeze = load(FREEZE)
    frozen_all = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
    }
    xp_all = {
        key: value for key, value in frozen_all.items()
        if key.startswith(("xp_cpu_", "xp_io_"))
    }
    if (
        freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or len(xp_all) != 8
    ):
        raise RuntimeError("dual recovery freeze differs")
    values = {
        "AUTHORITY_VERSION": "algorithm_c_v82_dual_partial_coverage_recovery",
        "OUTPUT_SCHEMA": "mysql_algorithm_c_v82_dual_partial_coverage_reset_v1",
        "XP_IDS": set(xp_all),
        "XP_CONTAINER_ALLOWLIST": {
            key: str(value["container"]) for key, value in xp_all.items()
        },
        "RESET_SAME_FREEZE": True,
    }
    reset._impl.update(copy.deepcopy(values))
    OUTPUT.mkdir(parents=True, mode=0o700)
    scene_results: dict[str, Any] = {}
    for scene, raw in CONFIG.items():
        config = dict(raw)
        prefix = str(config["prefix"])
        expected = {f"{prefix}_w{i}" for i in range(1, 5)}
        missing = str(config["missing"])
        failed_state = load(Path(config["failed_state"]))
        checkpoint = load(Path(config["checkpoint"]))
        prior_document = load(Path(config["prior"]))
        partial_stage = Path(config["partial_stage"])
        frozen = {key: xp_all[key] for key in expected}
        prior = {
            str(row["worker_id"]): copy.deepcopy(dict(row))
            for row in prior_document.get("workers") or []
            if row.get("worker_id") in expected
        }
        sibling_ids = expected - {missing}
        sibling_paths = {
            worker_id: partial_stage / worker_id / "reset_worker.json"
            for worker_id in sibling_ids
        }
        if (
            failed_state.get("status") != "FAILED_CLOSED"
            or failed_state.get("stage") != "FAILED"
            or failed_state.get("error") != config["error"]
            or int(failed_state.get("outer_attempt") or -1) != int(config["outer"])
            or int((failed_state.get("accepted_rounds") or {}).get(scene) or 0)
            != int(config["accepted"])
            or int(checkpoint.get("completed_outer_attempts") or -1)
            != int(config["outer"])
            or int((checkpoint.get("reconstructed_state", {}).get("accepted_rounds") or {}).get(scene) or 0)
            != int(config["accepted"])
            or checkpoint.get("freeze_sha256") != sha(FREEZE)
            or prior_document.get("freeze_sha256") != sha(FREEZE)
            or set(prior) != expected
            or any(not path.is_file() for path in sibling_paths.values())
            or (partial_stage / missing / "reset_worker.json").exists()
        ):
            raise RuntimeError(f"{scene} partial coverage recovery lineage differs")
        rows = {worker_id: load(path) for worker_id, path in sibling_paths.items()}
        for worker_id, path in sibling_paths.items():
            rows[worker_id]["worker_evidence_path"] = str(path)
            rows[worker_id]["worker_evidence_sha256"] = sha(path)
        quarantine = (
            Path(str(freeze["storage"]["runtime_root"]))
            / "quarantine/current_interval_plan_coverage" / str(config["token"])
        )
        target_stage = OUTPUT / scene / "resumed_missing_worker"
        rows[missing] = reset._prepare_fresh_xp(
            freeze, frozen[missing], prior[missing], quarantine, target_stage,
            True, "formal_point", True, False, False, False,
        )
        validate_live(freeze=freeze, frozen=frozen, rows=rows)
        handoff.dynamic_gate = gate_v17
        artifacts = handoff.authority_artifacts(
            scene=scene, freeze_path=FREEZE, freeze=freeze,
            live_rows=rows, output=OUTPUT / scene / "authority",
        )
        scene_results[scene] = {
            "status": "PASS",
            "completed_outer_attempts_preserved": int(config["outer"]),
            "accepted_rounds_preserved": int(config["accepted"]),
            "resumed_worker_ids": [missing],
            "retained_completed_sibling_worker_ids": sorted(sibling_ids),
            "candidate_measurements_reused": 0,
            "algorithm_a_role": "independent_posthoc_check_only",
            "algorithm_a_feedback_to_b_or_c": False,
            "algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c": False,
            "failed_state": {"path": str(config["failed_state"]), "sha256": sha(Path(config["failed_state"]))},
            "checkpoint": {"path": str(config["checkpoint"]), "sha256": sha(Path(config["checkpoint"]))},
            "partial_stage": str(partial_stage),
            "artifacts": {key: str(value) for key, value in artifacts.items()},
        }
        atomic(OUTPUT / scene / "recovery_manifest.json", scene_results[scene])
    manifest = {
        "schema_version": "mysql_c_xp_dual_partial_coverage_reset_recovery_v1",
        "status": "PASS", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_a_role": "independent_posthoc_check_only",
        "algorithm_a_feedback_to_b_or_c": False,
        "algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c": False,
        "scenes": scene_results,
    }
    atomic(OUTPUT / "recovery_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

