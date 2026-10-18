#!/usr/bin/env python3
"""Verify XP effective envelope evidence and the TPC-C effective-readback gap."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PAIRED = ROOT / "config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json"
READINESS = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/readiness_manifest.json"
XP = ROOT / "history/mysql_sgd_env_agents_20260723/xp/formal_mysql_normalized_envelope_readback.json"
XP_STATIC = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/xp_static_isolation_audit_20260723.json"
TPCC_GAP = ROOT / "config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/tpcc_e1_envelope_gap_audit_20260723.json"
TPCC = ROOT / "history/mysql_sgd_env_agents_20260723/tpcc/formal_mysql_normalized_envelope_readback.json"
XP_BUNDLE = ROOT / "history/mysql_sgd_env_agents_20260723/xp/xp_sql_count_20260723_0620"
TPCC_BUNDLE = ROOT / "history/mysql_sgd_env_agents_20260723/tpcc/tpcc_e1_resume_publish_20260722T220924Z_1489393"
TPCC_PROBE = ROOT / "history/mysql_sgd_env_agents_20260723/tpcc/tpcc_envelope_probe_20260722T223216Z"


class VerificationError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def obj(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise VerificationError(f"not a JSON object: {path}")
    return value


def repo_file(raw: Any) -> Path:
    path = (ROOT / str(raw)).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise VerificationError(f"source escapes repository: {raw}") from exc
    if not path.is_file() or path.is_symlink():
        raise VerificationError(f"source absent or symlink: {raw}")
    return path


def sidecar(path: Path) -> None:
    check = path.with_name(path.name + ".sha256")
    expected = f"{sha256(path)}  {path.name}\n"
    if not check.is_file() or check.is_symlink() or check.read_text(encoding="ascii") != expected:
        raise VerificationError(f"sidecar mismatch: {path}")


def checksum_bundle(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        child = (path.parent / name.removeprefix("./")).resolve()
        if not separator or child.parent != path.parent.resolve() or not child.is_file() or child.is_symlink():
            raise VerificationError(f"unsafe checksum member: {name}")
        if sha256(child) != digest:
            raise VerificationError(f"checksum mismatch: {child}")
        count += 1
    if not count:
        raise VerificationError("empty checksum bundle")
    return count


def expand_cpuset(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.split(","):
        bounds = [int(item) for item in part.split("-")]
        result.update(range(bounds[0], bounds[-1] + 1))
    return result


def stable_inspect(document: Any) -> dict[str, Any]:
    item = document[0]
    host = item["HostConfig"]
    return {
        "id": item["Id"], "name": item["Name"], "running": item["State"]["Running"],
        "pid": item["State"]["Pid"], "nano_cpus": host["NanoCpus"],
        "memory": host["Memory"], "memory_swap": host["MemorySwap"],
        "pids": host["PidsLimit"], "cpus": host["CpusetCpus"], "mems": host["CpusetMems"],
        "read": host["BlkioDeviceReadBps"], "write": host["BlkioDeviceWriteBps"],
        "mount_sources": sorted(m["Source"] for m in item["Mounts"]),
    }


def live_topology(host: str, mysql_cpus: str, pg_cpus: str, mysql_dev: str, pg_dev: str) -> None:
    command = (
        "set -euo pipefail; lscpu -p=CPU,CORE,SOCKET,NODE | sed '/^#/d'; "
        f"printf 'MYSQLDEV='; lsblk -dn -o MAJ:MIN -- {mysql_dev}; "
        f"printf 'PGDEV='; lsblk -dn -o MAJ:MIN -- {pg_dev}"
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", host, command],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise VerificationError(f"topology readback failed on {host}: {result.stderr.strip()}")
    cpu_to_core: dict[int, tuple[int, int, int]] = {}
    mysql_major = pg_major = None
    for line in result.stdout.splitlines():
        if re.fullmatch(r"\d+,\d+,\d+,\d+", line):
            cpu, core, socket, node = map(int, line.split(",")); cpu_to_core[cpu] = (core, socket, node)
        elif line.startswith("MYSQLDEV="):
            mysql_major = line.split("=", 1)[1].strip()
        elif line.startswith("PGDEV="):
            pg_major = line.split("=", 1)[1].strip()
    mysql_logical = expand_cpuset(mysql_cpus); pg_logical = expand_cpuset(pg_cpus)
    if mysql_logical & pg_logical or not mysql_logical or not pg_logical:
        raise VerificationError("logical CPU sets overlap or are empty")
    mysql_phys = {cpu_to_core[cpu] for cpu in mysql_logical}; pg_phys = {cpu_to_core[cpu] for cpu in pg_logical}
    if mysql_phys & pg_phys:
        raise VerificationError("physical CPU sets overlap")
    if len(mysql_phys) != len(mysql_logical) // 2:
        raise VerificationError("MySQL cpuset is not composed of complete SMT sibling pairs")
    if mysql_major is None or pg_major is None or mysql_major == pg_major:
        raise VerificationError("data devices are absent or overlap")


def verify_xp(profile: Mapping[str, Any]) -> tuple[int, bool]:
    sidecar(XP); sidecar(XP_STATIC)
    document = obj(XP); static = obj(XP_STATIC)
    if document.get("status") != "MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY":
        raise VerificationError("XP normalized status mismatch")
    if document.get("normalized_envelope") != profile:
        raise VerificationError("XP normalized envelope differs from paired io profile")
    source = document["source_evidence"]
    for ref in source.values():
        path = repo_file(ref["path"])
        if sha256(path) != ref["sha256"]:
            raise VerificationError(f"XP source SHA mismatch: {path}")
    members = checksum_bundle(XP_BUNDLE / "checksums.sha256")
    text = (XP_BUNDLE / "cgroup_readback.txt").read_text()
    required = ("1600000\n100000", "18-33,54-69\n1", "8589934592\n8589934592", "\n1024\n", "259:1 104857600\n259:1 104857600")
    if any(item not in text for item in required):
        raise VerificationError("XP live effective cgroup readback mismatch")
    running = json.loads((XP_BUNDLE / "container_inspect_running.json").read_text())[0]
    host = running["HostConfig"]
    if running["Id"] != document["container_id"] or running["State"]["Running"] is not True or running["State"]["Pid"] != 47979:
        raise VerificationError("XP live container identity/PID mismatch")
    expected_requested = (16000000000, 8589934592, 8589934592, 1024, "18-33,54-69", "1")
    if (host["NanoCpus"], host["Memory"], host["MemorySwap"], host["PidsLimit"], host["CpusetCpus"], host["CpusetMems"]) != expected_requested:
        raise VerificationError("XP requested/effective cross-check mismatch")
    if host["BlkioDeviceReadBps"] != [{"Path": "/dev/nvme0n1", "Rate": 104857600}] \
            or host["BlkioDeviceWriteBps"] != [{"Path": "/dev/nvme0n1", "Rate": 104857600}]:
        raise VerificationError("XP requested blkio mismatch")
    before = stable_inspect(json.loads((XP_BUNDLE / "pg_read_only_before.json").read_text()))
    after = stable_inspect(json.loads((XP_BUNDLE / "pg_read_only_after.json").read_text()))
    if before != after or before["id"] != static["observed_postgresql"]["container_id"]:
        raise VerificationError("observed PG stable fields changed")
    mysql_cpus = document["effective_readback"]["cpuset_cpus"]
    if expand_cpuset(mysql_cpus) & expand_cpuset(before["cpus"]):
        raise VerificationError("XP/observed-PG cpusets overlap")
    if document["effective_readback"]["cpuset_mems"] == before["mems"]:
        raise VerificationError("XP/observed-PG NUMA nodes overlap")
    live_topology("anonymous@192.0.2.4", mysql_cpus, before["cpus"], "/dev/nvme0n1", "/dev/nvme2n1")
    decision = static["decision"]
    if decision != {
        "mysql_effective_envelope_verified": True,
        "observed_preparation_pair_static_non_overlap_verified": True,
        "formal_xp_postgresql_counterpart_present": False,
        "postgresql_effective_cgroup_readback_present": False,
        "cross_engine_envelope_equality": False,
        "formal_static_isolation_gate_passed": False,
        "ready": False,
    }:
        raise VerificationError("XP static-isolation decision is not fail-closed")
    return members, True


def verify_tpcc(profile: Mapping[str, Any]) -> tuple[int, bool]:
    sidecar(TPCC)
    document = obj(TPCC)
    if (
        document.get("status") != "MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY"
        or document.get("scenario") != "tpcc_cpu"
        or document.get("engine") != "mysql"
        or document.get("profile") != "tpcc_cpu_r0_preregistered"
        or document.get("freeze_id") != "frozen_paired_pg_mysql_resource_envelopes_20260723_v1"
        or document.get("normalized_envelope") != profile
    ):
        raise VerificationError("TPC-C normalized identity/profile mismatch")
    members = checksum_bundle(TPCC_PROBE / "checksums.sha256")
    hashes = document["source_evidence"]
    if hashes.get("directory") != str(TPCC_PROBE.relative_to(ROOT)):
        raise VerificationError("TPC-C source directory mismatch")
    paths = {
        "checksums_sha256": "checksums.sha256",
        "normalized_envelope_sha256": "normalized_envelope.json",
        "live_cgroup_readback_sha256": "live_cgroup_readback.json",
        "mysql_inspect_running_sha256": "mysql_inspect_running.json",
        "mysql_inspect_stopped_sha256": "mysql_inspect_stopped.json",
        "pg_before_sha256": "pg_before.json", "pg_after_sha256": "pg_after.json",
        "summary_sha256": "summary.json", "native_mysql_idle_sha256": "native_mysql_idle_before.json",
    }
    for field, name in paths.items():
        if sha256(TPCC_PROBE / name) != hashes[field]:
            raise VerificationError(f"TPC-C source SHA mismatch: {name}")
    normalized = obj(TPCC_PROBE / "normalized_envelope.json")
    if normalized.get("status") != "PASS" or normalized.get("normalized_envelope") != profile:
        raise VerificationError("TPC-C probe normalization mismatch")
    live = obj(TPCC_PROBE / "live_cgroup_readback.json")
    expected_live = {
        "cpu_quota_us": 400000, "cpu_period_us": 100000,
        "cpuset_cpus": "21-24,57-60", "cpuset_mems": "1",
        "memory_limit_bytes": 8589934592, "memory_memsw_limit_bytes": 8589934592,
        "pids_max": 512, "blkio_read_bps_lines": ["259:2 1205862400"],
        "blkio_write_bps_lines": ["259:2 1205862400"],
    }
    if any(live.get(key) != value for key, value in expected_live.items()):
        raise VerificationError("TPC-C live effective cgroup mismatch")
    running = json.loads((TPCC_PROBE / "mysql_inspect_running.json").read_text())[0]
    stopped = json.loads((TPCC_PROBE / "mysql_inspect_stopped.json").read_text())[0]
    host = running["HostConfig"]
    if (
        running["Id"] != document["container_id"] or running["State"]["Running"] is not True
        or stopped["Id"] != running["Id"] or stopped["State"]["Running"] is not False
        or (host["NanoCpus"], host["Memory"], host["MemorySwap"], host["PidsLimit"], host["CpusetCpus"], host["CpusetMems"])
            != (4000000000,8589934592,8589934592,512,"21-24,57-60","1")
        or host["BlkioDeviceReadBps"] != [] or host["BlkioDeviceWriteBps"] != []
    ):
        raise VerificationError("TPC-C Docker/effective cross-check mismatch")
    if (TPCC_PROBE / "pg_before.json").read_bytes() != (TPCC_PROBE / "pg_after.json").read_bytes():
        raise VerificationError("TPC-C PG before/after bytes differ")
    pg = json.loads((TPCC_PROBE / "pg_before.json").read_text())[0]
    live_topology("anonymous@192.0.2.7", host["CpusetCpus"], pg["HostConfig"]["CpusetCpus"], "/dev/nvme3n1", "/dev/nvme2n1")
    static = document.get("static_isolation") or {}
    if (
        static.get("postgresql_role") != "co_resident_tpch_runtime_not_tpcc_counterpart"
        or static.get("postgresql_profile_matches_tpcc") is not False
        or static.get("cross_engine_envelope_equality") is not False
        or static.get("formal_static_isolation_gate_passed") is not False
        or document.get("formal_scope") != {
            "environment_readback_only": True, "r0": False, "sgd": False,
            "accepted_iteration": False, "readiness_changed": False,
            "ready": False, "launch_authorized": False,
        }
    ):
        raise VerificationError("TPC-C normalized evidence is not fail-closed")
    return members, True


def verify_one_readiness_projection(
    readiness: Mapping[str, Any], scenario_name: str, source_path: Path,
    profile: Mapping[str, Any], allow_unpromoted: bool
) -> bool:
    sidecar(READINESS)
    if readiness.get("status") != "NOT_READY" or readiness.get("launch_authorized") is not False:
        raise VerificationError("readiness became authorized")
    scenario = readiness["scenarios"][scenario_name]
    source = obj(source_path)
    expected = {
        "freeze_id": source["freeze_id"],
        "normalized": dict(profile),
        "readback": {
            "path": str(source_path.relative_to(ROOT)),
            "sha256": sha256(source_path),
        },
    }
    mysql = scenario.get("mysql_envelope")
    promoted = mysql == expected
    if not promoted and not (
        allow_unpromoted
        and mysql == {"freeze_id": None, "normalized": None, "readback": None}
    ):
        raise VerificationError(f"{scenario_name} MySQL readiness projection is neither exact nor unpromoted")
    if scenario.get("postgresql_envelope") != {
        "freeze_id": None, "normalized": None, "readback": None
    }:
        raise VerificationError(f"{scenario_name} PostgreSQL envelope was promoted")
    if scenario.get("static_isolation") != {"evidence": None, "passed": False}:
        raise VerificationError(f"{scenario_name} formal static-isolation gate changed")
    for field in ("dynamic_interference", "r0_calibration"):
        value = scenario.get(field)
        if not isinstance(value, Mapping) or value.get("passed") is not False:
            raise VerificationError(f"{scenario_name} {field} gate changed")
    return promoted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-unpromoted-readiness", action="store_true",
        help="permit the exact all-null XP MySQL envelope before promoter commit",
    )
    args = parser.parse_args(argv)
    try:
        readiness = obj(READINESS)
        paired = obj(PAIRED)
        xp_projected = verify_one_readiness_projection(
            readiness, "hybench_xp_io", XP, paired["profiles"]["io"], args.allow_unpromoted_readiness
        )
        tpcc_profile = paired["profiles"]["tpcc_cpu_r0_preregistered"]
        tpcc_projected = verify_one_readiness_projection(
            readiness, "tpcc_cpu", TPCC, tpcc_profile, args.allow_unpromoted_readiness
        )
        if paired["scenarios"]["tpcc_cpu"].get("formal_value_frozen") is not False:
            raise VerificationError("TPC-C preregistered profile was misrepresented as formally frozen")
        xp_members, xp_static = verify_xp(paired["profiles"]["io"])
        tpcc_members, tpcc_normalized = verify_tpcc(tpcc_profile)
        result = {
            "status": "PASS", "xp_mysql_normalized_envelope": True,
            "xp_readiness_projection": xp_projected,
            "xp_bundle_members": xp_members, "xp_observed_static_nonoverlap": xp_static,
            "xp_formal_static_gate": False, "xp_cross_engine_equality": False,
            "tpcc_mysql_normalized_envelope": tpcc_normalized,
            "tpcc_readiness_projection": tpcc_projected,
            "tpcc_bundle_members": tpcc_members, "tpcc_formal_static_gate": False,
            "tpcc_cross_engine_equality": False, "tpcc_formal_value_frozen": False,
            "r0": False, "sgd": False, "ready": False,
        }
    except (OSError, KeyError, ValueError, json.JSONDecodeError, VerificationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True)); return 1
    print(json.dumps(result, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
