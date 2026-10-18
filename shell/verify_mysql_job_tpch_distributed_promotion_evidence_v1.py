#!/usr/bin/env python3
"""Offline verifier for JOB/TPC-H distributed promotion evidence.

The verifier reads only local, sealed evidence.  It never opens a network
connection or mutates a process/container.  Debug qualification requires a
fresh live readback plus a same-attempt static isolation gate.  Fresh-R0
promotion additionally requires the same-attempt 180-second bidirectional
dynamic interference gate.  Neither mode grants formal10 authority.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

try:
    from . import runtime_tree_content_identity_v3 as runtime_tree
except ImportError:  # Direct ``python shell/<script>.py`` execution.
    import runtime_tree_content_identity_v3 as runtime_tree


ROOT = Path(__file__).resolve().parents[1]
PYTHON_MANIFEST = (
    ROOT / "preflight/runtime_manifests_v3_20260729/"
    "node04_python311_v3-"
    "0db72f3e4e14b3902ff94d17eca9e5c8af97577efdf6ab8b3941fbe189da8c33.json"
)
JDK_MANIFEST = (
    ROOT / "preflight/runtime_manifests_v3_20260729/"
    "node04_jdk23_v3-"
    "bf3b427e9510ac77fe6618a60f99f51e1562884d3e81f0cec3cd9f47bfa4852e.json"
)
PYTHON_MANIFEST_SHA256 = (
    "0db72f3e4e14b3902ff94d17eca9e5c8af97577efdf6ab8b3941fbe189da8c33"
)
PYTHON_TREE_SHA256 = (
    "1d53a2c90b5a6cac15ffa5e1631c6ac04729accd271f6cd2a05e6b162dd9f9f7"
)
PYTHON_TREE_CONTENT_SHA256 = (
    "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
)
PYTHON_EXECUTABLE_SHA256 = (
    "05bd6d6d900bd990b8c00d17df3b3e1425d6c32f0408f07cd29bc495ac4aff39"
)
JDK_MANIFEST_SHA256 = (
    "bf3b427e9510ac77fe6618a60f99f51e1562884d3e81f0cec3cd9f47bfa4852e"
)
JDK_TREE_SHA256 = (
    "5d6558a8c933daa4bbf412d18f90a4526c57988c9632a2114365e23b55df7139"
)
JDK_TREE_CONTENT_SHA256 = (
    "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
)
JAVA_EXECUTABLE_SHA256 = (
    "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
)
MAX_EVIDENCE_AGE_SECONDS = 900
MAX_FUTURE_SKEW_SECONDS = 30
STATIC_CHECKS = {
    "logical_cpuset_intersection_is_empty",
    "physical_core_sibling_intersection_is_empty",
    "resolved_device_major_minor_is_distinct",
    "mount_backing_block_device_is_distinct",
    "mysql_port_is_distinct",
    "template_runtime_output_and_cgroup_paths_are_distinct",
    "memory_swap_and_pids_capacity_is_feasible",
    "co_resident_process_inventory_is_complete",
}
DYNAMIC_DIRECTIONS = {
    "A_solo_vs_A_while_B_runs",
    "B_solo_vs_B_while_A_runs",
}
DYNAMIC_STREAMS = {
    "workload_throughput_or_completion_rate",
    "p50_p95_p99_or_query_duration",
    "cpu_usage_and_throttle_ratio",
    "cpu_pressure",
    "memory_current_swap_and_pressure",
    "per_declared_device_bytes_iops_latency_queue_and_utilization",
    "database_counters",
    "process_cpu_affinity_and_device_inventory",
}
EXPECTED = {
    "job": {
        "scenario": "job_memory",
        "peer_scenario": "hybench_xp_io",
        "host_id": "node02",
        "ssh_target": "anonymous@192.0.2.4",
        "address": "192.0.2.4",
        "project": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job",
        "mount": "/mnt/mysql_nvme1n1",
        "template":
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
        "device": "/dev/nvme1n1",
        "major_minor": "259:3",
        "filesystem": "xfs",
        "cpuset": "18-20,54-56",
        "mems": "1",
        "planner_port": 65102,
        "replay_port": 65202,
        "python_root": (
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
            "runtime_algorithm_c_successor_20260729/runtime/"
            f"python311-{PYTHON_TREE_CONTENT_SHA256}"
        ),
        "python_interpreter": (
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/"
            "runtime_algorithm_c_successor_20260729/runtime/"
            f"python311-{PYTHON_TREE_CONTENT_SHA256}/bin/python"
        ),
        "jdk_required": False,
        "envelope": {
            "cpu_quota_cores": 1.15,
            "memory_bytes": 4294967296,
            "memory_swap_bytes": 4294967296,
            "pids_max": 512,
            "io_read_bps": 7340032,
            "io_write_bps": 7340032,
            "buffer_pool_bytes": 3221225472,
        },
    },
    "tpch": {
        "scenario": "tpch_memory",
        "peer_scenario": "tpcc_cpu",
        "host_id": "node04",
        "ssh_target": "anonymous@192.0.2.7",
        "address": "192.0.2.7",
        "project": "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch",
        "mount": "/mnt/nvme0n1",
        "template": "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template",
        "device": "/dev/nvme0n1",
        "major_minor": "259:1",
        "filesystem": "xfs",
        "cpuset": "18-20,54-56",
        "mems": "1",
        "planner_port": 65103,
        "replay_port": 65203,
        "python_root": "/home/anonymous/recost_runtime_py311_24_11_1",
        "python_interpreter":
            "/home/anonymous/recost_runtime_py311_24_11_1/bin/python",
        "jdk_required": True,
        "jdk_root": (
            "/home/anonymous/recost_deploy_20260721_v1/runtime/"
            "jdk-23.0.2-oracle-x64"
        ),
        "java_path": (
            "/home/anonymous/recost_deploy_20260721_v1/runtime/"
            "jdk-23.0.2-oracle-x64/bin/java"
        ),
        "envelope": {
            "cpu_quota_cores": 4,
            "memory_bytes": 5583454208,
            "memory_swap_bytes": 5583454208,
            "pids_max": 512,
            "io_read_bps": 100663296,
            "io_write_bps": 100663296,
            "buffer_pool_bytes": 4294967296,
        },
    },
}


class EvidenceError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value, sort_keys=True, indent=2, ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path, label: str) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise EvidenceError(f"{label} absent or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{label} invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    return value


def _entry(
    manifest: Mapping[str, Any], relative: str
) -> Mapping[str, Any]:
    entries = (manifest.get("inventory") or {}).get("entries")
    if not isinstance(entries, list):
        raise EvidenceError("runtime inventory entries absent")
    matches = [
        entry for entry in entries
        if isinstance(entry, Mapping) and entry.get("path") == relative
    ]
    if len(matches) != 1:
        raise EvidenceError(f"runtime entry cardinality differs: {relative}")
    return matches[0]


def verify_runtime_manifests() -> dict[str, Any]:
    python = load(PYTHON_MANIFEST, "Python runtime manifest")
    jdk = load(JDK_MANIFEST, "JDK runtime manifest")
    checks = (
        (
            python,
            PYTHON_MANIFEST,
            PYTHON_MANIFEST_SHA256,
            "node04_python311_v3",
            "/home/anonymous/recost_runtime_py311_24_11_1",
            PYTHON_TREE_SHA256,
        ),
        (
            jdk,
            JDK_MANIFEST,
            JDK_MANIFEST_SHA256,
            "node04_jdk23_v3",
            (
                "/home/anonymous/recost_deploy_20260721_v1/runtime/"
                "jdk-23.0.2-oracle-x64"
            ),
            JDK_TREE_SHA256,
        ),
    )
    portable_results = {}
    for document, path, digest, label, root, tree in checks:
        inventory = document.get("inventory") or {}
        if (
            sha256(path) != digest
            or document.get("schema_version")
            != "mysql_algorithm_c_remote_runtime_manifest_v1"
            or document.get("formal_scope") != "mysql_only"
            or document.get("cross_engine_comparison_prohibited") is not True
            or document.get("launch_authorized") is not False
            or document.get("remote_mutation_performed") is not False
            or document.get("host") != "anonymous@192.0.2.7"
            or document.get("label") != label
            or document.get("remote_root") != root
            or document.get("tree_sha256") != tree
            or inventory.get("root") != root
            or inventory.get("schema_version")
            != "remote_runtime_tree_inventory_v2"
            or hashlib.sha256(canonical(inventory)).hexdigest() != tree
            or not isinstance(inventory.get("file_count"), int)
            or inventory.get("file_count", 0) <= 0
        ):
            raise EvidenceError(f"{label} runtime manifest differs")
        try:
            portable_results[label] = runtime_tree.validate_publication(document)
        except runtime_tree.RuntimeTreeError as exc:
            raise EvidenceError(
                f"{label} portable runtime identity invalid: {exc}"
            ) from exc
    if (
        portable_results["node04_python311_v3"]["tree_content_sha256"]
        != PYTHON_TREE_CONTENT_SHA256
        or portable_results["node04_jdk23_v3"]["tree_content_sha256"]
        != JDK_TREE_CONTENT_SHA256
    ):
        raise EvidenceError("portable runtime content identity differs")
    if (
        _entry(python, "bin/python3.11").get("sha256")
        != PYTHON_EXECUTABLE_SHA256
        or _entry(python, "bin/python").get("target") != "python3.11"
        or _entry(jdk, "bin/java").get("sha256")
        != JAVA_EXECUTABLE_SHA256
    ):
        raise EvidenceError("runtime executable identity differs")
    return {
        "python_manifest_sha256": PYTHON_MANIFEST_SHA256,
        "python_tree_sha256": PYTHON_TREE_SHA256,
        "python_tree_sha256_semantics":
            "legacy_location_bound_inventory_digest",
        "python_tree_content_sha256": PYTHON_TREE_CONTENT_SHA256,
        "python_executable_sha256": PYTHON_EXECUTABLE_SHA256,
        "jdk_manifest_sha256": JDK_MANIFEST_SHA256,
        "jdk_tree_sha256": JDK_TREE_SHA256,
        "jdk_tree_sha256_semantics":
            "legacy_location_bound_inventory_digest",
        "jdk_tree_content_sha256": JDK_TREE_CONTENT_SHA256,
        "java_executable_sha256": JAVA_EXECUTABLE_SHA256,
    }


def _exact_runtime_tree_readback(
    row: Mapping[str, Any],
    *,
    root: str,
    legacy_tree_sha256: str,
    content_tree_sha256: str,
    authority_manifest_sha256: str,
) -> bool:
    """Require portable exact-tree proof; the legacy ID alone is insufficient."""
    return (
        row.get("root") == root
        and row.get("tree_sha256") == legacy_tree_sha256
        and row.get("tree_sha256_semantics")
        == "authority_legacy_location_bound_inventory_digest"
        and row.get("tree_content_sha256") == content_tree_sha256
        and row.get("tree_content_identity_algorithm")
        == "sha256_canonical_relative_tree_entries_v3"
        and row.get("authority_manifest_sha256")
        == authority_manifest_sha256
        and row.get("exact_entry_set_verified") is True
        and row.get("directory_entry_set_verified") is True
        and row.get("file_sha256_size_mode_verified") is True
        and row.get("symlink_target_verified") is True
        and row.get("special_entries_rejected") is True
    )


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceError(f"{label} timestamp absent")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError(f"{label} timestamp invalid") from exc
    if parsed.tzinfo is None:
        raise EvidenceError(f"{label} timestamp lacks timezone")
    return parsed.astimezone(timezone.utc)


def _verify_fresh(
    document: Mapping[str, Any], now: datetime, label: str
) -> None:
    captured = _timestamp(document.get("captured_at_utc"), label)
    now = now.astimezone(timezone.utc)
    age = (now - captured).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS or age > MAX_EVIDENCE_AGE_SECONDS:
        raise EvidenceError(f"{label} is stale or from the future")
    expires = _timestamp(document.get("expires_at_utc"), label)
    if expires <= now or (expires - captured).total_seconds() > MAX_EVIDENCE_AGE_SECONDS:
        raise EvidenceError(f"{label} expiry window differs")


def _verify_raw(
    document_path: Path, document: Mapping[str, Any], label: str
) -> None:
    raw = document.get("raw_artifacts")
    if not isinstance(raw, Mapping) or not raw:
        raise EvidenceError(f"{label} raw artifacts absent")
    base = document_path.resolve().parent
    for relative, digest in raw.items():
        if (
            not isinstance(relative, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", relative)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise EvidenceError(f"{label} raw artifact pointer invalid")
        path = (base / relative).resolve()
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise EvidenceError(f"{label} raw artifact escapes evidence root") from exc
        if not path.is_file() or path.is_symlink() or sha256(path) != digest:
            raise EvidenceError(f"{label} raw artifact differs: {relative}")


def _verify_common(
    path: Path,
    document: Mapping[str, Any],
    scenario: str,
    attempt: str,
    now: datetime,
    schema: str,
    label: str,
) -> None:
    if (
        document.get("schema_version") != schema
        or document.get("status") != "PASS"
        or document.get("scenario") != EXPECTED[scenario]["scenario"]
        or document.get("attempt_token") != attempt
        or document.get("formal_scope") != "mysql_only"
        or document.get("cross_engine_comparison_prohibited") is not True
        or document.get("accepted_round_authorized") is not False
        or document.get("formal10_authorized") is not False
    ):
        raise EvidenceError(f"{label} identity/scope differs")
    _verify_fresh(document, now, label)
    _verify_raw(path, document, label)


def validate_dynamic_gate_document(
    dynamic: Mapping[str, Any], expected: Mapping[str, Any]
) -> None:
    """Validate the one canonical four-scenario dynamic-gate payload."""
    if (
        dynamic.get("policy_version")
        != "mysql_algorithm_c_same_host_bidirectional_interference_gate_v1"
        or dynamic.get("peer_scenario") != expected["peer_scenario"]
        or dynamic.get("host_id") != expected["host_id"]
        or dynamic.get("duration_seconds") != 180
        or dynamic.get("sample_period_seconds") != 1
        or dynamic.get("minimum_samples_per_stream", 0) < 181
        or set(dynamic.get("directions_passed") or [])
        != DYNAMIC_DIRECTIONS
        or set(dynamic.get("streams_complete") or [])
        != DYNAMIC_STREAMS
        or dynamic.get("database_counter_missing_samples") != 0
        or dynamic.get("database_counter_timeout_count") != 0
        or dynamic.get("all_tolerances_passed") is not True
        or dynamic.get("ongoing_monitor_required") is not True
        or dynamic.get("authorization_reuse") != "PROHIBITED_ACROSS_ATTEMPTS"
        or dynamic.get("qualification_capture_r0_formal_accounting")
        != "PROHIBITED"
    ):
        raise EvidenceError("dynamic interference gate differs")


def verify_promotion_evidence(
    *,
    scenario: str,
    mode: str,
    live_readback_path: Path,
    static_gate_path: Path,
    dynamic_gate_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if scenario not in EXPECTED:
        raise EvidenceError("scenario must be job or tpch")
    if mode not in {"debug_qualification", "fresh_r0"}:
        raise EvidenceError("mode must be debug_qualification or fresh_r0")
    now = now or datetime.now(timezone.utc)
    runtime = verify_runtime_manifests()
    live_path = live_readback_path.resolve()
    static_path = static_gate_path.resolve()
    live = load(live_path, "live readback")
    static = load(static_path, "static gate")
    attempt = live.get("attempt_token")
    if not isinstance(attempt, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_.-]{15,127}", attempt
    ):
        raise EvidenceError("live readback attempt token invalid")
    _verify_common(
        live_path, live, scenario, attempt, now,
        "mysql_algorithm_c_distributed_live_readback_evidence_v1",
        "live readback",
    )
    _verify_common(
        static_path, static, scenario, attempt, now,
        "mysql_algorithm_c_distributed_static_gate_evidence_v1",
        "static gate",
    )
    expected = EXPECTED[scenario]
    host = live.get("host") or {}
    storage = live.get("storage") or {}
    resource = live.get("resource") or {}
    mysql = live.get("mysql") or {}
    python = (live.get("runtime") or {}).get("python") or {}
    jdk = (live.get("runtime") or {}).get("jdk") or {}
    inventory = live.get("co_resident_inventory") or {}
    if {
        "host_id": host.get("host_id"),
        "ssh_target": host.get("ssh_target"),
        "address": host.get("address"),
    } != {
        "host_id": expected["host_id"],
        "ssh_target": expected["ssh_target"],
        "address": expected["address"],
    }:
        raise EvidenceError("live host identity differs")
    if {
        "project_path": storage.get("project_path"),
        "mount_path": storage.get("mount_path"),
        "template_path": storage.get("template_path"),
        "device": storage.get("device"),
        "device_major_minor": storage.get("device_major_minor"),
    } != {
        "project_path": expected["project"],
        "mount_path": expected["mount"],
        "template_path": expected["template"],
        "device": expected["device"],
        "device_major_minor": expected["major_minor"],
    }:
        raise EvidenceError("live storage identity differs")
    if (
        storage.get("mount_source_verified") is not True
        or storage.get("filesystem") != expected["filesystem"]
        or storage.get("template_tree_verified") is not True
        or storage.get("exact_row_counts_verified") is not True
        or storage.get("clean_shutdown") is not True
        or storage.get("no_live_template_owner") is not True
        or storage.get("full_physical_copy_no_reflink") is not True
    ):
        raise EvidenceError("live storage gate incomplete")
    expected_resource = {
        "cpuset_cpus": expected["cpuset"],
        "cpuset_mems": expected["mems"],
        **expected["envelope"],
    }
    if any(resource.get(key) != value for key, value in expected_resource.items()):
        raise EvidenceError("live resource envelope differs")
    if (
        resource.get("physical_core_siblings_verified") is not True
        or resource.get("numa_distance_verified") is not True
        or resource.get("live_cgroup_readback") is not True
    ):
        raise EvidenceError("live resource readback incomplete")
    if (
        mysql.get("planner_host_port") != expected["planner_port"]
        or mysql.get("replay_host_port") != expected["replay_port"]
        or mysql.get("container_port") != 3306
        or mysql.get("process_affinity") != expected["cpuset"]
        or mysql.get("buffer_pool_bytes")
        != expected["envelope"]["buffer_pool_bytes"]
        or mysql.get("fixed_settings_read_back") is not True
        or mysql.get("database_counter_timeout_policy") != "FAIL"
    ):
        raise EvidenceError("live MySQL readback differs")
    if (
        not _exact_runtime_tree_readback(
            python,
            root=expected["python_root"],
            legacy_tree_sha256=PYTHON_TREE_SHA256,
            content_tree_sha256=PYTHON_TREE_CONTENT_SHA256,
            authority_manifest_sha256=PYTHON_MANIFEST_SHA256,
        )
        or python.get("interpreter_path") != expected["python_interpreter"]
        or python.get("interpreter_sha256") != PYTHON_EXECUTABLE_SHA256
        or python.get("version") != "3.11.11"
        or python.get("live_tree_and_interpreter_readback") is not True
    ):
        raise EvidenceError("live Python runtime differs")
    if expected["jdk_required"] and (
        not _exact_runtime_tree_readback(
            jdk,
            root=expected["jdk_root"],
            legacy_tree_sha256=JDK_TREE_SHA256,
            content_tree_sha256=JDK_TREE_CONTENT_SHA256,
            authority_manifest_sha256=JDK_MANIFEST_SHA256,
        )
        or jdk.get("java_path") != expected["java_path"]
        or jdk.get("java_sha256") != JAVA_EXECUTABLE_SHA256
        or jdk.get("live_tree_and_java_readback") is not True
    ):
        raise EvidenceError("live JDK runtime differs")
    if (
        inventory.get("complete") is not True
        or inventory.get("postgresql_inventoried_and_untouched") is not True
        or inventory.get("database_and_benchmark_processes_complete") is not True
        or inventory.get("ports_free_before_launch") is not True
        or live.get("collected_by_main_agent") is not True
        or live.get("remote_mutation_performed") is not False
    ):
        raise EvidenceError("live co-resident inventory/read-only boundary differs")
    if (
        static.get("peer_scenario") != expected["peer_scenario"]
        or static.get("host_id") != expected["host_id"]
        or set((static.get("checks") or {}).keys()) != STATIC_CHECKS
        or any(value is not True for value in (static.get("checks") or {}).values())
        or static.get("co_resident_inventory_sha256")
        != sha256(live_path)
    ):
        raise EvidenceError("static isolation gate differs")
    dynamic = None
    if mode == "debug_qualification":
        if dynamic_gate_path is not None:
            raise EvidenceError("debug qualification must not consume dynamic gate")
    else:
        if dynamic_gate_path is None:
            raise EvidenceError("fresh R0 requires dynamic gate")
        dynamic_path = dynamic_gate_path.resolve()
        dynamic = load(dynamic_path, "dynamic gate")
        _verify_common(
            dynamic_path, dynamic, scenario, attempt, now,
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
            "dynamic gate",
        )
        validate_dynamic_gate_document(dynamic, expected)
    return {
        "schema_version":
            "mysql_job_tpch_distributed_promotion_evidence_result_v1",
        "status": "PASS",
        "scenario": expected["scenario"],
        "mode": mode,
        "attempt_token": attempt,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "debug_qualification_launch_authorized": True,
        "fresh_r0_launch_authorized": mode == "fresh_r0",
        "formal10_authorized": False,
        "accepted_round_authorized": False,
        "runtime_manifests": runtime,
        "evidence": {
            "live_readback": {
                "path": str(live_path), "sha256": sha256(live_path)
            },
            "static_gate": {
                "path": str(static_path), "sha256": sha256(static_path)
            },
            "dynamic_gate": (
                None if dynamic_gate_path is None else {
                    "path": str(dynamic_gate_path.resolve()),
                    "sha256": sha256(dynamic_gate_path.resolve()),
                }
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("job", "tpch"), required=True)
    parser.add_argument(
        "--mode", choices=("debug_qualification", "fresh_r0"), required=True
    )
    parser.add_argument("--live-readback", type=Path, required=True)
    parser.add_argument("--static-gate", type=Path, required=True)
    parser.add_argument("--dynamic-gate", type=Path)
    args = parser.parse_args()
    try:
        result = verify_promotion_evidence(
            scenario=args.scenario,
            mode=args.mode,
            live_readback_path=args.live_readback,
            static_gate_path=args.static_gate,
            dynamic_gate_path=args.dynamic_gate,
        )
    except (EvidenceError, OSError, ValueError) as exc:
        print(json.dumps({
            "status": "BLOCKED",
            "remote_commands_executed": False,
            "formal10_authorized": False,
            "accepted_round_authorized": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
