#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("memory", "cpu", "io", "none")


def run_command(args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(args),
        cwd=str(ROOT),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed rc={proc.returncode}: {' '.join(args)}\n{proc.stdout}")
    return proc


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if text:
                rows.append(json.loads(text))
    return rows


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def evaluator(prefix: Path, *extra_args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command(
        [
            sys.executable,
            "shell/evaluate_bonect_estimator_ranking.py",
            "--output-prefix",
            str(prefix),
            *extra_args,
        ],
        check=check,
    )


def make_synthetic_full_labels(manifest_path: Path, output_path: Path) -> int:
    manifest = read_json(manifest_path)
    rows: List[Dict[str, Any]] = []
    for scenario, info in (manifest.get("scenarios") or {}).items():
        for candidate in info.get("candidates") or []:
            rank = int(candidate.get("robust_rank") or candidate.get("rank") or 999)
            rows.append(
                {
                    "scenario": scenario,
                    "candidate_id": candidate.get("candidate_id"),
                    "candidate_index": candidate.get("candidate_index"),
                    "params": candidate.get("params"),
                    "actual_score": 1000.0 - rank,
                    "actual_resource": {"cpu": float(rank), "memory": float(rank), "io": float(rank)},
                    "db_delta": {"db_qps": 1.0, "db_xact_tps": 1.0},
                    "benchmark_latency": {"p95": float(rank), "p99": float(rank)},
                }
            )
    write_jsonl(output_path, rows)
    return len(rows)


def validate_default_payload(payload: Dict[str, Any]) -> None:
    gate = payload.get("goal_gate") or {}
    assert_true(payload.get("online_no_candidate_real_label_leakage") is True, "online leakage audit should pass")
    leakage = payload.get("label_leakage_boundary_audit") or {}
    assert_true(leakage.get("online_boundary_ok") is True, "online boundary should be clean")
    formula_hits = ((leakage.get("online_formula_file") or {}).get("total_hits") or 0)
    assert_true(int(formula_hits) == 0, "online robust formula must not reference real-label tokens")
    for window in leakage.get("online_integration_windows") or []:
        assert_true(int(window.get("total_hits") or 0) == 0, f"integration window leaked labels: {window}")

    summary = payload.get("validation_manifest_summary") or {}
    total_missing = int(summary.get("total_missing_label_count") or 0)
    pending_queue_count = int(summary.get("pending_queue_count") or 0)
    assert_true(total_missing > 0, "default report should retain an incomplete validation queue")
    assert_true(
        pending_queue_count == total_missing,
        "pending queue count should match total missing labels",
    )
    per_missing = summary.get("per_scenario_missing") or {}
    assert_true(
        sum(int(per_missing.get(scenario) or 0) for scenario in SCENARIOS) == total_missing,
        "per-scenario missing labels should add up to the total",
    )
    for scenario in SCENARIOS:
        expected_missing = int(per_missing.get(scenario) or 0)
        info = ((gate.get("per_scenario") or {}).get(scenario) or {})
        top15_labeled = int(info.get("top15_labeled_items") or 0)
        assert_true(
            expected_missing == max(0, 15 - top15_labeled),
            f"{scenario} missing count should complement top15 label coverage",
        )
        assert_true(
            bool(info.get("coverage_pass")) == (top15_labeled == 15),
            f"{scenario} coverage gate should match top15 label count",
        )
    assert_true(gate.get("full_pass") is False, "default full gate should fail with pending labels")
    assert_true(gate.get("strict_goal_pass") is False, "default strict goal should fail with pending labels")

    exact = payload.get("exact_reusable_label_audit") or {}
    assert_true(
        exact.get("method") == "exact candidate_id or seven-cost-parameter match against current robust top15",
        "exact reusable label audit should use strict candidate matching",
    )
    assert_true(int(exact.get("searched_file_count") or 0) >= 1, "should search existing validation result files")
    total_new_external_matches = int(exact.get("total_new_external_match_rows") or 0)
    assert_true(
        total_new_external_matches >= 0,
        "exact reusable label audit should report a non-negative external match count",
    )
    if total_new_external_matches:
        assert_true(
            int(summary.get("total_missing_label_count") or 0) == total_missing,
            "external reusable labels must not silently shrink the default pending queue",
        )
        for scenario in SCENARIOS:
            info = ((gate.get("per_scenario") or {}).get(scenario) or {})
            assert_true(
                int(info.get("matched_extra_label_count") or 0) == 0,
                f"{scenario} default metrics must not ingest external reusable-label audit rows",
            )
    historical = payload.get("historical_param_occurrence_audit") or {}
    assert_true(int(historical.get("searched_file_count") or 0) >= 1, "historical param audit should scan files")
    assert_true(
        int(historical.get("total_occurrence_count") or 0) >= int(exact.get("total_external_match_rows") or 0),
        "historical param occurrence audit should be at least as broad as validation label matching",
    )
    assert_true(
        "not merged as real labels" in str(historical.get("note") or ""),
        "historical param audit must not imply automatic label reuse",
    )

    completion = payload.get("completion_audit") or {}
    assert_true(completion.get("overall_complete") is False, "default completion audit should remain incomplete")
    assert_true(
        int(completion.get("pending_validation_count") or 0) == total_missing,
        "completion audit should count pending labels",
    )
    requirements = {item.get("id"): item for item in completion.get("requirements") or []}
    assert_true(
        (requirements.get("top10_top15_real_label_coverage") or {}).get("proved") is False,
        "completion audit should require full top10/top15 real labels",
    )
    assert_true(
        (requirements.get("pairwise_inversion_target") or {}).get("proved") is False,
        "completion audit should not prove inversion target without full labels",
    )
    assert_true(
        (requirements.get("same_round_real_label_leakage_guard") or {}).get("proved") is True,
        "completion audit should prove no online label leakage",
    )
    recommendation = payload.get("next_round_recommendation") or {}
    assert_true(
        recommendation.get("status") == "pending_full_validation",
        "default recommendation should wait for full validation coverage",
    )
    assert_true(recommendation.get("ready_for_next_round") is False, "default recommendation should not be ready")
    assert_true(recommendation.get("apply_to_online_now") is False, "recommendation must not auto-apply online")
    assert_true(
        recommendation.get("recommended_default_variant") is None,
        "default recommendation should not select a next-round variant before full labels",
    )


def validate_full_payload(payload: Dict[str, Any]) -> None:
    gate = payload.get("goal_gate") or {}
    assert_true(gate.get("partial_pass") is True, "synthetic partial gate should pass")
    assert_true(gate.get("full_pass") is True, "synthetic full gate should pass")
    assert_true(gate.get("strict_goal_pass") is True, "synthetic strict goal should pass")
    completion = payload.get("completion_audit") or {}
    assert_true(completion.get("overall_complete") is True, "synthetic full labels should complete the audit")
    requirements = {item.get("id"): item for item in completion.get("requirements") or []}
    for req_id in (
        "robust_online_code_landed",
        "offline_evaluator_and_report_landed",
        "same_round_real_label_leakage_guard",
        "top10_top15_real_label_coverage",
        "pairwise_inversion_target",
    ):
        assert_true((requirements.get(req_id) or {}).get("proved") is True, f"{req_id} should be proved")
    for scenario in SCENARIOS:
        info = ((gate.get("per_scenario") or {}).get(scenario) or {})
        assert_true(int(info.get("top10_labeled_items") or 0) == 10, f"{scenario} synthetic top10 coverage")
        assert_true(int(info.get("top15_labeled_items") or 0) == 15, f"{scenario} synthetic top15 coverage")
        assert_true(float(info.get("top15_pairwise_inversion_rate") or 0.0) == 0.0, f"{scenario} synthetic inversion")
    recommendation = payload.get("next_round_recommendation") or {}
    assert_true(
        recommendation.get("status") == "ready_for_next_round",
        "synthetic full labels should make next-round recommendation ready",
    )
    assert_true(recommendation.get("ready_for_next_round") is True, "full recommendation should be ready")
    assert_true(recommendation.get("apply_to_online_now") is False, "full recommendation still must not auto-apply")
    assert_true(
        recommendation.get("recommended_default_variant"),
        "full recommendation should contain an explicit next-round variant",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Bonect robust ranking evaluator invariants.")
    parser.add_argument("--keep-output", action="store_true")
    args = parser.parse_args()

    tmp_root = Path(tempfile.mkdtemp(prefix="bonect_robust_eval_check_"))
    try:
        run_command(
            [
                sys.executable,
                "-m",
                "py_compile",
                "bonect_robust_ranking.py",
                "shell/evaluate_bonect_estimator_ranking.py",
                "shell/bonect_real_validate_candidates.py",
                "parameter_optimizer.py",
                "shell/bonect_full_grid_estimator_scan.py",
                "plan_resource_estimator.py",
                "resource_model_retrainer.py",
                "resource_feature_predictor.py",
            ]
        )

        concurrent_append_path = tmp_root / "concurrent_validation_results.jsonl"
        append_worker = (
            "import sys\n"
            "from pathlib import Path\n"
            "from shell.bonect_real_validate_candidates import append_jsonl\n"
            "path = Path(sys.argv[1])\n"
            "worker = int(sys.argv[2])\n"
            "for i in range(20):\n"
            "    append_jsonl(path, {'candidate_id': f'w{worker}_{i}', 'status': 'ok', 'actual_score': worker * 100 + i})\n"
        )
        workers = [
            subprocess.Popen(
                [sys.executable, "-c", append_worker, str(concurrent_append_path), str(worker)],
                cwd=str(ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for worker in range(8)
        ]
        worker_outputs = [worker.communicate(timeout=30) for worker in workers]
        for worker, (stdout, stderr) in zip(workers, worker_outputs):
            assert_true(worker.returncode == 0, f"concurrent append worker failed: stdout={stdout} stderr={stderr}")
        concurrent_rows = read_jsonl(concurrent_append_path)
        assert_true(len(concurrent_rows) == 160, "concurrent append should preserve every JSONL row")
        assert_true(len({row.get("candidate_id") for row in concurrent_rows}) == 160, "concurrent append row ids should be unique")

        lock_path = tmp_root / "validation_run.lock"
        lock_holder_code = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from shell.bonect_real_validate_candidates import acquire_validation_run_lock, release_validation_run_lock\n"
            "handle = acquire_validation_run_lock(Path(sys.argv[1]), wait=False)\n"
            "print('locked', flush=True)\n"
            "time.sleep(5)\n"
            "release_validation_run_lock(handle)\n"
        )
        lock_holder = subprocess.Popen(
            [sys.executable, "-c", lock_holder_code, str(lock_path)],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            first_line = lock_holder.stdout.readline() if lock_holder.stdout is not None else ""
            assert_true(first_line.strip() == "locked", "lock holder should acquire validation run lock")
            lock_contender = run_command(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys\n"
                        "from pathlib import Path\n"
                        "from shell.bonect_real_validate_candidates import acquire_validation_run_lock\n"
                        "acquire_validation_run_lock(Path(sys.argv[1]), wait=False)\n"
                    ),
                    str(lock_path),
                ],
                check=False,
            )
            assert_true(lock_contender.returncode != 0, "second validation run lock acquisition should fail fast")
            assert_true(
                "another Bonect validation run is active" in lock_contender.stdout,
                "lock failure should explain active validation run",
            )
        finally:
            lock_holder.terminate()
            try:
                lock_holder.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                lock_holder.kill()
                lock_holder.communicate(timeout=10)

        default_prefix = tmp_root / "default_eval"
        evaluator(default_prefix)
        default_payload = read_json(default_prefix.with_suffix(".json"))
        validate_default_payload(default_payload)
        default_summary = default_payload.get("validation_manifest_summary") or {}
        default_pending = int(default_summary.get("total_missing_label_count") or 0)
        default_missing_by_scenario = {
            scenario: int((default_summary.get("per_scenario_missing") or {}).get(scenario) or 0)
            for scenario in SCENARIOS
        }
        assert_true(default_pending >= 2, "status self-test needs at least two pending default candidates")
        pending_queue = Path(default_payload["pending_validation_queue_jsonl"])
        latest_index = tmp_root / "latest_robust_ranking_calibration_index.json"
        latest_report = tmp_root / "latest_robust_ranking_calibration.json"
        latest_manifest = tmp_root / "latest_robust_ranking_validation_manifest.json"
        latest_queue = tmp_root / "latest_robust_ranking_pending_validation_queue.jsonl"
        latest_recommendation = tmp_root / "latest_robust_ranking_next_round_recommendation.json"
        assert_true(latest_index.exists(), "default evaluator should write a latest index")
        assert_true(latest_report.exists(), "default evaluator should write a latest report JSON")
        assert_true(latest_manifest.exists(), "default evaluator should write a latest manifest JSON")
        assert_true(latest_queue.exists(), "default evaluator should write a latest pending queue")
        assert_true(latest_recommendation.exists(), "default evaluator should write a latest recommendation JSON")
        latest_payload = read_json(latest_report)
        assert_true(
            latest_payload.get("generated_at") == default_payload.get("generated_at"),
            "latest report should mirror the timestamped default report",
        )
        latest_index_payload = read_json(latest_index)
        assert_true(
            latest_index_payload.get("timestamped_outputs", {}).get("report_json") == str(default_prefix.with_suffix(".json")),
            "latest index should point back to the timestamped report",
        )
        assert_true(
            latest_index_payload.get("latest_outputs", {}).get("next_round_recommendation_json") == str(latest_recommendation),
            "latest index should expose the next-round recommendation",
        )

        strict_prefix = tmp_root / "default_strict"
        strict_proc = evaluator(strict_prefix, "--strict-goal", check=False)
        assert_true(strict_proc.returncode == 2, f"default strict should fail with rc=2, got {strict_proc.returncode}")

        candidates_only = run_command(
            [
                sys.executable,
                "shell/bonect_real_validate_candidates.py",
                "--candidates-json",
                str(pending_queue),
                "--candidates-only",
                "--pending-only",
            ]
        )
        assert_true(
            f"count={default_pending}" in candidates_only.stdout,
            f"pending queue should load as {default_pending} validation candidates",
        )

        plan_dir = tmp_root / "validation_plan"
        run_command(
            [
                sys.executable,
                "shell/bonect_real_validate_candidates.py",
                "--candidates-json",
                str(pending_queue),
                "--output-dir",
                str(plan_dir),
                "--pending-only",
                "--apply-vm-profile",
                "--retry-failed",
                "--restore-defaults",
                "--run-partial-evaluation-after-status",
                "--plan-only",
            ]
        )
        plan = read_json(plan_dir / "validation_plan.json")
        assert_true(
            int(plan.get("candidate_count") or 0) == default_pending,
            f"validation plan should contain {default_pending} candidates",
        )
        assert_true(plan.get("retry_failed") is True, "validation plan should preserve retry-failed intent")
        assert_true("--retry-failed" in str(plan.get("full_command")), "full validation command should retry failed rows")
        assert_true("--status-only" in str(plan.get("status_refresh_command")), "plan should include status refresh command")
        assert_true(
            "--scenario-batch-size 3" in str(plan.get("status_refresh_command")),
            "plan status refresh command should preserve chunk size",
        )
        assert_true(
            "--strict-goal" not in str(plan.get("partial_evaluation_command")),
            "plan partial evaluation command should not be strict",
        )
        assert_true(
            "--strict-goal" in str(plan.get("strict_evaluation_command")),
            "plan strict evaluation command should be strict",
        )
        assert_true(
            plan.get("post_evaluation_command") == plan.get("strict_evaluation_command"),
            "plan post-evaluation command should be the strict completion gate",
        )
        assert_true(
            "--output-prefix" in str(plan.get("partial_evaluation_command")),
            "plan partial evaluation command should use a stable output prefix",
        )
        for scenario in SCENARIOS:
            expected_missing = default_missing_by_scenario[scenario]
            assert_true(
                int((plan.get("scenario_counts") or {}).get(scenario) or 0) == expected_missing,
                f"{scenario} plan count",
            )
        assert_true(not (plan_dir / "validation_results.jsonl").exists(), "plan-only must not create real results")
        plan_runbook_text = (plan_dir / "scenario_batches.md").read_text(encoding="utf-8")
        assert_true("Refresh Status" in plan_runbook_text, "plan runbook should include status refresh section")
        assert_true("After Each Chunk" in plan_runbook_text, "plan runbook should include partial evaluation section")
        assert_true("validation_run.lock" in plan_runbook_text, "plan runbook should document validation run lock")
        plan_batches = (plan.get("scenario_batches") or {}).get("batches") or {}
        assert_true(
            int((plan.get("scenario_batches") or {}).get("scenario_batch_size") or 0) == 3,
            "plan scenario batch size",
        )
        assert_true((plan_dir / "scenario_batches.md").exists(), "plan-only should write scenario batch runbook")
        for scenario in SCENARIOS:
            expected_missing = default_missing_by_scenario[scenario]
            expected_chunk_count = (expected_missing + 2) // 3
            batch = plan_batches.get(scenario) or {}
            assert_true(int(batch.get("candidate_count") or 0) == expected_missing, f"{scenario} planned batch count")
            assert_true(Path(str(batch.get("queue_jsonl"))).exists(), f"{scenario} planned queue exists")
            assert_true(
                len(read_jsonl(Path(str(batch.get("queue_jsonl"))))) == expected_missing,
                f"{scenario} planned queue rows",
            )
            assert_true("--retry-failed" in str(batch.get("command")), f"{scenario} planned batch should retry failed rows")
            assert_true(
                "--status-candidates-json" in str(batch.get("command")),
                f"{scenario} planned batch should refresh global status candidates",
            )
            assert_true(
                "--run-partial-evaluation-after-status" in str(batch.get("command")),
                f"{scenario} planned batch should auto-run partial evaluation",
            )
            chunks = batch.get("chunks") or []
            assert_true(len(chunks) == expected_chunk_count, f"{scenario} planned chunk count")
            assert_true(
                sum(int(chunk.get("candidate_count") or 0) for chunk in chunks) == expected_missing,
                f"{scenario} planned chunk total",
            )
            for chunk in chunks:
                chunk_path = Path(str(chunk.get("queue_jsonl")))
                assert_true(chunk_path.exists(), f"{scenario} planned chunk queue exists")
                assert_true(
                    len(read_jsonl(chunk_path)) == int(chunk.get("candidate_count") or 0),
                    f"{scenario} planned chunk queue rows",
                )
                assert_true("--retry-failed" in str(chunk.get("command")), f"{scenario} planned chunk command")
                assert_true(
                    "--status-candidates-json" in str(chunk.get("command")),
                    f"{scenario} planned chunk should refresh global status candidates",
                )
                assert_true(
                    "--run-partial-evaluation-after-status" in str(chunk.get("command")),
                    f"{scenario} planned chunk should auto-run partial evaluation",
                )
        plan_index_path = tmp_root / "latest_robust_ranking_validation_plan_index.json"
        assert_true(plan_index_path.exists(), "plan-only should write a stable validation plan index")
        plan_index_payload = read_json(plan_index_path)
        plan_outputs = plan_index_payload.get("latest_validation_plan_outputs") or {}
        assert_true(plan_outputs.get("run_dir") == str(plan_dir), "plan index should point at the plan output dir")
        assert_true(
            plan_outputs.get("validation_plan_json") == str(plan_dir / "validation_plan.json"),
            "plan index should point at validation_plan.json",
        )
        assert_true(
            plan_outputs.get("scenario_batch_runbook_md") == str(plan_dir / "scenario_batches.md"),
            "plan index should point at scenario_batches.md",
        )
        latest_index_payload = read_json(latest_index)
        assert_true(
            latest_index_payload.get("validation_plan_index_json") == str(plan_index_path),
            "calibration latest index should include the validation plan index",
        )
        assert_true(
            latest_index_payload.get("latest_validation_plan_outputs", {}).get("run_dir") == str(plan_dir),
            "calibration latest index should include validation plan outputs",
        )

        status_dir = tmp_root / "validation_status"
        pending_rows = read_jsonl(pending_queue)
        write_jsonl(
            status_dir / "validation_results.jsonl",
            [
                {
                    "scenario": pending_rows[0].get("scenario"),
                    "candidate_id": pending_rows[0].get("candidate_id"),
                    "status": "ok",
                    "actual_score": 1.0,
                    "actual_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
                },
                {
                    "scenario": pending_rows[1].get("scenario"),
                    "candidate_id": pending_rows[1].get("candidate_id"),
                    "status": "failed",
                    "error": "synthetic failure",
                },
            ],
        )
        run_command(
            [
                sys.executable,
                "shell/bonect_real_validate_candidates.py",
                "--candidates-json",
                str(pending_queue),
                "--output-dir",
                str(status_dir),
                "--pending-only",
                "--retry-failed",
                "--run-partial-evaluation-after-status",
                "--status-only",
            ]
        )
        status = read_json(status_dir / "validation_status.json")
        expected_status_pending = default_pending - 2
        expected_status_remaining = expected_status_pending + 1
        assert_true(
            int(status.get("candidate_count") or 0) == default_pending,
            f"validation status should cover {default_pending} candidates",
        )
        assert_true(int(status.get("ok_count") or 0) == 1, "validation status ok count")
        assert_true(int(status.get("failed_count") or 0) == 1, "validation status failed count")
        assert_true(
            int(status.get("pending_count") or 0) == expected_status_pending,
            "validation status pending count",
        )
        assert_true(status.get("complete") is False, "partial validation status must not be complete")
        assert_true(status.get("retry_failed") is True, "validation status should preserve retry-failed intent")
        assert_true("--retry-failed" in str(status.get("resume_command")), "resume command should retry failed rows")
        assert_true("remaining_validation_queue.jsonl" in str(status.get("resume_command")), "resume command should use remaining queue")
        assert_true("--status-only" in str(status.get("status_refresh_command")), "status should include refresh command")
        assert_true("--retry-failed" in str(status.get("status_refresh_command")), "status refresh should retry failed rows")
        assert_true(
            "--strict-goal" not in str(status.get("partial_evaluation_command")),
            "status partial evaluation should not be strict",
        )
        assert_true(
            "--strict-goal" in str(status.get("strict_evaluation_command")),
            "status strict evaluation should be strict",
        )
        assert_true(
            "--strict-goal" not in str(status.get("recommended_evaluation_command")),
            "incomplete status should recommend partial evaluation",
        )
        assert_true(
            status.get("post_evaluation_command") == status.get("strict_evaluation_command"),
            "status post-evaluation command should be the strict completion gate",
        )
        assert_true(
            "bonect_validation_queue_orchestrator.py" in str(status.get("queue_orchestrator_dry_run_command")),
            "status should include dry-run queue orchestrator command",
        )
        assert_true(
            "--execute" in str(status.get("queue_orchestrator_execute_next_command")),
            "status should include execute-next queue orchestrator command",
        )
        assert_true(
            "--strict-after-complete" in str(status.get("queue_orchestrator_execute_next_command")),
            "execute-next queue command should run strict evaluation after completion",
        )
        assert_true(
            "--allow-no-progress" not in str(status.get("queue_orchestrator_execute_next_command")),
            "generated real-validation queue command should keep no-progress protection enabled",
        )
        assert_true(
            "--output-prefix" in str(status.get("partial_evaluation_command")),
            "status partial evaluation command should use a stable output prefix",
        )
        post_status_eval = status.get("post_status_partial_evaluation") or {}
        assert_true(post_status_eval.get("status") == "ok", "status-only should run partial evaluation when requested")
        active_eval_summary = status.get("active_evaluation_summary") or {}
        assert_true(active_eval_summary, "status should embed the latest offline evaluation gate summary")
        assert_true(active_eval_summary.get("strict_goal_pass") is False, "embedded summary should show strict gate incomplete")
        assert_true(
            (active_eval_summary.get("per_scenario") or {}).get("memory"),
            "embedded summary should include per-scenario gate details",
        )
        assert_true(Path(str(status.get("partial_evaluation_json"))).exists(), "partial evaluation JSON should exist")
        assert_true(Path(str(status.get("partial_evaluation_md"))).exists(), "partial evaluation Markdown should exist")
        queue_counts = status.get("status_queue_counts") or {}
        assert_true(
            int(queue_counts.get("remaining") or 0) == expected_status_remaining,
            "remaining queue should include failed plus pending",
        )
        assert_true(int(queue_counts.get("failed") or 0) == 1, "failed retry queue count")
        assert_true(int(queue_counts.get("pending") or 0) == expected_status_pending, "pending-only queue count")
        assert_true(
            len(read_jsonl(Path(status["remaining_validation_queue_jsonl"]))) == expected_status_remaining,
            "remaining queue file rows",
        )
        assert_true(len(read_jsonl(Path(status["failed_retry_queue_jsonl"]))) == 1, "failed queue file rows")
        assert_true(
            len(read_jsonl(Path(status["pending_only_queue_jsonl"]))) == expected_status_pending,
            "pending queue file rows",
        )
        assert_true((status_dir / "validation_status.md").exists(), "status-only should write markdown")
        status_md_text = (status_dir / "validation_status.md").read_text(encoding="utf-8")
        assert_true("Next chunk" in status_md_text, "status markdown should include the next chunk command")
        assert_true(
            "Current Offline Evaluation Gate" in status_md_text,
            "status markdown should include the embedded evaluation gate",
        )
        assert_true("offline partial ranking metrics" in status_md_text, "status markdown should include partial evaluation command")
        next_batch = status.get("next_validation_batch") or {}
        assert_true(next_batch, "status should expose the next validation batch")
        assert_true(int(next_batch.get("candidate_count") or 0) > 0, "next batch should have candidates")
        assert_true(Path(str(next_batch.get("queue_jsonl"))).exists(), "next batch queue should exist")
        assert_true(
            "--status-candidates-json" in str(status.get("next_validation_command")),
            "next validation command should refresh global status candidates",
        )
        assert_true((status_dir / "scenario_batches.md").exists(), "status-only should write scenario batch runbook")
        status_runbook_text = (status_dir / "scenario_batches.md").read_text(encoding="utf-8")
        assert_true("Refresh Status" in status_runbook_text, "status runbook should include refresh section")
        assert_true("validation_run.lock" in status_runbook_text, "status runbook should document validation run lock")
        status_batches = (status.get("scenario_batches") or {}).get("batches") or {}
        assert_true(
            int((status.get("scenario_batches") or {}).get("scenario_batch_size") or 0) == 3,
            "status scenario batch size",
        )
        status_batch_total = 0
        status_chunk_total = 0
        for scenario in SCENARIOS:
            batch = status_batches.get(scenario) or {}
            batch_count = int(batch.get("candidate_count") or 0)
            status_batch_total += batch_count
            assert_true(Path(str(batch.get("queue_jsonl"))).exists(), f"{scenario} remaining batch queue exists")
            assert_true(
                len(read_jsonl(Path(str(batch.get("queue_jsonl"))))) == batch_count,
                f"{scenario} remaining batch queue rows",
            )
            assert_true("remaining_" in str(batch.get("queue_jsonl")), f"{scenario} remaining queue naming")
            assert_true(
                "--status-candidates-json" in str(batch.get("command")),
                f"{scenario} remaining batch should refresh global status candidates",
            )
            assert_true(
                "--run-partial-evaluation-after-status" in str(batch.get("command")),
                f"{scenario} remaining batch should auto-run partial evaluation",
            )
            chunks = batch.get("chunks") or []
            status_chunk_total += sum(int(chunk.get("candidate_count") or 0) for chunk in chunks)
            for chunk in chunks:
                chunk_path = Path(str(chunk.get("queue_jsonl")))
                assert_true(chunk_path.exists(), f"{scenario} remaining chunk queue exists")
                assert_true(
                    len(read_jsonl(chunk_path)) == int(chunk.get("candidate_count") or 0),
                    f"{scenario} remaining chunk queue rows",
                )
                assert_true(
                    "--status-candidates-json" in str(chunk.get("command")),
                    f"{scenario} remaining chunk should refresh global status candidates",
                )
                assert_true(
                    "--run-partial-evaluation-after-status" in str(chunk.get("command")),
                    f"{scenario} remaining chunk should auto-run partial evaluation",
                )
        assert_true(
            status_batch_total == expected_status_remaining,
            "remaining scenario batches should add up to failed plus pending",
        )
        assert_true(
            status_chunk_total == expected_status_remaining,
            "remaining scenario chunk batches should add up to failed plus pending",
        )
        dry_run_output = status_dir / "orchestrator_dry_run.json"
        dry_run = run_command(
            [
                sys.executable,
                "shell/bonect_validation_queue_orchestrator.py",
                "--status-json",
                str(status_dir / "validation_status.json"),
                "--output-json",
                str(dry_run_output),
            ]
        )
        assert_true('"action": "dry_run"' in dry_run.stdout, "orchestrator default should dry-run")
        dry_run_payload = read_json(dry_run_output)
        dry_run_records = dry_run_payload.get("records") or []
        assert_true(len(dry_run_records) == 1, "orchestrator dry-run should emit one record")
        assert_true(dry_run_records[0].get("action") == "dry_run", "orchestrator record should be dry-run")
        assert_true(
            (dry_run_payload.get("strict_after_complete") or {}).get("action") == "disabled",
            "strict-after-complete should be disabled by default",
        )
        assert_true(
            dry_run_records[0].get("next_validation_command") == status.get("next_validation_command"),
            "orchestrator should read the generated next validation command",
        )
        dry_run_strict = run_command(
            [
                sys.executable,
                "shell/bonect_validation_queue_orchestrator.py",
                "--status-json",
                str(status_dir / "validation_status.json"),
                "--strict-after-complete",
                "--output-json",
                str(dry_run_output),
            ]
        )
        assert_true('"action": "dry_run"' in dry_run_strict.stdout, "strict dry-run should still dry-run")
        dry_run_strict_payload = read_json(dry_run_output)
        assert_true(
            (dry_run_strict_payload.get("strict_after_complete") or {}).get("action") == "skip_not_complete",
            "strict-after-complete should skip incomplete status",
        )
        assert_true(
            int(read_json(status_dir / "validation_status.json").get("ok_count") or 0) == 1,
            "orchestrator dry-run must not mutate validation status",
        )
        complete_status_path = status_dir / "complete_validation_status.json"
        complete_status = dict(status)
        complete_status.update(
            {
                "complete": True,
                "ok_count": int(status.get("candidate_count") or 0),
                "failed_count": 0,
                "pending_count": 0,
                "next_validation_command": "python3 -c 'raise SystemExit(99)'",
                "strict_evaluation_command": f"{sys.executable} -c \"print('strict-ok')\"",
            }
        )
        write_json(complete_status_path, complete_status)
        complete_orch = run_command(
            [
                sys.executable,
                "shell/bonect_validation_queue_orchestrator.py",
                "--status-json",
                str(complete_status_path),
                "--execute",
            ]
        )
        assert_true('"action": "stop_complete"' in complete_orch.stdout, "orchestrator should stop on complete status")
        assert_true('"returncode"' not in complete_orch.stdout, "complete status should not execute next command")
        complete_strict_orch = run_command(
            [
                sys.executable,
                "shell/bonect_validation_queue_orchestrator.py",
                "--status-json",
                str(complete_status_path),
                "--execute",
                "--strict-after-complete",
            ]
        )
        assert_true(
            '"action": "execute_strict_evaluation"' in complete_strict_orch.stdout,
            "complete status should execute strict evaluation when requested",
        )
        assert_true('"strict-ok' in complete_strict_orch.stdout, "strict evaluation stdout should be captured")

        missing_status_path = status_dir / "missing_next_validation_status.json"
        missing_status = dict(status)
        missing_status.update({"complete": False, "next_validation_command": ""})
        write_json(missing_status_path, missing_status)
        missing_orch = run_command(
            [
                sys.executable,
                "shell/bonect_validation_queue_orchestrator.py",
                "--status-json",
                str(missing_status_path),
            ],
            check=False,
        )
        assert_true(missing_orch.returncode == 1, "missing next command should fail")
        assert_true(
            '"action": "stop_no_next_command"' in missing_orch.stdout,
            "missing next command should be explicit",
        )

        no_progress_status_path = status_dir / "no_progress_validation_status.json"
        no_progress_output = status_dir / "orchestrator_no_progress.json"
        no_progress_status = dict(status)
        no_progress_status.update(
            {
                "complete": False,
                "next_validation_command": f"{sys.executable} -c \"print('noop', flush=True)\"",
            }
        )
        write_json(no_progress_status_path, no_progress_status)
        no_progress_orch = run_command(
            [
                sys.executable,
                "shell/bonect_validation_queue_orchestrator.py",
                "--status-json",
                str(no_progress_status_path),
                "--execute",
                "--output-json",
                str(no_progress_output),
            ],
            check=False,
        )
        assert_true(no_progress_orch.returncode == 1, "no-progress execute should fail by default")
        no_progress_payload = read_json(no_progress_output)
        no_progress_records = no_progress_payload.get("records") or []
        assert_true(len(no_progress_records) == 1, "no-progress execute should emit one record")
        assert_true(no_progress_records[0].get("action") == "execute", "no-progress command should be executed once")
        assert_true(
            no_progress_records[0].get("action_after_execute") == "stop_no_progress",
            "no-progress execute should stop explicitly",
        )
        assert_true(no_progress_records[0].get("progressed") is False, "no-progress record should mark no progress")
        status_index_payload = read_json(plan_index_path)
        status_outputs = status_index_payload.get("latest_validation_plan_outputs") or {}
        assert_true(status_outputs.get("run_dir") == str(status_dir), "status-only should refresh the stable plan index")
        assert_true(
            status_outputs.get("validation_status_json") == str(status_dir / "validation_status.json"),
            "stable plan index should point at validation_status.json",
        )
        latest_index_payload = read_json(latest_index)
        assert_true(
            latest_index_payload.get("latest_validation_plan_outputs", {}).get("run_dir") == str(status_dir),
            "calibration latest index should reflect the latest status output dir",
        )

        execute_status_path = status_dir / "execute_next_validation_status.json"
        execute_orch_dir = tmp_root / "orchestrator_execute_run"
        execute_status_candidates = tmp_root / "orchestrator_execute_status_candidates.jsonl"
        execute_empty_chunk = tmp_root / "orchestrator_execute_empty_chunk.jsonl"
        write_jsonl(execute_status_candidates, pending_rows[:2])
        write_jsonl(execute_empty_chunk, [])
        execute_status = dict(status)
        execute_status.update(
            {
                "complete": False,
                "next_validation_command": (
                    f"{sys.executable} shell/bonect_real_validate_candidates.py "
                    f"--candidates-json {execute_empty_chunk} "
                    f"--status-candidates-json {execute_status_candidates} "
                    f"--output-dir {execute_orch_dir} "
                    "--pending-only --resume --retry-failed --run-partial-evaluation-after-status"
                ),
            }
        )
        write_json(execute_status_path, execute_status)
        execute_orch_output = status_dir / "orchestrator_execute.json"
        execute_orch = run_command(
            [
                sys.executable,
                "shell/bonect_validation_queue_orchestrator.py",
                "--status-json",
                str(execute_status_path),
                "--execute",
                "--allow-no-progress",
                "--output-json",
                str(execute_orch_output),
            ]
        )
        execute_payload = read_json(execute_orch_output)
        execute_records = execute_payload.get("records") or []
        assert_true(len(execute_records) == 1, "orchestrator execute should emit one record")
        assert_true(execute_records[0].get("action") == "execute", "orchestrator should execute when requested")
        assert_true(int(execute_records[0].get("returncode")) == 0, "orchestrator execute command should pass")
        assert_true(execute_records[0].get("progressed") is False, "allow-no-progress test should record no progress")
        assert_true(execute_payload.get("allow_no_progress") is True, "allow-no-progress flag should be recorded")
        assert_true((execute_orch_dir / "validation_status.json").exists(), "orchestrator execute should refresh status")
        execute_status_payload = read_json(execute_orch_dir / "validation_status.json")
        assert_true(
            (execute_status_payload.get("post_status_partial_evaluation") or {}).get("status") == "ok",
            "orchestrator execute should run post-status partial evaluation through the validator",
        )

        auto_status_dir = tmp_root / "auto_status_after_run"
        auto_status_candidates = tmp_root / "auto_status_candidates.jsonl"
        auto_chunk = tmp_root / "auto_empty_chunk.jsonl"
        auto_rows = pending_rows[:2]
        write_jsonl(auto_status_candidates, auto_rows)
        write_jsonl(auto_chunk, [])
        write_jsonl(
            auto_status_dir / "validation_results.jsonl",
            [
                {
                    "scenario": auto_rows[0].get("scenario"),
                    "candidate_id": auto_rows[0].get("candidate_id"),
                    "status": "ok",
                    "actual_score": 1.0,
                    "actual_resource": {"cpu": 1.0, "memory": 1.0, "io": 1.0},
                }
            ],
        )
        run_command(
            [
                sys.executable,
                "shell/bonect_real_validate_candidates.py",
                "--candidates-json",
                str(auto_chunk),
                "--status-candidates-json",
                str(auto_status_candidates),
                "--output-dir",
                str(auto_status_dir),
            "--pending-only",
            "--resume",
            "--retry-failed",
            "--run-partial-evaluation-after-status",
        ]
    )
        auto_status = read_json(auto_status_dir / "validation_status.json")
        assert_true(int(auto_status.get("candidate_count") or 0) == 2, "auto status should use status candidates")
        assert_true(int(auto_status.get("ok_count") or 0) == 1, "auto status ok count")
        assert_true(int(auto_status.get("pending_count") or 0) == 1, "auto status pending count")
        assert_true(
            "--strict-goal" not in str(auto_status.get("recommended_evaluation_command")),
            "auto incomplete status should recommend partial evaluation",
        )
        auto_eval = auto_status.get("post_status_partial_evaluation") or {}
        assert_true(auto_eval.get("status") == "ok", "auto run should run partial evaluation when requested")
        assert_true(Path(str(auto_status.get("partial_evaluation_json"))).exists(), "auto partial evaluation JSON should exist")
        auto_next_batch = auto_status.get("next_validation_batch") or {}
        assert_true(auto_next_batch, "auto status should expose the next validation batch")
        assert_true(int(auto_next_batch.get("candidate_count") or 0) == 1, "auto next batch should contain the remaining row")
        assert_true(Path(str(auto_next_batch.get("queue_jsonl"))).exists(), "auto next batch queue should exist")
        assert_true(
            "--status-candidates-json" in str(auto_status.get("next_validation_command")),
            "auto next validation command should refresh global status candidates",
        )
        assert_true((auto_status_dir / "scenario_batches.md").exists(), "auto run should refresh scenario runbook")
        auto_config = read_json(auto_status_dir / "validation_run_config.json")
        assert_true(
            auto_config.get("status_candidates_json") == str(auto_status_candidates),
            "run config should record status candidate source",
        )

        synthetic_labels = tmp_root / "synthetic_full_labels.jsonl"
        label_count = make_synthetic_full_labels(Path(default_payload["validation_manifest_json"]), synthetic_labels)
        assert_true(label_count == 60, "synthetic manifest labels should include all top15 rows across 4 scenarios")
        synthetic_prefix = tmp_root / "synthetic_full_eval"
        synthetic_proc = evaluator(
            synthetic_prefix,
            "--extra-labels",
            str(synthetic_labels),
            "--strict-goal",
            check=False,
        )
        assert_true(synthetic_proc.returncode == 0, f"synthetic strict should pass, got rc={synthetic_proc.returncode}")
        validate_full_payload(read_json(synthetic_prefix.with_suffix(".json")))

        synthetic_dir = tmp_root / "synthetic_label_dir"
        write_jsonl(synthetic_dir / "validation_results.jsonl", read_jsonl(synthetic_labels))
        synthetic_dir_prefix = tmp_root / "synthetic_dir_eval"
        synthetic_dir_proc = evaluator(
            synthetic_dir_prefix,
            "--extra-labels",
            str(synthetic_dir),
            "--strict-goal",
            check=False,
        )
        assert_true(
            synthetic_dir_proc.returncode == 0,
            f"synthetic directory strict should pass, got rc={synthetic_dir_proc.returncode}",
        )
        synthetic_dir_payload = read_json(synthetic_dir_prefix.with_suffix(".json"))
        validate_full_payload(synthetic_dir_payload)
        resolved = synthetic_dir_payload.get("resolved_extra_label_files") or []
        assert_true(
            str((synthetic_dir / "validation_results.jsonl").resolve()) in resolved,
            "directory extra-label input should resolve to validation_results.jsonl",
        )

        duplicate_labels = tmp_root / "synthetic_duplicate_labels.jsonl"
        duplicate_rows = read_jsonl(synthetic_labels)
        duplicate_rows.append(dict(duplicate_rows[0]))
        write_jsonl(duplicate_labels, duplicate_rows)
        duplicate_prefix = tmp_root / "synthetic_duplicate_eval"
        duplicate_proc = evaluator(
            duplicate_prefix,
            "--extra-labels",
            str(duplicate_labels),
            "--strict-goal",
            check=False,
        )
        assert_true(duplicate_proc.returncode == 0, f"duplicate strict should pass, got rc={duplicate_proc.returncode}")
        duplicate_payload = read_json(duplicate_prefix.with_suffix(".json"))
        validate_full_payload(duplicate_payload)
        duplicate_audit = duplicate_payload.get("extra_label_resolution_audit") or {}
        assert_true(
            int(duplicate_audit.get("total_duplicate_candidate_groups") or 0) == 1,
            "duplicate extra-label audit should identify the repeated candidate",
        )
        assert_true(
            int(duplicate_audit.get("total_conflict_candidate_groups") or 0) == 0,
            "same-score duplicate labels should not be conflicts",
        )

        conflict_labels = tmp_root / "synthetic_conflict_labels.jsonl"
        conflict_rows = read_jsonl(synthetic_labels)
        conflict_row = dict(conflict_rows[0])
        conflict_row["actual_score"] = float(conflict_row["actual_score"]) + 123.0
        conflict_rows.append(conflict_row)
        write_jsonl(conflict_labels, conflict_rows)
        conflict_prefix = tmp_root / "synthetic_conflict_eval"
        conflict_proc = evaluator(
            conflict_prefix,
            "--extra-labels",
            str(conflict_labels),
            "--strict-goal",
            check=False,
        )
        assert_true(conflict_proc.returncode == 2, f"conflicting strict should fail, got rc={conflict_proc.returncode}")
        conflict_payload = read_json(conflict_prefix.with_suffix(".json"))
        conflict_audit = conflict_payload.get("extra_label_resolution_audit") or {}
        assert_true(
            int(conflict_audit.get("total_conflict_candidate_groups") or 0) == 1,
            "conflict audit should identify one conflicting candidate",
        )
        conflict_gate = conflict_payload.get("goal_gate") or {}
        assert_true(conflict_gate.get("label_conflict_pass") is False, "conflict gate should fail")
        assert_true(conflict_gate.get("strict_goal_pass") is False, "strict goal must fail on conflicting labels")

        print(f"[OK] Bonect robust ranking evaluator checks passed in {tmp_root}", flush=True)
        return 0
    finally:
        if args.keep_output:
            print(f"[Keep] {tmp_root}", flush=True)
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
