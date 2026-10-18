"""Self-contained fail-closed verifier for TPC-H v35/v27 readiness."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


EXPECTED_SERVICE = "shell.mysql_tpch_algorithm_c_local_services_v27:build"
EXPECTED_MANIFEST = "/disk2/ra_mysql_isolated_v2/tpch-storage-v2.manifest.json"
EXPECTED_COPY_MODE = "full_physical_copy_no_overlay_no_reflink"
EXPECTED_PROFILE = {
    "cpuset": "200-207,456-463",
    "mems": "1",
    "cpu_cores": 4,
    "memory_bytes": 5583454208,
    "pids": 512,
    "read_bps": 100663296,
    "write_bps": 100663296,
    "buffer_pool_bytes": 4294967296,
}
EXPECTED_RESOURCE = {
    "schema_version": "mysql_tpch_execution_resource_authority_v1",
    "authority_role": "sole_execution_authority",
    "execution_authorized": True,
    "scenario": "tpch_memory",
    "host_id": "compute00",
    "cpuset_cpus": "200-207,456-463",
    "cpuset_mems": "1",
    "cpu_quota_cores": 4,
    "memory_bytes": 5583454208,
    "memory_swap_bytes": 5583454208,
    "io_read_bps": 100663296,
    "io_write_bps": 100663296,
    "pids_max": 512,
    "buffer_pool_bytes": 4294967296,
    "storage_device": "/dev/nvme2n1",
    "storage_device_major_minor": "259:6",
}
EXPECTED_STORAGE_BASE = {
    "schema_version": "mysql_tpch_execution_storage_authority_v1",
    "authority_role": "sole_execution_authority",
    "execution_authorized": True,
    "scenario": "tpch_memory",
    "host_id": "compute00",
    "template_path": "/disk2/ra_mysql_isolated_v2/tpch-memory",
    "manifest_path": EXPECTED_MANIFEST,
    "profile_id": "tpch_local_storage_v2_nvme_fullcopy",
    "device": "/dev/nvme2n1",
    "device_major_minor": "259:6",
    "filesystem": "ext4",
    "fresh_copy_mode": EXPECTED_COPY_MODE,
    "overlay": False,
    "reflink": False,
}
FORBIDDEN_EXECUTION_VALUES = (
    "5583457485",
    "/dev/nvme0n1p1",
    "/dev/nvme1n1",
    "/dev/nvme3n1",
    "/dev/sda",
    "/mnt/mysql_",
)


@dataclass(frozen=True)
class Check:
    requirement: str
    item: str
    passed: bool
    reason: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pointer(pointer: Any) -> tuple[bool, Path]:
    if not isinstance(pointer, Mapping):
        return False, Path("")
    path = Path(str(pointer.get("path") or ""))
    expected = str(pointer.get("sha256") or "").lower()
    return (
        path.is_file()
        and not path.is_symlink()
        and len(expected) == 64
        and _sha256(path) == expected,
        path,
    )


def verify_document(
    document: Mapping[str, Any], _root: Path | None = None,
) -> list[Check]:
    checks: list[Check] = []

    def add(
        requirement: str, item: str, passed: bool, reason: str,
    ) -> None:
        checks.append(Check(requirement, item, bool(passed), reason))

    add(
        "R02",
        "scope",
        document.get("formal_scope") == "mysql_only"
        and document.get("cross_engine_comparison_prohibited") is True
        and document.get("paired_result_merge_prohibited") is True,
        "mysql_only and cross-engine result prohibition are mandatory",
    )
    add(
        "AUTH",
        "launch",
        document.get("status") == "READY"
        and document.get("launch_authorized") is True
        and document.get("execution_authorized_scenarios")
        == ["tpch_memory"],
        "only local TPC-H may be execution-authorized",
    )
    scenarios = document.get("scenarios")
    add(
        "SCHEMA",
        "scenario_cardinality",
        isinstance(scenarios, Mapping) and set(scenarios) == {"tpch_memory"},
        "executable scenario map must contain only TPC-H",
    )
    execution = document.get("execution_authority")
    storage = (
        execution.get("storage")
        if isinstance(execution, Mapping)
        else None
    )
    resource = (
        execution.get("resource")
        if isinstance(execution, Mapping)
        else None
    )
    manifest_path = Path(EXPECTED_MANIFEST)
    manifest_sha = _sha256(manifest_path) if manifest_path.is_file() else ""
    expected_storage = {
        **EXPECTED_STORAGE_BASE,
        "manifest_sha256": manifest_sha,
    }
    add(
        "R03",
        "storage_authority",
        isinstance(storage, Mapping) and dict(storage) == expected_storage,
        "TPC-H storage must be exactly /disk2 259:6 full-copy",
    )
    add(
        "R01",
        "resource_authority",
        isinstance(resource, Mapping)
        and {
            key: value
            for key, value in resource.items()
            if key not in {"profile_path", "profile_sha256"}
        }
        == EXPECTED_RESOURCE,
        "CPU, memory, IO, pids and device must match exactly",
    )
    encoded = json.dumps(execution, sort_keys=True, separators=(",", ":"))
    add(
        "R04",
        "no_phantom_execution_authority",
        isinstance(execution, Mapping)
        and not any(token in encoded for token in FORBIDDEN_EXECUTION_VALUES),
        "old memory value, remote path and phantom device are forbidden",
    )
    add(
        "R05",
        "pre_session_gate",
        isinstance(execution, Mapping)
        and execution.get("runtime_interference_gate_revision") == 2
        and execution.get("pre_session_cpu_device_first_clean_sample")
        is True
        and execution.get("workload_start_requires_runtime_gate_ready")
        is True,
        "v26 CPU+device first-clean-sample gate is mandatory",
    )
    method = document.get("method_bundle")
    method_ok, _ = _pointer(
        method.get("method_manifest") if isinstance(method, Mapping) else None
    )
    config_ok, config_path = _pointer(
        method.get("config") if isinstance(method, Mapping) else None
    )
    add("R07", "method_manifest", method_ok, "method manifest must be sealed")
    add("R08", "config_pointer", config_ok, "config must be sealed")

    config: Mapping[str, Any] = {}
    if config_ok:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping):
            config = loaded
    add(
        "R06",
        "config_authority_binding",
        bool(config)
        and "data_template" not in config
        and config.get("execution_storage_authority") == storage
        and config.get("execution_resource_authority") == resource
        and (config.get("production") or {}).get("service_factory")
        == EXPECTED_SERVICE
        and (config.get("workload") or {}).get("host_control_cpuset")
        == EXPECTED_RESOURCE["cpuset_cpus"],
        "sealed config must mirror both sole authorities",
    )
    profile_path = (
        Path(str(resource.get("profile_path") or ""))
        if isinstance(resource, Mapping)
        else Path("")
    )
    profile_ok = (
        profile_path.is_absolute()
        and profile_path.is_file()
        and not profile_path.is_symlink()
        and _sha256(profile_path)
        == str(resource.get("profile_sha256") or "").lower()
        and str(config.get("profile") or "") == str(profile_path)
        and json.loads(profile_path.read_text(encoding="utf-8"))
        == EXPECTED_PROFILE
    )
    add(
        "AUTH",
        "profile_binding",
        profile_ok,
        "profile must exactly freeze 5,583,454,208 bytes and all fields",
    )
    provenance = document.get("provenance_only")
    add(
        "R09",
        "legacy_provenance_non_executable",
        isinstance(provenance, Mapping)
        and provenance.get("authority_role") == "provenance_only"
        and provenance.get("execution_authorized") is False
        and provenance.get("may_participate_in_launch") is False
        and isinstance(provenance.get("legacy_scenarios"), Mapping),
        "old global and remote readiness may only be provenance",
    )
    scenario = (
        scenarios.get("tpch_memory")
        if isinstance(scenarios, Mapping)
        else None
    )
    add(
        "R11",
        "readiness_mirrors",
        isinstance(scenario, Mapping)
        and scenario.get("resource_envelope") == resource
        and scenario.get("storage") == storage,
        "TPC-H readiness mirrors must exactly equal execution authority",
    )
    add(
        "R10",
        "dynamic_non_interference",
        isinstance(execution, Mapping)
        and execution.get("pre_session_cpu_device_first_clean_sample")
        is True,
        "dynamic CPU and device non-interference is mandatory",
    )
    return checks


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text(encoding="utf-8"))
    checks = verify_document(document)
    failed = [item for item in checks if not item.passed]
    print(json.dumps({
        "passed": not failed,
        "check_count": len(checks),
        "failed": [
            {
                "requirement": item.requirement,
                "item": item.item,
                "reason": item.reason,
            }
            for item in failed
        ],
    }, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
