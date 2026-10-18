#!/usr/bin/env python3
"""Unit checks for the idle v16 scheduler handoff."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import continue_double_loop_v16_after_idle as continuation


def main() -> int:
    state = {
        "tasks": {
            "a13_r1": {
                "task_id": "a13_r1",
                "order": 0,
                "status": "needs_review",
            },
            "b6_r2": {
                "task_id": "b6_r2",
                "order": 1,
                "status": "held",
            },
            "b6_r3": {
                "task_id": "b6_r3",
                "order": 2,
                "status": "held",
            },
            "done": {
                "task_id": "done",
                "order": 3,
                "status": "completed",
            },
        }
    }
    command = continuation.continuation_command(
        state,
        config=Path("/tmp/config.json"),
        max_active=4,
    )
    assert command.count("--retry-task") == 1
    assert command[command.index("--retry-task") + 1] == "a13_r1"
    assert command.count("--release-hold") == 2
    assert "b6_r2" in command and "b6_r3" in command
    assert command[command.index("--max-active") + 1] == "4"
    excluded = continuation.continuation_command(
        state,
        config=Path("/tmp/config.json"),
        max_active=4,
        exclude_retry_task_ids={"a13_r1"},
    )
    assert "--retry-task" not in excluded
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "scheduler_state.json").write_text(
            json.dumps(
                {
                    "tasks": {
                        "a14_r1_retry4": {
                            "task_id": "a14_r1_retry4",
                            "status": "preparing",
                            "worker_pid": os.getpid(),
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        assert continuation.externally_owned_task_ids([root]) == {"a14_r1"}
        (root / "scheduler_state.json").write_text(
            json.dumps(
                {
                    "tasks": {
                        "a14_r1": {
                            "task_id": "a14_r1",
                            "status": "completed",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        assert continuation.externally_owned_task_ids([root]) == {"a14_r1"}
        assert continuation.externally_owned_task_ids(
            [root],
            include_completed=False,
        ) == set()
    print("v16 idle continuation tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
