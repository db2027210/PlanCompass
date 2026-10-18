#!/usr/bin/env python3
"""Build the sealed systemd-219/runtime-inventory successor offline."""
from __future__ import annotations

import hashlib
import importlib.util
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
PARENT_ID = "9e8414630d1f18d11fffad6325683d9a01bf78970da0a049a5ccded90e4ca11d"
PARENT_MANIFEST_SHA256 = "b5d13db2d9c75af787600bd195328cc47b999ab0ad95b00929f41f73d18452bd"
PARENT = ROOT / "artifacts/mysql_xp_compute00_executable_bound_v3" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_systemd219_runtime_inventory_v4"
SCHEMA = "mysql_xp_compute00_systemd219_runtime_inventory_successor_v4"
STATUS = "SEALED_COMPUTE00_SYSTEMD219_RUNTIME_INVENTORY_NOT_LAUNCHED"
TEST = "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"
SERVICE = "launch/mysql_xp_compute00_adopted_service_v1.py"
PREFLIGHT = "preflight/mysql_xp_compute00_disk1_preflight_v2.py"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
RUNBOOK = "operator/mysql_xp_compute00_fresh_r0_runbook_v1.md"
INVALID_ATTEMPT = "xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a"
PUBLISHED_SUPERSEDED_ID = "8c1b7ef4192dc09d7620a9858789a8698e115c7c65af7767c7f504b1a19f2ab9"
PUBLISHED_SUPERSEDED_MANIFEST_SHA256 = "6f4c1a3c3388f60ec910bb082d67741883668426ecb4bcf721842ba8c7428b80"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError("%s patch point count differs" % label)
    return text.replace(old, new, 1)


def writable(root):
    root.chmod(0o755)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BuildError("bundle symlink prohibited")
        path.chmod(0o755 if path.is_dir() else
                   stat.S_IMODE(path.stat().st_mode) | 0o200)


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("bundle symlink prohibited")
        executable = path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o111
        path.chmod(0o555 if executable else 0o444)
    root.chmod(0o555)


def files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            if relative not in ("bundle_manifest.json", "bundle_manifest.json.sha256"):
                result[relative] = sha(path)
    return result


def patch_service(stage):
    path = stage / SERVICE
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '        != "mysql_xp_compute00_executable_bound_successor_v3"',
        '        != "%s"' % SCHEMA,
        "service schema",
    )
    marker = "\ndef _host_executor_identity(expected_unit):\n"
    helper = '''
def _transient_unit_storage_identity(unit, expected_unit):
    pair = (unit.get("FragmentPath"), unit.get("UnitFileState"))
    accepted = {
        ("/run/systemd/system/" + expected_unit, "static"):
            "system_static_v1",
        ("/run/systemd/transient/" + expected_unit, "transient"):
            "systemd219_transient_v1",
    }
    representation = accepted.get(pair)
    if representation is None:
        raise ServiceError("transient executor storage identity differs")
    return {
        "fragment_path": pair[0],
        "unit_file_state": pair[1],
        "representation": representation,
    }


def _host_executor_identity(expected_unit):
'''
    text = replace_once(text, marker, "\n" + helper, "storage helper")
    old = '''    if (
        int(unit.get("MainPID") or 0) != os.getpid()
        or unit.get("ActiveState") != "activating"
        or unit.get("SubState") != "start"
        or unit.get("ControlGroup") != "/system.slice/" + expected_unit
        or unit.get("Transient") != "yes"
        or unit.get("FragmentPath") != "/run/systemd/system/" + expected_unit
        or unit.get("SourcePath") not in ("", None)
        or unit.get("UnitFileState") != "static"
    ):
        raise ServiceError("transient executor systemd identity differs")
'''
    new = '''    storage_identity = _transient_unit_storage_identity(
        unit, expected_unit
    )
    if (
        int(unit.get("MainPID") or 0) != os.getpid()
        or unit.get("ActiveState") != "activating"
        or unit.get("SubState") != "start"
        or unit.get("ControlGroup") != "/system.slice/" + expected_unit
        or unit.get("Transient") != "yes"
        or unit.get("SourcePath") not in ("", None)
    ):
        raise ServiceError("transient executor systemd identity differs")
'''
    text = replace_once(text, old, new, "systemd identity")
    text = replace_once(
        text,
        '        "control_group": unit["ControlGroup"],\n',
        '        "control_group": unit["ControlGroup"],\n'
        '        "transient_unit_storage_identity": storage_identity,\n',
        "executor evidence",
    )
    text = replace_once(
        text,
        '    args = parser.parse_args(argv)\n',
        '    args = parser.parse_args(argv)\n'
        '    if args.attempt_id == "%s":\n'
        '        raise ServiceError("invalidated zero-credit attempt reuse prohibited")\n'
        % INVALID_ATTEMPT,
        "invalid attempt gate",
    )
    path.write_text(text, encoding="utf-8")


