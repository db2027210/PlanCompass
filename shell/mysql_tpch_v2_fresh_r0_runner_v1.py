#!/usr/bin/env python3
"""Run and seal one real 181-sample/180-second TPC-H fresh R0."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Mapping


DB_COUNTER_NAMES = (
    "Questions",
    "Com_select",
    "Com_insert",
    "Com_update",
    "Com_delete",
    "Threads_running",
    "Innodb_rows_read",
    "Innodb_rows_inserted",
    "Innodb_rows_updated",
    "Innodb_rows_deleted",
)
SPILL_COUNTER_NAMES = (
    "Created_tmp_tables",
    "Created_tmp_disk_tables",
    "Sort_merge_passes",
)
HEX64 = re.compile(r"[0-9a-f]{64}$")


class R0Error(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path: Path, label: str) -> dict[str, Any]:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise R0Error(f"{label} must be a regular file")
    expected = f"{sha256(path)}  {path.name}\n"
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise R0Error(f"{label} sidecar differs")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise R0Error(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise R0Error(f"{label} must be an object")
    return value


def read_token(path: Path) -> str:
    path = Path(path)
    if (
        path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
    ):
        raise R0Error("main-agent token file must be regular 0600")
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32 or any(character.isspace() for character in token):
        raise R0Error("main-agent token differs")
    return token


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if any(candidate.exists() or candidate.is_symlink() for candidate in (path, sidecar)):
        raise R0Error("refusing to overwrite fresh-R0 evidence")
    payload = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    sidecar_temporary = sidecar.with_name(f".{sidecar.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with sidecar_temporary.open("xb") as stream:
            stream.write(
                f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n".encode(
                    "ascii"
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
        os.chmod(path, 0o600)
        os.chmod(sidecar, 0o600)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def _run(argv: list[str], timeout: int) -> str:
    completed = subprocess.run(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise R0Error(
            f"command failed rc={completed.returncode}: {argv!r}; "
            f"stderr={completed.stderr[-2000:]}"
        )
    return completed.stdout


def mysql_status(
    client_container: str, defaults_file: Path
) -> dict[str, int]:
    names = DB_COUNTER_NAMES + SPILL_COUNTER_NAMES
    sql = (
        "SELECT VARIABLE_NAME,VARIABLE_VALUE FROM performance_schema."
        "global_status WHERE VARIABLE_NAME IN (%s)"
        % ",".join("'%s'" % name for name in names)
    )
    output = _run(
        [
            "docker",
            "exec",
            client_container,
            "/usr/bin/mysql",
            f"--defaults-extra-file={defaults_file}",
            "--protocol=TCP",
            "--host=127.0.0.1",
            "--port=65103",
            "--user=root",
            "--batch",
            "--raw",
            "--skip-column-names",
            "--execute",
            sql,
        ],
        timeout=3,
    )
    canonical_names = {name.lower(): name for name in names}
    observed: dict[str, int] = {}
    for line in output.splitlines():
        pieces = line.split("\t")
        canonical_name = (
            canonical_names.get(pieces[0].lower())
            if len(pieces) == 2
            else None
        )
        if canonical_name is not None:
            observed[canonical_name] = int(pieces[1])
    if set(observed) != set(names):
        raise R0Error(
            "database counter coverage incomplete: "
            + ",".join(sorted(set(names) - set(observed)))
        )
    return observed


def explain_documents(
    bundle: Path, client_container: str, defaults_file: Path
) -> list[dict[str, Any]]:
    query_dir = bundle / "qualification/sql/tpch"
    paths = sorted(
        query_dir.glob("Q*.sql"),
        key=lambda path: int(path.stem[1:]),
    )
    if [path.name for path in paths] != [
        f"Q{index}.sql" for index in range(1, 23)
    ]:
        raise R0Error("TPC-H SQL closure differs")
    result = []
    for path in paths:
        sql = path.read_text(encoding="utf-8").strip().rstrip(";")
        output = _run(
            [
                "docker",
                "exec",
                client_container,
                "/usr/bin/mysql",
                f"--defaults-extra-file={defaults_file}",
                "--protocol=TCP",
                "--host=127.0.0.1",
                "--port=65103",
                "--user=root",
                "--database=tpch",
                "--batch",
                "--raw",
                "--skip-column-names",
                "--execute",
                "EXPLAIN FORMAT=JSON " + sql,
            ],
            timeout=60,
        ).strip()
        try:
            document = json.loads(output)
        except json.JSONDecodeError as exc:
            raise R0Error(f"{path.name} EXPLAIN JSON differs") from exc
        if not isinstance(document, dict) or "query_block" not in document:
            raise R0Error(f"{path.name} EXPLAIN structure differs")
        result.append(
            {
                "query": path.name,
                "sql_sha256": sha256(path),
                "explain_sha256": hashlib.sha256(
                    canonical(document)
                ).hexdigest(),
                "document": document,
            }
        )
    return result


def _walk_explain(value: Any, state: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "query_block",
                "nested_loop",
                "table",
                "grouping_operation",
                "ordering_operation",
                "duplicates_removal",
                "union_result",
                "materialized_from_subquery",
                "attached_subqueries",
            }:
                state["operator_types"].add(key)
            if key == "table_name" and isinstance(child, str):
                state["join_order"].append(child)
            elif key == "access_type" and isinstance(child, str):
                state["scan_types"].add(child)
            elif key in {
                "rows_examined_per_scan",
                "rows_produced_per_join",
            } and isinstance(child, (int, float)):
                state["intermediate_rows"].append(float(child))
            elif key == "using_join_buffer" and isinstance(child, str):
                state["join_buffers"].add(child)
            elif "batch" in key.lower() and isinstance(child, (int, float)):
                state["hash_batches"].append(int(child))
            _walk_explain(child, state)
    elif isinstance(value, list):
        for child in value:
            _walk_explain(child, state)


def fingerprint(
    explain_rows: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    counters_before: Mapping[str, int],
    counters_after: Mapping[str, int],
) -> dict[str, Any]:
    query_rows = []
    aggregate_operators: set[str] = set()
    aggregate_scans: set[str] = set()
    aggregate_join_order: list[str] = []
    intermediate_rows: list[float] = []
    hash_batches: list[int] = []
    join_buffers: set[str] = set()
    for row in explain_rows:
        state = {
            "operator_types": set(),
            "scan_types": set(),
            "join_order": [],
            "intermediate_rows": [],
            "hash_batches": [],
            "join_buffers": set(),
        }
        _walk_explain(row["document"], state)
        if not state["operator_types"] or not state["scan_types"]:
            raise R0Error(f"{row['query']} L1 EXPLAIN evidence incomplete")
        aggregate_operators.update(state["operator_types"])
        aggregate_scans.update(state["scan_types"])
        aggregate_join_order.extend(state["join_order"])
        intermediate_rows.extend(state["intermediate_rows"])
        hash_batches.extend(state["hash_batches"])
        join_buffers.update(state["join_buffers"])
        query_rows.append(
            {
                "query": row["query"],
                "sql_sha256": row["sql_sha256"],
                "explain_sha256": row["explain_sha256"],
                "operator_types": sorted(state["operator_types"]),
                "join_order": state["join_order"],
                "scan_types": sorted(state["scan_types"]),
                "estimated_intermediate_rows":
                    state["intermediate_rows"],
                "join_buffers": sorted(state["join_buffers"]),
            }
        )
    memory_values = [
        int(
            sample["required_streams"][
                "memory_current_swap_and_pressure"
            ]["memory_current_bytes"]
        )
        for sample in samples
    ]
    if len(memory_values) != 181:
        raise R0Error("L3 memory sample coverage differs")
    spill_delta = {
        name: int(counters_after[name]) - int(counters_before[name])
        for name in SPILL_COUNTER_NAMES
    }
    if any(value < 0 for value in spill_delta.values()):
        raise R0Error("spill counter reset during fresh R0")
    return {
        "schema_version": "mysql_tpch_r0_l1_l2_l3_fingerprint_v1",
        "query_fingerprints": query_rows,
        "l1": {
            "operator_types": sorted(aggregate_operators),
            "join_order": aggregate_join_order,
            "scan_types": sorted(aggregate_scans),
            "parallel_structure": {
                "mysql_parallel_worker_nodes_observed": 0,
                "source": "mysql_8_explain_json",
            },
        },
        "l2": {
            "workers": {
                "observed": 0,
                "source": "mysql_8_explain_json",
            },
            "hash_batches": {
                "observed": hash_batches,
                "coverage": (
                    "reported"
                    if hash_batches
                    else "not_exposed_by_mysql_8_explain_json"
                ),
            },
            "sort_spill": spill_delta,
            "bitmap_lossy_pages": {
                "applicable": False,
                "engine": "mysql",
            },
            "intermediate_result_scale": {
                "estimated_rows": intermediate_rows,
                "peak_estimated_rows": max(intermediate_rows or [0.0]),
            },
            "join_buffers": sorted(join_buffers),
        },
        "l3": {
            "memory_peak_bytes": max(memory_values),
            "dynamic_execution_memory_bytes":
                max(memory_values) - min(memory_values),
            "spill": spill_delta,
        },
        "missing_level_policy": "independent_candidate",
    }


def build_fresh_r0(
    bundle: Path,
    bootstrap: Mapping[str, Any],
    gate: Mapping[str, Any],
    gate_sha256: str,
    raw: Mapping[str, Any],
    raw_sha256: str,
    counters_before: Mapping[str, int],
    counters_after: Mapping[str, int],
    explain_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    manifest = load_sealed(bundle / "bundle_manifest.json", "bundle manifest")
    identity = bootstrap.get("same_live_identity")
    summary = raw.get("summary") or {}
    resource = summary.get("resource_utilization") or {}
    initial = raw.get("initial_live_readback") or {}
    container = initial.get("container") or {}
    client_transport = initial.get("database_counter_transport") or {}
    samples = raw.get("samples")
    if (
        manifest.get("bundle_id") != bundle.name
        or bootstrap.get("status")
        != "PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY"
        or bootstrap.get("bundle_id") != bundle.name
        or not isinstance(identity, dict)
        or gate.get("schema_version")
        != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or gate.get("status") != "PASS"
        or gate.get("scenario") != "tpch_memory"
        or gate.get("host_id") != "node04"
        or gate.get("attempt_token") != bootstrap.get("attempt_token")
        or gate.get("duration_seconds") != 180
        or gate.get("minimum_samples_per_stream") != 181
        or gate.get("database_counter_missing_samples") != 0
        or gate.get("database_counter_timeout_count") != 0
        or gate.get("all_tolerances_passed") is not True
        or gate.get("serial_fallback_required") is not False
        or raw.get("schema_version")
        != "mysql_algorithm_c_debug_qualification_evidence_v2"
        or raw.get("status")
        != "PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY"
        or raw.get("mode") != "qualification_solo"
        or raw.get("bundle_id") != bundle.name
        or raw.get("scenario") != "tpch_memory"
        or raw.get("hostname") != "node04"
        or raw.get("duration_seconds") != 180
        or raw.get("minimum_samples") != 181
        or not isinstance(samples, list)
        or len(samples) != 181
        or float(summary.get("elapsed_seconds") or 0) < 180.0
        or int(summary.get("sample_count") or 0) != 181
        or container.get("container_id")
        != identity.get("database_container_id")
        or int(container.get("pid") or 0) != identity.get("database_pid")
        or client_transport.get("client_container_id")
        != identity.get("client_container_id")
        or int(client_transport.get("client_pid") or 0)
        != identity.get("client_pid")
        or int(client_transport.get("client_process_start_ticks") or 0)
        != identity.get("client_starttime_ticks")
        or client_transport.get("client_inspect_config_sha256")
        != identity.get("client_inspect_config_sha256")
        or raw.get("accepted_round_authorized") is not False
    ):
        raise R0Error("fresh-R0 source/gate/same-live authority differs")
    for sample in samples:
        counters = (sample.get("required_streams") or {}).get(
            "database_counters"
        )
        if not isinstance(counters, dict) or set(counters) != set(
            DB_COUNTER_NAMES
        ):
            raise R0Error("database counter sample coverage differs")
    utilization = {
        "cpu": float(resource.get("cpu_usage_ratio")),
        "memory": float(resource.get("memory_utilization_ratio")),
        "io": float(resource.get("device_utilization_ratio")),
    }
    if (
        not 0.30 <= utilization["cpu"] <= 0.50
        or not 0.85 <= utilization["memory"] <= 0.95
        or not 0.30 <= utilization["io"] <= 0.50
    ):
        raise R0Error("fresh-R0 resource profile differs")
    fp = fingerprint(
        explain_rows, samples, counters_before, counters_after
    )
    return {
        "schema_version": "mysql_tpch_distributed_v2_fresh_r0_evidence_v1",
        "status": "PASS_FRESH_R0",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": bundle.name,
        "bundle_manifest_sha256": sha256(
            bundle / "bundle_manifest.json"
        ),
        "attempt_token": bootstrap["attempt_token"],
        "same_live_identity": identity,
        "dynamic_gate_sha256": gate_sha256,
        "raw_qualification_sha256": raw_sha256,
        "v40_reused": False,
        "r0_profile_gate_passed": True,
        "database_counter_gate_passed": True,
        "database_counter_missing_samples": 0,
        "database_counter_timeout_count": 0,
        "database_counter_fail_closed_proof": {
            "sample_count": 181,
            "required_counter_names": list(DB_COUNTER_NAMES),
            "pre_post_extended_counter_names":
                list(DB_COUNTER_NAMES + SPILL_COUNTER_NAMES),
            "raw_qualification_completed": True,
        },
        "stable_seconds": float(summary["elapsed_seconds"]),
        "sample_count": 181,
        "utilization": utilization,
        "fingerprint": fp,
        "accepted_rounds_credit": 0,
        "accepted_round_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bootstrap", type=Path, required=True)
    parser.add_argument("--dynamic-gate", type=Path, required=True)
    parser.add_argument("--command-spec", type=Path, required=True)
    parser.add_argument("--main-agent-authority", type=Path, required=True)
    parser.add_argument("--main-agent-token-file", type=Path, required=True)
    parser.add_argument("--database-container", required=True)
    parser.add_argument("--client-container", required=True)
    parser.add_argument("--mysql-defaults-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = args.bundle.resolve(strict=True)
    manifest = load_sealed(bundle / "bundle_manifest.json", "bundle manifest")
    if manifest.get("bundle_id") != bundle.name:
        raise R0Error("bundle identity differs")
    bootstrap = load_sealed(args.bootstrap, "bootstrap")
    gate = load_sealed(args.dynamic_gate, "dynamic gate")
    command = load_sealed(args.command_spec, "command spec")
    load_sealed(args.main_agent_authority, "main-agent authority")
    token = read_token(args.main_agent_token_file)
    if (
        command.get("bundle_id") != bundle.name
        or command.get("mode") != "qualification_solo"
        or command.get("cwd") != "/measurement"
        or command.get("client_container", {}).get("name")
        != args.client_container
    ):
        raise R0Error("fresh-R0 command binding differs")
    raw_path = args.output.with_name(
        args.output.name + ".raw_qualification.json"
    )
    if raw_path.exists() or Path(str(raw_path) + ".sha256").exists():
        raise R0Error("fresh-R0 raw output already exists")
    counters_before = mysql_status(
        args.client_container, args.mysql_defaults_file
    )
    collector = (
        bundle
        / "qualification/mysql_distributed_live_readback_qualification_v1.py"
    )
    _run(
        [
            sys.executable,
            str(collector),
            "--qualification-solo",
            "--bundle",
            str(bundle),
            "--container",
            args.database_container,
            "--mysql-binary",
            "/usr/bin/mysql",
            "--mysql-client-container",
            args.client_container,
            "--mysql-binary-in-client",
            "/usr/bin/mysql",
            "--mysql-defaults-file",
            str(args.mysql_defaults_file),
            "--output",
            str(raw_path),
            "--main-agent-token",
            token,
            "--main-agent-authority",
            str(args.main_agent_authority),
            "--command-spec",
            str(args.command_spec),
        ],
        timeout=600,
    )
    raw = load_sealed(raw_path, "raw fresh-R0 qualification")
    counters_after = mysql_status(
        args.client_container, args.mysql_defaults_file
    )
    explains = explain_documents(
        bundle, args.client_container, args.mysql_defaults_file
    )
    result = build_fresh_r0(
        bundle,
        bootstrap,
        gate,
        sha256(args.dynamic_gate),
        raw,
        sha256(raw_path),
        counters_before,
        counters_after,
        explains,
    )
    atomic_json(args.output, result)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        UnicodeError,
        ValueError,
        subprocess.TimeoutExpired,
        R0Error,
    ) as exc:
        raise SystemExit(f"NOT_READY: {exc}")
