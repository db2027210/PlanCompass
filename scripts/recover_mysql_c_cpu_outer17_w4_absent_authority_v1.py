#!/usr/bin/env python3
"""Rebuild the exact absent CPU-C w4 plane and seal a fresh four-worker authority."""

from __future__ import annotations

import argparse
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
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v2 as reset


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_four_worker_20260828_v3"
    / "four_scene_effective_resource_successor.json"
)
SCENE = "hybench_xp_cpu"
EXPECTED = {f"xp_cpu_w{i}" for i in range(1, 5)}
TARGET = "xp_cpu_w4"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-reset", type=Path, required=True)
    parser.add_argument("--failed-state", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prior_path = args.prior_reset.resolve()
    failed_path = args.failed_state.resolve()
    checkpoint_path = args.checkpoint.resolve()
    output = args.output.resolve()
    if HISTORY.resolve() not in output.parents or output.exists() or output.is_symlink():
        raise RuntimeError("fresh recovery output beneath formal history required")
    freeze = load(FREEZE)
    prior_document = load(prior_path)
    failed = load(failed_path)
    checkpoint = load(checkpoint_path)
    frozen_all = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
    }
    frozen = {key: value for key, value in frozen_all.items() if key in EXPECTED}
    prior = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prior_document.get("workers") or []
        if row.get("worker_id") in EXPECTED
    }
    target_container = str(frozen.get(TARGET, {}).get("container") or "")
    absent = reset._docker_run(
        ["docker", "ps", "-aq", "--filter", f"name=^{target_container}$"],
        timeout=60,
    ).stdout.strip()
    if (
        freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or prior_document.get("status") != "PASS"
        or prior_document.get("freeze_sha256") != sha(FREEZE)
        or set(frozen) != EXPECTED
        or set(prior) != EXPECTED
        or failed.get("status") != "FAILED_CLOSED"
        or "docker', 'inspect', 'ra_pgssd_mysql_baseline_tpch_w2_20260826'"
        not in str(failed.get("error") or "")
        or int(checkpoint.get("completed_outer_attempts") or 0) != 17
        or checkpoint.get("formal_scope") != "mysql_only"
        or absent
    ):
        raise RuntimeError("CPU-C w4 absent recovery authority differs")

    xp_rows = {
        key: value for key, value in frozen_all.items()
        if key.startswith(("xp_cpu_", "xp_io_"))
    }
    allowlist = {key: str(value["container"]) for key, value in xp_rows.items()}
    reset._impl.update({
        "XP_IDS": set(xp_rows),
        "XP_CONTAINER_ALLOWLIST": allowlist,
    })
    output.mkdir(parents=True, mode=0o700)
    reset_stage = output / "xp_resets/w4_absent_rebuild"
    quarantine = output / "discard_quarantine"
    rebuilt = reset._impl["_prepare_fresh_xp"](
        freeze, frozen[TARGET], prior[TARGET], quarantine, reset_stage,
        True, "formal_point", True, False, False, True,
    )
    live = {key: copy.deepcopy(value) for key, value in prior.items()}
    live[TARGET] = rebuilt
    for worker_id in sorted(EXPECTED):
        row = live[worker_id]
        inspected = reset._impl["_docker_json"]("inspect", str(row["container"]))
        state = inspected.get("State") or {}
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspected.get("Mounts") or []
        }
        defaults = Path(str(row["credential_defaults"]))
        benchmark = Path(str(row["benchmark_defaults"]))
        if (
            state.get("Running") is not True
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or mounts.get("/var/lib/mysql") != str(row["datadir"])
            or not defaults.is_file()
            or not benchmark.is_file()
        ):
            raise RuntimeError(f"{worker_id} recovered live identity differs")
        sessions = reset._impl["_host_mysql"](
            row, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'", timeout=30,
        ).stdout.strip()
        identity = reset._impl["_host_mysql"](
            row, benchmark, "SELECT CURRENT_USER(),DATABASE();", timeout=30,
        ).stdout.strip().split("\t")
        if sessions != "0" or not identity or not identity[0].startswith("ra_benchmark@"):
            raise RuntimeError(f"{worker_id} recovered account identity differs")
        row.update({
            "ready": True,
            "container_id": str(inspected["Id"]),
            "container_pid": int(state.get("Pid") or 0),
            "benchmark_user": "ra_benchmark",
            "external_current_user": identity[0],
            "external_database": "hybench_sf10x",
            "database": "hybench_sf10x",
            "kernel_cgroup_resources": reset._impl["prep"]._kernel_resource_readback(
                inspected, frozen[worker_id]
            ),
            "blkio_authority": reset._impl["prep"]._enforce_blkio(
                inspected, freeze, frozen[worker_id]
            ),
            "passwords_emitted": False,
        })

    handoff.dynamic_gate = gate_v17
    artifacts = handoff.authority_artifacts(
        scene=SCENE, freeze_path=FREEZE, freeze=freeze,
        live_rows=live, output=output / "authority",
    )
    result = {
        "schema_version": "mysql_c_cpu_outer17_w4_absent_authority_recovery_v1",
        "status": "PASS", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_a_candidate_qps_range_or_ranking_prior_used": False,
        "candidate_measurements_reused": 0,
        "accepted_or_rejected_credit_changed": False,
        "completed_outer_attempts_preserved": 17,
        "accepted_rounds_preserved": 6,
        "source_prior_reset": {"path": str(prior_path), "sha256": sha(prior_path)},
        "source_failed_state": {"path": str(failed_path), "sha256": sha(failed_path)},
        "source_checkpoint": {"path": str(checkpoint_path), "sha256": sha(checkpoint_path)},
        "rebuilt_worker": TARGET,
        "rebuilt_worker_evidence": rebuilt.get("worker_evidence_path"),
        "artifacts": {key: str(value) for key, value in artifacts.items()},
    }
    atomic(output / "recovery_manifest.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
