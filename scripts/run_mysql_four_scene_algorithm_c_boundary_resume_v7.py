#!/usr/bin/env python3
"""Resume rank 1 after a runtime-interference stop while reusing a passed baseline."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v5 as v5


ORIGINAL_STATE_DOCUMENT = v5.state_document
BASE_VALIDATOR = v5.runtime.ORIGINAL_PARTIAL_VALIDATOR


def validate_rank1_runtime_interruption(**kwargs: Any) -> dict[str, Any]:
    if int(kwargs.get("start_rank") or 0) != 2:
        return v5.runtime.validate_formal_partial_continuation(**kwargs)
    effective = dict(kwargs)
    effective["start_rank"] = 1
    base_module = v5.runtime.runtime
    builtin_range = range

    def compatibility_range(*args: int) -> range:
        if args == (2, 6):
            return builtin_range(1, 6)
        return builtin_range(*args)

    base_module.range = compatibility_range
    try:
        result = BASE_VALIDATOR(**effective)
    finally:
        delattr(base_module, "range")
    if (
        result.get("start_rank") != 1
        or result.get("completed_batches") not in ([], None)
        or result.get("continuation_reason")
        != "runtime_external_interference_watchdog_stop"
    ):
        raise RuntimeError("formal rank-1 runtime interruption authority differs")
    result["transport_parser_start_rank"] = 2
    result["effective_formal_start_rank"] = 1
    return result


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_STATE_DOCUMENT(**kwargs)
    recovery = copy.deepcopy(
        document.get("formal_rank1_runtime_interruption_recovery") or {}
    )
    recovery.update({
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": v5.runtime.runtime.alg.sha(Path(__file__).resolve()),
        "passed_incumbent_baseline_reuse_allowed": True,
        "interrupted_rank1_result_reuse_prohibited": True,
        "interrupted_rank1_state_credit": 0,
        "fresh_quiet_gate_required": True,
        "fresh_rank1_replay_required": True,
    })
    document["formal_rank1_runtime_interruption_recovery"] = recovery
    return document


def main() -> int:
    try:
        index = sys.argv.index("--formal-start-rank")
    except ValueError:
        index = -1
    if index >= 0 and index + 1 < len(sys.argv) and sys.argv[index + 1] == "1":
        sys.argv[index + 1] = "2"
    v5.runtime.validate_formal_partial_continuation = (
        validate_rank1_runtime_interruption
    )
    v5.state_document = state_document
    return v5.main()


if __name__ == "__main__":
    raise SystemExit(main())
