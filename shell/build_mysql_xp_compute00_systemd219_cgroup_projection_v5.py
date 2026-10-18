#!/usr/bin/env python3
"""Build the offline compute00 systemd-219 cgroup-projection successor."""
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
PARENT_ID = "8af574ae79b9057736102e7390739fc21afa081df3b0e05b11a54f247a898755"
PARENT_MANIFEST_SHA256 = "6e007dd1ab01f24bcbcc849a210d463cc18d138675252c8d8316050876f2bd70"
PARENT = ROOT / "artifacts/mysql_xp_compute00_systemd219_runtime_inventory_v4" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_systemd219_cgroup_projection_v5"
SCHEMA = "mysql_xp_compute00_systemd219_cgroup_projection_successor_v5"
STATUS = "SEALED_COMPUTE00_SYSTEMD219_CGROUP_PROJECTION_NOT_LAUNCHED"
SERVICE = "launch/mysql_xp_compute00_adopted_service_v1.py"
TEST = "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
CURRENT_FAILED_ATTEMPT = "xp-formal-qual-20260803T093340Z-940fb57f7c0911e6552e4cd4"


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
        '        != "mysql_xp_compute00_systemd219_runtime_inventory_successor_v4"',
        '        != "%s"' % SCHEMA,
        "service schema",
    )
    marker = "\ndef _host_executor_identity(expected_unit):\n"
    helper = '''
def _executor_cgroup_v1_projection(cgroup, expected_unit):
    expected = "/system.slice/" + expected_unit
    slice_parent = "/system.slice"
    required_active = {
        "name=systemd", "cpu", "cpuacct", "devices", "memory", "pids",
        "blkio",
    }
    passive = {
        "cpuset", "perf_event", "freezer", "hugetlb", "net_cls",
        "net_prio",
    }
    rows = []
    exact, parent, root, seen = set(), set(), set(), set()
    for line in cgroup.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3 or not fields[0].isdigit():
            raise ServiceError("executor cgroup readback differs")
        controllers = [value for value in fields[1].split(",") if value]
        if fields[1] == "name=systemd":
            controllers = ["name=systemd"]
        if not controllers or any(value in seen for value in controllers):
            raise ServiceError("executor cgroup controller projection differs")
        seen.update(controllers)
        path = fields[2]
        if path == expected:
            exact.update(controllers)
            projection = "exact_transient_unit"
        elif path == slice_parent and set(controllers) <= passive:
            parent.update(controllers)
            projection = "systemd219_passive_slice_parent"
        elif path == "/" and not (set(controllers) & required_active):
            root.update(controllers)
            projection = "passive_root"
        else:
            raise ServiceError("executor cgroup controller projection differs")
        rows.append({
            "hierarchy": fields[0], "controllers": controllers,
            "path": path, "projection": projection,
        })
    if (
        not rows
        or not required_active.issubset(exact)
        or "name=systemd" not in exact
        or exact.intersection(parent | root)
        or parent.intersection(root)
    ):
        raise ServiceError("executor is not a PID1-owned transient host service")
    return {
        "rows": rows,
        "required_active_controllers": sorted(required_active),
        "exact_unit_controllers": sorted(exact),
        "slice_parent_controllers": sorted(parent),
        "root_only_controllers": sorted(root),
    }


def _host_executor_identity(expected_unit):
'''
    text = replace_once(text, marker, "\n" + helper, "cgroup helper")
    old = '''    cgroup_rows = []
    exact_unit_controllers = set()
    root_only_controllers = set()
    expected_cgroup_path = "/system.slice/" + expected_unit
    for line in cgroup.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            raise ServiceError("executor cgroup readback differs")
        controllers = [value for value in fields[1].split(",") if value]
        if fields[1] == "name=systemd":
            controllers = ["name=systemd"]
        if not controllers or fields[2] not in (expected_cgroup_path, "/"):
            raise ServiceError("executor cgroup controller projection differs")
        destination = (
            exact_unit_controllers
            if fields[2] == expected_cgroup_path
            else root_only_controllers
        )
        destination.update(controllers)
        cgroup_rows.append({
            "hierarchy": fields[0],
            "controllers": controllers,
            "path": fields[2],
        })
    required_unit_controllers = {
        "name=systemd", "cpu", "cpuacct", "devices", "memory", "pids", "blkio",
    }
'''
    new = '''    projection = _executor_cgroup_v1_projection(
        cgroup, expected_unit
    )
    cgroup_rows = projection["rows"]
    required_unit_controllers = set(
        projection["required_active_controllers"]
    )
    exact_unit_controllers = set(projection["exact_unit_controllers"])
    slice_parent_controllers = set(
        projection["slice_parent_controllers"]
    )
    root_only_controllers = set(projection["root_only_controllers"])
'''
    text = replace_once(text, old, new, "cgroup parser")
    text = replace_once(
        text,
        '        or not cgroup_rows\n        or not required_unit_controllers.issubset(exact_unit_controllers)\n        or exact_unit_controllers.intersection(root_only_controllers)\n',
        '        or not cgroup_rows\n'
        '        or not required_unit_controllers.issubset(exact_unit_controllers)\n'
        '        or exact_unit_controllers.intersection(\n'
        '            slice_parent_controllers | root_only_controllers\n'
        '        )\n'
        '        or slice_parent_controllers.intersection(root_only_controllers)\n',
        "host final projection gate",
    )
    text = replace_once(
        text,
        '        "root_only_controllers": sorted(root_only_controllers),\n',
        '        "slice_parent_controllers": sorted(slice_parent_controllers),\n'
        '        "root_only_controllers": sorted(root_only_controllers),\n',
        "projection evidence",
    )
    old_gate = (
        '    if args.attempt_id == '
        '"xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a":\n'
        '        raise ServiceError("invalidated zero-credit attempt reuse prohibited")\n'
    )
    new_gate = (
        '    if args.attempt_id in (\n'
        '        "xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a",\n'
        '        "%s",\n'
        '    ):\n'
        '        raise ServiceError("invalidated zero-credit attempt reuse prohibited")\n'
    ) % CURRENT_FAILED_ATTEMPT
    text = replace_once(text, old_gate, new_gate, "failed attempt reuse")
    path.write_text(text, encoding="utf-8")


