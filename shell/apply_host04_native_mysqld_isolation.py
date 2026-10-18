#!/usr/bin/env python3
"""Conditionally isolate host04 native mysqld online, with automatic rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "history/mysql_sgd_env_agents_20260723/tpch/host04_native_mysqld_isolation_20260723_0703"


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="anonymous@192.0.2.7")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    remote = r'''set -Eeuo pipefail
p=2436
cg=ra_mysql_native_mysqld_2436_20260723
cpudir=/sys/fs/cgroup/cpuset/$cg
memdir=/sys/fs/cgroup/memory/$cg
orig_memdir=/sys/fs/cgroup/memory/system.slice/mysqld.service
cpus=16-17,52-53
mems=0
limit=2147483648
mutated=0
rollback() {
  rc=$?
  trap - ERR
  if test "$mutated" = 1; then
    echo "ROLLBACK_BEGIN"
    if test -r /proc/$p/status; then
      for task in /proc/$p/task/[0-9]*; do
        tid=${task##*/}
        echo "$tid" | sudo -n tee /sys/fs/cgroup/cpuset/tasks >/dev/null || true
      done
      echo "$p" | sudo -n tee "$orig_memdir/cgroup.procs" >/dev/null || true
    fi
    sudo -n rmdir "$cpudir" 2>/dev/null || true
    sudo -n rmdir "$memdir" 2>/dev/null || true
    echo "ROLLBACK_END"
  fi
  exit "$rc"
}
trap rollback ERR
echo "PRECHECK_BEGIN"
echo "OBSERVED_AT:$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "HOSTNAME:$(hostname)"
test "$(systemctl show mysqld.service -p MainPID | cut -d= -f2)" = "$p"
test "$(systemctl is-active mysqld.service)" = active
test "$(cat /proc/$p/comm)" = mysqld
starttime=$(awk '{print $22}' /proc/$p/stat)
echo "STARTTIME:$starttime"
rss_kib=$(awk '/^VmRSS:/{print $2}' /proc/$p/status)
test "$rss_kib" -lt 1048576
echo "RSS_KIB:$rss_kib"
test "$(sudo -n netstat -tnp 2>/dev/null | awk '$6=="ESTABLISHED" && ($4 ~ /:3306$/ || $5 ~ /:3306$/){n++}END{print n+0}')" = 0
echo "ESTABLISHED_3306:0"
sudo -n true
command -v migratepages >/dev/null
test -f /sys/fs/cgroup/cpuset/cpuset.memory_migrate
test -f /sys/fs/cgroup/memory/memory.move_charge_at_immigrate
test ! -e "$cpudir"
test ! -e "$memdir"
test "$(cat /sys/devices/system/node/node0/cpulist)" = "0-17,36-53"
before_ticks=$(awk '{print $14+$15}' /proc/$p/stat)
before_io=$(sudo -n awk '/^(read_bytes|write_bytes):/{s+=$2}END{print s}' /proc/$p/io)
sleep 3
after_ticks=$(awk '{print $14+$15}' /proc/$p/stat)
after_io=$(sudo -n awk '/^(read_bytes|write_bytes):/{s+=$2}END{print s}' /proc/$p/io)
test "$before_ticks" = "$after_ticks"
test "$before_io" = "$after_io"
echo "CPU_TICKS_DELTA:0"
echo "IO_BYTES_DELTA:0"
driver_mask=$(awk '/^Cpus_allowed_list:/{print $2}' /proc/88883/status)
driver_mems=$(awk '/^Mems_allowed_list:/{print $2}' /proc/88883/status)
pgpid=$(docker inspect pg5_tpch_s04_20260722 --format '{{.State.Pid}}')
pgmask=$(awk '/^Cpus_allowed_list:/{print $2}' /proc/$pgpid/status)
pgmems=$(awk '/^Mems_allowed_list:/{print $2}' /proc/$pgpid/status)
test "$pgmask" = "0-15,36-51"
test "$pgmems" = 0
echo "PG_DRIVER_BEFORE:$driver_mask|$driver_mems"
echo "PG_DB_BEFORE:$pgpid|$pgmask|$pgmems"
echo "ORIGINAL_CGROUP:$(cat /proc/$p/cgroup | tr '\n' ';')"
echo "PRECHECK_END"

sudo -n mkdir "$cpudir"
sudo -n mkdir "$memdir"
mutated=1
echo "$mems" | sudo -n tee "$cpudir/cpuset.mems" >/dev/null
echo "$cpus" | sudo -n tee "$cpudir/cpuset.cpus" >/dev/null
echo 1 | sudo -n tee "$cpudir/cpuset.memory_migrate" >/dev/null
echo 3 | sudo -n tee "$memdir/memory.move_charge_at_immigrate" >/dev/null
echo "$limit" | sudo -n tee "$memdir/memory.limit_in_bytes" >/dev/null
echo "$limit" | sudo -n tee "$memdir/memory.memsw.limit_in_bytes" >/dev/null
echo "$p" | sudo -n tee "$memdir/cgroup.procs" >/dev/null
echo "$p" | sudo -n tee "$cpudir/cgroup.procs" >/dev/null
sudo -n migratepages "$p" 1 0

echo "VERIFY_BEGIN"
test "$(systemctl show mysqld.service -p MainPID | cut -d= -f2)" = "$p"
test "$(systemctl is-active mysqld.service)" = active
test "$(awk '{print $22}' /proc/$p/stat)" = "$starttime"
test "$(awk '/^Cpus_allowed_list:/{print $2}' /proc/$p/status)" = "$cpus"
test "$(awk '/^Mems_allowed_list:/{print $2}' /proc/$p/status)" = "$mems"
test "$(cat "$memdir/memory.limit_in_bytes")" = "$limit"
test "$(cat "$memdir/memory.memsw.limit_in_bytes")" = "$limit"
test "$(cat "$memdir/memory.move_charge_at_immigrate")" = 3
test "$(cat "$cpudir/cpuset.memory_migrate")" = 1
thread_count=0
for task in /proc/$p/task/[0-9]*; do
  tid=${task##*/}
  test "$(awk '/^Cpus_allowed_list:/{print $2}' "$task/status")" = "$cpus"
  test "$(awk '/^Mems_allowed_list:/{print $2}' "$task/status")" = "$mems"
  grep -qx "$tid" "$cpudir/tasks"
  thread_count=$((thread_count+1))
done
n1=$(sudo -n awk '{for(i=1;i<=NF;i++)if($i~/^N1=/)n+=substr($i,4)}END{print n+0}' /proc/$p/numa_maps)
test "$n1" = 0
test "$(awk '/^Cpus_allowed_list:/{print $2}' /proc/88883/status)" = "$driver_mask"
test "$(awk '/^Mems_allowed_list:/{print $2}' /proc/88883/status)" = "$driver_mems"
test "$(awk '/^Cpus_allowed_list:/{print $2}' /proc/$pgpid/status)" = "$pgmask"
test "$(awk '/^Mems_allowed_list:/{print $2}' /proc/$pgpid/status)" = "$pgmems"
echo "PID:$p"
echo "STARTTIME:$starttime"
echo "THREAD_COUNT:$thread_count"
echo "CPUSET:$(awk '/^Cpus_allowed_list:/{print $2}' /proc/$p/status)"
echo "MEMS:$(awk '/^Mems_allowed_list:/{print $2}' /proc/$p/status)"
echo "N1_PAGES:$n1"
echo "MEMORY_USAGE:$(cat "$memdir/memory.usage_in_bytes")"
echo "MEMSW_USAGE:$(cat "$memdir/memory.memsw.usage_in_bytes")"
echo "MEMORY_LIMIT:$(cat "$memdir/memory.limit_in_bytes")"
echo "MEMSW_LIMIT:$(cat "$memdir/memory.memsw.limit_in_bytes")"
echo "SERVICE:$(systemctl is-active mysqld.service)"
echo "PG_DRIVER_AFTER:$(awk '/^Cpus_allowed_list:/{print $2}' /proc/88883/status)|$(awk '/^Mems_allowed_list:/{print $2}' /proc/88883/status)"
echo "PG_DB_AFTER:$pgpid|$(awk '/^Cpus_allowed_list:/{print $2}' /proc/$pgpid/status)|$(awk '/^Mems_allowed_list:/{print $2}' /proc/$pgpid/status)"
echo "VERIFY_END"
trap - ERR
echo "RESULT:APPLIED_AND_VERIFIED"
'''
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", args.host, remote],
        text=True, capture_output=True, timeout=45, check=False,
    )
    output_root = args.output_root.resolve()
    raw = (result.stdout + result.stderr).encode()
    raw_sha = write(output_root / "apply.log", raw)
    applied = result.returncode == 0 and "RESULT:APPLIED_AND_VERIFIED" in result.stdout
    rolled_back = "ROLLBACK_BEGIN" in result.stdout and "ROLLBACK_END" in result.stdout
    def value(name: str) -> str | None:
        found = re.search(rf"^{re.escape(name)}:(.*)$", result.stdout, re.MULTILINE)
        return found.group(1).strip() if found else None
    evidence = {
        "applied_and_verified": applied,
        "automatic_rollback_observed": rolled_back,
        "candidate": {"cpus": "16-17,52-53", "memory_limit_bytes": 2147483648, "mems": "0", "memsw_limit_bytes": 2147483648},
        "database_connection_attempted": False,
        "explain_or_benchmark_started": False,
        "formal_static_promoted": False,
        "host": args.host,
        "mysql_pid": int(value("PID")) if value("PID") else 2436,
        "mysql_service": value("SERVICE"),
        "n1_pages": int(value("N1_PAGES")) if value("N1_PAGES") else None,
        "pg_modified": False,
        "raw_log": {"path": str(output_root / "apply.log"), "sha256": raw_sha},
        "remote_exit_code": result.returncode,
        "schema_version": "host04_native_mysqld_online_isolation_v1",
        "status": "APPLIED_AND_VERIFIED" if applied else ("FAILED_AND_ROLLED_BACK" if rolled_back else "PRECHECK_FAILED_NO_MUTATION"),
        "thread_count": int(value("THREAD_COUNT")) if value("THREAD_COUNT") else None,
    }
    evidence_sha = write(output_root / "evidence.json", canonical_bytes(evidence))
    rollback = (
        "# Runtime rollback\n\n"
        "Validate mysqld.service MainPID is still 2436 and /proc/2436 starttime matches apply.log. "
        "Then move every /proc/2436/task TID to /sys/fs/cgroup/cpuset/tasks, move PID 2436 to "
        "/sys/fs/cgroup/memory/system.slice/mysqld.service/cgroup.procs, and remove only "
        "ra_mysql_native_mysqld_2436_20260723 after both cgroups are empty. Do not restart mysqld or touch PostgreSQL.\n"
    ).encode()
    rollback_sha = write(output_root / "rollback.md", rollback)
    print(json.dumps({"evidence_sha256": evidence_sha, "rollback_sha256": rollback_sha, "status": evidence["status"]}, sort_keys=True))
    return 0 if applied else 1


if __name__ == "__main__":
    raise SystemExit(main())