def patch_schema_consumers(stage):
    for relative in (
        "launch/mysql_xp_compute00_formal10_materializer_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_xp_qualification_authority_v1.py",
    ):
        path = stage / relative
        text = path.read_text(encoding="utf-8")
        anchor = '"mysql_xp_compute00_executable_bound_successor_v3",\n'
        text = replace_once(text, anchor, anchor + '            "%s",\n' % SCHEMA,
                            relative + " schema")
        path.write_text(text, encoding="utf-8")
    path = stage / MATERIALS
    text = path.read_text(encoding="utf-8")
    anchor = ('    "mysql_xp_compute00_executable_bound_successor_v3": '
              '"b4bd3255634e952e33ea603bbd4ec4b5eb0493450987c041ca3a220414c8a7c4",\n')
    text = replace_once(text, anchor,
                        anchor + '    "%s": "%s",\n' % (SCHEMA, PARENT_ID),
                        "materials mapping")
    text = replace_once(
        text,
        '    attempt_id = new_attempt_id(now)\n',
        '    attempt_id = new_attempt_id(now)\n'
        '    if attempt_id == "%s":\n'
        '        raise MaterialError("invalidated zero-credit attempt reuse prohibited")\n'
        % INVALID_ATTEMPT,
        "materials invalid attempt gate",
    )
    path.write_text(text, encoding="utf-8")


