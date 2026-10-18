#!/usr/bin/env python3
"""Build the immutable AP trained-estimator T input bundle; default is dry-run."""
from __future__ import annotations
import argparse,copy,hashlib,json,os,shutil,tempfile,tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any,Mapping

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'config/formal/frozen_mysql_workloads_and_parameters_20260723_v3'
OUT=ROOT/'config/formal/frozen_mysql_workloads_and_parameters_20260723_v4_estimator_t'
REG0=OLD/'plan_workload_registries/hybench_ap.mysql_plan_workload_registry_v1.json'
SPACE0=OLD/'effective_parameter_spaces.json'; WORK0=OLD/'effective_workload_bundle.json'
RUN=ROOT/'history/mysql_sgd_env_agents_20260723/ap/production_binding_trace_20260723_0630'
TRACE=RUN/'artifacts/production_server_binding_trace.jsonl'; TOML=RUN/'stmt_mysql.toml'
EST=ROOT/'trained_model_estimator.py'; COLLECTOR=ROOT/'shell/collect_mysql_trained_estimator_endpoint_evidence.py'
MODELS={
 'cpu':Path('/home/anonymous/program/resource_adaptive/mysql_plan_models/cpu/artifacts/mysql_cpu_plan_model.joblib'),
 'memory':Path('/home/anonymous/program/resource_adaptive/mysql_plan_models/memory/artifacts/global_memory_model.joblib'),
 'io':Path('/home/anonymous/program/resource_adaptive/mysql_plan_models/io/output/mysql_total_io_plan_model.joblib')}
FREEZE='frozen_mysql_workloads_and_parameters_20260723_v4_estimator_t'; BENCH='hybench_ap'; SC='hybench_ap_memory'
ORDER=['AP-1','AP-2-RAND','AP-2']+[f'AP-{i}'for i in range(3,14)]
class E(RuntimeError):pass
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def canon(v:Any)->bytes:return(json.dumps(v,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))+'\n').encode()
def obj(p:Path)->dict[str,Any]:
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise E(f'not object: {p}')
 return v
def checked(p:Path)->dict[str,str]:
 if not p.is_file()or p.is_symlink():raise E(f'missing/unsafe: {p}')
 return{'path':str(p.resolve()),'sha256':sha(p)}
def side(p:Path)->None:
 s=p.with_name(p.name+'.sha256')
 if not s.is_file()or s.read_text()!=f'{sha(p)}  {p.name}\n':raise E(f'sidecar mismatch: {p}')
def norm(s:str)->str:
 import re
 return re.sub(r'\s+',' ',s.strip().rstrip(';')).strip()
