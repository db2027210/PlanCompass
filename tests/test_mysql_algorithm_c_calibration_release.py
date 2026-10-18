from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import mysql_algorithm_c_calibration_release as release


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _compatibility(workload_contract: dict, *, snapshot: str = "xp-template-v1") -> dict:
    parameters = []
    for index, name in enumerate(release.CALIBRATED_PARAMETERS, 1):
        parameters.append({
            "name": name,
            "active": True,
            "initial": float(index),
            "lower": float(index) / 10.0,
            "upper": float(index) * 10.0,
        })
    return {
        "contract": release.COMPATIBILITY_CONTRACT,
        "database": "mysql",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_cpu",
        "workload": {
            "benchmark": "hybench_xp",
            "workload_version": "native-iq1-iq7-v2",
            "scale_factor": "10x",
            "full_workload_required": True,
            "workload_contract_sha256": release.canonical_sha256(workload_contract),
        },
        "concurrency": {
            "xap_xtp_ratio": "1:1",
            "clients": 8,
            "contract_sha256": "1" * 64,
        },
        "snapshot": {"name": snapshot, "sha256": "2" * 64},
        "resource_capacity": {
            "cpu_quota_cores": 1.7,
            "memory_bytes": 8 * 1024**3,
        },
        "plan_feature_model": {
            "contract_sha256": "3" * 64,
            "implementation_sha256": "4" * 64,
        },
        "optimizer_parameters": parameters,
    }


def _fingerprint(*, changed: bool = False) -> dict:
    queries = {
        f"IQ{index}": f"sha-{index}{'-changed' if changed and index == 4 else ''}"
        for index in range(1, 8)
    }
    return {
        "contract": release.EXPLAIN_CONTRACT,
        "complete": True,
        "expected_query_count": 7,
        "missing_query_ids": [],
        "query_fingerprints": queries,
        "aggregate_sha256": release.canonical_sha256(queries),
    }


def _row(scene: str, split: str, index: int, workload_sha: str) -> dict:
    params = {
        name: float(position) + index / 100.0
        for position, name in enumerate(release.CALIBRATED_PARAMETERS, 1)
    }
    return {
        "replay_id": f"{split}-{index:02d}",
        "scene": scene,
        "split": split,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "real_replay": True,
        "hard_gate_passed": True,
        "coverage_complete": True,
        "technical_failure": False,
        "qps_used_for_resource_model": False,
        "workload_contract_sha256": workload_sha,
        "full_explain_fingerprint_sha256": f"fingerprint-{split}-{index}",
        "params": params,
    }


def _release_fixture(tmp_path: Path) -> tuple[Path, dict, dict]:
    source = tmp_path / "source"
    source.mkdir()
    workload_contract = {
        "benchmark": "hybench_xp",
        "mode": "native_runxp",
        "iq_selection": "uniform_random_IQ1_IQ7",
        "at_mix": [35, 25, 15, 15, 7, 3],
        "xap_xtp_ratio": "1:1",
    }
    compatibility = _compatibility(workload_contract)
    workload_sha = release.canonical_sha256(workload_contract)
    train = [_row("hybench_xp_cpu", "train", index, workload_sha) for index in range(10)]
    holdout = [
        _row("hybench_xp_cpu", "holdout", index + 20, workload_sha)
        for index in range(5)
    ]
    table = {
        name: {"cpu": 0.01, "memory": -0.02, "io": 0.03}
        for name in release.CALIBRATED_PARAMETERS
    }
    artifact = {
        "schema_version": release.ARTIFACT_CONTRACT,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_cpu",
        "source": "mysql_real_replay_calibration",
        "real_measurement": True,
        "qps_used_for_resource_model": False,
        "calibrated_parameters": list(release.CALIBRATED_PARAMETERS),
        "table": table,
        "fit": {
            "train_count": 10,
            "holdout_count": 5,
            "independent_holdout": True,
            "holdout_used_for_model_selection": False,
            "holdout_refit_into_model": False,
        },
        "formal_admission": {
            "ready": True,
            "errors": [],
            "minimum_train_count": 10,
            "minimum_holdout_count": 5,
        },
    }
    artifact["artifact_sha256"] = release.canonical_sha256(artifact)
    paths = {
        "experience_table": source / "experience_table.json",
        "train_replays": source / "train_replays.jsonl",
        "holdout_replays": source / "holdout_replays.jsonl",
        "plan_predictions": source / "plan_predictions.json",
        "workload_contract": source / "workload_contract.json",
        "fresh_calibration_config": source / "fresh_calibration_config.json",
    }
    _write_json(paths["experience_table"], artifact)
    _write_jsonl(paths["train_replays"], train)
    _write_jsonl(paths["holdout_replays"], holdout)
    _write_json(paths["plan_predictions"], {
        "complete": True,
        "baseline": _fingerprint(),
        "points": {
            row["replay_id"]: {"predicted": {"cpu": 1, "memory": 1, "io": 1}}
            for row in train + holdout
        },
    })
    _write_json(paths["workload_contract"], workload_contract)
    _write_json(paths["fresh_calibration_config"], {"train": 10, "holdout": 5})
    request = {
        "schema_version": release.REQUEST_CONTRACT,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_cpu",
        "compatibility_payload": compatibility,
    }
    for name, path in paths.items():
        request[f"{name}_path"] = str(path)
        request[f"{name}_sha256"] = release.file_sha256(path)
    request_path = source / "release_request.json"
    _write_json(request_path, request)
    return request_path, compatibility, artifact


