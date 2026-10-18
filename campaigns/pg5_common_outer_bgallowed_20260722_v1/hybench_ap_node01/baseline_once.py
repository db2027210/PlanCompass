#!/usr/bin/env python3
"""Run the campaign's one shared HyBench AP P0 baseline on node01."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import statistics
import subprocess
import time
from typing import Any, Dict

from formal_contracts import PG5_STABILITY_GATE_SHA256


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def evaluate_ap13_measurement_correctness(
    query_log_path: Path,
    expected_query_hash_by_id: Dict[int, str],
) -> Dict[str, Any]:
    """evaluate ap13 measurement correctness."""
    failures = []
    rows = []
    expected_ids = list(range(1, 14))
    if sorted(expected_query_hash_by_id) != expected_ids:
        failures.append("expected_query_hash_by_id must contain exactly AP1-AP13")
    expected_hashes = set(expected_query_hash_by_id.values())
    if len(expected_hashes) != len(expected_query_hash_by_id):
        failures.append("AP1-AP13 target SQL template hashes must be unique")

    payload = b""
    if not query_log_path.is_file():
        failures.append(f"query evidence log is missing: {query_log_path}")
    else:
        payload = query_log_path.read_bytes()
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            failures.append(f"query evidence log is not valid UTF-8: {exc}")
            text = ""
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            if not raw_line.strip():
                failures.append(f"blank JSONL record at line {line_number}")
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                failures.append(f"invalid JSONL record at line {line_number}: {exc}")
                continue
            if not isinstance(row, dict):
                failures.append(f"non-object JSONL record at line {line_number}")
                continue
            rows.append((line_number, row))
    if not rows:
        failures.append("query evidence log contains no valid records")

    covered_hashes = set()
    auxiliary_hashes = set()
    rows_per_hash: Dict[str, int] = {}
    result_hashes_by_sql: Dict[str, set[str]] = {}
    for line_number, row in rows:
        sql_hash = row.get("sql_sha256")
        if (
            not isinstance(sql_hash, str)
            or len(sql_hash) != 64
            or any(character not in "0123456789abcdef" for character in sql_hash)
        ):
            failures.append(f"invalid sql_sha256 at line {line_number}")
            continue
        rows_per_hash[sql_hash] = rows_per_hash.get(sql_hash, 0) + 1
        if sql_hash in expected_hashes:
            covered_hashes.add(sql_hash)
        else:
            auxiliary_hashes.add(sql_hash)
        if row.get("event") != "query_complete":
            failures.append(f"unexpected event at line {line_number}: {row.get('event')!r}")
        if row.get("success") is not True:
            failures.append(f"query was not successful at line {line_number}")
        if row.get("commit_after_query") is not True:
            failures.append(f"query was not committed at line {line_number}")
        if row.get("error") is not None:
            failures.append(f"query error field is non-null at line {line_number}")
        row_count = row.get("row_count")
        if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
            failures.append(f"invalid row_count at line {line_number}")
        result_hash = row.get("result_sha256")
        if result_hash is not None:
            if (
                not isinstance(result_hash, str)
                or len(result_hash) != 64
                or any(character not in "0123456789abcdef" for character in result_hash)
            ):
                failures.append(f"invalid result_sha256 at line {line_number}")
            else:
                result_hashes_by_sql.setdefault(sql_hash, set()).add(result_hash)

    missing_ids = [
        query_id
        for query_id, sql_hash in sorted(expected_query_hash_by_id.items())
        if sql_hash not in covered_hashes
    ]
    if missing_ids:
        failures.append(f"missing successful AP query templates: {missing_ids}")
    return {
        "schema_version": 1,
        "gate_id": "hybench_ap13_measurement_correctness_v1",
        "status": "passed" if not failures else "failed",
        "all_records_successful_committed_and_error_free": not any(
            "successful" in failure or "committed" in failure or "error field" in failure
            for failure in failures
        ),
        "query_log_path": str(query_log_path),
        "query_log_sha256": hashlib.sha256(payload).hexdigest() if payload else None,
        "event_count": len(rows),
        "expected_query_hash_by_id": {
            str(query_id): sql_hash
            for query_id, sql_hash in sorted(expected_query_hash_by_id.items())
        },
        "covered_query_ids": [
            query_id
            for query_id, sql_hash in sorted(expected_query_hash_by_id.items())
            if sql_hash in covered_hashes
        ],
        "missing_query_ids": missing_ids,
        "auxiliary_sql_sha256": sorted(auxiliary_hashes),
        "auxiliary_sql_is_allowed": True,
        "records_per_sql_sha256": dict(sorted(rows_per_hash.items())),
        "observed_result_sha256_by_sql": {
            sql_hash: sorted(values)
            for sql_hash, values in sorted(result_hashes_by_sql.items())
        },
        "parameterized_result_policy": {
            "result_sha256_is_provenance_only": True,
            "cross_execution_result_sha256_equality_required": False,
            "reason": "bound parameters may legitimately change AP query results",
        },
        "failures": failures,
    }


def host_snapshot() -> Dict[str, Any]:
    load1, load5, load15 = os.getloadavg()
    uptime = subprocess.run(
        ["uptime"], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return {
        "captured_at_epoch": time.time(),
        "loadavg": {"load1": load1, "load5": load5, "load15": load15},
        "uptime": uptime.stdout.strip(),
        "background_load_allowed": True,
        "host_wide_idle_gate_skipped": True,
    }


def du_bytes(path: Path) -> int:
    value = subprocess.run(
        ["du", "-sb", str(path)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.split()[0]
    return int(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane-root", required=True, type=Path)
    parser.add_argument("--measurement-seconds", type=int, default=600)
    parser.add_argument("--attempt-id", default="attempt_1")
    parser.add_argument("--cpu-quota-cores", type=float, default=3.0)
    parser.add_argument("--memory-bytes", type=int, default=6 * 1024 ** 3)
    parser.add_argument("--io-read-bps", type=int, default=1150 * 1024 ** 2)
    parser.add_argument("--io-write-bps", type=int, default=1150 * 1024 ** 2)
    parser.add_argument("--scoring-contract", required=True, type=Path)
    parser.add_argument("--scoring-contract-sha256", required=True)
    parser.add_argument("--resource-gate-contract", required=True, type=Path)
    parser.add_argument("--resource-gate-contract-sha256", required=True)
    parser.add_argument("--stability-contract", required=True, type=Path)
    parser.add_argument("--stability-contract-sha256", required=True)
    parser.add_argument("--io-utilization-contract", required=True, type=Path)
    parser.add_argument("--io-utilization-contract-sha256", required=True)
    args = parser.parse_args()

    if args.measurement_seconds != 600:
        raise ValueError("formal online measurement must use the frozen 600-second hard maximum")
    if args.cpu_quota_cores <= 0 or args.cpu_quota_cores > 8:
        raise ValueError("CPU quota must be in (0, 8] for cpuset 16-23")
    if args.memory_bytes <= 0:
        raise ValueError("memory limit must be positive")
    if args.io_read_bps <= 0 or args.io_write_bps <= 0:
        raise ValueError("I/O limits must be positive")

    def load_bound_contract(path: Path, expected_sha256: str) -> tuple[Dict[str, Any], str]:
        payload = path.read_bytes()
        actual_sha256 = hashlib.sha256(payload).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"contract hash mismatch: path={path} expected={expected_sha256} actual={actual_sha256}"
            )
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError(f"contract must be a JSON object: {path}")
        return value, actual_sha256

    scoring_contract, scoring_contract_sha256 = load_bound_contract(
        args.scoring_contract, args.scoring_contract_sha256,
    )
    resource_gate_contract, resource_gate_contract_sha256 = load_bound_contract(
        args.resource_gate_contract, args.resource_gate_contract_sha256,
    )
    stability_contract, stability_contract_sha256 = load_bound_contract(
        args.stability_contract, args.stability_contract_sha256,
    )
    io_utilization_contract, io_utilization_contract_sha256 = load_bound_contract(
        args.io_utilization_contract, args.io_utilization_contract_sha256,
    )
    if scoring_contract.get("score_mode") != "band_target":
        raise RuntimeError("formal scoring contract must use band_target")
    if resource_gate_contract.get("contract_id") != "pg5_r0_single_bottleneck_gate_v2":
        raise RuntimeError("unexpected formal R0 resource gate contract")
    if (
        resource_gate_contract.get("target_min_pct_inclusive") != 85
        or resource_gate_contract.get("target_max_pct_inclusive") != 95
        or resource_gate_contract.get("non_target_min_pct_inclusive") != 30
        or resource_gate_contract.get("non_target_max_pct_inclusive") != 50
    ):
        raise RuntimeError("formal R0 resource gate must use target 85-95 and non-target 30-50")
    if args.stability_contract_sha256 != PG5_STABILITY_GATE_SHA256:
        raise RuntimeError("configured stability hash differs from formal_contracts.py")
    if stability_contract.get("contract_id") != "pg5_six_window_resource_mean_stability_v2":
        raise RuntimeError("unexpected formal R0 stability contract")
    if stability_contract.get("throughput_fields_required_for_stability") is not False:
        raise RuntimeError("throughput must not participate in the v2 stability gate")
    if stability_contract.get("minimum_measurement_seconds") != 180:
        raise RuntimeError("formal stability gate must allow the first six-window check at 180 seconds")
    if stability_contract.get("maximum_measurement_seconds") != 600:
        raise RuntimeError("formal v2 stability hard maximum must be 600 seconds")
    if io_utilization_contract.get("contract_id") != "pg5_io_total_throughput_utilization_v1":
        raise RuntimeError("unexpected formal I/O utilization contract")

    import p24_pg_recost_runner as p24

    original_p24_run = p24.run

    def lane_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command and command[0] == "sudo":
            command = command[1:]
        return original_p24_run(command, **kwargs)

    # The transferred template is extracted --no-same-owner into this unique
    # lane.  Revalidation therefore needs no host sudo and must not depend on a
    # pre-existing sudo credential cache.
    p24.run = lane_run

    _, points, validation = p24.configure_modules("ap")
    import formal_pg_engine as engine

    benchmark_matrix_path = points.SHARED_CONFIG / "benchmark_matrix.json"
    benchmark_matrix_sha256 = points.sha256_file(benchmark_matrix_path)
    expected_benchmark_matrix_sha256 = (
        "23b20f81a129cc90d48456f01983fb911a9d76d9171af716fe028ca5edb87121"
    )
    if benchmark_matrix_sha256 != expected_benchmark_matrix_sha256:
        raise RuntimeError(
            "frozen benchmark matrix hash mismatch: "
            f"expected={expected_benchmark_matrix_sha256} actual={benchmark_matrix_sha256}"
        )
    benchmark_matrix = points.read_json(benchmark_matrix_path)
    frozen_ap_config = benchmark_matrix["workloads"]["hybench_ap"]
    expected_ap_query_ids = list(range(1, 14))
    expected_ap_query_weights = [6, 6, 6, 6, 6, 6, 6, 6, 6, 28, 6, 6, 6]
    expected_ap_clients = 4
    if frozen_ap_config.get("formal_name") != "HyBench-AP13-mixed-AP10-weighted":
        raise RuntimeError("unexpected frozen HyBench AP formal workload name")
    if frozen_ap_config.get("ap_query_ids") != expected_ap_query_ids:
        raise RuntimeError("frozen HyBench AP query IDs differ from the AP1-AP13 contract")
    if frozen_ap_config.get("ap_query_weights") != expected_ap_query_weights:
        raise RuntimeError("frozen HyBench AP query weights differ from the contract")
    if frozen_ap_config.get("clients", {}).get("ap") != expected_ap_clients:
        raise RuntimeError("frozen HyBench AP client count differs from the contract")

    ap_sequence_contract_path = args.lane_root / "ap_query_sequence_v1.json"
    ap_sequence_contract_sha256 = points.sha256_file(ap_sequence_contract_path)
    expected_ap_sequence_contract_sha256 = (
        "379d3060d61ab1bf906eac6bae6f08632b54db122cb1802c58e1fcf81e65b27d"
    )
    if ap_sequence_contract_sha256 != expected_ap_sequence_contract_sha256:
        raise RuntimeError(
            "AP query sequence contract hash mismatch: "
            f"expected={expected_ap_sequence_contract_sha256} "
            f"actual={ap_sequence_contract_sha256}"
        )
    ap_sequence_contract = points.read_json(ap_sequence_contract_path)
    expected_ap_query_sequence = ap_sequence_contract.get("sequence")
    if (
        ap_sequence_contract.get("contract_id")
        != "hybench_ap13_stride4_interleaved_sequence_v1"
        or ap_sequence_contract.get("client_count") != expected_ap_clients
        or ap_sequence_contract.get("global_sequence_length") != 100
        or ap_sequence_contract.get("client_stride") != expected_ap_clients
        or ap_sequence_contract.get("client_start_offsets") != [0, 1, 2, 3]
        or ap_sequence_contract.get("sequence_limit") != 0
        or not isinstance(expected_ap_query_sequence, list)
        or len(expected_ap_query_sequence) != 100
    ):
        raise RuntimeError("invalid fixed interleaved AP query sequence contract")
    actual_sequence_counts = {
        query_id: expected_ap_query_sequence.count(query_id)
        for query_id in expected_ap_query_ids
    }
    expected_sequence_counts = {
        query_id: weight
        for query_id, weight in zip(expected_ap_query_ids, expected_ap_query_weights)
    }
    if actual_sequence_counts != expected_sequence_counts:
        raise RuntimeError(
            "fixed AP query sequence does not preserve exact weights: "
            f"expected={expected_sequence_counts} actual={actual_sequence_counts}"
        )
    if any(
        expected_ap_query_sequence[offset::expected_ap_clients].count(10) != 7
        for offset in range(expected_ap_clients)
    ):
        raise RuntimeError("fixed AP query sequence does not allocate seven AP10s per client")
    expected_ap_query_sequence_csv = ",".join(
        str(value) for value in expected_ap_query_sequence
    )

    def parse_hybench_properties(payload: str) -> Dict[str, str]:
        parsed: Dict[str, str] = {}
        for raw_line in payload.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise RuntimeError(f"invalid HyBench property line: {raw_line!r}")
            key, value = line.split("=", 1)
            if key in parsed:
                raise RuntimeError(f"duplicate HyBench property: {key}")
            parsed[key] = value
        return parsed

    def validate_ap13_properties(parsed: Dict[str, str]) -> None:
        expected = {
            "tpclient": "0",
            "apclient": str(expected_ap_clients),
            "xtpclient": "0",
            "xapclient": str(expected_ap_clients),
            "ap_query_ids": ",".join(str(value) for value in expected_ap_query_ids),
            "ap_query_weights": ",".join(
                str(value) for value in expected_ap_query_weights
            ),
            "ap_query_sequence": expected_ap_query_sequence_csv,
            "ap_query_sequence_limit": "0",
        }
        mismatches = {
            key: {"expected": value, "actual": parsed.get(key)}
            for key, value in expected.items()
            if parsed.get(key) != value
        }
        if mismatches:
            raise RuntimeError(f"HyBench AP runtime properties mismatch: {mismatches}")

    original_runtime_hybench_properties = points.runtime_hybench_properties

    def frozen_ap13_runtime_hybench_properties(
        workload: str,
        duration_seconds: int,
    ) -> str:
        payload = original_runtime_hybench_properties(workload, duration_seconds)
        if workload != "hybench_ap":
            return payload
        overrides = {
            "tpclient": "0",
            "apclient": str(expected_ap_clients),
            "xtpclient": "0",
            "xapclient": str(expected_ap_clients),
            "ap_query_ids": ",".join(str(value) for value in expected_ap_query_ids),
            "ap_query_weights": ",".join(
                str(value) for value in expected_ap_query_weights
            ),
            "ap_query_sequence": expected_ap_query_sequence_csv,
            "ap_query_sequence_limit": "0",
        }
        appendable = {"ap_query_sequence", "ap_query_sequence_limit"}
        rewritten = []
        replaced = set()
        for line in payload.splitlines():
            key = line.split("=", 1)[0] if "=" in line else None
            if key in overrides:
                rewritten.append(f"{key}={overrides[key]}")
                replaced.add(key)
            else:
                rewritten.append(line)
        for key in sorted(appendable - replaced):
            rewritten.append(f"{key}={overrides[key]}")
            replaced.add(key)
        missing = set(overrides) - replaced
        if missing:
            raise RuntimeError(f"base HyBench properties are missing required fields: {missing}")
        rewritten_payload = "\n".join(rewritten) + "\n"
        validate_ap13_properties(parse_hybench_properties(rewritten_payload))
        return rewritten_payload

    points.runtime_hybench_properties = frozen_ap13_runtime_hybench_properties

    fixed_driver_source_path = args.lane_root / "private_jdbc" / "FormalPgCommitDriver.java"
    fixed_driver_jar_path = (
        args.lane_root / "private_jdbc" / "build" / "formal-pg-commit-fixed.jar"
    )
    fixed_driver_regression_path = (
        args.lane_root / "regressions" / "ap13_fixed_driver_v2" / "summary.json"
    )
    fixed_driver_source_sha256 = (
        "d1c1f7ba6edcfd31bd5132aa5c94a7b38e2850435338d01cb6ab30137d0b5109"
    )
    fixed_driver_jar_sha256 = (
        "e687e11704d1401bc03cbbad7f80bd51a8dfa89ae76502407800c5bcfb82d186"
    )
    fixed_driver_regression_sha256 = (
        "f086d2f33833137a260bf8a38af2cbf497a683ae1ec0d594a6c82b920a3b7dc0"
    )
    for artifact_path, expected_sha256 in (
        (fixed_driver_source_path, fixed_driver_source_sha256),
        (fixed_driver_jar_path, fixed_driver_jar_sha256),
        (fixed_driver_regression_path, fixed_driver_regression_sha256),
    ):
        actual_sha256 = points.sha256_file(artifact_path)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"private JDBC patch artifact hash mismatch: path={artifact_path} "
                f"expected={expected_sha256} actual={actual_sha256}"
            )
    fixed_driver_regression = points.read_json(fixed_driver_regression_path)
    if (
        fixed_driver_regression.get("status") != "passed"
        or fixed_driver_regression.get("all_passed") is not True
        or fixed_driver_regression.get("query_ids") != expected_ap_query_ids
        or len(fixed_driver_regression.get("results", [])) != 13
        or any(
            row.get("passed") is not True
            for row in fixed_driver_regression.get("results", [])
        )
    ):
        raise RuntimeError("private JDBC patch lacks a passing AP1-AP13 isolated regression")
    expected_query_hash_by_id = {
        int(row["query_id"]): str(row["target_sql_sha256"])
        for row in fixed_driver_regression["results"]
    }
    if sorted(expected_query_hash_by_id) != expected_ap_query_ids:
        raise RuntimeError("private JDBC regression lacks AP1-AP13 target SQL hashes")
    shared_formal_jdbc_jar = points.FORMAL_JDBC_JAR
    shared_formal_jdbc_jar_sha256 = points.sha256_file(Path(str(shared_formal_jdbc_jar)))
    private_formal_jdbc_classpath = f"{fixed_driver_jar_path}:{shared_formal_jdbc_jar}"

    apclient_patch_jar_path = (
        args.lane_root / "private_hybench" / "build" / "apclient-sequence-stride4.jar"
    )
    apclient_patch_summary_path = apclient_patch_jar_path.with_suffix(".summary.json")
    apclient_patch_summary = points.read_json(apclient_patch_summary_path)
    apclient_patch_jar_sha256 = points.sha256_file(apclient_patch_jar_path)
    shared_hybench_jar = Path(str(points.HYBENCH_JAR))
    shared_hybench_jar_sha256 = points.sha256_file(shared_hybench_jar)
    if (
        apclient_patch_summary.get("patch_id")
        != "hybench_apclient_sequence_stride4_v1"
        or apclient_patch_summary.get("patch_count") != 1
        or apclient_patch_summary.get("source_jar_sha256") != shared_hybench_jar_sha256
        or apclient_patch_summary.get("output_jar_sha256") != apclient_patch_jar_sha256
        or apclient_patch_summary.get("patched_pattern_hex")
        != "1908150b840b041908be702e"
    ):
        raise RuntimeError("lane-private HyBench APClient stride-4 patch is invalid")
    private_hybench_classpath = f"{apclient_patch_jar_path}:{shared_hybench_jar}"

    def write_hybench_props_readback(
        props_path: Path,
        output_dir: Path,
        phase: str,
    ) -> Dict[str, Any]:
        payload = props_path.read_text(encoding="utf-8")
        parsed = parse_hybench_properties(payload)
        validate_ap13_properties(parsed)
        evidence_path = output_dir / f"{phase}_hybench_props_readback.json"
        evidence = {
            "schema_version": 1,
            "phase": phase,
            "passed": True,
            "formal_name": frozen_ap_config["formal_name"],
            "runtime_properties_derived_from_frozen_benchmark_config": True,
            "benchmark_matrix_path": str(benchmark_matrix_path.resolve()),
            "benchmark_matrix_sha256": benchmark_matrix_sha256,
            "properties_path": str(props_path),
            "properties_sha256": points.sha256_file(props_path),
            "readback": {
                "tpclient": int(parsed["tpclient"]),
                "apclient": int(parsed["apclient"]),
                "xtpclient": int(parsed["xtpclient"]),
                "xapclient": int(parsed["xapclient"]),
                "ap_query_ids": [int(value) for value in parsed["ap_query_ids"].split(",")],
                "ap_query_weights": [
                    int(value) for value in parsed["ap_query_weights"].split(",")
                ],
                "ap_query_sequence": [
                    int(value) for value in parsed["ap_query_sequence"].split(",")
                ],
                "ap_query_sequence_limit": int(parsed["ap_query_sequence_limit"]),
            },
        }
        write_json(evidence_path, evidence)
        return {
            **evidence,
            "evidence_path": str(evidence_path),
            "evidence_sha256": points.sha256_file(evidence_path),
        }

    engine.IMAGE = os.environ["RA_PG_IMAGE"]
    engine.IMAGE_DIGEST = os.environ["RA_PG_IMAGE"].removeprefix("postgres@")

    original_machine_config = engine.machine_config

    def frozen_lane_machine_config() -> Dict[str, Any]:
        config = original_machine_config()
        nvme_device = os.stat("/dev/nvme1n1").st_rdev
        config["engines"]["postgresql"]["observed_device"] = (
            f"{os.major(nvme_device)}:{os.minor(nvme_device)}"
        )
        profile = config["resource_profiles"]["memory"]
        profile["cpuset_cpus"] = "16-23"
        profile["cpuset_mems"] = "0"
        profile["cpu_quota_cores"] = args.cpu_quota_cores
        profile["memory_bytes"] = args.memory_bytes
        profile["memory_swap_bytes"] = args.memory_bytes
        profile["io_read_bps"] = args.io_read_bps
        profile["io_write_bps"] = args.io_write_bps
        profile["pids_limit"] = 512
        return config

    engine.machine_config = frozen_lane_machine_config
    points.machine_config = frozen_lane_machine_config
    original_fixed_postgres_arguments = engine.fixed_postgres_arguments

    def fixed_postgres_arguments_with_statement_stats(
        profile: str,
        cost_parameters: Any = None,
    ) -> list[str]:
        arguments = original_fixed_postgres_arguments(profile, cost_parameters)
        arguments.extend([
            "-c", "shared_preload_libraries=pg_stat_statements",
            "-c", "compute_query_id=on",
            "-c", "pg_stat_statements.track=all",
        ])
        return arguments

    engine.fixed_postgres_arguments = fixed_postgres_arguments_with_statement_stats
    validation["resource_assignment"].update({
        "cpuset": "16-23",
        "cpuset_mems": "0",
        "cpu_quota_cores": args.cpu_quota_cores,
        "memory_bytes": args.memory_bytes,
        "memory_swap_bytes": args.memory_bytes,
        "io_read_bps": args.io_read_bps,
        "io_write_bps": args.io_write_bps,
        "pids_limit": 512,
    })

    def lane_cgroup_directory(controller: str, container_id: str) -> Path:
        candidates = (
            Path("/sys/fs/cgroup") / controller / "docker" / container_id,
            Path("/sys/fs/cgroup") / controller / "docker-limited" / container_id,
        )
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
        raise RuntimeError(
            f"Docker cgroup is missing for controller={controller} id={container_id}"
        )

    def validate_docker_io_limits(container_id: str, profile_name: str) -> None:
        requested = frozen_lane_machine_config()["resource_profiles"][profile_name]
        inspected = json.loads(
            engine.run(["docker", "inspect", container_id]).stdout
        )[0]["HostConfig"]
        expected_read = requested["io_read_bps"]
        expected_write = requested["io_write_bps"]
        actual_read = {row["Path"]: row["Rate"] for row in inspected["BlkioDeviceReadBps"]}
        actual_write = {row["Path"]: row["Rate"] for row in inspected["BlkioDeviceWriteBps"]}
        expected_path = "/dev/nvme1n1"
        if actual_read != {expected_path: expected_read}:
            raise RuntimeError(f"Docker read BPS metadata mismatch: {actual_read}")
        if actual_write != {expected_path: expected_write}:
            raise RuntimeError(f"Docker write BPS metadata mismatch: {actual_write}")
        blkio = lane_cgroup_directory("blkio", container_id)
        device = str(frozen_lane_machine_config()["engines"]["postgresql"]["observed_device"])
        for field, filename, expected in (
            ("read", "blkio.throttle.read_bps_device", expected_read),
            ("write", "blkio.throttle.write_bps_device", expected_write),
        ):
            rows = [line.split() for line in (blkio / filename).read_text(encoding="utf-8").splitlines()]
            if rows != [[device, str(expected)]]:
                raise RuntimeError(f"actual cgroup {field} BPS mismatch: {rows}")

    engine.apply_cgroup_io_limits = validate_docker_io_limits
    points.cgroup_directory = lane_cgroup_directory

    original_derive_resource_samples = points.derive_resource_samples

    def derive_resource_samples_with_total_io(
        rows: list[Dict[str, Any]],
        scenario: str,
    ) -> list[Dict[str, float]]:
        samples = original_derive_resource_samples(rows, scenario)
        profile = frozen_lane_machine_config()["resource_profiles"][scenario]
        read_cap = float(profile["io_read_bps"])
        write_cap = float(profile["io_write_bps"])
        total_cap = read_cap + write_cap
        if total_cap <= 0:
            raise RuntimeError("formal total I/O cap must be positive")
        for sample in samples:
            cpu_raw = float(sample["cpu_utilization_raw"])
            memory_raw = float(sample["memory_utilization_raw"])
            io_raw = (
                float(sample["read_bps"]) + float(sample["write_bps"])
            ) / total_cap
            raw_utilizations = {
                "cpu": cpu_raw,
                "memory": memory_raw,
                "io": io_raw,
            }
            invalid_raw = {
                name: value
                for name, value in raw_utilizations.items()
                if not math.isfinite(value) or value < 0.0
            }
            if invalid_raw:
                raise RuntimeError(
                    f"resource utilization counters are non-finite or negative: {invalid_raw}"
                )
            sample["cpu_utilization"] = cpu_raw
            sample["memory_utilization"] = memory_raw
            sample["io_utilization"] = io_raw
            sample["cpu_utilization_raw"] = cpu_raw
            sample["memory_utilization_raw"] = memory_raw
            sample["io_utilization_raw"] = io_raw
        return samples

    points.derive_resource_samples = derive_resource_samples_with_total_io

    original_execute_workload = points.execute_workload

    def common_cross_window_stability(
        samples: list[Dict[str, float]],
        workload: str,
    ) -> Dict[str, Any]:
        if workload != "hybench_ap":
            raise RuntimeError("this lane stability evaluator is restricted to HyBench AP")
        metric_fields = (
            "cpu_utilization",
            "memory_utilization",
            "io_utilization",
        )
        if not samples:
            return {
                "passed": False,
                "stable": False,
                "reason": "no_resource_samples",
                "complete_window_count": 0,
                "consecutive_stable_windows": 0,
                "earliest_stable_stop_seconds": None,
                "windows": [],
                "blocks": [],
                "source": "pg5_six_window_resource_mean_stability_v2",
            }

        window_seconds = 30
        required_windows = 6
        expected_samples = 30
        per_window_coverage_min = 0.9
        cumulative_coverage_min = 0.9
        cv_max = 0.1
        start_epoch = float(samples[0]["start_epoch"])
        end_epoch = float(samples[-1]["end_epoch"])
        elapsed = min(600.0, max(0.0, end_epoch - start_epoch))
        complete_window_count = min(int(elapsed // window_seconds), 20)
        aligned = [
            (
                (float(sample["start_epoch"]) + float(sample["end_epoch"])) / 2.0
                - start_epoch,
                sample,
            )
            for sample in samples
        ]

        windows = []
        for index in range(complete_window_count):
            lower = index * window_seconds
            upper = lower + window_seconds
            rows = [row for relative, row in aligned if lower <= relative < upper]
            coverage = min(1.0, len(rows) / expected_samples)
            means = {
                field: (
                    statistics.fmean(float(row[field]) for row in rows)
                    if rows
                    else None
                )
                for field in metric_fields
            }
            windows.append({
                "window_index": index,
                "start_elapsed_seconds": lower,
                "end_elapsed_seconds": upper,
                "sample_count": len(rows),
                "expected_sample_count": expected_samples,
                "coverage": coverage,
                "coverage_passed": coverage >= per_window_coverage_min,
                "metric_means": means,
            })

        def cross_window_cv(values: list[float]) -> Any:
            mean_value = statistics.fmean(values)
            if mean_value == 0.0:
                return None
            return statistics.pstdev(values) / abs(mean_value)

        blocks = []
        earliest_stop = None
        for end_index in range(required_windows, complete_window_count + 1):
            block_windows = windows[end_index - required_windows:end_index]
            cumulative_count = sum(window["sample_count"] for window in windows[:end_index])
            cumulative_coverage = min(
                1.0,
                cumulative_count / float(end_index * expected_samples),
            )
            metrics = {}
            for field in metric_fields:
                raw_means = [window["metric_means"][field] for window in block_windows]
                if any(value is None for value in raw_means):
                    cv_value = None
                else:
                    cv_value = cross_window_cv([float(value) for value in raw_means])
                metrics[field] = {
                    "window_means": raw_means,
                    "cross_window_cv": cv_value,
                    "passed": cv_value is not None and cv_value <= cv_max,
                }
            passed = bool(
                all(window["coverage_passed"] for window in block_windows)
                and cumulative_coverage >= cumulative_coverage_min
                and all(value["passed"] for value in metrics.values())
            )
            end_seconds = end_index * window_seconds
            block = {
                "start_window_index": end_index - required_windows,
                "end_window_index": end_index - 1,
                "end_elapsed_seconds": end_seconds,
                "cumulative_coverage": cumulative_coverage,
                "cumulative_coverage_passed": cumulative_coverage
                >= cumulative_coverage_min,
                "metrics": metrics,
                "passed": passed,
            }
            blocks.append(block)
            if passed and earliest_stop is None and end_seconds >= 180:
                earliest_stop = end_seconds

        total_expected = complete_window_count * expected_samples
        aggregate_count = sum(window["sample_count"] for window in windows)
        aggregate_coverage = (
            min(1.0, aggregate_count / float(total_expected)) if total_expected else 0.0
        )
        latest_passed = bool(blocks and blocks[-1]["passed"])
        return {
            "passed": earliest_stop is not None,
            "stable": earliest_stop is not None,
            "elapsed_seconds": elapsed,
            "complete_window_count": complete_window_count,
            "consecutive_stable_windows": required_windows if latest_passed else 0,
            "earliest_stable_stop_seconds": earliest_stop,
            "reached_hard_maximum": end_epoch - start_epoch >= 600,
            "aggregate_sample_count": aggregate_count,
            "aggregate_sample_coverage": aggregate_coverage,
            "per_window_coverage_min_inclusive": per_window_coverage_min,
            "cumulative_coverage_min_inclusive": cumulative_coverage_min,
            "cross_window_cv_max_inclusive": cv_max,
            "within_window_one_second_cv_required": False,
            "throughput_fields_required_for_stability": False,
            "initial_probe_seconds": 180,
            "windows": windows,
            "blocks": blocks,
            "source": "pg5_six_window_resource_mean_stability_v2",
        }

    points.resource_stability = common_cross_window_stability

    original_summarize_measurement = points.summarize_measurement

    def summarize_measurement_with_total_io(
        workload: str,
        scenario: str,
        output_dir: Path,
        execution: Dict[str, Any],
        sampler_path: Path,
        requested_seconds: int,
    ) -> Dict[str, Any]:
        summary = original_summarize_measurement(
            workload,
            scenario,
            output_dir,
            execution,
            sampler_path,
            requested_seconds,
        )
        profile = frozen_lane_machine_config()["resource_profiles"][scenario]
        derived_samples = points.derive_resource_samples(
            points.parse_jsonl(sampler_path), scenario,
        )
        mean_read_bps = (
            statistics.fmean(float(sample["read_bps"]) for sample in derived_samples)
            if derived_samples
            else None
        )
        mean_write_bps = (
            statistics.fmean(float(sample["write_bps"]) for sample in derived_samples)
            if derived_samples
            else None
        )
        summary["io_metric_semantics"] = {
            "formula": "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)",
            "read_bps": mean_read_bps,
            "write_bps": mean_write_bps,
            "read_cap_bps": int(profile["io_read_bps"]),
            "write_cap_bps": int(profile["io_write_bps"]),
            "io_total_cap_bps": int(profile["io_read_bps"] + profile["io_write_bps"]),
            "io_utilization": summary["resource_mean_utilization"]["io"],
            "legacy_max_semantics_used": False,
            "contract_sha256": io_utilization_contract_sha256,
        }
        summary["utilization_value_semantics"] = {
            "clipping_applied": False,
            "allowed_domain": "finite_and_non_negative_unbounded",
            "values_above_one_preserved": True,
            "stability_and_summary_use_raw_values": True,
        }
        formal_db_qps = execution.get("formal_db_qps")
        if (
            not isinstance(formal_db_qps, dict)
            or not isinstance(formal_db_qps.get("value"), dict)
            or formal_db_qps["value"].get("passed") is not True
        ):
            raise RuntimeError("formal measurement lacks passing DB QPS evidence")
        db_qps_value = float(formal_db_qps["value"]["db_qps"])
        if not math.isfinite(db_qps_value) or db_qps_value <= 0:
            raise RuntimeError(f"formal DB QPS is invalid: {db_qps_value}")
        summary["ap_qps"] = summary.get("qps")
        summary["qps"] = db_qps_value
        summary["db_qps"] = db_qps_value
        summary["qps_semantics"] = {
            "formal_qps_field": "db_qps",
            "source": "pg_stat_statements_sum_calls_delta_per_second",
            "ap_qps_preserved_as_diagnostic_only": True,
            "ap_qps_used_as_substitute": False,
            "evidence_path": formal_db_qps["evidence_path"],
            "evidence_sha256": formal_db_qps["evidence_sha256"],
        }
        points.atomic_json(output_dir / "measurement_summary.json", summary, exclusive=False)
        return summary

    points.summarize_measurement = summarize_measurement_with_total_io

    def online_resource_samples(
        workload: str,
        profile: str,
        sampler_path: Path,
        query_path: Path,
    ) -> list[Dict[str, float]]:
        if not sampler_path.is_file():
            return []
        sampler_rows = points.parse_jsonl(sampler_path)
        samples = points.derive_resource_samples(sampler_rows, profile)
        query_rows = points.parse_jsonl(query_path) if query_path.is_file() else []
        successful_epochs = [
            dt.datetime.fromisoformat(
                str(row["timestamp_utc"]).replace("Z", "+00:00")
            ).timestamp()
            for row in query_rows
            if row.get("event") == "query_complete" and row.get("success") is True
        ]
        for sample in samples:
            completed = sum(
                1
                for epoch in successful_epochs
                if sample["start_epoch"] < epoch <= sample["end_epoch"]
            )
            sample["qps"] = completed / float(sample["seconds"])
        return samples

    def snapshot_formal_db_calls(*, ensure_extension: bool = False) -> Dict[str, Any]:
        """Snapshot database-wide completed statement calls using server time."""
        container_name, _ = points.container_spec("hybench")
        base_command = [
            "docker", "exec", container_name,
            "psql", "-X", "-A", "-t", "-F", "\t",
            "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "hybench_sf10x",
        ]
        if ensure_extension:
            extension = subprocess.run(
                base_command + ["-c", "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if extension.returncode != 0:
                raise RuntimeError(
                    "failed to create pg_stat_statements extension: "
                    f"stdout={extension.stdout.strip()!r} stderr={extension.stderr.strip()!r}"
                )
        sql = (
            "SELECT extract(epoch FROM clock_timestamp())::double precision, "
            "COALESCE(SUM(calls), 0)::bigint "
            "FROM pg_stat_statements "
            "WHERE dbid = (SELECT oid FROM pg_database "
            "WHERE datname = current_database()) "
            "AND query NOT ILIKE '%pg_stat_statements%'"
        )
        completed = subprocess.run(
            base_command + ["-c", sql],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "pg_stat_statements snapshot failed: "
                f"stdout={completed.stdout.strip()!r} stderr={completed.stderr.strip()!r}"
            )
        rows = [line for line in completed.stdout.splitlines() if line.strip()]
        if len(rows) != 1:
            raise RuntimeError(f"unexpected pg_stat_statements snapshot output: {rows!r}")
        fields = rows[0].split("\t")
        if len(fields) != 2:
            raise RuntimeError(f"invalid pg_stat_statements snapshot row: {rows[0]!r}")
        server_epoch = float(fields[0])
        calls = int(fields[1])
        if not math.isfinite(server_epoch) or server_epoch <= 0 or calls < 0:
            raise RuntimeError(
                f"invalid pg_stat_statements snapshot values: epoch={server_epoch} calls={calls}"
            )
        return {
            "server_epoch": server_epoch,
            "sum_calls": calls,
            "host_epoch_after_snapshot": time.time(),
        }

    def evaluate_formal_db_qps(
        start: Dict[str, Any],
        end: Dict[str, Any],
    ) -> Dict[str, Any]:
        elapsed_seconds = float(end["server_epoch"]) - float(start["server_epoch"])
        delta_calls = int(end["sum_calls"]) - int(start["sum_calls"])
        db_qps = delta_calls / elapsed_seconds if elapsed_seconds > 0 else None
        passed = bool(
            elapsed_seconds > 0
            and delta_calls > 0
            and db_qps is not None
            and math.isfinite(db_qps)
            and db_qps > 0
        )
        return {
            "schema_version": 1,
            "gate_id": "formal_db_qps_pg_stat_statements_sum_calls_v1",
            "passed": passed,
            "source": "pg_stat_statements_sum_calls_delta_per_second",
            "database": "hybench_sf10x",
            "scope": "all_completed_statements_in_formal_database_during_measurement_window",
            "snapshot_query_excluded_by_text_filter": True,
            "start": start,
            "end": end,
            "elapsed_seconds_server_clock": elapsed_seconds,
            "delta_sum_calls": delta_calls,
            "db_qps": db_qps,
            "ap_qps_used_as_substitute": False,
            "fail_closed_if_unavailable": True,
        }

    def execute_hybench_with_online_stability(
        workload: str,
        duration_seconds: int,
        output_dir: Path,
        phase: str,
    ) -> Dict[str, Any]:
        if phase != "measurement" or workload not in {"hybench_ap", "hybench_xp"}:
            db_qps_preflight = None
            if phase == "warmup" and workload == "hybench_ap":
                preflight_path = output_dir / "preflight_db_qps_start_snapshot.json"
                try:
                    preflight_snapshot = snapshot_formal_db_calls(ensure_extension=True)
                    db_qps_preflight = {
                        "schema_version": 1,
                        "gate_id": "formal_db_qps_start_snapshot_preflight_v1",
                        "passed": True,
                        "source": "pg_stat_statements_sum_calls",
                        "database": "hybench_sf10x",
                        "container_kind": "hybench",
                        "snapshot": preflight_snapshot,
                        "ap_qps_used_as_substitute": False,
                        "before_warmup_and_formal_measurement": True,
                    }
                except Exception as exc:
                    db_qps_preflight = {
                        "schema_version": 1,
                        "gate_id": "formal_db_qps_start_snapshot_preflight_v1",
                        "passed": False,
                        "source": "pg_stat_statements_sum_calls",
                        "database": "hybench_sf10x",
                        "container_kind": "hybench",
                        "error": f"{type(exc).__name__}: {exc}",
                        "ap_qps_used_as_substitute": False,
                        "before_warmup_and_formal_measurement": True,
                    }
                write_json(preflight_path, db_qps_preflight)
                if db_qps_preflight.get("passed") is not True:
                    raise RuntimeError(
                        f"formal DB QPS preflight failed; evidence={preflight_path}"
                    )
            saved_formal_jdbc_jar = points.FORMAL_JDBC_JAR
            saved_hybench_jar = points.HYBENCH_JAR
            points.FORMAL_JDBC_JAR = private_formal_jdbc_classpath
            points.HYBENCH_JAR = private_hybench_classpath
            try:
                execution = original_execute_workload(
                    workload, duration_seconds, output_dir, phase,
                )
            finally:
                points.FORMAL_JDBC_JAR = saved_formal_jdbc_jar
                points.HYBENCH_JAR = saved_hybench_jar
            if workload == "hybench_ap":
                execution["hybench_props_readback"] = write_hybench_props_readback(
                    Path(str(execution["properties_path"])), output_dir, phase,
                )
                if db_qps_preflight is not None:
                    execution["formal_db_qps_preflight"] = {
                        "evidence_path": str(preflight_path),
                        "evidence_sha256": points.sha256_file(preflight_path),
                        "value": db_qps_preflight,
                    }
            return execution
        if workload != "hybench_ap":
            raise RuntimeError("this lane online controller is restricted to HyBench AP")
        if duration_seconds != 600:
            raise RuntimeError("online controller requires a 600-second hard maximum")

        props_path = output_dir / "hybench_measurement.props"
        props_path.write_text(
            points.runtime_hybench_properties(workload, duration_seconds),
            encoding="utf-8",
        )
        os.chmod(props_path, 0o600)
        props_readback = write_hybench_props_readback(props_path, output_dir, phase)
        result_log = output_dir / "measurement_queries.jsonl"
        transaction_log = output_dir / "measurement_transactions.jsonl"
        stdout_path = output_dir / "measurement_hybench.log"
        sampler_path = output_dir / "measurement_resource_samples.jsonl"
        controller_path = output_dir / "measurement_online_stability_controller.jsonl"
        resource_gate_path = output_dir / "measurement_r0_resource_gate.json"
        db_qps_path = output_dir / "measurement_db_qps.json"
        command = [
            "java",
            "-cp",
            f"{points.JDBC_JAR}:{private_formal_jdbc_classpath}:{private_hybench_classpath}",
            "com.hybench.HyBench",
            "-t",
            "runap",
            "-c",
            str(props_path),
            "-f",
            str(points.HYBENCH_STMTS),
        ]
        environment = dict(os.environ)
        environment["FORMAL_PG_RESULT_LOG"] = str(result_log)
        environment["FORMAL_PG_TXN_LOG"] = str(transaction_log)
        try:
            db_qps_start = snapshot_formal_db_calls()
        except Exception as exc:
            write_json(db_qps_path, {
                "schema_version": 1,
                "gate_id": "formal_db_qps_pg_stat_statements_sum_calls_v1",
                "passed": False,
                "source": "pg_stat_statements_sum_calls_delta_per_second",
                "database": "hybench_sf10x",
                "stage": "start_snapshot",
                "error": f"{type(exc).__name__}: {exc}",
                "ap_qps_used_as_substitute": False,
                "fail_closed_if_unavailable": True,
            })
            raise RuntimeError(
                f"formal DB QPS start snapshot failed; evidence={db_qps_path}"
            ) from exc
        began = time.monotonic()
        last_complete_windows = -1
        controller_stop_reason = "natural_process_exit"
        raw_returncode: int
        final_assessment: Dict[str, Any] = {}
        resource_gate_evaluated = False

        with (
            stdout_path.open("x", encoding="utf-8") as stdout,
            controller_path.open("x", encoding="utf-8") as controller_log,
        ):
            process = subprocess.Popen(
                command,
                cwd=points.HYBENCH_ROOT,
                env=environment,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                text=True,
            )
            while True:
                raw = process.poll()
                elapsed = time.monotonic() - began
                if raw is not None:
                    raw_returncode = int(raw)
                    if elapsed >= 600:
                        controller_stop_reason = "natural_exit_at_or_after_hard_maximum"
                    break

                if elapsed >= 180:
                    try:
                        samples = online_resource_samples(
                            workload, "memory", sampler_path, result_log,
                        )
                        assessment = points.resource_stability(samples, workload)
                        complete_windows = int(assessment.get("complete_window_count") or 0)
                        if complete_windows > last_complete_windows:
                            last_complete_windows = complete_windows
                            final_assessment = assessment
                            check = {
                                "schema_version": 1,
                                "controller_pid": os.getpid(),
                                "elapsed_seconds": elapsed,
                                "complete_window_count": complete_windows,
                                "consecutive_stable_windows": assessment.get(
                                    "consecutive_stable_windows"
                                ),
                                "earliest_stable_stop_seconds": assessment.get(
                                    "earliest_stable_stop_seconds"
                                ),
                                "stable": assessment.get("stable"),
                                "latest_six_window_block": (
                                    assessment.get("blocks", [])[-1]
                                    if assessment.get("blocks")
                                    else None
                                ),
                                "decision": "continue",
                            }
                            if complete_windows >= 6 and not resource_gate_evaluated:
                                resource_gate_evaluated = True
                                first_block = assessment["blocks"][0]
                                resource_means = {
                                    resource: statistics.fmean(
                                        float(value)
                                        for value in first_block["metrics"][
                                            f"{resource}_utilization"
                                        ]["window_means"]
                                    )
                                    for resource in ("cpu", "memory", "io")
                                }
                                resource_checks = {
                                    "memory": 0.85 <= resource_means["memory"] <= 0.95,
                                    "cpu": 0.30 <= resource_means["cpu"] <= 0.50,
                                    "io": 0.30 <= resource_means["io"] <= 0.50,
                                }
                                resource_gate_passed = all(resource_checks.values())
                                resource_gate_evidence = {
                                    "schema_version": 1,
                                    "gate_id": "pg5_r0_single_bottleneck_gate_v2",
                                    "target_resource": "memory",
                                    "evaluation_end_elapsed_seconds": first_block[
                                        "end_elapsed_seconds"
                                    ],
                                    "window_count": 6,
                                    "resource_mean_utilization": resource_means,
                                    "checks": resource_checks,
                                    "target_band_inclusive": [0.85, 0.95],
                                    "non_target_band_inclusive": [0.30, 0.50],
                                    "passed": resource_gate_passed,
                                    "contract_path": str(args.resource_gate_contract.resolve()),
                                    "contract_sha256": resource_gate_contract_sha256,
                                }
                                write_json(resource_gate_path, resource_gate_evidence)
                                check["r0_resource_gate"] = resource_gate_evidence
                                if not resource_gate_passed:
                                    check["decision"] = "formal_r0_resource_gate_failed"
                                    controller_stop_reason = "r0_resource_gate_failed"
                            if (
                                check["decision"] == "continue"
                                and resource_gate_evaluated
                                and assessment.get("stable") is True
                                and complete_windows >= 6
                                and int(assessment.get("consecutive_stable_windows") or 0) >= 6
                            ):
                                check["decision"] = "stable_early_stop"
                                controller_stop_reason = "six_consecutive_stable_windows"
                            controller_log.write(
                                json.dumps(check, ensure_ascii=False, sort_keys=True) + "\n"
                            )
                            controller_log.flush()
                            os.fsync(controller_log.fileno())
                            if check["decision"] in {
                                "stable_early_stop",
                                "formal_r0_resource_gate_failed",
                            }:
                                process.terminate()
                                try:
                                    raw_returncode = int(process.wait(timeout=30))
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                    raw_returncode = int(process.wait(timeout=30))
                                break
                    except Exception as exc:
                        controller_stop_reason = "online_controller_assessment_error"
                        controller_log.write(
                            json.dumps(
                                {
                                    "schema_version": 1,
                                    "controller_pid": os.getpid(),
                                    "elapsed_seconds": elapsed,
                                    "decision": "formal_fail_closed",
                                    "assessment_error": f"{type(exc).__name__}: {exc}",
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                            + "\n"
                        )
                        controller_log.flush()
                        os.fsync(controller_log.fileno())
                        process.terminate()
                        try:
                            raw_returncode = int(process.wait(timeout=30))
                        except subprocess.TimeoutExpired:
                            process.kill()
                            raw_returncode = int(process.wait(timeout=30))
                        break

                if elapsed >= 600:
                    controller_stop_reason = "hard_maximum_600_seconds"
                    process.terminate()
                    try:
                        raw_returncode = int(process.wait(timeout=30))
                    except subprocess.TimeoutExpired:
                        process.kill()
                        raw_returncode = int(process.wait(timeout=30))
                    break
                time.sleep(1.0)

            stdout.flush()
            os.fsync(stdout.fileno())

        try:
            db_qps_end = snapshot_formal_db_calls()
            db_qps_evidence = evaluate_formal_db_qps(db_qps_start, db_qps_end)
        except Exception as exc:
            db_qps_evidence = {
                "schema_version": 1,
                "gate_id": "formal_db_qps_pg_stat_statements_sum_calls_v1",
                "passed": False,
                "source": "pg_stat_statements_sum_calls_delta_per_second",
                "database": "hybench_sf10x",
                "stage": "end_snapshot_or_evaluation",
                "start": db_qps_start,
                "error": f"{type(exc).__name__}: {exc}",
                "ap_qps_used_as_substitute": False,
                "fail_closed_if_unavailable": True,
            }
        write_json(db_qps_path, db_qps_evidence)
        if db_qps_evidence.get("passed") is not True:
            raise RuntimeError(f"formal DB QPS gate failed; evidence={db_qps_path}")

        controller_directed = controller_stop_reason in {
            "six_consecutive_stable_windows",
            "hard_maximum_600_seconds",
        }
        logical_exit_code = 0 if controller_directed else raw_returncode
        return {
            "command": command,
            "exit_code": logical_exit_code,
            "raw_process_returncode": raw_returncode,
            "wall_seconds": time.monotonic() - began,
            "properties_path": str(props_path),
            "properties_sha256": points.sha256_file(props_path),
            "hybench_props_readback": props_readback,
            "stdout_path": str(stdout_path),
            "stdout_sha256": points.sha256_file(stdout_path),
            "query_log_path": str(result_log),
            "query_log_sha256": points.sha256_file(result_log) if result_log.is_file() else None,
            "transaction_log_path": str(transaction_log),
            "transaction_log_sha256": (
                points.sha256_file(transaction_log) if transaction_log.is_file() else None
            ),
            "formal_db_qps": {
                "evidence_path": str(db_qps_path),
                "evidence_sha256": points.sha256_file(db_qps_path),
                "value": db_qps_evidence,
            },
            "r0_resource_gate": (
                {
                    "evidence_path": str(resource_gate_path),
                    "evidence_sha256": points.sha256_file(resource_gate_path),
                    "value": points.read_json(resource_gate_path),
                }
                if resource_gate_path.is_file()
                else None
            ),
            "online_stability_controller": {
                "controller_pid": os.getpid(),
                "minimum_measurement_seconds": 180,
                "initial_probe_seconds": 180,
                "maximum_measurement_seconds": 600,
                "window_seconds": 30,
                "required_consecutive_stable_windows": 6,
                "stability_metric_fields": [
                    "cpu_utilization",
                    "memory_utilization",
                    "io_utilization",
                ],
                "throughput_fields_required_for_stability": False,
                "stop_reason": controller_stop_reason,
                "controller_directed_process_stop": controller_directed,
                "raw_process_returncode": raw_returncode,
                "logical_exit_code": logical_exit_code,
                "final_online_assessment": final_assessment,
                "evidence_path": str(controller_path),
                "evidence_sha256": points.sha256_file(controller_path),
            },
        }

    points.execute_workload = execute_hybench_with_online_stability

    lane_runtime_root = (args.lane_root / "runtime").resolve()

    def restore_lane_copy(kind: str, token: str) -> Dict[str, Any]:
        manifest = points.validate_template(kind)
        lower = Path(str(manifest["resolved_pgdata"]))
        root = points.working_root() / "postgresql" / kind / token
        resolved_root = root.resolve()
        resolved_root.relative_to(lane_runtime_root)
        if root.exists() or root.is_symlink():
            raise RuntimeError(f"fresh runtime root already exists: {root}")
        merged = root / "pgdata"
        merged.mkdir(parents=True, mode=0o700)
        started = time.monotonic()
        subprocess.run(
            ["cp", "-a", str(lower) + "/.", str(merged)],
            check=True,
        )
        copied_bytes = du_bytes(merged)
        expected_bytes = int(manifest["build_runtime"]["pgdata_bytes"])
        if copied_bytes != expected_bytes:
            raise RuntimeError(
                f"fresh physical copy size mismatch: expected={expected_bytes} actual={copied_bytes}"
            )
        control = merged / "global" / "pg_control"
        control_sha256 = points.sha256_file(control)
        expected_control_sha256 = str(
            manifest["build_runtime"]["pg_control_sha256_after_clean_shutdown"]
        )
        if control_sha256 != expected_control_sha256:
            raise RuntimeError("fresh physical copy pg_control checksum mismatch")
        subprocess.run(["chmod", "-R", "u+w", str(merged)], check=True)
        mount = subprocess.run(
            ["findmnt", "-T", str(merged), "-n", "-o", "SOURCE,FSTYPE,OPTIONS"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        return {
            "template_manifest_sha256": manifest["resolved_template_manifest_sha256"],
            "template_pgdata": str(lower),
            "runtime_root": str(root),
            "merged": str(merged),
            "restore_strategy": "lane_owned_physical_cp_no_reflink_option",
            "copied_pgdata_bytes": copied_bytes,
            "copied_pg_control_sha256": control_sha256,
            "copy_wall_seconds": time.monotonic() - started,
            "cache_eviction": "skipped_no_host_sudo_preserve_restore_cache",
            "cache_state_inherited": True,
            "xp_committed_state_inherited": False,
            "mount_readback": mount,
            "frozen_template_modified": False,
            "background_load_allowed": True,
        }

    def cleanup_lane_copy(kind: str, restore: Dict[str, Any]) -> Dict[str, Any]:
        name, _ = points.container_spec(kind)
        points.remove_stopped_container(name)
        root = Path(str(restore["runtime_root"])).resolve()
        root.relative_to(lane_runtime_root)
        runtime_bytes = int(restore.get("copied_pgdata_bytes") or 0)
        if root.exists():
            cleanup_name = f"pg5hya201_cleanup_{secrets.token_hex(4)}"
            subprocess.run(
                [
                    "docker", "run", "--rm", "--name", cleanup_name,
                    "--volume", f"{root}:/cleanup", "--entrypoint", "/bin/sh",
                    engine.IMAGE, "-c", "find /cleanup -mindepth 1 -delete",
                ],
                check=True,
            )
            root.rmdir()
        return {
            "cleanup_complete": not root.exists(),
            "discarded_transient_runtime_bytes": runtime_bytes,
            "discarded_restore_strategy": restore.get("restore_strategy"),
            "template_preserved": True,
        }

    points.restore_overlay = restore_lane_copy
    points.cleanup_overlay = cleanup_lane_copy

    baseline_root = args.lane_root / "results" / "shared_baseline"
    point_dir = baseline_root / f"P0_repeat_1_{args.attempt_id}"
    invocation = {
        "schema_version": 1,
        "campaign_id": os.environ["RA_PG_CAMPAIGN_ID"],
        "lane_id": "hybench_ap_node01",
        "case_id": "pg_hybench_ap_memory",
        "point_id": "P0",
        "repeat_id": 1,
        "attempt_id": args.attempt_id,
        "warmup_seconds": 60,
        "measurement_max_seconds": args.measurement_seconds,
        "measurement_min_seconds": 180,
        "stable_early_stop_requested": True,
        "stable_early_stop_runtime_support": True,
        "utilization_clipping": False,
        "utilization_domain": "finite_and_non_negative_unbounded",
        "utilization_values_above_one_preserved": True,
        "online_stability_controller": {
            "mode": "inline_runner_process",
            "controller_pid": os.getpid(),
            "sample_interval_seconds": 1,
            "window_seconds": 30,
            "required_consecutive_stable_windows": 6,
            "minimum_measurement_seconds": 180,
            "initial_probe_seconds": 180,
            "maximum_measurement_seconds": 600,
            "per_window_coverage_min_inclusive": 0.9,
            "cumulative_coverage_min_inclusive": 0.9,
            "cross_window_cv_max_inclusive": 0.1,
            "within_window_one_second_cv_required": False,
            "stability_metric_fields": [
                "cpu_utilization",
                "memory_utilization",
                "io_utilization",
            ],
            "throughput_fields_required_for_stability": False,
            "evaluator": "pg5_six_window_resource_mean_stability_v2",
            "evidence_relative_path": (
                f"results/shared_baseline/P0_repeat_1_{args.attempt_id}/"
                "measurement_online_stability_controller.jsonl"
            ),
        },
        "background_load_allowed": True,
        "hybench_runtime_semantics": {
            "formal_name": frozen_ap_config["formal_name"],
            "runtime_properties_derived_from_frozen_benchmark_config": True,
            "benchmark_matrix_path": str(benchmark_matrix_path.resolve()),
            "benchmark_matrix_sha256": benchmark_matrix_sha256,
            "ap_clients": expected_ap_clients,
            "ap_query_ids": expected_ap_query_ids,
            "ap_query_weights": expected_ap_query_weights,
            "selection_policy": "fixed_stratified_interleaved_sequence_stride4",
            "weighted_random_sampling_disabled_by_configured_sequence": True,
            "ap_query_sequence": expected_ap_query_sequence,
            "ap_query_sequence_limit": 0,
            "ap_query_sequence_contract_path": str(ap_sequence_contract_path.resolve()),
            "ap_query_sequence_contract_sha256": ap_sequence_contract_sha256,
            "apclient_patch_jar_path": str(apclient_patch_jar_path.resolve()),
            "apclient_patch_jar_sha256": apclient_patch_jar_sha256,
            "apclient_patch_summary_path": str(apclient_patch_summary_path.resolve()),
            "apclient_patch_summary_sha256": points.sha256_file(
                apclient_patch_summary_path
            ),
            "shared_hybench_jar_path": str(shared_hybench_jar.resolve()),
            "shared_hybench_jar_sha256": shared_hybench_jar_sha256,
            "warmup_properties_readback_relative_path": (
                f"results/shared_baseline/P0_repeat_1_{args.attempt_id}/"
                "warmup_hybench_props_readback.json"
            ),
            "measurement_properties_readback_relative_path": (
                f"results/shared_baseline/P0_repeat_1_{args.attempt_id}/"
                "measurement_hybench_props_readback.json"
            ),
        },
        "jdbc_resultset_lifecycle_patch": {
            "scope": "lane_private_classpath_precedence_only",
            "shared_frozen_driver_mutated": False,
            "root_cause": (
                "shared FormalPgCommitDriver drained and closed executeQuery ResultSet "
                "before HyBench AP2 called next()"
            ),
            "fix": (
                "lazy ResultSet proxy hashes rows as the client consumes them, drains "
                "unread rows on close, then commits and records evidence"
            ),
            "source_path": str(fixed_driver_source_path),
            "source_sha256": fixed_driver_source_sha256,
            "jar_path": str(fixed_driver_jar_path),
            "jar_sha256": fixed_driver_jar_sha256,
            "shared_formal_jdbc_jar": str(shared_formal_jdbc_jar),
            "shared_formal_jdbc_jar_sha256": shared_formal_jdbc_jar_sha256,
            "regression_summary_path": str(fixed_driver_regression_path),
            "regression_summary_sha256": fixed_driver_regression_sha256,
            "regression_ap1_through_ap13_all_passed": True,
        },
        "formal_throughput_semantics": {
            "required_for_outer_entry": True,
            "source": "pg_stat_statements_sum_calls_delta_per_second",
            "database": "hybench_sf10x",
            "shared_preload_libraries": "pg_stat_statements",
            "compute_query_id": "on",
            "pg_stat_statements_track": "all",
            "extension_created_before_warmup": True,
            "measurement_window_snapshots": "immediately_before_and_after_formal_workload_process",
            "ap_qps_used_as_substitute": False,
            "fail_closed_if_unavailable": True,
        },
        "frozen_resource_contract": {
            "cpu_quota_cores": args.cpu_quota_cores,
            "cpuset_cpus": "16-23",
            "cpuset_mems": "0",
            "memory_bytes": args.memory_bytes,
            "memory_swap_bytes": args.memory_bytes,
            "io_read_bps": args.io_read_bps,
            "io_write_bps": args.io_write_bps,
            "io_total_bps": args.io_read_bps + args.io_write_bps,
            "io_utilization_formula": (
                "(read_bps + write_bps) / (read_cap_bps + write_cap_bps)"
            ),
            "pids_limit": 512,
            "max_parallel_workers_per_gather": 2
        },
        "resource_assignment": validation["resource_assignment"],
        "common_contracts": {
            "scoring": {
                "path": str(args.scoring_contract.resolve()),
                "sha256": scoring_contract_sha256,
                "value": scoring_contract,
            },
            "r0_resource_gate": {
                "path": str(args.resource_gate_contract.resolve()),
                "sha256": resource_gate_contract_sha256,
                "value": resource_gate_contract,
            },
            "r0_stability_gate": {
                "path": str(args.stability_contract.resolve()),
                "sha256": stability_contract_sha256,
                "value": stability_contract,
            },
            "io_utilization": {
                "path": str(args.io_utilization_contract.resolve()),
                "sha256": io_utilization_contract_sha256,
                "value": io_utilization_contract,
            },
        },
        "host_at_start": host_snapshot(),
    }
    write_json(baseline_root / f"baseline_invocation_{args.attempt_id}.json", invocation)

    value = points.run_cost_point(
        "pg_hybench_ap_memory",
        "P0",
        1,
        point_dir,
        points.default_cost_parameters(),
        evidence_context={
            "purpose": "shared_baseline_before_common_outer_methods",
            "background_load_allowed": True,
            "host_wide_idle_gate_skipped": True,
            "method_results_must_reuse_this_baseline": True,
            "common_scoring_contract_sha256": scoring_contract_sha256,
            "r0_resource_gate_contract_sha256": resource_gate_contract_sha256,
            "r0_stability_gate_contract_sha256": stability_contract_sha256,
            "io_utilization_contract_sha256": io_utilization_contract_sha256,
            "ap_query_sequence_contract_sha256": ap_sequence_contract_sha256,
            "formal_db_qps_required_for_outer_entry": True,
            "ap_qps_used_as_db_qps_substitute": False,
            "utilization_clipping": False,
            "utilization_domain": "finite_and_non_negative_unbounded",
        },
        host_lock_already_held=True,
        warmup_seconds=60,
        measurement_seconds=args.measurement_seconds,
    )
    resource_gate_path = point_dir / "measurement_r0_resource_gate.json"
    if not resource_gate_path.is_file():
        raise RuntimeError("formal R0 85-95 resource gate evidence is missing")
    resource_gate_evidence = points.read_json(resource_gate_path)
    correctness_gate = evaluate_ap13_measurement_correctness(
        point_dir / "measurement_queries.jsonl",
        expected_query_hash_by_id,
    )
    correctness_gate_path = point_dir / "measurement_ap13_correctness_gate.json"
    write_json(correctness_gate_path, correctness_gate)
    measurement_summary_path = point_dir / "measurement_summary.json"
    if not measurement_summary_path.is_file():
        raise RuntimeError("formal measurement summary is missing")
    measurement_summary = points.read_json(measurement_summary_path)
    resource_stability = measurement_summary.get("resource_stability")
    stability_passed = bool(
        isinstance(resource_stability, dict)
        and resource_stability.get("stable") is True
        and resource_stability.get("passed") is True
    )
    db_qps_path = point_dir / "measurement_db_qps.json"
    db_qps_evidence = points.read_json(db_qps_path) if db_qps_path.is_file() else None
    db_qps_value = (
        db_qps_evidence.get("db_qps")
        if isinstance(db_qps_evidence, dict)
        else None
    )
    db_qps_passed = bool(
        isinstance(db_qps_evidence, dict)
        and db_qps_evidence.get("passed") is True
        and isinstance(db_qps_value, (int, float))
        and not isinstance(db_qps_value, bool)
        and math.isfinite(float(db_qps_value))
        and float(db_qps_value) > 0
        and db_qps_evidence.get("ap_qps_used_as_substitute") is False
    )
    outer_entry_guard = {
        "schema_version": 1,
        "gate_id": "hybench_ap_common_outer_entry_v1",
        "passed": bool(
            resource_gate_evidence.get("passed") is True
            and stability_passed
            and correctness_gate["status"] == "passed"
            and db_qps_passed
        ),
        "checks": {
            "r0_resource_gate_passed": resource_gate_evidence.get("passed") is True,
            "six_window_resource_stability_passed": stability_passed,
            "ap13_correctness_gate_passed": correctness_gate["status"] == "passed",
            "formal_db_qps_passed": db_qps_passed,
        },
        "formal_db_qps": db_qps_value,
        "formal_db_qps_source": "pg_stat_statements_sum_calls_delta_per_second",
        "ap_qps_used_as_substitute": False,
        "r0_resource_gate_path": str(resource_gate_path),
        "stability_evidence_path": str(measurement_summary_path),
        "correctness_gate_path": str(correctness_gate_path),
        "db_qps_evidence_path": str(db_qps_path),
        "fail_closed": True,
    }
    outer_entry_guard_path = point_dir / "measurement_outer_entry_guard.json"
    write_json(outer_entry_guard_path, outer_entry_guard)
    if outer_entry_guard["passed"] is not True:
        raise RuntimeError(
            "formal outer-entry gate failed: "
            f"checks={outer_entry_guard['checks']} evidence={outer_entry_guard_path}"
        )
    write_json(
        baseline_root / f"baseline_complete_{args.attempt_id}.json",
        {
            "status": value.get("status"),
            "point_result": str(point_dir / "point_result.json"),
            "ap13_correctness_gate": str(correctness_gate_path),
            "ap13_correctness_gate_sha256": points.sha256_file(correctness_gate_path),
            "db_qps": db_qps_value,
            "db_qps_evidence": str(db_qps_path),
            "db_qps_evidence_sha256": points.sha256_file(db_qps_path),
            "outer_entry_guard": str(outer_entry_guard_path),
            "outer_entry_guard_sha256": points.sha256_file(outer_entry_guard_path),
            "completed_at_epoch": time.time(),
        },
    )
    print(json.dumps({"status": value.get("status"), "point_dir": str(point_dir)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
