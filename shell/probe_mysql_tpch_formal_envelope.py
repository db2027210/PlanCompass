#!/usr/bin/env python3
"""Default-dry-run idle envelope probe using only the stopped TPC-H work copy."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from pathlib import Path
import re,subprocess,sys
ROOT=Path(__file__).resolve().parents[1];HOST='anonymous@192.0.2.7';P='/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch';DATA=P+'/work/tpch_rowcount_verify_20260723_0206/datadir';TEMPLATE=P+'/template';IMAGE='sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172';RUN_RE=re.compile(r'tpch_envelope_probe_[0-9]{8}T[0-9]{6}Z\Z')
def ssh(s,*a):return subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','--',HOST,'bash','-s','--',*a],cwd=ROOT,input=s,text=True,capture_output=True,check=False)
DRY=r'''set -euo pipefail
p="$1";data="$2";template="$3";image="$4";test "$(hostname)" = node04;test -d "$data";test "$(realpath "$data")" != "$(realpath "$template")";test "$(docker inspect ra_mysql_tpch04_runtime --format '{{.State.Running}}|{{.State.ExitCode}}|{{range .Mounts}}{{.Source}}{{end}}')" = "false|0|$data";test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image";test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_'||true)";printf '{"status":"DRY_RUN_OK","independent_work_copy":true,"sealed_template_never_started":true,"r0":false,"sgd":false,"ready":false}\n'
'''
EXEC=r'''set -Eeuo pipefail
umask 077
p="$1";data="$2";template="$3";image="$4";run="$5";ev="$p/evidence/$run";lock="$p/work/tpch_envelope_probe.lock";name="ra_mysql_tpch04_envelope_${run#tpch_envelope_probe_}";net="${name}_net";cid="";success=0
cleanup() { rc=$?;trap - EXIT HUP INT TERM;if test -n "$cid"&&docker inspect "$cid">/dev/null 2>&1;then docker stop --time 120 "$cid">/dev/null 2>&1||true;docker logs "$cid">"$ev/mysql_failure_or_final.log" 2>&1||true;docker rm -f "$cid">/dev/null 2>&1||true;fi;docker network rm "$net">/dev/null 2>&1||true;printf '{"event":"cleanup_complete","rc":%s,"container_absent":true,"network_absent":true,"work_copy_retained":true}\n' "$rc">>"$ev/events.jsonl"||true;flock -u 9||true;if test "$success" = 1;then(cd "$ev"&&find . -type f ! -name checksums.sha256 -print0|sort -z|xargs -0 sha256sum>checksums.sha256)||rc=1;fi;exit "$rc"; }
mkdir -p "$p/evidence";test ! -e "$ev";mkdir "$ev";chmod 0700 "$ev";trap cleanup EXIT HUP INT TERM;exec 9>"$lock";flock -n 9
test "$(hostname)" = node04;test -d "$data";test "$(realpath "$data")" != "$(realpath "$template")";test "$(docker inspect ra_mysql_tpch04_runtime --format '{{.State.Running}}|{{.State.ExitCode}}|{{range .Mounts}}{{.Source}}{{end}}')" = "false|0|$data";test "$(docker image inspect "$image" --format '{{.Id}}')" = "$image";test -z "$(docker ps --format '{{.Names}}'|grep -E '^ra_mysql_'||true)";! pgrep -x java>/dev/null;! pgrep -x sysbench>/dev/null
docker inspect pg5_tpch_s04_20260722>"$ev/pg_before.json";test "$(docker inspect pg5_tpch_s04_20260722 --format '{{.State.Running}}|{{.HostConfig.CpusetCpus}}|{{.HostConfig.CpusetMems}}')" = 'true|0-15,36-51|0';docker inspect pg5_tpch_s04_20260722 --format '{{range .Mounts}}{{println .Source}}{{end}}'|grep -q '^/mnt/nvme2n1/'
driver=$(pgrep -f '/tpch_node04/.*/parameter_optimizer.py'|head -1);test -n "$driver";python3 - "$driver">"$ev/host_driver_isolation.json" <<'PY'
import json,pathlib,sys
p=int(sys.argv[1]);s=pathlib.Path(f'/proc/{p}/status').read_text();d={x.split(':',1)[0]:x.split(':',1)[1].strip()for x in s.splitlines()if':'in x};print(json.dumps({'pid':p,'cpus_allowed_list':d['Cpus_allowed_list'],'mems_allowed_list':d['Mems_allowed_list'],'affinity_isolated':False},sort_keys=True))
PY
native=$(pgrep -x mysqld||true);test -n "$native";test -z "$(sudo -n ss -Htnp state established 2>/dev/null|grep -F 'pid='"$native"','||true)";sudo -n python3 - "$native">"$ev/native_mysql_isolation.json" <<'PY'
import json,os,pathlib,sys,time
p=int(sys.argv[1]);hz=os.sysconf('SC_CLK_TCK')
def s():
 a=pathlib.Path(f'/proc/{p}/stat').read_text().split();i={k:int(v)for k,v in(x.split(': ')for x in pathlib.Path(f'/proc/{p}/io').read_text().splitlines())};return int(a[13])+int(a[14]),i['read_bytes']+i['write_bytes']
a=s();time.sleep(2);b=s();st=pathlib.Path(f'/proc/{p}/status').read_text();d={x.split(':',1)[0]:x.split(':',1)[1].strip()for x in st.splitlines()if':'in x};assert b[0]-a[0]<=hz//5 and b[1]-a[1]<=1048576;print(json.dumps({'pid':p,'cpu_ticks_delta':b[0]-a[0],'io_bytes_delta':b[1]-a[1],'established_connections':0,'cpus_allowed_list':d['Cpus_allowed_list'],'mems_allowed_list':d['Mems_allowed_list'],'isolated':False,'pressure_conflict':False},sort_keys=True))
PY
printf '{"event":"admission_pass","sealed_template_started":false}\n'>"$ev/events.jsonl";test -L "$data/mysql.sock";test "$(readlink "$data/mysql.sock")" = /var/run/mysqld/mysqld.sock
docker run --rm --network none --cpuset-cpus 25-28,61-64 --cpuset-mems 1 --cpus 1 --memory 268435456 --memory-swap 268435456 --pids-limit 64 -v "$data:/copy:rw" --entrypoint /bin/sh "$image" -c 'test -L /copy/mysql.sock;rm /copy/mysql.sock;chmod -R u+rwX /copy'
docker network create --internal "$net">"$ev/network_create.txt";cid=$(docker run -d --name "$name" --network "$net" --cpuset-cpus 18-20,54-56 --cpuset-mems 1 --cpus 3 --memory 6442450944 --memory-swap 6442450944 --pids-limit 512 -v "$data:/var/lib/mysql:rw" "$image" --skip-grant-tables --skip-networking --innodb-flush-method=O_DIRECT --skip-log-bin --max-connections=32)
cg="/sys/fs/cgroup/blkio/docker/$cid";test -d "$cg";printf '259:1 1205862400\n'|sudo -n tee "$cg/blkio.throttle.read_bps_device">/dev/null;printf '259:1 1205862400\n'|sudo -n tee "$cg/blkio.throttle.write_bps_device">/dev/null;docker inspect "$cid">"$ev/mysql_inspect_running.json";pid=$(docker inspect "$cid" --format '{{.State.Pid}}');ready=0;for _ in $(seq 1 180);do if docker exec "$cid" mysql -N -B -e 'SELECT 1'>/dev/null 2>&1;then ready=1;break;fi;sleep 1;done;test "$ready" = 1
python3 - "$pid" "$cid">"$ev/live_cgroup_readback.json" <<'PY'
import json,pathlib,sys
p=int(sys.argv[1]);cid=sys.argv[2];raw=pathlib.Path(f'/proc/{p}/cgroup').read_text();g={}
for l in raw.splitlines():
 _,cs,r=l.split(':',2)
 for c in cs.split(','):g[c]=r
def v(c,n):return(pathlib.Path('/sys/fs/cgroup')/c/g[c].lstrip('/')/n).read_text().strip()
assert cid in raw;print(json.dumps({'container_id':cid,'pid':p,'cpu_quota_us':int(v('cpu','cpu.cfs_quota_us')),'cpu_period_us':int(v('cpu','cpu.cfs_period_us')),'cpuset_cpus':v('cpuset','cpuset.cpus'),'cpuset_mems':v('cpuset','cpuset.mems'),'memory_limit_bytes':int(v('memory','memory.limit_in_bytes')),'memory_memsw_limit_bytes':int(v('memory','memory.memsw.limit_in_bytes')),'pids_max':int(v('pids','pids.max')),'blkio_read_bps_lines':v('blkio','blkio.throttle.read_bps_device').splitlines(),'blkio_write_bps_lines':v('blkio','blkio.throttle.write_bps_device').splitlines()},sort_keys=True))
PY
python3 - "$ev/live_cgroup_readback.json">"$ev/normalized_envelope.json" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]));e={'cpu_quota_cores':3,'memory_bytes':6442450944,'memory_swap_bytes':6442450944,'io_read_bps':1205862400,'io_write_bps':1205862400,'pids_max':512,'cpu_isolation_degree':3,'numa_isolation_degree':1};assert(c['cpu_quota_us'],c['cpu_period_us'],c['cpuset_cpus'],c['cpuset_mems'],c['memory_limit_bytes'],c['memory_memsw_limit_bytes'],c['pids_max'])==(300000,100000,'18-20,54-56','1',6442450944,6442450944,512);assert'259:1 1205862400'in c['blkio_read_bps_lines']and'259:1 1205862400'in c['blkio_write_bps_lines'];print(json.dumps({'status':'PASS','normalized_envelope':e,'source':'live_cgroup_v1'},sort_keys=True))
PY
docker exec "$cid" mysql -N -B -e 'SELECT @@version,@@read_only,@@super_read_only,COUNT(*) FROM information_schema.tables WHERE table_schema="tpch_sf10"'>"$ev/mysql_readback.tsv";docker stop --time 120 "$cid">/dev/null;docker inspect "$cid">"$ev/mysql_inspect_stopped.json";docker logs "$cid">"$ev/mysql.log" 2>&1;grep -F 'Shutdown complete' "$ev/mysql.log">/dev/null;docker rm "$cid">/dev/null;cid="";docker network rm "$net">"$ev/network_remove.txt";docker inspect pg5_tpch_s04_20260722>"$ev/pg_after.json"
python3 - "$ev/pg_before.json" "$ev/pg_after.json" <<'PY'
import json,sys
a=json.load(open(sys.argv[1]))[0];b=json.load(open(sys.argv[2]))[0];assert a['Id']==b['Id']and b['State']['Running']is True
for k in('NanoCpus','Memory','MemorySwap','PidsLimit','CpusetCpus','CpusetMems','BlkioDeviceReadBps','BlkioDeviceWriteBps'):assert a['HostConfig'][k]==b['HostConfig'][k]
PY
printf '{"status":"PASS","scope":"TPCH_MYSQL_EFFECTIVE_ENVELOPE_ONLY","work_copy_retained":true,"work_copy_path":"%s","sealed_template_started":false,"r0":false,"sgd":false,"ready":false,"formal_static_gate":false,"reasons":["PG profile mismatch","host driver unisolated","native mysqld unisolated"]}\n' "$data">"$ev/summary.json";success=1
'''
def main():
 a=argparse.ArgumentParser();a.add_argument('--execute',action='store_true');a.add_argument('--run-id');a.add_argument('--confirm-host');a.add_argument('--confirm-project');x=a.parse_args();run=x.run_id or datetime.now(timezone.utc).strftime('tpch_envelope_probe_%Y%m%dT%H%M%SZ')
 if not RUN_RE.fullmatch(run):return 2
 if x.execute and(x.confirm_host!=HOST or x.confirm_project!=P):return 2
 r=ssh(EXEC if x.execute else DRY,P,DATA,TEMPLATE,IMAGE,*([run]if x.execute else []));print(r.stdout,end='');print(r.stderr,end='',file=sys.stderr)
 if r.returncode:return r.returncode
 if x.execute:
  d=ROOT/'history/mysql_sgd_env_agents_20260723/tpch'/run;d.mkdir(parents=True,mode=0o700);q=subprocess.run(['rsync','-a','--',f'{HOST}:{P}/evidence/{run}/',str(d)+'/']);print(f'LOCAL_EVIDENCE={d.relative_to(ROOT)}');return q.returncode
 return 0
if __name__=='__main__':raise SystemExit(main())
