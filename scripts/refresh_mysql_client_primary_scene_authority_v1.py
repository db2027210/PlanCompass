#!/usr/bin/env python3
"""Refresh only one completed scene while retaining the frozen 8-worker cohort."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import prepare_mysql_four_scene_v4 as prepare
from scripts import refresh_mysql_client_primary_authority_after_xp_reset_v1 as base


HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = base.FREEZE
SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--source-authority-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,180}", args.run_id) is None:
        parser.error("unsafe run id")
    source = args.source_authority_root.expanduser().resolve()
    if HISTORY.resolve() not in source.parents or source.is_symlink():
        parser.error("source authority must be a regular history directory")
    output = HISTORY / args.run_id / "authority"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh scene authority output required: {output}")

    freeze = load(FREEZE)
    preflight = load(source / "transition_preflight.json")
    prepared = load(source / "prepared_binding.json")
    accounts = load(source / "benchmark_accounts_binding.json")
    if (
        preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or float(preflight.get("observed_seconds") or 0.0) < 60.0
        or preflight.get("freeze_sha256") != base.sha(FREEZE)
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != base.sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != base.sha(FREEZE)
    ):
        raise RuntimeError("source authority differs")
    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    prepared_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prepared["workers"]
    }
    account_rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in accounts["workers"]
    }
    if set(frozen) != set(prepared_rows) or set(frozen) != set(account_rows):
        raise RuntimeError("scene authority worker cohort differs")
    target_ids = sorted(
        worker_id for worker_id, row in frozen.items()
        if str(row.get("scene") or "") == args.scene
    )
    if len(target_ids) != 2:
        raise RuntimeError("scene authority requires exactly two workers")

    readbacks: list[dict[str, Any]] = []
    rebound: list[str] = []
    for worker_id in target_ids:
        worker = frozen[worker_id]
        current = base.inspect(str(worker["container"]))
        host = dict(current.get("HostConfig") or {})
        state = dict(current.get("State") or {})
        labels = dict((current.get("Config") or {}).get("Labels") or {})
        mounts = {
            str(row.get("Destination") or ""): str(row.get("Source") or "")
            for row in current.get("Mounts") or []
        }
        expected = {
            "CpusetCpus": str(worker["database_cpuset"]),
            "CpusetMems": str(worker["database_cpuset_mems"]),
            "NanoCpus": int(round(float(worker["cpu_quota_cores"]) * 1_000_000_000)),
            "Memory": int(worker["memory_bytes"]),
            "MemorySwappiness": 0,
            "PidsLimit": int(worker["pids_max"]),
        }
        differences = {
            key: {"actual": host.get(key), "expected": value}
            for key, value in expected.items() if host.get(key) != value
        }
        runtime_root = Path(str(freeze["storage"]["runtime_root"])) / worker_id
        if (
            differences or state.get("Running") is not True
            or state.get("Restarting") is True
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or mounts.get("/var/lib/mysql") != str(runtime_root / "datadir")
            or mounts.get("/run/ra-credentials") != str(runtime_root / "credentials")
        ):
            raise RuntimeError(f"{worker_id} live scene identity differs: {differences}")
        probe = subprocess.run([
            "docker", "exec", str(worker["container"]), "mysql",
            "--defaults-file=/run/ra-credentials/selected.cnf", "-Nse", "SELECT 1",
        ], cwd=ROOT, capture_output=True, text=True, timeout=60, check=False)
        if probe.returncode != 0 or probe.stdout.strip() != "1":
            raise RuntimeError(f"{worker_id} MySQL liveness differs")
        expected_cgroup = (
            Path("/sys/fs/cgroup/blkio/docker-limited") / str(current["Id"])
        )
        blkio = dict(prepared_rows[worker_id].get("blkio_authority") or {})
        if blkio.get("cgroup_path") != str(expected_cgroup):
            blkio = prepare._enforce_blkio(current, freeze, worker)
            rebound.append(worker_id)
        identity = {
            "container_id": str(current["Id"]),
            "container_pid": int(state.get("Pid") or 0),
        }
        prepared_rows[worker_id].update(identity)
        prepared_rows[worker_id]["blkio_authority"] = blkio
        prepared_rows[worker_id]["scene_scoped_identity_refresh"] = args.scene
        account_rows[worker_id].update(identity)
        account_rows[worker_id]["scene_scoped_identity_refresh"] = args.scene
        readbacks.append({
            "worker_id": worker_id,
            "scene": args.scene,
            "container": worker["container"],
            **identity,
            "resource_differences": differences,
            "mysql_liveness": True,
            "blkio_cgroup_path": str(blkio.get("cgroup_path") or ""),
        })

    output.mkdir(parents=True, mode=0o700)
    preflight_path = output / "transition_preflight.json"
    inherited = copy.deepcopy(preflight)
    inherited.update({
        "schema_version": "mysql_client_primary_scene_refresh_preflight_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "scene_scope": args.scene,
        "source_preflight_path": str(source / "transition_preflight.json"),
        "source_preflight_sha256": base.sha(source / "transition_preflight.json"),
        "sibling_scene_identity_refresh_required": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    base.atomic(preflight_path, inherited)

    prepared_path = output / "prepared_binding.json"
    prepared_doc = copy.deepcopy(prepared)
    prepared_doc.update({
        "schema_version": "mysql_client_primary_scene_refresh_prepared_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "preflight_path": str(preflight_path),
        "preflight_sha256": base.sha(preflight_path),
        "workers": [prepared_rows[key] for key in sorted(prepared_rows)],
        "scene_scope": args.scene,
        "refreshed_worker_ids": target_ids,
        "sibling_scene_identity_refresh_required": False,
        "workload_started": False,
        "fresh_r0_complete": False,
        "formal_outer_started": False,
    })
    base.atomic(prepared_path, prepared_doc)

    accounts_path = output / "benchmark_accounts_binding.json"
    accounts_doc = copy.deepcopy(accounts)
    accounts_doc.update({
        "schema_version": "mysql_client_primary_scene_refresh_accounts_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": base.sha(prepared_path),
        "workers": [account_rows[key] for key in sorted(account_rows)],
        "scene_scope": args.scene,
        "refreshed_worker_ids": target_ids,
        "passwords_emitted": False,
        "workload_started": False,
    })
    base.atomic(accounts_path, accounts_doc)

    result = {
        "schema_version": "mysql_client_primary_scene_refresh_authority_v1",
        "status": "PASS",
        "created_at": datetime.now().astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene_scope": args.scene,
        "refreshed_worker_ids": target_ids,
        "sibling_scene_identity_refresh_required": False,
        "freeze": {"path": str(FREEZE), "sha256": base.sha(FREEZE)},
        "preflight": {"path": str(preflight_path), "sha256": base.sha(preflight_path)},
        "prepared": {"path": str(prepared_path), "sha256": base.sha(prepared_path)},
        "accounts": {"path": str(accounts_path), "sha256": base.sha(accounts_path)},
        "readbacks": readbacks,
        "blkio_rebound_worker_ids": rebound,
        "source_authority_root": str(source),
        "source_authority_sha256": base.sha(source / "authority_result.json"),
        "container_mutation_performed": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    base.atomic(output / "authority_result.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
