from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shell/mysql_tpcc_v16_same_live_gate_control_v1.py"
SPEC = importlib.util.spec_from_file_location("tpcc_v16_gate", SOURCE)
gate = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(gate)
BASE = ROOT / "artifacts/mysql_tpcc_prefault_successor_v16" / gate.BASE_ID
BINDING = BASE / "authority/collector_payload_1878a2ca8b68e1b3917021499cda396e23f60a23baacd299496c78346e29c86c/tpcc_offline_binding_manifest.json"


def write(path: Path, value: dict):
    path.write_bytes(gate.canonical(value))
    Path(str(path) + ".sha256").write_text(gate.sha(path) + "  " + path.name + "\n")


def service():
    return {"schema_version":"mysql_tpcc_node04_adopted_service_authority_v2","status":"PASS",
        "formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "bundle_id":gate.BASE_ID,"attempt_id":gate.ATTEMPT,"formal_launch_authorized":False,
        "accepted_round_authorized":False,
        "database":{"container_id":"a"*64,"pid":101,"process_start_ticks":1001,"datadir":"/data"},
        "client":{"container_id":"b"*64,"pid":102,"process_start_ticks":1002}}


def ledger():
    db = service()["database"]
    phase = {"status":"PASS","attempt_id":gate.ATTEMPT,"bundle_id":gate.BASE_ID,
        "observed_epoch_ms":100000,"client_target_device_io_zero_verified":True,
        "client_target_device_io_delta":{"rbytes":0,"wbytes":0,"rios":0,"wios":0,"dbytes":0,"dios":0}}
    for key, value in db.items(): phase["db_" + key] = value
    value = {"schema_version":"mysql_tpcc_same_live_container_formal10_handoff_v2",
        "status":"INCOMPLETE","formal_scope":"mysql_only","cross_engine_comparison_prohibited":True,
        "bundle_id":gate.BASE_ID,"attempt_id":gate.ATTEMPT,"accepted_round_authorized":False,
        "container_recreated":False,"datadir_replaced":False,"database_restarted":False,
        "phases":{"qualification":phase}}
    for key, item in db.items(): value["db_" + key] = item
    return value


def sample():
    return {"required_streams":{"database_counters":{"Queries":1}}}


def flow(scenario, mode, bundle_id, container_id, pid, collector="c"*64):
    return {"schema_version":"mysql_algorithm_c_debug_qualification_evidence_v2",
        "status":"PASS_COMPLETE_8_STREAM_DEBUG_QUALIFICATION_NOT_GATE_AUTHORITY",
        "mode":mode,"duration_seconds":180,"minimum_samples":181,"maximum_sample_gap_seconds":3.5,
        "scenario":scenario,"hostname":"node04","bundle_id":bundle_id,"collector_sha256":collector,
        "summary":{},"samples":[sample() for _ in range(181)],"debug_only":True,
        "capture_authorized":False,"formal_r0_authorized":False,"formal_authorized":False,
        "accepted_round_authorized":False,"initial_live_readback":{
            "status":"PASS_LIVE_READBACK_NOT_LAUNCH_AUTHORITY","scenario":scenario,"hostname":"node04",
            "bundle_id":bundle_id,"formal_scope":"mysql_only","container":{"container_id":container_id,"pid":pid},
            "storage":{"device":gate.DEVICE,"major_minor":gate.MAJOR_MINOR},
            "resource_envelope":{"cpuset_cpus":gate.DB_CPUSET,"mysql_port":gate.PORT}}}


def snapshot():
    return {"schema_version":"mysql_tpcc_v16_same_live_readback_v1",
        "status":"PASS_READ_ONLY_LIVE_IDENTITY_NOT_CAPTURE_AUTHORITY","formal_scope":"mysql_only",
        "cross_engine_comparison_prohibited":True,"postgresql_mutation_prohibited":True,"hostname":"node04",
        "collected_at_utc":datetime.now(timezone.utc).isoformat(),
        "bundle_id":gate.BASE_ID,"attempt_id":gate.ATTEMPT,"qualification_observed_epoch_ms":100000,
        "database":{"container_id":"a"*64,"pid":101,"process_start_ticks":1001,"cpuset_cpus":gate.DB_CPUSET,
            "device":gate.DEVICE,"device_major_minor":gate.MAJOR_MINOR,"mysql_port":gate.PORT},
        "client":{"container_id":"b"*64,"pid":102,"process_start_ticks":1002,"cpuset_cpus":gate.CLIENT_CPUSET},
        "accepted_rounds_credit":0}


