"""JOB v27 local service binding with a single executable storage authority.

The data-plane implementation remains v26/v25.  This module adds a fail-closed
configuration boundary before any service object (and therefore before any
container or workload) can be created.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from shell.mysql_algorithm_c_local_services_v26 import (  # noqa: F401
    LocalCampaign,
    LocalProductionServices,
)


EXPECTED_MANIFEST = Path(
    "/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json"
)
EXPECTED_TEMPLATE = "/disk1/ra_mysql_isolated_v2/job-base"
EXPECTED_DEVICE = "/dev/nvme1n1"
EXPECTED_MAJOR_MINOR = "259:5"
EXPECTED_PROFILE = "job_local_storage_v2_nvme_fullcopy"
EXPECTED_COPY_MODE = "full_physical_copy_no_overlay_no_reflink"
EXPECTED_SERVICE = "shell.mysql_algorithm_c_local_services_v27:build"
FORBIDDEN_EXECUTION_VALUES = (
    "/mnt/mysql_nvme1n1/",
    "/mnt/mysql_nvme0n1p1",
    "/mnt/mysql_tpcc_nvme3n1",
    "/dev/nvme0n1p1",
    "/dev/nvme3n1",
)


class StorageAuthorityError(RuntimeError):
    """Raised before data-plane construction when storage authority diverges."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden(key) or _contains_forbidden(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden(item) for item in value)
    if isinstance(value, str):
        return any(token in value for token in FORBIDDEN_EXECUTION_VALUES)
    return False


def validate_storage_authority(
    config: Mapping[str, Any], *, expected_service: str = EXPECTED_SERVICE,
) -> None:
    """Validate the sole executable JOB storage binding.

    Old remote template evidence is allowed only below ``template_provenance``
    and must be explicitly non-executable.  It is never inspected as runtime
    authority.
    """

    if config.get("formal_scope") != "mysql_only":
        raise StorageAuthorityError("formal_scope must be mysql_only")
    if config.get("cross_engine_comparison_prohibited") is not True:
        raise StorageAuthorityError("cross-engine comparison must be prohibited")
    if "data_template" in config:
        raise StorageAuthorityError(
            "data_template is a second execution authority and is prohibited"
        )
    if config.get("local_storage_profile_id") != EXPECTED_PROFILE:
        raise StorageAuthorityError("unexpected local storage profile")
    if config.get("local_storage_manifest") != str(EXPECTED_MANIFEST):
        raise StorageAuthorityError("unexpected local storage manifest")
    if not EXPECTED_MANIFEST.is_file() or EXPECTED_MANIFEST.is_symlink():
        raise StorageAuthorityError("local storage manifest is absent or symlinked")
    expected_manifest_sha = _sha256(EXPECTED_MANIFEST)
    if config.get("local_storage_manifest_sha256") != expected_manifest_sha:
        raise StorageAuthorityError("local storage manifest digest differs")

    authority = config.get("execution_storage_authority")
    if not isinstance(authority, Mapping):
        raise StorageAuthorityError("execution_storage_authority is required")
    expected = {
        "schema_version": "mysql_job_execution_storage_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "job_memory",
        "host_id": "compute00",
        "template_path": EXPECTED_TEMPLATE,
        "manifest_path": str(EXPECTED_MANIFEST),
        "manifest_sha256": expected_manifest_sha,
        "profile_id": EXPECTED_PROFILE,
        "device": EXPECTED_DEVICE,
        "device_major_minor": EXPECTED_MAJOR_MINOR,
        "filesystem": "ext4",
        "fresh_copy_mode": EXPECTED_COPY_MODE,
        "overlay": False,
        "reflink": False,
    }
    if dict(authority) != expected:
        raise StorageAuthorityError("execution storage authority differs")
    if _contains_forbidden(authority):
        raise StorageAuthorityError("phantom or remote device entered execution authority")

    workload = config.get("workload")
    if not isinstance(workload, Mapping):
        raise StorageAuthorityError("workload storage contract is absent")
    if (
        workload.get("fresh_copy_mode") != EXPECTED_COPY_MODE
        or workload.get("fresh_full_physical_copy_required") is not True
    ):
        raise StorageAuthorityError("full physical copy contract differs")
    production = config.get("production")
    if (
        not isinstance(production, Mapping)
        or production.get("service_factory") != expected_service
    ):
        raise StorageAuthorityError("formal launch is not bound to v27")

    provenance = config.get("template_provenance")
    if provenance is not None and (
        not isinstance(provenance, Mapping)
        or provenance.get("authority_role") != "provenance_only"
        or provenance.get("execution_authorized") is not False
        or provenance.get("may_participate_in_launch") is not False
    ):
        raise StorageAuthorityError("template provenance is not explicitly non-executable")


def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_storage_authority(config)
    return LocalProductionServices(config).bundle()


__all__ = [
    "LocalCampaign",
    "LocalProductionServices",
    "StorageAuthorityError",
    "build",
    "validate_storage_authority",
]
