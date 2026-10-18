#!/usr/bin/env python3
"""Prepare (but never launch) XP cont12 with sealed ratio-scale samples."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
SOURCE = ATTEMPT / "formal10_cont11_accepted4_sealed_ratio_anchor_v2_20260729T2040"
OUTPUT = ATTEMPT / "formal10_cont12_accepted4_sealed_ratio_samples_v1_20260729T2050"
PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
OPTIMIZER = PROJECT_ROOT / "parameter_optimizer.py"
SOURCE_HISTORY = SOURCE / "optimizer_history_2026-07-29_203232.jsonl"
SEALED_SUMMARY = (
    ATTEMPT
    / "candidate_measurements"
    / "20260729T143213+0800_2725057"
    / "summary.json"
)
MEASUREMENT_RUNNER = (
    ATTEMPT / "wrapper" / "candidate_stable_measurement_formal_v5.py"
)

EXPECTED_SHA = {
    "optimizer": "73b1b20d4c1c1f5505d3a4e340cc596334e55d3705325f6aaeb574c5d2cc7156",
    "source_config": "70158e023ce90a210013e859e9c3e3eab1d51ee477d0ec52d927387ca994d26b",
    "source_manifest": "e55bff3f722c2bc35aa380d3c1bd8b8589ba7c3ee82111d07dc23ad29c48402b",
    "source_log": "1b31af0dedeb243d87f23854f725b0b9d3d24d63fca916676f8065c01b444e48",
    "source_history": "80113f72134f5820c4d5d292cb531801356fe14e1b80e771a5255f831f1e70e7",
    "sealed_summary": "a7865a46bbc8d2c4ad6d31c9226ad58890a62e0b07289f2adecca1b92307f027",
    "measurement_runner": "576bd9f0e0218b744b1860420348dad0179a8b33b7c4d2bbcd2d22adeec45bf2",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"SHA mismatch for {path}: {actual} != {expected}")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def validate_source() -> Dict[str, Any]:
    if OUTPUT.exists():
        raise RuntimeError(f"output already exists: {OUTPUT}")
    for path, key in (
        (OPTIMIZER, "optimizer"),
        (SOURCE / "config.json", "source_config"),
        (SOURCE / "formal_manifest.json", "source_manifest"),
        (SOURCE / "direct.log", "source_log"),
        (SOURCE_HISTORY, "source_history"),
        (SEALED_SUMMARY, "sealed_summary"),
        (MEASUREMENT_RUNNER, "measurement_runner"),
    ):
        require_sha(path, EXPECTED_SHA[key])

    traceback_text = (SOURCE / "direct.log").read_text(encoding="utf-8")
    required_failure_markers = (
        "ratio-scale measured baseline failed preliminary correctness/stability gate",
        "'formal_samples_bound_to_summary_point': False",
        "'schema': 'hybench_xp_formal_summary_v1'",
    )
    if not all(marker in traceback_text for marker in required_failure_markers):
        raise RuntimeError("cont11 failure is not the sealed-samples binding failure")

    events = [
        json.loads(line)
        for line in SOURCE_HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    protocol_events = [
        event
        for event in events
        if event.get("event") == "ratio_scale_baseline_measurement_protocol"
    ]
    replay_events = [
        event
        for event in events
        if event.get("event") == "feedback_final_validation"
    ]
    if len(protocol_events) != 1 or replay_events:
        raise RuntimeError("cont11 does not prove one pre-replay gate failure")
    evidence = (
        protocol_events[0].get("measurement_protocol", {})
        .get("correctness_evidence", {})
    )
    if (
        evidence.get("passed") is not False
        or evidence.get("schema") != "hybench_xp_formal_summary_v1"
        or evidence.get("summary_path") != str(SEALED_SUMMARY)
        or (evidence.get("checks") or {}).get(
            "formal_samples_bound_to_summary_point"
        ) is not False
        or (evidence.get("checks") or {}).get(
            "formal_sample_file_count_matches"
        ) is not False
        or (evidence.get("checks") or {}).get(
            "summary_formal_samples_match_runtime"
        ) is not False
    ):
        raise RuntimeError("cont11 gate evidence changed")

    expected_counters = {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 8,
        "global_replay_counter.txt": 8,
        "formal_startup_baseline_counter.txt": 0,
    }
    for name, expected in expected_counters.items():
        actual = int((SOURCE / "state" / name).read_text().strip())
        if actual != expected:
            raise RuntimeError(f"{name} changed: {actual} != {expected}")

    summary = read_json(SEALED_SUMMARY)
    formal = summary.get("formal_pg_resource") or {}
    samples_path = Path(str(formal.get("samples_path") or ""))
    if (
        SEALED_SUMMARY.parent.name != "20260729T143213+0800_2725057"
        or summary.get("status") != "ok"
        or summary.get("correctness_passed") is not True
        or summary.get("stable") is not True
        or summary.get("technical_failure") is not False
        or summary.get("stability_contract_sha256")
        != "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
        or not samples_path.is_file()
        or int(formal.get("sample_count") or 0)
        != sum(1 for line in samples_path.read_text().splitlines() if line.strip())
    ):
        raise RuntimeError("sealed XP ratio anchor is not complete and stable")

    source_config = read_json(SOURCE / "config.json")
    exclusions = (
        source_config.get("formal_outer_protocol", {})
        .get("excluded_prior_three_level_fingerprints", [])
    )
    if (
        len(exclusions) != 6
        or len(set(exclusions)) != 6
        or not all(
            isinstance(item, str)
            and item.startswith("three-level-sha256:")
            and len(item) == 83
            for item in exclusions
        )
    ):
        raise RuntimeError("cont11 new-fingerprint exclusion set changed")
    return {
        "events": len(events),
        "sealed_samples_path": str(samples_path),
        "sealed_samples_sha256": sha256(samples_path),
        "excluded_fingerprints": list(exclusions),
    }


def build_launcher(config_sha: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

OUTPUT={OUTPUT}
CONFIG="${{OUTPUT}}/config.json"
OPTIMIZER={OPTIMIZER}
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
AFFINITY=32-51,288-307
EXPECTED_CONFIG_SHA={config_sha}
EXPECTED_OPTIMIZER_SHA={EXPECTED_SHA["optimizer"]}

test "$(sha256sum "$CONFIG" | awk '{{print $1}}')" = "$EXPECTED_CONFIG_SHA"
test "$(sha256sum "$OPTIMIZER" | awk '{{print $1}}')" = "$EXPECTED_OPTIMIZER_SHA"
test "$(cat "${{OUTPUT}}/state/formal_search_accepted_counter.txt")" = "4"
test "$(cat "${{OUTPUT}}/state/formal_search_replay_counter.txt")" = "8"
test "$(cat "${{OUTPUT}}/state/global_replay_counter.txt")" = "8"
test "$(cat "${{OUTPUT}}/state/formal_startup_baseline_counter.txt")" = "0"
! pgrep -af "$CONFIG"

EXPECTED_BASELINE=$'cpu_index_tuple_cost=0.005\\ncpu_operator_cost=0.0025\\ncpu_tuple_cost=0.01\\nparallel_setup_cost=1000\\nparallel_tuple_cost=0.1\\nrandom_page_cost=4\\nseq_page_cost=0.2'
ACTUAL_BASELINE="$(PGPASSWORD=postgres psql -h 127.0.0.1 -p 59630 -U postgres -d postgres -Atc "select name||'='||setting from pg_settings where name in ('seq_page_cost','random_page_cost','cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost','parallel_tuple_cost') order by name")"
test "$ACTUAL_BASELINE" = "$EXPECTED_BASELINE"

cd {PROJECT_ROOT}
exec taskset -c "$AFFINITY" env OPTIMIZER_CONFIG="$CONFIG" "$PYTHON" -u "$OPTIMIZER"
"""


