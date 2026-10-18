#!/usr/bin/env python3
"""Prepare XP formal cont10 after fail-closed cont9 physical reconciliation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
SOURCE = ATTEMPT / "formal10_cont9_accepted4_basin_uncertain_v2_20260729T1920"
OUTPUT = ATTEMPT / "formal10_cont10_accepted4_budgeted_v1_20260729T2005"
PROJECT_ROOT = Path("/home/anonymous/program/resource_adaptive")
OPTIMIZER = PROJECT_ROOT / "parameter_optimizer.py"
OPTIMIZER_SHA256 = "a09cc0039bbe3fee313556d319414952a0756148068fb0790c72d113a0d5f3a0"
SOURCE_CONFIG_SHA256 = "ec278a1182d545d15f6d35b065cd3f61d65a2a11970e32c215c217d136f11a47"
SOURCE_MANIFEST_SHA256 = "8507a75e0fed81ef8ee30045ea9f26176ee54e82bd073f747d5f05c57770e103"
SOURCE_HISTORY_SHA256 = "0a005ecef23c29610cf397553eaf4a462e3d24a3806b8d71543a377f48ffdfdf"
RECONCILIATION_SHA256 = "194605c5cfc776e9650aded612463bff87c0b1523be08414fee5706ed12df826"
SUMMARY_ROWS = [
    ("20260729T192147+0800_3471770", "6af8e1b89fb245c3982b0c2c3b7c3188ec2e26e4aeb78ac586a4c36a6be5cb18"),
    ("20260729T192520+0800_3522062", "785394637379b125d8e78d9bcee9cf43e06fc47eec63f102ed58f32a066438cb"),
    ("20260729T192853+0800_3530548", "443165577de26941e6d9dc1cd9d6c356b9d528d64c8d1d1f41a5fed5bdb9deea"),
    ("20260729T193234+0800_3538817", "f934aff3d7a750fffce060fa3677fc635b05d3d44ea4916a414eb8fd78c28ddf"),
    ("20260729T193614+0800_3545898", "79f168c1dc612dd0014ed37cfad33c6a86c5c22075147e626090282f4d33a12e"),
    ("20260729T194018+0800_3558218", "3e15ffeae830c525d9f3f6a78810f3146a8639ee12a0037c8bbf94acb5674f58"),
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def require_sha(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"SHA mismatch for {path}: {actual} != {expected}")


def read_counter(name: str) -> int:
    return int((SOURCE / "state" / name).read_text(encoding="utf-8").strip())


def validate_source() -> Dict[str, Any]:
    if OUTPUT.exists():
        raise RuntimeError(f"output already exists: {OUTPUT}")
    require_sha(OPTIMIZER, OPTIMIZER_SHA256)
    require_sha(SOURCE / "config.json", SOURCE_CONFIG_SHA256)
    require_sha(SOURCE / "formal_manifest.json", SOURCE_MANIFEST_SHA256)
    history = SOURCE / "optimizer_history_2026-07-29_192005.jsonl"
    require_sha(history, SOURCE_HISTORY_SHA256)
    audit_path = SOURCE / "state" / "xp_cont9_counter_reconciliation_audit.json"
    require_sha(audit_path, RECONCILIATION_SHA256)
    counters = {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 8,
        "global_replay_counter.txt": 8,
        "formal_startup_baseline_counter.txt": 0,
    }
    for name, expected in counters.items():
        actual = read_counter(name)
        if actual != expected:
            raise RuntimeError(f"{name} changed: {actual} != {expected}")

    audit = read_json(audit_path)
    if (
        audit.get("restored") != {
            "formal_search_accepted": 4,
            "formal_search_replay": 8,
            "global_replay": 8,
            "startup_baseline": 0,
        }
        or (audit.get("accepted_split") or {}).get("official_total") != 4
        or (audit.get("quarantine") or {}).get("counts_as_accepted") is not False
        or (audit.get("quarantine") or {}).get("physical_call_6")
        != "candidate_measurements/20260729T194018+0800_3558218/summary.json"
    ):
        raise RuntimeError("cont9 reconciliation/quarantine audit is not exact")

    summaries: List[Dict[str, Any]] = []
    for ordinal, (point_id, expected_sha) in enumerate(SUMMARY_ROWS, start=1):
        path = ATTEMPT / "candidate_measurements" / point_id / "summary.json"
        require_sha(path, expected_sha)
        payload = read_json(path)
        summary = payload[-1] if isinstance(payload, list) else payload
        if (
            summary.get("status") != "ok"
            or summary.get("stable") is not True
            or summary.get("correctness_passed") is not True
            or summary.get("technical_failure") is not False
        ):
            raise RuntimeError(f"cont9 summary {ordinal} is not valid: {path}")
        summaries.append(
            {
                "physical_call_ordinal": ordinal,
                "point_id": point_id,
                "path": str(path),
                "sha256": expected_sha,
                "quarantined": ordinal == 6,
                "counts_as_accepted": False,
            }
        )

    ranking_event = None
    feedback_validation_count = 0
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event") == "experience_table_all_subset_ranking" and event.get("round_index") == 0:
            ranking_event = event
        if event.get("event") == "feedback_final_validation":
            feedback_validation_count += 1
    if ranking_event is None or feedback_validation_count != 6:
        raise RuntimeError("cont9 history does not prove exactly six validations")
    rankings = ranking_event.get("candidate_rankings") or []
    if len(rankings) < 6:
        raise RuntimeError("cont9 ranking lacks physical-call rank 1..6")
    physical_fingerprints = [
        str((rankings[index].get("three_level_fingerprint") or {}).get("id") or "")
        for index in range(6)
    ]
    if any(not value.startswith("three-level-sha256:") for value in physical_fingerprints):
        raise RuntimeError("cont9 physical-call fingerprint is missing")
    if physical_fingerprints[5] != physical_fingerprints[2]:
        raise RuntimeError("quarantined call 6 no longer maps to the sealed rank-3 fingerprint")
    return {
        "summaries": summaries,
        "physical_fingerprints": physical_fingerprints,
        "quarantine_audit_path": str(audit_path),
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
EXPECTED_OPTIMIZER_SHA={OPTIMIZER_SHA256}

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
nohup taskset -c "$AFFINITY" env OPTIMIZER_CONFIG="$CONFIG" "$PYTHON" -u "$OPTIMIZER" >>"${{OUTPUT}}/formal_launcher.log" 2>&1 &
PID=$!
printf '%s\\n' "$PID" >"${{OUTPUT}}/formal_launcher.pid"
test "$(taskset -cp "$PID" | sed 's/.*: //')" = "$AFFINITY"
printf 'started XP cont10 pid=%s affinity=%s\\n' "$PID" "$AFFINITY"
"""


