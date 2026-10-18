#!/usr/bin/env python3
"""Publish the immutable JOB v27 single-local-storage-authority successor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import build_job_v8_debug_bundle as v8


PREDECESSOR_ID = (
    "cd4420e2a3c9912bff3d4457967dd7c7fafe16d09a8a744b57b4ba225b76a581"
)
INVALID_V27_PREDECESSOR_ID = (
    "1fa1a929e5b29c40d07b33a540debce45f8050881b48a5e53878417a2bee2209"
)
INCOMPLETE_LAUNCH_BINDING_PREDECESSOR_ID = (
    "9533444fd4b41611e55a557e7f077f332d012d3687d8bd17b2b89375aef98d91"
)
PREDECESSOR = ROOT / "artifacts/mysql_job_v8_debug" / PREDECESSOR_ID
PREDECESSOR_MANIFEST = PREDECESSOR / "bundle_manifest.json"
PREDECESSOR_READINESS = next(PREDECESSOR.glob("readiness_manifest-*.json"))
PREDECESSOR_METHOD = PREDECESSOR / "experience_table_sgd_method_manifest.json"
BASE_SOURCE = Path(
    json.loads(PREDECESSOR_MANIFEST.read_text(encoding="utf-8"))[
        "source_directory"
    ]
)
STORAGE_MANIFEST = Path(
    "/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json"
)
TEMPLATE = "/disk1/ra_mysql_isolated_v2/job-base"
DEVICE = "/dev/nvme1n1"
MAJOR_MINOR = "259:5"
PROFILE = "job_local_storage_v2_nvme_fullcopy"
COPY_MODE = "full_physical_copy_no_overlay_no_reflink"
SERVICE = "shell.mysql_algorithm_c_local_services_v27:build"
SOURCE_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/readiness_job_v27_local_authority_v1"
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v27_debug"
OUTPUT_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "job_v27_original_gradient_fresh_r0_debug_20260729"
)
VERIFIER = ROOT / "shell/verify_mysql_job_v27_readiness.py"
VERIFIER_RELATIVE = "shell/verify_mysql_job_v27_readiness.py"
LAUNCH_LABEL = "JOB v27"
SERVICE_SOURCE = ROOT / "shell/mysql_algorithm_c_local_services_v27.py"
AUTHORITY_TEST = ROOT / "tests/test_mysql_job_v27_storage_authority.py"
BUILDER = Path(__file__).resolve()
EXTRA_SOURCES = {
    "shell/mysql_algorithm_c_local_services_v27.py": SERVICE_SOURCE,
    "shell/verify_mysql_job_v27_readiness.py": VERIFIER,
    "tests/test_mysql_job_v27_storage_authority.py": AUTHORITY_TEST,
}


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def pointer(path: Path, role: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha(path),
        "role": role,
        "scenario_scope": ["job_memory"],
    }


def validate_inputs() -> dict[str, Any]:
    required = [
        PREDECESSOR_MANIFEST,
        PREDECESSOR_READINESS,
        PREDECESSOR_METHOD,
        STORAGE_MANIFEST,
        BUILDER,
        *EXTRA_SOURCES.values(),
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"authority input absent or symlinked: {path}")
    predecessor = json.loads(PREDECESSOR_MANIFEST.read_text(encoding="utf-8"))
    if predecessor.get("bundle_id") != PREDECESSOR_ID:
        raise RuntimeError("predecessor identity differs")
    for name, expected in predecessor.get("files", {}).items():
        candidate = PREDECESSOR / name
        if not candidate.is_file() or sha(candidate) != expected:
            raise RuntimeError(f"predecessor closure differs: {name}")
    source_manifest = json.loads(
        (BASE_SOURCE / "source_manifest.json").read_text(encoding="utf-8")
    )
    for relative, expected in source_manifest.get("files", {}).items():
        candidate = BASE_SOURCE / relative
        if (
            not candidate.is_file()
            or candidate.is_symlink()
            or sha(candidate) != expected
        ):
            raise RuntimeError(f"predecessor source differs: {relative}")
    storage = json.loads(STORAGE_MANIFEST.read_text(encoding="utf-8"))
    expected_storage = {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": TEMPLATE,
        "device": DEVICE,
        "device_major_minor": MAJOR_MINOR,
        "filesystem": "ext4",
        "fresh_copy_mode": COPY_MODE,
        "overlay": False,
        "reflink": False,
    }
    for key, expected in expected_storage.items():
        if storage.get(key) != expected:
            raise RuntimeError(
                f"local storage manifest {key} differs: "
                f"{storage.get(key)!r} != {expected!r}"
            )
    return source_manifest


def publish_source(base: Mapping[str, Any]) -> tuple[Path, dict[str, str], str]:
    files = dict(base["files"])
    for relative, source in EXTRA_SOURCES.items():
        files[relative] = sha(source)
    identity = {
        "schema_version": "mysql_job_v27_source_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "predecessor_source_bundle_id": base["bundle_id"],
        "original_gradient_only": True,
        "single_local_storage_authority": True,
        "pre_session_cpu_device_first_clean_sample": True,
        "files": dict(sorted(files.items())),
    }
    source_id = sha_bytes(canonical(identity))
    final = SOURCE_ROOT / f"source-{source_id}"
    expected_manifest = {**identity, "bundle_id": source_id}
    if final.exists():
        if json.loads(
            (final / "source_manifest.json").read_text(encoding="utf-8")
        ) != expected_manifest:
            raise RuntimeError("existing v27 source manifest differs")
        for relative, expected in files.items():
            if sha(final / relative) != expected:
                raise RuntimeError(f"existing v27 source differs: {relative}")
        return final, files, source_id

    SOURCE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = SOURCE_ROOT / f".source-{source_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        for relative in sorted(files):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = EXTRA_SOURCES.get(relative, BASE_SOURCE / relative)
            shutil.copyfile(source, destination)
        (stage / "source_manifest.json").write_bytes(
            canonical(expected_manifest)
        )
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final, files, source_id


def execution_storage_authority() -> dict[str, Any]:
    return {
        "schema_version": "mysql_job_execution_storage_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "job_memory",
        "host_id": "compute00",
        "template_path": TEMPLATE,
        "manifest_path": str(STORAGE_MANIFEST),
        "manifest_sha256": sha(STORAGE_MANIFEST),
        "profile_id": PROFILE,
        "device": DEVICE,
        "device_major_minor": MAJOR_MINOR,
        "filesystem": "ext4",
        "fresh_copy_mode": COPY_MODE,
        "overlay": False,
        "reflink": False,
    }


def config_payload(source: Path) -> bytes:
    config = json.loads(v8.config_payload(source))
    legacy_template = config.pop("data_template", None)
    config.update({
        "exp_id": "mysql_algorithm_c.job_memory.v27_one_round_debug",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "local_storage_profile_id": PROFILE,
        "local_storage_manifest": str(STORAGE_MANIFEST),
        "local_storage_manifest_sha256": sha(STORAGE_MANIFEST),
        "execution_storage_authority": execution_storage_authority(),
        "storage_authority_policy": {
            "schema_version": "mysql_job_single_storage_authority_policy_v1",
            "executable_authority_count": 1,
            "second_template_authority_action": "FAIL_CLOSED",
            "phantom_device_action": "FAIL_CLOSED",
            "runtime_readback_must_match": {
                "device": DEVICE,
                "device_major_minor": MAJOR_MINOR,
                "template_path": TEMPLATE,
                "fresh_copy_mode": COPY_MODE,
            },
        },
        "template_provenance": {
            "authority_role": "provenance_only",
            "execution_authorized": False,
            "may_participate_in_launch": False,
            "source_bundle_id": PREDECESSOR_ID,
            "legacy_remote_template_evidence": legacy_template,
        },
    })
    config["production"].pop("readiness_manifest", None)
    config["production"].update({
        "launch_authorized": True,
        "service_factory": SERVICE,
    })
    config["workload"].update({
        "fresh_copy_mode": COPY_MODE,
        "fresh_full_physical_copy_required": True,
    })
    config["output"]["directory"] = str(OUTPUT_ROOT)
    return canonical(config)


def launcher_payload(source: Path) -> bytes:
    debug = source / "shell/debug_mysql_gradient_round_v25.py"
    frozen_verifier = source / VERIFIER_RELATIVE
    return f"""#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import runpy
