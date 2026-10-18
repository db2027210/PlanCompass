#!/usr/bin/env python3
"""Default-dry-run idle probe for the effective JOB MySQL envelope."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from pathlib import Path
import re,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]; HOST='anonymous@192.0.2.4'; PROJECT='/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job'; IMAGE='sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172'; RUN_RE=re.compile(r'job_envelope_probe_[0-9]{8}T[0-9]{6}Z\Z')
LOCK='/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_pressure.lock'; JOURNAL='/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_lease_journal.jsonl'
def ssh(script,*args):return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','--',HOST,'bash','-s','--',*args],cwd=ROOT,input=script,text=True,capture_output=True,check=False)
DRY=r'''set -euo pipefail
p="$1"; image="$2"; lock="$3"; test "$(hostname)" = node02; test -d "$p/template"; test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"; exec 9>"$lock"; flock -n 9; test "$(docker inspect ra_pg5_job_node02_v1_a2 --format '{{.State.Running}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'true|12-17,48-53|0'; test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_'||true)"; printf '{"status":"DRY_RUN_OK","shared_mutex":true,"mutations":false,"r0":false,"sgd":false,"ready":false}\n'
'''
EXEC=r'''set -Eeuo pipefail
umask 077
p="$1"; image="$2"; lock="$3"; journal="$4"; run="$5"; template="$p/template"; wr="$p/.envelope_work"; work="$wr/$run"; data="$work/datadir"; ev="$p/evidence/$run"; state="${journal%.jsonl}.state.json"; name="ra_mysql_job02_envelope_${run#job_envelope_probe_}"; net="${name}_net"; cid=""; success=0
journal_event(){ python3 - "$journal" "$state" "$run" "$lock" "$1" <<'PY'
import datetime,hashlib,json,os,pathlib,sys,tempfile
j,s,owner,lock,event=map(str,sys.argv[1:]); p=pathlib.Path(j); rows=[]; prior=None
if p.exists():
 for n,line in enumerate(p.read_text().splitlines(),1):
  if not line.strip():continue
  r=json.loads(line); unsigned={k:v for k,v in r.items() if k!='event_sha256'}; actual=hashlib.sha256(json.dumps(unsigned,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  assert r['event_sha256']==actual and r['previous_event_sha256']==prior; prior=r['event_sha256']; rows.append(r)
u={'schema_version':1,'sequence':len(rows)+1,'event':event,'timestamp':datetime.datetime.now().astimezone().isoformat(),'owner_id':owner,'pid':os.getppid(),'scenario':'job_memory','lock_path':lock,'previous_event_sha256':prior,'payload':{'scope':'idle_envelope_probe','r0':False,'sgd':False}}
r=dict(u); r['event_sha256']=hashlib.sha256(json.dumps(u,sort_keys=True,separators=(',',':')).encode()).hexdigest(); p.parent.mkdir(parents=True,exist_ok=True)
with p.open('a') as f:f.write(json.dumps(r,sort_keys=True,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
sp=pathlib.Path(s); fd,tmp=tempfile.mkstemp(prefix='.'+sp.name+'.',dir=sp.parent)
with os.fdopen(fd,'w') as f:f.write(json.dumps(r,sort_keys=True,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
os.replace(tmp,sp)
PY
}
remove_work(){ test "$(dirname "$work")" = "$wr"; if test -d "$work"; then docker run --rm --network none --cpuset-cpus 30-32,66-68 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$work:/cleanup:rw" --entrypoint /bin/sh "$image" -c 'chmod -R u+rwX /cleanup 2>/dev/null||true; find /cleanup -mindepth 1 -delete' >/dev/null 2>&1||true; rmdir "$work" 2>/dev/null||true; fi; }
cleanup(){ rc=$?; trap - EXIT HUP INT TERM; if test -n "$cid"&&docker inspect "$cid" >/dev/null 2>&1; then docker stop --time 120 "$cid" >/dev/null 2>&1||true; docker logs "$cid" >"$ev/mysql_failure_or_final.log" 2>&1||true; docker rm -f "$cid" >/dev/null 2>&1||true; fi; docker network rm "$net" >/dev/null 2>&1||true; if test "$success" != 1;then remove_work;fi; printf '{"event":"cleanup_complete","rc":%s,"container_absent":true,"network_absent":true,"work_copy_retained":%s}\n' "$rc" "$(test "$success" = 1&&echo true||echo false)" >>"$ev/events.jsonl"||true; if test "$success" = 1;then journal_event recovery_checksum_scope_complete||true;else journal_event campaign_aborted||true;fi; journal_event lock_released||true; flock -u 9||true; if test "$success" = 1; then (cd "$ev"&&find . -type f ! -name checksums.sha256 -print0|sort -z|xargs -0 sha256sum >checksums.sha256)||rc=1; fi; exit "$rc"; }
mkdir -p "$wr" "$p/evidence"; chmod 0700 "$wr"; test ! -e "$ev"; mkdir "$ev"; chmod 0700 "$ev"; trap cleanup EXIT HUP INT TERM; exec 9>"$lock"; flock -n 9; journal_event lock_acquired; journal_event preflight_zero_pressure_passed; journal_event handoff_acquired
test "$(hostname)" = node02; ! pgrep -x gaussdb >/dev/null; ! pgrep -x mysqld >/dev/null; ! pgrep -x java >/dev/null; ! pgrep -x sysbench >/dev/null; ! pgrep -x mysqlslap >/dev/null; test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_'||true)"; test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image"; test -d "$template"; test -L "$template/mysql.sock"; test "$(readlink "$template/mysql.sock")" = /var/run/mysqld/mysqld.sock; test -z "$(find "$template" -xdev \( -type f -o -type d \) -perm /0222 -print -quit)"
docker inspect ra_pg5_job_node02_v1_a2 >"$ev/pg_before.json"; test "$(docker inspect ra_pg5_job_node02_v1_a2 --format '{{.State.Running}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'true|12-17,48-53|0'; docker inspect ra_pg5_job_node02_v1_a2 --format '{{range .Mounts}}{{println .Source}}{{end}}'|grep -q '^/mnt/nvme2n1/'
printf '{"event":"admission_pass"}\n' >"$ev/events.jsonl"; mkdir "$work"; cp -a --reflink=auto "$template" "$data"; test -L "$data/mysql.sock"; test "$(readlink "$data/mysql.sock")" = /var/run/mysqld/mysqld.sock
docker run --rm --network none --cpuset-cpus 30-32,66-68 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$data:/copy:rw" --entrypoint /bin/sh "$image" -c 'test -L /copy/mysql.sock; test "$(readlink /copy/mysql.sock)" = /var/run/mysqld/mysqld.sock; rm /copy/mysql.sock; chmod -R u+rwX /copy'
docker network create --internal "$net" >"$ev/network_create.txt"; cid=$(docker run -d --name "$name" --network "$net" --cpuset-cpus 18-20,54-56 --cpuset-mems 1 --cpus 3 --memory 6442450944 --memory-swap 6442450944 --pids-limit 512 --device-read-bps /dev/nvme1n1:1150mb --device-write-bps /dev/nvme1n1:1150mb -v "$data:/var/lib/mysql:rw" "$image" --skip-grant-tables --skip-networking --innodb-flush-method=O_DIRECT --skip-log-bin --max-connections=32)
cg="/sys/fs/cgroup/blkio/docker/$cid"; test -d "$cg"
docker inspect "$cid" >"$ev/mysql_inspect_running.json"; pid=$(docker inspect "$cid" --format '{{.State.Pid}}'); test "$pid" -gt 1; ready=0; for _ in $(seq 1 180); do if docker exec "$cid" mysql -N -B -e 'SELECT 1' >/dev/null 2>&1;then ready=1;break;fi;sleep 1;done;test "$ready" = 1
python3 - "$pid" "$cid" >"$ev/live_cgroup_readback.json" <<'PY'
import json,pathlib,sys
pid=int(sys.argv[1]);cid=sys.argv[2];raw=pathlib.Path(f'/proc/{pid}/cgroup').read_text();g={}
for line in raw.splitlines():
 _,cs,rel=line.split(':',2)
 for c in cs.split(','):g[c]=rel
def v(c,n):return(pathlib.Path('/sys/fs/cgroup')/c/g[c].lstrip('/')/n).read_text().strip()
assert cid in raw;print(json.dumps({'container_id':cid,'pid':pid,'cpu_quota_us':int(v('cpu','cpu.cfs_quota_us')),'cpu_period_us':int(v('cpu','cpu.cfs_period_us')),'cpuset_cpus':v('cpuset','cpuset.cpus'),'cpuset_mems':v('cpuset','cpuset.mems'),'memory_limit_bytes':int(v('memory','memory.limit_in_bytes')),'memory_memsw_limit_bytes':int(v('memory','memory.memsw.limit_in_bytes')),'pids_max':int(v('pids','pids.max')),'blkio_read_bps_lines':v('blkio','blkio.throttle.read_bps_device').splitlines(),'blkio_write_bps_lines':v('blkio','blkio.throttle.write_bps_device').splitlines()},sort_keys=True))
PY
python3 - "$ev/live_cgroup_readback.json" "$ev/mysql_inspect_running.json" >"$ev/normalized_envelope.json" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]));h=json.load(open(sys.argv[2]))[0]['HostConfig'];e={'cpu_quota_cores':3,'memory_bytes':6442450944,'memory_swap_bytes':6442450944,'io_read_bps':1205862400,'io_write_bps':1205862400,'pids_max':512,'cpu_isolation_degree':3,'numa_isolation_degree':1}
assert(c['cpu_quota_us'],c['cpu_period_us'],c['cpuset_cpus'],c['cpuset_mems'],c['memory_limit_bytes'],c['memory_memsw_limit_bytes'],c['pids_max'])==(300000,100000,'18-20,54-56','1',6442450944,6442450944,512);assert'259:3 1205862400'in c['blkio_read_bps_lines']and'259:3 1205862400'in c['blkio_write_bps_lines'];assert(h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'])==(3000000000,6442450944,6442450944,512,'18-20,54-56','1');print(json.dumps({'status':'PASS','normalized_envelope':e,'source':'live_cgroup_v1','io_application_method':'explicit_cgroup_v1_write_and_readback'},sort_keys=True))
PY
docker exec "$cid" mysql -N -B -e 'SELECT @@version,@@read_only,@@super_read_only,COUNT(*) FROM information_schema.tables WHERE table_schema="imdb_job"' >"$ev/mysql_readback.tsv"; ! pgrep -x java >/dev/null; ! pgrep -x sysbench >/dev/null; printf '{"event":"effective_readback_complete"}\n' >>"$ev/events.jsonl"
docker stop --time 120 "$cid" >/dev/null; docker inspect "$cid" >"$ev/mysql_inspect_stopped.json"; docker logs "$cid" >"$ev/mysql.log" 2>&1; grep -F 'Shutdown complete' "$ev/mysql.log" >/dev/null; docker rm "$cid" >/dev/null;cid="";docker network rm "$net">"$ev/network_remove.txt";docker inspect ra_pg5_job_node02_v1_a2 >"$ev/pg_after.json"
python3 - "$ev/pg_before.json" "$ev/pg_after.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))[0];b=json.load(open(sys.argv[2]))[0];assert a['Id']==b['Id']and b['State']['Running']is True
for k in('NanoCpus','Memory','MemorySwap','PidsLimit','CpusetCpus','CpusetMems','BlkioDeviceReadBps','BlkioDeviceWriteBps'):assert a['HostConfig'][k]==b['HostConfig'][k]
PY
docker run --rm --network none --cpuset-cpus 30-32,66-68 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$data:/data:ro" --entrypoint /bin/sh "$image" -c 'cd /data; find . -xdev -type f -print0|sort -z|xargs -0 sha256sum' >"$ev/work_copy_file_sha256.txt"
docker run --rm --network none --cpuset-cpus 30-32,66-68 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$data:/data:ro" --entrypoint /bin/sh "$image" -c 'cd /data; find . -xdev -printf "%m %u %g %y %p\n"|sort' >"$ev/work_copy_permissions.txt"
manifest_sha=$(sha256sum "$ev/work_copy_file_sha256.txt"|cut -d' ' -f1); printf '{"status":"PASS","scope":"JOB_MYSQL_EFFECTIVE_ENVELOPE_ONLY","work_copy_retained":true,"work_copy_path":"%s","work_copy_file_manifest_sha256":"%s","r0":false,"sgd":false,"ready":false,"formal_static_gate":false,"reason":"co-resident PostgreSQL memory does not match frozen 6GiB JOB profile"}\n' "$data" "$manifest_sha">"$ev/summary.json";success=1
'''
def main():
 p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--run-id');p.add_argument('--confirm-host');p.add_argument('--confirm-project');a=p.parse_args();run=a.run_id or datetime.now(timezone.utc).strftime('job_envelope_probe_%Y%m%dT%H%M%SZ')
 if not RUN_RE.fullmatch(run):return 2
 if a.execute and(a.confirm_host!=HOST or a.confirm_project!=PROJECT):return 2
 r=ssh(EXEC if a.execute else DRY,PROJECT,IMAGE,LOCK,*([JOURNAL,run]if a.execute else []));print(r.stdout,end='');print(r.stderr,end='',file=sys.stderr)
 if r.returncode:return r.returncode
 if a.execute:
  d=ROOT/'history/mysql_sgd_env_agents_20260723/job'/run;d.mkdir(parents=True,mode=0o700);q=subprocess.run(['rsync','-a','--',f'{HOST}:{PROJECT}/evidence/{run}/',str(d)+'/']);
  if q.returncode:return 4
  print(f'LOCAL_EVIDENCE={d.relative_to(ROOT)}')
 return 0
if __name__=='__main__':raise SystemExit(main())
