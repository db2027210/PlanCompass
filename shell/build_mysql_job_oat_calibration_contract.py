#!/usr/bin/env python3
"""Publish the immutable JOB real-OAT calibration contract; default dry-run."""
from __future__ import annotations
import argparse,copy,hashlib,json,os,shutil,tempfile
from pathlib import Path
from typing import Any,Mapping
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'config/formal/frozen_mysql_job_oat_calibration_20260723_v1'
V3=ROOT/'config/formal/frozen_mysql_workloads_and_parameters_20260723_v3'
REG=V3/'plan_workload_registries/job.mysql_plan_workload_registry_v1.json';WORK=V3/'effective_workload_bundle.json';SPACE=V3/'effective_parameter_spaces.json'
EST=ROOT/'trained_model_estimator.py';CAL=ROOT/'shell/calibrate_mysql_experience_table.py'
MODELS={'cpu':Path('/home/anonymous/program/resource_adaptive/mysql_plan_models/cpu/artifacts/mysql_cpu_plan_model.joblib'),'memory':Path('/home/anonymous/program/resource_adaptive/mysql_plan_models/memory/artifacts/global_memory_model.joblib'),'io':Path('/home/anonymous/program/resource_adaptive/mysql_plan_models/io/output/mysql_total_io_plan_model.joblib')}
FREEZE='frozen_mysql_job_oat_calibration_20260723_v1';WF='frozen_mysql_workloads_and_parameters_20260723_v3';SC='job_memory';BENCH='job'
class E(RuntimeError):pass
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def ptr(p):
 if not p.is_file()or p.is_symlink():raise E('missing/unsafe '+str(p))
 return{'path':str(p.resolve()),'sha256':sha(p)}
def canon(v):return(json.dumps(v,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))+'\n').encode()
def side(p):
 s=p.with_name(p.name+'.sha256')
 if not s.is_file()or s.read_text()!=f'{sha(p)}  {p.name}\n':raise E('sidecar '+str(p))