def patch_preflight(stage):
    path = stage / PREFLIGHT
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "import argparse,hashlib,importlib.util,json,os,re,socket,sys",
                        "import argparse,hashlib,importlib.util,json,os,pwd,grp,re,socket,stat,sys",
                        "preflight imports")
    anchor = 'FORBIDDEN_PATH_PREFIX="/disk2";FORBIDDEN_NAMES=("mysqld","hybench","benchbase","mysql_xp","mysql-xp")\n'
    constants = '''PREDECESSOR_BUNDLE_ID="%s"
PREDECESSOR_MANIFEST_SHA256="%s"
PUBLISHED_SUPERSEDED_BUNDLE_ID="%s"
PUBLISHED_SUPERSEDED_MANIFEST_SHA256="%s"
INVALIDATED_ATTEMPT_ID="%s"
CONTROLLED_AUTH_FILES={
 "private_credentials/mysql-root-bootstrap.cnf":"cfbc72b91a6942527e7c96a2c628ae28d66013a4b880a4ef4c94986327fbc68f",
 "private_credentials/mysql-client.cnf":"85515191e119b11dab0335fb4d36620be6204cee78664738c0cf6fbf1d2f4708",
 "private_credentials/hybench_xp_qualification.properties":"5111fdf54d87f82bdd1ec1c4b826579ed17a1a4edeec0cd9f4f213e2817ea6fe",
 "private_authority/main_agent_token":"8f84343d3dcae5aa54402e676a447a289429cf14355b2bf26d659ba260422667",
}
''' % (PARENT_ID, PARENT_MANIFEST_SHA256, PUBLISHED_SUPERSEDED_ID,
       PUBLISHED_SUPERSEDED_MANIFEST_SHA256, INVALID_ATTEMPT)
    text = replace_once(text, anchor, anchor + constants, "preflight constants")
    marker = 'def mount_row(path=Path("/proc/self/mountinfo"))->dict[str,Any]:\n'
    helpers = '''def canonical(value:Any)->bytes:return (json.dumps(value,ensure_ascii=True,indent=2,sort_keys=True,allow_nan=False)+"\\n").encode()
def owner(path:Path)->tuple[str,str]:
 value=path.stat();return pwd.getpwuid(value.st_uid).pw_name,grp.getgrgid(value.st_gid).gr_name
def sealed_bundle_identity(bundle:Path,expected_id:str,expected_manifest_sha:str|None=None)->dict[str,Any]:
 if bundle.is_symlink() or not bundle.is_dir() or bundle.name!=expected_id:raise PreflightError("controlled bundle path differs")
 if owner(bundle)!=("anonymous","anonymous") or stat.S_IMODE(bundle.stat().st_mode)!=0o555:raise PreflightError("controlled bundle root metadata differs")
 manifest_path=bundle/"bundle_manifest.json";manifest=json.loads(manifest_path.read_text())
 unsigned=dict(manifest);observed=unsigned.pop("bundle_id",None)
 if observed!=expected_id or hashlib.sha256(canonical(unsigned)).hexdigest()!=expected_id:raise PreflightError("controlled bundle content address differs")
 if expected_manifest_sha and sha(manifest_path)!=expected_manifest_sha:raise PreflightError("predecessor manifest identity differs")
 actual={}
 for item in sorted(bundle.rglob("*")):
  if item.is_symlink():raise PreflightError("controlled bundle symlink prohibited")
  expected_mode=0o555 if item.is_dir() or (item.is_file() and stat.S_IMODE(item.stat().st_mode)&0o111) else 0o444
  if owner(item)!=("anonymous","anonymous") or stat.S_IMODE(item.stat().st_mode)!=expected_mode:raise PreflightError("controlled bundle metadata differs")
  relative=item.relative_to(bundle).as_posix()
  if item.is_file() and relative not in ("bundle_manifest.json","bundle_manifest.json.sha256"):actual[relative]=sha(item)
 if actual!=manifest.get("files"):raise PreflightError("controlled bundle closure differs")
 if (bundle/"bundle_manifest.json.sha256").read_text()!=sha(manifest_path)+"  bundle_manifest.json\\n":raise PreflightError("controlled bundle sidecar differs")
 return {"bundle_id":expected_id,"manifest_sha256":sha(manifest_path),"files":len(actual)}
def controlled_runtime_inventory(runtime:Path,current_bundle:Path|None=None)->dict[str,Any]:
 if runtime.is_symlink() or not runtime.is_dir():raise PreflightError("controlled runtime root absent or linked")
 if owner(runtime)!=("root","root") or stat.S_IMODE(runtime.stat().st_mode)!=0o755:raise PreflightError("runtime root metadata differs")
 if {p.name for p in runtime.iterdir()}!={"bundles","authorizations"}:raise PreflightError("runtime root top-level inventory differs")
 bundles=runtime/"bundles";auth=runtime/"authorizations"
 if bundles.is_symlink() or auth.is_symlink() or not bundles.is_dir() or not auth.is_dir():raise PreflightError("runtime inventory roots differ")
 if any(owner(p)!=("root","root") or stat.S_IMODE(p.stat().st_mode)!=0o755 for p in (bundles,auth)):raise PreflightError("runtime inventory root metadata differs")
 current_id=None;current_manifest_sha=None
 if current_bundle is not None and (current_bundle/"bundle_manifest.json").is_file():
  current_manifest=json.loads((current_bundle/"bundle_manifest.json").read_text());current_id=current_manifest.get("bundle_id");current_manifest_sha=sha(current_bundle/"bundle_manifest.json")
 allowed={PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID};
 if current_id:allowed.add(current_id)
 observed={p.name for p in bundles.iterdir()}
 if not {PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID}<=observed or not observed<=allowed:raise PreflightError("runtime bundle inventory differs")
 identities=[sealed_bundle_identity(bundles/PREDECESSOR_BUNDLE_ID,PREDECESSOR_BUNDLE_ID,PREDECESSOR_MANIFEST_SHA256),sealed_bundle_identity(bundles/PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_SUPERSEDED_MANIFEST_SHA256)]
 if current_id in observed and current_id!=PREDECESSOR_BUNDLE_ID:identities.append(sealed_bundle_identity(bundles/current_id,current_id,current_manifest_sha))
 if {p.name for p in auth.iterdir()}!={INVALIDATED_ATTEMPT_ID}:raise PreflightError("authorization inventory differs")
 old=auth/INVALIDATED_ATTEMPT_ID
 expected_dirs={".","private_credentials","private_authority"};actual_dirs=set();actual_files={}
 for item in [old,*old.rglob("*")]:
  if item.is_symlink():raise PreflightError("authorization symlink prohibited")
  if owner(item)!=("anonymous","anonymous"):raise PreflightError("authorization owner differs")
  relative="." if item==old else item.relative_to(old).as_posix()
  mode=stat.S_IMODE(item.stat().st_mode)
  if item.is_dir():
   actual_dirs.add(relative)
   if mode!=0o700:raise PreflightError("authorization directory mode differs")
  elif item.is_file():
   if mode!=0o600:raise PreflightError("authorization file mode differs")
   actual_files[relative]=sha(item)
  else:raise PreflightError("authorization special file prohibited")
 if actual_dirs!=expected_dirs or actual_files!=CONTROLLED_AUTH_FILES:raise PreflightError("zero-credit authorization closure differs")
 if (runtime/INVALIDATED_ATTEMPT_ID).exists() or (runtime/INVALIDATED_ATTEMPT_ID).is_symlink():raise PreflightError("invalidated attempt root unexpectedly exists")
 return {"status":"PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY","bundles":identities,"invalidated_authorization":{"attempt_id":INVALIDATED_ATTEMPT_ID,"accepted_rounds_credit":0,"reusable":False,"files":sorted(actual_files)},"new_bundle_no_overwrite_required":True,"fresh_attempt_and_authorization_required":True}
'''
    text = replace_once(text, marker, helpers + marker, "preflight inventory helpers")
    text = text.replace("runtime_exists:bool,template_exists:bool", "runtime_exists:bool,template_exists:bool,runtime_inventory:dict[str,Any]|None=None", 1)
    text = replace_once(text, ' if runtime_exists:errors.append("runtime_root_not_fresh")',
                        ' if not runtime_exists or not isinstance(runtime_inventory,dict) or runtime_inventory.get("status")!="PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY":errors.append("controlled_runtime_inventory")',
                        "preflight runtime gate")
    text = replace_once(text, '"runtime_root_exists":runtime_exists,"template":str(TEMPLATE)',
                        '"runtime_root_exists":runtime_exists,"runtime_inventory":runtime_inventory,"template":str(TEMPLATE)',
                        "preflight output inventory")
    old = ' return validate(hostname=socket.gethostname(),row=row,device_dev=Path("/sys/class/block/nvme1n1/dev").read_text().strip(),device_numa=int(Path("/sys/class/block/nvme1n1/device/numa_node").read_text()),node1=node1,online=online,conflicts=process_inventory(),runtime_exists=RUNTIME.exists(),template_exists=exists,template_identity=identity,port_free=free,require_ready=require_ready)'
    new = ' inventory=controlled_runtime_inventory(RUNTIME,Path(__file__).resolve().parents[1]) if RUNTIME.exists() else None\n return validate(hostname=socket.gethostname(),row=row,device_dev=Path("/sys/class/block/nvme1n1/dev").read_text().strip(),device_numa=int(Path("/sys/class/block/nvme1n1/device/numa_node").read_text()),node1=node1,online=online,conflicts=process_inventory(),runtime_exists=RUNTIME.exists(),template_exists=exists,template_identity=identity,port_free=free,runtime_inventory=inventory,require_ready=require_ready)'
    text = replace_once(text, old, new, "preflight observe")
    path.write_text(text, encoding="utf-8")


