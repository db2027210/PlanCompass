#!/usr/bin/env python3
"""Zero-workload preflight for the XP v35 authoritative R0 builder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import resume_xp_fresh0_fixed_contract_target10_v33 as v33
    import resume_xp_fresh0_fixed_contract_target10_v34 as v34
    import resume_xp_fresh0_fixed_contract_target10_v35 as v35
    import xp_v34_process_guard as process_guard
except ModuleNotFoundError:
    from tools import resume_xp_fresh0_fixed_contract_target10_v33 as v33
    from tools import resume_xp_fresh0_fixed_contract_target10_v34 as v34
    from tools import resume_xp_fresh0_fixed_contract_target10_v35 as v35
    from tools import xp_v34_process_guard as process_guard


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BUNDLE = Path("/tmp/pg_xp_unattended_20260730_1235/bundle_fix_v33_plan_quarantine")
CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v35_20260803T1220"
V34_CAMPAIGN = PROJECT / "history/xp_final_algorithm_fresh10_v34_20260803T1205"
FORMAL = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/"
    "formal10_after_v3g_entry_gate_20260729T1040"
)


def _require_sha(path: Path, digest: str) -> None:
    if not path.is_file() or v33.sha256(path) != digest:
        raise RuntimeError(f"XP v35 preflight SHA mismatch: {path}")


def main() -> int:
    if CAMPAIGN.exists():
        raise RuntimeError(f"XP v35 campaign already exists: {CAMPAIGN}")
    pins = {
        BUNDLE / "bundle_manifest.json": "323032499d52473b26e1db50ce0320dbb6951c4baae55fa72a4ac1d854277cb5",
        BUNDLE / "fresh0_initial_state.json": "f6f24906986e91a1cef1756dd75ce8cc738affc08afef2ebbdb246c73955a80f",
        BUNDLE / "fresh0_initialization_manifest.json": "15a956be96a50ef0578a2d7589708af132b2f1f5a370ee986de021de7f34df37",
        PROJECT / "tools/resume_xp_fresh0_fixed_contract_target10_v33.py": "364b5b28973d04a4fff039158de73ef7ce22e4519d4ebdb4e1c41942ebec94f8",
        PROJECT / "tools/resume_xp_fresh0_fixed_contract_target10_v34.py": "b9c86b3bf17bc762077585309f414b0ecfc2e2d4deab357fb5727bde7105a979",
        PROJECT / "tools/resume_xp_fresh0_fixed_contract_target10_v35.py": "b4f0ec2b59df319c2f68ad5d5f5861392f797d19ec9f5cb6448beef1cee98d38",
        PROJECT / "tests/test_xp_v35_authoritative_formal_root.py": "7a074da94124b6180779fa6d800bd01947b833317f18c5190bb413b7de173123",
        PROJECT / "history/xp_v34_partial_r0_builder_technical_audit_20260803.json": "08f240905b4806999cc179f5305591500fe1ce2c3f2b99ade8c59702301a19ac",
        V34_CAMPAIGN / "v34_pre_r0_restore_audit.json": "59d418c97ad20a74018d7baaa9551651fbbfcb041f719b515254e117e97bfe8a",
        FORMAL / "formal_manifest.json": "b36d2db6a013421f57c0a41922f62e676be841641b583986a8440d3134165081",
        FORMAL / "optimizer_history_2026-07-29_104036.jsonl": "fafb67e227b8015c01d680f392d8b5b33a98343c754dd86d2bde9c745fce9719",
        FORMAL / "state/formal_search_accepted_counter.txt": "9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa",
    }
    for path, digest in pins.items():
        _require_sha(path, digest)
    completed = subprocess.run(
        [
            sys.executable,
            str(PROJECT / "tools/verify_relative_sha256_manifest.py"),
            "--root",
            str(BUNDLE),
            "--manifest",
            str(BUNDLE / "bundle_manifest.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise RuntimeError("XP v35 bundle relative SHA verification failed")
    partial = v33.load(
        PROJECT / "history/xp_v34_partial_r0_builder_technical_audit_20260803.json"
    )
    state = v33.load(V34_CAMPAIGN / "state.json")
    r0_dir = V34_CAMPAIGN / "batches/r0fresh000"
    if (
        partial.get("physical_workload_calls") != 0
        or partial.get("valid_replays") != 0
        or partial.get("technical_failures") != 1
        or partial.get("formal_state_inheritance_allowed") is not False
        or partial.get("restore_audit_reusable") is not True
        or state.get("accepted_count") != 0
        or state.get("incumbent_score") is not None
        or (state.get("gradient_state") or {}).get("update_count") != 0
        or not r0_dir.is_dir()
        or any(r0_dir.iterdir())
    ):
        raise RuntimeError("XP v34 partial campaign is not quarantined 0-call")
    if (FORMAL / "state/formal_search_accepted_counter.txt").read_text().strip() != "0":
        raise RuntimeError("XP formal authority counter is not zero")
    v35._authoritative_formal_root(BUNDLE)
    config = v33.load(BUNDLE / "authoritative_round9/config.json")
    readiness = v35.verify_postgres_with_retry(config)
    postgres = readiness["postgres"]
    processes = process_guard.proc_processes(
        excluded_pids=process_guard._ancestor_pids(os.getpid())
    )
    active = process_guard.active_matches(
        processes,
        (
            r"resume_xp_fresh0_fixed_contract_target10_v3[345]\.py",
            r"run_xp_incumbent_revalidation_once\.py",
        ),
    )
    if active:
        raise RuntimeError(f"XP live controller/R0 process exists: {active}")
    print(
        json.dumps(
            {
                "active_process_count": 0,
                "authoritative_formal_root": str(BUNDLE / "authoritative_round9"),
                "database": postgres["database"],
                "formal_accepted_count": 0,
                "passed": True,
                "physical_workload_calls_added": 0,
                "postgres_readiness_attempt_count": readiness["attempt_count"],
                "postgres_readiness_failure_count": readiness["failure_count"],
                "restore_reexecution_required": False,
                "v34_partial_training_eligible": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
