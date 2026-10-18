from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = ROOT / "shell/build_mysql_xp_tpcc_distributed_successors.py"
SPEC = importlib.util.spec_from_file_location("distributed_builder", BUILDER_PATH)
assert SPEC and SPEC.loader
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)
LAUNCHER_SPEC = importlib.util.spec_from_file_location(
    "distributed_qualification_launcher",
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py",
)
assert LAUNCHER_SPEC and LAUNCHER_SPEC.loader
LAUNCHER = importlib.util.module_from_spec(LAUNCHER_SPEC)
LAUNCHER_SPEC.loader.exec_module(LAUNCHER)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def built() -> dict:
    return BUILDER.build_all()


def test_build_is_content_addressed_transactional_and_idempotent(built: dict) -> None:
    repeated = BUILDER.build_all()
    assert repeated["xp_bundle_id"] == built["xp_bundle_id"]
    assert repeated["tpcc_bundle_id"] == built["tpcc_bundle_id"]
    assert built["remote_actions"] == 0
    for key in ("xp_bundle", "tpcc_bundle"):
        bundle = Path(built[key])
        manifest = BUILDER.verify_successor(bundle)
        assert manifest["bundle_id"] == bundle.name
        assert manifest["publication"] == {
            "content_addressed": True,
            "transactional_same_filesystem_atomic_rename": True,
            "idempotent_existing_verified": True,
            "recursive_write_bits_removed_before_publish": True,
            "bytecode_cache_creation_prohibited_by_immutable_modes": True,
        }
        assert manifest["formal_launch_authorized"] is False
        assert manifest["accepted_round_authorized"] is False


def test_xp_distributed_binding_and_jdk23_deployment_are_exact(built: dict) -> None:
    bundle = Path(built["xp_bundle"])
    runtime = load(bundle / "distributed_runtime_contract.json")
    assert runtime["scenario"] == "hybench_xp_io"
    assert runtime["host"] == {
        "hostname": "node02",
        "address": "192.0.2.4",
    }
    assert runtime["storage"]["device"] == "/dev/nvme0n1p1"
    assert runtime["storage"]["mount"] == "/mnt/mysql_nvme0n1p1"
    assert runtime["storage"]["expected_live_major_minor"] == "259:4"
    envelope = runtime["resource_envelope"]
    assert envelope["cpuset_cpus"] == "21-33,57-69"
    assert envelope["cpuset_mems"] == "1"
    assert envelope["mysql_port"] == 65104
    assert envelope["cpu_quota_cores"] == 5.3
    assert envelope["io_read_bps"] == 29360128
    assert envelope["io_write_bps"] == 524288
    toolchain = runtime["toolchain"]
    assert toolchain["deployment_mode"] == "content_addressed_full_tree_copy"
    assert toolchain["deployment_target"].startswith(
        "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/runtime/"
        "jdk-23.0.2-oracle-x64-"
    )
    assert toolchain["deployment_target"].endswith(
        toolchain["tree_content_sha256"]
    )
    assert toolchain["tree_content_sha256"] == (
        "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
    )
    assert toolchain["java_binary_sha256"] == (
        "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
    )
    assert toolchain["usr_bin_java_or_java8_prohibited"] is True
    assert runtime["runtime_capabilities"]["formal_outer"] is False


def test_tpcc_distributed_binding_typed_contract_and_toolchain_are_exact(
    built: dict,
) -> None:
    bundle = Path(built["tpcc_bundle"])
    runtime = load(bundle / "distributed_runtime_contract.json")
    assert runtime["scenario"] == "tpcc_cpu"
    assert runtime["host"] == {
        "hostname": "node04",
        "address": "192.0.2.7",
    }
    assert runtime["storage"]["device"] == "/dev/nvme3n1"
    assert runtime["storage"]["mount"] == "/mnt/mysql_tpcc_nvme3n1"
    assert runtime["storage"]["expected_live_major_minor"] == "259:2"
    envelope = runtime["resource_envelope"]
    assert envelope["cpuset_cpus"] == "21-24,57-60"
    assert envelope["cpuset_mems"] == "1"
    assert envelope["mysql_port"] == 65106
    toolchain = runtime["toolchain"]
    assert toolchain["deployment_target"] == (
        "/home/anonymous/recost_deploy_20260721_v1/runtime/"
        "jdk-23.0.2-oracle-x64"
    )
    assert toolchain["python_runtime"] == (
        "/home/anonymous/recost_runtime_py311_24_11_1/bin/python3.11"
    )
    assert toolchain["python_tree_content_sha256"] == (
        "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89"
    )
    assert toolchain["python_live_full_tree_hash_readback_required"] is True
    assert (
        bundle / toolchain["python_tree_manifest"]
    ).is_file()
    assert toolchain["live_full_tree_hash_readback_required"] is True
    typed = runtime["typed_runtime_invariants"]
    assert typed["binding_count"] == 34
    assert typed["jdbc_binding_count"] == 34
    assert typed["sensitive_binding_count"] == 1
    assert typed["transactions"] == [
        "Delivery",
        "NewOrder",
        "OrderStatus",
        "Payment",
        "StockLevel",
    ]
    assert typed["sensitive_stock_level_normalizes_to"] == "StockLevel"
    assert typed["qps_resource_input_prohibited"] is True


