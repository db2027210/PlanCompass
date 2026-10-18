#!/usr/bin/env python3
"""Fail-closed XP template SQL row-count closure; defaults to read-only dry-run."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HOST = "anonymous@192.0.2.4"
TEMPLATE = Path("/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/template_v2")
BASE = TEMPLATE.parent
LOCK = Path("/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock")
OLD_CONTAINER = "ra_mysql_xp02_template"
PG_CONTAINER = "ra_pg5_job_node02_v1_a2"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
RUN_ID_RE = re.compile(r"xp_sql_count_[0-9]{8}_[0-9]{4}\Z")
EXPECTED_ROWS = {
    "checking": 6000000,
    "checkingAccount": 3020000,
    "company": 20000,
    "customer": 3000000,
    "loanapps": 6000000,
    "loantrans": 6000000,
    "savingAccount": 3020000,
    "transfer": 60000000,
}


class ClosureError(RuntimeError):
    pass


def _ssh(script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--",
            HOST, "bash", "-s", "--", *arguments,
        ],
        cwd=ROOT,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )


def _preflight_script() -> str:
    return r'''set -euo pipefail
old="$1"; pg="$2"; template="$3"; lock="$4"; image="$5"
fail() { printf 'PREFLIGHT_FAIL=%s\n' "$1" >&2; exit 40; }
test -d "$template/datadir" || fail template_datadir_missing
test ! -L "$template" || fail template_is_symlink
test "$(stat -c %a "$template")" = 555 || fail template_root_not_0555
test "$(sha256sum "$template/database_manifest.json" | awk '{print $1}')" = fd7c9918890fe6eea33f900da05a8d2825e204bc3f3cbf90505ddc43b98b52fa || fail manifest_sha_mismatch
test "$(docker inspect "$old" --format '{{.State.Running}}')" = true || fail old_container_not_running
test "$(docker inspect "$old" --format '{{.Image}}')" = "$image" || fail old_image_mismatch
test "$(docker inspect "$old" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Source}}{{end}}{{end}}')" = "${template%/template_v2}/template" || fail old_mount_not_project_template
test "$(docker inspect "$old" --format '{{.HostConfig.CpusetCpus}}')" = 18-33,54-69 || fail old_cpuset_mismatch
test "$(docker inspect "$old" --format '{{.HostConfig.CpusetMems}}')" = 1 || fail old_numa_mismatch
docker exec "$old" test -s /root/.my.cnf || fail old_private_defaults_missing
active="$(docker exec "$old" mysql --defaults-extra-file=/root/.my.cnf -Nse "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE ID <> CONNECTION_ID() AND COMMAND NOT IN ('Sleep','Daemon')" 2>/dev/null)"
test "$active" = 0 || fail old_container_not_idle
job_containers="$(docker ps --format '{{.Names}}' | grep -Ec '(mysql.*job|job.*mysql|outerpoint|outercontrol)' || true)"
job_processes="$(ps -eo args= | grep -E 'outerpoint|outercontrol|BenchBase|benchmarksql' | grep -Ev 'grep|bash -s' | wc -l || true)"
test "$job_containers" = 0 || fail job_container_pressure_nonzero
test "$job_processes" = 0 || fail job_process_pressure_nonzero
test "$(docker inspect "$pg" --format '{{.State.Running}}')" = true || fail pg_not_running
test "$(docker inspect "$pg" --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}')" = healthy || fail pg_not_healthy
test "$(docker inspect "$pg" --format '{{.HostConfig.CpusetCpus}}')" = 12-17,48-53 || fail pg_cpuset_unexpected
test "$(docker inspect "$pg" --format '{{.HostConfig.CpusetMems}}')" = 0 || fail pg_numa_unexpected
pg_mounts="$(docker inspect "$pg" --format '{{range .Mounts}}{{println .Source}}{{end}}')"
printf '%s\n' "$pg_mounts" | grep -q '^/mnt/nvme2n1/' || fail pg_disk_unexpected
test ! -L "$lock" || fail lease_lock_symlink
printf '{"status":"DRY_RUN_PREFLIGHT_OK","mutations":false,"job_pressure_count":0,"old_xp_idle":true,"old_xp_project_owned":true,"template_v2_read_only":true,"pg_untouched":true,"pg_cpuset":"12-17,48-53","pg_mems":"0","pg_disk":"/mnt/nvme2n1","planned_mysql_cpuset":"18-33,54-69","planned_mysql_mems":"1","planned_mysql_data_partition":"/dev/nvme0n1p1","planned_mysql_throttle_parent":"/dev/nvme0n1"}\n'
'''


def _execute_script() -> str:
    rows = " ".join(f"{name}:{count}" for name, count in EXPECTED_ROWS.items())
    sql = " UNION ALL ".join(
        f"SELECT '{name}',COUNT(*) FROM hybench_sf10x.{name}"
        for name in EXPECTED_ROWS
    )
    return rf'''set -euo pipefail
old="$1"; pg="$2"; template="$3"; lock="$4"; image="$5"; run_id="$6"
base="{BASE}"; run="$base/$run_id"; work="$run/work"; evidence="$run/evidence"
container="ra_mysql_xp02_${{run_id}}"; network="${{container}}_net"
old_stopped=0; temp_created=0; network_created=0; clean_shutdown=0; rc=0
fail() {{ printf 'EXECUTE_FAIL=%s\n' "$1" >&2; exit 41; }}
test ! -e "$run" && test ! -L "$run" || fail run_path_exists
mkdir -p "$evidence"
mkdir -p "${{lock%/*}}"
exec 9>"$lock"
flock -n 9 || fail host02_lane_busy
printf '{{"event":"lock_acquired","at":"%s","scenario":"hybench_xp_io","lock":"%s"}}\n' "$(date --iso-8601=seconds)" "$lock" >> "$evidence/lease_events.jsonl"
cleanup() {{
  trap - EXIT INT TERM HUP
  set +e
  if test "$temp_created" = 1; then
    if test "$(docker inspect "$container" --format '{{{{.State.Running}}}}' 2>/dev/null)" = true; then
      docker stop --time 30 "$container" >>"$evidence/recovery.log" 2>&1
    fi
    docker rm "$container" >>"$evidence/recovery.log" 2>&1
  fi
  if test "$network_created" = 1; then docker network rm "$network" >>"$evidence/recovery.log" 2>&1; fi
  if test "$old_stopped" = 1; then
    docker start "$old" >>"$evidence/recovery.log" 2>&1
    for i in $(seq 1 90); do
      docker exec "$old" mysqladmin --defaults-extra-file=/root/.my.cnf ping --silent >/dev/null 2>&1 && break
      sleep 1
    done
    printf 'old_restored_running=%s\n' "$(docker inspect "$old" --format '{{{{.State.Running}}}}')" >> "$evidence/recovery.log"
  fi
  printf '{{"event":"lock_released","at":"%s","scenario":"hybench_xp_io","result_rc":%s}}\n' "$(date --iso-8601=seconds)" "$rc" >> "$evidence/lease_events.jsonl"
  (cd "$evidence" && find . -maxdepth 1 -type f ! -name checksums.sha256 -printf '%P\n' | LC_ALL=C sort | xargs -r sha256sum > checksums.sha256)
  flock -u 9
  exit "$rc"
}}
trap 'rc=$?; cleanup' EXIT INT TERM HUP

# Repeat every read-only gate while holding the atomic JOB/XP lane lease.
test "$(docker inspect "$old" --format '{{{{.State.Running}}}}')" = true || fail old_container_not_running
test "$(docker inspect "$old" --format '{{{{.Image}}}}')" = "$image" || fail old_image_mismatch
test "$(docker inspect "$old" --format '{{{{range .Mounts}}}}{{{{if eq .Destination "/var/lib/mysql"}}}}{{{{.Source}}}}{{{{end}}}}{{{{end}}}}')" = "${{template%/template_v2}}/template" || fail old_mount_not_project_template
test "$(docker exec "$old" mysql --defaults-extra-file=/root/.my.cnf -Nse "SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE ID <> CONNECTION_ID() AND COMMAND NOT IN ('Sleep','Daemon')" 2>/dev/null)" = 0 || fail old_not_idle
test "$(docker ps --format '{{{{.Names}}}}' | grep -Ec '(mysql.*job|job.*mysql|outerpoint|outercontrol)' || true)" = 0 || fail job_container_pressure_nonzero
test "$(ps -eo args= | grep -E 'outerpoint|outercontrol|BenchBase|benchmarksql' | grep -Ev 'grep|bash -s' | wc -l || true)" = 0 || fail job_process_pressure_nonzero
test "$(docker inspect "$pg" --format '{{{{.State.Running}}}}')" = true || fail pg_not_running
test "$(docker inspect "$pg" --format '{{{{.HostConfig.CpusetCpus}}}}')" = 12-17,48-53 || fail pg_cpuset_changed
test "$(docker inspect "$pg" --format '{{{{.HostConfig.CpusetMems}}}}')" = 0 || fail pg_numa_changed
docker inspect "$old" > "$evidence/old_xp_before.json"
docker inspect "$pg" > "$evidence/pg_read_only_before.json"
docker stats --no-stream --format '{{{{json .}}}}' "$old" "$pg" > "$evidence/stats_before.jsonl"
stat -c '%A|%a|%U:%G|%s|%n' "$template" "$template/database_manifest.json" > "$evidence/template_v2_before.txt"
sha256sum "$template/database_manifest.json" "$template/database_manifest.json.sha256" >> "$evidence/template_v2_before.txt"
docker stop --time 60 "$old" > "$evidence/old_xp_stop.txt"
old_stopped=1
test "$(docker inspect "$old" --format '{{{{.State.Running}}}}')" = false || fail old_stop_failed

mkdir -p "$work/datadir"
cp -a --reflink=auto "$template/datadir/." "$work/datadir/"
docker run --rm --network none --cpuset-cpus 18 --cpuset-mems 1 --cpus 0.25 \
  --memory 134217728 --memory-swap 134217728 --pids-limit 64 \
  --entrypoint sh -v "$work/datadir:/work" "$image" \
  -c 'chown -R mysql:mysql /work && find /work -type d -exec chmod 0750 {{}} + && find /work -type f -exec chmod 0640 {{}} + && du -sb /work' > "$evidence/work_copy_prepare.log" 2>&1
printf '%s\n' "$work/datadir" > "$evidence/work_copy_retained_path.txt"

docker network create --internal "$network" > "$evidence/network_create.txt"
network_created=1
docker run --detach --name "$container" --network "$network" \
  --cpuset-cpus 18-33,54-69 --cpuset-mems 1 --cpus 16 \
  --memory 8589934592 --memory-swap 8589934592 --pids-limit 1024 \
  --device-read-bps /dev/nvme0n1:104857600 \
  --device-write-bps /dev/nvme0n1:104857600 \
  --volume "$work/datadir:/var/lib/mysql" \
  --shm-size 1073741824 "$image" \
  --skip-grant-tables=ON --skip-networking=ON --read-only=ON --super-read-only=ON \
  --innodb-buffer-pool-size=6442450944 --innodb-buffer-pool-instances=8 \
  --innodb-flush-method=O_DIRECT --innodb-flush-log-at-trx-commit=1 \
  --sync-binlog=1 --innodb-doublewrite=ON --max-connections=512 \
  --transaction-isolation=READ-COMMITTED --performance-schema=ON \
  --innodb-io-capacity=100 --innodb-io-capacity-max=100 > "$evidence/container_create.txt"
temp_created=1
ready=0
for i in $(seq 1 120); do
  if docker exec "$container" mysqladmin -u root ping --silent >/dev/null 2>&1; then ready=1; break; fi
  test "$(docker inspect "$container" --format '{{{{.State.Running}}}}')" = true || break
  sleep 2
done
test "$ready" = 1 || {{ docker logs "$container" > "$evidence/mysql_start_failure.log" 2>&1; fail mysql_not_ready; }}
docker inspect "$container" > "$evidence/container_inspect_running.json"
pid="$(docker inspect "$container" --format '{{{{.State.Pid}}}}')"
{{ printf 'pid=%s\n' "$pid"; cat "/proc/$pid/cgroup"; 
  for c in cpu cpuset memory pids blkio; do
    path="$(awk -F: -v c="$c" '$2 ~ c {{print $3; exit}}' "/proc/$pid/cgroup")"
    printf '\n[%s]%s\n' "$c" "$path"
    case "$c" in
      cpu) for f in cpu.cfs_quota_us cpu.cfs_period_us; do cat "/sys/fs/cgroup/cpu${{path}}/$f"; done;;
      cpuset) for f in cpuset.cpus cpuset.mems; do cat "/sys/fs/cgroup/cpuset${{path}}/$f"; done;;
      memory) for f in memory.limit_in_bytes memory.memsw.limit_in_bytes; do cat "/sys/fs/cgroup/memory${{path}}/$f"; done;;
      pids) cat "/sys/fs/cgroup/pids${{path}}/pids.max";;
      blkio) for f in blkio.throttle.read_bps_device blkio.throttle.write_bps_device; do cat "/sys/fs/cgroup/blkio${{path}}/$f"; done;;
    esac
  done
}} > "$evidence/cgroup_readback.txt"
docker exec "$container" mysql -u root -Nse "SELECT @@version,@@global.read_only,@@global.super_read_only,@@global.skip_networking" > "$evidence/mysql_readback.tsv"
docker exec "$container" mysql -u root -Nse "{sql}" > "$evidence/sql_count_results.tsv"
python3 - "$evidence/sql_count_results.tsv" "$evidence/sql_count_verdict.json" <<'PY'
import json,sys
expected=dict(item.split(':',1) for item in "{rows}".split())
expected={{k:int(v) for k,v in expected.items()}}
actual={{}}
for line in open(sys.argv[1],encoding='utf-8'):
    key,value=line.rstrip('\n').split('\t')
    actual[key]=int(value)
passed=actual==expected and sum(actual.values())==87060000
payload={{'schema_version':1,'database':'hybench_sf10x','expected':expected,'actual':actual,'expected_total':87060000,'actual_total':sum(actual.values()),'passed':passed,'scope':'template_sql_count_closure_not_R0_not_SGD'}}
open(sys.argv[2],'w',encoding='utf-8').write(json.dumps(payload,sort_keys=True,indent=2)+'\n')
raise SystemExit(0 if passed else 42)
PY
set +e
docker exec "$container" mysqladmin -u root shutdown > "$evidence/clean_shutdown.txt" 2>&1
shutdown_client_rc=$?
set -e
for i in $(seq 1 60); do test "$(docker inspect "$container" --format '{{{{.State.Running}}}}')" = false && break; sleep 1; done
docker inspect "$container" > "$evidence/container_inspect_stopped.json"
test "$(docker inspect "$container" --format '{{{{.State.Running}}}}')" = false || fail clean_shutdown_not_observed
test "$(docker inspect "$container" --format '{{{{.State.ExitCode}}}}')" = 0 || fail mysql_exit_nonzero
test "$(docker inspect "$container" --format '{{{{.State.OOMKilled}}}}')" = false || fail mysql_oom_killed
clean_shutdown=1
printf 'mysqladmin_client_rc=%s\ncontainer_exit_code=0\noom_killed=false\n' "$shutdown_client_rc" >> "$evidence/clean_shutdown.txt"
docker rm "$container" >> "$evidence/recovery.log"; temp_created=0
docker network rm "$network" >> "$evidence/recovery.log"; network_created=0
docker start "$old" >> "$evidence/recovery.log"; old_stopped=0
for i in $(seq 1 90); do docker exec "$old" mysqladmin --defaults-extra-file=/root/.my.cnf ping --silent >/dev/null 2>&1 && break; sleep 1; done
test "$(docker inspect "$old" --format '{{{{.State.Running}}}}')" = true || fail old_restore_failed
docker inspect "$old" > "$evidence/old_xp_after.json"
docker inspect "$pg" > "$evidence/pg_read_only_after.json"
stat -c '%A|%a|%U:%G|%s|%n' "$template" "$template/database_manifest.json" > "$evidence/template_v2_after.txt"
sha256sum "$template/database_manifest.json" "$template/database_manifest.json.sha256" >> "$evidence/template_v2_after.txt"
cmp -s "$evidence/template_v2_before.txt" "$evidence/template_v2_after.txt" || fail frozen_template_changed
printf 'clean_shutdown=%s\ntemporary_container_removed=true\nnetwork_removed=true\nold_container_restored=true\ncredential_material_used=false\nauthentication_mode=isolated_skip_grant_tables_skip_networking\nwork_copy_retained=%s\npg_mutation_performed=false\nr0=false\nsgd=false\naccepted=false\n' "$clean_shutdown" "$work/datadir" >> "$evidence/recovery.log"
printf '{{"status":"SQL_ROWCOUNT_CLOSURE_PASS","run_id":"%s","total_rows":87060000,"authentication_mode":"isolated_skip_grant_tables_skip_networking","credential_material_used":false,"r0":false,"sgd":false,"accepted":false,"readiness_changed":false,"pg_mutation_performed":false,"work_copy_retained":"%s","evidence":"%s"}}\n' "$run_id" "$work/datadir" "$evidence" > "$evidence/summary.json"
'''


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id", default="xp_sql_count_" + datetime.now().strftime("%Y%m%d_%H%M"))
    parser.add_argument("--confirm-host")
    parser.add_argument("--confirm-template")
    parser.add_argument("--confirm-work-root")
    args = parser.parse_args(argv)
    if RUN_ID_RE.fullmatch(args.run_id) is None:
        print("ERROR: invalid --run-id", file=sys.stderr)
        return 2
    work_root = BASE / args.run_id
    if args.execute and (
        args.confirm_host != HOST
        or args.confirm_template != str(TEMPLATE)
        or args.confirm_work_root != str(work_root)
    ):
        print("ERROR: --execute requires exact host/template/work-root confirmations", file=sys.stderr)
        return 2
    common = (OLD_CONTAINER, PG_CONTAINER, str(TEMPLATE), str(LOCK), IMAGE_ID)
    result = _ssh(
        _execute_script() if args.execute else _preflight_script(),
        *common, *([args.run_id] if args.execute else []),
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        return result.returncode
    if not args.execute:
        return 0
    local = ROOT / "history/mysql_sgd_env_agents_20260723/xp" / args.run_id
    if local.exists() or local.is_symlink():
        print(f"ERROR: local evidence path already exists: {local}", file=sys.stderr)
        return 3
    local.mkdir(parents=True, mode=0o700)
    remote_evidence = f"{HOST}:{work_root}/evidence/"
    copied = subprocess.run(
        ["rsync", "-a", "--", remote_evidence, str(local) + "/"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    if copied.returncode != 0:
        print("ERROR: closure passed remotely but local evidence copy failed", file=sys.stderr)
        print(copied.stderr[-1000:], file=sys.stderr)
        return 4
    print(json.dumps({"local_evidence": str(local.relative_to(ROOT))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
