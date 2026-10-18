#!/usr/bin/env python3
"""Build the evidence-exact systemd-219 hybrid-cgroup successor offline."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
PYTHON = Path("/home/anonymous/miniconda3/envs/py313/bin/python")
PARENT_ID = "0394a18d03f56fc43b1f4a43d9bc8faf9238af7ac3e1c83f883ee5833a8c3f30"
PARENT_MANIFEST_SHA256 = "c26eb0958ca745d7ebda7aa66f4b918de6f48963f5003e59ee13ab465507fd09"
PARENT = ROOT / "artifacts/mysql_xp_compute00_systemd219_cgroup_projection_v5" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_xp_compute00_systemd219_hybrid_cgroup_v6"
SCHEMA = "mysql_xp_compute00_systemd219_hybrid_cgroup_successor_v6"
STATUS = "SEALED_COMPUTE00_SYSTEMD219_HYBRID_CGROUP_NOT_LAUNCHED"
SERVICE = "launch/mysql_xp_compute00_adopted_service_v1.py"
TEST = "tests_compute00/test_mysql_xp_compute00_executable_binding_v3.py"
MATERIALS = "test_support/mysql_xp_qualification_bootstrap_materials_v5.py"
FAILED_ATTEMPT = "xp-formal-qual-20260803T095237Z-65fe3b3fd60a537fbdd55118"
FAILED_UNIT = "ra-mysql-xp-v12-copy-05d07bf99c8317ae.service"
PROBE_UNIT = "ra-mysql-xp-cgroup-probe-20260803T1758P08.service"


class BuildError(RuntimeError):
    pass


def load_v5_builder():
    path = ROOT / "shell/build_mysql_xp_compute00_systemd219_cgroup_projection_v5.py"
    spec = importlib.util.spec_from_file_location("xp_v5_builder_for_v6", path)
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


V5 = load_v5_builder()
canonical, sha, sha_bytes = V5.canonical, V5.sha, V5.sha_bytes
writable, freeze, files = V5.writable, V5.freeze, V5.files


def replace_once(text, old, new, label):
    if text.count(old) != 1:
        raise BuildError("%s patch point count differs" % label)
    return text.replace(old, new, 1)


EVIDENCE_CGROUP_TEMPLATE = """12:cpuset:/
11:rdma:/
10:pids:/system.slice/{unit}
9:freezer:/
8:perf_event:/
7:blkio:/system.slice/{unit}
6:cpu,cpuacct:/system.slice/{unit}
5:memory:/system.slice/{unit}
4:devices:/system.slice/{unit}
3:hugetlb:/
2:net_cls,net_prio:/
1:name=systemd:/system.slice/{unit}
0::/system.slice/{unit}
"""


HELPER = '''def _executor_cgroup_v1_projection(cgroup, expected_unit):
    expected = "/system.slice/" + expected_unit
    required_active = {
        "name=systemd", "cpu", "cpuacct", "devices", "memory", "pids",
        "blkio",
    }
    required_root = {
        "cpuset", "rdma", "freezer", "perf_event", "hugetlb", "net_cls",
        "net_prio",
    }
    rows = []
    exact, root, seen_controllers, seen_hierarchies = set(), set(), set(), set()
    hybrid_unified_rows = 0
    for line in cgroup.splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3 or not fields[0].isdigit():
            raise ServiceError("executor cgroup readback differs")
        hierarchy, raw_controllers, path = fields
        if hierarchy in seen_hierarchies:
            raise ServiceError("executor cgroup controller projection differs")
        seen_hierarchies.add(hierarchy)
        if hierarchy == "0":
            if raw_controllers != "" or path != expected or hybrid_unified_rows:
                raise ServiceError("executor cgroup controller projection differs")
            hybrid_unified_rows += 1
            rows.append({
                "hierarchy": hierarchy, "controllers": [], "path": path,
                "projection": "systemd219_hybrid_unified_exact_transient_unit",
            })
            continue
        controllers = [value for value in raw_controllers.split(",") if value]
        if raw_controllers == "name=systemd":
            controllers = ["name=systemd"]
        if (
            not controllers
            or any(value in seen_controllers for value in controllers)
            or any(value not in required_active | required_root for value in controllers)
        ):
            raise ServiceError("executor cgroup controller projection differs")
        seen_controllers.update(controllers)
        values = set(controllers)
        if path == expected and values <= required_active:
            exact.update(values);projection = "v1_active_exact_transient_unit"
        elif path == "/" and values <= required_root:
            root.update(values);projection = "v1_passive_or_rdma_root"
        else:
            raise ServiceError("executor cgroup controller projection differs")
        rows.append({
            "hierarchy": hierarchy, "controllers": controllers,
            "path": path, "projection": projection,
        })
    if (
        hybrid_unified_rows != 1
        or exact != required_active
        or root != required_root
        or seen_controllers != required_active | required_root
    ):
        raise ServiceError("executor is not a PID1-owned transient host service")
    return {
        "rows": rows,
        "required_active_controllers": sorted(required_active),
        "exact_unit_controllers": sorted(exact),
        "slice_parent_controllers": [],
        "root_only_controllers": sorted(root),
        "hybrid_unified_exact_unit_rows": hybrid_unified_rows,
        "rdma_projection": "root",
    }
'''


def patch_service(stage):
    path = stage / SERVICE;text = path.read_text(encoding="utf-8")
    text = replace_once(text,
        '        != "mysql_xp_compute00_systemd219_cgroup_projection_successor_v5"',
        '        != "%s"' % SCHEMA, "service schema")
    start = text.index("def _executor_cgroup_v1_projection(cgroup, expected_unit):")
    end = text.index("\ndef _host_executor_identity(expected_unit):", start)
    text = text[:start] + HELPER + text[end:]
    text = replace_once(text,
        '        "root_only_controllers": sorted(root_only_controllers),\n',
        '        "root_only_controllers": sorted(root_only_controllers),\n'
        '        "hybrid_unified_exact_unit_rows": projection[\n'
        '            "hybrid_unified_exact_unit_rows"\n'
        '        ],\n'
        '        "rdma_projection": projection["rdma_projection"],\n',
        "service hybrid evidence")
    old = '''    if args.attempt_id in (
        "xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a",
        "xp-formal-qual-20260803T093340Z-940fb57f7c0911e6552e4cd4",
    ):
        raise ServiceError("invalidated zero-credit attempt reuse prohibited")
'''
    new = old.replace("    ):\n", '        "%s",\n    ):\n' % FAILED_ATTEMPT)
    text = replace_once(text, old, new, "service failed attempt")
    path.write_text(text, encoding="utf-8")


def patch_schema_consumers(stage):
    old_schema = "mysql_xp_compute00_systemd219_cgroup_projection_successor_v5"
    for relative in (
        "launch/mysql_xp_compute00_formal10_materializer_v1.py",
        "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "qualification/mysql_xp_qualification_authority_v1.py",
    ):
        path=stage/relative;text=path.read_text(encoding="utf-8");anchor='"%s",\n'%old_schema
        text=replace_once(text,anchor,anchor+'            "%s",\n'%SCHEMA,relative+" schema");path.write_text(text,encoding="utf-8")
    path=stage/MATERIALS;text=path.read_text(encoding="utf-8")
    anchor='    "%s": "8af574ae79b9057736102e7390739fc21afa081df3b0e05b11a54f247a898755",\n'%old_schema
    text=replace_once(text,anchor,anchor+'    "%s": "%s",\n'%(SCHEMA,PARENT_ID),"materials schema")
    old='''    if attempt_id in (
        "xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a",
        "xp-formal-qual-20260803T093340Z-940fb57f7c0911e6552e4cd4",
    ):
        raise MaterialError("invalidated zero-credit attempt reuse prohibited")
'''
    new=old.replace("    ):\n",'        "%s",\n    ):\n'%FAILED_ATTEMPT)
    text=replace_once(text,old,new,"materials failed attempt");path.write_text(text,encoding="utf-8")


def patch_preflight(stage):
    path=stage/"preflight/mysql_xp_compute00_disk1_preflight_v2.py";text=path.read_text(encoding="utf-8")
    anchor='PUBLISHED_FAILED_MANIFEST_SHA256="6e007dd1ab01f24bcbcc849a210d463cc18d138675252c8d8316050876f2bd70"\n'
    addition=('PUBLISHED_HYBRID_FAILED_BUNDLE_ID="%s"\nPUBLISHED_HYBRID_FAILED_MANIFEST_SHA256="%s"\nFAILED_HYBRID_ATTEMPT_ID="%s"\nCONTROLLED_HYBRID_FAILED_AUTH_FILES={\n "private_credentials/mysql-root-bootstrap.cnf":"cfbc72b91a6942527e7c96a2c628ae28d66013a4b880a4ef4c94986327fbc68f",\n "private_credentials/mysql-client.cnf":"564b8caeaa67cd5667ed81ad82c42f60256d1bfa44be242228df7ac0b46d4578",\n "private_credentials/hybench_xp_qualification.properties":"2ebc004babc84b7d7eecfbb9e8b12eca0df6e9435b0cf70509ae559de66e24bf",\n "private_authority/main_agent_token":"8e652a1c132b660fc2c4e8efe6bede64d6f7fd82ce9a33e70f04e6bc417ecbc4",\n}\n')%(PARENT_ID,PARENT_MANIFEST_SHA256,FAILED_ATTEMPT)
    text=replace_once(text,anchor,anchor+addition,"preflight hybrid failure")
    text=replace_once(text,
        ' allowed={PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID};',
        ' allowed={PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID,PUBLISHED_HYBRID_FAILED_BUNDLE_ID};',"allowed bundles")
    text=replace_once(text,
        ' if not {PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID}<=observed or not observed<=allowed:raise PreflightError("runtime bundle inventory differs")',
        ' if not {PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID,PUBLISHED_HYBRID_FAILED_BUNDLE_ID}<=observed or not observed<=allowed:raise PreflightError("runtime bundle inventory differs")',"required bundles")
    anchor_identity=' identities.append(sealed_bundle_identity(bundles/PUBLISHED_FAILED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID,PUBLISHED_FAILED_MANIFEST_SHA256))\n'
    text=replace_once(text,anchor_identity,anchor_identity+' identities.append(sealed_bundle_identity(bundles/PUBLISHED_HYBRID_FAILED_BUNDLE_ID,PUBLISHED_HYBRID_FAILED_BUNDLE_ID,PUBLISHED_HYBRID_FAILED_MANIFEST_SHA256))\n',"hybrid bundle identity")
    text=replace_once(text,
        ' if current_id in observed and current_id!=PREDECESSOR_BUNDLE_ID:identities.append(sealed_bundle_identity(bundles/current_id,current_id,current_manifest_sha))',
        ' if current_id in observed and current_id not in {PREDECESSOR_BUNDLE_ID,PUBLISHED_SUPERSEDED_BUNDLE_ID,PUBLISHED_FAILED_BUNDLE_ID,PUBLISHED_HYBRID_FAILED_BUNDLE_ID}:identities.append(sealed_bundle_identity(bundles/current_id,current_id,current_manifest_sha))',"current identity dedupe")
    text=replace_once(text,
        ' expected_auth={INVALIDATED_ATTEMPT_ID:CONTROLLED_AUTH_FILES,FAILED_CGROUP_ATTEMPT_ID:CONTROLLED_FAILED_AUTH_FILES}',
        ' expected_auth={INVALIDATED_ATTEMPT_ID:CONTROLLED_AUTH_FILES,FAILED_CGROUP_ATTEMPT_ID:CONTROLLED_FAILED_AUTH_FILES,FAILED_HYBRID_ATTEMPT_ID:CONTROLLED_HYBRID_FAILED_AUTH_FILES}',"auth inventory")
    path.write_text(text,encoding="utf-8")


def patch_runbook(stage):
    path=stage/"operator/mysql_xp_compute00_fresh_r0_runbook_v1.md";text=path.read_text(encoding="utf-8")
    text=replace_once(text,"length == 2","length == 3","runbook auth count");path.write_text(text,encoding="utf-8")


def patch_test(stage):
    path=stage/TEST;text=path.read_text(encoding="utf-8")
    text=replace_once(text,'SCHEMA = "mysql_xp_compute00_systemd219_cgroup_projection_successor_v5"','SCHEMA = "%s"'%SCHEMA,"test schema")
    text=replace_once(text,'PARENT_ID = "8af574ae79b9057736102e7390739fc21afa081df3b0e05b11a54f247a898755"','PARENT_ID = "%s"'%PARENT_ID,"test parent")
    text=replace_once(text,'"SEALED_COMPUTE00_SYSTEMD219_CGROUP_PROJECTION_NOT_LAUNCHED"','"%s"'%STATUS,"test status")
    start=text.index("\n    def test_systemd219_passive_slice_parent_projection_is_exact")
    end=text.index('\n\nif __name__ == "__main__":',start)
    methods='''
    def test_real_systemd219_hybrid_projection_is_exact(self):
        unit = "ra-mysql-xp-v12-copy-0123456789abcdef.service"
        fixture = EVIDENCE_CGROUP_TEMPLATE.format(unit=unit)
        result = self.service._executor_cgroup_v1_projection(fixture, unit)
        self.assertEqual(result["hybrid_unified_exact_unit_rows"], 1)
        self.assertEqual(result["rdma_projection"], "root")
        self.assertEqual(result["slice_parent_controllers"], [])
        self.assertEqual(set(result["exact_unit_controllers"]), {"name=systemd","cpu","cpuacct","devices","memory","pids","blkio"})
        self.assertEqual(set(result["root_only_controllers"]), {"cpuset","rdma","freezer","perf_event","hugetlb","net_cls","net_prio"})

    def test_hybrid_empty_controller_row_is_uniquely_constrained(self):
        unit = "ra-mysql-xp-v12-copy-0123456789abcdef.service";exact = "/system.slice/" + unit
        valid = EVIDENCE_CGROUP_TEMPLATE.format(unit=unit)
        mutations = (
            valid.replace("0::" + exact, "13::" + exact),
            valid.replace("0::" + exact, "0::/"),
            valid.replace("0::" + exact, "0:cpu:" + exact),
            valid + "0::" + exact + "\\n",
            valid.replace("0::" + exact + "\\n", ""),
        )
        for fixture in mutations:
            with self.assertRaises(self.service.ServiceError):
                self.service._executor_cgroup_v1_projection(fixture, unit)

    def test_real_projection_rejects_path_controller_and_hierarchy_drift(self):
        unit = "ra-mysql-xp-v12-copy-0123456789abcdef.service";exact = "/system.slice/" + unit
        valid = EVIDENCE_CGROUP_TEMPLATE.format(unit=unit)
        mutations = (
            valid.replace("12:cpuset:/", "12:cpuset:/system.slice"),
            valid.replace("11:rdma:/", "11:rdma:" + exact),
            valid.replace("10:pids:" + exact, "10:pids:/"),
            valid.replace("11:rdma:/", "11:unknown:/"),
            valid.replace("12:cpuset:/", "11:cpuset:/"),
        )
        for fixture in mutations:
            with self.assertRaises(self.service.ServiceError):
                self.service._executor_cgroup_v1_projection(fixture, unit)
'''
    text=text[:start]+"\n"+methods+text[end:]
    # The sealed test module needs the literal evidence fixture without importing the builder.
    literal='\nEVIDENCE_CGROUP_TEMPLATE = '+repr(EVIDENCE_CGROUP_TEMPLATE)+'\n'
    class_anchor='\n\nclass ExecutableBindingTests(unittest.TestCase):\n'
    text=replace_once(text,class_anchor,literal+class_anchor,"test evidence fixture")
    path.write_text(text,encoding="utf-8")


def bind_contracts(stage):
    path=ROOT/"shell/build_mysql_xp_compute00_executable_bound_v3.py";spec=importlib.util.spec_from_file_location("xp_v3_builder_for_v6",path)
    base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base);base.SCHEMA=SCHEMA;base.PARENT_ID=PARENT_ID
    for relative in ("xp_executable_launch_contract_v3.json","distributed_runtime_contract.json"):base.bind_contract(stage/relative,stage)


def validate_target(target,bundle_id):
    manifest=json.loads((target/"bundle_manifest.json").read_text());unsigned=dict(manifest);observed=unsigned.pop("bundle_id",None)
    if observed!=bundle_id or target.name!=bundle_id or sha_bytes(canonical(unsigned))!=bundle_id or files(target)!=manifest.get("files"):raise BuildError("published content-addressed closure differs")
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE="1",XP_BUNDLE_UNDER_TEST=str(target),XP_EXPECTED_BUNDLE_ID=bundle_id)
    completed=subprocess.run([str(PYTHON),"-B",str(target/TEST),"-v"],cwd=str(ROOT),env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
    if completed.returncode:raise BuildError("sealed tests failed:\n"+completed.stdout)
    spec=importlib.util.spec_from_file_location("xp_v6_materials_fixture",target/MATERIALS);materials=importlib.util.module_from_spec(spec);spec.loader.exec_module(materials)
    with tempfile.TemporaryDirectory(prefix="xp-v6-materials-fixture-",dir="/tmp") as temporary:
        fixture=materials.create_materials(target,Path(temporary)/"output",10,"NON_SECRET_OFFLINE_HYBRID_CGROUP_FIXTURE")
        if fixture.get("status")!="SEALED_QUALIFICATION_ONLY_NOT_LAUNCHED" or fixture.get("bundle_id")!=bundle_id or fixture.get("accepted_round_authorized") is not False:raise BuildError("offline materials fixture differs")
    return completed.stdout


def build():
    if sha(PARENT/"bundle_manifest.json")!=PARENT_MANIFEST_SHA256:raise BuildError("parent manifest identity differs")
    OUTPUT.mkdir(parents=True,exist_ok=True);raw=Path(tempfile.mkdtemp(prefix=".stage-xp-hybrid-v6-",dir=str(OUTPUT)));stage=raw/"bundle"
    try:
        shutil.copytree(PARENT,stage);writable(stage)
        for name in ("bundle_manifest.json","bundle_manifest.json.sha256"):(stage/name).unlink()
        patch_service(stage);patch_schema_consumers(stage);patch_preflight(stage);patch_runbook(stage);patch_test(stage)
        shutil.copy2(SELF,stage/"builder/build_mysql_xp_compute00_systemd219_hybrid_cgroup_v6.py")
        evidence={"schema_version":"mysql_xp_compute00_systemd219_hybrid_cgroup_probe_v1","status":"SEALED_READ_ONLY_HOST_EVIDENCE","host":"compute00","systemd_version":219,"probe_unit":PROBE_UNIT,"proc_self_cgroup":EVIDENCE_CGROUP_TEMPLATE.format(unit=PROBE_UNIT),"active_v1_exact_unit_controllers":["name=systemd","cpu","cpuacct","devices","memory","pids","blkio"],"root_v1_controllers":["cpuset","rdma","freezer","perf_event","hugetlb","net_cls","net_prio"],"hybrid_unified_row":{"hierarchy":"0","controllers":"","path":"/system.slice/"+PROBE_UNIT},"rdma_projection":"root","postgresql_mutation":False,"data_plane_started":False}
        (stage/"authority/mysql_xp_compute00_systemd219_hybrid_cgroup_probe_20260803.json").write_bytes(canonical(evidence))
        failure={"schema_version":"mysql_xp_039_hybrid_cgroup_failure_v1","status":"INVALID_ZERO_CREDIT_DO_NOT_REUSE","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"bundle_id":PARENT_ID,"bundle_manifest_sha256":PARENT_MANIFEST_SHA256,"attempt_id":FAILED_ATTEMPT,"unit":FAILED_UNIT,"failure":"executor cgroup controller projection differs","exact_failure_condition":"not controllers for hierarchy 0 hybrid/unified row","failure_line":1828,"attempt_root_created":False,"database_started":False,"workload_started":False,"accepted_rounds_credit":0,"authorization_reuse_prohibited":True,"postgresql_mutation":False}
        (stage/"authority/mysql_xp_039_hybrid_cgroup_failure_20260803.json").write_bytes(canonical(failure));bind_contracts(stage)
        parent=json.loads((PARENT/"bundle_manifest.json").read_text());manifest={k:v for k,v in parent.items() if k not in ("bundle_id","files")}
        manifest.update({"schema_version":SCHEMA,"status":STATUS,"predecessor_bundle_id":PARENT_ID,"supersedes_bundle_id":PARENT_ID,"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"cgroup_v1_executor_projection_contract":{"evidence_unit":PROBE_UNIT,"required_exact_active_v1_controllers":["name=systemd","cpu","cpuacct","devices","memory","pids","blkio"],"required_root_v1_controllers":["cpuset","rdma","freezer","perf_event","hugetlb","net_cls","net_prio"],"required_unique_hybrid_unified_row":{"hierarchy":"0","controllers":"","path":"/system.slice/<sealed_transient_unit>"},"rdma_projection":"root","other_empty_controller_rows_prohibited":True,"slice_parent_projection_prohibited":True,"unknown_or_duplicate_controller_prohibited":True,"duplicate_hierarchy_prohibited":True,"container_or_nsenter_ancestry_prohibited":True},"controlled_existing_runtime_inventory":{"required_bundle_ids":["9e8414630d1f18d11fffad6325683d9a01bf78970da0a049a5ccded90e4ca11d","8c1b7ef4192dc09d7620a9858789a8698e115c7c65af7767c7f504b1a19f2ab9","8af574ae79b9057736102e7390739fc21afa081df3b0e05b11a54f247a898755",PARENT_ID],"invalidated_attempt_ids":["xp-formal-qual-20260803T084703Z-a1c8d9d653b9983ba8a2246a","xp-formal-qual-20260803T093340Z-940fb57f7c0911e6552e4cd4",FAILED_ATTEMPT],"accepted_rounds_credit":0,"reuse_prohibited":True,"new_bundle_no_overwrite_atomic_publication_required":True},"service_sha256":sha(stage/SERVICE),"collector_sha256":sha(stage/"qualification/mysql_distributed_live_readback_qualification_v1.py"),"formal_launch_authorized":False,"formal_r0_authorized":False,"accepted_round_authorized":False,"accepted_rounds_credit":0,"remote_actions":0,"files":files(stage)})
        bundle_id=sha_bytes(canonical(manifest));manifest["bundle_id"]=bundle_id;target=OUTPUT/bundle_id
        if target.exists():return target,validate_target(target,bundle_id),"ADOPTED_IDENTICAL"
        (stage/"bundle_manifest.json").write_bytes(canonical(manifest));(stage/"bundle_manifest.json.sha256").write_text(sha(stage/"bundle_manifest.json")+"  bundle_manifest.json\n")
        freeze(stage);stage.chmod(0o755);os.rename(stage,target);target.chmod(0o555);return target,validate_target(target,bundle_id),"PUBLISHED_NEW"
    finally:
        if raw.exists():writable(raw);shutil.rmtree(raw)


def main():
    target,output,mode=build();print(json.dumps({"schema_version":"mysql_xp_compute00_systemd219_hybrid_cgroup_build_result_v1","status":"PASS_IMMUTABLE_EVIDENCE_EXACT_HYBRID_CGROUP_NOT_LAUNCHED","publish_mode":mode,"bundle":str(target),"bundle_id":target.name,"manifest_sha256":sha(target/"bundle_manifest.json"),"tests":"PASS","offline_bootstrap_materials_fixture":"PASS_NON_SECRET_NOT_LAUNCHED","test_output":output.strip().splitlines()[-1],"formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,"formal_launch_authorized":False,"accepted_rounds_credit":0,"remote_actions":0},indent=2,sort_keys=True))


if __name__=="__main__":
    try:main()
    except (BuildError,OSError,ValueError,subprocess.SubprocessError) as error:raise SystemExit("NOT_READY: "+str(error))
