#!/usr/bin/env python3
"""Deprecated, fail-closed historical shared-disk successor builder.

The disk2 layout conflicts with the active PostgreSQL TPC-C device on compute00.
It was superseded by the frozen distributed successor contract on 2026-07-29:
JOB uses node02/NVMe1 and TPC-H uses node04/NVMe0.  Keeping the historical
implementation importable preserves its audit tests, but every build or
publication entry point now refuses to run.

This module must never be used as launch or publication authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.verify_mysql_job_v31_disk2_successor import (
    validate_storage_manifest,
)


BUILDER = Path(__file__).resolve()
LOCK_MODULE = ROOT / "shell/mysql_disk2_shared_lane_v1.py"
CONTRACT_PATH = (
    ROOT / "preflight/mysql_job_v31_disk2_successor_contract_20260729.json"
)
JOB_PREDECESSOR = (
    ROOT / "artifacts/mysql_job_v30_debug"
    / "b888870bc6ef136932c26676c797823d2b99858cf6b2b3891f7afe1ef42d3190"
)
TPCH_PREDECESSOR = (
    ROOT / "artifacts/mysql_tpch_v36_debug"
    / "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4"
)
JOB_OUTPUT = (
    ROOT / "history/mysql_gradient_debug/"
    "job_v31_disk2_original_gradient_fresh_r0_debug_20260729"
)
TPCH_OUTPUT = (
    ROOT / "history/mysql_gradient_debug/"
    "tpch_v37_disk2_shared_lane_fresh_r0_debug_20260729"
)
JOB_BUNDLE_ROOT = ROOT / "artifacts/mysql_job_v31_disk2_debug"
TPCH_BUNDLE_ROOT = ROOT / "artifacts/mysql_tpch_v37_disk2_debug"
OVERLAY_ROOT = (
    ROOT / "history/mysql_gradient_debug/readiness_mysql_job_v31_disk2_overlay"
)
PAIR_ROOT = ROOT / "artifacts/mysql_disk2_job_tpch_shared_lane_v1"
SUPERSEDING_CONTRACT = (
    ROOT
    / "preflight/mysql_algorithm_c_distributed_four_scene_successor_contract_20260729.json"
)


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise BuildError(f"{label} absent or symlinked: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"{label} must be a JSON object")
    return value


def verify_bundle_closure(bundle: Path) -> dict[str, Any]:
    manifest = load_object(bundle / "bundle_manifest.json", "predecessor manifest")
    if manifest.get("bundle_id") != bundle.name:
        raise BuildError("predecessor bundle identity differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = bundle / relative
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256(path) != expected
        ):
            raise BuildError(f"predecessor closure differs: {path}")
    return manifest


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise BuildError(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new)


def job_overlay_payloads(source: Path) -> dict[str, bytes]:
    v25_path = source / "shell/mysql_algorithm_c_local_services_v25.py"
    v27_path = source / "shell/mysql_algorithm_c_local_services_v27.py"
    v28_path = source / "shell/mysql_algorithm_c_local_services_v28.py"
    v25 = v25_path.read_text(encoding="utf-8")
    replacements = {
        '"/disk1/resource_adaptive_mysql_algorithm_c_local_v2/job"':
            '"/disk2/resource_adaptive_mysql_algorithm_c_local_v3/job"',
        'profile_id="job_local_storage_v2_nvme_fullcopy"':
            'profile_id="job_local_storage_v3_disk2_nvme_fullcopy"',
        'schema_version="mysql_job_local_storage_v2"':
            'schema_version="mysql_job_local_storage_v3"',
        'template="/disk1/ra_mysql_isolated_v2/job-base"':
            'template="/disk2/ra_mysql_isolated_v2/job-base"',
        'manifest="/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json"':
            'manifest="/disk2/ra_mysql_isolated_v2/job-storage-v3.manifest.json"',
        'device="/dev/nvme1n1"': 'device="/dev/nvme2n1"',
        'device_major_minor="259:5"': 'device_major_minor="259:6"',
    }
    for old, new in replacements.items():
        v25 = replace_exact(v25, old, new, f"v25 {old}")

    v27 = v27_path.read_text(encoding="utf-8")
    for old, new in {
        '"/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json"':
            '"/disk2/ra_mysql_isolated_v2/job-storage-v3.manifest.json"',
        'EXPECTED_TEMPLATE = "/disk1/ra_mysql_isolated_v2/job-base"':
            'EXPECTED_TEMPLATE = "/disk2/ra_mysql_isolated_v2/job-base"',
        'EXPECTED_DEVICE = "/dev/nvme1n1"':
            'EXPECTED_DEVICE = "/dev/nvme2n1"',
        'EXPECTED_MAJOR_MINOR = "259:5"':
            'EXPECTED_MAJOR_MINOR = "259:6"',
        'EXPECTED_PROFILE = "job_local_storage_v2_nvme_fullcopy"':
            'EXPECTED_PROFILE = "job_local_storage_v3_disk2_nvme_fullcopy"',
    }.items():
        v27 = replace_exact(v27, old, new, f"v27 {old}")

    v28 = v28_path.read_text(encoding="utf-8")
    for old, new in {
        '"storage_device": "/dev/nvme1n1"':
            '"storage_device": "/dev/nvme2n1"',
        '"storage_device_major_minor": "259:5"':
            '"storage_device_major_minor": "259:6"',
        'or lane.device != "/dev/nvme1n1"':
            'or lane.device != "/dev/nvme2n1"',
    }.items():
        v28 = replace_exact(v28, old, new, f"v28 {old}")

    verify_v27 = (
        source / "shell/verify_mysql_job_v27_readiness.py"
    ).read_text(encoding="utf-8")
    for old, new in {
        'EXPECTED_MANIFEST = "/disk1/ra_mysql_isolated_v2/job-storage-v2.manifest.json"':
            'EXPECTED_MANIFEST = "/disk2/ra_mysql_isolated_v2/job-storage-v3.manifest.json"',
        'EXPECTED_TEMPLATE = "/disk1/ra_mysql_isolated_v2/job-base"':
            'EXPECTED_TEMPLATE = "/disk2/ra_mysql_isolated_v2/job-base"',
        'EXPECTED_DEVICE = "/dev/nvme1n1"':
            'EXPECTED_DEVICE = "/dev/nvme2n1"',
        'EXPECTED_MAJOR_MINOR = "259:5"':
            'EXPECTED_MAJOR_MINOR = "259:6"',
        '"profile_id": "job_local_storage_v2_nvme_fullcopy"':
            '"profile_id": "job_local_storage_v3_disk2_nvme_fullcopy"',
        '"local /disk1 259:5 authority must match exactly"':
            '"local /disk2 259:6 authority must match exactly"',
    }.items():
        verify_v27 = replace_exact(verify_v27, old, new, f"verify v27 {old}")
    return {
        "shell/mysql_algorithm_c_local_services_v25.py": v25.encode(),
        "shell/mysql_algorithm_c_local_services_v27.py": v27.encode(),
        "shell/mysql_algorithm_c_local_services_v28.py": v28.encode(),
        "shell/verify_mysql_job_v27_readiness.py": verify_v27.encode(),
    }


def publish_overlay(source: Path, execute: bool) -> tuple[Path, str]:
    payloads = job_overlay_payloads(source)
    identity = {
        "schema_version": "mysql_job_v31_disk2_runtime_overlay_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "base_source_directory": str(source),
        "base_source_manifest_sha256": sha256(source / "source_manifest.json"),
        "files": {
            relative: sha_bytes(payload)
            for relative, payload in sorted(payloads.items())
        },
    }
    overlay_id = sha_bytes(canonical(identity))
    final = OVERLAY_ROOT / f"overlay-{overlay_id}"
    expected_manifest = {**identity, "overlay_id": overlay_id}
    if final.exists():
        if load_object(final / "overlay_manifest.json", "overlay manifest") != expected_manifest:
            raise BuildError("existing overlay manifest differs")
        for relative, digest in identity["files"].items():
            if sha256(final / relative) != digest:
                raise BuildError(f"existing overlay file differs: {relative}")
        return final, overlay_id
    if not execute:
        return final, overlay_id
    OVERLAY_ROOT.mkdir(parents=True, exist_ok=True)
    stage = OVERLAY_ROOT / f".overlay-{overlay_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        for relative, payload in payloads.items():
            path = stage / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        (stage / "overlay_manifest.json").write_bytes(canonical(expected_manifest))
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return final, overlay_id


def readonly(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def storage_authority(
    storage_manifest: Mapping[str, Any], manifest_sha: str
) -> dict[str, Any]:
    return {
        "schema_version": "mysql_job_execution_storage_authority_v1",
        "authority_role": "sole_execution_authority",
        "execution_authorized": True,
        "scenario": "job_memory",
        "host_id": "compute00",
        "template_path": storage_manifest["template_path"],
        "manifest_path": str(
            Path("/disk2/ra_mysql_isolated_v2/job-storage-v3.manifest.json")
        ),
        "manifest_sha256": manifest_sha,
        "profile_id": storage_manifest["profile_id"],
        "device": storage_manifest["device"],
        "device_major_minor": storage_manifest["device_major_minor"],
        "filesystem": storage_manifest["filesystem"],
        "fresh_copy_mode": storage_manifest["fresh_copy_mode"],
        "overlay": False,
        "reflink": False,
    }


def resource_on_disk2(resource: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(resource)
    result["storage_device"] = "/dev/nvme2n1"
    result["storage_device_major_minor"] = "259:6"
    return result


def successor_config(
    scenario: str,
    predecessor: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    storage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = json.loads(json.dumps(predecessor))
    lane = contract["shared_device_lane"]
    result["shared_device_lane"] = lane
    result["accepted_round_authorized"] = False
    old_mutex = dict(result.get("host02_job_xp_mutex") or {})
    old_mutex.update({
        "required": False,
        "mode": "superseded_by_disjoint_job_xp_devices",
        "execution_authority": False,
    })
    result["host02_job_xp_mutex"] = old_mutex
    if scenario == "job":
        assert storage is not None
        result["execution_resource_authority"] = resource_on_disk2(
            result["execution_resource_authority"]
        )
        result["execution_storage_authority"] = dict(storage)
        result["local_storage_profile_id"] = storage["profile_id"]
        result["local_storage_manifest"] = storage["manifest_path"]
        result["local_storage_manifest_sha256"] = storage["manifest_sha256"]
        output = JOB_OUTPUT
    else:
        output = TPCH_OUTPUT
    result["output"] = {**dict(result.get("output") or {}), "directory": str(output)}
    outer = dict(result.get("formal_outer_protocol") or {})
    outer["formal_checkpoint_path"] = str(output / "formal_outer_checkpoint.json")
    outer["formal_real_r0_checkpoint_path"] = str(output / "formal_real_r0.json")
    result["formal_outer_protocol"] = outer
    result["fresh_resource_readback_required"] = True
    result["old_resource_readback_reusable"] = False
    return result


def launcher_payload(
    predecessor_launcher: Path,
    scenario: str,
    *,
    overlay: Path | None,
    pair_path: Path,
    pair_id: str,
    own_bundle_id: str,
) -> bytes:
    text = predecessor_launcher.read_text(encoding="utf-8")
    marker = "import sys\n"
    insertion = (
        "import json\nimport hashlib\n"
        "from mysql_disk2_shared_lane_v1 import Disk2SharedLane\n"
    )
    text = replace_exact(text, marker, marker + insertion, "launcher imports")
    if overlay is not None:
        source_line = next(
            line for line in text.splitlines() if line.startswith("SOURCE = Path(")
        )
        text = replace_exact(
            text,
            source_line,
            source_line + f"\nOVERLAY = Path({str(overlay)!r})",
            "launcher overlay constant",
        )
        text = replace_exact(
            text,
            'shell_package.__path__ = [str(SOURCE / "shell")]',
            'shell_package.__path__ = [str(OVERLAY / "shell"), str(SOURCE / "shell")]',
            "launcher shell overlay",
        )
        text = replace_exact(
            text,
            "sys.path[:] = [str(SOURCE)] + [",
            "sys.path[:] = [str(OVERLAY), str(SOURCE)] + [",
            "launcher path overlay",
        )
    registry_guard = (
        f"\nPAIR_REGISTRY = Path({str(pair_path)!r})\n"
        f"EXPECTED_PAIR_ID = {pair_id!r}\n"
        f"EXPECTED_OWN_BUNDLE_ID = {own_bundle_id!r}\n"
        "if not PAIR_REGISTRY.is_file() or PAIR_REGISTRY.is_symlink():\n"
        "    raise RuntimeError('shared disk2 pair registry is absent')\n"
        "_pair = json.loads(PAIR_REGISTRY.read_text(encoding='utf-8'))\n"
        "if _pair.get('pair_id') != EXPECTED_PAIR_ID or "
        "EXPECTED_OWN_BUNDLE_ID not in _pair.get('bundle_ids', []):\n"
        "    raise RuntimeError('shared disk2 pair registry differs')\n"
    )
    text = replace_exact(
        text, 'os.chdir("/tmp")', registry_guard + '\nos.chdir("/tmp")',
        "launcher pair guard",
    )
    run_line = "runpy.run_path(str(DEBUG), run_name=\"__main__\")"
    text = replace_exact(
        text,
        run_line,
        f'with Disk2SharedLane("{scenario}_memory"):\n    {run_line}',
        "launcher shared lane",
    )
    return text.encode("utf-8")


def bundle_identity(
    scenario: str,
    predecessor_id: str,
    config: Mapping[str, Any],
    overlay_id: str | None,
    contract_sha: str,
) -> dict[str, Any]:
    return {
        "schema_version": (
            "mysql_job_v31_disk2_bundle_closure_v1"
            if scenario == "job"
            else "mysql_tpch_v37_disk2_shared_lane_bundle_closure_v1"
        ),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "immutable": True,
        "supersedes_bundle_ids": [predecessor_id],
        "scenario": f"{scenario}_memory",
        "accepted_round_authorized": False,
        "fresh_in_band_r0_required": True,
        "fresh_resource_readback_required": True,
        "old_resource_readback_reusable": False,
        "execution_resource_authority": config["execution_resource_authority"],
        "execution_storage_authority": config["execution_storage_authority"],
        "shared_device_lane": config["shared_device_lane"],
        "runtime_overlay_id": overlay_id,
        "contract_sha256": contract_sha,
        "publication": "staged_then_atomic_rename",
        "idempotency": "content_addressed_existing_verified",
        "builder_sha256": sha256(BUILDER),
        "lock_module_sha256": sha256(LOCK_MODULE),
    }


def readiness_document(
    predecessor: Mapping[str, Any],
    *,
    scenario: str,
    bundle_id: str,
    source_id: str,
    resource: Mapping[str, Any],
    storage: Mapping[str, Any],
    lane: Mapping[str, Any],
    final: Path,
    config_sha: str,
    launcher_name: str,
    launcher_sha: str,
    method_sha: str,
) -> dict[str, Any]:
    result = json.loads(json.dumps(predecessor))
    result["schema_version"] = (
        "mysql_job_v31_disk2_readiness_v1"
        if scenario == "job"
        else "mysql_tpch_v37_disk2_shared_lane_readiness_v1"
    )
    execution = dict(result.get("execution_authority") or {})
    execution.update({
        "bundle_id": bundle_id,
        "source_bundle_id": source_id,
        "resource": dict(resource),
        "storage": dict(storage),
        "supersedes_debug_bundle_id": (
            JOB_PREDECESSOR.name if scenario == "job" else TPCH_PREDECESSOR.name
        ),
        "accepted_round_authorized": False,
        "shared_device_lane": dict(lane),
        "fresh_resource_readback_required": True,
        "old_resource_readback_reusable": False,
    })
    result["execution_authority"] = execution
    scenario_name = f"{scenario}_memory"
    card = dict((result.get("scenarios") or {}).get(scenario_name) or {})
    card.update({
        "resource_envelope": dict(resource),
        "storage": dict(storage),
        "accepted_round_authorized": False,
        "fresh_in_band_r0_required": True,
        "shared_device_lane": dict(lane),
    })
    result["scenarios"] = {scenario_name: card}
    result["shared_device_lane"] = dict(lane)
    result["method_bundle"] = {
        "config": {"path": str(final / "config.json"), "sha256": config_sha},
        "launcher": {
            "path": str(final / launcher_name), "sha256": launcher_sha
        },
        "method_manifest": {
            "path": str(final / "experience_table_sgd_method_manifest.json"),
            "sha256": method_sha,
        },
    }
    return result


def method_document(
    predecessor: Mapping[str, Any],
    final: Path,
    launcher_name: str,
    source: Path,
    overlay: Path | None,
    lane: Mapping[str, Any],
) -> dict[str, Any]:
    result = json.loads(json.dumps(predecessor))
    runtime = dict(result.get("runtime_binding") or {})
    runtime.update({
        "config": str(final / "config.json"),
        "entrypoint": str(final / launcher_name),
        "source_directory": str(source),
        "runtime_overlay": None if overlay is None else str(overlay),
        "shared_device_lane": dict(lane),
        "accepted_round_authorized": False,
    })
    result["runtime_binding"] = runtime
    result["shared_device_lane"] = dict(lane)
    return result


def publish_bundle(
    *,
    scenario: str,
    predecessor: Path,
    predecessor_manifest: Mapping[str, Any],
    config: Mapping[str, Any],
    identity: Mapping[str, Any],
    bundle_id: str,
    overlay: Path | None,
    pair_path: Path,
    pair_id: str,
    execute: bool,
) -> Path:
    root = JOB_BUNDLE_ROOT if scenario == "job" else TPCH_BUNDLE_ROOT
    final = root / bundle_id
    if final.exists():
        verify_published_bundle(final, identity, bundle_id)
        return final
    if not execute:
        return final
    root.mkdir(parents=True, exist_ok=True)
    stage = root / f".{bundle_id}.tmp.{os.getpid()}"
    stage.mkdir()
    try:
        config_payload = canonical(config)
        (stage / "config.json").write_bytes(config_payload)
        launcher_name = (
            "launch_job_v31_disk2_debug.py"
            if scenario == "job"
            else "launch_tpch_v37_disk2_debug.py"
        )
        predecessor_launcher = predecessor / (
            "launch_job_v29_debug.py"
            if scenario == "job"
            else "launch_tpch_v35_debug.py"
        )
        launcher = launcher_payload(
            predecessor_launcher,
            scenario,
            overlay=overlay,
            pair_path=pair_path,
            pair_id=pair_id,
            own_bundle_id=bundle_id,
        )
        (stage / launcher_name).write_bytes(launcher)
        shutil.copyfile(LOCK_MODULE, stage / LOCK_MODULE.name)
        source = Path(str(predecessor_manifest["source_directory"]))
        method = method_document(
            load_object(
                predecessor / "experience_table_sgd_method_manifest.json",
                "predecessor method manifest",
            ),
            final,
            launcher_name,
            source,
            overlay,
            config["shared_device_lane"],
        )
        method_payload = canonical(method)
        (stage / "experience_table_sgd_method_manifest.json").write_bytes(
            method_payload
        )
        old_readiness = list(predecessor.glob("readiness_manifest-*.json"))
        if len(old_readiness) != 1:
            raise BuildError("predecessor readiness cardinality differs")
        readiness = readiness_document(
            load_object(old_readiness[0], "predecessor readiness"),
            scenario=scenario,
            bundle_id=bundle_id,
            source_id=str(predecessor_manifest["source_bundle_id"]),
            resource=config["execution_resource_authority"],
            storage=config["execution_storage_authority"],
            lane=config["shared_device_lane"],
            final=final,
            config_sha=sha_bytes(config_payload),
            launcher_name=launcher_name,
            launcher_sha=sha_bytes(launcher),
            method_sha=sha_bytes(method_payload),
        )
        readiness_payload = canonical(readiness)
        readiness_name = (
            "readiness_manifest-" + sha_bytes(readiness_payload) + ".json"
        )
        (stage / readiness_name).write_bytes(readiness_payload)
        closure = dict(identity)
        (stage / "closure.json").write_bytes(canonical(closure))
        files = {
            path.name: sha256(path)
            for path in stage.iterdir()
            if path.is_file()
        }
        manifest = {
            **identity,
            "bundle_id": bundle_id,
            "source_directory": str(source),
            "runtime_overlay": None if overlay is None else str(overlay),
            "output_root": str(
                JOB_OUTPUT if scenario == "job" else TPCH_OUTPUT
            ),
            "files": dict(sorted(files.items())),
        }
        (stage / "bundle_manifest.json").write_bytes(canonical(manifest))
        verify_published_bundle(stage, identity, bundle_id)
        readonly(stage)
        stage.rename(final)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    verify_published_bundle(final, identity, bundle_id)
    return final


def verify_published_bundle(
    path: Path, identity: Mapping[str, Any], bundle_id: str
) -> None:
    manifest = load_object(path / "bundle_manifest.json", "successor manifest")
    if manifest.get("bundle_id") != bundle_id:
        raise BuildError("successor bundle ID differs")
    if sha256(path / "closure.json") != bundle_id:
        raise BuildError("successor closure content address differs")
    for key, value in identity.items():
        if manifest.get(key) != value:
            raise BuildError(f"successor manifest identity differs at {key}")
    for relative, digest in (manifest.get("files") or {}).items():
        file_path = path / relative
        if (
            not file_path.is_file()
            or file_path.is_symlink()
            or sha256(file_path) != digest
        ):
            raise BuildError(f"successor file differs: {relative}")


def build_pair(execute: bool = False) -> dict[str, Any]:
    raise BuildError(
        "DEPRECATED_SHARED_PG_DEVICE: compute00 /dev/nvme2n1 is assigned to "
        "PostgreSQL TPC-C; use the distributed successor contract at "
        f"{SUPERSEDING_CONTRACT}"
    )

    # Historical implementation retained below for source-level audit only.
    contract = load_object(CONTRACT_PATH, "v31 contract")
    contract_sha = sha256(CONTRACT_PATH)
    storage_path = Path(
        contract["required_executable_bindings"]["manifest_path"]
    )
    if not storage_path.is_file() or storage_path.is_symlink():
        raise BuildError(
            "JOB disk2 manifest absent; run prepare_mysql_job_disk2_template_v3.py "
            "--execute first"
        )
    storage_manifest = load_object(storage_path, "JOB disk2 storage manifest")
    problems = validate_storage_manifest(storage_manifest, contract)
    if problems:
        raise BuildError("; ".join(problems))
    if not Path(storage_manifest["template_path"]).is_dir():
        raise BuildError("JOB disk2 template absent")
    storage_sha = sha256(storage_path)
    job_pred_manifest = verify_bundle_closure(JOB_PREDECESSOR)
    tpch_pred_manifest = verify_bundle_closure(TPCH_PREDECESSOR)
    job_source = Path(str(job_pred_manifest["source_directory"]))
    overlay, overlay_id = publish_overlay(job_source, execute)
    job_config = successor_config(
        "job",
        load_object(JOB_PREDECESSOR / "config.json", "JOB config"),
        contract,
        storage=storage_authority(storage_manifest, storage_sha),
    )
    tpch_config = successor_config(
        "tpch",
        load_object(TPCH_PREDECESSOR / "config.json", "TPC-H config"),
        contract,
    )
    job_identity = bundle_identity(
        "job", JOB_PREDECESSOR.name, job_config, overlay_id, contract_sha
    )
    tpch_identity = bundle_identity(
        "tpch", TPCH_PREDECESSOR.name, tpch_config, None, contract_sha
    )
    job_id = sha_bytes(canonical(job_identity))
    tpch_id = sha_bytes(canonical(tpch_identity))
    pair_identity = {
        "schema_version": "mysql_disk2_job_tpch_authority_pair_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "bundle_ids": [job_id, tpch_id],
        "shared_device_lane": contract["shared_device_lane"],
        "direct_predecessor_launch_prohibited": True,
    }
    pair_id = sha_bytes(canonical(pair_identity))
    pair_path = PAIR_ROOT / pair_id / "pair_manifest.json"
    job_bundle = publish_bundle(
        scenario="job",
        predecessor=JOB_PREDECESSOR,
        predecessor_manifest=job_pred_manifest,
        config=job_config,
        identity=job_identity,
        bundle_id=job_id,
        overlay=overlay,
        pair_path=pair_path,
        pair_id=pair_id,
        execute=execute,
    )
    tpch_bundle = publish_bundle(
        scenario="tpch",
        predecessor=TPCH_PREDECESSOR,
        predecessor_manifest=tpch_pred_manifest,
        config=tpch_config,
        identity=tpch_identity,
        bundle_id=tpch_id,
        overlay=None,
        pair_path=pair_path,
        pair_id=pair_id,
        execute=execute,
    )
    if execute and not pair_path.exists():
        final = pair_path.parent
        PAIR_ROOT.mkdir(parents=True, exist_ok=True)
        stage = PAIR_ROOT / f".{pair_id}.tmp.{os.getpid()}"
        stage.mkdir()
        try:
            (stage / "pair_manifest.json").write_bytes(
                canonical({**pair_identity, "pair_id": pair_id})
            )
            readonly(stage)
            stage.rename(final)
        except BaseException:
            if stage.exists():
                shutil.rmtree(stage)
            raise
    if execute:
        pair = load_object(pair_path, "pair registry")
        if pair != {**pair_identity, "pair_id": pair_id}:
            raise BuildError("published pair registry differs")
    return {
        "status": "PUBLISHED" if execute else "READY_TO_PUBLISH",
        "executed": execute,
        "job_bundle": str(job_bundle),
        "tpch_bundle": str(tpch_bundle),
        "pair_manifest": str(pair_path),
        "pair_id": pair_id,
        "shared_device_lane": contract["shared_device_lane"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        result = build_pair(execute=args.execute)
    except BuildError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, indent=2))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