def patch_runbook(stage):
    path = stage / RUNBOOK
    text = path.read_text(encoding="utf-8")
    text = text.replace("The runtime root must not exist at this gate.",
                        "The runtime root must match the sealed zero-credit predecessor inventory exactly at this gate.")
    text = text.replace('.runtime_root_exists == false and ',
                        '.runtime_root_exists == true and .runtime_inventory.status == "PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY" and .runtime_inventory.invalidated_authorization.reusable == false and ')
    text = text.replace('mkdir -p "$RUNTIME_ROOT/bundles"\ncp -a --',
                        'test -d "$RUNTIME_ROOT/bundles/%s"\ntest ! -e "$RUNTIME_ROOT/bundles/.${BUNDLE_ID}.staging"\ncp -a --' % PARENT_ID)
    text = text.replace("Install the sealed external authorization directory exactly",
                        "Never reuse `%s` or its installed authorization. Install a newly generated sealed external authorization directory exactly" % INVALID_ATTEMPT)
    path.write_text(text, encoding="utf-8")


def patch_test(stage):
    path = stage / TEST
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, 'SCHEMA = "mysql_xp_compute00_executable_bound_successor_v3"', 'SCHEMA = "%s"' % SCHEMA, "test schema")
    text = replace_once(text, 'PARENT_ID = "b4bd3255634e952e33ea603bbd4ec4b5eb0493450987c041ca3a220414c8a7c4"', 'PARENT_ID = "%s"' % PARENT_ID, "test parent")
    text = replace_once(text, '"SEALED_COMPUTE00_EXECUTABLE_BOUND_NOT_LAUNCHED"', '"%s"' % STATUS, "test status")
    text = replace_once(text, 'conflicts=[], runtime_exists=False, template_exists=True,', 'conflicts=[], runtime_exists=True, template_exists=True,\n            runtime_inventory={"status": "PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY"},', "test preflight")
    insert = '''
    def test_systemd219_transient_storage_identity_is_exact_pair(self):
        unit = {"FragmentPath": "/run/systemd/transient/example.service", "UnitFileState": "transient"}
        observed = self.service._transient_unit_storage_identity(unit, "example.service")
        self.assertEqual(observed["representation"], "systemd219_transient_v1")
        legacy = {"FragmentPath": "/run/systemd/system/example.service", "UnitFileState": "static"}
        self.assertEqual(self.service._transient_unit_storage_identity(legacy, "example.service")["representation"], "system_static_v1")
        for bad in (
            {"FragmentPath": "/run/systemd/transient/example.service", "UnitFileState": "static"},
            {"FragmentPath": "/run/systemd/system/example.service", "UnitFileState": "transient"},
            {"FragmentPath": "/tmp/example.service", "UnitFileState": "transient"},
        ):
            with self.assertRaises(self.service.ServiceError):
                self.service._transient_unit_storage_identity(bad, "example.service")

    def test_zero_credit_runtime_inventory_gate_is_fail_closed(self):
        good = {"status": "PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY"}
        target = self.preflight.cpus(RESERVED_CPUS)
        common = dict(hostname="compute00", row={"source":"/dev/nvme1n1","major_minor":"259:6","filesystem":"ext4","mount_options":["rw"],"super_options":[]}, device_dev="259:6", device_numa=0, node1=target, online=target, conflicts=[], template_exists=True, template_identity={"status":"PASS_EXACT_CANONICAL_TEMPLATE"}, port_free=True, require_ready=False)
        self.assertEqual(self.preflight.validate(runtime_exists=True, runtime_inventory=good, **common)["status"], "PASS_READY_NOT_LAUNCHED")
        for exists, inventory in ((False, None), (True, None), (True, {"status":"NOT_READY"})):
            self.assertEqual(self.preflight.validate(runtime_exists=exists, runtime_inventory=inventory, **common)["status"], "NOT_READY")

'''
    text = replace_once(text, '\n\nif __name__ == "__main__":\n', '\n' + insert + '\nif __name__ == "__main__":\n', "test insert")
    path.write_text(text, encoding="utf-8")


