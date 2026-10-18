#!/usr/bin/env python3
"""Regression test for target-only stability plus all-resource R0 gating."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import run_tpcc_sensitive_stable_point as runner
from run_tpcc_sensitive_stable_point import r0_profile_window_means_pct


def main() -> None:
    assessment = {
        "cross_window_resource_checks": [
            {
                "end_elapsed_seconds": 180,
                "fields": [
                    {"field": "cpu_utilization", "mean_of_window_means": 0.8886},
                    {"field": "memory_utilization", "mean_of_window_means": 0.4586},
                    {"field": "io_utilization", "mean_of_window_means": 0.4869},
                ],
            }
        ]
    }
    assert r0_profile_window_means_pct(
        assessment,
        end_elapsed_seconds=180,
    ) == {
        "cpu": 88.86,
        "memory": 45.86,
        "io": 48.69,
    }
    assert r0_profile_window_means_pct(
        assessment,
        end_elapsed_seconds=210,
    ) == {}

    with tempfile.TemporaryDirectory(prefix="tpcc-stable-point-test-") as temp_dir:
        output_dir = Path(temp_dir)
        runner._FAILURE_CONTEXT.clear()
        runner._FAILURE_CONTEXT.update(
            {
                "output_dir": output_dir,
                "stage": "measurement",
                "samples": [
                    {
                        "cpu_utilization": 0.86,
                        "memory_utilization": 0.42,
                        "io_utilization": 0.49,
                    }
                ],
                "counter_state": {
                    "error_count": 1.0,
                    "counter_reset_count": 0.0,
                    "reconnect_count": 1.0,
                    "last_error": "connection lost",
                },
            }
        )
        try:
            raise RuntimeError("synthetic failure")
        except RuntimeError as exc:
            summary_path = runner.persist_failure_summary(exc)
        assert summary_path == output_dir / "summary.json"
        failure = json.loads(summary_path.read_text(encoding="utf-8"))
        assert failure["status"] == "failed"
        assert failure["failure_stage"] == "measurement"
        assert failure["resource_utilization"] == {
            "cpu": 0.86,
            "memory": 0.42,
            "io": 0.49,
        }
        assert failure["database_counter_continuity"]["passed"] is False
        assert failure["database_counter_continuity"]["error_count"] == 1

        runtime_root = output_dir / "owned-runtime"
        runtime_root.mkdir()
        detached = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=runtime_root,
            start_new_session=True,
        )
        try:
            time.sleep(0.1)
            cleanup = runner.terminate_owned_runtime_processes(runtime_root)
            assert detached.pid in cleanup["matched_pids"]
            assert cleanup["fully_released"] is True
            assert cleanup["remaining_pids"] == []
            assert detached.wait(timeout=2.0) < 0
        finally:
            if detached.poll() is None:
                detached.kill()
                detached.wait()
    print("TPC-C target-only stability/all-resource R0 regression passed", flush=True)


if __name__ == "__main__":
    main()
