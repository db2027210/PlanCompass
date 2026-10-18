from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from shell import mysql_xp_postcapture_promotion_v1 as promotion


def _write(path: Path, value: Any, *, sidecar: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n",
        encoding="utf-8",
    )
    if sidecar:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_name(path.name + ".sha256").write_text(
            f"{digest}  {path.name}\n", encoding="ascii"
        )
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture_fixture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    token = "XP_CAPTURE_REAL_FIXTURE"
    artifacts: dict[str, Path] = {}
    for name in (
        "trace",
        "trace_manifest",
        "runtime_authority",
        "capture_authorization",
    ):
        artifacts[name] = _write(tmp_path / f"{name}.json", {"fixture": name})
    artifacts["capture_spec"] = _write(
        tmp_path / "capture_spec.json",
        {
            "schema_version": "mysql_xp_fresh_local_capture_spec_v1",
            "runtime_bundle_id": promotion.SOURCE_BUNDLE_ID,
            "launch_token": token,
            "capture_only": True,
            "outer_execution_authorized": False,
            "frozen_resource_envelope": {
                "io_read_bps": 1205862400,
                "io_write_bps": 1205862400,
            },
        },
    )
    artifacts["capture_completion"] = _write(
        tmp_path / "capture_completion.json",
        {
            "schema_version": "mysql_xp_capture_completion_v1",
            "status": "COMPLETE",
            "launch_token": token,
            "runtime_bundle_id": promotion.SOURCE_BUNDLE_ID,
            "process_exit_code": 0,
            "trace_sealed": True,
            "worker_future_exception_count": 0,
            "technical_failure_count": 0,
            "worker_futures": [
                {
                    "worker": worker,
                    "status": "SUCCESS",
                    "exception": None,
                    "cancelled": False,
                }
                for worker in promotion.REQUIRED_WORKERS
            ],
            "artifact_sha256": {
                name: _sha(path)
                for name, path in artifacts.items()
                if name
                in {
                    "trace",
                    "trace_manifest",
                    "runtime_authority",
                    "capture_authorization",
                    "capture_spec",
                }
            },
        },
    )
    artifacts["cleanup"] = _write(
        tmp_path / "cleanup.json",
        {
            "schema_version": "mysql_xp_capture_cleanup_v1",
            "status": "PASS",
            "launch_token": token,
            "runtime_bundle_id": promotion.SOURCE_BUNDLE_ID,
            "graceful_quiescence_seconds": 120,
            "cleanup_complete": True,
            "zero_pressure_verified": True,
            "workload_process_absent": True,
            "mysql_container_absent": True,
            "worker_future_exception_count": 0,
            "trace_sha256": _sha(artifacts["trace"]),
        },
    )
    manifest = {
        "schema_version": "mysql_xp_postcapture_input_v1",
        "evidence_origin": "synthetic_fixture",
        "synthetic": True,
        "status": "CAPTURE_COMPLETE",
        "runtime_bundle_id": promotion.SOURCE_BUNDLE_ID,
        "launch_token": token,
        "capture_only": True,
        "r0_authorized": False,
        "accepted_round_authorized": False,
        "files": {
            name: {"path": str(path), "sha256": _sha(path)}
            for name, path in artifacts.items()
        },
    }
    return _write(tmp_path / "capture_evidence.json", manifest), manifest


def _trace_validation(token: str) -> dict[str, Any]:
    events = []
    for at_type in promotion.REQUIRED_AT:
        events.extend(
            [
                {
                    "event_type": "statement",
                    "outcome": "success",
                    "transaction_type": at_type,
                },
                {
                    "event_type": "commit",
                    "outcome": "success",
                    "transaction_type": at_type,
                },
            ]
        )
    return {
        "capture_authorized": True,
        "execution_authorized": False,
        "reasons": [],
        "run_id": token,
        "events": events,
    }


def _validator_for(token: str):
    return lambda *_args: _trace_validation(token)


def test_source_bundle_and_shared_contract_are_exact() -> None:
    manifest = promotion.verify_runtime_bundle(
        promotion.SOURCE_BUNDLE, source=True
    )
    contract = promotion._contract()
    assert manifest["bundle_id"] == promotion.SOURCE_BUNDLE_ID
    assert contract["candidate_space"]["xp"] == {
        "parameter_count": 7,
        "nonempty_subset_count": 127,
    }
    assert (
        promotion.sha256_file(promotion.FORMAL10_CONTRACT)
        == promotion.FORMAL10_CONTRACT_SHA256
    )


