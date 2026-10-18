#!/usr/bin/env python3
"""Promote only the verified AP MySQL envelope; default is dry-run."""
from __future__ import annotations

import argparse, copy, hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT=Path(__file__).resolve().parents[1]
DRAFT=ROOT/'config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft'
READINESS=DRAFT/'readiness_manifest.json'
PAIRED=ROOT/'config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json'
SOURCE=ROOT/'history/mysql_sgd_env_agents_20260723/ap/formal_mysql_normalized_envelope_readback.json'
VERIFIER=ROOT/'shell/verify_mysql_ap_formal_envelope_readback.py'
READY_VERIFIER=ROOT/'shell/verify_mysql_sgd_v2_readiness.py'
PROMOTER=ROOT/'shell/promote_mysql_ap_envelope_readback.py'
AUDIT=DRAFT/'evidence/ap_mysql_envelope_readiness_promotion_audit.json'
SCENARIO='hybench_ap_memory'; FREEZE='frozen_paired_pg_mysql_resource_envelopes_20260723_v1'
ALLOWED={f'scenarios.{SCENARIO}.mysql_envelope.{x}' for x in ('freeze_id','normalized','readback')}
PROTECTED={'postgresql_envelope_unchanged':True,'postgresql_envelope_unpromoted':True,'cross_engine_envelope_gate_false':True,'static_isolation_unchanged':True,'dynamic_interference_unchanged':True,'r0_calibration_unchanged':True,'global_status_not_ready':True,'launch_authorized_false':True}
class Error(RuntimeError):pass
def sha_bytes(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def sha(p:Path)->str:return sha_bytes(p.read_bytes())
def obj(p:Path)->dict[str,Any]:
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise Error(f'not object: {p}')
 return v
def canonical(v:Mapping[str,Any])->bytes:return (json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
def sidecar(p:Path)->str:
 d=sha(p);s=p.with_name(p.name+'.sha256')
 if not p.is_file() or p.is_symlink() or not s.is_file() or s.is_symlink() or s.read_text()!=f'{d}  {p.name}\n':raise Error(f'sidecar mismatch: {p}')
 return d
def pointer(p:Path,d:str|None=None)->dict[str,str]:return {'path':str(p.relative_to(ROOT)),'sha256':d or sha(p)}
def changes(a:Any,b:Any,prefix:str='')->set[str]:
 if isinstance(a,Mapping) and isinstance(b,Mapping):
  out=set()
  for k in set(a)|set(b):out|=changes(a.get(k),b.get(k),f'{prefix}.{k}' if prefix else str(k))
  return out
 return set() if a==b else {prefix}
def run(command:list[str],rc:int)->dict[str,Any]:
 r=subprocess.run(command,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 try:v=json.loads(r.stdout)
 except json.JSONDecodeError as e:raise Error(f'non-JSON verifier: {r.stdout}') from e
 if r.returncode!=rc:raise Error(f'verifier rc={r.returncode}: {r.stdout}')
 return v
def verify_proposed(payload:bytes)->dict[str,Any]:
 fd,raw=tempfile.mkstemp(prefix='.ap-envelope-',suffix='.json',dir=DRAFT);p=Path(raw)
 try:
  with os.fdopen(fd,'wb') as f:f.write(payload)
  v=run([sys.executable,str(READY_VERIFIER),'--manifest',str(p),'--json'],1)
 finally:p.unlink(missing_ok=True)
 if v.get('status')!='NOT_READY' or v.get('ready') is not False:raise Error('proposed readiness became ready')
 return v
def publish(outputs:Mapping[Path,bytes])->None:
 staged={}
 try:
  for target,payload in outputs.items():
   fd,raw=tempfile.mkstemp(prefix='.'+target.name+'.',dir=target.parent);tmp=Path(raw)
   with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
   staged[target]=tmp
  for target in sorted(outputs,key=lambda x:(x==READINESS,str(x))):os.replace(staged.pop(target),target)
 finally:
  for tmp in staged.values():tmp.unlink(missing_ok=True)
def main()->int:
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--execute',action='store_true');a=parser.parse_args()
 before=READINESS.read_bytes();ready=obj(READINESS);source_sha=sidecar(SOURCE);sidecar(READINESS)
 paired=obj(PAIRED);expected=f'{sha(PAIRED)}  {PAIRED.name}'
 if expected not in (PAIRED.parent/'checksums.sha256').read_text().splitlines():raise Error('paired checksum mismatch')
 profile=paired['profiles']['memory'];source=obj(SOURCE)
 if source.get('status')!='MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY' or source.get('scenario')!=SCENARIO or source.get('freeze_id')!=FREEZE or source.get('normalized_envelope')!=profile or source.get('requested_effective_equal') is not True:raise Error('source identity/profile mismatch')
 if source.get('formal_scope')!={'environment_readback_only':True,'r0':False,'sgd':False,'accepted_iteration':False,'readiness_changed':False,'ready':False,'launch_authorized':False}:raise Error('source scope mismatch')
 if source.get('static_isolation')!={'postgresql_counterpart_present':False,'postgresql_counterpart_envelope_proven':False,'native_mysql_isolation_complete':False,'cross_engine_envelope_equality':False,'formal_static_isolation_gate_passed':False}:raise Error('source static scope mismatch')
 if ready.get('status')!='NOT_READY' or ready.get('launch_authorized') is not False:raise Error('readiness authorized')
 item=ready['scenarios'][SCENARIO]
 if item.get('postgresql_envelope')!={'freeze_id':None,'normalized':None,'readback':None} or item.get('static_isolation')!={'evidence':None,'passed':False} or item.get('r0_calibration',{}).get('passed') is not False:raise Error('protected AP gate already changed')
 evidence=run([sys.executable,str(VERIFIER),'--allow-unpromoted-readiness'],0)
 if evidence.get('status')!='PASS' or evidence.get('eight_effective_fields') is not True or evidence.get('formal_static_gate') is not False or evidence.get('r0') is not False:raise Error('canonical verifier scope mismatch')
 desired={'freeze_id':FREEZE,'normalized':profile,'readback':pointer(SOURCE,source_sha)};updated=copy.deepcopy(ready);updated['scenarios'][SCENARIO]['mysql_envelope']=desired
 paths=changes(ready,updated);already=not paths
 if paths!=ALLOWED and not already:raise Error(f'forbidden paths: {sorted(paths)}')
 for field in ('postgresql_envelope','static_isolation','dynamic_interference','r0_calibration'):
  if updated['scenarios'][SCENARIO][field]!=ready['scenarios'][SCENARIO][field]:raise Error(f'protected field changed: {field}')
 proposed=canonical(updated);rv=verify_proposed(proposed)
 if already:
  sidecar(AUDIT);audit=obj(AUDIT)
  if audit.get('scope')!='hybench_ap_memory_mysql_envelope_only' or audit.get('promoted_mysql_envelope')!=desired or audit.get('protected_fields_unchanged')!=PROTECTED:raise Error('existing audit stale')
  if audit.get('after_readiness_sha256')==sha_bytes(proposed) and audit.get('promoter')==pointer(PROMOTER) and audit.get('evidence_verifier')==pointer(VERIFIER) and audit.get('readiness_verifier')==pointer(READY_VERIFIER):audit_payload=AUDIT.read_bytes()
  else:
   audit=copy.deepcopy(audit);audit['superseded_after_readiness_sha256']=audit.get('after_readiness_sha256');audit['after_readiness_sha256']=sha_bytes(proposed);audit['audit_refresh_reason']='unrelated readiness evidence changed; AP envelope projection and fail-closed gates revalidated';audit['promoter']=pointer(PROMOTER);audit['evidence_verifier']=pointer(VERIFIER);audit['evidence_verifier_result']=evidence;audit['readiness_verifier']=pointer(READY_VERIFIER);audit['readiness_reverification']={'status':rv.get('status'),'ready':rv.get('ready'),'passed':rv.get('passed'),'failed':rv.get('failed')};audit_payload=canonical(audit)
 else:
  audit_payload=canonical({'schema_version':1,'scope':'hybench_ap_memory_mysql_envelope_only','before_readiness_sha256':sha_bytes(before),'after_readiness_sha256':sha_bytes(proposed),'changed_json_paths':sorted(paths),'already_promoted':False,'promoted_mysql_envelope':desired,'promoter':pointer(PROMOTER),'evidence_verifier':pointer(VERIFIER),'evidence_verifier_result':evidence,'readiness_verifier':pointer(READY_VERIFIER),'readiness_reverification':{'status':rv.get('status'),'ready':rv.get('ready'),'passed':rv.get('passed'),'failed':rv.get('failed')},'protected_fields_unchanged':PROTECTED})
 outputs={READINESS:proposed,READINESS.with_name(READINESS.name+'.sha256'):f'{sha_bytes(proposed)}  {READINESS.name}\n'.encode(),AUDIT:audit_payload,AUDIT.with_name(AUDIT.name+'.sha256'):f'{sha_bytes(audit_payload)}  {AUDIT.name}\n'.encode()}
 if a.execute:
  publish(outputs)
  if any(p.read_bytes()!=v for p,v in outputs.items()):raise Error('post-publish mismatch')
 print(json.dumps({'status':'PASS','mode':'execute' if a.execute else 'dry-run','already_promoted':already,'changed_json_paths':sorted(paths),'readiness_status':'NOT_READY','launch_authorized':False,'static_gate':False,'r0':False,'replacement_sha256':{str(p.relative_to(ROOT)):sha_bytes(v) for p,v in outputs.items()}},sort_keys=True));return 0
if __name__=='__main__':
 try:raise SystemExit(main())
 except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,Error) as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));raise SystemExit(2)