def patch_schema_consumers(stage):
    old_schema = "mysql_xp_compute00_systemd219_runtime_inventory_successor_v4"
    for relative in (
        "launch/mysql_xp_compute00_formal10_materializer_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_xp_qualification_authority_v1.py",
    ):
        path = stage / relative
        text = path.read_text(encoding="utf-8")
        anchor = '"%s",\n' % old_schema
        text = replace_once(text, anchor, anchor + '            "%s",\n' % SCHEMA,
                            relative + " schema")
        path.write_text(text, encoding="utf-8")
    path = stage / MATERIALS
    text = path.read_text(encoding="utf-8")
    anchor = '    "%s": "9e8414630d1f18d11fffad6325683d9a01bf78970da0a049a5ccded90e4ca11d",\n' % old_schema
    text = replace_once(text, anchor,
                        anchor + '    "%s": "%s",\n' % (SCHEMA, PARENT_ID),
                        "materials schema mapping")
    old_gate = (
        '    if attempt_id == '
        '"xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a":\n'
        '        raise MaterialError("invalidated zero-credit attempt reuse prohibited")\n'
    )
    new_gate = (
        '    if attempt_id in (\n'
        '        "xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a",\n'
        '        "%s",\n'
        '    ):\n'
        '        raise MaterialError("invalidated zero-credit attempt reuse prohibited")\n'
    ) % CURRENT_FAILED_ATTEMPT
    text = replace_once(text, old_gate, new_gate, "materials attempt reuse")
    path.write_text(text, encoding="utf-8")


