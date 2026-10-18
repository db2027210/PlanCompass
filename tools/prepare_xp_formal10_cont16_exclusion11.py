#!/usr/bin/env python3
"""Build and validate the XP cont16 continuation without running a workload."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
SOURCE = ATTEMPT / "formal10_cont15_accepted4_guc_tolerance_guard_v1_20260729T2140"
TARGET = ATTEMPT / "formal10_cont16_accepted4_exclusion11_v1_20260729T2228"
STAGING = Path("/tmp/xp_cont16_accepted4_exclusion11_v1_20260729T2228")
PROJECT = Path("/home/anonymous/program/resource_adaptive")
OPTIMIZER = PROJECT / "parameter_optimizer.py"
SOURCE_HISTORY = SOURCE / "optimizer_history_2026-07-29_214236.jsonl"
OPTIMIZER_SHA = "960f999b552c23f1b84579c6f9fe4e895e06c47b66da0de91a2981b684d11c19"
SOURCE_HISTORY_SHA = "97d63ee2b357d981fdd097b53c19e446bba9c05a30b9a908e22239d2a2e58106"
SOURCE_CONFIG_SHA = "87fcacc04eaa0dac565a37816ea315598c1142534e4cac06f49a3c5a40ad5df7"
SOURCE_BASIN_SHA = "12757774eb3a7c9534c73f482eeeefb7640d6989ac760573b9b0e12efe511fe2"
AUDIT_FIELDS = (
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
NAMES = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


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


def fingerprint(candidate: Dict[str, Any]) -> str:
    value = candidate.get("three_level_fingerprint")
    if isinstance(value, dict):
        value = value.get("id")
    if not isinstance(value, str) or not value.startswith("three-level-sha256:"):
        raise RuntimeError("candidate has no valid three-level fingerprint")
    return value


def params_equal(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return set(left) == set(right) == set(NAMES) and all(
        math.isclose(float(left[name]), float(right[name]), rel_tol=1e-12, abs_tol=1e-15)
        for name in NAMES
    )


def latest_event(events: Iterable[Dict[str, Any]], name: str) -> Dict[str, Any]:
    for event in reversed(list(events)):
        if event.get("event") == name:
            return event
    raise RuntimeError(f"missing event: {name}")


def replace_path(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: replace_path(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_path(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def guard_source() -> str:
    return f"""#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

OUTPUT = Path({str(TARGET)!r})
NAMES = {NAMES!r}

class GuardFailure(RuntimeError):
    pass

def fail(message: str) -> int:
    print(json.dumps({{
        "status": "fail_closed_before_workload",
        "reason": message,
        "workload_started": False,
    }}, sort_keys=True), flush=True)
    return 42

def resolve_history(directory: Path) -> Path:
    histories = sorted(
        path for path in directory.glob("optimizer_history_*.jsonl")
        if path.is_file() and not path.name.endswith("_db_monitor.csv")
    )
    if len(histories) != 1:
        raise GuardFailure(
            f"expected exactly one dated optimizer history JSONL, found {{len(histories)}}"
        )
    return histories[0]

def candidate_fingerprint(history: Path, params: dict) -> str:
    ranking = None
    for raw in reversed(history.read_text(encoding="utf-8").splitlines()):
        if not raw.strip():
            continue
        row = json.loads(raw)
        if row.get("event") == "experience_table_all_subset_ranking":
            ranking = row.get("candidate_rankings") or []
            break
    if not isinstance(ranking, list) or not ranking:
        raise GuardFailure("dated history has no non-empty latest ranking")
    matches = []
    for candidate in ranking:
        candidate_params = candidate.get("optimized_params") or {{}}
        if set(candidate_params) != set(NAMES):
            continue
        if all(math.isclose(
            float(candidate_params[name]), float(params[name]),
            rel_tol=1e-5, abs_tol=1e-9,
        ) for name in NAMES):
            matches.append(candidate)
    if len(matches) != 1:
        raise GuardFailure(
            f"current GUC-normalized candidate matched {{len(matches)}} ranking rows; "
            "exactly one is required"
        )
    value = matches[0].get("three_level_fingerprint") or {{}}
    matched = value.get("id") if isinstance(value, dict) else value
    if not isinstance(matched, str) or not matched.startswith("three-level-sha256:"):
        raise GuardFailure("current candidate has no three-level fingerprint")
    return matched

