#!/usr/bin/env python3
"""Build the canonical AP MySQL envelope artifact; default is dry-run."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "history/mysql_sgd_env_agents_20260723/ap/ap_envelope_probe_20260722T223955Z"
OUTPUT = ROOT / "history/mysql_sgd_env_agents_20260723/ap/formal_mysql_normalized_envelope_readback.json"
PAIRED = ROOT / "config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json"
RUN_ID = "ap_envelope_probe_20260722T223955Z"


class BuildError(RuntimeError):
    pass


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def obj(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise BuildError(f"not an object: {path}")
    return value


def verify_bundle() -> int:
    count = 0
    for line in (BUNDLE / "checksums.sha256").read_text().splitlines():
        digest, separator, name = line.partition("  ")
        path = (BUNDLE / name.removeprefix("./")).resolve()
        if not separator or path.parent != BUNDLE.resolve() or not path.is_file() or path.is_symlink() or sha(path) != digest:
            raise BuildError(f"bad bundle member: {name}")
        count += 1
    if count != 16:
        raise BuildError(f"expected 16 bundle members, got {count}")
    return count


def atomic_write(path: Path, payload: bytes) -> None:
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    count = verify_bundle()
    paired = obj(PAIRED); profile = paired["profiles"]["memory"]
    normalized = obj(BUNDLE / "normalized_envelope.json")
    live = obj(BUNDLE / "live_cgroup_readback.json")
    idle = obj(BUNDLE / "native_mysql_idle_before.json")
    summary = obj(BUNDLE / "summary.json")
    running = json.loads((BUNDLE / "mysql_inspect_running.json").read_text())[0]
    stopped = json.loads((BUNDLE / "mysql_inspect_stopped.json").read_text())[0]
    version, read_only, super_read_only = (BUNDLE / "mysql_readback.tsv").read_text().strip().split("\t")
    if (
        normalized.get("status") != "PASS" or normalized.get("normalized_envelope") != profile
        or normalized.get("requested_effective_equal") is not True
        or summary.get("status") != "PASS" or summary.get("sealed_template_started") is not False
        or summary.get("postgresql_process_count") != 0
        or idle.get("pressure_conflict") is not False or idle.get("isolation_complete") is not False
        or running["State"]["Running"] is not True or stopped["State"]["Running"] is not False
        or stopped["State"]["ExitCode"] != 0 or stopped["State"]["OOMKilled"] is not False
        or "Shutdown complete" not in (BUNDLE / "mysql.log").read_text()
    ):
        raise BuildError("probe does not satisfy canonical envelope-only scope")
    source_names = {
        "checksums_sha256": "checksums.sha256", "normalized_envelope_sha256": "normalized_envelope.json",
        "live_cgroup_readback_sha256": "live_cgroup_readback.json",
        "mysql_inspect_running_sha256": "mysql_inspect_running.json",
        "mysql_inspect_stopped_sha256": "mysql_inspect_stopped.json",
        "mysql_readback_sha256": "mysql_readback.tsv", "mysql_log_sha256": "mysql.log",
        "native_mysql_idle_sha256": "native_mysql_idle_before.json",
        "native_mysql_pidstat_sha256": "native_mysql_pidstat.txt",
        "postgres_before_sha256": "postgres_before.txt", "postgres_after_sha256": "postgres_after.txt",
        "postgres_containers_before_sha256": "postgres_containers_before.jsonl",
        "postgres_containers_after_sha256": "postgres_containers_after.jsonl",
        "summary_sha256": "summary.json",
    }
    document = {
        "schema_version": "mysql_normalized_resource_envelope_readback_v1",
        "status": "MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY",
        "scenario": "hybench_ap_memory", "engine": "mysql", "host": "192.0.2.3",
        "run_id": RUN_ID, "runtime_role": "idle_mysql_database_on_session_owned_production_binding_trace_datadir",
        "container_id": running["Id"], "profile": "memory",
        "freeze_id": paired["freeze_id"], "normalized_envelope": dict(profile),
        "effective_readback": dict(live), "requested_effective_equal": True,
        "database_readback": {"mysql_version": version, "read_only": read_only == "1", "super_read_only": super_read_only == "1", "benchmark_queries_executed": False},
        "storage_scope": {
            "session_owned_datadir": "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/production_binding_trace_20260723_0630/datadir",
            "sealed_canonical_template_datadir": "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/mysql_template_data",
            "paths_distinct": True, "sealed_template_started": False, "session_datadir_retained": True,
        },
        "pressure_admission": {
            "postgresql_process_count": 0, "postgresql_running_container_count": 0,
            "native_mysql_pid": idle["pid"], "native_mysql_cpu_ticks_delta": idle["cpu_ticks_delta"],
            "native_mysql_io_read_kib_per_second": idle["io_read_kib_per_second"],
            "native_mysql_io_write_kib_per_second": idle["io_write_kib_per_second"],
            "native_mysql_established_connections": idle["established_connections"],
            "native_mysql_pressure_conflict": False, "native_mysql_isolation_complete": False,
        },
        "static_isolation": {
            "postgresql_counterpart_present": False, "postgresql_counterpart_envelope_proven": False,
            "native_mysql_isolation_complete": False, "cross_engine_envelope_equality": False,
            "formal_static_isolation_gate_passed": False,
        },
        "closure": {"clean_mysql_shutdown": True, "temporary_container_removed": True, "temporary_network_removed": True, "session_datadir_retained": True, "sealed_template_started": False},
        "source_evidence": {"directory": str(BUNDLE.relative_to(ROOT)), **{key: sha(BUNDLE / name) for key, name in source_names.items()}},
        "formal_scope": {"environment_readback_only": True, "r0": False, "sgd": False, "accepted_iteration": False, "readiness_changed": False, "ready": False, "launch_authorized": False},
        "scope_note": "AP MySQL-side live envelope only. PostgreSQL count was zero, but native mysqld was merely low-pressure and not isolated; static, paired, R0, READY and launch gates remain false.",
    }
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    side = f"{hashlib.sha256(payload).hexdigest()}  {OUTPUT.name}\n".encode()
    if args.execute:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(OUTPUT, payload); atomic_write(OUTPUT.with_name(OUTPUT.name + ".sha256"), side)
    print(json.dumps({"status": "PASS", "mode": "execute" if args.execute else "dry-run", "bundle_members": count, "output": str(OUTPUT.relative_to(ROOT)), "sha256": hashlib.sha256(payload).hexdigest(), "formal_static_gate": False, "r0": False, "ready": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, BuildError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True)); raise SystemExit(1)
