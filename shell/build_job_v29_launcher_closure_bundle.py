#!/usr/bin/env python3
"""Publish immutable JOB v29 fixing the v28 launcher import closure."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import build_job_v27_local_authority_bundle as base


V28_ID = "59cbc6843414fa88ade1c56951b747e85ccf90154d98003909ebbaacdf3c1ec0"
V28_BUNDLE = ROOT / "artifacts/mysql_job_v28_debug" / V28_ID
V28_MANIFEST = V28_BUNDLE / "bundle_manifest.json"
V28_READINESS = next(V28_BUNDLE.glob("readiness_manifest-*.json"))
V28_METHOD = V28_BUNDLE / "experience_table_sgd_method_manifest.json"
V28_SOURCE = Path(
    json.loads(V28_MANIFEST.read_text(encoding="utf-8"))["source_directory"]
)
SERVICE = "shell.mysql_algorithm_c_local_services_v28:build"
VERIFIER = ROOT / "shell/verify_mysql_job_v29_readiness.py"
TEST = ROOT / "tests/test_mysql_job_v29_launcher.py"
BUILDER = Path(__file__).resolve()
SOURCE_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/readiness_job_v29_launcher_closure_v1"
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v29_debug"
OUTPUT_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "job_v29_original_gradient_fresh_r0_debug_20260729"
)
PROFILE_RELATIVE = (
    "config/formal/mysql_sgd_production/profiles/job_memory.json"
)
LAUNCHER_NAME = "launch_job_v29_debug.py"
READBACK_NAME = "job_v29_mysql_only_resource_readback.json"
EXTRA_SOURCES = {
    "shell/build_job_v29_launcher_closure_bundle.py": BUILDER,
    "shell/verify_mysql_job_v29_readiness.py": VERIFIER,
    "tests/test_mysql_job_v29_launcher.py": TEST,
}


def validate_inputs() -> Mapping[str, Any]:
    required = [
        V28_MANIFEST,
        V28_READINESS,
        V28_METHOD,
        base.STORAGE_MANIFEST,
        BUILDER,
        VERIFIER,
        TEST,
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"v29 input absent or symlinked: {path}")
    manifest = json.loads(V28_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("bundle_id") != V28_ID:
        raise RuntimeError("v28 predecessor identity differs")
    for name, expected in manifest.get("files", {}).items():
        candidate = V28_BUNDLE / name
        if not candidate.is_file() or base.sha(candidate) != expected:
            raise RuntimeError(f"v28 predecessor closure differs: {name}")
    source_manifest_path = V28_SOURCE / "source_manifest.json"
    source_manifest = json.loads(
        source_manifest_path.read_text(encoding="utf-8")
    )
    for relative, expected in source_manifest.get("files", {}).items():
        candidate = V28_SOURCE / relative
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or base.sha(candidate) != expected
        ):
            raise RuntimeError(f"v28 source closure differs: {relative}")
    storage = json.loads(
        base.STORAGE_MANIFEST.read_text(encoding="utf-8")
    )
    expected_storage = {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": base.TEMPLATE,
        "device": base.DEVICE,
        "device_major_minor": base.MAJOR_MINOR,
        "fresh_copy_mode": base.COPY_MODE,
        "overlay": False,
        "reflink": False,
    }
    for key, expected in expected_storage.items():
        if storage.get(key) != expected:
            raise RuntimeError(
                f"storage authority differs for {key}: "
                f"{storage.get(key)!r} != {expected!r}"
            )
    return source_manifest


def publish_source(
    predecessor: Mapping[str, Any],
) -> tuple[Path, dict[str, str], str]:
    files = dict(predecessor["files"])
    for relative, path in EXTRA_SOURCES.items():
        files[relative] = base.sha(path)
    identity = {
        "schema_version": "mysql_job_v29_source_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "predecessor_source_bundle_id": predecessor["bundle_id"],
        "supersedes_bundle_id": V28_ID,
        "original_gradient_only": True,
        "enumerate_all_nonempty_subspaces": True,
        "parameter_count": 4,
        "candidate_count": 15,
        "fixed_top_k_real_replay": 5,
        "three_level_fingerprint_required": True,
        "single_local_storage_authority": True,
        "single_cpu_resource_authority": True,
        "launcher_clean_cwd_import_closed": True,
        "files": dict(sorted(files.items())),
    }
    source_id = base.sha_bytes(base.canonical(identity))
    final = SOURCE_ROOT / f"source-{source_id}"
    expected_manifest = {**identity, "bundle_id": source_id}
    if final.exists():
        if json.loads(
            (final / "source_manifest.json").read_text(encoding="utf-8")
        ) != expected_manifest:
            raise RuntimeError("existing v29 source manifest differs")
        for relative, expected in files.items():
            if base.sha(final / relative) != expected:
                raise RuntimeError(
                    f"existing v29 source differs: {relative}"
                )
        return final, files, source_id

    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = SOURCE_ROOT / f".source-{source_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        for relative in sorted(files):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = EXTRA_SOURCES.get(relative, V28_SOURCE / relative)
            shutil.copyfile(source, destination)
        (stage / "source_manifest.json").write_bytes(
            base.canonical(expected_manifest)
        )
        base.readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final, files, source_id


def execution_resource_authority(source: Path) -> dict[str, Any]:
    profile = source / PROFILE_RELATIVE
    return {
        "schema_version": "mysql_job_execution_resource_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "job_memory",
        "host_id": "compute00",
        "cpuset_cpus": "176-183,432-439",
        "cpuset_mems": "1",
        "cpu_quota_cores": 1.15,
        "memory_bytes": 4294967296,
        "memory_swap_bytes": 4294967296,
        "io_read_bps": 7340032,
        "io_write_bps": 7340032,
        "pids_max": 512,
        "buffer_pool_bytes": 3221225472,
        "storage_device": "/dev/nvme1n1",
        "storage_device_major_minor": "259:5",
        "profile_path": str(profile),
        "profile_sha256": base.sha(profile),
    }


def normalized_envelope(
    resource: Mapping[str, Any],
) -> dict[str, Any]:
    normalized, _effective = base.normalized_envelope()
    for key in (
        "cpuset_cpus",
        "cpuset_mems",
        "cpu_quota_cores",
        "memory_bytes",
        "memory_swap_bytes",
        "io_read_bps",
        "io_write_bps",
        "pids_max",
        "storage_device",
        "storage_device_major_minor",
    ):
        normalized[key] = resource[key]
    return normalized


def config_payload(source: Path) -> bytes:
    config = json.loads((V28_BUNDLE / "config.json").read_text(
        encoding="utf-8"
    ))
    resource = execution_resource_authority(source)
    config.update({
        "exp_id": "mysql_algorithm_c.job_memory.v29_one_round_debug",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "profile": resource["profile_path"],
        "execution_resource_authority": resource,
    })
    config["workload"]["host_control_cpuset"] = resource["cpuset_cpus"]
    config["production"]["service_factory"] = SERVICE
    config["output"]["directory"] = str(OUTPUT_ROOT)
    config["template_provenance"]["source_bundle_id"] = V28_ID
    return base.canonical(config)


def launcher_payload(source: Path) -> bytes:
    verifier = source / "shell/verify_mysql_job_v29_readiness.py"
    debug = source / "shell/debug_mysql_gradient_round_v25.py"
    return f"""#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import runpy
