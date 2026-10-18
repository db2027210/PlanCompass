#!/usr/bin/env python3
"""Seal the observed XP bundle owners and empty publication residues."""
from __future__ import annotations
import hashlib,json,os
from pathlib import Path
import shutil,stat,subprocess,tempfile

ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();PYTHON=Path('/home/anonymous/miniconda3/envs/py313/bin/python')
PARENT_ID='ea4f1ea656ac4de4d2e2e2279d523db378d128b9140f0fec5325ae096422bd83';PARENT=ROOT/'artifacts/mysql_xp_compute00_full_discover_successor_v9'/PARENT_ID
PARENT_SHA='544575ed2d1679433774d92e25bc62d9399b996bdc1f1ca7f6b33b6242c4bfe5';OUT=ROOT/'artifacts/mysql_xp_compute00_controlled_inventory_successor_v10'
PREF='preflight/mysql_xp_compute00_disk1_preflight_v2.py';CONTRACTS=('distributed_runtime_contract.json','xp_executable_launch_contract_v3.json')
ROOT_BUNDLES=('47a2367d2548fe140e7ea5e2536be4a9ab1f60b9dfb52c7c4cdbf7d1f95c2c52','f60b1a7e4fa082d8455f060e402065d07e7beee5aa86a7a737735c0cb98c7fda')
RESIDUES=('.txn-47a-20260803t1835p08','.txn-f60-20260803t1840p08')
class E(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+'\n').encode()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def files(r):return {p.relative_to(r).as_posix():sha(p) for p in sorted(r.rglob('*')) if p.is_file() and p.relative_to(r).as_posix() not in ('bundle_manifest.json','bundle_manifest.json.sha256')}
def writable(r):
 for p in [r,*r.rglob('*')]:
  if not p.is_symlink():p.chmod(stat.S_IMODE(p.stat().st_mode)|(0o700 if p.is_dir() else 0o200))
def freeze(r):
 for p in sorted(r.rglob('*'),reverse=True):
  if p.is_symlink():raise E('symlink')
  p.chmod(0o555 if p.is_dir() or (p.is_file() and stat.S_IMODE(p.stat().st_mode)&0o111) else 0o444)
def once(t,o,n,l):
 if t.count(o)!=1:raise E(l+' patch count '+str(t.count(o)))
 return t.replace(o,n,1)
def patch_preflight(s):
 p=s/PREF;t=p.read_text();anchor='POSTGRESQL_IDENTITY=[{"pid":7405,"startticks":85360,"cpuset":"0-511","cmdline_sha256":"9cde4588860e292e7b38be4e4306b3169f89ff71b90170775439565d8cb550c2"}]\n'
 t=once(t,anchor,anchor+'ROOT_OWNED_BUNDLE_IDS='+repr(set(ROOT_BUNDLES))+'\nCONTROLLED_PUBLICATION_RESIDUES='+repr(set(RESIDUES))+'\n','inventory constants')
 t=once(t,' if owner(bundle)!=("anonymous","anonymous") or stat.S_IMODE(bundle.stat().st_mode)!=0o555:raise PreflightError("controlled bundle root metadata differs")',' expected_owner=("root","root") if expected_id in ROOT_OWNED_BUNDLE_IDS or expected_id not in POST_ADOPTION_BUNDLE_MANIFESTS else ("anonymous","anonymous")\n if owner(bundle)!=expected_owner or stat.S_IMODE(bundle.stat().st_mode)!=0o555:raise PreflightError("controlled bundle root metadata differs")','bundle owner')
 t=once(t,'  if owner(item)!=("anonymous","anonymous") or stat.S_IMODE(item.stat().st_mode)!=expected_mode:raise PreflightError("controlled bundle metadata differs")','  if owner(item)!=expected_owner or stat.S_IMODE(item.stat().st_mode)!=expected_mode:raise PreflightError("controlled bundle metadata differs")','entry owner')
 old=' observed={p.name for p in bundles.iterdir()}\n if not required<=observed or not observed<=allowed:raise PreflightError("post-adoption runtime bundle inventory differs")\n identities=[sealed_bundle_identity(bundles/key,key,value) for key,value in sorted(POST_ADOPTION_BUNDLE_MANIFESTS.items())]'
 new=' entries={p.name for p in bundles.iterdir()}\n observed={name for name in entries if not name.startswith(".")}\n residues={name for name in entries if name.startswith(".")}\n if residues!=CONTROLLED_PUBLICATION_RESIDUES:raise PreflightError("controlled publication residue inventory differs")\n residue_evidence=[]\n for name in sorted(residues):\n  path=bundles/name\n  if path.is_symlink() or not path.is_dir() or owner(path)!=("root","root") or stat.S_IMODE(path.stat().st_mode)!=0o700 or any(path.iterdir()):raise PreflightError("controlled publication residue differs")\n  residue_evidence.append({"name":name,"owner":"root:root","mode":"0700","empty":True,"reusable":False})\n if not required<=observed or not observed<=allowed:raise PreflightError("post-adoption runtime bundle inventory differs")\n identities=[sealed_bundle_identity(bundles/key,key,value) for key,value in sorted(POST_ADOPTION_BUNDLE_MANIFESTS.items())]'
 t=once(t,old,new,'observed inventory')
 t=once(t,'"runtime_top_level":["authorizations","bundles"],"daemon_adoption_receipt_root":str(RECEIPT_ROOT),"bundles":identities','"runtime_top_level":["authorizations","bundles"],"daemon_adoption_receipt_root":str(RECEIPT_ROOT),"controlled_publication_residues":residue_evidence,"bundles":identities','residue evidence')
 p.write_text(t)
