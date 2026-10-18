"""Fail-closed readiness verifier for the JOB v28 unified resource authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from shell.mysql_algorithm_c_local_services_v28 import (
    EXPECTED_CPUSET,
    EXPECTED_MEMS,
    EXPECTED_PROFILE,
    EXPECTED_RESOURCE,
    EXPECTED_SERVICE,
)
from shell.verify_mysql_job_v27_readiness import Check
from shell.verify_mysql_job_v27_readiness import (
    verify_document as verify_v27_document,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_document(
    document: Mapping[str, Any], _root: Path | None = None,
) -> list[Check]:
    checks = list(verify_v27_document(document, _root))

    def add(
        requirement: str, item: str, passed: bool, reason: str,
    ) -> None:
        checks.append(Check(requirement, item, bool(passed), reason))

    execution = document.get("execution_authority")
    resource = (
        execution.get("resource")
        if isinstance(execution, Mapping)
        else None
    )
    comparable = (
        {
            key: value
            for key, value in resource.items()
            if key not in {"profile_path", "profile_sha256"}
        }
        if isinstance(resource, Mapping)
        else None
    )
    add(
        "R01",
        "single_resource_authority",
        comparable == EXPECTED_RESOURCE,
        "CPU, memory, IO, pids and device authority must match v28 exactly",
    )
    add(
        "R05",
        "cpu_isolation_authority",
        isinstance(resource, Mapping)
        and resource.get("cpuset_cpus") == EXPECTED_CPUSET
        and resource.get("cpuset_mems") == EXPECTED_MEMS
        and "18-20,54-56" not in json.dumps(execution, sort_keys=True),
        "only CPUs 176-183,432-439 and mems 1 may enter execution authority",
    )

    method = document.get("method_bundle")
    config_pointer = (
        method.get("config") if isinstance(method, Mapping) else None
    )
    config_path = (
        Path(str(config_pointer.get("path") or ""))
        if isinstance(config_pointer, Mapping)
        else Path("")
    )
    config: Mapping[str, Any] = {}
    config_ok = False
    if (
        isinstance(config_pointer, Mapping)
        and config_path.is_file()
        and not config_path.is_symlink()
        and _sha256(config_path)
        == str(config_pointer.get("sha256") or "").lower()
    ):
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, Mapping):
            config = loaded
            config_ok = True
    add(
        "R08",
        "config_resource_binding",
        config_ok
        and config.get("execution_resource_authority") == resource
        and (config.get("workload") or {}).get("host_control_cpuset")
        == EXPECTED_CPUSET
        and (config.get("production") or {}).get("service_factory")
        == EXPECTED_SERVICE,
        "config must mirror the sole v28 resource authority and factory",
    )

    profile_path = (
        Path(str(resource.get("profile_path") or ""))
        if isinstance(resource, Mapping)
        else Path("")
    )
    profile_sha = (
        str(resource.get("profile_sha256") or "").lower()
        if isinstance(resource, Mapping)
        else ""
    )
    profile_ok = False
    if (
        profile_path.is_absolute()
        and profile_path.is_file()
        and not profile_path.is_symlink()
        and _sha256(profile_path) == profile_sha
        and str(config.get("profile") or "") == str(profile_path)
    ):
        profile_ok = (
            json.loads(profile_path.read_text(encoding="utf-8"))
            == EXPECTED_PROFILE
        )
    add(
        "AUTH",
        "profile_resource_binding",
        profile_ok,
        "profile must be checksum-bound and exactly match the sole authority",
    )
    scenario = (
        (document.get("scenarios") or {}).get("job_memory")
        if isinstance(document.get("scenarios"), Mapping)
        else None
    )
    add(
        "R11",
        "readiness_resource_mirror",
        isinstance(scenario, Mapping)
        and scenario.get("resource_envelope") == resource,
        "JOB readiness resource mirror must equal execution authority",
    )
    return checks


def verify_ready(document: Mapping[str, Any]) -> None:
    failed = [item for item in verify_document(document) if not item.passed]
    if failed:
        detail = "; ".join(f"{item.item}: {item.reason}" for item in failed)
        raise RuntimeError(f"JOB v28 readiness rejected: {detail}")


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
