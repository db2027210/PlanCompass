#!/usr/bin/env python3
"""Recover the HUP-aborted v30 validation04 without a workload invocation."""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import math
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

try:
    import tpcc_autonomous_target10_controller_v25 as v25
    import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29
except ModuleNotFoundError:
    from tools import tpcc_autonomous_target10_controller_v25 as v25
    from tools import tpcc_autonomous_target10_controller_v29_prelaunch_fingerprint as v29


PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
V30_STATE = PROJECT_ROOT / "history/tpcc_final_algorithm_fresh0_v30_20260803/autonomous_controller/controller_state.json"
V30_FORMAL = PROJECT_ROOT / "history/tpcc_final_algorithm_fresh0_v30_20260803/formal_state.json"
V31_STATE = PROJECT_ROOT / "history/tpcc_final_algorithm_fresh0_v31_20260803/autonomous_controller/controller_state.json"
V31_FORMAL = PROJECT_ROOT / "history/tpcc_final_algorithm_fresh0_v31_20260803/formal_state.json"
V31_REPORT = PROJECT_ROOT / "history/tpcc_final_algorithm_fresh0_v31_20260803/v31_zero_call_initializer/initializer_report.json"
V31_MANIFEST = Path("/tmp/tpcc_final_fresh0_autonomous_v50_cross_run_stability_v31_20260803/manifest.json")
RUN_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v30_20260803/autonomous_runs_v30/"
    "autonomous_diagnostic_validation_04"
)
LEDGER = RUN_ROOT / "strict_top5_real_replay_round14_ledger_v1.json"
EVENTS = RUN_ROOT / "top5_real_replay_round14_diagnostic_v1/events.jsonl"
SUMMARY = RUN_ROOT / "top5_real_replay_round14_diagnostic_v1/summary.json"
SELECTION = RUN_ROOT / "top5.json"
ROUND_LOCK = RUN_ROOT / "strict_top5_real_replay.lock"
RESTORE = PROJECT_ROOT / "shell/restore_pg5_tpcc_from_template.sh"

EXPECTED_SHA256 = {
    str(V30_STATE): "30c96e3397ec5d98bfcfdd24911e99e44226cb1d37b57febc39ff039480d804c",
    str(V30_FORMAL): "114693ee42c16f4c03460d7d4edb62dcb13d2fd7c86ffbb33d6089d851c93168",
    str(V31_STATE): "37362f012742de134112906b683fc22bed9ebc14f7e45956dd33b493cb46641f",
    str(V31_FORMAL): "489ab1e14ea90bcbaeb99fa2d7759b68c016756fed854397f04f69ed7fe05378",
    str(V31_MANIFEST): "662b7664d1d2bd0dbe4b6a07d4e8ce005168119dbee56d76ee6a501a084e6eb8",
    str(SELECTION): "51578f76b8869c30c5a0ea320afa630832a0faad5f9bbaff2eac0747b210695e",
    str(LEDGER): "afec55e8967745a2b64b8f49518309664abfcfa504de1052a3cd9854ef7853aa",
    str(EVENTS): "841bdac922863a5760955a46b0c4090d7ac2eb400dd0de5c1ae3c51bf47e3d7d",
    str(RESTORE): "1af8eca4b8fbd229f525b627293eb58a51aa3510472c97340b5ea317be5cde59",
}
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0026541154853235117,
    "cpu_tuple_cost": 0.010616461941294047,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
