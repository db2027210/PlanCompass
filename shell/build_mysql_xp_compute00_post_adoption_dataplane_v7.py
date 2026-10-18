#!/usr/bin/env python3
"""Build the minimal XP data-plane successor after exact v12 daemon adoption."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID = "02b460d96dfd69972ca76a2b9620228fc2d239d46453d3c880d8ac001a7f12b4"
PARENT_MANIFEST_SHA256 = "1c5c02d5e6cf1a57e815de50797fda01339827a6e58caef5d0d1d1aea38811be"
PARENT = ROOT / "artifacts/mysql_xp_compute00_systemd219_hybrid_cgroup_v6" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_post_adoption_dataplane_v7"
RECEIPT_SOURCE = ROOT / "preflight/mysql_xp_v12_daemon_adoption_receipt_20260803.json"
RECEIPT_RELATIVE = "authority/mysql_xp_v12_daemon_adoption_receipt_20260803.json"
RECEIPT_SHA256 = "6e402264835700d6b8f68f6fa7eab0ceaa30814e006698c6db3494292c4f59b7"
V12_ID = "feb0662c6d9f8cac7e428ad2d4559f640be3c7c58108d63f84ff5082f3acbf4a"
V12_MANIFEST_SHA256 = "6e490820bce97d2fe81a772a1c18c974d981eb1b6469281bbfd3abf463e827fd"
SCHEMA = "mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6"
STATUS = "SEALED_COMPUTE00_POST_DAEMON_ADOPTION_DATA_PLANE_NOT_LAUNCHED"
TEST = "tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py"
PREFLIGHT = "preflight/mysql_xp_compute00_disk1_preflight_v2.py"
RUNBOOK = "operator/mysql_xp_compute00_fresh_r0_runbook_v1.md"
PRESERVED_PREFIXES = ("launch/", "qualification/", "test_support/", "runtime/")
PRESERVED_EXACT = ("xp_executable_launch_contract_v3.json", "distributed_runtime_contract.json")

BUNDLES = {
    "9e8414630d1f18d11fffad6325683d9a01bf78970da0a049a5ccded90e4ca11d": "b5d13db2d9c75af787600bd195328cc47b999ab0ad95b00929f41f73d18452bd",
    "8c1b7ef4192dc09d7620a9858789a8698e115c7c65af7767c7f504b1a19f2ab9": "6f4c1a3c3388f60ec910bb082d67741883668426ecb4bcf721842ba8c7428b80",
    "8af574ae79b9057736102e7390739fc21afa081df3b0e05b11a54f247a898755": "6e007dd1ab01f24bcbcc849a210d463cc18d138675252c8d8316050876f2bd70",
    "0394a18d03f56fc43b1f4a43d9bc8faf9238af7ac3e1c83f883ee5833a8c3f30": "c26eb0958ca745d7ebda7aa66f4b918de6f48963f5003e59ee13ab465507fd09",
    PARENT_ID: PARENT_MANIFEST_SHA256,
    "47a2367d2548fe140e7ea5e2536be4a9ab1f60b9dfb52c7c4cdbf7d1f95c2c52": "dc0de7d15f9cc9b1ca2a1d3e7e7fe795817cb85a0cb2d948631c2acb43251c25",
    "f60b1a7e4fa082d8455f060e402065d07e7beee5aa86a7a737735c0cb98c7fda": "0622043fee503417832c10cdbd7fe3612b6972779c3efdea9da616324e6a67e1",
    "24d1324d264688fc8d4a737126b6a729b499688063ecf4cc22e24431833362ee": "26fc54fb0bb856703280ae666902473d2621bb27cad72b7497e98affff9dd484",
    "671cbb6f98c9b223f46ad7f5499311c6aebd4a87ee7f7ce1e9fcca5169780b96": "2fa6a0a5839006619e51cce8babb78bd5f6fb7971dbdabb1aee6aa082d38de23",
    "59b782a4bcdba646635b36b9b5b1e9d349340ea62cbe2f28a9b8d8af68b4ae07": "d0f05763f0e373f7641890eab05b66fc43b7f9f47ac5890dfea305cf88e16baa",
    V12_ID: V12_MANIFEST_SHA256,
}


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            result[path.relative_to(root).as_posix()] = sha(path)
    return result


def writable(root):
    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | (0o700 if path.is_dir() else 0o200))


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("symlink prohibited in sealed bundle")
        if path.is_dir():
            path.chmod(0o555)
        elif path.is_file():
            path.chmod(0o555 if stat.S_IMODE(path.stat().st_mode) & 0o111 else 0o444)


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError("%s patch point count differs" % label)
    return text.replace(old, new, 1)


RUNTIME_CONSTANTS = '''POST_ADOPTION_BUNDLE_MANIFESTS=%s
DAEMON_RECEIPT_BUNDLE_ID="%s"
DAEMON_RECEIPT_MANIFEST_SHA256="%s"
DAEMON_RECEIPT_SHA256="%s"
DAEMON_RECEIPT_FILENAME=DAEMON_RECEIPT_BUNDLE_ID+".json"
DAEMON_PID=4149472;DAEMON_STARTTICKS=37998304
DAEMON_CMDLINE_SHA256="eb036eba455e55f13d26da8355e7aa3f6c590d24fc2e95ba2e497ab8737ced27"
DAEMON_CPUSET="192-193,448-449";DAEMON_SOCKET=Path("/run/ra-docker-mysql-xp-v2.sock")
DAEMON_SOCKET_DEVICE=25;DAEMON_SOCKET_INODE=2915;DAEMON_ENDPOINT_INODE=221659057
POSTGRESQL_IDENTITY=[{"pid":7405,"startticks":85360,"cpuset":"0-511","cmdline_sha256":"9cde4588860e292e7b38be4e4306b3169f89ff71b90170775439565d8cb550c2"}]
''' % (repr(BUNDLES), V12_ID, V12_MANIFEST_SHA256, RECEIPT_SHA256)


RUNTIME_FUNCTIONS = r'''def proc_startticks(path:Path)->int:
 raw=path.read_text(encoding="ascii");right=raw.rfind(")")
 if right<0:raise PreflightError("process stat identity differs")
 return int(raw[right+2:].split()[19])
def proc_status_value(path:Path,key:str)->str:
 m=re.search(r"^"+re.escape(key)+r":\s*(\S+)",path.read_text(errors="strict"),re.M)
 if not m:raise PreflightError("process status identity differs")
 return m.group(1)
def validate_receipt_object(receipt:dict[str,Any],authority:dict[str,Any])->dict[str,Any]:
 if receipt!=authority:raise PreflightError("daemon adoption receipt content differs")
 expected={"schema_version":"mysql_xp_compute00_daemon_adoption_receipt_v3","status":"PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY","bundle_id":DAEMON_RECEIPT_BUNDLE_ID,"bundle_manifest_sha256":DAEMON_RECEIPT_MANIFEST_SHA256,"disposition":"ADOPTED_EXACT_EXISTING_TRANSIENT_UNIT","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"daemon_adoption_authorized":True,"daemon_deployment_authorized":False,"systemd_run_authorized":False,"formal_launch_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"database_container_started":False,"client_container_started":False,"workload_started":False,"postgresql_mutation":False,"postgresql_process_identity_unchanged":True}
 for key,value in expected.items():
  if receipt.get(key)!=value:raise PreflightError("daemon adoption receipt field differs: "+key)
 live=receipt.get("live_identity") or {};sock=live.get("socket") or {};fs=sock.get("filesystem") or {};endpoint=sock.get("endpoint") or {};docker=live.get("docker") or {};unit=live.get("unit") or {}
 checks=(live.get("main_pid")==DAEMON_PID,live.get("process_startticks")==DAEMON_STARTTICKS,live.get("ppid")==1,live.get("cmdline_sha256")==DAEMON_CMDLINE_SHA256,live.get("cpuset_cpus")==DAEMON_CPUSET,sock.get("path")==str(DAEMON_SOCKET),fs.get("device")==DAEMON_SOCKET_DEVICE,fs.get("inode")==DAEMON_SOCKET_INODE,fs.get("mode")=="0660",fs.get("uid")==0,fs.get("gid")==0,endpoint.get("kernel_endpoint_inode")==DAEMON_ENDPOINT_INODE,endpoint.get("path")==str(DAEMON_SOCKET),endpoint.get("flags")==65536,endpoint.get("protocol")==0,endpoint.get("ref_count")==2,endpoint.get("type")==1,endpoint.get("state")==1,sock.get("owned_by_main_pid") is True,sock.get("owner_fds")==[3],docker.get("container_count")==0,docker.get("cgroup_driver")=="cgroupfs",docker.get("storage_driver")=="overlay2",docker.get("data_root_device")==DEVICE,docker.get("data_root_major_minor")==MAJOR_MINOR,unit.get("MainPID")==str(DAEMON_PID),unit.get("Id")=="ra-docker-mysql-xp-v2.service",unit.get("LoadState")=="loaded",unit.get("ActiveState")=="active",unit.get("SubState")=="running",receipt.get("postgresql_after")==POSTGRESQL_IDENTITY)
 if not all(checks):raise PreflightError("daemon adoption receipt live identity differs")
 return {"status":"PASS_EXACT_V12_DAEMON_ADOPTION_RECEIPT","bundle_id":DAEMON_RECEIPT_BUNDLE_ID,"receipt_sha256":DAEMON_RECEIPT_SHA256,"pid":DAEMON_PID,"process_startticks":DAEMON_STARTTICKS,"socket":str(DAEMON_SOCKET),"socket_endpoint_inode":DAEMON_ENDPOINT_INODE,"postgresql_identity":POSTGRESQL_IDENTITY}
def live_postgresql_identity(proc=Path("/proc"))->list[dict[str,Any]]:
 rows=[]
 for expected in POSTGRESQL_IDENTITY:
  root=proc/str(expected["pid"])
  try:rows.append({"pid":expected["pid"],"startticks":proc_startticks(root/"stat"),"cpuset":proc_status_value(root/"status","Cpus_allowed_list"),"cmdline_sha256":hashlib.sha256((root/"cmdline").read_bytes()).hexdigest()})
  except (OSError,UnicodeError,ValueError) as error:raise PreflightError("PostgreSQL read-only identity absent") from error
 if rows!=POSTGRESQL_IDENTITY:raise PreflightError("PostgreSQL read-only identity changed")
 return rows
def live_daemon_identity(proc=Path("/proc"))->dict[str,Any]:
 root=proc/str(DAEMON_PID)
 try:
  raw_cmd=(root/"cmdline").read_bytes();ppid=int(proc_status_value(root/"status","PPid"));affinity=proc_status_value(root/"status","Cpus_allowed_list")
 except (OSError,UnicodeError,ValueError) as error:raise PreflightError("adopted daemon process absent") from error
 if proc_startticks(root/"stat")!=DAEMON_STARTTICKS or hashlib.sha256(raw_cmd).hexdigest()!=DAEMON_CMDLINE_SHA256 or ppid!=1 or affinity!=DAEMON_CPUSET:raise PreflightError("adopted daemon process identity changed")
 state=DAEMON_SOCKET.stat(follow_symlinks=False)
 if DAEMON_SOCKET.is_symlink() or not stat.S_ISSOCK(state.st_mode) or state.st_dev!=DAEMON_SOCKET_DEVICE or state.st_ino!=DAEMON_SOCKET_INODE or stat.S_IMODE(state.st_mode)!=0o660 or state.st_uid!=0 or state.st_gid!=0:raise PreflightError("adopted daemon socket filesystem identity changed")
 endpoints=[]
 for line in (root/"net/unix").read_text(errors="strict").splitlines()[1:]:
  fields=line.split()
  if len(fields)>=8 and fields[-1]==str(DAEMON_SOCKET):endpoints.append(fields)
 if len(endpoints)!=1 or int(endpoints[0][6])!=DAEMON_ENDPOINT_INODE or endpoints[0][3]!="00010000" or endpoints[0][4]!="0001" or endpoints[0][5]!="01":raise PreflightError("adopted daemon socket endpoint identity changed")
 owned=[]
 for fd in (root/"fd").iterdir():
  try:
   if os.readlink(fd)=="socket:[%d]"%DAEMON_ENDPOINT_INODE:owned.append(int(fd.name))
  except OSError:continue
 if sorted(owned)!=[3]:raise PreflightError("adopted daemon socket ownership changed")
 completed=subprocess.run(["/usr/bin/docker","--host","unix:///run/ra-docker-mysql-xp-v2.sock","ps","-aq"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=15,check=False)
 if completed.returncode or completed.stdout.strip():raise PreflightError("dedicated daemon container-empty readback differs")
 return {"status":"PASS_EXACT_ADOPTED_DAEMON_LIVE_IDENTITY","pid":DAEMON_PID,"process_startticks":DAEMON_STARTTICKS,"cmdline_sha256":DAEMON_CMDLINE_SHA256,"cpuset":DAEMON_CPUSET,"socket":str(DAEMON_SOCKET),"socket_filesystem_inode":DAEMON_SOCKET_INODE,"socket_endpoint_inode":DAEMON_ENDPOINT_INODE,"owner_fds":owned,"container_count":0}
def controlled_runtime_inventory(runtime:Path,current_bundle:Path|None=None)->dict[str,Any]:
 if runtime.is_symlink() or not runtime.is_dir():raise PreflightError("controlled runtime root absent or linked")
 if owner(runtime)!=("root","root") or stat.S_IMODE(runtime.stat().st_mode)!=0o755:raise PreflightError("runtime root metadata differs")
 if {p.name for p in runtime.iterdir()}!={"bundles","authorizations","daemon_adoption_receipts"}:raise PreflightError("runtime root top-level inventory differs")
 bundles=runtime/"bundles";auth=runtime/"authorizations";receipts=runtime/"daemon_adoption_receipts"
 for path in (bundles,auth,receipts):
  if path.is_symlink() or not path.is_dir() or owner(path)!=("root","root") or stat.S_IMODE(path.stat().st_mode)!=0o755:raise PreflightError("runtime inventory root metadata differs")
 current_id=None;current_manifest_sha=None
 if current_bundle is not None and (current_bundle/"bundle_manifest.json").is_file():
  current_manifest=json.loads((current_bundle/"bundle_manifest.json").read_text());current_id=current_manifest.get("bundle_id");current_manifest_sha=sha(current_bundle/"bundle_manifest.json")
 required=set(POST_ADOPTION_BUNDLE_MANIFESTS);allowed=set(required)
 if current_id:allowed.add(current_id)
 observed={p.name for p in bundles.iterdir()}
 if not required<=observed or not observed<=allowed:raise PreflightError("post-adoption runtime bundle inventory differs")
 identities=[sealed_bundle_identity(bundles/key,key,value) for key,value in sorted(POST_ADOPTION_BUNDLE_MANIFESTS.items())]
 if current_id in observed and current_id not in required:identities.append(sealed_bundle_identity(bundles/current_id,current_id,current_manifest_sha))
 expected_auth={INVALIDATED_ATTEMPT_ID:CONTROLLED_AUTH_FILES,FAILED_CGROUP_ATTEMPT_ID:CONTROLLED_FAILED_AUTH_FILES,FAILED_HYBRID_ATTEMPT_ID:CONTROLLED_HYBRID_FAILED_AUTH_FILES}
 if {p.name for p in auth.iterdir()}!=set(expected_auth):raise PreflightError("authorization inventory differs")
 invalidated=[]
 for attempt_id,expected_files in sorted(expected_auth.items()):
  old=auth/attempt_id;expected_dirs={".","private_credentials","private_authority"};actual_dirs=set();actual_files={}
  for item in [old,*old.rglob("*")]:
   if item.is_symlink():raise PreflightError("authorization symlink prohibited")
   if owner(item)!=("anonymous","anonymous"):raise PreflightError("authorization owner differs")
   relative="." if item==old else item.relative_to(old).as_posix();mode=stat.S_IMODE(item.stat().st_mode)
   if item.is_dir():
    actual_dirs.add(relative)
    if mode!=0o700:raise PreflightError("authorization directory mode differs")
   elif item.is_file():
    if mode!=0o600:raise PreflightError("authorization file mode differs")
    actual_files[relative]=sha(item)
   else:raise PreflightError("authorization special file prohibited")
  if actual_dirs!=expected_dirs or actual_files!=expected_files:raise PreflightError("zero-credit authorization closure differs")
  if (runtime/attempt_id).exists() or (runtime/attempt_id).is_symlink():raise PreflightError("invalidated attempt root unexpectedly exists")
  invalidated.append({"attempt_id":attempt_id,"accepted_rounds_credit":0,"reusable":False,"files":sorted(actual_files)})
 if {p.name for p in receipts.iterdir()}!={DAEMON_RECEIPT_FILENAME}:raise PreflightError("daemon adoption receipt inventory differs")
 receipt_path=receipts/DAEMON_RECEIPT_FILENAME
 if receipt_path.is_symlink() or not receipt_path.is_file() or owner(receipt_path)!=("root","root") or stat.S_IMODE(receipt_path.stat().st_mode)!=0o600 or sha(receipt_path)!=DAEMON_RECEIPT_SHA256:raise PreflightError("daemon adoption receipt metadata differs")
 authority_path=Path(__file__).resolve().parents[1]/"authority/mysql_xp_v12_daemon_adoption_receipt_20260803.json"
 receipt=validate_receipt_object(json.loads(receipt_path.read_text()),json.loads(authority_path.read_text()))
 daemon=live_daemon_identity();postgresql=live_postgresql_identity()
 return {"status":"PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY","bundles":identities,"invalidated_authorizations":invalidated,"daemon_adoption_receipt":receipt,"adopted_daemon":daemon,"postgresql_read_only_identity":postgresql,"new_bundle_no_overwrite_required":True,"fresh_attempt_and_authorization_required":True}
'''


TEST_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations
import hashlib,importlib.util,json,os
from pathlib import Path
import unittest

BUNDLE=Path(os.environ.get("XP_BUNDLE_UNDER_TEST",Path(__file__).resolve().parents[1]))
PARENT_ID="02b460d96dfd69972ca76a2b9620228fc2d239d46453d3c880d8ac001a7f12b4"
PARENT_MANIFEST_SHA="1c5c02d5e6cf1a57e815de50797fda01339827a6e58caef5d0d1d1aea38811be"
RECEIPT_SHA="6e402264835700d6b8f68f6fa7eab0ceaa30814e006698c6db3494292c4f59b7"
def canonical(value):return (json.dumps(value,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
class PostAdoptionDataPlaneTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.manifest=json.loads((BUNDLE/"bundle_manifest.json").read_text())
  spec=importlib.util.spec_from_file_location("preflight",BUNDLE/"preflight/mysql_xp_compute00_disk1_preflight_v2.py");cls.preflight=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.preflight)
 def test_content_address_and_closure(self):
  unsigned=dict(self.manifest);observed=unsigned.pop("bundle_id")
  self.assertEqual(observed,BUNDLE.name);self.assertEqual(hashlib.sha256(canonical(unsigned)).hexdigest(),observed)
  actual={p.relative_to(BUNDLE).as_posix():sha(p) for p in sorted(BUNDLE.rglob("*")) if p.is_file() and p.name not in ("bundle_manifest.json","bundle_manifest.json.sha256")}
  self.assertEqual(actual,self.manifest["files"])
 def test_parent_and_scope_are_sealed(self):
  self.assertEqual(self.manifest["schema_version"],"mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6")
  self.assertEqual(self.manifest["predecessor_bundle_id"],PARENT_ID);self.assertEqual(self.manifest["predecessor_manifest_sha256"],PARENT_MANIFEST_SHA)
  self.assertEqual(self.manifest["formal_scope"],"mysql_only");self.assertTrue(self.manifest["cross_engine_comparison_prohibited"]);self.assertTrue(self.manifest["postgresql_mutation_prohibited"])
  self.assertFalse(self.manifest["formal_launch_authorized"]);self.assertEqual(self.manifest["accepted_rounds_credit"],0)
 def test_receipt_fixture_and_exact_validator(self):
  path=BUNDLE/"authority/mysql_xp_v12_daemon_adoption_receipt_20260803.json";self.assertEqual(sha(path),RECEIPT_SHA)
  receipt=json.loads(path.read_text());result=self.preflight.validate_receipt_object(receipt,receipt)
  self.assertEqual(result["status"],"PASS_EXACT_V12_DAEMON_ADOPTION_RECEIPT");self.assertEqual(result["pid"],4149472);self.assertEqual(result["socket_endpoint_inode"],221659057)
  damaged=json.loads(path.read_text());damaged["live_identity"]["main_pid"]+=1
  with self.assertRaises(self.preflight.PreflightError):self.preflight.validate_receipt_object(damaged,receipt)
 def test_02b_launch_and_resource_payload_is_byte_identical(self):
  preserved=self.manifest["byte_identical_02b_payload_sha256"]
  self.assertGreater(len(preserved),20)
  for relative,digest in preserved.items():self.assertEqual(sha(BUNDLE/relative),digest,relative)
  self.assertEqual(sha(BUNDLE/"launch/mysql_xp_fresh_r0_executor_v2.py"),"fc2be4fff1251f029aa35bfe9c63c0a5f3016c08a4bfebf11d2d3c104b875fa2")
  self.assertEqual(sha(BUNDLE/"test_support/mysql_xp_qualification_bootstrap_materials_v5.py"),"8ab4ee72e59c8af49f499bfbb92e53499bf25f71b8e4d2f9a6e94784f4e86e33")
 def test_inventory_is_complete(self):
  contract=self.manifest["controlled_existing_runtime_inventory"]
  self.assertEqual(len(contract["required_bundle_manifests"]),11);self.assertIn("feb0662c6d9f8cac7e428ad2d4559f640be3c7c58108d63f84ff5082f3acbf4a",contract["required_bundle_manifests"])
  self.assertEqual(len(contract["invalidated_attempt_ids"]),3);self.assertEqual(contract["required_receipt_sha256"],RECEIPT_SHA)
 def test_preflight_excludes_only_exact_adopted_pid(self):
  source=(BUNDLE/"preflight/mysql_xp_compute00_disk1_preflight_v2.py").read_text()
  self.assertIn("exclude_pids={inventory[\"adopted_daemon\"][\"pid\"]}",source)
  self.assertIn("PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY",source)
 def test_manifest_sidecar(self):
  self.assertEqual((BUNDLE/"bundle_manifest.json.sha256").read_text(),sha(BUNDLE/"bundle_manifest.json")+"  bundle_manifest.json\n")
if __name__=="__main__":unittest.main()
'''


def patch_preflight(stage):
    path = stage / PREFLIGHT
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "import argparse,hashlib,importlib.util,json,os,pwd,grp,re,socket,stat,sys", "import argparse,hashlib,importlib.util,json,os,pwd,grp,re,socket,stat,subprocess,sys", "preflight subprocess import")
    anchor = 'PUBLISHED_HYBRID_FAILED_MANIFEST_SHA256="c26eb0958ca745d7ebda7aa66f4b918de6f48963f5003e59ee13ab465507fd09"\n'
    text = replace_once(text, anchor, anchor + RUNTIME_CONSTANTS, "post-adoption constants")
    start = text.index("def controlled_runtime_inventory(")
    end = text.index("def mount_row(", start)
    text = text[:start] + RUNTIME_FUNCTIONS + text[end:]
    text = replace_once(text, 'excluded=ancestor_pids(proc) if exclude_pids is None and proc==Path("/proc") else set(exclude_pids or ())', 'excluded=(ancestor_pids(proc)|set(exclude_pids or ())) if proc==Path("/proc") else set(exclude_pids or ())', "preserve ancestry exclusions")
    old = ' inventory=controlled_runtime_inventory(RUNTIME,Path(__file__).resolve().parents[1]) if RUNTIME.exists() else None\n return validate(hostname=socket.gethostname(),row=row,device_dev=Path("/sys/class/block/nvme1n1/dev").read_text().strip(),device_numa=int(Path("/sys/class/block/nvme1n1/device/numa_node").read_text()),node1=node1,online=online,conflicts=process_inventory(),runtime_exists=RUNTIME.exists(),template_exists=exists,template_identity=identity,port_free=free,runtime_inventory=inventory,require_ready=require_ready)'
    new = ' inventory=controlled_runtime_inventory(RUNTIME,Path(__file__).resolve().parents[1]) if RUNTIME.exists() else None\n excluded={inventory["adopted_daemon"]["pid"]} if inventory else set()\n return validate(hostname=socket.gethostname(),row=row,device_dev=Path("/sys/class/block/nvme1n1/dev").read_text().strip(),device_numa=int(Path("/sys/class/block/nvme1n1/device/numa_node").read_text()),node1=node1,online=online,conflicts=process_inventory(exclude_pids={inventory["adopted_daemon"]["pid"]} if inventory else set()),runtime_exists=RUNTIME.exists(),template_exists=exists,template_identity=identity,port_free=free,runtime_inventory=inventory,require_ready=require_ready)'
    text = replace_once(text, old, new, "observe exact daemon exclusion")
    text = replace_once(text, 'runtime_inventory.get("status")!="PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY"', 'runtime_inventory.get("status")!="PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY"', "runtime status")
    text = replace_once(text, '"schema_version":"mysql_xp_compute00_disk1_preflight_v2"', '"schema_version":"mysql_xp_compute00_post_adoption_disk1_preflight_v3"', "preflight schema")
    path.write_text(text, encoding="utf-8")


def patch_runbook(stage):
    path = stage / RUNBOOK
    text = path.read_text(encoding="utf-8")
    text = text.replace("# compute00 MySQL XP fresh-R0 runbook", "# compute00 MySQL XP post-daemon-adoption data-plane runbook", 1)
    text = text.replace('python3 -B "$STAGED_BUNDLE/tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"', 'python3 -B "$STAGED_BUNDLE/tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"\npython3 -B "$STAGED_BUNDLE/tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py"', 1)
    text = text.replace("The runtime root must match the sealed zero-credit predecessor inventory exactly at this gate.", "The runtime root must match the sealed post-adoption inventory exactly, including 02b, daemon v7-v12, the v12 root-owned receipt, and the three invalidated zero-credit authorizations. The adopted PID/socket and PostgreSQL identity are re-read live.", 1)
    text = text.replace('.runtime_inventory.status == "PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY"', '.runtime_inventory.status == "PASS_CONTROLLED_POST_ADOPTION_RUNTIME_INVENTORY" and .runtime_inventory.daemon_adoption_receipt.status == "PASS_EXACT_V12_DAEMON_ADOPTION_RECEIPT" and .runtime_inventory.adopted_daemon.pid == 4149472 and .runtime_inventory.adopted_daemon.process_startticks == 37998304 and .runtime_inventory.adopted_daemon.socket == "/run/ra-docker-mysql-xp-v2.sock"', 1)
    text = text.replace('python3 -B "$FINAL_BUNDLE/tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"', 'python3 -B "$FINAL_BUNDLE/tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"\npython3 -B "$FINAL_BUNDLE/tests_compute00/test_mysql_xp_compute00_post_adoption_dataplane_v7.py"', 1)
    text = text.replace("Do not publish or launch if any command fails.", "Do not publish or start a fresh attempt if any command fails. The adopted daemon occupies its sealed daemon CPU lane by design; it is not an XP database/client workload and must not be stopped. Other matching processes remain fail-closed conflicts.", 1)
    text = text.replace("## 3. Materialize the short-lived bootstrap plan", "## 3. Start a new data-plane attempt\n\nThe exact v12 receipt/PID/socket are prerequisites. The materialization command below generates a new attempt ID and binds the existing container-empty daemon; it does not deploy another daemon.\n\n### 3.1 Materialize the short-lived bootstrap plan", 1)
    path.write_text(text, encoding="utf-8")


def preserved_hashes(parent_manifest):
    selected = {key: value for key, value in parent_manifest["files"].items()
                if key.startswith(PRESERVED_PREFIXES) or key in PRESERVED_EXACT}
    if not selected:
        raise BuildError("preserved payload selection empty")
    return selected


def validate_target(target, bundle_id):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest); observed = unsigned.pop("bundle_id", None)
    if observed != bundle_id or target.name != bundle_id or sha_bytes(canonical(unsigned)) != bundle_id or files(target) != manifest.get("files"):
        raise BuildError("published content-addressed closure differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XP_BUNDLE_UNDER_TEST=str(target))
    completed = subprocess.run([str(PYTHON), "-B", str(target / TEST), "-v"], cwd=str(ROOT), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    if completed.returncode:
        raise BuildError("sealed tests failed:\n" + completed.stdout)
    return completed.stdout


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("02b parent manifest identity differs")
    if sha(RECEIPT_SOURCE) != RECEIPT_SHA256:
        raise BuildError("v12 daemon adoption receipt identity differs")
    receipt = json.loads(RECEIPT_SOURCE.read_text())
    if receipt.get("bundle_id") != V12_ID or receipt.get("bundle_manifest_sha256") != V12_MANIFEST_SHA256 or receipt.get("status") != "PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY":
        raise BuildError("v12 daemon adoption receipt fields differ")
    parent_manifest = json.loads((PARENT / "bundle_manifest.json").read_text())
    preserved = preserved_hashes(parent_manifest)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-post-adoption-v7-", dir=str(OUTPUT)))
    stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage); writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
            (stage / name).unlink()
        patch_preflight(stage); patch_runbook(stage)
        shutil.copy2(RECEIPT_SOURCE, stage / RECEIPT_RELATIVE)
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_post_adoption_dataplane_v7.py")
        (stage / TEST).write_text(TEST_SOURCE, encoding="utf-8"); (stage / TEST).chmod(0o755)
        for relative, digest in preserved.items():
            if sha(stage / relative) != digest:
                raise BuildError("02b byte-identical payload changed: " + relative)
        manifest = {key: value for key, value in parent_manifest.items() if key not in ("bundle_id", "files")}
        manifest.update({
            "schema_version": SCHEMA,
            "status": STATUS,
            "predecessor_bundle_id": PARENT_ID,
            "predecessor_manifest_sha256": PARENT_MANIFEST_SHA256,
            "supersedes_bundle_id": PARENT_ID,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "formal_launch_authorized": False,
            "formal_r0_authorized": False,
            "accepted_round_authorized": False,
            "accepted_rounds_credit": 0,
            "daemon_adoption_binding": {
                "receipt_bundle_id": V12_ID,
                "receipt_bundle_manifest_sha256": V12_MANIFEST_SHA256,
                "receipt_sha256": RECEIPT_SHA256,
                "receipt_relative": RECEIPT_RELATIVE,
                "status": "PASS_ADOPTED_EXISTING_XP_DAEMON_ONLY",
                "disposition": "ADOPTED_EXACT_EXISTING_TRANSIENT_UNIT",
                "pid": 4149472,
                "process_startticks": 37998304,
                "cmdline_sha256": "eb036eba455e55f13d26da8355e7aa3f6c590d24fc2e95ba2e497ab8737ced27",
                "cpuset": "192-193,448-449",
                "socket": "/run/ra-docker-mysql-xp-v2.sock",
                "socket_endpoint_inode": 221659057,
                "container_count_required_before_data_plane": 0,
            },
            "controlled_existing_runtime_inventory": {
                "required_bundle_manifests": BUNDLES,
                "current_successor_optionally_present_before_publication": True,
                "required_receipt_filename": V12_ID + ".json",
                "required_receipt_sha256": RECEIPT_SHA256,
                "invalidated_attempt_ids": [
                    "xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a",
                    "xp-formal-qual-20260803T093340Z-940fb57f7c0911e6552e4cd4",
                    "xp-formal-qual-20260803T095237Z-65fe3b3fd60a537fbdd55118",
                ],
                "fresh_attempt_and_authorization_required": True,
                "accepted_rounds_credit": 0,
            },
            "postgresql_read_only_gate": {
                "required_identity": [{"pid": 7405, "startticks": 85360, "cpuset": "0-511", "cmdline_sha256": "9cde4588860e292e7b38be4e4306b3169f89ff71b90170775439565d8cb550c2"}],
                "mutation_prohibited": True,
            },
            "byte_identical_02b_payload_sha256": preserved,
            "active_preflight": PREFLIGHT,
            "operator_runbook": RUNBOOK,
            "offline_build_only": True,
            "remote_actions": 0,
            "files": files(stage),
        })
        bundle_id = sha_bytes(canonical(manifest)); manifest["bundle_id"] = bundle_id
        target = OUTPUT / bundle_id
        if target.exists():
            return target, validate_target(target, bundle_id), "ADOPTED_IDENTICAL"
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(sha(stage / "bundle_manifest.json") + "  bundle_manifest.json\n")
        freeze(stage); stage.chmod(0o755); os.rename(stage, target); target.chmod(0o555)
        return target, validate_target(target, bundle_id), "PUBLISHED_NEW"
    finally:
        if raw.exists():
            writable(raw); shutil.rmtree(raw)


def main():
    target, output, mode = build()
    print(json.dumps({
        "schema_version": "mysql_xp_compute00_post_adoption_dataplane_build_result_v1",
        "status": "PASS_IMMUTABLE_POST_ADOPTION_DATA_PLANE_NOT_LAUNCHED",
        "publish_mode": mode,
        "bundle": str(target),
        "bundle_id": target.name,
        "manifest_sha256": sha(target / "bundle_manifest.json"),
        "tests": "PASS",
        "test_output": output.strip().splitlines()[-1],
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions": 0,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit("NOT_READY: " + str(error))
