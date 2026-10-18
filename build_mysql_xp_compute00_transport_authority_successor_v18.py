#!/usr/bin/env python3
"""Build immutable XP v18 transport-authority and sealed-runner successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parent
HERE = Path(__file__).resolve()
PRE_ID = "b8f365fba6a741006423ca6430aed2c36b6b7e4f6c7a6ca9332c54443dafeaf4"
PRE_SHA = "30be0966ea1abf0193231fc3b3a00f317e4ae7ead7dc591db24598b1f87f6f41"
PRE = ROOT / "artifacts/mysql_xp_compute00_argv_hash_successor_v17" / PRE_ID
OUT = ROOT / "artifacts/mysql_xp_compute00_transport_authority_successor_v18"
V16_ATTEMPT = "xp-formal-qual-20260804T082652Z-a5f9121eeef9124e914316ff"
V16_UNIT = "ra-mysql-xp-v16-image-0582f5d645555ec6.service"
V17_ATTEMPT = "xp-formal-qual-20260804T084742Z-4b4c9a883ca3ca5231fd678f"


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                   allow_nan=False) + "\n"
    ).encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    return {
        path.relative_to(root).as_posix(): sha_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


PUBLISHER = r'''#!/usr/bin/env python3
"""Seal exact source digest and save/load destination content identity."""
from __future__ import annotations
import argparse,hashlib,json,os,re,subprocess
from pathlib import Path
IMAGE_ID="sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
REPO_DIGEST="mysql@sha256:a3dff78d876222746a0bacc36dd7e4bf9e673c85fb7ee0d12ed25bd32c43c19b"
SOURCE="unix:///var/run/docker.sock";DEST="unix:///run/ra-docker-mysql-xp-v2.sock"
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
def raw_inspect(host,ref,allow_absent=False):
 r=subprocess.run(["docker","--host",host,"image","inspect",ref],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
 if r.returncode:
  if allow_absent and b"No such image" in r.stderr:return None
  raise Error("image inspect failed: "+r.stderr.decode("utf-8","replace").strip())
 rows=json.loads(r.stdout)
 if len(rows)!=1:raise Error("image inspect cardinality differs")
 row=rows[0];layers=row.get("RootFS",{}).get("Layers");digests=row.get("RepoDigests") or []
 if row.get("Id")!=IMAGE_ID or not isinstance(layers,list) or len(layers)!=11 or any(not re.fullmatch(r"sha256:[0-9a-f]{64}",x or "") for x in layers) or not isinstance(digests,list):raise Error("frozen image ID/layers shape differs")
 return {"Id":row["Id"],"RepoDigests":sorted(digests),"Layers":layers,"layer_count":len(layers)}
def source_inspect():
 value=raw_inspect(SOURCE,IMAGE_ID)
 if REPO_DIGEST not in value["RepoDigests"]:raise Error("source registry digest differs")
 return value
def destination_inspect(allow_absent=False):
 value=raw_inspect(DEST,IMAGE_ID,allow_absent)
 if value is not None and value["RepoDigests"] not in ([],[REPO_DIGEST]) and REPO_DIGEST not in value["RepoDigests"]:raise Error("destination has conflicting registry digest")
 return value
def content_identity(value):return {"Id":value["Id"],"Layers":value["Layers"],"layer_count":value["layer_count"]}
def atomic_json(path,value):
 path=Path(path);side=Path(str(path)+".sha256")
 if path.exists() or side.exists() or path.is_symlink() or side.is_symlink():raise Error("refusing to overwrite image authority")
 raw=canonical(value);tmp=path.with_name("."+path.name+".%d.tmp"%os.getpid());stmp=side.with_name("."+side.name+".%d.tmp"%os.getpid())
 fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:os.write(fd,raw);os.fsync(fd)
 finally:os.close(fd)
 stmp.write_text(hashlib.sha256(raw).hexdigest()+"  "+path.name+"\n",encoding="ascii");os.chmod(stmp,0o600);os.replace(tmp,path);os.replace(stmp,side)
def publish(attempt,output,unit):
 if ATTEMPT_RE.fullmatch(attempt) is None:raise Error("attempt identity differs")
 expected=Path(RUNTIME)/"image_authorities"/attempt/"frozen_image_publication.json";output=Path(output)
 if output!=expected:raise Error("image authority path differs")
 if output.parent.exists() or output.parent.is_symlink():raise Error("fresh image authority root must be absent")
 output.parent.mkdir(parents=True,mode=0o700)
 source=source_inspect();pre=destination_inspect(True);archive=output.parent/".frozen-image.tar.partial"
 with archive.open("xb") as stream:run(["docker","--host",SOURCE,"save",REPO_DIGEST],stdout=stream)
 archive_sha=sha_file(archive);loaded=False
 if pre is None:
  with archive.open("rb") as stream:run(["docker","--host",DEST,"load"],stream)
  loaded=True
 elif content_identity(pre)!=content_identity(source):raise Error("preexisting destination content differs")
 post=destination_inspect()
 if content_identity(post)!=content_identity(source):raise Error("destination ID/layers differ from source")
 digest_absent=(post["RepoDigests"]==[])
 value={"schema_version":"mysql_xp_dedicated_frozen_image_transport_authority_v2","status":"PASS_SOURCE_DIGEST_DESTINATION_ID_LAYERS","attempt_id":attempt,"executor_unit":unit,"source_daemon":SOURCE,"dedicated_daemon":DEST,"image_id":IMAGE_ID,"required_source_repo_digest":REPO_DIGEST,"source_registry_digest_verified":True,"source_inspect":source,"dedicated_inspect_before":pre,"dedicated_inspect":post,"image_id_exact_match":True,"layers_exact_match":True,"layer_count_exact":11,"transport":"docker_save_load","fresh_archive_sha256":archive_sha,"fresh_archive_created_in_this_attempt":True,"loaded_in_this_attempt":loaded,"preexisting_exact_content_adopted":pre is not None,"destination_registry_digest_preservation_required":False,"destination_repo_digests_absent_expected_after_save_load":digest_absent,"destination_conflicting_digest_rejected":True,"partial_failure_policy":"FAIL_CLOSED_INVALIDATE_ATTEMPT_ZERO_CREDIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0}
 atomic_json(output,value)
 if archive.exists():archive.unlink()
 return value
def main():
 p=argparse.ArgumentParser();p.add_argument("--attempt-id",required=True);p.add_argument("--output",type=Path,required=True);p.add_argument("--executor-unit",required=True);a=p.parse_args();print(json.dumps(publish(a.attempt_id,a.output,a.executor_unit),sort_keys=True))
if __name__=="__main__":
 try:main()
 except Error as e:raise SystemExit("NOT_READY: "+str(e))
'''


def generic_runner():
    source = (ROOT / "scripts/xp_v16_attempt_stage_runner.py").read_text()
    start = source.index("MATERIALS = Path(")
    end = source.index("STAGES = (")
    source = source[:start] + (
        'MATERIALS = None\nMATERIALS_MANIFEST_SHA256 = None\n'
        'SUPPORT_BUNDLE_ID = None\nSUPPORT_BUNDLE_MANIFEST_SHA256 = None\n'
        'ATTEMPT = None\nRUNNER_SCHEMA = "mysql_xp_v18_attempt_stage_runner_receipt_v2"\n'
    ) + source[end:]
    source = source.replace(
        '("mysql_xp_dedicated_frozen_image_publication_authority_v1", "PASS")',
        '("mysql_xp_dedicated_frozen_image_transport_authority_v2", "PASS_SOURCE_DIGEST_DESTINATION_ID_LAYERS")',
    )
    source = source.replace(
        'document.get("required_repo_digest") != gate["required_repo_digest"]',
        'document.get("required_source_repo_digest") != gate["required_source_repo_digest"]',
    )
    source = source.replace(
        'raise GateError("v16 frozen image publisher hash differs")',
        'raise GateError("frozen image publisher hash differs")',
    )
    main_start = source.index("def main(argv=None) -> int:")
    trailer = source.index('\n\nif __name__ == "__main__":', main_start)
    new_main = '''def main(argv=None) -> int:
    global MATERIALS, MATERIALS_MANIFEST_SHA256, SUPPORT_BUNDLE_ID
    global SUPPORT_BUNDLE_MANIFEST_SHA256, ATTEMPT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--materials-manifest-sha256", required=True)
    parser.add_argument("--expected-support-bundle-id", required=True)
    parser.add_argument("--expected-support-manifest-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-attempt")
    args = parser.parse_args(argv)
    MATERIALS=args.materials.resolve()
    MATERIALS_MANIFEST_SHA256=args.materials_manifest_sha256
    SUPPORT_BUNDLE_ID=args.expected_support_bundle_id
    SUPPORT_BUNDLE_MANIFEST_SHA256=args.expected_support_manifest_sha256
    if not re.fullmatch(r"[0-9a-f]{64}", MATERIALS_MANIFEST_SHA256 or "") or not re.fullmatch(r"[0-9a-f]{64}", SUPPORT_BUNDLE_ID or "") or not re.fullmatch(r"[0-9a-f]{64}", SUPPORT_BUNDLE_MANIFEST_SHA256 or ""):
        raise GateError("trusted identity argument shape differs")
    if sha_file(MATERIALS/"materials_manifest.json") != MATERIALS_MANIFEST_SHA256:
        raise GateError("trusted materials manifest identity differs")
    ATTEMPT=load_json(MATERIALS/"materials_manifest.json")["attempt_id"]
    manifest,plan,spec,install,expiry=verify_local_materials(MATERIALS)
    if not args.execute:
        print(json.dumps({"status":"PASS_LOCAL_ONLY_NO_DATA_PLANE_ACTION","attempt_id":ATTEMPT,"expires_at_utc":expiry.isoformat(),"stage_order":list(STAGES),"materials_manifest_sha256":MATERIALS_MANIFEST_SHA256},sort_keys=True));return 0
    if args.confirm_attempt != ATTEMPT:raise GateError("--confirm-attempt must equal the sealed attempt id")
    execute(MATERIALS,manifest,plan,spec,install,expiry);return 0
'''
    return source[:main_start] + new_main + source[trailer:]


def patched_materializer():
    source = (PRE / "materializer/mysql_xp_v17_materializer.py").read_text()
    source = source.replace("ra-mysql-xp-v17-", "ra-mysql-xp-v18-")
    source = source.replace(
        "bootstrap_spec_v5_argv_hash_closed_fresh_ttl_dedicated_image_output_split",
        "bootstrap_spec_v6_transport_authority_runner_bound_fresh_ttl",
    ).replace(
        "command_plan_v6_argv_hash_closed_fresh_ttl_dedicated_image_output_split",
        "command_plan_v7_transport_authority_runner_bound_fresh_ttl",
    ).replace(
        "materials_manifest_v6_argv_hash_closed_fresh_ttl_dedicated_image_output_split",
        "materials_manifest_v7_transport_authority_runner_bound_fresh_ttl",
    )
    old = 'spec["dedicated_frozen_image_gate"]={"source_daemon":"unix:///var/run/docker.sock","dedicated_daemon":"unix:///run/ra-docker-mysql-xp-v2.sock","image_id":IMAGE_ID,"required_repo_digest":REPO_DIGEST,"source_exact_id_digest_layers_required":True,"transport":"docker_save_load_only","dedicated_exact_id_digest_layers_readback_required":True,"sealed_non_overwritable_authority":image_authority,"must_pass_before_physical_copy":True,"partial_failure_policy":"FAIL_CLOSED_INVALIDATE_ATTEMPT_ZERO_CREDIT"}'
    new = 'spec["dedicated_frozen_image_gate"]={"source_daemon":"unix:///var/run/docker.sock","dedicated_daemon":"unix:///run/ra-docker-mysql-xp-v2.sock","image_id":IMAGE_ID,"required_source_repo_digest":REPO_DIGEST,"source_exact_id_digest_layers_required":True,"transport":"docker_save_load","fresh_archive_sha256_required":True,"dedicated_exact_id_layers_required":True,"dedicated_registry_digest_preservation_required":False,"dedicated_empty_repo_digests_expected_after_save_load":True,"fresh_sealed_authority_even_if_destination_present":True,"sealed_non_overwritable_authority":image_authority,"must_pass_before_physical_copy":True,"partial_failure_policy":"FAIL_CLOSED_INVALIDATE_ATTEMPT_ZERO_CREDIT"}'
    if source.count(old) != 1:
        raise RuntimeError("transport gate anchor differs")
    source = source.replace(old, new)
    source = source.replace('"required_repo_digest":REPO_DIGEST', '"required_source_repo_digest":REPO_DIGEST')
    source = source.replace(
        'verify_dedicated_inspect_exact_id_repo_digest_layers_and_sealed_authority',
        'verify_source_digest_and_dedicated_exact_id_layers_and_sealed_transport_authority',
    )
    anchor = ' manifest["invalidated_failed_attempts"].append({"attempt_id":IMAGE_ONLY_ATTEMPT,"failed_unit":IMAGE_ONLY_UNIT,"fresh_error":"image-only stage launched under argv-hash-inconsistent v16 plan","image_only_zero_credit_abandoned":True,"formal_r0_credit":0,"accepted_rounds_credit":0,"rerun_prohibited":True,"delete_prohibited":True})\n'
    replacement = anchor + (
        ' manifest["invalidated_failed_attempts"][-1].update({"fresh_error":"dedicated RepoDigests absent after save/load while v16 wrongly required registry digest","image_authority_created":False,"partial_archive_preserved":True})\n'
        ' manifest["invalidated_failed_attempts"].append({"attempt_id":"%s","fresh_error":"offline v17 runner control-manifest publisher key mismatch; never launched","offline_superseded_zero_credit":True,"formal_r0_credit":0,"accepted_rounds_credit":0,"rerun_prohibited":True})\n' % V17_ATTEMPT
    )
    if source.count(anchor) != 1:
        raise RuntimeError("v16/v17 invalidation anchor differs")
    return source.replace(anchor, replacement)


def tests_source():
    return r'''from __future__ import annotations
import datetime,hashlib,importlib.util,json,os,pathlib,shutil,tempfile
B=pathlib.Path(__file__).resolve().parents[1];BASE=pathlib.Path(os.environ["XP_V18_BASE"])
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
M=module("materializer",B/"materializer/mysql_xp_v18_materializer.py");R=module("runner",B/"runtime/mysql_xp_attempt_stage_runner_v2.py");P=module("publisher",B/"runtime/mysql_xp_dedicated_frozen_image_publication_v1.py")
def load(p):return json.loads(pathlib.Path(p).read_text())
def seal_bundle(root,payload):
 (root/"runtime").mkdir(parents=True);member=root/"runtime/mysql_xp_dedicated_frozen_image_publication_v1.py";member.write_bytes(payload);os.chmod(member,0o444);manifest={"files":{"runtime/mysql_xp_dedicated_frozen_image_publication_v1.py":hashlib.sha256(payload).hexdigest()},"frozen_image_publisher_sha256":hashlib.sha256(payload).hexdigest()};raw=R.canonical(manifest);(root/"bundle_manifest.json").write_bytes(raw);(root/"bundle_manifest.json.sha256").write_text(hashlib.sha256(raw).hexdigest()+"  bundle_manifest.json\n");os.chmod(root/"bundle_manifest.json",0o444);os.chmod(root/"bundle_manifest.json.sha256",0o444);os.chmod(root/"runtime",0o555);os.chmod(root,0o555);return hashlib.sha256(raw).hexdigest(),member
def test_materials_all_six_runner_and_transport_gate():
 root=pathlib.Path(tempfile.mkdtemp())
 try:
  result=M.materialize(BASE,root,B.name);d=pathlib.Path(result["materials_directory"]);m=load(d/"materials_manifest.json");plan=load(d/"command_plan.json");spec=load(d/"bootstrap_spec.json");install=load(d/"authorization_install_plan.json");host=plan["host_systemd_run_commands_after_separate_authorization"];polls=plan["controller_poll_after_each_systemd_run"]
  assert set(host)==set(polls)=={"physical_copy","jdk_publish","plan_only","bootstrap","adopt_readback","frozen_image_publish"}
  for stage in host:assert host[stage][6:]==polls[stage]["expected_exec_start_argv"] and M.sha_bytes(M.canonical(host[stage][6:]))==polls[stage]["expected_exec_start_argv_sha256"]
  gate=spec["dedicated_frozen_image_gate"];assert gate["required_source_repo_digest"]==M.REPO_DIGEST and gate["dedicated_exact_id_layers_required"] and gate["dedicated_registry_digest_preservation_required"] is False and gate["fresh_sealed_authority_even_if_destination_present"]
  assert any(step.get("action")=="verify_source_digest_and_dedicated_exact_id_layers_and_sealed_transport_authority" for step in install["strict_install_steps"])
  assert not any("exact_id_repo_digest_layers" in step.get("action","") for step in install["strict_install_steps"])
  assert m["invalidated_failed_attempts"][-2]["attempt_id"].startswith("xp-formal-qual-20260804T082652Z") and m["invalidated_failed_attempts"][-2]["partial_archive_preserved"]
  assert m["invalidated_failed_attempts"][-1]["attempt_id"].startswith("xp-formal-qual-20260804T084742Z") and m["invalidated_failed_attempts"][-1]["offline_superseded_zero_credit"]
  manifest_sha=hashlib.sha256((d/"materials_manifest.json").read_bytes()).hexdigest();support_sha=hashlib.sha256((B/"bundle_manifest.json").read_bytes()).hexdigest();assert R.main(["--materials",str(d),"--materials-manifest-sha256",manifest_sha,"--expected-support-bundle-id",B.name,"--expected-support-manifest-sha256",support_sha])==0
 finally:shutil.rmtree(root)
def test_execute_preflight_reaches_exact_first_mutation_without_manifest_key_assumption(monkeypatch):
 root=pathlib.Path(tempfile.mkdtemp());old=(R.SUPPORT_BUNDLE_ID,R.SUPPORT_BUNDLE_MANIFEST_SHA256)
 try:
  data=root/("d"*64);control=root/("c"*64);data_sha,_=seal_bundle(data,b"data");control_sha,publisher=seal_bundle(control,b"publisher")
  R.SUPPORT_BUNDLE_ID=control.name;R.SUPPORT_BUNDLE_MANIFEST_SHA256=control_sha;attempt="xp-formal-qual-20990101T000000Z-"+"a"*24;attempt_root=root/attempt
  commands={};polls={}
  for stage in R.SYSTEMD_STAGES:
   unit="ra-mysql-xp-v18-%s-test.service"%stage;inner=["taskset","-c","184-191,440-447","env","PYTHONDONTWRITEBYTECODE=1","python3","-B",str(publisher)]
   if stage=="frozen_image_publish":inner += ["--attempt-id",attempt,"--output",str(root/"image_authorities"/attempt/"frozen_image_publication.json"),"--executor-unit",unit]
   commands[stage]=["systemd-run","--quiet","--service-type=oneshot","--remain-after-exit","--unit="+unit,"--"]+inner
  plan={"fresh_attempt_root_gate":{"path":str(attempt_root)},"host_systemd_run_commands_after_separate_authorization":commands,"controller_poll_after_each_systemd_run":polls};spec={"bundle":{"remote_path":str(data),"remote_manifest_sha256_required":data_sha}};seen=[]
  class Barrier(Exception):pass
  def barrier(argv,capture=False):seen.append(argv);raise Barrier()
  class Done:
   returncode=0;stdout="not-found\n"
  monkeypatch.setattr(R.os,"geteuid",lambda:0);monkeypatch.setattr(R.socket,"gethostname",lambda:"compute00");monkeypatch.setattr(R.subprocess,"run",lambda *a,**k:Done());monkeypatch.setattr(R,"run_checked",barrier)
  try:R.execute(root,{},plan,spec,{},datetime.datetime(2099,1,1,tzinfo=datetime.timezone.utc))
  except Barrier:pass
  else:raise AssertionError("first mutation barrier not reached")
  assert seen==[commands["frozen_image_publish"]]
 finally:
  R.SUPPORT_BUNDLE_ID,R.SUPPORT_BUNDLE_MANIFEST_SHA256=old
  for path in root.rglob("*"):
   try:os.chmod(path,0o700 if path.is_dir() else 0o600)
   except FileNotFoundError:pass
  shutil.rmtree(root)
def test_publisher_accepts_destination_empty_repo_digests_but_exact_id_layers(monkeypatch,tmp_path):
 layers=["sha256:"+("%064x"%i) for i in range(1,12)];source={"Id":P.IMAGE_ID,"RepoDigests":[P.REPO_DIGEST],"Layers":layers,"layer_count":11};dest=dict(source,RepoDigests=[]);monkeypatch.setattr(P,"RUNTIME",str(tmp_path));monkeypatch.setattr(P,"source_inspect",lambda:source);monkeypatch.setattr(P,"destination_inspect",lambda allow_absent=False:dest)
 def run(argv,stdin=None,stdout=None):
  if argv[-2:]==["save",P.REPO_DIGEST]:stdout.write(b"fresh exact archive")
 monkeypatch.setattr(P,"run",run);attempt="xp-formal-qual-20990101T000000Z-"+"b"*24;out=tmp_path/"image_authorities"/attempt/"frozen_image_publication.json";value=P.publish(attempt,out,"ra-mysql-xp-v18-image-test.service");assert value["source_registry_digest_verified"] and value["destination_repo_digests_absent_expected_after_save_load"] and value["image_id_exact_match"] and value["layers_exact_match"] and not value["loaded_in_this_attempt"]
'''


def build():
    predecessor = json.loads((PRE / "bundle_manifest.json").read_text())
    without_id = dict(predecessor);without_id.pop("bundle_id")
    if PRE.name != PRE_ID or sha_file(PRE / "bundle_manifest.json") != PRE_SHA or sha_bytes(canonical(without_id)) != PRE_ID or file_map(PRE) != predecessor["files"]:
        raise RuntimeError("v17 predecessor closure differs")
    content = {
        "builder/build_mysql_xp_compute00_transport_authority_successor_v18.py": HERE.read_bytes(),
        "runtime/mysql_xp_dedicated_frozen_image_publication_v1.py": PUBLISHER.encode(),
        "runtime/mysql_xp_attempt_stage_runner_v2.py": generic_runner().encode(),
        "materializer/mysql_xp_v18_materializer.py": patched_materializer().encode(),
        "tests/test_mysql_xp_v18_transport_runner.py": tests_source().encode(),
        "README.md": b"XP v18 immutable successor: source registry digest plus destination exact image ID and 11 layers, fresh archive authority, all-six argv hashes, sealed generic stage runner, and execute-before-mutation simulation. v16 failed image-only and v17 offline materials are zero-credit/rerun-prohibited. MySQL-only; PostgreSQL mutation prohibited.\n",
    }
    files={name:sha_bytes(value) for name,value in sorted(content.items())}
    identity={"schema_version":"mysql_xp_compute00_transport_authority_successor_bundle_v18","status":"PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED","immutable":True,"predecessor_support_bundle_id":PRE_ID,"predecessor_support_manifest_sha256":PRE_SHA,"publisher_schema":"mysql_xp_dedicated_frozen_image_transport_authority_v2","source_registry_digest_required":True,"destination_exact_id_layers_required":True,"destination_registry_digest_preservation_required":False,"destination_empty_repo_digests_expected_after_save_load":True,"fresh_archive_and_authority_required_per_attempt":True,"all_six_normalized_argv_hashes_required":True,"sealed_runner_in_bundle":True,"execute_before_mutation_test_required":True,"invalidated_v16_attempt":V16_ATTEMPT,"invalidated_v16_unit":V16_UNIT,"invalidated_v16_zero_credit":True,"invalidated_v16_partial_delete_prohibited":True,"superseded_v17_attempt":V17_ATTEMPT,"superseded_v17_never_launched":True,"new_attempt_required":True,"ttl_minutes_required":240,"unit_prefix":"ra-mysql-xp-v18-","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"data_plane_actions":0,"files":files}
    bundle_id=sha_bytes(canonical(identity));manifest=dict(identity,bundle_id=bundle_id);destination=OUT/bundle_id
    if destination.exists():return destination
    OUT.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix=".xp-v18-",dir=OUT))
    try:
        for name,value in content.items():path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
        raw=canonical(manifest);(stage/"bundle_manifest.json").write_bytes(raw);(stage/"bundle_manifest.json.sha256").write_text(sha_bytes(raw)+"  bundle_manifest.json\n")
        for path in sorted(stage.rglob("*"),reverse=True):os.chmod(path,0o555 if path.is_dir() else 0o444)
        os.chmod(stage,0o555);os.replace(stage,destination)
    finally:
        if stage.exists():shutil.rmtree(stage)
    if file_map(destination)!=files:raise RuntimeError("v18 closure differs")
    return destination


if __name__=="__main__":
    result=build();print(json.dumps({"status":"PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED","bundle":str(result),"bundle_id":result.name,"manifest_sha256":sha_file(result/"bundle_manifest.json"),"data_plane_actions":0},sort_keys=True))
