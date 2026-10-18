from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
AUDITOR = ROOT / "tools/audit_job_v57_first_batch.py"
PARAMETERS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate(rank: int, role: str) -> dict:
    fingerprint = {
        "id": "three-level-sha256:" + f"{rank:064x}",
        "valid": True,
        "plan_level": "ratio-plan-sha256:" + f"{rank:064x}",
        "feature_ratio_level": {"cpu": rank, "io": rank, "memory": rank},
        "predicted_resource_level": {"cpu": rank, "io": rank, "memory": rank},
    }
    return {
        "rank": rank,
        "selection_role": role,
        "candidate_params": {**PARAMETERS, "cpu_operator_cost": 0.0025 + rank * 0.0001},
        "boundary_corner": False,
        "three_level_fingerprint": fingerprint,
        "feature_ratio": {"cpu": 0.2, "io": 0.3, "memory": 0.8},
        "predicted_resource": {"cpu": 1.0 + rank * 0.1, "io": 50.0 + rank, "memory": 2.0 + rank * 0.01},
        "utilization": {"cpu": 0.3, "io": 0.2, "memory": 0.85},
        "dScore/dU": {"cpu_operator_cost": {"cpu": 1.0}},
        "T": {"cpu_operator_cost": {"cpu": 0.2}},
        "confidence": {"exact_plan_regime": 1.0},
        "g_j": {"cpu_operator_cost": 0.1},
        "eta": 0.5,
        "delta_param": {"cpu_operator_cost": {"absolute": rank * 0.0001}},
        "candidate_score": float(rank),
    }


def summary(selection_path: Path, candidates: list[dict]) -> dict:
    successful = []
    for item in candidates:
        rank = int(item["rank"])
        actual_score = float(rank) + 0.1 if rank <= 3 else 0.1
        successful.append(
            {
                "rank": rank,
                "selection_role": item["selection_role"],
                "technical_failure": False,
                "accepted": False,
                "gradient_update_applied": False,
                "incumbent_update_applied": False,
                "range_update_applied": False,
                "actual_score": actual_score,
                "actual_resource": item["predicted_resource"],
                "candidate_binding": {
                    "candidate_params": item["candidate_params"],
                    "fingerprint_id": item["three_level_fingerprint"]["id"],
                },
                "incumbent_state_before": {"accepted_count": 0},
                "incumbent_state_after": {"accepted_count": 0},
            }
        )
    state = {"accepted_count": 0}
    return {
        "diagnostic_only": True,
        "physical_invocation_count": 5,
        "real_workload_invocation_count": 5,
        "successful_real_replay_count": 5,
        "technical_failure_count": 0,
        "successful": successful,
        "technical_failures": [],
        "invocations": [{"rank": rank} for rank in range(1, 6)],
        "selection_sha256": sha(selection_path),
        "baseline_params_restored": True,
        "baseline_restore": {"success": True},
        "initial_state": state,
        "final_state": state,
        "formal_update_flags": {"accepted": False, "gradient": False, "incumbent": False, "range": False},
    }


def build_fixture(root: Path) -> None:
    quarantine_id = "three-level-sha256:" + "f" * 64
    write_json(
        root / "v55_failed_formal_fingerprint_quarantine.json",
        {"status": "quarantined_no_retry", "retry_allowed": False, "fingerprint_id": quarantine_id},
    )
    write_json(root / "package/formal_state.json", {"accepted_count": 0})
    (root / "formal_search_accepted_counter.txt").write_text("0\n", encoding="utf-8")
    cycle = root / "cycle_001"
    write_json(
        cycle / "global_exclusion.json",
        {"fingerprints": [{"three_level_fingerprint": {"id": quarantine_id}}]},
    )

    discovery_candidates = [candidate(rank, "probe") for rank in range(1, 6)]
    discovery = cycle / "discovery_replay_01/top5.json"
    write_json(discovery, {"top5": discovery_candidates, "diagnostic_only": True})
    discovery_summary = cycle / "discovery_replay_01/replay/summary.json"
    write_json(discovery_summary, summary(discovery, discovery_candidates))

    repeat_candidates = [candidate(rank, "trusted" if rank <= 3 else "probe") for rank in range(1, 6)]
    repeat = cycle / "repeat_01/top5.json"
    write_json(repeat, {"top5": repeat_candidates, "diagnostic_only": True})
    repeat_summary = cycle / "repeat_01/replay/summary.json"
    write_json(repeat_summary, summary(repeat, repeat_candidates))
    gate = {
        "passed": True,
        "checks": {
            "trusted_only_spearman_positive": True,
            "actual_best_in_predicted_top3": True,
            "trusted_resource_no_order_of_magnitude_error": True,
        },
        "trusted_only_spearman": 1.0,
        "actual_best_predicted_rank": 3,
        "maximum_trusted_resource_factor": 1.0,
    }
    write_json(cycle / "repeat_01/entry_gate_raw.json", gate)
    write_json(cycle / "repeat_01/entry_gate_raw.second.json", gate)
    write_json(
        cycle / "repeat_01/entry_gate_binding_audit.json",
        {
            "selection_sha256": sha(repeat),
            "summary_sha256": sha(repeat_summary),
            "new_workload_calls": 0,
        },
    )


def test_complete_diagnostics_and_gate_pass() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "v57"
        build_fixture(root)
        completed = subprocess.run(
            [sys.executable, str(AUDITOR), "--root", str(root)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert completed.returncode == 0, completed.stdout
        result = json.loads(completed.stdout)
        assert result["status"] == "passed_waiting_for_formal"
        assert len(result["diagnostic_audits"]) == 2
        assert result["rank_gate_audits"][0]["trusted_only_spearman"] == 1.0
        assert result["rank_gate_audits"][0]["actual_best_predicted_rank"] == 3
        assert result["repeat_binding_audits"][0]["exact_parameter_set_preserved"] is True


def test_running_phase_does_not_invent_technical_failure() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "v57"
        build_fixture(root)
        partial = root / "cycle_002/discovery_replay_01"
        candidates = [candidate(rank + 10, "probe") for rank in range(1, 6)]
        quarantine_id = "three-level-sha256:" + "f" * 64
        write_json(
            root / "cycle_002/global_exclusion.json",
            {"fingerprints": [{"three_level_fingerprint": {"id": quarantine_id}}]},
        )
        write_json(partial / "top5.json", {"top5": candidates})
        (partial / "replay/rank_01").mkdir(parents=True)
        (partial / "replay/rank_01/workload.log").write_text("running\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(AUDITOR), "--root", str(root)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert completed.returncode == 0, completed.stdout
        result = json.loads(completed.stdout)
        assert result["status"] == "passed_with_running_diagnostic"
        audit = result["partial_phase_audits"][0]
        assert audit["known_technical_failures"] is None
        assert audit["technical_classification_deferred_until_sealed_summary"] is True


def main() -> int:
    test_complete_diagnostics_and_gate_pass()
    test_running_phase_does_not_invent_technical_failure()
    print("JOB v57 first-batch auditor tests passed: 2", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