import sys

SOURCE = Path({str(source)!r})
VERIFIER = Path({str(verifier)!r})
DEBUG = Path({str(debug)!r})
EXPECTED_SERVICE = {SERVICE!r}
LAUNCH_LABEL = "JOB v29"
os.chdir("/tmp")
argv = sys.argv[1:]
if "--help" not in argv and "-h" not in argv:
    def required_option(name):
        if argv.count(name) != 1:
            raise RuntimeError(LAUNCH_LABEL + " launch requires exactly one " + name)
        index = argv.index(name)
        if index + 1 >= len(argv):
            raise RuntimeError(LAUNCH_LABEL + " launch option has no value: " + name)
        return argv[index + 1]

    if required_option("--service-factory") != EXPECTED_SERVICE:
        raise RuntimeError(LAUNCH_LABEL + " service factory override is prohibited")
    readiness = Path(required_option("--readiness-manifest"))
    bundle_dir = Path(__file__).resolve().parent
    if (
        readiness.is_symlink()
        or readiness.resolve().parent != bundle_dir
        or not readiness.name.startswith("readiness_manifest-")
        or readiness.suffix != ".json"
    ):
        raise RuntimeError(LAUNCH_LABEL + " readiness must be the sibling immutable manifest")
    if "--r0-authority-readiness-manifest" in argv:
        raise RuntimeError(LAUNCH_LABEL + " requires a fresh in-band R0")
    if "--resume-directory" not in argv:
        config = Path(required_option("--config"))
        if config.is_symlink() or config.resolve() != bundle_dir / "config.json":
            raise RuntimeError(LAUNCH_LABEL + " config must be the sibling immutable config")

