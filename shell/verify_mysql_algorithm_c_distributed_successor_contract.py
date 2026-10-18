#!/usr/bin/env python3
"""Offline verifier for the distributed MySQL Algorithm C successor contract.

This verifier deliberately performs no SSH, process, container, cgroup, or
device mutations.  Live read-back remains a separate, fail-closed main-agent
launch prerequisite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = (
    REPO_ROOT
    / "preflight/mysql_algorithm_c_distributed_four_scene_successor_contract_v2_20260729.json"
)
SUPERSEDED_CONTRACT = (
    REPO_ROOT
    / "preflight/mysql_algorithm_c_distributed_four_scene_successor_contract_20260729.json"
)
SUPERSEDED_CONTRACT_SHA256 = (
    "9b812fd55274512694d1e87f1f5347a35fea0f05d76c24ce4e99c00ffd314ac4"
)

EXPECTED_PLACEMENT = {
    "job": {
        "scenario": "job_memory",
        "hostname": "node02",
        "address": "192.0.2.4",
        "device": "/dev/nvme1n1",
        "mount": "/mnt/mysql_nvme1n1",
        "template_path": "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
        "cpuset_cpus": "18-20,54-56",
        "mysql_port": 65102,
        "expected_live_major_minor": "259:3",
    },
    "xp": {
        "scenario": "hybench_xp_io",
        "hostname": "node02",
        "address": "192.0.2.4",
        "device": "/dev/nvme0n1p1",
        "mount": "/mnt/mysql_nvme0n1p1",
        "template_path": "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/template_v2",
        "cpuset_cpus": "21-33,57-69",
        "mysql_port": 65104,
        "expected_live_major_minor": "259:4",
    },
    "tpch": {
        "scenario": "tpch_memory",
        "hostname": "node04",
        "address": "192.0.2.7",
        "device": "/dev/nvme0n1",
        "mount": "/mnt/nvme0n1",
        "template_path": "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template",
        "cpuset_cpus": "18-20,54-56",
        "mysql_port": 65103,
        "expected_live_major_minor": "259:1",
    },
    "tpcc": {
        "scenario": "tpcc_cpu",
        "hostname": "node04",
        "address": "192.0.2.7",
        "device": "/dev/nvme3n1",
        "mount": "/mnt/mysql_tpcc_nvme3n1",
        "template_path": "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/template",
        "cpuset_cpus": "21-24,57-60",
        "mysql_port": 65106,
        "expected_live_major_minor": "259:2",
    },
}

EXPECTED_ENVELOPES = {
    "job": {
        "cpu_quota_cores": 1.15,
        "memory_bytes": 4294967296,
        "memory_swap_bytes": 4294967296,
        "pids_max": 512,
        "io_read_bps": 7340032,
        "io_write_bps": 7340032,
        "innodb_buffer_pool_bytes": 3221225472,
    },
    "xp": {
        "cpu_quota_cores": 5.3,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592,
        "pids_max": 1024,
        "io_read_bps": 29360128,
        "io_write_bps": 524288,
        "innodb_buffer_pool_bytes": 4294967296,
    },
    "tpch": {
        "cpu_quota_cores": 4,
        "memory_bytes": 5583454208,
        "memory_swap_bytes": 5583454208,
        "pids_max": 512,
        "io_read_bps": 100663296,
        "io_write_bps": 100663296,
        "innodb_buffer_pool_bytes": 4294967296,
    },
    "tpcc": {
        "cpu_quota_cores": 4,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592,
        "pids_max": 512,
        "io_read_bps": 1205862400,
        "io_write_bps": 1205862400,
        "innodb_buffer_pool_bytes": 4294967296,
    },
}

EXPECTED_CLIENT_ENVELOPES = {
    "job": {
        "cpuset_cpus": "0-2,36-38",
        "cpuset_mems": "0",
        "cpu_quota_cores": 6,
        "memory_bytes": 2147483648,
        "memory_swap_bytes": 2147483648,
        "pids_max": 256,
        "tmpfs_bytes": 1073741824,
    },
    "xp": {
        "cpuset_cpus": "3-11,39-47",
        "cpuset_mems": "0",
        "cpu_quota_cores": 18,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592,
        "pids_max": 1024,
        "tmpfs_bytes": 2147483648,
    },
    "tpch": {
        "cpuset_cpus": "0-7,36-43",
        "cpuset_mems": "0",
        "cpu_quota_cores": 16,
        "memory_bytes": 4294967296,
        "memory_swap_bytes": 4294967296,
        "pids_max": 512,
        "tmpfs_bytes": 1073741824,
    },
    "tpcc": {
        "cpuset_cpus": "8-15,44-51",
        "cpuset_mems": "0",
        "cpu_quota_cores": 16,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592,
        "pids_max": 1024,
        "tmpfs_bytes": 2147483648,
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expand_cpuset(spec: str) -> set[int]:
    result: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            raise ValueError(f"empty cpuset token in {spec!r}")
        if "-" in token:
            first_text, last_text = token.split("-", 1)
            first, last = int(first_text), int(last_text)
            if first > last:
                raise ValueError(f"descending cpuset range {token!r}")
            result.update(range(first, last + 1))
        else:
            result.add(int(token))
    if not result:
        raise ValueError("cpuset is empty")
    return result


def verify_contract(contract_path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    errors: list[str] = []
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    require(
        contract.get("schema_version")
        == "mysql_algorithm_c_distributed_four_scene_successor_contract_v2",
        "unexpected schema_version",
    )
    superseded = contract.get("supersedes_contract", {})
    require(
        superseded.get("path")
        == str(SUPERSEDED_CONTRACT.relative_to(REPO_ROOT)),
        "superseded contract path differs",
    )
    require(
        superseded.get("sha256") == SUPERSEDED_CONTRACT_SHA256
        and _sha256(SUPERSEDED_CONTRACT) == SUPERSEDED_CONTRACT_SHA256,
        "superseded contract identity differs",
    )
    require(contract.get("formal_scope") == "mysql_only", "scope must be mysql_only")
    require(
        contract.get("cross_engine_comparison_prohibited") is True,
        "cross-engine comparison must be prohibited",
    )
    require(
        contract.get("paired_cross_engine_result_merge_prohibited") is True,
        "paired-scope result merge must be prohibited",
    )
    require(
        contract.get("postgresql_mutation_prohibited") is True,
        "PostgreSQL mutation prohibition missing",
    )
    require(
        contract.get("data_plane_launch_authority") == "main_agent_only",
        "data-plane authority must remain main_agent_only",
    )
    require(
        contract.get("formal_launch_authorized") is False,
        "offline contract must not authorize formal launch",
    )

    scenarios = contract.get("scenarios", {})
    require(set(scenarios) == set(EXPECTED_PLACEMENT), "scenario set must be exact")
    for name, expected in EXPECTED_PLACEMENT.items():
        scene = scenarios.get(name, {})
        host = scene.get("host", {})
        storage = scene.get("storage", {})
        envelope = scene.get("resource_envelope", {})
        client = scene.get("client_resource_envelope", {})
        evidence = scene.get("template_evidence", {})
        actual_placement = {
            "scenario": scene.get("scenario"),
            "hostname": host.get("hostname"),
            "address": host.get("address"),
            "device": storage.get("device"),
            "mount": storage.get("mount"),
            "template_path": storage.get("template_path"),
            "cpuset_cpus": envelope.get("cpuset_cpus"),
            "mysql_port": envelope.get("mysql_port"),
            "expected_live_major_minor": storage.get("expected_live_major_minor"),
        }
        require(actual_placement == expected, f"{name}: placement mismatch")
        for key, value in EXPECTED_ENVELOPES[name].items():
            require(envelope.get(key) == value, f"{name}: {key} mismatch")
        for key, value in EXPECTED_CLIENT_ENVELOPES[name].items():
            require(
                client.get(key) == value,
                f"{name}: client {key} mismatch",
            )
        require(
            client.get("execution_mode")
            == "dedicated_read_only_docker_client_cgroup",
            f"{name}: dedicated client cgroup missing",
        )
        for key in (
            "root_filesystem_read_only",
            "no_new_privileges",
            "runtime_and_bundle_mounts_read_only",
            "target_mysql_device_write_prohibited",
            "pid_and_all_descendant_affinity_readback_required",
        ):
            require(client.get(key) is True, f"{name}: client {key} missing")
        require(
            client.get("linux_capabilities_drop") == "ALL",
            f"{name}: client capabilities must all be dropped",
        )
        require(
            client.get("docker_log_driver") == "none",
            f"{name}: client Docker logging must be disabled",
        )
        require(
            client.get("measurement_output_medium") == "tmpfs",
            f"{name}: client output must be tmpfs",
        )
        require(envelope.get("cpuset_mems") == "1", f"{name}: mems must be 1")
        require(
            storage.get("full_physical_copy_required") is True,
            f"{name}: full physical copy is required",
        )
        require(
            storage.get("overlay_or_reflink_prohibited") is True,
            f"{name}: overlay/reflink must be prohibited",
        )
        require(
            storage.get("runtime_root", "").startswith(storage.get("mount", "") + "/"),
            f"{name}: runtime root must reside on declared mount",
        )
        require(scene.get("fresh_r0_required") is True, f"{name}: fresh R0 missing")
        require(
            scene.get("formal_launch_authorized") is False,
            f"{name}: scene must remain unauthorized",
        )

        evidence_path = REPO_ROOT / str(evidence.get("path", ""))
        require(evidence_path.is_file(), f"{name}: template evidence missing")
        if evidence_path.is_file():
            require(
                _sha256(evidence_path) == evidence.get("sha256"),
                f"{name}: template evidence hash mismatch",
            )
        predecessor = REPO_ROOT / str(scene.get("resource_predecessor_bundle", ""))
        require(predecessor.is_dir(), f"{name}: predecessor bundle missing")

    for host, pair in {
        "node02": ("job", "xp"),
        "node04": ("tpch", "tpcc"),
    }.items():
        left, right = (scenarios.get(name, {}) for name in pair)
        left_storage, right_storage = left.get("storage", {}), right.get("storage", {})
        left_env, right_env = (
            left.get("resource_envelope", {}),
            right.get("resource_envelope", {}),
        )
        try:
            cpus_disjoint = _expand_cpuset(left_env.get("cpuset_cpus", "")) .isdisjoint(
                _expand_cpuset(right_env.get("cpuset_cpus", ""))
            )
            client_cpus_disjoint = _expand_cpuset(
                left.get("client_resource_envelope", {}).get(
                    "cpuset_cpus", ""
                )
            ).isdisjoint(
                _expand_cpuset(
                    right.get("client_resource_envelope", {}).get(
                        "cpuset_cpus", ""
                    )
                )
            )
            every_lane = (
                _expand_cpuset(left_env.get("cpuset_cpus", "")),
                _expand_cpuset(right_env.get("cpuset_cpus", "")),
                _expand_cpuset(
                    left.get("client_resource_envelope", {}).get(
                        "cpuset_cpus", ""
                    )
                ),
                _expand_cpuset(
                    right.get("client_resource_envelope", {}).get(
                        "cpuset_cpus", ""
                    )
                ),
            )
            every_lane_disjoint = all(
                every_lane[first].isdisjoint(every_lane[second])
                for first in range(len(every_lane))
                for second in range(first + 1, len(every_lane))
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"{host}: invalid cpuset: {exc}")
            cpus_disjoint = False
            client_cpus_disjoint = False
            every_lane_disjoint = False
        require(cpus_disjoint, f"{host}: logical CPU sets overlap")
        require(
            client_cpus_disjoint,
            f"{host}: client logical CPU sets overlap",
        )
        require(
            every_lane_disjoint,
            f"{host}: database/client CPU lanes overlap",
        )
        require(
            left_storage.get("device") != right_storage.get("device"),
            f"{host}: devices overlap",
        )
        require(
            left_storage.get("mount") != right_storage.get("mount"),
            f"{host}: mounts overlap",
        )
        require(
            left_storage.get("expected_live_major_minor")
            != right_storage.get("expected_live_major_minor"),
            f"{host}: expected live major:minor overlaps",
        )
        require(
            left_env.get("mysql_port") != right_env.get("mysql_port"),
            f"{host}: ports overlap",
        )

    policy = contract.get("same_host_parallel_policy", {})
    static = policy.get("static_gate", {})
    dynamic = policy.get("dynamic_bidirectional_gate", {})
    require(
        policy.get("parallel_decision")
        == "ALLOW_CONCURRENT_CAPTURE_R0_OR_DEBUG_ONLY_IF_STATIC_AND_180S_BIDIRECTIONAL_GATES_PASS",
        "parallel decision is not fail-closed",
    )
    require(
        policy.get("fallback_decision")
        == "SERIALIZE_PER_HOST_IF_ANY_GATE_IS_MISSING_FAILED_STALE_OR_INCONCLUSIVE",
        "serial fallback is missing",
    )
    require(static.get("all_required") is True, "all static checks must be required")
    require(dynamic.get("duration_seconds") == 180, "dynamic gate must be 180 seconds")
    require(dynamic.get("minimum_samples_per_stream", 0) >= 181, "too few samples")
    require(
        dynamic.get("database_counter_timeout_or_missing_sample_result") == "FAIL",
        "missing dynamic samples/counters must fail closed",
    )
    require(
        set(dynamic.get("directions_required", []))
        == {"A_solo_vs_A_while_B_runs", "B_solo_vs_B_while_A_runs"},
        "both interference directions are required",
    )
    require(
        dynamic.get("authorization_reuse") == "PROHIBITED_ACROSS_ATTEMPTS",
        "dynamic qualification must not be reused",
    )
    require(
        dynamic.get("qualification_runs_are_debug_only") is True,
        "qualification runs must be debug-only",
    )
    require(
        dynamic.get("qualification_data_plane_authority") == "main_agent_only",
        "qualification data-plane authority must remain main-agent-only",
    )
    require(
        dynamic.get("qualification_capture_r0_and_formal_prohibited") is True,
        "qualification must prohibit capture, R0, and formal accounting",
    )
    rolling = dynamic.get("rolling_process_and_device_attribution", {})
    for field in (
        "sample_every_second",
        "all_host_database_and_benchmark_processes_required",
        "all_docker_database_and_benchmark_containers_required",
        "process_pid_start_time_cmdline_cpu_affinity_and_cgroup_required",
        "client_pid_and_all_descendants_required",
        "external_cgroup_io_on_declared_mysql_major_minor_required",
        "external_open_file_or_mount_attribution_on_declared_mysql_device_required",
    ):
        require(rolling.get(field) is True, f"rolling gate: {field} missing")
    require(
        rolling.get(
            "missing_permission_timeout_pid_churn_or_incomplete_attribution_result"
        )
        == "FAIL",
        "rolling gate must fail closed on incomplete attribution",
    )
    require(
        rolling.get("foreign_writer_bytes_max") == 0,
        "rolling gate must reject foreign writer bytes",
    )
    exception = contract.get("live_readback_gate", {}).get(
        "qualification_data_plane_exception", {}
    )
    require(
        exception.get("authorization") == "main_agent_only",
        "qualification exception authority must remain main-agent-only",
    )
    for field in (
        "capture_prohibited",
        "r0_prohibited",
        "formal_prohibited",
        "accepted_round_accounting_prohibited",
    ):
        require(exception.get(field) is True, f"qualification exception: {field} missing")

    algorithm = contract.get("algorithm_invariants", {})
    require(
        algorithm.get("gradient_formula")
        == "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
        "original gradient formula changed",
    )
    require(algorithm.get("fixed_top_k_replay") == 5, "Top-5 replay must be fixed")
    require(
        algorithm.get("enumerate_all_nonempty_subspaces") is True,
        "full nonempty subspace enumeration missing",
    )

    result = {
        "ok": not errors,
        "contract": str(contract_path),
        "contract_sha256": _sha256(contract_path),
        "scenario_count": len(scenarios),
        "errors": errors,
        "remote_actions": 0,
        "formal_launch_authorized": False,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    args = parser.parse_args()
    result = verify_contract(args.contract.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