def patch_preflight(stage):
    path = stage / "preflight/mysql_xp_compute00_disk1_preflight_v2.py"
    text = path.read_text(encoding="utf-8")
    anchor = 'PUBLISHED_SUPERSEDED_MANIFEST_SHA256="6f4c1a3c3388f60ec910bb082d67741883668426ecb4bcf721842ba8c7428b80"\n'
    addition = (
        'PUBLISHED_FAILED_BUNDLE_ID="%s"\n'
        'PUBLISHED_FAILED_MANIFEST_SHA256="%s"\n'
        'FAILED_CGROUP_ATTEMPT_ID="%s"\n'
        'CONTROLLED_FAILED_AUTH_FILES={\n'
        ' "private_credentials/mysql-root-bootstrap.cnf":"cfbc72b91a6942527e7c96a2c628ae28d66013a4b880a4ef4c94986327fbc68f",\n'
        ' "private_credentials/mysql-client.cnf":"10916ac0431f4ccbb2f6397cc0901707ce902afa820153ee899400df3bc4b346",\n'
        ' "private_credentials/hybench_xp_qualification.properties":"da464e1fafcc471123210d060fcaf64457ba6c20b9ed4013109ad66a11d592f5",\n'
        ' "private_authority/main_agent_token":"27d1138c6b4f32f5732e9955616180972ac22e2e744fc8a4bbefe04731f0dfae",\n'
        '}\n'
    ) % (PARENT_ID, PARENT_MANIFEST_SHA256, CURRENT_FAILED_ATTEMPT)
    text = replace_once(text, anchor, anchor + addition, "preflight current failure")
    text = replace_once(
        text,
        ' allowed={PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID};',
        ' allowed={PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID};',
        "preflight allowed bundles",
    )
    text = replace_once(
        text,
        ' if not {PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID}<=observed or not observed<=allowed:raise PreflightError("runtime bundle inventory differs")',
        ' if not {PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID}<=observed or not observed<=allowed:raise PreflightError("runtime bundle inventory differs")',
        "preflight required bundles",
    )
    old_identity = ' identities=[sealed_bundle_identity(bundles/PREDECESSOR_BUNDLE_ID,PREDECESSOR_BUNDLE_ID,PREDECESSOR_MANIFEST_SHA256),sealed_bundle_identity(bundles/PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_SUPERSEDED_MANIFEST_SHA256)]'
    new_identity = old_identity + '\n identities.append(sealed_bundle_identity(bundles/PUBLISHED_FAILED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID,PUBLISHED_FAILED_MANIFEST_SHA256))'
    text = replace_once(text, old_identity, new_identity, "preflight bundle identities")
    old_auth = ''' if {p.name for p in auth.iterdir()}!={INVALIDATED_ATTEMPT_ID}:raise PreflightError("authorization inventory differs")
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
    new_auth = ''' expected_auth={INVALIDATED_ATTEMPT_ID:CONTROLLED_AUTH_FILES,FAILED_CGROUP_ATTEMPT_ID:CONTROLLED_FAILED_AUTH_FILES}
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
 return {"status":"PASS_CONTROLLED_ZERO_CREDIT_RUNTIME_INVENTORY","bundles":identities,"invalidated_authorizations":invalidated,"new_bundle_no_overwrite_required":True,"fresh_attempt_and_authorization_required":True}
'''
    text = replace_once(text, old_auth, new_auth, "preflight authorizations")
    path.write_text(text, encoding="utf-8")


def patch_runbook(stage):
    path = stage / "operator/mysql_xp_compute00_fresh_r0_runbook_v1.md"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '.runtime_inventory.invalidated_authorization.reusable == false',
        '(.runtime_inventory.invalidated_authorizations | length == 2 and all(.[]; .reusable == false))',
        "runbook invalidated authorization gate",
    )
    path.write_text(text, encoding="utf-8")


def patch_test(stage):
    path = stage / TEST
    text = path.read_text(encoding="utf-8")
    text = replace_once(text,
                        'SCHEMA = "mysql_xp_compute00_systemd219_runtime_inventory_successor_v4"',
                        'SCHEMA = "%s"' % SCHEMA, "test schema")
    text = replace_once(text, 'PARENT_ID = "9e8414630d1f18d11fffad6325683d9a01bf78970da0a049a5ccded90e4ca11d"',
                        'PARENT_ID = "%s"' % PARENT_ID, "test parent")
    text = replace_once(text, '"SEALED_COMPUTE00_SYSTEMD219_RUNTIME_INVENTORY_NOT_LAUNCHED"',
                        '"%s"' % STATUS, "test status")
    insert = '''
    def test_systemd219_passive_slice_parent_projection_is_exact(self):
        unit = "ra-mysql-xp-v12-copy-0123456789abcdef.service"
        exact = "/system.slice/" + unit
        fixture = "\\n".join((
            "11:hugetlb:/", "10:perf_event:/", "9:freezer:/",
            "8:net_cls,net_prio:/", "7:cpuset:/system.slice",
            "6:pids:" + exact, "5:memory:" + exact,
            "4:devices:" + exact, "3:blkio:" + exact,
            "2:cpu,cpuacct:" + exact, "1:name=systemd:" + exact,
        )) + "\\n"
        result = self.service._executor_cgroup_v1_projection(fixture, unit)
        self.assertEqual(result["slice_parent_controllers"], ["cpuset"])
        self.assertEqual(set(result["required_active_controllers"]), {"name=systemd","cpu","cpuacct","devices","memory","pids","blkio"})
        self.assertTrue(set(result["required_active_controllers"]) <= set(result["exact_unit_controllers"]))

    def test_cgroup_projection_rejects_active_or_unknown_slice_and_duplicates(self):
        unit = "ra-mysql-xp-v12-copy-0123456789abcdef.service";exact = "/system.slice/" + unit
        active = "2:cpu,cpuacct:/system.slice\\n1:name=systemd:" + exact + "\\n"
        unknown = "9:rdma:/system.slice\\n1:name=systemd:" + exact + "\\n"
        escape = "7:cpuset:/system.slice/escape.service\\n1:name=systemd:" + exact + "\\n"
        duplicate = "7:cpuset:/\\n6:cpuset:/system.slice\\n1:name=systemd:" + exact + "\\n"
        for fixture in (active, unknown, escape, duplicate):
            with self.assertRaises(self.service.ServiceError):
                self.service._executor_cgroup_v1_projection(fixture, unit)

