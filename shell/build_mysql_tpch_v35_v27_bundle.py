#!/usr/bin/env python3
"""Publish immutable TPC-H v35 bundle with v27 local service authority."""

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

PREDECESSOR_ID = (
    "e5fa69ad3af786cf5e9e41917695b703cd9b92f92eb1c017f8a43d9dbfb4d3d5"
)
PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_v34_debug" / PREDECESSOR_ID
)
PREDECESSOR_MANIFEST = PREDECESSOR / "bundle_manifest.json"
PREDECESSOR_READINESS = next(PREDECESSOR.glob("readiness_manifest-*.json"))
PREDECESSOR_METHOD = (
    PREDECESSOR / "experience_table_sgd_method_manifest.json"
)
BASE_SOURCE = Path(
    json.loads(PREDECESSOR_MANIFEST.read_text(encoding="utf-8"))[
        "source_directory"
    ]
)
STORAGE_MANIFEST = Path(
    "/disk2/ra_mysql_isolated_v2/tpch-storage-v2.manifest.json"
)
PROFILE_SOURCE = (
    ROOT / "config/formal/mysql_sgd_production/profiles/"
    "tpch_v35_debug_profile.json"
)
PROFILE_RELATIVE = "profiles/tpch_v35_debug_profile.json"
SERVICE_SOURCE = ROOT / "shell/mysql_tpch_algorithm_c_local_services_v27.py"
VERIFIER_SOURCE = ROOT / "shell/verify_mysql_tpch_v35_readiness.py"
TEST_SOURCE = ROOT / "tests/test_mysql_tpch_v35_authority.py"
BUILDER = Path(__file__).resolve()
SERVICE = "shell.mysql_tpch_algorithm_c_local_services_v27:build"
SOURCE_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_tpch_v35_v27_v1"
)
BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_v35_debug"
OUTPUT_ROOT = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_v35_original_gradient_fresh_r0_debug_20260729"
)
V33_EFFECTIVE_READBACK = (
    ROOT
    / "history/mysql_gradient_debug/"
    "tpch_local_r0_v33_cpu450_io576_terminal4_deterministic_v1/"
    "production_points/alg_c_tpch_r0_20260729T133913_697ed2c7/"
    "effective_resource_readback_v2.json"
)
EXTRA_SOURCES = {
    PROFILE_RELATIVE: PROFILE_SOURCE,
    "shell/mysql_tpch_algorithm_c_local_services_v27.py": SERVICE_SOURCE,
    "shell/verify_mysql_tpch_v35_readiness.py": VERIFIER_SOURCE,
    "tests/test_mysql_tpch_v35_authority.py": TEST_SOURCE,
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
        "scenario_scope": ["tpch_memory"],
    }


def validate_inputs() -> Mapping[str, Any]:
    required = [
        PREDECESSOR_MANIFEST,
        PREDECESSOR_READINESS,
        PREDECESSOR_METHOD,
        STORAGE_MANIFEST,
        PROFILE_SOURCE,
        SERVICE_SOURCE,
        VERIFIER_SOURCE,
        TEST_SOURCE,
        BUILDER,
        V33_EFFECTIVE_READBACK,
    ]
    for path in required:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"authority input absent or symlinked: {path}")
    predecessor = json.loads(
        PREDECESSOR_MANIFEST.read_text(encoding="utf-8")
    )
    if predecessor.get("bundle_id") != PREDECESSOR_ID:
        raise RuntimeError("TPC-H predecessor identity differs")
    for name, expected in predecessor.get("files", {}).items():
        candidate = PREDECESSOR / name
        if not candidate.is_file() or sha(candidate) != expected:
            raise RuntimeError(f"TPC-H predecessor closure differs: {name}")
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
            raise RuntimeError(f"TPC-H predecessor source differs: {relative}")
    storage = json.loads(STORAGE_MANIFEST.read_text(encoding="utf-8"))
    expected_storage = {
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "template_path": "/disk2/ra_mysql_isolated_v2/tpch-memory",
        "device": "/dev/nvme2n1",
        "device_major_minor": "259:6",
        "filesystem": "ext4",
        "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "overlay": False,
        "reflink": False,
    }
    for key, expected in expected_storage.items():
        if storage.get(key) != expected:
            raise RuntimeError(f"TPC-H storage {key} differs")
    profile = json.loads(PROFILE_SOURCE.read_text(encoding="utf-8"))
    if profile.get("memory_bytes") != 5583454208:
        raise RuntimeError("corrected TPC-H profile memory differs")
    return source_manifest


