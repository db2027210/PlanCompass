#!/usr/bin/env python3
"""Fail-closed v13 same-live continuation identity authority.

This program is deliberately read-only: its inspect inputs must be captured by
the caller from the currently live full container IDs.  It reconciles two
different, manifest-bound projections of *that same* client inspect record;
their SHA-256 values are not expected to be equal.
"""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, shutil, sys
from pathlib import Path

V13="4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
ATTEMPT="tpcc-v4-v13-typed-authority-20260731T035359Z-7f2419e89da29daf29e5935d"
ADOPTED_HASH="051485dabcf29fde6b77e0362445db5214d00e46e05fdd1543d2dcc98ef75023"
QUALIFICATION_HASH="dbadc39c276a942804f4b4338c1a96fa64e9819681cdb56592d3ec270f42899d"
DB="471dab9bd1c478050027c2fbee5481de6d41c577e20fee09d94236dec7c86c62"
CLIENT="4d951f5877ff6f9a693dd2a1ffceddd1565287929daa6c72e655431a9d187f35"
DB_PID,CLIENT_PID,DB_TICKS,CLIENT_TICKS=65097,65281,232732290,232732402

class Error(RuntimeError): pass
def canon(v): return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha_bytes(v): return hashlib.sha256(v).hexdigest()
def sha(p):
 h=hashlib.sha256()
 with p.open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def regular(p,what):
 try:p=Path(p).resolve(strict=True)
 except OSError as e:raise Error(what+" absent") from e
 if p.is_symlink() or not p.is_file():raise Error(what+" must be a regular file")
 return p
def load(p,what):
 try:v=json.loads(regular(p,what).read_text())
 except (UnicodeError,json.JSONDecodeError) as e:raise Error(what+" invalid JSON") from e
 if isinstance(v,list) and len(v)==1:v=v[0]
 if not isinstance(v,dict):raise Error(what+" requires a docker inspect object")
 return v
def sidecar(p,what):
 s=regular(str(p)+".sha256",what+" sidecar")
 if s.read_text(encoding="ascii")!=sha(p)+"  "+Path(p).name+"\n":raise Error(what+" sidecar differs")
def projection_adopted(row):return {k:row.get(k) for k in ("Config","HostConfig","Mounts","NetworkSettings")}
def projection_qualification(row):return {k:row.get(k) for k in ("Config","HostConfig","Mounts","Image","Created")}
def identity(row,what,expected,pid,ticks):
 if row.get("Id")!=expected:raise Error(what+" full ID differs")
 state=row.get("State") or {}
 if state.get("Pid")!=pid or not state.get("Running"):raise Error(what+" live PID/running state differs")
 # The caller's process readback is authoritative for start ticks, because
 # docker inspect does not expose /proc starttime.
 return state
def proc_ticks(pid):
 raw=Path("/proc/%d/stat"%pid).read_text(encoding="ascii");right=raw.rfind(")")
 return int(raw[right+2:].split()[19])
