#!/usr/bin/env python3
"""Generate XP v37's automatic exact-point repeat without a workload call."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v37_20260803"
FORMAL = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/"
    "formal10_after_v3g_entry_gate_20260729T1040"
)
if str(PROJECT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "tools"))
if str(BUNDLE) not in sys.path:
    sys.path.insert(0, str(BUNDLE))

import xp_unattended_adapters as adapter  # type: ignore
from xp_unattended_adapters import FormalPaths  # type: ignore
import xp_v37_robust_state_patch as v37


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    state_path = CAMPAIGN / "state.json"
    state_sha = sha256(state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    paths = FormalPaths(
        root=BUNDLE,
        state=state_path,
        batch_root=CAMPAIGN / "batches",
        formal_manifest=FORMAL / "formal_manifest.json",
        authority_counter=FORMAL / "state/formal_search_accepted_counter.txt",
        authority_history=FORMAL / "optimizer_history_2026-07-29_104036.jsonl",
        canonical_state=BUNDLE / "fresh0_initial_state.json",
        canonical_manifest=BUNDLE / "fresh0_initialization_manifest.json",
        acceptance_manifest=FORMAL / "formal_manifest.json",
        query_snapshot=BUNDLE / "authority_refresh_accepted4_v5_20260731/query_snapshot_v3.json",
        replay_audit=CAMPAIGN / "batches/seed000/replay_summary.json",
        rank1_evidence=CAMPAIGN / "batches/seed000/selection.json",
        restore_script=Path(
            "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
            "hybench_xp_compute00_59630/experience_sgd/"
            "restore_with_physical_io_reapply_attempt25.sh"
        ),
    )
    v37.install(adapter)
    with tempfile.TemporaryDirectory(prefix="xp_v37_repeat_") as raw:
        output = Path(raw)
        selection = adapter.generate_selection(paths, "cont002", state, output)
        roles = [item["selection_role"] for item in selection["top5"]]
        expected = [
            "trusted_calibrated_rank",
            "trusted_calibrated_rank",
            "trusted_calibrated_rank",
            "maximin_uncertain_probe",
            "maximin_uncertain_probe",
        ]
        assert selection["batch_mode"] == "diagnostic_validation"
        assert roles == expected
        audit = selection["v37_local_noise_acquisition"]
        assert audit["contract"] == v37.NO_PRIOR_REPEAT_NOISE_CONTRACT
        assert audit["post_repeat_entry_gate_relaxed"] is False
        assert audit["manual_candidate_injection"] is False
        assert selection["score_span_noise_authority"]["bound"] == 0.0
        source = json.loads(
            (CAMPAIGN / "batches/cont001/selection.json").read_text(encoding="utf-8")
        )
        source_params = {
            json.dumps(item["candidate_params"], sort_keys=True)
            for item in source["top5"]
        }
        repeat_params = {
            json.dumps(item["candidate_params"], sort_keys=True)
            for item in selection["top5"]
        }
        assert repeat_params == source_params
        assert not list(output.rglob("workload.log"))
        assert sha256(state_path) == state_sha
    print("PASS XP v37 automatic exact-point repeat generation (zero workload)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