def test_capture_seal_accepts_complete_synthetic_fixture_for_tests_only(
    tmp_path: Path,
) -> None:
    path, manifest = _capture_fixture(tmp_path)
    result = promotion.audit_capture_evidence(
        promotion.SOURCE_BUNDLE,
        path,
        validator=_validator_for(manifest["launch_token"]),
        allow_synthetic_for_test=True,
    )
    assert result["at_commit_coverage"] == {
        name: True for name in promotion.REQUIRED_AT
    }
    assert result["at_statement_coverage"]["AT4"] == 1
    assert result["at_statement_coverage"]["AT5"] == 1
    assert result["worker_future_exception_count"] == 0
    assert result["formal_r0_launch_authorized"] is True
    assert result["formal10_launch_authorized"] is False


def test_synthetic_capture_can_never_authorize_public_promotion(
    tmp_path: Path,
) -> None:
    path, manifest = _capture_fixture(tmp_path)
    with pytest.raises(promotion.PromotionError, match="non-synthetic"):
        promotion.audit_capture_evidence(
            promotion.SOURCE_BUNDLE,
            path,
            validator=_validator_for(manifest["launch_token"]),
        )


@pytest.mark.parametrize(
    ("role", "mutation", "message"),
    [
        (
            "capture_completion",
            lambda doc: doc.update(worker_future_exception_count=1),
            "worker Future",
        ),
        (
            "capture_completion",
            lambda doc: doc["worker_futures"][0].update(
                status="FAILED", exception="injected"
            ),
            "worker Future",
        ),
        (
            "cleanup",
            lambda doc: doc.update(zero_pressure_verified=False),
            "cleanup",
        ),
        (
            "cleanup",
            lambda doc: doc.update(launch_token="other"),
            "cleanup",
        ),
    ],
)
def test_capture_promotion_fails_closed_on_worker_cleanup_or_token_drift(
    tmp_path: Path, role: str, mutation, message: str,
) -> None:
    path, manifest = _capture_fixture(tmp_path)
    evidence = json.loads(path.read_text(encoding="utf-8"))
    target = Path(evidence["files"][role]["path"])
    document = json.loads(target.read_text(encoding="utf-8"))
    mutation(document)
    _write(target, document)
    evidence["files"][role]["sha256"] = _sha(target)
    _write(path, evidence)
    with pytest.raises(promotion.PromotionError, match=message):
        promotion.audit_capture_evidence(
            promotion.SOURCE_BUNDLE,
            path,
            validator=_validator_for(manifest["launch_token"]),
            allow_synthetic_for_test=True,
        )


def test_capture_promotion_requires_at4_and_at5_real_statements(
    tmp_path: Path,
) -> None:
    path, manifest = _capture_fixture(tmp_path)
    validation = _trace_validation(manifest["launch_token"])
    validation["events"] = [
        row
        for row in validation["events"]
        if not (
            row["event_type"] == "statement"
            and row["transaction_type"] in {"AT4", "AT5"}
        )
    ]
    with pytest.raises(promotion.PromotionError, match="AT1-AT6"):
        promotion.audit_capture_evidence(
            promotion.SOURCE_BUNDLE,
            path,
            validator=lambda *_args: validation,
            allow_synthetic_for_test=True,
        )


def _epochs(intervals: list[float]) -> list[int]:
    now = 0.0
    result = []
    for interval in intervals:
        now += interval
        result.append(round(now * 1000))
    return result


def test_strict_elapsed_cadence_accepts_exact_and_one_bounded_pair() -> None:
    exact = promotion.strict_elapsed_cadence_audit(
        _epochs([1.0] * 180),
        start_epoch_ms=0,
        end_epoch_ms=180000,
    )
    assert exact["real_elapsed_coverage_ratio"] == 1.0
    intervals = [1.0] * 180
    intervals[80:82] = [1.89, 0.11]
    delayed = promotion.strict_elapsed_cadence_audit(
        _epochs(intervals),
        start_epoch_ms=0,
        end_epoch_ms=180000,
    )
    assert delayed["compensated_catchup_count"] == 1
    assert delayed["compensated_catchups"][0][
        "combined_interval_seconds"
    ] == pytest.approx(2.0)