def load_base():
    path = ROOT / "shell/build_mysql_xp_compute00_executable_bound_v3.py"
    spec = importlib.util.spec_from_file_location("xp_v3_builder", path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.SCHEMA = SCHEMA;module.PARENT_ID = PARENT_ID
    return module


def bind_contracts(stage):
    base = load_base()
    for relative in ("xp_executable_launch_contract_v3.json", "distributed_runtime_contract.json"):
        base.bind_contract(stage / relative, stage)


def validate_target(target, bundle_id):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest);observed = unsigned.pop("bundle_id", None)
    if observed != bundle_id or target.name != bundle_id or sha_bytes(canonical(unsigned)) != bundle_id or files(target) != manifest.get("files"):
        raise BuildError("published content-addressed closure differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XP_BUNDLE_UNDER_TEST=str(target), XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run([str(PYTHON), "-B", str(target / TEST)], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    if completed.returncode:
        raise BuildError("sealed tests failed:\n" + completed.stdout)
    material_path = target / MATERIALS
    spec = importlib.util.spec_from_file_location("xp_v4_materials_fixture", material_path)
    materials = importlib.util.module_from_spec(spec);spec.loader.exec_module(materials)
    with tempfile.TemporaryDirectory(prefix="xp-v4-materials-fixture-", dir="/tmp") as temporary:
        fixture = materials.create_materials(
            target, Path(temporary) / "output", valid_minutes=10,
            root_password="CHANGE_ME",
        )
        if (
            fixture.get("status") != "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED"
            or fixture.get("bundle_id") != bundle_id
            or fixture.get("formal_r0_authorized") is not False
            or fixture.get("accepted_round_authorized") is not False
        ):
            raise BuildError("offline bootstrap materials fixture differs")
    return completed.stdout


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("parent manifest identity differs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-systemd219-v4-", dir=str(OUTPUT)));stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage);writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):(stage/name).unlink()
        patch_service(stage);patch_schema_consumers(stage);patch_preflight(stage);patch_runbook(stage);patch_test(stage)
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_systemd219_runtime_inventory_v4.py")
        remediation = {"schema_version":"mysql_xp_systemd219_transient_runtime_inventory_remediation_v1","status":"SEALED_FIX_NOT_LAUNCHED","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"predecessor_bundle_id":PARENT_ID,"published_superseded_zero_credit_bundle_id":PUBLISHED_SUPERSEDED_ID,"failed_attempt_id":INVALID_ATTEMPT,"failed_attempt_credit":0,"failed_attempt_reuse_prohibited":True,"observed_systemd":{"FragmentPath":"/run/systemd/transient/ra-mysql-xp-v12-copy-b43be47066b74d81.service","UnitFileState":"transient","Transient":"yes"},"incompatible_predecessor_expectation":{"FragmentPath":"/run/systemd/system/<unit>","UnitFileState":"static"},"accepted_exact_representations":[["/run/systemd/system/<unit>","static"],["/run/systemd/transient/<unit>","transient"]],"mixed_representation_prohibited":True,"runtime_root_existing_inventory_required":True,"new_bundle_no_overwrite_atomic_publication_required":True,"postgresql_mutation_prohibited":True}
        (stage / "authority/mysql_xp_systemd219_transient_runtime_inventory_remediation_v1.json").write_bytes(canonical(remediation))
        materialization_failure = {"schema_version":"mysql_xp_published_materialization_failure_v1","status":"INVALID_ZERO_CREDIT_DO_NOT_LAUNCH","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"bundle_id":PUBLISHED_SUPERSEDED_ID,"bundle_manifest_sha256":PUBLISHED_SUPERSEDED_MANIFEST_SHA256,"failure":"NOT_READY: successor parent-throttle collector closure differs","failure_phase":"verified_bundle_before_attempt_material_creation","root_cause":"bundle_manifest.collector_sha256 retained predecessor value instead of the schema-patched live-readback collector hash","attempt_created":False,"authorization_created":False,"database_started":False,"workload_started":False,"accepted_rounds_credit":0,"reuse_prohibited":True,"postgresql_mutation":False}
        (stage / "authority/mysql_xp_8c1_materialization_failure_20260803.json").write_bytes(canonical(materialization_failure))
        bind_contracts(stage)
        parent = json.loads((PARENT / "bundle_manifest.json").read_text())
        manifest = {k:v for k,v in parent.items() if k not in ("bundle_id","files")}
        manifest.update({"schema_version":SCHEMA,"status":STATUS,"predecessor_bundle_id":PARENT_ID,"supersedes_bundle_id":PARENT_ID,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"systemd219_transient_compatibility":{"accepted_exact_pairs":[{"fragment_path":"/run/systemd/system/<unit>","unit_file_state":"static"},{"fragment_path":"/run/systemd/transient/<unit>","unit_file_state":"transient"}],"mixed_pairs_prohibited":True},"controlled_existing_runtime_inventory":{"required_bundle_ids":[PARENT_ID,PUBLISHED_SUPERSEDED_ID],"published_superseded_zero_credit_bundle_id":PUBLISHED_SUPERSEDED_ID,"invalidated_attempt_id":INVALID_ATTEMPT,"accepted_rounds_credit":0,"reuse_prohibited":True,"new_bundle_no_overwrite_atomic_publication_required":True},"service_sha256":sha(stage/SERVICE),"collector_sha256":sha(stage/"qualification/mysql_distributed_live_readback_qualification_v1.py"),"active_preflight":PREFLIGHT,"executable_binding_test":TEST,"operator_runbook":RUNBOOK,"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files(stage)})
        bundle_id = sha_bytes(canonical(manifest));manifest["bundle_id"] = bundle_id;target = OUTPUT / bundle_id
        if target.exists():return target,validate_target(target,bundle_id),"ADOPTED_IDENTICAL"
        (stage/"bundle_manifest.json").write_bytes(canonical(manifest));(stage/"bundle_manifest.json.sha256").write_text(sha(stage/"bundle_manifest.json")+"  bundle_manifest.json\n")
        freeze(stage);stage.chmod(0o755);os.rename(stage,target);target.chmod(0o555)
        return target,validate_target(target,bundle_id),"PUBLISHED_NEW"
    finally:
        if raw.exists():writable(raw);shutil.rmtree(raw)


def main():
    target,output,mode=build()
    print(json.dumps({"schema_version":"mysql_xp_compute00_systemd219_runtime_inventory_build_result_v1","status":"PASS_IMMUTABLE_COMPATIBILITY_SUCCESSOR_NOT_LAUNCHED","publish_mode":mode,"bundle":str(target),"bundle_id":target.name,"manifest_sha256":sha(target/"bundle_manifest.json"),"tests":"PASS","offline_bootstrap_materials_fixture":"PASS_NON_SECRET_NOT_LAUNCHED","test_output":output.strip().splitlines()[-1],"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"formal_launch_authorized":False,"accepted_rounds_credit":0,"remote_actions":0},indent=2,sort_keys=True))


if __name__ == "__main__":
    try:main()
    except (BuildError,OSError,ValueError,subprocess.SubprocessError) as error:raise SystemExit("NOT_READY: "+str(error))
