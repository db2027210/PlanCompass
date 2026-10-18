#!/usr/bin/env python3
"""Promote only the verified JOB MySQL envelope; default is dry-run."""
from __future__ import annotations
import argparse,copy,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from typing import Any,Mapping
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'config/formal/frozen_mysql_deployment_schedule_20260723_v2_draft';R=D/'readiness_manifest.json';P=ROOT/'config/formal/frozen_paired_pg_mysql_resource_envelopes_20260723_v1/paired_resource_envelopes.json';S=ROOT/'history/mysql_sgd_env_agents_20260723/job/formal_mysql_normalized_envelope_readback.json';V=ROOT/'shell/verify_mysql_job_formal_envelope_readback.py';RV=ROOT/'shell/verify_mysql_sgd_v2_readiness.py';ME=ROOT/'shell/promote_mysql_job_envelope_readback.py';A=D/'evidence/job_mysql_envelope_readiness_promotion_audit.json';SC='job_memory';FREEZE='frozen_paired_pg_mysql_resource_envelopes_20260723_v1';ALLOWED={f'scenarios.{SC}.mysql_envelope.{x}'for x in('freeze_id','normalized','readback')};PROTECTED={'postgresql_envelope_unchanged':True,'postgresql_envelope_unpromoted':True,'cross_engine_envelope_gate_false':True,'static_isolation_unchanged':True,'dynamic_interference_unchanged':True,'r0_calibration_unchanged':True,'global_status_not_ready':True,'launch_authorized_false':True}
class E(RuntimeError):pass
def hb(b):return hashlib.sha256(b).hexdigest()
def sh(p):return hb(p.read_bytes())
def ob(p):
 v=json.loads(p.read_text())
 if not isinstance(v,dict):raise E('not object '+str(p))
 return v
