#!/usr/bin/env python3
"""Recover exact CPU-C w2-w4 stale containerd tasks and seal fresh authority."""

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
TARGETS = {"xp_cpu_w2", "xp_cpu_w3", "xp_cpu_w4"}


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
    parser.add_argument("--stopped-state", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prior_path = args.prior_reset.resolve()
    state_path = args.stopped_state.resolve()
    checkpoint_path = args.checkpoint.resolve()
    output = args.output.resolve()
    if HISTORY.resolve() not in output.parents or output.exists() or output.is_symlink():
        raise RuntimeError("fresh recovery output beneath formal history required")
    freeze = load(FREEZE)
    prior_document = load(prior_path)
    stopped_state = load(state_path)
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
    if (
        freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
        or prior_document.get("status") != "PASS"
        or prior_document.get("freeze_sha256") != sha(FREEZE)
        or set(frozen) != EXPECTED
        or set(prior) != EXPECTED
        or stopped_state.get("status") != "RUNNING"
        or stopped_state.get("stage") != "OUTER_018_FALLBACK_PLAN_CHANGE_WAVE_1_RESETTING_XP"
        or int(stopped_state.get("outer_attempt") or 0) != 17
        or int((stopped_state.get("accepted_rounds") or {}).get(SCENE) or 0) != 6
        or int(checkpoint.get("completed_outer_attempts") or 0) != 17
        or checkpoint.get("formal_scope") != "mysql_only"
    ):
        raise RuntimeError("CPU-C stale triple recovery lineage differs")
    xp_rows = {
        key: value for key, value in frozen_all.items()
        if key.startswith(("xp_cpu_", "xp_io_"))
    }
    reset._impl.update({
        "XP_IDS": set(xp_rows),
        "XP_CONTAINER_ALLOWLIST": {
            key: str(value["container"]) for key, value in xp_rows.items()
        },
    })
    removed: list[dict[str, Any]] = []
    for worker_id in sorted(TARGETS):
        worker = frozen[worker_id]
        inspected = reset._impl["_docker_json"]("inspect", str(worker["container"]))
        state = inspected.get("State") or {}
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        host = inspected.get("HostConfig") or {}
        mounts = {
            str(row.get("Destination") or ""): str(row.get("Source") or "")
            for row in inspected.get("Mounts") or []
        }
        if (
            state.get("Running") is not False
            or int(state.get("Pid") or 0) != 0
            or "already exists" not in str(state.get("Error") or "")
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or str(host.get("CpusetCpus") or "") != str(worker["database_cpuset"])
            or mounts.get("/var/lib/mysql") != str(worker["runtime_root"] + "/datadir")
        ):
            raise RuntimeError(f"{worker_id} stale task evidence differs")
        removed.append({
            "worker_id": worker_id, "container": worker["container"],
            "container_id": inspected["Id"], "state_error": state.get("Error"),
            "datadir_preserved": mounts["/var/lib/mysql"],
        })
        reset._impl["_docker_run"](
            ["docker", "rm", "-f", str(worker["container"])], timeout=300,
        )
    output.mkdir(parents=True, mode=0o700)
    stage = output / "xp_resets/stale_triple_rebuild"
    quarantine = output / "discard_quarantine"
    live = {key: copy.deepcopy(value) for key, value in prior.items()}
    rebuilt: dict[str, dict[str, Any]] = {}
    for worker_id in sorted(TARGETS):
        row = prior[worker_id]
        prior_id = str(row.get("container_id") or "")
        prior_cgroup = Path(str((row.get("blkio_authority") or {}).get("cgroup_path") or ""))
        if (
            len(prior_id) != 64
            or prior_cgroup != Path("/sys/fs/cgroup/blkio/docker-limited") / prior_id
            or prior_cgroup.exists()
        ):
            raise RuntimeError(f"{worker_id} absent predecessor cgroup differs")
        rebuilt[worker_id] = reset._impl["_prepare_fresh_xp"](
            freeze, frozen[worker_id], row, quarantine, stage,
            True, "formal_point", True, False, False, True,
        )
        live[worker_id] = rebuilt[worker_id]
    for worker_id in sorted(EXPECTED):
        worker = frozen[worker_id]
        row = live[worker_id]
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
            worker, defaults,
            "SELECT COUNT(*) FROM information_schema.processlist "
            "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
            "AND USER<>'event_scheduler'", timeout=30,
        ).stdout.strip()
        identity = reset._impl["_host_mysql"](
            worker, benchmark,
            "SELECT CURRENT_USER(),DATABASE(),"
            "(SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x');", timeout=30,
        ).stdout.strip().split("\t")
        if sessions != "0" or len(identity) != 3 or not identity[0].startswith("ra_benchmark@") or identity[2] != "8":
            raise RuntimeError(f"{worker_id} recovered account identity differs")
        row.update({
            **worker, "ready": True, "container_id": str(inspected["Id"]),
            "container_pid": int(state.get("Pid") or 0),
            "datadir": mounts["/var/lib/mysql"],
            "credential_defaults": str(defaults),
            "benchmark_defaults": str(benchmark),
            "benchmark_user": "ra_benchmark", "external_current_user": identity[0],
            "external_database": "hybench_sf10x", "database": "hybench_sf10x",
            "table_count": 8,
            "kernel_cgroup_resources": reset._impl["prep"]._kernel_resource_readback(
                inspected, worker
            ),
            "blkio_authority": reset._impl["prep"]._enforce_blkio(
                inspected, freeze, worker
            ),
            "passwords_emitted": False,
        })
    handoff.dynamic_gate = gate_v17
    artifacts = handoff.authority_artifacts(
        scene=SCENE, freeze_path=FREEZE, freeze=freeze,
        live_rows=live, output=output / "authority",
    )
    result = {
        "schema_version": "mysql_c_cpu_outer17_stale_triple_authority_recovery_v1",
        "status": "PASS", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_a_candidate_qps_range_or_ranking_prior_used": False,
        "candidate_measurements_reused": 0,
        "completed_outer_attempts_preserved": 17,
        "accepted_rounds_preserved": 6,
        "removed_stale_containers": removed,
        "rebuilt_worker_ids": sorted(rebuilt),
        "source_prior_reset": {"path": str(prior_path), "sha256": sha(prior_path)},
        "source_stopped_state": {"path": str(state_path), "sha256": sha(state_path)},
        "source_checkpoint": {"path": str(checkpoint_path), "sha256": sha(checkpoint_path)},
        "artifacts": {key: str(value) for key, value in artifacts.items()},
    }
    atomic(output / "recovery_manifest.json", result)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
