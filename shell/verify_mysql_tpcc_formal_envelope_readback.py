#!/usr/bin/env python3
"""Independently verify the idle TPC-C MySQL effective-envelope probe."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
import subprocess,sys

ROOT=Path(__file__).resolve().parents[1]
CANON=ROOT/'history/mysql_sgd_env_agents_20260723/tpcc/formal_mysql_normalized_envelope_readback.json'
BUNDLE=ROOT/'history/mysql_sgd_env_agents_20260723/tpcc/tpcc_envelope_probe_20260722T223216Z'
PAIRED=ROOT/'config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json'
READINESS=ROOT/'config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/readiness_manifest.json'
HOST='anonymous@192.0.2.7'; PROJECT='/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc'; RUN='tpcc_envelope_probe_20260722T223216Z'
class Error(RuntimeError):pass
def sha(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def checksums(p:Path)->int:
 n=0
 for line in p.read_text().splitlines():
  d,sep,name=line.partition('  '); child=(p.parent/name.removeprefix('./')).resolve()
  if not sep or child.parent!=p.parent.resolve() or not child.is_file() or child.is_symlink() or sha(child)!=d:raise Error(f'bad bundle member {name}')
  n+=1
 if not n:raise Error('empty bundle')
 return n
def cpus(s:str)->set[int]:
 out=set()
 for x in s.split(','):
  a=[int(v) for v in x.split('-')];out.update(range(a[0],a[-1]+1))
 return out
def main()->int:
 try:
  doc=json.loads(CANON.read_text()); side=CANON.with_name(CANON.name+'.sha256')
  if side.read_text()!=f'{sha(CANON)}  {CANON.name}\n':raise Error('canonical sidecar mismatch')
  profile=json.loads(PAIRED.read_text())['profiles']['tpcc_cpu_r0_preregistered']
  if doc['normalized_envelope']!=profile or doc['status']!='MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY':raise Error('profile/status mismatch')
  scope=doc['formal_scope']
  if scope!={'environment_readback_only':True,'r0':False,'sgd':False,'accepted_iteration':False,'readiness_changed':False,'ready':False,'launch_authorized':False}:raise Error('formal scope mismatch')
  ready=json.loads(READINESS.read_text())
  if ready.get('status')!='NOT_READY' or ready.get('launch_authorized') is not False:raise Error('readiness authorized')
  count=checksums(BUNDLE/'checksums.sha256'); src=doc['source_evidence']
  mapping={'checksums_sha256':'checksums.sha256','normalized_envelope_sha256':'normalized_envelope.json','live_cgroup_readback_sha256':'live_cgroup_readback.json','mysql_inspect_running_sha256':'mysql_inspect_running.json','mysql_inspect_stopped_sha256':'mysql_inspect_stopped.json','pg_before_sha256':'pg_before.json','pg_after_sha256':'pg_after.json','summary_sha256':'summary.json','native_mysql_idle_sha256':'native_mysql_idle_before.json'}
  for key,name in mapping.items():
   if sha(BUNDLE/name)!=src[key]:raise Error(f'source mismatch {name}')
  c=json.loads((BUNDLE/'live_cgroup_readback.json').read_text()); norm=json.loads((BUNDLE/'normalized_envelope.json').read_text())
  if norm['normalized_envelope']!=profile or norm['status']!='PASS':raise Error('normalized source mismatch')
  if (c['cpu_quota_us'],c['cpu_period_us'],c['cpuset_cpus'],c['cpuset_mems'],c['memory_limit_bytes'],c['memory_memsw_limit_bytes'],c['pids_max'])!=(400000,100000,'21-24,57-60','1',8589934592,8589934592,512):raise Error('effective non-IO fields mismatch')
  if c['blkio_read_bps_lines']!=['259:2 1205862400'] or c['blkio_write_bps_lines']!=['259:2 1205862400']:raise Error('effective IO mismatch')
  running=json.loads((BUNDLE/'mysql_inspect_running.json').read_text())[0]; stopped=json.loads((BUNDLE/'mysql_inspect_stopped.json').read_text())[0]
  h=running['HostConfig']; expected=(4000000000,8589934592,8589934592,512,'21-24,57-60','1')
  if (h['NanoCpus'],h['Memory'],h['MemorySwap'],h['PidsLimit'],h['CpusetCpus'],h['CpusetMems'])!=expected or running['State']['Running'] is not True:raise Error('running inspect mismatch')
  if stopped['State']['Running'] is not False or stopped['State']['ExitCode']!=0 or stopped['State']['OOMKilled'] is not False or 'Shutdown complete' not in (BUNDLE/'mysql.log').read_text():raise Error('clean shutdown mismatch')
  if (BUNDLE/'pg_before.json').read_bytes()!=(BUNDLE/'pg_after.json').read_bytes():raise Error('PG changed')
  pg=json.loads((BUNDLE/'pg_before.json').read_text())[0]
  if cpus(h['CpusetCpus'])&cpus(pg['HostConfig']['CpusetCpus']) or h['CpusetMems']==pg['HostConfig']['CpusetMems']:raise Error('PG CPU/NUMA overlap')
  idle=json.loads((BUNDLE/'native_mysql_idle_before.json').read_text())
  if idle['pressure_conflict'] is not False or idle['cpu_ticks_delta']!=0 or idle['io_bytes_delta']!=0 or idle['established_connections']!=0:raise Error('native MySQL pressure conflict')
  remote=f'''set -euo pipefail; p={PROJECT}; e=$p/evidence/{RUN}; (cd "$e"&&sha256sum -c checksums.sha256 >/dev/null); sha256sum "$e/checksums.sha256"; test ! -e "$p/locks/host04_mysql_lane.owner.json"; exec 9>"$p/locks/host04_mysql_lane.lock"; flock -n 9; test ! -e "$p/.envelope_work/{RUN}"; test -z "$(docker ps -a --format '{{{{.Names}}}}'|grep -E '^ra_mysql_tpcc04_envelope_20260722T223216Z$'||true)"; test -z "$(docker network ls --format '{{{{.Name}}}}'|grep -E '^ra_mysql_tpcc04_envelope_20260722T223216Z_net$'||true)"; test "$(docker inspect pg5_tpch_s04_20260722 --format '{{{{.Id}}}}|{{{{.State.Running}}}}')" = '5bfdab1bb8e8e6d382e70b15c252c9f3b2d74ae3e0ba0a1e05532e2291b479cc|true' '''
  r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','--',HOST,'bash','-s'],input=remote,text=True,capture_output=True)
  if r.returncode or not r.stdout.split() or r.stdout.split()[0]!=src['checksums_sha256']:raise Error('remote copy/cleanup/PG verification failed')
  if doc['static_isolation']['formal_static_isolation_gate_passed'] is not False or doc['static_isolation']['cross_engine_envelope_equality'] is not False:raise Error('static gate must remain false')
  print(json.dumps({'status':'PASS','bundle_members':count,'eight_effective_fields':True,'local_remote_evidence_identical':True,'clean_shutdown_cleanup':True,'postgresql_unchanged':True,'observed_cpu_numa_disk_nonoverlap':True,'formal_static_gate':False,'r0':False,'sgd':False,'ready':False},sort_keys=True));return 0
 except (OSError,KeyError,ValueError,json.JSONDecodeError,Error) as e:
  print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
