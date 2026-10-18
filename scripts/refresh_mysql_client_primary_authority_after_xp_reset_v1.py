#!/usr/bin/env python3
"""Seal a read-only v20 identity successor after a technical XP reset.

This does not stop, recreate, update, or otherwise mutate any container.  It
verifies all eight live MySQL data planes against the v20 resource envelope,
then refreshes only the container identity fields invalidated by an XP reset.
The already-passed 60-second non-interference preflight is inherited by hash.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
FREEZE_SCHEMA = "mysql_four_scene_two_worker_client_primary_successor_v20"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def inspect(container: str) -> dict[str, Any]:
    completed = subprocess.run(
        ["docker", "inspect", container], cwd=ROOT, capture_output=True,
        text=True, check=True, timeout=300,
    )
    rows = json.loads(completed.stdout)
    if not isinstance(rows, list) or len(rows) != 1:
        raise RuntimeError(f"Docker inspect cardinality differs: {container}")
    return dict(rows[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-authority-root", type=Path, required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    source = args.source_authority_root.expanduser().resolve()
    if HISTORY.resolve() not in source.parents or source.is_symlink():
        parser.error("source authority must be a regular history directory")
    output = HISTORY / args.run_id / "authority"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh authority output required: {output}")

    freeze = load(FREEZE)
    source_preflight = source / "transition_preflight.json"
    source_prepared = source / "prepared_binding.json"
    source_accounts = source / "benchmark_accounts_binding.json"
    preflight = load(source_preflight)
    prepared = load(source_prepared)
    accounts = load(source_accounts)
    if (
        freeze.get("schema_version") != FREEZE_SCHEMA
        or preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or float(preflight.get("observed_seconds") or 0) < 60.0
        or preflight.get("freeze_sha256") != sha(FREEZE)
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != sha(FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != sha(FREEZE)
    ):
        raise RuntimeError("source v20 authority differs")

    frozen = {str(row["worker_id"]): dict(row) for row in freeze["workers"]}
    old_prepared = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in prepared["workers"]
    }
    old_accounts = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in accounts["workers"]
    }
    if set(frozen) != set(old_prepared) or set(frozen) != set(old_accounts):
        raise RuntimeError("source authority worker set differs")

    refreshed_prepared: list[dict[str, Any]] = []
    refreshed_accounts: list[dict[str, Any]] = []
    readbacks: list[dict[str, Any]] = []
    for worker_id in sorted(frozen):
        worker = frozen[worker_id]
        current = inspect(str(worker["container"]))
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
        runtime = Path(str(freeze["storage"]["runtime_root"])) / worker_id
        if (
            differences or state.get("Running") is not True
            or state.get("Restarting") is True
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or labels.get("ra.worker_id") != worker_id
            or mounts.get("/var/lib/mysql") != str(runtime / "datadir")
            or mounts.get("/run/ra-credentials") != str(runtime / "credentials")
        ):
            raise RuntimeError(
                f"{worker_id} live v20 resource identity differs: {differences}"
            )
        probe = subprocess.run(
            ["docker", "exec", str(worker["container"]), "mysql",
             "--defaults-file=/run/ra-credentials/selected.cnf", "-Nse", "SELECT 1"],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=60,
        )
        if probe.returncode != 0 or probe.stdout.strip() != "1":
            raise RuntimeError(f"{worker_id} MySQL liveness differs")
        identity = {
            "container_id": str(current["Id"]),
            "container_pid": int(state.get("Pid") or 0),
        }
        prepared_row = {**old_prepared[worker_id], **identity}
        prepared_row["technical_identity_refresh_after_xp_reset"] = True
        account_row = {**old_accounts[worker_id], **identity}
        account_row["account_mutation_performed_in_this_run"] = False
        account_row["technical_identity_refresh_after_xp_reset"] = True
        refreshed_prepared.append(prepared_row)
        refreshed_accounts.append(account_row)
        readbacks.append({
            "worker_id": worker_id, "scene": worker["scene"],
            "container": worker["container"], **identity,
            "resource_differences": differences,
            "memory_swappiness": host.get("MemorySwappiness"),
            "mysql_liveness": True,
        })

    output.mkdir(parents=True, mode=0o700)
    preflight_path = output / "transition_preflight.json"
    inherited_preflight = copy.deepcopy(preflight)
    inherited_preflight.update({
        "schema_version": "mysql_client_primary_identity_refresh_preflight_v1",
        "inherited_preflight_path": str(source_preflight),
        "inherited_preflight_sha256": sha(source_preflight),
        "identity_refresh_is_read_only": True,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    atomic(preflight_path, inherited_preflight)

    prepared_path = output / "prepared_binding.json"
    prepared_document = copy.deepcopy(prepared)
    prepared_document.update({
        "schema_version": "mysql_client_primary_identity_refresh_prepared_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "preflight_path": str(preflight_path),
        "preflight_sha256": sha(preflight_path),
        "workers": refreshed_prepared,
        "technical_recovery_only": True,
        "candidate_or_acceptance_state_reused": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    })
    atomic(prepared_path, prepared_document)

    accounts_path = output / "benchmark_accounts_binding.json"
    accounts_document = copy.deepcopy(accounts)
    accounts_document.update({
        "schema_version": "mysql_client_primary_identity_refresh_accounts_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "prepared_binding_path": str(prepared_path),
        "prepared_binding_sha256": sha(prepared_path),
        "workers": refreshed_accounts,
        "passwords_emitted": False,
        "technical_recovery_only": True,
    })
    atomic(accounts_path, accounts_document)
    result = {
        "schema_version": "mysql_client_primary_identity_refresh_authority_v1",
        "status": "PASS", "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "freeze": {"path": str(FREEZE), "sha256": sha(FREEZE)},
        "preflight": {"path": str(preflight_path), "sha256": sha(preflight_path)},
        "prepared": {"path": str(prepared_path), "sha256": sha(prepared_path)},
        "accounts": {"path": str(accounts_path), "sha256": sha(accounts_path)},
        "readbacks": readbacks,
        "source_authority_root": str(source),
        "source_authority_sha256": sha(source / "authority_result.json"),
        "container_mutation_performed": False,
        "external_process_control_or_stop_performed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    atomic(output / "authority_result.json", result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
