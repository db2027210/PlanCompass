#!/usr/bin/env python3
"""Independently verify the canonical AP MySQL envelope-only evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / "history/mysql_sgd_env_agents_20260723/ap/formal_mysql_normalized_envelope_readback.json"
BUNDLE = ROOT / "history/mysql_sgd_env_agents_20260723/ap/ap_envelope_probe_20260722T223955Z"
PAIRED = ROOT / "config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json"
READINESS = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/readiness_manifest.json"
HOST = "anonymous@192.0.2.3"
PROJECT = "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap"
RUN = "ap_envelope_probe_20260722T223955Z"


class VerificationError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def obj(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise VerificationError(f"not an object: {path}")
    return value


def sidecar(path: Path) -> None:
    check = path.with_name(path.name + ".sha256")
    if check.read_text(encoding="ascii") != f"{sha(path)}  {path.name}\n":
        raise VerificationError(f"sidecar mismatch: {path}")


def bundle() -> int:
    count = 0
    for line in (BUNDLE / "checksums.sha256").read_text().splitlines():
        digest, separator, name = line.partition("  ")
        path = (BUNDLE / name.removeprefix("./")).resolve()
        if not separator or path.parent != BUNDLE.resolve() or not path.is_file() or path.is_symlink() or sha(path) != digest:
            raise VerificationError(f"bad bundle member: {name}")
        count += 1
    if count != 16:
        raise VerificationError(f"bundle member count changed: {count}")
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-unpromoted-readiness", action="store_true")
    args = parser.parse_args(argv)
    try:
        sidecar(CANON); sidecar(READINESS)
        document = obj(CANON); paired = obj(PAIRED); profile = paired["profiles"]["memory"]
        if (
            document.get("status") != "MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY"
            or document.get("scenario") != "hybench_ap_memory" or document.get("engine") != "mysql"
            or document.get("profile") != "memory" or document.get("freeze_id") != paired["freeze_id"]
            or document.get("normalized_envelope") != profile or document.get("requested_effective_equal") is not True
        ):
            raise VerificationError("canonical identity/profile mismatch")
        count = bundle(); sources = document["source_evidence"]
        if sources.get("directory") != str(BUNDLE.relative_to(ROOT)):
            raise VerificationError("source directory mismatch")
        names = {
            "checksums_sha256": "checksums.sha256", "normalized_envelope_sha256": "normalized_envelope.json",
            "live_cgroup_readback_sha256": "live_cgroup_readback.json", "mysql_inspect_running_sha256": "mysql_inspect_running.json",
            "mysql_inspect_stopped_sha256": "mysql_inspect_stopped.json", "mysql_readback_sha256": "mysql_readback.tsv",
            "mysql_log_sha256": "mysql.log", "native_mysql_idle_sha256": "native_mysql_idle_before.json",
            "native_mysql_pidstat_sha256": "native_mysql_pidstat.txt", "postgres_before_sha256": "postgres_before.txt",
            "postgres_after_sha256": "postgres_after.txt", "postgres_containers_before_sha256": "postgres_containers_before.jsonl",
            "postgres_containers_after_sha256": "postgres_containers_after.jsonl", "summary_sha256": "summary.json",
        }
        for field, name in names.items():
            if sources.get(field) != sha(BUNDLE / name):
                raise VerificationError(f"source mismatch: {name}")
        live = obj(BUNDLE / "live_cgroup_readback.json"); normalized = obj(BUNDLE / "normalized_envelope.json")
        if normalized.get("normalized_envelope") != profile or normalized.get("status") != "PASS":
            raise VerificationError("normalization mismatch")
        if (
            (live["cpu_quota_us"], live["cpu_period_us"], live["cpuset_cpus"], live["cpuset_mems"], live["memory_limit_bytes"], live["memory_memsw_limit_bytes"], live["pids_max"])
            != (300000, 100000, "24-26,60-62", "1", 6442450944, 6442450944, 512)
            or live["blkio_read_bps_lines"] != ["259:1 1205862400"]
            or live["blkio_write_bps_lines"] != ["259:1 1205862400"]
        ):
            raise VerificationError("live effective envelope mismatch")
        running = json.loads((BUNDLE / "mysql_inspect_running.json").read_text())[0]
        stopped = json.loads((BUNDLE / "mysql_inspect_stopped.json").read_text())[0]; host = running["HostConfig"]
        if (
            running["Id"] != document["container_id"] or running["State"]["Running"] is not True
            or stopped["Id"] != running["Id"] or stopped["State"]["Running"] is not False
            or stopped["State"]["ExitCode"] != 0 or stopped["State"]["OOMKilled"] is not False
            or (host["NanoCpus"], host["Memory"], host["MemorySwap"], host["PidsLimit"], host["CpusetCpus"], host["CpusetMems"])
                != (3000000000, 6442450944, 6442450944, 512, "24-26,60-62", "1")
            or host["BlkioDeviceReadBps"] != [{"Path": "/dev/nvme0n1", "Rate": 1205862400}]
            or host["BlkioDeviceWriteBps"] != [{"Path": "/dev/nvme0n1", "Rate": 1205862400}]
            or "Shutdown complete" not in (BUNDLE / "mysql.log").read_text()
        ):
            raise VerificationError("Docker/readback/clean-shutdown mismatch")
        mounts = {item["Destination"]: item for item in running["Mounts"]}
        if (
            mounts["/var/lib/mysql"]["Source"] != document["storage_scope"]["session_owned_datadir"]
            or document["storage_scope"]["paths_distinct"] is not True
            or document["storage_scope"]["sealed_template_started"] is not False
        ):
            raise VerificationError("session/sealed storage scope mismatch")
        idle = obj(BUNDLE / "native_mysql_idle_before.json")
        if (
            idle.get("pressure_conflict") is not False or idle.get("isolation_complete") is not False
            or idle.get("io_read_kib_per_second") != 0 or idle.get("io_write_kib_per_second") != 0
            or idle.get("established_connections") != 0 or idle.get("cpu_ticks_delta", 99) > 20
        ):
            raise VerificationError("native mysqld low-pressure/fail-closed evidence mismatch")
        if any((BUNDLE / name).read_bytes() for name in ("postgres_before.txt", "postgres_after.txt", "postgres_containers_before.jsonl", "postgres_containers_after.jsonl")):
            raise VerificationError("PostgreSQL count was not zero")
        if document["static_isolation"] != {
            "postgresql_counterpart_present": False, "postgresql_counterpart_envelope_proven": False,
            "native_mysql_isolation_complete": False, "cross_engine_envelope_equality": False,
            "formal_static_isolation_gate_passed": False,
        }:
            raise VerificationError("static/paired gates are not fail-closed")
        readiness = obj(READINESS); item = readiness["scenarios"]["hybench_ap_memory"]
        expected = {"freeze_id": paired["freeze_id"], "normalized": dict(profile), "readback": {"path": str(CANON.relative_to(ROOT)), "sha256": sha(CANON)}}
        projected = item.get("mysql_envelope") == expected
        if not projected and not (args.allow_unpromoted_readiness and item.get("mysql_envelope") == {"freeze_id": None, "normalized": None, "readback": None}):
            raise VerificationError("AP readiness projection is neither exact nor unpromoted")
        if item.get("postgresql_envelope") != {"freeze_id": None, "normalized": None, "readback": None} or item.get("static_isolation") != {"evidence": None, "passed": False} or item.get("r0_calibration", {}).get("passed") is not False:
            raise VerificationError("AP PG/static/R0 gate was promoted")
        remote = f'''set -euo pipefail; p={PROJECT}; e=$p/envelope_evidence/{RUN}; (cd "$e"&&sha256sum -c checksums.sha256 >/dev/null); sha256sum "$e/checksums.sha256"; test ! -e "$p/locks/host01_mysql_lane.owner.json"; exec 9>"$p/locks/host01_mysql_lane.lock"; flock -n 9; test -d "$p/production_binding_trace_20260723_0630/datadir"; test -d "$p/mysql_template_data"; test "$(docker inspect mysql_hybench_ap_formal --format '{{{{.State.Running}}}}')" = false; test -z "$(docker ps -a --format '{{{{.Names}}}}'|grep '^ra_mysql_ap01_envelope_20260722T223955Z$'||true)"; test -z "$(docker network ls --format '{{{{.Name}}}}'|grep '^ra_mysql_ap01_envelope_20260722T223955Z_net$'||true)"'''
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", HOST, "bash", "-s"], input=remote, text=True, capture_output=True)
        if result.returncode or not result.stdout.split() or result.stdout.split()[0] != sources["checksums_sha256"]:
            raise VerificationError("remote evidence/cleanup verification failed")
        print(json.dumps({"status": "PASS", "bundle_members": count, "eight_effective_fields": True, "clean_shutdown_cleanup": True, "session_datadir_retained": True, "sealed_template_started": False, "postgresql_process_count": 0, "native_mysql_low_pressure": True, "native_mysql_isolation_complete": False, "formal_static_gate": False, "cross_engine_equality": False, "r0": False, "ready": False, "readiness_projection": projected}, sort_keys=True)); return 0
    except (OSError, KeyError, ValueError, json.JSONDecodeError, VerificationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True)); return 1


if __name__ == "__main__":
    raise SystemExit(main())