def cgroup_v2(pid):
 for line in Path("/proc/%d/cgroup"%pid).read_text(encoding="ascii").splitlines():
  parts=line.split(":",2)
  if len(parts)==3 and parts[0]=="0" and parts[1]=="":return parts[2]
 raise Error("unified cgroup path is absent")
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ("--adopted-service","--qualification-command","--typed-command","--same-live-handoff","--db-inspect","--client-inspect","--runtime-processes","--output-root"):p.add_argument(n,type=Path,required=True)
 a=p.parse_args()
 adopted=load(a.adopted_service,"adopted service");sidecar(a.adopted_service,"adopted service")
 qual=load(a.qualification_command,"qualification command");sidecar(a.qualification_command,"qualification command")
 typed=load(a.typed_command,"typed command");sidecar(a.typed_command,"typed command")
 handoff=load(a.same_live_handoff,"same-live handoff");sidecar(a.same_live_handoff,"same-live handoff")
 db=load(a.db_inspect,"database live inspect");client=load(a.client_inspect,"client live inspect");runtime=load(a.runtime_processes,"runtime process readback")
 if adopted.get("attempt_id")!=ATTEMPT or qual.get("attempt_id")!=ATTEMPT or typed.get("attempt_id")!=ATTEMPT or handoff.get("attempt_id")!=ATTEMPT:raise Error("historical authority attempt differs")
 if adopted.get("bundle_id")!=V13 or qual.get("bundle_id")!=V13 or typed.get("bundle_id")!=V13 or handoff.get("bundle_id")!=V13:raise Error("v13 bundle differs")
 identity(db,"database",DB,DB_PID,DB_TICKS);identity(client,"client",CLIENT,CLIENT_PID,CLIENT_TICKS)
 if runtime.get("old_workload_pids")!=[]:raise Error("old workload process is still live")
 for label,expected_id,expected_pid,expected_ticks in (("database",DB,DB_PID,DB_TICKS),("client",CLIENT,CLIENT_PID,CLIENT_TICKS)):
  observed=runtime.get(label) or {}
  if observed.get("container_id")!=expected_id or observed.get("pid")!=expected_pid or observed.get("process_start_ticks")!=expected_ticks:raise Error(label+" PID/startticks differs")
  if proc_ticks(expected_pid)!=expected_ticks or observed.get("cgroup_v2")!=cgroup_v2(expected_pid):raise Error(label+" current proc/cgroup differs")
 ap=sha_bytes(canon(projection_adopted(client))); qp=sha_bytes(canon(projection_qualification(client)))
 if ap!=ADOPTED_HASH:raise Error("adopted-service projection differs")
 if qp!=QUALIFICATION_HASH:raise Error("qualification projection differs")
 historical_client=(adopted.get("client") or {})
 if historical_client.get("container_id")!=CLIENT or historical_client.get("pid")!=CLIENT_PID or historical_client.get("process_start_ticks")!=CLIENT_TICKS or historical_client.get("inspect_config_sha256")!=ADOPTED_HASH:raise Error("adopted service client binding differs")
 for command in (qual,typed):
  c=command.get("client_container") or {}
  if c.get("container_id")!=CLIENT or c.get("pid")!=CLIENT_PID or c.get("process_start_ticks")!=CLIENT_TICKS or c.get("inspect_config_sha256")!=QUALIFICATION_HASH:raise Error("qualification/typed client binding differs")
 authority={"schema_version":"mysql_tpcc_v13_dual_projection_translation_authority_v1","status":"PASS_SAME_INSPECT_DUAL_PROJECTION","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"attempt_id":ATTEMPT,"v13_bundle_id":V13,"same_live_identity":{"database":{"container_id":DB,"pid":DB_PID,"process_start_ticks":DB_TICKS},"client":{"container_id":CLIENT,"pid":CLIENT_PID,"process_start_ticks":CLIENT_TICKS,"live_inspect_sha256":sha(a.client_inspect)}},"dual_projection_translation_authority":{"same_live_client_inspect_required":True,"hashes_equal":False,"equivalence_claim":"PROHIBITED_DIFFERENT_MANIFEST_BOUND_PROJECTIONS","adopted_service_projection":{"fields":["Config","HostConfig","Mounts","NetworkSettings"],"sha256":ap,"expected_sha256":ADOPTED_HASH},"qualification_projection":{"fields":["Config","HostConfig","Mounts","Image","Created"],"sha256":qp,"expected_sha256":QUALIFICATION_HASH}},"inputs":{"adopted_service":sha(a.adopted_service),"qualification_command":sha(a.qualification_command),"typed_command":sha(a.typed_command),"same_live_handoff":sha(a.same_live_handoff),"db_inspect":sha(a.db_inspect),"client_inspect":sha(a.client_inspect),"runtime_processes":sha(a.runtime_processes)},"formal_launch_authorized":False,"accepted_round_authorized":False}
 ident=sha_bytes(canon(authority));authority["authority_id"]=ident;root=a.output_root.resolve();root.mkdir(parents=True,exist_ok=True);target=root/ident
 if target.exists():
  if load(target/"dual_projection_translation_authority.json","existing authority")!=authority:raise Error("existing authority differs")
  print(json.dumps({"status":"PASS_IDEMPOTENT_SAME_INSPECT_DUAL_PROJECTION","authority":str(target)},sort_keys=True));return
 stage=root/(".stage-"+ident+"-"+secrets.token_hex(6));stage.mkdir(mode=0o700)
 try:
  (stage/"dual_projection_translation_authority.json").write_bytes(canon(authority));os.rename(stage,target)
 except BaseException:
  if stage.exists():shutil.rmtree(stage)
  raise
 print(json.dumps({"status":authority["status"],"authority":str(target),"authority_id":ident,"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (Error,OSError,ValueError,KeyError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
