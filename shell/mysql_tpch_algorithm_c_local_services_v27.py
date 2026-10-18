"""TPC-H v35/v27 local service with one executable resource authority."""

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


EXPECTED_SERVICE = "shell.mysql_tpch_algorithm_c_local_services_v27:build"
EXPECTED_MANIFEST = Path(
    "/disk2/ra_mysql_isolated_v2/tpch-storage-v2.manifest.json"
)
EXPECTED_PROFILE_ID = "tpch_local_storage_v2_nvme_fullcopy"
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
EXPECTED_STORAGE = {
    "schema_version": "mysql_tpch_execution_storage_authority_v1",
    "authority_role": "sole_execution_authority",
    "execution_authorized": True,
    "scenario": "tpch_memory",
    "host_id": "compute00",
    "template_path": "/disk2/ra_mysql_isolated_v2/tpch-memory",
    "manifest_path": str(EXPECTED_MANIFEST),
    "profile_id": EXPECTED_PROFILE_ID,
    "device": "/dev/nvme2n1",
    "device_major_minor": "259:6",
    "filesystem": "ext4",
    "fresh_copy_mode": EXPECTED_COPY_MODE,
    "overlay": False,
    "reflink": False,
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
FORBIDDEN_EXECUTION_VALUES = (
    "5583457485",
    "/dev/nvme0n1p1",
    "/dev/nvme1n1",
    "/dev/nvme3n1",
    "/dev/sda",
    "/mnt/mysql_",
)


class TpchAuthorityError(RuntimeError):
    """Fail-closed authority error raised before service construction."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _forbidden(value: Any) -> bool:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return any(token in encoded for token in FORBIDDEN_EXECUTION_VALUES)


def validate_authorities(config: Mapping[str, Any]) -> None:
    if (
        config.get("formal_scope") != "mysql_only"
        or config.get("cross_engine_comparison_prohibited") is not True
    ):
        raise TpchAuthorityError("mysql_only scope is mandatory")
    if "data_template" in config:
        raise TpchAuthorityError("data_template is a second authority")
    production = config.get("production")
    if (
        not isinstance(production, Mapping)
        or production.get("service_factory") != EXPECTED_SERVICE
    ):
        raise TpchAuthorityError("TPC-H v27 service binding differs")

    manifest_sha = _sha256(EXPECTED_MANIFEST)
    if (
        config.get("local_storage_profile_id") != EXPECTED_PROFILE_ID
        or config.get("local_storage_manifest") != str(EXPECTED_MANIFEST)
        or config.get("local_storage_manifest_sha256") != manifest_sha
    ):
        raise TpchAuthorityError("storage manifest pointer differs")
    storage = config.get("execution_storage_authority")
    expected_storage = {
        **EXPECTED_STORAGE,
        "manifest_sha256": manifest_sha,
    }
    if not isinstance(storage, Mapping) or dict(storage) != expected_storage:
        raise TpchAuthorityError("sole storage authority differs")

    resource = config.get("execution_resource_authority")
    if not isinstance(resource, Mapping):
        raise TpchAuthorityError("sole resource authority is absent")
    comparable = {
        key: value
        for key, value in resource.items()
        if key not in {"profile_path", "profile_sha256"}
    }
    if comparable != EXPECTED_RESOURCE or _forbidden(resource):
        raise TpchAuthorityError("sole resource authority differs")
    profile_path = Path(str(resource.get("profile_path") or ""))
    if (
        not profile_path.is_absolute()
        or not profile_path.is_file()
        or profile_path.is_symlink()
        or str(config.get("profile") or "") != str(profile_path)
        or _sha256(profile_path)
        != str(resource.get("profile_sha256") or "").lower()
        or json.loads(profile_path.read_text(encoding="utf-8"))
        != EXPECTED_PROFILE
    ):
        raise TpchAuthorityError("profile is divergent or unsealed")

    workload = config.get("workload")
    if (
        not isinstance(workload, Mapping)
        or workload.get("host_control_cpuset")
        != EXPECTED_RESOURCE["cpuset_cpus"]
        or workload.get("tpch_terminals") != 4
        or workload.get("fresh_copy_mode") != EXPECTED_COPY_MODE
        or workload.get("fresh_full_physical_copy_required") is not True
    ):
        raise TpchAuthorityError("workload resource mirror differs")
    mirror = workload.get("v35_resource_contract")
    expected_mirror = {
        "authority_role": "mirror_only",
        "cpu_quota_cores": 4,
        "memory_bytes": 5583454208,
        "io_read_bps": 100663296,
        "io_write_bps": 100663296,
        "terminals": 4,
    }
    if mirror != expected_mirror:
        raise TpchAuthorityError("workload v35 resource mirror differs")
    policy = config.get("authority_policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("executable_storage_authority_count") != 1
        or policy.get("executable_resource_authority_count") != 1
        or policy.get("mismatch_action") != "FAIL_CLOSED"
    ):
        raise TpchAuthorityError("single-authority policy differs")

    provenance = config.get("provenance_only")
    if provenance is not None and (
        not isinstance(provenance, Mapping)
        or provenance.get("authority_role") != "provenance_only"
        or provenance.get("execution_authorized") is not False
        or provenance.get("may_participate_in_launch") is not False
    ):
        raise TpchAuthorityError("legacy provenance is executable")
    lane = LOCAL_LANES.get("tpch")
    if (
        lane is None
        or lane.cpuset != EXPECTED_RESOURCE["cpuset_cpus"]
        or lane.mems != EXPECTED_RESOURCE["cpuset_mems"]
        or lane.device != EXPECTED_RESOURCE["storage_device"]
    ):
        raise TpchAuthorityError("compiled local TPC-H lane differs")


def build(config: Mapping[str, Any]) -> Mapping[str, Any]:
    validate_authorities(config)
    return LocalProductionServices(config).bundle()


__all__ = [
    "LocalCampaign",
    "LocalProductionServices",
    "TpchAuthorityError",
    "build",
    "validate_authorities",
]
