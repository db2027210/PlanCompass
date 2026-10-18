#!/usr/bin/env python3
"""Build the exact retry5 index168 journal/systemd failure sealer."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_retry5_index168_failure_sealer_v1"
SUPPORT_ID="4f2bbda6b350c9551f133840b7a350cbd7b77743004a6824a73a01df739073b1";SUPPORT_SHA="41e1bcc0acd25baf0fa7edb7d17e407e898e1785eafb7ef3b1fcc1a8b077c863";ROOT_CAUSE_REL="authority/mysql_tpch_retry5_index168_native_metric_root_cause_v1.json";ROOT_CAUSE_SHA="31ad90c606ca187f0549b40f12efe8114d082a905a5977d2850f85e53d0fef17";MATERIALIZER_ID="0e8edac7b6e30ae1e17e416f5e3f85deae252864a20a60cc6179d3fc8ad6afc4";MATERIALIZER_SHA="3395e86735eddc499eab7665f07ebb169741978d4e5e77693bcb3af10582263b"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(b):return hashlib.sha256(b).hexdigest()
def sf(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
SEALER=rf'''#!/usr/bin/env python3
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess,sys
from pathlib import Path
SUPPORT_ID="{SUPPORT_ID}";SUPPORT_SHA="{SUPPORT_SHA}";ROOT_CAUSE_REL="{ROOT_CAUSE_REL}";ROOT_CAUSE_SHA="{ROOT_CAUSE_SHA}";EXPECTED_UNIT="ra-mysql-tpch-qual-retry5-20260803t1300.service";FINAL_ERROR="NOT_READY: workload metric sample missing/stale/timed out: workload metric contiguous suffix is absent"
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
 v=json.loads(p.read_text());
 if not isinstance(v,dict):raise Error(label+" object required")
 return p,v
def support(p):
 p=Path(p).resolve(strict=True);mp,m=sealed(p/"bundle_manifest.json","4f2 manifest");rc=p/ROOT_CAUSE_REL
 if p.name!=SUPPORT_ID or m.get("bundle_id")!=SUPPORT_ID or sha(mp)!=SUPPORT_SHA or (m.get("files") or {{}}).get(ROOT_CAUSE_REL)!=ROOT_CAUSE_SHA or sha(rc)!=ROOT_CAUSE_SHA:raise Error("4f2 root-cause closure differs")
 root=json.loads(rc.read_text())
 if root.get("failed_attempt")!=EXPECTED_UNIT or root.get("observed_error")!="workload metric contiguous suffix is absent" or root.get("observed_last_successful_sample_index")!=168 or root.get("failed_attempt_credit")!=0:raise Error("4f2 root cause differs")
 return p,root
def write(p,b):
 if p.exists() or p.is_symlink():raise Error("refusing overwrite: "+str(p))
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.chmod(p,0o600)
def seal(p,v):
 b=canonical(v);write(p,b);write(Path(str(p)+".sha256"),(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode())
def collect(unit,start,end,journalctl):
 argv=[str(journalctl),"--no-pager","--output=json","--unit",unit,"--after-cursor",start];r=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
 if r.returncode:raise Error("journalctl failed rc="+str(r.returncode))
 rows=[]
 for line in r.stdout.splitlines():
  try:v=json.loads(line)
  except ValueError as e:raise Error("journal JSON differs") from e
  if not isinstance(v,dict) or not isinstance(v.get("__CURSOR"),str):raise Error("journal cursor record differs")
  rows.append(v)
  if v["__CURSOR"]==end:break
 if not rows or rows[-1].get("__CURSOR")!=end:raise Error("exact end cursor absent")
 return argv,rows
def systemd(unit,systemctl):
 argv=[str(systemctl),"show","--property=LoadState","--property=ActiveState","--property=SubState","--property=ExecMainCode","--property=ExecMainStatus",unit];r=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
 if r.returncode:raise Error("systemctl show failed")
 try:v=dict(x.split("=",1) for x in r.stdout.splitlines() if "=" in x)
 except ValueError as e:raise Error("systemd readback differs") from e
 if v.get("LoadState")!="loaded" or v.get("ActiveState")!="failed" or v.get("ExecMainStatus")!="2" or v.get("ExecMainCode") not in ("1","exited"):raise Error("exact systemd exit2 differs")
 return argv,v
def validate_rows(rows,attempt,unit,start,end):
 if start==end or rows[-1].get("__CURSOR")!=end or any(x.get("__CURSOR")==start for x in rows):raise Error("cursor interval differs")
 serial=[json.dumps(x,sort_keys=True) for x in rows];ai=[i for i,x in enumerate(serial) if attempt in x];fi=[i for i,x in enumerate(rows) if str(x.get("MESSAGE") or "").strip()==FINAL_ERROR];ui=[i for i,x in enumerate(serial) if unit in x]
 if not ai or not ui or len(fi)!=1 or fi[0]<ai[0]:raise Error("unique index168 failure/attempt/unit binding differs")
 times=[]
 for x in rows:
  if x.get("__REALTIME_TIMESTAMP") is not None:times.append(int(x["__REALTIME_TIMESTAMP"]))
 if times and times!=sorted(times):raise Error("journal realtime order differs")
 return ai,ui,fi[0],times
def main():
 p=argparse.ArgumentParser();p.add_argument("--support-bundle",type=Path,required=True);p.add_argument("--retry5-command",type=Path,required=True);p.add_argument("--retry5-materialization",type=Path,required=True);p.add_argument("--journal-unit",required=True);p.add_argument("--after-cursor",required=True);p.add_argument("--end-cursor",required=True);p.add_argument("--journalctl",type=Path,default=Path("/usr/bin/journalctl"));p.add_argument("--systemctl",type=Path,default=Path("/usr/bin/systemctl"));p.add_argument("--output",type=Path,required=True);a=p.parse_args()
 if a.journal_unit!=EXPECTED_UNIT or len(a.after_cursor)<8 or len(a.end_cursor)<8:raise Error("exact unit/cursors differ")
 _,root=support(a.support_bundle);cp,cmd=sealed(a.retry5_command,"retry5 command");mp,mat=sealed(a.retry5_materialization,"retry5 materialization");attempt=cmd.get("qualification_attempt_id");fresh=mat.get("fresh_paths") or {{}}
 if not isinstance(attempt,str) or len(attempt)<32 or mat.get("schema_version")!="mysql_tpch_qualification_retry5_completion_materialization_v1" or mat.get("status")!="SEALED_FRESH_RETRY5_NOT_LAUNCHED" or mat.get("qualification_attempt_id")!=attempt or Path(fresh.get("command","")).resolve()!=cp or mat.get("formal_launch_authorized") is not False or mat.get("accepted_round_authorized") is not False:raise Error("retry5 command/materialization differs")
 journalctl=a.journalctl.resolve(strict=True);systemctl=a.systemctl.resolve(strict=True)
 for x in (journalctl,systemctl):
  if x.is_symlink() or not x.is_file():raise Error("readback binary differs")
 jargv,rows=collect(a.journal_unit,a.after_cursor,a.end_cursor,journalctl);ai,ui,fi,times=validate_rows(rows,attempt,a.journal_unit,a.after_cursor,a.end_cursor);sargv,state=systemd(a.journal_unit,systemctl);segment=canonical(rows)
 evidence={{"schema_version":"mysql_tpch_retry5_index168_failure_tombstone_v1","status":"PASS_SEALED_RETRY5_INDEX168_FAILURE_ZERO_CREDIT","error":FINAL_ERROR,"qualification_attempt_id":attempt,"retry5_command_sha256":sha(cp),"retry5_materialization_sha256":sha(mp),"journal_unit":a.journal_unit,"journal_after_cursor_exclusive":a.after_cursor,"journal_end_cursor_inclusive":a.end_cursor,"journal_record_count":len(rows),"journal_segment_sha256":hashlib.sha256(segment).hexdigest(),"attempt_record_indexes":ai,"unit_record_indexes":ui,"failure_record_index":fi,"first_realtime_timestamp_usec":None if not times else times[0],"last_realtime_timestamp_usec":None if not times else times[-1],"journalctl_argv":jargv,"systemctl_argv":sargv,"systemd_readback":state,"systemd_exec_main_status":2,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"root_cause_sha256":ROOT_CAUSE_SHA,"predecessor_reuse_prohibited":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"workload_or_database_mutation_performed":False}}
 if not a.output.is_absolute() or not a.output.parent.is_dir():raise Error("fresh absolute output in existing directory required")
 seal(a.output,evidence);print(json.dumps({{"status":evidence["status"],"output":str(a.output),"qualification_attempt_id":attempt,"accepted_rounds_credit":0}},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
TEST=r'''import importlib.util,json
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("s",B/"sealer/mysql_tpch_retry5_index168_failure_sealer_v1.py");S=importlib.util.module_from_spec(s);s.loader.exec_module(S)
REAL_ERROR="NOT_READY: workload metric sample missing/stale/timed out: workload metric contiguous suffix is absent"
def rows(a):return [{"__CURSOR":"cursor001","__REALTIME_TIMESTAMP":"1","MESSAGE":"unit "+S.EXPECTED_UNIT+" attempt "+a},{"__CURSOR":"cursor002","__REALTIME_TIMESTAMP":"2","MESSAGE":REAL_ERROR,"_SYSTEMD_UNIT":S.EXPECTED_UNIT}]
def test_unique_error_attempt_unit_and_cursors():
 assert S.FINAL_ERROR==REAL_ERROR
 a="retry5-"+"a"*40;ai,ui,fi,t=S.validate_rows(rows(a),a,S.EXPECTED_UNIT,"cursor000","cursor002");assert ai==[0] and ui==[0,1] and fi==1 and t==[1,2]
def test_similar_or_duplicate_error_rejected():
 a="retry5-"+"a"*40;r=rows(a);r[1]["MESSAGE"]="prefix "+S.FINAL_ERROR
 with pytest.raises(S.Error):S.validate_rows(r,a,S.EXPECTED_UNIT,"cursor000","cursor002")
 r=rows(a)+[dict(rows(a)[1],__CURSOR="cursor003")]
 with pytest.raises(S.Error):S.validate_rows(r,a,S.EXPECTED_UNIT,"cursor000","cursor003")
def test_exact_exit2_readback(monkeypatch):
 class R:returncode=0;stderr="";stdout="LoadState=loaded\nActiveState=failed\nSubState=failed\nExecMainCode=1\nExecMainStatus=2\n"
 monkeypatch.setattr(S.subprocess,"run",lambda *a,**k:R());_,v=S.systemd(S.EXPECTED_UNIT,Path("/usr/bin/systemctl"));assert v["ExecMainStatus"]=="2"
def test_no_mutation_and_zero_credit():
 x=(B/"sealer/mysql_tpch_retry5_index168_failure_sealer_v1.py").read_text();assert '"accepted_rounds_credit":0' in x and '"workload_or_database_mutation_performed":False' in x and "subprocess.Popen" not in x
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 content={"sealer/mysql_tpch_retry5_index168_failure_sealer_v1.py":SEALER.encode(),"tests/test_mysql_tpch_retry5_index168_failure_sealer_v1.py":TEST.encode(),"builder/build_mysql_tpch_retry5_index168_failure_sealer_v1.py":HERE.read_bytes(),"templates/commands.json":canonical({"seal_argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/sealer/mysql_tpch_retry5_index168_failure_sealer_v1.py","--support-bundle","[REMOTE_4F2_BUNDLE]","--retry5-command","[FAILED_R5_COMMAND]","--retry5-materialization","[FAILED_R5_MATERIALIZATION]","--journal-unit","ra-mysql-tpch-qual-retry5-20260803t1300.service","--after-cursor","[EXACT_CURSOR_BEFORE_UNIT]","--end-cursor","[EXACT_FINAL_ERROR_CURSOR]","--output","[FRESH_ABSOLUTE_TOMBSTONE]"],"then_materializer_bundle_id":MATERIALIZER_ID,"manual_json_edit_required":False})};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_retry5_index168_failure_sealer_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_SEALER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"support_bundle_id":SUPPORT_ID,"support_manifest_sha256":SUPPORT_SHA,"root_cause_sha256":ROOT_CAUSE_SHA,"successor_materializer_bundle_id":MATERIALIZER_ID,"successor_materializer_manifest_sha256":MATERIALIZER_SHA,"exact_unit":"ra-mysql-tpch-qual-retry5-20260803t1300.service","exact_error":"workload metric contiguous suffix is absent","systemd_exec_main_status":2,"exact_cursor_interval_required":True,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);dst=OUT/bid
 if dst.exists():
  if sf(dst/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return dst
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".retry5-index168-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,dst)
 finally:
  if st.exists():shutil.rmtree(st)
 return dst
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"sealer_sha256":sf(b/"sealer/mysql_tpch_retry5_index168_failure_sealer_v1.py"),"remote_actions":0},sort_keys=True))
