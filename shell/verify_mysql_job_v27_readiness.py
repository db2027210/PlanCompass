"""Fail-closed verifier for the JOB-only v27 successor readiness document."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


EXPECTED_MANIFEST = "/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json"
EXPECTED_TEMPLATE = "/disk1/ra_mysql_isolated_v2/job-base"
EXPECTED_DEVICE = "/dev/nvme1n1"
EXPECTED_MAJOR_MINOR = "259:5"
EXPECTED_COPY_MODE = "full_physical_copy_no_overlay_no_reflink"
FORBIDDEN_EXECUTION_VALUES = (
    "/mnt/mysql_nvme1n1/",
    "/mnt/mysql_nvme0n1p1",
    "/mnt/mysql_tpcc_nvme3n1",
    "/dev/nvme0n1p1",
    "/dev/nvme3n1",
)


@dataclass(frozen=True)
class Check:
    requirement: str
    item: str
    passed: bool
    reason: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden(key) or _contains_forbidden(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        return any(token in value for token in FORBIDDEN_EXECUTION_VALUES)
    return False


def _pointer_ok(pointer: Any) -> bool:
    if not isinstance(pointer, Mapping):
        return False
    path = Path(str(pointer.get("path", "")))
    expected = str(pointer.get("sha256", "")).lower()
    return (
        len(expected) == 64
        and path.is_file()
        and not path.is_symlink()
        and _sha256(path) == expected
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
        and document.get("cross_engine_comparison_prohibited") is True,
        "mysql_only and cross-engine prohibition are mandatory",
    )
    add(
        "AUTH",
        "launch",
        document.get("status") == "READY"
        and document.get("launch_authorized") is True
        and document.get("execution_authorized_scenarios") == ["job_memory"],
        "only JOB may be execution-authorized",
    )
    scenarios = document.get("scenarios")
    add(
        "SCHEMA",
        "scenario_cardinality",
        isinstance(scenarios, Mapping) and set(scenarios) == {"job_memory"},
        "executable scenarios must contain JOB only",
    )
    execution = document.get("execution_authority")
    expected_manifest_sha = (
        _sha256(Path(EXPECTED_MANIFEST))
        if Path(EXPECTED_MANIFEST).is_file()
        else ""
    )
    expected_storage = {
        "schema_version": "mysql_job_execution_storage_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "job_memory",
        "host_id": "compute00",
        "template_path": EXPECTED_TEMPLATE,
        "manifest_path": EXPECTED_MANIFEST,
        "manifest_sha256": expected_manifest_sha,
        "profile_id": "job_local_storage_v2_nvme_fullcopy",
        "device": EXPECTED_DEVICE,
        "device_major_minor": EXPECTED_MAJOR_MINOR,
        "filesystem": "ext4",
        "fresh_copy_mode": EXPECTED_COPY_MODE,
        "overlay": False,
        "reflink": False,
    }
    storage = (
        execution.get("storage")
        if isinstance(execution, Mapping)
        else None
    )
    add(
        "R03",
        "single_storage_authority",
        isinstance(storage, Mapping) and dict(storage) == expected_storage,
        "local /disk1 259:5 authority must match exactly",
    )
    add(
        "R04",
        "no_phantom_execution_device",
        isinstance(execution, Mapping) and not _contains_forbidden(execution),
        "remote and phantom devices are forbidden from execution authority",
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
        "v26 CPU+device first-clean-sample barrier is mandatory",
    )
    add(
        "R01",
        "copy_mode",
        isinstance(execution, Mapping)
        and execution.get("fresh_full_physical_copy_required") is True,
        "full physical copy is mandatory",
    )
    provenance = document.get("provenance_only")
    legacy = (
        provenance.get("legacy_remote_scenarios")
        if isinstance(provenance, Mapping)
        else None
    )
    add(
        "R09",
        "legacy_provenance_non_executable",
        isinstance(provenance, Mapping)
        and provenance.get("authority_role") == "provenance_only"
        and provenance.get("execution_authorized") is False
        and provenance.get("may_participate_in_launch") is False
        and isinstance(legacy, Mapping),
        "legacy remote scenario evidence must be explicitly non-executable",
    )
    method = document.get("method_bundle")
    add(
        "R07",
        "method_manifest",
        isinstance(method, Mapping)
        and _pointer_ok(method.get("method_manifest")),
        "method manifest must be present and checksum-closed",
    )
    add(
        "R08",
        "config",
        isinstance(method, Mapping) and _pointer_ok(method.get("config")),
        "execution config must be present and checksum-closed",
    )
    add(
        "R11",
        "storage_manifest",
        expected_manifest_sha != "",
        "local storage manifest must exist",
    )
    add(
        "R06",
        "method_contract",
        isinstance(method, Mapping)
        and _pointer_ok(method.get("method_manifest"))
        and _pointer_ok(method.get("config")),
        "checksummed method and config must jointly bind the JOB method",
    )
    add(
        "R10",
        "dynamic_non_interference",
        isinstance(execution, Mapping)
        and execution.get("runtime_interference_gate_revision") == 2
        and execution.get("pre_session_cpu_device_first_clean_sample")
        is True,
        "dynamic CPU and device non-interference gate is mandatory",
    )
    return checks


def verify_ready(document: Mapping[str, Any]) -> None:
    failed = [item for item in verify_document(document) if not item.passed]
    if failed:
        detail = "; ".join(f"{item.item}: {item.reason}" for item in failed)
        raise RuntimeError(f"JOB v27 readiness rejected: {detail}")


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
            {"item": item.item, "reason": item.reason} for item in failed
        ],
    }, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
