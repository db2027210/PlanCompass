#!/usr/bin/env python3
"""Independently verify canonical AP binding-trace evidence."""
from __future__ import annotations
from collections import Counter,defaultdict
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];C=ROOT/'config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/evidence/ap_binding_trace_canonical_20260723.json';BASE=ROOT/'history/mysql_sgd_env_agents_20260723/ap/candidate_binding_trace_20260723_0503';READY=ROOT/'config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft/readiness_manifest.json'
class E(RuntimeError):pass
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb')as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def cpus(s):
 out=set()
 for x in s.split(','):
  a=list(map(int,x.split('-')));out.update(range(a[0],a[-1]+1))
 return out
def main():
 try:
  d=json.loads(C.read_text());side=C.with_name(C.name+'.sha256')
  if side.read_text()!=f'{sha(C)}  {C.name}\n' or d['status']!='BINDING_TRACE_VERIFIED_NOT_READY':raise E('canonical/sidecar')
  if json.loads(READY.read_text()).get('launch_authorized')is not False:raise E('readiness authorized')
  # The bundle manifest is rooted at the repository, not at its own directory.
  manifest=BASE/'evidence_files.sha256';n=0
  for line in manifest.read_text().splitlines():
   digest,sep,name=line.partition('  ');p=(ROOT/name).resolve()
   if not sep or not p.is_file()or p.is_symlink()or sha(p)!=digest:raise E('bundle '+name)
   n+=1
  src=d['source_evidence'];trace=ROOT/src['raw_trace_path'];tm=ROOT/src['trace_manifest_path']
  if sha(manifest)!=src['bundle_checksums_sha256']or sha(trace)!=src['raw_trace_sha256']or sha(tm)!=src['trace_manifest_sha256']:raise E('source hash')
  seal=json.loads(tm.read_text());rows=[json.loads(x)for x in trace.read_text().splitlines()]
  if seal['events_sha256']!=sha(trace)or seal['event_count']!=56 or len(rows)!=56 or seal['failed_attempt_count']!=0:raise E('trace seal')
  workers={'T1','T2','T3','T4'};queries={'AP-1','AP-2','AP-2-RAND','AP-3','AP-4','AP-5','AP-6','AP-7','AP-8','AP-9','AP-10','AP-11','AP-12','AP-13'};by=defaultdict(list)
  ids=set()
  for r in rows:
   if r['schema_version']!='formal_mysql_binding_trace_event_v1'or r['run_id']!='ap_binding_trace_20260723_0503'or r['outcome']!='success':raise E('event scope/outcome')
   if len(r['template_sql_sha256'])!=64 or len(r['canonical_executable_sql_sha256'])!=64 or len(r['event_id'])!=64 or r['event_id']in ids:raise E('event identity')
   ids.add(r['event_id']);positions=[x['position']for x in r['actual_bindings']]
   if positions!=list(range(1,len(positions)+1))or any(not x.get('jdbc_type')for x in r['actual_bindings']):raise E('binding positions/types')
   if not r.get('seed_derivation')or not r.get('seed_state'):raise E('seed provenance')
   by[r['worker_or_terminal']].append(r)
  if set(by)!=workers:raise E('workers')
  for w,events in by.items():
   if {x['query_id']for x in events}!=queries or sorted(x['sequence']for x in events)!=list(range(1,15)):raise E('per-worker coverage '+w)
  result=json.loads((BASE/'execution_result.json').read_text());db=json.loads((BASE/'remote_artifacts/db_container_inspect.json').read_text())[0];client=json.loads((BASE/'remote_artifacts/client_container_inspect_initial.json').read_text())[0]
  if result['trace']['event_count']!=56 or result['formal_ready']is not False or result['r0']is not False or result['sgd']is not False:raise E('execution scope')
  dh,ch=db['HostConfig'],client['HostConfig']
  if cpus(dh['CpusetCpus'])&cpus(ch['CpusetCpus'])or(dh['NanoCpus'],dh['Memory'],dh['MemorySwap'],dh['PidsLimit'])!=(3000000000,6442450944,6442450944,512)or(ch['NanoCpus'],ch['Memory'],ch['MemorySwap'],ch['PidsLimit'])!=(3000000000,2147483648,2147483648,256):raise E('DB/client isolation')
  rec=(BASE/'remote_artifacts/remote_recovery_readback.txt').read_text()
  for x in('candidate_client_removed=yes','candidate_db_removed=yes','candidate_network_removed=yes','exact_postgres_processes=0'):
   if x not in rec:raise E('recovery '+x)
  q=d['qualification'];scope=d['formal_scope']
  if q!={'binding_evidence':True,'strict_physical_plan_evidence':False,'postgresql_counterpart_evidence':False,'formal_static_isolation_gate_passed':False,'cross_engine_envelope_equality':False}or any(scope[k]is not False for k in('r0','sgd','accepted_iteration','readiness_changed','ready','launch_authorized')):raise E('fail-closed scope')
  print(json.dumps({'status':'PASS','bundle_members':n,'binding_events':56,'workers':4,'query_ids_per_worker':14,'actual_bindings_verified':True,'trace_seal_verified':True,'db_client_cpu_isolation_verified':True,'recovery_verified':True,'strict_plan':False,'formal_static_gate':False,'r0':False,'sgd':False,'ready':False},sort_keys=True));return 0
 except Exception as x:print(json.dumps({'status':'FAIL','error':str(x)},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