@pytest.mark.parametrize(
    "intervals",
    [
        [1.0] * 80 + [2.8, 0.01] + [1.0] * 97,
        [0.1, 0.9] * 90,
        [1.0] * 80 + [1.89, 0.11, 0.11] + [1.0] * 96,
        [1.0] * 80 + [1.89, 0.30] + [1.0] * 97,
        [1.0] * 160,
    ],
)
def test_strict_elapsed_cadence_rejects_gap_fake_density_and_missing_samples(
    intervals: list[float],
) -> None:
    epochs = _epochs(intervals)
    epochs = [value for value in epochs if value <= 180000]
    with pytest.raises(promotion.PromotionError):
        promotion.strict_elapsed_cadence_audit(
            epochs, start_epoch_ms=0, end_epoch_ms=180000
        )


def test_runtime_patch_replaces_count_only_coverage_with_elapsed_audits() -> None:
    source_path = (
        promotion.SOURCE_BUNDLE
        / "orchestration/formal_hybench_continuous_session.py"
    )
    patched = promotion.patch_hybench_sampling(
        source_path.read_text(encoding="utf-8")
    )
    assert promotion.RUNTIME_PATCH_MARKER in patched
    assert '"resource_cadence_audit": resource_cadence_audit' in patched
    assert '"progress_cadence_audit": progress_cadence_audit' in patched
    assert (
        '"resource_sample_coverage": min(1.0, len(resources)'
        not in patched
    )
    assert promotion.patch_hybench_sampling(patched) == patched


def _cadence() -> dict[str, Any]:
    return {
        "schema_version": "mysql_xp_strict_elapsed_cadence_audit_v1",
        "status": "PASS",
        "policy": promotion.RUNTIME_PATCH_MARKER,
        "real_elapsed_coverage_ratio": 1.0,
    }


def _gradient(active_subset: tuple[str, ...]) -> dict[str, Any]:
    slopes = {name: {"cpu": 1.0} for name in active_subset}
    table = {name: {"cpu": 0.5} for name in active_subset}
    confidence = {name: 0.8 for name in active_subset}
    gradients = {name: 0.4 for name in active_subset}
    return {
        "gradient_source": "original_dScore_dU_times_T_times_confidence",
        "feature_ratio": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
        "predicted_resource": {"cpu": 0.4, "memory": 0.8, "io": 0.9},
        "utilization": {"cpu": 0.4, "memory": 0.8, "io": 0.9},
        "dScore/dU": slopes,
        "T": table,
        "confidence": confidence,
        "g_j": gradients,
        "eta": 0.1,
        "delta_param": {
            name: {
                "before": 1.0,
                "proposed_before_clip": 1.04,
                "after_clip": 1.04,
                "absolute": 0.04,
                "relative": 0.04,
                "clipped": False,
            }
            for name in active_subset
        },
        "candidate_score": 1.0,
    }


def _fingerprint() -> dict[str, Any]:
    return {
        "l1": {
            "operator_types": ["scan"],
            "join_order": [],
            "scan_types": ["range"],
            "parallel_structure": {"workers": 1},
        },
        "l2": {
            "workers": 1,
            "hash_batches": 1,
            "sort_or_spill": "none",
            "bitmap_lossy_pages": 0,
            "intermediate_result_sizes": [1],
        },
        "l3": {
            "shared_local_temp_read_write": {
                "shared_read": 1,
                "local_read": 0,
                "temp_read": 0,
            },
            "io_wait": 0.1,
        },
    }


