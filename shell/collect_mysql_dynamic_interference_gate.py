#!/usr/bin/env python3
"""Collect and promote the five real 180-second MySQL-only interference gates."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
READINESS = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "readiness_manifest.json"
)
REMOTE_SAMPLER = ROOT / "shell/remote_sample_mysql_dynamic_interference.py"
OUTPUT = (
    ROOT
    / "config/formal/frozen_mysql_dynamic_interference_20260724_v1"
)
RAW_ROOT = (
    ROOT
    / "history/mysql_sgd_env_agents_20260723"
    / "dynamic_interference_20260724_v1"
)
FREEZE_ID = "frozen_mysql_dynamic_interference_20260724_v1"
HOSTS = {
    "host02": ("anonymous@192.0.2.4", "node02"),
    "host04": ("anonymous@192.0.2.7", "node04"),
}
SCENARIO_HOST = {
    "job_memory": "host02",
    "hybench_xp_io": "host02",
    "tpch_memory": "host04",
    "tpcc_cpu": "host04",
}
INTERVAL_SECONDS = 30
INTERVAL_COUNT = 6


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def pointer(path: Path) -> dict[str, str]:
    return {"path": relative(path), "sha256": sha256_file(path)}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_with_sidecar(path: Path, value: Mapping[str, Any]) -> str:
    data = canonical_bytes(value)
    digest = hashlib.sha256(data).hexdigest()
    atomic_write(path, data)
    atomic_write(
        path.with_name(path.name + ".sha256"),
        f"{digest}  {path.name}\n".encode("ascii"),
    )
    return digest


def collect_host(
    host_id: str,
    target: str,
    expected_hostname: str,
    source: bytes,
) -> tuple[str, dict[str, Any]]:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            target,
            "python3",
            "-",
            "--interval-seconds",
            str(INTERVAL_SECONDS),
            "--interval-count",
            str(INTERVAL_COUNT),
        ],
        input=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=240,
        check=False,
    )
    samples = []
    parse_errors = []
    for line_number, raw in enumerate(result.stdout.decode("utf-8", "replace").splitlines(), 1):
        try:
            value = json.loads(raw)
            if isinstance(value, dict):
                samples.append(value)
            else:
                parse_errors.append(f"line {line_number}: non-object")
        except json.JSONDecodeError as exc:
            parse_errors.append(f"line {line_number}: {exc}")
    payload = {
        "schema_version": "mysql_dynamic_interference_host_window_v1",
        "host_id": host_id,
        "target": target,
        "expected_hostname": expected_hostname,
        "collector_started_at": datetime.now().astimezone().isoformat(
            timespec="seconds"
        ),
        "returncode": result.returncode,
        "stderr": result.stderr.decode("utf-8", "replace").strip(),
        "parse_errors": parse_errors,
        "samples": samples,
    }
    return host_id, payload


def process_ticks(sample: Mapping[str, Any]) -> int | None:
    values = [
        row.get("cpu_ticks")
        for row in sample.get("database_processes") or []
    ]
    if not values or any(not isinstance(value, int) for value in values):
        return None
    return sum(values)


def process_io(sample: Mapping[str, Any], field: str) -> int | None:
    values = [
        row.get(field) for row in sample.get("database_processes") or []
    ]
    available = [value for value in values if isinstance(value, int)]
    return sum(available) if available else None


def container_signature(sample: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            row.get("name"),
            row.get("status"),
            row.get("cpuset_cpus"),
            row.get("cpuset_mems"),
            row.get("memory"),
            row.get("memory_swap"),
        )
        for row in sample.get("running_containers") or []
    )


def analyse(host_window: Mapping[str, Any]) -> dict[str, Any]:
    samples = host_window.get("samples") or []
    valid_samples = [
        row
        for row in samples
        if isinstance(row, Mapping)
        and row.get("hostname") == host_window.get("expected_hostname")
        and isinstance(row.get("system_cpu_ticks"), int)
        and isinstance(row.get("database_processes"), list)
        and isinstance(row.get("driver_processes"), list)
        and isinstance(row.get("running_containers"), list)
    ]
    intervals = max(0, len(valid_samples) - 1)
    coverage = min(1.0, intervals / INTERVAL_COUNT)
    durations = []
    host_cpu_ratios = []
    io_rates = []
    for before, after in zip(valid_samples, valid_samples[1:]):
        duration = float(after["monotonic_seconds"]) - float(
            before["monotonic_seconds"]
        )
        durations.append(duration)
        system_delta = int(after["system_cpu_ticks"]) - int(
            before["system_cpu_ticks"]
        )
        before_ticks = process_ticks(before)
        after_ticks = process_ticks(after)
        if (
            system_delta > 0
            and before_ticks is not None
            and after_ticks is not None
            and after_ticks >= before_ticks
        ):
            host_cpu_ratios.append((after_ticks - before_ticks) / system_delta)
        before_io = process_io(before, "read_bytes")
        after_io = process_io(after, "read_bytes")
        before_write = process_io(before, "write_bytes")
        after_write = process_io(after, "write_bytes")
        if (
            duration > 0
            and before_io is not None
            and after_io is not None
            and before_write is not None
            and after_write is not None
            and after_io >= before_io
            and after_write >= before_write
        ):
            io_rates.append(
                (after_io - before_io + after_write - before_write) / duration
            )
    all_processes_complete = bool(valid_samples) and all(
        row.get("database_processes")
        and all(
            process.get("cpus_allowed_list")
            and process.get("mems_allowed_list")
            and isinstance(process.get("cpu_ticks"), int)
            for process in row.get("database_processes") or []
        )
        for row in valid_samples
    )
    driver_count = sum(
        len(row.get("driver_processes") or []) for row in valid_samples
    )
    signatures = [container_signature(row) for row in valid_samples]
    containers_stable = bool(signatures) and all(
        signature == signatures[0] for signature in signatures
    )
    duration_ok = (
        len(durations) == INTERVAL_COUNT
        and all(27.0 <= value <= 35.0 for value in durations)
        and sum(durations) >= 175.0
    )
    cpu_stable = bool(host_cpu_ratios) and all(
        math.isfinite(value) and value <= 0.10 for value in host_cpu_ratios
    )
    io_stable = not io_rates or all(
        math.isfinite(value) and value <= 16 * 1024 * 1024
        for value in io_rates
    )
    passed = all(
        (
            host_window.get("returncode") == 0,
            not host_window.get("parse_errors"),
            coverage >= 0.90,
            duration_ok,
            all_processes_complete,
            driver_count == 0,
            containers_stable,
            cpu_stable,
            io_stable,
        )
    )
    return {
        "passed": passed,
        "sample_coverage": coverage,
        "valid_sample_count": len(valid_samples),
        "interval_count": intervals,
        "window_seconds": sum(durations),
        "interval_durations_seconds": durations,
        "co_resident_process_inventory_complete": all_processes_complete,
        "active_driver_observation_count": driver_count,
        "container_signature_stable": containers_stable,
        "database_host_cpu_ratio_max": (
            max(host_cpu_ratios) if host_cpu_ratios else None
        ),
        "database_io_bytes_per_second_max": (
            max(io_rates) if io_rates else None
        ),
        "database_cpu_stability_passed": cpu_stable,
        "database_io_stability_passed": io_stable,
    }


def update_checksum_index(bundle: Path, manifest_digest: str) -> None:
    checksum_path = bundle / "checksums.sha256"
    values: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="ascii").splitlines():
        parts = line.split("  ", 1)
        if len(parts) == 2:
            values[parts[1]] = parts[0]
    sidecar = bundle / "readiness_manifest.json.sha256"
    values["readiness_manifest.json"] = manifest_digest
    values["readiness_manifest.json.sha256"] = sha256_file(sidecar)
    atomic_write(
        checksum_path,
        "".join(
            f"{values[name]}  {name}\n" for name in sorted(values)
        ).encode("ascii"),
    )


def promote(entries: Mapping[str, Mapping[str, str]]) -> None:
    manifest = json.loads(READINESS.read_text(encoding="utf-8"))
    for scenario, artifact_pointer in entries.items():
        manifest["scenarios"][scenario]["dynamic_interference"] = {
            "passed": True,
            "evidence": dict(artifact_pointer),
        }
    data = canonical_bytes(manifest)
    digest = hashlib.sha256(data).hexdigest()
    atomic_write(READINESS, data)
    atomic_write(
        READINESS.with_name(READINESS.name + ".sha256"),
        f"{digest}  {READINESS.name}\n".encode("ascii"),
    )
    update_checksum_index(READINESS.parent, digest)


def collect(
    promote_evidence: bool,
    recollect_hosts: set[str] | None = None,
) -> dict[str, Any]:
    source = REMOTE_SAMPLER.read_bytes()
    windows: dict[str, dict[str, Any]] = {}
    selected = set(HOSTS) if recollect_hosts is None else set(recollect_hosts)
    unknown = selected - set(HOSTS)
    if unknown:
        raise ValueError(f"unknown hosts requested: {sorted(unknown)}")
    for host_id in sorted(set(HOSTS) - selected):
        existing = RAW_ROOT / f"{host_id}.window.json"
        if not existing.is_file():
            raise FileNotFoundError(
                f"cannot reuse missing host window: {existing}"
            )
        value = json.loads(existing.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid reusable host window: {existing}")
        windows[host_id] = value
    with ThreadPoolExecutor(max_workers=len(HOSTS)) as executor:
        futures = [
            executor.submit(
                collect_host,
                host_id,
                target,
                hostname,
                source,
            )
            for host_id, (target, hostname) in HOSTS.items()
            if host_id in selected
        ]
        for future in as_completed(futures):
            host_id, payload = future.result()
            windows[host_id] = payload
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    host_pointers: dict[str, dict[str, str]] = {}
    analyses: dict[str, dict[str, Any]] = {}
    for host_id in sorted(windows):
        path = RAW_ROOT / f"{host_id}.window.json"
        atomic_write(path, canonical_bytes(windows[host_id]))
        host_pointers[host_id] = pointer(path)
        analyses[host_id] = analyse(windows[host_id])
    failed_hosts = {
        host_id: analysis
        for host_id, analysis in analyses.items()
        if analysis.get("passed") is not True
    }
    if failed_hosts:
        raise RuntimeError(
            "dynamic interference host gate failed: "
            + json.dumps(failed_hosts, ensure_ascii=False, sort_keys=True)
        )
    manifest = json.loads(READINESS.read_text(encoding="utf-8"))
    limits = manifest["policy"]["co_resident_interference_limits"]
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict[str, str]] = {}
    for scenario, host_id in SCENARIO_HOST.items():
        analysis = analyses[host_id]
        evidence = {
            "schema_version": "mysql_only_dynamic_interference_evidence_v1",
            "freeze_id": FREEZE_ID,
            "scenario": scenario,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "passed": True,
            "mode": "exclusive_host_no_co_resident_pressure",
            "latest_window_seconds": 180,
            "latest_window_count": 6,
            "max_continuous_seconds": 600,
            "sample_coverage": analysis["sample_coverage"],
            "limits": limits,
            "co_resident_process_inventory_complete": analysis[
                "co_resident_process_inventory_complete"
            ],
            "host_admission_no_unregistered_pressure": (
                analysis["active_driver_observation_count"] == 0
            ),
            "co_resident_background_health_unchanged": (
                analysis["container_signature_stable"]
            ),
            "mysql_resource_stability_passed": (
                analysis["database_cpu_stability_passed"]
                and analysis["database_io_stability_passed"]
            ),
            "not_applicable_metrics": [
                "throughput_drop_ratio",
                "p95_increase_ratio",
                "p99_increase_ratio",
                "target_resource_shift_percentage_points",
            ],
            "not_applicable_reason": "no_co_resident_workload_or_latency_stream",
            "host_analysis": analysis,
            "sources": {
                "host_window": host_pointers[host_id],
                "remote_sampler": pointer(REMOTE_SAMPLER),
                "collector": pointer(Path(__file__)),
            },
        }
        path = OUTPUT / f"{scenario}.dynamic_interference_v1.json"
        write_json_with_sidecar(path, evidence)
        entries[scenario] = pointer(path)
    bundle = {
        "schema_version": "mysql_only_dynamic_interference_bundle_v1",
        "freeze_id": FREEZE_ID,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "status": "FROZEN_DYNAMIC_INTERFERENCE_PASS",
        "host_windows": host_pointers,
        "host_analyses": analyses,
        "evidence": entries,
    }
    write_json_with_sidecar(OUTPUT / "manifest.json", bundle)
    lines = [
        f"{sha256_file(path)}  {path.name}\n"
        for path in sorted(OUTPUT.iterdir())
        if path.is_file() and path.name != "checksums.sha256"
    ]
    atomic_write(OUTPUT / "checksums.sha256", "".join(lines).encode("ascii"))
    if promote_evidence:
        promote(entries)
    return {
        "status": "FROZEN_AND_PROMOTED" if promote_evidence else "FROZEN",
        "freeze_id": FREEZE_ID,
        "host_analyses": analyses,
        "evidence": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument(
        "--recollect-host",
        action="append",
        choices=sorted(HOSTS),
        help="recollect only this host and reuse the other sealed raw windows",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            collect(
                args.promote,
                set(args.recollect_host) if args.recollect_host else None,
            ),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