CANDIDATE_PARAMS = {
    "cpu_index_tuple_cost": 0.0010676250000000002,
    "cpu_operator_cost": 0.0005732889448298788,
    "cpu_tuple_cost": 0.01893976810326859,
    "parallel_setup_cost": 500.00001000000003,
    "parallel_tuple_cost": 0.021599999999999987,
    "random_page_cost": 0.8639999999999999,
    "seq_page_cost": 0.21599999999999997,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _psql(sql: str) -> str:
    completed = subprocess.run(
        [
            "psql", "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
            "-p", "59640", "-U", "postgres", "-d", "fullgrid_tpcc", "-Atc", sql,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": "postgres"},
    )
    if completed.returncode != 0:
        raise RuntimeError(f"psql failed: {completed.stdout} {completed.stderr}")
    return completed.stdout.strip()


def _read_params() -> Dict[str, float]:
    names = ",".join(f"'{name}'" for name in sorted(BASELINE_PARAMS))
    output = _psql(
        "SELECT name||'='||setting FROM pg_settings "
        f"WHERE name IN ({names}) ORDER BY name;"
    )
    return {
        line.partition("=")[0]: float(line.partition("=")[2])
        for line in output.splitlines()
    }


def _params_match(actual: Dict[str, float], expected: Dict[str, float]) -> bool:
    return set(actual) == set(expected) and all(
        math.isclose(actual[name], expected[name], rel_tol=1e-5, abs_tol=1e-12)
        for name in expected
    )


def _append_event(path: Path, value: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main() -> int:
    args = parse_args()
    for raw_path, expected in EXPECTED_SHA256.items():
        path = Path(raw_path)
        if not path.is_file() or v25.sha256(path) != expected:
            raise RuntimeError(f"sealed SHA mismatch: {path}")
    if SUMMARY.exists():
        raise RuntimeError("validation04 unexpectedly has a summary")
    ledger = v25.load_json(LEDGER)
    selection = v25.load_json(SELECTION)
    v30_state = v25.load_json(V30_STATE)
    v31_state = v25.load_json(V31_STATE)
    v31_manifest = v25.load_json(V31_MANIFEST)
    current = v30_state.get("current_run") or {}
    if (
        Path(str(current.get("root") or "")).resolve() != RUN_ROOT
        or int(ledger.get("real_workload_invocation_count", -1)) != 1
        or ledger.get("successful") != []
        or ledger.get("technical_failures") != []
        or int(V30_FORMAL.stat().st_size) <= 0
        or int(v25.load_json(V30_FORMAL).get("accepted_count", -1)) != 0
        or int(v25.load_json(V31_FORMAL).get("accepted_count", -1)) != 0
    ):
        raise RuntimeError("validation04 handover boundary mismatch")
    rank1 = selection["top5"][0]
    fingerprint = str(rank1["three_level_fingerprint"]["id"])
    if fingerprint != "three-level-sha256:9fb1a2487c1976297038d28c361b7188abf4fe4c1d50cb120642e3740d74df5a":
        raise RuntimeError("validation04 physical fingerprint mismatch")
    observed = _read_params()
    if not _params_match(observed, CANDIDATE_PARAMS):
        raise RuntimeError(f"unexpected live PG parameter state: {observed}")

    lock_handle = ROUND_LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("validation04 runner still owns the replay lock") from exc
    if not args.apply:
        print("passed=true", flush=True)
        print("apply_required=true", flush=True)
        print("physical=1 valid=0 technical_to_record=1 accepted=0", flush=True)
        print(f"fingerprint={fingerprint}", flush=True)
        print("physical_workload_calls_added_by_recovery=0", flush=True)
        return 0

    timestamp = time.strftime("%Y%m%dT%H%M%S")
    recovery_root = (
        PROJECT_ROOT
        / "history/tpcc_final_algorithm_fresh0_v31_20260803"
        / f"v30_validation04_handover_recovery_{timestamp}"
    )
    recovery_root.mkdir(parents=True, exist_ok=False)
    for path in (V30_STATE, V30_FORMAL, V31_STATE, V31_FORMAL, V31_MANIFEST, LEDGER, EVENTS, SELECTION):
        shutil.copy2(path, recovery_root / path.name)

    restored = subprocess.run(
        [str(RESTORE)],
        cwd=str(PROJECT_ROOT),
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if restored.returncode != 0:
        raise RuntimeError(f"snapshot restore failed: {restored.stdout} {restored.stderr}")
    for name, value in sorted(BASELINE_PARAMS.items()):
        _psql(f"ALTER SYSTEM SET {name} = '{value:.17g}';")
    _psql("SELECT pg_reload_conf();")
    baseline_readback = _read_params()
    if not _params_match(baseline_readback, BASELINE_PARAMS):
        raise RuntimeError(f"baseline parameter restore mismatch: {baseline_readback}")

    failure = {
        "rank": 1,
        "attempt": 1,
        "error": "HandoverSIGHUP: controller pane ended after physical reservation",
        "three_level_fingerprint": copy.deepcopy(rank1["three_level_fingerprint"]),
        "consumes_successful_replay_budget": False,
        "consumes_technical_failure_budget": True,
        "eligible_for_incumbent_or_accepted": False,
    }
    ledger["technical_failures"] = [failure]
    v25.atomic_json(LEDGER, ledger)
    _append_event(
        EVENTS,
        {
            "event": "tpcc_gradient_real_replay_technical_failure",
            **failure,
            "timestamp_epoch": time.time(),
        },
    )
    _append_event(
        EVENTS,
        {
            "event": "tpcc_gradient_baseline_restore_handover",
            "baseline_snapshot_restored": True,
            "baseline_params_restored": True,
            "baseline_param_readback": baseline_readback,
            "physical_workload_calls_added": 0,
            "timestamp_epoch": time.time(),
        },
    )
    quarantine = RUN_ROOT.with_name(
        f"quarantine_{RUN_ROOT.name}_handover_technical_{timestamp}"
    )
    RUN_ROOT.replace(quarantine)

    technical_audit = {
        "event": "v30_validation04_handover_technical_quarantine",
        "mode": "diagnostic_validation",
        "physical_calls": 1,
        "valid_replays": 0,
        "technical_failures": 1,
        "accepted_delta": 0,
        "formal_state_mutated": False,
        "baseline_restored": True,
        "fingerprint_ids": [fingerprint],
        "quarantine_root": str(quarantine),
        "ledger_path": str(quarantine / LEDGER.relative_to(RUN_ROOT)),
        "events_path": str(quarantine / EVENTS.relative_to(RUN_ROOT)),
    }
    v31_state.setdefault("technical_failure_audits", []).append(technical_audit)
    v31_state["quarantined_fingerprints"] = sorted(
        set(v31_state.get("quarantined_fingerprints") or []) | {fingerprint}
    )
    v25.atomic_json(V31_STATE, v31_state)
    frozen_selection = v25.load_json(
        Path(v31_manifest["v31_frozen_next_selection_path"])
    )
    fingerprint_gate = v29.global_prelaunch_fingerprint_gate(
        frozen_selection,
        "diagnostic_validation",
        v31_state,
    )
    if fingerprint_gate.get("passed") is not True:
        raise RuntimeError("v31 frozen selection collided after handover exclusion")

    audit = {
        "schema_version": "tpcc_v30_validation04_handover_recovery_v1",
        "passed": True,
        "physical_workload_calls_before_after": 1,
        "valid_replays": 0,
        "technical_failures": 1,
        "accepted_delta": 0,
        "baseline_snapshot_restored": True,
        "baseline_params_restored": True,
        "baseline_param_readback": baseline_readback,
        "fingerprint_quarantined": fingerprint,
        "v30_formal_sha256_unchanged": v25.sha256(V30_FORMAL),
        "v31_formal_sha256_unchanged": v25.sha256(V31_FORMAL),
        "v31_controller_state_sha256": v25.sha256(V31_STATE),
        "physical_workload_calls_added_by_recovery": 0,
        "quarantine_root": str(quarantine),
    }
    audit_path = recovery_root / "recovery_audit.json"
    v25.atomic_json(audit_path, audit)
    initializer_report = v25.load_json(V31_REPORT)
    initializer_report["v30_validation04_handover_recovery_path"] = str(audit_path)
    initializer_report["v30_validation04_handover_recovery_sha256"] = v25.sha256(audit_path)
    initializer_report["controller_state_sha256"] = v25.sha256(V31_STATE)
    v25.atomic_json(V31_REPORT, initializer_report)

    v31_manifest["initial_controller_state_sha256"] = v25.sha256(V31_STATE)
    v31_manifest["v31_zero_call_initializer_report_sha256"] = v25.sha256(V31_REPORT)
    v31_manifest["v30_validation04_handover_recovery_path"] = str(audit_path)
    v31_manifest["v30_validation04_handover_recovery_sha256"] = v25.sha256(audit_path)
    v31_manifest["v30_validation04_physical_excluded"] = 1
    pin_paths = {
        str(Path(item["path"]).resolve()): copy.deepcopy(item)
        for item in v31_manifest.get("pinned_files") or []
    }
    recovery_path = Path(__file__).resolve()
    pin_paths[str(recovery_path)] = {
        "path": str(recovery_path),
        "sha256": v25.sha256(recovery_path),
    }
    v31_manifest["pinned_files"] = [pin_paths[key] for key in sorted(pin_paths)]
    v25.atomic_json(V31_MANIFEST, v31_manifest)
    print(f"recovery_audit={audit_path}", flush=True)
    print(f"recovery_audit_sha256={v25.sha256(audit_path)}", flush=True)
    print(f"v31_controller_state_sha256={v25.sha256(V31_STATE)}", flush=True)
    print(f"v31_initializer_report_sha256={v25.sha256(V31_REPORT)}", flush=True)
    print(f"v31_manifest_sha256={v25.sha256(V31_MANIFEST)}", flush=True)
    print("physical=1 valid=0 technical=1 accepted=0", flush=True)
    print("physical_workload_calls_added_by_recovery=0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