def _debug_fixture(tmp_path: Path) -> tuple[Path, dict[str, Path], list[dict[str, Any]]]:
    bundle_id = promotion.SOURCE_BUNDLE_ID
    attempt = "DEBUG_ATTEMPT_FIXTURE"
    rows = []
    ordinal = 0
    for size in range(1, 8):
        for subset in itertools.combinations(promotion.ACTIVE_PARAMETERS, size):
            ordinal += 1
            rows.append(
                {
                    "active_subset": list(subset),
                    "sealed_candidate_identity": f"candidate-{ordinal:03d}",
                    "candidate_score": float(1000 - ordinal),
                    "gradient_events": [_gradient(subset)],
                }
            )
    sealed = [
        {
            "rank": rank,
            "sealed_candidate_identity": row["sealed_candidate_identity"],
            "candidate_score": row["candidate_score"],
        }
        for rank, row in enumerate(rows[:5], 1)
    ]
    files = {
        "formal_r0_authority": _write(
            tmp_path / "r0.json",
            {
                "schema_version": "mysql_xp_formal_point_gate_v1",
                "bundle_id": bundle_id,
                "status": "FORMAL_R0_PASSED",
                "fresh_in_band": True,
                "capture_or_historical_r0": False,
                "r0_gate_passed": True,
                "correctness_passed": True,
                "stability_passed": True,
                "accepted_round_authorized": False,
                "resource_cadence_audit": _cadence(),
                "progress_cadence_audit": _cadence(),
            },
        ),
        "candidate_inventory": _write(
            tmp_path / "candidates.json",
            {
                "schema_version":
                    "mysql_xp_all_subset_candidate_inventory_v1",
                "runtime_bundle_id": bundle_id,
                "attempt_token": attempt,
                "status": "COMPLETE",
                "active_parameters": list(promotion.ACTIVE_PARAMETERS),
                "expected_nonempty_subsets": 127,
                "completed_nonempty_subsets": 127,
                "ranking": "global_final_candidate_score",
                "candidates": rows,
            },
        ),
        "top5_seal": _write(
            tmp_path / "top5.json",
            {
                "schema_version": "mysql_xp_fixed_top5_seal_v1",
                "runtime_bundle_id": bundle_id,
                "attempt_token": attempt,
                "status": "SEALED",
                "fixed": True,
                "replay_top_k": 5,
                "sealed_candidates": sealed,
            },
        ),
        "replay_audit": _write(
            tmp_path / "replays.json",
            {
                "schema_version": "mysql_xp_fixed_top5_replay_audit_v1",
                "runtime_bundle_id": bundle_id,
                "attempt_token": attempt,
                "status": "COMPLETE",
                "real_replay": True,
                "fixed_replay_set_unchanged": True,
                "replays": [
                    {
                        "rank": rank,
                        "sealed_candidate_identity":
                            row["sealed_candidate_identity"],
                        "attempt_token": f"rank-{rank}-token",
                        "real_replay": True,
                        "status": "COMPLETE",
                        "failed": False,
                        "point_journal_present": True,
                        "point_journal_complete": True,
                        "completion_present": True,
                        "measurement_present": True,
                        "resource_cadence_audit": _cadence(),
                        "progress_cadence_audit": _cadence(),
                        "three_level_fingerprint": _fingerprint(),
                    }
                    for rank, row in enumerate(sealed, 1)
                ],
            },
        ),
        "ranking_audit": _write(
            tmp_path / "ranking.json",
            {
                "schema_version": "mysql_xp_ranking_audit_v1",
                "runtime_bundle_id": bundle_id,
                "attempt_token": attempt,
                "status": "PASS",
                "predicted_ranking_complete": True,
                "real_ranking_complete": True,
                "compared_fixed_top5_only": True,
                "unresolved_sampling_gradient_estimation_fingerprint_issues": [],
            },
        ),
        "cleanup": _write(
            tmp_path / "cleanup.json",
            {
                "schema_version": "mysql_xp_debug_cleanup_v1",
                "runtime_bundle_id": bundle_id,
                "attempt_token": attempt,
                "status": "PASS",
                "zero_pressure_verified": True,
                "exact_costs_restored": True,
                "atomic_checkpoint_committed": True,
            },
        ),
    }
    manifest = {
        "schema_version": "mysql_xp_debug_round_input_v1",
        "evidence_origin": "synthetic_fixture",
        "synthetic": True,
        "status": "DEBUG_ROUND_COMPLETE",
        "runtime_bundle_id": bundle_id,
        "attempt_token": attempt,
        "accepted_rounds": 0,
        "debug_round_counts_as_accepted": False,
        "files": {
            name: {"path": str(path), "sha256": _sha(path)}
            for name, path in files.items()
        },
    }
    return _write(tmp_path / "debug_evidence.json", manifest), files, rows


def test_complete_debug_round_is_formal10_eligible_but_starts_at_zero(
    tmp_path: Path,
) -> None:
    evidence, _files, _rows = _debug_fixture(tmp_path)
    authority = promotion.audit_debug_round(
        promotion.SOURCE_BUNDLE,
        evidence,
        allow_synthetic_for_test=True,
    )
    assert authority["candidate_count"] == 127
    assert authority["sealed_ranks"] == [1, 2, 3, 4, 5]
    assert authority["real_replay_count"] == 5
    assert authority["initial_accepted_rounds"] == 0
    assert authority["target_accepted_rounds"] == 10
    assert authority["accepted_round_authorized"] is False


