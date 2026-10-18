#!/usr/bin/env python3
"""Unit checks for delayed fresh-task launch helpers."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

import launch_double_loop_task_after_external_idle as delayed


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "scheduler_state.json").write_text(
            json.dumps(
                {
                    "tasks": {
                        "a14_r1": {
                            "status": "preparing",
                            "worker_pid": os.getpid(),
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        assert delayed.external_root_busy(root)
        (root / "scheduler_state.json").write_text(
            json.dumps({"tasks": {"a14_r1": {"status": "completed"}}}),
            encoding="utf-8",
        )
        assert not delayed.external_root_busy(root)
        assert delayed.target_task_completed(root, "a14_r1")

    args = argparse.Namespace(
        config=Path("/tmp/config.json"),
        target_scheduler_root=Path("/tmp/scheduler"),
        target_overlay_root=Path("/tmp/overlay"),
        target_runtime_overlay_root=Path("/tmp/runtime"),
        task_id="a14_r1",
        max_active=4,
    )
    command = delayed.scheduler_command(args)
    assert command[command.index("--only-task") + 1] == "a14_r1"
    assert command[command.index("--max-active") + 1] == "4"
    print("delayed fresh-task launch tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
