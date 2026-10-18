#!/usr/bin/env python3
"""Rebind an XP8 reset authority to verified current replacement identities."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import refresh_mysql_xp8_live_authority_from_reset_v1 as refresh


PREFIX = {"hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(PREFIX), required=True)
    parser.add_argument("--source-reset-binding", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    scene = args.scene
    source = Path(args.source_reset_binding).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    binding_path = output / "rebound_reset_binding.json"
    authority_root = output / "authority"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh authority output required: {output}")

    freeze = refresh.load(refresh.FREEZE)
    original = refresh.load(source)
    prefix = PREFIX[scene]
    expected = {f"{prefix}_w{i}" for i in range(1, 9)}
    frozen = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in freeze.get("workers") or []
        if row.get("worker_id") in expected
    }
    rows = {
        str(row["worker_id"]): copy.deepcopy(dict(row))
        for row in original.get("workers") or []
        if row.get("worker_id") in expected
    }
    if original.get("status") != "PASS" or set(frozen) != expected or set(rows) != expected:
        raise RuntimeError("XP8 source reset authority differs")

    reset8 = refresh.reset8
    reset8.install_for_scene(rows)
    reset8.configure(freeze=refresh.FREEZE, prepared=source)
    impl = reset8.predecessor.base._impl
    rebound: list[dict[str, Any]] = []
    replacements: list[dict[str, str]] = []
    for worker_id in sorted(expected):
        prior = rows[worker_id]
        inspect = reset8._docker_json("inspect", str(prior["container"]))
        state = inspect.get("State") or {}
        labels = (inspect.get("Config") or {}).get("Labels") or {}
        mounts = {
            str(item.get("Destination") or ""): str(item.get("Source") or "")
            for item in inspect.get("Mounts") or []
        }
        defaults = Path(str(prior.get("credential_defaults") or ""))
        benchmark_defaults = Path(str(prior.get("benchmark_defaults") or ""))
        readback = impl["_host_mysql"](
            prior, defaults,
            "SELECT @@version,@@global.innodb_buffer_pool_size,"
            "@@global.innodb_use_native_aio,@@global.innodb_flush_method; "
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='hybench_sf10x';",
            timeout=60,
        ).stdout.splitlines()
        if (
            len(str(inspect.get("Id") or "")) != 64
            or state.get("Running") is not True
            or int(state.get("Pid") or 0) <= 1
            or labels.get("ra.worker_id") != worker_id
            or labels.get("ra.owner") != "mysql_four_scene_v5"
            or mounts.get("/var/lib/mysql") != str(prior["datadir"])
            or not defaults.is_file()
            or not benchmark_defaults.is_file()
            or len(readback) != 2
            or readback[1] != "8"
        ):
            raise RuntimeError(f"{worker_id} current replacement identity differs")
        version, buffer_pool, native_aio, flush_method = readback[0].split("\t")
        if (
            version != "8.0.45"
            or int(buffer_pool) != int(frozen[worker_id]["innodb_buffer_pool_bytes"])
            or native_aio != "0"
            or flush_method.lower() != "fsync"
        ):
            raise RuntimeError(f"{worker_id} current MySQL readback differs")
        current_id = str(inspect["Id"])
        row = {
            **copy.deepcopy(frozen[worker_id]),
            **copy.deepcopy(prior),
            "container_id": current_id,
            "container_pid": int(state["Pid"]),
            "kernel_cgroup_resources": impl["prep"]._kernel_resource_readback(
                inspect, frozen[worker_id]
            ),
            "blkio_authority": impl["prep"]._enforce_blkio(
                inspect, freeze, frozen[worker_id]
            ),
            "ready": True,
            "technical_current_identity_rebind": True,
            "technical_current_identity_rebind_at": refresh.now(),
            "source_container_id": str(prior.get("container_id") or ""),
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
        }
        if current_id != str(prior.get("container_id") or ""):
            replacements.append({
                "worker_id": worker_id,
                "source_container_id": str(prior.get("container_id") or ""),
                "current_container_id": current_id,
            })
        rebound.append(row)

    output.mkdir(parents=True, mode=0o700)
    refresh.atomic(binding_path, {
        **copy.deepcopy(original),
        "schema_version": "mysql_xp8_current_live_identity_rebind_v1",
        "status": "PASS",
        "created_at": refresh.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "workers": rebound,
        "technical_identity_rebind": {
            "source_reset_binding_path": str(source),
            "source_reset_binding_sha256": refresh.sha(source),
            "replacement_identities": replacements,
            "candidate_measurements_reused": 0,
            "accepted_or_rejected_round_credit": 0,
            "data_plane_reset_performed": False,
            "mysql_schema_and_resource_readback_required": True,
        },
    })
    result = refresh.prepare(scene, binding_path, authority_root)
    print({
        "status": "PASS",
        "reset_binding": str(binding_path),
        "authority": result["authority"],
        "replacement_workers": [row["worker_id"] for row in replacements],
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
