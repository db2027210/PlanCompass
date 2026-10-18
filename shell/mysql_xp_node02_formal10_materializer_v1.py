#!/usr/bin/env python3
"""Materialize the fail-closed XP evidence chain through formal-10 handoff.

This utility is evidence-only: it never starts a process or container.  Each
stage consumes immutable JSON plus a matching SHA-256 sidecar, validates the
whole prior chain and writes a new non-overwriting JSON/sidecar pair.
"""

import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re


PARAMETERS = [
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_row_cost",
]
L3_FIELDS = {
    "shared_read_blocks",
    "shared_write_blocks",
    "local_read_blocks",
    "local_write_blocks",
    "temp_read_blocks",
    "temp_write_blocks",
    "io_wait_ms",
}
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COLLECTOR_RELATIVE = (
    "qualification/mysql_distributed_live_readback_qualification_v1.py"
)


class MaterializerError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_sealed(path, label):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if path.is_symlink() or not path.is_file():
        raise MaterializerError("%s must be a regular file" % label)
    expected = "%s  %s\n" % (sha256(path), path.name)
    if (
        sidecar.is_symlink()
        or not sidecar.is_file()
        or sidecar.read_text(encoding="ascii") != expected
    ):
        raise MaterializerError("%s sidecar differs" % label)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MaterializerError("%s must be a JSON object" % label)
    return value


def atomic_json(path, value):
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if (
        path.exists()
        or path.is_symlink()
        or sidecar.exists()
        or sidecar.is_symlink()
    ):
        raise MaterializerError("refusing to overwrite output")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    temporary = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    temporary.write_bytes(payload)
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(str(temporary), str(path))
    sidecar.write_text(
        "%s  %s\n" % (hashlib.sha256(payload).hexdigest(), path.name),
        encoding="ascii",
    )


