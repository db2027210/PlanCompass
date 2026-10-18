#!/usr/bin/env python3
"""Fail-closed, content-addressed sealer for real TPC-C typed trace registry v2.

This program never starts a workload, DB, container, or network connection.
It accepts only already-produced local evidence and refuses synthetic data,
missing owner-trace evidence, incomplete typed coverage, QPS inputs, or an
inconsistent same-live identity.  Publication is directory-transactional and
idempotent by content address.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
V13_ID = "22bf40890070a2bf147e593d8bd163ea2e16c13123d063583e52ff563461721d"
V13_MANIFEST = "73314232b0344b83828fb6fbba4a4ede30499eb7a4d8b1e573d1b8eb71f2a2a3"
CAPTURE_ID = "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516"
COLLECTOR_ID = "032eb2c9f4ba01a7edf8a438cdadc7fb115243246ba34923dbb18386802099bd"
COLLECTOR_SHA = "f95c21304314474eaca1930fc3fd14931e3034f2c0ecd875813fa53b7d227994"
MANIFEST_SHA = "689d4bc1558d14042c3e43a2267d3cc332d71ba567c635d92180a189c3f90182"
DB_PREFIX = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/"
TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{16,128}$")
HEX = re.compile(r"^[0-9a-f]{64}$")
TX = ["Delivery", "NewOrder", "OrderStatus", "Payment", "StockLevel"]
COLLECTOR_TX = ["NewOrder", "Payment", "OrderStatus", "Delivery", "StockLevel"]


class SealError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path: Path, label: str) -> Path:
    try:
        path = Path(path).resolve(strict=True)
    except OSError as exc:
        raise SealError(label + " is absent") from exc
    if path.is_symlink() or not path.is_file():
        raise SealError(label + " must be a regular non-symlink file")
    return path


def load(path: Path, label: str) -> dict[str, Any]:
    path = regular(path, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SealError(label + " is invalid JSON") from exc
    if not isinstance(value, dict):
        raise SealError(label + " must be a JSON object")
    return value


def verify_sidecar(path: Path, label: str) -> None:
    sidecar = regular(Path(str(path) + ".sha256"), label + " sidecar")
    expect(sidecar.read_text(encoding="ascii") == sha(path) + "  " + path.name + "\n", label + " sidecar differs")


def reject_qps(value: Any, location: str = "evidence") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in {"qps", "throughput", "transactions_per_second"}:
                raise SealError("QPS resource input prohibited: " + location + "." + str(key))
            reject_qps(child, location + "." + str(key))
    elif isinstance(value, list):
        for number, child in enumerate(value):
            reject_qps(child, location + "[%d]" % number)


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SealError(message)


def collector_from(v13: Path):
    source = v13 / "authority" / ("collector_payload_" + COLLECTOR_ID) / "tpcc_typed_collector.py"
    expect(sha(regular(source, "sealed typed collector")) == COLLECTOR_SHA, "sealed typed collector hash differs")
    spec = importlib.util.spec_from_file_location("sealed_tpcc_registry_collector", str(source))
    if spec is None or spec.loader is None:
        raise SealError("sealed typed collector is not importable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return source, module


def verify_v13(v13: Path) -> Path:
    v13 = Path(v13).resolve(strict=True)
    manifest_path = v13 / "bundle_manifest.json"
    expect(v13.name == V13_ID and sha(regular(manifest_path, "v13 manifest")) == V13_MANIFEST, "v13 bundle binding differs")
    verify_sidecar(manifest_path, "v13 manifest")
    manifest = load(manifest_path, "v13 manifest")
    expect(manifest.get("bundle_id") == V13_ID and manifest.get("formal_scope") == "mysql_only" and manifest.get("postgresql_mutation_prohibited") is True, "v13 authority differs")
    collector_from(v13)
    binding = v13 / "predecessor/config/tpcc_offline_binding_manifest.json"
    expect(sha(regular(binding, "v13 binding manifest")) == MANIFEST_SHA, "v13 binding manifest differs")
    return v13


def owner_authority(path: Path, trace: Path, manifest_sha: str, attempt: str) -> dict[str, Any]:
    value = load(path, "OWNER sensitive join authority")
    lock = regular(Path(str(trace) + ".lock"), "OWNER trace lock")
    header = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    expect(isinstance(header, dict), "OWNER trace header differs")
    expected = {
        "schema_version": "mysql_tpcc_sensitive_stock_level_real_join_authority_v1",
        "status": "PASS_REAL_OWNER_TRACE_JOIN",
        "capture_only": True,
        "normalized_transaction_type": "StockLevel",
        "binding_transaction_type": "SensitiveStockLevel",
        "binding_id": "34:SensitiveStockLevel:SensitiveStockLevel/item-in-orderline-w1",
        "sensitive_sql_sha256": "cd8fed7df71c5a681b001083397181804b185816225e68ae8449f8cc384d507e",
        "qps_resource_input_prohibited": True,
        "synthetic_or_fabricated_join_prohibited": True,
        "outer_execution_authorized": False,
        "accepted_round_authorized": False,
    }
    expect(all(value.get(key) == item for key, item in expected.items()), "OWNER sensitive join authority fields differ")
    expect(value.get("trace_path") == str(trace) and value.get("lock_path") == str(lock) and value.get("trace_header") == header, "OWNER trace/lock/header binding differs")
    expect(header.get("capture_token") == attempt and header.get("manifest_sha256") == manifest_sha and header.get("qps_included") is False, "OWNER header token/manifest/QPS differs")
    regular(Path(value.get("consumer_authority_path", "")), "performance-schema consumer authority")
    expect(sha(Path(value["consumer_authority_path"])) == value.get("consumer_authority_sha256"), "consumer authority hash differs")
    reject_qps(value, "owner_authority")
    return value


def evidence(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Path]]:
    v13 = verify_v13(args.v13_bundle)
    supplied_manifest = regular(args.bundle_manifest, "supplied v13 manifest")
    expect(supplied_manifest == (v13 / "bundle_manifest.json").resolve() and sha(supplied_manifest) == V13_MANIFEST, "supplied v13 manifest binding differs")
    paths = {
        "trace.jsonl": regular(args.trace, "trace"),
        "production_command.json": regular(args.production_command, "production command"),
        "dynamic_gate.json": regular(args.dynamic_gate, "dynamic gate"),
        "dynamic_gate_raw_evidence.json": regular(args.dynamic_gate_raw, "dynamic gate raw evidence"),
        "dynamic_gate_A_solo.json": regular(args.dynamic_gate_a_solo, "dynamic gate A_solo source"),
        "dynamic_gate_A_paired.json": regular(args.dynamic_gate_a_paired, "dynamic gate A_paired source"),
        "dynamic_gate_B_solo.json": regular(args.dynamic_gate_b_solo, "dynamic gate B_solo source"),
        "dynamic_gate_B_paired.json": regular(args.dynamic_gate_b_paired, "dynamic gate B_paired source"),
        "db_live_readback.json": regular(args.db_live_readback, "DB live readback"),
        "client_live_readback.json": regular(args.client_live_readback, "client live readback"),
        "production_trace_provenance.json": regular(args.provenance, "production provenance"),
        "qualification_ledger.json": regular(args.qualification_ledger, "qualification ledger"),
        "qualification_command.json": regular(args.qualification_command, "qualification command"),
        "qualification_execution_receipt.json": regular(args.qualification_receipt, "qualification receipt"),
        "typed_capture_command.json": regular(args.typed_command, "typed capture command"),
        "typed_capture_result.json": regular(args.capture_result, "typed capture result"),
        "typed_capture_completion.json": regular(args.capture_completion, "typed completion"),
        "performance_schema_authority.json": regular(args.performance_schema_authority, "performance-schema authority"),
        "sensitive_events.jsonl": regular(args.sensitive_events, "sensitive event stream"),
        "typed_capture_coverage.json": regular(args.coverage, "typed coverage"),
        "resource_readback.json": regular(args.resource_readback, "resource readback"),
        "effective_resource_readback.json": regular(args.effective_resource_readback, "effective resource readback"),
        "runtime_process_gate.json": regular(args.runtime_process_gate, "runtime process gate"),
        "workload_process_evidence.json": regular(args.workload_process_evidence, "workload process evidence"),
    }
    binding = v13 / "predecessor/config/tpcc_offline_binding_manifest.json"
    paths["typed_binding_manifest.json"] = regular(binding, "binding manifest")
    collector_source, collector = collector_from(v13)
    paths["typed_collector.py"] = collector_source
    output = Path(args.collector_output).resolve(strict=True)
    if output.is_symlink() or not output.is_dir():
        raise SealError("collector output must be a regular directory")
    for source, target in (("tpcc_agent_validation.json", "collector_validation.json"), ("tpcc_agent_l2_transactions.json", "l2_transactions.json"), ("tpcc_agent_l3_vectors.json", "l3_vectors.json")):
        paths[target] = regular(output / source, "collector output " + source)
    trace = paths["trace.jsonl"]
    verify_sidecar(trace, "trace")
    expect(Path(args.trace_lock).resolve(strict=True) == Path(str(trace) + ".lock").resolve(strict=True), "trace lock path differs")
    regular(args.trace_lock, "trace lock")
    typed_sidecar = regular(args.typed_command_sidecar, "typed command sidecar")
    expect(typed_sidecar == Path(str(paths["typed_capture_command.json"]) + ".sha256").resolve(strict=True), "typed command sidecar path differs")
    verify_sidecar(paths["typed_capture_command.json"], "typed command")
    rebuilt = collector.collect_and_validate(paths["typed_binding_manifest.json"], trace)
    observed = load(paths["collector_validation.json"], "collector validation")
    l2 = json.loads(paths["l2_transactions.json"].read_text(encoding="utf-8"))
    l3 = json.loads(paths["l3_vectors.json"].read_text(encoding="utf-8"))
    expect(rebuilt == observed and l2 == rebuilt.get("l2_transactions") and l3.get("vectors") == rebuilt.get("l3_vectors"), "collector output is not reproducible")
    expect(rebuilt.get("binding_count") == 34 and rebuilt.get("observed_binding_count") == 34 and rebuilt.get("transaction_types") == COLLECTOR_TX and set(rebuilt.get("l3_vectors") or {}) == set(TX) and rebuilt.get("qps_included") is False, "34/34 typed L2/L3 evidence differs")
    sensitive = "34:SensitiveStockLevel:SensitiveStockLevel/item-in-orderline-w1"
    expect(any(statement.get("binding_id") == sensitive for tx in rebuilt["l2_transactions"] if tx.get("transaction_type") == "StockLevel" for statement in tx.get("statements", [])), "SensitiveStockLevel normalization differs")
    provenance = load(paths["production_trace_provenance.json"], "production provenance")
    attempt = str(provenance.get("attempt_id") or "")
    expect(TOKEN.fullmatch(attempt) is not None, "production attempt token differs")
    owner = owner_authority(args.owner_trace_authority, trace, sha(binding), attempt)
    paths["owner_trace_authority.json"] = regular(args.owner_trace_authority, "OWNER sensitive join authority")
    command, gate, db, client = (load(paths[name], name) for name in ("production_command.json", "dynamic_gate.json", "db_live_readback.json", "client_live_readback.json"))
    raw_gate = load(paths["dynamic_gate_raw_evidence.json"], "dynamic gate raw evidence")
    qualification_ledger = load(paths["qualification_ledger.json"], "qualification ledger")
    qualification_command = load(paths["qualification_command.json"], "qualification command")
    qualification_receipt = load(paths["qualification_execution_receipt.json"], "qualification receipt")
    typed_command = load(paths["typed_capture_command.json"], "typed capture command")
    capture_result = load(paths["typed_capture_result.json"], "typed capture result")
    capture_completion = load(paths["typed_capture_completion.json"], "typed completion")
    performance = load(paths["performance_schema_authority.json"], "performance-schema authority")
    coverage = load(paths["typed_capture_coverage.json"], "typed coverage")
    runtime_gate = load(paths["runtime_process_gate.json"], "runtime process gate")
    workload = load(paths["workload_process_evidence.json"], "workload process evidence")
    for name, value in (("provenance", provenance), ("command", command), ("gate", gate), ("db", db), ("client", client)):
        reject_qps(value, name)
    required_hashes = {"argv_sha256": None, "environment_sha256": None, "command_spec_sha256": sha(paths["production_command.json"]), "dynamic_gate_sha256": sha(paths["dynamic_gate.json"]), "trace_sha256": sha(trace)}
    expect(all(HEX.fullmatch(str(provenance.get(key) or "")) for key in required_hashes), "production provenance hash is absent")
    expect(all(provenance[key] == value for key, value in required_hashes.items() if value is not None), "production provenance content hash differs")
    expect(provenance.get("schema_version") == "mysql_tpcc_production_trace_provenance_v1" and provenance.get("real_data_plane") is True and provenance.get("production_client") is True and provenance.get("synthetic_fixture") is False and provenance.get("actual_main_class") == "com.oltpbenchmark.DBWorkload" and provenance.get("classpath_overlay_first") is True and provenance.get("native_latency_initialized") is True and provenance.get("measurement_started_observed") is True and isinstance(provenance.get("started_epoch_ms"), int) and isinstance(provenance.get("ended_epoch_ms"), int) and provenance["ended_epoch_ms"] > provenance["started_epoch_ms"], "production provenance differs")
    for key in ("db_container_id", "client_container_id"):
        expect(HEX.fullmatch(str(provenance.get(key) or "")) is not None, "production container identity differs")
    expect(isinstance(provenance.get("db_pid"), int) and provenance["db_pid"] > 1 and isinstance(provenance.get("client_pid"), int) and provenance["client_pid"] > 1 and isinstance(provenance.get("db_process_start_ticks"), int) and provenance["db_process_start_ticks"] > 0 and str(provenance.get("db_datadir") or "").startswith(DB_PREFIX) and provenance.get("db_device_major_minor") == "259:2" and provenance.get("db_cpuset_cpus") == "21-24,57-60" and provenance.get("db_cpuset_mems") == "1" and provenance.get("mysql_port") == 65106 and provenance.get("client_cpuset_cpus") == "8-15,44-51" and provenance.get("client_cpuset_mems") == "0", "same-live resource identity differs")
    expect(command.get("actual_main_class") == "com.oltpbenchmark.DBWorkload" and command.get("classpath_overlay_first") is True and command.get("attempt_id") == attempt and command.get("db_container_id") == provenance["db_container_id"] and command.get("client_container_id") == provenance["client_container_id"] and command.get("synthetic_fixture") is False, "production command identity differs")
    expect(qualification_ledger.get("attempt_id") == attempt and qualification_ledger.get("bundle_id") == V13_ID and qualification_command.get("phase") == "qualification" and qualification_receipt.get("status") == "PASS_TERMINAL_GATE" and qualification_receipt.get("attempt_id") == attempt and typed_command.get("phase") == "typed_capture" and typed_command.get("capture_only") is True and typed_command.get("attempt_id") == attempt, "qualification/typed command chain differs")
    environment = typed_command.get("environment") or {}
    expect(environment.get("MYSQL_TPCC_TYPED_TRACE_ROLE") == "OWNER" and environment.get("MYSQL_TPCC_TYPED_CAPTURE_TOKEN") == attempt and environment.get("MYSQL_TPCC_TYPED_MANIFEST_SHA256") == MANIFEST_SHA, "typed OWNER command binding differs")
    expect(capture_result.get("status") == "PASS_CAPTURE_ONLY" and capture_result.get("capture_token") == attempt and capture_result.get("observed_binding_count") == 34 and capture_result.get("accepted_round_authorized") is False and capture_completion.get("capture_token") == attempt, "typed capture completion differs")
    expect(performance.get("status") == "PASS_ENABLED_BEFORE_WORKLOAD_START" and performance.get("qps_resource_input_prohibited") is True, "performance-schema authority differs")
    expect(coverage.get("attempt_id") == attempt and runtime_gate.get("status") == "PASS" and runtime_gate.get("passed") is True and workload.get("attempt_id") == attempt, "runtime coverage/gate/process evidence differs")
    raw_ref = gate.get("raw_artifacts") or {}
    directions = gate.get("directions") or {}
    expect(gate.get("schema_version") == "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1" and gate.get("status") == "PASS" and gate.get("attempt_token") == attempt and gate.get("duration_seconds", 0) >= 180 and gate.get("minimum_samples_per_stream", 0) >= 181 and gate.get("maximum_sample_gap_seconds", 99) <= 1.5 and gate.get("database_counter_missing_samples") == 0 and gate.get("database_counter_timeout_count") == 0 and len(directions) == 2 and all(direction.get("passed") is True and direction.get("foreign_device_io_attribution_bytes") == 0 for direction in directions.values()) and raw_ref.get(paths["dynamic_gate_raw_evidence.json"].name) == sha(paths["dynamic_gate_raw_evidence.json"]), "dynamic gate differs")
    expect(raw_gate.get("schema_version") == "mysql_algorithm_c_dynamic_gate_raw_evidence_closure_v1" and set(raw_gate.get("inputs") or {}) == {"A_solo", "A_paired", "B_solo", "B_paired"}, "dynamic raw closure differs")
    for label, item in raw_gate["inputs"].items():
        expect(isinstance(item, dict) and isinstance(item.get("evidence"), dict), "dynamic raw input differs: " + label)
        source = paths["dynamic_gate_" + label + ".json"]
        expect(HEX.fullmatch(str(item.get("sha256") or "")) is not None and sha(source) == item["sha256"], "dynamic raw source hash differs: " + label)
    expect(db.get("container_id") == provenance["db_container_id"] and db.get("pid") == provenance["db_pid"] and db.get("process_start_ticks") == provenance["db_process_start_ticks"] and db.get("datadir") == provenance["db_datadir"] and db.get("device_major_minor") == "259:2", "DB live readback differs")
    expect(client.get("container_id") == provenance["client_container_id"] and client.get("pid") == provenance["client_pid"] and client.get("cpuset_cpus") == "8-15,44-51" and client.get("cpuset_mems") == "0", "client live readback differs")
    paths["source_v13_bundle_manifest.json"] = supplied_manifest
    paths["source_v13_bundle_manifest.json.sha256"] = regular(Path(str(supplied_manifest) + ".sha256"), "v13 manifest sidecar")
    paths["trace.jsonl.sha256"] = regular(Path(str(trace) + ".sha256"), "trace sidecar")
    paths["trace.jsonl.lock"] = regular(args.trace_lock, "trace lock")
    paths["typed_capture_command.json.sha256"] = typed_sidecar
    return {"attempt": attempt, "provenance": provenance, "owner": owner, "rebuilt": rebuilt, "v13": v13}, paths


def publish(args: argparse.Namespace) -> dict[str, Any]:
    checked, paths = evidence(args)
    payloads = {name: path.read_bytes() for name, path in sorted(paths.items())}
    # The collector emits a wrapper for its own input.  Registry-v2's public
    # l3_vectors.json is deliberately the verified inner vector mapping, while
    # preserving the original wrapper as auditable source material.
    source_l3 = payloads.pop("l3_vectors.json")
    payloads["collector_l3_source.json"] = source_l3
    payloads["l3_vectors.json"] = canonical(checked["rebuilt"]["l3_vectors"])
    files = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
    manifest = {"schema_version": "mysql_tpcc_typed_trace_registry_bundle_v2", "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True, "immutable": True, "synthetic_fixture": False, "real_production_trace": True, "capture_bundle_id": CAPTURE_ID, "source_v13_bundle_id": V13_ID, "source_v13_manifest_sha256": V13_MANIFEST, "collector_payload_id": COLLECTOR_ID, "binding_count": 34, "observed_binding_count": 34, "transaction_types": TX, "sensitive_stock_level_normalized_to": "StockLevel", "setter_type_position_sql_hash_verified": True, "l2_l3_complete": True, "qps_resource_input_prohibited": True, "production_trace_provenance": checked["provenance"], "owner_trace_authority_sha256": sha(paths["owner_trace_authority.json"]), "files": files}
    reject_qps(manifest)
    bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
    manifest["bundle_id"] = bundle_id
    root = Path(args.output_root).resolve(); root.mkdir(parents=True, exist_ok=True)
    target = root / bundle_id
    if target.exists() or target.is_symlink():
        existing = load(target / "bundle_manifest.json", "existing registry manifest")
        if existing != manifest: raise SealError("existing registry content differs")
        return {"status": "PASS_IDEMPOTENT_EXISTING_REAL_TRACE_REGISTRY", "bundle": str(target), "bundle_id": bundle_id, "manifest_sha256": sha(target / "bundle_manifest.json")}
    stage = root / (".stage-" + bundle_id + "-" + secrets.token_hex(8)); stage.mkdir(mode=0o700)
    try:
        for name, data in payloads.items():
            path = stage / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data); os.chmod(path, 0o444)
        raw = canonical(manifest); (stage / "bundle_manifest.json").write_bytes(raw); (stage / "bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  bundle_manifest.json\n", encoding="ascii")
        os.rename(stage, target)
    except BaseException:
        if stage.exists(): shutil.rmtree(stage)
        raise
    return {"status": "PASS_SEALED_REAL_TRACE_REGISTRY_NOT_FORMAL_AUTHORITY", "bundle": str(target), "bundle_id": bundle_id, "manifest_sha256": sha(target / "bundle_manifest.json"), "accepted_round_authorized": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seal", nargs="?")
    for option in ("--v13-bundle", "--bundle-manifest", "--trace", "--trace-lock", "--collector-output", "--production-command", "--dynamic-gate", "--dynamic-gate-raw", "--dynamic-gate-a-solo", "--dynamic-gate-a-paired", "--dynamic-gate-b-solo", "--dynamic-gate-b-paired", "--db-live-readback", "--client-live-readback", "--provenance", "--owner-trace-authority", "--qualification-ledger", "--qualification-command", "--qualification-receipt", "--typed-command", "--typed-command-sidecar", "--capture-result", "--capture-completion", "--performance-schema-authority", "--sensitive-events", "--coverage", "--resource-readback", "--effective-resource-readback", "--runtime-process-gate", "--workload-process-evidence", "--output-root"):
        parser.add_argument(option, type=Path, required=True)
    args = parser.parse_args(); print(json.dumps(publish(args), sort_keys=True)); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except (SealError, OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc); raise SystemExit(2)
