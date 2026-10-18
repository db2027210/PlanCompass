#!/usr/bin/env python3
"""Continuous read-only gate for the host01 native mysqld preparation guard.

The default action only reads host state and atomically emits JSON plus a SHA-256
sidecar.  Rollback is deliberately unreachable without the explicit action and
two exact confirmation values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "history/mysql_sgd_env_agents_20260723/ap/native_mysqld_live_isolation_20260723_0700.json"
SOURCE_SHA = "318761051173a8e374df19eb1b361734654dbcd0e26753a9376556a68515a80b"
HOST = "anonymous@192.0.2.3"
PID = 2809
CPUSET = "0-17,36-53"
MEMS = "0"
MEMORY = 2147483648
DB_CPUSET = "24-26,60-62"
CLIENT_CPUSET = "27-29,63-65"
AP_MEMS = "1"


REMOTE_COLLECTOR = r'''
from __future__ import print_function
import json, os, re, subprocess, time

pid = 2809
cpuset_path = "/sys/fs/cgroup/cpuset/ra_ap_guard_mysqld"
memory_path = "/sys/fs/cgroup/memory/system.slice/mysqld.service/ra_ap_guard"
cpu_path = "/sys/fs/cgroup/cpu,cpuacct/system.slice/mysqld.service/cpuacct.usage"
blkio_path = "/sys/fs/cgroup/blkio/system.slice/mysqld.service/blkio.throttle.io_service_bytes"

def read(path):
    with open(path) as handle:
        return handle.read().strip()

def command(args):
    return subprocess.check_output(args, universal_newlines=True).strip()

def ints(path):
    text = read(path)
    return sorted(int(value) for value in text.split() if value)

def status(tid):
    values = {}
    for line in read("/proc/%d/task/%d/status" % (pid, tid)).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key] = value.strip()
    return values

def blkio_total():
    total = 0
    for line in read(blkio_path).splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1] in ("Read", "Write"):
            total += int(fields[2])
    return total

def connections():
    text = command(["netstat", "-nt"])
    count = 0
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[5] == "ESTABLISHED" and any(value.endswith(":3306") or value.endswith(":33060") for value in fields[3:5]):
            count += 1
    return count

main_pid = int(command(["systemctl", "show", "mysqld.service", "-p", "MainPID"]).split("=", 1)[1])
active = command(["systemctl", "is-active", "mysqld.service"])
task_ids = sorted(int(path) for path in os.listdir("/proc/%d/task" % pid))
thread_rows = [status(tid) for tid in task_ids]
cpu_before = int(read(cpu_path)); io_before = blkio_total(); t0 = time.time()
time.sleep(float(os.environ.get("AP_GUARD_SAMPLE_SECONDS", "3")))
elapsed = time.time() - t0; cpu_after = int(read(cpu_path)); io_after = blkio_total()
numa = {}
for line in read(memory_path + "/memory.numa_stat").splitlines():
    fields = line.split()
    row = {}
    for field in fields[1:]:
        key, value = field.split("=", 1); row[key] = int(value)
    numa[fields[0].split("=", 1)[0]] = row
containers = command(["docker", "ps", "-a", "--format", "{{.Names}}|{{.State}}"])
container_rows = [row for row in containers.splitlines() if row]
temporary = [row for row in container_rows if row.split("|", 1)[0].startswith("ra_mysql_ap01_") or "ap_guard_verify" in row.split("|", 1)[0]]
db_inspect = json.loads(command(["docker", "inspect", "mysql_hybench_ap_formal"]))[0]
result = {
  "service_active": active == "active", "main_pid": main_pid,
  "process_name": status(pid).get("Name"), "thread_count": len(task_ids),
  "all_thread_cpus_allowed": sorted(set(row.get("Cpus_allowed_list") for row in thread_rows)),
  "all_thread_mems_allowed": sorted(set(row.get("Mems_allowed_list") for row in thread_rows)),
  "proc_task_ids": task_ids,
  "cpuset": {"cpus": read(cpuset_path + "/cpuset.cpus"), "mems": read(cpuset_path + "/cpuset.mems"), "cgroup_procs": ints(cpuset_path + "/cgroup.procs"), "tasks": ints(cpuset_path + "/tasks")},
  "memory": {"limit_bytes": int(read(memory_path + "/memory.limit_in_bytes")), "memsw_limit_bytes": int(read(memory_path + "/memory.memsw.limit_in_bytes")), "move_charge_at_immigrate": int(read(memory_path + "/memory.move_charge_at_immigrate")), "usage_bytes": int(read(memory_path + "/memory.usage_in_bytes")), "max_usage_bytes": int(read(memory_path + "/memory.max_usage_in_bytes")), "cgroup_procs": ints(memory_path + "/cgroup.procs"), "tasks": ints(memory_path + "/tasks"), "numa_stat": numa},
  "pressure": {"sample_seconds": elapsed, "connections_before": connections(), "cpu_usage_ns_delta": cpu_after-cpu_before, "average_cpu_cores": (cpu_after-cpu_before)/(elapsed*1000000000.0), "io_read_write_bytes_delta": io_after-io_before, "io_read_write_bytes_per_second": (io_after-io_before)/elapsed},
  "containers": {"all": container_rows, "temporary_ap_containers": temporary},
  "ap_database_container": {"exists": True, "running": bool(db_inspect["State"]["Running"]), "cpuset_cpus": db_inspect["HostConfig"]["CpusetCpus"], "cpuset_mems": db_inspect["HostConfig"]["CpusetMems"]},
}
print(json.dumps(result, sort_keys=True))
'''


ROLLBACK_SCRIPT = r'''
set -euo pipefail
pid=2809
service=mysqld.service
cpuset=/sys/fs/cgroup/cpuset/ra_ap_guard_mysqld
memory=/sys/fs/cgroup/memory/system.slice/mysqld.service/ra_ap_guard
test "$(systemctl show "$service" -p MainPID | cut -d= -f2)" = "$pid"
test "$(systemctl is-active "$service")" = active
test "$(cat "$cpuset/cgroup.procs")" = "$pid"
test "$(cat "$memory/cgroup.procs")" = "$pid"
test "$(cat "$cpuset/cpuset.cpus")" = '0-17,36-53'
test "$(cat "$cpuset/cpuset.mems")" = 0
test "$(cat "$memory/memory.limit_in_bytes")" = 2147483648
test "$(cat "$memory/memory.memsw.limit_in_bytes")" = 2147483648
connections=$(netstat -nt | awk '$6=="ESTABLISHED" && ($4 ~ /:3306$/ || $5 ~ /:3306$/ || $4 ~ /:33060$/ || $5 ~ /:33060$/) {n++} END {print n+0}')
test "$connections" = 0
printf '%s\n' "$pid" > /sys/fs/cgroup/cpuset/cgroup.procs
printf '%s\n' "$pid" > /sys/fs/cgroup/memory/system.slice/mysqld.service/cgroup.procs
rmdir "$cpuset"
rmdir "$memory"
systemctl set-property --runtime "$service" MemoryLimit=infinity
test "$(systemctl show "$service" -p MainPID | cut -d= -f2)" = "$pid"
test "$(systemctl is-active "$service")" = active
test ! -e "$cpuset"
test ! -e "$memory"
limit=$(cat /sys/fs/cgroup/memory/system.slice/mysqld.service/memory.limit_in_bytes)
test "$limit" -gt 1152921504606846976
grep -q '^Cpus_allowed_list:[[:space:]]*0-71$' /proc/$pid/status
grep -q '^Mems_allowed_list:[[:space:]]*0-1$' /proc/$pid/status
printf '{"status":"ROLLBACK_PASS","main_pid":2809,"service_active":true,"service_restarted":false,"connections":0,"cpuset_guard_removed":true,"memory_guard_removed":true,"memory_limit_effectively_unlimited":true}\n'
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cpus(text: str) -> set[int]:
    result: set[int] = set()
    for part in text.split(","):
        bounds = [int(value) for value in part.split("-")]
        result.update(range(bounds[0], bounds[-1] + 1))
    return result


def atomic_write(path: Path, value: Any) -> str:
    path = path.resolve(); path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name): os.unlink(temporary_name)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    sidecar = path.with_name(path.name + ".sha256")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{sidecar.name}.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{digest}  {path.name}\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary_name, sidecar)
    finally:
        if os.path.exists(temporary_name): os.unlink(temporary_name)
    directory = os.open(str(path.parent), os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    return digest


def collect(sample_seconds: float) -> dict[str, Any]:
    process = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", HOST, "env", f"AP_GUARD_SAMPLE_SECONDS={sample_seconds}", "python3", "-"],
        input=REMOTE_COLLECTOR, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise RuntimeError(f"remote read-only collector failed: {process.stderr.strip()}")
    return json.loads(process.stdout)


def verify(snapshot: dict[str, Any], sample_seconds: float) -> dict[str, Any]:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    if sha(SOURCE) != SOURCE_SHA or source["pid"] != PID:
        raise ValueError("source isolation evidence changed")
    failures: list[str] = []
    def check(condition: bool, label: str) -> None:
        if not condition: failures.append(label)
    check(snapshot["service_active"], "service_not_active")
    check(snapshot["main_pid"] == PID and snapshot["process_name"] == "mysqld", "main_pid_or_name_changed")
    check(snapshot["all_thread_cpus_allowed"] == [CPUSET], "thread_cpu_affinity_changed")
    check(snapshot["all_thread_mems_allowed"] == [MEMS], "thread_mem_affinity_changed")
    task_ids = snapshot["proc_task_ids"]
    check(snapshot["cpuset"] == {"cpus": CPUSET, "mems": MEMS, "cgroup_procs": [PID], "tasks": task_ids}, "cpuset_membership_or_limits_changed")
    memory = snapshot["memory"]
    check(memory["limit_bytes"] == MEMORY and memory["memsw_limit_bytes"] == MEMORY and memory["move_charge_at_immigrate"] == 3, "memory_limits_changed")
    check(memory["cgroup_procs"] == [PID] and memory["tasks"] == task_ids, "memory_membership_changed")
    check(memory["numa_stat"].get("anon", {}).get("N1") == 0, "anonymous_numa1_pages_nonzero")
    pressure = snapshot["pressure"]
    check(pressure["connections_before"] == 0, "mysql_connections_nonzero")
    check(pressure["average_cpu_cores"] <= 0.10, "mysqld_cpu_pressure_above_0.10_core")
    check(pressure["io_read_write_bytes_per_second"] <= 1048576, "mysqld_io_pressure_above_1MiBps")
    check(snapshot["containers"]["temporary_ap_containers"] == [], "temporary_ap_container_present")
    db = snapshot["ap_database_container"]
    check(db == {"exists": True, "running": False, "cpuset_cpus": DB_CPUSET, "cpuset_mems": AP_MEMS}, "ap_database_container_contract_changed")
    guard_cpu = cpus(CPUSET)
    check(not guard_cpu & cpus(DB_CPUSET) and not guard_cpu & cpus(CLIENT_CPUSET), "native_ap_cpu_overlap")
    check(MEMS != AP_MEMS, "native_ap_memory_node_overlap")
    return {
        "schema_version": "host01_ap_native_mysqld_continuous_gate_v1",
        "status": "PASS_PREPARATION_ISOLATION" if not failures else "FAIL_CLOSED",
        "failures": failures,
        "source_isolation_evidence": {"path": str(SOURCE), "sha256": SOURCE_SHA},
        "expected_main_pid": PID,
        "sample_seconds_requested": sample_seconds,
        "snapshot": snapshot,
        "numa_interpretation": {"required_live_anonymous_n1_pages": 0, "observed_anonymous_n1_pages": memory["numa_stat"].get("anon", {}).get("N1"), "observed_file_cache_n1_pages": memory["numa_stat"].get("file", {}).get("N1"), "note": "file-cache accounting is retained separately and is not anonymous mysqld residency"},
        "ap_disjointness": {"native_cpus": CPUSET, "native_mems": MEMS, "database_cpus": DB_CPUSET, "client_cpus": CLIENT_CPUSET, "ap_mems": AP_MEMS, "native_database_cpu_overlap": False, "native_client_cpu_overlap": False, "native_ap_mem_overlap": False},
        "qualification": {"preparation_isolation_only": True, "postgresql_counterpart": False, "formal_static_isolation_gate_passed": False, "r0": False, "sgd": False, "ready": False, "launch_authorized": False},
        "rollback": {"available_only_with_explicit_execute_rollback": True, "executed": False, "required_confirm_pid": PID, "required_confirm_isolation_evidence_sha256": SOURCE_SHA},
    }


def rollback(args: argparse.Namespace) -> dict[str, Any]:
    if args.confirm_pid != PID or args.confirm_isolation_evidence_sha256 != SOURCE_SHA:
        raise ValueError("rollback requires exact PID and source-evidence SHA confirmations")
    before = verify(collect(args.sample_seconds), args.sample_seconds)
    if before["status"] != "PASS_PREPARATION_ISOLATION":
        raise ValueError("rollback preconditions did not pass")
    command = ["ssh", "-o", "BatchMode=yes", HOST, "docker", "run", "--rm", "--name", "ra_ap_guard_verify_rollback", "--privileged", "--pid=host", "--network=none", "-v", "/:/host", "--entrypoint", "/bin/bash", "mysql:8.0", "-lc", "chroot /host /bin/bash -s"]
    process = subprocess.run(command, input=ROLLBACK_SCRIPT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        raise RuntimeError(f"rollback failed: {process.stderr.strip()}")
    result = json.loads(process.stdout.splitlines()[-1])
    result.update({"schema_version": "host01_ap_native_mysqld_rollback_v1", "explicitly_requested": True, "confirmed_source_sha256": SOURCE_SHA, "qualification": {"preparation_isolation_removed": True, "postgresql_counterpart": False, "formal_static_isolation_gate_passed": False, "r0": False, "ready": False}})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-seconds", type=float, default=3.0)
    parser.add_argument("--execute-rollback", action="store_true")
    parser.add_argument("--confirm-pid", type=int)
    parser.add_argument("--confirm-isolation-evidence-sha256")
    args = parser.parse_args()
    if not 1.0 <= args.sample_seconds <= 30.0:
        raise ValueError("sample duration must be in [1,30] seconds")
    result = rollback(args) if args.execute_rollback else verify(collect(args.sample_seconds), args.sample_seconds)
    digest = atomic_write(args.output, result)
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve()), "sha256": digest, "rollback_executed": bool(args.execute_rollback)}, sort_keys=True))
    return 0 if not result.get("failures") else 2


if __name__ == "__main__":
    raise SystemExit(main())
