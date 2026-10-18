#!/usr/bin/env python3
"""Create an immutable effective terminal-state projection from reconciliation."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reconcile_mysql_algorithm_c_async_terminal_v1 as reconcile


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_symlink() or not path.is_file():
        raise argparse.ArgumentTypeError(f"regular file required: {path}")
    return path


def project(source_state_path: Path, reconciliation_path: Path) -> dict[str, Any]:
    state = reconcile.load(source_state_path)
    correction = reconcile.load(reconciliation_path)
    scene = state.get("async_scene")
    source_run = source_state_path.parent
    expected = reconcile.reconcile(source_run)
    if (
        correction.get("schema_version")
        != "mysql_algorithm_c_async_terminal_reconciliation_v1"
        or correction.get("status") != "PASS"
        or correction.get("effective_status") != "COMPLETE"
        or correction.get("scene") != scene
        or correction.get("source_run_state", {}).get("sha256")
        != reconcile.sha256(source_state_path)
        or correction.get("terminal_decision") != expected.get("terminal_decision")
        or correction.get("physical_rerun_required") is not False
        or correction.get("new_physical_measurement_invoked") is not False
    ):
        raise RuntimeError("terminal reconciliation binding differs")
    projected = copy.deepcopy(state)
    projected.update({
        "status": "COMPLETE",
        "completion_reason": "all_active_log_domain_widths_reached_1pct",
        "effective_terminal_state_projection": True,
        "source_state_immutable": True,
        "source_state_path": str(source_state_path),
        "source_state_sha256": reconcile.sha256(source_state_path),
        "terminal_reconciliation_path": str(reconciliation_path),
        "terminal_reconciliation_sha256": reconcile.sha256(reconciliation_path),
        "new_physical_measurement_invoked_for_projection": False,
        "accepted_or_rejected_credit_increment_for_projection": 0,
    })
    return projected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-state", type=regular_file, required=True)
    parser.add_argument("--reconciliation", type=regular_file, required=True)
    parser.add_argument("--output", type=reconcile.fresh_path, required=True)
    args = parser.parse_args()
    reconcile.atomic(args.output, project(args.source_state, args.reconciliation))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
