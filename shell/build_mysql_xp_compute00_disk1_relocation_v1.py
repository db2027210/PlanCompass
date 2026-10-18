#!/usr/bin/env python3
"""Build a deterministic, fully re-bound compute00 /disk1 XP v28 successor."""
from __future__ import annotations
import hashlib,json,os,secrets,shutil,stat,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];SELF=Path(__file__).resolve();PY=Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID="a0187979839c8fdb9530da525425a031bca9d955234d754cc495dbe0d5b342e9";PARENT_SHA="faf226fff3c150b097632fac2dd3b96990fca00e1501cb5b46ac3c390a032fb1"
PARENT=ROOT/"artifacts/mysql_xp_node02_successor_v28"/PARENT_ID;OUT=ROOT/"artifacts/mysql_xp_compute00_disk1_relocation_v1"
PREFLIGHT=ROOT/"shell/mysql_xp_compute00_disk1_preflight_v1.py";TEST=ROOT/"shell/test_mysql_xp_compute00_disk1_preflight_v1.py"
SCHEMA="mysql_xp_compute00_successor_bundle_v28";DB_CPU="184-191,440-447";CLIENT_CPU="192-199,448-455";ALL_CPU="184-199,440-455";PG_CPU="32-51,288-307"
RUNTIME="/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/runtime_algorithm_c_successor_20260803";TEMPLATE="/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/template_v2";DOCKER_ROOT="/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp/docker_ra_xp_v29"
class BuildError(RuntimeError):pass
def canonical(v):return (json.dumps(v,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha_bytes(v):return hashlib.sha256(v).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def replace(text,old,new,minimum=1):
 if text.count(old)<minimum:raise BuildError("patch point absent: "+old[:80])
 return text.replace(old,new)
def writable(root):
 root.chmod(0o755)
 for p in root.rglob("*"):
  if p.is_dir():p.chmod(0o755)
  elif p.is_file():p.chmod(stat.S_IMODE(p.stat().st_mode)|0o200)
def rename_active(stage):
 moves=[]
 for base in (stage/"launch",stage/"runtime",stage/"authority"):
  for p in base.rglob("*node02*"):
   if p.is_file() and "invalidation" not in p.name:moves.append(p)
 for p in sorted(moves,key=lambda x:len(x.parts),reverse=True):p.rename(p.with_name(p.name.replace("node02","compute00")))
def active_files(stage):
 for name in ("launch","qualification","runtime","test_support"):
  for p in (stage/name).rglob("*"):
   if p.is_file():yield p
 for name in ("xp_executable_launch_contract_v3.json","distributed_runtime_contract.json","authority/compute00_dedicated_lane_binding_v1.json"):
  p=stage/name
  if p.is_file():yield p
def patch_text(stage):
 for p in active_files(stage):
  try:text=p.read_text(encoding="utf-8")
  except UnicodeDecodeError:continue
  changes=(("node02","compute00"),("/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/runtime_algorithm_c_successor_20260729",RUNTIME),("/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/docker_ra_xp_v2",DOCKER_ROOT),("/mnt/mysql_nvme0n1p1","/disk1"),("/dev/nvme0n1p1","/dev/nvme1n1"),("/dev/nvme0n1","/dev/nvme1n1"),("259:2","259:6"),("259:0","259:6"),("21-33,57-69",DB_CPU),("3-11,39-47",CLIENT_CPU),("34-35,70-71","192-193,448-449"))
  for old,new in changes:text=text.replace(old,new)
  p.write_text(text,encoding="utf-8")
 materials=stage/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py";text=materials.read_text()
 text=replace(text,'    "mysql_xp_compute00_successor_bundle_v28": "fb5f9a45066dc091112abad26028f98354a12dd98447a0874acad7838af343e6",','    "mysql_xp_compute00_successor_bundle_v28": "'+PARENT_ID+'",')
 text=text.replace('service_relative != "launch/mysql_xp_compute00_adopted_service_v1.py"','service_relative != "launch/mysql_xp_compute00_adopted_service_v1.py"')
 text=text.replace('data_partition_and_throttle_parent_must_not_be_conflated") is not True','data_partition_and_throttle_parent_must_not_be_conflated") is not False')
 materials.write_text(text)
 service=stage/"launch/mysql_xp_compute00_adopted_service_v1.py";text=service.read_text();start=text.index("def _validate_storage_topology(");end=text.index("\n\ndef _cmdline_flag",start)
 whole='''def _validate_storage_topology(\n    mountinfo_path=Path("/proc/self/mountinfo"),\n    sys_dev_block=Path("/sys/dev/block"),\n    swaps_path=Path("/proc/swaps"),\n    block_stat=os.stat,\n):\n    if DATA_PARTITION != THROTTLE_DEVICE or DATA_PARTITION_MAJOR_MINOR != THROTTLE_DEVICE_MAJOR_MINOR:\n        raise ServiceError("whole-disk data/throttle identity differs")\n    if _block_node_major_minor(DATA_PARTITION, block_stat) != DATA_PARTITION_MAJOR_MINOR:\n        raise ServiceError("frozen whole-disk block node identity differs")\n    link = Path(sys_dev_block) / DATA_PARTITION_MAJOR_MINOR\n    if not link.exists() or link.resolve().name != Path(DATA_PARTITION).name:\n        raise ServiceError("whole-disk sysfs identity differs")\n    node = link.resolve(); holders=node / "holders"\n    if not holders.is_dir() or list(holders.iterdir()):\n        raise ServiceError("whole disk has block holders")\n    related=[row for row in _mountinfo_rows(mountinfo_path) if row["major_minor"] == DATA_PARTITION_MAJOR_MINOR]\n    if (len(related)!=1 or related[0]["root"]!="/" or Path(related[0]["mount_point"]).resolve()!=Path(MOUNT).resolve()\n            or related[0]["source"]!=DATA_PARTITION or "rw" not in (related[0]["mount_options"]|related[0]["super_options"])):\n        raise ServiceError("whole disk has another, read-only, or incorrect mount")\n    swaps=Path(swaps_path).read_text(encoding="utf-8").splitlines();active={line.split()[0] for line in swaps[1:] if line.split()}\n    if DATA_PARTITION in active:raise ServiceError("whole disk is in use as swap")\n    return {"data_mount":MOUNT,"data_partition":DATA_PARTITION,"data_partition_major_minor":DATA_PARTITION_MAJOR_MINOR,\n            "throttle_parent":THROTTLE_DEVICE,"throttle_parent_major_minor":THROTTLE_DEVICE_MAJOR_MINOR,\n            "whole_disk_filesystem_and_throttle_identity_same":True,"related_mount_count":1,\n            "parent_or_partition_holders":[],"parent_or_partition_swap_entries":[],"parent_throttle_required_for_cgroup_v1":False}\n'''
 service.write_text(text[:start]+whole+text[end:])
def tune_contract(path):
 v=json.loads(path.read_text());storage=v.get("storage") or {};storage.update({"mount":"/disk1","device":"/dev/nvme1n1","data_partition":"/dev/nvme1n1","data_partition_major_minor":"259:6","expected_live_major_minor":"259:6","io_throttle_parent_device":"/dev/nvme1n1","io_throttle_parent_major_minor":"259:6","docker_bps_declaration_device":"/dev/nvme1n1","effective_cgroup_v1_throttle_major_minor":"259:6","runtime_root":RUNTIME,"template_path":TEMPLATE,"data_partition_and_throttle_parent_must_not_be_conflated":False,"whole_disk_filesystem_and_throttle_identity_same":True,"unique_partition_required":False});v["storage"]=storage
 db=v.get("database_resource_envelope") or {};db.update({"cpu_quota_cores":5.3,"cpuset_cpus":DB_CPU,"cpuset_mems":"1","io_read_bps":29360128,"io_write_bps":524288});v["database_resource_envelope"]=db
 client=v.get("client_resource_envelope") or {};client.update({"cpu_quota_cores":8,"cpuset_cpus":CLIENT_CPU,"cpuset_mems":"1"});v["client_resource_envelope"]=client
 host=v.get("distributed_host_contract") or {};host.update({"host_id":"compute00","database_cpuset_cpus":DB_CPU,"database_cpuset_mems":"1","client_cpuset_cpus":CLIENT_CPU,"client_cpuset_mems":"1","mysql_xp_reserved_cpuset":ALL_CPU,"postgresql_reserved_cpuset":PG_CPU,"postgresql_mysql_cpuset_overlap":False});host.pop("compute00_cpuset_is_not_valid_for_compute00",None);v["distributed_host_contract"]=host
 for key in ("collector_throttle_binding",):
  row=v.get(key) or {};row.update({"data_partition":"/dev/nvme1n1","data_partition_major_minor":"259:6","docker_bps_declaration_device":"/dev/nvme1n1","effective_cgroup_v1_throttle_major_minor":"259:6","data_partition_and_throttle_parent_must_not_be_conflated":False,"whole_disk_filesystem_and_throttle_identity_same":True});v[key]=row
 if isinstance(v.get("capture_transition"),dict):v["capture_transition"].update({"host":"compute00","database_blkio_parent_major_minor":"259:6","capture_io_bps_per_direction":1205862400,"capture_r0_formal_accepted_authority":False})
 if isinstance(v.get("dedicated_docker_successor"),dict):v["dedicated_docker_successor"].update({"data_root":DOCKER_ROOT,"data_root_major_minor":"259:6","daemon_cpuset_cpus":"192-193,448-449"})
 if isinstance(v.get("adopted_service"),dict):v["adopted_service"].update({"host":"compute00","path":"launch/mysql_xp_compute00_adopted_service_v1.py","dedicated_docker_data_root":DOCKER_ROOT,"data_path_partition_identity_required":"/dev/nvme1n1@259:6","cgroup_v1_throttle_parent_identity_required":"/dev/nvme1n1@259:6","copy_process_cpu_affinity_enforced":DB_CPU,"unique_partition_and_parent_exclusivity_gate_required":False,"whole_disk_exclusive_mount_gate_required":True})
 path.write_bytes(canonical(v))
def update_hash_refs(value,stage):
 if isinstance(value,dict):
  rel=value.get("path")
  if isinstance(rel,str) and isinstance(value.get("sha256"),str) and (stage/rel).is_file():value["sha256"]=sha(stage/rel)
  for x in value.values():update_hash_refs(x,stage)
 elif isinstance(value,list):
  for x in value:update_hash_refs(x,stage)
def files(root):return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
def freeze(root):
 for p in sorted(root.rglob("*"),reverse=True):p.chmod(0o555 if p.is_dir() or stat.S_IMODE(p.stat().st_mode)&0o111 else 0o444)
 # Keep the staging inode owner-writable until the atomic rename; seal it at visibility.
 root.chmod(0o755)
def build():
 if sha(PARENT/"bundle_manifest.json")!=PARENT_SHA:raise BuildError("v28 parent differs")
 r=subprocess.run([str(PY),"-B","-m","pytest","-p","no:cacheprovider","-q",str(PARENT/"tests")],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
 if r.returncode:raise BuildError("parent 93-test closure failed:\n"+r.stdout)
 r2=subprocess.run([str(PY),"-B","-m","unittest","-v",str(TEST)],cwd=ROOT,env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1"),text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
 if r2.returncode:raise BuildError("relocation tests failed:\n"+r2.stdout)
 OUT.mkdir(parents=True,exist_ok=True);raw=tempfile.mkdtemp(prefix=".stage-xp-compute00-",dir=OUT);stage=Path(raw)/"bundle";shutil.copytree(PARENT,stage);writable(stage);(stage/"bundle_manifest.json").unlink();(stage/"bundle_manifest.json.sha256").unlink();rename_active(stage);patch_text(stage)
 lane=stage/"authority/compute00_dedicated_lane_binding_v1.json";lv=json.loads(lane.read_text());lv.update({"schema_version":"mysql_xp_compute00_disk1_dedicated_lane_binding_v1","host_id":"compute00","database_cpuset":DB_CPU,"client_cpuset":CLIENT_CPU,"mysql_xp_reserved_cpuset":ALL_CPU,"postgresql_reserved_cpuset":PG_CPU,"cpuset_overlap":False,"storage":{"block_device":"/dev/nvme1n1","major_minor":"259:6","mount":"/disk1","whole_disk_filesystem_and_throttle_identity_same":True,"dedicated_runtime_subtree":"/disk1/resource_adaptive_mysql_algorithm_c_local_v2/xp"},"formal_envelope":{"database_cpu_quota_cores":5.3,"database_io_read_bps":29360128,"database_io_write_bps":524288,"graceful_quiescence_seconds":120},"capture_only_envelope":{"io_bps_per_direction":1205862400,"capture_is_r0":False,"accepted_round_credit":0},"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"formal_launch_authorized":False,"accepted_round_authorized":False});lane.write_bytes(canonical(lv))
 for name in ("xp_executable_launch_contract_v3.json","distributed_runtime_contract.json"):tune_contract(stage/name)
 for name in ("xp_executable_launch_contract_v3.json","distributed_runtime_contract.json"):
  p=stage/name;v=json.loads(p.read_text());update_hash_refs(v,stage);p.write_bytes(canonical(v))
 service=stage/"launch/mysql_xp_compute00_adopted_service_v1.py";contract=stage/"xp_executable_launch_contract_v3.json";v=json.loads(contract.read_text());v["adopted_service"]["sha256"]=sha(service);update_hash_refs(v,stage);contract.write_bytes(canonical(v))
 (stage/"preflight").mkdir(exist_ok=True);shutil.copy2(PREFLIGHT,stage/"preflight/mysql_xp_compute00_disk1_preflight_v1.py");(stage/"tests_compute00").mkdir(exist_ok=True);shutil.copy2(TEST,stage/"tests_compute00/test_mysql_xp_compute00_disk1_preflight_v1.py");(stage/"builder").mkdir(exist_ok=True);shutil.copy2(SELF,stage/"builder/build_mysql_xp_compute00_disk1_relocation_v1.py")
 parent=json.loads((PARENT/"bundle_manifest.json").read_text());m={k:v for k,v in parent.items() if k not in ("bundle_id","files")};m.update({"schema_version":SCHEMA,"status":"SEALED_COMPUTE00_DISK1_RELOCATION_NOT_LAUNCHED","predecessor_bundle_id":PARENT_ID,"supersedes_bundle_id":PARENT_ID,"source_v28_manifest_sha256":PARENT_SHA,"host_binding":"compute00","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"disk2_access_prohibited":True,"postgresql_reserved_cpuset":PG_CPU,"mysql_xp_reserved_cpuset":ALL_CPU,"database_cpuset":DB_CPU,"client_cpuset":CLIENT_CPU,"cpuset_overlap":False,"numa_mems":"1","storage":{"mount":"/disk1","device":"/dev/nvme1n1","major_minor":"259:6","device_numa_node":0,"whole_disk_filesystem_and_throttle_identity_same":True,"runtime_root":RUNTIME,"template_path":TEMPLATE},"formal_envelope":{"cpu_cores":5.3,"read_bps":29360128,"write_bps":524288,"graceful_quiescence_seconds":120},"capture_envelope":{"read_bps":1205862400,"write_bps":1205862400,"capture_is_r0":False,"accepted_rounds_credit":0},"active_parameter_count":7,"required_nonempty_subsets":127,"fixed_top5_replay":5,"original_gradient_only":True,"qps_outer_acceptance_only":True,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"preflight_current_known_blockers":["xp_template_absent"],"files":files(stage)})
 bid=sha_bytes(canonical(m));m["bundle_id"]=bid;target=OUT/bid
 if target.exists():shutil.rmtree(raw);return target
 (stage/"bundle_manifest.json").write_bytes(canonical(m));(stage/"bundle_manifest.json.sha256").write_text(sha(stage/"bundle_manifest.json")+"  bundle_manifest.json\n");freeze(stage);os.rename(stage,target);target.chmod(0o555);shutil.rmtree(raw)
 actual=json.loads((target/"bundle_manifest.json").read_text());u=dict(actual);u.pop("bundle_id");
 if sha_bytes(canonical(u))!=bid or files(target)!=m["files"] or any(stat.S_IMODE(p.stat().st_mode)&0o222 for p in [target,*target.rglob("*")]):raise BuildError("published closure differs")
 return target
def main():
 t=build();print(json.dumps({"status":"PASS_IMMUTABLE_COMPUTE00_DISK1_RELOCATION_NOT_LAUNCHED","bundle":str(t),"bundle_id":t.name,"manifest_sha256":sha(t/"bundle_manifest.json"),"parent_tests":"93 passed","relocation_tests":"5 passed","current_preflight":"NOT_READY_xp_template_absent","remote_actions":0,"accepted_rounds_credit":0},indent=2,sort_keys=True))
if __name__=="__main__":
 try:main()
 except (BuildError,OSError,ValueError,subprocess.SubprocessError) as e:sys.stderr.write("NOT_READY: %s\n"%e);raise SystemExit(2)
