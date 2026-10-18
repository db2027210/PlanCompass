#!/usr/bin/env python3
"""Fail-closed XP distributed formal-10 readiness verifier.

This module is deliberately data-plane agnostic.  It validates an immutable
XP v2 successor and, when evidence files are supplied, proves that the exact
successor crossed each promotion gate.  It never launches a workload,
container, or remote command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


class ReadinessError(RuntimeError):
    pass


EXPECTED_PARAMETERS = [
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_row_cost",
]
EXPECTED_GRADIENT_FIELDS = [
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
]
EXPECTED_DB_ENVELOPE = {
    "cpu_quota_cores": 5.3,
    "cpuset_cpus": "21-33,57-69",
    "cpuset_mems": "1",
    "innodb_buffer_pool_bytes": 4294967296,
    "io_read_bps": 29360128,
    "io_write_bps": 524288,
    "memory_bytes": 8589934592,
    "memory_swap_bytes": 8589934592,
    "mysql_port": 65104,
    "pids_max": 1024,
}
EXPECTED_CLIENT_ENVELOPE = {
    "cpu_quota_cores": 18,
    "cpuset_cpus": "3-11,39-47",
    "cpuset_mems": "0",
    "docker_log_driver": "none",
    "execution_mode": "dedicated_read_only_docker_client_cgroup",
    "linux_capabilities_drop": "ALL",
    "measurement_output_medium": "tmpfs",
    "memory_bytes": 8589934592,
    "memory_swap_bytes": 8589934592,
    "network_mode": "host",
    "no_new_privileges": True,
    "pid_and_all_descendant_affinity_readback_required": True,
    "pids_max": 1024,
    "root_filesystem_read_only": True,
    "runtime_and_bundle_mounts_read_only": True,
    "target_mysql_device_write_prohibited": True,
    "tmpfs_bytes": 2147483648,
}


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ReadinessError(f"{label} must be a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"{label} is unreadable/invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"{label} must be a JSON object")
    return value


def verify_sidecar(path: Path, label: str) -> None:
    sidecar = Path(str(path) + ".sha256")
    expected = f"{sha256(path)}  {path.name}\n"
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise ReadinessError(f"{label} SHA-256 sidecar absent or differs")


def _relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReadinessError(f"symlink prohibited in XP bundle: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def _require_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ReadinessError(f"{label} differs")


def verify_bundle(bundle: Path) -> dict[str, Any]:
    bundle = bundle.resolve()
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path, "XP v2 manifest")
    verify_sidecar(manifest_path, "XP v2 manifest")
    if (
        manifest.get("schema_version")
        != "mysql_xp_distributed_formal10_successor_v2"
        or manifest.get("bundle_id") != bundle.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("immutable") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise ReadinessError("XP v2 identity/scope/authority differs")
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if hashlib.sha256(canonical(identity)).hexdigest() != bundle.name:
        raise ReadinessError("XP v2 content address differs")
    actual = _relative_files(bundle)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    _require_equal(actual, manifest.get("files"), "XP v2 complete file closure")
    for path in [bundle, *sorted(bundle.rglob("*"))]:
        if path.stat().st_mode & 0o222:
            raise ReadinessError(f"writable entry in XP immutable bundle: {path}")

    contract = load_object(
        bundle / "xp_formal10_contract.json", "XP formal-10 contract"
    )
    _require_equal(
        sha256(bundle / "xp_formal10_contract.json"),
        manifest.get("formal10_contract_sha256"),
        "XP formal-10 contract hash",
    )
    if (
        contract.get("schema_version")
        != "mysql_xp_algorithm_c_formal10_contract_v2"
        or contract.get("scenario") != "hybench_xp_io"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("formal_launch_authorized") is not False
        or contract.get("accepted_round_authorized") is not False
    ):
        raise ReadinessError("XP formal-10 static contract differs")
    _require_equal(
        contract.get("database_resource_envelope"),
        EXPECTED_DB_ENVELOPE,
        "XP database resource envelope",
    )
    _require_equal(
        contract.get("client_resource_envelope"),
        EXPECTED_CLIENT_ENVELOPE,
        "XP client resource envelope",
    )
    algorithm = contract.get("algorithm")
    if not isinstance(algorithm, dict):
        raise ReadinessError("XP Algorithm C contract absent")
    _require_equal(
        algorithm.get("active_parameters"),
        EXPECTED_PARAMETERS,
        "XP active parameter order",
    )
    if (
        algorithm.get("active_parameter_count") != 7
        or algorithm.get("required_nonempty_subsets") != 127
        or algorithm.get("candidate_order")
        != "global_descending_final_candidate_score"
        or algorithm.get("fixed_real_replay_top_k") != 5
        or algorithm.get("target_accepted_rounds") != 10
        or algorithm.get("qps_role") != "real_replay_outer_acceptance_only"
        or algorithm.get("original_gradient_only") is not True
        or algorithm.get("gradient_formula")
        != "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
    ):
        raise ReadinessError("XP Algorithm C cardinality/ranking/gradient differs")
    _require_equal(
        algorithm.get("required_gradient_event_fields"),
        EXPECTED_GRADIENT_FIELDS,
        "XP gradient event field set/order",
    )
    return {"manifest": manifest, "contract": contract}


def _require_bundle_binding(
    document: Mapping[str, Any],
    bundle_id: str,
    bundle_manifest_sha256: str,
    label: str,
) -> None:
    if (
        document.get("bundle_id") != bundle_id
        or document.get("bundle_manifest_sha256") != bundle_manifest_sha256
        or document.get("formal_scope") != "mysql_only"
        or document.get("cross_engine_comparison_prohibited") is not True
    ):
        raise ReadinessError(f"{label} is not bound to this XP v2 bundle")


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReadinessError(f"{label} must be a positive integer")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ReadinessError(f"{label} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ReadinessError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise ReadinessError(f"{label} must be finite")
    return result


def verify_promotion_evidence(
    bundle: Path,
    *,
    at_registry_path: Path,
    trace_registry_path: Path,
    dynamic_gate_path: Path,
    db_readback_path: Path,
    client_readback_path: Path,
    fresh_r0_path: Path,
    debug_round_path: Path,
) -> dict[str, Any]:
    static = verify_bundle(bundle)
    manifest = static["manifest"]
    contract = static["contract"]
    bundle_id = str(manifest["bundle_id"])
    manifest_sha = sha256(bundle / "bundle_manifest.json")
    paths = {
        "at_registry": at_registry_path,
        "trace_registry": trace_registry_path,
        "dynamic_gate": dynamic_gate_path,
        "db_readback": db_readback_path,
        "client_readback": client_readback_path,
        "fresh_r0": fresh_r0_path,
        "debug_round": debug_round_path,
    }
    documents: dict[str, dict[str, Any]] = {}
    for label, path in paths.items():
        document = load_object(path, label)
        verify_sidecar(path, label)
        _require_bundle_binding(document, bundle_id, manifest_sha, label)
        documents[label] = document

    at = documents["at_registry"]
    if (
        at.get("schema_version") != "mysql_xp_at1_at6_registry_v2"
        or at.get("status") != "SEALED"
        or at.get("sealed") is not True
        or at.get("capture_only") is not True
        or at.get("capture_is_r0") is not False
        or at.get("formal_r0_authorized") is not True
        or at.get("outer_execution_authorized") is not False
        or at.get("accepted_round_authorized") is not False
        or at.get("coverage")
        != {f"AT{index}": True for index in range(1, 7)}
        or at.get("worker_failures") != 0
        or at.get("qps_resource_input_present") is not False
    ):
        raise ReadinessError("XP AT1-AT6 registry is not a safe complete seal")

    trace = documents["trace_registry"]
    if (
        trace.get("schema_version") != "mysql_xp_production_trace_registry_v2"
        or trace.get("status") != "SEALED"
        or trace.get("immutable") is not True
        or trace.get("capture_only") is not True
        or trace.get("formal_replay_schedule_unchanged") is not True
        or trace.get("worker_future_get_verified") is not True
        or trace.get("xapclient_independent_check_verified") is not True
        or trace.get("at_registry_sha256") != sha256(at_registry_path)
    ):
        raise ReadinessError("XP production trace registry differs")
    trace_path = Path(str(trace.get("trace_path") or ""))
    if (
        not trace_path.is_absolute()
        or not trace_path.is_file()
        or trace_path.is_symlink()
        or trace.get("trace_sha256") != sha256(trace_path)
    ):
        raise ReadinessError("XP production trace file identity differs")

    gate = documents["dynamic_gate"]
    if (
        gate.get("schema_version")
        != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or gate.get("status") != "PASS"
        or gate.get("bidirectional") is not True
        or gate.get("duration_seconds_each_direction") != 180
        or gate.get("foreign_device_io_attribution_bytes") != 0
        or gate.get("same_collector_version") is not True
        or gate.get("all_required_streams_complete") is not True
    ):
        raise ReadinessError("XP bidirectional noninterference gate differs")

    db = documents["db_readback"]
    if (
        db.get("schema_version") != "mysql_xp_database_envelope_readback_v2"
        or db.get("status") != "PASS"
        or db.get("effective_resource_envelope") != EXPECTED_DB_ENVELOPE
        or db.get("full_physical_copy") is not True
        or db.get("overlay_or_reflink") is not False
        or db.get("device_major_minor") != "259:4"
    ):
        raise ReadinessError("XP database live envelope/readback differs")
    container_id = str(db.get("container_id") or "")
    mysql_pid = _positive_integer(db.get("mysql_pid"), "XP mysql PID")
    mysql_starttime_ticks = _positive_integer(
        db.get("mysql_starttime_ticks"), "XP mysql starttime"
    )
    physical_datadir = str(db.get("physical_datadir") or "")
    handoff_token = str(db.get("live_identity_handoff_token") or "")
    if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
        raise ReadinessError("XP database container id must be 64 lowercase hex")
    if (
        not physical_datadir.startswith("/mnt/mysql_nvme0n1p1/")
        or len(handoff_token) < 32
        or any(character.isspace() for character in handoff_token)
        or gate.get("live_identity_handoff_token") != handoff_token
        or gate.get("container_id") != container_id
        or gate.get("mysql_pid") != mysql_pid
        or gate.get("mysql_starttime_ticks") != mysql_starttime_ticks
        or gate.get("physical_datadir") != physical_datadir
    ):
        raise ReadinessError("XP database/gate live identity handoff differs")

    client = documents["client_readback"]
    if (
        client.get("schema_version") != "mysql_xp_client_envelope_readback_v2"
        or client.get("status") != "PASS"
        or client.get("effective_resource_envelope") != EXPECTED_CLIENT_ENVELOPE
        or client.get("root_and_all_descendants_exact") is not True
        or client.get("target_mysql_device_write_bytes") != 0
        or client.get("bundle_mount_read_only") is not True
        or client.get("runtime_mount_read_only") is not True
        or client.get("root_filesystem_read_only") is not True
    ):
        raise ReadinessError("XP client Docker envelope/readback differs")
    client_container_id = str(client.get("client_container_id") or "")
    workload_pid = _positive_integer(
        client.get("workload_pid"), "XP workload PID"
    )
    descendant_pids = client.get("client_descendant_pids")
    if (
        len(client_container_id) != 64
        or any(
            character not in "0123456789abcdef"
            for character in client_container_id
        )
        or not isinstance(descendant_pids, list)
        or workload_pid not in descendant_pids
        or client.get("live_identity_handoff_token") != handoff_token
        or gate.get("client_container_id") != client_container_id
        or gate.get("workload_pid") != workload_pid
    ):
        raise ReadinessError("XP client/gate live identity handoff differs")

    r0 = documents["fresh_r0"]
    if (
        r0.get("schema_version") != "mysql_xp_fresh_r0_result_v2"
        or r0.get("status") != "R0_PASS"
        or r0.get("capture_result") is not False
        or r0.get("failed_replay") is not False
        or r0.get("accepted_round_credit") != 0
        or r0.get("container_id") != container_id
        or r0.get("mysql_pid") != mysql_pid
        or r0.get("mysql_starttime_ticks") != mysql_starttime_ticks
        or r0.get("physical_datadir") != physical_datadir
        or r0.get("client_container_id") != client_container_id
        or r0.get("workload_pid") != workload_pid
        or r0.get("live_identity_handoff_token") != handoff_token
        or r0.get("at_registry_sha256") != sha256(at_registry_path)
        or r0.get("trace_registry_sha256") != sha256(trace_registry_path)
        or r0.get("dynamic_gate_sha256") != sha256(dynamic_gate_path)
        or r0.get("database_counter_timeout_count") != 0
        or r0.get("all_required_l1_l2_l3_present") is not True
    ):
        raise ReadinessError("XP fresh R0 authority differs")

    debug = documents["debug_round"]
    if (
        debug.get("schema_version") != "mysql_xp_algorithm_c_debug_round_v2"
        or debug.get("status") != "PASS"
        or debug.get("container_id") != container_id
        or debug.get("mysql_pid") != mysql_pid
        or debug.get("mysql_starttime_ticks") != mysql_starttime_ticks
        or debug.get("physical_datadir") != physical_datadir
        or debug.get("client_container_id") != client_container_id
        or debug.get("workload_pid") != workload_pid
        or debug.get("live_identity_handoff_token") != handoff_token
        or debug.get("fresh_r0_sha256") != sha256(fresh_r0_path)
        or debug.get("enumerated_nonempty_subsets") != 127
        or debug.get("completed_nonempty_subsets") != 127
        or debug.get("global_candidate_score_sort_verified") is not True
        or debug.get("sealed_top5_ranks") != [1, 2, 3, 4, 5]
        or debug.get("replayed_top5_ranks") != [1, 2, 3, 4, 5]
        or debug.get("real_replay_success_count") != 5
        or debug.get("prediction_vs_real_ranking_compared") is not True
        or debug.get("original_gradient_only") is not True
        or debug.get("parameter_update_source") != "g_j_and_eta_only"
        or debug.get("qps_used_before_real_replay") is not False
        or debug.get("missing_fingerprint_evidence_deduplicated") is not False
        or debug.get("failed_replay_accepted") is not False
        or debug.get("accepted_round_credit") != 0
    ):
        raise ReadinessError("XP full-candidate/fixed-Top5 debug round differs")
    events = debug.get("gradient_events")
    if not isinstance(events, list) or not events:
        raise ReadinessError("XP debug gradient events absent")
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ReadinessError(f"XP gradient event {index} is not an object")
        missing = [field for field in EXPECTED_GRADIENT_FIELDS if field not in event]
        if missing:
            raise ReadinessError(
                f"XP gradient event {index} misses fields: {','.join(missing)}"
            )
        _finite(event["g_j"], f"XP gradient event {index} g_j")
        _finite(event["eta"], f"XP gradient event {index} eta")
        _finite(event["delta_param"], f"XP gradient event {index} delta_param")
        _finite(
            event["candidate_score"],
            f"XP gradient event {index} candidate_score",
        )

    return {
        "schema_version": "mysql_xp_formal10_readiness_result_v2",
        "status": "FORMAL10_READY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle_id,
        "bundle_manifest_sha256": manifest_sha,
        "container_id": container_id,
        "mysql_pid": mysql_pid,
        "mysql_starttime_ticks": mysql_starttime_ticks,
        "physical_datadir": physical_datadir,
        "client_container_id": client_container_id,
        "workload_pid": workload_pid,
        "live_identity_handoff_token": handoff_token,
        "fresh_r0_passed": True,
        "enumerated_nonempty_subsets": 127,
        "fixed_top5_real_replay_passed": True,
        "target_accepted_rounds": contract["algorithm"]["target_accepted_rounds"],
        "accepted_rounds_at_handoff": 0,
        "formal_launch_authorized": True,
        "accepted_round_authorized": False,
        "main_agent_launch_decision_required": True,
        "evidence_sha256": {
            label: sha256(path) for label, path in paths.items()
        },
    }


def atomic_write_result(path: Path, value: Mapping[str, Any]) -> None:
    path = path.resolve()
    if path.exists() or path.is_symlink():
        raise ReadinessError("readiness output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise ReadinessError("readiness temporary output already exists")
    temporary.write_bytes(payload)
    temporary.replace(path)
    sidecar = Path(str(path) + ".sha256")
    sidecar.write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--at-registry", type=Path)
    parser.add_argument("--trace-registry", type=Path)
    parser.add_argument("--dynamic-gate", type=Path)
    parser.add_argument("--db-readback", type=Path)
    parser.add_argument("--client-readback", type=Path)
    parser.add_argument("--fresh-r0", type=Path)
    parser.add_argument("--debug-round", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.static_only:
        static = verify_bundle(args.bundle)
        result = {
            "schema_version": "mysql_xp_formal10_readiness_result_v2",
            "status": "STATIC_PASS_EVIDENCE_PENDING",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "bundle_id": static["manifest"]["bundle_id"],
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
        }
    else:
        required = {
            "at_registry_path": args.at_registry,
            "trace_registry_path": args.trace_registry,
            "dynamic_gate_path": args.dynamic_gate,
            "db_readback_path": args.db_readback,
            "client_readback_path": args.client_readback,
            "fresh_r0_path": args.fresh_r0,
            "debug_round_path": args.debug_round,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ReadinessError(
                "all promotion evidence is required: " + ",".join(missing)
            )
        result = verify_promotion_evidence(args.bundle, **required)
    if args.output is not None:
        atomic_write_result(args.output, result)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
