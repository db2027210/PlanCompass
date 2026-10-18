#!/usr/bin/env python3
"""Build the immutable TPC-C v12 qualification/fresh-R0 controller bundle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve()
CONTROL_SOURCE = ROOT / "shell/mysql_tpcc_qualification_fresh_r0_v12.py"
LIB_SOURCE = ROOT / "artifacts/mysql_tpcc_inventory_controller_successor_v1/3ca53574e0a37d389015734c758e233384224fd375b5ce74f3285c23a1be0183/controller/mysql_tpcc_v16_independent_parallel_one_click_v1.py"
TYPED_ID = "1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c"
TYPED = ROOT / "artifacts/mysql_tpcc_typed_collector_portable_v2" / TYPED_ID
OUT = ROOT / "artifacts/mysql_tpcc_qualification_fresh_r0_v12"
PINNED = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/runtime/python311-3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89/bin/python3.11"
OUTPUT = "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/measurement/v12-qualification-fresh-r0"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def file_digest(path):
    return digest(Path(path).read_bytes())


def freeze(root):
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o555 if path.is_dir() else 0o444)
    os.chmod(root, 0o555)


TEST = r'''import ast,hashlib,json
from pathlib import Path
B=Path(__file__).resolve().parents[1]
M=B/"bundle_manifest.json";C=B/"control/mysql_tpcc_qualification_fresh_r0_v12.py";L=B/"lib/mysql_tpcc_inventory_controller_v1.py"
def canonical(v):return (json.dumps(v,sort_keys=True,indent=2)+"\n").encode()
def test_content_address_scope_and_zero_credit():
 m=json.loads(M.read_text());u=dict(m);u.pop("bundle_id");assert hashlib.sha256(canonical(u)).hexdigest()==m["bundle_id"]==B.name;assert m["formal_scope"]=="mysql_only" and m["cross_engine_comparison_prohibited"] is True and m["postgresql_mutation_prohibited"] is True and m["accepted_rounds_credit"]==0
def test_full_manifest_closure():
 m=json.loads(M.read_text());assert set(m["files"])
 for rel,want in m["files"].items():p=B/rel;assert p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==want
def test_only_new_service_authority_is_bound():
 s=C.read_text();a=json.loads((B/"authority/recreated_service_binding_v12.json").read_text());assert a["database"]["container_id"] in s and a["client"]["container_id"] in s and str(a["database"]["pid"]) in s and str(a["client"]["pid"]) in s
 for old in ("5cdcc870ef04435bad3b44eb89135fd00b5d4825179887e2a51862712f1e3db6","445c4e1732fe6cf8cb07e8f32159aac5cf0aa10bcd1fd51a9db3e5a725a0a1e6","20831","21060","adopted_service.json"):
  assert old not in s and old not in json.dumps(a)
def test_exact_window_and_order():
 s=C.read_text();ast.parse(s);assert "DURATION = 180" in s and "SAMPLES = 181" in s and 'run_stage(plan, "qualification"' in s and 'run_stage(plan, "fresh_r0"' in s;assert s.index('run_stage(plan, "qualification"')<s.index('run_stage(plan, "fresh_r0"')
def test_r0_is_distinct_and_outer_stays_blocked():
 s=C.read_text();assert '"raw_adapter_is_authority": False' in s and '"capture_result_is_r0": False' in s and '"fresh_r0_result": True' in s and '"formal_outer_authorized": False' in s and '"accepted_rounds_credit": 0' in s
def test_library_runtime_bug_is_fixed():
 s=L.read_text();ast.parse(s);assert "pg=sample.get" in s and "pg=value.get" not in s
def test_typed_34_five_txn_no_qps_closure():
 m=json.loads((B/"typed_collector/bundle_manifest.json").read_text());assert m["bundle_id"]=="1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c" and m["binding_count"]==34 and m["required_transaction_types"]==["Delivery","NewOrder","OrderStatus","Payment","StockLevel"] and m["sensitive_stock_level_normalized_to"]=="StockLevel" and m["qps_resource_input_prohibited"] is True
def test_no_pg_mutation_or_build_side_effect():
 m=json.loads(M.read_text());r=json.loads((B/"runbook/one_click.json").read_text());assert m["remote_actions_performed"]==0 and m["data_plane_actions_performed"]==0 and r["postgresql_mutation_prohibited"] is True and r["build_executes_workload"] is False
'''


def main():
    typed_manifest = json.loads((TYPED / "bundle_manifest.json").read_text())
    if typed_manifest.get("bundle_id") != TYPED_ID or typed_manifest.get("binding_count") != 34:
        raise RuntimeError("portable typed collector differs")
    for relative, want in typed_manifest["files"].items():
        if file_digest(TYPED / relative) != want:
            raise RuntimeError("portable typed collector closure differs: " + relative)

    library = LIB_SOURCE.read_bytes()
    old = b"pg=value.get(\"mysql_only_pg_http_dynamic_noninterference\")"
    new = b"pg=sample.get(\"mysql_only_pg_http_dynamic_noninterference\")"
    if library.count(old) != 1:
        raise RuntimeError("expected exactly one inherited dynamic-validator defect")
    library = library.replace(old, new)

    binding = {
        "schema_version": "mysql_tpcc_recreated_service_binding_v12",
        "status": "SEALED_CURRENT_AUTHORITY_ONLY_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "adopted_service_path": "/mnt/mysql_tpcc_nvme3n1/anonymous/resource_adaptive_mysql/tpcc/runtime_algorithm_c_successor_20260729/tpcc-v16-7e2e13aa-20260801t121500p08/adopted_service.recreated_swappiness0_v3.json",
        "adopted_service_sha256": "b9fcd51325ea80d4284df5cf3c9df81fd022a79df5fa3002d2635fe7a8765001",
        "database": {"container_id": "fc87aaf6a954e0d5131ff695ca808247c906525af9b4fad18c94aa6387c55948", "pid": 100769, "process_start_ticks": 268728753, "process_cgroup_sha256": "0ac085e5b6b435d7610695e5fcb2c5e3b97f2e720b845d38565d6cdfde3bae3a", "memory_swappiness": 0, "total_swap_bytes": 0},
        "client": {"container_id": "51c437f082bec5a7f95c7f0ce9a54bf549d87db7aa41a45bcec69390de6801a4", "pid": 100845, "process_start_ticks": 268728786, "process_cgroup_sha256": "895eb232d02a68f416ae00a73e413ae43d399f8620ba4ff11be045ebee405d8d", "memory_swappiness": 0, "total_swap_bytes": 0},
        "dynamic_window_seconds": 180,
        "exact_sample_count": 181,
        "qualification_before_fresh_r0": True,
        "formal_outer_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
    }
    runbook = {
        "schema_version": "mysql_tpcc_qualification_fresh_r0_one_click_runbook_v12",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "main_agent_execution_required": True,
        "build_executes_workload": False,
        "execution_order": ["qualification_exact_180s_181_samples", "fresh_r0_exact_180s_181_samples"],
        "argv": [PINNED, "-B", "[THIS_PUBLISHED_BUNDLE]/control/mysql_tpcc_qualification_fresh_r0_v12.py", "one-click", "--output-root", OUTPUT],
        "success_state": "qualification and fresh R0 sealed; outer remains blocked pending explicit promotion",
        "accepted_rounds_credit": 0,
    }
    contents = {
        "control/mysql_tpcc_qualification_fresh_r0_v12.py": CONTROL_SOURCE.read_bytes(),
        "lib/mysql_tpcc_inventory_controller_v1.py": library,
        "authority/recreated_service_binding_v12.json": canonical(binding),
        "runbook/one_click.json": canonical(runbook),
        "tests/test_mysql_tpcc_qualification_fresh_r0_v12.py": TEST.encode(),
        "builder/build_mysql_tpcc_qualification_fresh_r0_v12.py": HERE.read_bytes(),
    }
    for path in TYPED.iterdir():
        if path.is_file():
            contents["typed_collector/" + path.name] = path.read_bytes()
    files = {relative: digest(payload) for relative, payload in sorted(contents.items())}
    unsigned = {
        "schema_version": "mysql_tpcc_qualification_fresh_r0_bundle_v12",
        "status": "SEALED_NOT_PUBLISHED_NOT_EXECUTED",
        "immutable": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "new_recreated_service_authority_only": True,
        "dynamic_window_seconds": 180,
        "exact_sample_count": 181,
        "qualification_before_fresh_r0": True,
        "typed_collector_bundle_id": TYPED_ID,
        "inherited_dynamic_validator_fix": "sample_scoped_pg_http_evidence",
        "formal_outer_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "remote_actions_performed": 0,
        "data_plane_actions_performed": 0,
        "files": files,
    }
    bundle_id = digest(canonical(unsigned))
    manifest = dict(unsigned, bundle_id=bundle_id)
    raw = canonical(manifest)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / bundle_id
    if target.exists():
        if (target / "bundle_manifest.json").read_bytes() != raw:
            raise RuntimeError("existing manifest differs")
        for relative, payload in contents.items():
            if (target / relative).read_bytes() != payload:
                raise RuntimeError("existing closure differs: " + relative)
    else:
        stage = Path(tempfile.mkdtemp(prefix=".tpcc-v12-qualification-r0-", dir=OUT))
        try:
            for relative, payload in contents.items():
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            (stage / "bundle_manifest.json").write_bytes(raw)
            (stage / "bundle_manifest.json.sha256").write_text(digest(raw) + "  bundle_manifest.json\n")
            freeze(stage)
            os.rename(stage, target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print(bundle_id)
    print(digest(raw))
    print(target)


if __name__ == "__main__":
    main()
