#!/usr/bin/env python3
"""Run v44 score separation through the corrected v45 serial main path."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict

try:
    import audit_tpcc_v44_postrun_gate as auditor
    import build_tpcc_v44_plan_regime_score_separation as builder
    import prepare_tpcc_v43_trusted3_sha_bound_handoff as handoff
    import run_tpcc_formal_target10_trusted3_v43_sha_bound as formal_runner
    import run_tpcc_v45_plan_regime_score_separation_strict_serial as diagnostic_runner
    import run_tpcc_v43_trusted3_formal_substrate as formal_substrate
except ModuleNotFoundError:
    from tools import audit_tpcc_v44_postrun_gate as auditor
    from tools import build_tpcc_v44_plan_regime_score_separation as builder
    from tools import prepare_tpcc_v43_trusted3_sha_bound_handoff as handoff
    from tools import run_tpcc_formal_target10_trusted3_v43_sha_bound as formal_runner
    from tools import run_tpcc_v45_plan_regime_score_separation_strict_serial as diagnostic_runner
    from tools import run_tpcc_v43_trusted3_formal_substrate as formal_substrate


PROJECT = Path("/home/anonymous/program/resource_adaptive")
BASELINE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "candidate_measurements/point_20260729_022349_1052396/summary.json"
)
FORMAL_STATE = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"
DIAGNOSTIC_RUNNER = PROJECT / "tools/run_tpcc_v45_plan_regime_score_separation_strict_serial.py"
FORMAL_SUBSTRATE = PROJECT / "tools/run_tpcc_v43_trusted3_formal_substrate.py"
FORMAL_RUNNER = PROJECT / "tools/run_tpcc_formal_target10_trusted3_v43_sha_bound.py"
HANDOFF = PROJECT / "tools/prepare_tpcc_v43_trusted3_sha_bound_handoff.py"
MAX_DIAGNOSTIC_BATCHES = 64


def _sha(path: Path) -> str:
    return builder.v6._sha256(path.resolve())


def _load(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(command: list[str], *, timeout: int = 10800) -> int:
    completed = subprocess.run(command, check=False, timeout=timeout, env=os.environ.copy())
    return int(completed.returncode)


def _verify_runtime(selection: Path, replay_root: Path) -> None:
    payload = _load(selection)
    diagnostic_runner._validate_v44(payload, payload["top5"])
    runtime = diagnostic_runner.load_runtime()
    analysis, generation_root, candidates, _ = runtime[
        "validate_generation_and_top5"
    ](selection, replay_root)
    if generation_root != selection.parent or len(candidates) != 5:
        raise RuntimeError("TPC-C v45 autonomous diagnostic preflight mismatch")
    if formal_substrate.load_runtime()["MAX_REAL_WORKLOAD_INVOCATIONS"] != 3:
        raise RuntimeError("TPC-C v43 formal substrate is not trusted3-only")
    if formal_runner.load_runtime()["TARGET"] != 10:
        raise RuntimeError("TPC-C v43 formal runner target is not 10")
    if not callable(handoff._promote_trusted3) or not callable(auditor.audit_v44):
        raise RuntimeError("TPC-C v44 autonomous branch is incomplete")


def _paths(selection: Path, batch_number: int) -> tuple[Path, Path, Path, Path]:
    generation_root = selection.parent
    replay_root = generation_root / (
        "top5_real_replay_round16_v45_score_separation_diagnostic_02"
        if batch_number == 1
        else "top5_real_replay_round16_v45_score_separation_diagnostic_01"
    )
    ledger = generation_root / "strict_top5_real_replay_round16_v45_ledger_v1.json"
    return replay_root, ledger, replay_root / "events.jsonl", replay_root / "summary.json"


def _prepare_and_launch_formal(
    selection: Path,
    ledger: Path,
    events: Path,
    summary: Path,
    gate_root: Path,
    campaign_root: Path,
    batch_number: int,
    expected_formal_sha: str,
) -> int:
    handoff_root = campaign_root / f"trusted3_handoff_after_diagnostic_{batch_number:02d}"
    formal_batch_root = campaign_root / f"formal_target10_trusted3_batch_{batch_number:02d}"
    if handoff_root.exists() or formal_batch_root.exists():
        raise FileExistsError("TPC-C v43 autonomous formal roots already exist")
    command = [
        sys.executable, "-u", str(HANDOFF),
        "--selection", str(selection), "--expected-selection-sha256", _sha(selection),
        "--ledger", str(ledger), "--expected-ledger-sha256", _sha(ledger),
        "--events", str(events), "--expected-events-sha256", _sha(events),
        "--summary", str(summary), "--expected-summary-sha256", _sha(summary),
        "--formal-state", str(FORMAL_STATE), "--expected-formal-state-sha256", expected_formal_sha,
        "--formal-runner", str(FORMAL_RUNNER), "--expected-formal-runner-sha256", _sha(FORMAL_RUNNER),
        "--diagnostic-runner", str(FORMAL_SUBSTRATE), "--expected-diagnostic-runner-sha256", _sha(FORMAL_SUBSTRATE),
        "--baseline", str(BASELINE), "--expected-baseline-sha256", _sha(BASELINE),
        "--output-root", str(handoff_root), "--formal-batch-root", str(formal_batch_root),
    ]
    if _run(command, timeout=300) != 0:
        return 3
    formal_selection = handoff_root / "formal_trusted3.json"
    formal_gate = handoff_root / "formal_gate.json"
    # The gate-preparation process never launches a workload.  This explicit
    # next call is automatic and occurs only after the persisted gate passed.
    return _run(
        [
            sys.executable, "-u", str(FORMAL_RUNNER),
            str(formal_selection), _sha(formal_selection),
            str(formal_gate), _sha(formal_gate),
            str(FORMAL_SUBSTRATE), str(BASELINE), str(FORMAL_STATE),
            expected_formal_sha, str(formal_batch_root),
        ]
    )


def run(selection: Path, campaign_root: Path, expected_formal_sha: str) -> int:
    for batch_number in range(1, MAX_DIAGNOSTIC_BATCHES + 1):
        replay_root, ledger, events, summary = _paths(selection, batch_number)
        if replay_root.exists() or ledger.exists():
            raise FileExistsError("TPC-C v45 autonomous diagnostic root/ledger already exists")
        _verify_runtime(selection, replay_root)
        returncode = _run(
            [sys.executable, "-u", str(DIAGNOSTIC_RUNNER), str(selection), str(replay_root), str(BASELINE)]
        )
        if returncode != 0 or not all(path.is_file() for path in (ledger, events, summary)):
            return returncode or 2
        if _sha(FORMAL_STATE) != expected_formal_sha:
            raise RuntimeError("TPC-C v45 diagnostic mutated formal state")
        gate_root = campaign_root / f"postrun_gate_diagnostic_{batch_number:02d}"
        if gate_root.exists():
            raise FileExistsError("TPC-C v45 postrun gate root already exists")
        gate_root.mkdir(parents=True)
        gate = auditor.audit_v44(
            selection, ledger, events, summary, FORMAL_STATE, expected_formal_sha
        )
        gate_path = gate_root / "postrun_gate.json"
        auditor.durable._atomic_json(gate_path, gate)
        if gate.get("passed") is True:
            return _prepare_and_launch_formal(
                selection, ledger, events, summary, gate_root, campaign_root,
                batch_number, expected_formal_sha,
            )
        if (
            gate.get("schema_version") == builder.ACQUISITION_GATE_SCHEMA
            and gate.get("acquisition_complete") is not True
        ):
            raise RuntimeError("TPC-C v45 score-separation acquisition failed closed")
        retry_root = campaign_root / f"automatic_retry_score_separation_{batch_number:02d}"
        if retry_root.exists():
            raise FileExistsError("TPC-C v45 retry root already exists")
        retry_root.mkdir(parents=True)
        next_selection = retry_root / "top5.json"
        rebuilt = builder.build(
            selection_path=selection,
            selection_sha256=_sha(selection),
            ledger_path=ledger,
            ledger_sha256=_sha(ledger),
            events_path=events,
            events_sha256=_sha(events),
            summary_path=summary,
            summary_sha256=_sha(summary),
            gate_path=gate_path,
            gate_sha256=_sha(gate_path),
            formal_state_path=FORMAL_STATE,
            formal_state_sha256=expected_formal_sha,
        )
        builder.v6.repeat_v2.import_base().atomic_json(next_selection, rebuilt)
        selection = next_selection
    raise RuntimeError("TPC-C v45 exceeded autonomous diagnostic batch safety cap")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    selection = args.selection.resolve()
    campaign_root = args.campaign_root.resolve()
    initial_replay, _, _, _ = _paths(selection, 1)
    _verify_runtime(selection, initial_replay)
    if args.verify_only:
        print(json.dumps({
            "status": "tpcc_v45_autonomous_score_separation_preflight_passed",
            "diagnostic_gate_automatic": True,
            "gate_fail_starts_new5_automatic": True,
            "gate_pass_starts_formal_target10_automatic": True,
            "probe_formal_call_count": 0,
            "workload_calls_added": 0,
        }, sort_keys=True), flush=True)
        return 0
    return run(selection, campaign_root, args.expected_formal_state_sha256)


if __name__ == "__main__":
    raise SystemExit(main())
