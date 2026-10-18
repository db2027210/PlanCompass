#!/usr/bin/env python3
"""Fail-closed, read-only launch preflight for the four MySQL Algorithm C lanes.

This program never starts, stops, or modifies a process, container, VM, socket,
or experiment artifact.  Exit 0 means that the requested lane has 60 clean
one-second intervals and may advance to its *next declared stage*.  It is not
formal-R0, outer-search, replay, or accepted-round authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import socket
import time
from typing import Any, Iterable, Mapping


ROOT = Path("/home/anonymous/program/resource_adaptive_mysql")
SCENARIOS: dict[str, dict[str, Any]] = {
    "job": {
        "stage": "debug_one_round_fresh_r0",
        "bundle": ROOT / "artifacts/mysql_job_v30_debug"
        / "b888870bc6ef136932c26676c797823d2b99858cf6b2b3891f7afe1ef42d3190",
        "manifest": "bundle_manifest.json",
        "readiness": (
            "readiness_manifest-"
            "144486a4a36a205c63ef5b2580b3b0d42cc6cf2ffbd8dd03b94baa7c0cee7810.json"
        ),
        "supersedes": [
            "d812b811e0fdbed0c8bbb966b907134946ade1ce63600eb46619db8b23cad454"
        ],
        "cpuset": "176-183,432-439",
        "device": "/dev/nvme1n1",
        "device_major_minor": "259:5",
        "ports": [65202],
        "required_subsets": 15,
        "fixed_top5": 5,
        "accepted_authorized": False,
    },
    "tpch": {
        "stage": "debug_one_round_fresh_r0",
        "bundle": ROOT / "artifacts/mysql_tpch_v36_debug"
        / "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4",
        "manifest": "bundle_manifest.json",
        "readiness": (
            "readiness_manifest-"
            "bb1d9f3360879559dd61e32930a49b1e9a53daf722f47e3d8c85ccc063ba9792.json"
        ),
        "supersedes": [
            "6ed4de90b5a97b439da6c0f6d2293ddfd3d0813ffb4d82ea0b2cec765591c82d"
        ],
        "cpuset": "200-207,456-463",
        "device": "/dev/nvme2n1",
        "device_major_minor": "259:6",
        "ports": [65203],
        "required_subsets": 15,
        "fixed_top5": 5,
        "accepted_authorized": False,
    },
    "tpcc": {
        "stage": "typed_capture_only",
        "bundle": ROOT / "artifacts/mysql_tpcc_typed_runtime_v1"
        / "fe7aaa24d15256f4e771af598ac672cb72ad9106a02c6dea5e30cc679e55d516",
        "manifest": "bundle_manifest.json",
        "readiness": None,
        "cpuset": "208-215,464-471",
        "device": "/dev/sda",
        "device_major_minor": "8:0",
        "ports": [65206],
        "required_subsets": 15,
        "fixed_top5": 5,
        "accepted_authorized": False,
    },
    "xp": {
        "stage": "at1_at6_capture_only",
        "bundle": ROOT / "artifacts/mysql_xp_v8"
        / "a7d2386cd2a703b6bb73e5269cb8cace807b5fb0eb5fb3d5ccb6e71c2b9caeed",
        "manifest": "manifest.json",
        "readiness": None,
        "cpuset": "184-199,440-455",
        "device": "/dev/sda",
        "device_major_minor": "8:0",
        "ports": [65204, 65304],
        "required_subsets": 127,
        "fixed_top5": 5,
        "accepted_authorized": False,
    },
}

DEVICE_PATHS = {
    "/disk1/": "259:5",
    "/disk2/": "259:6",
    "/disk3/": "8:0",
}


def cpus(value: str) -> set[int]:
    result: set[int] = set()
    for field in value.split(","):
        field = field.strip()
        if not field:
            continue
        if "-" in field:
            first, last = field.split("-", 1)
            result.update(range(int(first), int(last) + 1))
        else:
            result.add(int(field))
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"regular non-symlink file required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def validate_bundle(name: str, spec: Mapping[str, Any]) -> dict[str, Any]:
    bundle = Path(spec["bundle"])
    manifest_path = bundle / str(spec["manifest"])
    manifest = load_object(manifest_path)
    problems: list[str] = []
    if bundle.name != manifest.get("bundle_id"):
        problems.append("bundle_id/path mismatch")
    if manifest.get("formal_scope") != "mysql_only":
        problems.append("formal_scope is not mysql_only")
    if manifest.get("cross_engine_comparison_prohibited") is not True:
        problems.append("cross-engine prohibition is absent")
    if name in {"job", "tpch"}:
        readiness = load_object(bundle / str(spec["readiness"]))
        if (
            readiness.get("status") != "READY"
            or readiness.get("launch_authorized") is not True
            or readiness.get("formal_scope") != "mysql_only"
            or readiness.get("cross_engine_comparison_prohibited") is not True
        ):
            problems.append("immutable readiness manifest differs")
        if manifest.get("accepted_round_authorized") is not False:
            problems.append("debug bundle improperly authorizes accepted rounds")
        if manifest.get("supersedes_bundle_ids") != spec["supersedes"]:
            problems.append(
                f"{name.upper()} successor does not exactly supersede "
                "the rejected cadence predecessor"
            )
        resource = manifest.get("execution_resource_authority") or {}
        if (
            resource.get("cpuset_cpus") != spec["cpuset"]
            or resource.get("storage_device_major_minor")
            != spec["device_major_minor"]
        ):
            problems.append("resource authority differs from queue")
    elif name == "tpcc":
        authorization = manifest.get("authorization") or {}
        if (
            manifest.get("capture_only") is not True
            or manifest.get("outer_execution_authorized") is not False
            or authorization.get("accepted_round_authorized") is not False
            or authorization.get("formal_r0_authorized") is not False
        ):
            problems.append("TPC-C bundle is not exact capture-only authority")
    else:
        if (
            manifest.get("capture_authorized") is not True
            or manifest.get("formal_r0_authorized") is not False
            or manifest.get("outer_execution_authorized") is not False
            or manifest.get("required_nonempty_subsets") != 127
            or manifest.get("fixed_replay_count") != 5
        ):
            problems.append("XP bundle is not exact capture-only authority")
        envelope = manifest.get("resource_envelope") or {}
        if envelope.get("cpuset") != spec["cpuset"]:
            problems.append("XP cpuset differs")
    return {
        "bundle_id": bundle.name,
        "manifest_sha256": sha256(manifest_path),
        "valid": not problems,
        "problems": problems,
    }


def read_status(pid: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in Path(f"/proc/{pid}/status").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            result[key] = value.strip()
    return result


def live_processes() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit():
            continue
        pid = int(directory.name)
        try:
            status = read_status(pid)
            command = (directory / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", errors="replace"
            ).strip()
            rows.append(
                {
                    "pid": pid,
                    "comm": status.get("Name", ""),
                    "cmdline": command,
                    "cpuset": status.get("Cpus_allowed_list", ""),
                }
            )
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return rows


def live_listening_ports() -> list[int]:
    ports: set[int] = set()
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = path.read_text(encoding="ascii").splitlines()[1:]
        except (FileNotFoundError, PermissionError):
            raise RuntimeError(f"cannot inventory listening ports: {path}")
        for line in lines:
            fields = line.split()
            if len(fields) >= 4 and fields[3] == "0A":
                ports.add(int(fields[1].split(":")[1], 16))
    return sorted(ports)


def live_sample() -> dict[str, Any]:
    return {
        "timestamp": time.monotonic(),
        "processes": live_processes(),
        "listening_ports": live_listening_ports(),
    }


def fixture_samples(path: Path) -> list[dict[str, Any]]:
    fixture = load_object(path)
    samples: list[dict[str, Any]] = []
    now = 0.0
    for row in fixture.get("samples") or []:
        if not isinstance(row, Mapping):
            raise RuntimeError("fixture sample must be an object")
        repeat = int(row.get("repeat", 1))
        interval = float(row.get("interval_seconds", 1.0))
        for _ in range(repeat):
            copy = dict(row)
            copy.pop("repeat", None)
            copy.pop("interval_seconds", None)
            copy["timestamp"] = now
            copy.setdefault("processes", [])
            copy.setdefault("listening_ports", [])
            samples.append(copy)
            now += interval
    return samples


def process_devices(row: Mapping[str, Any]) -> set[str]:
    explicit = {
        str(value)
        for value in row.get("devices", [])
        if isinstance(value, (str, int))
    }
    command = str(row.get("cmdline") or "")
    for prefix, major_minor in DEVICE_PATHS.items():
        if prefix in command:
            explicit.add(major_minor)
    return explicit


def classify_blockers(
    spec: Mapping[str, Any], sample: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[int]]:
    target_cpus = cpus(str(spec["cpuset"]))
    target_device = str(spec["device_major_minor"])
    qemu: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    real_pids: list[int] = []
    for row in sample.get("processes") or []:
        if not isinstance(row, Mapping):
            raise RuntimeError("process row must be an object")
        pid = int(row.get("pid", -1))
        comm = str(row.get("comm") or "").lower()
        command = str(row.get("cmdline") or "").lower()
        allowed = cpus(str(row.get("cpuset") or ""))
        overlap = sorted(target_cpus.intersection(allowed))
        devices = process_devices(row)
        is_qemu = "qemu" in comm or "qemu-system" in command or comm.startswith("kvm")
        is_java_workload = (
            comm == "java"
            and (
                "benchbase" in command
                or "hybenchformalmain" in command
                or "hybench-" in command
            )
        )
        is_workload_shell = (
            comm in {"sh", "bash"}
            and (
                "run_tpcc_sensitive_mix.sh" in command
                or "formal_hybench" in command
            )
        )
        is_mysql_controller = (
            comm.startswith("python")
            and any(
                marker in command
                for marker in (
                    "debug_mysql_gradient_round",
                    "launch_job_v28_debug",
                    "launch_job_v29_debug",
                    "launch_tpch_v35_debug",
                    "mysql_tpcc_typed_capture_entry",
                    "mysql_xp_local_capture_entry",
                )
            )
        )
        is_data_plane = (
            is_qemu
            or "mysqld" in comm
            or is_java_workload
            or is_workload_shell
            or is_mysql_controller
        )
        if is_data_plane:
            real_pids.append(pid)
        if not is_data_plane:
            continue
        device_hit = target_device in devices
        unknown_qemu_device = is_qemu and not devices
        if overlap or device_hit or unknown_qemu_device:
            blocker = {
                "pid": pid,
                "comm": comm,
                "cpu_overlap": overlap,
                "devices": sorted(devices),
                "reasons": [
                    reason
                    for reason, true in (
                        ("cpu_overlap", bool(overlap)),
                        ("target_device", device_hit),
                        ("qemu_device_unknown", unknown_qemu_device),
                    )
                    if true
                ],
            }
            (qemu if is_qemu else other).append(blocker)
    occupied = sorted(
        set(int(value) for value in sample.get("listening_ports") or [])
        .intersection(int(value) for value in spec["ports"])
    )
    blockers = qemu + other
    if occupied:
        blockers.append(
            {
                "pid": None,
                "comm": "listener",
                "reasons": ["target_port_occupied"],
                "ports": occupied,
            }
        )
    return blockers, sorted(set(real_pids))


def evaluate(name: str, samples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    spec = SCENARIOS[name]
    static = validate_bundle(name, spec)
    rows = list(samples)
    if not rows:
        raise RuntimeError("at least one sample required")
    all_blockers: list[dict[str, Any]] = []
    real_pids: set[int] = set()
    for row in rows:
        blockers, pids = classify_blockers(spec, row)
        all_blockers.extend(blockers)
        real_pids.update(pids)
    unique_blockers = {
        json.dumps(row, sort_keys=True): row for row in all_blockers
    }
    blockers = list(unique_blockers.values())
    has_qemu = any("qemu" in str(row.get("comm")) or "kvm" in str(row.get("comm"))
                   for row in blockers)
    timestamps = [float(row["timestamp"]) for row in rows]
    gaps = [
        later - earlier for earlier, later in zip(timestamps, timestamps[1:])
    ]
    dynamic_ok = (
        len(gaps) >= 60
        and timestamps[-1] - timestamps[0] >= 60.0
        and all(0.0 < gap <= 1.5 for gap in gaps)
        and not blockers
    )
    if not static["valid"]:
        status = "BLOCKED_BUNDLE"
    elif has_qemu:
        status = "BLOCKED_QEMU"
    elif blockers:
        status = "BLOCKED_INTERFERENCE"
    elif not dynamic_ok:
        status = "PENDING_60S_DYNAMIC_GATE"
    else:
        status = "READY_NEXT_STAGE"
    return {
        "scenario": name,
        "status": status,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "stage": spec["stage"],
        "bundle": static,
        "cpuset": spec["cpuset"],
        "device": spec["device"],
        "device_major_minor": spec["device_major_minor"],
        "ports": spec["ports"],
        "real_data_plane_pids": sorted(real_pids),
        "dynamic_gate": {
            "required_intervals": 60,
            "maximum_gap_seconds": 1.5,
            "observed_intervals": len(gaps),
            "observed_duration_seconds": (
                timestamps[-1] - timestamps[0] if timestamps else 0.0
            ),
            "passed": dynamic_ok,
        },
        "blockers": blockers,
        "counting_boundary": {
            "r0": "only a natural PASS of the fresh in-band R0 stage",
            "candidate": f"only all {spec['required_subsets']} nonempty subsets",
            "top5": f"only all fixed {spec['fixed_top5']} real replays",
            "accepted": (
                "never authorized by this bundle/stage; capture, R0, failed "
                "replay, debug and gate failures remain 0/10"
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument(
        "--live-seconds",
        type=int,
        default=0,
        help="live read-only sampling duration; use 60 before an actual launch",
    )
    args = parser.parse_args()
    if socket.gethostname() != "compute00":
        raise SystemExit("BLOCKED_HOST: direct compute00 execution required")
    if args.fixture:
        samples = fixture_samples(args.fixture)
    else:
        samples = [live_sample()]
        initial_blockers, _ = classify_blockers(
            SCENARIOS[args.scenario], samples[0]
        )
        for _ in range(0 if initial_blockers else args.live_seconds):
            time.sleep(1.0)
            current = live_sample()
            samples.append(current)
            blockers, _ = classify_blockers(SCENARIOS[args.scenario], current)
            if blockers:
                break
    result = evaluate(args.scenario, samples)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "READY_NEXT_STAGE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
