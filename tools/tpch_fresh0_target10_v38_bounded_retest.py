#!/usr/bin/env python3
"""Run TPC-H v40 from a fresh accepted-zero root.

The previous campaign is immutable training/audit input only.  No accepted,
incumbent, range, or gradient state is inherited.  The formal state starts
from the measured default-parameter baseline with the full configured ranges.
The failed v38 and v39 roots are retained as zero-workload audit evidence and
are never included in v40 novelty exclusions.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
FAILED_V38_ROOT = B / "tpch_final_algorithm_fresh0_v38_bounded_retest_20260803"
FAILED_V38_LOOP_ROOT = FAILED_V38_ROOT / "target10_loop_v38"
FAILED_V38_ACCEPTED_COUNTER = FAILED_V38_ROOT / "formal_search_accepted_counter.txt"
FAILED_V39_ROOT = B / "tpch_final_algorithm_fresh0_v39_runner_compat_20260803"
FAILED_V39_LOOP_ROOT = FAILED_V39_ROOT / "target10_loop_v39"
FAILED_V39_ACCEPTED_COUNTER = FAILED_V39_ROOT / "formal_search_accepted_counter.txt"
NEW_ROOT = B / "tpch_final_algorithm_fresh0_v40_retest_compat_20260803"
LOOP_ROOT = NEW_ROOT / "target10_loop_v40"
ACCEPTED_COUNTER = NEW_ROOT / "formal_search_accepted_counter.txt"

V26 = Path("/tmp/tpch_fresh0_target10_loop_v36_base.py")
V27 = Path("/tmp/tpch_fresh10_target10_loop_v27.py")
V30 = Path("/tmp/tpch_fresh10_target10_loop_v30.py")
V32 = Path("/tmp/tpch_fresh10_target10_loop_v32_v6_hierarchical.py")
V33 = Path("/tmp/tpch_fresh10_target10_loop_v33_prethird_bound.py")
BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v9_bounded_retest.py"
)
EXPANDER = Path(
    "/tmp/tpch_exact_regime_neighbor_expand_v5_bounded.py"
)
DIAGNOSTIC_RUNNER = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_v40.py"
)

V37_ROOT = B / "tpch_final_algorithm_fresh0_v37_feasible_ladder_20260803"
SEED_SELECTION = V37_ROOT / "target10_loop_v37/cycle_004/probe_top5.a.json"
SEED_RUN = V37_ROOT / "target10_loop_v37/cycle_004/diagnostic_probe"

EXPECTED = {
    V26: "34107e91b4bcfd0b2fe8d2911a0855e1512fc881665f4db3c3cb67618bd055fb",
    V27: "d8e1672b4aceea5065f612170d57c1b16917a1dce9bf039fb3af274be4492589",
    V30: "384f2ce8f944525711d706c1e85493bfe61ed075d20d6794f61caed3dd67d7f8",
    V32: "54ac22227f190bfbbc1f2889927cba9500cbe1bd6f4347287d69c3647d6e98b5",
    V33: "e5c18e3ebe7b8152e12ada134b86f50b32edc610f86346030c37aba5ebd688a0",
    BUILDER: "3f2514db76a9e86484029b2bd72162c96f190f5f71af218543335e1b4c93208a",
    EXPANDER: "dce066d239ca68eb95ec10bd39a77e798212395df782ae850ffe7d27d5346a78",
    DIAGNOSTIC_RUNNER: (
        "c3d352b980daf403a3344b7381ad5699215f6b2ba59b2a40c10b6e71f91a908d"
    ),
    SEED_SELECTION: "87402de012a5bf3224079e9d8570b1d1b7e52d1f7cd57ecd67822e8e10a6440c",
    SEED_RUN / "diagnostic_summary.json": (
        "d805d1617baa709297785d1d88f18b02768a02c2ac9c3622dc7b5e2b8c2382c0"
    ),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def import_module(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def selection_paths_with_loop(
    original_selection_paths: Any,
    root: Path,
    loop_root: Path,
) -> Any:
    """Include every earlier cycle from this v40 root in exclusions."""
    seen = set()
    for path in original_selection_paths(root):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            yield resolved
    for path in loop_root.glob("cycle_*/*.json"):
        resolved = path.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        try:
            payload = load(resolved)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload.get("top5"), list) and len(payload["top5"]) == 5:
            seen.add(resolved)
            yield resolved


def audit_failed_zero_workload(
    root: Path,
    loop_root: Path,
    accepted_counter: Path,
    campaign_version: str,
) -> Dict[str, Any]:
    """Prove a failed predecessor consumed no workload and keep it immutable."""
    if campaign_version not in {"v38", "v39"}:
        raise RuntimeError("unsupported predecessor audit version")
    if not root.is_dir() or not loop_root.is_dir():
        raise RuntimeError(f"failed {campaign_version} audit root is missing")
    if (
        not accepted_counter.is_file()
        or accepted_counter.read_text(encoding="utf-8").strip() != "0"
    ):
        raise RuntimeError(
            f"failed {campaign_version} accepted counter is not exactly zero"
        )

    selections = []
    for path in sorted(loop_root.glob("cycle_*/*.json")):
        try:
            payload = load(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload.get("top5"), list) and len(payload["top5"]) == 5:
            selections.append(
                {"path": str(path), "sha256": sha256(path)}
            )
    if not selections:
        raise RuntimeError(
            f"failed {campaign_version} root has no deterministic Top-5 evidence"
        )

    replay_states = []
    for path in sorted(loop_root.rglob("*state.json")):
        payload = load(path)
        physical_calls = int(payload.get("physical_call_count", 0))
        replay_states.append(
            {
                "path": str(path),
                "sha256": sha256(path),
                "physical_call_count": physical_calls,
            }
        )
        if physical_calls != 0:
            raise RuntimeError(
                f"failed {campaign_version} root contains a physical workload call"
            )
    attempt_directories = [
        path
        for path in loop_root.rglob("attempt_*")
        if path.is_dir()
    ]
    if attempt_directories:
        raise RuntimeError(
            f"failed {campaign_version} root contains workload attempt directories"
        )
    return {
        "contract": "tpch_failed_selection_zero_workload_audit_v1",
        "campaign_version": campaign_version,
        "source_root": str(root),
        "source_root_preserved": True,
        "accepted_count": 0,
        "physical_workload_calls": 0,
        "selection_count": len(selections),
        "selections": selections,
        "replay_states": replay_states,
        "selection_reuse_allowed": True,
        "excluded_from_v40_global_novelty_history": True,
    }


def run_with_expansion_timeout(
    original_run: Any,
    atomic_text: Any,
    expander: Path,
    argv: Any,
    log: Path,
    environment: Any,
) -> None:
    """Apply a hard process timeout only to EXPLAIN expansion commands."""
    if str(expander) not in [str(value) for value in argv]:
        original_run(argv, log, environment)
        return
    try:
        completed = subprocess.run(
            list(argv),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=dict(environment),
            timeout=1860,
        )
    except subprocess.TimeoutExpired as exc:
        atomic_text(log, str(exc.stdout or ""))
        raise RuntimeError(
            "dynamic expansion exceeded 1860-second process timeout"
        ) from exc
    atomic_text(log, completed.stdout or "")
    if completed.returncode != 0:
        raise RuntimeError(
            f"bounded expansion failed rc={completed.returncode}"
        )


def write_first_retest_bypass(
    v26_module: Any,
    v27_module: Any,
    cycle_root: Path,
    evidence: Path,
    resume_state: Path,
) -> Path:
    """Materialize the zero-EXPLAIN expansion input for the first retest."""
    output_root = cycle_root / "dynamic_expansion_v40_retest_bypass"
    output_root.mkdir(mode=0o700, parents=True)
    output = output_root / "expanded_candidates.json"
    v27_module.atomic_json(
        output,
        {
            "contract": "tpch_first_exact_retest_zero_explain_bypass_v1",
            "diagnostic_only": True,
            "explain_only": True,
            "new_workload_calls": 0,
            "new_explain_calls": 0,
            "formal_state_mutated": False,
            "source_evidence": str(evidence),
            "source_evidence_sha256": v26_module.sha256(evidence),
            "resume_state_sha256": v26_module.sha256(resume_state),
            "ranked_candidates": [],
        },
    )
    return output


def preflight(anchor_state: Path, anchor_sha256: str) -> Dict[str, Any]:
    for path, expected in EXPECTED.items():
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"TPC-H v40 SHA mismatch: {path}")
    if not anchor_state.is_file() or sha256(anchor_state) != anchor_sha256:
        raise RuntimeError("TPC-H v40 current-epoch anchor SHA mismatch")
    baseline = load(anchor_state)
    anchor = baseline.get("current_epoch_anchor_revalidation") or {}
    score_values = anchor.get("actual_score_values") or []
    summary = load(SEED_RUN / "diagnostic_summary.json")
    ranges = baseline.get("range_state")
    if not (
        int(baseline.get("accepted_count", -1)) == 0
        and baseline.get("status") == "complete"
        and baseline.get("baseline_restored") is True
        and int(baseline.get("technical_failure_count", -1)) == 0
        and anchor.get("contract") == "tpch_current_epoch_anchor_revalidation_v1"
        and anchor.get("state_neutral") is True
        and anchor.get("baseline_restored") is True
        and int(anchor.get("observation_count", -1)) >= 2
        and int(anchor.get("technical_failure_count", -1)) == 0
        and len(score_values) == int(anchor.get("observation_count", -1))
        and all(isinstance(value, (int, float)) for value in score_values)
        and float(anchor.get("maximum_resource_factor", 999.0)) <= 1.5
        and float(anchor.get("actual_score_span", 999.0)) <= 0.05
        and anchor.get("candidate_params") == baseline.get("incumbent_params")
        and anchor.get("anchor_score_policy")
        == "lower_observed_score_exact_resource_v1"
        and anchor.get("anchor_resource") == baseline.get("incumbent_resource")
        and float(anchor.get("anchor_score", math.nan))
        == float(baseline.get("incumbent_score", math.inf))
        and isinstance(ranges, dict)
        and len(ranges) == 7
        and int(summary.get("physical_call_count", -1)) == 5
        and int(summary.get("valid_replay_count", -1)) == 5
        and int(summary.get("technical_failure_count", -1)) == 0
        and summary.get("baseline_restored") is True
    ):
        raise RuntimeError("TPC-H v40 fresh0/current-epoch authority is not clean")
    if LOOP_ROOT.exists() or ACCEPTED_COUNTER.exists():
        raise RuntimeError("TPC-H v40 fresh0 output already exists")
    return {
        "contract": "tpch_v40_predecessor_zero_workload_audit_v1",
        "v38": audit_failed_zero_workload(
            FAILED_V38_ROOT,
            FAILED_V38_LOOP_ROOT,
            FAILED_V38_ACCEPTED_COUNTER,
            "v38",
        ),
        "v39": audit_failed_zero_workload(
            FAILED_V39_ROOT,
            FAILED_V39_LOOP_ROOT,
            FAILED_V39_ACCEPTED_COUNTER,
            "v39",
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--current-anchor-state", type=Path, required=True)
    parser.add_argument("--current-anchor-state-sha256", required=True)
    args = parser.parse_args()
    anchor_state = args.current_anchor_state.resolve()
    failed_v38_audit = preflight(anchor_state, args.current_anchor_state_sha256)
    if args.preflight_only:
        print("TPC-H v40 fresh0 preflight passed", flush=True)
        return 0
    v26 = import_module("tpch_v36_v26", V26)
    v27 = import_module("tpch_v36_v27", V27)
    v30 = import_module("tpch_v36_v30", V30)
    v32 = import_module("tpch_v36_v32", V32)
    v33 = import_module("tpch_v36_v33", V33)

    v26.ROOT = NEW_ROOT
    v26.LOOP_ROOT = LOOP_ROOT
    v26.ACCEPTED_COUNTER = ACCEPTED_COUNTER
    v26.INITIAL_PREVIOUS_SELECTION = SEED_SELECTION
    v26.INITIAL_PREVIOUS_RUN = SEED_RUN
    v26.MAX_CYCLES = 100
    v26.SUCCESSOR_BUILDER = BUILDER
    v26.EXPECTED["successor_builder"] = EXPECTED[BUILDER]
    v26.ROBUST_BUILDER = v32.ROBUST_BUILDER
    v26.EXPECTED["robust_builder"] = v32.ROBUST_BUILDER_SHA256

    v32.SUCCESSOR_BUILDER = BUILDER
    v32.SUCCESSOR_BUILDER_SHA256 = EXPECTED[BUILDER]
    v32.EXPANDER = EXPANDER
    v32.EXPANDER_SHA256 = EXPECTED[EXPANDER]

    original_selection_paths = v26.selection_paths

    def fresh_authority(_common: Any, accepted_before: int) -> tuple[int, Path, bool]:
        if accepted_before != 0:
            raise RuntimeError("TPC-H v40 accepted counter did not start at zero")
        return 0, anchor_state, True

    original_import_common = v26.import_common
    original_dynamic_expansion = v32.dynamic_expansion

    def import_common_with_v36_hooks() -> Any:
        common = original_import_common()
        common.DIAGNOSTIC_RUNNER = DIAGNOSTIC_RUNNER
        common.EXPECTED["diagnostic_runner"] = EXPECTED[DIAGNOSTIC_RUNNER]
        original_common_run = common.run

        def run_with_bounded_expansion(
            argv: Any,
            log: Path,
            environment: Any,
        ) -> None:
            run_with_expansion_timeout(
                original_common_run,
                common.atomic_text,
                EXPANDER,
                argv,
                log,
                environment,
            )

        def dynamic_expansion_with_first_retest_bypass(
            v26_module: Any,
            v27_module: Any,
            common_module: Any,
            cycle_root: Path,
            evidence: Path,
            resume_state: Path,
            environment: Any,
        ) -> Path:
            if evidence.resolve() != SEED_SELECTION.resolve():
                return original_dynamic_expansion(
                    v26_module,
                    v27_module,
                    common_module,
                    cycle_root,
                    evidence,
                    resume_state,
                    environment,
                )
            return write_first_retest_bypass(
                v26_module,
                v27_module,
                cycle_root,
                evidence,
                resume_state,
            )

        common.run = run_with_bounded_expansion
        v32.dynamic_expansion = dynamic_expansion_with_first_retest_bypass
        v32.install_algorithm_hooks(v26, v27, v30, common)
        v33.install_prethird_hook(v26, v27)
        return common

    v26.resolve_initial_authority = fresh_authority
    v26.import_common = import_common_with_v36_hooks
    v26.selection_paths = lambda root: selection_paths_with_loop(
        original_selection_paths,
        root,
        LOOP_ROOT,
    )

    NEW_ROOT.mkdir(mode=0o700, parents=True, exist_ok=False)
    ACCEPTED_COUNTER.write_text("0\n", encoding="utf-8")
    v27.atomic_json(
        NEW_ROOT / "failed_v38_v39_zero_workload_audit.json",
        failed_v38_audit,
    )
    sys.argv = [sys.argv[0]]
    return int(v26.main())


if __name__ == "__main__":
    raise SystemExit(main())