def patch_contracts(s,old):
 new=sha(s/PREF)
 for rel in CONTRACTS:
  p=s/rel;t=p.read_text()
  if t.count(old)!=1:raise E(rel+' old hash count')
  p.write_text(t.replace(old,new,1))
def patch_tests(s):
 for rel in ('tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py','tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py','tests_compute00/test_mysql_xp_compute00_sibling_receipt_successor_v8.py'):
  p=s/rel;t=p.read_text();t=t.replace('PARENT_ID = "bebe8302e96cbd016b4e14826913d922b45e7b78bc82fe9368c381c71486af0a"','PARENT_ID = "'+PARENT_ID+'"').replace('PARENT_ID="bebe8302e96cbd016b4e14826913d922b45e7b78bc82fe9368c381c71486af0a"','PARENT_ID="'+PARENT_ID+'"')
  t=t.replace('PARENT_MANIFEST_SHA="0edcca67d28efec120180e237ec6a483e82b0cf53f450b22e4db7c586744f35c"','PARENT_MANIFEST_SHA="'+PARENT_SHA+'"');p.write_text(t)
 test=s/'tests_compute00/test_mysql_xp_compute00_controlled_publication_residues_v10.py';test.write_text("""import importlib.util,os\nfrom pathlib import Path\nimport unittest\n+B=Path(os.environ.get('XP_BUNDLE_UNDER_TEST',Path(__file__).resolve().parents[1]));P=B/'preflight/mysql_xp_compute00_disk1_preflight_v2.py'\n+spec=importlib.util.spec_from_file_location('p',P);p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)\n+class Tests(unittest.TestCase):\n+ def test_exact_residues(self):self.assertEqual(p.CONTROLLED_PUBLICATION_RESIDUES,{'.txn-47a-20260803t1835p08','.txn-f60-20260803t1840p08'})\n+ def test_exact_root_owned_bundles(self):self.assertEqual(p.ROOT_OWNED_BUNDLE_IDS,{'47a2367d2548fe140e7ea5e2536be4a9ab1f60b9dfb52c7c4cdbf7d1f95c2c52','f60b1a7e4fa082d8455f060e402065d07e7beee5aa86a7a737735c0cb98c7fda'})\n+ def test_residues_are_fail_closed(self):\n+  t=P.read_text();self.assertIn('residues!=CONTROLLED_PUBLICATION_RESIDUES',t);self.assertIn('or any(path.iterdir())',t)\n+ def test_scope(self):\n+  import json;m=json.loads((B/'bundle_manifest.json').read_text());self.assertEqual(m['formal_scope'],'mysql_only');self.assertFalse(m['formal_launch_authorized']);self.assertEqual(m['accepted_rounds_credit'],0)\n+if __name__=='__main__':unittest.main()\n+""".replace('\n+','\n'));test.chmod(0o755)