def main() -> None:
    evidence = validate_source()
    source_config = read_json(SOURCE / "config.json")
    config = copy.deepcopy(source_config)
    preserved = {
        "p_real": copy.deepcopy(config.get("p_real")),
        "param_ranges": copy.deepcopy(config.get("param_ranges")),
    }

    state_dir = OUTPUT / "state"
    subset_dir = OUTPUT / "subset_logs"
    state_dir.mkdir(parents=True)
    subset_dir.mkdir()
    atomic_text(
        state_dir / "plan_basin_escape_state.json",
        (SOURCE / "state" / "plan_basin_escape_state.json").read_text(),
    )
    for name, value in {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 8,
        "global_replay_counter.txt": 8,
        "formal_startup_baseline_counter.txt": 0,
    }.items():
        atomic_text(state_dir / name, f"{value}\n")

    formal = config["formal_outer_protocol"]
    formal.update(
        {
            "formal_search_accepted_count_start": 4,
            "formal_search_replay_count_start": 8,
            "global_replay_count_start": 8,
            "global_replay_ordinal_at_binding": 8,
            "initial_real_replay_count": 8,
            "formal_search_attempt_safety_cap": 13,
            "formal_search_maximum_replay_count": 13,
            "maximum_cumulative_real_replay_count": 13,
            "formal_search_replay_cumulative_tiers": [13],
            "formal_search_accepted_counter_path": str(
                state_dir / "formal_search_accepted_counter.txt"
            ),
            "formal_search_replay_counter_path": str(
                state_dir / "formal_search_replay_counter.txt"
            ),
            "global_replay_counter_path": str(
                state_dir / "global_replay_counter.txt"
            ),
            "formal_startup_baseline_counter_path": str(
                state_dir / "formal_startup_baseline_counter.txt"
            ),
        }
    )
    if formal.get("excluded_prior_three_level_fingerprints") != evidence[
        "excluded_fingerprints"
    ]:
        raise RuntimeError("fingerprint exclusions were not preserved exactly")

    retraining = config["metrics"]["plan_estimator"]["model_retraining"]
    retraining.update(
        {
            "enabled": False,
            "apply_to_runtime": False,
            "strict_retrain_gate": False,
            "fallback_on_error": False,
            "disabled_reason": (
                "sealed exact-p_real ratio anchor is reused; no mutable model "
                "retraining may precede ratio calibration"
            ),
        }
    )
    reuse = config["startup_baseline_reuse"]
    reuse.update(
        {
            "sealed_summary_path": str(SEALED_SUMMARY),
            "sealed_summary_sha256": EXPECTED_SHA["sealed_summary"],
            "measurement_runner_path": str(MEASUREMENT_RUNNER),
            "measurement_runner_sha256": EXPECTED_SHA["measurement_runner"],
        }
    )
    config["_xp_formal_continuation"].update(
        {
            "contract": "xp_formal10_cont12_sealed_ratio_samples_v1",
            "source_continuation": str(SOURCE),
            "accepted_start": 4,
            "formal_replay_start": 8,
            "global_replay_start": 8,
            "batch_physical_cap": 5,
            "maximum_candidate_calls": 5,
            "parameter_optimizer_sha256": EXPECTED_SHA["optimizer"],
            "sealed_ratio_anchor_path": str(SEALED_SUMMARY),
            "sealed_ratio_anchor_sha256": EXPECTED_SHA["sealed_summary"],
            "resource_model_retraining_enabled": False,
        }
    )
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["optimizer"]["gradient"]["subset_log_dir"] = str(subset_dir)
    config["optimizer"]["gradient"]["plan_basin_escape"]["state_path"] = str(
        state_dir / "plan_basin_escape_state.json"
    )
    if preserved != {
        "p_real": config.get("p_real"),
        "param_ranges": config.get("param_ranges"),
    }:
        raise RuntimeError("accepted incumbent/range fields changed")

    config_path = OUTPUT / "config.json"
    atomic_text(config_path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    config_sha = sha256(config_path)
    launcher_path = OUTPUT / "launch_cont12_persistent_pty.sh"
    atomic_text(launcher_path, build_launcher(config_sha), mode=0o700)
    manifest = {
        "schema_version": 1,
        "contract": "xp_formal10_cont12_sealed_ratio_samples_v1",
        "source_cont11": str(SOURCE),
        "source_artifact_sha256": {
            "config": EXPECTED_SHA["source_config"],
            "manifest": EXPECTED_SHA["source_manifest"],
            "direct_log": EXPECTED_SHA["source_log"],
            "history": EXPECTED_SHA["source_history"],
        },
        "cont11_failure": {
            "stage": "ratio_scale_baseline_measurement_protocol",
            "physical_calls": 0,
            "valid_replays": 0,
            "state_pollution": False,
        },
        "accepted_split": {
            "inherited": 3,
            "prior_formal": 1,
            "this_batch": 0,
            "official_total": 4,
        },
        "counter_start": {
            "accepted": 4,
            "formal_replay": 8,
            "global_replay": 8,
            "startup": 0,
        },
        "per_batch_physical_cap": 5,
        "new_fingerprint_exclusions": evidence["excluded_fingerprints"],
        "sealed_ratio_anchor": {
            "path": str(SEALED_SUMMARY),
            "sha256": EXPECTED_SHA["sealed_summary"],
            "samples_path": evidence["sealed_samples_path"],
            "samples_sha256": evidence["sealed_samples_sha256"],
            "measurement_runner_path": str(MEASUREMENT_RUNNER),
            "measurement_runner_sha256": EXPECTED_SHA["measurement_runner"],
            "schema": "hybench_xp_formal_summary_v1",
        },
        "resource_model_retraining_enabled": False,
        "parameter_optimizer_path": str(OPTIMIZER),
        "parameter_optimizer_sha256": EXPECTED_SHA["optimizer"],
        "config_path": str(config_path),
        "config_sha256": config_sha,
        "launcher_path": str(launcher_path),
        "launcher_sha256": sha256(launcher_path),
        "launcher_mode": "foreground_exec_requires_persistent_pty",
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "do_not_touch_cpu_184-199,440-455_or_mysql_state",
        "prepared_only_no_workload_started": True,
    }
    atomic_text(
        OUTPUT / "formal_manifest.json",
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
