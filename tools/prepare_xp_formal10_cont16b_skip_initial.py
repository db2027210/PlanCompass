#!/usr/bin/env python3
"""Build XP cont16b with the excluded incumbent initial replay skipped."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT = Path("/home/anonymous/program/resource_adaptive")
ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
SOURCE = ATTEMPT / "formal10_cont16_accepted4_exclusion11_v1_20260729T2228"
TARGET = ATTEMPT / "formal10_cont16b_accepted4_skip_initial_v1_20260729T2245"
STAGING = Path("/tmp/xp_cont16b_accepted4_skip_initial_v1_20260729T2245")
SOURCE_HISTORY = SOURCE / "optimizer_history_2026-07-29_223751.jsonl"
BASE_OPTIMIZER = PROJECT / "parameter_optimizer.py"
PATCHED_OPTIMIZER_NAME = "parameter_optimizer_xp_cont16b.py"
BASELINE_RESTORE_AUDIT = Path("/tmp/xp_cont16_baseline_restore_20260729T2244.log")
BASELINE_VERIFY_AUDIT = Path("/tmp/xp_cont16_baseline_verified_20260729T2244.txt")
EXPECTED_SHA = {
    "source_config": "beb09718cb85108ef50f5f74dfabe1397f17e8b58e04060273b8bfcc8688339c",
    "source_top5": "4b9598d2e56549ee0fe175f01852f143989259ef41468956a664f45e51d175b5",
    "source_history": "f7193b47b61c120981ac0fcaf6f2db94a115e040ce9973dc4af40b8d5b792b5f",
    "source_basin": "12757774eb3a7c9534c73f482eeeefb7640d6989ac760573b9b0e12efe511fe2",
    "base_optimizer": "960f999b552c23f1b84579c6f9fe4e895e06c47b66da0de91a2981b684d11c19",
    "baseline_restore": "a736ac0503cb3257e33d069d9078a6c5ac0f8cebc9d815455d0176d25564f08b",
    "baseline_verify": "a0bc42853647612d6e95f178355699682fcbfd2c3dbd2c43e64b97bd43b0b2c6",
}
EXPECTED_BASELINE = {
    "cpu_index_tuple_cost": "0.005",
    "cpu_operator_cost": "0.0025",
    "cpu_tuple_cost": "0.01",
    "parallel_setup_cost": "1000",
    "parallel_tuple_cost": "0.1",
    "random_page_cost": "4",
    "seq_page_cost": "0.2",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_sha(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise RuntimeError(f"SHA mismatch for {path}: {actual} != {expected}")


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def replace_path(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: replace_path(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_path(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def load_cont16_builder() -> Any:
    path = PROJECT / "tools" / "prepare_xp_formal10_cont16_exclusion11.py"
    spec = importlib.util.spec_from_file_location("xp_cont16_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load cont16 builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TARGET = TARGET
    return module


def patch_optimizer(source: str) -> str:
    parse_anchor = """    final_eval_run_validation = _to_bool(
        final_eval_cfg.get("run_validation", True),
        True,
    )
"""
    parse_replacement = parse_anchor + """    skip_initial_final_real_validation = _to_bool(
        (
            cfg.get("formal_outer_protocol")
            if isinstance(cfg.get("formal_outer_protocol"), dict)
            else {}
        ).get("skip_initial_final_real_validation", False),
        False,
    )
"""
    skip_anchor = """        if not final_eval_run_validation:
            raise FinalValidationSkipped()
