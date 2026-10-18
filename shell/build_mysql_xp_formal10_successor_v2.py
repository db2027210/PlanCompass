#!/usr/bin/env python3
"""Build the strictly immutable XP distributed formal-10 successor v2.

No SSH, Docker, workload, or data-plane action is performed.  Publication is
same-filesystem, frozen before visibility, atomic, non-overwriting, and
idempotent under concurrent identical builders.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
BUILDER = Path(__file__).resolve()
READINESS = ROOT / "shell/mysql_xp_formal10_readiness_v2.py"
CURRENT_QUALIFICATION_COLLECTOR = (
    ROOT / "shell/mysql_distributed_live_readback_qualification_v1.py"
)
CURRENT_METRIC_ADAPTER = (
    ROOT / "shell/mysql_distributed_workload_metric_adapter_v1.py"
)
CURRENT_RUNTIME_TREE_HELPER = (
    ROOT / "shell/runtime_tree_content_identity_v3.py"
)
DEFAULT_BASE = (
    ROOT
    / "artifacts/mysql_xp_distributed_successor_v1"
    / "ea1c826ffa33b409bb94df04eec0d45c70702b80c7c48ff44ac0ba557e20f1b9"
)
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_formal10_successor_v2"

PARAMETERS = [
    "io_block_read_cost",
    "memory_block_read_cost",
    "row_evaluate_cost",
    "key_compare_cost",
    "disk_temptable_create_cost",
    "disk_temptable_row_cost",
    "memory_temptable_row_cost",
]
GRADIENT_FIELDS = [
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
]


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise BuildError(f"{label} must be a regular file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BuildError(f"{label} must be a JSON object")
    return value


def relative_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"symlink prohibited in bundle closure: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def copy_regular_tree(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise BuildError(f"regular source tree required: {source}")
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise BuildError(f"symlink prohibited in source closure: {path}")
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            os.chmod(target, stat.S_IMODE(path.stat().st_mode) | 0o600)


def freeze(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError(f"symlink prohibited in bundle closure: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, mode & ~0o222)
    os.chmod(root, stat.S_IMODE(root.stat().st_mode) & ~0o222)


def thaw_directories(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    for path in [root, *sorted(root.rglob("*"))]:
        if path.is_dir() and not path.is_symlink():
            os.chmod(path, stat.S_IMODE(path.stat().st_mode) | 0o700)


def fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    for path in sorted(
        [item for item in root.rglob("*") if item.is_dir()],
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def verify_base(base: Path) -> dict[str, Any]:
    manifest_path = base / "bundle_manifest.json"
    manifest = load_object(manifest_path, "XP distributed predecessor manifest")
    sidecar = base / "bundle_manifest.json.sha256"
    if (
        manifest.get("schema_version")
        != "mysql_algorithm_c_distributed_successor_bundle_v1"
        or manifest.get("scenario") != "xp"
        or manifest.get("bundle_id") != base.name
        or manifest.get("formal_scope") != "mysql_only"
        or manifest.get("cross_engine_comparison_prohibited") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or manifest.get("four_scene_contract_sha256")
        != "52ba2faa2035dfa9035cb0f6ab75d4461430000097f846c3e94e4a81f99aea58"
    ):
        raise BuildError("XP distributed predecessor identity/scope differs")
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if hashlib.sha256(canonical(identity)).hexdigest() != base.name:
        raise BuildError("XP distributed predecessor content address differs")
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(manifest_path)}  bundle_manifest.json\n"
    ):
        raise BuildError("XP distributed predecessor sidecar differs")
    actual = relative_files(base)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    if actual != manifest.get("files"):
        raise BuildError("XP distributed predecessor file closure differs")
    runtime = load_object(
        base / "distributed_runtime_contract.json",
        "XP distributed runtime contract",
    )
    expected_db = {
        "cpu_quota_cores": 5.3,
        "cpuset_cpus": "21-33,57-69",
        "cpuset_mems": "1",
        "innodb_buffer_pool_bytes": 4294967296,
        "io_read_bps": 29360128,
        "io_write_bps": 524288,
        "memory_bytes": 8589934592,
        "memory_swap_bytes": 8589934592,
        "mysql_port": 65104,
        "pids_max": 1024,
    }
    if runtime.get("resource_envelope") != expected_db:
        raise BuildError("XP distributed database envelope differs")
    predecessor = load_object(
        base / "predecessor/manifest.json", "XP v8 immutable manifest"
    )
    if (
        predecessor.get("active_parameter_count") != 7
        or predecessor.get("required_nonempty_subsets") != 127
        or predecessor.get("fixed_replay_count") != 5
        or predecessor.get("seal_policy", {}).get("worker_future_get_required")
        is not True
        or predecessor.get("seal_policy", {}).get(
            "worker_exception_seal_ineligible"
        )
        is not True
        or predecessor.get("seal_policy", {}).get(
            "xapclient_checked_independently"
        )
        is not True
    ):
        raise BuildError("XP v8 fixed defects/cardinality contract differs")
    return {"manifest": manifest, "runtime": runtime, "v8": predecessor}


def formal10_contract(
    base: Path,
    base_state: Mapping[str, Any],
    qualification_hashes: Mapping[str, str],
) -> dict[str, Any]:
    runtime = base_state["runtime"]
    v8 = base_state["v8"]
    return {
        "schema_version": "mysql_xp_algorithm_c_formal10_contract_v2",
        "scenario": "hybench_xp_io",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "base_distributed_successor_id": base.name,
        "base_distributed_manifest_sha256": sha256(
            base / "bundle_manifest.json"
        ),
        "immutable_v8_bundle_id": v8["bundle_id"],
        "database_resource_envelope": runtime["resource_envelope"],
        "client_resource_envelope": runtime["client_resource_envelope"],
        "storage": runtime["storage"],
        "toolchain": runtime["toolchain"],
        "formal_graceful_quiescence_seconds": 120,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "algorithm": {
            "method": "experience_table_sgd",
            "active_parameters": PARAMETERS,
            "active_parameter_count": 7,
            "required_nonempty_subsets": 127,
            "subset_enumeration": "all_2^7_minus_1_nonempty_subspaces",
            "candidate_order": "global_descending_final_candidate_score",
            "fixed_real_replay_top_k": 5,
            "target_accepted_rounds": 10,
            "original_gradient_only": True,
            "gradient_formula":
                "g_j = sum_r(dScore/dU_r * T[j,r] * confidence)",
            "parameter_update_source": "g_j_and_eta_only",
            "required_gradient_event_fields": GRADIENT_FIELDS,
            "qps_role": "real_replay_outer_acceptance_only",
            "replacement_search_algorithm_prohibited": True,
        },
        "fingerprint": {
            "l1_required": True,
            "l2_required": True,
            "l3_required": True,
            "xp_l3": [
                "shared_read_blocks",
                "shared_write_blocks",
                "local_read_blocks",
                "local_write_blocks",
                "temp_read_blocks",
                "temp_write_blocks",
                "io_wait_ms",
            ],
            "duplicate_rule":
                "exact_parameter_signature_and_equal_l1_and_tolerant_l2_l3",
            "missing_any_level_policy": "independent_candidate",
        },
        "promotion_chain": [
            "immutable_bundle_static_pass",
            "production_trace_AT1_AT6_sealed",
            "client_and_database_live_envelope_readback_pass",
            "180s_bidirectional_dynamic_noninterference_pass",
            "fresh_R0_pass",
            "127_candidates_and_fixed_Top5_debug_pass",
            "main_agent_formal10_launch_decision",
        ],
        "same_live_identity_handoff_required": [
            "database_container_id",
            "mysql_pid_and_starttime",
            "physical_datadir",
            "client_container_id",
            "bundle_id",
            "attempt_token",
        ],
        "accounting_exclusions": [
            "capture",
            "R0",
            "qualification",
            "failed_replay",
            "debug_round",
            "gate_failure",
        ],
        "real_latency": {
            "producer":
                "predecessor/qualification/native_latency_producer/classes/"
                "com/hybench/HyBenchFormalMain.class",
            "source":
                "predecessor/qualification/native_latency_producer/source/"
                "HyBenchFormalMain.java",
            "adapter":
                "qualification/"
                "mysql_distributed_workload_metric_adapter_v1.py",
            "source_kind": "native_HyBench_Result_Histogram",
            "required_percentiles": ["p50", "p95", "p99"],
            "synthetic_or_estimated_latency_prohibited": True,
            "measurement_phase_only": True,
        },
        "trace_seal": {
            "required_at_coverage": [
                "AT1",
                "AT2",
                "AT3",
                "AT4",
                "AT5",
                "AT6",
            ],
            "capture_is_not_r0": True,
            "worker_failure_count_required": 0,
            "qps_resource_input_prohibited": True,
        },
        "qualification_runtime": {
            "collector":
                "qualification/"
                "mysql_distributed_live_readback_qualification_v1.py",
            "metric_adapter":
                "qualification/"
                "mysql_distributed_workload_metric_adapter_v1.py",
            "runtime_tree_helper":
                "qualification/runtime_tree_content_identity_v3.py",
            "collector_sha256": qualification_hashes["collector"],
            "metric_adapter_sha256": qualification_hashes["metric_adapter"],
            "runtime_tree_helper_sha256":
                qualification_hashes["runtime_tree_helper"],
            "predecessor_qualification_copies_superseded": True,
        },
    }


def verify_bundle(bundle: Path, expected_id: str | None = None) -> dict[str, Any]:
    manifest_path = bundle / "bundle_manifest.json"
    manifest = load_object(manifest_path, "XP formal-10 successor manifest")
    bundle_id = manifest.get("bundle_id")
    if expected_id is not None and bundle_id != expected_id:
        raise BuildError("XP formal-10 expected content address differs")
    if expected_id is None and bundle_id != bundle.name:
        raise BuildError("XP formal-10 directory/content address differs")
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if hashlib.sha256(canonical(identity)).hexdigest() != bundle_id:
        raise BuildError("XP formal-10 manifest content address differs")
    sidecar = bundle / "bundle_manifest.json.sha256"
    if (
        not sidecar.is_file()
        or sidecar.is_symlink()
        or sidecar.read_text(encoding="ascii")
        != f"{sha256(manifest_path)}  bundle_manifest.json\n"
    ):
        raise BuildError("XP formal-10 manifest sidecar differs")
    actual = relative_files(bundle)
    actual.pop("bundle_manifest.json", None)
    actual.pop("bundle_manifest.json.sha256", None)
    if actual != manifest.get("files"):
        raise BuildError("XP formal-10 full file closure differs")
    for path in [bundle, *sorted(bundle.rglob("*"))]:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o222:
            raise BuildError(f"XP formal-10 immutable mode differs: {path}")
    return manifest


def publish(base: Path, output_root: Path) -> Path:
    base = base.resolve()
    output_root = output_root.resolve()
    base_state = verify_base(base)
    required_sources = {
        "XP readiness verifier": READINESS,
        "current qualification collector": CURRENT_QUALIFICATION_COLLECTOR,
        "current metric adapter": CURRENT_METRIC_ADAPTER,
        "current runtime tree helper": CURRENT_RUNTIME_TREE_HELPER,
    }
    for label, path in required_sources.items():
        if not path.is_file() or path.is_symlink():
            raise BuildError(f"{label} absent")
    output_root.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=".xp-formal10-v2.", dir=output_root)
    )
    try:
        copy_regular_tree(base, stage / "predecessor")
        shutil.copyfile(READINESS, stage / "xp_formal10_readiness_v2.py")
        os.chmod(stage / "xp_formal10_readiness_v2.py", 0o755)
        (stage / "qualification").mkdir()
        qualification_hashes: dict[str, str] = {}
        for source, name, identity_name in (
            (
                CURRENT_QUALIFICATION_COLLECTOR,
                "mysql_distributed_live_readback_qualification_v1.py",
                "collector",
            ),
            (
                CURRENT_METRIC_ADAPTER,
                "mysql_distributed_workload_metric_adapter_v1.py",
                "metric_adapter",
            ),
            (
                CURRENT_RUNTIME_TREE_HELPER,
                "runtime_tree_content_identity_v3.py",
                "runtime_tree_helper",
            ),
        ):
            destination = stage / "qualification" / name
            before = sha256(source)
            shutil.copyfile(source, destination)
            after = sha256(source)
            copied = sha256(destination)
            if before != after or copied != after:
                raise BuildError(
                    f"qualification source changed while sealing: {source}"
                )
            qualification_hashes[identity_name] = copied
            os.chmod(destination, 0o755)
        contract = formal10_contract(
            base, base_state, qualification_hashes
        )
        contract_payload = canonical(contract)
        (stage / "xp_formal10_contract.json").write_bytes(contract_payload)
        files = relative_files(stage)
        identity = {
            "schema_version": "mysql_xp_distributed_formal10_successor_v2",
            "scenario": "hybench_xp_io",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "immutable": True,
            "offline_build_only": True,
            "remote_actions": 0,
            "base_successor_id": base.name,
            "base_successor_manifest_sha256": sha256(
                base / "bundle_manifest.json"
            ),
            "formal10_contract_sha256": hashlib.sha256(
                contract_payload
            ).hexdigest(),
            "builder_sha256": sha256(BUILDER),
            "readiness_verifier_sha256": sha256(READINESS),
            "qualification_collector_sha256":
                qualification_hashes["collector"],
            "metric_adapter_sha256":
                qualification_hashes["metric_adapter"],
            "runtime_tree_helper_sha256":
                qualification_hashes["runtime_tree_helper"],
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "publication": {
                "content_addressed": True,
                "same_filesystem": True,
                "all_entries_frozen_before_visibility": True,
                "atomic_directory_rename": True,
                "non_overwriting": True,
                "concurrent_identical_publish_idempotent": True,
                "fsync_before_and_after_rename": True,
            },
            "files": dict(sorted(files.items())),
        }
        bundle_id = hashlib.sha256(canonical(identity)).hexdigest()
        manifest = {**identity, "bundle_id": bundle_id}
        payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            f"{hashlib.sha256(payload).hexdigest()}  bundle_manifest.json\n",
            encoding="ascii",
        )
        freeze(stage)
        verify_bundle(stage, bundle_id)
        fsync_tree(stage)
        final = output_root / bundle_id
        if final.exists() or final.is_symlink():
            verify_bundle(final)
            if final.name != bundle_id:
                raise BuildError("existing XP formal-10 bundle identity differs")
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        try:
            os.rename(stage, final)
        except OSError as exc:
            if exc.errno not in (errno.EEXIST, errno.ENOTEMPTY):
                raise
            verify_bundle(final)
            if final.name != bundle_id:
                raise BuildError("concurrent XP formal-10 publication differs")
            thaw_directories(stage)
            shutil.rmtree(stage)
            return final
        descriptor = os.open(output_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        verify_bundle(final)
        return final
    except BaseException:
        if stage.exists():
            thaw_directories(stage)
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--verify-bundle", type=Path)
    args = parser.parse_args()
    if args.verify_bundle is not None:
        result = verify_bundle(args.verify_bundle.resolve())
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 0
    bundle = publish(args.base, args.output_root)
    print(
        json.dumps(
            {
                "schema_version":
                    "mysql_xp_formal10_successor_build_result_v2",
                "status": "BUILT_STATIC_PASS_EVIDENCE_PENDING",
                "formal_scope": "mysql_only",
                "cross_engine_comparison_prohibited": True,
                "remote_actions": 0,
                "bundle": str(bundle),
                "bundle_id": bundle.name,
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
