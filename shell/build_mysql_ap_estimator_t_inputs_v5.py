#!/usr/bin/env python3
"""Build immutable AP estimator-T inputs v5; default dry-run, never contacts MySQL."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import build_mysql_ap_estimator_t_inputs_v4 as base

ROOT=Path(__file__).resolve().parents[1]
V4=ROOT/'config/formal/frozen_mysql_workloads_and_parameters_20260723_v4_estimator_t'
OUT=ROOT/'config/formal/frozen_mysql_workloads_and_parameters_20260723_v5_estimator_t'
FREEZE='frozen_mysql_workloads_and_parameters_20260723_v5_estimator_t'
class E(RuntimeError):pass
def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def canon(v):return(json.dumps(v,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':'))+'\n').encode()
def build():
 if not(V4/'checksums.sha256').is_file():raise E('immutable v4 predecessor missing')
 base.OUT=OUT;base.FREEZE=FREEZE
 data=base.outputs()
 runtime=json.loads(data['ap.runtime_config.template.json'])
 runtime['freeze_id']=FREEZE
 runtime['collection_request']={'path':'ap.collection_request.json','sha256':hashlib.sha256(data['ap.collection_request.json']).hexdigest()}
 runtime['estimator_sha256']=sha(base.EST);runtime['collector_sha256']=sha(base.COLLECTOR)
 data['ap.runtime_config.template.json']=canon(runtime)
 migration=json.loads(data['migration_manifest.json'])
 migration['schema_version']='mysql_ap_estimator_t_input_migration_v2'
 migration['migration_reason']='new immutable freeze after shared read-only SQL validator gained strict leading-parenthesized SELECT/UNION support; v4 remains preserved and unmodified'
 migration['supersedes_failed_input_freeze']={'freeze_id':'frozen_mysql_workloads_and_parameters_20260723_v4_estimator_t','checksums':{'path':str((V4/'checksums.sha256').relative_to(ROOT)),'sha256':sha(V4/'checksums.sha256')},'failure':'authoritative AP-7 begins with parenthesized SELECT/UNION and was rejected by the prior validator'}
 migration['published_checksums']={n:hashlib.sha256(b).hexdigest()for n,b in sorted(data.items())if n not in {'migration_manifest.json','checksums.sha256'}}
 data['migration_manifest.json']=canon(migration)
 data['checksums.sha256']=''.join(f'{hashlib.sha256(data[n]).hexdigest()}  {n}\n'for n in sorted(data)if n!='checksums.sha256').encode()
 return data
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--execute',action='store_true');a=p.parse_args();data=build();already=OUT.exists()
 if a.execute:base.OUT=OUT;base.publish(data)
 print(json.dumps({'status':'PASS','mode':'execute'if a.execute else'dry-run','already_published':already,'freeze_id':FREEZE,'output':str(OUT.relative_to(ROOT)),'files':{n:hashlib.sha256(b).hexdigest()for n,b in sorted(data.items())},'database_contacted':False,'readiness_changed':False,'t_created':False},sort_keys=True));return 0
if __name__=='__main__':
 try:raise SystemExit(main())
 except(E,base.E,OSError,ValueError,KeyError,json.JSONDecodeError)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));raise SystemExit(2)