import sys

ROOT = Path({str(ROOT)!r})
SOURCE = Path({str(source)!r})
VERIFIER = Path({str(frozen_verifier)!r})
DEBUG = Path({str(debug)!r})
EXPECTED_SERVICE = {SERVICE!r}
LAUNCH_LABEL = {LAUNCH_LABEL!r}
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
        if (
            config.is_symlink()
            or config.resolve() != bundle_dir / "config.json"
        ):
            raise RuntimeError(LAUNCH_LABEL + " config must be the sibling immutable config")
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
sys.path[:] = [str(SOURCE)] + [
    item for item in sys.path if item not in ("", str(ROOT), str(SOURCE))
]
runpy.run_path(str(DEBUG), run_name="__main__")
""".encode("utf-8")


def normalized_envelope() -> tuple[dict[str, Any], dict[str, Any]]:
    readback_path = (
        v8.R0_POINT / "effective_resource_readback_v2.json"
    )
    raw = json.loads(readback_path.read_text(encoding="utf-8"))
    effective = raw["effective"]
    normalized = {
        "cpu_isolation_degree": 16,
        "cpu_quota_cores": effective["cpu_quota_us"] / 100000.0,
        "io_read_bps": effective["io_read_bps"],
        "io_write_bps": effective["io_write_bps"],
        "memory_bytes": effective["memory_bytes"],
        "memory_swap_bytes": effective["memory_bytes"],
        "numa_isolation_degree": 1,
        "pids_max": effective["pids_max"],
        "cpuset_cpus": effective["cpuset_cpus"],
        "cpuset_mems": effective["cpuset_mems"],
        "storage_device": DEVICE,
        "storage_device_major_minor": MAJOR_MINOR,
        "fresh_copy_mode": COPY_MODE,
    }
    return normalized, effective


def verify_readiness(path: Path) -> dict[str, int]:
    spec = importlib.util.spec_from_file_location(
        "job_v27_readiness_verifier", VERIFIER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import JOB v27 verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    document = json.loads(path.read_text(encoding="utf-8"))
    checks = module.verify_document(document, ROOT)
    failed = [item for item in checks if not item.passed]
    if failed:
        detail = [(item.item, item.reason) for item in failed]
        raise RuntimeError(f"published v27 readiness failed: {detail}")
    return {"verifier_passed": len(checks), "verifier_failed": 0}


def verify_existing(final: Path, bundle_id: str) -> tuple[Path, dict[str, int]]:
    manifest_path = final / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("bundle_id") != bundle_id:
        raise RuntimeError("existing v27 bundle identity differs")
    for name, expected in manifest.get("files", {}).items():
        candidate = final / name
        if not candidate.is_file() or sha(candidate) != expected:
            raise RuntimeError(f"existing v27 bundle differs: {name}")
    readiness_paths = list(final.glob("readiness_manifest-*.json"))
    if len(readiness_paths) != 1:
        raise RuntimeError("existing v27 readiness cardinality differs")
    return readiness_paths[0], verify_readiness(readiness_paths[0])


def main() -> int:
    base = validate_inputs()
    source, source_files, source_id = publish_source(base)
    config = config_payload(source)
    launcher = launcher_payload(source)
    normalized, effective = normalized_envelope()
    closure = {
        "schema_version": "mysql_job_v27_bundle_closure_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "accepted_round_authorized": False,
        "supersedes_bundle_ids": [
            PREDECESSOR_ID,
            INVALID_V27_PREDECESSOR_ID,
            INCOMPLETE_LAUNCH_BINDING_PREDECESSOR_ID,
        ],
        "single_local_storage_authority": True,
        "runtime_interference_gate_revision": 2,
        "pre_session_cpu_device_first_clean_sample": True,
        "workload_start_requires_runtime_gate_ready": True,
        "debug_outer_attempts": 1,
        "source_bundle_id": source_id,
        "source_manifest_sha256": sha(source / "source_manifest.json"),
        "config_sha256": sha_bytes(config),
        "launcher_sha256": sha_bytes(launcher),
        "storage_manifest_sha256": sha(STORAGE_MANIFEST),
        "execution_storage_authority": execution_storage_authority(),
        "normalized_envelope": normalized,
        "builder_sha256": sha(BUILDER),
    }
    bundle_id = sha_bytes(canonical(closure))
    final = BUNDLE_ROOT / bundle_id
    if final.exists():
        readiness, result = verify_existing(final, bundle_id)
        print(json.dumps({
            "status": "EXISTS_VERIFIED",
            "bundle_id": bundle_id,
            "source_bundle_id": source_id,
            "directory": str(final),
            "config": str(final / "config.json"),
            "readiness_manifest": str(readiness),
            "launcher": str(final / "launch_job_v27_debug.py"),
            **result,
        }, sort_keys=True))
        return 0

    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = BUNDLE_ROOT / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    readiness_name = ""
    try:
        (stage / "config.json").write_bytes(config)
        (stage / "launch_job_v27_debug.py").write_bytes(launcher)
        (stage / "launch_job_v27_debug.py").chmod(0o555)

        final_config = final / "config.json"
        final_launcher = final / "launch_job_v27_debug.py"
        readback = final / "job_v27_mysql_only_envelope_readback.json"
        readback_payload = canonical({
            "schema_version": "mysql_job_v27_scope_storage_readback_v1",
            "scenario": "job_memory",
            "engine": "mysql",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "cross_engine_envelope_equality_required": False,
            "status": "FROZEN_FROM_AUTHENTIC_MYSQL_READBACK_FRESH_R0_REQUIRED",
            "normalized_envelope": normalized,
            "execution_storage_authority": execution_storage_authority(),
            "source_effective_readback": {
                "path": str(
                    v8.R0_POINT / "effective_resource_readback_v2.json"
                ),
                "sha256": sha(
                    v8.R0_POINT / "effective_resource_readback_v2.json"
                ),
                "authority_role": "envelope_readiness_only",
                "fresh_in_band_r0_required": True,
            },
            "swap_normalization": {
                "raw_memory_swap_bytes": effective["memory_swap_bytes"],
                "normalized_memory_swap_bytes": effective["memory_bytes"],
                "mode": "host_swap_disabled_no_memsw_controller",
                "additional_swap_available": False,
            },
        })
        (stage / readback.name).write_bytes(readback_payload)

        entries = [
            pointer(source / relative, "source")
            for relative in sorted(source_files)
        ]
        entries.extend([
            pointer(source / "source_manifest.json", "source_manifest"),
            {
                "path": str(final_config),
                "sha256": sha_bytes(config),
                "role": "sole_execution_config",
                "scenario_scope": ["job_memory"],
            },
            {
                "path": str(final_launcher),
                "sha256": sha_bytes(launcher),
                "role": "launch_wrapper",
                "scenario_scope": ["job_memory"],
            },
            pointer(STORAGE_MANIFEST, "sole_execution_storage_manifest"),
            {
                "path": str(readback),
                "sha256": sha_bytes(readback_payload),
                "role": "mysql_only_envelope_storage_readback",
                "scenario_scope": ["job_memory"],
            },
        ])

        method = json.loads(PREDECESSOR_METHOD.read_text(encoding="utf-8"))
        method.update({
            "schema_version": "mysql_job_v27_method_manifest_v1",
            "registered_sources": entries,
            "formal_launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_authorized": False,
            "execution_authorized_scenarios": ["job_memory"],
            "runtime_binding": {
                "entrypoint": str(final_launcher),
                "source_directory": str(source),
                "config": str(final_config),
                "profile": str(
                    source
                    / "config/formal/mysql_sgd_production/profiles/"
                    "job_memory.json"
                ),
                "service_factory": SERVICE,
                "accepted_round_authorized": False,
                "debug_outer_attempts": 1,
                "single_local_storage_authority": True,
            },
        })
        method_payload = canonical(method)
        method_name = "experience_table_sgd_method_manifest.json"
        (stage / method_name).write_bytes(method_payload)

        old_readiness = json.loads(
            PREDECESSOR_READINESS.read_text(encoding="utf-8")
        )
        old_scenarios = old_readiness.get("scenarios") or {}
        legacy_remote = {
            key: {
                "authority_role": "provenance_only",
                "execution_authorized": False,
                "may_participate_in_launch": False,
                "source": value,
            }
            for key, value in old_scenarios.items()
            if key != "job_memory"
        }
        readiness = {
            "schema_version": "mysql_job_v27_readiness_v1",
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
                    "resource_envelope": normalized,
                    "storage": execution_storage_authority(),
                },
            },
            "execution_authority": {
                "schema_version": "mysql_job_v27_execution_authority_v1",
                "bundle_id": bundle_id,
                "source_bundle_id": source_id,
                "scenario": "job_memory",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "storage": execution_storage_authority(),
                "resource_envelope": normalized,
                "runtime_interference_gate_revision": 2,
                "pre_session_cpu_device_first_clean_sample": True,
                "workload_start_requires_runtime_gate_ready": True,
                "fresh_full_physical_copy_required": True,
            },
            "method_bundle": {
                "method_manifest": {
                    "path": str(final / method_name),
                    "sha256": sha_bytes(method_payload),
                },
                "config": {
                    "path": str(final_config),
                    "sha256": sha_bytes(config),
                },
            },
            "provenance_only": {
                "authority_role": "provenance_only",
                "execution_authorized": False,
                "may_participate_in_launch": False,
                "source_predecessor_bundle_id": PREDECESSOR_ID,
                "source_predecessor_readiness": {
                    "path": str(PREDECESSOR_READINESS),
                    "sha256": sha(PREDECESSOR_READINESS),
                },
                "legacy_remote_scenarios": legacy_remote,
            },
        }
        readiness_payload = canonical(readiness)
        readiness_sha = sha_bytes(readiness_payload)
        readiness_name = f"readiness_manifest-{readiness_sha}.json"
        (stage / readiness_name).write_bytes(readiness_payload)
        (stage / "closure.json").write_bytes(canonical(closure))

        files = {
            path.name: sha(path)
            for path in sorted(stage.iterdir())
            if path.is_file()
        }
        (stage / "bundle_manifest.json").write_bytes(canonical({
            **closure,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "output_root": str(OUTPUT_ROOT),
            "files": files,
            "publication": "staged_then_atomic_rename",
        }))
        readonly(stage)
        stage.rename(final)
        try:
            result = verify_readiness(final / readiness_name)
        except BaseException:
            final.rename(BUNDLE_ROOT / f".{bundle_id}.failed.{os.getpid()}")
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
        "launcher": str(final / "launch_job_v27_debug.py"),
        **result,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
