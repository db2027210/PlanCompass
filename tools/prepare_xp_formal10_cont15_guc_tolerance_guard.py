#!/usr/bin/env python3
"""Prepare, but never launch, the XP cont15 formal continuation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
SOURCE = ATTEMPT / "formal10_cont14_accepted4_dated_history_guard_v1_20260729T2128"
OUTPUT = ATTEMPT / "formal10_cont15_accepted4_guc_tolerance_guard_v1_20260729T2140"
PROJECT = Path("/home/anonymous/program/resource_adaptive")
OPTIMIZER = PROJECT / "parameter_optimizer.py"
SOURCE_HISTORY = SOURCE / "optimizer_history_2026-07-29_213236.jsonl"
FORBIDDEN_FP = (
    "three-level-sha256:"
    "20472599c5b67c1cd1c93b692026e9e9b0de624c5d2fa6f76f2b18639aa0dfed"
)
FORBIDDEN_SIGNATURE = (
    "5b3321ad67e822a33c4f96c53d23a2c820df98248473cd2d793f785d0ae88912"
)
EXPECTED = {
    "optimizer": "960f999b552c23f1b84579c6f9fe4e895e06c47b66da0de91a2981b684d11c19",
    "source_config": "95140a42848bd16edfa76c74c972b33d36b2cde60c9119c11b88e9ab932b5144",
    "source_manifest": "d3ac3f1bf5729add0e7282d6da614920c70968f33d6f85e417c7015fe39fb0be",
    "source_guard": "50d7c25f2cb88091c9aab60a1da76a9c34b836c513163fe7f54c2d749a7ff117",
    "source_history": "af768a1401bccacd9ac8af783059b9ab074c2a4eece579d7638b4e61f3024cbc",
    "source_basin": "12757774eb3a7c9534c73f482eeeefb7640d6989ac760573b9b0e12efe511fe2",
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


def guard_source() -> str:
    return f"""#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent
