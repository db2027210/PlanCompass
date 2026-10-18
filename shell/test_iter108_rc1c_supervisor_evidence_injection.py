"""iter108 RC#1c supervisor evidence-injection tests.

These tests do not launch Bonect, PG, qemu, HyBench, or the optimizer. They
exercise the supervisor-side control-decision metadata that carries Memory
replay-calibration evidence into the next runner launch.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.bonect_supervisor import (  # type: ignore
    TriggerDecision,
    build_memory_replay_calibration_injection,
    decision_to_dict,
    write_decision,
)


def _check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[PASS] {name}", flush=True)
        return
    print(f"[FAIL] {name}: {detail}", flush=True)
    raise AssertionError(f"{name}: {detail}")


def _feedback_event(est_mem: float, act_mem: float) -> Dict[str, Any]:
    return {
        "event": "feedback_estimation_error",
        "estimated_resource": {"cpu": 1.0, "memory": est_mem, "io": 1.0},
        "actual_resource": {"cpu": 1.0, "memory": act_mem, "io": 1.0},
    }


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _memory_decision(history_path: Path) -> TriggerDecision:
    return TriggerDecision(
        run_id="pg_bonect_phase2_iter108_test",
        bottleneck="memory",
        benchmark="hybench_ap",
        history_path=str(history_path),
        latest_round=0,
        triggers=[
            {
                "name": "small_loop_ranking_gate_failed",
                "rounds": [0],
                "required_action": "stop_and_fix_small_loop_ranking",
            }
        ],
        action="stop_and_fix_small_loop_ranking",
        reason="small_loop_ranking_gate_failed",
        created_at=datetime.now().isoformat(timespec="seconds"),
    )


def test_memory_decision_builds_evidence_and_next_run_args() -> None:
    with tempfile.TemporaryDirectory(prefix="bonect_iter108_") as tmp:
        run_dir = Path(tmp) / "history" / "pg_bonect_phase2_iter108_test"
        history_path = run_dir / "memory" / "hybench_ap_optimizer_history_2026-07-07_000000.jsonl"
        rows = [_feedback_event(3.380, 3.380)]
        rows.extend(_feedback_event(3.360, 3.470) for _ in range(6))
        _write_jsonl(history_path, rows)

        injection = build_memory_replay_calibration_injection(
            run_dir,
            _memory_decision(history_path),
            baseline_memory=3.380,
        )

        evidence_path = Path(str(injection.get("evidence_path")))
        next_args = injection.get("next_run_args") or []
        _check("memory_injection_enabled", injection.get("enabled") is True, str(injection))
        _check("evidence_file_written", evidence_path.is_file(), str(evidence_path))
        _check(
            "next_args_enable_calibration",
            "--memory-replay-calibration-enabled" in next_args,
            str(next_args),
        )
        _check(
            "next_args_include_evidence_path",
            "--memory-replay-calibration-evidence-path" in next_args
            and str(evidence_path) in next_args,
            str(next_args),
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        _check(
            "evidence_nonempty",
            evidence.get("calibration_count", 0) >= 7
            and evidence.get("predicted_down_actual_up_count", 0) >= 6,
            str(evidence),
        )


def test_write_decision_embeds_injection_json_and_markdown() -> None:
    with tempfile.TemporaryDirectory(prefix="bonect_iter108_") as tmp:
        run_dir = Path(tmp) / "history" / "pg_bonect_phase2_iter108_test"
        history_path = run_dir / "memory" / "hybench_ap_optimizer_history_2026-07-07_000000.jsonl"
        rows = [_feedback_event(3.380, 3.380)]
        rows.extend(_feedback_event(3.360, 3.470) for _ in range(6))
        _write_jsonl(history_path, rows)

        md_path, json_path = write_decision(run_dir, _memory_decision(history_path))
        data = json.loads(json_path.read_text(encoding="utf-8"))
        injection = data.get("memory_replay_calibration_injection") or {}
        md_text = md_path.read_text(encoding="utf-8")
        _check("decision_json_has_injection", injection.get("enabled") is True, str(data))
        _check(
            "decision_json_has_next_args",
            "--memory-replay-calibration-evidence-path"
            in (injection.get("next_run_args") or []),
            str(injection),
        )
        _check(
            "decision_markdown_has_section",
            "Memory Replay Calibration Injection" in md_text
            and "Next-run runner args" in md_text,
            md_text[:500],
        )


def test_non_memory_decision_does_not_enable_memory_calibration() -> None:
    with tempfile.TemporaryDirectory(prefix="bonect_iter108_") as tmp:
        run_dir = Path(tmp) / "history" / "pg_bonect_phase2_iter108_test"
        history_path = run_dir / "cpu" / "hybench_xp_optimizer_history_2026-07-07_000000.jsonl"
        _write_jsonl(history_path, [_feedback_event(3.360, 3.470)])
        decision = TriggerDecision(
            run_id="pg_bonect_phase2_iter108_test",
            bottleneck="cpu",
            benchmark="hybench_xp",
            history_path=str(history_path),
            latest_round=0,
            triggers=[],
            action="stop_and_fix_small_loop_ranking",
            reason="small_loop_ranking_gate_failed",
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        injection = build_memory_replay_calibration_injection(run_dir, decision)
        _check("non_memory_disabled", injection.get("enabled") is False, str(injection))
        _check(
            "non_memory_reason",
            injection.get("reason") == "not_memory_bottleneck",
            str(injection),
        )


def test_empty_memory_evidence_is_not_enabled() -> None:
    with tempfile.TemporaryDirectory(prefix="bonect_iter108_") as tmp:
        run_dir = Path(tmp) / "history" / "pg_bonect_phase2_iter108_test"
        history_path = run_dir / "memory" / "hybench_ap_optimizer_history_2026-07-07_000000.jsonl"
        _write_jsonl(history_path, [{"event": "not_feedback_estimation_error"}])
        injection = build_memory_replay_calibration_injection(
            run_dir,
            _memory_decision(history_path),
        )
        _check("empty_evidence_disabled", injection.get("enabled") is False, str(injection))
        _check(
            "empty_evidence_reason",
            injection.get("reason") == "memory_replay_calibration_evidence_empty",
            str(injection),
        )


def main() -> int:
    print("=" * 70)
    print("iter108 RC#1c supervisor evidence injection tests")
    print("=" * 70)
    tests = [
        test_memory_decision_builds_evidence_and_next_run_args,
        test_write_decision_embeds_injection_json_and_markdown,
        test_non_memory_decision_does_not_enable_memory_calibration,
        test_empty_memory_evidence_is_not_enabled,
    ]
    for test in tests:
        test()
    print("=" * 70)
    print(f"ALL iter108 RC#1c TESTS PASSED ({len(tests)}/{len(tests)})")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
