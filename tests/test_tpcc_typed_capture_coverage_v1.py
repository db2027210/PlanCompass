import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "capture/mysql_tpcc_typed_capture_entry_v1.py"


def load_entry():
    spec = importlib.util.spec_from_file_location("tpcc_capture_entry", ENTRY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_trace_binding_coverage_is_exact_and_ignores_partial_record(tmp_path):
    module = load_entry()
    manifest = tmp_path / "manifest.json"
    trace = tmp_path / "trace.jsonl"
    statements = [{"binding_id": f"b{i:02d}"} for i in range(34)]
    manifest.write_text(json.dumps({"statements": statements}), encoding="utf-8")
    complete_records = [
        json.dumps(
            {
                "record_type": "statement",
                "binding_id": row["binding_id"],
            }
        )
        for row in statements[:-1]
    ]
    trace.write_text(
        "\n".join(complete_records)
        + "\n"
        + json.dumps(
            {"record_type": "statement", "binding_id": statements[-1]["binding_id"]}
        ),
        encoding="utf-8",
    )

    incomplete = module.trace_binding_coverage(trace, manifest)
    assert incomplete["observed_binding_count"] == 33
    assert incomplete["complete"] is False
    assert incomplete["missing_binding_ids"] == ["b33"]

    with trace.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    complete = module.trace_binding_coverage(trace, manifest)
    assert complete["observed_binding_count"] == 34
    assert complete["complete"] is True
    assert complete["missing_binding_ids"] == []


def test_capture_uses_the_collectors_exact_output_dir_cli():
    source = ENTRY.read_text(encoding="utf-8")
    collector_call = source[source.index('self.bundle / "collector/tpcc_typed_collector.py"') :]
    collector_call = collector_call[: collector_call.index("completed.returncode")]
    assert '"--output-dir",' in collector_call
    assert '"--output",' not in collector_call


def test_capture_result_binds_the_immutable_runtime_and_collector_identity():
    source = ENTRY.read_text(encoding="utf-8")
    for field in (
        '"capture_token": self.capture_token',
        '"immutable_runtime_bundle_id": self.bundle.name',
        '"immutable_runtime_bundle_manifest_sha256": sha256(',
        '"bundle_bound_capture_config_sha256": sha256(',
        '"bundle_bound_collector_sha256": sha256(',
        '"typed_binding_manifest_sha256": sha256(manifest_path)',
        '"trace_sha256_sidecar_path": str(',
        '"collector_l2_sha256": sha256(collector_l2_path)',
        '"collector_l3_sha256": sha256(collector_l3_path)',
        '"coverage_sha256": sha256(coverage_path)',
        '"effective_resource_readback_sha256": sha256(',
        '"performance_schema_readback_sha256": sha256(',
        '"correctness_evidence": dict(correctness)',
        '"mysql_tpcc_typed_capture_completion_authority_v1"',
        '"typed_capture_result_sha256": sha256(',
        '"runtime_process_interference_gate_sha256": sha256(',
    ):
        assert field in source


def test_capture_fails_closed_on_exact_collector_authority_shape():
    source = ENTRY.read_text(encoding="utf-8")
    for gate in (
        'validation.get("binding_count") != 34',
        'validation.get("observed_binding_count") != 34',
        'validation.get("transaction_types") != expected_transaction_types',
        'set(l3_vectors) != set(expected_transaction_types)',
        'validation.get("qps_included") is not False',
        'validation.get("trace_sha256") != trace_sha256',
        'trace_header.get("capture_token") != self.capture_token',
        '"fresh TPC-C typed capture token/output authority already exists"',
        "not thread.is_alive()",
        '"TPC-C typed completion gates are absent after cleanup"',
    ):
        assert gate in source