def build():
 side(REG);old=json.loads(SPACE.read_text());reg=json.loads(REG.read_text())
 if reg.get('freeze_id')!=WF or reg.get('benchmark')!=BENCH or reg.get('immutable')is not True or len(reg.get('queries',[]))!=9 or any(q.get('dynamic')is not False or q.get('representative_bindings')!=[] for q in reg['queries']):raise E('JOB strict registry')
 active=old['benchmark_candidate_parameters'][BENCH];defs=old['all_parameter_definitions'];ranges={p:{'lower':defs[p]['lower'],'upper':defs[p]['upper']}for p in active};anchor={p:defs[p]['default']for p in active}
 if len(active)!=4 or any(not ranges[p]['lower']<anchor[p]<ranges[p]['upper']for p in active):raise E('JOB parameter space')
 models={k:ptr(v)for k,v in MODELS.items()};est=ptr(EST)
 workload={'schema_version':'mysql_job_oat_workload_binding_v1','freeze_id':WF,'immutable':True,'database':'mysql','benchmark':BENCH,'effective_workload_bundle':ptr(WORK),'strict_sql_registry':ptr(REG),'query_count':9,'registry_query_ids':[q['query_id']for q in reg['queries']],'all_queries_static_read_only':True}
 space={'schema_version':'mysql_job_oat_parameter_space_v1','freeze_id':FREEZE,'immutable':True,'database':'mysql','benchmark':BENCH,'active_params':active,'ranges':ranges,'anchor':anchor,'derived_from':ptr(SPACE),'current_estimator':est,'model_artifacts':models,'trained_estimator_job_route_available':False,'trained_estimator_predictions_used_for_oat':False}
 data={'job.workload_binding.json':canon(workload),'job.parameter_space.json':canon(space)}
 def np(n):return{'path':n,'sha256':hashlib.sha256(data[n]).hexdigest()}
 request={'schema_version':'mysql_t_calibration_request_v1','binding':{'database':'mysql','scenario':SC,'benchmark':BENCH,'benchmark_version':WF,'version':'mysql_job_T_real_oat_v1','workload':{'freeze_id':WF,'artifact':np('job.workload_binding.json')},'estimator':{'id':'current_mysql_estimator_identity_not_used_for_JOB_predictions','version':est['sha256'],'artifact':est},'parameter_space':{'version':FREEZE,'artifact':np('job.parameter_space.json'),'active_params':active,'ranges':ranges}},'calibration_policy':{'minimum_contrasts_per_side':3,'minimum_sign_consistency':0.8,'maximum_relative_mad':1.0,'neutral_effect_threshold':0.005},'contrasts':[]}
 schedule=[]
 for repeat in range(1,4):
  schedule.append({'ordinal':len(schedule)+1,'point_id':f'anchor_r{repeat}','repeat_id':repeat,'evidence_class':'frozen_anchor','anchor_id':f'anchor_r{repeat}','params':anchor})
  for param in active:
   for side_name in('lower','upper'):
    values=dict(anchor);values[param]=ranges[param][side_name]
    schedule.append({'ordinal':len(schedule)+1,'point_id':f'{param}_{side_name}_r{repeat}','repeat_id':repeat,'evidence_class':'oat_treatment','anchor_id':f'anchor_r{repeat}','parameter':param,'side':side_name,'params':values})
 gates={'postgresql_matching_resource_envelope_readback':False,'cross_engine_resource_envelope_equal':False,'static_cpu_nonoverlap':False,'static_numa_nonoverlap':False,'static_memory_swap_nonoverlap':False,'static_physical_disk_nonoverlap':False,'host_admission_passed':False,'dynamic_interference_passed':False,'host02_job_xp_mutex_zero_pressure':False,'exclusive_lane_lease_active':False,'session_owned_fresh_work_copy_verified':False}
 controller={'schema_version':'mysql_job_oat_controller_plan_v1','freeze_id':FREEZE,'status':'DRY_RUN_BLOCKED','execution_authorized':False,'database_contacted':False,'formal_r0_started':False,'formal_sgd_started':False,'oat_started':False,'scenario':SC,'design':{'active_parameter_count':4,'repeat_count':3,'anchor_reused_within_repeat':True,'minimum_contrasts_per_side':3,'minimum_distinct_formal_points':27,'schedule':schedule},'measurement':{'stability_resources':['cpu','memory','io'],'qps_stability_required':False,'first_check_seconds':180,'retry_step_seconds':30,'latest_window_seconds':180,'latest_window_count':6,'checks_seconds':list(range(180,601,30)),'max_seconds':600,'sample_coverage_min':0.90,'unstable_first_run_valid_independent_run_count':3,'independent_run_aggregation':'arithmetic_mean','technical_failures_count_as_valid_runs':False,'io_utilization_formula':'(read_throughput+write_throughput)/total_io_capability','dbqps_source':'database'},'per_point_requirements':['exact requested/effective/readback optimizer costs','same frozen resource envelope and readback for paired anchor/treatment','complete nine-query JOB workload','correctness passed','clean exit; no timeout/OOM/MySQL restart','exact cost snapshot restore and fresh work-copy reset'],'runtime_gates':gates,'duration':{'minimum_valid_runs_if_every_point_stable_first_run':27,'maximum_valid_runs_if_every_point_triggers_three-run_rule':81,'measurement_seconds_best_case':4860,'measurement_seconds_all_points_extend_to_600':16200,'measurement_seconds_three_run_contingency_at_600':48600,'best_case_minutes':81,'stable_points_upper_minutes':270,'full_instability_contingency_hours':13.5,'overhead_excluded':['template/work-copy reset','correctness checks','snapshot/readback','artifact hashing']}}
 runtime={'schema_version':'mysql_job_oat_runtime_template_v1','freeze_id':FREEZE,'ready':False,'execution_authorized':False,'request':None,'controller_plan':None,'external_defaults_file_required':True,'secrets_must_not_be_published':True,'resource_profile':{'name':'memory','cpu_quota_cores':3,'memory_bytes':6442450944,'memory_swap_bytes':6442450944,'io_read_bps':1205862400,'io_write_bps':1205862400,'pids_max':512,'cpu_isolation_degree':3,'numa_isolation_degree':1},'runtime_gates':gates}
 data['job.oat_calibration_request.json']=canon(request);data['job.oat_controller_plan.json']=canon(controller)
 runtime['request']={'path':'job.oat_calibration_request.json','sha256':hashlib.sha256(data['job.oat_calibration_request.json']).hexdigest()};runtime['controller_plan']={'path':'job.oat_controller_plan.json','sha256':hashlib.sha256(data['job.oat_controller_plan.json']).hexdigest()};data['job.runtime.template.json']=canon(runtime)
 manifest={'schema_version':'mysql_job_oat_contract_manifest_v1','freeze_id':FREEZE,'immutable':True,'status':'CONTRACT_READY_MEASUREMENT_NOT_STARTED','source_checksums':{'v3_workload':ptr(WORK),'v3_job_registry':ptr(REG),'v3_parameter_space':ptr(SPACE),'current_estimator':est,'models':models,'calibrator':ptr(CAL)},'published_checksums':{n:hashlib.sha256(b).hexdigest()for n,b in sorted(data.items())},'minimum_distinct_formal_points':27,'database_contacted':False,'oat_started':False,'r0_started':False,'sgd_started':False,'t_created':False,'readiness_changed':False}
 data['manifest.json']=canon(manifest);data['checksums.sha256']=''.join(f'{hashlib.sha256(data[n]).hexdigest()}  {n}\n'for n in sorted(data)).encode();return data
def publish(data:Mapping[str,bytes]):
 if OUT.exists():
  if not OUT.is_dir()or set(p.name for p in OUT.iterdir())!=set(data)or any((OUT/n).read_bytes()!=b for n,b in data.items()):raise E('existing immutable contract differs')
  return
 tmp=Path(tempfile.mkdtemp(prefix='.'+OUT.name+'.',dir=OUT.parent))
 try:
  for n,b in data.items():
   p=tmp/n;p.write_bytes(b)
   with p.open('rb')as f:os.fsync(f.fileno())
  os.replace(tmp,OUT)
 except BaseException:shutil.rmtree(tmp,ignore_errors=True);raise
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');a=p.parse_args();data=build();already=OUT.exists()
 if a.execute:publish(data)
 print(json.dumps({'status':'PASS','mode':'execute'if a.execute else'dry-run','already_published':already,'output':str(OUT.relative_to(ROOT)),'files':{n:hashlib.sha256(b).hexdigest()for n,b in sorted(data.items())},'minimum_distinct_formal_points':27,'database_contacted':False,'oat_started':False,'t_created':False,'readiness_changed':False},sort_keys=True));return 0
if __name__=='__main__':
 try:raise SystemExit(main())
 except(E,OSError,ValueError,KeyError,json.JSONDecodeError)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));raise SystemExit(2)
