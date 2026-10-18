#!/usr/bin/env python3
"""TPC-C target-10 controller with zero-call global fingerprint admission.

Every generated Top-5 is compared with all structured fingerprint evidence in
the controller state before a launcher can be sealed.  A collision in a
validation or formal selection quarantines the whole selection and routes the
same ordinal through fresh diagnostic discovery without a workload call.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v28_role_aware as v28
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v28_role_aware as v28


def global_prelaunch_fingerprint_gate(
    selection: Mapping[str, Any],
    mode: str,
    controller_state: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return collisions against every sealed structured historical ID."""
    candidate_ids = [
        str((candidate.get("three_level_fingerprint") or {}).get("id") or "")
        for candidate in selection.get("top5") or []
    ]
    historical_ids = {
        str(identifier)
        for audit in controller_state.get("audits") or []
        for field in ("fingerprint_ids", "physical_fingerprint_ids")
        for identifier in audit.get(field) or []
    }
    historical_ids.update(
        str(identifier)
        for identifier in controller_state.get("quarantined_fingerprints") or []
    )
    for source in controller_state.get("sources") or []:
        source_path = Path(str(source.get("selection_path") or "")).resolve()
        expected_sha256 = str(source.get("selection_sha256") or "")
        if (
            not source_path.is_file()
            or not expected_sha256
            or v25.sha256(source_path) != expected_sha256
        ):
            raise RuntimeError(
                "historical selection source failed prelaunch SHA check: "
                f"{source_path}"
            )
        source_selection = v25.load_json(source_path)
        historical_ids.update(
            str(
                (candidate.get("three_level_fingerprint") or {}).get("id")
                or ""
            )
            for candidate in source_selection.get("top5") or []
        )
    historical_ids.discard("")
    collisions = sorted(set(candidate_ids) & historical_ids)
    return {
        "contract": "tpcc_global_three_level_fingerprint_prelaunch_v1",
        "checked": True,
        "mode": mode,
        "candidate_fingerprint_ids": candidate_ids,
        "historical_fingerprint_ids": sorted(historical_ids),
        "collision_ids": collisions,
        "passed": not collisions,
        "workload_calls_added": 0,
        "manual_candidate_injection": False,
    }


def _configure_from_argv(argv: Sequence[str]) -> None:
    v28._configure_from_argv(argv)


def main() -> int:
    _configure_from_argv(sys.argv)
    v25.prelaunch_fingerprint_gate = global_prelaunch_fingerprint_gate
    return v28.main()


if __name__ == "__main__":
    raise SystemExit(main())
