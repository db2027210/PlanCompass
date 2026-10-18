#!/usr/bin/env python3
"""Convert four completed memory slots and prepare one eight-worker XP authority."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import reset_mysql_xp_data_planes_mixed_eight_worker_v1 as reset8


FREEZE = ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/xp_effective_resource_successor.json"
LEGACY_PREPARED = ROOT / "history/mysql_four_scene_20260809/four_scene_live_rebind_v26_20260811T143500p0800/prepared_binding.json"
JOB_PREPARED = ROOT / "history/mysql_four_scene_20260809/v502_to_b_job_mixed_four_worker_authority_v71_20260827/prepared_binding.json"
TPCH_PREPARED = ROOT / "history/mysql_four_scene_20260809/v503_to_b_tpch_mixed_four_worker_authority_v71_20260827/prepared_binding.json"
PREFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def parse_cpuset(value: str) -> set[int]:
    result: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            result.update(range(int(left), int(right) + 1))
        else:
            result.add(int(token))
    return result


def prepare(scene: str, current_reset: Path, output: Path) -> dict[str, Any]:
    freeze = load(FREEZE)
    current = load(current_reset)
    legacy_documents = [load(path) for path in (LEGACY_PREPARED, JOB_PREPARED, TPCH_PREPARED)]
    prefix = PREFIX[scene]
    expected_current = {f"{prefix}_w{i}" for i in range(1, 5)}
    current_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in current.get("workers") or []
        if row.get("worker_id") in expected_current
    }
    if current.get("status") != "PASS" or set(current_rows) != expected_current:
        raise RuntimeError(f"{scene} current four-worker reset binding differs")
    frozen_rows = {str(row["worker_id"]): copy.deepcopy(dict(row)) for row in freeze.get("workers") or []}
    if len(frozen_rows) != 16:
        raise RuntimeError("eight-worker freeze cardinality differs")
    reset8.install_for_workers(frozen_rows)
    legacy_rows: dict[str, dict[str, Any]] = {}
    for document in legacy_documents:
        for row in document.get("workers") or []:
            if (
                row.get("ready") is False
                or row.get("authority_placeholder_for_other_scene") is True
                or not row.get("container_id")
            ):
                continue
            legacy_rows[str(row["worker_id"])] = copy.deepcopy(dict(row))
    additions: dict[str, dict[str, Any]] = {}
    quarantine = output / "discard_quarantine"
    reset_output = output / "new_worker_resets"

    def one(worker_id: str) -> dict[str, Any]:
        frozen = copy.deepcopy(frozen_rows[worker_id])
        source_id = str(frozen.get("reassigned_from_completed_scene_worker_id") or "")
        prior = copy.deepcopy(legacy_rows.get(source_id) or {})
        if str(prior.get("container") or "") != str(frozen.get("container") or ""):
            raise RuntimeError(f"{worker_id} completed-slot predecessor differs")
        storage = dict((freeze.get("storage_by_worker") or {}).get(worker_id) or freeze["storage"])
        old_target = (
            Path(str(storage["runtime_root"])) / "quarantine"
            / output.name / "discard_quarantine" / worker_id
        )
        impl = reset8.base._impl
        existing = impl["_docker_run"](
            ["docker", "ps", "-aq", "--filter", f"name=^{frozen['container']}$"],
            timeout=60,
        ).stdout.strip()
        if existing and old_target.is_dir():
            inspect = impl["_docker_json"]("inspect", str(frozen["container"]))
            labels = (inspect.get("Config") or {}).get("Labels") or {}
            state = inspect.get("State") or {}
            runtime_root = Path(str(frozen["runtime_root"]))
            datadir = runtime_root / "datadir"
            defaults = runtime_root / "credentials/selected.cnf"
            benchmark = runtime_root / "credentials/benchmark.cnf"
            mounts = {
                str(row.get("Destination") or ""): str(row.get("Source") or "")
                for row in inspect.get("Mounts") or []
            }
            if (
                state.get("Running") is True
                and labels.get("ra.worker_id") == worker_id
                and labels.get("ra.owner") == "mysql_four_scene_v5"
                and mounts.get("/var/lib/mysql") == str(datadir)
                and defaults.is_file() and benchmark.is_file()
            ):
                readback = impl["_host_mysql"](
                    frozen, defaults,
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='hybench_sf10x';",
                    timeout=60,
                ).stdout.strip()
                if readback != "8":
                    raise RuntimeError(f"{worker_id} resumed XP schema differs")
                row = {
                    **prior, **frozen, "ready": True,
                    "container_id": inspect["Id"],
                    "container_pid": int(state.get("Pid") or 0),
                    "datadir": str(datadir),
                    "credential_defaults": str(defaults),
                    "benchmark_defaults": str(benchmark),
                    "database": "hybench_sf10x", "table_count": 8,
                    "kernel_cgroup_resources": impl["prep"]._kernel_resource_readback(inspect, frozen),
                    "blkio_authority": impl["prep"]._enforce_blkio(inspect, freeze, frozen),
                    "quarantined_predecessor_runtime_root": str(old_target),
                    "quarantined_invalid_runtime_root": str(old_target),
                    "staged_physical_copy_reused": True,
                    "resumed_after_parallel_sibling_failure": True,
                    "passwords_emitted": False,
                }
                worker_output = reset_output / worker_id
                worker_output.mkdir(parents=True, exist_ok=True)
                evidence = worker_output / "reset_worker.json"
                atomic(evidence, row)
                row["worker_evidence_path"] = str(evidence)
                row["worker_evidence_sha256"] = sha(evidence)
                return row
        return reset8.prepare_fresh_worker(
            freeze=freeze, worker=frozen, prior=prior,
            quarantine_root=quarantine, output_root=reset_output,
            reset_context="formal_point",
            resume_stalled_copy=old_target.is_dir(),
        )

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"expand-{prefix}") as pool:
        futures = {pool.submit(one, f"{prefix}_w{i}"): f"{prefix}_w{i}" for i in range(5, 9)}
        for future in as_completed(futures):
            additions[futures[future]] = future.result()
    live_rows = {**current_rows, **additions}
    if set(live_rows) != {f"{prefix}_w{i}" for i in range(1, 9)}:
        raise RuntimeError(f"{scene} eight-worker live set differs")
    target_cpus = set().union(*(
        parse_cpuset(str(row["database_cpuset"])) | parse_cpuset(str(row["client_cpuset"]))
        for row in live_rows.values()
    ))
    rolling = gate_v17.rolling_quiet_gate(
        run_id=f"mysql-b-xp8-expand-{prefix}-{os.getpid()}",
        required_seconds=60.0, maximum_wait_seconds=600.0,
        maximum_load1=512.0, target_cpus=target_cpus,
    )
    if rolling.get("status") != "PASS":
        raise RuntimeError(f"{scene} eight-worker rolling gate failed closed")
    all_rows = []
    for frozen in freeze.get("workers") or []:
        worker_id = str(frozen["worker_id"])
        all_rows.append(copy.deepcopy(live_rows[worker_id]) if worker_id in live_rows else {
            **copy.deepcopy(dict(frozen)), "ready": False,
            "authority_placeholder_for_other_scene": True,
        })
    common = {
        "status": "PASS", "created_at": now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority_scene_scope": scene,
        "freeze_path": str(FREEZE), "freeze_sha256": sha(FREEZE),
        "formal_launch_authorized": True,
        "rolling_dynamic_noninterference_gate": rolling,
        "observed_seconds": float(rolling.get("observed_seconds") or 0.0),
        "external_process_control_or_stop_performed": False,
        "completed_candidate_measurements_replayed": False,
        "accepted_round_history_preserved": True,
    }
    preflight = output / "transition_preflight.json"
    atomic(preflight, {**common, "schema_version": "mysql_algorithm_b_xp8_transition_preflight_v1"})
    prepared = output / "prepared_binding.json"
    atomic(prepared, {
        **common, "status": "PREPARED",
        "schema_version": "mysql_algorithm_b_xp8_transition_prepared_v1",
        "preflight_path": str(preflight), "preflight_sha256": sha(preflight),
        "workers": all_rows, "workload_started": False,
        "fresh_r0_complete": False, "formal_outer_started": False,
    })
    accounts = output / "benchmark_accounts_binding.json"
    atomic(accounts, {
        **common, "schema_version": "mysql_algorithm_b_xp8_transition_accounts_v1",
        "prepared_binding_path": str(prepared), "prepared_binding_sha256": sha(prepared),
        "workers": [{**copy.deepcopy(row), "passwords_emitted": False} for row in all_rows],
        "passwords_emitted": False, "workload_started": False,
    })
    manifest = output / "expansion_manifest.json"
    atomic(manifest, {
        **common, "schema_version": "mysql_algorithm_b_xp8_boundary_expansion_v1",
        "scene": scene, "source_reset_binding": str(current_reset),
        "source_reset_binding_sha256": sha(current_reset),
        "worker_ids": sorted(live_rows), "physical_replay_parallelism": 8,
        "new_worker_ids": sorted(additions),
        "preflight_path": str(preflight), "prepared_binding_path": str(prepared),
        "benchmark_accounts_path": str(accounts),
    })
    return {"status": "PASS", "manifest": str(manifest), "workers": sorted(live_rows)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", choices=sorted(PREFIX), required=True)
    parser.add_argument("--current-reset", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = prepare(args.scene, Path(args.current_reset).resolve(), Path(args.output).resolve())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