def publish_source(
    base: Mapping[str, Any],
) -> tuple[Path, dict[str, str], str]:
    files = {
        relative: expected
        for relative, expected in base["files"].items()
        if not relative.startswith("profiles/")
        and not relative.startswith(
            "config/formal/mysql_sgd_production/profiles/"
        )
    }
    for relative, path in EXTRA_SOURCES.items():
        files[relative] = sha(path)
    identity = {
        "schema_version": "mysql_tpch_v35_v27_source_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "predecessor_source_bundle_id": base["bundle_id"],
        "original_gradient_only": True,
        "single_execution_storage_authority": True,
        "single_execution_resource_authority": True,
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
            raise RuntimeError("existing TPC-H v35 source manifest differs")
        for relative, expected in files.items():
            if sha(final / relative) != expected:
                raise RuntimeError(
                    f"existing TPC-H v35 source differs: {relative}"
                )
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


def storage_authority() -> dict[str, Any]:
    return {
        "schema_version": "mysql_tpch_execution_storage_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "tpch_memory",
        "host_id": "compute00",
        "template_path": "/disk2/ra_mysql_isolated_v2/tpch-memory",
        "manifest_path": str(STORAGE_MANIFEST),
        "manifest_sha256": sha(STORAGE_MANIFEST),
        "profile_id": "tpch_local_storage_v2_nvme_fullcopy",
        "device": "/dev/nvme2n1",
        "device_major_minor": "259:6",
        "filesystem": "ext4",
        "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "overlay": False,
        "reflink": False,
    }


def resource_authority(source: Path) -> dict[str, Any]:
    profile = source / PROFILE_RELATIVE
    return {
        "schema_version": "mysql_tpch_execution_resource_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "tpch_memory",
        "host_id": "compute00",
        "cpuset_cpus": "200-207,456-463",
        "cpuset_mems": "1",
        "cpu_quota_cores": 4,
        "memory_bytes": 5583454208,
        "memory_swap_bytes": 5583454208,
        "io_read_bps": 100663296,
        "io_write_bps": 100663296,
        "pids_max": 512,
        "buffer_pool_bytes": 4294967296,
        "storage_device": "/dev/nvme2n1",
        "storage_device_major_minor": "259:6",
        "profile_path": str(profile),
        "profile_sha256": sha(profile),
    }


def config_payload(source: Path) -> bytes:
    config = json.loads(
        (PREDECESSOR / "config.json").read_text(encoding="utf-8")
    )
    legacy_template = config.pop("data_template", None)
    workload = config.get("workload") or {}
    legacy_contracts = {
        key: workload.pop(key)
        for key in list(workload)
        if key.startswith("v") and key.endswith("_resource_contract")
    }
    resource = resource_authority(source)
    config.update({
        "exp_id": "mysql_algorithm_c.tpch_memory.v35_v27_one_round_debug",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "accepted_round_authorized": False,
        "profile": resource["profile_path"],
        "local_storage_profile_id":
            "tpch_local_storage_v2_nvme_fullcopy",
        "local_storage_manifest": str(STORAGE_MANIFEST),
        "local_storage_manifest_sha256": sha(STORAGE_MANIFEST),
        "execution_storage_authority": storage_authority(),
        "execution_resource_authority": resource,
        "authority_policy": {
            "schema_version": "mysql_tpch_single_authority_policy_v1",
            "executable_storage_authority_count": 1,
            "executable_resource_authority_count": 1,
            "mismatch_action": "FAIL_CLOSED",
            "runtime_readback_must_equal_authority": True,
        },
        "provenance_only": {
            "authority_role": "provenance_only",
            "execution_authorized": False,
            "may_participate_in_launch": False,
            "source_predecessor_bundle_id": PREDECESSOR_ID,
            "legacy_data_template": legacy_template,
            "legacy_resource_contracts": legacy_contracts,
            "known_profile_memory_mismatch": {
                "legacy": 5583457485,
                "authorized": 5583454208,
                "delta_bytes": 3277,
            },
        },
    })
    workload.update({
        "host_control_cpuset": resource["cpuset_cpus"],
        "tpch_terminals": 4,
        "fresh_copy_mode": "full_physical_copy_no_overlay_no_reflink",
        "fresh_full_physical_copy_required": True,
        "v35_resource_contract": {
            "authority_role": "mirror_only",
            "cpu_quota_cores": 4,
            "memory_bytes": 5583454208,
            "io_read_bps": 100663296,
            "io_write_bps": 100663296,
            "terminals": 4,
        },
    })
    config["workload"] = workload
    config["production"].pop("readiness_manifest", None)
    config["production"].update({
        "launch_authorized": True,
        "service_factory": SERVICE,
    })
    config["output"]["directory"] = str(OUTPUT_ROOT)
    early = config.get("early_stop_source_bundle")
    if isinstance(early, dict):
        early["source"] = str(source)
        early["source_manifest_sha256"] = sha(
            source / "source_manifest.json"
        )
    return canonical(config)


def launcher_payload(source: Path) -> bytes:
    debug = source / "shell/debug_mysql_gradient_round_v25.py"
    verifier = source / "shell/verify_mysql_tpch_v35_readiness.py"
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
LABEL = "TPC-H v35/v27"
os.chdir("/tmp")
argv = sys.argv[1:]
if "--help" not in argv and "-h" not in argv:
    def required_option(name):
        if argv.count(name) != 1:
            raise RuntimeError(LABEL + " requires exactly one " + name)
        index = argv.index(name)
        if index + 1 >= len(argv):
            raise RuntimeError(LABEL + " option has no value: " + name)
        return argv[index + 1]
    if required_option("--service-factory") != EXPECTED_SERVICE:
        raise RuntimeError(LABEL + " service factory override is prohibited")
    bundle_dir = Path(__file__).resolve().parent
    readiness = Path(required_option("--readiness-manifest"))
    if (
        readiness.is_symlink()
        or readiness.resolve().parent != bundle_dir
        or not readiness.name.startswith("readiness_manifest-")
        or readiness.suffix != ".json"
    ):
        raise RuntimeError(LABEL + " readiness must be the sibling manifest")
    if "--r0-authority-readiness-manifest" in argv:
        raise RuntimeError(LABEL + " requires a fresh in-band R0")
    if "--resume-directory" not in argv:
        config = Path(required_option("--config"))
        if config.is_symlink() or config.resolve() != bundle_dir / "config.json":
            raise RuntimeError(LABEL + " config must be the sibling config")
shell_package = type(sys)("shell")
shell_package.__path__ = [str(SOURCE / "shell")]
sys.modules["shell"] = shell_package
spec = importlib.util.spec_from_file_location(
    "shell.verify_mysql_sgd_v4_readiness", VERIFIER
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load frozen " + LABEL + " verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
sys.path[:] = [str(SOURCE)] + [
    item for item in sys.path if item not in ("", str(SOURCE))
]
runpy.run_path(str(DEBUG), run_name="__main__")
""".encode("utf-8")


def verify_readiness(path: Path) -> dict[str, int]:
    spec = importlib.util.spec_from_file_location(
        "mysql_tpch_v35_verifier", VERIFIER_SOURCE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import TPC-H v35 verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    checks = module.verify_document(
        json.loads(path.read_text(encoding="utf-8")), ROOT
    )
    failed = [item for item in checks if not item.passed]
    required = {"AUTH", "SCHEMA", *(f"R{x:02d}" for x in range(1, 12))}
    observed = {item.requirement for item in checks}
    if failed or observed != required:
        raise RuntimeError(
            "TPC-H v35 readiness failed: "
            f"failed={[(x.item, x.reason) for x in failed]} "
            f"groups={sorted(observed)}"
        )
    return {"verifier_passed": len(checks), "verifier_failed": 0}


def verify_existing(
    final: Path, bundle_id: str,
) -> tuple[Path, dict[str, int]]:
    manifest = json.loads(
        (final / "bundle_manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("bundle_id") != bundle_id:
        raise RuntimeError("existing TPC-H v35 identity differs")
    for name, expected in manifest.get("files", {}).items():
        candidate = final / name
        if not candidate.is_file() or sha(candidate) != expected:
            raise RuntimeError(f"existing TPC-H v35 differs: {name}")
    readiness = list(final.glob("readiness_manifest-*.json"))
    if len(readiness) != 1:
        raise RuntimeError("existing TPC-H v35 readiness count differs")
    return readiness[0], verify_readiness(readiness[0])


def main() -> int:
    base_manifest = validate_inputs()
    source, source_files, source_id = publish_source(base_manifest)
    config = config_payload(source)
    launcher = launcher_payload(source)
    storage = storage_authority()
    resource = resource_authority(source)
    closure = {
        "schema_version": "mysql_tpch_v35_v27_bundle_closure_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "accepted_round_authorized": False,
        "supersedes_bundle_ids": [PREDECESSOR_ID],
        "single_execution_storage_authority": True,
        "single_execution_resource_authority": True,
        "runtime_interference_gate_revision": 2,
        "pre_session_cpu_device_first_clean_sample": True,
        "workload_start_requires_runtime_gate_ready": True,
        "debug_outer_attempts": 1,
        "source_bundle_id": source_id,
        "source_manifest_sha256": sha(
            source / "source_manifest.json"
        ),
        "config_sha256": sha_bytes(config),
        "launcher_sha256": sha_bytes(launcher),
        "storage_manifest_sha256": sha(STORAGE_MANIFEST),
        "execution_storage_authority": storage,
        "execution_resource_authority": resource,
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
            "launcher": str(final / "launch_tpch_v35_debug.py"),
            **result,
        }, sort_keys=True))
        return 0

    BUNDLE_ROOT.mkdir(parents=True, exist_ok=True)
    stage = BUNDLE_ROOT / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    readiness_name = ""
    try:
        (stage / "config.json").write_bytes(config)
        (stage / "launch_tpch_v35_debug.py").write_bytes(launcher)
        (stage / "launch_tpch_v35_debug.py").chmod(0o555)
        final_config = final / "config.json"
        final_launcher = final / "launch_tpch_v35_debug.py"

        readback = final / "tpch_v35_mysql_only_resource_readback.json"
        readback_payload = canonical({
            "schema_version": "mysql_tpch_v35_resource_readback_v1",
            "scenario": "tpch_memory",
            "engine": "mysql",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "status": "FROZEN_FRESH_IN_BAND_READBACK_REQUIRED",
            "execution_storage_authority": storage,
            "execution_resource_authority": resource,
            "runtime_readback_required": {
                **{
                    key: resource[key]
                    for key in (
                        "cpuset_cpus", "cpuset_mems", "cpu_quota_cores",
                        "memory_bytes", "memory_swap_bytes", "io_read_bps",
                        "io_write_bps", "pids_max", "storage_device",
                        "storage_device_major_minor",
                    )
                },
                "mismatch_action": "FAIL_CLOSED",
            },
            "source_effective_readback": {
                "path": str(V33_EFFECTIVE_READBACK),
                "sha256": sha(V33_EFFECTIVE_READBACK),
                "authority_role": "historical_readiness_only",
                "fresh_in_band_r0_required": True,
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
                "scenario_scope": ["tpch_memory"],
            },
            {
                "path": str(final_launcher),
                "sha256": sha_bytes(launcher),
                "role": "launch_wrapper",
                "scenario_scope": ["tpch_memory"],
            },
            pointer(STORAGE_MANIFEST, "sole_execution_storage_manifest"),
            pointer(
                source / PROFILE_RELATIVE,
                "sole_execution_resource_profile",
            ),
            {
                "path": str(readback),
                "sha256": sha_bytes(readback_payload),
                "role": "mysql_only_resource_readback",
                "scenario_scope": ["tpch_memory"],
            },
        ])
        method = json.loads(
            PREDECESSOR_METHOD.read_text(encoding="utf-8")
        )
        method.update({
            "schema_version": "mysql_tpch_v35_v27_method_manifest_v1",
            "registered_sources": entries,
            "formal_launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_authorized": False,
            "execution_authorized_scenarios": ["tpch_memory"],
            "runtime_binding": {
                "entrypoint": str(final_launcher),
                "source_directory": str(source),
                "config": str(final_config),
                "profile": resource["profile_path"],
                "profile_sha256": resource["profile_sha256"],
                "service_factory": SERVICE,
                "accepted_round_authorized": False,
                "debug_outer_attempts": 1,
                "single_execution_storage_authority": True,
                "single_execution_resource_authority": True,
            },
        })
        method_payload = canonical(method)
        method_name = "experience_table_sgd_method_manifest.json"
        (stage / method_name).write_bytes(method_payload)

        old_readiness = json.loads(
            PREDECESSOR_READINESS.read_text(encoding="utf-8")
        )
        readiness = {
            "schema_version": "mysql_tpch_v35_v27_readiness_v1",
            "status": "READY",
            "launch_authorized": True,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "paired_result_merge_prohibited": True,
            "execution_authorized_scenarios": ["tpch_memory"],
            "scenarios": {
                "tpch_memory": {
                    "benchmark": "tpch",
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
                    "mysql_tpch_v35_v27_execution_authority_v1",
                "bundle_id": bundle_id,
                "source_bundle_id": source_id,
                "scenario": "tpch_memory",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
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
                "known_profile_memory_mismatch": {
                    "legacy": 5583457485,
                    "authorized": 5583454208,
                    "delta_bytes": 3277,
                },
                "legacy_scenarios": old_readiness.get("scenarios", {}),
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
        "launcher": str(final / "launch_tpch_v35_debug.py"),
        **result,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