def outputs()->dict[str,bytes]:
 side(REG0);side(TRACE)
 oldreg=obj(REG0);oldspace=obj(SPACE0);oldwork=obj(WORK0)
 if oldreg.get('freeze_id')!='frozen_mysql_workloads_and_parameters_20260723_v3'or oldreg.get('benchmark')!=BENCH or oldreg.get('immutable')is not True:raise E('old registry identity')
 if [r.get('query_id')for r in oldreg.get('queries',[])]!=ORDER:raise E('registry order')
 rows=[json.loads(x)for x in TRACE.read_text().splitlines()];by=defaultdict(list)
 for r in rows:by[r['query_id']].append(r)
 if len(rows)!=56 or set(by)!=set(ORDER)or any(len(v)!=4 for v in by.values()):raise E('56-binding authority')
 parsed=tomllib.loads(TOML.read_text());sqls={f'AP-{i}':norm(parsed[f'AP-{i}']['sql'])for i in range(1,14)};sqls['AP-2-RAND']=norm(parsed['AP-2']['1']['sql'])
 feature_rows=[]
 for rr in oldreg['queries']:
  q=rr['query_id'];sql=sqls[q]
  if hashlib.sha256(sql.encode()).hexdigest()!=rr['sql_sha256']:raise E(f'SQL hash: {q}')
  observed_by_id={}
  for event in by[q]:
   vals=[x['value']for x in event['actual_bindings']]
   bid='observed_'+hashlib.sha256(canon(vals)).hexdigest()[:16]
   observed_by_id.setdefault(bid,{'binding_id':bid,'values':vals})
   if event['template_sql_sha256']!=rr['sql_sha256']:raise E(f'trace template: {q}')
  expected=rr['representative_bindings'];observed=sorted(observed_by_id.values(),key=lambda x:x['binding_id'])
  if rr['dynamic']and observed!=expected:raise E(f'bindings: {q}')
  if not rr['dynamic']and expected!=[]:raise E(f'static bindings: {q}')
  feature_rows.append({'query_id':q,'sql':sql,'weight':rr['weight'],'dynamic':rr['dynamic'],'representative_bindings':expected})
 reg=copy.deepcopy(oldreg);reg['freeze_id']=FREEZE;reg['derived_from']={'path':str(REG0.relative_to(ROOT)),'sha256':sha(REG0),'binding_authority_trace':checked(TRACE)}
 regb=canon(reg);regsha=hashlib.sha256(regb).hexdigest()
 active=oldspace['benchmark_candidate_parameters'][BENCH];defs=oldspace['all_parameter_definitions'];ranges={p:{'lower':defs[p]['lower'],'upper':defs[p]['upper']}for p in active}
 model_ptr={k:checked(v)for k,v in MODELS.items()};estptr=checked(EST)
 space=copy.deepcopy(oldspace);space['freeze_id']=FREEZE;space['estimator']=estptr;space['model_artifacts']=model_ptr;space['migration']={'reason':'refresh stale embedded estimator identity for legal trained-estimator T collection; AP active parameters and ranges unchanged','from_freeze_id':oldspace['freeze_id'],'from_artifact':checked(SPACE0),'active_parameters_unchanged':True,'ranges_unchanged':True}
 work={'schema_version':'mysql_estimator_t_workload_binding_v1','freeze_id':FREEZE,'immutable':True,'engine':'mysql','benchmark':BENCH,'execution_authorized':False,'derived_from':checked(WORK0),'binding_authority':{'registry_source':checked(REG0),'trace':checked(TRACE),'event_count':56,'query_count':14}}
 feature={'schema_version':'mysql_trained_estimator_feature_source_v1','freeze_id':FREEZE,'immutable':True,'benchmark':BENCH,'active_sql_count':106.0,'benchmark_id':BENCH,'queries':feature_rows,'registry_query_ids':ORDER,'require_strict_plan_registry':True,'workload_freeze_id':FREEZE,'workload_registry_path':str((OUT/'ap.mysql_plan_workload_registry_v1.json').resolve()),'workload_registry_sha256':regsha,'binding_authority_event_count':56}
 config={'schema_version':'mysql_trained_estimator_config_v1','type':'trained_models','benchmark':'CH','case_number':21,'window_seconds':5,'artifacts':{k:v['path']for k,v in model_ptr.items()},'feature_source':feature,'memory_context':{'source':'performance_schema'},'normalization':{'window_seconds':5,'memory_mb_per_unit':1024,'io_divisor':5,'cpu_divisor':5}}
 # Materialize bytes first because request pointers bind the exact new artifacts.
 data={'ap.mysql_plan_workload_registry_v1.json':regb,'effective_parameter_spaces.json':canon(space),'effective_workload_bundle.json':canon(work),'ap.trained_estimator_feature_source.json':canon(feature),'ap.trained_estimator_config.json':canon(config)}
 def np(name):return{'path':name,'sha256':hashlib.sha256(data[name]).hexdigest()}
 request={'schema_version':'mysql_trained_estimator_endpoint_collection_request_v1','binding':{'database':'mysql','scenario':SC,'benchmark':BENCH,'benchmark_version':FREEZE,'version':'mysql_ap_T_estimator_v1','workload':{'freeze_id':FREEZE,'artifact':np('effective_workload_bundle.json')},'sql_registry':{'id':'ap_mysql_plan_registry_v1','artifact':np('ap.mysql_plan_workload_registry_v1.json')},'estimator':{'id':'mysql_trained_resource_estimator','version':estptr['sha256'],'artifact':estptr,'model_artifacts':model_ptr},'parameter_space':{'version':FREEZE,'artifact':np('effective_parameter_spaces.json'),'active_params':active,'ranges':ranges}},'anchor':{p:defs[p]['default']for p in active},'trained_estimator_config':np('ap.trained_estimator_config.json'),'sensitivity_tolerance':1e-9}
 runtime={'schema_version':'mysql_endpoint_runtime_template_v1','ready':False,'database_connection_started':False,'mysql_client':{'mysql_binary':'mysql','defaults_extra_file':'__REQUIRED_ABSOLUTE_RUNTIME_DEFAULTS_FILE__','host':'127.0.0.1','port':13306,'user':'root','database':'hybench_ap','query_timeout_sec':120},'adapter':{'engine_name':'default','device_type':0},'serialization_lock_file':'/mnt/mysql_nvme0n1/anonymous/resource_adaptive_mysql/hybench_ap/locks/ap_estimator_t_endpoint.lock','runtime_gate':{'id':'verified_isolated_ap_work_copy_endpoint','required':True,'satisfied':False,'requirements':['session-owned retained datadir started as a temporary MySQL endpoint','defaults-extra-file supplied outside the evidence bundle','endpoint identity and zero competing MySQL pressure verified','serialization lock held through exact snapshot, all 17 serial endpoints, restore, and shutdown']}}
 data['ap.collection_request.json']=canon(request);data['ap.runtime_config.template.json']=canon(runtime)
 migration={'schema_version':'mysql_ap_estimator_t_input_migration_v1','freeze_id':FREEZE,'immutable':True,'status':'FROZEN_INPUTS_READY_RUNTIME_NOT_STARTED','source_freeze':oldspace['freeze_id'],'migration_reason':space['migration']['reason'],'source_checksums':{'parameter_space':checked(SPACE0),'workload_bundle':checked(WORK0),'registry':checked(REG0),'binding_trace':checked(TRACE),'statement_templates':checked(TOML),'estimator':estptr,'models':model_ptr,'collector':checked(COLLECTOR)},'published_checksums':{n:hashlib.sha256(b).hexdigest()for n,b in sorted(data.items())},'ap_active_params':active,'ap_ranges':ranges,'query_count':14,'binding_authority_event_count':56,'endpoint_count':17,'database_contacted':False,'readiness_changed':False,'t_created':False}
 data['migration_manifest.json']=canon(migration)
 checks=''.join(f'{hashlib.sha256(data[n]).hexdigest()}  {n}\n'for n in sorted(data));data['checksums.sha256']=checks.encode()
 return data
