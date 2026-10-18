#!/usr/bin/env python3
"""Tests for Algorithm C formal startup stability evidence."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shell.prepare_pg_algorithm_c_budgeted_formal import (
    FORMAL_ROLE,
    SCENES,
    algorithm_design_sha256,
    source_hashes,
)
from shell import verify_pg_algorithm_c_budgeted_formal_startup as verifier


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def append_json(path: Path, value) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")


def valid_baseline_event(scene: str, worker: str, stamp: float):
    return {
        "event": "algorithm_c_baseline_worker_validation",
        "scene": scene,
        "time": stamp,
        "validation": {
            "baseline_repetition": 1,
            "worker_key": worker,
            "status": "ok",
            "technical_failure": False,
            "resource_stats": {"resource_monitor_ok": True},
            "workload_completeness": {"passed": True},
        },
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="algorithm-c-startup-") as temporary:
        root = Path(temporary).resolve()
        sealed_source_hashes = source_hashes()
        write_json(root / "budgeted_launch_manifest.json", {
            "campaign_role": FORMAL_ROLE,
            "algorithm_revision": "coverage_revisit_v2",
            "algorithm_design_sha256": algorithm_design_sha256(sealed_source_hashes),
            "source_sha256": sealed_source_hashes,
        })
        write_json(root / "formal_launch_preflight.json", {"passed": True})
        write_json(root / "launcher_status.json", {
            "phase": "launched",
            "started_at_epoch_s": 100.0,
            "pids": {scene: 1000 + index for index, scene in enumerate(SCENES)},
            "support_pid": 2000,
            "deadline_signals": [],
        })
        for scene in SCENES:
            log_path = root / scene / "algorithm_c.jsonl"
            write_json(log_path, valid_baseline_event(scene, "w1", 101.0))
            append_json(log_path, valid_baseline_event(scene, "w2", 102.0))

        def fake_cmdline(pid: int):
            if pid == 2000:
                return f"python {verifier.REQUIRED_SUPPORT} --campaign-root {root}"
            for index, scene in enumerate(SCENES):
                if pid == 1000 + index:
                    return f"python {verifier.REQUIRED_CONTROLLER} --output-dir {root / scene}"
            return None

        with mock.patch.object(verifier, "process_cmdline", side_effect=fake_cmdline):
            passed = verifier.verify(root)
        assert passed["passed"] is True
        assert passed["source_integrity"]["passed"] is True
        assert passed["support_process_live_and_owned"] is True
        assert all(row["stable_wave"]["passed"] for row in passed["scene_rows"].values())

        for scene in SCENES:
            append_json(root / scene / "algorithm_c.jsonl", {
                "event": "algorithm_c_physical_wave_accounted",
                "time": 102.5,
                "batch_kind": "formal_search",
                "wave_sequence": 1,
                "accounting": {
                    "valid_replays": 2,
                    "technical_failures": 0,
                    "physical_workload_invocations": 2,
                },
            })
        for scene, target in (("tpch_memory", "memory"), ("xp_io", "io")):
            write_json(root / scene / "experience_table_c_only.json", {
                "resource_prediction_calibration": {
                    "resources": {
                        target: {"parameter_support_abstention_enabled": True}
                    }
                }
            })
            append_json(root / scene / "algorithm_c.jsonl", {
                "event": "algorithm_c_global_plan_coverage_ranking",
                "time": 102.6,
                "round_index": 1,
                "target_support_expansion_probe_count": 4,
                "target_support_expansion_uses_qps": False,
                "target_support_expansion_requires_real_replay": True,
            })
        with mock.patch.object(verifier, "process_cmdline", side_effect=fake_cmdline):
            search_passed = verifier.verify(root, require_search_wave=True)
        assert search_passed["passed"] is True
        assert search_passed["formal_search_wave_required"] is True

        append_json(root / "xp_io" / "algorithm_c.jsonl", {
            "event": "algorithm_c_candidate_technical_failure",
            "time": 103.0,
        })
        with mock.patch.object(verifier, "process_cmdline", side_effect=fake_cmdline):
            failed = verifier.verify(root)
        assert failed["passed"] is False
        assert "xp_io:no_clean_complete_physical_wave" in failed["errors"]

        write_json(root / "launcher_status.json", {
            "phase": "launched",
            "started_at_epoch_s": 100.0,
            "pids": {scene: 1000 + index for index, scene in enumerate(SCENES)},
            "support_pid": 2000,
            "deadline_signals": [],
        })
        with mock.patch.object(verifier, "process_cmdline", return_value=None):
            missing = verifier.verify(root)
        assert missing["passed"] is False
        assert "support_process_missing" in missing["errors"]
        assert all(
            "controller_process_missing" in row["errors"]
            for row in missing["scene_rows"].values()
        )

    print("PASS: Algorithm C formal startup stability verifier", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
