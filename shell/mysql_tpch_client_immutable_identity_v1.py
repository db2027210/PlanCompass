#!/usr/bin/env python3
"""Canonical immutable projection for the TPC-H client container.

Docker runtime state, notably ``State.ExecIDs``, is deliberately excluded.
Lifecycle is validated separately by container ID, running PID, process start
ticks, creation identity, and restart count in the surrounding authorities.
"""

try:
    from collections.abc import Mapping
except ImportError:
    from collections import Mapping


def client_container_immutable_projection(container_row):
    config = container_row.get("Config") or {}
    host = container_row.get("HostConfig") or {}
    mounts = container_row.get("Mounts") or []
    if (
        not isinstance(config, Mapping)
        or not isinstance(host, Mapping)
        or not isinstance(mounts, list)
    ):
        raise ValueError("client immutable inspect projection differs")
    fixed_mounts = []
    for mount in mounts:
        if not isinstance(mount, Mapping):
            raise ValueError("client immutable mount projection differs")
        fixed_mounts.append(
            {
                "type": mount.get("Type"),
                "source": mount.get("Source"),
                "destination": mount.get("Destination"),
                "rw": mount.get("RW"),
            }
        )
    fixed_mounts.sort(
        key=lambda value: (
            str(value["type"]),
            str(value["source"]),
            str(value["destination"]),
            str(value["rw"]),
        )
    )
    labels = config.get("Labels")
    return {
        "id": container_row.get("Id"),
        "name": container_row.get("Name"),
        "image": container_row.get("Image"),
        "config": {
            "image": config.get("Image"),
            "entrypoint": config.get("Entrypoint"),
            "cmd": config.get("Cmd"),
            "user": config.get("User"),
            "labels": dict(labels) if isinstance(labels, Mapping) else labels,
        },
        "host_config": {
            "cpu_period": host.get("CpuPeriod"),
            "cpu_quota": host.get("CpuQuota"),
            "cpuset_cpus": host.get("CpusetCpus"),
            "cpuset_mems": host.get("CpusetMems"),
            "memory": host.get("Memory"),
            "memory_swap": host.get("MemorySwap"),
            "pids_limit": host.get("PidsLimit"),
            "readonly_rootfs": host.get("ReadonlyRootfs"),
            "network_mode": host.get("NetworkMode"),
            "security_opt": sorted(
                str(value) for value in (host.get("SecurityOpt") or [])
            ),
            "cap_drop": sorted(
                str(value) for value in (host.get("CapDrop") or [])
            ),
            "log_config": host.get("LogConfig"),
            "restart_policy": host.get("RestartPolicy"),
        },
        "mounts": fixed_mounts,
    }
