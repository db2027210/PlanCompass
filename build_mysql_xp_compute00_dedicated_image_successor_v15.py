#!/usr/bin/env python3
"""Build immutable XP v15 dedicated-daemon image publication materials."""
from __future__ import annotations
import hashlib,json,os,shutil,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent; HERE=Path(__file__).resolve()
OUT=ROOT/"artifacts/mysql_xp_compute00_dedicated_image_successor_v15"
IMAGE_ID="sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
REPO_DIGEST="mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
DATA_ID="3b91c974bb07539e2b0927401c8bf30efcb43e44ce57e8e297a0cb85aff560dd"
DATA_SHA="5f436c19d4431fd952eb7a2ac42e12cc6014d8785cc6a9e01655a6eac58df552"
FAILED_ATTEMPT="xp-formal-qual-20260803T132047Z-77435774a1e56e8986285441"
FAILED_UNIT="ra-mysql-xp-v12-bootstrap-8291df7e98fff472.service"

def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha_file(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()

PUBLISHER=r'''#!/usr/bin/env python3
"""Publish one exact frozen image from the default daemon to the XP daemon."""
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess
from pathlib import Path

IMAGE_ID="sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
REPO_DIGEST="mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
SOURCE="unix:///var/run/docker.sock"; DEST="unix:///run/ra-docker-mysql-xp-v2.sock"
RUNTIME="/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803"
ATTEMPT_RE=re.compile(r"xp-formal-qual-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{24}")
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
def sha_file(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def run(argv,stdin=None,stdout=subprocess.PIPE):
 r=subprocess.run(argv,stdin=stdin,stdout=stdout,stderr=subprocess.PIPE,check=False)
 if r.returncode:raise Error("command failed (%d): %s: %s"%(r.returncode," ".join(argv),r.stderr.decode("utf-8","replace").strip()))
 return r.stdout
def inspect(host,ref,allow_absent=False):
 r=subprocess.run(["docker","--host",host,"image","inspect",ref],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
 if r.returncode:
  if allow_absent and b"No such image" in r.stderr:return None
  raise Error("image inspect failed: "+r.stderr.decode("utf-8","replace").strip())
 rows=json.loads(r.stdout)
 if len(rows)!=1:return None
 row=rows[0]; layers=row.get("RootFS",{}).get("Layers")
 if row.get("Id")!=IMAGE_ID or REPO_DIGEST not in (row.get("RepoDigests") or []) or not isinstance(layers,list) or not layers or any(not re.fullmatch(r"sha256:[0-9a-f]{64}",x or "") for x in layers):raise Error("frozen image identity/digest/layers differ")
 return {"Id":row["Id"],"RepoDigests":sorted(row["RepoDigests"]),"Layers":layers,"layer_count":len(layers)}
def atomic_json(path,value):
 path=Path(path);side=Path(str(path)+".sha256")
 if path.exists() or side.exists() or path.is_symlink() or side.is_symlink():raise Error("refusing to overwrite image authority")
 raw=canonical(value);tmp=path.with_name("."+path.name+".%d.tmp"%os.getpid());stmp=side.with_name("."+side.name+".%d.tmp"%os.getpid())
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  os.write(fd,raw);os.fsync(fd)
 finally:os.close(fd)
 stmp.write_text(hashlib.sha256(raw).hexdigest()+"  "+path.name+"\n",encoding="ascii");os.chmod(stmp,0o600);os.replace(tmp,path);os.replace(stmp,side)
def publish(attempt,output,unit):
 if ATTEMPT_RE.fullmatch(attempt) is None:raise Error("attempt identity differs")
 expected=Path(RUNTIME)/"image_authorities"/attempt/"frozen_image_publication.json";output=Path(output)
 if output!=expected:raise Error("image authority path differs")
 if output.parent.exists() or output.parent.is_symlink():raise Error("fresh image authority root must be absent")
 output.parent.mkdir(parents=True,mode=0o700)
 source=inspect(SOURCE,IMAGE_ID); pre=inspect(DEST,IMAGE_ID,True); archive=output.parent/".frozen-image.tar.partial"
 loaded=False;archive_sha=None
 if pre is None:
  with archive.open("xb") as stream:run(["docker","--host",SOURCE,"save",REPO_DIGEST],stdout=stream)
  archive_sha=sha_file(archive)
  with archive.open("rb") as stream:run(["docker","--host",DEST,"load"],stream)
  loaded=True
 post=inspect(DEST,IMAGE_ID)
 if post!=source:raise Error("dedicated image readback differs from source")
 value={"schema_version":"mysql_xp_dedicated_frozen_image_publication_authority_v1","status":"PASS","attempt_id":attempt,"executor_unit":unit,"source_daemon":SOURCE,"dedicated_daemon":DEST,"image_id":IMAGE_ID,"required_repo_digest":REPO_DIGEST,"source_inspect":source,"dedicated_inspect":post,"layers_exact_match":True,"loaded_in_this_attempt":loaded,"archive_sha256":archive_sha,"partial_failure_policy":"FAIL_CLOSED_INVALIDATE_ATTEMPT_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 atomic_json(output,value)
 if archive.exists():archive.unlink()
 return value
def main():
 p=argparse.ArgumentParser();p.add_argument("--attempt-id",required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--executor-unit",required=True);a=p.parse_args();print(json.dumps(publish(a.attempt_id,a.output,a.executor_unit),sort_keys=True))
if __name__=="__main__":
 try:main()
 except Error as e:raise SystemExit("NOT_READY: "+str(e))
'''

MATERIALIZER=r'''#!/usr/bin/env python3
"""Seal a fresh XP v15 attempt with image publication and split outputs."""
from __future__ import annotations
import argparse,copy,hashlib,json,os,shutil,stat
from pathlib import Path
DATA_ID="3b91c974bb07539e2b0927401c8bf30efcb43e44ce57e8e297a0cb85aff560dd";DATA_SHA="5f436c19d4431fd952eb7a2ac42e12cc6014d8785cc6a9e01655a6eac58df552"
IMAGE_ID="sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172";REPO_DIGEST="mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
FAILED_ATTEMPT="xp-formal-qual-20260803T132047Z-77435774a1e56e8986285441";FAILED_UNIT="ra-mysql-xp-v12-bootstrap-8291df7e98fff472.service"
RUNTIME="/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803"
PRIVATE=("private_authority/main_agent_token","private_credentials/hybench_xp_qualification.properties","private_credentials/mysql-client.cnf","private_credentials/mysql-root-bootstrap.cnf")
class Error(RuntimeError):pass
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2,ensure_ascii=True,allow_nan=False)+"\n").encode()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha_file(p):
 d=hashlib.sha256()
 with Path(p).open("rb") as f:
  for b in iter(lambda:f.read(1048576),b""):d.update(b)
 return d.hexdigest()
def load(p):return json.loads(Path(p).read_text())
def replace_strings(v,old,new):
 if isinstance(v,str):return v.replace(old,new)
 if isinstance(v,list):return [replace_strings(x,old,new) for x in v]
 if isinstance(v,dict):return {k:replace_strings(x,old,new) for k,x in v.items()}
 return v
def outarg(a):return a[a.index("--output")+1]
def replace_output(a,old,new):
 a=list(a);i=a.index("--output")+1
 if a[i]!=old:raise Error("old output differs")
 a[i]=new;return a
def write(root,name,v,mode=0o444):
 p=root/name;raw=canonical(v);p.write_bytes(raw);os.chmod(p,mode);s=Path(str(p)+".sha256");s.write_text(sha_bytes(raw)+"  "+p.name+"\n");os.chmod(s,mode);return sha_bytes(raw)
def poll(unit,expected):
 return {"argv":["systemctl","show",unit,"--property=Id","--property=Transient","--property=RemainAfterExit","--property=ExecStart","--property=ActiveState","--property=SubState","--property=ExecMainStatus","--property=Result","--property=MainPID","--property=ControlGroup"],"expected_exec_start_argv":expected,"expected_exec_start_argv_sha256":sha_bytes(canonical(expected)),"poll_interval_seconds":1,"deadline_seconds":1800,"running_state":{"Id":unit,"Transient":"yes","RemainAfterExit":"yes","ActiveState":"activating","SubState":"start","MainPID":"positive","ControlGroup":"/system.slice/"+unit},"success_state":{"Id":unit,"Transient":"yes","RemainAfterExit":"yes","ActiveState":"active","SubState":"exited","ExecMainStatus":"0","Result":"success","MainPID":"0","ControlGroup":"/system.slice/"+unit},"all_other_states":"terminal_failure_or_fail_closed","timeout_policy":"fail_closed_and_abandon_attempt","stop_prohibited":True,"reset_failed_prohibited":True,"collect_prohibited":True,"unit_retained_for_audit":True}
def materialize(base,output_root,support_id):
 base=Path(base).resolve();m=load(base/"materials_manifest.json");spec=load(base/"bootstrap_spec.json");plan=load(base/"command_plan.json");install=load(base/"authorization_install_plan.json")
 if m["bundle_id"]!=DATA_ID or m["bundle_manifest_sha256"]!=DATA_SHA or m["formal_scope"]!="mysql_only" or not m["postgresql_mutation_prohibited"]:raise Error("base closure/scope differs")
 for name,h in m["files"].items():
  if sha_file(base/name)!=h:raise Error("base file differs: "+name)
 attempt=m["attempt_id"]
 if attempt==FAILED_ATTEMPT:raise Error("failed attempt reuse prohibited")
 output=Path(output_root).resolve()/attempt
 if output.exists():raise Error("successor attempt materials already exist")
 attempt_root=spec["paths"]["attempt_root"];final_output=spec["paths"]["adoption"];bootstrap_output=attempt_root+"/bootstrap_adoption.json"
 unit_suffix=hashlib.sha256(attempt.encode()).hexdigest()[:16];units={x:"ra-mysql-xp-v15-%s-%s.service"%(x,unit_suffix) for x in ("image","copy","jdk","plan","bootstrap","adopt")}
 spec=replace_strings(copy.deepcopy(spec),"ra-mysql-xp-v12-","ra-mysql-xp-v15-");plan=replace_strings(copy.deepcopy(plan),"ra-mysql-xp-v12-","ra-mysql-xp-v15-")
 spec=replace_strings(spec,str(base),str(output));plan=replace_strings(plan,str(base),str(output));install=replace_strings(install,str(base),str(output))
 spec["schema_version"]="mysql_xp_successor_qualification_bootstrap_spec_v3_dedicated_image_output_split";spec["status"]="QUALIFICATION_ONLY_FRESH_IMAGE_GATE_NOT_LAUNCHED";spec["attempt"]["executor_units"]=units
 image_authority=RUNTIME+"/image_authorities/"+attempt+"/frozen_image_publication.json";spec["paths"]["bootstrap_adoption"]=bootstrap_output;spec["paths"]["image_authority"]=image_authority
 spec["dedicated_frozen_image_gate"]={"source_daemon":"unix:///var/run/docker.sock","dedicated_daemon":"unix:///run/ra-docker-mysql-xp-v2.sock","image_id":IMAGE_ID,"required_repo_digest":REPO_DIGEST,"source_exact_id_digest_layers_required":True,"transport":"docker_save_load_only","dedicated_exact_id_digest_layers_readback_required":True,"sealed_non_overwritable_authority":image_authority,"must_pass_before_physical_copy":True,"partial_failure_policy":"FAIL_CLOSED_INVALIDATE_ATTEMPT_ZERO_CREDIT"}
 spec["output_authority_contract"]={"bootstrap_output":bootstrap_output,"final_adopt_output":final_output,"distinct":True,"attempt_owned":True,"overwrite_prohibited":True,"accepted_rounds_credit":0}
 remote_support=RUNTIME+"/control_bundles/"+support_id
 image_direct=["taskset","-c","184-191,440-447","env","PYTHONDONTWRITEBYTECODE=1","python3","-B",remote_support+"/runtime/mysql_xp_dedicated_frozen_image_publication_v1.py","--attempt-id",attempt,"--output",image_authority,"--executor-unit",units["image"]]
 image_host=["systemd-run","--quiet","--service-type=oneshot","--remain-after-exit","--unit="+units["image"],"--"]+image_direct
 direct=plan["commands_to_run_on_compute00_after_separate_authorization"];host=plan["host_systemd_run_commands_after_separate_authorization"];polls=plan["controller_poll_after_each_systemd_run"]
 for c in (direct,host):c["bootstrap"]=replace_output(c["bootstrap"],final_output,bootstrap_output)
 polls["bootstrap"]["expected_exec_start_argv"]=replace_output(polls["bootstrap"]["expected_exec_start_argv"],final_output,bootstrap_output);polls["bootstrap"]["expected_exec_start_argv_sha256"]=sha_bytes(canonical(polls["bootstrap"]["expected_exec_start_argv"]))
 direct["frozen_image_publish"]=image_direct;host["frozen_image_publish"]=image_host;polls["frozen_image_publish"]=poll(units["image"],image_direct)
 order=plan["execution_order"];anchor=order.index("physical_copy");order[anchor:anchor]=["publish_frozen_image_to_dedicated_daemon","verify_sealed_dedicated_image_authority"]
 plan["schema_version"]="mysql_xp_successor_qualification_command_plan_v4_dedicated_image_output_split";plan["status"]="PLANNED_FRESH_IMAGE_GATE_NOT_LAUNCHED";plan["dedicated_frozen_image_gate"]=spec["dedicated_frozen_image_gate"];plan["output_authority_gate"]={"bootstrap_output":bootstrap_output,"final_adopt_output":final_output,"distinct":True,"overwrite_or_delete_prohibited":True,"failure_policy":"FAIL_CLOSED_ABANDON_ATTEMPT"}
 spec_sha=sha_bytes(canonical(spec));plan["spec_sha256"]=spec_sha
 install["schema_version"]="mysql_xp_external_authorization_install_plan_v2_dedicated_image";install["status"]="PLANNED_FRESH_IMAGE_GATE_NOT_INSTALLED";install["source_materials_directory"]=str(output)
 steps=install["strict_install_steps"]
 for s in steps:
  if s["step"]>=5:s["step"]+=2
 steps.insert(4,{"step":5,"action":"publish_exact_frozen_image_default_to_dedicated_daemon_via_docker_save_load","authority":image_authority,"failure_policy":"INVALIDATE_ATTEMPT_ZERO_CREDIT"});steps.insert(5,{"step":6,"action":"verify_dedicated_inspect_exact_id_repo_digest_layers_and_sealed_authority","failure_policy":"INVALIDATE_ATTEMPT_ZERO_CREDIT"})
 output.parent.mkdir(parents=True,exist_ok=True);output.mkdir(mode=0o700)
 for rel in PRIVATE:
  dst=output/rel;dst.parent.mkdir(parents=True,exist_ok=True,mode=0o700);shutil.copyfile(base/rel,dst);os.chmod(dst,0o600)
 hashes={"bootstrap_spec.json":write(output,"bootstrap_spec.json",spec),"command_plan.json":write(output,"command_plan.json",plan),"authorization_install_plan.json":write(output,"authorization_install_plan.json",install)}
 manifest={"schema_version":"mysql_xp_successor_qualification_materials_manifest_v4_dedicated_image_output_split","status":"SEALED_QUALIFICATION_ONLY_FRESH_IMAGE_GATE_NOT_LAUNCHED","attempt_id":attempt,"created_at_utc":m["created_at_utc"],"expires_at_utc":m["expires_at_utc"],"bundle_id":DATA_ID,"bundle_manifest_sha256":DATA_SHA,"support_bundle_id":support_id,"files":dict(hashes,**{x:sha_file(output/x) for x in PRIVATE}),"formal_gate_binding":m["formal_gate_binding"],"dedicated_frozen_image_gate":spec["dedicated_frozen_image_gate"],"output_authority_contract":spec["output_authority_contract"],"invalidated_failed_attempts":[{"attempt_id":FAILED_ATTEMPT,"failed_unit":FAILED_UNIT,"fresh_error":"dedicated daemon missing exact frozen image","formal_r0_credit":0,"accepted_rounds_credit":0,"rerun_prohibited":True}],"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_r0_authorized":False,"formal_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"data_plane_actions":0}
 manifest_sha=write(output,"materials_manifest.json",manifest)
 return {"status":manifest["status"],"attempt_id":attempt,"expires_at_utc":m["expires_at_utc"],"materials_directory":str(output),"materials_manifest_sha256":manifest_sha,"bootstrap_spec_sha256":hashes["bootstrap_spec.json"],"command_plan_sha256":hashes["command_plan.json"],"image_unit":units["image"],"data_plane_actions":0}
def main():
 p=argparse.ArgumentParser();p.add_argument("--base-materials",type=Path,required=True);p.add_argument("--output-root",type=Path,required=True);a=p.parse_args();support=Path(__file__).resolve().parents[1];print(json.dumps(materialize(a.base_materials,a.output_root,support.name),sort_keys=True))
if __name__=="__main__":main()
'''

TEST=r'''from __future__ import annotations
import hashlib,importlib.util,json,os,shutil,tempfile
from pathlib import Path
B=Path(__file__).resolve().parents[1];BASE=Path(os.environ["XP_V15_BASE"])
s=importlib.util.spec_from_file_location("m",B/"materializer/mysql_xp_v15_materializer.py");M=importlib.util.module_from_spec(s);s.loader.exec_module(M)
q=importlib.util.spec_from_file_location("p",B/"runtime/mysql_xp_dedicated_frozen_image_publication_v1.py");P=importlib.util.module_from_spec(q);q.loader.exec_module(P)
def load(p):return json.loads(Path(p).read_text())
def out(a):return a[a.index("--output")+1]
def test_successor_materials():
 root=Path(tempfile.mkdtemp());
 try:
  r=M.materialize(BASE,root,B.name);d=Path(r["materials_directory"]);m=load(d/"materials_manifest.json");p=load(d/"command_plan.json");s=load(d/"bootstrap_spec.json")
  assert m["formal_scope"]=="mysql_only" and m["postgresql_mutation_prohibited"] and m["accepted_rounds_credit"]==0
  assert m["formal_gate_binding"]["database_cpu_quota_cores"]==5.3 and m["formal_gate_binding"]["database_io_read_bps"]==29360128 and m["formal_gate_binding"]["database_io_write_bps"]==524288
  assert m["formal_gate_binding"]["required_nonempty_subsets"]==127 and m["formal_gate_binding"]["fixed_real_replay_top_k"]==5 and m["formal_gate_binding"]["required_trace_transactions"]==["AT1","AT2","AT3","AT4","AT5","AT6"]
  assert p["execution_order"].index("publish_frozen_image_to_dedicated_daemon")<p["execution_order"].index("physical_copy")
  assert out(p["commands_to_run_on_compute00_after_separate_authorization"]["bootstrap"])==s["paths"]["bootstrap_adoption"]!=s["paths"]["adoption"]
  assert all(x.startswith("ra-mysql-xp-v15-") for x in s["attempt"]["executor_units"].values())
  assert s["dedicated_frozen_image_gate"]["image_id"]==M.IMAGE_ID and s["dedicated_frozen_image_gate"]["required_repo_digest"]==M.REPO_DIGEST
  assert m["invalidated_failed_attempts"][0]["rerun_prohibited"]
  for rel,h in m["files"].items():assert M.sha_file(d/rel)==h
 finally:shutil.rmtree(root)

def test_publisher_save_load_exact_readback_and_partial_fail_closed():
 root=Path(tempfile.mkdtemp());old_runtime=P.RUNTIME;old_inspect=P.inspect;old_run=P.run
 identity={"Id":P.IMAGE_ID,"RepoDigests":[P.REPO_DIGEST],"Layers":["sha256:"+"1"*64],"layer_count":1};calls=[]
 def inspect_ok(host,ref,allow_absent=False):
  calls.append(("inspect",host,allow_absent));return None if host==P.DEST and allow_absent else identity
 def run_ok(argv,stdin=None,stdout=None):
  calls.append((argv[-1],stdin is not None,stdout is not None))
  if argv[-2:]==["save",P.REPO_DIGEST]:stdout.write(b"sealed archive")
  if argv[-1]=="load":assert stdin.read()==b"sealed archive"
 try:
  P.RUNTIME=str(root);P.inspect=inspect_ok;P.run=run_ok;attempt="xp-formal-qual-20260803T152502Z-"+"a"*24;out=root/"image_authorities"/attempt/"frozen_image_publication.json"
  value=P.publish(attempt,out,"ra-mysql-xp-v15-image-test.service")
  assert value["status"]=="PASS" and value["layers_exact_match"] and value["loaded_in_this_attempt"]
  assert out.is_file() and Path(str(out)+".sha256").is_file() and not (out.parent/".frozen-image.tar.partial").exists()
  bad_attempt="xp-formal-qual-20260803T152503Z-"+"b"*24;bad=root/"image_authorities"/bad_attempt/"frozen_image_publication.json"
  def fail_load(argv,stdin=None,stdout=None):
   if argv[-2:]==["save",P.REPO_DIGEST]:stdout.write(b"partial archive");return None
   raise P.Error("load failed")
  P.run=fail_load
  try:P.publish(bad_attempt,bad,"ra-mysql-xp-v15-image-test2.service")
  except P.Error:pass
  else:raise AssertionError("partial load accepted")
  assert bad.parent.exists() and not bad.exists()
 finally:P.RUNTIME=old_runtime;P.inspect=old_inspect;P.run=old_run;shutil.rmtree(root)
'''

README=b'''# XP v15 dedicated frozen-image successor\n\nOffline control/materials successor. It creates a fresh attempt and fresh v15 units, publishes only the exact default-daemon frozen image through docker save/load into the XP dedicated daemon, seals exact Id/RepoDigest/layer readback, then permits fresh copy/JDK/tmpfs/plan/bootstrap/adopt gates. The failed v14 attempt is zero-credit and cannot be rerun. MySQL-only; PostgreSQL mutation and cross-engine comparison are prohibited.\n'''

def fmap(root):return {p.relative_to(root).as_posix():sha_file(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def build():
 c={"builder/build_mysql_xp_compute00_dedicated_image_successor_v15.py":HERE.read_bytes(),"runtime/mysql_xp_dedicated_frozen_image_publication_v1.py":PUBLISHER.encode(),"materializer/mysql_xp_v15_materializer.py":MATERIALIZER.encode(),"tests/test_mysql_xp_v15_materializer.py":TEST.encode(),"README.md":README};files={k:sha_bytes(v) for k,v in sorted(c.items())}
 ident={"schema_version":"mysql_xp_compute00_dedicated_image_successor_bundle_v15","status":"PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED","immutable":True,"predecessor_data_bundle_id":DATA_ID,"predecessor_data_bundle_manifest_sha256":DATA_SHA,"exact_image_id":IMAGE_ID,"required_repo_digest":REPO_DIGEST,"transport":"docker_save_load_only","dedicated_daemon":"unix:///run/ra-docker-mysql-xp-v2.sock","failed_attempt":FAILED_ATTEMPT,"failed_unit":FAILED_UNIT,"failed_attempt_rerun_prohibited":True,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"data_plane_actions":0,"files":files}
 bid=sha_bytes(canonical(ident));manifest=dict(ident,bundle_id=bid);dest=OUT/bid
 if dest.exists():return dest
 OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".xp-v15-",dir=OUT))
 try:
  for n,v in c.items():p=stage/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(v)
  raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha_bytes(raw)+"  bundle_manifest.json\n")
  for p in sorted(stage.rglob("*"),reverse=True):os.chmod(p,0o555 if p.is_dir() else 0o444)
  os.chmod(stage,0o555);os.replace(stage,dest)
 finally:
  if stage.exists():shutil.rmtree(stage)
 if fmap(dest)!=files:raise RuntimeError("closure differs")
 return dest
if __name__=="__main__":
 r=build();print(json.dumps({"status":"PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED","bundle":str(r),"bundle_id":r.name,"manifest_sha256":sha_file(r/"bundle_manifest.json"),"data_plane_actions":0},sort_keys=True))
