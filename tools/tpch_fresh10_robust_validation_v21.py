#!/usr/bin/env python3
"""Run robust two-observation TPC-H validation and start formal batch002.

This controller runs on node01 and never opens SSH.  It is intentionally
bounded to one novel probe batch, one exact-point validation batch, and one
formal batch.  Diagnostic batches are state-neutral and each batch is capped
at five physical workload invocations.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, Mapping, Sequence


B = Path(
    "/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/"
    "pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/"
    "experience_sgd/attempt48_global_replay_cap5_20260726"
)
FRESH_ROOT = B / "tpch_final_algorithm_fresh10_v58_20260731T2340"
SOURCE_PACKAGE = B / "gradient_debug_round57_probe5_recovery_v18_20260731T2310_package"
FORMAL_BATCH001 = FRESH_ROOT / "formal_batch001"
PROBE_SELECTION = FRESH_ROOT / "successor_diagnostic002_top5_v1.json"
PROBE_RUN = FRESH_ROOT / "diagnostic_probe002"
SOURCE_REPEAT_SELECTION = FRESH_ROOT / "diagnostic_probe002_repeat_top5.a.json"
SOURCE_REPEAT_RUN = FRESH_ROOT / "diagnostic_validation002"
ROBUST_SELECTION_A = FRESH_ROOT / "robust_repeat003_top5.a.json"
ROBUST_SELECTION_B = FRESH_ROOT / "robust_repeat003_top5.b.json"
VALIDATION_RUN = FRESH_ROOT / "diagnostic_validation003"
FORMAL_PACKAGE = FRESH_ROOT / "formal_batch002_package"
FORMAL_RUN = FRESH_ROOT / "formal_batch002"
ACCEPTED_COUNTER = FRESH_ROOT / "formal_search_accepted_counter.txt"
CONFIG = FRESH_ROOT / "config_fresh0.json"
BASELINE = B / "candidate_measurements/formal_point_20260729T050311Z_3000/summary.json"
DIAGNOSTIC_RUNNER = SOURCE_PACKAGE / "run_tpch_round29_diagnostic_exact_plan_regime_batch.py"
FORMAL_RUNNER = B / "code/run_tpch_formal_exact_plan_regime_batch.py"
WORKLOAD = SOURCE_PACKAGE / "run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh"
SOURCE_GATE = SOURCE_PACKAGE / "gate.json"
REPEAT_BUILDER = Path("/tmp/build_tpch_two_observation_robust_repeat_v1.py")
ANALYZER = Path("/tmp/tpch_fresh0_handover_v19/analyze_top5_validation_gate.py")
PYTHON = Path("/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11")
VENDOR = B / "vendor/py311"
SITE = Path("/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages")
CONTAINER = "pg5_tpch_ap_slot_node01_20260726"
DATABASE = "template_fullgrid_tpch_pg5"

EXPECTED = {
    "probe_selection": "e6e60ad522862ca4a16803acdf8bd59a5bb24c22199faa46b92997d7ddd82f87",
    "probe_summary": "4c997cd9bcbd820f43351d080b69358dbc1052441d5530c4db28ac2af8c64a3d",
    "probe_state": "98edd4153ae952f98c32bc6b29ec59b6db638b2ceccf95aa6d528c4812c7748c",
    "source_repeat_selection": "071542e63511b69c446b776ffdcb959cd5d7eacb4b005e5b12f8cb1592d3ae1e",
    "source_repeat_summary": "e3fd5e7f8fa783449ca18a26200aec5ac0d750275f3acf9948d957c015879e93",
    "source_repeat_state": "6b9c340a5a5bcf9b37b09dc49e5fba0f7a899dda6bc623498d988545ff6c8dbb",
    "failed_gate_analysis": "0b4c249068009d72179a1294aa7962d5c2d695a43da3786771d8fed56337caf4",
    "formal_state": "3360a08ccb6e05488ed6917481f179a315dc05cea57f97f4db41010ab28f295d",
    "config": "0708698830d20441c2dcceed1ccc98f66527c41cdad6d4d577011bfc5f93da06",
    "source_gate": "02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1",
    "baseline": "50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67",
    "workload": "25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008",
    "diagnostic_runner": "12b6ade0325541dbe1eadfd40af873fd3144fc1cbf97d9eca52f11a430731872",
    "formal_runner": "5db636e8983b8e743520a870549e744af65e6c40fcb00eab61e66108a091ffb9",
    "repeat_builder": "21cff3c92a7780ce6c259cd2bd9052acb714951f2a7a8cb1f96e05333c9be340",
    "analyzer": "49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30",
}

REQUIRED_FIELDS = (
    "feature_ratio",
    "predicted_resource",
    "utilization",
    "dScore/dU",
    "T",
    "confidence",
    "g_j",
    "eta",
    "delta_param",
    "candidate_score",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def verify(path: Path, expected: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"SHA mismatch: {path}")


def finite(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise RuntimeError("non-finite numeric value")
    return result


def runtime_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("LD_LIBRARY_PATH", None)
    environment.update(
        {
            "PYTHONPATH": f"{SOURCE_PACKAGE}:{VENDOR}:{B / 'code'}:{SITE}",
            "PYTHONNOUSERSITE": "1",
            "LD_PRELOAD": "/usr/lib64/libstdc++.so.6",
            "PGPASSWORD": "postgres",
            "PGHOST": "127.0.0.1",
            "PGPORT": "63301",
            "PGUSER": "postgres",
        }
    )
    return environment


def run(argv: Sequence[str], log: Path, environment: Mapping[str, str]) -> None:
    completed = subprocess.run(
        list(argv),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=dict(environment),
    )
    atomic_text(log, completed.stdout or "")
    if completed.returncode != 0:
        raise RuntimeError(f"command failed rc={completed.returncode}: {argv[0]}")


def fingerprint(candidate: Mapping[str, Any]) -> str:
    value = candidate.get("three_level_fingerprint") or {}
    identifier = str(value.get("id") if isinstance(value, Mapping) else "")
    if not identifier.startswith("three-level-sha256:"):
        raise RuntimeError("invalid three-level fingerprint")
    return identifier


def validate_selection(selection: Mapping[str, Any], expected_roles: Sequence[str]) -> None:
    candidates = selection.get("top5") or []
    roles = [str(item.get("selection_role")) for item in candidates]
    fingerprints = [fingerprint(item) for item in candidates]
    if (
        len(candidates) != 5
        or roles != list(expected_roles)
        or len(set(fingerprints)) != 5
        or any(
            item.get("boundary_corner") is True
            or item.get("boundary_plateau") is True
            or any(field not in item for field in REQUIRED_FIELDS)
            for item in candidates
        )
    ):
        raise RuntimeError("Top-5 selection contract mismatch")


def validate_clean_diagnostic(state: Mapping[str, Any], accepted_before: int) -> None:
    checks = (
        state.get("status") == "complete",
        state.get("diagnostic_only") is True,
        state.get("formal_state_mutation_allowed") is False,
        state.get("accepted_update_allowed") is False,
        state.get("incumbent_update_allowed") is False,
        state.get("range_update_allowed") is False,
        state.get("gradient_update_allowed") is False,
        int(state.get("physical_call_count", -1)) == 5,
        int(state.get("valid_replay_count", -1)) == 5,
        int(state.get("technical_failure_count", -1)) == 0,
        int(state.get("accepted_count", -1)) == accepted_before,
        state.get("baseline_restored") is True,
    )
    if not all(checks):
        raise RuntimeError("diagnostic is not clean state-neutral phys5/valid5")


def validate_previous_formal() -> int:
    state = load(FORMAL_BATCH001 / "formal_state.json")
    summary = load(FORMAL_BATCH001 / "summary.json")
    if (
        state.get("status") != "complete"
        or int(state.get("physical_call_count", -1)) != 5
        or int(state.get("valid_replay_count", -1)) != 5
        or int(state.get("technical_failure_count", -1)) != 0
        or int(state.get("accepted_count", -1)) != 0
        or int(state.get("batch_added_accepted_count", -1)) != 0
        or state.get("baseline_restored") is not True
        or int(summary.get("physical_call_count", -1)) != 5
        or int(summary.get("valid_replay_count", -1)) != 5
        or int(summary.get("technical_failure_count", -1)) != 0
        or int(summary.get("batch_added_accepted_count", -1)) != 0
        or summary.get("baseline_restored") is not True
    ):
        raise RuntimeError("formal batch001 is not clean all-rejected evidence")
    if ACCEPTED_COUNTER.read_text(encoding="utf-8").strip() != "0":
        raise RuntimeError("fresh accepted counter is not zero")
    return 0


def diagnostic_argv(selection: Path, output: Path) -> list[str]:
    return [
        str(PYTHON),
        "-u",
        str(DIAGNOSTIC_RUNNER),
        "--diagnostic-only",
        "--selection",
        str(selection),
        "--selection-sha256",
        sha256(selection),
        "--gate-summary",
        str(SOURCE_GATE),
        "--gate-summary-sha256",
        EXPECTED["source_gate"],
        "--baseline-summary",
        str(BASELINE),
        "--baseline-summary-sha256",
        EXPECTED["baseline"],
        "--config",
        str(CONFIG),
        "--config-sha256",
        EXPECTED["config"],
        "--workload",
        str(WORKLOAD),
        "--workload-sha256",
        EXPECTED["workload"],
        "--accepted-counter",
        str(ACCEPTED_COUNTER),
        "--selection-start-rank",
        "1",
        "--maximum-real-replay-invocations",
        "5",
        "--output",
        str(output),
        "--container",
        CONTAINER,
        "--database",
        DATABASE,
        "--timeout-seconds",
        "900",
    ]


def promote_formal(repeat: Mapping[str, Any], analysis: Mapping[str, Any]) -> tuple[Path, Path]:
    formal_selection = copy.deepcopy(dict(repeat))
    trusted = formal_selection["top5"][:3]
    predicted_span = max(finite(item["candidate_score"]) for item in trusted) - min(
        finite(item["candidate_score"]) for item in trusted
    )
    noise_bound = 2.0 * finite(analysis.get("trusted_score_mae") or 0.0)
    gradient_allowed = predicted_span > noise_bound
    formal_selection.update(
        {
            "diagnostic_only": False,
            "formal_continuation": True,
            "accepted_update_allowed": True,
            "incumbent_update_allowed": True,
            "range_update_allowed": True,
            "gradient_update_allowed": gradient_allowed,
            "formal_expected_improvement_required": False,
            "formal_admission_contract": "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1",
            "promoted_from_diagnostic_selection_sha256": sha256(ROBUST_SELECTION_A),
        }
    )
    for item in formal_selection["top5"]:
        trusted_role = item["selection_role"] == "trusted"
        item["diagnostic_only"] = False
        item["accepted_update_allowed"] = trusted_role
        item["incumbent_update_allowed"] = trusted_role
        item["range_update_allowed"] = trusted_role
        item["gradient_update_allowed"] = trusted_role and gradient_allowed
        item["used_for_gradient"] = trusted_role and gradient_allowed
        item["used_for_inner_incumbent_update"] = trusted_role
    FORMAL_PACKAGE.mkdir(mode=0o700)
    selection_path = FORMAL_PACKAGE / "top5_formal.json"
    atomic_json(selection_path, formal_selection)
    gate = {
        "schema_version": "tpch_formal_entry_gate_v1",
        "passed": True,
        "successful_real_replay_count": 5,
        "technical_failure_count": 0,
        "baseline_params_restored": True,
        "accepted_count": 0,
        "historical_accepted_excluded": 1,
        "qps_used_for_inner_scoring": False,
        "checks": copy.deepcopy(analysis["checks"]),
        "stability_analysis": copy.deepcopy(dict(analysis)),
        "gradient_rank_update_allowed": gradient_allowed,
        "bindings": {
            "diagnostic_selection_sha256": sha256(ROBUST_SELECTION_A),
            "diagnostic_summary_sha256": sha256(VALIDATION_RUN / "diagnostic_summary.json"),
            "diagnostic_state_sha256": sha256(VALIDATION_RUN / "diagnostic_state.json"),
            "formal_selection_sha256": sha256(selection_path),
            "fresh_config_sha256": EXPECTED["config"],
        },
    }
    gate_path = FORMAL_PACKAGE / "formal_entry_gate.json"
    atomic_json(gate_path, gate)
    return selection_path, gate_path


def start_formal(selection: Path, gate: Path, environment: Mapping[str, str]) -> int:
    FORMAL_RUN.mkdir(mode=0o700)
    driver_log = (FORMAL_RUN / "driver.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [
            str(PYTHON),
            "-u",
            str(FORMAL_RUNNER),
            "--selection",
            str(selection),
            "--selection-sha256",
            sha256(selection),
            "--gate-summary",
            str(gate),
            "--gate-summary-sha256",
            sha256(gate),
            "--baseline-summary",
            str(BASELINE),
            "--baseline-summary-sha256",
            EXPECTED["baseline"],
            "--config",
            str(CONFIG),
            "--config-sha256",
            EXPECTED["config"],
            "--workload",
            str(WORKLOAD),
            "--workload-sha256",
            EXPECTED["workload"],
            "--accepted-counter",
            str(ACCEPTED_COUNTER),
            "--output",
            str(FORMAL_RUN),
            "--container",
            CONTAINER,
            "--database",
            DATABASE,
            "--timeout-seconds",
            "900",
        ],
        stdout=driver_log,
        stderr=subprocess.STDOUT,
        env=dict(environment),
        start_new_session=True,
    )
    atomic_text(FORMAL_RUN / "driver.pid", f"{process.pid}\n")
    deadline = time.monotonic() + 45
    state_path = FORMAL_RUN / "formal_state.json"
    while time.monotonic() < deadline:
        if state_path.is_file():
            state = load(state_path)
            if state.get("status") in {"running", "complete"}:
                if int(state.get("accepted_shrink_target", -1)) != 10:
                    raise RuntimeError("formal target mismatch")
                if int(state.get("physical_call_count", -1)) < 1:
                    raise RuntimeError("formal did not start a physical replay")
                return process.pid
        if process.poll() is not None:
            break
        time.sleep(1)
    raise RuntimeError("formal batch002 did not produce a bound running/complete state")


def main() -> int:
    lock_path = FRESH_ROOT / "robust_validation_v21.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name, path in (
            ("probe_selection", PROBE_SELECTION),
            ("probe_summary", PROBE_RUN / "diagnostic_summary.json"),
            ("probe_state", PROBE_RUN / "diagnostic_state.json"),
            ("source_repeat_selection", SOURCE_REPEAT_SELECTION),
            (
                "source_repeat_summary",
                SOURCE_REPEAT_RUN / "diagnostic_summary.json",
            ),
            (
                "source_repeat_state",
                SOURCE_REPEAT_RUN / "diagnostic_state.json",
            ),
            (
                "failed_gate_analysis",
                FRESH_ROOT / "entry_gate_analysis002.json",
            ),
            ("formal_state", FORMAL_BATCH001 / "formal_state.json"),
            ("config", CONFIG),
            ("source_gate", SOURCE_GATE),
            ("baseline", BASELINE),
            ("workload", WORKLOAD),
            ("diagnostic_runner", DIAGNOSTIC_RUNNER),
            ("formal_runner", FORMAL_RUNNER),
            ("repeat_builder", REPEAT_BUILDER),
            ("analyzer", ANALYZER),
        ):
            verify(path, EXPECTED[name])
        accepted_before = validate_previous_formal()
        validate_selection(load(PROBE_SELECTION), ["probe"] * 5)
        validate_selection(
            load(SOURCE_REPEAT_SELECTION),
            ["trusted", "trusted", "trusted", "probe", "probe"],
        )
        validate_clean_diagnostic(
            load(PROBE_RUN / "diagnostic_state.json"), accepted_before
        )
        validate_clean_diagnostic(
            load(SOURCE_REPEAT_RUN / "diagnostic_state.json"),
            accepted_before,
        )
        failed_analysis = load(FRESH_ROOT / "entry_gate_analysis002.json")
        if (
            failed_analysis.get("passed") is not False
            or (failed_analysis.get("checks") or {}).get(
                "trusted_only_spearman_positive"
            )
            is not False
            or (failed_analysis.get("checks") or {}).get(
                "actual_best_in_predicted_top3"
            )
            is not False
        ):
            raise RuntimeError("source failure is not the expected ranking failure")
        for path in (
            ROBUST_SELECTION_A,
            ROBUST_SELECTION_B,
            VALIDATION_RUN,
            FORMAL_PACKAGE,
            FORMAL_RUN,
        ):
            if path.exists():
                raise RuntimeError(f"refusing to reuse output: {path}")
        environment = runtime_environment()
        builder = [
            str(PYTHON),
            "-u",
            str(REPEAT_BUILDER),
            "--source-selection",
            str(PROBE_SELECTION),
            "--source-summary",
            str(PROBE_RUN / "diagnostic_summary.json"),
            "--repeat-selection",
            str(SOURCE_REPEAT_SELECTION),
            "--repeat-summary",
            str(SOURCE_REPEAT_RUN / "diagnostic_summary.json"),
            "--maximum-trusted-score-span",
            "0.01",
            "--maximum-trusted-resource-factor",
            "1.5",
            "--output",
        ]
        run(
            [*builder, str(ROBUST_SELECTION_A)],
            FRESH_ROOT / "robust_repeat003_builder_a.log",
            environment,
        )
        run(
            [*builder, str(ROBUST_SELECTION_B)],
            FRESH_ROOT / "robust_repeat003_builder_b.log",
            environment,
        )
        if ROBUST_SELECTION_A.read_bytes() != ROBUST_SELECTION_B.read_bytes():
            raise RuntimeError("robust repeat builder is not deterministic")
        repeat = load(ROBUST_SELECTION_A)
        validate_selection(repeat, ["trusted", "trusted", "trusted", "probe", "probe"])

        run(
            diagnostic_argv(ROBUST_SELECTION_A, VALIDATION_RUN),
            FRESH_ROOT / "diagnostic_validation003_driver.log",
            environment,
        )
        validate_clean_diagnostic(load(VALIDATION_RUN / "diagnostic_state.json"), accepted_before)
        if ACCEPTED_COUNTER.read_text(encoding="utf-8").strip() != str(accepted_before):
            raise RuntimeError("validation diagnostic mutated accepted counter")

        analysis_path = FRESH_ROOT / "entry_gate_analysis003.json"
        run(
            [
                str(PYTHON),
                "-u",
                str(ANALYZER),
                "--selection",
                str(ROBUST_SELECTION_A),
                "--observations",
                str(VALIDATION_RUN / "diagnostic_summary.json"),
                "--output",
                str(analysis_path),
            ],
            FRESH_ROOT / "entry_gate_analysis003.log",
            environment,
        )
        analysis = load(analysis_path)
        if analysis.get("passed") is not True:
            raise RuntimeError(f"validation gate failed: {analysis.get('checks')}")
        selection, gate = promote_formal(repeat, analysis)
        pid = start_formal(selection, gate, environment)
        atomic_json(
            FRESH_ROOT / "robust_validation_v21_handover.json",
            {
                "status": "formal_batch002_started",
                "pid": pid,
                "accepted_before": accepted_before,
                "accepted_target": 10,
                "probe_selection_sha256": EXPECTED["probe_selection"],
                "source_repeat_selection_sha256": EXPECTED[
                    "source_repeat_selection"
                ],
                "robust_selection_sha256": sha256(ROBUST_SELECTION_A),
                "entry_gate_analysis_sha256": sha256(analysis_path),
                "formal_selection_sha256": sha256(selection),
                "formal_gate_sha256": sha256(gate),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