def test_base_and_34_binding_closure():
    _root, manifest = gate.verify_bundle(BASE)
    assert manifest["bundle_id"] == gate.BASE_ID
    value = gate.verify_binding(BINDING)
    assert value["binding_count"] == 34
    assert value["transaction_aliases"] == {"SensitiveStockLevel":"StockLevel"}


def test_ledger_requires_qualification_only_zero_io():
    gate.verify_ledger(ledger(), service()["database"])
    bad = ledger(); bad["phases"]["qualification"]["client_target_device_io_delta"]["wbytes"] = 1
    with pytest.raises(gate.GateError): gate.verify_ledger(bad, service()["database"])


def test_qps_is_rejected_recursively():
    with pytest.raises(gate.GateError): gate.reject_qps({"resource":{"QPS":10}}, "fixture")


def test_four_flow_dynamic_gate_current_identity_and_freshness(tmp_path):
    values = {
        "A_solo":flow("tpch_memory","qualification_solo","d"*64,"d"*64,201),
        "A_paired":flow("tpch_memory","qualification_paired","d"*64,"d"*64,201),
        "B_solo":flow("tpcc_cpu","qualification_solo",gate.BASE_ID,"a"*64,101),
        "B_paired":flow("tpcc_cpu","qualification_paired",gate.BASE_ID,"a"*64,101),
    }
    paths = {}; checked = {}
    for label, value in values.items():
        path = tmp_path / (label + ".json"); write(path, value); paths[label] = path
        checked[label] = gate.verify_flow(path, label)
    raw = {"schema_version":"mysql_algorithm_c_dynamic_gate_raw_evidence_closure_v1",
        "inputs":{label:{"path":str(paths[label]),"sha256":gate.sha(paths[label]),"evidence":value}
                  for label, value in values.items()}}
    raw_path = tmp_path / "gate.json.raw_evidence.json"; write(raw_path, raw)
    future = datetime.now(timezone.utc) + timedelta(minutes=10)
    dynamic = {"schema_version":"mysql_algorithm_c_distributed_dynamic_gate_evidence_v1",
        "status":"PASS","scenario":"tpcc_cpu","host_id":"node04","formal_scope":"mysql_only",
        "cross_engine_comparison_prohibited":True,"accepted_round_authorized":False,"duration_seconds":180,
        "minimum_samples_per_stream":181,"maximum_sample_gap_seconds":3.5,"database_counter_missing_samples":0,
        "database_counter_timeout_count":0,"all_tolerances_passed":True,"both_directions_same_version_verified":True,
        "expires_at_utc":future.isoformat(),"directions":{"one":{"passed":True,"foreign_device_io_attribution_bytes":0},
        "two":{"passed":True,"foreign_device_io_attribution_bytes":0}},"raw_artifacts":{raw_path.name:gate.sha(raw_path)}}
    dynamic_path = tmp_path / "gate.json"; write(dynamic_path, dynamic)
    gate.verify_dynamic(dynamic_path, raw_path, checked, snapshot())
    dynamic["database_counter_timeout_count"] = 1; write(tmp_path / "bad.json", dynamic)
    with pytest.raises(gate.GateError): gate.verify_dynamic(tmp_path / "bad.json", raw_path, checked, snapshot())


def test_collector_requires_setter_type_position_sql_and_l2_l3():
    manifest = json.loads(BINDING.read_text())
    by_type = {name:[] for name in gate.TX}
    for row in manifest["statements"]:
        name = "StockLevel" if row["transaction_type"] == "SensitiveStockLevel" else row["transaction_type"]
        by_type[name].append({"binding_id":row["binding_id"],"typed_bindings":[
            {"position":position,"setter":"setInt","jdbc_type":"INTEGER"}
            for position in row["parameter_positions"]]})
    statements = [{"transaction_type":name,"statements":rows} for name,rows in by_type.items()]
    value = {"schema_version":"mysql_tpcc_typed_binding_l2_l3_v1","manifest_sha256":gate.BINDING_SHA,
        "correctness_verified":True,"binding_count":34,"observed_binding_count":34,"transaction_types":sorted(gate.TX),
        "qps_included":False,"l2_transactions":statements,"l3_vectors":{name:{} for name in gate.TX}}
    gate.verify_collector(value)
    target = next(row for row in value["l2_transactions"] if row["statements"] and row["statements"][0]["typed_bindings"])
    target["statements"][0]["typed_bindings"][0].pop("setter")
    with pytest.raises(gate.GateError): gate.verify_collector(value)
