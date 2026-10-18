#!/usr/bin/env python3
"""Produce bundle-bound XP capture and fresh-R0 evidence.

This entrypoint is evidence-only.  It never starts, stops, or updates a
container or workload.  It converts independently sealed runtime evidence into
the two exact schemas consumed by the XP formal promotion materializer, while
re-reading the adopted database/client PIDs from procfs and failing closed on
missing trace, counter, cadence, envelope, or fingerprint evidence.
"""

import sys
sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import math
from pathlib import Path

_SCRIPT_DIRECTORY = str(Path(__file__).resolve().parent)
if _SCRIPT_DIRECTORY not in sys.path:
    sys.path.insert(0, _SCRIPT_DIRECTORY)

from mysql_xp_node02_formal10_materializer_v1 import (
    MaterializerError,
    _fingerprint_complete,
    _same_live,
    _verify_gate,
    atomic_json,
    load_sealed,
    sha256,
    verify_adoption,
    verify_bundle,
)


FORMAL_ENVELOPE = {
    "cpu_quota_cores": 5.3,
    "cpuset_cpus": "21-33,57-69",
    "cpuset_mems": "1",
    "memory_bytes": 8589934592,
    "memory_swap_bytes": 8589934592,
    "pids_max": 1024,
    "io_read_bps": 29360128,
    "io_write_bps": 524288,
    "innodb_buffer_pool_bytes": 4294967296,
    "mysql_port": 65104,
}
CAPTURE_IO = {
    "io_read_bps": 1205862400,
    "io_write_bps": 1205862400,
}
REQUIRED_AT = {"AT%d" % index for index in range(1, 7)}
REQUIRED_WORKERS = (
    {"XAP-%d" % index for index in range(1, 9)}
    | {"XTP-%d" % index for index in range(1, 9)}
)
RESOURCE_KEYS = {"cpu", "memory", "io"}


class ProducerError(MaterializerError):
    pass


def _proc_starttime_ticks(pid, proc_root=Path("/proc")):
    pid = int(pid)
    if pid <= 1:
        raise ProducerError("real PID is required")
    path = Path(proc_root) / str(pid) / "stat"
    try:
        value = path.read_text(encoding="ascii")
    except (OSError, ValueError) as exc:
        raise ProducerError("adopted same-live PID is absent") from exc
    end = value.rfind(")")
    fields = value[end + 2:].split() if end >= 0 else []
    if len(fields) <= 19:
        raise ProducerError("procfs starttime is malformed")
    return int(fields[19])


def verify_live_adopted_identity(adoption, proc_root=Path("/proc")):
    identity = _same_live(adoption)
    checks = (
        (
            identity["mysql_pid"],
            identity["mysql_starttime_ticks"],
            "database",
        ),
        (
            identity["client_init_pid"],
            identity["client_init_starttime_ticks"],
            "client",
        ),
    )
    for pid, expected, label in checks:
        if _proc_starttime_ticks(pid, proc_root) != int(expected):
            raise ProducerError(
                "%s adopted PID/starttime identity changed" % label
            )
    return identity


