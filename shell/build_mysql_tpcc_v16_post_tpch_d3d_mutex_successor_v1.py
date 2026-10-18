#!/usr/bin/env python3
"""Build TPC-C mutex successor bound to the next successful d3d Retry9 run."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
OLD_ID = "23d9716ce17818cce06364e2d87a2219be9c7786cf6f3dc1ba9d257c665393dd"
OLD_SHA = "f59b329f658fecdfe9ca5a4139efa64b39109a666a6d13a8b591956d4d56f0f0"
OLD = ROOT / "artifacts/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1" / OLD_ID
TPH_OLD_ID = "1ef07fd3c8b547598d3c6d1419c11383abf08ca7d23d8c85e8820707963e0bf3"
TPH_OLD_SHA = "88e06baaec02c84e3cebb78cd74bf2e06b03de674118766aac23581d4c7963bd"
TPH_ID = "d3d0bb8048afa3c134458e56da91ae879d5a93fd5fbec1e4d4cd1bd1e13dec22"
TPH_SHA = "371ce278d69f782ef48787b971541fcec64ec8761174685fc38ce293af116939"
MAT_ID = "d8f8935db191a9c223d28fb3fa29ed0a5ad75afc596ed1908aa9a9275770b360"
WRAP_ID = "084a7cd468a5c1ddf906f7b0c762874d0afb14521d7faf52feb165ff704845b7"
SUPPORT_ID = "947fe36dd89966bfcf1427a67ecd60e872b450ce656556f6255199e47f353739"
FAILED_ATTEMPT = "tpch-v2-20260803t102014z-b235retry948271876b5f8"
OUT = ROOT / "artifacts/mysql_tpcc_v16_post_tpch_d3d_mutex_one_click_successor_v1"
CONTROLLER = "controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py"
BUILDER = "builder/build_mysql_tpcc_v16_post_tpch_d3d_mutex_successor_v1.py"
TEST = "tests/test_mysql_tpcc_v16_post_tpch_d3d_mutex_successor_v1.py"
INVOCATION = "templates/node04_shortest_launch_chain.json"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode()


def sb(data):
    return hashlib.sha256(data).hexdigest()


def sf(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def fmap(root):
    return {
        path.relative_to(root).as_posix(): sf(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.relative_to(root).as_posix()
        not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def verify_old():
    manifest = json.loads((OLD / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id", None)
    if (OLD.name != OLD_ID or manifest.get("bundle_id") != OLD_ID
            or sf(OLD / "bundle_manifest.json") != OLD_SHA
            or sb(canonical(unsigned)) != OLD_ID
            or fmap(OLD) != manifest.get("files")):
        raise RuntimeError("23d predecessor closure differs")
    return manifest


def once(text, old, new, label):
    if text.count(old) != 1:
        raise RuntimeError(label + " anchor differs")
    return text.replace(old, new)


def patch_controller(text):
    text = once(text, 'TPH_ID = "' + TPH_OLD_ID + '"',
                'TPH_ID = "' + TPH_ID + '"', "TPC-H id")
    text = once(text, 'TPH_SHA = "' + TPH_OLD_SHA + '"',
                'TPH_SHA = "' + TPH_SHA + '"', "TPC-H sha")
    anchor = 'TARGET_ID = "b235d1ca300e5ad02fe77005096fc511bfbdc5d91b9601233a66a10502be5a47"\n'
    constants = anchor + '''TPH_MATERIALIZER_ID = "''' + MAT_ID + '''"
TPH_WRAPPER_ID = "''' + WRAP_ID + '''"
TPH_SUPPORT_ID = "''' + SUPPORT_ID + '''"
FAILED_TPH_ATTEMPT = "''' + FAILED_ATTEMPT + '''"
TPH_DOCKER_SOCKET = Path("/run/ra-docker-mysql-tpch-v1.sock")
TPCC_DOCKER_SOCKET = Path("/run/ra-docker-mysql-tpcc-v1.sock")
TPCC_ADOPTED_SERVICE = Path("/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/adopted_service.json")
TPH_DB_CPUS = "18-20,54-56"
TPH_CLIENT_CPUS = "0-7,36-43"
TPCC_DB_CPUS = "21-24,57-60"
TPCC_CLIENT_CPUS = "8-15,44-51"
'''
    text = once(text, anchor, constants, "constants")
    old_signature = '''def verify_retry9_completion(completion_path: Path, spec: Path,
                             evidence: Path, defaults: Path) -> dict[str, Any]:
'''
    new_signature = '''def verify_retry9_completion(completion_path: Path, spec: Path,
                             evidence: Path, defaults: Path,
                             retry9_bundle: Path) -> dict[str, Any]:
'''
    text = once(text, old_signature, new_signature, "completion signature")
    old_plan = '''            or plan.get("schema_version") != "mysql_tpch_retry9_one_click_plan_v1"
            or plan.get("status") != "SEALED_NOT_EXECUTED"
            or plan.get("mutex") != str(MUTEX)
'''
    new_plan = old_plan + '''            or Path(str(plan.get("materializer"))).resolve(strict=True) != retry9_bundle / "dependencies/materializer" / TPH_MATERIALIZER_ID
            or Path(str(plan.get("wrapper"))).resolve(strict=True) != retry9_bundle / "dependencies/wrapper" / TPH_WRAPPER_ID
            or Path(str(plan.get("support"))).resolve(strict=True) != retry9_bundle / "dependencies/support" / TPH_SUPPORT_ID
            or plan.get("failed_measurement_attempt") != FAILED_TPH_ATTEMPT
            or attempt == FAILED_TPH_ATTEMPT
'''
    text = once(text, old_plan, new_plan, "d3d plan closure")
    text = once(
        text,
        '            or completion.get("tpcc_fourflow_mutex_held_for_full_window") is not True\n',
        '            or completion.get("tpcc_fourflow_mutex_held_for_full_window") is not True\n            or completion.get("package_closure_unchanged_after_execute") is not True\n            or completion.get("target_closure_unchanged_after_execute") is not True\n',
        "success closure",
    )
    insert_before_mutex = '''def _cpus(raw: str) -> set[int]:
    values: set[int] = set()
    for item in raw.split(","):
        bounds = item.split("-", 1)
        start = int(bounds[0])
        end = int(bounds[-1])
        if start > end:
            raise GateError("CPU set differs")
        values.update(range(start, end + 1))
    return values


def verify_independent_tpcc_resources(spec_path: Path, evidence_path: Path,
                                      adopted_path: Path) -> dict[str, Any]:
    spec_path, spec = sealed(spec_path, "TPC-H bootstrap spec isolation")
    evidence_path, evidence = sealed(evidence_path, "TPC-H bootstrap evidence isolation")
    adopted_path, adopted = sealed(adopted_path, "TPC-C adopted service")
    if adopted_path != TPCC_ADOPTED_SERVICE.resolve(strict=True):
        raise GateError("TPC-C adopted-service path differs")
    tpch_db = evidence.get("database") or {}
    tpch_client = evidence.get("client") or {}
    tpch_db_host = (tpch_db.get("inspect") or {}).get("HostConfig") or {}
    tpch_client_host = (tpch_client.get("inspect") or {}).get("HostConfig") or {}
    tpcc_db = adopted.get("database") or {}
    tpcc_client = adopted.get("client") or {}
    identities = evidence.get("same_live_identity") or {}
    ids = [tpch_db.get("container_id"), tpch_client.get("container_id"),
           tpcc_db.get("container_id"), tpcc_client.get("container_id")]
    cpus = [TPH_DB_CPUS, TPH_CLIENT_CPUS, TPCC_DB_CPUS, TPCC_CLIENT_CPUS]
    expanded = [_cpus(value) for value in cpus]
    if (spec.get("attempt_token") == FAILED_TPH_ATTEMPT
            or evidence.get("attempt_token") != spec.get("attempt_token")
            or identities.get("database_container_id") != tpch_db.get("container_id")
            or identities.get("client_container_id") != tpch_client.get("container_id")
            or tpch_db_host.get("CpusetCpus") != TPH_DB_CPUS
            or tpch_client_host.get("CpusetCpus") != TPH_CLIENT_CPUS
            or tpcc_db.get("cpuset_cpus") != TPCC_DB_CPUS
            or tpcc_client.get("cpuset_cpus") != TPCC_CLIENT_CPUS
            or len(ids) != len(set(ids))
            or any(not isinstance(value, str) or len(value) != 64 for value in ids)
            or any(expanded[left] & expanded[right]
                   for left in range(len(expanded))
                   for right in range(left + 1, len(expanded)))
            or (evidence.get("effective_blkio_authority") or {}).get("device_major_minor") != "259:1"
            or tpcc_db.get("device") != "/dev/nvme3n1"
            or tpcc_db.get("device_major_minor") != "259:2"
            or tpcc_db.get("mysql_port") != 65106
            or not str((spec.get("paths") or {}).get("datadir") or "").startswith("/mnt/nvme0n1/")
            or adopted.get("formal_scope") != "mysql_only"
            or adopted.get("cross_engine_comparison_prohibited") is not True
            or adopted.get("formal_launch_authorized") is not False
            or adopted.get("accepted_round_authorized") is not False):
        raise GateError("TPC-H/TPC-C independent resource identity differs")
    socket_states = []
    for path in (TPH_DOCKER_SOCKET, TPCC_DOCKER_SOCKET):
        state = path.lstat()
        if not stat.S_ISSOCK(state.st_mode):
            raise GateError("dedicated Docker socket differs: " + str(path))
        socket_states.append((state.st_dev, state.st_ino))
    if socket_states[0] == socket_states[1]:
        raise GateError("TPC-H/TPC-C Docker sockets are not independent")
    return {"schema_version": "mysql_tpcc_v16_independent_resource_prelaunch_readback_v1",
            "tpch_attempt": spec.get("attempt_token"),
            "tpch_database_container": ids[0], "tpch_client_container": ids[1],
            "tpcc_database_container": ids[2], "tpcc_client_container": ids[3],
            "tpch_database_cpus": TPH_DB_CPUS, "tpch_client_cpus": TPH_CLIENT_CPUS,
            "tpcc_database_cpus": TPCC_DB_CPUS, "tpcc_client_cpus": TPCC_CLIENT_CPUS,
            "tpch_device_major_minor": "259:1", "tpcc_device_major_minor": "259:2",
            "tpch_mysql_port": 65103, "tpcc_mysql_port": 65106,
            "distinct_container_ids": True, "pairwise_disjoint_cpus": True,
            "distinct_devices": True, "distinct_docker_sockets": True,
            "tpch_client_policy": "read_only_inventory_only"}


'''
    text = once(text, 'def acquire_mutex() -> tuple[int, int]:\n',
                insert_before_mutex + 'def acquire_mutex() -> tuple[int, int]:\n',
                "isolation function")
    old_run = '''    verify_self()
    verify_bundle(args.tpch_retry9_bundle, TPH_ID, TPH_SHA, "Retry9 one-click")
    tpcc = verify_bundle(args.tpcc_oneclick_bundle, TPCC_ID, TPCC_SHA,
                         "TPC-C predecessor one-click")
    spec = regular(args.tpch_bootstrap_spec, "TPC-H bootstrap spec")
    evidence = regular(args.tpch_bootstrap_evidence, "TPC-H bootstrap evidence")
    defaults = regular(args.tpch_defaults_file, "TPC-H defaults")
    verify_retry9_completion(args.tpch_retry9_completion, spec, evidence, defaults)
    mutex_identity = acquire_mutex()
'''
    new_run = '''    verify_self()
    retry9 = verify_bundle(args.tpch_retry9_bundle, TPH_ID, TPH_SHA, "Retry9 one-click")
    tpcc = verify_bundle(args.tpcc_oneclick_bundle, TPCC_ID, TPCC_SHA,
                         "TPC-C predecessor one-click")
    spec = regular(args.tpch_bootstrap_spec, "TPC-H bootstrap spec")
    evidence = regular(args.tpch_bootstrap_evidence, "TPC-H bootstrap evidence")
    defaults = regular(args.tpch_defaults_file, "TPC-H defaults")
    adopted = regular(args.tpcc_adopted_service, "TPC-C adopted service")
    verify_retry9_completion(args.tpch_retry9_completion, spec, evidence, defaults, retry9)
    verify_independent_tpcc_resources(spec, evidence, adopted)
    mutex_identity = acquire_mutex()
'''
    text = once(text, old_run, new_run, "run preflight")
    text = once(
        text,
        '        verify_retry9_completion(args.tpch_retry9_completion, spec, evidence, defaults)\n',
        '        verify_retry9_completion(args.tpch_retry9_completion, spec, evidence, defaults, retry9)\n        verify_independent_tpcc_resources(spec, evidence, adopted)\n',
        "post-mutex revalidation",
    )
    text = once(
        text,
        '    parser.add_argument("--tpch-defaults-file", type=Path, required=True)\n',
        '    parser.add_argument("--tpch-defaults-file", type=Path, required=True)\n    parser.add_argument("--tpcc-adopted-service", type=Path, required=True)\n',
        "adopted argument",
    )
    return text


TEST_SOURCE = '''from __future__ import annotations
import hashlib,importlib.util,json
from pathlib import Path
B=Path(__file__).resolve().parents[1];S=B/"controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py";q=importlib.util.spec_from_file_location("t",S);M=importlib.util.module_from_spec(q);q.loader.exec_module(M)
def test_content_address_scope_and_d3d_binding():
 m=json.loads((B/"bundle_manifest.json").read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256((json.dumps(u,sort_keys=True,indent=2)+"\\n").encode()).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and M.TPH_ID=="d3d0bb8048afa3c134458e56da91ae879d5a93fd5fbec1e4d4cd1bd1e13dec22"
def test_failed_1ef_and_482_cannot_satisfy_success_gate():
 s=S.read_text();assert "1ef07fd3c8b547598d3c6d1419c11383abf08ca7d23d8c85e8820707963e0bf3" not in s;assert M.FAILED_TPH_ATTEMPT=="tpch-v2-20260803t102014z-b235retry948271876b5f8" and 'attempt == FAILED_TPH_ATTEMPT' in s
def test_all_cpu_sets_are_pairwise_disjoint():
 rows=[M._cpus(x) for x in (M.TPH_DB_CPUS,M.TPH_CLIENT_CPUS,M.TPCC_DB_CPUS,M.TPCC_CLIENT_CPUS)];assert all(not rows[a]&rows[b] for a in range(4) for b in range(a+1,4))
def test_independent_client_device_socket_and_port_gates_present():
 s=S.read_text();assert 'len(ids) != len(set(ids))' in s and '"/dev/nvme3n1"' in s and '"259:1"' in s and '"259:2"' in s and '65106' in s and 'distinct_docker_sockets' in s and 'tpch_client_policy": "read_only_inventory_only"' in s
def test_completion_is_bound_to_d3d_embedded_dependencies():
 s=S.read_text();assert 'retry9_bundle / "dependencies/materializer" / TPH_MATERIALIZER_ID' in s and 'retry9_bundle / "dependencies/wrapper" / TPH_WRAPPER_ID' in s and 'package_closure_unchanged_after_execute' in s
'''


def build():
    old_manifest = verify_old()
    content = {}
    patched = patch_controller((OLD / CONTROLLER).read_text()).encode()
    for name in sorted(old_manifest["files"]):
        if name.startswith("builder/") or name.startswith("tests/"):
            continue
        content[name] = patched if name == CONTROLLER else (OLD / name).read_bytes()
    content[BUILDER] = HERE.read_bytes()
    content[TEST] = TEST_SOURCE.encode()
    content[INVOCATION] = canonical({
        "schema_version": "mysql_tpcc_v16_post_tpch_d3d_shortest_launch_chain_v1",
        "status": "SEALED_TEMPLATE_WAITING_FOR_D3D_PASS",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "prerequisites_in_order": [
            "publish_this_successor_and_d3d_to_node04_content_addressed_bundles",
            "execute_one_fresh_d3d_retry9_attempt_to_PASS_FRESH_B235_RETRY9_COMPLETED_ZERO_CREDIT",
            "use_spec_evidence_defaults_and_completion_from_that_same_successful_attempt",
            "confirm_both_dedicated_docker_sockets_exist_and_tpcc_adopted_service_is_sealed",
        ],
        "argv": [
            "/mnt/nvme0n1/anonymous/resource_adaptive_mysql/tpch/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11",
            "-B",
            "<this_successor_bundle>/controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py",
            "--tpch-retry9-bundle", "<node04_bundle_root>/" + TPH_ID,
            "--tpch-retry9-completion", "<d3d_output_root>/completion/retry9.completion.json",
            "--tpcc-oneclick-bundle", "<node04_bundle_root>/025b4b9595755761b4c10bdb3324dc8f7dc2f7edea76bb906e166de4dcbdb6d8",
            "--tpch-bootstrap-spec", "<d3d_output_root>/bootstrap/bootstrap_spec.json",
            "--tpch-bootstrap-evidence", "<d3d_attempt_root>/bootstrap_evidence.json",
            "--tpch-defaults-file", "<d3d_attempt_root>/credentials/mysql.cnf",
            "--tpcc-adopted-service", "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/adopted_service.json",
        ],
        "single_controller_invocation_after_d3d_pass": True,
        "shell_wrapper_required": False,
        "tpcc_workload_authorized_by_template": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    })
    files = {name: sb(data) for name, data in sorted(content.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_v16_post_tpch_d3d_mutex_one_click_successor_bundle_v1",
        "status": "SEALED_WAIT_FOR_NEXT_D3D_SUCCESS_THEN_ISOLATED_TPCC_LOCAL_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "host": "node04",
        "local_only": True,
        "predecessor_bundle_id": OLD_ID,
        "predecessor_manifest_sha256": OLD_SHA,
        "failed_tpch_oneclick_bundle_id": TPH_OLD_ID,
        "failed_tpch_attempt_token": FAILED_ATTEMPT,
        "failed_tpch_attempt_credit": 0,
        "failed_tpch_attempt_rerun_prohibited": True,
        "tpch_retry9_bundle_id": TPH_ID,
        "tpch_retry9_manifest_sha256": TPH_SHA,
        "tpch_next_successful_attempt_dynamic": True,
        "tpch_full_success_completion_required": True,
        "tpch_embedded_dependency_plan_binding_required": True,
        "tpcc_predecessor_bundle_id": "025b4b9595755761b4c10bdb3324dc8f7dc2f7edea76bb906e166de4dcbdb6d8",
        "tpcc_predecessor_manifest_sha256": "de362d1680ae78611487a5bab9109937d5f31ce941085c81d05a8bbcdfe45a04",
        "mutex": "/run/lock/ra-mysql-node04-tpch-tpcc-fourflow.lock",
        "prelaunch_and_post_mutex_resource_readback_required": True,
        "distinct_container_ids_required": True,
        "pairwise_disjoint_cpu_sets_required": True,
        "distinct_devices_required": True,
        "distinct_docker_sockets_required": True,
        "tpch_client_policy": "read_only_inventory_only",
        "tpcc_database_cpuset": "21-24,57-60",
        "tpcc_client_cpuset": "8-15,44-51",
        "tpcc_device": "/dev/nvme3n1",
        "tpcc_mysql_port": 65106,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_during_build": 0,
        "files": files,
    }
    bundle_id = sb(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if not destination.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".tpcc-d3d-mutex-", dir=OUT))
        try:
            for name, data in content.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            raw = canonical(manifest)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(
                sb(raw) + "  bundle_manifest.json\n"
            )
            for path in sorted(stage.rglob("*"), reverse=True):
                os.chmod(path, 0o555 if path.is_dir() else 0o444)
            os.chmod(stage, 0o555)
            os.replace(stage, destination)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    if fmap(destination) != files or sf(destination / "bundle_manifest.json") != sb(canonical(manifest)):
        raise RuntimeError("successor closure differs")
    return destination


if __name__ == "__main__":
    bundle = build()
    print(json.dumps({"bundle": str(bundle), "bundle_id": bundle.name,
                      "manifest_sha256": sf(bundle / "bundle_manifest.json"),
                      "controller_sha256": sf(bundle / CONTROLLER),
                      "remote_actions": 0}, sort_keys=True))
