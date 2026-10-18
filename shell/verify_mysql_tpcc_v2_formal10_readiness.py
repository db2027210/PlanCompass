#!/usr/bin/env python3
"""Offline, fail-closed TPC-C v2 formal-ten readiness verifier.

This verifier never starts a process and never contacts a remote host.  It is
deliberately stricter than the qualification successor: a qualification-only
bundle, an unmaterialized command template, or a trace registry without
production provenance is reported as NOT_READY.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


SCRIPT = Path(__file__).resolve()
_EMBEDDED_BUNDLE = (
    SCRIPT.parents[1]
    if SCRIPT.parent.name == "authority"
    and (SCRIPT.parents[1] / "predecessor/bundle_manifest.json").is_file()
    else None
)
ROOT = (
    Path(
        os.environ.get(
            "MYSQL_TPCC_PROJECT_ROOT",
            str(SCRIPT.parents[1]),
        )
    ).resolve()
    if _EMBEDDED_BUNDLE is None
    else SCRIPT.parents[1]
)
TPCC_SUCCESSOR_ROOT = (
    ROOT / "artifacts/mysql_tpcc_typed_distributed_successor_v2"
)
TPCC_CAPTURE_BUNDLE_ID = (
    "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516"
)
TPCC_CAPTURE_BUNDLE = (
    ROOT / "predecessor"
    if _EMBEDDED_BUNDLE is not None
    else ROOT / "artifacts/mysql_tpcc_typed_runtime_v1" / TPCC_CAPTURE_BUNDLE_ID
)
COLLECTOR_PAYLOAD_ID = (
    "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
)
COLLECTOR_PAYLOAD = (
    ROOT / "authority" / f"collector_payload_{COLLECTOR_PAYLOAD_ID}"
    if _EMBEDDED_BUNDLE is not None
    else ROOT / "artifacts/mysql_tpcc_typed_collector" / COLLECTOR_PAYLOAD_ID
)
COLLECTOR_FILES = {
    "README.md":
        "03792e4ff6724d032f07897bd33d9b8f0caccc23197fbe523b462c333963ce5d",
    "test_tpcc_typed_collector.py":
        "6c23587a3f05dc801e6a6dfcb7cc0dd3afadde5fd32f6646b8e64c2d8c285102",
    "tpcc_typed_collector.py":
        "f95c21304314474eaca1930fc3fd14931e3034f2c0ecd875813fa53b7d227994",
}
FOUR_SCENE_CONTRACT_SHA256 = (
    "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
)
EXPECTED_DB_CPUSET = "21-24,57-60"
EXPECTED_CLIENT_CPUSET = "8-15,44-51"
EXPECTED_DB_MEMS = "1"
EXPECTED_CLIENT_MEMS = "0"
EXPECTED_DEVICE = "/dev/nvme3n1"
EXPECTED_MAJOR_MINOR = "259:2"
EXPECTED_PORT = 65106
EXPECTED_DB_DATADIR_PREFIX = (
    "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/"
    "runtime_algorithm_c_successor_20260729/"
)
TRANSACTIONS = ("Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel")
PHASES = (
    "qualification",
    "typed_capture",
    "fresh_r0",
    "debug",
    "formal10_launch",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_RE = SHA256_RE
TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")
PLACEHOLDER_RE = re.compile(r"(?:REPLACE_WITH|OMIT_FOR_|COPY_EXACTLY|\$\{)")


class ReadinessError(RuntimeError):
    """A missing or inconsistent authority item."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReadinessError(f"regular JSON file required: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReadinessError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"JSON object required: {path}")
    return value


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise ReadinessError(message)


def verify_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    expected = f"{sha256(path)}  {path.name}\n"
    _expect(
        sidecar.is_file()
        and not sidecar.is_symlink()
        and sidecar.read_text(encoding="ascii") == expected,
        f"SHA-256 sidecar differs: {path}",
    )