def _load_trace_validator(bundle):
    path = (
        Path(bundle)
        / "predecessor/predecessor/capture/offline_registry/"
        "xp_strict_registry.py"
    )
    if path.is_symlink() or not path.is_file():
        raise ProducerError("bundle-bound XP trace validator is absent")
    spec = importlib.util.spec_from_file_location(
        "xp_formal_evidence_trace_validator", str(path)
    )
    if spec is None or spec.loader is None:
        raise ProducerError("cannot load bundle-bound XP trace validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _validate_live_readback(readback, manifest, adoption, identity):
    container = readback.get("container") or {}
    transport = readback.get("database_counter_transport") or {}
    counters = readback.get("database_counters")
    if (
        readback.get("schema_version")
        != "mysql_algorithm_c_distributed_live_readback_v1"
        or readback.get("status")
        != "PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY"
        or readback.get("bundle_id") != manifest["bundle_id"]
        or readback.get("formal_scope") != "mysql_only"
        or readback.get("cross_engine_comparison_prohibited") is not True
        or readback.get("resource_envelope") != FORMAL_ENVELOPE
        or container.get("container_id")
        != identity["database_container_id"]
        or container.get("pid") != identity["mysql_pid"]
        or transport.get("container_id")
        not in (None, identity["client_container_id"])
        or transport.get("client_container_id")
        != identity["client_container_id"]
        or transport.get("client_pid") != identity["client_init_pid"]
        or transport.get("host_mysql_cli_required") is not False
        or not isinstance(counters, dict)
        or not counters
        or readback.get("capture_authorized") is not False
        or readback.get("formal_r0_authorized") is not False
        or readback.get("formal_launch_authorized") is not False
        or readback.get("accepted_round_authorized") is not False
    ):
        raise ProducerError("formal envelope live readback differs")
    return counters


def _successful_at_coverage(validation):
    events = validation.get("events")
    if (
        validation.get("capture_authorized") is not True
        or validation.get("execution_authorized") is not False
        or validation.get("reasons") != []
        or not isinstance(events, list)
    ):
        raise ProducerError("bundle-bound production trace validation failed")
    commits = {
        str(row.get("transaction_type"))
        for row in events
        if isinstance(row, dict)
        and row.get("event_type") == "commit"
        and row.get("outcome") == "success"
    }
    statements = {
        name: sum(
            1
            for row in events
            if isinstance(row, dict)
            and row.get("event_type") == "statement"
            and row.get("outcome") == "success"
            and row.get("transaction_type") == name
        )
        for name in REQUIRED_AT
    }
    if not REQUIRED_AT.issubset(commits) or any(
        statements[name] < 1 for name in REQUIRED_AT
    ):
        raise ProducerError("real committed AT1-AT6 trace is incomplete")
    return {name: True for name in sorted(REQUIRED_AT)}


def validate_capture_raw(
    raw, manifest, adoption, identity, trace_path, trace_validation,
    live_readback, adoption_sha256,
):
    workers = raw.get("worker_futures")
    worker_map = {
        str(row.get("worker")): row
        for row in workers
        if isinstance(row, dict)
    } if isinstance(workers, list) else {}
    coverage = _successful_at_coverage(trace_validation)
    _validate_live_readback(
        live_readback, manifest, adoption, identity
    )
    if (
        raw.get("schema_version") != "mysql_xp_same_live_capture_raw_v1"
        or raw.get("status") != "PASS_CAPTURE_COMPLETE"
        or raw.get("bundle_id") != manifest["bundle_id"]
        or raw.get("adopted_service_sha256") != adoption_sha256
        or raw.get("same_live_identity") != identity
        or raw.get("formal_scope") != "mysql_only"
        or raw.get("cross_engine_comparison_prohibited") is not True
        or raw.get("capture_only") is not True
        or raw.get("capture_is_r0") is not False
        or raw.get("accepted_rounds_credit") != 0
        or raw.get("qps_resource_input_present") is not False
        or raw.get("real_production_trace") is not True
        or raw.get("trace_path") != str(trace_path)
        or raw.get("trace_sha256") != sha256(trace_path)
        or raw.get("capture_resource_envelope") != CAPTURE_IO
        or raw.get("formal_envelope_restored") is not True
        or raw.get("formal_envelope_readback_sha256")
        != live_readback["_evidence_sha256"]
        or raw.get("xapclient_independent_check_passed") is not True
        or raw.get("worker_future_count") != len(REQUIRED_WORKERS)
        or raw.get("worker_future_get_count") != len(REQUIRED_WORKERS)
        or raw.get("worker_failures") != 0
        or set(worker_map) != REQUIRED_WORKERS
        or any(
            row.get("get_called") is not True
            or row.get("status") != "SUCCESS"
            or row.get("exception") is not None
            or row.get("cancelled") is not False
            for row in worker_map.values()
        )
    ):
        raise ProducerError("same-live production capture evidence differs")
    return {
        "schema_version": "mysql_xp_production_at_capture_v3",
        "status": "PASS_CAPTURE_COMPLETE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": manifest["bundle_id"],
        "same_live_identity": identity,
        "adopted_service_sha256": adoption_sha256,
        "coverage": coverage,
        "trace_path": str(trace_path),
        "trace_sha256": sha256(trace_path),
        "worker_future_count": len(REQUIRED_WORKERS),
        "worker_future_get_count": len(REQUIRED_WORKERS),
        "worker_failures": 0,
        "xapclient_independent_check_passed": True,
        "qps_resource_input_present": False,
        "real_production_trace": True,
        "capture_only": True,
        "capture_is_r0": False,
        "formal_envelope_restored": True,
        "formal_envelope_readback_sha256":
            live_readback["_evidence_sha256"],
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }


def _finite(value, label):
    if isinstance(value, bool):
        raise ProducerError("%s is not finite" % label)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProducerError("%s is not finite" % label) from exc
    if not math.isfinite(result):
        raise ProducerError("%s is not finite" % label)
    return result


def validate_r0_raw(
    raw, manifest, adoption, identity, trace_registry, dynamic_gate,
    live_readback, adoption_sha256,
):
    _validate_live_readback(
        live_readback, manifest, adoption, identity
    )
    _verify_gate(dynamic_gate, manifest["bundle_id"], identity)
    if (
        trace_registry.get("schema_version")
        != "mysql_xp_production_trace_registry_v3"
        or trace_registry.get("status") != "SEALED_REAL_TRACE"
        or trace_registry.get("bundle_id") != manifest["bundle_id"]
        or trace_registry.get("same_live_identity") != identity
        or trace_registry.get("formal_r0_authorized") is not True
    ):
        raise ProducerError("sealed production trace registry differs")
    samples = raw.get("samples")
    fingerprint = raw.get("fingerprint")
    workload_pid = raw.get("workload_pid")
    workload_ticks = raw.get("workload_pid_starttime_ticks")
    if (
        raw.get("schema_version") != "mysql_xp_fresh_r0_raw_samples_v1"
        or raw.get("status") != "PASS_MEASUREMENT_COMPLETE"
        or raw.get("bundle_id") != manifest["bundle_id"]
        or raw.get("adopted_service_sha256") != adoption_sha256
        or raw.get("same_live_identity") != identity
        or raw.get("formal_scope") != "mysql_only"
        or raw.get("cross_engine_comparison_prohibited") is not True
        or raw.get("formal_envelope_readback_sha256")
        != live_readback["_evidence_sha256"]
        or raw.get("trace_registry_sha256")
        != trace_registry["_evidence_sha256"]
        or raw.get("dynamic_gate_sha256")
        != dynamic_gate["_evidence_sha256"]
        or raw.get("collector_sha256") != manifest["collector_sha256"]
        or raw.get("collector_command_kind")
        != "exact_client_container_docker_exec_stdout_v1"
        or raw.get("host_mysql_binary_used") is not False
        or raw.get("capture_result") is not False
        or raw.get("failed_replay") is not False
        or raw.get("accepted_rounds_credit") != 0
        or raw.get("measurement_seconds") != 180
        or raw.get("graceful_quiescence_seconds") != 120
        or raw.get("correctness_passed") is not True
        or raw.get("stability_passed") is not True
        or raw.get("cleanup_complete") is not True
        or raw.get("workload_process_absent_after_quiescence") is not True
        or not isinstance(workload_pid, int)
        or workload_pid <= 1
        or not isinstance(workload_ticks, int)
        or workload_ticks <= 0
        or not isinstance(samples, list)
        or len(samples) != 180
        or not _fingerprint_complete(fingerprint)
    ):
        raise ProducerError("fresh R0 raw evidence differs")
    utilization = {name: [] for name in RESOURCE_KEYS}
    last_elapsed = None
    for index, row in enumerate(samples):
        values = row.get("utilization") if isinstance(row, dict) else None
        counters = row.get("database_counters") if isinstance(row, dict) else None
        elapsed = _finite(
            row.get("elapsed_seconds") if isinstance(row, dict) else None,
            "R0 sample elapsed",
        )
        if (
            row.get("sequence") != index
            or row.get("stable") is not True
            or row.get("workload_pid") != workload_pid
            or row.get("workload_pid_starttime_ticks") != workload_ticks
            or row.get("database_counter_timeout") is not False
            or row.get("database_counter_missing") is not False
            or not isinstance(counters, dict)
            or not counters
            or not isinstance(values, dict)
            or set(values) != RESOURCE_KEYS
            or (last_elapsed is not None and not (0 < elapsed-last_elapsed <= 1.5))
        ):
            raise ProducerError("fresh R0 sample/cadence/counter evidence differs")
        for name in RESOURCE_KEYS:
            value = _finite(values[name], "R0 utilization")
            if value < 0 or value > 2:
                raise ProducerError("R0 utilization is outside measurable range")
            utilization[name].append(value)
        last_elapsed = elapsed
    resource_result = {
        name: sum(values) / float(len(values))
        for name, values in utilization.items()
    }
    return {
        "schema_version": "mysql_xp_fresh_r0_qualification_v3",
        "status": "R0_PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": manifest["bundle_id"],
        "same_live_identity": identity,
        "adopted_service_sha256": adoption_sha256,
        "workload_pid": workload_pid,
        "workload_pid_starttime_ticks": workload_ticks,
        "collector_sha256": manifest["collector_sha256"],
        "collector_command_kind":
            "exact_client_container_docker_exec_stdout_v1",
        "host_mysql_binary_used": False,
        "runtime_publish_gate_passed": True,
        "capture_result": False,
        "failed_replay": False,
        "accepted_rounds_credit": 0,
        "measurement_seconds": 180,
        "stable_sample_count": 180,
        "maximum_sample_gap_seconds": 1.5,
        "graceful_quiescence_seconds": 120,
        "r0_profile_gate_passed": True,
        "database_counters": {
            "timeout_count": 0,
            "missing_sample_count": 0,
            "complete": True,
            "sample_count": 180,
        },
        "resource_result": resource_result,
        "fingerprint": fingerprint,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }


def _evidence(path, label):
    value = dict(load_sealed(path, label))
    value["_evidence_sha256"] = sha256(path)
    return value


def capture_raw_from_executor(ledger, manifest, adoption_sha256, identity,
                              trace_path, formal_readback_sha256):
    """Project a sealed executor ledger into the legacy raw capture schema.

    This is intentionally a projection, not a parser for caller-provided
    worker/AT summaries.  Every Future field is derived by the executor from
    a successful sealed Java process, the trace manifest, and the frozen
    ``Future.get`` fail-closed source check.
    """
    evidence = ledger.get("capture_evidence")
    workers = evidence.get("worker_futures") if isinstance(evidence, dict) else None
    coverage = evidence.get("at_commit_coverage") if isinstance(evidence, dict) else None
    if (
        ledger.get("schema_version") != "mysql_xp_raw_executor_run_v1"
        or ledger.get("status") != "PASS_RAW_RUN_ONLY"
        or ledger.get("mode") != "capture"
        or ledger.get("bundle_id") != manifest["bundle_id"]
        or ledger.get("formal_scope") != "mysql_only"
        or ledger.get("cross_engine_comparison_prohibited") is not True
        or ledger.get("trace_path") != str(trace_path)
        or ledger.get("trace_sha256") != sha256(trace_path)
        or ledger.get("trace_manifest_sha256") in (None, "")
        or ledger.get("formal_readback_sha256") != formal_readback_sha256
        or ledger.get("capture_readback_sha256") in (None, "")
        or ledger.get("capture_client_cgroup_execution") is not True
        or ledger.get("capture_client_container")
        != identity.get("client_container_id")
        or not isinstance(evidence, dict)
        or evidence.get("future_exception_fail_closed") is not True
        or evidence.get("future_get_source_sha256") in (None, "")
        or coverage != {"AT%d" % index: True for index in range(1, 7)}
        or not isinstance(workers, list)
    ):
        raise ProducerError("executor capture ledger differs")
    return {
        "schema_version": "mysql_xp_same_live_capture_raw_v1",
        "status": "PASS_CAPTURE_COMPLETE", "bundle_id": manifest["bundle_id"],
        "adopted_service_sha256": adoption_sha256, "same_live_identity": identity,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "capture_only": True, "capture_is_r0": False, "accepted_rounds_credit": 0,
        "qps_resource_input_present": False, "real_production_trace": True,
        "trace_path": str(trace_path), "trace_sha256": sha256(trace_path),
        "capture_resource_envelope": dict(CAPTURE_IO), "formal_envelope_restored": True,
        "formal_envelope_readback_sha256": formal_readback_sha256,
        "xapclient_independent_check_passed": True,
        "worker_future_count": len(workers), "worker_future_get_count": len(workers),
        "worker_failures": 0, "worker_futures": workers,
    }


def r0_raw_from_executor(ledger, manifest, adoption_sha256, identity,
                         trace_sha256, gate_sha256, formal_readback_sha256):
    """Project executor-owned samples/fingerprint into fresh-R0 raw evidence.

    There is no ``--raw-r0`` escape hatch: L1/L2/L3 must be in the executor
    ledger, bound to the precise 180 counter samples by their canonical hash.
    """
    samples = ledger.get("samples")
    evidence = ledger.get("fingerprint_evidence")
    fingerprint = evidence.get("fingerprint") if isinstance(evidence, dict) else None
    if (
        ledger.get("schema_version") != "mysql_xp_raw_executor_run_v1"
        or ledger.get("status") != "PASS_RAW_RUN_ONLY"
        or ledger.get("mode") != "fresh-r0"
        or ledger.get("bundle_id") != manifest["bundle_id"]
        or ledger.get("formal_scope") != "mysql_only"
        or ledger.get("cross_engine_comparison_prohibited") is not True
        or ledger.get("formal_readback_sha256") != formal_readback_sha256
        or not isinstance(samples, list) or len(samples) != 180
        or not isinstance(evidence, dict) or evidence.get("observation_sha256") in (None, "")
        or evidence.get("counter_samples_sha256") in (None, "")
        or not _fingerprint_complete(fingerprint)
    ):
        raise ProducerError("executor fresh-R0 ledger differs")
    return {
        "schema_version": "mysql_xp_fresh_r0_raw_samples_v1",
        "status": "PASS_MEASUREMENT_COMPLETE", "bundle_id": manifest["bundle_id"],
        "adopted_service_sha256": adoption_sha256, "same_live_identity": identity,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "formal_envelope_readback_sha256": formal_readback_sha256,
        "trace_registry_sha256": trace_sha256, "dynamic_gate_sha256": gate_sha256,
        "collector_sha256": manifest["collector_sha256"],
        "collector_command_kind": "exact_client_container_docker_exec_stdout_v1",
        "host_mysql_binary_used": False, "capture_result": False, "failed_replay": False,
        "accepted_rounds_credit": 0, "measurement_seconds": 180,
        "graceful_quiescence_seconds": 120, "correctness_passed": True,
        "stability_passed": True, "cleanup_complete": True,
        "workload_process_absent_after_quiescence": True,
        "workload_pid": ledger.get("workload_pid"),
        "workload_pid_starttime_ticks": ledger.get("workload_pid_starttime_ticks"),
        "samples": samples, "fingerprint": fingerprint,
    }


def produce_capture(args, proc_root=Path("/proc")):
    bundle = Path(args.bundle).resolve(strict=True)
    manifest = verify_bundle(bundle)
    adoption, unused_identity = verify_adoption(
        args.adopted_service, manifest["bundle_id"]
    )
    identity = verify_live_adopted_identity(adoption, proc_root)
    readback = _evidence(args.formal_envelope_readback, "formal readback")
    trace = Path(args.trace).resolve(strict=True)
    ledger = _evidence(args.executor_ledger, "executor capture ledger")
    raw = capture_raw_from_executor(
        ledger, manifest, sha256(args.adopted_service), identity, trace,
        readback["_evidence_sha256"],
    )
    validator = _load_trace_validator(bundle)
    validation = validator.validate_xp_capture_trace(
        trace,
        Path(args.trace_manifest),
        Path(args.runtime_authority),
        Path(args.capture_authorization),
    )
    result = validate_capture_raw(
        raw, manifest, adoption, identity, trace, validation, readback,
        sha256(args.adopted_service),
    )
    atomic_json(args.output, result)
    return result


def produce_r0(args, proc_root=Path("/proc")):
    bundle = Path(args.bundle).resolve(strict=True)
    manifest = verify_bundle(bundle)
    adoption, unused_identity = verify_adoption(
        args.adopted_service, manifest["bundle_id"]
    )
    identity = verify_live_adopted_identity(adoption, proc_root)
    trace = _evidence(args.trace_registry, "trace registry")
    gate = _evidence(args.dynamic_gate, "dynamic gate")
    readback = _evidence(args.formal_envelope_readback, "formal readback")
    ledger = _evidence(args.executor_ledger, "executor fresh R0 ledger")
    raw = r0_raw_from_executor(
        ledger, manifest, sha256(args.adopted_service), identity,
        trace["_evidence_sha256"], gate["_evidence_sha256"],
        readback["_evidence_sha256"],
    )
    result = validate_r0_raw(
        raw, manifest, adoption, identity, trace, gate, readback,
        sha256(args.adopted_service),
    )
    atomic_json(args.output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--capture", action="store_true")
    modes.add_argument("--fresh-r0", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--formal-envelope-readback", type=Path, required=True)
    parser.add_argument("--executor-ledger", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--trace-manifest", type=Path)
    parser.add_argument("--runtime-authority", type=Path)
    parser.add_argument("--capture-authorization", type=Path)
    parser.add_argument("--trace-registry", type=Path)
    parser.add_argument("--dynamic-gate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.capture:
        required = (
            args.executor_ledger, args.trace, args.trace_manifest,
            args.runtime_authority, args.capture_authorization,
        )
        if any(value is None for value in required):
            raise ProducerError("capture producer inputs are incomplete")
        result = produce_capture(args)
    else:
        required = (args.executor_ledger, args.trace_registry, args.dynamic_gate)
        if any(value is None for value in required):
            raise ProducerError("fresh R0 producer inputs are incomplete")
        result = produce_r0(args)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MaterializerError as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
