#!/usr/bin/env python3
"""TPC-H round57 clean probe -> exact repeat -> fresh0 formal handover.

The program runs on node01 and never opens SSH.  It is deliberately bounded
to one clean discovery batch, one exact-point validation batch (five physical
calls), and one formal target-10 batch.  Every transition is fail-closed.
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


B = Path("/mnt/nvme1n1/anonymous_resource_adaptive/campaigns/pg5_common_outer_bgallowed_20260722_v1/tpch_on_ap_slot_node01/experience_sgd/attempt48_global_replay_cap5_20260726")
CURRENT_PACKAGE = B / "gradient_debug_round57_probe5_recovery_v18_20260731T2310_package"
CURRENT_RUN = B / "gradient_debug_round57_probe5_recovery_v18_20260731T2310_run"
FRESH_ROOT = B / "tpch_final_algorithm_fresh10_v58_20260731T2340"
REPEAT_RUN = FRESH_ROOT / "diagnostic_exact_repeat001"
FORMAL_PACKAGE = FRESH_ROOT / "formal_batch001_package"
FORMAL_RUN = FRESH_ROOT / "formal_batch001"
ACCEPTED_COUNTER = FRESH_ROOT / "formal_search_accepted_counter.txt"
BASELINE = B / "candidate_measurements/formal_point_20260729T050311Z_3000/summary.json"
DIAGNOSTIC_RUNNER = CURRENT_PACKAGE / "run_tpch_round29_diagnostic_exact_plan_regime_batch.py"
FORMAL_RUNNER = B / "code/run_tpch_formal_exact_plan_regime_batch.py"
WORKLOAD = CURRENT_PACKAGE / "run_tpch_weighted_candidate_attempt48_v5_cleanpyenv_completiongate.sh"
DEPLOY_ROOT = Path("/tmp/tpch_fresh0_handover_v19")
REPEAT_BUILDER = DEPLOY_ROOT / "build_tpch_exact_plan_repeat_validation.py"
ANALYZER = DEPLOY_ROOT / "analyze_top5_validation_gate.py"
PYTHON = Path("/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/bin/python3.11")
VENDOR = B / "vendor/py311"
SITE = Path("/mnt/nvme1n1/anonymous_resource_adaptive/python311_c7/lib/python3.11/site-packages")
CURRENT_PID = 89649
CONTAINER = "pg5_tpch_ap_slot_node01_20260726"
DATABASE = "template_fullgrid_tpch_pg5"

EXPECTED = {
    "current_selection": "d61376adfc7f6774c1cc606d0592b104005d5f82fe68fd098faa18707fc746a6",
    "current_config": "0b6be9e78681028b730682e87bf01a76306169336e921c60b03bd7c0dfd69b92",
    "current_gate": "02d19baf6ad8ef9bce7432243ed1ffcb97ed7b402e2fe7c4199243260c109be1",
    "baseline": "50ae4c9dce7a876af0bbea46e76fb0010256c0481f99605237020f79cb995d67",
    "workload": "25b75a8de7b3fdbe7e06a228dabbda8d42c2d0e2544d9b96a26331c67b6b5008",
    "diagnostic_runner": "12b6ade0325541dbe1eadfd40af873fd3144fc1cbf97d9eca52f11a430731872",
    "formal_runner": "5db636e8983b8e743520a870549e744af65e6c40fcb00eab61e66108a091ffb9",
    "repeat_builder": "4f1684e73c9c91f32d94655053a4c6af5a8f8f0d2d2eb8a94363eaa24b48cfe7",
    "analyzer": "49936cbc4319ef6055d6653c276892cd9df408a98d8560460c3e4f0bd7e37d30",
}


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
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def verify(path: Path, expected: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise RuntimeError(f"SHA mismatch: {path}")


def run(argv: Sequence[str], log: Path, *, env: Mapping[str, str]) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        list(argv),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=dict(env),
    )
    atomic_text(log, completed.stdout or "")
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {argv[0]}"
        )


def process_command(pid: int) -> str:
    path = Path(f"/proc/{pid}/cmdline")
    if not path.is_file():
        return ""
    return path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")


def wait_current() -> None:
    deadline = time.monotonic() + 3 * 60 * 60
    while Path(f"/proc/{CURRENT_PID}").exists():
        if "gradient_debug_round57_probe5_recovery_v18_20260731T2310_package" not in process_command(CURRENT_PID):
            raise RuntimeError("round57 PID command binding changed")
        if time.monotonic() >= deadline:
            raise RuntimeError("round57 exceeded three-hour wait bound")
        time.sleep(10)


def validate_clean_diagnostic(state: Mapping[str, Any]) -> None:
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
        state.get("baseline_restored") is True,
    )
    if not all(checks):
        raise RuntimeError("diagnostic is not clean, state-neutral phys5/valid5")


def validate_repeat_selection(selection: Mapping[str, Any]) -> None:
    candidates = selection.get("top5") or []
    roles = [item.get("selection_role") for item in candidates]
    fingerprints = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in candidates
    ]
    required = (
        "feature_ratio", "predicted_resource", "utilization", "dScore/dU",
        "T", "confidence", "g_j", "eta", "delta_param", "candidate_score",
    )
    if (
        len(candidates) != 5
        or roles != ["trusted", "trusted", "trusted", "probe", "probe"]
        or len(set(fingerprints)) != 5
        or not all(fingerprints)
        or any(
            item.get("boundary_corner") is True
            or item.get("boundary_plateau") is True
            or any(field not in item for field in required)
            for item in candidates
        )
    ):
        raise RuntimeError("exact-repeat Top-5 contract mismatch")


def runtime_environment() -> Dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONHOME", None)
    environment.pop("LD_LIBRARY_PATH", None)
    environment.update(
        {
            "PYTHONPATH": f"{CURRENT_PACKAGE}:{VENDOR}:{B / 'code'}:{SITE}",
            "PYTHONNOUSERSITE": "1",
            "LD_PRELOAD": "/usr/lib64/libstdc++.so.6",
            "PGPASSWORD": "postgres",
            "PGHOST": "127.0.0.1",
            "PGPORT": "63301",
            "PGUSER": "postgres",
        }
    )
    return environment


def main() -> int:
    lock_path = B / "tpch_round57_fresh0_handover_v19.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify(CURRENT_PACKAGE / "top5.json", EXPECTED["current_selection"])
        verify(CURRENT_PACKAGE / "config.json", EXPECTED["current_config"])
        verify(CURRENT_PACKAGE / "gate.json", EXPECTED["current_gate"])
        verify(BASELINE, EXPECTED["baseline"])
        verify(WORKLOAD, EXPECTED["workload"])
        verify(DIAGNOSTIC_RUNNER, EXPECTED["diagnostic_runner"])
        verify(FORMAL_RUNNER, EXPECTED["formal_runner"])
        verify(REPEAT_BUILDER, EXPECTED["repeat_builder"])
        verify(ANALYZER, EXPECTED["analyzer"])
        if FRESH_ROOT.exists():
            raise RuntimeError(f"fresh campaign already exists: {FRESH_ROOT}")

        wait_current()
        current_state = load(CURRENT_RUN / "diagnostic_state.json")
        validate_clean_diagnostic(current_state)
        current_summary = CURRENT_RUN / "diagnostic_summary.json"
        if not current_summary.is_file():
            raise RuntimeError("round57 diagnostic summary is missing")

        FRESH_ROOT.mkdir(mode=0o700)
        atomic_text(ACCEPTED_COUNTER, "0\n")
        config = load(CURRENT_PACKAGE / "config.json")
        formal = config.get("formal_outer_protocol") or {}
        formal["formal_search_accepted_count_start"] = 0
        formal["accepted_shrink_target"] = 10
        formal["formal_search_accepted_target"] = 10
        formal["formal_search_max_replay_top_k"] = 5
        config["formal_outer_protocol"] = formal
        config_path = FRESH_ROOT / "config_fresh0.json"
        atomic_json(config_path, config)

        repeat_a = FRESH_ROOT / "repeat_top5.a.json"
        repeat_b = FRESH_ROOT / "repeat_top5.b.json"
        builder_argv = [
            str(PYTHON), "-u", str(REPEAT_BUILDER),
            "--selection", str(CURRENT_PACKAGE / "top5.json"),
            "--summary", str(current_summary),
            "--output",
        ]
        environment = runtime_environment()
        run([*builder_argv, str(repeat_a)], FRESH_ROOT / "builder_a.log", env=environment)
        run([*builder_argv, str(repeat_b)], FRESH_ROOT / "builder_b.log", env=environment)
        if repeat_a.read_bytes() != repeat_b.read_bytes():
            raise RuntimeError("exact-repeat builder is not deterministic")
        repeat_selection = load(repeat_a)
        validate_repeat_selection(repeat_selection)
        repeat_sha = sha256(repeat_a)

        run(
            [
                str(PYTHON), "-u", str(DIAGNOSTIC_RUNNER),
                "--diagnostic-only",
                "--selection", str(repeat_a),
                "--selection-sha256", repeat_sha,
                "--gate-summary", str(CURRENT_PACKAGE / "gate.json"),
                "--gate-summary-sha256", EXPECTED["current_gate"],
                "--baseline-summary", str(BASELINE),
                "--baseline-summary-sha256", EXPECTED["baseline"],
                "--config", str(config_path),
                "--config-sha256", sha256(config_path),
                "--workload", str(WORKLOAD),
                "--workload-sha256", EXPECTED["workload"],
                "--accepted-counter", str(ACCEPTED_COUNTER),
                "--selection-start-rank", "1",
                "--maximum-real-replay-invocations", "5",
                "--output", str(REPEAT_RUN),
                "--container", CONTAINER,
                "--database", DATABASE,
                "--timeout-seconds", "900",
            ],
            FRESH_ROOT / "diagnostic_repeat_driver.log",
            env=environment,
        )
        repeat_state = load(REPEAT_RUN / "diagnostic_state.json")
        validate_clean_diagnostic(repeat_state)
        if int(repeat_state.get("accepted_count", -1)) != 0:
            raise RuntimeError("fresh diagnostic inherited accepted progress")
        if ACCEPTED_COUNTER.read_text(encoding="utf-8").strip() != "0":
            raise RuntimeError("diagnostic changed fresh accepted counter")

        analysis_path = FRESH_ROOT / "entry_gate_analysis.json"
        run(
            [
                str(PYTHON), "-u", str(ANALYZER),
                "--selection", str(repeat_a),
                "--observations", str(REPEAT_RUN / "diagnostic_summary.json"),
                "--output", str(analysis_path),
            ],
            FRESH_ROOT / "entry_gate_analysis.log",
            env=environment,
        )
        analysis = load(analysis_path)
        if analysis.get("passed") is not True:
            raise RuntimeError(f"exact-repeat gate failed: {analysis.get('checks')}")

        trusted = repeat_selection["top5"][:3]
        predicted_span = max(float(item["candidate_score"]) for item in trusted) - min(
            float(item["candidate_score"]) for item in trusted
        )
        noise_bound = 2.0 * float(analysis.get("trusted_score_mae") or 0.0)
        gradient_rank_update_allowed = predicted_span > noise_bound

        formal_selection = copy.deepcopy(repeat_selection)
        formal_selection.update(
            {
                "diagnostic_only": False,
                "formal_continuation": True,
                "accepted_update_allowed": True,
                "incumbent_update_allowed": True,
                "range_update_allowed": True,
                "gradient_update_allowed": gradient_rank_update_allowed,
                "formal_expected_improvement_required": False,
                "formal_admission_contract": "diagnostic_quality_gate_passed_then_real_replay_accept_or_reject_v1",
                "promoted_from_diagnostic_selection_sha256": repeat_sha,
                "gradient_rank_update_noise_audit": {
                    "trusted_predicted_score_span": predicted_span,
                    "trusted_replay_noise_two_mae": noise_bound,
                    "gradient_rank_update_allowed": gradient_rank_update_allowed,
                },
            }
        )
        for item in formal_selection["top5"]:
            trusted_role = item["selection_role"] == "trusted"
            item["diagnostic_only"] = False
            item["accepted_update_allowed"] = trusted_role
            item["incumbent_update_allowed"] = trusted_role
            item["range_update_allowed"] = trusted_role
            item["gradient_update_allowed"] = (
                trusted_role and gradient_rank_update_allowed
            )
            item["used_for_gradient"] = (
                trusted_role and gradient_rank_update_allowed
            )
            item["used_for_inner_incumbent_update"] = trusted_role

        FORMAL_PACKAGE.mkdir(mode=0o700)
        formal_selection_path = FORMAL_PACKAGE / "top5_formal.json"
        atomic_json(formal_selection_path, formal_selection)
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
            "stability_analysis": copy.deepcopy(analysis),
            "gradient_rank_update_allowed": gradient_rank_update_allowed,
            "bindings": {
                "diagnostic_selection_sha256": repeat_sha,
                "diagnostic_summary_sha256": sha256(REPEAT_RUN / "diagnostic_summary.json"),
                "diagnostic_state_sha256": sha256(REPEAT_RUN / "diagnostic_state.json"),
                "formal_selection_sha256": sha256(formal_selection_path),
                "fresh_config_sha256": sha256(config_path),
            },
        }
        gate_path = FORMAL_PACKAGE / "formal_entry_gate.json"
        atomic_json(gate_path, gate)
        atomic_json(
            FRESH_ROOT / "campaign_manifest.json",
            {
                "schema_version": "tpch_fresh0_target10_campaign_v1",
                "accepted_target": 10,
                "historical_accepted_excluded": 1,
                "fresh_accepted_count_start": 0,
                "diagnostic_physical_cap": 5,
                "qps_role": "outer_audit_only_not_inner_score",
                "source_round57_selection_sha256": EXPECTED["current_selection"],
                "repeat_selection_sha256": repeat_sha,
                "entry_gate_analysis_sha256": sha256(analysis_path),
                "formal_selection_sha256": sha256(formal_selection_path),
                "formal_gate_sha256": sha256(gate_path),
            },
        )

        FORMAL_RUN.mkdir(mode=0o700)
        driver_log = (FORMAL_RUN / "driver.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                str(PYTHON), "-u", str(FORMAL_RUNNER),
                "--selection", str(formal_selection_path),
                "--selection-sha256", sha256(formal_selection_path),
                "--gate-summary", str(gate_path),
                "--gate-summary-sha256", sha256(gate_path),
                "--baseline-summary", str(BASELINE),
                "--baseline-summary-sha256", EXPECTED["baseline"],
                "--config", str(config_path),
                "--config-sha256", sha256(config_path),
                "--workload", str(WORKLOAD),
                "--workload-sha256", EXPECTED["workload"],
                "--accepted-counter", str(ACCEPTED_COUNTER),
                "--output", str(FORMAL_RUN),
                "--container", CONTAINER,
                "--database", DATABASE,
                "--timeout-seconds", "900",
            ],
            stdout=driver_log,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        atomic_text(FORMAL_RUN / "driver.pid", f"{process.pid}\n")
        deadline = time.monotonic() + 30
        state_path = FORMAL_RUN / "formal_state.json"
        state: Dict[str, Any] | None = None
        while process.poll() is None:
            if state_path.is_file():
                state = load(state_path)
                if state.get("status") == "running":
                    break
            if time.monotonic() >= deadline:
                raise RuntimeError("fresh formal state was not running in 30 seconds")
            time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError("fresh formal controller exited during startup")
        if state is None:
            raise RuntimeError("fresh formal state is missing")
        if (
            state.get("status") != "running"
            or int(state.get("accepted_count", -1)) != 0
            or int(state.get("accepted_shrink_target", -1)) != 10
            or int(state.get("physical_call_count", -1)) != 1
        ):
            raise RuntimeError("fresh formal startup state mismatch")
        atomic_json(
            FRESH_ROOT / "handover_complete.json",
            {
                "status": "fresh_formal_target10_started",
                "pid": process.pid,
                "accepted_count": 0,
                "accepted_target": 10,
                "historical_accepted_excluded": 1,
                "formal_state_sha256": sha256(state_path),
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
