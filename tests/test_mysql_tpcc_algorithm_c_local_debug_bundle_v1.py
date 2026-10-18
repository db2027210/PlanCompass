from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "shell/build_mysql_tpcc_algorithm_c_local_debug_bundle_v1.py"
LAUNCHER = ROOT / "shell/mysql_tpcc_algorithm_c_local_debug_launcher_v1.py"
CAPTURE_ID = (
    "eb1b515a7371fb1c4cae26062dbf5279"
    "aba2da776959eb5c03db186454cc153c"
)


def load_builder():
    spec = importlib.util.spec_from_file_location("tpcc_debug_builder", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_debug_config_is_original_gradient_and_one_outer_attempt(
    tmp_path: Path,
) -> None:
    builder = load_builder()
    source = tmp_path / "source"
    source.mkdir()
    (source / "source_manifest.json").write_text("{}\n", encoding="utf-8")
    config = json.loads(
        builder.config_payload(source, "a" * 64)
    )
    assert config["formal_scope"] == "mysql_only"
    assert config["cross_engine_comparison_prohibited"] is True
    assert config["accepted_round_authorized"] is False
    assert config["optimizer"]["original_gradient_only"] is True
    assert config["formal_outer_protocol"]["max_outer_attempts"] == 1
    assert config["formal_outer_protocol"]["target_accepted_shrinks"] == 10
    assert config["debug_contract"]["parameter_count"] == 4
    assert config["debug_contract"]["all_nonempty_subset_count"] == 15
    assert config["debug_contract"]["fixed_real_replay_top_k"] == 5
    assert config["scoring"]["qps_role"] == "outer_acceptance_only"
    assert config["workload"]["external_driver_allowed"] is False


def test_launcher_requires_selected_capture_and_separate_authorities() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    required = (
        CAPTURE_ID[:32],
        CAPTURE_ID[32:],
        'EXPECTED_CPUSET = "208-215,464-471"',
        'EXPECTED_DEVICE = "/dev/sda"',
        "EXPECTED_PORT = 65206",
        '"preflight", "r0-only", "seal-outer", "outer-debug"',
        "PASS_CAPTURE_ONLY_COMPLETE",
        "R0_COMPLETED_OUTER_BLOCKED",
        "OUTER_DEBUG_AUTHORIZED",
        "all_nonempty_subset_count",
        "fixed_global_top_k",
        "qps_resource_input_prohibited",
        "zero_pressure_verified",
    )
    for marker in required:
        assert marker in source


def test_runtime_config_replaces_both_typed_manifest_markers(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location("tpcc_debug_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "profile.json").write_text("{}\n", encoding="utf-8")
    (bundle / "typed_binding_manifest.json").write_text(
        '{"binding_count":34}\n', encoding="utf-8"
    )
    config = {
        "profile": "__BUNDLE_PROFILE__",
        "output": {"directory": str(tmp_path / "output")},
        "early_stop_source_bundle": {},
        "typed_capture_authority": {},
        "workload_registry": {
            "path": "__BUNDLE_TYPED_MANIFEST__",
            "sha256": "",
        },
        "estimator": {
            "feature_source": {
                "workload_registry_path": "__BUNDLE_TYPED_MANIFEST__",
                "workload_registry_sha256": "",
            }
        },
        "formal_outer_protocol": {},
    }
    (bundle / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "source_manifest.json").write_text("{}\n", encoding="utf-8")
    runtime = launcher.runtime_config(
        bundle,
        {"source_directory": str(source)},
        {"sha256": "b" * 64},
        "TPCC_TEST_TOKEN_1234",
    )
    expected = str((bundle / "typed_binding_manifest.json").resolve())
    assert runtime["workload_registry"]["path"] == expected
    assert (
        runtime["estimator"]["feature_source"]["workload_registry_path"]
        == expected
    )
