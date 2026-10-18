#!/usr/bin/env python3
"""Self-tests for the post-terminal adaptive Algorithm C auditor."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from algorithm_c_budget_policy import ACTIVE_TIME_CONTRACT, CONTRACT  # noqa: E402
from algorithm_c_execution_role import FORMAL, FORMAL_ROLE  # noqa: E402
from shell import audit_pg_algorithm_c_adaptive_formal as audit  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_references(root: Path) -> tuple[Path, Path, Path]:
    comparison = root / "comparison.json"
    archive = root / "archive.json"
    timing = root / "timing.json"
    rows = []
    for scene in audit.SCENES:
        rows.extend([
            {
                "scene": scene,
                "method": "Algorithm A",
                "final_qps": 10.0,
                "qps_improvement_pct": 10.0,
            },
            {
                "scene": scene,
                "method": "Algorithm B",
                "final_qps": 20.0,
                "qps_improvement_pct": 20.0,
            },
        ])
    write_json(comparison, {
        "absolute_qps_cross_method_comparable": False,
        "comparison_metric": "method_own_contemporaneous_baseline_qps_improvement_pct",
        "normalization_contract": "each_method_final_qps_divided_by_its_own_contemporaneous_default_qps_v1",
        "four_scene_results": rows,
    })
    write_json(archive, {
        "scenes": {
            scene: {
                "Algorithm B": {"observed_finalist": {"observed_qps": 21.0}}
            }
            for scene in audit.SCENES
        }
    })
    write_json(timing, {
        "timing_scope": {"actual_metric": audit.SEARCH_TIMING_METRIC},
        "postgresql": {
            "scenes": {
                scene: {
                    "algorithms": {
                        "Algorithm A": {"actual_search_hours": 10.0},
                        "Algorithm B": {"actual_search_hours": 5.0},
                    }
                }
                for scene in audit.SCENES
            }
        },
    })
    return comparison, archive, timing


def formal_result(scene: str) -> dict:
    return {
        "contract": CONTRACT,
        "scene": scene,
        "status": "validated",
        "timing_scope": audit.FORMAL_TIMING_SCOPE,
        "external_baseline_information_used_at_runtime": False,
        "historical_timing_comparison_is_provisional": False,
        "adaptive_policy_audit": {
            "contract": "algorithm_c_adaptive_search_policy_v4",
            "numeric_refinement_uses_progressive_low_discrepancy_windows": True,
            "same_plan_new_parameter_identity_is_not_plan_level_hard_pruned": True,
            "outer_feasibility_boundary_refinement_enabled": True,
            "outer_boundary_endpoints_use_c_current_run_evidence_only": True,
            "feasible_champion_stops_after_outer_stability": True,
            "champion_stability_uses_external_baseline_time": False,
        },
        "policy": {
            "contract": CONTRACT,
            "execution_role": FORMAL_ROLE,
            "algorithm_revision": "test_revision",
            "algorithm_design_sha256": "a" * 64,
            "adaptive_search": {"worker_count": 2},
        },
        "trial": {
            "contract": CONTRACT,
            "active_time_contract": ACTIVE_TIME_CONTRACT,
            "search_stop_reason": "adaptive_convergence",
            "algorithmic_convergence_reached": True,
            "operational_safety_guard_reached": False,
            "normal_fixed_budget_used": False,
            "valid_for_quality_or_efficiency_claim": True,
            "search_replays": 2,
            "search_valid_replays": 2,
            "search_technical_failures": 0,
            "wall_s": 500.0,
            "clean_restore_receipt": {"passed": True},
            "validation": {
                "passed": True,
                "aggregation": "balanced_worker_repetition_geometric_mean",
                "physical_replays": 8,
                "normalized_qps_geomean": 30.0,
                "bracketed_speedup_geomean": 1.5,
                "raw_finalist_qps": [30.0] * 4,
                "raw_post_baseline_qps": [20.0] * 4,
            },
        },
    }


def formal_log() -> str:
    events = []
    for index in range(2):
        events.append({
            "event": "algorithm_c_candidate_replay",
            "batch_kind": FORMAL.search_batch_kind,
            "validation": {
                "status": "ok",
                "technical_failure": False,
                "used_real_replay": True,
                "window": [1000.0 + index * 100.0, 1100.0 + index * 100.0],
            },
        })
    for batch_kind in (
        FORMAL.finalist_batch_kind(1),
        FORMAL.finalist_batch_kind(2),
        FORMAL.post_baseline_batch_kind(1),
        FORMAL.post_baseline_batch_kind(2),
    ):
        for worker in ("w1", "w2"):
            events.append({
                "event": "algorithm_c_candidate_replay",
                "batch_kind": batch_kind,
                "validation": {
                    "status": "ok",
                    "technical_failure": False,
                    "used_real_replay": True,
                    "worker_key": worker,
                    "window": [2000.0, 2100.0],
                },
            })
    events.extend([
        {
            "event": "algorithm_c_adaptive_search_decision",
            "decision": {
                "qps_used_for_inner_scoring": False,
                "baseline_runtime_or_candidate_inputs_used": False,
            },
            "external_baseline_information_used": False,
        },
        {
            "event": FORMAL.finished_event,
            "external_baseline_information_used_at_runtime": False,
        },
    ])
    return "".join(json.dumps(event) + "\n" for event in events)


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        campaign = root / "formal"
        campaign.mkdir()
        comparison, archive, timing = write_references(root)
        startup = root / "startup.json"
        write_json(startup, {
            "contract": "algorithm_c_formal_startup_stability_v1",
            "passed": True,
            "scene_rows": {scene: {} for scene in audit.SCENES},
        })
        startup_search = root / "startup_search"
        automatic_startup = startup_search / "pg_algorithm_c_adaptive_formal_test_startup_1.json"
        write_json(automatic_startup, {
            "contract": "algorithm_c_formal_startup_stability_v1",
            "campaign_root": str(campaign.resolve()),
            "passed": True,
            "scene_rows": {scene: {} for scene in audit.SCENES},
        })
        write_json(
            startup_search / "pg_algorithm_c_adaptive_formal_older_startup_1.json",
            {
                "contract": "algorithm_c_formal_startup_stability_v1",
                "campaign_root": str((root / "older_formal").resolve()),
                "passed": True,
                "scene_rows": {scene: {} for scene in audit.SCENES},
            },
        )
        resolved_startup, startup_errors = audit.resolve_startup_receipt(
            campaign,
            None,
            search_dir=startup_search,
        )
        assert resolved_startup == automatic_startup.resolve()
        assert startup_errors == []
        duplicate_startup = startup_search / "pg_algorithm_c_adaptive_formal_test_startup_2.json"
        write_json(duplicate_startup, json.loads(automatic_startup.read_text(encoding="utf-8")))
        unresolved_startup, duplicate_errors = audit.resolve_startup_receipt(
            campaign,
            None,
            search_dir=startup_search,
        )
        assert unresolved_startup is None
        assert duplicate_errors and "ambiguous" in duplicate_errors[0]
        source = ROOT / "algorithm_c_adaptive_search.py"
        manifest = {
            "contract": CONTRACT,
            "campaign_role": FORMAL_ROLE,
            "algorithm_revision": "test_revision",
            "algorithm_design_sha256": "a" * 64,
            "historical_candidates_imported": False,
            "external_baseline_runtime_inputs": [],
            "external_baseline_candidate_inputs": [],
            "external_baseline_training_sources": [],
            "qps_target_used_for_inner_search": False,
            "qps_used_for_outer_interval_control": True,
            "same_two_workers_per_scene": True,
            "uninterrupted_execution_required": True,
            "source_sha256": {source.name: file_sha256(source)},
            "scenes": {},
        }
        for scene in audit.SCENES:
            scene_dir = campaign / scene
            scene_dir.mkdir()
            model = scene_dir / "experience_table_c_only.json"
            write_json(model, {
                "fit": {
                    "source_audit": {
                        "source_counts": {f"/evidence/algorithm_c/{scene}/run.jsonl": 2}
                    }
                }
            })
            config = scene_dir / "optimizer_config.json"
            write_json(config, {
                "algorithm_c": {
                    "experience_table_path": str(model),
                    "budgeted_trial": {
                        "execution_role": FORMAL_ROLE,
                        "algorithm_revision": "test_revision",
                        "algorithm_design_sha256": "a" * 64,
                        "adaptive_search": {"worker_count": 2},
                    },
                }
            })
            manifest["scenes"][scene] = {
                "config": str(config),
                "model_sha256": file_sha256(model),
            }
        write_json(campaign / "budgeted_launch_manifest.json", manifest)
        write_json(campaign / "formal_launch_preflight.json", {
            "contract": "algorithm_c_post_pilot_formal_preflight_v1",
            "passed": True,
            "external_baseline_information_used": False,
            "normal_fixed_search_or_wall_budget": False,
        })

        # Non-terminal audits must not even try to open the intentionally absent references.
        write_json(campaign / "launcher_status.json", {"phase": "launched"})
        pending = audit.audit(
            campaign,
            comparison_path=root / "must_not_be_opened.json",
            archive_path=root / "must_not_be_opened_either.json",
            timing_path=root / "also_must_not_be_opened.json",
            startup_path=startup,
        )
        assert pending["complete"] is False
        assert pending["post_terminal_baseline_evidence_loaded"] is False

        for scene in audit.SCENES:
            write_json(campaign / scene / FORMAL.result_filename, formal_result(scene))
            (campaign / scene / "algorithm_c.jsonl").write_text(formal_log(), encoding="utf-8")
        write_json(campaign / "launcher_status.json", {
            "phase": "finished",
            "returncodes": {scene: 0 for scene in audit.SCENES},
        })
        terminal = audit.audit(
            campaign,
            comparison_path=comparison,
            archive_path=archive,
            timing_path=timing,
            startup_path=startup,
        )
        assert terminal["complete"] is True
        assert terminal["post_terminal_baseline_evidence_loaded"] is True
        assert terminal["runtime_independence"]["passed"] is True
        assert terminal["coverage_summary"]["terminal_evidence_integrity_passed"] is True
        assert terminal["historical_four_scene_quality_and_efficiency_diagnostic_met"] is True
        assert terminal["publication_claim_allowed"] is False
        assert terminal["matched_absolute_qps"]["available"] is False
        assert all(row["evidence_integrity_passed"] for row in terminal["scenes"].values())

        weakened_path = campaign / audit.SCENES[0] / FORMAL.result_filename
        weakened = json.loads(weakened_path.read_text(encoding="utf-8"))
        weakened["adaptive_policy_audit"][
            "numeric_refinement_uses_progressive_low_discrepancy_windows"
        ] = False
        write_json(weakened_path, weakened)
        weakened_terminal = audit.audit(
            campaign,
            comparison_path=comparison,
            archive_path=archive,
            timing_path=timing,
            startup_path=startup,
        )
        assert weakened_terminal["coverage_summary"][
            "terminal_evidence_integrity_passed"
        ] is False
        assert "dynamic_numeric_refinement_policy" in weakened_terminal[
            "scenes"
        ][audit.SCENES[0]]["evidence_errors"]
        write_json(weakened_path, formal_result(audit.SCENES[0]))

        nested_qps_violation = root / "nested_qps_violation.jsonl"
        nested_qps_violation.write_text(
            json.dumps({
                "event": "unit",
                "nested": {"qps_used_for_resource_prediction": True},
            }) + "\n",
            encoding="utf-8",
        )
        assert audit.scan_formal_log(nested_qps_violation)["qps_inner_violations"]

        matched = root / "matched.json"
        matched_qps = {
            "Algorithm A": 10.0,
            "Algorithm B": 20.0,
            "Algorithm C": 30.0,
        }
        write_json(matched, {
            "contract": audit.MATCHED_ABSOLUTE_QPS_CONTRACT,
            "aggregation": "balanced_worker_repetition_geometric_mean",
            "same_workers_same_workload_same_baseline_brackets": True,
            "source_common_domain_contract": audit.MATCHED_SOURCE_CONTRACT,
            "search_performed": False,
            "algorithm_c_was_terminal_before_a_b_evidence_loaded": True,
            "distinct_repetition_seeds": True,
            "source_runs": [
                {
                    "repetition": repetition,
                    "seed": 20260909 + repetition,
                    "root": str(root / f"abc_rep{repetition}"),
                    "authority_sha256": "a" * 64,
                    "state_sha256": "b" * 64,
                    "contract_sha256": "c" * 64,
                }
                for repetition in (1, 2)
            ],
            "scenes": {
                scene: {
                    method: {
                        "qps_geomean": qps,
                        "worker_count": 2,
                        "repetitions_per_worker": 2,
                        "params": {"seq_page_cost": 1.0},
                        "raw_qps": [
                            {
                                "repetition": repetition,
                                "worker": worker,
                                "qps": qps,
                                "source": str(
                                    root
                                    / f"{scene}_{method.replace(' ', '_')}_{repetition}_{worker}.json"
                                ),
                                "source_sha256": "d" * 64,
                            }
                            for repetition in (1, 2)
                            for worker in ("w1", "w2")
                        ],
                    }
                    for method, qps in matched_qps.items()
                }
                for scene in audit.SCENES
            },
        })
        publishable = audit.audit(
            campaign,
            comparison_path=comparison,
            archive_path=archive,
            timing_path=timing,
            startup_path=startup,
            matched_absolute_qps_path=matched,
        )
        assert publishable["publication_claim_allowed"] is True
        assert publishable["publication_absolute_qps_and_efficiency_four_scene_target_met"] is True

        weak_matched = root / "weak_matched.json"
        weak_payload = json.loads(matched.read_text(encoding="utf-8"))
        weak_payload["scenes"]["xp_io"]["Algorithm C"].pop("raw_qps")
        write_json(weak_matched, weak_payload)
        rejected = audit.audit(
            campaign,
            comparison_path=comparison,
            archive_path=archive,
            timing_path=timing,
            startup_path=startup,
            matched_absolute_qps_path=weak_matched,
        )
        assert rejected["publication_claim_allowed"] is False
        assert "raw_qps_count_mismatch:xp_io:Algorithm C" in rejected["matched_absolute_qps"]["errors"]

    print("PASS: adaptive Algorithm C post-terminal auditor", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
