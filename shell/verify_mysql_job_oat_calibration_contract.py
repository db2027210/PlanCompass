#!/usr/bin/env python3
"""Verify the JOB OAT contract offline; no workload or database access."""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'config/formal/frozen_mysql_job_oat_calibration_20260723_v1';sys.path.insert(0,str(ROOT/'shell'))
from calibrate_mysql_experience_table import _load_json,_validate_binding,build_artifact,CalibrationError
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 try:
  files={}
  for line in(D/'checksums.sha256').read_text().splitlines():
   h,s,n=line.partition('  ');p=(D/n).resolve()
   if not s or p.parent!=D.resolve()or not p.is_file()or p.is_symlink()or sha(p)!=h:raise ValueError('checksum '+n)
   files[n]=h
  reqp=D/'job.oat_calibration_request.json';req=_load_json(reqp);binding=_validate_binding(req,D);plan=_load_json(D/'job.oat_controller_plan.json');runtime=_load_json(D/'job.runtime.template.json');manifest=_load_json(D/'manifest.json')
  if binding['scenario']!='job_memory'or binding['benchmark']!='job'or len(binding['parameter_space']['active_params'])!=4:raise ValueError('binding')
  schedule=plan['design']['schedule']
  if len(schedule)!=27 or [x['ordinal']for x in schedule]!=list(range(1,28))or len([x for x in schedule if x['evidence_class']=='frozen_anchor'])!=3 or len([x for x in schedule if x['evidence_class']=='oat_treatment'])!=24:raise ValueError('schedule')
  if req['contrasts']!=[]:raise ValueError('dry-run request unexpectedly contains evidence')
  try:build_artifact(req,reqp);raise ValueError('empty request unexpectedly calibrated T')
  except CalibrationError as e:
   expected={f'{p}.{s} has 0 contrasts; requires 3'for p in binding['parameter_space']['active_params']for s in('lower','upper')}
   if set(e.reasons)!=expected:raise ValueError('unexpected fail-closed reasons: '+repr(e.reasons))
  if plan.get('execution_authorized')is not False or runtime.get('ready')is not False or runtime.get('execution_authorized')is not False:raise ValueError('runtime authorized')
  if any(manifest.get(k)is not False for k in('database_contacted','oat_started','r0_started','sgd_started','t_created','readiness_changed')):raise ValueError('scope')
  false_gates=sorted(k for k,v in runtime['runtime_gates'].items()if v is False)
  print(json.dumps({'status':'PASS','contract_ready':True,'measurement_started':False,'minimum_distinct_formal_points':27,'minimum_valid_runs':27,'maximum_contingency_valid_runs':81,'best_case_minutes':81,'stable_points_upper_minutes':270,'full_instability_contingency_hours':13.5,'fail_closed_missing_contrasts':8,'runtime_gates_unsatisfied':false_gates,'files_verified':len(files),'database_contacted':False,'t_created':False,'readiness_changed':False},sort_keys=True));return 0
 except(Exception)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
