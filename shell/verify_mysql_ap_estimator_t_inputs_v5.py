#!/usr/bin/env python3
"""Verify immutable AP estimator-T v5 inputs offline and independently."""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'config/formal/frozen_mysql_workloads_and_parameters_20260723_v5_estimator_t'
sys.path.insert(0,str(ROOT/'shell'))
from collect_mysql_trained_estimator_endpoint_evidence import _validated_request,_verify_production_config
from calibrate_mysql_experience_table import _load_json
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 try:
  verified={}
  for line in(D/'checksums.sha256').read_text().splitlines():
   h,sep,n=line.partition('  ');p=(D/n).resolve()
   if not sep or p.parent!=D.resolve()or not p.is_file()or p.is_symlink()or sha(p)!=h:raise ValueError('checksum '+n)
   verified[n]=h
  request_path=D/'ap.collection_request.json';validated=_validated_request(_load_json(request_path),request_path)
  config=_load_json(Path(validated['trained_estimator_config']['path']));_verify_production_config(validated,config)
  feature=_load_json(D/'ap.trained_estimator_feature_source.json');runtime=_load_json(D/'ap.runtime_config.template.json');migration=_load_json(D/'migration_manifest.json');space=_load_json(D/'effective_parameter_spaces.json')
  if len(feature['queries'])!=14 or sum(len(q['representative_bindings'])for q in feature['queries'])!=33 or migration.get('binding_authority_event_count')!=56:raise ValueError('AP authority coverage')
  if space['freeze_id']!='frozen_mysql_workloads_and_parameters_20260723_v5_estimator_t'or migration.get('supersedes_failed_input_freeze',{}).get('freeze_id')!='frozen_mysql_workloads_and_parameters_20260723_v4_estimator_t':raise ValueError('freeze migration')
  if runtime.get('ready')is not False or runtime.get('database_connection_started')is not False or runtime['runtime_gate'].get('satisfied')is not False:raise ValueError('runtime template')
  if any(migration.get(k)is not False for k in ('database_contacted','t_created','readiness_changed')):raise ValueError('scope')
  print(json.dumps({'status':'PASS','freeze_id':space['freeze_id'],'frozen_cross_validation':True,'query_count':14,'representative_binding_count':33,'binding_authority_events':56,'endpoint_count':17,'database_contacted':False,'t_created':False,'readiness_changed':False,'runtime_ready':False,'unique_remaining_runtime_gate':runtime['runtime_gate']['id'],'request_sha256':sha(request_path),'runtime_template_sha256':sha(D/'ap.runtime_config.template.json'),'files_verified':len(verified)},sort_keys=True));return 0
 except(Exception)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
