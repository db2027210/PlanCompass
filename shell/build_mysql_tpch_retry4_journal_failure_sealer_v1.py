#!/usr/bin/env python3
"""Build a deterministic journal-cursor retry4 stale-marker failure sealer."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1];HERE=Path(__file__).resolve();OUT=ROOT/"artifacts/mysql_tpch_retry4_journal_failure_sealer_v1"
SUPPORT_ID="da455246652a82bb13d63f5f1ba6ebeb2e92fd08389e3f06707e5897e12646b6";SUPPORT_SHA="0f835d084459a2c66729327a9a4b9ed6ec070116e9873631874ac667039b34c4";ROOT_CAUSE_REL="authority/mysql_tpch_retry4_attempt2_native_marker_root_cause_v1.json";ROOT_CAUSE_SHA="20e9d0759b404bd7c2e9f27b8722da2a8b8efc8d0b6872ffaf0cdfeaabc76d3b";RETRY5_MATERIALIZER_ID="8481b98082e398c3086b9ea1e8d76eacb9e54ff1af3ce9faf125a3b982f6a9a0";RETRY5_MATERIALIZER_SHA="0edb6f0481193574ab083f60dbc4cd6bbf01ee1274b3b31bf45d25d352ad0889"
def canonical(v:Any)->bytes:return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sb(v:bytes)->str:return hashlib.sha256(v).hexdigest()
def sf(p:Path)->str:
 d=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
SEALER=rf'''#!/usr/bin/env python3
"""Read one exact journal cursor interval and seal retry4 stale failure."""
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess,sys
from pathlib import Path
SUPPORT_ID="{SUPPORT_ID}";SUPPORT_SHA="{SUPPORT_SHA}";ROOT_CAUSE_REL="{ROOT_CAUSE_REL}";ROOT_CAUSE_SHA="{ROOT_CAUSE_SHA}";FINAL_ERROR="native workload metric producer failed: native workload metric missing/stale/timed out: native workload marker is stale"
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
def support(p):
 p=Path(p).resolve(strict=True);mp,m=sealed(p/"bundle_manifest.json","da455 manifest");rc=p/ROOT_CAUSE_REL
 if p.name!=SUPPORT_ID or m.get("bundle_id")!=SUPPORT_ID or sha(mp)!=SUPPORT_SHA or (m.get("files") or {{}}).get(ROOT_CAUSE_REL)!=ROOT_CAUSE_SHA or sha(rc)!=ROOT_CAUSE_SHA:raise Error("da455/root-cause manifest closure differs")
 root=json.loads(rc.read_text())
 if root.get("status")!="ROOT_CAUSE_CONFIRMED_LOCAL_CODE_AUDIT_ZERO_CREDIT" or root.get("observed_failure")!="native workload marker is stale" or root.get("erroneous_next_request_sequence")!=181 or root.get("last_requested_successful_sequence")!=180 or root.get("missing_sample_threshold_relaxed") is not False or root.get("timeout_threshold_relaxed") is not False or root.get("freshness_threshold_relaxed") is not False:raise Error("da455 root cause differs")
 return p,mp,rc,root
def write(p,b):
 if p.exists() or p.is_symlink():raise Error("refusing overwrite: "+str(p))
 with p.open("xb") as f:f.write(b);f.flush();os.fsync(f.fileno())
 os.chmod(p,0o600)
def seal(p,v):
 b=canonical(v);write(p,b);write(Path(str(p)+".sha256"),(hashlib.sha256(b).hexdigest()+"  "+p.name+"\n").encode("ascii"))
def collect(unit,start,end,journalctl):
 argv=[str(journalctl),"--no-pager","--output=json","--unit",unit,"--after-cursor",start];r=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30)
 if r.returncode:raise Error("journalctl failed rc="+str(r.returncode)+" stderr="+r.stderr[-500:])
 rows=[];found=False
 for line in r.stdout.splitlines():
  try:v=json.loads(line)
  except ValueError as e:raise Error("journal JSON line differs") from e
  if not isinstance(v,dict) or not isinstance(v.get("__CURSOR"),str):raise Error("journal cursor record differs")
  rows.append(v)
  if v["__CURSOR"]==end:found=True;break
 if not found or not rows:raise Error("exact end cursor absent")
 return argv,rows
def validate_rows(rows,attempt,unit,start,end):
 if start==end or rows[-1].get("__CURSOR")!=end or any(v.get("__CURSOR")==start for v in rows):raise Error("journal cursor interval differs")
 serialized=[json.dumps(v,sort_keys=True,ensure_ascii=True) for v in rows];attempt_indexes=[i for i,x in enumerate(serialized) if attempt in x];failure_indexes=[i for i,v in enumerate(rows) if FINAL_ERROR in str(v.get("MESSAGE") or "")]
 if not attempt_indexes:raise Error("retry4 attempt absent from cursor interval")
 if len(failure_indexes)!=1 or failure_indexes[0]<attempt_indexes[0]:raise Error("unique final stale-marker failure absent or out of order")
 times=[]
 for v in rows:
  raw=v.get("__REALTIME_TIMESTAMP")
  if raw is not None:
   try:times.append(int(raw))
   except (TypeError,ValueError) as e:raise Error("journal realtime timestamp differs") from e
 if times and times!=sorted(times):raise Error("journal realtime order differs")
 return attempt_indexes,failure_indexes,times
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--support-bundle",type=Path,required=True);p.add_argument("--retry4-command",type=Path,required=True);p.add_argument("--retry4-materialization",type=Path,required=True);p.add_argument("--journal-unit",required=True);p.add_argument("--after-cursor",required=True);p.add_argument("--end-cursor",required=True);p.add_argument("--journalctl",type=Path,default=Path("/usr/bin/journalctl"));p.add_argument("--output",type=Path,required=True);a=p.parse_args()
 if not re.fullmatch(r"[A-Za-z0-9_.@:-]{{3,160}}",a.journal_unit) or len(a.after_cursor)<8 or len(a.end_cursor)<8:raise Error("journal authority arguments differ")
 sp,mp,rc,root=support(a.support_bundle);cp,command=sealed(a.retry4_command,"retry4 command");matp,material=sealed(a.retry4_materialization,"retry4 materialization");attempt=command.get("qualification_attempt_id")
 if not isinstance(attempt,str) or len(attempt)<32 or material.get("schema_version")!="mysql_tpch_qualification_retry4_materialization_v1" or material.get("qualification_attempt_id")!=attempt or material.get("status")!="SEALED_FRESH_RETRY4_NOT_LAUNCHED" or Path((material.get("fresh_paths") or {{}}).get("command","")).resolve()!=cp or material.get("formal_launch_authorized") is not False or material.get("accepted_round_authorized") is not False:raise Error("retry4 materialization/command differs")
 journalctl=a.journalctl.resolve(strict=True)
 if journalctl.is_symlink() or not journalctl.is_file():raise Error("journalctl binary differs")
 argv,rows=collect(a.journal_unit,a.after_cursor,a.end_cursor,journalctl);attempt_indexes,failure_indexes,times=validate_rows(rows,attempt,a.journal_unit,a.after_cursor,a.end_cursor);segment=canonical(rows)
 evidence={{"schema_version":"mysql_tpch_retry4_stale_marker_journal_tombstone_v1","status":"PASS_SEALED_RETRY4_STALE_MARKER_FAILURE_ZERO_CREDIT","error":FINAL_ERROR,"qualification_attempt_id":attempt,"retry4_command_sha256":sha(cp),"retry4_materialization_sha256":sha(matp),"journal_unit":a.journal_unit,"journal_after_cursor_exclusive":a.after_cursor,"journal_end_cursor_inclusive":a.end_cursor,"journal_record_count":len(rows),"journal_segment_sha256":hashlib.sha256(segment).hexdigest(),"journalctl_argv":argv,"journalctl_binary_sha256":sha(journalctl),"attempt_binding_record_indexes":attempt_indexes,"failure_record_index":failure_indexes[0],"first_realtime_timestamp_usec":None if not times else times[0],"last_realtime_timestamp_usec":None if not times else times[-1],"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"root_cause_relative":ROOT_CAUSE_REL,"root_cause_sha256":ROOT_CAUSE_SHA,"root_cause_observed_failure":root["observed_failure"],"process_inventory_failure_artifact_is_final_stale_marker_evidence":False,"predecessor_reuse_prohibited":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_launch_authorized":False,"formal_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"workload_or_database_mutation_performed":False}}
 if not a.output.is_absolute() or not a.output.parent.is_dir():raise Error("absolute output in existing directory required")
 seal(a.output,evidence);print(json.dumps({{"status":evidence["status"],"output":str(a.output),"output_sha256":sha(a.output),"qualification_attempt_id":attempt,"accepted_rounds_credit":0}},sort_keys=True));return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except (Error,OSError,UnicodeError,ValueError,KeyError,subprocess.TimeoutExpired) as e:print("NOT_READY: "+str(e),file=sys.stderr);raise SystemExit(2)
'''
TEST=r'''import importlib.util,json
from pathlib import Path
import pytest
B=Path(__file__).resolve().parents[1];s=importlib.util.spec_from_file_location("j",B/"sealer/mysql_tpch_retry4_journal_failure_sealer_v1.py");J=importlib.util.module_from_spec(s);s.loader.exec_module(J)
def rows(a):return [{"__CURSOR":"c1","__REALTIME_TIMESTAMP":"1","MESSAGE":"launch "+a},{"__CURSOR":"c2","__REALTIME_TIMESTAMP":"2","MESSAGE":J.FINAL_ERROR}]
def test_exact_attempt_and_unique_failure():
 a="retry4-"+"a"*40;ai,fi,t=J.validate_rows(rows(a),a,"u.service","startcur","c2");assert ai==[0] and fi==[1] and t==[1,2]
def test_missing_attempt_or_failure_rejected():
 a="retry4-"+"a"*40
 with pytest.raises(J.Error,match="attempt absent"):J.validate_rows(rows("other"),a,"u","start","c2")
 bad=rows(a);bad[1]["MESSAGE"]="not failure"
 with pytest.raises(J.Error,match="failure absent"):J.validate_rows(bad,a,"u","start","c2")
def test_cursor_and_order_fail_closed():
 a="retry4-"+"a"*40;r=rows(a);r[0]["__REALTIME_TIMESTAMP"]="3"
 with pytest.raises(J.Error,match="order"):J.validate_rows(r,a,"u","start","c2")
def test_exact_read_only_journalctl_argv(monkeypatch,tmp_path):
 class R:returncode=0;stderr="";stdout=json.dumps(rows("retry4-"+"a"*40)[0])+"\n"+json.dumps(rows("retry4-"+"a"*40)[1])+"\n"
 seen=[]
 def run(argv,**kw):seen.append(argv);return R()
 monkeypatch.setattr(J.subprocess,"run",run);argv,out=J.collect("u.service","start","c2",Path("/usr/bin/journalctl"));assert argv==["/usr/bin/journalctl","--no-pager","--output=json","--unit","u.service","--after-cursor","start"] and out[-1]["__CURSOR"]=="c2"
def test_sealer_never_executes_workload_or_mutates_db():
 source=(B/"sealer/mysql_tpch_retry4_journal_failure_sealer_v1.py").read_text();assert 'argv=[str(journalctl),"--no-pager","--output=json","--unit",unit,"--after-cursor",start]' in source and '"workload_or_database_mutation_performed":False' in source and "subprocess.Popen" not in source
'''
def freeze(r):
 for p in sorted(r.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
 os.chmod(r,0o555)
def build():
 content={"sealer/mysql_tpch_retry4_journal_failure_sealer_v1.py":SEALER.encode(),"tests/test_mysql_tpch_retry4_journal_failure_sealer_v1.py":TEST.encode(),"builder/build_mysql_tpch_retry4_journal_failure_sealer_v1.py":HERE.read_bytes(),"templates/remote_commands.json":canonical({"schema_version":"mysql_tpch_retry4_journal_failure_remote_commands_v1","seal_argv":["[SEALED_PYTHON]","[THIS_BUNDLE]/sealer/mysql_tpch_retry4_journal_failure_sealer_v1.py","--support-bundle","[REMOTE_DA455_BUNDLE]","--retry4-command","[ATTEMPT2_RETRY4_COMMAND_JSON]","--retry4-materialization","[ATTEMPT2_RETRY4_MATERIALIZATION_JSON]","--journal-unit","[EXACT_ATTEMPT2_SYSTEMD_UNIT]","--after-cursor","[EXACT_CURSOR_BEFORE_ATTEMPT2]","--end-cursor","[EXACT_CURSOR_OF_FINAL_STALE_MARKER]","--output","[FRESH_ABSOLUTE_TOMBSTONE_JSON]"],"then_retry5_materialize_argv":["[SEALED_PYTHON]","[RETRY5_BUNDLE]/materializer/mysql_tpch_qualification_retry5_completion_materializer_v1.py","--bundle","[REMOTE_FF313_BUNDLE]","--support-bundle","[REMOTE_DA455_BUNDLE]","--retry4-command","[ATTEMPT2_RETRY4_COMMAND_JSON]","--retry4-failure-evidence","[FRESH_ABSOLUTE_TOMBSTONE_JSON]","--supplement","[BOOTSTRAP_ROOT]/blkio_throttle_authority.supplement.json","--mysql-defaults-file","[BOOTSTRAP_ROOT]/credentials/mysql.cnf","--output-dir","[FRESH_RETRY5_DIR]","--valid-seconds","1800"],"manual_json_edit_required":False,"workload_execution_performed_by_sealer":False,"database_mutation_performed_by_sealer":False})};files={n:sb(v) for n,v in sorted(content.items())};u={"schema_version":"mysql_tpch_retry4_journal_failure_sealer_bundle_v1","status":"PASS_IMMUTABLE_LOCAL_SEALER_NOT_EXECUTED","immutable":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"support_collector_bundle_id":SUPPORT_ID,"support_collector_manifest_sha256":SUPPORT_SHA,"root_cause_relative":ROOT_CAUSE_REL,"root_cause_sha256":ROOT_CAUSE_SHA,"retry5_materializer_bundle_id":RETRY5_MATERIALIZER_ID,"retry5_materializer_manifest_sha256":RETRY5_MATERIALIZER_SHA,"exact_journal_unit_and_cursor_interval_required":True,"retry4_attempt_command_materialization_binding_required":True,"manual_json_edit_required":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions_during_build":0,"files":files};bid=sb(canonical(u));m=dict(u,bundle_id=bid);f=OUT/bid
 if f.exists():
  if sf(f/"bundle_manifest.json")!=sb(canonical(m)):raise RuntimeError("existing differs")
  return f
 OUT.mkdir(parents=True,exist_ok=True);st=Path(tempfile.mkdtemp(prefix=".retry4-journal-",dir=OUT))
 try:
  for n,v in content.items():p=st/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(m);(st/"bundle_manifest.json").write_bytes(raw);(st/"bundle_manifest.json.sha256").write_text(sb(raw)+"  bundle_manifest.json\n");freeze(st);os.replace(st,f)
 finally:
  if st.exists():shutil.rmtree(st)
 return f
if __name__=="__main__":
 b=build();print(json.dumps({"bundle":str(b),"bundle_id":b.name,"manifest_sha256":sf(b/"bundle_manifest.json"),"sealer_sha256":sf(b/"sealer/mysql_tpch_retry4_journal_failure_sealer_v1.py"),"remote_actions_during_build":0},sort_keys=True))
