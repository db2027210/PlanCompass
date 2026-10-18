#!/usr/bin/env python3
"""Default-dry-run idle probe for the AP MySQL effective envelope."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HOST = "anonymous@192.0.2.3"
PROJECT = "/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap"
DATADIR = f"{PROJECT}/production_binding_trace_20260723_0630/datadir"
SEALED = f"{PROJECT}/mysql_template_data"
IMAGE = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
RUN_RE = re.compile(r"ap_envelope_probe_[0-9]{8}T[0-9]{6}Z\Z")


def ssh(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", HOST,
         "bash", "-s", "--", *args],
        cwd=ROOT, input=script, text=True, capture_output=True, check=False,
    )


DRY = r'''set -euo pipefail
p="$1"; data="$2"; sealed="$3"; image="$4"
test "$(hostname)" = node01; test -d "$data"; test -d "$sealed"
test "$(realpath "$data")" != "$(realpath "$sealed")"
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_ap01_envelope_'||true)"
python3 - "$data" <<'PY'
import json,subprocess,sys
p=sys.argv[1]; ids=subprocess.run(['docker','ps','-aq'],universal_newlines=True,stdout=subprocess.PIPE,check=True).stdout.split()
for cid in ids:
 d=json.loads(subprocess.run(['docker','inspect',cid],universal_newlines=True,stdout=subprocess.PIPE,check=True).stdout)[0]
 if d['State']['Running'] and any(m['Source']==p for m in d['Mounts']): raise SystemExit('session datadir already mounted')
PY
printf '{"status":"DRY_RUN_OK","mutations":false,"session_owned_datadir":true,"sealed_template_started":false,"r0":false,"static_gate":false,"ready":false}\n'
'''


EXEC = r'''set -Eeuo pipefail
umask 077
p="$1"; data="$2"; sealed="$3"; image="$4"; run="$5"; ev="$p/envelope_evidence/$run"
lock="$p/locks/host01_mysql_lane.lock"; owner="$p/locks/host01_mysql_lane.owner.json"
name="ra_mysql_ap01_envelope_${run#ap_envelope_probe_}"; net="${name}_net"; cid=""; success=0
cleanup(){ rc=$?; trap - EXIT HUP INT TERM; if test -n "$cid"&&docker inspect "$cid" >/dev/null 2>&1; then docker stop --time 120 "$cid" >/dev/null 2>&1||true; docker logs "$cid" >"$ev/mysql_failure_or_final.log" 2>&1||true; docker rm -f "$cid" >/dev/null 2>&1||true; fi; docker network rm "$net" >/dev/null 2>&1||true; printf '{"event":"cleanup_complete","rc":%s,"container_absent":true,"network_absent":true,"session_datadir_retained":true,"sealed_template_started":false}\n' "$rc" >>"$ev/events.jsonl"||true; rm -f "$owner"; flock -u 9||true; if test "$success" = 1; then (cd "$ev"&&find . -type f ! -name checksums.sha256 -print0|sort -z|xargs -0 sha256sum >checksums.sha256)||rc=1; fi; exit "$rc"; }
mkdir -p "$p/locks" "$p/envelope_evidence"; test ! -e "$ev"; mkdir "$ev"; chmod 0700 "$ev"; trap cleanup EXIT HUP INT TERM
exec 9>"$lock"; flock -n 9; test ! -e "$owner"; printf '{"run_id":"%s","pid":%s,"scope":"ap_idle_envelope_probe"}\n' "$run" "$$" >"$owner.partial.$$"; chmod 0600 "$owner.partial.$$"; mv -T "$owner.partial.$$" "$owner"
test "$(hostname)" = node01; test -d "$data"; test -d "$sealed"; test "$(realpath "$data")" != "$(realpath "$sealed")"
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"; test -f /tmp/mysql_template.cnf
test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_'||true)"; ! pgrep -x sysbench >/dev/null; ! pgrep -x mysqlslap >/dev/null; ! pgrep -x java >/dev/null
python3 - "$data" <<'PY'
import json,subprocess,sys
p=sys.argv[1]; ids=subprocess.run(['docker','ps','-aq'],universal_newlines=True,stdout=subprocess.PIPE,check=True).stdout.split()
for cid in ids:
 d=json.loads(subprocess.run(['docker','inspect',cid],universal_newlines=True,stdout=subprocess.PIPE,check=True).stdout)[0]
 if d['State']['Running'] and any(m['Source']==p for m in d['Mounts']): raise SystemExit('session datadir already mounted')
PY
pgrep -a postgres >"$ev/postgres_before.txt"||true; test ! -s "$ev/postgres_before.txt"
docker ps --no-trunc --format '{{json .}}'|grep -i postgres >"$ev/postgres_containers_before.jsonl"||true; test ! -s "$ev/postgres_containers_before.jsonl"
native_pid=$(pgrep -x mysqld||true); test "$(printf '%s\n' "$native_pid"|sed '/^$/d'|wc -l)" -eq 1
pidstat -d -p "$native_pid" 1 2 >"$ev/native_mysql_pidstat.txt"; native_connections=$(netstat -nt 2>/dev/null|awk 'NR>2&&($4~/:3306$/||$5~/:3306$/)&&$6=="ESTABLISHED"{n++}END{print n+0}')
python3 - "$native_pid" "$ev/native_mysql_pidstat.txt" "$native_connections" >"$ev/native_mysql_idle_before.json" <<'PY'
import json,os,pathlib,sys,time
p=int(sys.argv[1]);hz=os.sysconf('SC_CLK_TCK');stat=pathlib.Path(f'/proc/{p}/stat');a=stat.read_text().split();a=int(a[13])+int(a[14]);time.sleep(2);b=stat.read_text().split();dt=int(b[13])+int(b[14])-a
st=pathlib.Path(f'/proc/{p}/status').read_text();f={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in st.splitlines() if ':' in x};avg=[x for x in pathlib.Path(sys.argv[2]).read_text().splitlines() if x.startswith('Average:')][-1].split();rd=float(avg[3]);wr=float(avg[4]);con=int(sys.argv[3])
assert dt<=hz//5 and rd==0 and wr==0 and con==0
print(json.dumps({'pid':p,'sample_seconds':4,'cpu_ticks_delta':dt,'io_read_kib_per_second':rd,'io_write_kib_per_second':wr,'established_connections':con,'cpus_allowed_list':f['Cpus_allowed_list'],'mems_allowed_list':f['Mems_allowed_list'],'pressure_conflict':False,'isolation_complete':False},sort_keys=True))
PY
printf '{"event":"admission_pass","postgresql_process_count":0,"native_mysql_pressure_conflict":false,"native_mysql_isolation_complete":false}\n' >"$ev/events.jsonl"
docker network create --internal "$net" >"$ev/network_create.txt"
cid=$(docker run -d --name "$name" --network "$net" --cpus 3 --cpuset-cpus 24-26,60-62 --cpuset-mems 1 --memory 6442450944 --memory-swap 6442450944 --pids-limit 512 --device-read-bps /dev/nvme0n1:1205862400 --device-write-bps /dev/nvme0n1:1205862400 -v "$data:/var/lib/mysql:rw" -v /tmp/mysql_template.cnf:/etc/mysql/conf.d/template.cnf:ro -e MYSQL_ALLOW_EMPTY_PASSWORD=yes "$image" mysqld --read-only=ON --super-read-only=ON)
docker inspect "$cid" >"$ev/mysql_inspect_running.json"; pid=$(docker inspect "$cid" --format '{{.State.Pid}}'); test "$pid" -gt 1
ready=0; for _ in $(seq 1 90); do if docker exec "$cid" mysqladmin ping --silent >/dev/null 2>&1; then ready=1; break; fi; sleep 2; done; test "$ready" = 1
python3 - "$pid" "$cid" >"$ev/live_cgroup_readback.json" <<'PY'
import json,pathlib,sys
pid=int(sys.argv[1]);cid=sys.argv[2];raw=pathlib.Path(f'/proc/{pid}/cgroup').read_text();g={}
for line in raw.splitlines():
 _,cs,rel=line.split(':',2)
 for c in cs.split(','):g[c]=rel
def val(c,n):return (pathlib.Path('/sys/fs/cgroup')/c/g[c].lstrip('/')/n).read_text().strip()
d={'container_id':cid,'pid':pid,'cpu_quota_us':int(val('cpu','cpu.cfs_quota_us')),'cpu_period_us':int(val('cpu','cpu.cfs_period_us')),'cpuset_cpus':val('cpuset','cpuset.cpus'),'cpuset_mems':val('cpuset','cpuset.mems'),'memory_limit_bytes':int(val('memory','memory.limit_in_bytes')),'memory_memsw_limit_bytes':int(val('memory','memory.memsw.limit_in_bytes')),'pids_max':int(val('pids','pids.max')),'blkio_read_bps_lines':val('blkio','blkio.throttle.read_bps_device').splitlines(),'blkio_write_bps_lines':val('blkio','blkio.throttle.write_bps_device').splitlines()}
print(json.dumps(d,sort_keys=True))
PY
python3 - "$ev/live_cgroup_readback.json" "$ev/mysql_inspect_running.json" >"$ev/normalized_envelope.json" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]));h=json.load(open(sys.argv[2]))[0]['HostConfig'];e={'cpu_quota_cores':3,'memory_bytes':6442450944,'memory_swap_bytes':6442450944,'io_read_bps':1205862400,'io_write_bps':1205862400,'pids_max':512,'cpu_isolation_degree':3,'numa_isolation_degree':1}
assert (c['cpu_quota_us'],c['cpu_period_us'],c['cpuset_cpus'],c['cpuset_mems'],c['memory_limit_bytes'],c['memory_memsw_limit_bytes'],c['pids_max'])==(300000,100000,'24-26,60-62','1',6442450944,6442450944,512)
assert '259:1 1205862400' in c['blkio_read_bps_lines'] and '259:1 1205862400' in c['blkio_write_bps_lines']
assert (h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'])==(3000000000,6442450944,6442450944,512,'24-26,60-62','1')
print(json.dumps({'status':'PASS','normalized_envelope':e,'requested_effective_equal':True,'source':'live_cgroup_v1'},sort_keys=True))
PY
docker exec "$cid" mysql -NBe 'SELECT @@version,@@read_only,@@super_read_only' >"$ev/mysql_readback.tsv"
printf '{"event":"effective_readback_complete"}\n' >>"$ev/events.jsonl"; docker stop --time 120 "$cid" >/dev/null; docker inspect "$cid" >"$ev/mysql_inspect_stopped.json"; docker logs "$cid" >"$ev/mysql.log" 2>&1; grep -F 'Shutdown complete' "$ev/mysql.log" >/dev/null; docker rm "$cid" >/dev/null; cid=""; docker network rm "$net" >"$ev/network_remove.txt"
pgrep -a postgres >"$ev/postgres_after.txt"||true; test ! -s "$ev/postgres_after.txt"; docker ps --no-trunc --format '{{json .}}'|grep -i postgres >"$ev/postgres_containers_after.jsonl"||true; test ! -s "$ev/postgres_containers_after.jsonl"
test "$(pgrep -x mysqld)" = "$native_pid"; test -d "$data"; test -d "$sealed"; test "$(docker inspect mysql_hybench_ap_formal --format '{{.State.Running}}')" = false
printf '{"status":"PASS","scope":"AP_MYSQL_EFFECTIVE_ENVELOPE_ONLY","session_datadir_retained":true,"sealed_template_started":false,"postgresql_process_count":0,"native_mysql_pressure_conflict":false,"native_mysql_isolation_complete":false,"formal_static_gate":false,"r0":false,"sgd":false,"ready":false}\n' >"$ev/summary.json"; success=1
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--confirm-host")
    parser.add_argument("--confirm-datadir")
    args = parser.parse_args()
    run = args.run_id or datetime.now(timezone.utc).strftime("ap_envelope_probe_%Y%m%dT%H%M%SZ")
    if not RUN_RE.fullmatch(run):
        print("ERROR: invalid run id", file=sys.stderr); return 2
    if args.execute and (args.confirm_host != HOST or args.confirm_datadir != DATADIR):
        print("ERROR: exact host/datadir confirmations required", file=sys.stderr); return 2
    command = EXEC if args.execute else DRY
    extra = (run,) if args.execute else ()
    result = ssh(command, PROJECT, DATADIR, SEALED, IMAGE, *extra)
    print(result.stdout, end=""); print(result.stderr, end="", file=sys.stderr)
    if args.execute:
        destination = ROOT / "history/mysql_sgd_env_agents_20260723/ap" / run
        if destination.exists():
            return 3
        destination.mkdir(parents=True, mode=0o700)
        copy = subprocess.run(["rsync", "-a", "--", f"{HOST}:{PROJECT}/envelope_evidence/{run}/", str(destination) + "/"], cwd=ROOT, check=False)
        if copy.returncode:
            return 4
        print(f"LOCAL_EVIDENCE={destination.relative_to(ROOT)}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