def atomic_text_with_sidecar(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_temporary = sidecar.with_name(
        f".{sidecar.name}.tmp.{os.getpid()}"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(payload.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        digest = sha256(temporary)
        with sidecar_temporary.open("xb") as stream:
            stream.write(f"{digest}  {path.name}\n".encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.replace(sidecar_temporary, sidecar)
    finally:
        for candidate in (temporary, sidecar_temporary):
            if candidate.exists():
                candidate.unlink()


def verify_collector_payload() -> dict[str, Any]:
    _expect(
        COLLECTOR_PAYLOAD.is_dir() and not COLLECTOR_PAYLOAD.is_symlink(),
        "correct three-file collector payload is absent",
    )
    actual = {
        path.name
        for path in COLLECTOR_PAYLOAD.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    _expect(actual == set(COLLECTOR_FILES), "collector payload file set differs")
    for relative, expected in COLLECTOR_FILES.items():
        _expect(
            sha256(COLLECTOR_PAYLOAD / relative) == expected,
            f"collector payload differs: {relative}",
        )
    return {
        "payload_id": COLLECTOR_PAYLOAD_ID,
        "files": dict(COLLECTOR_FILES),
    }


def verify_typed_capture_closure() -> dict[str, Any]:
    manifest_path = TPCC_CAPTURE_BUNDLE / "bundle_manifest.json"
    manifest = load(manifest_path)
    _expect(
        (
            _EMBEDDED_BUNDLE is not None
            or TPCC_CAPTURE_BUNDLE.name == TPCC_CAPTURE_BUNDLE_ID
        )
        and manifest.get("bundle_id") == TPCC_CAPTURE_BUNDLE_ID,
        "typed capture bundle identity differs",
    )
    _expect(
        manifest.get("schema_version") == "mysql_tpcc_typed_runtime_identity_v1"
        and manifest.get("formal_scope") == "mysql_only"
        and manifest.get("cross_engine_comparison_prohibited") is True
        and manifest.get("capture_only") is True,
        "typed capture bundle scope differs",
    )
    files = manifest.get("files")
    _expect(isinstance(files, Mapping) and len(files) >= 50, "typed closure is incomplete")
    for relative, expected in files.items():
        candidate = TPCC_CAPTURE_BUNDLE / str(relative)
        _expect(
            candidate.is_file()
            and not candidate.is_symlink()
            and sha256(candidate) == expected,
            f"typed capture closure differs: {relative}",
        )
    binding = load(
        TPCC_CAPTURE_BUNDLE / "config/tpcc_offline_binding_manifest.json"
    )
    statements = binding.get("statements")
    _expect(
        binding.get("binding_count") == 34
        and isinstance(statements, list)
        and len(statements) == 34,
        "typed JDBC statement inventory is not 34/34",
    )
    binding_ids = [str(row.get("binding_id") or "") for row in statements]
    _expect(
        all(binding_ids) and len(set(binding_ids)) == 34,
        "typed JDBC binding IDs are missing or duplicated",
    )
    normalized_types = {
        "StockLevel"
        if row.get("transaction_type") == "SensitiveStockLevel"
        else row.get("transaction_type")
        for row in statements
    }
    _expect(normalized_types == set(TRANSACTIONS), "five transaction types differ")
    sensitive = [
        row for row in statements
        if row.get("transaction_type") == "SensitiveStockLevel"
    ]
    _expect(len(sensitive) == 1, "SensitiveStockLevel cardinality differs")
    for row in statements:
        count = row.get("parameter_count")
        positions = row.get("parameter_positions")
        _expect(
            isinstance(count, int)
            and count >= 0
            and positions == list(range(1, count + 1)),
            "JDBC parameter positions are not exact and contiguous",
        )
        _expect(
            SHA256_RE.fullmatch(str(row.get("template_sql_sha256") or ""))
            is not None,
            "SQL template hash is absent",
        )
    collector_source = (
        TPCC_CAPTURE_BUNDLE / "collector/tpcc_typed_collector.py"
    ).read_text(encoding="utf-8")
    for semantic in (
        "_validate_typed_bindings",
        "_SETTER_TYPES",
        "typed binding positions are not contiguous",
        "incompatible JDBC setter/type",
        "statement does not match manifest",
        "_reject_qps",
        "SensitiveStockLevel",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_p99_ms",
    ):
        _expect(semantic in collector_source, f"collector semantic absent: {semantic}")
    return {
        "bundle_id": TPCC_CAPTURE_BUNDLE_ID,
        "binding_count": 34,
        "transactions": list(TRANSACTIONS),
        "sensitive_binding_id": sensitive[0]["binding_id"],
        "setter_type_position_sql_hash_verified_by_collector": True,
        "l2_l3_contract_present": True,
        "qps_resource_input_prohibited": True,
    }


def _canonical_successor_unsigned(manifest: Mapping[str, Any]) -> bytes:
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    return (
        json.dumps(
            unsigned,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _canonical_registry_unsigned(manifest: Mapping[str, Any]) -> bytes:
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    return (
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def verify_successor_content(bundle: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = bundle.resolve(strict=True)
    manifest = load(bundle / "bundle_manifest.json")
    _expect(
        manifest.get("bundle_id") == bundle.name
        and hashlib.sha256(_canonical_successor_unsigned(manifest)).hexdigest()
        == bundle.name,
        "distributed successor content identity differs",
    )
    files = manifest.get("files")
    _expect(isinstance(files, Mapping) and files, "successor file closure absent")
    for relative, expected in files.items():
        candidate = bundle / str(relative)
        _expect(
            candidate.is_file()
            and not candidate.is_symlink()
            and sha256(candidate) == expected,
            f"successor closure differs: {relative}",
        )
    runtime = load(bundle / "distributed_runtime_contract.json")
    _expect(
        manifest.get("four_scene_contract_sha256")
        == FOUR_SCENE_CONTRACT_SHA256,
        "v2 four-scene contract differs",
    )
    _expect(
        runtime.get("scenario") == "tpcc_cpu"
        and runtime.get("formal_scope") == "mysql_only"
        and runtime.get("cross_engine_comparison_prohibited") is True,
        "TPC-C successor scope differs",
    )
    return manifest, runtime


def _reject_qps(value: Any, location: str = "authority") -> None:
    forbidden = {"qps", "throughput", "transactions_per_second"}
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in forbidden:
                raise ReadinessError(f"QPS resource input prohibited: {location}.{key}")
            _reject_qps(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_qps(item, f"{location}[{index}]")


def _load_registry_collector(path: Path):
    spec = importlib.util.spec_from_file_location(
        "sealed_tpcc_v2_registry_collector", path
    )
    _expect(
        spec is not None and spec.loader is not None,
        "registry typed collector cannot be loaded",
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def validate_production_registry(registry: Path) -> dict[str, Any]:
    registry = registry.resolve(strict=True)
    manifest_path = registry / "bundle_manifest.json"
    manifest = load(manifest_path)
    _expect(
        manifest.get("bundle_id") == registry.name
        and hashlib.sha256(_canonical_registry_unsigned(manifest)).hexdigest()
        == registry.name,
        "production registry content identity differs",
    )
    _expect(
        manifest.get("schema_version")
        == "mysql_tpcc_typed_trace_registry_bundle_v2",
        "production registry v2 schema required",
    )
    _expect(
        manifest.get("formal_scope") == "mysql_only"
        and manifest.get("cross_engine_comparison_prohibited") is True
        and manifest.get("immutable") is True
        and manifest.get("synthetic_fixture") is False
        and manifest.get("real_production_trace") is True,
        "registry does not prove a real production trace",
    )
    _expect(
        manifest.get("capture_bundle_id") == TPCC_CAPTURE_BUNDLE_ID
        and manifest.get("binding_count") == 34
        and manifest.get("observed_binding_count") == 34
        and manifest.get("transaction_types") == list(TRANSACTIONS)
        and manifest.get("sensitive_stock_level_normalized_to") == "StockLevel"
        and manifest.get("setter_type_position_sql_hash_verified") is True
        and manifest.get("l2_l3_complete") is True
        and manifest.get("qps_resource_input_prohibited") is True,
        "registry typed/L2/L3 authority differs",
    )
    provenance = manifest.get("production_trace_provenance")
    _expect(isinstance(provenance, Mapping), "production trace provenance absent")
    attempt = str(provenance.get("attempt_id") or "")
    _expect(
        provenance.get("schema_version")
        == "mysql_tpcc_production_trace_provenance_v1"
        and provenance.get("real_data_plane") is True
        and provenance.get("production_client") is True
        and provenance.get("synthetic_fixture") is False
        and TOKEN_RE.fullmatch(attempt) is not None,
        "production trace provenance flags/token differ",
    )
    _expect(
        provenance.get("actual_main_class") == "com.oltpbenchmark.DBWorkload"
        and provenance.get("classpath_overlay_first") is True
        and provenance.get("native_latency_initialized") is True
        and provenance.get("measurement_started_observed") is True,
        "actual production main/native latency provenance differs",
    )
    _expect(
        isinstance(provenance.get("started_epoch_ms"), int)
        and isinstance(provenance.get("ended_epoch_ms"), int)
        and provenance["ended_epoch_ms"] > provenance["started_epoch_ms"],
        "production trace time boundary differs",
    )
    for field in (
        "argv_sha256",
        "environment_sha256",
        "command_spec_sha256",
        "dynamic_gate_sha256",
        "trace_sha256",
    ):
        _expect(
            SHA256_RE.fullmatch(str(provenance.get(field) or "")) is not None,
            f"production provenance hash absent: {field}",
        )
    db_container = str(provenance.get("db_container_id") or "")
    client_container = str(provenance.get("client_container_id") or "")
    _expect(
        CONTAINER_RE.fullmatch(db_container) is not None
        and isinstance(provenance.get("db_pid"), int)
        and provenance["db_pid"] > 1
        and isinstance(provenance.get("db_process_start_ticks"), int)
        and provenance["db_process_start_ticks"] > 0
        and str(provenance.get("db_datadir") or "").startswith(
            EXPECTED_DB_DATADIR_PREFIX
        )
        and provenance.get("db_device_major_minor") == EXPECTED_MAJOR_MINOR
        and provenance.get("db_cpuset_cpus") == EXPECTED_DB_CPUSET
        and provenance.get("db_cpuset_mems") == EXPECTED_DB_MEMS
        and provenance.get("mysql_port") == EXPECTED_PORT,
        "production trace database identity/envelope differs",
    )
    _expect(
        CONTAINER_RE.fullmatch(client_container) is not None
        and isinstance(provenance.get("client_pid"), int)
        and provenance["client_pid"] > 1
        and provenance.get("client_cpuset_cpus") == EXPECTED_CLIENT_CPUSET
        and provenance.get("client_cpuset_mems") == EXPECTED_CLIENT_MEMS,
        "production trace client identity/envelope differs",
    )
    _reject_qps(manifest)
    files = manifest.get("files")
    _expect(isinstance(files, Mapping) and files, "registry closure absent")
    required_files = {
        "trace.jsonl",
        "typed_binding_manifest.json",
        "typed_collector.py",
        "collector_validation.json",
        "l2_transactions.json",
        "l3_vectors.json",
        "production_command.json",
        "dynamic_gate.json",
        "db_live_readback.json",
        "client_live_readback.json",
    }
    _expect(required_files.issubset(files), "registry executable/evidence closure incomplete")
    for relative, expected in files.items():
        candidate = registry / str(relative)
        _expect(
            candidate.is_file()
            and not candidate.is_symlink()
            and sha256(candidate) == expected,
            f"registry closure differs: {relative}",
        )
    _expect(
        sha256(registry / "typed_binding_manifest.json")
        == sha256(
            TPCC_CAPTURE_BUNDLE / "config/tpcc_offline_binding_manifest.json"
        )
        and sha256(registry / "typed_collector.py")
        == COLLECTOR_FILES["tpcc_typed_collector.py"],
        "registry typed manifest/collector authority differs",
    )
    collector = _load_registry_collector(registry / "typed_collector.py")
    rebuilt = collector.collect_and_validate(
        registry / "typed_binding_manifest.json",
        registry / "trace.jsonl",
    )
    observed = load(registry / "collector_validation.json")
    observed_l2 = json.loads(
        (registry / "l2_transactions.json").read_text(encoding="utf-8")
    )
    observed_l3 = json.loads(
        (registry / "l3_vectors.json").read_text(encoding="utf-8")
    )
    _expect(rebuilt == observed, "registry collector validation does not reproduce")
    _expect(
        rebuilt.get("binding_count") == 34
        and rebuilt.get("observed_binding_count") == 34
        and rebuilt.get("transaction_types") == [
            "NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"
        ]
        and set((rebuilt.get("l3_vectors") or {})) == set(TRANSACTIONS)
        and rebuilt.get("qps_included") is False,
        "registry 34 bindings/five transactions/L3/QPS evidence differs",
    )
    _expect(
        observed_l2 == rebuilt["l2_transactions"]
        and observed_l3 == rebuilt["l3_vectors"],
        "registry L2/L3 materialization differs",
    )
    binding = load(registry / "typed_binding_manifest.json")
    sensitive_id = next(
        row["binding_id"]
        for row in binding["statements"]
        if row["transaction_type"] == "SensitiveStockLevel"
    )
    stock_statements = [
        statement
        for transaction in rebuilt["l2_transactions"]
        if transaction["transaction_type"] == "StockLevel"
        for statement in transaction["statements"]
    ]
    _expect(
        any(row["binding_id"] == sensitive_id for row in stock_statements),
        "SensitiveStockLevel was not normalized into StockLevel",
    )
    _reject_qps(rebuilt)
    command = load(registry / "production_command.json")
    _expect(
        command.get("actual_main_class") == "com.oltpbenchmark.DBWorkload"
        and command.get("classpath_overlay_first") is True
        and command.get("attempt_id") == attempt
        and command.get("db_container_id") == db_container
        and command.get("client_container_id") == client_container
        and command.get("synthetic_fixture") is False,
        "registry production command binding differs",
    )
    _reject_qps(command)
    gate = load(registry / "dynamic_gate.json")
    _expect(
        gate.get("schema_version")
        == "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        and gate.get("status") == "PASS"
        and gate.get("passed") is True
        and gate.get("attempt_id") == attempt
        and gate.get("duration_seconds", 0) >= 180
        and gate.get("sample_count", 0) >= 181
        and gate.get("maximum_sample_gap_seconds", 99) <= 1.5
        and gate.get("foreign_device_io_attribution_bytes") == 0,
        "registry 180-second dynamic gate differs",
    )
    db_readback = load(registry / "db_live_readback.json")
    client_readback = load(registry / "client_live_readback.json")
    _expect(
        db_readback.get("container_id") == db_container
        and db_readback.get("pid") == provenance["db_pid"]
        and db_readback.get("process_start_ticks")
        == provenance["db_process_start_ticks"]
        and db_readback.get("datadir") == provenance["db_datadir"]
        and db_readback.get("device_major_minor") == EXPECTED_MAJOR_MINOR,
        "registry database live readback differs",
    )
    _expect(
        client_readback.get("container_id") == client_container
        and client_readback.get("pid") == provenance["client_pid"]
        and client_readback.get("cpuset_cpus") == EXPECTED_CLIENT_CPUSET
        and client_readback.get("cpuset_mems") == EXPECTED_CLIENT_MEMS,
        "registry client live readback differs",
    )
    return manifest


def _validate_phase_identity(
    phase: Mapping[str, Any],
    *,
    expected_attempt: str,
    expected_bundle: str,
    expected_container: str,
    expected_pid: int,
    expected_start_ticks: int,
    expected_datadir: str,
) -> None:
    _expect(phase.get("status") == "PASS", "handoff phase did not pass")
    _expect(
        phase.get("attempt_id") == expected_attempt
        and phase.get("bundle_id") == expected_bundle,
        "handoff attempt/bundle identity changed",
    )
    _expect(
        phase.get("db_container_id") == expected_container
        and phase.get("db_pid") == expected_pid
        and phase.get("db_process_start_ticks") == expected_start_ticks
        and phase.get("db_datadir") == expected_datadir,
        "handoff live database identity changed",
    )
    _expect(
        phase.get("db_device_major_minor") == EXPECTED_MAJOR_MINOR
        and phase.get("db_cpuset_cpus") == EXPECTED_DB_CPUSET
        and phase.get("db_cpuset_mems") == EXPECTED_DB_MEMS
        and phase.get("mysql_port") == EXPECTED_PORT,
        "handoff database envelope changed",
    )
    _expect(
        CONTAINER_RE.fullmatch(str(phase.get("client_container_id") or ""))
        is not None
        and isinstance(phase.get("client_pid"), int)
        and phase["client_pid"] > 1
        and phase.get("client_cpuset_cpus") == EXPECTED_CLIENT_CPUSET
        and phase.get("client_cpuset_mems") == EXPECTED_CLIENT_MEMS,
        "handoff client envelope differs",
    )
    _expect(
        SHA256_RE.fullmatch(str(phase.get("live_readback_sha256") or ""))
        is not None,
        "handoff live readback hash absent",
    )
    _expect(
        phase.get("client_target_device_io_zero_verified") is True
        and phase.get("client_target_device_io_delta")
        == {
            "rbytes": 0, "wbytes": 0, "rios": 0,
            "wios": 0, "dbytes": 0, "dios": 0,
        },
        "handoff client target-device real-window IO is not zero",
    )


def validate_same_live_container_handoff(
    value: Mapping[str, Any],
    *,
    bundle_id: str,
    registry_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    status = value.get("status")
    _expect(
        value.get("schema_version")
        == "mysql_tpcc_same_live_container_formal10_handoff_v1"
        and status in {"PRELAUNCH_READY", "PASS"}
        and value.get("formal_scope") == "mysql_only"
        and value.get("cross_engine_comparison_prohibited") is True,
        "same-live-container handoff authority differs",
    )
    _expect(
        value.get("container_recreated") is False
        and value.get("datadir_replaced") is False
        and value.get("database_restarted") is False,
        "same-live-container continuity was broken",
    )
    attempt = str(value.get("attempt_id") or "")
    provenance = registry_manifest["production_trace_provenance"]
    _expect(
        TOKEN_RE.fullmatch(attempt) is not None
        and attempt == provenance.get("attempt_id")
        and value.get("bundle_id") == bundle_id,
        "handoff attempt/registry/bundle binding differs",
    )
    container_id = str(value.get("db_container_id") or "")
    pid = value.get("db_pid")
    start_ticks = value.get("db_process_start_ticks")
    datadir = str(value.get("db_datadir") or "")
    _expect(
        CONTAINER_RE.fullmatch(container_id) is not None
        and isinstance(pid, int)
        and pid > 1
        and isinstance(start_ticks, int)
        and start_ticks > 0
        and datadir.startswith(EXPECTED_DB_DATADIR_PREFIX)
        and ".." not in Path(datadir).parts,
        "handoff root live database identity differs",
    )
    _expect(
        container_id == provenance.get("db_container_id")
        and pid == provenance.get("db_pid")
        and start_ticks == provenance.get("db_process_start_ticks")
        and datadir == provenance.get("db_datadir"),
        "handoff does not adopt the production-trace database",
    )
    phases = value.get("phases")
    expected_phases = PHASES if status == "PASS" else PHASES[:-1]
    _expect(
        isinstance(phases, Mapping) and list(phases) == list(expected_phases),
        "handoff phase set/order differs",
    )
    epochs: list[int] = []
    for name in expected_phases:
        phase = phases[name]
        _expect(isinstance(phase, Mapping), f"handoff phase absent: {name}")
        _validate_phase_identity(
            phase,
            expected_attempt=attempt,
            expected_bundle=bundle_id,
            expected_container=container_id,
            expected_pid=pid,
            expected_start_ticks=start_ticks,
            expected_datadir=datadir,
        )
        epoch = phase.get("observed_epoch_ms")
        _expect(isinstance(epoch, int), "handoff phase timestamp absent")
        epochs.append(epoch)
    _expect(
        epochs == sorted(epochs) and len(set(epochs)) == len(epochs),
        "handoff phases are not strictly time ordered",
    )
    return dict(value)


def validate_materialized_command(
    command: Mapping[str, Any], runtime: Mapping[str, Any]
) -> dict[str, Any]:
    serialized = json.dumps(command, sort_keys=True)
    _expect(PLACEHOLDER_RE.search(serialized) is None, "command is still a template")
    _expect(
        command.get("schema_version")
        == "mysql_tpcc_formal10_materialized_command_v1"
        and command.get("status") == "SEALED_FORMAL10_COMMAND"
        and command.get("formal_authorized") is True
        and command.get("accepted_round_authorized") is False,
        "formal10 command authorization flags differ",
    )
    recipe = command.get("native_java_launch_recipe")
    _expect(isinstance(recipe, Mapping), "native Java launch recipe absent")
    argv = command.get("argv")
    java_argv = command.get("actual_java_argv", argv)
    _expect(
        isinstance(argv, list)
        and isinstance(java_argv, list)
        and "-cp" in java_argv
        and "com.oltpbenchmark.DBWorkload" in java_argv
        and "-jar" not in java_argv,
        "actual BenchBase main/classpath launch differs",
    )
    _expect(
        recipe.get("main_class") == "com.oltpbenchmark.DBWorkload"
        and recipe.get("classpath_first_entry")
        == "qualification/native_latency_producer/classes"
        and recipe.get("java_jar_mode_prohibited") is True,
        "native latency overlay/main binding differs",
    )
    _expect(
        command.get("manifest_bound_java_binary")
        == "/opt/mysql-tpcc-jdk/bin/java"
        and command.get("manifest_bound_python_binary")
        == "/opt/mysql-tpcc-python/bin/python3.11"
        and command.get("jdk_tree_content_sha256")
        == "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
        and command.get("python_tree_content_sha256")
        == "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
        and SHA256_RE.fullmatch(
            str(command.get("prefault_authority_sha256") or "")
        ) is not None
        and command.get("target_device_real_window_io_required_zero") is True,
        "manifest-bound runtimes or client zero-IO gate differs",
    )
    environment = command.get("environment")
    _expect(
        isinstance(environment, Mapping)
        and environment.get("BENCHBASE_FORMAL_WARMUP_SECONDS") == "60"
        and environment.get("BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS")
        == "600"
        and str(environment.get("BENCHBASE_FORMAL_STOP_FILE") or "").startswith(
            "/measurement/"
        ),
        "native formal timing environment differs",
    )
    client = command.get("client_container")
    expected = runtime["client_resource_envelope"]
    _expect(
        isinstance(client, Mapping)
        and CONTAINER_RE.fullmatch(str(client.get("container_id") or ""))
        is not None
        and client.get("cpuset_cpus") == EXPECTED_CLIENT_CPUSET
        and client.get("cpuset_mems") == EXPECTED_CLIENT_MEMS
        and client.get("cpu_quota_cores") == expected["cpu_quota_cores"]
        and client.get("memory_bytes") == expected["memory_bytes"]
        and client.get("memory_swap_bytes") == expected["memory_swap_bytes"]
        and client.get("pids_max") == expected["pids_max"]
        and client.get("root_filesystem_read_only") is True
        and client.get("network_mode") == "host"
        and client.get("target_mysql_device_write_prohibited") is True,
        "materialized client envelope differs",
    )
    _expect(
        re.fullmatch(
            r"sha256:[0-9a-f]{64}", str(client.get("image_id") or "")
        ) is not None,
        "materialized client image ID is not content-addressed",
    )
    _reject_qps(command)
    return dict(command)


def readiness_blockers(
    bundle: Path,
    *,
    registry: Path | None = None,
    handoff: Path | None = None,
    command: Path | None = None,
) -> list[str]:
    blockers: list[str] = []
    try:
        verify_collector_payload()
        verify_typed_capture_closure()
        manifest, runtime = verify_successor_content(bundle)
    except (ReadinessError, OSError, ValueError) as exc:
        return [str(exc)]

    capabilities = runtime.get("runtime_capabilities") or {}
    if manifest.get("formal_launch_authorized") is not True:
        blockers.append("successor manifest does not authorize formal launch")
    if not all(
        capabilities.get(name) is True
        for name in ("qualification", "typed_capture_after_gate", "fresh_r0", "debug", "formal_outer")
    ):
        blockers.append("successor runtime capabilities are qualification/capture-only")
    storage = runtime.get("storage") or {}
    envelope = runtime.get("resource_envelope") or {}
    client = runtime.get("client_resource_envelope") or {}
    if (
        storage.get("device") != EXPECTED_DEVICE
        or storage.get("expected_live_major_minor") != EXPECTED_MAJOR_MINOR
        or envelope.get("cpuset_cpus") != EXPECTED_DB_CPUSET
        or envelope.get("cpuset_mems") != EXPECTED_DB_MEMS
        or envelope.get("mysql_port") != EXPECTED_PORT
        or client.get("cpuset_cpus") != EXPECTED_CLIENT_CPUSET
        or client.get("cpuset_mems") != EXPECTED_CLIENT_MEMS
    ):
        blockers.append("v2 database/client/storage envelope differs")

    if registry is None:
        blockers.append("real production typed trace registry v2 is absent")
        registry_manifest = None
    else:
        try:
            registry_manifest = validate_production_registry(registry)
        except (ReadinessError, OSError, ValueError) as exc:
            blockers.append(str(exc))
            registry_manifest = None

    if command is None:
        blockers.append("sealed materialized formal10 client command is absent")
    else:
        try:
            validate_materialized_command(load(command), runtime)
        except (ReadinessError, OSError, ValueError) as exc:
            blockers.append(str(exc))

    if handoff is None:
        blockers.append("same-live-container formal10 handoff is absent")
    elif registry_manifest is None:
        blockers.append("handoff cannot be checked without a valid production registry")
    else:
        try:
            validate_same_live_container_handoff(
                load(handoff),
                bundle_id=bundle.name,
                registry_manifest=registry_manifest,
            )
        except (ReadinessError, OSError, ValueError) as exc:
            blockers.append(str(exc))
    return blockers


def latest_successor() -> Path:
    candidates = [
        path
        for path in TPCC_SUCCESSOR_ROOT.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and re.fullmatch(r"[0-9a-f]{64}", path.name)
    ]
    _expect(bool(candidates), "no TPC-C distributed successor exists")
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--command", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    bundle = args.bundle or latest_successor()
    blockers = readiness_blockers(
        bundle,
        registry=args.registry,
        handoff=args.handoff,
        command=args.command,
    )
    result = {
        "schema_version": "mysql_tpcc_v2_formal10_readiness_audit_v1",
        "status": "READY" if not blockers else "NOT_READY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle.name,
        "collector_payload_id": COLLECTOR_PAYLOAD_ID,
        "capture_bundle_id": TPCC_CAPTURE_BUNDLE_ID,
        "blockers": blockers,
    }
    payload = (
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )
    if args.output:
        atomic_text_with_sidecar(args.output, payload)
    print(payload, end="")
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