def test_debug_round_rejects_started_replay_without_point_journal(
    tmp_path: Path,
) -> None:
    evidence, files, _rows = _debug_fixture(tmp_path)
    document = json.loads(files["replay_audit"].read_text(encoding="utf-8"))
    document["replays"][0]["point_journal_present"] = False
    _write(files["replay_audit"], document)
    manifest = json.loads(evidence.read_text(encoding="utf-8"))
    manifest["files"]["replay_audit"]["sha256"] = _sha(files["replay_audit"])
    _write(evidence, manifest)
    with pytest.raises(promotion.PromotionError, match="per-rank"):
        promotion.audit_debug_round(
            promotion.SOURCE_BUNDLE,
            evidence,
            allow_synthetic_for_test=True,
        )


def test_debug_round_rejects_gradient_formula_drift(
    tmp_path: Path,
) -> None:
    evidence, files, _rows = _debug_fixture(tmp_path)
    document = json.loads(
        files["candidate_inventory"].read_text(encoding="utf-8")
    )
    parameter = document["candidates"][0]["active_subset"][0]
    document["candidates"][0]["gradient_events"][0]["g_j"][parameter] = 0.5
    _write(files["candidate_inventory"], document)
    manifest = json.loads(evidence.read_text(encoding="utf-8"))
    manifest["files"]["candidate_inventory"]["sha256"] = _sha(
        files["candidate_inventory"]
    )
    _write(evidence, manifest)
    with pytest.raises(promotion.PromotionError, match="raw"):
        promotion.audit_debug_round(
            promotion.SOURCE_BUNDLE,
            evidence,
            allow_synthetic_for_test=True,
        )


def test_debug_round_rejects_missing_l3_or_unresolved_sampling_issue(
    tmp_path: Path,
) -> None:
    evidence, files, _rows = _debug_fixture(tmp_path)
    document = json.loads(files["replay_audit"].read_text(encoding="utf-8"))
    document["replays"][2]["three_level_fingerprint"]["l3"] = {}
    _write(files["replay_audit"], document)
    manifest = json.loads(evidence.read_text(encoding="utf-8"))
    manifest["files"]["replay_audit"]["sha256"] = _sha(files["replay_audit"])
    _write(evidence, manifest)
    with pytest.raises(promotion.PromotionError, match="L3"):
        promotion.audit_debug_round(
            promotion.SOURCE_BUNDLE,
            evidence,
            allow_synthetic_for_test=True,
        )


def test_content_addressed_publication_is_transactional_and_idempotent(
    tmp_path: Path,
) -> None:
    authority = {
        "schema_version": "synthetic_publication_fixture_v1",
        "synthetic": True,
        "launch_authorized": False,
    }
    first = promotion._publish_tree(
        promotion.SOURCE_BUNDLE,
        output_root=tmp_path / "published",
        stage="formal_debug",
        authority=authority,
    )
    second = promotion._publish_tree(
        promotion.SOURCE_BUNDLE,
        output_root=tmp_path / "published",
        stage="formal_debug",
        authority=authority,
    )
    assert first == second
    assert first.name == promotion.canonical_sha256(
        {
            "schema_version": "mysql_xp_promoted_runtime_identity_v1",
            "stage": "formal_debug",
            "source_bundle_id": promotion.SOURCE_BUNDLE_ID,
            "source_manifest_sha256": promotion.sha256_file(
                promotion.SOURCE_BUNDLE / "manifest.json"
            ),
            "authority_sha256": promotion.canonical_sha256(authority),
            "formal10_contract_sha256": promotion.FORMAL10_CONTRACT_SHA256,
            "runtime_patch": promotion.RUNTIME_PATCH_MARKER,
        }
    )
    manifest = promotion.verify_runtime_bundle(first)
    assert manifest["publication"] == {
        "content_addressed": True,
        "transactional": True,
        "idempotent": True,
        "immutable_after_publish": True,
    }
    assert manifest["accepted_round_authorized"] is False
    assert not list((tmp_path / "published").glob(".mysql_xp_promotion.*"))