"""
    skip_replacement = """        if skip_initial_final_real_validation:
            main_logger.log_struct(
                {
                    "event": "formal_initial_final_real_validation_skipped",
                    "reason": "excluded_incumbent_must_not_consume_candidate_replay",
                    "physical_call_reserved": False,
                    "workload_started": False,
                    "accepted_counter_updated": False,
                    "gradient_incumbent_or_range_updated": False,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            raise FinalValidationSkipped()
        if not final_eval_run_validation:
            raise FinalValidationSkipped()
"""
    seed_anchor = """    if (
        final_feedback_enabled
        and optimizer_mode in {"gradient", "bruteforce"}
        and final_eval_run_validation
        and final_validation
    ):
"""
    seed_replacement = """    if skip_initial_final_real_validation and not final_validation:
        result.optimized_params = copy.deepcopy(result.initial_params)
        result.final_resource = copy.deepcopy(baseline_runtime_normalized)
        result.final_utilization = copy.deepcopy(baseline_runtime_util)
        final_validation = _baseline_validation_for_feedback()
        final_top_sql = copy.deepcopy(baseline_top_sql)
        final_plan_samples = copy.deepcopy(baseline_plan_samples)
        final_plan_changes = []
        degradation_summary = None
        main_logger.log_struct(
            {
                "event": "formal_initial_final_baseline_seeded_without_replay",
                "reason": "dispatch_guarded_unseen_ranked_candidates_directly",
                "seed_params": copy.deepcopy(result.optimized_params),
                "physical_call_reserved": False,
                "workload_started": False,
                "accepted_counter_updated": False,
                "gradient_incumbent_or_range_updated": False,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    if (
        final_feedback_enabled
        and optimizer_mode in {"gradient", "bruteforce"}
        and final_eval_run_validation
        and final_validation
    ):
"""
    replacements = (
        (parse_anchor, parse_replacement),
        (skip_anchor, skip_replacement),
        (seed_anchor, seed_replacement),
    )
    result = source
    for anchor, replacement in replacements:
        if result.count(anchor) != 1:
            raise RuntimeError(f"optimizer patch anchor count changed: {anchor[:50]!r}")
        result = result.replace(anchor, replacement)
    return result


def zero_call_test_source() -> str:
    return """#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, "/home/anonymous/program/resource_adaptive")
OPTIMIZER = HERE / "parameter_optimizer_xp_cont16b.py"
CONFIG = HERE / "config.json"
TOP5 = HERE / "cont16b_offline_top5.json"
SOURCE_HISTORY = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726/"
    "formal10_cont16_accepted4_exclusion11_v1_20260729T2228/"
    "optimizer_history_2026-07-29_223751.jsonl"
)
spec = importlib.util.spec_from_file_location("xp_cont16b_optimizer", OPTIMIZER)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot import isolated optimizer")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
guard_spec = importlib.util.spec_from_file_location(
    "xp_cont16b_guard", HERE / "xp_candidate_exclusion_guard.py"
)
if guard_spec is None or guard_spec.loader is None:
    raise RuntimeError("cannot import cont16b guard")
guard = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(guard)
config = json.loads(CONFIG.read_text(encoding="utf-8"))
top5 = json.loads(TOP5.read_text(encoding="utf-8"))
events = [
    json.loads(line) for line in SOURCE_HISTORY.read_text(encoding="utf-8").splitlines()
    if line.strip()
]
ranking = next(
    event["candidate_rankings"]
    for event in reversed(events)
    if event.get("event") == "experience_table_all_subset_ranking"
)
excluded = config["formal_outer_protocol"]["excluded_prior_three_level_fingerprints"]
filtered, audit = module._filter_gradient_rankings_by_three_level_fingerprint(
    ranking, excluded
)
actual = [row["three_level_fingerprint"]["id"] for row in filtered[:5]]
expected = [row["three_level_fingerprint"]["id"] for row in top5["candidates"]]
assert actual == expected
assert len(set(actual)) == 5
assert not set(actual) & set(excluded)
guard_excluded, guard_selected = guard.contract(HERE)
assert guard_excluded == set(excluded)
assert guard_selected == set(expected)
source = OPTIMIZER.read_text(encoding="utf-8")
assert source.count("formal_initial_final_real_validation_skipped") == 1
assert source.count("formal_initial_final_baseline_seeded_without_replay") == 1
assert '"physical_call_reserved": False' in source
assert config["formal_outer_protocol"]["skip_initial_final_real_validation"] is True
assert config["formal_outer_protocol"]["formal_search_replay_count_start"] == 17
assert config["formal_outer_protocol"]["formal_search_maximum_replay_count"] == 21
print(json.dumps({
    "status": "passed",
    "physical_calls": 0,
    "workload_started": False,
    "initial_final_reservation_skipped": True,
    "baseline_seeded_without_replay": True,
    "filtered_queue_matches_attested_top5": True,
    "guard_contract_matches_attested_top5": True,
    "filtered_candidate_count": len(filtered),
    "excluded_count": audit["excluded_fingerprint_count"],
    "remaining_physical_budget": 4,
}, sort_keys=True))
"""


def launcher(
    *,
    config_sha: str,
    top5_sha: str,
    guard_sha: str,
    optimizer_sha: str,
    test_sha: str,
    manifest_sha: str,
) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
OUTPUT={TARGET}
CONFIG="$OUTPUT/config.json"
TOP5="$OUTPUT/cont16b_offline_top5.json"
GUARD="$OUTPUT/xp_candidate_exclusion_guard.py"
OPTIMIZER="$OUTPUT/{PATCHED_OPTIMIZER_NAME}"
TEST="$OUTPUT/test_xp_cont16b_zero_call.py"
MANIFEST="$OUTPUT/formal_manifest.json"
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
AFFINITY=32-51,288-307

test "$AFFINITY" = "32-51,288-307"
test "$(sha256sum "$CONFIG" | awk '{{print $1}}')" = "{config_sha}"
test "$(sha256sum "$TOP5" | awk '{{print $1}}')" = "{top5_sha}"
test "$(sha256sum "$GUARD" | awk '{{print $1}}')" = "{guard_sha}"
test "$(sha256sum "$OPTIMIZER" | awk '{{print $1}}')" = "{optimizer_sha}"
test "$(sha256sum "$TEST" | awk '{{print $1}}')" = "{test_sha}"
test "$(sha256sum "$MANIFEST" | awk '{{print $1}}')" = "{manifest_sha}"
test "$(cat "$OUTPUT/state/formal_search_accepted_counter.txt")" = "4"
test "$(cat "$OUTPUT/state/formal_search_replay_counter.txt")" = "17"
test "$(cat "$OUTPUT/state/global_replay_counter.txt")" = "17"
test "$(cat "$OUTPUT/state/formal_startup_baseline_counter.txt")" = "0"
test "$(jq -r '.formal_outer_protocol.skip_initial_final_real_validation' "$CONFIG")" = "true"
test "$(jq -r '.formal_outer_protocol.formal_search_maximum_replay_count' "$CONFIG")" = "21"
test "$(jq -r '.formal_outer_protocol.excluded_prior_three_level_fingerprints | unique | length' "$CONFIG")" = "11"
test "$(jq -r '.candidates | length' "$TOP5")" = "5"
"$PYTHON" "$TEST"
! pgrep -af "$CONFIG"

EXPECTED_BASELINE=$'cpu_index_tuple_cost=0.005\\ncpu_operator_cost=0.0025\\ncpu_tuple_cost=0.01\\nparallel_setup_cost=1000\\nparallel_tuple_cost=0.1\\nrandom_page_cost=4\\nseq_page_cost=0.2'
ACTUAL_BASELINE="$(PGPASSWORD=postgres psql -h 127.0.0.1 -p 59630 -U postgres -d postgres -Atc "select name||'='||setting from pg_settings where name in ('seq_page_cost','random_page_cost','cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost','parallel_tuple_cost') order by name")"
test "$ACTUAL_BASELINE" = "$EXPECTED_BASELINE"

cd {PROJECT}
exec taskset -c "$AFFINITY" env OPTIMIZER_CONFIG="$CONFIG" "$PYTHON" -u "$OPTIMIZER"
"""


def main() -> None:
    if STAGING.exists():
        raise RuntimeError(f"staging already exists: {STAGING}")
    for path, key in (
        (SOURCE / "config.json", "source_config"),
        (SOURCE / "cont16_offline_top5.json", "source_top5"),
        (SOURCE_HISTORY, "source_history"),
        (SOURCE / "state" / "plan_basin_escape_state.json", "source_basin"),
        (BASE_OPTIMIZER, "base_optimizer"),
        (BASELINE_RESTORE_AUDIT, "baseline_restore"),
        (BASELINE_VERIFY_AUDIT, "baseline_verify"),
    ):
        require_sha(path, EXPECTED_SHA[key])
    observed_baseline = dict(
        line.split("=", 1)
        for line in BASELINE_VERIFY_AUDIT.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    if observed_baseline != EXPECTED_BASELINE:
        raise RuntimeError(f"baseline verification changed: {observed_baseline}")
    expected_counters = {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 17,
        "global_replay_counter.txt": 17,
        "formal_startup_baseline_counter.txt": 0,
    }
    for name, expected in expected_counters.items():
        actual = int((SOURCE / "state" / name).read_text().strip())
        if actual != expected:
            raise RuntimeError(f"cont16 counter changed: {name}={actual}")
    events = [
        json.loads(line)
        for line in SOURCE_HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reservations = [
        event
        for event in events
        if event.get("event") == "formal_initial_final_physical_call_reserved"
    ]
    if (
        len(reservations) != 1
        or reservations[0].get("formal_search_replay_before") != 16
        or reservations[0].get("formal_search_replay_after") != 17
        or reservations[0].get("technical_failure_consumes_reserved_call") is not True
        or reservations[0].get("accepted_counter_updated") is not False
        or reservations[0].get("gradient_incumbent_or_range_updated") is not False
    ):
        raise RuntimeError("cont16 technical-failure reservation evidence changed")
    exception = next(
        (
            event
            for event in reversed(events)
            if event.get("event") == "optimizer_process_exception"
        ),
        None,
    )
    if (
        exception is None
        or exception.get("exception_type") != "RuntimeError"
        or "actual_score_required=true" not in str(exception.get("exception_message"))
    ):
        raise RuntimeError("cont16 fail-closed exception evidence changed")

    STAGING.mkdir(parents=True)
    (STAGING / "state").mkdir()
    (STAGING / "subset_logs").mkdir()
    for name, value in expected_counters.items():
        atomic_text(STAGING / "state" / name, f"{value}\n")
    basin_text = (SOURCE / "state" / "plan_basin_escape_state.json").read_text(
        encoding="utf-8"
    )
    atomic_text(STAGING / "state" / "plan_basin_escape_state.json", basin_text)

    config = replace_path(
        json.loads((SOURCE / "config.json").read_text(encoding="utf-8")),
        str(SOURCE),
        str(TARGET),
    )
    formal = config["formal_outer_protocol"]
    formal.update({
        "skip_initial_final_real_validation": True,
        "formal_search_accepted_count_start": 4,
        "formal_search_replay_count_start": 17,
        "global_replay_count_start": 17,
        "global_replay_ordinal_at_binding": 17,
        "initial_real_replay_count": 17,
        "formal_search_attempt_safety_cap": 21,
        "formal_search_maximum_replay_count": 21,
        "maximum_cumulative_real_replay_count": 21,
        "formal_search_replay_cumulative_tiers": [21],
        "formal_search_accepted_counter_path": str(
            TARGET / "state" / "formal_search_accepted_counter.txt"
        ),
        "formal_search_replay_counter_path": str(
            TARGET / "state" / "formal_search_replay_counter.txt"
        ),
        "global_replay_counter_path": str(TARGET / "state" / "global_replay_counter.txt"),
        "formal_startup_baseline_counter_path": str(
            TARGET / "state" / "formal_startup_baseline_counter.txt"
        ),
    })
    config["logging"]["history_path"] = str(TARGET / "optimizer_history.jsonl")
    config["optimizer"]["gradient"]["subset_log_dir"] = str(TARGET / "subset_logs")
    config["optimizer"]["gradient"]["plan_basin_escape"]["state_path"] = str(
        TARGET / "state" / "plan_basin_escape_state.json"
    )
    config["_xp_formal_continuation"] = {
        "contract": "xp_formal10_cont16b_skip_initial_real_validation_v1",
        "source_continuation": str(SOURCE),
        "accepted_start": 4,
        "formal_replay_start": 17,
        "global_replay_start": 17,
        "hard_stop": 21,
        "remaining_physical_budget": 4,
        "initial_final_real_validation_skipped": True,
        "initial_final_physical_call_reserved": False,
        "dispatch_queue": "guarded_unseen_ranked_candidates",
        "sealed_top5_path": str(TARGET / "cont16b_offline_top5.json"),
        "qps_used_for_inner_scoring": False,
        "technical_failure_consumes_physical_cap": True,
        "technical_failure_counts_as_valid_or_accepted": False,
        "rejected_candidates_update_gradient_incumbent_range_or_accepted": False,
        "same_scene_real_replay_serial": True,
    }
    config_path = STAGING / "config.json"
    atomic_text(config_path, json.dumps(config, indent=2) + "\n")

    top5 = json.loads(
        (SOURCE / "cont16_offline_top5.json").read_text(encoding="utf-8")
    )
    top5.update({
        "schema_version": "xp_cont16b_offline_top5_v1",
        "source_cont16": str(SOURCE),
        "formal_replay_start": 17,
        "formal_replay_hard_stop": 21,
        "remaining_physical_budget": 4,
        "selected_count": 5,
        "actual_queue_policy": (
            "attested Top-5 order; hard stop permits at most first four physical calls"
        ),
    })
    top5_path = STAGING / "cont16b_offline_top5.json"
    atomic_text(top5_path, json.dumps(top5, indent=2) + "\n")

    builder = load_cont16_builder()
    guard_path = STAGING / "xp_candidate_exclusion_guard.py"
    guard_text = builder.guard_source().replace(
        '"cont16_offline_top5.json"',
        '"cont16b_offline_top5.json"',
    )
    if guard_text.count('"cont16b_offline_top5.json"') != 1:
        raise RuntimeError("cont16b guard Top-5 filename patch failed")
    atomic_text(guard_path, guard_text, mode=0o700)
    patched_optimizer_path = STAGING / PATCHED_OPTIMIZER_NAME
    atomic_text(
        patched_optimizer_path,
        patch_optimizer(BASE_OPTIMIZER.read_text(encoding="utf-8")),
        mode=0o700,
    )
    compile_result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(patched_optimizer_path)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if compile_result.returncode != 0:
        raise RuntimeError(f"isolated optimizer compile failed: {compile_result.stderr}")

    test_path = STAGING / "test_xp_cont16b_zero_call.py"
    atomic_text(test_path, zero_call_test_source(), mode=0o700)
    test_result = subprocess.run(
        [sys.executable, str(test_path)],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if test_result.returncode != 0:
        raise RuntimeError(f"zero-call test failed:\n{test_result.stdout}\n{test_result.stderr}")
    audit = json.loads(test_result.stdout)
    audit_path = STAGING / "zero_call_validation.json"
    atomic_text(audit_path, json.dumps(audit, indent=2, sort_keys=True) + "\n")

    manifest = {
        "schema_version": "xp_formal_continuation_manifest_v1",
        "contract": "xp_formal10_cont16b_skip_initial_real_validation_v1",
        "source_cont16": str(SOURCE),
        "target_output": str(TARGET),
        "staging_package": str(STAGING),
        "accepted_split": {
            "inherited": 3,
            "prior_formal": 1,
            "this_batch": 0,
            "official_total": 4,
        },
        "counter_start": {
            "accepted": 4,
            "formal_replay": 17,
            "global_replay": 17,
            "startup": 0,
        },
        "counter_hard_stop": {"formal_replay": 21, "global_replay": 21},
        "remaining_physical_budget": 4,
        "initial_final_skip": {
            "enabled": True,
            "physical_call_reserved": False,
            "workload_started": False,
            "baseline_seed_source": "sealed startup baseline reuse",
            "candidate_dispatch": "filtered unseen ranking guarded by attested Top-5",
        },
        "candidate_count": 5,
        "cumulative_exclusion_count": 11,
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "do_not_touch_cpu_184-199,440-455_or_mysql_state",
        "same_scene_real_replay_serial": True,
        "qps_used_for_inner_scoring": False,
        "technical_failure_consumes_physical_cap": True,
        "technical_failure_counts_as_valid_or_accepted": False,
        "rejected_updates_forbidden": True,
        "prepared_only_no_workload_started": True,
        "baseline_restore_audit": {
            "path": str(BASELINE_RESTORE_AUDIT),
            "sha256": EXPECTED_SHA["baseline_restore"],
            "verified_path": str(BASELINE_VERIFY_AUDIT),
            "verified_sha256": EXPECTED_SHA["baseline_verify"],
        },
        "source_sha256": EXPECTED_SHA,
        "package_sha256": {
            "config.json": sha256(config_path),
            "cont16b_offline_top5.json": sha256(top5_path),
            "xp_candidate_exclusion_guard.py": sha256(guard_path),
            PATCHED_OPTIMIZER_NAME: sha256(patched_optimizer_path),
            "test_xp_cont16b_zero_call.py": sha256(test_path),
            "zero_call_validation.json": sha256(audit_path),
        },
        "zero_call_validation": audit,
    }
    manifest_path = STAGING / "formal_manifest.json"
    atomic_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    launcher_path = STAGING / "launch_cont16b_persistent_pty.sh"
    atomic_text(
        launcher_path,
        launcher(
            config_sha=sha256(config_path),
            top5_sha=sha256(top5_path),
            guard_sha=sha256(guard_path),
            optimizer_sha=sha256(patched_optimizer_path),
            test_sha=sha256(test_path),
            manifest_sha=sha256(manifest_path),
        ),
        mode=0o700,
    )
    print(json.dumps({
        "staging": str(STAGING),
        "target": str(TARGET),
        "config_sha256": sha256(config_path),
        "top5_sha256": sha256(top5_path),
        "guard_sha256": sha256(guard_path),
        "patched_optimizer_sha256": sha256(patched_optimizer_path),
        "test_sha256": sha256(test_path),
        "manifest_sha256": sha256(manifest_path),
        "launcher_sha256": sha256(launcher_path),
        "accepted_start": 4,
        "formal_replay_start": 17,
        "hard_stop": 21,
        "remaining_physical_budget": 4,
        "physical_calls": 0,
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
