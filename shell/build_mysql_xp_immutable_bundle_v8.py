#!/usr/bin/env python3
"""Build the fail-closed, content-addressed MySQL XP runtime bundle v8."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(
    "/tmp/mysql_xp_trace_fix_v7_"
    "174d05a7dff2c75af7eb9e8ec9d4c2204d21c38d4a7c512985500213ceea8b8b"
)
OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_v8"
SUPERSEDES_BUNDLE_ID = (
    "0b28b65267703b738f4ba7baebd1c191b04829a3d287e4d97cc472965c47aff6"
)
JAR_NAME = (
    "HyBench-1.0-SNAPSHOT-formal-xp-binding-trace-candidate-"
    "jar-with-dependencies.jar"
)
CONNECTOR = Path(
    "/home/anonymous/HyBench-2024-master/lib/mysql-connector-java-8.0.30.jar"
)
STATEMENTS = (
    ROOT
    / "history/brute_formal_mixed_20260722_v6_remaining_xp_retry2/mysql/"
    "brute_force/A8_r1/round_000_baseline/attempt_01/hybench_runtime/conf/"
    "stmt_mysql_formal_case_normalized.toml"
)
LOG4J = Path("/home/anonymous/HyBench-2024-master/conf/log4j2.properties")
CAPTURE_CONFIG_TEMPLATE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1/"
    "xp_capture_v8_config_20260729T105254P08.json"
)
BINDING_CAPTURE_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_workloads_and_parameters_20260723_v3/"
    "plan_workload_registries/"
    "hybench_xp.production_binding_capture_contract_v1.json"
)
EXPERIENCE_TABLE = (
    ROOT
    / "config/formal/frozen_mysql_operator_physics_experience_tables_20260724_v1/"
    "T.hybench_xp_io.operator_physics_v1.json"
)
HISTORICAL_R0_CALIBRATION = (
    ROOT
    / "config/formal/frozen_mysql_r0_calibration_20260724_v1/"
    "hybench_xp_io.r0_calibration_v1.json"
)
LOCAL_SERVICES = (
    ROOT / "shell/mysql_algorithm_c_local_services_v25.py"
)
CAPTURE_AT_SEALER = ROOT / "shell/seal_mysql_xp_capture_at_coverage.py"
CAPTURE_AT_SEALER_TEST = (
    ROOT / "tests/test_mysql_xp_capture_at_coverage_seal.py"
)
FORMAL_R0_ENTRY = ROOT / "shell/mysql_xp_formal_r0_entry_v1.py"
LAUNCH_CONTRACT = ROOT / "shell/mysql_xp_launch_contract_v2.py"
OUTER_AUTHORIZER = ROOT / "shell/seal_mysql_xp_outer_authorization.py"
ALGORITHM_SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1/"
    "source-v24-6e3baf1d78a00ce039491cb44695ad3d8578f8a1950dc4a07bfe0a55d79abd50"
)
FAIL_CLOSED_FORMAL_CONFIG = (
    ALGORITHM_SOURCE / "configs/hybench_xp_io.algorithm_c.optimizer.json"
)
ENVELOPE = {
    "cpu_cores": 5.3,
    "cpuset": "184-199,440-455",
    "mems": "1",
    "read_bps": 29360128,
    "write_bps": 524288,
}
PROFILE = {
    "buffer_pool_bytes": 4294967296,
    "cpu_cores": ENVELOPE["cpu_cores"],
    "cpuset": ENVELOPE["cpuset"],
    "memory_bytes": 8589934592,
    "mems": ENVELOPE["mems"],
    "pids": 1024,
    "read_bps": ENVELOPE["read_bps"],
    "write_bps": ENVELOPE["write_bps"],
    "formal_scope": "mysql_only",
    "cross_engine_comparison_prohibited": True,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise RuntimeError(f"required XP closure input is absent: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise RuntimeError(f"required XP closure tree is absent: {source}")
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
            continue
        if path.name in {".DS_Store"}:
            continue
        copy_file(path, destination / path.relative_to(source))


def patch_formal_campaign_runtime_baseline(path: Path) -> None:
    """Bind XP estimator resource scale to the verified in-band fresh R0.

    The v24 frozen source already contains the required raw-all-subset,
    three-level-fingerprint, and fixed-Top-5 contracts.  Apply only this
    narrowly-scoped authority correction instead of replacing that source with
    a live project file whose surrounding algorithm contract may differ.
    """
    source = path.read_text(encoding="utf-8")
    if "def _bind_runtime_estimator_baseline(" in source:
        raise RuntimeError("unexpected pre-patched XP formal campaign source")
    import_anchor = "import argparse\n"
    if source.count(import_anchor) != 1:
        raise RuntimeError("XP campaign argparse import anchor is not unique")
    source = source.replace(
        import_anchor,
        "import argparse\nimport copy\n",
        1,
    )
    hashlib_anchor = "import hashlib\n"
    if source.count(hashlib_anchor) != 1:
        raise RuntimeError("XP campaign hashlib import anchor is not unique")
    source = source.replace(
        hashlib_anchor,
        "import hashlib\nimport math\n",
        1,
    )
    json_anchor = "import json\n"
    if source.count(json_anchor) != 1:
        raise RuntimeError("XP campaign json import anchor is not unique")
    source = source.replace(json_anchor, "import json\nimport os\n", 1)
    helper_anchor = "\ndef _production_r0_identity(\n"
    if source.count(helper_anchor) != 1:
        raise RuntimeError("XP campaign R0 identity anchor is not unique")
    helper = '''

def _bind_runtime_estimator_baseline(
    config: Mapping[str, Any],
    baseline_validation: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind plan/ratio prediction scale to this campaign's verified fresh R0."""
    bound = copy.deepcopy(dict(config))
    estimator = bound.get("estimator")
    if not isinstance(estimator, Mapping):
        return bound
    estimator_type = str(estimator.get("type") or "")
    if estimator_type not in {"plan_physics", "ratio_scale"}:
        return bound
    utilization = baseline_validation.get("utilization_pct")
    if not isinstance(utilization, Mapping):
        raise FormalSGDNotReady(
            "NOT_READY: fresh R0 lacks estimator utilization calibration"
        )
    try:
        runtime_baseline = {
            resource: float(utilization[resource])
            for resource in ("cpu", "memory", "io")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise FormalSGDNotReady(
            "NOT_READY: fresh R0 estimator calibration is incomplete"
        ) from exc
    if any(
        not math.isfinite(value) or value < 0.0
        for value in runtime_baseline.values()
    ):
        raise FormalSGDNotReady(
            "NOT_READY: fresh R0 estimator calibration is non-finite"
        )
    bound_estimator = copy.deepcopy(dict(estimator))
    bound_estimator["baseline_real"] = runtime_baseline
    bound_estimator["baseline_real_authority"] = {
        "source": "verified_fresh_in_band_r0",
        "qps_used": False,
        "resources": ["cpu", "memory", "io"],
    }
    bound["estimator"] = bound_estimator
    return bound
'''
    source = source.replace(helper_anchor, helper + helper_anchor, 1)
    launch_anchor = """            driver = build_replay_driver(benchmark, registration, dependencies, journal)
            return run_optimization(
                config,
"""
    if source.count(launch_anchor) != 1:
        raise RuntimeError("XP campaign optimizer launch anchor is not unique")
    source = source.replace(
        launch_anchor,
        """            driver = build_replay_driver(benchmark, registration, dependencies, journal)
            search_config = _bind_runtime_estimator_baseline(
                config, baseline_validation
            )
            return run_optimization(
                search_config,
""",
        1,
    )
    readiness_anchor = '''    schema_version = document.get("schema_version")
    try:
'''
    if source.count(readiness_anchor) != 1:
        raise RuntimeError("XP outer readiness anchor is not unique")
    source = source.replace(
        readiness_anchor,
        '''    schema_version = document.get("schema_version")
    if schema_version == "mysql_xp_outer_authorization_v1":
        if (
            document.get("status") != "SEALED_OUTER_AUTHORIZED"
            or document.get("launch_authorized") is not True
            or document.get("outer_execution_authorized") is not True
            or document.get("algorithm_c_execution_authorized") is not True
            or document.get("accepted_round_authorized") is not False
            or document.get("formal_r0_passed") is not True
            or document.get("strict_registries_authorized") is not True
            or document.get("formal_scope") != "mysql_only"
            or document.get("cross_engine_comparison_prohibited") is not True
            or document.get("bundle_id")
                != os.environ.get("MYSQL_XP_CONTRACT_BUNDLE_ID")
            or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                != os.environ.get(
                    "MYSQL_XP_CONTRACT_OUTER_AUTHORIZATION_SHA256"
                )
            or document.get("config_sha256")
                != os.environ.get("MYSQL_XP_CONTRACT_CONFIG_SHA256")
            or os.environ.get("MYSQL_XP_CONTRACT_MODE") != "formal_outer"
        ):
            raise FormalSGDNotReady(
                "NOT_READY: XP outer authority differs from launch contract"
            )
        return document
    try:
''',
        1,
    )
    identity_anchor = '''def _production_r0_identity(
    config: Mapping[str, Any], *, benchmark: str, readiness_manifest: Path,
    registration: Mapping[str, Any], service_factory_spec: str,
) -> dict[str, Any]:
    return {
        "benchmark": benchmark,
        "config_sha256": _canonical_sha256(config),
        "readiness_manifest_sha256": hashlib.sha256(
            Path(readiness_manifest).resolve().read_bytes()
        ).hexdigest(),
        "registration_sha256": _canonical_sha256(registration),
        "service_factory_spec": str(service_factory_spec),
    }
'''
    if source.count(identity_anchor) != 1:
        raise RuntimeError("XP campaign R0 identity implementation anchor is not unique")
    source = source.replace(
        identity_anchor,
        '''def _production_r0_identity(
    config: Mapping[str, Any], *, benchmark: str, readiness_manifest: Path,
    registration: Mapping[str, Any], service_factory_spec: str,
) -> dict[str, Any]:
    explicit = config.get("formal_r0_identity")
    if explicit is not None:
        if (
            benchmark != "hybench_xp"
            or not isinstance(explicit, Mapping)
            or explicit.get("schema_version")
                != "mysql_xp_formal_r0_identity_v1"
            or explicit.get("formal_scope") != "mysql_only"
            or explicit.get("cross_engine_comparison_prohibited") is not True
            or not isinstance(explicit.get("bundle_id"), str)
            or len(explicit["bundle_id"]) != 64
            or not isinstance(explicit.get("capture_at_seal_sha256"), str)
            or len(explicit["capture_at_seal_sha256"]) != 64
            or not isinstance(explicit.get("capture_trace_sha256"), str)
            or len(explicit["capture_trace_sha256"]) != 64
            or explicit.get("mysql_port") != 65304
        ):
            raise FormalSGDNotReady(
                "NOT_READY: explicit XP formal R0 identity is incomplete"
            )
        return {
            "benchmark": benchmark,
            "formal_r0_identity_sha256": _canonical_sha256(explicit),
            "service_factory_spec": str(service_factory_spec),
        }
    return {
        "benchmark": benchmark,
        "config_sha256": _canonical_sha256(config),
        "readiness_manifest_sha256": hashlib.sha256(
            Path(readiness_manifest).resolve().read_bytes()
        ).hexdigest(),
        "registration_sha256": _canonical_sha256(registration),
        "service_factory_spec": str(service_factory_spec),
    }
''',
        1,
    )
    path.write_text(source, encoding="utf-8")


def command_version(command: list[str]) -> str:
    completed = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        check=True, timeout=30,
    )
    return completed.stdout.strip()


def launch_contract_source() -> str:
    return LAUNCH_CONTRACT.read_text(encoding="utf-8")
    return """#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, socket, sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def local_file(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"regular local file required (symlink rejected): {path}")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise RuntimeError(f"regular local file required: {path}")
    return resolved

def load_object(path: Path) -> dict:
    value = json.loads(local_file(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value

def valid_sidecar(path: Path) -> bool:
    sidecar = path.with_name(path.name + ".sha256")
    return (
        sidecar.is_file()
        and not sidecar.is_symlink()
        and sidecar.read_text(encoding="ascii")
        == f"{sha256(path)}  {path.name}\\n"
    )

def validate_formal_r0_seal(path: Path, manifest: dict) -> dict:
    path = local_file(path)
    if not valid_sidecar(path):
        raise RuntimeError("formal XP AT seal sidecar is absent or differs")
    registry = load_object(path)
    expected_coverage = {
        "AT1": True, "AT2": True, "AT3": True,
        "AT4": True, "AT5": True, "AT6": True,
    }
    if (
        registry.get("schema_version")
            != "mysql_xp_capture_at_coverage_seal_v1"
        or registry.get("status")
            != "SEALED_CAPTURE_AT_COVERAGE_FOR_FORMAL_R0_ONLY"
        or registry.get("bundle_id") != manifest["bundle_id"]
        or registry.get("bundle_manifest_sha256")
            != sha256(BUNDLE / "manifest.json")
        or registry.get("sealed") is not True
        or registry.get("at_coverage") != expected_coverage
        or registry.get("capture_authorized") is not True
        or registry.get("formal_r0_launch_authorized") is not True
        or registry.get("outer_execution_authorized") is not False
        or registry.get("algorithm_c_execution_authorized") is not False
        or registry.get("accepted_round_authorized") is not False
        or registry.get("capture_io_is_not_r0") is not True
        or registry.get("qps_resource_input_prohibited") is not True
        or registry.get("formal_scope") != "mysql_only"
        or registry.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError(
            "formal XP AT seal is not a complete bundle-bound R0-only authority"
        )
    sources = registry.get("sources")
    capture_source = (
        sources.get("capture_spec") if isinstance(sources, dict) else None
    )
    if not isinstance(capture_source, dict):
        raise RuntimeError("formal XP AT seal capture spec authority is absent")
    capture_spec = local_file(Path(str(capture_source.get("path") or "")))
    if (
        capture_source.get("sha256") != sha256(capture_spec)
        or capture_source.get("schema_version")
            != "mysql_xp_fresh_local_capture_spec_v1"
        or not valid_sidecar(capture_spec)
    ):
        raise RuntimeError("formal XP AT seal capture spec hash/schema differs")
    spec = load_object(capture_spec)
    workload = (
        (spec.get("frozen_workload") or {}).get("workload", {})
        if isinstance(spec.get("frozen_workload"), dict) else {}
    )
    envelope = spec.get("frozen_resource_envelope") or {}
    if (
        spec.get("schema_version") != "mysql_xp_fresh_local_capture_spec_v1"
        or spec.get("runtime_bundle_id") != manifest["bundle_id"]
        or spec.get("formal_scope") != "mysql_only"
        or spec.get("cross_engine_comparison_prohibited") is not True
        or spec.get("capture_only") is not True
        or spec.get("outer_execution_authorized") is not False
        or workload.get("capture_schedule")
            != "deterministic_finite_ap13_at6_fixed_seed_binding_capture"
        or workload.get("qps_latency_resource_results") != "prohibited"
        or envelope.get("io_device") != "8:0"
        or envelope.get("io_read_bps") != 1205862400
        or envelope.get("io_write_bps") != 1205862400
    ):
        raise RuntimeError(
            "formal XP AT seal capture spec is not exact capture-only authority"
        )
    return registry

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("capture", "formal"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--sealed-at-registry", type=Path)
    args = parser.parse_args()
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    if socket.gethostname() != "compute00":
        raise RuntimeError("XP bundle is bound to local host compute00")
    if manifest["formal_scope"] != "mysql_only":
        raise RuntimeError("XP bundle scope is not mysql_only")
    if manifest["cross_engine_comparison_prohibited"] is not True:
        raise RuntimeError("cross-engine prohibition is absent")
    profile = json.loads((BUNDLE / manifest["profile"]["path"]).read_text())
    expected = manifest["resource_envelope"]
    actual = {key: profile[key] for key in expected}
    if actual != expected:
        raise RuntimeError(f"XP envelope mismatch: expected={expected} actual={actual}")
    for relative, expected_sha in manifest["payload_files"].items():
        if sha256(BUNDLE / relative) != expected_sha:
            raise RuntimeError(f"bundle payload hash mismatch: {relative}")
    if args.mode == "formal":
        if args.sealed_at_registry is None:
            raise RuntimeError("formal XP launch requires a sealed AT1-AT6 registry")
        validate_formal_r0_seal(args.sealed_at_registry, manifest)
    environment = dict(os.environ)
    environment["MYSQL_XP_RUNTIME_BUNDLE"] = str(BUNDLE)
    environment["MYSQL_FORMAL_SCOPE"] = "mysql_only"
    environment["MYSQL_CROSS_ENGINE_COMPARISON_PROHIBITED"] = "1"
    bundled_python = [
        str(BUNDLE / "python"),
        str(BUNDLE / "orchestration"),
        str(BUNDLE / "driver"),
        str(BUNDLE / "capture"),
    ]
    prior_pythonpath = environment.get("PYTHONPATH")
    if prior_pythonpath:
        bundled_python.append(prior_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(bundled_python)
    os.execve(
        sys.executable,
        [sys.executable, str(args.driver.resolve()), str(args.config.resolve())],
        environment,
    )

if __name__ == "__main__":
    raise SystemExit(main())
"""


def verify_bundle(bundle: Path, expected_id: str | None = None) -> dict[str, Any]:
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if expected_id is not None and manifest.get("bundle_id") != expected_id:
        raise RuntimeError("published XP bundle id differs")
    if manifest.get("resource_envelope") != ENVELOPE:
        raise RuntimeError("published XP bundle envelope differs")
    barrier = manifest.get("capture_prestart_interference_barrier")
    if (
        manifest.get("supersedes_bundle_id") != SUPERSEDES_BUNDLE_ID
        or barrier != {
            "authorization_then_fresh_rescan_then_hybench": True,
            "same_token_required": True,
            "cpu_overlap_allowed": False,
            "shared_sata_device_allowed": False,
            "unknown_relevant_device_allowed": False,
            "exact_observer_identity_required": True,
            "observer_data_plane_descendants_allowed": False,
            "observer_target_cpu_or_device_hit_allowed": False,
            "observer_inventory_complete": True,
            "synchronous_runtime_first_sample": True,
            "failure_cleanup_before_hybench": True,
        }
    ):
        raise RuntimeError("published XP pre-start interference barrier differs")
    sealed_tests = manifest.get("sealed_test_contract")
    if (
        not isinstance(sealed_tests, dict)
        or sealed_tests.get("arbitrary_cwd") is not True
        or sealed_tests.get("bundle_payload_imports_only") is not True
        or sealed_tests.get("workspace_or_legacy_tmp_imports_allowed") is not False
        or sealed_tests.get("runner")
        != "tests/run_sealed_bundle_tests.py"
    ):
        raise RuntimeError("published XP sealed test contract differs")
    for relative, expected in manifest.get("payload_files", {}).items():
        actual = sha256(bundle / relative)
        if actual != expected:
            raise RuntimeError(f"published XP payload mismatch: {relative}")
    return manifest


def main() -> int:
    # Publication must finish with one same-filesystem atomic rename.  Staging
    # in /tmp is not safe when the artifact root is on another mount.
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(
        tempfile.mkdtemp(prefix=".mysql_xp_v8.build.", dir=OUTPUT_ROOT)
    )
    staging = staging_parent / "payload"
    staging.mkdir()
    try:
        copy_file(SOURCE / f"target/{JAR_NAME}", staging / JAR_NAME)
        copy_tree(
            SOURCE / "target/formal-wrapper-classes",
            staging / "formal-wrapper-classes",
        )
        copy_file(CONNECTOR, staging / "runtime/mysql-connector-java-8.0.30.jar")
        copy_file(STATEMENTS, staging / "config/stmt_mysql_formal_case_normalized.toml")
        copy_file(LOG4J, staging / "config/log4j2.properties")
        copy_file(
            ALGORITHM_SOURCE / "configs/hybench_xp_io.algorithm_c.optimizer.json",
            staging / "config/hybench_xp_io.algorithm_c.optimizer.template.json",
        )
        copy_file(
            CAPTURE_CONFIG_TEMPLATE,
            staging / "capture/xp_capture_v8_config_template.json",
        )
        copy_file(
            BINDING_CAPTURE_CONTRACT,
            staging
            / "authority/hybench_xp.production_binding_capture_contract_v1.json",
        )
        copy_file(
            EXPERIENCE_TABLE,
            staging / "authority/T.hybench_xp_io.operator_physics_v1.json",
        )
        copy_file(
            HISTORICAL_R0_CALIBRATION,
            staging / "authority/hybench_xp_io.historical_r0_calibration.json",
        )
        copy_file(
            SOURCE / "src/main/resources/parameters.toml",
            staging / "config/parameters.toml",
        )
        (staging / "profile").mkdir()
        (staging / "profile/hybench_xp_io_formal_v8.json").write_bytes(
            canonical(PROFILE)
        )

        copy_tree(SOURCE / "src/main/java", staging / "source/src/main/java")
        copy_tree(SOURCE / "src/test/java", staging / "source/src/test/java")
        copy_file(
            SOURCE
            / "provenance/formal_clients_20260721/com/hybench/"
            "HyBenchFormalMain.java",
            staging
            / "source/provenance/formal_clients_20260721/com/hybench/"
            "HyBenchFormalMain.java",
        )
        copy_tree(
            SOURCE / "local_capture",
            staging / "capture/local_capture",
        )
        copy_tree(
            SOURCE / "offline_registry",
            staging / "capture/offline_registry",
        )
        for source in sorted(ALGORITHM_SOURCE.glob("*.py")):
            copy_file(source, staging / "python" / source.name)
        copy_tree(ALGORITHM_SOURCE / "configs", staging / "python/configs")
        for source in sorted((ALGORITHM_SOURCE / "shell").glob("*.py")):
            copy_file(source, staging / "python/shell" / source.name)
        # source-v24 is the frozen Algorithm C implementation, but its source
        # pack intentionally omitted several imported support modules.  Fill
        # only absent names from the live project; never overwrite a frozen
        # v24 algorithm module with an older root copy.
        for source in sorted(ROOT.glob("*.py")):
            destination = staging / "python" / source.name
            if not destination.exists():
                copy_file(source, destination)
        for source in sorted((ROOT / "shell").glob("*.py")):
            destination = staging / "python/shell" / source.name
            if not destination.exists():
                copy_file(source, destination)
        patch_formal_campaign_runtime_baseline(
            staging / "python/formal_mysql_sgd_campaign.py"
        )
        copy_file(
            ROOT / "shell/debug_mysql_gradient_round_v25.py",
            staging / "python/debug_mysql_gradient_round.py",
        )
        copy_file(SOURCE / "pom.xml", staging / "build/pom.xml")
        copy_file(SOURCE / "src/pom.xml", staging / "build/src-pom.xml")
        copy_file(
            ROOT
            / "preflight/frozen_formal_config_20260727_v10/"
            "measurement_protocol.json",
            staging
            / "python/preflight/frozen_formal_config_20260727_v10/"
            "measurement_protocol.json",
        )
        copy_file(
            FAIL_CLOSED_FORMAL_CONFIG,
            staging
            / "python/config/formal/mysql_sgd_production/"
            "hybench_xp_io.algorithm_c.optimizer.json",
        )
        copy_file(
            SOURCE / "target/dependency-classpath.txt",
            staging / "build/dependency-classpath.txt",
        )
        copy_file(
            SOURCE / "src/test/java/com/hybench/workload/"
            "ClientExceptionalFutureNoDbProbe.java",
            staging / "build/ClientExceptionalFutureNoDbProbe.java",
        )

        closure_files = {
            "python/mysql_process_classification.py":
                ROOT / "shell/mysql_process_classification_v10.py",
            "python/shell/mysql_process_classification_v10.py":
                ROOT / "shell/mysql_process_classification_v10.py",
            "python/shell/mysql_four_scene_shared_snapshot.py":
                ROOT / "shell/mysql_four_scene_shared_snapshot.py",
            "python/shell/mysql_xp_capture_evidence_collector_v9.py":
                ROOT / "shell/mysql_xp_capture_evidence_collector_v9.py",
            "python/shell/mysql_xp_formal_r0_entry_v1.py":
                FORMAL_R0_ENTRY,
            "python/shell/mysql_xp_launch_contract_v2.py":
                LAUNCH_CONTRACT,
            "python/shell/seal_mysql_xp_capture_at_coverage.py":
                CAPTURE_AT_SEALER,
            "python/shell/build_mysql_xp_immutable_bundle_v8.py":
                ROOT / "shell/build_mysql_xp_immutable_bundle_v8.py",
            "capture/mysql_four_scene_shared_snapshot.py":
                ROOT / "shell/mysql_four_scene_shared_snapshot.py",
            "python/shell/prepare_mysql_xp_capture_config_v8.py":
                ROOT / "shell/prepare_mysql_xp_capture_config_v8.py",
            "campaign/manual_mysql_sgd_r0_campaign.py":
                ROOT / "shell/manual_mysql_sgd_r0_campaign.py",
            "campaign/mysql_xp_formal_r0_entry.py":
                FORMAL_R0_ENTRY,
            "shell/manual_mysql_sgd_r0_campaign.py":
                ROOT / "shell/manual_mysql_sgd_r0_campaign.py",
            "orchestration/formal_hybench_continuous_session.py":
                ROOT / "formal_hybench_continuous_session.py",
            "orchestration/formal_mysql_sgd_orchestration.py":
                ROOT / "formal_mysql_sgd_orchestration.py",
            "orchestration/mysql_algorithm_b_production_services.py":
                ROOT / "mysql_algorithm_b_production_services.py",
            "orchestration/mysql_algorithm_c_local_services.py": LOCAL_SERVICES,
            "capture/mysql_xp_local_capture_entry.py":
                ROOT / "shell/mysql_xp_local_capture_entry_v8.py",
            "capture/test_mysql_xp_local_capture_entry.py":
                ROOT / "tests/test_mysql_xp_capture_sequence_v1.py",
            "capture/mysql_xp_capture_preflight.py":
                ROOT / "shell/mysql_xp_capture_preflight_v8.py",
            "capture/mysql_xp_capture_evidence_collector.py":
                ROOT / "shell/mysql_xp_capture_evidence_collector_v9.py",
            "capture/seal_mysql_xp_capture_at_coverage.py":
                CAPTURE_AT_SEALER,
            "capture/test_mysql_xp_capture_at_coverage_seal.py":
                CAPTURE_AT_SEALER_TEST,
            "capture/seal_mysql_xp_outer_authorization.py":
                OUTER_AUTHORIZER,
            "capture/mysql_process_classification.py":
                ROOT / "shell/mysql_process_classification_v10.py",
            "driver/formal_mysql_sgd_campaign.py":
                staging / "python/formal_mysql_sgd_campaign.py",
            "driver/debug_mysql_gradient_round.py":
                ROOT / "shell/debug_mysql_gradient_round_v25.py",
            "driver/mysql_parameter_optimizer.py":
                ALGORITHM_SOURCE / "mysql_parameter_optimizer.py",
            "driver/gradient_descent_optimizer.py":
                ALGORITHM_SOURCE / "gradient_descent_optimizer.py",
            "driver/experience_table_subset_optimizer.py":
                ALGORITHM_SOURCE / "experience_table_subset_optimizer.py",
            "driver/mysql_three_level_fingerprint.py":
                ALGORITHM_SOURCE / "mysql_three_level_fingerprint.py",
            "driver/plan_resource_estimator.py":
                ALGORITHM_SOURCE / "plan_resource_estimator.py",
            "tests/test_formal_runtime_estimator_baseline.py":
                ROOT / "tests/test_formal_runtime_estimator_baseline.py",
            "tests/test_mysql_xp_formal_authority_chain_v1.py":
                ROOT / "tests/test_mysql_xp_formal_authority_chain_v1.py",
            "tests/test_mysql_benchmark_monitor_observer_signature_v1.py":
                ROOT / "tests/test_mysql_benchmark_monitor_observer_signature_v1.py",
            "tests/test_mysql_four_scene_shared_snapshot_observer_v2.py":
                ROOT
                / "tests/test_mysql_four_scene_shared_snapshot_observer_v2.py",
            "tests/test_mysql_xp_capture_evidence_collector_v9.py":
                ROOT
                / "tests/test_mysql_xp_capture_evidence_collector_v9.py",
            "tests/run_sealed_bundle_tests.py":
                ROOT / "shell/run_mysql_xp_sealed_bundle_tests_v1.py",
            "python/tests/__init__.py":
                ROOT / "tests/__init__.py",
            "python/tests/test_mysql_tuner.py":
                ROOT / "tests/test_mysql_tuner.py",
            "python/tests/fake_estimator.py":
                ROOT / "tests/fake_estimator.py",
        }
        for relative, source in closure_files.items():
            copy_file(source, staging / relative)
        copy_tree(
            ROOT / "formal_random_walk_clients_20260721/classes",
            staging / "driver/attribution-classes",
        )
        copy_tree(
            ROOT / "formal_clients_20260721/classes",
            staging / "driver/formal-classes",
        )
        (staging / "launch_contract.py").write_text(
            launch_contract_source(), encoding="utf-8"
        )

        dependency_paths = [
            Path(piece) for piece in
            (SOURCE / "target/dependency-classpath.txt")
            .read_text(encoding="utf-8").strip().split(os.pathsep)
            if piece
        ]
        dependencies = [
            {"path": str(path), "sha256": sha256(path)}
            for path in dependency_paths
        ]
        payload_files = {
            str(path.relative_to(staging)): sha256(path)
            for path in sorted(staging.rglob("*"))
            if path.is_file()
        }
        contract = {
            "schema_version": "mysql_xp_v8_immutable_runtime_v1",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "host_binding": "compute00",
            "ssh_prohibited": True,
            "resource_envelope": ENVELOPE,
            "formal_graceful_quiescence_seconds": 120,
            "active_parameter_count": 7,
            "required_nonempty_subsets": 127,
            "fixed_replay_count": 5,
            "supersedes_bundle_id": SUPERSEDES_BUNDLE_ID,
            "supersession_reason":
                "sealed_tests_bundle_only_import_closure",
            "capture_prestart_interference_barrier": {
                "authorization_then_fresh_rescan_then_hybench": True,
                "same_token_required": True,
                "cpu_overlap_allowed": False,
                "shared_sata_device_allowed": False,
                "unknown_relevant_device_allowed": False,
                "exact_observer_identity_required": True,
                "observer_data_plane_descendants_allowed": False,
                "observer_target_cpu_or_device_hit_allowed": False,
                "observer_inventory_complete": True,
                "synchronous_runtime_first_sample": True,
                "failure_cleanup_before_hybench": True,
            },
            "runtime_estimator_baseline_authority": {
                "source": "verified_fresh_in_band_r0",
                "resources": ["cpu", "memory", "io"],
                "qps_used": False,
                "patch_scope": "formal_campaign_only",
            },
            "sealed_test_contract": {
                "runner": "tests/run_sealed_bundle_tests.py",
                "arbitrary_cwd": True,
                "bundle_payload_imports_only": True,
                "workspace_or_legacy_tmp_imports_allowed": False,
                "manifest_payload_hash_verification_before_pytest": True,
                "module_origin_verification_before_and_after_pytest": True,
            },
            "capture_io_envelope_is_not_r0": True,
            "capture_authorized": True,
            "formal_r0_authorized": False,
            "outer_execution_authorized": False,
            "capture_schedule": {
                "mode": "finite_global_exact_once",
                "ap_sequence": [
                    "AP1", "AP2", "AP3", "AP4", "AP5", "AP6", "AP7",
                    "AP8", "AP9", "AP10", "AP11", "AP12", "AP13",
                ],
                "at_sequence": [
                    "AT1", "AT2", "AT3", "AT4", "AT5", "AT6",
                ],
                "stop_admitting_when_queues_empty": True,
                "formal_replay_schedule_unchanged": True,
            },
            "seal_policy": {
                "worker_future_get_required": True,
                "worker_exception_seal_ineligible": True,
                "xapclient_checked_independently": True,
                "required_at_coverage": [
                    "AT1", "AT2", "AT3", "AT4", "AT5", "AT6"
                ],
            },
            "build_tools": {
                "java": command_version(["java", "-version"]),
                "maven": command_version(["mvn", "-version"]),
            },
            "formal_runtime_closure": {
                "campaign": {
                    "path": "python/formal_mysql_sgd_campaign.py",
                    "sha256": payload_files[
                        "python/formal_mysql_sgd_campaign.py"
                    ],
                },
                "optimizer": {
                    "path": "python/mysql_parameter_optimizer.py",
                    "sha256": payload_files[
                        "python/mysql_parameter_optimizer.py"
                    ],
                },
                "config_template": {
                    "path":
                        "config/hybench_xp_io.algorithm_c.optimizer.template.json",
                    "sha256": payload_files[
                        "config/hybench_xp_io.algorithm_c.optimizer.template.json"
                    ],
                },
                "orchestration": {
                    "path":
                        "orchestration/mysql_algorithm_c_local_services.py",
                    "sha256": payload_files[
                        "orchestration/mysql_algorithm_c_local_services.py"
                    ],
                },
                "profile": {
                    "path": "profile/hybench_xp_io_formal_v8.json",
                    "sha256": payload_files[
                        "profile/hybench_xp_io_formal_v8.json"
                    ],
                },
                "statement": {
                    "path": "config/stmt_mysql_formal_case_normalized.toml",
                    "sha256": payload_files[
                        "config/stmt_mysql_formal_case_normalized.toml"
                    ],
                },
                "connector": {
                    "path": "runtime/mysql-connector-java-8.0.30.jar",
                    "sha256": payload_files[
                        "runtime/mysql-connector-java-8.0.30.jar"
                    ],
                },
                "pom": {
                    "path": "build/pom.xml",
                    "sha256": payload_files["build/pom.xml"],
                },
                "source_pom": {
                    "path": "build/src-pom.xml",
                    "sha256": payload_files["build/src-pom.xml"],
                },
                "dependency_classpath": {
                    "path": "build/dependency-classpath.txt",
                    "sha256": payload_files[
                        "build/dependency-classpath.txt"
                    ],
                },
                "launch_contract": {
                    "path": "launch_contract.py",
                    "sha256": payload_files["launch_contract.py"],
                },
            },
            "dependency_closure": dependencies,
            "profile": {
                "path": "profile/hybench_xp_io_formal_v8.json",
                "sha256": payload_files[
                    "profile/hybench_xp_io_formal_v8.json"
                ],
            },
            "payload_files": payload_files,
        }
        bundle_id = hashlib.sha256(canonical(contract)).hexdigest()
        manifest = dict(contract)
        manifest["bundle_id"] = bundle_id
        manifest["runtime"] = {
            "jar": JAR_NAME,
            "jar_sha256": payload_files[JAR_NAME],
            "wrapper": (
                "formal-wrapper-classes/com/hybench/HyBenchFormalMain.class"
            ),
            "wrapper_sha256": payload_files[
                "formal-wrapper-classes/com/hybench/HyBenchFormalMain.class"
            ],
            "connector": "runtime/mysql-connector-java-8.0.30.jar",
            "connector_sha256": payload_files[
                "runtime/mysql-connector-java-8.0.30.jar"
            ],
            "statements": "config/stmt_mysql_formal_case_normalized.toml",
            "statements_sha256": payload_files[
                "config/stmt_mysql_formal_case_normalized.toml"
            ],
        }
        (staging / "manifest.json").write_bytes(canonical(manifest))

        destination = OUTPUT_ROOT / bundle_id
        if destination.exists():
            verify_bundle(destination, bundle_id)
            print(f"MYSQL_XP_V8_ALREADY_PUBLISHED {destination}")
            return 0
        publish_staging = OUTPUT_ROOT / f".{bundle_id}.{os.getpid()}"
        if publish_staging.exists():
            raise RuntimeError(f"unexpected XP publish staging exists: {publish_staging}")
        os.rename(staging, publish_staging)
        for path in sorted(publish_staging.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(publish_staging, 0o555)
        try:
            os.rename(publish_staging, destination)
        except FileExistsError:
            verify_bundle(destination, bundle_id)
        verify_bundle(destination, bundle_id)
        print(f"MYSQL_XP_V8_PUBLISHED {destination}")
        return 0
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"MYSQL_XP_V8_BUILD_FAILED {exc}", file=sys.stderr)
        raise
