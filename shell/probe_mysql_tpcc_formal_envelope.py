#!/usr/bin/env python3
"""Default-dry-run idle probe for the effective TPC-C MySQL envelope."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re, subprocess, sys

ROOT=Path(__file__).resolve().parents[1]
HOST="anonymous@192.0.2.7"
PROJECT="/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc"
IMAGE="sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
RUN_RE=re.compile(r"tpcc_envelope_probe_[0-9]{8}T[0-9]{6}Z\Z")

def ssh(script:str,*args:str)->subprocess.CompletedProcess[str]:
 return subprocess.run(["ssh","-o","BatchMode=yes","-o","ConnectTimeout=10","--",HOST,"bash","-s","--",*args],cwd=ROOT,input=script,text=True,capture_output=True,check=False)

DRY=r'''set -euo pipefail
p="$1"; image="$2"
test "$(hostname)" = node04; test -d "$p/template/datadir"
(cd "$p/template" && sha256sum -c template_manifest.json.sha256 >/dev/null)
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"
test "$(docker inspect pg5_tpch_s04_20260722 --format '{{.State.Running}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'true|0-15,36-51|0'
test -z "$(docker ps -a --format '{{.Names}}'|grep -E '^ra_mysql_tpcc04_envelope_'||true)"
printf '{"status":"DRY_RUN_OK","mutations":false,"r0":false,"sgd":false,"ready":false}\n'
'''

EXEC=r'''set -Eeuo pipefail
umask 077
p="$1"; image="$2"; run="$3"; template="$p/template"; wr="$p/.envelope_work"; work="$wr/$run"; data="$work/datadir"; ev="$p/evidence/$run"
lock="$p/locks/host04_mysql_lane.lock"; owner="$p/locks/host04_mysql_lane.owner.json"; name="ra_mysql_tpcc04_envelope_${run#tpcc_envelope_probe_}"; net="${name}_net"; secret="$p/secrets/mysql_root_password"
cid=""; success=0
remove_work(){ test "$(dirname "$work")" = "$wr"; if test -d "$work"; then docker run --rm --network none --cpuset-cpus 25-28,61-64 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$work:/cleanup:rw" --entrypoint /bin/sh "$image" -c 'chmod -R u+rwX /cleanup 2>/dev/null||true; find /cleanup -mindepth 1 -delete' >/dev/null 2>&1||true; rmdir "$work" 2>/dev/null||true; fi; }
cleanup(){ rc=$?; trap - EXIT HUP INT TERM; if test -n "$cid"&&docker inspect "$cid" >/dev/null 2>&1; then docker stop --time 120 "$cid" >/dev/null 2>&1||true; docker logs "$cid" >"$ev/mysql_failure_or_final.log" 2>&1||true; docker rm -f "$cid" >/dev/null 2>&1||true; fi; docker network rm "$net" >/dev/null 2>&1||true; remove_work; printf '{"event":"cleanup_complete","rc":%s,"container_absent":true,"network_absent":true,"work_copy_retained":false}\n' "$rc" >>"$ev/events.jsonl"||true; rm -f "$owner"; flock -u 9||true; if test "$success" = 1; then (cd "$ev"&&find . -type f ! -name checksums.sha256 -print0|sort -z|xargs -0 sha256sum >checksums.sha256)||rc=1; fi; exit "$rc"; }
mkdir -p "$p/locks" "$wr" "$p/evidence"; chmod 0700 "$wr"; test ! -e "$ev"; mkdir "$ev"; chmod 0700 "$ev"; trap cleanup EXIT HUP INT TERM
exec 9>"$lock"; flock -n 9; test ! -e "$owner"; printf '{"run_id":"%s","pid":%s,"scope":"idle_envelope_probe"}\n' "$run" "$$" >"$owner.partial.$$"; chmod 0600 "$owner.partial.$$"; mv -T "$owner.partial.$$" "$owner"
test "$(hostname)" = node04; ! pgrep -x gaussdb >/dev/null
test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_'||true)"; ! pgrep -x sysbench >/dev/null; ! pgrep -x mysqlslap >/dev/null; ! pgrep -x java >/dev/null
test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"; test -d "$template/datadir"; test -f "$secret"; test ! -L "$secret"
(cd "$template"&&sha256sum -c template_manifest.json.sha256 >/dev/null)
docker inspect pg5_tpch_s04_20260722 >"$ev/pg_before.json"; test "$(docker inspect pg5_tpch_s04_20260722 --format '{{.State.Running}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'true|0-15,36-51|0'; docker inspect pg5_tpch_s04_20260722 --format '{{range .Mounts}}{{println .Source}}{{end}}'|grep -q '^/mnt/nvme2n1/'
native_pid=$(pgrep -x mysqld||true); test "$(printf '%s\n' "$native_pid"|sed '/^$/d'|wc -l)" -le 1
if test -n "$native_pid"; then
  test -z "$(sudo -n ss -Htnp state established 2>/dev/null|grep -F 'pid='"$native_pid"','||true)"
  sudo -n python3 - "$native_pid" >"$ev/native_mysql_idle_before.json" <<'PY'
import json,os,pathlib,sys,time
p=int(sys.argv[1]); hz=os.sysconf('SC_CLK_TCK')
def snap():
 s=pathlib.Path(f'/proc/{p}/stat').read_text().split(); io={k:int(v) for k,v in (x.split(': ') for x in pathlib.Path(f'/proc/{p}/io').read_text().splitlines())}; return int(s[13])+int(s[14]),io['read_bytes']+io['write_bytes']
a=snap(); time.sleep(2); b=snap(); dt=b[0]-a[0]; dio=b[1]-a[1]
assert dt <= hz//5 and dio <= 1048576
st=pathlib.Path(f'/proc/{p}/status').read_text(); fields={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in st.splitlines() if ':' in x}
print(json.dumps({'pid':p,'sample_seconds':2,'cpu_ticks_delta':dt,'io_bytes_delta':dio,'cpus_allowed_list':fields['Cpus_allowed_list'],'mems_allowed_list':fields['Mems_allowed_list'],'established_connections':0,'pressure_conflict':False},sort_keys=True))
PY
fi
printf '{"event":"admission_pass"}\n' >"$ev/events.jsonl"
mkdir "$work"; sudo -n cp -a --reflink=auto "$template/datadir" "$data"
docker run --rm --network none --cpuset-cpus 25-28,61-64 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$data:/copy:rw" --entrypoint /bin/sh "$image" -c 'chmod -R u+rwX /copy'
docker network create --internal "$net" >"$ev/network_create.txt"
cid=$(docker run -d --name "$name" --network "$net" --cpuset-cpus 21-24,57-60 --cpuset-mems 1 --cpus 4 --memory 8589934592 --memory-swap 8589934592 --pids-limit 512 -e MYSQL_ROOT_PASSWORD_FILE=/run/secrets/root_password -v "$secret:/run/secrets/root_password:ro" -v "$data:/var/lib/mysql:rw" "$image" --innodb-flush-method=O_DIRECT --skip-log-bin --max-connections=64)
cg="/sys/fs/cgroup/blkio/docker/$cid"; test -d "$cg"; printf '259:2 1205862400\n'|sudo -n tee "$cg/blkio.throttle.read_bps_device" >/dev/null; printf '259:2 1205862400\n'|sudo -n tee "$cg/blkio.throttle.write_bps_device" >/dev/null
docker inspect "$cid" >"$ev/mysql_inspect_running.json"; pid=$(docker inspect "$cid" --format '{{.State.Pid}}'); test "$pid" -gt 1
ready=0
for _ in $(seq 1 120); do if docker exec "$cid" sh -c 'umask 077; { printf "[client]\nuser=root\npassword="CHANGE_ME"\n"; } >/tmp/root.cnf; mysql --defaults-extra-file=/tmp/root.cnf -N -B -e "SELECT 1"' >/dev/null 2>&1; then ready=1; break; fi; sleep 1; done
test "$ready" = 1
python3 - "$pid" "$cid" >"$ev/live_cgroup_readback.json" <<'PY'
import json,pathlib,sys
pid=int(sys.argv[1]); cid=sys.argv[2]; raw=pathlib.Path(f'/proc/{pid}/cgroup').read_text(); groups={}
for line in raw.splitlines():
 _,cs,rel=line.split(':',2)
 for c in cs.split(','): groups[c]=rel
def val(c,n): return (pathlib.Path('/sys/fs/cgroup')/c/groups[c].lstrip('/')/n).read_text().strip()
assert cid in raw
d={'container_id':cid,'pid':pid,'cpu_quota_us':int(val('cpu','cpu.cfs_quota_us')),'cpu_period_us':int(val('cpu','cpu.cfs_period_us')),'cpuset_cpus':val('cpuset','cpuset.cpus'),'cpuset_mems':val('cpuset','cpuset.mems'),'memory_limit_bytes':int(val('memory','memory.limit_in_bytes')),'memory_memsw_limit_bytes':int(val('memory','memory.memsw.limit_in_bytes')),'pids_max':int(val('pids','pids.max')),'blkio_read_bps_lines':val('blkio','blkio.throttle.read_bps_device').splitlines(),'blkio_write_bps_lines':val('blkio','blkio.throttle.write_bps_device').splitlines()}
print(json.dumps(d,sort_keys=True))
PY
python3 - "$ev/live_cgroup_readback.json" "$ev/mysql_inspect_running.json" >"$ev/normalized_envelope.json" <<'PY'
import json,sys
c=json.load(open(sys.argv[1])); h=json.load(open(sys.argv[2]))[0]['HostConfig']; e={'cpu_quota_cores':4,'memory_bytes':8589934592,'memory_swap_bytes':8589934592,'io_read_bps':1205862400,'io_write_bps':1205862400,'pids_max':512,'cpu_isolation_degree':4,'numa_isolation_degree':1}
assert c['cpu_quota_us']/c['cpu_period_us']==4 and c['cpuset_cpus']=='21-24,57-60' and c['cpuset_mems']=='1'; assert c['memory_limit_bytes']==c['memory_memsw_limit_bytes']==8589934592 and c['pids_max']==512; assert '259:2 1205862400' in c['blkio_read_bps_lines'] and '259:2 1205862400' in c['blkio_write_bps_lines']
assert (h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'])==(4000000000,8589934592,8589934592,512,'21-24,57-60','1')
print(json.dumps({'status':'PASS','normalized_envelope':e,'requested_effective_equal_for_six_docker_fields':True,'io_application_method':'explicit_cgroup_v1_write_and_readback','docker_inspect_io_arrays':{'read':h['BlkioDeviceReadBps'],'write':h['BlkioDeviceWriteBps']},'source':'live_cgroup_v1'},sort_keys=True))
PY
docker exec "$cid" mysql --defaults-extra-file=/tmp/root.cnf -N -B -e 'SELECT @@version,@@read_only,@@super_read_only' >"$ev/mysql_readback.tsv"; docker exec "$cid" rm -f /tmp/root.cnf
! pgrep -x sysbench >/dev/null; ! pgrep -x mysqlslap >/dev/null; ! pgrep -x java >/dev/null; printf '{"event":"effective_readback_complete"}\n' >>"$ev/events.jsonl"
docker stop --time 120 "$cid" >/dev/null; docker inspect "$cid" >"$ev/mysql_inspect_stopped.json"; docker logs "$cid" >"$ev/mysql.log" 2>&1; grep -F 'Shutdown complete' "$ev/mysql.log" >/dev/null; docker rm "$cid" >/dev/null; cid=""; docker network rm "$net" >"$ev/network_remove.txt"
docker inspect pg5_tpch_s04_20260722 >"$ev/pg_after.json"
python3 - "$ev/pg_before.json" "$ev/pg_after.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))[0]; b=json.load(open(sys.argv[2]))[0]; assert a['Id']==b['Id'] and b['State']['Running'] is True
for k in ('NanoCpus','Memory','MemorySwap','PidsLimit','CpusetCpus','CpusetMems','BlkioDeviceReadBps','BlkioDeviceWriteBps'): assert a['HostConfig'][k]==b['HostConfig'][k]
assert sorted((m['Source'],m['Destination']) for m in a['Mounts'])==sorted((m['Source'],m['Destination']) for m in b['Mounts'])
PY
printf '{"status":"PASS","scope":"TPC-C_MYSQL_EFFECTIVE_ENVELOPE_ONLY","work_copy_retained":false,"r0":false,"sgd":false,"ready":false,"formal_static_gate":false,"reason":"co-resident PostgreSQL is TPCH runtime and does not match TPC-C paired profile"}\n' >"$ev/summary.json"; success=1
'''

def main()->int:
 p=argparse.ArgumentParser(description=__doc__); p.add_argument('--execute',action='store_true'); p.add_argument('--run-id'); p.add_argument('--confirm-host'); p.add_argument('--confirm-project'); a=p.parse_args()
 run=a.run_id or datetime.now(timezone.utc).strftime('tpcc_envelope_probe_%Y%m%dT%H%M%SZ')
 if not RUN_RE.fullmatch(run): print('ERROR: invalid run id',file=sys.stderr); return 2
 if a.execute and (a.confirm_host!=HOST or a.confirm_project!=PROJECT): print('ERROR: exact confirmations required',file=sys.stderr); return 2
 r=ssh(EXEC if a.execute else DRY,PROJECT,IMAGE,*([run] if a.execute else [])); print(r.stdout,end=''); print(r.stderr,end='',file=sys.stderr)
 if r.returncode:return r.returncode
 if a.execute:
  dst=ROOT/'history/mysql_sgd_env_agents_20260723/tpcc'/run
  if dst.exists(): return 3
  dst.mkdir(parents=True,mode=0o700); q=subprocess.run(['rsync','-a','--',f'{HOST}:{PROJECT}/evidence/{run}/',str(dst)+'/'],cwd=ROOT,check=False)
  if q.returncode:return 4
  print(f'LOCAL_EVIDENCE={dst.relative_to(ROOT)}')
 return 0
if __name__=='__main__': raise SystemExit(main())
