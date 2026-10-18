#!/usr/bin/env python3
"""Build the immutable TPC-C post-TPC-H-a81 qualification successor."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "65155e51a06947ed1794e23619cd25e74b1a31023dcb175beaa3fafc3e11c95c"
SOURCE_SHA = "37067245ea00e6cf8d34ff82d2bf6a35bf64d5050d85016a517a9055a87f6b86"
A81_ID = "042961efb0d52bf29e8410546d4ff3c6122b54b7fcdad11db53b64cfe6b57dbd"
A81_SHA = "24041d1fafd0858939dde8222d2f03625a45556681d7aa4989f34c570cb4e5d3"
SOURCE = ROOT / "artifacts/mysql_tpcc_v16_post_tpch_d3d_mutex_one_click_successor_v1" / SOURCE_ID
OUT = ROOT / "artifacts/mysql_tpcc_v16_post_tpch_a81_mutex_one_click_successor_v1"


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


TEST = r'''from __future__ import annotations
import hashlib, importlib.util, json
from pathlib import Path
import pytest

B = Path(__file__).resolve().parents[1]
S = B / "controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py"
q = importlib.util.spec_from_file_location("tpcc_a81", S)
M = importlib.util.module_from_spec(q)
q.loader.exec_module(M)

def _seal(path: Path, value: dict) -> None:
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    Path(str(path) + ".sha256").write_text(hashlib.sha256(payload).hexdigest() + "  " + path.name + "\n")

def _fixture(tmp_path: Path, wrong_plan_root: bool = False):
    retry9 = tmp_path / M.TPH_ID
    for kind, identity in (("materializer", M.TPH_MATERIALIZER_ID),
                           ("wrapper", M.TPH_WRAPPER_ID),
                           ("support", M.TPH_SUPPORT_ID)):
        (retry9 / "dependencies" / kind / identity).mkdir(parents=True)
    spec = tmp_path / "bootstrap_spec.json"; spec.write_text("{}\n")
    evidence = tmp_path / "bootstrap_evidence.json"; evidence.write_text("{}\n")
    defaults = tmp_path / "mysql.cnf"; defaults.write_text("[client]\n")
    target = tmp_path / M.TARGET_ID; target.mkdir()
    output = tmp_path / "output"
    attempt = "tpch-v2-20260803t111835z-b235retry9281acdfda980"
    dependency_root = tmp_path / "wrong-d3d" if wrong_plan_root else retry9
    if wrong_plan_root:
        for kind, identity in (("materializer", M.TPH_MATERIALIZER_ID),
                               ("wrapper", M.TPH_WRAPPER_ID),
                               ("support", M.TPH_SUPPORT_ID)):
            (dependency_root / "dependencies" / kind / identity).mkdir(parents=True)
    plan = {
        "schema_version": "mysql_tpch_retry9_one_click_plan_v1",
        "status": "SEALED_NOT_EXECUTED", "mutex": str(M.MUTEX),
        "attempt_token": attempt,
        "materializer": str(dependency_root / "dependencies/materializer" / M.TPH_MATERIALIZER_ID),
        "wrapper": str(dependency_root / "dependencies/wrapper" / M.TPH_WRAPPER_ID),
        "support": str(dependency_root / "dependencies/support" / M.TPH_SUPPORT_ID),
        "failed_measurement_attempt": M.FAILED_TPH_ATTEMPT,
        "bootstrap_spec": str(spec), "bootstrap_evidence": str(evidence),
        "mysql_defaults": str(defaults), "target": str(target),
    }
    _seal(output / "controller/retry9.plan.json", plan)
    inner = output / "inner.json"; inner.write_text("{}\n")
    completion = {
        "schema_version": "mysql_tpch_retry9_one_click_completion_v1",
        "status": "PASS_FRESH_B235_RETRY9_COMPLETED_ZERO_CREDIT",
        "attempt_token": attempt,
        "tpcc_fourflow_mutex_held_for_full_window": True,
        "package_closure_unchanged_after_execute": True,
        "target_closure_unchanged_after_execute": True,
        "accepted_round_authorized": False, "accepted_rounds_credit": 0,
        "pids": {role: {"pid": n} for n, role in enumerate(("bootstrap", "controller", "workload"), 2)},
        "inner_completion": str(inner),
        "inner_completion_sha256": hashlib.sha256(inner.read_bytes()).hexdigest(),
    }
    completion_path = output / "completion/retry9.completion.json"
    _seal(completion_path, completion)
    return completion_path, spec.resolve(), evidence.resolve(), defaults.resolve(), retry9.resolve()

def test_content_address_scope_and_exact_a81_binding():
    m = json.loads((B / "bundle_manifest.json").read_text()); unsigned = dict(m); unsigned.pop("bundle_id")
    assert hashlib.sha256((json.dumps(unsigned, sort_keys=True, indent=2) + "\n").encode()).hexdigest() == m["bundle_id"] == B.name
    assert m["formal_scope"] == "mysql_only" and m["cross_engine_comparison_prohibited"] is True
    assert M.TPH_ID == "042961efb0d52bf29e8410546d4ff3c6122b54b7fcdad11db53b64cfe6b57dbd"
    assert M.TPH_SHA == "24041d1fafd0858939dde8222d2f03625a45556681d7aa4989f34c570cb4e5d3"

def test_a81_completion_plan_paths_pass(tmp_path):
    M.verify_retry9_completion(*_fixture(tmp_path))

def test_non_a81_completion_plan_paths_fail_closed(tmp_path):
    with pytest.raises(M.GateError, match="completion/plan/input binding"):
        M.verify_retry9_completion(*_fixture(tmp_path, True))

def test_resource_mutex_and_pg_prohibition_preserved():
    assert (M.TPH_DB_CPUS, M.TPH_CLIENT_CPUS, M.TPCC_DB_CPUS, M.TPCC_CLIENT_CPUS) == ("18-20,54-56", "0-7,36-43", "21-24,57-60", "8-15,44-51")
    rows = [M._cpus(x) for x in (M.TPH_DB_CPUS, M.TPH_CLIENT_CPUS, M.TPCC_DB_CPUS, M.TPCC_CLIENT_CPUS)]
    assert all(not rows[a] & rows[b] for a in range(4) for b in range(a + 1, 4))
    text = S.read_text()
    assert '"/dev/nvme3n1"' in text and '"259:1"' in text and '"259:2"' in text
    assert "65106" in text and "distinct_docker_sockets" in text and "postgresql_mutation_prohibited" in (B / "bundle_manifest.json").read_text()
'''


def main() -> None:
    if sha256(SOURCE / "bundle_manifest.json") != SOURCE_SHA:
        raise SystemExit("source manifest differs")
    source_manifest = json.loads((SOURCE / "bundle_manifest.json").read_text())
    if source_manifest.get("bundle_id") != SOURCE_ID:
        raise SystemExit("source identity differs")
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".build-a81-", dir=OUT))
    try:
        controller_rel = "controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py"
        controller = (SOURCE / controller_rel).read_text()
        controller = controller.replace(
            'TPH_ID = "d3d0bb8048afa3c134458e56da91ae879d5a93fd5fbec1e4d4cd1bd1e13dec22"',
            f'TPH_ID = "{A81_ID}"').replace(
            'TPH_SHA = "371ce278d69f782ef48787b971541fcec64ec8761174685fc38ce293af116939"',
            f'TPH_SHA = "{A81_SHA}"')
        write(stage / controller_rel, controller.encode())

        template = json.loads((SOURCE / "templates/node04_shortest_launch_chain.json").read_text())
        template["argv"][2] = "<this_a81_successor_bundle>/controller/mysql_tpcc_v16_post_tpch_retry9_mutex_one_click_successor_v1.py"
        template["argv"][4] = "<node04_tpch_control_bundle_root>/" + A81_ID
        template["argv"][6] = "<a81_output_root>/completion/retry9.completion.json"
        template["argv"][10] = "<a81_output_root>/bootstrap/bootstrap_spec.json"
        template["argv"][12] = "<a81_attempt_root>/bootstrap_evidence.json"
        template["argv"][14] = "<a81_attempt_root>/credentials/mysql.cnf"
        template["prerequisites_in_order"] = [
            "publish_this_successor_and_a81_to_node04_content_addressed_bundles",
            "wait_for_the_single_fresh_a81_attempt_to_PASS_FRESH_B235_RETRY9_COMPLETED_ZERO_CREDIT",
            "use_spec_evidence_defaults_completion_and_plan_from_that_same_a81_attempt",
            "confirm_both_dedicated_docker_sockets_exist_and_tpcc_adopted_service_is_sealed",
        ]
        template["schema_version"] = "mysql_tpcc_v16_post_tpch_a81_shortest_launch_chain_v1"
        template["status"] = "SEALED_TEMPLATE_WAITING_FOR_A81_PASS"
        write(stage / "templates/node04_shortest_launch_chain.json", canonical(template))
        write(stage / "tests/test_mysql_tpcc_v16_post_tpch_a81_mutex_successor_v1.py", TEST.encode())
        write(stage / "builder/build_mysql_tpcc_v16_post_tpch_a81_mutex_successor_v1.py", Path(__file__).read_bytes())

        files = {}
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                files[path.relative_to(stage).as_posix()] = sha256(path)
        manifest = dict(source_manifest)
        manifest.pop("bundle_id", None)
        manifest.update({
            "schema_version": "mysql_tpcc_v16_post_tpch_a81_mutex_one_click_successor_bundle_v1",
            "status": "SEALED_WAIT_FOR_A81_SUCCESS_THEN_ISOLATED_TPCC_LOCAL_NOT_EXECUTED",
            "predecessor_bundle_id": SOURCE_ID,
            "predecessor_manifest_sha256": SOURCE_SHA,
            "tpch_retry9_bundle_id": A81_ID,
            "tpch_retry9_manifest_sha256": A81_SHA,
            "tpch_next_successful_attempt_dynamic": False,
            "tpch_a81_attempt_token": "tpch-v2-20260803t111835z-b235retry9281acdfda980",
            "tpch_a81_completion_plan_path_binding_required": True,
            "local_only": True,
            "remote_actions_during_build": 0,
            "files": files,
        })
        identity = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = identity
        payload = canonical(manifest)
        write(stage / "bundle_manifest.json", payload)
        write(stage / "bundle_manifest.json.sha256",
              (hashlib.sha256(payload).hexdigest() + "  bundle_manifest.json\n").encode())
        final = OUT / identity
        if final.exists():
            if sha256(final / "bundle_manifest.json") != hashlib.sha256(payload).hexdigest():
                raise SystemExit("existing final differs")
            shutil.rmtree(stage)
        else:
            for path in sorted(stage.rglob("*"), reverse=True):
                os.chmod(path, 0o444 if path.is_file() else 0o555)
            os.chmod(stage, 0o555)
            os.rename(stage, final)
        print(identity)
        print(sha256(final / "bundle_manifest.json"))
        print(final)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


if __name__ == "__main__":
    main()