FORBIDDEN_FP = {FORBIDDEN_FP!r}
NAMES = (
    "seq_page_cost", "random_page_cost", "cpu_tuple_cost",
    "cpu_index_tuple_cost", "cpu_operator_cost",
    "parallel_setup_cost", "parallel_tuple_cost",
)

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
            float(candidate_params[name]), params[name],
            rel_tol=1e-5, abs_tol=1e-9,
        ) for name in NAMES):
            matches.append(candidate)
    if len(matches) != 1:
        raise GuardFailure(
            f"current GUC-normalized candidate matched {{len(matches)}} ranking rows; "
            "exactly one is required"
        )
    matched_fp = (
        matches[0].get("three_level_fingerprint") or {{}}
    ).get("id")
    if not isinstance(matched_fp, str) or not matched_fp.startswith(
        "three-level-sha256:"
    ):
        raise GuardFailure("current candidate missing from dated ranking")
    return matched_fp

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
        env={{**__import__("os").environ, "PGPASSWORD": "postgres"}},
        text=True, capture_output=True, timeout=20, check=False,
    )
    if completed.returncode != 0:
        return fail("candidate parameter readback failed")
    params = {{}}
    for line in completed.stdout.splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            params[name] = float(value)
    if set(params) != set(NAMES):
        return fail("candidate parameter readback incomplete")
    if math.isclose(params["random_page_cost"], 100.0, rel_tol=0.0, abs_tol=1e-9):
        return fail("forbidden random_page_cost=100 candidate")
    try:
        history = resolve_history(OUTPUT)
        matched_fp = candidate_fingerprint(history, params)
    except (GuardFailure, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return fail(str(exc))
    if matched_fp == FORBIDDEN_FP:
        return fail("forbidden prior three-level fingerprint")
    return subprocess.run(sys.argv[1:], check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())
"""


def guard_test_source() -> str:
    return """#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

GUARD = Path(__file__).with_name("xp_candidate_exclusion_guard.py")
spec = importlib.util.spec_from_file_location("xp_guard_under_test", GUARD)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load guard")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

rank1 = {
    "seq_page_cost": 0.14278661564843484,
    "random_page_cost": 52.897786569735594,
    "cpu_tuple_cost": 0.0015070407649694704,
    "cpu_index_tuple_cost": 0.004823308099555999,
    "cpu_operator_cost": 0.00037676019124236755,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.7003457540181302,
}
rank1_readback = {
    "seq_page_cost": 0.142787,
    "random_page_cost": 52.8978,
    "cpu_tuple_cost": 0.00150704,
    "cpu_index_tuple_cost": 0.00482331,
    "cpu_operator_cost": 0.00037676,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.700346,
}
rank2 = {
    "seq_page_cost": 0.1590605573894812,
    "random_page_cost": 79.71912073724609,
    "cpu_tuple_cost": 0.0014024100004218954,
    "cpu_index_tuple_cost": 0.003886825151193881,
    "cpu_operator_cost": 0.0002784934649987005,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.5066260303541305,
}
fingerprint1 = "three-level-sha256:" + "1" * 64
fingerprint2 = "three-level-sha256:" + "2" * 64
with tempfile.TemporaryDirectory(prefix="xp_guard_zero_call_") as raw:
    directory = Path(raw)
    history = directory / "optimizer_history_2026-07-29_235959.jsonl"
    history.write_text(json.dumps({
        "event": "experience_table_all_subset_ranking",
        "candidate_rankings": [
            {
                "rank": 1,
                "optimized_params": rank1,
                "three_level_fingerprint": {"id": fingerprint1},
            },
            {
                "rank": 2,
                "optimized_params": rank2,
                "three_level_fingerprint": {"id": fingerprint2},
            },
        ],
    }) + "\\n", encoding="utf-8")
    (directory / "optimizer_history_2026-07-29_235959_db_monitor.csv").write_text(
        "timestamp,db_qps\\n0,0\\n", encoding="utf-8"
    )
    assert module.resolve_history(directory) == history
    assert module.candidate_fingerprint(history, rank1_readback) == fingerprint1
    missing = dict(rank1_readback)
    missing["random_page_cost"] = 53.0
    try:
        module.candidate_fingerprint(history, missing)
    except module.GuardFailure:
        pass
    else:
        raise AssertionError("missing candidate did not fail closed")
    ambiguous_event = {
        "event": "experience_table_all_subset_ranking",
        "candidate_rankings": [
            {
                "rank": 1,
                "optimized_params": rank1,
                "three_level_fingerprint": {"id": fingerprint1},
            },
            {
                "rank": 2,
                "optimized_params": dict(rank1),
                "three_level_fingerprint": {"id": fingerprint2},
            },
        ],
    }
    history.write_text(json.dumps(ambiguous_event) + "\\n", encoding="utf-8")
    try:
        module.candidate_fingerprint(history, rank1_readback)
    except module.GuardFailure:
        pass
    else:
        raise AssertionError("ambiguous candidate match did not fail closed")
    history.write_text(json.dumps({
        "event": "experience_table_all_subset_ranking",
        "candidate_rankings": [{
            "optimized_params": rank1,
            "three_level_fingerprint": {"id": fingerprint1},
        }],
    }) + "\\n", encoding="utf-8")
    second = directory / "optimizer_history_2026-07-29_235958.jsonl"
    second.write_text(history.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        module.resolve_history(directory)
    except module.GuardFailure:
        pass
    else:
        raise AssertionError("multiple dated histories did not fail closed")
print(json.dumps({
    "status": "passed",
    "physical_calls": 0,
    "workload_started": False,
    "dated_jsonl_selected": True,
    "db_monitor_csv_excluded": True,
    "guc_match_rel_tol": 1e-5,
    "guc_match_abs_tol": 1e-9,
    "rank1_truncated_readback_unique_match": True,
    "adjacent_rank2_not_mismatched": True,
    "ambiguous_matches_fail_closed": True,
    "missing_candidate_fails_closed": True,
    "multiple_histories_fail_closed": True,
}, sort_keys=True))
"""


def launcher(config_sha: str, guard_sha: str, manifest_sha: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail
OUTPUT={OUTPUT}
CONFIG="$OUTPUT/config.json"
MANIFEST="$OUTPUT/formal_manifest.json"
GUARD="$OUTPUT/xp_candidate_exclusion_guard.py"
OPTIMIZER={OPTIMIZER}
PYTHON=/home/anonymous/miniconda3/envs/resource_adaptive/bin/python
AFFINITY=32-51,288-307

test "$(sha256sum "$CONFIG" | awk '{{print $1}}')" = "{config_sha}"
test "$(sha256sum "$MANIFEST" | awk '{{print $1}}')" = "{manifest_sha}"
test "$(sha256sum "$GUARD" | awk '{{print $1}}')" = "{guard_sha}"
test "$(sha256sum "$OPTIMIZER" | awk '{{print $1}}')" = "{EXPECTED['optimizer']}"
test "$(cat "$OUTPUT/state/formal_search_accepted_counter.txt")" = "4"
test "$(cat "$OUTPUT/state/formal_search_replay_counter.txt")" = "11"
test "$(cat "$OUTPUT/state/global_replay_counter.txt")" = "11"
test "$(cat "$OUTPUT/state/formal_startup_baseline_counter.txt")" = "0"
test "$(jq -r '.batch_index' "$OUTPUT/state/plan_basin_escape_state.json")" = "4"
test "$(jq -r '.formal_outer_protocol.formal_search_maximum_replay_count' "$CONFIG")" = "16"
test "$(jq -r '.formal_outer_protocol.excluded_prior_three_level_fingerprints[]' "$CONFIG" | rg -x '{FORBIDDEN_FP}')"
! pgrep -af "$CONFIG"

EXPECTED_BASELINE=$'cpu_index_tuple_cost=0.005\\ncpu_operator_cost=0.0025\\ncpu_tuple_cost=0.01\\nparallel_setup_cost=1000\\nparallel_tuple_cost=0.1\\nrandom_page_cost=4\\nseq_page_cost=0.2'
ACTUAL_BASELINE="$(PGPASSWORD=postgres psql -h 127.0.0.1 -p 59630 -U postgres -d postgres -Atc "select name||'='||setting from pg_settings where name in ('seq_page_cost','random_page_cost','cpu_tuple_cost','cpu_index_tuple_cost','cpu_operator_cost','parallel_setup_cost','parallel_tuple_cost') order by name")"
test "$ACTUAL_BASELINE" = "$EXPECTED_BASELINE"

cd {PROJECT}
exec taskset -c "$AFFINITY" env OPTIMIZER_CONFIG="$CONFIG" "$PYTHON" -u "$OPTIMIZER"
"""


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"output already exists: {OUTPUT}")
    for path, key in (
        (OPTIMIZER, "optimizer"),
        (SOURCE / "config.json", "source_config"),
        (SOURCE / "formal_manifest.json", "source_manifest"),
        (SOURCE / "xp_candidate_exclusion_guard.py", "source_guard"),
        (SOURCE_HISTORY, "source_history"),
        (SOURCE / "state" / "plan_basin_escape_state.json", "source_basin"),
    ):
        require_sha(path, EXPECTED[key])

    expected_counters = {
        "formal_search_accepted_counter.txt": 4,
        "formal_search_replay_counter.txt": 11,
        "global_replay_counter.txt": 11,
        "formal_startup_baseline_counter.txt": 0,
    }
    for name, expected in expected_counters.items():
        actual = int((SOURCE / "state" / name).read_text().strip())
        if actual != expected:
            raise RuntimeError(f"source counter changed: {name}={actual}")

    source_config = read_json(SOURCE / "config.json")
    exclusions = source_config["formal_outer_protocol"][
        "excluded_prior_three_level_fingerprints"
    ]
    if FORBIDDEN_FP not in exclusions:
        raise RuntimeError("forbidden fingerprint is not excluded")
    events = [
        json.loads(line) for line in SOURCE_HISTORY.read_text().splitlines()
        if line.strip()
    ]
    source_continuation = source_config.get("_xp_formal_continuation") or {}
    if (
        source_continuation.get("forbidden_parameter")
        != {"random_page_cost": 100.0}
        or source_continuation.get("forbidden_parameter_signature_sha256")
        != FORBIDDEN_SIGNATURE
        or source_continuation.get("forbidden_three_level_fingerprint")
        != FORBIDDEN_FP
    ):
        raise RuntimeError("cont14 forbidden parameter/fingerprint contract changed")

    reservations = [
        event for event in events
        if event.get("event") == "formal_initial_final_physical_call_reserved"
    ]
    exceptions = [
        event for event in events
        if event.get("event") == "optimizer_process_exception"
    ]
    if (
        len(reservations) != 1
        or reservations[0].get("formal_search_replay_before") != 10
        or reservations[0].get("formal_search_replay_after") != 11
        or reservations[0].get("global_replay_before") != 10
        or reservations[0].get("global_replay_after") != 11
        or reservations[0].get("accepted_counter_updated") is not False
        or reservations[0].get("gradient_incumbent_or_range_updated") is not False
        or len(exceptions) != 1
        or exceptions[0].get("exception_type") != "RuntimeError"
    ):
        raise RuntimeError("cont14 technical-failure accounting evidence changed")
    source_guard_text = (SOURCE / "xp_candidate_exclusion_guard.py").read_text()
    if (
        "rel_tol=1e-9, abs_tol=1e-12" not in source_guard_text
        or not SOURCE_HISTORY.is_file()
    ):
        raise RuntimeError("cont14 strict-GUC-tolerance root cause changed")

    OUTPUT.mkdir(parents=True)
    state_dir = OUTPUT / "state"
    state_dir.mkdir()
    (OUTPUT / "subset_logs").mkdir()
    for name, value in expected_counters.items():
        atomic_text(state_dir / name, f"{value}\n")
    source_basin = read_json(SOURCE / "state" / "plan_basin_escape_state.json")
    if source_basin != {
        "accepted_shrinks": 4,
        "basin_exhausted": False,
        "batch_index": 4,
        "batch_name": "stratified_boundary",
        "status": "stratified_boundary",
    }:
        raise RuntimeError("cont14 plan-basin state changed")
    next_basin = copy.deepcopy(source_basin)
    atomic_text(
        state_dir / "plan_basin_escape_state.json",
        json.dumps(next_basin, indent=2, sort_keys=True) + "\n",
    )

    config = copy.deepcopy(source_config)
    preserved = {
        "initial_params": copy.deepcopy(config.get("initial_params")),
        "param_ranges": copy.deepcopy(config.get("param_ranges")),
    }
    formal = config["formal_outer_protocol"]
    formal.update({
        "formal_search_accepted_count_start": 4,
        "formal_search_replay_count_start": 11,
        "global_replay_count_start": 11,
        "global_replay_ordinal_at_binding": 11,
        "initial_real_replay_count": 11,
        "formal_search_attempt_safety_cap": 16,
        "formal_search_maximum_replay_count": 16,
        "maximum_cumulative_real_replay_count": 16,
        "formal_search_replay_cumulative_tiers": [16],
        "formal_search_accepted_counter_path": str(
            state_dir / "formal_search_accepted_counter.txt"
        ),
        "formal_search_replay_counter_path": str(
            state_dir / "formal_search_replay_counter.txt"
        ),
        "global_replay_counter_path": str(state_dir / "global_replay_counter.txt"),
        "formal_startup_baseline_counter_path": str(
            state_dir / "formal_startup_baseline_counter.txt"
        ),
    })
    guard_path = OUTPUT / "xp_candidate_exclusion_guard.py"
    atomic_text(guard_path, guard_source(), mode=0o700)
    original_workload = str(config["benchmark"]["workload_cmd"])
    old_guard_prefix = (
        "/home/anonymous/miniconda3/envs/py313/bin/python -u "
        f"{SOURCE / 'xp_candidate_exclusion_guard.py'} "
    )
    if not original_workload.startswith(old_guard_prefix):
        raise RuntimeError("cont14 guarded workload command changed")
    original_workload = original_workload[len(old_guard_prefix):]
    config["benchmark"]["workload_cmd"] = (
        f"/home/anonymous/miniconda3/envs/py313/bin/python -u {guard_path} "
        f"{original_workload}"
    )
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        OUTPUT / "subset_logs"
    )
    config["optimizer"]["gradient"]["plan_basin_escape"]["state_path"] = str(
        state_dir / "plan_basin_escape_state.json"
    )
    config["_xp_formal_continuation"].update({
        "contract": "xp_formal10_cont15_guc_tolerance_guard_v1",
        "source_continuation": str(SOURCE),
        "accepted_start": 4,
        "formal_replay_start": 11,
        "global_replay_start": 11,
        "batch_physical_cap": 5,
        "maximum_candidate_calls": 5,
        "parameter_optimizer_sha256": EXPECTED["optimizer"],
        "forbidden_parameter": {"random_page_cost": 100.0},
        "forbidden_parameter_signature_sha256": FORBIDDEN_SIGNATURE,
        "forbidden_three_level_fingerprint": FORBIDDEN_FP,
        "candidate_generation_state_transition": {
            "from": source_basin,
            "to": next_basin,
            "reason": "preserve_cont14_stratified_boundary_after_guard_technical_failure",
            "accepted_gradient_incumbent_range_updates": False,
            "physical_calls": 0,
        },
    })
    if preserved != {
        "initial_params": config.get("initial_params"),
        "param_ranges": config.get("param_ranges"),
    }:
        raise RuntimeError("formal incumbent/range changed")

    config_path = OUTPUT / "config.json"
    atomic_text(config_path, json.dumps(config, indent=2) + "\n")
    config_sha = sha256(config_path)
    guard_sha = sha256(guard_path)
    test_path = OUTPUT / "test_xp_guard_zero_call.py"
    atomic_text(test_path, guard_test_source(), mode=0o700)
    test_result = subprocess.run(
        [sys.executable, str(test_path)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if test_result.returncode != 0:
        raise RuntimeError(
            "zero-call guard test failed: "
            f"{test_result.stdout}\n{test_result.stderr}"
        )
    test_audit = json.loads(test_result.stdout)
    if (
        test_audit.get("status") != "passed"
        or test_audit.get("physical_calls") != 0
        or test_audit.get("workload_started") is not False
    ):
        raise RuntimeError("zero-call guard test did not prove no workload call")
    atomic_text(
        OUTPUT / "zero_call_guard_test_audit.json",
        json.dumps(test_audit, indent=2, sort_keys=True) + "\n",
    )
    manifest = {
        "schema_version": 1,
        "contract": "xp_formal10_cont15_guc_tolerance_guard_v1",
        "source_cont14": str(SOURCE),
        "source_sha256": EXPECTED,
        "accepted_split": {
            "inherited": 3,
            "prior_formal": 1,
            "this_batch": 0,
            "official_total": 4,
        },
        "counter_start": {
            "accepted": 4, "formal_replay": 11, "global_replay": 11, "startup": 0,
        },
        "counter_hard_stop": {"formal_replay": 16, "global_replay": 16},
        "per_batch_physical_cap": 5,
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "do_not_touch_cpu_184-199,440-455_or_mysql_state",
        "forbidden_candidate": {
            "params": {"random_page_cost": 100.0},
            "parameter_signature_sha256": FORBIDDEN_SIGNATURE,
            "three_level_fingerprint": FORBIDDEN_FP,
            "config_fingerprint_excluded": True,
            "pre_workload_guard_path": str(guard_path),
            "pre_workload_guard_sha256": guard_sha,
            "history_resolution": (
                "exactly_one_optimizer_history_*.jsonl_file; "
                "exclude_db_monitor_csv; current_candidate_must_uniquely_match_"
                "ranking_under_guc_rel1e-5_abs1e-9"
            ),
        },
        "zero_call_guard_test": {
            **test_audit,
            "path": str(test_path),
            "sha256": sha256(test_path),
            "audit_path": str(OUTPUT / "zero_call_guard_test_audit.json"),
            "audit_sha256": sha256(
                OUTPUT / "zero_call_guard_test_audit.json"
            ),
        },
        "plan_basin_transition": {"before": source_basin, "after": next_basin},
        "incumbent_and_range_unchanged": True,
        "diagnostic_state_pollution": False,
        "parameter_optimizer_path": str(OPTIMIZER),
        "parameter_optimizer_sha256": EXPECTED["optimizer"],
        "config_path": str(config_path),
        "config_sha256": config_sha,
        "prepared_only_no_workload_started": True,
    }
    manifest_path = OUTPUT / "formal_manifest.json"
    atomic_text(manifest_path, json.dumps(manifest, indent=2) + "\n")
    manifest_sha = sha256(manifest_path)
    launcher_path = OUTPUT / "launch_cont15_persistent_pty.sh"
    atomic_text(
        launcher_path,
        launcher(config_sha, guard_sha, manifest_sha),
        mode=0o700,
    )
    print(json.dumps({
        "output": str(OUTPUT),
        "config_sha256": config_sha,
        "manifest_sha256": manifest_sha,
        "guard_sha256": guard_sha,
        "launcher": str(launcher_path),
        "launcher_sha256": sha256(launcher_path),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
