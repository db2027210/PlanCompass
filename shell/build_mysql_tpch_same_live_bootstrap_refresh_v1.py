#!/usr/bin/env python3
"""Build a fail-closed ff313 same-live bootstrap spec/evidence refresher."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_same_live_bootstrap_refresh_v1"
TARGET_ID="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4";TARGET_SHA="af1d0facca8b33e2638c0a2af6a751bececc407d483055ca064a34308d27bb52";AUTHORITY_SHA="120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408"
SUPPORT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";SUPPORT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";ENTRY_SHA="5545dcb6277fd8979a4b6e1994bf7941aa8db19b4a369498bf22ae4b8c2412b1";CORE_SHA="ae8f22b8c4dab4f4e02e023122c72f21b7ebb244ba3d57e136b91468d7542863"
DB="ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_db";CLIENT="ra_mysql_tpch_v2_tpch_v2_20260803t022500z_afbe6badff313374deadbee_client"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
MATERIALIZER=rf'''#!/usr/bin/env python3
"""Refresh ff313 same-live bootstrap spec/evidence without restarting MySQL."""
from __future__ import annotations
import argparse,hashlib,importlib.util,json,os,secrets,shlex,stat,subprocess,sys,time
from datetime import datetime,timedelta,timezone
from pathlib import Path
TARGET_ID="{TARGET_ID}";TARGET_SHA="{TARGET_SHA}";AUTHORITY_SHA="{AUTHORITY_SHA}";SUPPORT_ID="{SUPPORT_ID}";SUPPORT_SHA="{SUPPORT_SHA}";ENTRY_SHA="{ENTRY_SHA}";CORE_SHA="{CORE_SHA}";DB="{DB}";CLIENT="{CLIENT}";ENTRY_REL="qualification/mysql_tpch_tpcc_shared_collector_entry_current_v1.py";CORE_REL="qualification/mysql_distributed_live_readback_qualification_v1.py";AUTHORITY_REL="mysql_tpch_distributed_v2_authority.py"
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def sealed(p,label):
 p=Path(p).resolve(strict=True);s=Path(str(p)+".sha256")
 if p.is_symlink() or not p.is_file() or s.is_symlink() or not s.is_file() or s.read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(label+" seal differs")
 v=json.loads(p.read_text(encoding="utf-8"))
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def bundle(p,bid,msha,label):
 p=Path(p).resolve(strict=True);mp,m=sealed(p/"bundle_manifest.json",label+" manifest")
 if p.name!=bid or m.get("bundle_id")!=bid or sha(mp)!=msha or m.get("formal_scope")!="mysql_only" or m.get("cross_engine_comparison_prohibited") is not True or m.get("postgresql_mutation_prohibited") is not True or m.get("formal_launch_authorized") is not False or m.get("accepted_round_authorized") is not False:raise Error(label+" identity/scope differs")
 return p,m
def import_file(p,name):s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def write(p,b,mode=0o600):
 if p.exists() or p.is_symlink():raise Error("refusing overwrite: "+str(p))
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.chmod(p,mode)
def seal(p,v):
 b=canonical(v);write(p,b);write(Path(str(p)+".sha256"),(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode("ascii"))
def live_identity(v):
 c=v.get("container") or {{}};t=v.get("database_counter_transport") or {{}};x={{"database_container_id":c.get("container_id"),"client_container_id":t.get("client_container_id")}}
 if any(not isinstance(z,str) or len(z)!=64 for z in x.values()):raise Error("fresh readback identity absent")
 return x
def refresh_spec(old,token,now,ttl):
 v=json.loads(json.dumps(old));v["created_at_utc"]=now.isoformat();v["expires_at_utc"]=(now+timedelta(seconds=ttl)).isoformat();v["main_agent_token_sha256"]=hashlib.sha256(token.encode()).hexdigest();return v
def refresh_evidence(old,spec,live,old_spec_sha,old_evidence_sha,live_sha):
 v=json.loads(json.dumps(old));v["spec_sha256"]=hashlib.sha256(canonical(spec)).hexdigest();v["collect_only"]={{"sha256":hashlib.sha256(canonical(live)).hexdigest(),"document":live}};v["same_live_refresh"]={{"schema_version":"mysql_tpch_same_live_bootstrap_refresh_binding_v1","predecessor_spec_sha256":old_spec_sha,"predecessor_evidence_sha256":old_evidence_sha,"fresh_collect_only_sha256":live_sha,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_core_sha256":CORE_SHA,"database_restart_performed":False,"client_restart_performed":False,"container_recreation_performed":False,"datadir_replaced":False,"expiry_bypass_performed":False,"manual_resigning_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False}}
 return v
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ("bundle","support-bundle","old-bootstrap-spec","old-bootstrap-evidence","mysql-defaults-file","output-dir"):p.add_argument("--"+n,type=Path,required=True)
 p.add_argument("--valid-seconds",type=int,default=1800);a=p.parse_args()
 if not 300<=a.valid_seconds<=3600:raise Error("fresh validity must be 300..3600 seconds")
 target,tm=bundle(a.bundle,TARGET_ID,TARGET_SHA,"ff313");support,sm=bundle(a.support_bundle,SUPPORT_ID,SUPPORT_SHA,"da455");authority_path=target/AUTHORITY_REL;entry=support/ENTRY_REL;core=support/CORE_REL
 if sha(authority_path)!=AUTHORITY_SHA or (tm.get("files") or {{}}).get(AUTHORITY_REL)!=AUTHORITY_SHA or sha(entry)!=ENTRY_SHA or sha(core)!=CORE_SHA:raise Error("validator/collector closure differs")
 oldsp,oldspec=sealed(a.old_bootstrap_spec,"old bootstrap spec");oldep,oldevidence=sealed(a.old_bootstrap_evidence,"old bootstrap evidence")
 if oldevidence.get("status")!="PASS_BOOTSTRAP_LIVE_NOT_R0_OR_FORMAL_AUTHORITY" or oldevidence.get("attempt_token")!=oldspec.get("attempt_token") or oldevidence.get("bundle_id")!=TARGET_ID or oldevidence.get("formal_r0_authorized") is not False or oldevidence.get("accepted_round_authorized") is not False:raise Error("old bootstrap identity/authority differs")
 defaults=a.mysql_defaults_file.resolve(strict=True)
 if defaults.is_symlink() or not defaults.is_file() or stat.S_IMODE(defaults.stat().st_mode)&0o077:raise Error("private MySQL defaults file required")
 out=a.output_dir
 if not out.is_absolute() or out.exists() or out.is_symlink():raise Error("fresh absolute output directory required")
 out.mkdir(parents=True,mode=0o700);readback=out/"fresh_tpch_collect_only.json";stdout=out/"collect_only.stdout";stderr=out/"collect_only.stderr"
 argv=[sys.executable,str(entry),"--collect-only","--bundle",str(target),"--container",DB,"--mysql-binary","/usr/bin/mysql","--mysql-client-container",CLIENT,"--mysql-binary-in-client","/usr/bin/mysql","--mysql-defaults-file",str(defaults),"--output",str(readback)]
 with stdout.open("xb") as so,stderr.open("xb") as se:r=subprocess.run(argv,stdout=so,stderr=se,timeout=900)
 if r.returncode:raise Error("fresh read-only TPC-H collect failed rc="+str(r.returncode))
 rp,live=sealed(readback,"fresh TPC-H collect-only");now_epoch=time.time();epoch=live.get("collected_epoch")
 if live.get("schema_version")!="mysql_algorithm_c_distributed_live_readback_v1" or live.get("status")!="PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY" or live.get("bundle_id")!=TARGET_ID or live.get("scenario")!="tpch_memory" or live.get("hostname")!="node04" or live.get("formal_launch_authorized") is not False or live.get("accepted_round_authorized") is not False or isinstance(epoch,bool) or not isinstance(epoch,(int,float)) or not -5<=now_epoch-float(epoch)<=120:raise Error("fresh TPC-H collect-only differs or stale")
 oldid=oldevidence.get("same_live_identity") or {{}};freshid=live_identity(live)
 if freshid!={{"database_container_id":oldid.get("database_container_id"),"client_container_id":oldid.get("client_container_id")}}:raise Error("same-live database/client identity changed")
 token=secrets.token_hex(48);tokenp=out/"same_live_refresh.token";write(tokenp,(token+"\n").encode());now=datetime.now(timezone.utc);spec=refresh_spec(oldspec,token,now,a.valid_seconds);specp=out/"fresh_bootstrap_spec.json";seal(specp,spec);authority=import_file(authority_path,"ff313_refresh_authority");authority.validate_spec(spec);evidence=refresh_evidence(oldevidence,spec,live,sha(oldsp),sha(oldep),sha(rp));identity=authority.validate_bootstrap_evidence(evidence,spec);evidence["same_live_identity"]=identity;authority.validate_bootstrap_evidence(evidence,spec);evidencep=out/"fresh_bootstrap_evidence.json";seal(evidencep,evidence)
 refresh_authority={{"schema_version":"mysql_tpch_same_live_bootstrap_refresh_authority_v1","status":"PASS_FRESH_SPEC_AND_EVIDENCE_VALIDATED_BY_FF313","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_SHA,"ff313_validator_sha256":AUTHORITY_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"attempt_token":spec["attempt_token"],"same_live_identity":identity,"old_bootstrap_spec_sha256":sha(oldsp),"old_bootstrap_evidence_sha256":sha(oldep),"fresh_collect_only_sha256":sha(rp),"fresh_bootstrap_spec_sha256":sha(specp),"fresh_bootstrap_evidence_sha256":sha(evidencep),"token_sha256":hashlib.sha256(token.encode()).hexdigest(),"created_at_utc":spec["created_at_utc"],"expires_at_utc":spec["expires_at_utc"],"database_restart_performed":False,"client_restart_performed":False,"container_recreation_performed":False,"datadir_replaced":False,"expiry_bypass_performed":False,"old_token_reused":False,"failed_output_reused":False,"manual_json_edit_required":False,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_workload_executed":False}}
 ap=out/"same_live_refresh.authority.json";seal(ap,refresh_authority);material={{"schema_version":"mysql_tpch_same_live_bootstrap_refresh_materialization_v1","status":"PASS_FRESH_FF313_BOOTSTRAP_INPUTS_NOT_WORKLOAD_AUTHORITY","paths":{{"bootstrap_spec":str(specp),"bootstrap_evidence":str(evidencep),"refresh_authority":str(ap),"token":str(tokenp),"fresh_collect_only":str(rp)}},"tpcc_69df_args":["--tpch-bootstrap-spec",str(specp),"--tpch-bootstrap-evidence",str(evidencep)],"expires_at_utc":spec["expires_at_utc"],"same_live_identity":identity,"database_restart_performed":False,"container_recreation_performed":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_workload_executed":False}};mp=out/"same_live_refresh.materialization.json";seal(mp,material);print(json.dumps({{"status":material["status"],"bootstrap_spec":str(specp),"bootstrap_evidence":str(evidencep),"materialization":str(mp),"expires_at_utc":spec["expires_at_utc"],"database_restart_performed":False,"remote_workload_executed":False}},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
TEST=r'''from datetime import datetime,timezone
import importlib.util,hashlib
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("r",B/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py");R=importlib.util.module_from_spec(s);s.loader.exec_module(R)
def old():return {"attempt_token":"tpch-v2-"+"a"*40,"created_at_utc":"2020-01-01T00:00:00+00:00","expires_at_utc":"2020-01-01T00:15:00+00:00","main_agent_token_sha256":"x"*64}
def test_refresh_spec_uses_fresh_bounded_window_and_token():
 now=datetime(2026,8,3,tzinfo=timezone.utc);v=R.refresh_spec(old(),"secret",now,1800);assert v["attempt_token"]==old()["attempt_token"] and v["created_at_utc"]==now.isoformat() and v["main_agent_token_sha256"]==hashlib.sha256(b"secret").hexdigest() and v["expires_at_utc"]!="2020-01-01T00:15:00+00:00"
def test_evidence_refresh_rebinds_spec_and_fresh_collect_only():
 spec=old();live={"x":1};v=R.refresh_evidence({"collect_only":{},"spec_sha256":"old"},spec,live,"a"*64,"b"*64,"c"*64);assert v["spec_sha256"]==hashlib.sha256(R.canonical(spec)).hexdigest() and v["collect_only"]["document"]==live and v["same_live_refresh"]["database_restart_performed"] is False and v["same_live_refresh"]["expiry_bypass_performed"] is False
def test_no_restart_no_expiry_bypass_no_workload():
 source=(B/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py").read_text();assert '"--collect-only"' in source and '"database_restart_performed":False' in source and '"expiry_bypass_performed":False' in source and '"remote_workload_executed":False' in source and 'docker restart' not in source and 'docker stop' not in source
def test_exact_ff313_and_da455_closure():assert R.TARGET_ID=="ff313374f846ac726244955c2fb27bed8266b93dd37df9624e5c8d4a56653da4" and R.SUPPORT_ID=="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6" and R.AUTHORITY_SHA=="120a848c63c6aaa5ecce24f55ed33d6b678d7a21e30ed2050b7a2efdd7a7b408"
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 content={"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py":MATERIALIZER.encode(),"tests/test_mysql_tpch_same_live_bootstrap_refresh_v1.py":TEST.encode(),"builder/build_mysql_tpch_same_live_bootstrap_refresh_v1.py":HERE.read_bytes(),"templates/remote_command.json":canonical({"schema_version":"mysql_tpch_same_live_bootstrap_refresh_remote_command_v1","refresh_argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py","--bundle","[REMOTE_FF313_BUNDLE]","--support-bundle","[REMOTE_DA455_BUNDLE]","--old-bootstrap-spec","[CURRENT_STALE_TPCH_BOOTSTRAP_SPEC_JSON]","--old-bootstrap-evidence","[CURRENT_TPCH_BOOTSTRAP_EVIDENCE_JSON]","--mysql-defaults-file","[BOOTSTRAP_ATTEMPT_ROOT]/credentials/mysql.cnf","--output-dir","[FRESH_ABSOLUTE_REFRESH_DIR]","--valid-seconds","1800"],"then_pass_to_69df":["--tpch-bootstrap-spec","[FRESH_ABSOLUTE_REFRESH_DIR]/fresh_bootstrap_spec.json","--tpch-bootstrap-evidence","[FRESH_ABSOLUTE_REFRESH_DIR]/fresh_bootstrap_evidence.json"],"manual_json_edit_required":False,"database_restart_performed":False,"workload_executed":False})};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_same_live_bootstrap_refresh_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_REFRESHER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"target_bundle_id":TARGET_ID,"target_manifest_sha256":TARGET_SHA,"ff313_validator_sha256":AUTHORITY_SHA,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"support_collector_entry_sha256":ENTRY_SHA,"support_collector_core_sha256":CORE_SHA,"fresh_read_only_collect_required":True,"ff313_validate_bootstrap_evidence_required":True,"same_live_database_and_client_required":True,"database_restart_performed":False,"container_recreation_performed":False,"expiry_bypass_performed":False,"old_token_reused":False,"failed_output_reused":False,"manual_json_edit_required":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_during_build":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);f=OUT/bid
 if f.exists():
  if sf(f/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return f
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".same-live-refresh-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,f)
 finally:
  if st.exists():shutil.rmtree(st)
 return f
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"materializer_sha256":sf(b/"materializer/mysql_tpch_same_live_bootstrap_refresh_v1.py"),"remote_actions_during_build":0},sort_keys=True))