def contract(directory: Path) -> tuple[set[str], set[str]]:
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    top5 = json.loads((directory / "cont16_offline_top5.json").read_text(encoding="utf-8"))
    excluded = set(config["formal_outer_protocol"]["excluded_prior_three_level_fingerprints"])
    selected = {{row["three_level_fingerprint"]["id"] for row in top5["candidates"]}}
    if len(excluded) != 11 or len(selected) != 5 or excluded & selected:
        raise GuardFailure("sealed exclusion/Top-5 contract is inconsistent")
    return excluded, selected

def validate_candidate(directory: Path, params: dict) -> str:
    matched = candidate_fingerprint(resolve_history(directory), params)
    excluded, selected = contract(directory)
    if matched in excluded:
        raise GuardFailure("prior physical-call fingerprint is excluded")
    if matched not in selected:
        raise GuardFailure("candidate is outside sealed cont16 Top-5")
    return matched

def main() -> int:
    if len(sys.argv) < 2:
        return fail("missing delegated workload command")
    query = (
        "select name||'='||setting from pg_settings where name in ("
        + ",".join("'" + name + "'" for name in NAMES)
        + ") order by name"
    )
    completed = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-p", "59630", "-U", "postgres",
         "-d", "postgres", "-Atc", query],
        env={{**os.environ, "PGPASSWORD": "postgres"}},
        text=True, capture_output=True, timeout=20, check=False,
    )
    if completed.returncode != 0:
        return fail("candidate parameter readback failed")
    params = {{}}
    try:
        for line in completed.stdout.splitlines():
            name, value = line.split("=", 1)
            params[name] = float(value)
        if set(params) != set(NAMES):
            raise GuardFailure("candidate parameter readback incomplete")
        validate_candidate(OUTPUT, params)
    except (GuardFailure, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return fail(str(exc))
    return subprocess.run(sys.argv[1:], check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
"""


def zero_call_test_source() -> str:
    return """#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("xp_guard", HERE / "xp_candidate_exclusion_guard.py")
if spec is None or spec.loader is None:
    raise RuntimeError("cannot import guard")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
sealed = json.loads((HERE / "cont16_offline_top5.json").read_text(encoding="utf-8"))
config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
excluded = config["formal_outer_protocol"]["excluded_prior_three_level_fingerprints"]
with tempfile.TemporaryDirectory(prefix="xp_cont16_zero_call_") as raw:
    directory = Path(raw)
    (directory / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (directory / "cont16_offline_top5.json").write_text(json.dumps(sealed), encoding="utf-8")
    ranking = sealed["candidates"] + [{
        "optimized_params": sealed["cont15_physical_candidates"][0]["optimized_params"],
        "three_level_fingerprint": {"id": excluded[-5]},
    }]
    history = directory / "optimizer_history_2026-07-29_235959.jsonl"
    history.write_text(json.dumps({
        "event": "experience_table_all_subset_ranking",
        "candidate_rankings": ranking,
    }) + "\\n", encoding="utf-8")
    (directory / "optimizer_history_2026-07-29_235959_db_monitor.csv").write_text(
        "timestamp,db_qps\\n", encoding="utf-8"
    )
    for candidate in sealed["candidates"]:
        actual = guard.validate_candidate(directory, candidate["optimized_params"])
        assert actual == candidate["three_level_fingerprint"]["id"]
    try:
        guard.validate_candidate(
            directory, sealed["cont15_physical_candidates"][0]["optimized_params"]
        )
    except guard.GuardFailure:
        pass
    else:
        raise AssertionError("excluded physical-call fingerprint was allowed")
    missing = dict(sealed["candidates"][0]["optimized_params"])
    missing["random_page_cost"] = float(missing["random_page_cost"]) + 1.0
    try:
        guard.validate_candidate(directory, missing)
    except guard.GuardFailure:
        pass
    else:
        raise AssertionError("missing candidate did not fail closed")
print(json.dumps({
    "status": "passed",
    "physical_calls": 0,
    "workload_started": False,
    "selected_candidates_allowed": 5,
    "excluded_cont15_candidate_blocked": True,
    "missing_candidate_blocked": True,
    "db_monitor_csv_ignored": True,
}, sort_keys=True))
"""


def launcher(
    config_sha: str,
    top5_sha: str,
    guard_sha: str,
    test_sha: str,
    manifest_sha: str,
) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
OUTPUT={TARGET}
CONFIG="$OUTPUT/config.json"
TOP5="$OUTPUT/cont16_offline_top5.json"
GUARD="$OUTPUT/xp_candidate_exclusion_guard.py"
TEST="$OUTPUT/test_xp_cont16_zero_call.py"
MANIFEST="$OUTPUT/formal_manifest.json"
OPTIMIZER={OPTIMIZER}
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
AFFINITY=32-51,288-307

test "$AFFINITY" = "32-51,288-307"
test "$(sha256sum "$CONFIG" | awk '{{print $1}}')" = "{config_sha}"
test "$(sha256sum "$TOP5" | awk '{{print $1}}')" = "{top5_sha}"
test "$(sha256sum "$GUARD" | awk '{{print $1}}')" = "{guard_sha}"
test "$(sha256sum "$TEST" | awk '{{print $1}}')" = "{test_sha}"
test "$(sha256sum "$MANIFEST" | awk '{{print $1}}')" = "{manifest_sha}"
test "$(sha256sum "$OPTIMIZER" | awk '{{print $1}}')" = "{OPTIMIZER_SHA}"
test "$(cat "$OUTPUT/state/formal_search_accepted_counter.txt")" = "4"
test "$(cat "$OUTPUT/state/formal_search_replay_counter.txt")" = "16"
test "$(cat "$OUTPUT/state/global_replay_counter.txt")" = "16"
test "$(cat "$OUTPUT/state/formal_startup_baseline_counter.txt")" = "0"
test "$(jq -r '.formal_outer_protocol.formal_search_maximum_replay_count' "$CONFIG")" = "21"
test "$(jq -r '.formal_outer_protocol.maximum_cumulative_real_replay_count' "$CONFIG")" = "21"
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
    require_sha(OPTIMIZER, OPTIMIZER_SHA)
    require_sha(SOURCE_HISTORY, SOURCE_HISTORY_SHA)
    require_sha(SOURCE / "config.json", SOURCE_CONFIG_SHA)
    require_sha(SOURCE / "state" / "plan_basin_escape_state.json", SOURCE_BASIN_SHA)

    expected_counters = {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 16,
        "global_replay_counter.txt": 16,
        "formal_startup_baseline_counter.txt": 0,
    }
    for name, expected in expected_counters.items():
        actual = int((SOURCE / "state" / name).read_text().strip())
        if actual != expected:
            raise RuntimeError(f"cont15 state changed: {name}={actual}")

    events = [
        json.loads(line)
        for line in SOURCE_HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reservations = [
        row for row in events
        if row.get("event") in {
            "formal_initial_final_physical_call_reserved",
            "formal_feedback_physical_call_reserved",
        }
    ]
    if len(reservations) != 5:
        raise RuntimeError(f"cont15 physical reservations changed: {len(reservations)}")
    if any(
        row.get("accepted_counter_updated") is not False
        or row.get("gradient_incumbent_or_range_updated") is not False
        for row in reservations
    ):
        raise RuntimeError("cont15 reservation changed formal state")

    replay = latest_event(events, "experience_table_gradient_initial_real_replay_complete")
    evaluations = replay.get("candidate_evaluations") or []
    if len(evaluations) != 5:
        raise RuntimeError("cont15 does not contain exactly five candidate evaluations")
    if any(
        row.get("accepted_by_actual_score") is not False
        or row.get("candidate_actual_score") is None
        for row in evaluations
    ):
        raise RuntimeError("cont15 calls were not five valid rejected replays")

    ranking_event = latest_event(events, "experience_table_all_subset_ranking")
    rankings = ranking_event.get("candidate_rankings") or []
    if not rankings:
        raise RuntimeError("cont15 latest ranking is empty")
    physical_candidates: List[Dict[str, Any]] = []
    physical_fingerprints: List[str] = []
    for evaluation in evaluations:
        params = evaluation.get("candidate_optimized_params") or {}
        matches = [
            row for row in rankings
            if params_equal(row.get("optimized_params") or {}, params)
        ]
        if len(matches) != 1:
            raise RuntimeError(f"cont15 physical candidate matched {len(matches)} ranking rows")
        candidate = copy.deepcopy(matches[0])
        candidate["actual_score"] = evaluation["candidate_actual_score"]
        candidate["accepted_by_actual_score"] = False
        physical_candidates.append(candidate)
        physical_fingerprints.append(fingerprint(candidate))
    if len(set(physical_fingerprints)) != 5:
        raise RuntimeError("cont15 physical fingerprints are not five unique values")

    source_config = json.loads((SOURCE / "config.json").read_text(encoding="utf-8"))
    prior_exclusions = source_config["formal_outer_protocol"][
        "excluded_prior_three_level_fingerprints"
    ]
    exclusions = list(dict.fromkeys(prior_exclusions + physical_fingerprints))
    if len(prior_exclusions) != 6 or len(exclusions) != 11:
        raise RuntimeError("expected six prior and eleven cumulative exclusions")

    selected: List[Dict[str, Any]] = []
    seen = set(exclusions)
    for original in rankings:
        fp = fingerprint(original)
        if fp in seen:
            continue
        missing = [field for field in AUDIT_FIELDS if field not in original]
        if missing:
            raise RuntimeError(f"candidate {fp} missing audit fields: {missing}")
        params = original.get("optimized_params") or {}
        if set(params) != set(NAMES):
            raise RuntimeError(f"candidate {fp} has incomplete parameters")
        candidate = copy.deepcopy(original)
        candidate["original_rank"] = candidate.get("rank")
        candidate["replay_rank"] = len(selected) + 1
        selected.append(candidate)
        seen.add(fp)
        if len(selected) == 5:
            break
    if len(selected) != 5:
        raise RuntimeError("fewer than five new candidates remain")

    STAGING.mkdir(parents=True)
    (STAGING / "state").mkdir()
    (STAGING / "subset_logs").mkdir()
    for name, value in expected_counters.items():
        atomic_text(STAGING / "state" / name, f"{value}\n")
    basin = json.loads(
        (SOURCE / "state" / "plan_basin_escape_state.json").read_text(encoding="utf-8")
    )
    atomic_text(
        STAGING / "state" / "plan_basin_escape_state.json",
        json.dumps(basin, indent=2, sort_keys=True) + "\n",
    )

    config = replace_path(copy.deepcopy(source_config), str(SOURCE), str(TARGET))
    formal = config["formal_outer_protocol"]
    formal.update({
        "excluded_prior_three_level_fingerprints": exclusions,
        "formal_search_accepted_count_start": 4,
        "formal_search_replay_count_start": 16,
        "global_replay_count_start": 16,
        "global_replay_ordinal_at_binding": 16,
        "initial_real_replay_count": 16,
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
    gradient = config["optimizer"]["gradient"]
    gradient["subset_log_dir"] = str(TARGET / "subset_logs")
    gradient["plan_basin_escape"]["state_path"] = str(
        TARGET / "state" / "plan_basin_escape_state.json"
    )
    old_guard = f"/home/anonymous/miniconda3/envs/py313/bin/python -u {TARGET / 'xp_candidate_exclusion_guard.py'} "
    workload = config["benchmark"]["workload_cmd"]
    if not workload.startswith(old_guard):
        raise RuntimeError("cont15 workload guard prefix changed")
    delegated = workload[len(old_guard):]
    config["benchmark"]["workload_cmd"] = (
        f"/home/anonymous/miniconda3/envs/py313/bin/python -u "
        f"{TARGET / 'xp_candidate_exclusion_guard.py'} {delegated}"
    )
    config["_xp_formal_continuation"] = {
        "contract": "xp_formal10_cont16_exclusion11_v1",
        "source_continuation": str(SOURCE),
        "accepted_start": 4,
        "formal_replay_start": 16,
        "global_replay_start": 16,
        "batch_physical_cap": 5,
        "maximum_candidate_calls": 5,
        "excluded_prior_fingerprint_count": 11,
        "sealed_top5_path": str(TARGET / "cont16_offline_top5.json"),
        "qps_used_for_inner_scoring": False,
        "technical_failure_consumes_physical_cap": True,
        "technical_failure_counts_as_valid_or_accepted": False,
        "rejected_candidates_update_gradient_incumbent_range_or_accepted": False,
        "same_scene_real_replay_serial": True,
        "parameter_optimizer_sha256": OPTIMIZER_SHA,
    }
    config_path = STAGING / "config.json"
    atomic_text(config_path, json.dumps(config, indent=2) + "\n")

    top5 = {
        "schema_version": "xp_cont16_offline_top5_v1",
        "source_cont15": str(SOURCE),
        "source_history": str(SOURCE_HISTORY),
        "source_history_sha256": SOURCE_HISTORY_SHA,
        "physical_calls_consumed_by_cont15": 5,
        "accepted_start": 4,
        "formal_replay_start": 16,
        "formal_replay_hard_stop": 21,
        "excluded_prior_three_level_fingerprints": exclusions,
        "selected_count": 5,
        "serial_real_replay_required": True,
        "qps_used_for_inner_scoring": False,
        "technical_failure_consumes_physical_cap": True,
        "technical_failure_counts_as_valid_or_accepted": False,
        "rejected_updates_forbidden": True,
        "cont15_physical_candidates": physical_candidates,
        "candidates": selected,
    }
    top5_path = STAGING / "cont16_offline_top5.json"
    atomic_text(top5_path, json.dumps(top5, indent=2) + "\n")

    guard_path = STAGING / "xp_candidate_exclusion_guard.py"
    atomic_text(guard_path, guard_source(), mode=0o700)
    test_path = STAGING / "test_xp_cont16_zero_call.py"
    atomic_text(test_path, zero_call_test_source(), mode=0o700)
    test_result = subprocess.run(
        [sys.executable, str(test_path)],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if test_result.returncode != 0:
        raise RuntimeError(f"zero-call test failed:\n{test_result.stdout}\n{test_result.stderr}")
    audit = json.loads(test_result.stdout)
    audit_path = STAGING / "zero_call_validation.json"
    atomic_text(audit_path, json.dumps(audit, indent=2, sort_keys=True) + "\n")

    manifest = {
        "schema_version": "xp_formal_continuation_manifest_v1",
        "contract": "xp_formal10_cont16_exclusion11_v1",
        "source_cont15": str(SOURCE),
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
            "formal_replay": 16,
            "global_replay": 16,
            "startup": 0,
        },
        "counter_hard_stop": {"formal_replay": 21, "global_replay": 21},
        "per_batch_physical_cap": 5,
        "candidate_count": 5,
        "cumulative_exclusion_count": 11,
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "do_not_touch_cpu_184-199,440-455_or_mysql_state",
        "same_scene_real_replay_serial": True,
        "qps_used_for_inner_scoring": False,
        "technical_failure_consumes_physical_cap": True,
        "technical_failure_counts_as_valid_or_accepted": False,
        "rejected_updates_forbidden": True,
        "incumbent_and_range_unchanged_from_cont15": True,
        "prepared_only_no_workload_started": True,
        "source_sha256": {
            "config": SOURCE_CONFIG_SHA,
            "history": SOURCE_HISTORY_SHA,
            "basin": SOURCE_BASIN_SHA,
            "parameter_optimizer": OPTIMIZER_SHA,
        },
        "package_sha256": {
            "config.json": sha256(config_path),
            "cont16_offline_top5.json": sha256(top5_path),
            "xp_candidate_exclusion_guard.py": sha256(guard_path),
            "test_xp_cont16_zero_call.py": sha256(test_path),
            "zero_call_validation.json": sha256(audit_path),
        },
        "zero_call_validation": audit,
    }
    manifest_path = STAGING / "formal_manifest.json"
    atomic_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    launcher_path = STAGING / "launch_cont16_persistent_pty.sh"
    atomic_text(
        launcher_path,
        launcher(
            sha256(config_path),
            sha256(top5_path),
            sha256(guard_path),
            sha256(test_path),
            sha256(manifest_path),
        ),
        mode=0o700,
    )
    print(json.dumps({
        "staging": str(STAGING),
        "target": str(TARGET),
        "config_sha256": sha256(config_path),
        "top5_sha256": sha256(top5_path),
        "guard_sha256": sha256(guard_path),
        "test_sha256": sha256(test_path),
        "manifest_sha256": sha256(manifest_path),
        "launcher_sha256": sha256(launcher_path),
        "selected_fingerprints": [fingerprint(row) for row in selected],
        "excluded_fingerprints": exclusions,
        "physical_calls": 0,
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
