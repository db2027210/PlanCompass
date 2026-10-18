#!/usr/bin/env python3
"""Atomically remediate only the live TPC-H dockerd thread affinity.

The helper is intentionally host/root scoped and has no container, storage,
network, PostgreSQL, or systemd mutation code.  It accepts one exact unit,
one exact before/after CPU set, verifies the peer TPC-C daemon remains
disjoint, updates every current dockerd thread, and emits immutable evidence.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


UNIT = "ra-docker-mysql-tpch-v1.service"
PEER_UNIT = "ra-docker-mysql-tpcc-v1.service"
BEFORE = "34-35,70-71"
AFTER = "32-33,68-69"
SOCKET_ARGUMENT = "--host=unix:///run/ra-docker-mysql-tpch-v1.sock"
DATA_ROOT_ARGUMENT = (
    "--data-root=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/docker_ra_tpch_v1"
)


class RemediationError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def cpuset(text):
    values = set()
    for part in text.split(","):
        if "-" in part:
            first, last = (int(value) for value in part.split("-", 1))
            if first > last:
                raise RemediationError("descending CPU range")
            values.update(range(first, last + 1))
        else:
            values.add(int(part))
    if not values:
        raise RemediationError("empty CPU set")
    return values


def systemd_pid(unit):
    completed = subprocess.run(
        ["systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )
    if completed.returncode:
        raise RemediationError("systemctl readback failed")
    values = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    if values.get("ActiveState") != "active" or values.get("SubState") != "running":
        raise RemediationError("daemon unit is not active/running")
    pid = int(values.get("MainPID") or 0)
    if pid <= 0:
        raise RemediationError("daemon MainPID is absent")
    return pid


def startticks(pid):
    raw = (Path("/proc") / str(pid) / "stat").read_text(encoding="ascii")
    boundary = raw.rfind(") ")
    if boundary < 0:
        raise RemediationError("process stat differs")
    fields = raw[boundary + 2 :].split()
    return int(fields[19])


def cmdline(pid):
    return [
        value.decode("utf-8")
        for value in (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
        if value
    ]


def tids(pid):
    directory = Path("/proc") / str(pid) / "task"
    values = sorted(int(path.name) for path in directory.iterdir() if path.name.isdigit())
    if pid not in values or not values:
        raise RemediationError("daemon thread inventory differs")
    return values


def affinity_map(pid):
    result = {}
    for tid in tids(pid):
        result[tid] = set(os.sched_getaffinity(tid))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--peer-unit", required=True)
    parser.add_argument("--expected-main-pid", type=int, required=True)
    parser.add_argument("--expected-startticks", type=int, required=True)
    parser.add_argument("--expected-before", required=True)
    parser.add_argument("--required-after", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or os.getegid() != 0:
        raise RemediationError("affinity remediation requires host root")
    if (
        args.unit != UNIT
        or args.peer_unit != PEER_UNIT
        or args.expected_before != BEFORE
        or args.required_after != AFTER
        or args.output.is_symlink()
        or args.output.exists()
        or not re.fullmatch(r"[A-Za-z0-9_.-]+\.json", args.output.name)
    ):
        raise RemediationError("sealed remediation arguments differ")
    before = cpuset(BEFORE)
    after = cpuset(AFTER)
    if before & after:
        raise RemediationError("before/after CPU sets overlap")
    pid = systemd_pid(UNIT)
    peer_pid = systemd_pid(PEER_UNIT)
    if pid != args.expected_main_pid or startticks(pid) != args.expected_startticks:
        raise RemediationError("TPC-H daemon immutable identity differs")
    argv = cmdline(pid)
    if (
        not argv
        or argv[0] != "/usr/bin/dockerd"
        or SOCKET_ARGUMENT not in argv
        or DATA_ROOT_ARGUMENT not in argv
        or any("postgres" in value.lower() for value in argv)
    ):
        raise RemediationError("TPC-H daemon argv differs")
    peer_before = affinity_map(peer_pid)
    if not peer_before or any(values != before for values in peer_before.values()):
        raise RemediationError("TPC-C peer daemon affinity differs")
    observed_before = affinity_map(pid)
    if not observed_before or any(values != before for values in observed_before.values()):
        raise RemediationError("TPC-H daemon before-affinity differs")
    if any(values & after for values in peer_before.values()):
        raise RemediationError("required TPC-H CPUs overlap TPC-C daemon")
    try:
        for _ in range(10):
            current = affinity_map(pid)
            for tid, values in current.items():
                if values != after:
                    os.sched_setaffinity(tid, after)
            verified = affinity_map(pid)
            if verified and all(values == after for values in verified.values()):
                break
        else:
            raise RemediationError("TPC-H daemon threads did not converge")
        if systemd_pid(UNIT) != pid or startticks(pid) != args.expected_startticks:
            raise RemediationError("TPC-H daemon identity changed during remediation")
        peer_after = affinity_map(peer_pid)
        if peer_after != peer_before:
            raise RemediationError("TPC-C peer daemon changed during remediation")
    except BaseException:
        for tid in tids(pid):
            try:
                os.sched_setaffinity(tid, before)
            except OSError:
                pass
        raise
    final = affinity_map(pid)
    value = {
        "schema_version": "mysql_dedicated_daemon_live_affinity_remediation_v1",
        "status": "PASS_EFFECTIVE_DISJOINT_DAEMON_AFFINITY",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "unit": UNIT,
        "main_pid": pid,
        "main_pid_startticks": args.expected_startticks,
        "cmdline_sha256": hashlib.sha256(b"\0".join(value.encode("utf-8") for value in argv) + b"\0").hexdigest(),
        "declared_exec_affinity": BEFORE,
        "effective_affinity_before": BEFORE,
        "effective_affinity_after": AFTER,
        "all_threads_exact": True,
        "thread_ids_before": sorted(observed_before),
        "thread_ids_after": sorted(final),
        "peer_unit": PEER_UNIT,
        "peer_main_pid": peer_pid,
        "peer_effective_affinity": BEFORE,
        "effective_sets_disjoint": True,
        "applied_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "container_mutation_performed": False,
        "storage_mutation_performed": False,
        "network_mutation_performed": False,
        "systemd_unit_mutation_performed": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
    }
    unsigned = canonical(value)
    value["authority_sha256"] = hashlib.sha256(unsigned).hexdigest()
    payload = canonical(value)
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = args.output.with_name(".%s.tmp.%d" % (args.output.name, os.getpid()))
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o400)
    os.rename(str(temporary), str(args.output))
    sidecar = args.output.with_name(args.output.name + ".sha256")
    sidecar.write_text(
        hashlib.sha256(payload).hexdigest() + "  " + args.output.name + "\n",
        encoding="ascii",
    )
    os.chmod(sidecar, 0o444)
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (RemediationError, OSError, ValueError) as exc:
        sys.stderr.write("NOT_READY: %s\n" % exc)
        raise SystemExit(2)