def test_native_latency_producers_and_adapter_are_sealed(built: dict) -> None:
    xp = Path(built["xp_bundle"])
    tpcc = Path(built["tpcc_bundle"])
    common = (
        "qualification/mysql_distributed_workload_metric_adapter_v1.py"
    )
    assert (xp / common).is_file()
    assert (tpcc / common).is_file()
    assert (
        xp
        / "qualification/native_latency_producer/classes/com/hybench/"
        "HyBenchFormalMain.class"
    ).is_file()
    for relative in (
        "com/oltpbenchmark/FormalControl.class",
        "com/oltpbenchmark/api/Worker.class",
        "com/oltpbenchmark/api/Worker$1.class",
    ):
        assert (
            tpcc
            / "qualification/native_latency_producer/classes"
            / relative
        ).is_file()
    for bundle in (xp, tpcc):
        contract = load(
            bundle / "qualification/qualification_execution_contract.json"
        )
        producer = contract["qualification"]["native_latency_producer"]
        assert producer[
            "classpath_prepend_before_production_jar_required"
        ] is True
        assert producer["estimated_or_synthetic_latency_prohibited"] is True


@pytest.mark.parametrize(
    ("bundle_key", "kind"),
    [
        ("xp_bundle", "hybench_xp"),
        ("tpcc_bundle", "benchbase_tpcc"),
    ],
)
def test_sealed_native_java_recipe_materializes_to_valid_command_spec(
    built: dict,
    bundle_key: str,
    kind: str,
    tmp_path: Path,
) -> None:
    bundle = Path(built[bundle_key])
    template = load(
        bundle / "qualification/qualification_command_template.json"
    )
    recipe = template["native_java_launch_recipe"]
    java = Path("/usr/lib/jvm/jdk-23.0.2-oracle-x64/bin/java")
    config = tmp_path / "runtime-config.json"
    statements = tmp_path / "statements.toml"
    config.write_text("{}\n", encoding="utf-8")
    statements.write_text("[statements]\n", encoding="utf-8")
    replacements = {
        "${JAVA_BINARY}": str(java),
        "${BUNDLE_ROOT}": str(bundle),
        "${CONFIG_ABSOLUTE}": str(config),
        "${STATEMENTS_ABSOLUTE}": str(statements),
    }
    argv = []
    for raw in recipe["materialized_argv"]:
        value = raw
        for old, new in replacements.items():
            value = value.replace(old, new)
        argv.append(value)
    assert "-jar" not in argv
    assert argv[1] == "-cp"
    classpath = [Path(value) for value in argv[2].split(":")]
    assert classpath[0] == (
        bundle / recipe["classpath_first_entry"]
    )
    code_files = {str(java): BUILDER.sha256(java)}
    code_files[str(config)] = BUILDER.sha256(config)
    code_files[str(statements)] = BUILDER.sha256(statements)
    for entry in classpath:
        if entry.is_file():
            code_files[str(entry)] = BUILDER.sha256(entry)
        elif entry.is_dir():
            for path in sorted(entry.rglob("*.class")):
                code_files[str(path)] = BUILDER.sha256(path)
        else:
            pytest.fail(f"sealed classpath entry missing: {entry}")
    attempt = "attempt-" + ("8" * 32)
    command = {
        "schema_version":
            "mysql_algorithm_c_debug_qualification_command_v1",
        "bundle_id": bundle.name,
        "mode": "qualification_solo",
        "debug_only": True,
        "capture_authorized": False,
        "formal_r0_authorized": False,
        "formal_authorized": False,
        "accepted_round_authorized": False,
        "qualification_attempt_id": attempt,
        "workload_metric_stream": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_stream_v1",
            "path": str(tmp_path / "metrics.jsonl"),
        },
        "workload_metric_adapter": {
            "schema_version":
                "mysql_algorithm_c_debug_workload_metric_adapter_v1",
            "kind": kind,
        },
        "workload_process_isolation": template[
            "workload_process_isolation"
        ],
        "client_container": {
            "name": f"{kind}-client-attempt",
            "container_id": "a" * 64,
            "image_id": "sha256:" + ("b" * 64),
            "created": "2026-07-29T00:00:00Z",
            "inspect_config_sha256": "c" * 64,
            "required_read_only_mounts": [
                {"source": str(tmp_path), "destination": str(tmp_path)},
                {"source": str(bundle), "destination": str(bundle)},
                {
                    "source": str(java.parent.parent),
                    "destination": str(java.parent.parent),
                },
            ],
        },
        "measurement_output": {
            "host_tmpfs_directory": str(tmp_path),
            "container_tmpfs_destination": "/measurement",
        },
        "argv": argv,
        "executable_sha256": BUILDER.sha256(java),
        "code_files": code_files,
        "environment": (
            {}
            if kind == "hybench_xp"
            else template["environment"]
        ),
        "cwd": "/measurement",
    }
    command_path = tmp_path / "command.json"
    payload = LAUNCHER.canonical(command)
    command_path.write_bytes(payload)
    command_path.with_name("command.json.sha256").write_text(
        f"{LAUNCHER.sha_bytes(payload)}  command.json\n",
        encoding="ascii",
    )
    validated = LAUNCHER.validate_command_spec(
        command_path,
        bundle.name,
        "qualification_solo",
        load(bundle / "distributed_runtime_contract.json")[
            "client_resource_envelope"
        ],
    )
    assert validated["argv"] == argv


