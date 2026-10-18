#!/usr/bin/env python3
"""Read-only host04 patrol for TPC-C/TPC-H production capture readiness."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "history/mysql_sgd_env_agents_20260723/host04_mysql_capture_patrol/latest"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_bytes(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def write_json(path: Path, value: Any) -> str:
    return write_bytes(path, canonical_bytes(value))


def parse_processes(raw: str, role: str) -> list[dict[str, Any]]:
    rows = []
    pattern = re.compile(rf"PROCESS_BEGIN\|{re.escape(role)}\|(\d+)\n(.*?)PROCESS_END\|{re.escape(role)}\|\1", re.DOTALL)
    for match in pattern.finditer(raw):
        pid = int(match.group(1))
        body = match.group(2)
        def field(name: str) -> str | None:
            found = re.search(rf"^{re.escape(name)}:\s*(.*)$", body, re.MULTILINE)
            return found.group(1).strip() if found else None
        rows.append({
            "cmdline": field("CMD"),
            "cpu_percent": field("PS_CPU_PERCENT"),
            "cpus_allowed_list": field("Cpus_allowed_list"),
            "memory_percent": field("PS_MEM_PERCENT"),
            "mems_allowed_list": field("Mems_allowed_list"),
            "pid": pid,
            "state": field("State"),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="anonymous@192.0.2.7")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    remote = r'''set -u
echo "OBSERVED_AT:$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "HOSTNAME:$(hostname)"
echo "UPTIME:$(uptime)"
echo "LOADAVG:$(cat /proc/loadavg)"
emit_proc() {
  role=$1; p=$2
  test -r "/proc/$p/status" || return 0
  echo "PROCESS_BEGIN|$role|$p"
  grep -E '^(Name|State|Cpus_allowed_list|Mems_allowed_list):' "/proc/$p/status" || true
  echo "CGROUP_BEGIN"
  cat "/proc/$p/cgroup" || true
  echo "CGROUP_END"
  echo "CMD:$(tr '\0' ' ' <"/proc/$p/cmdline" 2>/dev/null || true)"
  echo "PS_CPU_PERCENT:$(ps -p "$p" -o pcpu= 2>/dev/null | tr -d ' ')"
  echo "PS_MEM_PERCENT:$(ps -p "$p" -o pmem= 2>/dev/null | tr -d ' ')"
  echo "PROCESS_END|$role|$p"
}
driver_name=parameter_optimizer
driver_name=${driver_name}.py
for proc in /proc/[0-9]*; do
  p=${proc#/proc/}
  cmd=$(tr '\0' ' ' <"$proc/cmdline" 2>/dev/null || true)
  case "$cmd" in *"$driver_name"*) emit_proc PG_DRIVER "$p" ;; esac
done
pg_json=$(docker inspect pg5_tpch_s04_20260722 --format '{{json .}}' 2>/dev/null || true)
echo "PG_DOCKER_JSON_BEGIN"
echo "$pg_json"
echo "PG_DOCKER_JSON_END"
pg_pid=$(docker inspect pg5_tpch_s04_20260722 --format '{{.State.Pid}}' 2>/dev/null || true)
case "$pg_pid" in ''|0) ;; *) emit_proc PG_DB "$pg_pid" ;; esac
mysql_pid=
for proc in /proc/[0-9]*; do
  p=${proc#/proc/}
  comm=$(cat "$proc/comm" 2>/dev/null || true)
  if test "$comm" = mysqld; then mysql_pid=$p; emit_proc NATIVE_MYSQLD "$p"; fi
done
echo "NATIVE_ISOLATION_BEGIN"
if test -n "$mysql_pid"; then
  cg=ra_mysql_native_mysqld_2436_20260723
  echo "pid=$mysql_pid"
  echo "service_main_pid=$(systemctl show mysqld.service -p MainPID | cut -d= -f2)"
  echo "service_state=$(systemctl is-active mysqld.service 2>/dev/null || true)"
  echo "cpuset_cpus=$(cat /sys/fs/cgroup/cpuset/$cg/cpuset.cpus 2>/dev/null || true)"
  echo "cpuset_mems=$(cat /sys/fs/cgroup/cpuset/$cg/cpuset.mems 2>/dev/null || true)"
  echo "memory_limit_bytes=$(cat /sys/fs/cgroup/memory/$cg/memory.limit_in_bytes 2>/dev/null || true)"
  echo "memsw_limit_bytes=$(cat /sys/fs/cgroup/memory/$cg/memory.memsw.limit_in_bytes 2>/dev/null || true)"
  echo "memory_usage_bytes=$(cat /sys/fs/cgroup/memory/$cg/memory.usage_in_bytes 2>/dev/null || true)"
  echo "memsw_usage_bytes=$(cat /sys/fs/cgroup/memory/$cg/memory.memsw.usage_in_bytes 2>/dev/null || true)"
  echo "n1_pages=$(sudo -n awk '{for(i=1;i<=NF;i++)if($i~/^N1=/)n+=substr($i,4)}END{print n+0}' /proc/$mysql_pid/numa_maps 2>/dev/null || true)"
  total=0; bad=0
  for task in /proc/$mysql_pid/task/[0-9]*; do
    total=$((total+1))
    c=$(grep '^Cpus_allowed_list:' "$task/status" | cut -f2)
    m=$(grep '^Mems_allowed_list:' "$task/status" | cut -f2)
    test "$c" = "16-17,52-53" && test "$m" = 0 || bad=$((bad+1))
  done
  echo "thread_count=$total"
  echo "thread_mismatch_count=$bad"
  echo "established_3306=$(sudo -n netstat -tnp 2>/dev/null | awk '$6=="ESTABLISHED" && ($4 ~ /:3306$/ || $5 ~ /:3306$/){n++}END{print n+0}')"
fi
echo "NATIVE_ISOLATION_END"
echo "MYSQL_CONTAINER_LIST_BEGIN"
docker ps -a --format '{{.Names}}|{{.Status}}' 2>/dev/null | grep -E 'mysql|tpcc|tpch' || true
echo "MYSQL_CONTAINER_LIST_END"
echo "DISK_READBACK_BEGIN"
df -B1 -T / /var/lib/mysql /mnt/nvme2n1 2>&1 || true
findmnt -T /var/lib/mysql -no SOURCE,FSTYPE,TARGET,OPTIONS 2>&1 || true
findmnt -T /mnt/nvme2n1 -no SOURCE,FSTYPE,TARGET,OPTIONS 2>&1 || true
echo "DISK_READBACK_END"
echo "IOSTAT_BEGIN"
command -v iostat >/dev/null && iostat -dx 1 2 || true
echo "IOSTAT_END"'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", args.host, remote],
        text=True, capture_output=True, timeout=20, check=False,
    )
    output_root = args.output_root.resolve()
    raw_payload = (result.stdout + result.stderr).encode()
    raw_sha = write_bytes(output_root / "host_readback.log", raw_payload)
    drivers = parse_processes(result.stdout, "PG_DRIVER")
    pg_processes = parse_processes(result.stdout, "PG_DB")
    mysqld = parse_processes(result.stdout, "NATIVE_MYSQLD")
    isolation_match = re.search(r"NATIVE_ISOLATION_BEGIN\n(.*?)NATIVE_ISOLATION_END", result.stdout, re.DOTALL)
    isolation: dict[str, Any] = {}
    if isolation_match:
        isolation = dict(
            line.split("=", 1) for line in isolation_match.group(1).splitlines() if "=" in line
        )
        for key in ["pid", "service_main_pid", "memory_limit_bytes", "memsw_limit_bytes",
                    "memory_usage_bytes", "memsw_usage_bytes", "n1_pages", "thread_count",
                    "thread_mismatch_count", "established_3306"]:
            if isolation.get(key, "").isdigit():
                isolation[key] = int(isolation[key])
    pg_json_match = re.search(r"PG_DOCKER_JSON_BEGIN\n(.*?)\nPG_DOCKER_JSON_END", result.stdout, re.DOTALL)
    pg_container: dict[str, Any] = {}
    if pg_json_match and pg_json_match.group(1).strip():
        docker = json.loads(pg_json_match.group(1))
        pg_container = {
            "cpuset_cpus": docker["HostConfig"].get("CpusetCpus"),
            "cpuset_mems": docker["HostConfig"].get("CpusetMems"),
            "memory_bytes": docker["HostConfig"].get("Memory"),
            "memory_swap_bytes": docker["HostConfig"].get("MemorySwap"),
            "name": docker.get("Name", "").lstrip("/"),
            "pid": docker["State"].get("Pid"),
            "running": docker["State"].get("Running"),
        }
    observed = re.search(r"^OBSERVED_AT:(.*)$", result.stdout, re.MULTILINE)
    hostname = re.search(r"^HOSTNAME:(.*)$", result.stdout, re.MULTILINE)
    loadavg = re.search(r"^LOADAVG:(.*)$", result.stdout, re.MULTILINE)
    status = "BLOCKED_PG_DRIVER_ACTIVE" if drivers else (
        "BLOCKED_HOST_UNREACHABLE" if result.returncode != 0 else "READY_FOR_CONTRACT_GATE_RECHECK"
    )
    summary = {
        "actions": {
            "benchmark_started": False,
            "database_connection_attempted": False,
            "explain_started": False,
            "mysql_only_capture_started": False,
            "pg_modified": False,
            "r0_or_sgd_started": False,
            "remote_mutation_performed": False,
        },
        "decision": status,
        "disk_and_load_present_in_full_log": all(marker in result.stdout for marker in ["LOADAVG:", "DISK_READBACK_BEGIN", "IOSTAT_BEGIN"]),
        "host": {"hostname": hostname.group(1).strip() if hostname else None, "ssh_target": args.host},
        "loadavg": loadavg.group(1).strip() if loadavg else None,
        "native_mysqld_processes": mysqld,
        "native_mysqld_isolation": isolation,
        "next_action": "do not start capture; repeat read-only patrol" if drivers else "run both independent contract verifiers and request main controller before capture",
        "observed_at_utc": observed.group(1).strip() if observed else None,
        "pg_container": pg_container,
        "pg_database_processes": pg_processes,
        "pg_driver_processes": drivers,
        "raw_readback": {"path": str(output_root / "host_readback.log"), "sha256": raw_sha},
        "remote_exit_code": result.returncode,
        "schema_version": "host04_mysql_capture_patrol_v1",
    }
    summary_sha = write_json(output_root / "patrol_summary.json", summary)
    print(json.dumps({"drivers": len(drivers), "mysqld": len(mysqld), "patrol_sha256": summary_sha, "status": status}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error), "status": "FAIL"}, sort_keys=True))
        raise SystemExit(1)
