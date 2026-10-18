#!/usr/bin/env python3
"""Produce one fresh full-host process zero-conflict preflight on node04."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import time


HERE = Path(__file__).resolve().parent
EXPECTED = {
    "job_memory": ("25-27,61-63", "28-31,64-67"),
    "tpch_memory": ("18-20,54-56", "0-7,36-43"),
    "tpcc_cpu": ("21-24,57-60", "8-15,44-51"),
}


class InventoryError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_module(name):
    path = HERE / name
    sys.path.insert(0, str(HERE))
    try:
        spec = importlib.util.spec_from_file_location("fresh_inventory_core", path)
        if spec is None or spec.loader is None:
            raise InventoryError("collector core load failed")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def load_readback(path):
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if (path.is_symlink() or not path.is_file() or sidecar.is_symlink()
            or not sidecar.is_file()
            or sidecar.read_text(encoding="ascii")
            != sha256(path) + "  " + path.name + "\n"):
        raise InventoryError("fresh readback sidecar differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    scenario = value.get("scenario")
    expected = EXPECTED.get(scenario)
    if (value.get("status") != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
            or value.get("hostname") != "node04" or expected is None
            or (value.get("resource_envelope") or {}).get("cpuset_cpus") != expected[0]
            or (value.get("client_resource_envelope") or {}).get("cpuset_cpus") != expected[1]
            or time.time() - float(value.get("collected_epoch") or 0) > 120):
        raise InventoryError("readback is stale or envelope differs")
    return value


def produce(bundle, readback_path, output):
    if socket.gethostname() != "node04" or os.geteuid() != 0:
        raise InventoryError("node04 host root is required")
    bundle = Path(bundle).resolve(strict=True)
    manifest = json.loads((bundle / "bundle_manifest.json").read_text())
    if (bundle.name != manifest.get("bundle_id")
            or manifest.get("formal_scope") != "mysql_only"
            or manifest.get("cross_engine_comparison_prohibited") is not True
            or not isinstance(manifest.get("files"), dict)):
        raise InventoryError("target bundle identity differs")
    for relative, expected_hash in manifest["files"].items():
        path = bundle / relative
        if path.is_symlink() or not path.is_file() or sha256(path) != expected_hash:
            raise InventoryError("target bundle closure differs: " + relative)
    readback = load_readback(readback_path)
    if readback.get("bundle_id") != manifest["bundle_id"]:
        raise InventoryError("readback does not bind target bundle")
    scenario = readback["scenario"]
    core_name = ("mysql_job_distributed_live_readback_qualification_v2.py"
                 if scenario == "job_memory"
                 else "mysql_distributed_live_readback_qualification_v1.py")
    core = load_module(core_name)
    target_pid = int(readback["container"]["pid"])
    transport = readback["database_counter_transport"]
    client_pid = int(transport["client_pid"])
    docker_containers, root_evidence = core._docker_container_inventory()
    inventory = core.rolling_process_inventory(
        target_pid, client_pid, os.getpid(), readback["storage"]["major_minor"],
        readback["resource_envelope"]["cpuset_cpus"],
        readback["client_resource_envelope"]["cpuset_cpus"],
        readback["client_resource_envelope"]["cpuset_mems"],
        docker_containers=docker_containers,
        root_docker_mount_evidence=root_evidence,
    )
    if (inventory.get("inventory_complete") is not True
            or inventory.get("conflicts") != []
            or inventory.get("permission_failure_count") != 0
            or inventory.get("race_count") != 0
            or inventory.get("foreign_writer_count") != 0):
        raise InventoryError("fresh full-process inventory conflicts or is incomplete")
    if scenario == "job_memory":
        if (inventory.get("target_infrastructure_daemon_count") != 1
                or inventory.get("target_infrastructure_is_foreign_writer") is not False):
            raise InventoryError("JOB dedicated daemon inventory differs")
    elif (inventory.get("target_dedicated_docker_daemon_verified") is not True
          or int(inventory.get("target_dedicated_docker_daemon_pid") or 0) <= 1):
        raise InventoryError("shared dedicated daemon inventory differs")
    value = {
        "schema_version": "mysql_unified_fresh_full_process_inventory_v1",
        "status": "PASS_FRESH_FULL_PROCESS_ZERO_CONFLICT_NOT_LAUNCH_AUTHORITY",
        "collected_epoch": time.time(), "hostname": "node04",
        "scenario": scenario, "bundle_id": manifest["bundle_id"],
        "readback_sha256": sha256(readback_path), "inventory": inventory,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_performed": False, "formal_launch_authorized": False,
        "qualification_credit": 0, "r0_credit": 0, "accepted_rounds_credit": 0,
    }
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise InventoryError("refusing existing process inventory output")
    raw = canonical(value)
    output.write_bytes(raw)
    output.with_name(output.name + ".sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "  " + output.name + "\n",
        encoding="ascii")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--readback", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(produce(args.bundle, args.readback, args.output), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (InventoryError, OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
