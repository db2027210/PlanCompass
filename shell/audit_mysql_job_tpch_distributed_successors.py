#!/usr/bin/env python3
"""Pure-local audit for JOB/TPC-H distributed-layout successors.

This program performs no SSH, socket, Docker, VM, or database operation.  It
validates the current immutable predecessors and the frozen transition
contract, then prints the minimum successor change set.  A PASS is explicitly
not launch authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (
    ROOT
    / "preflight/mysql_job_tpch_distributed_successor_contract_20260729.json"
)
FORMAL10_CONTRACT = (
    ROOT / "preflight/mysql_algorithm_c_formal10_contract_20260729.json"
)
BUNDLES = {
    "job": (
        ROOT / "artifacts/mysql_job_v30_debug"
        / "b888870bc6ef136932c26676c797823d2b99858cf6b2b3891f7afe1ef42d3190"
    ),
    "tpch": (
        ROOT / "artifacts/mysql_tpch_v36_debug"
        / "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4"
    ),
}
EXPECTED_LAYOUT = {
    "job": {
        "scenario": "job_memory",
        "host_id": "node02",
        "ssh_target": "anonymous@192.0.2.4",
        "remote_project_path":
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job",
        "remote_template_path":
            "/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/template",
        "storage_device": "/dev/nvme1n1",
        "storage_device_major_minor": "259:3",
        "cpuset_cpus": "18-20,54-56",
        "cpuset_mems": "1",
        "planner_port": 65102,
        "replay_port": 65202,
        "template_manifest_path": str(
            ROOT
            / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft"
            / "evidence/job_template_manifest_20260723.json"
        ),
        "template_manifest_sha256":
            "ce4fc5f5396904b82e4ae185f4f2a3d006f2cca502ac34cbc731e48f0a73acce",
    },
    "tpch": {
        "scenario": "tpch_memory",
        "host_id": "node04",
        "ssh_target": "anonymous@192.0.2.7",
        "remote_project_path":
            "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch",
        "remote_template_path":
            "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/template",
        "storage_device": "/dev/nvme0n1",
        "storage_device_major_minor": "259:1",
        "cpuset_cpus": "18-20,54-56",
        "cpuset_mems": "1",
        "planner_port": 65103,
        "replay_port": 65203,
        "template_manifest_path": str(
            ROOT
            / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft"
            / "evidence/tpch_template_manifest_20260723.json"
        ),
        "template_manifest_sha256":
            "87fccd293fc6fafc4a5ff0bbfd12a4e5fdd359511c83e747e09adf77d1ed1427",
    },
}
PRESERVED_RESOURCE_FIELDS = (
    "cpu_quota_cores",
    "memory_bytes",
    "memory_swap_bytes",
    "io_read_bps",
    "io_write_bps",
    "pids_max",
    "buffer_pool_bytes",
)
REQUIRED_SOURCE_CHANGES = [
    "freeze distributed LANES host/hostname/project/template/device/cpuset/mems/planner_port",
    "bind a new versioned distributed service factory and reject local service factories",
    "replace template copy --reflink=auto with --reflink=never",
    "replace host02-only pressure probe with same-host CPU/device/port/process dynamic interference gate",
    "require fresh remote cgroup, Docker inspect, NUMA, device and buffer-pool readback before workload start",
    "retain one-second elapsed sampling v2 and database-counter fail-closed behavior",
]


class AuditError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise AuditError(f"{label} is absent, relative, or symlinked: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"{label} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"{label} root must be an object")
    return value


def verify_bundle(name: str, expected: Mapping[str, Any]) -> dict[str, Any]:
    bundle = BUNDLES[name]
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path, f"{name} predecessor manifest")
    if (
        bundle.name != expected.get("bundle_id")
        or manifest.get("bundle_id") != expected.get("bundle_id")
        or sha256(manifest_path) != expected.get("bundle_manifest_sha256")
        or manifest.get("source_bundle_id") != expected.get("source_bundle_id")
        or manifest.get("source_manifest_sha256")
        != expected.get("source_manifest_sha256")
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise AuditError(f"{name} predecessor identity/scope differs")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise AuditError(f"{name} predecessor file closure is absent")
    for relative, digest in files.items():
        path = bundle / str(relative)
        if not path.is_file() or path.is_symlink() or sha256(path) != digest:
            raise AuditError(f"{name} predecessor file differs: {relative}")
    source = Path(str(manifest.get("source_directory") or ""))
    source_manifest_path = source / "source_manifest.json"
    source_manifest = load_object(
        source_manifest_path, f"{name} source manifest"
    )
    if (
        source_manifest.get("bundle_id") != expected.get("source_bundle_id")
        or sha256(source_manifest_path)
        != expected.get("source_manifest_sha256")
    ):
        raise AuditError(f"{name} source identity differs")
    source_files = source_manifest.get("files")
    if not isinstance(source_files, Mapping) or not source_files:
        raise AuditError(f"{name} source closure is absent")
    for relative, digest in source_files.items():
        path = source / str(relative)
        if not path.is_file() or path.is_symlink() or sha256(path) != digest:
            raise AuditError(f"{name} source file differs: {relative}")
    return manifest


def verify_template_evidence(name: str, layout: Mapping[str, Any]) -> None:
    path = Path(str(layout["template_manifest_path"]))
    evidence = load_object(path, f"{name} historical template evidence")
    expected_host = layout["ssh_target"].split("@", 1)[1]
    if (
        sha256(path) != layout["template_manifest_sha256"]
        or evidence.get("scenario") != layout["scenario"]
        or evidence.get("host") != expected_host
        or evidence.get("device") != layout["storage_device"]
        or evidence.get("template_path") != layout["remote_template_path"]
        or evidence.get("ready") is not True
        or evidence.get("frozen_read_only") is not True
        or (
            evidence.get("clean_shutdown") is not True
            if name == "job"
            else evidence.get("clean_build_exit_code") != 0
        )
    ):
        raise AuditError(f"{name} historical template evidence differs")


def verify_historical_layout_probe(
    name: str, pointer: Mapping[str, Any], layout: Mapping[str, Any],
) -> None:
    path = Path(str(pointer.get("path") or ""))
    if (
        pointer.get("evidence_only_not_reusable_readback") is not True
        or sha256(path) != pointer.get("sha256")
    ):
        raise AuditError(f"{name} historical layout evidence differs")
    text = path.read_text(encoding="utf-8")
    tokens = (
        layout["ssh_target"],
        f"test \"$(hostname)\" = {layout['host_id']}",
        layout["remote_project_path"],
        layout["storage_device_major_minor"],
        layout["cpuset_cpus"],
    )
    if any(token not in text for token in tokens):
        raise AuditError(
            f"{name} historical layout evidence lacks an exact binding"
        )


def validate_contract(
    contract: Mapping[str, Any],
    manifests: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    problems: list[str] = []
    if (
        contract.get("schema_version")
        != "mysql_job_tpch_distributed_successor_audit_contract_v1"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("audit_only") is not True
        or contract.get("remote_data_plane_authorized") is not False
        or contract.get("accepted_round_authorized") is not False
        or contract.get("historical_r0_or_readback_reusable") is not False
    ):
        problems.append("audit/scope/counting boundary differs")
    if contract.get("layout") != EXPECTED_LAYOUT:
        problems.append("distributed layout differs")
    if contract.get("required_source_changes") != REQUIRED_SOURCE_CHANGES:
        problems.append("minimum source change set differs")
    envelope = contract.get("envelope_policy")
    if (
        not isinstance(envelope, Mapping)
        or envelope.get("mode")
        != "preserve_current_v30_v36_calibrated_limits_change_only_placement"
    ):
        problems.append("envelope transition policy differs")
    else:
        for name in ("job", "tpch"):
            predecessor = (
                manifests[name].get("execution_resource_authority") or {}
            )
            expected = {
                field: predecessor.get(field)
                for field in PRESERVED_RESOURCE_FIELDS
            }
            if envelope.get(name) != expected:
                problems.append(
                    f"{name} changes calibrated limits instead of placement"
                )
    layouts = contract.get("layout")
    if isinstance(layouts, Mapping):
        ports: set[tuple[str, int]] = set()
        for name in ("job", "tpch"):
            row = layouts.get(name)
            if not isinstance(row, Mapping):
                continue
            if row.get("replay_port") != row.get("planner_port", 0) + 100:
                problems.append(f"{name} replay port is not planner+100")
            key = (str(row.get("host_id")), int(row.get("planner_port", 0)))
            replay = (str(row.get("host_id")), int(row.get("replay_port", 0)))
            if key in ports or replay in ports:
                problems.append(f"{name} port authority collides")
            ports.update((key, replay))
            project = str(row.get("remote_project_path") or "")
            template = str(row.get("remote_template_path") or "")
            if not template.startswith(project.rstrip("/") + "/"):
                problems.append(f"{name} template escapes remote project")
    shortcuts = contract.get("prohibited_shortcuts")
    required_shortcuts = {
        "reuse local compute00 readiness or readback",
        "reuse historical distributed envelope or R0 as current authority",
        "retain local service factory",
        "retain --reflink=auto",
        "treat template-only historical evidence as launch readiness",
    }
    if not isinstance(shortcuts, list) or not required_shortcuts <= set(
        shortcuts
    ):
        problems.append("fail-closed shortcut prohibitions are incomplete")
    fresh = contract.get("fresh_remote_authorization_inputs")
    if not isinstance(fresh, list) or len(fresh) != 10:
        problems.append("fresh remote authorization gates are incomplete")
    return problems


def audit(contract_path: Path = CONTRACT) -> dict[str, Any]:
    contract = load_object(contract_path.resolve(), "distributed contract")
    if (
        sha256(FORMAL10_CONTRACT)
        != contract.get("formal10_contract_sha256")
    ):
        raise AuditError("formal10 contract checksum differs")
    predecessors = contract.get("predecessors")
    if not isinstance(predecessors, Mapping):
        raise AuditError("predecessor authority is absent")
    manifests = {
        name: verify_bundle(name, predecessors.get(name) or {})
        for name in ("job", "tpch")
    }
    problems = validate_contract(contract, manifests)
    layouts = contract.get("layout") or {}
    historical = contract.get("historical_layout_evidence") or {}
    for name in ("job", "tpch"):
        verify_template_evidence(name, layouts[name])
        verify_historical_layout_probe(
            name, historical.get(name) or {}, layouts[name]
        )
    if problems:
        raise AuditError("; ".join(problems))
    return {
        "schema_version":
            "mysql_job_tpch_distributed_successor_audit_result_v1",
        "status": "AUDIT_PASS_STAGED_NOT_LAUNCH_AUTHORIZED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "audit_only": True,
        "remote_commands_executed": False,
        "remote_data_plane_authorized": False,
        "accepted_round_authorized": False,
        "historical_r0_or_readback_reusable": False,
        "contract_path": str(contract_path.resolve()),
        "contract_sha256": sha256(contract_path.resolve()),
        "predecessor_bundle_ids": {
            name: manifests[name]["bundle_id"]
            for name in ("job", "tpch")
        },
        "layout": layouts,
        "preserved_envelopes": contract["envelope_policy"],
        "minimum_source_changes": contract["required_source_changes"],
        "minimum_config_changes": contract["required_config_changes"],
        "minimum_bundle_changes": contract["required_bundle_changes"],
        "remaining_fresh_remote_authorization_inputs":
            contract["fresh_remote_authorization_inputs"],
        "next_safe_artifact":
            "content-addressed staged successor with launch_authorized=false "
            "until fresh remote authorization inputs are sealed",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=CONTRACT)
    args = parser.parse_args()
    try:
        print(json.dumps(
            audit(args.contract), ensure_ascii=False, indent=2, sort_keys=True
        ))
        return 0
    except AuditError as exc:
        print(json.dumps({
            "status": "AUDIT_BLOCKED",
            "audit_only": True,
            "remote_commands_executed": False,
            "remote_data_plane_authorized": False,
            "accepted_round_authorized": False,
            "reason": str(exc),
        }, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
