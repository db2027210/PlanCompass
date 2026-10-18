#!/usr/bin/env python3
"""Self-tests for the completed budgeted-pilot auditor."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell import audit_pg_algorithm_c_budgeted_trial as audit


TEST_PARAMS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 2.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def result(scene: str) -> dict:
    identity = audit.evidence_identity(TEST_PARAMS)
    return {
        "contract": audit.CONTRACT,
        "scene": scene,
        "status": "validated",
        "policy": {"budget": {"max_search_replays": 12}},
        "trial": {
            "contract": audit.CONTRACT,
            "active_time_contract": audit.ACTIVE_TIME_CONTRACT,
            "search_replays": 10,
            "planning_s": 30.0,
            "wall_s": 100.0,
            "wall_budget_met": True,
            "search_budget_met": True,
            "host_outage_excluded_s": 0.0,
            "feasible_archive": {identity: {}},
            "region_queue": [],
            "visited_regions": ["region"],
            "search_stop_reason": "search_replay_budget_exhausted",
            "finalist": {"identity": identity, "params": TEST_PARAMS},
            "clean_restore_receipt": {"passed": True},
            "validation": {
                "passed": True,
                "normalized_qps_geomean": 30.0,
                "bracketed_speedup_geomean": 1.2,
                "baseline_drift": {"w1": 0.01, "w2": -0.01},
                "physical_replays": 8,
                "aggregation": "balanced_worker_repetition_geometric_mean",
                "raw_finalist_qps": [30.0] * 4,
                "raw_post_baseline_qps": [25.0] * 4,
            },
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        missing = audit.audit(root)
        assert missing["complete"] is False
        assert missing["campaign_root"] == str(root.resolve())
        assert missing["safe_to_launch_formal"] is False

        formal_path = root / "formal.json"
        archive_path = root / "archive.json"
        write_json(formal_path, {
            "postgresql": {"scenes": {
                scene: {"algorithms": {
                    "Algorithm A": {"champion_qps": 10.0},
                    "Algorithm B": {"champion_qps": 20.0},
                }} for scene in audit.SCENES
            }}
        })
        write_json(archive_path, {"scenes": {
            scene: {"Algorithm B": {"observed_finalist": {
                "observed_qps": 21.0,
                "params": TEST_PARAMS,
            }}}
            for scene in audit.SCENES
        }})
        original_formal = audit.FORMAL_RESULTS
        original_archive = audit.FAIR_ARCHIVE_AUDIT
        audit.FORMAL_RESULTS = formal_path
        audit.FAIR_ARCHIVE_AUDIT = archive_path
        try:
            timing = {
                scene: {
                    "Algorithm A": {"log_wall_s": 300.0},
                    "Algorithm B": {"log_wall_s": 200.0},
                } for scene in audit.SCENES
            }
            write_json(root / "budgeted_launch_manifest.json", {"timing_references": timing})
            write_json(root / "launcher_status.json", {"phase": "launched"})
            running = audit.audit(root)
            assert running["complete"] is False
            assert running["reason"] == "pilot_is_not_terminal"

            write_json(root / "launcher_status.json", {"phase": "finished"})
            for scene in audit.SCENES:
                write_json(root / scene / "budgeted_trial_result.json", result(scene))
                events = [
                    {"event": "algorithm_c_region_revisited"},
                    {
                        "event": "algorithm_c_inflight_physical_wave_recovered",
                        "synthesized_labels": ["lost_w1", "lost_w2"],
                    },
                    {
                        "event": "algorithm_c_candidate_replay",
                        "batch_kind": "trial_search",
                        "validation": {
                            "status": "ok",
                            "label": "candidate_w1",
                            "worker_key": "w1",
                            "db_qps": 30.0,
                            "candidate_ranking": {"optimized_params": TEST_PARAMS},
                        },
                    },
                ]
                (root / scene / "algorithm_c.jsonl").write_text(
                    "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
                )
            completed = audit.audit(root)
            assert completed["complete"] is True
            assert completed["campaign_root"] == str(root.resolve())
            expected_evidence = {
                "budgeted_launch_manifest.json",
                "launcher_status.json",
                *(f"{scene}/budgeted_trial_result.json" for scene in audit.SCENES),
            }
            assert set(completed["source_evidence_sha256"]) == expected_evidence
            assert all(
                len(value) == 64 for value in completed["source_evidence_sha256"].values()
            )
            assert completed["evidence_integrity_passed"] is True
            assert completed["execution_budget_contract_passed"] is True
            assert completed["all_scenes_validated"] is True
            assert completed["provisional_four_scene_quality_and_efficiency_target_met"] is True
            assert completed["safe_to_modify_algorithm"] is True
            assert completed["safe_to_launch_formal"] is False
            for row in completed["scenes"].values():
                diagnostic = row["historical_b_region_diagnostic"]
                assert diagnostic["historical_b_exactly_evaluated"] is True
                assert diagnostic["gap_stage"] == "historical_b_exactly_evaluated_archived_and_selected"
                assert row["mechanism"]["unique_technical_failure_slots"] == 2
                assert row["mechanism"]["recovery_technical_failure_slots"] == 2
        finally:
            audit.FORMAL_RESULTS = original_formal
            audit.FAIR_ARCHIVE_AUDIT = original_archive
    print("PASS: budgeted Algorithm C pilot auditor", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