'''
    text = replace_once(text, '\n\nif __name__ == "__main__":\n',
                        '\n' + insert + '\nif __name__ == "__main__":\n',
                        "test cgroup insert")
    path.write_text(text, encoding="utf-8")


def bind_contracts(stage):
    path = ROOT / "shell/build_mysql_xp_compute00_executable_bound_v3.py"
    spec = importlib.util.spec_from_file_location("xp_v3_builder_for_v5", path)
    base = importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
    base.SCHEMA = SCHEMA;base.PARENT_ID = PARENT_ID
    for relative in ("xp_executable_launch_contract_v3.json", "distributed_runtime_contract.json"):
        base.bind_contract(stage / relative, stage)


def validate_target(target, bundle_id):
    manifest = json.loads((target / "bundle_manifest.json").read_text())
    unsigned = dict(manifest);observed = unsigned.pop("bundle_id", None)
    if observed != bundle_id or target.name != bundle_id or sha_bytes(canonical(unsigned)) != bundle_id or files(target) != manifest.get("files"):
        raise BuildError("published content-addressed closure differs")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", XP_BUNDLE_UNDER_TEST=str(target), XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed = subprocess.run([str(PYTHON), "-B", str(target / TEST), "-v"], cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=180)
    if completed.returncode:
        raise BuildError("sealed tests failed:\n" + completed.stdout)
    material_path = target / MATERIALS
    spec = importlib.util.spec_from_file_location("xp_v5_materials_fixture", material_path)
    materials = importlib.util.module_from_spec(spec);spec.loader.exec_module(materials)
    with tempfile.TemporaryDirectory(prefix="xp-v5-materials-fixture-", dir="/tmp") as temporary:
        fixture = materials.create_materials(target, Path(temporary) / "output", 10, "NON_SECRET_OFFLINE_CGROUP_PROJECTION_FIXTURE")
        if fixture.get("status") != "SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED" or fixture.get("bundle_id") != bundle_id or fixture.get("accepted_round_authorized") is not False:
            raise BuildError("offline bootstrap materials fixture differs")
    return completed.stdout


def build():
    if sha(PARENT / "bundle_manifest.json") != PARENT_MANIFEST_SHA256:
        raise BuildError("parent manifest identity differs")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    raw = Path(tempfile.mkdtemp(prefix=".stage-xp-cgroup-v5-", dir=str(OUTPUT)));stage = raw / "bundle"
    try:
        shutil.copytree(PARENT, stage);writable(stage)
        for name in ("bundle_manifest.json", "bundle_manifest.json.sha256"):(stage/name).unlink()
        patch_service(stage);patch_schema_consumers(stage);patch_preflight(stage);patch_runbook(stage);patch_test(stage)
        shutil.copy2(SELF, stage / "builder/build_mysql_xp_compute00_systemd219_cgroup_projection_v5.py")
        failure = {"schema_version":"mysql_xp_systemd219_cgroup_projection_failure_v1","status":"INVALID_ZERO_CREDIT_DO_NOT_REUSE","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"bundle_id":PARENT_ID,"bundle_manifest_sha256":PARENT_MANIFEST_SHA256,"attempt_id":CURRENT_FAILED_ATTEMPT,"unit":"ra-mysql-xp-v12-copy-4fddde72b06b2adc.service","failure":"executor cgroup controller projection differs","failure_phase":"prepare_copy_host_executor_identity_before_attempt_root_creation","attempt_root_created":False,"database_started":False,"workload_started":False,"accepted_rounds_credit":0,"authorization_reuse_prohibited":True,"postgresql_mutation":False}
        (stage / "authority/mysql_xp_8af_systemd219_cgroup_projection_failure_20260803.json").write_bytes(canonical(failure))
        bind_contracts(stage)
        parent = json.loads((PARENT / "bundle_manifest.json").read_text())
        manifest = {k:v for k,v in parent.items() if k not in ("bundle_id","files")}
        manifest.update({"schema_version":SCHEMA,"status":STATUS,"predecessor_bundle_id":PARENT_ID,"supersedes_bundle_id":PARENT_ID,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"cgroup_v1_executor_projection_contract":{"required_active_exact_unit_controllers":["name=systemd","cpu","cpuacct","devices","memory","pids","blkio"],"passive_slice_parent_controllers":["cpuset","perf_event","freezer","hugetlb","net_cls","net_prio"],"only_allowed_paths":["/","/system.slice","/system.slice/<sealed_transient_unit>"],"active_controller_parent_or_root_prohibited":True,"unknown_controller_slice_parent_prohibited":True,"duplicate_controller_prohibited":True,"container_or_nsenter_ancestry_prohibited":True},"controlled_existing_runtime_inventory":{"required_bundle_ids":["9e8414630d1f18d11fffad6325683d9a01bf78970da0a049a5ccded90e4ca11d","8c1b7ef4192dc09d7620a9858789a8698e115c7c65af7767c7f504b1a19f2ab9",PARENT_ID],"invalidated_attempt_ids":["xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a",CURRENT_FAILED_ATTEMPT],"accepted_rounds_credit":0,"reuse_prohibited":True,"new_bundle_no_overwrite_atomic_publication_required":True},"service_sha256":sha(stage/SERVICE),"collector_sha256":sha(stage/"qualification/mysql_distributed_live_readback_qualification_v1.py"),"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files(stage)})
        bundle_id = sha_bytes(canonical(manifest));manifest["bundle_id"] = bundle_id;target = OUTPUT / bundle_id
        if target.exists():return target,validate_target(target,bundle_id),"ADOPTED_IDENTICAL"
        (stage/"bundle_manifest.json").write_bytes(canonical(manifest));(stage/"bundle_manifest.json.sha256").write_text(sha(stage/"bundle_manifest.json")+"  bundle_manifest.json\n")
        freeze(stage);stage.chmod(0o755);os.rename(stage,target);target.chmod(0o555)
        return target,validate_target(target,bundle_id),"PUBLISHED_NEW"
    finally:
        if raw.exists():writable(raw);shutil.rmtree(raw)


def main():
    target,output,mode=build()
    print(json.dumps({"schema_version":"mysql_xp_compute00_systemd219_cgroup_projection_build_result_v1","status":"PASS_IMMUTABLE_CGROUP_PROJECTION_SUCCESSOR_NOT_LAUNCHED","publish_mode":mode,"bundle":str(target),"bundle_id":target.name,"manifest_sha256":sha(target/"bundle_manifest.json"),"tests":"PASS","offline_bootstrap_materials_fixture":"PASS_NON_SECRET_NOT_LAUNCHED","test_output":output.strip().splitlines()[-1],"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"formal_launch_authorized":False,"accepted_rounds_credit":0,"remote_actions":0},indent=2,sort_keys=True))


if __name__ == "__main__":
    try:main()
    except (BuildError,OSError,ValueError,subprocess.SubprocessError) as error:raise SystemExit("NOT_READY: "+str(error))
