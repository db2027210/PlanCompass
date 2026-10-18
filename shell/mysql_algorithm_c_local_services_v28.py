"""JOB v28 binding with one storage authority and one CPU/resource authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from shell.mysql_algorithm_c_local_services_v25 import LOCAL_LANES
from shell.mysql_algorithm_c_local_services_v26 import (
    LocalCampaign,
    LocalProductionServices,
)
from shell.mysql_algorithm_c_local_services_v27 import (
    StorageAuthorityError,
    validate_storage_authority,
)


EXPECTED_SERVICE = "shell.mysql_algorithm_c_local_services_v28:build"
EXPECTED_CPUSET = "176-183,432-439"
EXPECTED_MEMS = "1"
EXPECTED_PROFILE = {
    "cpuset": EXPECTED_CPUSET,
    "mems": EXPECTED_MEMS,
    "cpu_cores": 1.15,
    "memory_bytes": 4294967296,
    "pids": 512,
    "read_bps": 7340032,
    "write_bps": 7340032,
    "buffer_pool_bytes": 3221225472,
}
EXPECTED_RESOURCE = {
    "schema_version": "mysql_job_execution_resource_authority_v1",
    "authority_role": "sole_execution_authority",
    "execution_authorized": True,
    "scenario": "job_memory",
    "host_id": "compute00",
    "cpuset_cpus": EXPECTED_CPUSET,
    "cpuset_mems": EXPECTED_MEMS,
    "cpu_quota_cores": 1.15,
    "memory_bytes": 4294967296,
    "memory_swap_bytes": 4294967296,
    "io_read_bps": 7340032,
    "io_write_bps": 7340032,
    "pids_max": 512,
    "buffer_pool_bytes": 3221225472,
    "storage_device": "/dev/nvme1n1",
    "storage_device_major_minor": "259:5",
}
FORBIDDEN_CPUSET = "18-20,54-56"


class ResourceAuthorityError(StorageAuthorityError):
    """Raised before service construction when resource authorities diverge."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_resource_authority(config: Mapping[str, Any]) -> None:
    authority = config.get("execution_resource_authority")
    if not isinstance(authority, Mapping):
        raise ResourceAuthorityError("execution_resource_authority is required")
    comparable = {
        key: value
        for key, value in authority.items()
        if key not in {"profile_path", "profile_sha256"}
    }
    if comparable != EXPECTED_RESOURCE:
        raise ResourceAuthorityError("execution resource authority differs")
    if FORBIDDEN_CPUSET in json.dumps(authority, sort_keys=True):
        raise ResourceAuthorityError("legacy profile cpuset entered execution authority")

    profile_path = Path(str(authority.get("profile_path") or ""))
    profile_sha = str(authority.get("profile_sha256") or "").lower()
    if (
        not profile_path.is_absolute()
        or not profile_path.is_file()
        or profile_path.is_symlink()
        or _sha256(profile_path) != profile_sha
        or str(config.get("profile") or "") != str(profile_path)
    ):
        raise ResourceAuthorityError("profile pointer or digest differs")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    if profile != EXPECTED_PROFILE:
        raise ResourceAuthorityError("profile is a second or divergent CPU authority")

    workload = config.get("workload")
    if (
        not isinstance(workload, Mapping)
        or workload.get("host_control_cpuset") != EXPECTED_CPUSET
    ):
        raise ResourceAuthorityError("workload CPU mirror differs")
    policy = config.get("resource_authority_policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("executable_authority_count") != 1
        or policy.get("second_cpu_authority_action") != "FAIL_CLOSED"
    ):
        raise ResourceAuthorityError("single resource authority policy is absent")

    lane = LOCAL_LANES.get("job")
    if (
        lane is None
        or lane.cpuset != EXPECTED_CPUSET
        or lane.mems != EXPECTED_MEMS
        or lane.device != "/dev/nvme1n1"
    ):
        raise ResourceAuthorityError("compiled JOB lane differs")


def validate_authorities(config: Mapping[str, Any]) -> None:
    validate_storage_authority(config, expected_service=EXPECTED_SERVICE)
    validate_resource_authority(config)


def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_authorities(config)
    return LocalProductionServices(config).bundle()


__all__ = [
    "LocalCampaign",
    "LocalProductionServices",
    "ResourceAuthorityError",
    "StorageAuthorityError",
    "build",
    "validate_authorities",
    "validate_resource_authority",
]
