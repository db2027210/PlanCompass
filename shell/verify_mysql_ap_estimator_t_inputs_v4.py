#!/usr/bin/env python3
"""Independently verify frozen AP estimator-T inputs without contacting MySQL."""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'config/formal/frozen_mysql_workloads_and_parameters_20260723_v4_estimator_t'
sys.path.insert(0,str(ROOT/'shell'))
from collect_mysql_trained_estimator_endpoint_evidence import _validated_request,_verify_production_config
from calibrate_mysql_experience_table import _load_json,CalibrationError
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 try:
  expected={}
  for line in(D/'checksums.sha256').read_text().splitlines():
   h,sep,n=line.partition('  ');p=D/n
   if not sep or p.parent.resolve()!=D.resolve()or not p.is_file()or p.is_symlink()or sha(p)!=h:raise ValueError('checksum '+n)
   expected[n]=h
  reqp=D/'ap.collection_request.json';v=_validated_request(_load_json(reqp),reqp);cfg=_load_json(Path(v['trained_estimator_config']['path']));_verify_production_config(v,cfg)
  feature=_load_json(D/'ap.trained_estimator_feature_source.json');runtime=_load_json(D/'ap.runtime_config.template.json');migration=_load_json(D/'migration_manifest.json')
  if len(feature['queries'])!=14 or sum(len(x['representative_bindings'])for x in feature['queries'])!=33 or migration.get('binding_authority_event_count')!=56:raise ValueError('authority coverage')
  if runtime.get('ready')is not False or runtime.get('database_connection_started')is not False or runtime['runtime_gate'].get('satisfied')is not False:raise ValueError('runtime template')
  if migration.get('database_contacted')is not False or migration.get('t_created')is not False or migration.get('readiness_changed')is not False:raise ValueError('scope')
  print(json.dumps({'status':'PASS','frozen_cross_validation':True,'query_count':14,'binding_authority_events':56,'endpoint_count':17,'database_contacted':False,'t_created':False,'readiness_changed':False,'runtime_ready':False,'unique_remaining_runtime_gate':runtime['runtime_gate']['id'],'files_verified':len(expected)},sort_keys=True));return 0
 except(Exception)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));return 1
if __name__=='__main__':raise SystemExit(main())