# Close imports before loading the verifier.  v28 did this too late, so the
# verifier's transitive import of mysql_algorithm_b_production_services failed.
sys.path[:] = [str(SOURCE)] + [
    item for item in sys.path if item not in ("", str(SOURCE))
]
shell_package = type(sys)("shell")
shell_package.__path__ = [str(SOURCE / "shell")]
sys.modules["shell"] = shell_package
spec = importlib.util.spec_from_file_location(
    "shell.verify_mysql_sgd_v4_readiness", VERIFIER
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load frozen " + LAUNCH_LABEL + " readiness verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
runpy.run_path(str(DEBUG), run_name="__main__")
""".encode("utf-8")


def verify_readiness(path: Path) -> dict[str, Any]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-I", str(VERIFIER), str(path)],
        cwd="/tmp",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "published v29 readiness failed: "
            + completed.stdout
            + completed.stderr
        )
    result = json.loads(completed.stdout)
    if not result.get("passed"):
        raise RuntimeError(f"v29 verifier rejected publication: {result}")
    return result


def verify_existing(
    final: Path, bundle_id: str,
) -> tuple[Path, dict[str, Any]]:
    manifest = json.loads(
        (final / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("bundle_id") != bundle_id:
        raise RuntimeError("existing v29 bundle identity differs")
    for name, expected in manifest.get("files", {}).items():
        candidate = final / name
        if not candidate.is_file() or base.sha(candidate) != expected:
            raise RuntimeError(f"existing v29 bundle differs: {name}")
    readiness = list(final.glob("readiness_manifest-*.json"))
    if len(readiness) != 1:
        raise RuntimeError("existing v29 readiness cardinality differs")
    return readiness[0], verify_readiness(readiness[0])


def main() -> int:
    source_manifest = validate_inputs()
    source, source_files, source_id = publish_source(source_manifest)
    resource = execution_resource_authority(source)
    storage = base.execution_storage_authority()
    envelope = normalized_envelope(resource)
    config = config_payload(source)
    launcher = launcher_payload(source)
    closure = {
        "schema_version": "mysql_job_v29_bundle_closure_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "accepted_round_authorized": False,
        "supersedes_bundle_ids": [V28_ID],
        "single_local_storage_authority": True,
        "single_cpu_resource_authority": True,
        "runtime_interference_gate_revision": 2,
        "pre_session_cpu_device_first_clean_sample": True,
        "workload_start_requires_runtime_gate_ready": True,
        "debug_outer_attempts": 1,
        "original_gradient_only": True,
        "enumerate_all_nonempty_subspaces": True,
        "parameter_count": 4,
        "candidate_count": 15,
        "fixed_top_k_real_replay": 5,
        "three_level_fingerprint_required": True,
        "launcher_clean_cwd_import_closed": True,
        "source_bundle_id": source_id,
        "source_manifest_sha256": base.sha(
            source / "source_manifest.json"
        ),
        "config_sha256": base.sha_bytes(config),
        "launcher_sha256": base.sha_bytes(launcher),
        "storage_manifest_sha256": base.sha(base.STORAGE_MANIFEST),
        "execution_storage_authority": storage,
        "execution_resource_authority": resource,
        "normalized_envelope": envelope,
        "builder_sha256": base.sha(BUILDER),
        "verifier_sha256": base.sha(VERIFIER),
        "launcher_test_sha256": base.sha(TEST),
    }
    bundle_id = base.sha_bytes(base.canonical(closure))
    final = BUNDLE_ROOT / bundle_id
    if final.exists():
        readiness, result = verify_existing(final, bundle_id)
        print(json.dumps({
            "status": "EXISTS_VERIFIED",
            "bundle_id": bundle_id,
            "source_bundle_id": source_id,
            "directory": str(final),
            "readiness_manifest": str(readiness),
            "launcher": str(final / LAUNCHER_NAME),
            "verifier": result,
        }, sort_keys=True))
        return 0

    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = BUNDLE_ROOT / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    readiness_name = ""
    try:
        (stage / "config.json").write_bytes(config)
        (stage / LAUNCHER_NAME).write_bytes(launcher)
        (stage / LAUNCHER_NAME).chmod(0o555)
        final_config = final / "config.json"
        final_launcher = final / LAUNCHER_NAME
        readback = final / READBACK_NAME
        readback_payload = base.canonical({
            "schema_version":
                "mysql_job_v29_scope_resource_readback_v1",
            "scenario": "job_memory",
            "engine": "mysql",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "status": "FROZEN_FRESH_IN_BAND_READBACK_REQUIRED",
            "execution_storage_authority": storage,
            "execution_resource_authority": resource,
            "normalized_envelope": envelope,
            "runtime_readback_required": {
                "cpuset_cpus": resource["cpuset_cpus"],
                "cpuset_mems": resource["cpuset_mems"],
                "cpu_quota_cores": resource["cpu_quota_cores"],
                "memory_bytes": resource["memory_bytes"],
                "memory_swap_bytes": resource["memory_swap_bytes"],
                "io_read_bps": resource["io_read_bps"],
                "io_write_bps": resource["io_write_bps"],
                "pids_max": resource["pids_max"],
                "storage_device_major_minor":
                    resource["storage_device_major_minor"],
                "mismatch_action": "FAIL_CLOSED",
            },
        })
        (stage / READBACK_NAME).write_bytes(readback_payload)

        entries = [
            base.pointer(source / relative, "source")
            for relative in sorted(source_files)
        ]
        entries.extend([
            base.pointer(source / "source_manifest.json", "source_manifest"),
            {
                "path": str(final_config),
                "sha256": base.sha_bytes(config),
                "role": "sole_execution_config",
                "scenario_scope": ["job_memory"],
            },
            {
                "path": str(final_launcher),
                "sha256": base.sha_bytes(launcher),
                "role": "launch_wrapper",
                "scenario_scope": ["job_memory"],
            },
            base.pointer(
                base.STORAGE_MANIFEST,
                "sole_execution_storage_manifest",
            ),
            base.pointer(
                source / PROFILE_RELATIVE,
                "sole_execution_resource_profile",
            ),
            {
                "path": str(readback),
                "sha256": base.sha_bytes(readback_payload),
                "role": "mysql_only_resource_readback",
                "scenario_scope": ["job_memory"],
            },
        ])
        method = json.loads(V28_METHOD.read_text(encoding="utf-8"))
        method.update({
            "schema_version": "mysql_job_v29_method_manifest_v1",
            "registered_sources": entries,
            "formal_launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_authorized": False,
            "execution_authorized_scenarios": ["job_memory"],
            "runtime_binding": {
                "entrypoint": str(final_launcher),
                "source_directory": str(source),
                "config": str(final_config),
                "profile": resource["profile_path"],
                "profile_sha256": resource["profile_sha256"],
                "service_factory": SERVICE,
                "launcher_import_closure_verified": True,
                "accepted_round_authorized": False,
                "debug_outer_attempts": 1,
                "single_local_storage_authority": True,
                "single_cpu_resource_authority": True,
            },
        })
        method_payload = base.canonical(method)
        method_name = "experience_table_sgd_method_manifest.json"
        (stage / method_name).write_bytes(method_payload)

        old_readiness = json.loads(
            V28_READINESS.read_text(encoding="utf-8")
        )
        readiness = {
            **old_readiness,
            "schema_version": "mysql_job_v29_readiness_v1",
            "status": "READY",
            "launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "paired_result_merge_prohibited": True,
            "execution_authorized_scenarios": ["job_memory"],
            "scenarios": {
                "job_memory": {
                    "benchmark": "job",
                    "host_id": "compute00",
                    "execution_authorized": True,
                    "accepted_round_authorized": False,
                    "fresh_in_band_r0_required": True,
                    "historical_r0_role": "readiness_only",
                    "resource_envelope": resource,
                    "storage": storage,
                },
            },
            "execution_authority": {
                "schema_version":
                    "mysql_job_v29_execution_authority_v1",
                "bundle_id": bundle_id,
                "source_bundle_id": source_id,
                "supersedes_bundle_id": V28_ID,
                "scenario": "job_memory",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "accepted_round_authorized": False,
                "storage": storage,
                "resource": resource,
                "runtime_interference_gate_revision": 2,
                "pre_session_cpu_device_first_clean_sample": True,
                "workload_start_requires_runtime_gate_ready": True,
                "fresh_full_physical_copy_required": True,
            },
            "method_bundle": {
                "method_manifest": {
                    "path": str(final / method_name),
                    "sha256": base.sha_bytes(method_payload),
                },
                "config": {
                    "path": str(final_config),
                    "sha256": base.sha_bytes(config),
                },
                "launcher": {
                    "path": str(final_launcher),
                    "sha256": base.sha_bytes(launcher),
                },
            },
            "provenance_only": {
                "authority_role": "provenance_only",
                "execution_authorized": False,
                "may_participate_in_launch": False,
                "source_predecessor_bundle_id": V28_ID,
                "legacy_v28_readiness": {
                    "path": str(V28_READINESS),
                    "sha256": base.sha(V28_READINESS),
                    "known_defect":
                        "launcher loaded verifier before closing top-level "
                        "source imports",
                },
                "legacy_remote_scenarios": (
                    old_readiness.get("provenance_only", {}).get(
                        "legacy_remote_scenarios", {}
                    )
                ),
            },
        }
        readiness_payload = base.canonical(readiness)
        readiness_sha = base.sha_bytes(readiness_payload)
        readiness_name = f"readiness_manifest-{readiness_sha}.json"
        (stage / readiness_name).write_bytes(readiness_payload)
        (stage / "closure.json").write_bytes(base.canonical(closure))
        files = {
            path.name: base.sha(path)
            for path in sorted(stage.iterdir())
            if path.is_file()
        }
        (stage / "bundle_manifest.json").write_bytes(base.canonical({
            **closure,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "output_root": str(OUTPUT_ROOT),
            "files": files,
            "publication": "staged_then_atomic_rename",
            "idempotency": "content_addressed_existing_verified",
        }))
        base.readonly(stage)
        stage.rename(final)
        try:
            result = verify_readiness(final / readiness_name)
        except BaseException:
            failed = BUNDLE_ROOT / f".{bundle_id}.failed.{os.getpid()}"
            final.rename(failed)
            raise
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    print(json.dumps({
        "status": "PUBLISHED_VERIFIED",
        "bundle_id": bundle_id,
        "source_bundle_id": source_id,
        "directory": str(final),
        "config": str(final / "config.json"),
        "readiness_manifest": str(final / readiness_name),
        "launcher": str(final / LAUNCHER_NAME),
        "verifier": result,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