def verify(t,bid):
 m=json.loads((t/'bundle_manifest.json').read_text());u=dict(m);seen=u.pop('bundle_id',None)
 if seen!=bid or hashlib.sha256(canonical(u)).hexdigest()!=bid or files(t)!=m.get('files'):raise E('closure')
 env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',XP_BUNDLE_UNDER_TEST=str(t));c=subprocess.run([str(PYTHON),'-B','-m','unittest','discover','-s',str(t/'tests_compute00'),'-p','test*.py','-v'],cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=300)
 if c.returncode:raise E('discover failed\n'+c.stdout)
 count=sum(x.rstrip().endswith('... ok') for x in c.stdout.splitlines())
 if count<36 or 'OK' not in c.stdout:raise E('discover count')
 return count,c.stdout
def build():
 if sha(PARENT/'bundle_manifest.json')!=PARENT_SHA:raise E('parent sha')
 pm=json.loads((PARENT/'bundle_manifest.json').read_text());OUT.mkdir(parents=True,exist_ok=True);raw=Path(tempfile.mkdtemp(prefix='.stage-xp-v10-',dir=str(OUT)));s=raw/'bundle'
 try:
  shutil.copytree(PARENT,s);writable(s)
  for n in ('bundle_manifest.json','bundle_manifest.json.sha256'):(s/n).unlink()
  old=sha(s/PREF);patch_preflight(s);patch_contracts(s,old);patch_tests(s);shutil.copy2(SELF,s/'builder/build_mysql_xp_compute00_controlled_inventory_successor_v10.py')
  m={k:v for k,v in pm.items() if k not in ('bundle_id','files')};m['byte_identical_02b_payload_sha256']={k:v for k,v in m.get('byte_identical_02b_payload_sha256',{}).items() if k not in CONTRACTS}
  m.update({'predecessor_bundle_id':PARENT_ID,'predecessor_manifest_sha256':PARENT_SHA,'supersedes_bundle_id':PARENT_ID,'supersession_reason':'bind_exact_empty_root_owned_publication_residues_and_mixed_immutable_bundle_owners','controlled_publication_residues':[{'name':x,'owner':'root:root','mode':'0700','empty':True,'reusable':False} for x in RESIDUES],'required_bundle_owners':{x:'root:root' for x in ROOT_BUNDLES},'live_preflight':{'required_before_publication':True,'status':'NOT_EXECUTED_BY_OFFLINE_BUILDER','required_result':'PASS_READY_NOT_LAUNCHED'},'formal_scope':'mysql_only','cross_engine_comparison_prohibited':True,'postgresql_mutation_prohibited':True,'formal_launch_authorized':False,'formal_r0_authorized':False,'accepted_round_authorized':False,'accepted_rounds_credit':0,'offline_build_only':True,'remote_actions':0,'files':files(s)})
  bid=hashlib.sha256(canonical(m)).hexdigest();m['bundle_id']=bid;t=OUT/bid
  if t.exists():return t,*verify(t,bid),'ADOPTED_IDENTICAL'
  (s/'bundle_manifest.json').write_bytes(canonical(m));(s/'bundle_manifest.json.sha256').write_text(sha(s/'bundle_manifest.json')+'  bundle_manifest.json\n');freeze(s);s.chmod(0o755);os.rename(s,t);t.chmod(0o555);return t,*verify(t,bid),'PUBLISHED_NEW'
 finally:
  if raw.exists():writable(raw);shutil.rmtree(raw)
def main():
 t,n,o,mode=build();print(json.dumps({'status':'PASS_IMMUTABLE_V10_CONTROLLED_INVENTORY_NOT_LAUNCHED','bundle':str(t),'bundle_id':t.name,'manifest_sha256':sha(t/'bundle_manifest.json'),'tests_compute00_passed':n,'discover_summary':[x for x in o.splitlines() if x.startswith('Ran ') or x=='OK'],'live_preflight_required_before_publication':True,'formal_scope':'mysql_only','formal_launch_authorized':False,'accepted_rounds_credit':0,'remote_actions':0},indent=2,sort_keys=True))
if __name__=='__main__':
 try:main()
 except (E,OSError,ValueError,subprocess.SubprocessError) as e:raise SystemExit('NOT_READY: '+str(e))
