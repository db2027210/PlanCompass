#!/usr/bin/env python3
"""Verify the TPC-H idle MySQL envelope and fail-closed static-gate verdict."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1];C=ROOT/'history/mysql_sgd_env_agents_20260723/tpch/formal_mysql_normalized_envelope_readback.json';B=ROOT/'history/mysql_sgd_env_agents_20260723/tpch/tpch_envelope_probe_20260722T224654Z';P=ROOT/'config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json';R=ROOT/'config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/readiness_manifest.json';HOST='anonymous@192.0.2.7'
class E(RuntimeError):pass
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb')as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def bundle(p):
 n=0
 for l in p.read_text().splitlines():
  d,s,x=l.partition('  ');q=(p.parent/x.removeprefix('./')).resolve()
  if not s or q.parent!=p.parent.resolve()or not q.is_file()or q.is_symlink()or sha(q)!=d:raise E('bundle '+x)
  n+=1
 return n
def stable(x):
 x=x[0];h=x['HostConfig'];return(x['Id'],x['State']['Running'],h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'],sorted((m['Source'],m['Destination'])for m in x['Mounts']))
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--allow-unpromoted-readiness',action='store_true');a=parser.parse_args(argv)
 try:
  d=json.loads(C.read_text());side=C.with_name(C.name+'.sha256')
  if side.read_text()!=f'{sha(C)}  {C.name}\n' or d['normalized_envelope']!=json.loads(P.read_text())['profiles']['memory'] or d.get('status')!='MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY':raise E('canonical/profile/status')
  readiness=json.loads(R.read_text());rside=R.with_name(R.name+'.sha256')
  if rside.read_text()!=f'{sha(R)}  {R.name}\n' or readiness.get('status')!='NOT_READY' or readiness.get('launch_authorized')is not False:raise E('readiness')
  n=bundle(B/'checksums.sha256');src=d['source_evidence'];names={'checksums_sha256':'checksums.sha256','normalized_envelope_sha256':'normalized_envelope.json','live_cgroup_readback_sha256':'live_cgroup_readback.json','mysql_inspect_running_sha256':'mysql_inspect_running.json','mysql_inspect_stopped_sha256':'mysql_inspect_stopped.json','pg_before_sha256':'pg_before.json','pg_after_sha256':'pg_after.json','summary_sha256':'summary.json','host_driver_isolation_sha256':'host_driver_isolation.json','native_mysql_isolation_sha256':'native_mysql_isolation.json'}
  for k,v in names.items():
   if sha(B/v)!=src[k]:raise E('source '+v)
  c=json.loads((B/'live_cgroup_readback.json').read_text());e=d['normalized_envelope']
  if(c['cpu_quota_us'],c['cpu_period_us'],c['cpuset_cpus'],c['cpuset_mems'],c['memory_limit_bytes'],c['memory_memsw_limit_bytes'],c['pids_max'])!=(300000,100000,'18-20,54-56','1',6442450944,6442450944,512)or c['blkio_read_bps_lines']!=['259:1 1205862400']or c['blkio_write_bps_lines']!=['259:1 1205862400']:raise E('cgroup')
  run=json.loads((B/'mysql_inspect_running.json').read_text())[0];stop=json.loads((B/'mysql_inspect_stopped.json').read_text())[0];h=run['HostConfig']
  if(h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'])!=(3000000000,6442450944,6442450944,512,'18-20,54-56','1')or stop['State']['ExitCode']!=0 or stop['State']['OOMKilled']is not False or'Shutdown complete'not in(B/'mysql.log').read_text():raise E('inspect/shutdown')
  before=json.loads((B/'pg_before.json').read_text());after=json.loads((B/'pg_after.json').read_text())
  if stable(before)!=stable(after)or(B/'pg_before.json').read_bytes()!=(B/'pg_after.json').read_bytes():raise E('PG changed')
  driver=json.loads((B/'host_driver_isolation.json').read_text());native=json.loads((B/'native_mysql_isolation.json').read_text());s=d['static_isolation']
  if driver['affinity_isolated']is not False or native['isolated']is not False or s['formal_static_isolation_gate_passed']is not False or s['cross_engine_envelope_equality']is not False:raise E('static gate')
  remote='''set -euo pipefail;p=/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch;e=$p/evidence/tpch_envelope_probe_20260722T224654Z;data=$p/work/tpch_rowcount_verify_20260723_0206/datadir;(cd "$e"&&sha256sum -c checksums.sha256>/dev/null);sha256sum "$e/checksums.sha256";test -d "$data";test "$(realpath "$data")" != "$(realpath "$p/template")";test -z "$(docker ps -a --format '{{.Names}}'|grep -E '^ra_mysql_tpch04_envelope_20260722T224654Z$'||true)";test -z "$(docker network ls --format '{{.Name}}'|grep -E '^ra_mysql_tpch04_envelope_20260722T224654Z_net$'||true)";exec 9>"$p/work/tpch_envelope_probe.lock";flock -n 9;test "$(docker inspect pg5_tpch_s04_20260722 --format '{{.Id}}|{{.State.Running}}')" = '5bfdab1bb8e8e6d382e70b15c252c9f3b2d74ae3e0ba0a1e05532e2291b479cc|true' '''
  q=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','--',HOST,'bash','-s'],input=remote,text=True,capture_output=True)
  if q.returncode or not q.stdout.split()or q.stdout.split()[0]!=src['checksums_sha256']:raise E('remote cleanup/copy/PG')
  item=readiness['scenarios']['tpch_memory'];expected={'freeze_id':d['freeze_id'],'normalized':d['normalized_envelope'],'readback':{'path':str(C.relative_to(ROOT)),'sha256':sha(C)}};projected=item.get('mysql_envelope')==expected
  if not projected and not(a.allow_unpromoted_readiness and item.get('mysql_envelope')=={'freeze_id':None,'normalized':None,'readback':None}):raise E('TPC-H readiness projection is neither exact nor unpromoted')
  if item.get('postgresql_envelope')!={'freeze_id':None,'normalized':None,'readback':None}or item.get('static_isolation')!={'evidence':None,'passed':False}or item.get('r0_calibration',{}).get('passed')is not False:raise E('TPC-H PG/static/R0 gate changed')
  print(json.dumps({'status':'PASS','bundle_members':n,'eight_effective_fields':True,'sealed_template_started':False,'clean_shutdown_cleanup':True,'postgresql_unchanged':True,'database_cpu_numa_disk_nonoverlap':True,'host_driver_unisolated':True,'native_mysqld_unisolated':True,'formal_static_gate':False,'cross_engine_equality':False,'r0':False,'sgd':False,'ready':False,'readiness_projection':projected},sort_keys=True));return 0
 except Exception as x:print(json.dumps({'status':'FAIL','error':str(x)},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
