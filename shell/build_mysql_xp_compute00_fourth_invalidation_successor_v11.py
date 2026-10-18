#!/usr/bin/env python3
"""Bind the fourth sealed zero-credit XP authorization into inventory."""
from __future__ import annotations
import hashlib,json,os
from pathlib import Path
import shutil,stat,subprocess,tempfile
ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();PY=Path('/home/anonymous/miniconda3/envs/py313/bin/python')
PID='c2a15ff45d1368f0496e02ce0b0a4b317e810c1c7306db7f407e1b31eba91dea';P=ROOT/'artifacts/mysql_xp_compute00_controlled_inventory_successor_v10'/PID;PSHA='0f4be8ead8832efc09d1b11395bef594ef9e73a6153b4f8098363f32ea7897e0';OUT=ROOT/'artifacts/mysql_xp_compute00_fourth_invalidation_successor_v11'
AID='xp-formal-qual-20260803T100523Z-42fe86c2e1d3e04a97d7068b';AF={'private_credentials/mysql-root-bootstrap.cnf':'cfbc72b91a6942527e7c96a2c628ae28d66013a4b880a4ef4c94986327fbc68f','private_credentials/mysql-client.cnf':'abafd0feef127eb7725970aa19be699819bbcc0f4f2e8d7ff037738034a2dce3','private_credentials/hybench_xp_qualification.properties':'c2ccc40f554b871fc48e8de734927e1685192f91eaf7f82bb30d3cb59969ae9d','private_authority/main_agent_token':'cdb0d5ec89c8a18caacaeff79c51efc4d5b168f5e69088b5ba2dbb079dc25641'}
class E(RuntimeError):pass
def can(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def files(r):return {p.relative_to(r).as_posix():sha(p) for p in sorted(r.rglob('*')) if p.is_file() and p.relative_to(r).as_posix() not in ('bundle_manifest.json','bundle_manifest.json.sha256')}
def wr(r):
 for p in [r,*r.rglob('*')]:
  if not p.is_symlink():p.chmod(stat.S_IMODE(p.stat().st_mode)|(0o700 if p.is_dir() else 0o200))
def fr(r):
 for p in sorted(r.rglob('*'),reverse=True):
  if p.is_symlink():raise E('symlink')
  p.chmod(0o555 if p.is_dir() or (p.is_file() and stat.S_IMODE(p.stat().st_mode)&0o111) else 0o444)
def once(t,o,n,l):
 if t.count(o)!=1:raise E(l+' count '+str(t.count(o)))
 return t.replace(o,n,1)
def patch(s):
 pf=s/'preflight/mysql_xp_compute00_disk1_preflight_v2.py';old_preflight_sha=sha(pf);t=pf.read_text();anchor='FAILED_HYBRID_ATTEMPT_ID="xp-formal-qual-20260803T095237Z-65fe3b3fd60a537fbdd55118"\n'
 t=once(t,anchor,anchor+'FAILED_FOURTH_ATTEMPT_ID="'+AID+'"\nCONTROLLED_FOURTH_AUTH_FILES='+repr(AF)+'\n','fourth constants')
 t=once(t,'expected_auth={INVALIDATED_ATTEMPT_ID:CONTROLLED_AUTH_FILES,FAILED_CGROUP_ATTEMPT_ID:CONTROLLED_FAILED_AUTH_FILES,FAILED_HYBRID_ATTEMPT_ID:CONTROLLED_HYBRID_FAILED_AUTH_FILES}','expected_auth={INVALIDATED_ATTEMPT_ID:CONTROLLED_AUTH_FILES,FAILED_CGROUP_ATTEMPT_ID:CONTROLLED_FAILED_AUTH_FILES,FAILED_HYBRID_ATTEMPT_ID:CONTROLLED_HYBRID_FAILED_AUTH_FILES,FAILED_FOURTH_ATTEMPT_ID:CONTROLLED_FOURTH_AUTH_FILES}','expected auth');pf.write_text(t)
 new_preflight_sha=sha(pf)
 for rel in ('distributed_runtime_contract.json','xp_executable_launch_contract_v3.json'):
  p=s/rel;t=p.read_text()
  if t.count(old_preflight_sha)!=1:raise E(rel+' preflight hash count')
  p.write_text(t.replace(old_preflight_sha,new_preflight_sha,1))
 rb=s/'operator/mysql_xp_compute00_fresh_r0_runbook_v1.md';t=rb.read_text();t=once(t,'.runtime_inventory.invalidated_authorizations | length == 3','.runtime_inventory.invalidated_authorizations | length == 4','runbook count');rb.write_text(t)
 mat=s/'test_support/mysql_xp_qualification_bootstrap_materials_v5.py';t=mat.read_text();anchor='        "xp-formal-qual-20260803T095237Z-65fe3b3fd60a537fbdd55118",\n'
 t=once(t,anchor,anchor+'        "'+AID+'",\n','material invalidation');mat.write_text(t)
 # Upgrade versioned fixtures so complete discovery remains authoritative.
 for rel in ('tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py','tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py','tests_compute00/test_mysql_xp_compute00_sibling_receipt_successor_v8.py'):
  p=s/rel;t=p.read_text();t=t.replace('PARENT_ID = "ea4f1ea656ac4de4d2e2e2279d523db378d128b9140f0fec5325ae096422bd83"','PARENT_ID = "'+PID+'"').replace('PARENT_ID="ea4f1ea656ac4de4d2e2e2279d523db378d128b9140f0fec5325ae096422bd83"','PARENT_ID="'+PID+'"').replace('PARENT_MANIFEST_SHA="544575ed2d1679433774d92e25bc62d9399b996bdc1f1ca7f6b33b6242c4bfe5"','PARENT_MANIFEST_SHA="'+PSHA+'"');p.write_text(t)
 p=s/'tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py';t=p.read_text();t=once(t,'self.assertEqual(len(contract["invalidated_attempt_ids"]),3)','self.assertEqual(len(contract["invalidated_attempt_ids"]),4)','inventory test');t=once(t,'self.assertEqual(sha(BUNDLE/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py"),"8ab4ee72e59c8af49f499bfbb92e53499bf25f71b8e4d2f9a6e94784f4e86e33")','self.assertEqual(sha(BUNDLE/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py"),self.manifest["files"]["test_support/mysql_xp_qualification_bootstrap_materials_v5.py"])\n  self.assertIn("xp-formal-qual-20260803T100523Z-42fe86c2e1d3e04a97d7068b",(BUNDLE/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py").read_text())','material successor test');p.write_text(t)
def verify(t,bid):
 m=json.loads((t/'bundle_manifest.json').read_text());u=dict(m);seen=u.pop('bundle_id',None)
 if seen!=bid or hashlib.sha256(can(u)).hexdigest()!=bid or files(t)!=m.get('files'):raise E('closure')
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',XP_BUNDLE_UNDER_TEST=str(t));c=subprocess.run([str(PY),'-B','-m','unittest','discover','-s',str(t/'tests_compute00'),'-p','test*.py','-v'],cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=300)
 if c.returncode:raise E('discover\n'+c.stdout)
 n=sum(x.rstrip().endswith('... ok') for x in c.stdout.splitlines())
 if n<36 or 'OK' not in c.stdout:raise E('count')
 return n,c.stdout
def build():
 if sha(P/'bundle_manifest.json')!=PSHA:raise E('parent')
 pm=json.loads((P/'bundle_manifest.json').read_text());OUT.mkdir(parents=True,exist_ok=True);raw=Path(tempfile.mkdtemp(prefix='.stage-xp-v11-',dir=str(OUT)));s=raw/'bundle'
 try:
  shutil.copytree(P,s);wr(s)
  for n in ('bundle_manifest.json','bundle_manifest.json.sha256'):(s/n).unlink()
  patch(s);shutil.copy2(SELF,s/'builder/build_mysql_xp_compute00_fourth_invalidation_successor_v11.py')
  m={k:v for k,v in pm.items() if k not in ('bundle_id','files')};m['byte_identical_02b_payload_sha256']={k:v for k,v in m.get('byte_identical_02b_payload_sha256',{}).items() if k!='test_support/mysql_xp_qualification_bootstrap_materials_v5.py'};ci=dict(m['controlled_existing_runtime_inventory']);ids=list(ci['invalidated_attempt_ids']);ids.append(AID);ci['invalidated_attempt_ids']=ids;m['controlled_existing_runtime_inventory']=ci
  m.update({'predecessor_bundle_id':PID,'predecessor_manifest_sha256':PSHA,'supersedes_bundle_id':PID,'supersession_reason':'bind_fourth_exact_zero_credit_authorization_inventory','fourth_invalidated_authorization':{'attempt_id':AID,'files_sha256':AF,'attempt_root_absent_required':True,'reusable':False,'accepted_rounds_credit':0},'live_preflight':{'required_before_publication':True,'status':'NOT_EXECUTED_BY_OFFLINE_BUILDER','required_result':'PASS_READY_NOT_LAUNCHED'},'formal_scope':'mysql_only','cross_engine_comparison_prohibited':True,'postgresql_mutation_prohibited':True,'formal_launch_authorized':False,'formal_r0_authorized':False,'accepted_round_authorized':False,'accepted_rounds_credit':0,'offline_build_only':True,'remote_actions':0,'files':files(s)})
  bid=hashlib.sha256(can(m)).hexdigest();m['bundle_id']=bid;t=OUT/bid
  if t.exists():return t,*verify(t,bid),'ADOPTED_IDENTICAL'
  (s/'bundle_manifest.json').write_bytes(can(m));(s/'bundle_manifest.json.sha256').write_text(sha(s/'bundle_manifest.json')+'  bundle_manifest.json\n');fr(s);s.chmod(0o755);os.rename(s,t);t.chmod(0o555);return t,*verify(t,bid),'PUBLISHED_NEW'
 finally:
  if raw.exists():wr(raw);shutil.rmtree(raw)
def main():
 t,n,o,mode=build();print(json.dumps({'status':'PASS_IMMUTABLE_V11_FOURTH_INVALIDATION_NOT_LAUNCHED','bundle':str(t),'bundle_id':t.name,'manifest_sha256':sha(t/'bundle_manifest.json'),'tests_compute00_passed':n,'discover_summary':[x for x in o.splitlines() if x.startswith('Ran ') or x=='OK'],'live_preflight_required_before_publication':True,'formal_scope':'mysql_only','formal_launch_authorized':False,'accepted_rounds_credit':0,'remote_actions':0},indent=2,sort_keys=True))
if __name__=='__main__':
 try:main()
 except (E,OSError,ValueError,subprocess.SubprocessError) as e:raise SystemExit('NOT_READY: '+str(e))