def main() -> None:
    evidence = validate_source()
    source_config = read_json(SOURCE / "config.json")
    config = copy.deepcopy(source_config)
    preserved_state = {
        "p_real": copy.deepcopy(config.get("p_real")),
        "param_ranges": copy.deepcopy(config.get("param_ranges")),
    }

    state_dir = OUTPUT / "state"
    subset_dir = OUTPUT / "subset_logs"
    state_dir.mkdir(parents=True)
    subset_dir.mkdir()
    plan_state_source = SOURCE / "state" / "plan_basin_escape_state.json"
    atomic_text(
        state_dir / "plan_basin_escape_state.json",
        plan_state_source.read_text(encoding="utf-8"),
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
            "formal_search_initial_replay_top_k": 5,
            "formal_search_second_replay_top_k": 5,
            "formal_search_max_replay_top_k": 5,
            "candidate_replay_top_k": 5,
            "global_real_replay_top_k_hard_cap": 5,
            "maximum_resource_qualified_real_replays_per_batch": 5,
            "maximum_non_default_real_candidates": 5,
            "formal_search_attempt_safety_cap": 13,
            "formal_search_maximum_replay_count": 13,
            "maximum_cumulative_real_replay_count": 13,
            "formal_search_replay_cumulative_tiers": [13],
            "formal_search_accepted_counter_path": str(state_dir / "formal_search_accepted_counter.txt"),
            "formal_search_replay_counter_path": str(state_dir / "formal_search_replay_counter.txt"),
            "global_replay_counter_path": str(state_dir / "global_replay_counter.txt"),
            "formal_startup_baseline_counter_path": str(state_dir / "formal_startup_baseline_counter.txt"),
        }
    )
    prior_excluded = list(formal.get("excluded_prior_three_level_fingerprints") or [])
    formal["excluded_prior_three_level_fingerprints"] = list(
        dict.fromkeys(prior_excluded + evidence["physical_fingerprints"])
    )
    next_round = config["_xp_next_validation_round"]
    next_round["excluded_prior_three_level_fingerprints"] = copy.deepcopy(
        formal["excluded_prior_three_level_fingerprints"]
    )
    continuation = config["_xp_formal_continuation"]
    continuation.update(
        {
            "contract": "xp_formal10_cont10_budgeted_v1",
            "source_continuation": str(SOURCE),
            "accepted_start": 4,
            "formal_replay_start": 8,
            "global_replay_start": 8,
            "batch_physical_cap": 5,
            "maximum_candidate_calls": 5,
            "parameter_optimizer_sha256": OPTIMIZER_SHA256,
            "cont9_physical_call_fingerprints": evidence["physical_fingerprints"],
            "cont9_quarantined_call_ordinal": 6,
        }
    )
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["optimizer"]["gradient"]["subset_log_dir"] = str(subset_dir)
    config["optimizer"]["gradient"]["plan_basin_escape"]["state_path"] = str(
        state_dir / "plan_basin_escape_state.json"
    )
    if preserved_state != {
        "p_real": config.get("p_real"),
        "param_ranges": config.get("param_ranges"),
    }:
        raise RuntimeError("accepted incumbent/range fields changed")

    config_path = OUTPUT / "config.json"
    atomic_text(config_path, json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    config_sha = sha256(config_path)
    launcher_path = OUTPUT / "launch_cont10_after_baseline_gate.sh"
    atomic_text(launcher_path, build_launcher(config_sha), mode=0o700)
    manifest = {
        "schema_version": 1,
        "contract": "xp_formal10_cont10_budgeted_v1",
        "source_cont9": str(SOURCE),
        "source_config_sha256": SOURCE_CONFIG_SHA256,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "source_history_sha256": SOURCE_HISTORY_SHA256,
        "counter_reconciliation_audit": evidence["quarantine_audit_path"],
        "counter_reconciliation_audit_sha256": RECONCILIATION_SHA256,
        "cont9_physical_calls": evidence["summaries"],
        "cont9_physical_call_fingerprints": evidence["physical_fingerprints"],
        "cont9_unique_excluded_fingerprints": formal["excluded_prior_three_level_fingerprints"],
        "accepted_split": {"inherited": 3, "prior_formal": 1, "this_batch": 0, "official_total": 4},
        "counter_start": {"accepted": 4, "formal_replay": 8, "global_replay": 8, "startup": 0},
        "per_batch_physical_cap": 5,
        "parameter_optimizer_path": str(OPTIMIZER),
        "parameter_optimizer_sha256": OPTIMIZER_SHA256,
        "config_path": str(config_path),
        "config_sha256": config_sha,
        "launcher_path": str(launcher_path),
        "launcher_sha256": sha256(launcher_path),
        "launcher_must_validate_baseline_readback": True,
        "baseline_expected": {
            "cpu_index_tuple_cost": 0.005,
            "cpu_operator_cost": 0.0025,
            "cpu_tuple_cost": 0.01,
            "parallel_setup_cost": 1000,
            "parallel_tuple_cost": 0.1,
            "random_page_cost": 4,
            "seq_page_cost": 0.2,
        },
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