def verify_bundle(bundle):
    bundle = Path(bundle).resolve()
    manifest = load_sealed(bundle / "bundle_manifest.json", "bundle manifest")
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    if (
        bundle_id != bundle.name
        or SHA_RE.fullmatch(bundle.name) is None
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle.name
        or manifest.get("schema_version") not in (
            "mysql_xp_distributed_formal10_launch_v3",
            "mysql_xp_node02_successor_bundle_v18",
            "mysql_xp_node02_successor_bundle_v19",
        )
        or manifest.get("scenario") != "hybench_xp_io"
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise MaterializerError("launch bundle identity/scope differs")
    files = manifest.get("files")
    expected_collector = manifest.get("collector_sha256")
    collector = bundle / COLLECTOR_RELATIVE
    if (
        not isinstance(files, dict)
        or files.get(COLLECTOR_RELATIVE) != expected_collector
        or SHA_RE.fullmatch(str(expected_collector or "")) is None
        or collector.is_symlink()
        or not collector.is_file()
        or sha256(collector) != expected_collector
    ):
        raise MaterializerError("manifest-bound collector identity differs")
    return manifest


def _binding(document, bundle_id, label):
    if (
        document.get("bundle_id") != bundle_id
        or document.get("formal_scope") != "mysql_only"
        or document.get("cross_engine_comparison_prohibited") is not True
    ):
        raise MaterializerError("%s bundle/scope binding differs" % label)


def _same_live(adoption):
    database = adoption.get("database") or {}
    client = adoption.get("client") or {}
    identity = {
        "database_container_id": database.get("container_id"),
        "mysql_pid": database.get("pid"),
        "mysql_starttime_ticks": database.get("pid_starttime_ticks"),
        "physical_datadir": adoption.get("physical_datadir"),
        "client_container_id": client.get("container_id"),
        "client_init_pid": client.get("pid"),
        "client_init_starttime_ticks": client.get("pid_starttime_ticks"),
        "attempt_id": adoption.get("attempt_id"),
        "handoff_token_sha256": adoption.get("handoff_token_sha256"),
    }
    if (
        any(value in (None, "") for value in identity.values())
        or len(str(identity["database_container_id"])) != 64
        or len(str(identity["client_container_id"])) != 64
        or int(identity["mysql_pid"]) <= 1
        or int(identity["client_init_pid"]) <= 1
    ):
        raise MaterializerError("adopted same-live identity is incomplete")
    return identity


def verify_adoption(path, bundle_id):
    adoption = load_sealed(path, "adopted service")
    _binding(adoption, bundle_id, "adopted service")
    bridge = adoption.get("sql_stdout_bridge") or {}
    runtime = adoption.get("runtime_publication") or {}
    if (
        adoption.get("schema_version")
        != "mysql_xp_node02_adopted_service_authority_v1"
        or adoption.get("status") != "PASS_ADOPTED_SAME_LIVE_SERVICE"
        or adoption.get("host_mysql_binary_used") is not False
        or bridge.get("kind")
        != "collector_managed_client_docker_exec_stdout_v1"
        or bridge.get("host_mysql_binary_prohibited") is not True
        or bridge.get("stdout_only") is not True
        or bridge.get("client_private_tmpfs_not_host_bound") is not True
        or runtime.get("tree_content_sha256")
        != "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
        or runtime.get("publication_sha256") in (None, "")
        or adoption.get("formal_launch_authorized") is not False
    ):
        raise MaterializerError("adopted service authority differs")
    return adoption, _same_live(adoption)


def materialize_trace(bundle, adoption_path, capture_path):
    bundle_id = verify_bundle(bundle)["bundle_id"]
    adoption, identity = verify_adoption(adoption_path, bundle_id)
    capture = load_sealed(capture_path, "production AT capture")
    _binding(capture, bundle_id, "production AT capture")
    coverage = capture.get("coverage")
    trace_path = Path(str(capture.get("trace_path") or ""))
    trace_sidecar = Path(str(trace_path) + ".sha256")
    if (
        capture.get("schema_version")
        != "mysql_xp_production_at_capture_v3"
        or capture.get("status") != "PASS_CAPTURE_COMPLETE"
        or capture.get("same_live_identity") != identity
        or capture.get("capture_only") is not True
        or capture.get("capture_is_r0") is not False
        or capture.get("accepted_rounds_credit") != 0
        or coverage != {"AT%d" % index: True for index in range(1, 7)}
        or capture.get("worker_future_count", 0) <= 0
        or capture.get("worker_future_get_count")
        != capture.get("worker_future_count")
        or capture.get("worker_failures") != 0
        or capture.get("xapclient_independent_check_passed") is not True
        or capture.get("qps_resource_input_present") is not False
        or capture.get("real_production_trace") is not True
        or not trace_path.is_absolute()
        or trace_path.is_symlink()
        or not trace_path.is_file()
        or not trace_sidecar.is_file()
        or trace_sidecar.read_text(encoding="ascii")
        != "%s  %s\n" % (sha256(trace_path), trace_path.name)
        or capture.get("trace_sha256") != sha256(trace_path)
    ):
        raise MaterializerError("AT1-AT6 production capture is incomplete")
    return {
        "schema_version": "mysql_xp_production_trace_registry_v3",
        "status": "SEALED_REAL_TRACE",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle_id,
        "same_live_identity": identity,
        "adopted_service_sha256": sha256(adoption_path),
        "runtime_publication_sha256":
            adoption["runtime_publication"]["publication_sha256"],
        "capture_evidence_sha256": sha256(capture_path),
        "coverage": coverage,
        "trace_path": str(trace_path),
        "trace_sha256": sha256(trace_path),
        "worker_future_get_verified": True,
        "xapclient_independent_check_verified": True,
        "capture_only": True,
        "capture_is_r0": False,
        "qps_resource_input_present": False,
        "formal_r0_authorized": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }


def _verify_gate(gate, bundle_id, identity):
    _binding(gate, bundle_id, "dynamic gate")
    if (
        gate.get("schema_version")
        != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or gate.get("status") != "PASS"
        or gate.get("same_live_identity") != identity
        or gate.get("bidirectional") is not True
        or gate.get("duration_seconds_each_direction") != 180
        or gate.get("same_collector_version") is not True
        or gate.get("all_required_streams_complete") is not True
        or gate.get("database_counter_timeout_count") != 0
        or gate.get("foreign_device_io_attribution_bytes") != 0
        or gate.get("rolling_window_gate_passed") is not True
    ):
        raise MaterializerError("180s bidirectional dynamic gate differs")


def normalize_dynamic_gate(
    raw, bundle_id, identity, adopted_service_sha256, collector_sha256,
    raw_dynamic_gate_sha256,
):
    directions = raw.get("directions")
    expected_directions = {
        "A_solo_vs_A_while_B_runs",
        "B_solo_vs_B_while_A_runs",
    }
    streams = raw.get("streams_complete")
    required_streams = raw.get("required_streams")
    if (
        raw.get("schema_version")
        != "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        or raw.get("status") != "PASS"
        or raw.get("scenario") != "hybench_xp_io"
        or raw.get("formal_scope") != "mysql_only"
        or raw.get("cross_engine_comparison_prohibited") is not True
        or raw.get("duration_seconds") != 180
        or raw.get("database_counter_timeout_count") != 0
        or raw.get("both_directions_same_version_verified") is not True
        or raw.get("authorization_reuse_verified_absent") is not True
        or raw.get("ongoing_rolling_window_required") is not True
        or raw.get("serial_fallback_required") is not False
        or raw.get("collector_sha256") != collector_sha256
        or not isinstance(streams, list)
        or not streams
        or streams != required_streams
        or not isinstance(directions, dict)
        or set(directions) != expected_directions
        or any(
            not isinstance(row, dict)
            or row.get("passed") is not True
            or row.get("foreign_device_io_attribution_bytes") != 0
            for row in directions.values()
        )
    ):
        raise MaterializerError(
            "raw 180s bidirectional dynamic gate differs"
        )
    result = dict(raw)
    result.update({
        "bundle_id": bundle_id,
        "same_live_identity": identity,
        "adopted_service_sha256": adopted_service_sha256,
        "raw_dynamic_gate_sha256": raw_dynamic_gate_sha256,
        "bidirectional": True,
        "duration_seconds_each_direction": 180,
        "same_collector_version": True,
        "all_required_streams_complete": True,
        "foreign_device_io_attribution_bytes": 0,
        "rolling_window_gate_passed": True,
        "formal_r0_authorized": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    })
    return result


def materialize_dynamic_gate(bundle, adoption_path, raw_gate_path):
    manifest = verify_bundle(bundle)
    bundle_id = manifest["bundle_id"]
    unused_adoption, identity = verify_adoption(adoption_path, bundle_id)
    raw = load_sealed(raw_gate_path, "raw dynamic gate")
    return normalize_dynamic_gate(
        raw,
        bundle_id,
        identity,
        sha256(adoption_path),
        manifest["collector_sha256"],
        sha256(raw_gate_path),
    )


def _fingerprint_complete(row):
    return (
        isinstance(row, dict)
        and isinstance(row.get("parameter_signature"), str)
        and bool(row["parameter_signature"])
        and isinstance(row.get("l1"), dict)
        and bool(row["l1"])
        and isinstance(row.get("l2"), dict)
        and bool(row["l2"])
        and isinstance(row.get("l3"), dict)
        and set(row["l3"]) == L3_FIELDS
        and all(row["l3"][key] is not None for key in L3_FIELDS)
    )


def materialize_fresh_r0(
    bundle, adoption_path, trace_path, gate_path, qualification_path
):
    manifest = verify_bundle(bundle)
    bundle_id = manifest["bundle_id"]
    collector_sha256 = manifest["collector_sha256"]
    adoption, identity = verify_adoption(adoption_path, bundle_id)
    trace = load_sealed(trace_path, "trace registry")
    gate = load_sealed(gate_path, "dynamic gate")
    qualification = load_sealed(qualification_path, "fresh R0 qualification")
    _binding(trace, bundle_id, "trace registry")
    _binding(qualification, bundle_id, "fresh R0 qualification")
    _verify_gate(gate, bundle_id, identity)
    database = qualification.get("database_counters") or {}
    resource = qualification.get("resource_result") or {}
    fingerprint = qualification.get("fingerprint")
    if (
        trace.get("schema_version")
        != "mysql_xp_production_trace_registry_v3"
        or trace.get("status") != "SEALED_REAL_TRACE"
        or trace.get("same_live_identity") != identity
        or trace.get("adopted_service_sha256") != sha256(adoption_path)
        or trace.get("formal_r0_authorized") is not True
        or qualification.get("schema_version")
        != "mysql_xp_fresh_r0_qualification_v3"
        or qualification.get("status") != "R0_PASS"
        or qualification.get("same_live_identity") != identity
        or qualification.get("workload_pid", 0) <= 1
        or qualification.get("workload_pid_starttime_ticks", 0) <= 0
        or qualification.get("collector_sha256")
        != collector_sha256
        or qualification.get("collector_command_kind")
        != "exact_client_container_docker_exec_stdout_v1"
        or qualification.get("host_mysql_binary_used") is not False
        or qualification.get("runtime_publish_gate_passed") is not True
        or qualification.get("capture_result") is not False
        or qualification.get("failed_replay") is not False
        or qualification.get("accepted_rounds_credit") != 0
        or qualification.get("measurement_seconds") != 180
        or qualification.get("stable_sample_count", 0) < 180
        or qualification.get("graceful_quiescence_seconds") != 120
        or qualification.get("r0_profile_gate_passed") is not True
        or database.get("timeout_count") != 0
        or database.get("missing_sample_count") != 0
        or database.get("complete") is not True
        or set(resource) != {"cpu", "memory", "io"}
        or any(not math.isfinite(float(value)) for value in resource.values())
        or not _fingerprint_complete(fingerprint)
    ):
        raise MaterializerError("fresh R0 qualification differs")
    return {
        "schema_version": "mysql_xp_fresh_r0_result_v3",
        "status": "R0_PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_id": bundle_id,
        "same_live_identity": identity,
        "adopted_service_sha256": sha256(adoption_path),
        "trace_registry_sha256": sha256(trace_path),
        "dynamic_gate_sha256": sha256(gate_path),
        "qualification_sha256": sha256(qualification_path),
        "workload_pid": qualification["workload_pid"],
        "workload_pid_starttime_ticks":
            qualification["workload_pid_starttime_ticks"],
        "resource_result": resource,
        "fingerprint": fingerprint,
        "database_counter_timeout_count": 0,
        "all_required_l1_l2_l3_present": True,
        "capture_result": False,
        "failed_replay": False,
        "debug_round_authorized": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }


def finite(value, label):
    if isinstance(value, bool):
        raise MaterializerError("%s is not finite" % label)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise MaterializerError("%s is not finite" % label)
    if not math.isfinite(result):
        raise MaterializerError("%s is not finite" % label)
    return result


def close(left, right):
    return math.isclose(left, right, rel_tol=1e-10, abs_tol=1e-12)


def verify_gradient(event, label):
    required = {
        "feature_ratio", "predicted_resource", "utilization", "dScore/dU",
        "T", "confidence", "g_j", "eta", "delta_param", "candidate_score",
    }
    if (
        not required.issubset(event)
        or event.get("gradient_source")
        != "original_dScore_dU_times_T_times_confidence"
    ):
        raise MaterializerError("%s gradient fields/source differs" % label)
    matrices = [
        event.get("dScore/dU"), event.get("T"), event.get("confidence"),
        event.get("g_j"), event.get("delta_param"),
    ]
    if any(not isinstance(value, dict) or not value for value in matrices):
        raise MaterializerError("%s gradient maps are incomplete" % label)
    slopes, transform, confidence, gradients, deltas = matrices
    if set(transform) != set(slopes) or set(transform) != set(gradients):
        raise MaterializerError("%s gradient parameter sets differ" % label)
    eta = finite(event["eta"], label + ".eta")
    if eta <= 0:
        raise MaterializerError("%s eta must be positive" % label)
    finite(event["candidate_score"], label + ".candidate_score")
    for parameter in sorted(transform):
        if parameter not in PARAMETERS:
            raise MaterializerError("%s has unsealed parameter" % label)
        if (
            not isinstance(transform[parameter], dict)
            or not isinstance(slopes[parameter], dict)
            or set(transform[parameter]) != set(slopes[parameter])
        ):
            raise MaterializerError("%s resource maps differ" % label)
        confidence_map = confidence.get(parameter)
        if not isinstance(confidence_map, dict):
            confidence_map = {
                resource: confidence_map for resource in transform[parameter]
            }
        expected = sum(
            finite(slopes[parameter][resource], label + ".dScore/dU")
            * finite(transform[parameter][resource], label + ".T")
            * finite(confidence_map.get(resource), label + ".confidence")
            for resource in sorted(transform[parameter])
        )
        observed = finite(gradients[parameter], label + ".g_j")
        if not close(expected, observed):
            raise MaterializerError(
                "%s g_j is not sum(dScore/dU*T*confidence)" % label
            )
        delta = deltas.get(parameter)
        if not isinstance(delta, dict):
            raise MaterializerError("%s delta_param differs" % label)
        before = finite(delta.get("before"), label + ".before")
        proposed = finite(
            delta.get("proposed_before_clip"), label + ".proposed"
        )
        after = finite(delta.get("after_clip"), label + ".after")
        absolute = finite(delta.get("absolute"), label + ".absolute")
        relative = finite(delta.get("relative"), label + ".relative")
        if (
            before <= 0
            or not close(proposed, before * math.exp(eta * observed))
            or not close(absolute, after - before)
            or not close(relative, (after - before) / before)
            or (delta.get("clipped") is False and not close(after, proposed))
        ):
            raise MaterializerError(
                "%s update is not derived from g_j and eta" % label
            )


def verify_debug(raw, bundle_id, identity):
    _binding(raw, bundle_id, "debug evidence")
    events = raw.get("gradient_events")
    ranking = raw.get("global_candidate_ranking")
    journals = raw.get("top5_point_journals")
    fingerprints = raw.get("candidate_fingerprints")
    if (
        raw.get("schema_version") != "mysql_xp_algorithm_c_debug_round_v3"
        or raw.get("status") != "PASS_DEBUG_ROUND"
        or raw.get("same_live_identity") != identity
        or raw.get("completed_nonempty_subsets") != 127
        or raw.get("candidate_order")
        != "global_descending_final_candidate_score"
        or raw.get("fixed_top5_only") is not True
        or raw.get("sealed_top5_ranks") != [1, 2, 3, 4, 5]
        or raw.get("replayed_top5_ranks") != [1, 2, 3, 4, 5]
        or raw.get("algorithm_formula")
        != "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)"
        or raw.get("parameter_update_source") != "g_j_and_eta_only"
        or raw.get("replacement_search_algorithm_used") is not False
        or raw.get("qps_role")
        != "outer_acceptance_after_real_replay_only"
        or raw.get("fingerprint_duplicate_rule")
        != "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3"
        or raw.get("missing_fingerprint_level_policy")
        != "independent_candidate"
        or raw.get("prediction_vs_real_ranking_compared") is not True
        or raw.get("accepted_rounds_credit") != 0
        or not isinstance(events, list)
        or len(events) < 127
        or not isinstance(ranking, list)
        or len(ranking) != 127
        or not isinstance(journals, list)
        or len(journals) != 5
        or not isinstance(fingerprints, list)
        or len(fingerprints) != 132
    ):
        raise MaterializerError("XP debug-round structure differs")
    subsets = {}
    for index, event in enumerate(events):
        subset_id = event.get("subset_id")
        active = event.get("active_subset")
        if (
            not isinstance(subset_id, str)
            or not subset_id
            or not isinstance(active, list)
            or not active
            or len(active) != len(set(active))
            or any(parameter not in PARAMETERS for parameter in active)
        ):
            raise MaterializerError("debug active subset differs")
        normalized = tuple(
            parameter for parameter in PARAMETERS if parameter in set(active)
        )
        if subset_id in subsets and subsets[subset_id] != normalized:
            raise MaterializerError("subset ID collision")
        subsets[subset_id] = normalized
        verify_gradient(event, "gradient_events[%d]" % index)
    expected = {
        tuple(row)
        for width in range(1, 8)
        for row in itertools.combinations(PARAMETERS, width)
    }
    if set(subsets.values()) != expected or len(subsets) != 127:
        raise MaterializerError("all exact 2^7-1 subspaces were not enumerated")
    previous = math.inf
    ranked = []
    for index, row in enumerate(ranking, 1):
        score = finite(row.get("candidate_score"), "candidate score")
        if (
            row.get("rank") != index
            or row.get("subset_id") not in subsets
            or score > previous
        ):
            raise MaterializerError("global candidate ranking differs")
        previous = score
        ranked.append(row["subset_id"])
    if len(set(ranked)) != 127:
        raise MaterializerError("global ranking is not a permutation")
    for rank, journal in enumerate(journals, 1):
        if (
            journal.get("rank") != rank
            or journal.get("subset_id") != ranked[rank - 1]
            or not journal.get("sealed_candidate_identity")
            or not journal.get("attempt_token")
            or journal.get("started") is not True
            or journal.get("measurement_complete") is not True
            or journal.get("completion_recorded") is not True
            or journal.get("real_replay") is not True
            or journal.get("failed_replay") is not False
            or journal.get("database_counter_timeout_count") != 0
            or journal.get("accepted_rounds_credit") != 0
        ):
            raise MaterializerError("fixed Top-5 point journal differs")
    for row in fingerprints:
        if not _fingerprint_complete(row):
            raise MaterializerError("XP L1/L2/L3 fingerprint is incomplete")


def materialize_debug(
    bundle, adoption_path, trace_path, gate_path, r0_path, raw_debug_path
):
    bundle_id = verify_bundle(bundle)["bundle_id"]
    unused_adoption, identity = verify_adoption(adoption_path, bundle_id)
    trace = load_sealed(trace_path, "trace registry")
    gate = load_sealed(gate_path, "dynamic gate")
    r0 = load_sealed(r0_path, "fresh R0")
    raw = load_sealed(raw_debug_path, "raw debug round")
    _verify_gate(gate, bundle_id, identity)
    if (
        trace.get("same_live_identity") != identity
        or r0.get("schema_version") != "mysql_xp_fresh_r0_result_v3"
        or r0.get("status") != "R0_PASS"
        or r0.get("same_live_identity") != identity
        or r0.get("trace_registry_sha256") != sha256(trace_path)
        or r0.get("dynamic_gate_sha256") != sha256(gate_path)
        or raw.get("fresh_r0_sha256") != sha256(r0_path)
        or raw.get("dynamic_gate_sha256") != sha256(gate_path)
    ):
        raise MaterializerError("debug predecessor chain differs")
    verify_debug(raw, bundle_id, identity)
    result = dict(raw)
    result.update(
        {
            "schema_version":
                "mysql_xp_algorithm_c_debug_round_sealed_v3",
            "status": "PASS_DEBUG_ROUND_SEALED",
            "raw_debug_round_sha256": sha256(raw_debug_path),
            "trace_registry_sha256": sha256(trace_path),
            "formal10_handoff_authorized": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
        }
    )
    return result


def materialize_formal10(
    bundle, adoption_path, trace_path, gate_path, r0_path, debug_path
):
    manifest = verify_bundle(bundle)
    bundle_id = manifest["bundle_id"]
    collector_sha256 = manifest["collector_sha256"]
    adoption, identity = verify_adoption(adoption_path, bundle_id)
    trace = load_sealed(trace_path, "trace registry")
    gate = load_sealed(gate_path, "dynamic gate")
    r0 = load_sealed(r0_path, "fresh R0")
    debug = load_sealed(debug_path, "sealed debug round")
    _verify_gate(gate, bundle_id, identity)
    if (
        trace.get("same_live_identity") != identity
        or r0.get("same_live_identity") != identity
        or debug.get("schema_version")
        != "mysql_xp_algorithm_c_debug_round_sealed_v3"
        or debug.get("status") != "PASS_DEBUG_ROUND_SEALED"
        or debug.get("same_live_identity") != identity
        or debug.get("raw_debug_round_sha256") in (None, "")
        or debug.get("formal10_handoff_authorized") is not True
        or debug.get("accepted_rounds_credit") != 0
    ):
        raise MaterializerError("formal10 predecessor chain differs")
    return {
        "schema_version":
            "mysql_xp_node02_same_live_formal10_handoff_v1",
        "status": "READY_FORMAL10_SAME_LIVE_DB_AND_CLIENT",
        "scenario": "hybench_xp_io",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "bundle_id": bundle_id,
        "same_live_identity": identity,
        "adopted_service_sha256": sha256(adoption_path),
        "trace_registry_sha256": sha256(trace_path),
        "dynamic_gate_sha256": sha256(gate_path),
        "fresh_r0_sha256": sha256(r0_path),
        "debug_round_sha256": sha256(debug_path),
        "runtime_publication_sha256":
            adoption["runtime_publication"]["publication_sha256"],
        "collector_sha256": collector_sha256,
        "new_database_or_client_container_prohibited": True,
        "new_campaign_planner_prohibited": True,
        "ongoing_rolling_gate_required": True,
        "fixed_top5_debug_complete": True,
        "historical_accepted_round_credit": 0,
        "target_accepted_rounds": 10,
        "next_accepted_round": 1,
        "formal10_authorized": True,
        "accepted_round_authorized": False,
        "capture_r0_debug_or_failed_replay_countable_as_accepted": False,
        "main_agent_launch_decision_required": True,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("trace", "gate", "fresh-r0", "debug", "formal10"),
        required=True,
    )
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--trace-registry", type=Path)
    parser.add_argument("--raw-dynamic-gate", type=Path)
    parser.add_argument("--dynamic-gate", type=Path)
    parser.add_argument("--qualification", type=Path)
    parser.add_argument("--fresh-r0", type=Path)
    parser.add_argument("--raw-debug", type=Path)
    parser.add_argument("--debug-round", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.stage == "trace":
        if args.capture is None:
            raise MaterializerError("trace stage requires capture")
        result = materialize_trace(
            args.bundle, args.adopted_service, args.capture
        )
    elif args.stage == "gate":
        if args.raw_dynamic_gate is None:
            raise MaterializerError("gate stage requires raw dynamic gate")
        result = materialize_dynamic_gate(
            args.bundle, args.adopted_service, args.raw_dynamic_gate
        )
    elif args.stage == "fresh-r0":
        if None in (
            args.trace_registry, args.dynamic_gate, args.qualification
        ):
            raise MaterializerError("fresh-r0 evidence is incomplete")
        result = materialize_fresh_r0(
            args.bundle, args.adopted_service, args.trace_registry,
            args.dynamic_gate, args.qualification,
        )
    elif args.stage == "debug":
        if None in (
            args.trace_registry, args.dynamic_gate, args.fresh_r0,
            args.raw_debug,
        ):
            raise MaterializerError("debug evidence is incomplete")
        result = materialize_debug(
            args.bundle, args.adopted_service, args.trace_registry,
            args.dynamic_gate, args.fresh_r0, args.raw_debug,
        )
    else:
        if None in (
            args.trace_registry, args.dynamic_gate, args.fresh_r0,
            args.debug_round,
        ):
            raise MaterializerError("formal10 evidence is incomplete")
        result = materialize_formal10(
            args.bundle, args.adopted_service, args.trace_registry,
            args.dynamic_gate, args.fresh_r0, args.debug_round,
        )
    atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