def test_one_portable_tree_schema_is_used_for_jdk_and_python(
    built: dict,
) -> None:
    xp_bundle = Path(built["xp_bundle"])
    tpcc_bundle = Path(built["tpcc_bundle"])
    jdk = load(xp_bundle / "toolchain/jdk-23.0.2-tree-manifest.json")
    python = load(
        tpcc_bundle / "toolchain/python-3.11.11-tree-manifest.json"
    )
    for manifest, expected in (
        (
            jdk,
            "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c",
        ),
        (
            python,
            "3a8073397536ab806e125061395c291b20edd40c1da015eb4ebd87bc3c403c89",
        ),
    ):
        portable = BUILDER.runtime_tree.validate_portable_manifest(manifest)
        assert manifest["schema_version"] == (
            "mysql_algorithm_c_runtime_tree_manifest_v3"
        )
        assert manifest["algorithm"] == (
            "sha256_canonical_relative_tree_entries_v3"
        )
        assert portable["tree_content_sha256"] == expected
        assert "path" not in manifest["content_identity"]
        assert "java_version" not in manifest["content_identity"]
        directories = [
            row for row in manifest["content_identity"]["entries"]
            if row["type"] == "directory"
        ]
        assert len(directories) == manifest["content_identity"][
            "directory_count"
        ]
        assert directories


def test_complete_predecessor_and_external_python_closures_are_embedded(
    built: dict,
) -> None:
    xp_bundle = Path(built["xp_bundle"])
    xp_predecessor = load(
        xp_bundle / "predecessor/manifest.json"
    )
    for relative, digest in xp_predecessor["payload_files"].items():
        assert BUILDER.sha256(xp_bundle / "predecessor" / relative) == digest

    tpcc_bundle = Path(built["tpcc_bundle"])
    tpcc_predecessor = load(
        tpcc_bundle / "predecessor/bundle_manifest.json"
    )
    for relative, digest in tpcc_predecessor["files"].items():
        assert BUILDER.sha256(tpcc_bundle / "predecessor" / relative) == digest
    source_manifest = load(
        tpcc_bundle / "python_source_closure/source_manifest.json"
    )
    assert len(source_manifest["files"]) == 320
    for relative, digest in source_manifest["files"].items():
        assert BUILDER.sha256(
            tpcc_bundle / "python_source_closure" / relative
        ) == digest


def test_qualification_and_concurrency_policy_is_fail_closed(built: dict) -> None:
    for key in ("xp_bundle", "tpcc_bundle"):
        runtime = load(Path(built[key]) / "distributed_runtime_contract.json")
        policy = runtime["qualification_policy"]
        assert policy["main_agent_authorization_required"] is True
        assert policy["allowed_before_bidirectional_gate"] == [
            "debug_only_solo_qualification",
            "debug_only_paired_qualification",
        ]
        assert (
            policy[
                "capture_r0_formal_accounting_prohibited_during_qualification"
            ]
            is True
        )
        assert (
            policy[
                "concurrent_capture_r0_or_debug_requires_180s_bidirectional_pass"
            ]
            is True
        )
        assert policy["concurrent_formal_authorized"] is False
        assert policy["all_required_streams_missing_or_timeout_result"] == "FAIL"
        assert policy["solo_vs_paired_tolerance_comparator_required"] is True
        assert policy["dynamic_gate_schema"] == (
            "mysql_algorithm_c_distributed_dynamic_gate_evidence_v1"
        )
        assert runtime["live_readback"]["missing_or_timeout_result"] == "FAIL"
        contract = load(
            Path(built[key])
            / "qualification/qualification_execution_contract.json"
        )
        qualification = contract["qualification"]
        assert len(qualification["required_streams"]) == 8
        assert qualification["missing_or_timeout_result"] == "FAIL"
        comparator = contract["bidirectional_comparator"]
        assert comparator["mode"] == "--compare-bidirectional"
        assert comparator["four_distinct_attempt_tokens_required"] is True


def test_integrity_verifier_rejects_incomplete_bundle(tmp_path: Path) -> None:
    incomplete = tmp_path / ("0" * 64)
    incomplete.mkdir()
    (incomplete / "bundle_manifest.json").write_text(
        json.dumps({"bundle_id": incomplete.name}), encoding="utf-8"
    )
    with pytest.raises(BUILDER.BuildError):
        BUILDER.verify_successor(incomplete)