def test_publish_verify_reuse_and_materialize(tmp_path: Path) -> None:
    request, compatibility, artifact = _release_fixture(tmp_path)
    root = tmp_path / "releases"
    manifest = release.publish_calibration_release(
        request_path=request, release_root=root
    )
    verified = release.verify_release_manifest(
        Path(manifest["manifest_path"]), compatibility=compatibility
    )
    assert verified["fit"]["train_count"] == 10
    assert verified["fit"]["holdout_count"] == 5
    decision = release.resolve_release_reuse(
        compatibility=compatibility,
        observed_full_explain=_fingerprint(),
        release_root=root,
    )
    assert decision["status"] == "REUSE_CALIBRATION_RELEASE"
    assert decision["replay_count_skipped"] == 15
    destination = tmp_path / "materialized" / "T.json"
    materialized = release.materialize_calibration_release(
        manifest, destination=destination
    )
    assert materialized["materialized_sha256"] == release.file_sha256(destination)
    assert release.read_json(destination)["artifact_sha256"] == artifact["artifact_sha256"]


def test_explain_or_compatibility_mismatch_falls_back_to_fresh(tmp_path: Path) -> None:
    request, compatibility, _artifact = _release_fixture(tmp_path)
    root = tmp_path / "releases"
    release.publish_calibration_release(request_path=request, release_root=root)
    changed_plan = release.resolve_release_reuse(
        compatibility=compatibility,
        observed_full_explain=_fingerprint(changed=True),
        release_root=root,
    )
    assert changed_plan["status"] == "FRESH_CALIBRATION_REQUIRED"
    assert changed_plan["reason"] == "full_explain_fingerprint_mismatch"
    assert changed_plan["changed_query_ids"] == ["IQ4"]
    changed_compatibility = copy.deepcopy(compatibility)
    changed_compatibility["resource_capacity"]["memory_bytes"] += 4096
    incompatible = release.resolve_release_reuse(
        compatibility=changed_compatibility,
        observed_full_explain=_fingerprint(),
        release_root=root,
    )
    assert incompatible["status"] == "FRESH_CALIBRATION_REQUIRED"
    assert incompatible["reason"] == "compatible_release_not_found"
    assert incompatible["replay_count_required"] == 15


def test_old_non_measurement_t_and_overlapping_holdout_are_rejected(tmp_path: Path) -> None:
    old = {
        "schema_version": "mysql_experience_table_t_v1",
        "real_measurement": False,
        "table": {},
    }
    with pytest.raises(RuntimeError, match="10\\+5"):
        release.validate_calibration_artifact(old)

    request, _compatibility, _artifact = _release_fixture(tmp_path)
    value = release.read_json(request)
    train_rows = release.read_jsonl(Path(value["train_replays_path"]))
    holdout_path = Path(value["holdout_replays_path"])
    holdout_rows = release.read_jsonl(holdout_path)
    holdout_rows[0]["params"] = copy.deepcopy(train_rows[0]["params"])
    _write_jsonl(holdout_path, holdout_rows)
    value["holdout_replays_sha256"] = release.file_sha256(holdout_path)
    _write_json(request, value)
    with pytest.raises(RuntimeError, match="overlaps training"):
        release.publish_calibration_release(
            request_path=request, release_root=tmp_path / "releases"
        )


def test_tampered_release_evidence_is_rejected(tmp_path: Path) -> None:
    request, _compatibility, _artifact = _release_fixture(tmp_path)
    manifest = release.publish_calibration_release(
        request_path=request, release_root=tmp_path / "releases"
    )
    release_dir = Path(manifest["manifest_path"]).parent
    with (release_dir / "train_replays.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(RuntimeError, match="release evidence differs"):
        release.verify_release_manifest(Path(manifest["manifest_path"]))
