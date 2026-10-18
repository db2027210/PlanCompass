from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shell import promote_mysql_job_tpch_formal10 as promotion
from shell import seal_mysql_job_tpch_formal10_evidence as sealer


def raw_measurement(run_root: Path) -> dict:
    point = run_root / "production_points/replay-1"
    point.mkdir(parents=True)
    resource = point / "resource_samples.jsonl"
    resource.write_text("{}\n", encoding="utf-8")
    start_ms = 1_000_000
    end_ms = start_ms + 180_000
    rows = []
    for sampler_index in (0, 1):
        for offset in range(180):
            rows.append({
                "epoch_ms": start_ms + offset * 1000,
                "sampler_index": sampler_index,
                "completed_total": offset,
                "source": "database_status_counter",
            })
    (point / "database_samples.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return {
        "correctness_passed": True,
        "stable": True,
        "sample_coverage": 1.0,
        "db_qps_source": "database_status_counter",
        "db_qps": 10.0,
        "qps": 10.0,
        "oom": False,
        "mysql_restart": False,
        "container_restart": False,
        "evidence": {
            "per_second_samples": str(resource),
            "stability_windows_and_coverage": {
                "selected_observation": {
                    "resource_sample_coverage": 1.0,
                    "database_throughput": {
                        "source": "database_status_counter",
                        "reliable": True,
                        "requested_start_epoch_ms": start_ms,
                        "requested_end_epoch_ms": end_ms,
                        "start_boundary_distance_ms": 1000,
                        "end_boundary_distance_ms": 1000,
                    },
                }
            },
        },
    }


def test_normalizer_derives_both_coverages_from_runtime_evidence(
    tmp_path: Path,
) -> None:
    raw = raw_measurement(tmp_path)
    completion = copy.deepcopy(raw)
    completion["outer_gate"] = {"eligible": False}
    normalized = sealer.normalize_measurement(
        raw,
        {"candidate_final_validation": completion},
        {"fingerprint": "test"},
        rank=1,
        token="token-1",
        run_root=tmp_path,
        current_sha256="a" * 64,
    )
    assert normalized["resource_sample_coverage"] == 1.0
    assert normalized["database_counter_coverage"] == 1.0
    assert (
        normalized["database_counter_coverage_evidence"]["passed"]
        is True
    )
    assert normalized["accepted_round_credit"] == 0
    assert normalized["failed_replay"] is False


def test_normalizer_fails_closed_on_database_counter_loss(
    tmp_path: Path,
) -> None:
    raw = raw_measurement(tmp_path)
    database = (
        tmp_path
        / "production_points/replay-1/database_samples.jsonl"
    )
    rows = [
        json.loads(line)
        for line in database.read_text(encoding="utf-8").splitlines()
    ]
    rows = [
        row for row in rows
        if not (
            row["sampler_index"] == 1
            and 40 <= (row["epoch_ms"] - 1_000_000) // 1000 < 60
        )
    ]
    database.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    with pytest.raises(
        sealer.SealingError,
        match=r"database counter sampler 1 coverage failed",
    ):
        sealer.normalize_measurement(
            raw,
            {"candidate_final_validation": copy.deepcopy(raw)},
            {"fingerprint": "test"},
            rank=1,
            token="token-1",
            run_root=tmp_path,
            current_sha256="a" * 64,
        )


def test_debug_run_config_is_exactly_derived_and_fail_closed(
    tmp_path: Path,
) -> None:
    bundle = (
        Path(__file__).resolve().parents[1]
        / "artifacts/mysql_tpch_v36_debug"
        / "1332ab2e571e981761fc957cd65c36ff6e782663eb70ba4c8952ddc815f14dc4"
    )
    base = json.loads((bundle / "config.json").read_text(encoding="utf-8"))
    base["output"]["directory"] = str(tmp_path)
    output = tmp_path / "20260729T120000Z"
    derived = promotion.expected_debug_run_config(base, output)
    promotion.validate_debug_run_config(base, derived, output)
    changed = copy.deepcopy(derived)
    changed["mysql"]["port"] += 1
    with pytest.raises(
        promotion.PromotionError,
        match=r"not the frozen derived config",
    ):
        promotion.validate_debug_run_config(base, changed, output)


def test_runtime_journal_hash_chain_and_embedded_measurement(
    tmp_path: Path,
) -> None:
    token = "token-1"
    attempt = tmp_path / token
    attempt.mkdir()
    initial = {
        "resume_token": token,
        "accounting_token": token,
        "pending": {"stage": "initial_top_k"},
        "sequence": 1,
        "stage": "initialized",
        "previous_sha256": None,
    }
    first = attempt / "0001_initialized.json"
    first.write_bytes(promotion.compact_canonical(initial))
    first_sha = promotion.sha(first)
    first.with_name(first.name + ".sha256").write_text(
        first_sha + "\n", encoding="ascii"
    )
    complete = {
        "resume_token": token,
        "accounting_token": token,
        "pending": None,
        "measurement": {"db_qps": 1.0},
        "measurement_complete": True,
        "recovery_verified": True,
        "lease_released": True,
        "sequence": 2,
        "stage": "point_complete",
        "previous_sha256": first_sha,
    }
    second = attempt / "0002_point_complete.json"
    second.write_bytes(promotion.compact_canonical(complete))
    second_sha = promotion.sha(second)
    second.with_name(second.name + ".sha256").write_text(
        second_sha + "\n", encoding="ascii"
    )
    current = attempt / "current.json"
    current.write_bytes(second.read_bytes())
    current.with_name("current.json.sha256").write_text(
        second_sha + "\n", encoding="ascii"
    )
    current_path, observed_initial, observed_current = (
        promotion.verify_runtime_journal(tmp_path, token, "test journal")
    )
    assert current_path == current
    assert observed_initial == initial
    assert observed_current["measurement"] == {"db_qps": 1.0}
    first.with_name(first.name + ".sha256").write_text(
        "0" * 64 + "\n", encoding="ascii"
    )
    with pytest.raises(
        promotion.PromotionError,
        match=r"history checksum differs",
    ):
        promotion.verify_runtime_journal(tmp_path, token, "test journal")


def test_promotion_seal_closure_is_exact_and_rejects_extra_files(
    tmp_path: Path,
) -> None:
    seal_root = tmp_path / ("b" * 64)
    seal_root.mkdir()
    evidence = seal_root / "promotion_evidence_manifest.json"
    evidence.write_bytes(promotion.canonical({"status": "SEALED"}))
    evidence_sidecar = seal_root / "promotion_evidence_manifest.json.sha256"
    evidence_sidecar.write_text(
        f"{promotion.sha(evidence)}  {evidence.name}\n",
        encoding="ascii",
    )
    files = {
        evidence.name: promotion.sha(evidence),
        evidence_sidecar.name: promotion.sha(evidence_sidecar),
    }
    closure = seal_root / "seal_closure.json"
    closure.write_bytes(promotion.canonical({
        "schema_version":
            "mysql_algorithm_c_promotion_seal_closure_v1",
        "seal_id": seal_root.name,
        "transactional_publication": True,
        "idempotent": True,
        "immutable": True,
        "source_authority": {"debug_bundle_id": "test"},
        "files": files,
    }))
    observed_root, observed_sha = promotion.verify_promotion_seal(
        evidence
    )
    assert observed_root == seal_root
    assert observed_sha == promotion.sha(closure)
    (seal_root / "unexpected").write_text("x", encoding="utf-8")
    with pytest.raises(
        promotion.PromotionError,
        match=r"closure files differ",
    ):
        promotion.verify_promotion_seal(evidence)