def cj(v):return(json.dumps(v,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
def side(p):
 d=sh(p);s=p.with_name(p.name+'.sha256')
 if not p.is_file()or p.is_symlink()or not s.is_file()or s.is_symlink()or s.read_text()!=f'{d}  {p.name}\n':raise E('sidecar '+str(p))
 return d
def ptr(p,d=None):return{'path':str(p.relative_to(ROOT)),'sha256':d or sh(p)}
def diff(a,b,p=''):
 if isinstance(a,Mapping)and isinstance(b,Mapping):
  z=set()
  for k in set(a)|set(b):z|=diff(a.get(k),b.get(k),f'{p}.{k}'if p else str(k))
  return z
 return set()if a==b else{p}
def run(c,rc):
 q=subprocess.run(c,cwd=ROOT,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 try:v=json.loads(q.stdout)
 except json.JSONDecodeError as x:raise E('non-json '+q.stdout)from x
 if q.returncode!=rc:raise E(f'verifier rc={q.returncode} {q.stdout}')
 return v
def check(payload):
 fd,raw=tempfile.mkstemp(prefix='.job-envelope-',suffix='.json',dir=D);p=Path(raw)
 try:
  with os.fdopen(fd,'wb')as f:f.write(payload)
  v=run([sys.executable,str(RV),'--manifest',str(p),'--json'],1)
 finally:p.unlink(missing_ok=True)
 if v.get('status')!='NOT_READY'or v.get('ready')is not False:raise E('proposed ready')
 return v
def publish(outputs):
 staged={}
 try:
  for t,v in outputs.items():
   fd,raw=tempfile.mkstemp(prefix='.'+t.name+'.',dir=t.parent);p=Path(raw)
   with os.fdopen(fd,'wb')as f:f.write(v);f.flush();os.fsync(f.fileno())
   staged[t]=p
  for t in sorted(outputs,key=lambda x:(x==R,str(x))):os.replace(staged.pop(t),t)
 finally:
  for p in staged.values():p.unlink(missing_ok=True)
def main():
 pa=argparse.ArgumentParser(description=__doc__);pa.add_argument('--execute',action='store_true');a=pa.parse_args();before=R.read_bytes();ready=ob(R);source_sha=side(S);side(R);paired=ob(P)
 if f'{sh(P)}  {P.name}'not in(P.parent/'checksums.sha256').read_text().splitlines():raise E('paired checksum')
 profile=paired['profiles']['memory'];source=ob(S)
 if source.get('status')!='MYSQL_NORMALIZED_ENVELOPE_VERIFIED_NOT_READY'or source.get('scenario')!=SC or source.get('freeze_id')!=FREEZE or source.get('normalized_envelope')!=profile:raise E('source identity/profile')
 if source.get('formal_scope')!={'environment_readback_only':True,'r0':False,'sgd':False,'accepted_iteration':False,'readiness_changed':False,'ready':False,'launch_authorized':False}:raise E('scope')
 static=source.get('static_isolation')or{}
 if static.get('postgresql_profile_matches_job')is not False or static.get('cross_engine_envelope_equality')is not False or static.get('formal_static_isolation_gate_passed')is not False:raise E('static scope')
 if ready.get('status')!='NOT_READY'or ready.get('launch_authorized')is not False:raise E('authorized')
 item=ready['scenarios'][SC]
 if item.get('postgresql_envelope')!={'freeze_id':None,'normalized':None,'readback':None}or item.get('static_isolation')!={'evidence':None,'passed':False}or item.get('r0_calibration',{}).get('passed')is not False:raise E('protected gate')
 evidence=run([sys.executable,str(V),'--allow-unpromoted-readiness'],0)
 if evidence.get('status')!='PASS'or evidence.get('eight_effective_fields')is not True or evidence.get('formal_static_gate')is not False or evidence.get('cross_engine_equality')is not False or evidence.get('r0')is not False:raise E('verifier scope')
 desired={'freeze_id':FREEZE,'normalized':profile,'readback':ptr(S,source_sha)};updated=copy.deepcopy(ready);updated['scenarios'][SC]['mysql_envelope']=desired;paths=diff(ready,updated);already=not paths
 if paths!=ALLOWED and not already:raise E('forbidden '+repr(sorted(paths)))
 for f in('postgresql_envelope','static_isolation','dynamic_interference','r0_calibration'):
  if updated['scenarios'][SC][f]!=ready['scenarios'][SC][f]:raise E('changed '+f)
 proposed=cj(updated);rv=check(proposed)
 if already:
  side(A);audit=ob(A)
  if audit.get('scope')!='job_memory_mysql_envelope_only'or audit.get('promoted_mysql_envelope')!=desired or audit.get('protected_fields_unchanged')!=PROTECTED:raise E('stale audit')
  if audit.get('after_readiness_sha256')==hb(proposed)and audit.get('promoter')==ptr(ME)and audit.get('evidence_verifier')==ptr(V)and audit.get('readiness_verifier')==ptr(RV):ap=A.read_bytes()
  else:
   audit=copy.deepcopy(audit);audit['superseded_after_readiness_sha256']=audit.get('after_readiness_sha256');audit['after_readiness_sha256']=hb(proposed);audit['audit_refresh_reason']='unrelated readiness evidence changed; JOB envelope projection and fail-closed gates revalidated';audit['promoter']=ptr(ME);audit['evidence_verifier']=ptr(V);audit['evidence_verifier_result']=evidence;audit['readiness_verifier']=ptr(RV);audit['readiness_reverification']={'status':rv.get('status'),'ready':rv.get('ready'),'passed':rv.get('passed'),'failed':rv.get('failed')};ap=cj(audit)
 else:ap=cj({'schema_version':1,'scope':'job_memory_mysql_envelope_only','before_readiness_sha256':hb(before),'after_readiness_sha256':hb(proposed),'changed_json_paths':sorted(paths),'already_promoted':False,'promoted_mysql_envelope':desired,'promoter':ptr(ME),'evidence_verifier':ptr(V),'evidence_verifier_result':evidence,'readiness_verifier':ptr(RV),'readiness_reverification':{'status':rv.get('status'),'ready':rv.get('ready'),'passed':rv.get('passed'),'failed':rv.get('failed')},'protected_fields_unchanged':PROTECTED})
 outputs={R:proposed,R.with_name(R.name+'.sha256'):f'{hb(proposed)}  {R.name}\n'.encode(),A:ap,A.with_name(A.name+'.sha256'):f'{hb(ap)}  {A.name}\n'.encode()}
 if a.execute:
  publish(outputs)
  if any(p.read_bytes()!=v for p,v in outputs.items()):raise E('post publish')
 print(json.dumps({'status':'PASS','mode':'execute'if a.execute else'dry-run','already_promoted':already,'changed_json_paths':sorted(paths),'readiness_status':'NOT_READY','launch_authorized':False,'static_gate':False,'r0':False,'replacement_sha256':{str(p.relative_to(ROOT)):hb(v)for p,v in outputs.items()}},sort_keys=True));return 0
if __name__=='__main__':
 try:raise SystemExit(main())
 except(OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,E)as e:print(json.dumps({'status':'FAIL','error':str(e)},sort_keys=True));raise SystemExit(2)
