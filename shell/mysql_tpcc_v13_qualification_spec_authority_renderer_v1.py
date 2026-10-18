#!/usr/bin/env python3
"""Render one v13 TPC-C debug-qualification spec and short-lived authority.

This is a read-only renderer: it only inspects the adopted live client and
writes hash-closed command/authority documents.  Workload launch is delegated
to the sealed collector and never happens here.
"""
from __future__ import annotations
import argparse, hashlib, json, os, secrets, stat, subprocess, time
from pathlib import Path
V13="4e87faac238a2ecd1523f9dfc8044593243f732b3f1818a1f4f6fda560d0c8ab"
class Error(RuntimeError):pass
def canon(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(p):
 h=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
def load(p,what,side=False):
 p=Path(p).resolve(strict=True)
 if p.is_symlink() or not p.is_file():raise Error(what+" must be regular")
 if side and Path(str(p)+".sha256").read_text(encoding="ascii")!=sha(p)+"  "+p.name+"\n":raise Error(what+" sidecar differs")
 try:v=json.loads(p.read_text())
 except (UnicodeError,json.JSONDecodeError) as e:raise Error(what+" JSON differs") from e
 if not isinstance(v,dict):raise Error(what+" object required")
 return p,v
def inspect(full):
 r=subprocess.run(["docker","inspect",full],text=True,capture_output=True,check=False)
 if r.returncode:return (_ for _ in ()).throw(Error("live client inspect failed"))
 try:v=json.loads(r.stdout)
 except json.JSONDecodeError as e:raise Error("live client inspect invalid") from e
 if not isinstance(v,list) or len(v)!=1 or not isinstance(v[0],dict):raise Error("live client inspect cardinality differs")
 return v[0]
def mapped(container,mounts):
 q=Path(container);matches=[]
 for m in mounts:
  d=Path(m["destination"])
  if q==d or d in q.parents:matches.append((len(d.parts),d,Path(m["source"])))
 if not matches:raise Error("container path outside read-only mount closure: "+str(q))
 _,d,s=sorted(matches,reverse=True)[0];return s/q.relative_to(d)
def regular_hashes(path):
 path=Path(path)
 if path.is_symlink():raise Error("symlink in code closure")
 if path.is_file():return {str(path):sha(path)}
 if not path.is_dir():raise Error("code path absent")
 out={}
 for q in sorted(path.rglob("*")):
  if q.is_symlink() or (not q.is_file() and not q.is_dir()):raise Error("unsafe code closure entry")
  if q.is_file():out[str(q)]=sha(q)
 return out
def token(path):
 p=Path(path).resolve(strict=True);s=p.stat()
 if p.is_symlink() or not p.is_file() or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o600:raise Error("main token must be current-user 0600 file")
 x=p.read_text().strip()
 if len(x)<32 or any(c.isspace() for c in x):raise Error("main token differs")
 return x
def atomic(path,value,mode=0o600):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 if path.exists() or Path(str(path)+".sha256").exists():raise Error("refusing overwrite")
 tmp=path.with_name("."+path.name+"."+secrets.token_hex(6));raw=canon(value);tmp.write_bytes(raw);os.chmod(tmp,mode);os.replace(tmp,path);side=Path(str(path)+".sha256");side.write_text(sha(path)+"  "+path.name+"\n");os.chmod(side,mode)
def main():
 p=argparse.ArgumentParser(description=__doc__)
 for n in ("--bundle","--adopted-service","--dual-projection-authority","--main-agent-token-file","--measurement-root","--output-dir"):p.add_argument(n,type=Path,required=True)
 p.add_argument("--mode",choices=("qualification_solo","qualification_paired"),required=True);p.add_argument("--qualification-attempt-id",required=True);p.add_argument("--peer-binding",type=Path);p.add_argument("--valid-seconds",type=int,default=900);a=p.parse_args()
 if not 240<=a.valid_seconds<=1800 or len(a.qualification_attempt_id)<32:raise Error("TTL or qualification attempt ID differs")
 bundle=a.bundle.resolve(strict=True);manifest=load(bundle/"bundle_manifest.json","v13 manifest",True)[1]
 if bundle.name!=V13 or manifest.get("bundle_id")!=V13:raise Error("v13 bundle differs")
 _,adopted=load(a.adopted_service,"adopted service",True);_,dual=load(a.dual_projection_authority,"dual projection authority")
 if adopted.get("bundle_id")!=V13 or dual.get("status")!="PASS_SAME_INSPECT_DUAL_PROJECTION" or dual.get("attempt_id")!=adopted.get("attempt_id"):raise Error("same-live authority differs")
 client=adopted.get("client") or {};row=inspect(client.get("container_id",""));state=row.get("State") or {}
 if row.get("Id")!=client.get("container_id") or state.get("Pid")!=client.get("pid") or not state.get("Running"):raise Error("adopted client live identity differs")
 mounts=[]
 for m in row.get("Mounts") or []:
  if m.get("Type") in ("bind","volume") and m.get("RW") is False:mounts.append({"source":str(m.get("Source")),"destination":str(m.get("Destination"))})
 mounts.sort(key=lambda x:(x["source"],x["destination"]))
 if not mounts:raise Error("client readonly mount closure absent")
 # The frozen historical phase argv fixes the production classpath; only the
 # timing environment and attempt/metric bindings change for debug evidence.
 historical=Path(__file__).with_name("v13_historical_qualification_command.json");_,h=load(historical,"historical v13 qualification command",True)
 argv=list(h.get("argv") or [])
 if not argv or argv[0]!="/opt/mysql-tpcc-jdk/bin/java":raise Error("frozen TPC-C Java argv differs")
 code={}
 for value in argv:
  if value.startswith("/"):
   host=mapped(value,mounts);code.update(regular_hashes(host))
 for i,value in enumerate(argv[:-1]):
  if value in ("-cp","-classpath","--class-path"):
   for piece in argv[i+1].split(":"):code.update(regular_hashes(mapped(piece,mounts)))
 metric_root=a.measurement_root.resolve();metric=metric_root/(a.qualification_attempt_id+".metrics.jsonl")
 client_binding={"name":str(row.get("Name") or "").lstrip("/"),"container_id":row["Id"],"image_id":row.get("Image"),"created":row.get("Created"),"inspect_config_sha256":(dual["dual_projection_translation_authority"]["qualification_projection"]["sha256"]),"required_read_only_mounts":mounts}
 spec={"schema_version":"mysql_algorithm_c_debug_qualification_command_v1","bundle_id":V13,"mode":a.mode,"qualification_attempt_id":a.qualification_attempt_id,"argv":argv,"executable_sha256":code[str(mapped(argv[0],mounts))],"code_files":dict(sorted(code.items())),"workload_metric_stream":{"schema_version":"mysql_algorithm_c_debug_workload_metric_stream_v1","path":str(metric)},"workload_metric_adapter":{"schema_version":"mysql_algorithm_c_debug_workload_metric_adapter_v1","kind":"benchbase_tpcc"},"workload_process_isolation":{"cpuset_cpus":"8-15,44-51","cpuset_mems":"0","root_and_descendants_exact":True},"client_container":client_binding,"measurement_output":{"host_tmpfs_directory":str(metric_root),"container_tmpfs_destination":"/measurement"},"environment":{"BENCHBASE_FORMAL_STOP_FILE":"/measurement/benchbase-stop-request","BENCHBASE_FORMAL_WARMUP_SECONDS":"60","BENCHBASE_FORMAL_MAXIMUM_MEASUREMENT_SECONDS":"600"},"cwd":"/measurement","debug_only":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False}
 if a.mode=="qualification_paired":
  if a.peer_binding is None:raise Error("paired requires peer binding")
  _,peer=load(a.peer_binding,"peer binding",True);spec["paired_peer_workload"]=peer
 elif a.peer_binding is not None:raise Error("solo rejects peer binding")
 out=a.output_dir.resolve();command=out/(a.mode+".command.json");atomic(command,spec)
 t=token(a.main_agent_token_file);now=time.time();authority={"schema_version":"mysql_algorithm_c_main_agent_qualification_authority_v1","authorized_by":"main_agent","bundle_id":V13,"mode":a.mode,"qualification_attempt_id":a.qualification_attempt_id,"main_agent_token_sha256":hashlib.sha256(t.encode()).hexdigest(),"command_spec_sha256":sha(command),"debug_only":True,"capture_authorized":False,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False,"not_before_epoch":now-1,"expires_epoch":now+a.valid_seconds};auth=out/(a.mode+".authority.json");atomic(auth,authority)
 print(json.dumps({"status":"SEALED_V13_DEBUG_SPEC_AND_AUTHORITY_NOT_LAUNCHED","command":str(command),"authority":str(auth),"accepted_round_authorized":False},sort_keys=True))
if __name__=="__main__":
 try:main()
 except (Error,OSError,ValueError,KeyError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