def publish(data:Mapping[str,bytes])->None:
 if OUT.exists():
  if not OUT.is_dir()or any((OUT/n).read_bytes()!=b for n,b in data.items())or set(p.name for p in OUT.iterdir())!=set(data):raise E('existing v4 differs')
  return
 tmp=Path(tempfile.mkdtemp(prefix='.'+OUT.name+'.',dir=OUT.parent))
 try:
  for n,b in data.items():
   p=tmp/n;p.write_bytes(b)
   with p.open('rb')as f:os.fsync(f.fileno())
  os.replace(tmp,OUT)
 except BaseException:shutil.rmtree(tmp,ignore_errors=True);raise
def main()->int:
 a=argparse.ArgumentParser(description=__doc__);a.add_argument('--execute',action='store_true');x=a.parse_args();data=outputs();already=OUT.exists()
 if x.execute:publish(data)
 print(json.dumps({'status':'PASS','mode':'execute'if x.execute else'dry-run','already_published':already,'output':str(OUT.relative_to(ROOT)),'files':{n:hashlib.sha256(b).hexdigest()for n,b in sorted(data.items())},'database_contacted':False,'readiness_changed':False,'t_created':False},sort_keys=True));return 0
if __name__=='__main__':
 try:raise SystemExit(main())
 except(E,OSError,ValueError,KeyError,json.JSONDecodeError,tomllib.TOMLDecodeError)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));raise SystemExit(2)
