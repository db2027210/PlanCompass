#!/usr/bin/env python3
"""Verify JOB MySQL effective-envelope evidence and retained-copy handoff."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]; C=ROOT/'history/mysql_sgd_env_agents_20260723/job/formal_mysql_normalized_envelope_readback.json'; B=ROOT/'history/mysql_sgd_env_agents_20260723/job/job_envelope_probe_20260722T223739Z'; P=ROOT/'config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json'; R=ROOT/'config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/readiness_manifest.json'; HOST='anonymous@192.0.2.4'
class E(RuntimeError):pass
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb')as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def bundle(p):
 n=0
 for line in p.read_text().splitlines():
  d,s,name=line.partition('  ');q=(p.parent/name.removeprefix('./')).resolve()
  if not s or q.parent!=p.parent.resolve()or not q.is_file()or q.is_symlink()or sha(q)!=d:raise E('bundle mismatch '+name)
  n+=1
 return n
def cpus(s):
 o=set()
 for x in s.split(','):
  a=list(map(int,x.split('-')));o.update(range(a[0],a[-1]+1))
 return o
def stable(x):
 x=x[0];h=x['HostConfig'];return(x['Id'],x['State']['Running'],h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'],h['BlkioDeviceReadBps'],h['BlkioDeviceWriteBps'],sorted((m['Source'],m['Destination'])for m in x['Mounts']))
def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--allow-unpromoted-readiness',action='store_true');a=parser.parse_args(argv)
 try:
  d=json.loads(C.read_text());side=C.with_name(C.name+'.sha256')
  if side.read_text()!=f'{sha(C)}  {C.name}\n':raise E('sidecar')
  profile=json.loads(P.read_text())['profiles']['memory']
  if d['normalized_envelope']!=profile or d['status']!='MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY':raise E('profile/status')
  readiness=json.loads(R.read_text());rside=R.with_name(R.name+'.sha256')
  if rside.read_text()!=f'{sha(R)}  {R.name}\n' or readiness.get('status')!='NOT_READY' or readiness.get('launch_authorized')is not False:raise E('readiness authorized or sidecar mismatch')
  n=bundle(B/'checksums.sha256');src=d['source_evidence'];names={'checksums_sha256':'checksums.sha256','normalized_envelope_sha256':'normalized_envelope.json','live_cgroup_readback_sha256':'live_cgroup_readback.json','mysql_inspect_running_sha256':'mysql_inspect_running.json','mysql_inspect_stopped_sha256':'mysql_inspect_stopped.json','pg_before_sha256':'pg_before.json','pg_after_sha256':'pg_after.json','summary_sha256':'summary.json'}
  for k,v in names.items():
   if sha(B/v)!=src[k]:raise E('source '+v)
  c=json.loads((B/'live_cgroup_readback.json').read_text());norm=json.loads((B/'normalized_envelope.json').read_text())
  if norm['normalized_envelope']!=profile or(c['cpu_quota_us'],c['cpu_period_us'],c['cpuset_cpus'],c['cpuset_mems'],c['memory_limit_bytes'],c['memory_memsw_limit_bytes'],c['pids_max'])!=(300000,100000,'18-20,54-56','1',6442450944,6442450944,512)or c['blkio_read_bps_lines']!=['259:3 1205862400']or c['blkio_write_bps_lines']!=['259:3 1205862400']:raise E('effective cgroup')
  run=json.loads((B/'mysql_inspect_running.json').read_text())[0];stop=json.loads((B/'mysql_inspect_stopped.json').read_text())[0];h=run['HostConfig']
  if(h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'])!=(3000000000,6442450944,6442450944,512,'18-20,54-56','1')or run['State']['Running']is not True or stop['State']['ExitCode']!=0 or stop['State']['OOMKilled']is not False or'Shutdown complete'not in(B/'mysql.log').read_text():raise E('inspect/shutdown')
  before=json.loads((B/'pg_before.json').read_text());after=json.loads((B/'pg_after.json').read_text())
  if stable(before)!=stable(after):raise E('PG stable fields changed')
  pg=before[0];ph=pg['HostConfig']
  if cpus(h['CpusetCpus'])&cpus(ph['CpusetCpus'])or h['CpusetMems']==ph['CpusetMems']or not any(m['Source'].startswith('/mnt/nvme2n1/')for m in pg['Mounts']):raise E('static nonoverlap')
  if d['static_isolation']['formal_static_isolation_gate_passed']is not False or d['static_isolation']['cross_engine_envelope_equality']is not False:raise E('formal static gate')
  remote='''set -euo pipefail
p=/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job;run=job_envelope_probe_20260722T223739Z;e=$p/evidence/$run;data=$p/.envelope_work/$run/datadir
(cd "$e"&&sha256sum -c checksums.sha256 >/dev/null); test -d "$data"; sha256sum "$e/checksums.sha256" "$e/work_copy_file_sha256.txt"
test -z "$(docker ps -a --format '{{.Names}}'|grep -E '^ra_mysql_job02_envelope_20260722T223739Z$'||true)"; test -z "$(docker network ls --format '{{.Name}}'|grep -E '^ra_mysql_job02_envelope_20260722T223739Z_net$'||true)"; test "$(docker inspect ra_pg5_job_node02_v1_a2 --format '{{.Id}}|{{.State.Running}}')" = '26ea08aa46f10b79e919e404516d882667809d3135aab9126daa0073b74183cb|true'
python3 - /mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/host02_control/job_xp_lease_journal.jsonl <<'PY'
import hashlib,json,sys
prior=None;found=[]
for line in open(sys.argv[1]):
 r=json.loads(line);u={k:v for k,v in r.items()if k!='event_sha256'};assert hashlib.sha256(json.dumps(u,sort_keys=True,separators=(',',':')).encode()).hexdigest()==r['event_sha256'];assert r['previous_event_sha256']==prior;prior=r['event_sha256']
 if r['owner_id']=='job_envelope_probe_20260722T223739Z':found.append(r['event'])
assert found[-5:]==['lock_acquired','preflight_zero_pressure_passed','handoff_acquired','recovery_checksum_scope_complete','lock_released']
PY
'''
  q=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','--',HOST,'bash','-s'],input=remote,text=True,capture_output=True)
  out=q.stdout.split()
  if q.returncode or len(out)<4 or out[0]!=src['checksums_sha256']or out[2]!=d['closure']['work_copy_file_manifest_sha256']:raise E(f'remote copy/mutex/cleanup rc={q.returncode} out={q.stdout[-500:]!r} err={q.stderr[-500:]!r}')
  item=readiness['scenarios']['job_memory'];expected={'freeze_id':d['freeze_id'],'normalized':profile,'readback':{'path':str(C.relative_to(ROOT)),'sha256':sha(C)}};projected=item.get('mysql_envelope')==expected
  if not projected and not(a.allow_unpromoted_readiness and item.get('mysql_envelope')=={'freeze_id':None,'normalized':None,'readback':None}):raise E('JOB readiness projection is neither exact nor unpromoted')
  if item.get('postgresql_envelope')!={'freeze_id':None,'normalized':None,'readback':None}or item.get('static_isolation')!={'evidence':None,'passed':False}or item.get('r0_calibration',{}).get('passed')is not False:raise E('JOB PG/static/R0 gate changed')
  print(json.dumps({'status':'PASS','bundle_members':n,'eight_effective_fields':True,'retained_work_copy_manifest_file_verified':True,'retained_work_copy_current_contents_claimed_immutable':False,'shared_mutex_chain_verified':True,'clean_shutdown_container_network_removed':True,'postgresql_unchanged':True,'observed_cpu_numa_disk_nonoverlap':True,'formal_static_gate':False,'cross_engine_equality':False,'r0':False,'sgd':False,'ready':False,'readiness_projection':projected},sort_keys=True));return 0
 except(Exception)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
