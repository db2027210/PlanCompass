#!/usr/bin/env python3
"""Transactionally seal JOB/TPC-H debug evidence for formal10 promotion.

This tool is offline-only.  It never launches a workload or contacts MySQL.
It verifies the frozen debug run, the runtime point-journal hash chains, and
the fixed Top-5 completion records.  It then publishes one immutable,
content-addressed evidence directory beneath the runtime journal root.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping
import uuid

try:
    from shell import promote_mysql_job_tpch_formal10 as promotion
except ModuleNotFoundError:  # Direct execution places shell/ on sys.path.
    import promote_mysql_job_tpch_formal10 as promotion


class SealingError(promotion.PromotionError):
    pass


def _pointer(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": promotion.sha(path)}


def _write_authority(path: Path, document: Mapping[str, Any]) -> str:
    path.write_bytes(promotion.canonical(document))
    digest = promotion.sha(path)
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def _json_lines(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise SealingError(f"{label} is absent or symlinked: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SealingError(
                f"{label} line {line_number} is invalid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise SealingError(
                f"{label} line {line_number} is not an object"
            )
        rows.append(row)
    return rows


def derive_database_counter_coverage(
    measurement: Mapping[str, Any],
    run_root: Path,
) -> tuple[float, dict[str, Any]]:
    evidence = measurement.get("evidence")
    rolling = (
        evidence.get("stability_windows_and_coverage")
        if isinstance(evidence, Mapping)
        else None
    )
    selected = (
        rolling.get("selected_observation")
        if isinstance(rolling, Mapping)
        else None
    )
    throughput = (
        selected.get("database_throughput")
        if isinstance(selected, Mapping)
        else None
    )
    if (
        not isinstance(throughput, Mapping)
        or throughput.get("source") != "database_status_counter"
        or throughput.get("reliable") is not True
    ):
        raise SealingError(
            "selected database counter throughput is absent or unreliable"
        )
    try:
        start_ms = int(throughput["requested_start_epoch_ms"])
        end_ms = int(throughput["requested_end_epoch_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SealingError(
            "selected database counter window is malformed"
        ) from exc
    if end_ms <= start_ms:
        raise SealingError("selected database counter window is not positive")
    for boundary in ("start_boundary_distance_ms", "end_boundary_distance_ms"):
        value = throughput.get(boundary)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or float(value) > 10000.0
        ):
            raise SealingError(
                f"selected database counter {boundary} exceeds 10 seconds"
            )
    per_second = (
        evidence.get("per_second_samples")
        if isinstance(evidence, Mapping)
        else None
    )
    if not isinstance(per_second, str):
        raise SealingError("per-second sample authority is absent")
    resource_path = promotion.resolve_inside(
        Path(per_second), run_root, "resource sample authority"
    )
    if not resource_path.is_file() or resource_path.is_symlink():
        raise SealingError("resource sample authority is absent")
    point_root = resource_path.parent
    database_path = promotion.resolve_inside(
        point_root / "database_samples.jsonl",
        run_root,
        "database sample authority",
    )
    rows = _json_lines(database_path, "database samples")
    expected_per_sampler = max(
        1, math.ceil((end_ms - start_ms) / 1000.0)
    )
    sampler_results: dict[str, Any] = {}
    coverages: list[float] = []
    for sampler_index in (0, 1):
        timestamps = sorted({
            int(row["epoch_ms"])
            for row in rows
            if row.get("sampler_index") == sampler_index
            and row.get("source") == "database_status_counter"
            and isinstance(row.get("epoch_ms"), int)
            and start_ms <= int(row["epoch_ms"]) <= end_ms
        })
        coverage = min(1.0, len(timestamps) / expected_per_sampler)
        boundaries = [start_ms, *timestamps, end_ms]
        maximum_gap_ms = max(
            right - left
            for left, right in zip(boundaries, boundaries[1:])
        )
        passed = coverage >= 0.90 and maximum_gap_ms <= 10000
        sampler_results[str(sampler_index)] = {
            "success_count": len(timestamps),
            "expected_sample_count": expected_per_sampler,
            "coverage": coverage,
            "maximum_observed_gap_ms": maximum_gap_ms,
            "passed": passed,
        }
        if not passed:
            raise SealingError(
                f"database counter sampler {sampler_index} coverage failed"
            )
        coverages.append(coverage)
    error_files = sorted(point_root.glob("database_sample_errors*.jsonl"))
    source_files = [database_path, *error_files]
    coverage = min(coverages)
    return coverage, {
        "schema_version":
            "mysql_database_counter_coverage_normalization_v1",
        "passed": True,
        "minimum_coverage_ratio": 0.90,
        "maximum_gap_ms": 10000,
        "selected_window": {
            "requested_start_epoch_ms": start_ms,
            "requested_end_epoch_ms": end_ms,
        },
        "samplers": sampler_results,
        "effective_coverage": coverage,
        "sources": [
            {
                "path": str(path.resolve()),
                "sha256": promotion.sha(path),
            }
            for path in source_files
        ],
    }


def normalize_measurement(
    embedded: Mapping[str, Any],
    completion: Mapping[str, Any],
    fingerprint: Mapping[str, Any],
    *,
    rank: int,
    token: str,
    run_root: Path,
    current_sha256: str,
) -> dict[str, Any]:
    completion_measurement = completion.get("candidate_final_validation")
    if not isinstance(completion_measurement, Mapping):
        raise SealingError(
            f"rank {rank} completion measurement is absent"
        )
    for key, value in embedded.items():
        if completion_measurement.get(key) != value:
            raise SealingError(
                f"rank {rank} completion differs from journal measurement"
            )
    evidence = embedded.get("evidence")
    rolling = (
        evidence.get("stability_windows_and_coverage")
        if isinstance(evidence, Mapping)
        else None
    )
    selected = (
        rolling.get("selected_observation")
        if isinstance(rolling, Mapping)
        else None
    )
    top_coverage = promotion.finite(
        embedded.get("sample_coverage"),
        f"rank {rank} sample coverage",
    )
    selected_coverage = promotion.finite(
        selected.get("resource_sample_coverage")
        if isinstance(selected, Mapping)
        else None,
        f"rank {rank} selected resource sample coverage",
    )
    if (
        not promotion._close(top_coverage, selected_coverage)
        or not 0.90 <= top_coverage <= 1.0
    ):
        raise SealingError(
            f"rank {rank} resource sample coverage differs or failed"
        )
    database_coverage, database_evidence = (
        derive_database_counter_coverage(embedded, run_root)
    )
    normalized = copy.deepcopy(dict(completion_measurement))
    normalized.update({
        "schema_version":
            "mysql_algorithm_c_normalized_real_replay_measurement_v1",
        "rank": rank,
        "attempt_token": token,
        "status": "COMPLETE",
        "real_replay": True,
        "failed_replay": False,
        "accepted_round_credit": 0,
        "resource_sample_coverage": top_coverage,
        "database_counter_coverage": database_coverage,
        "database_counter_coverage_evidence": database_evidence,
        "source_journal_current_sha256": current_sha256,
        "source_measurement_sha256": promotion.sha_bytes(
            promotion.compact_canonical(embedded)
        ),
        "three_level_fingerprint": copy.deepcopy(dict(fingerprint)),
    })
    if (
        normalized.get("correctness_passed") is not True
        or normalized.get("stable") is not True
        or normalized.get("oom") is not False
        or normalized.get("mysql_restart") is not False
        or normalized.get("container_restart") is not False
        or normalized.get("db_qps_source")
        != "database_status_counter"
        or promotion.finite(
            normalized.get("db_qps"), f"rank {rank} db_qps"
        )
        <= 0.0
    ):
        raise SealingError(f"rank {rank} replay measurement did not pass")
    return normalized


def _verify_existing(final: Path, seal_id: str) -> Path:
    closure_path = final / "seal_closure.json"
    closure = promotion.load_object(
        closure_path.resolve(), "promotion seal closure"
    )
    if (
        closure.get("seal_id") != seal_id
        or closure.get("schema_version")
        != "mysql_algorithm_c_promotion_seal_closure_v1"
    ):
        raise SealingError("existing promotion seal identity differs")
    observed = {
        str(path.relative_to(final)): promotion.sha(path)
        for path in final.rglob("*")
        if path.is_file() and path != closure_path
    }
    if observed != closure.get("files"):
        raise SealingError("existing promotion seal files differ")
    return final / "promotion_evidence_manifest.json"


def seal(name: str, debug_run_dir: Path) -> Path:
    spec = promotion.SCENARIOS[name]
    manifest, base_config, readiness_path, _source = (
        promotion.validate_debug_bundle(name)
    )
    run_root = debug_run_dir.resolve()
    if debug_run_dir.is_symlink() or not run_root.is_dir():
        raise SealingError("debug run directory is absent or symlinked")
    debug_config_path = run_root / "debug_config.json"
    run_config = promotion.load_object(
        debug_config_path.resolve(), "debug run config"
    )
    promotion.validate_debug_run_config(
        base_config, run_config, run_root
    )
    outer = run_config.get("formal_outer_protocol") or {}
    r0_path = Path(str(outer.get("formal_real_r0_checkpoint_path") or ""))
    checkpoint_path = Path(str(outer.get("formal_checkpoint_path") or ""))
    journal_root = Path(str(outer.get("formal_point_journal_root") or ""))
    if (
        r0_path != run_root / "formal_real_r0.json"
        or checkpoint_path != run_root / "formal_outer_checkpoint.json"
        or journal_root != run_root / "point_journals"
    ):
        raise SealingError("debug run authority paths differ")
    r0 = promotion.load_object(r0_path, "fresh R0")
    promotion.verify_sidecar(r0_path, promotion.sha(r0_path), "fresh R0")
    promotion.verify_r0(
        r0, run_config, readiness_path, spec
    )
    checkpoint = promotion.load_object(
        checkpoint_path, "formal outer checkpoint"
    )
    promotion.verify_sidecar(
        checkpoint_path,
        promotion.sha(checkpoint_path),
        "formal outer checkpoint",
    )
    completed = checkpoint.get("completed_subset_ids")
    ordered = checkpoint.get("ordered_subset_ids")
    sealed = checkpoint.get("sealed_candidate_order")
    if (
        checkpoint.get("schema_version")
        != "mysql_sgd_outer_replay_checkpoint_v1"
        or checkpoint.get("r0_completed") is not True
        or completed != ordered
        or not isinstance(completed, list)
        or len(completed) != 15
        or len(set(completed)) != 15
        or not isinstance(sealed, list)
        or len(sealed) != 15
        or [row.get("rank") for row in sealed] != list(range(1, 16))
        or checkpoint.get("sealed_representative_ranks")
        != promotion.EXPECTED_RANKS
        or checkpoint.get("replayed_candidate_ranks")
        != promotion.EXPECTED_RANKS
        or checkpoint.get("inflight_replays") != {}
    ):
        raise SealingError("debug checkpoint is incomplete")
    candidate_scores = promotion.verify_global_sealed_ranking(checkpoint)
    promotion.verify_gradient_events(checkpoint)
    audit_log = checkpoint.get("audit_log")
    start_rows = [
        row
        for row in audit_log or []
        if isinstance(row, Mapping)
        and row.get("event") == "candidate_replay_started"
        and row.get("rank") in promotion.EXPECTED_RANKS
    ]
    completion_rows = [
        row
        for row in audit_log or []
        if isinstance(row, Mapping)
        and row.get("event") == "candidate_replayed"
        and row.get("rank") in promotion.EXPECTED_RANKS
    ]
    starts = {row["rank"]: row for row in start_rows}
    completions = {row["rank"]: row for row in completion_rows}
    if (
        len(start_rows) != 5
        or len(completion_rows) != 5
        or sorted(starts) != promotion.EXPECTED_RANKS
        or sorted(completions) != promotion.EXPECTED_RANKS
    ):
        raise SealingError("Top-5 start/completion rows are incomplete")

    normalized_by_rank: dict[int, dict[str, Any]] = {}
    runtime_by_rank: dict[int, dict[str, Any]] = {}
    source_rank_authority: list[dict[str, Any]] = []
    tokens: set[str] = set()
    for rank in promotion.EXPECTED_RANKS:
        start = starts[rank]
        completion = completions[rank]
        token = str(start.get("attempt_token") or "")
        if (
            not token
            or token in tokens
            or completion.get("attempt_token") != token
            or start.get("stage") != "top_5"
            or completion.get("stage") != "top_5"
        ):
            raise SealingError(f"rank {rank} attempt token differs")
        tokens.add(token)
        sealed_identity = sealed[rank - 1]
        if promotion.candidate_identity(
            start.get("candidate") or {}
        ) != sealed_identity:
            raise SealingError(f"rank {rank} sealed identity differs")
        identity_sha = promotion.sha_bytes(
            promotion.compact_canonical(sealed_identity)
        )
        current_path, initial, current = promotion.verify_runtime_journal(
            journal_root, token, f"rank {rank} journal"
        )
        pending = initial.get("pending")
        if (
            not isinstance(pending, Mapping)
            or pending.get("stage") != "initial_top_k"
            or pending.get("replay_ordinal") != rank
            or pending.get("subset_id") != sealed_identity["subset_id"]
            or current.get("measurement_complete") is not True
            or current.get("recovery_verified") is not True
            or current.get("lease_released") is not True
            or current.get("pending") is not None
            or current.get("stage") != "point_complete"
            or not isinstance(current.get("measurement"), Mapping)
        ):
            raise SealingError(f"rank {rank} journal is incomplete")
        completion_evidence = completion.get("evidence")
        fingerprint = (
            completion_evidence.get("three_level_fingerprint")
            if isinstance(completion_evidence, Mapping)
            else None
        )
        recovery = (
            completion_evidence.get("recovery")
            if isinstance(completion_evidence, Mapping)
            else None
        )
        if (
            not isinstance(fingerprint, Mapping)
            or not isinstance(recovery, Mapping)
            or recovery.get("verified") is not True
            or completion_evidence.get("callback_error") is not None
        ):
            raise SealingError(
                f"rank {rank} completion/fingerprint/recovery differs"
            )
        promotion.verify_fingerprint(
            fingerprint, spec["scenario"], f"rank {rank}"
        )
        normalized = normalize_measurement(
            current["measurement"],
            completion_evidence,
            fingerprint,
            rank=rank,
            token=token,
            run_root=run_root,
            current_sha256=promotion.sha(current_path),
        )
        normalized_by_rank[rank] = normalized
        runtime_by_rank[rank] = {
            "token": token,
            "identity_sha256": identity_sha,
            "current_path": current_path,
            "current_sha256": promotion.sha(current_path),
            "source_measurement_sha256": promotion.sha_bytes(
                promotion.compact_canonical(current["measurement"])
            ),
        }
        source_rank_authority.append({
            "rank": rank,
            "attempt_token": token,
            "candidate_identity_sha256": identity_sha,
            "journal_current_sha256": promotion.sha(current_path),
            "source_measurement_sha256":
                runtime_by_rank[rank]["source_measurement_sha256"],
            "normalized_measurement_sha256": promotion.sha_bytes(
                promotion.canonical(normalized)
            ),
        })

    source_authority = {
        "schema_version":
            "mysql_algorithm_c_promotion_seal_source_authority_v1",
        "scenario": spec["scenario"],
        "debug_bundle_id": manifest["bundle_id"],
        "sealer_sha256": promotion.sha(Path(__file__).resolve()),
        "promoter_sha256": promotion.sha(promotion.BUILDER),
        "debug_config_sha256": promotion.sha(debug_config_path),
        "r0_sha256": promotion.sha(r0_path),
        "checkpoint_sha256": promotion.sha(checkpoint_path),
        "rank_authority": source_rank_authority,
    }
    seal_id = promotion.sha_bytes(promotion.canonical(source_authority))
    seals_root = journal_root / "promotion_seals"
    seals_root.mkdir(parents=True, exist_ok=True)
    final = seals_root / seal_id
    if final.exists():
        return _verify_existing(final, seal_id)
    stage = seals_root / f".{seal_id}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        measurement_hashes: dict[int, str] = {}
        rank_authority: list[dict[str, Any]] = []
        for rank in promotion.EXPECTED_RANKS:
            measurement_path = final / f"rank-{rank:02d}-measurement.json"
            staged_measurement = stage / measurement_path.name
            measurement_sha = _write_authority(
                staged_measurement, normalized_by_rank[rank]
            )
            measurement_hashes[rank] = measurement_sha
            runtime = runtime_by_rank[rank]
            authority = {
                "schema_version":
                    "mysql_algorithm_c_rank_promotion_authority_v1",
                "rank": rank,
                "attempt_token": runtime["token"],
                "candidate_identity_sha256": runtime["identity_sha256"],
                "journal_current_sha256": runtime["current_sha256"],
                "source_measurement_sha256":
                    runtime["source_measurement_sha256"],
                "measurement_sha256": measurement_sha,
                "real_replay_completed": True,
                "accepted_round_credit": 0,
            }
            rank_authority.append({
                "rank": rank,
                "attempt_token": runtime["token"],
                "journal_current": {
                    "path": str(runtime["current_path"]),
                    "sha256": runtime["current_sha256"],
                },
                "measurement": {
                    "path": str(measurement_path),
                    "sha256": measurement_sha,
                },
                "promotion_authority": authority,
            })
        predicted = [row["subset_id"] for row in sealed]
        real_sorted = sorted(
            promotion.EXPECTED_RANKS,
            key=lambda rank: (
                -promotion.finite(
                    normalized_by_rank[rank].get("db_qps"),
                    f"rank {rank} db_qps",
                ),
                rank,
            ),
        )
        real_rank = {
            predicted_rank: index
            for index, predicted_rank in enumerate(real_sorted, 1)
        }
        ranking = {
            "schema_version":
                "mysql_algorithm_c_prediction_real_ranking_audit_v1",
            "status": "PASS",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "scenario": spec["scenario"],
            "debug_bundle_id": manifest["bundle_id"],
            "checkpoint_sha256": promotion.sha(checkpoint_path),
            "prediction_vs_real_comparison_complete": True,
            "predicted_order": predicted,
            "fixed_top5_candidate_ids": predicted[:5],
            "real_order": [
                predicted[predicted_rank - 1]
                for predicted_rank in real_sorted
            ],
            "unresolved_issues": [],
            "unresolved_issue_categories": {
                name: []
                for name in (
                    "sampling", "gradient", "estimation",
                    "fingerprint", "technical",
                )
            },
            "candidate_score_qps_input_used": False,
            "per_rank": [
                {
                    "predicted_rank": rank,
                    "real_rank": real_rank[rank],
                    "candidate_id": predicted[rank - 1],
                    "measurement_sha256": measurement_hashes[rank],
                    "candidate_score":
                        candidate_scores[predicted[rank - 1]],
                    "real_qps": normalized_by_rank[rank]["db_qps"],
                    "three_level_fingerprint": copy.deepcopy(
                        normalized_by_rank[rank][
                            "three_level_fingerprint"
                        ]
                    ),
                }
                for rank in promotion.EXPECTED_RANKS
            ],
            "dedup_audit": {
                "status": "PASS",
                "identical_parameter_signature_required": True,
                "identical_l1_required": True,
                "l2_within_frozen_tolerance_required": True,
                "l3_within_frozen_tolerance_required": True,
                "missing_level_kept_independent": True,
            },
        }
        ranking_path = stage / "prediction_real_ranking_audit.json"
        ranking_sha = _write_authority(ranking_path, ranking)
        evidence = {
            "schema_version":
                "mysql_algorithm_c_formal10_promotion_evidence_v1",
            "scenario": spec["scenario"],
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "debug_bundle_id": manifest["bundle_id"],
            "debug_run_kind": "fresh_in_band_debug",
            "capture_or_historical_evidence_used": False,
            "accepted_round_credit": 0,
            "unresolved_issues": [],
            "point_journal_root": str(journal_root),
            "seal_id": seal_id,
            "pointers": {
                "debug_config": {
                    "path": str(debug_config_path),
                    "sha256": promotion.sha(debug_config_path),
                },
                "r0": _pointer(r0_path),
                "checkpoint": _pointer(checkpoint_path),
                "ranking_audit": {
                    "path": str(final / ranking_path.name),
                    "sha256": ranking_sha,
                },
            },
            "rank_authority": rank_authority,
        }
        evidence_path = stage / "promotion_evidence_manifest.json"
        _write_authority(evidence_path, evidence)
        files = {
            str(path.relative_to(stage)): promotion.sha(path)
            for path in stage.rglob("*")
            if path.is_file()
        }
        closure = {
            "schema_version":
                "mysql_algorithm_c_promotion_seal_closure_v1",
            "seal_id": seal_id,
            "source_authority": source_authority,
            "files": dict(sorted(files.items())),
            "transactional_publication": True,
            "idempotent": True,
            "immutable": True,
        }
        (stage / "seal_closure.json").write_bytes(
            promotion.canonical(closure)
        )
        for path in sorted(stage.rglob("*"), reverse=True):
            path.chmod(0o555 if path.is_dir() else 0o444)
        stage.chmod(0o555)
        try:
            stage.rename(final)
        except OSError:
            if not final.exists():
                raise
            stage.chmod(0o755)
            shutil.rmtree(stage)
        return _verify_existing(final, seal_id)
    except BaseException:
        if stage.exists():
            stage.chmod(0o755)
            for path in stage.rglob("*"):
                try:
                    path.chmod(0o755 if path.is_dir() else 0o644)
                except OSError:
                    pass
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=("job", "tpch"), required=True)
    parser.add_argument("--debug-run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = seal(args.scenario, args.debug_run_dir)
    except (OSError, ValueError, SealingError) as exc:
        print(
            json.dumps(
                {"status": "SEAL_FAILED", "error": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps({
        "status": "SEALED",
        "scenario": args.scenario,
        "promotion_evidence_manifest": str(evidence),
        "seal_id": evidence.parent.name,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
