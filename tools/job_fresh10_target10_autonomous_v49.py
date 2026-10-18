#!/usr/bin/env python3
"""Drive JOB from sealed formal004 to accepted=10 without manual candidates.

Each cycle performs deterministic EXPLAIN-only probe discovery, obtains two
clean independent diagnostic observations, evaluates the fixed clean5 gate,
and admits one formal Top-5 whose trusted decisions use a third observation
and the median of all three scores.  A restart reuses completed sealed phases
and never replays a partial workload directory.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


B = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
T = B / "job_final_algorithm_fresh10_v36_20260731T2020"
P = B / "job_round8_corrected_top5_v6_20260731"
ROOT = T / "autonomous_target10_v49"
STATE = T / "package/formal_state.json"
GLOBAL_COUNTER = B / "replay_counter.txt"
FORMAL_REPLAY_COUNTER = B / "formal_search_replay_counter.txt"
ACCEPTED_COUNTER = B / "formal_search_accepted_counter.txt"

INITIAL_FORMAL = T / "autonomous_v45/formal_batch004"
HISTORY = P / "authority/ranking_analysis.json"
CONFIG = B / "job_batch02_exact_regime_calibration_v1_20260731T0332/remote_config.json"
CAPTURE = B / "job_batch02_exact_regime_calibration_v1_20260731T0332/baseline_plan_capture.json"
CODE_ROOT = B / "gradient_debug_round7_fresh_exclusion_v1/code"
BASE_RUNNER = P / "run_job_round8_strict_serial.py"
CONTRACT = T / "package/job_round8_strict_replay_contract.py"

GENERATOR = Path("/tmp/job_exact_regime_successor_expand.py")
EXACT_REPEAT_BUILDER = Path("/tmp/build_job_exact_plan_repeat_validation.py")
GATE_TOOL = Path("/tmp/evaluate_job_clean5_gate.py")
ROBUST_SELECTION_BUILDER = Path(
    "/tmp/build_job_three_observation_formal_selection_v1.py"
)
ROBUST_RUNNER_V7 = Path("/tmp/run_job_round8_strict_serial_robust_v7.py")
ROBUST_RUNNER = Path(
    "/tmp/run_job_round8_strict_serial_robust_v8_apply_failure.py"
)
FORMAL_CONTROLLER = Path("/tmp/job_formal_target10_batch_controller.py")

EXPECTED_SHA = {
    GENERATOR: "a03c2264cdfe2b26a8bb4a5bcbc5648498ce68020806d6d4a84e3cb592b51da5",
    EXACT_REPEAT_BUILDER: "89c3a2238830087c129cadee76cd5a28248d87ce10b0fd98c6d5f9cb62bdd6cf",
    GATE_TOOL: "72700f5169ca7ba3bd1b527397ac179036ab8bebebb5fb7e977f5be4de2b558e",
    ROBUST_SELECTION_BUILDER: "40c528bb43ecd6b31987daa6d0dec2704fde93da11d510261d854d9eceb532a7",
    ROBUST_RUNNER_V7: "9251d7fa68276113656826bd6df1777c7b7dfe71b42d012674fbd19ca4b2f09c",
    ROBUST_RUNNER: "5d965cd9410fba78486856dc2d78bd594b9ebf1d17eda4a8f593a5f43d6fa857",
    FORMAL_CONTROLLER: "3c9246b67a7fbaf5e28115efc59e4ac68066fc96e6515b6524099ca804c69668",
    BASE_RUNNER: "9d033a7592307cca042b9b4c70057f8ed54394a576162000592d3605b46594c1",
    CONTRACT: "a5ae7fddcfcd16e1715e35cc5a67c3d3d135b2542ff0e132a4cca5817afddf05",
}

PARAMETER_FIELDS = (
    "accepted_count",
    "incumbent_score",
    "incumbent_params",
    "range_by_parameter",
)
REQUIRED_CANDIDATE_FIELDS = (
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
TARGET_ACCEPTED = 10
MAX_DIAGNOSTIC_ATTEMPTS = 5
MAX_CYCLES = 64


class FailClosed(RuntimeError):
    """A contract violation that must not trigger another workload call."""


class NoveltyCollision(FailClosed):
    """A zero-formal-call collision that permits fresh algorithmic discovery."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FailClosed(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise FailClosed(f"invalid counter {path}: {raw!r}")
    return int(raw)


def same_params(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if set(left) != set(right):
        return False
    return all(
        math.isclose(
            float(left[name]),
            float(right[name]),
            rel_tol=5e-6,
            abs_tol=max(5e-12, abs(float(right[name])) * 5e-12),
        )
        for name in left
    )


def require_hashes() -> None:
    for path, expected in EXPECTED_SHA.items():
        if not path.is_file() or sha256_file(path) != expected:
            raise FailClosed(f"sealed dependency SHA mismatch: {path}")


def state_pin() -> tuple[str, dict[str, Any]]:
    state = read_json(STATE)
    accepted = int(state.get("accepted_count", -1))
    if (
        int(state.get("accepted_target", -1)) != TARGET_ACCEPTED
        or accepted < 0
        or accepted > TARGET_ACCEPTED
        or counter(ACCEPTED_COUNTER) != accepted
    ):
        raise FailClosed("formal state/accepted counter mismatch")
    return sha256_file(STATE), state


def run_logged(
    command: Sequence[str],
    log_path: Path,
    *,
    timeout_seconds: int,
    allowed_returncodes: Iterable[int] = (0,),
    cwd: Path | None = None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        raise FailClosed(f"refusing to overwrite process log: {log_path}")
    environment = dict(os.environ)
    environment.pop("RA_PG_FORMAL_SEARCH_MAX_REPLAYS", None)
    environment.pop("RA_PG_FORMAL_SEARCH_ATTEMPT_SAFETY_CAP", None)
    completed = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd is not None else None,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    log_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode not in set(allowed_returncodes):
        raise FailClosed(
            f"command failed rc={completed.returncode}; log={log_path}"
        )
    return completed.returncode


def candidate_list(selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    top5 = selection.get("top5")
    if not isinstance(top5, list) or len(top5) != 5:
        raise FailClosed("selection is not Top-5")
    if not all(isinstance(item, dict) for item in top5):
        raise FailClosed("selection contains non-object candidates")
    return top5


def validate_top5(
    path: Path,
    *,
    roles: Sequence[str],
    diagnostic_only: bool,
) -> dict[str, Any]:
    selection = read_json(path)
    top5 = candidate_list(selection)
    observed_roles = [str(item.get("selection_role") or "") for item in top5]
    fingerprints = [
        str((item.get("three_level_fingerprint") or {}).get("id") or "")
        for item in top5
    ]
    if (
        observed_roles != list(roles)
        or selection.get("diagnostic_only") is not diagnostic_only
        or "" in fingerprints
        or len(set(fingerprints)) != 5
        or any(
            item.get("boundary_corner") is not False
            or (item.get("three_level_fingerprint") or {}).get("valid") is not True
            or any(field not in item for field in REQUIRED_CANDIDATE_FIELDS)
            for item in top5
        )
    ):
        raise FailClosed(f"Top-5 contract violation: {path}")
    return selection


def validate_diagnostic(
    selection_path: Path,
    summary_path: Path,
    runner_log_path: Path,
    state_sha_before: str,
    counters_before: Mapping[str, int],
) -> tuple[bool, dict[str, Any]]:
    summary = read_json(summary_path)
    physical = int(summary.get("physical_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    budget_attempts = valid + technical
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    invocations = summary.get("invocations") or []
    if (
        not all(isinstance(items, list) for items in (successful, failures, invocations))
        or not all(
            isinstance(item, Mapping)
            for items in (successful, failures, invocations)
            for item in items
        )
    ):
        raise FailClosed(f"diagnostic replay ledger type violation: {summary_path}")
    successful_ranks = [int(item.get("rank", -1)) for item in successful]
    failure_ranks = [int(item.get("rank", -1)) for item in failures]
    invocation_ranks = [int(item.get("rank", -1)) for item in invocations]
    attempt_ranks = successful_ranks + failure_ranks
    missing_ranks = sorted(set(failure_ranks) - set(invocation_ranks))
    if not runner_log_path.is_file():
        raise FailClosed(f"diagnostic runner log is missing: {runner_log_path}")
    runner_log = runner_log_path.read_text(encoding="utf-8", errors="replace")
    exact_apply_failure = (
        "ValueError: physical invocation ledger is inconsistent"
    )
    ledger_valid = bool(
        len(successful) == valid
        and len(failures) == technical
        and len(invocations) == physical
        and len(set(attempt_ranks)) == len(attempt_ranks)
        and len(set(invocation_ranks)) == len(invocation_ranks)
        and sorted(attempt_ranks) == list(range(1, budget_attempts + 1))
        and set(successful_ranks).issubset(set(invocation_ranks))
        and set(invocation_ranks).issubset(set(attempt_ranks))
        and len(missing_ranks) == budget_attempts - physical
        and all(
            item.get("technical_failure") is True
            and item.get("accepted") is False
            and item.get("gradient_update_applied") is False
            and item.get("incumbent_update_applied") is False
            and item.get("range_update_applied") is False
            for item in failures
            if int(item.get("rank", -1)) in missing_ranks
        )
        and (
            (not missing_ranks and "Traceback (most recent call last)" not in runner_log)
            or (
                bool(missing_ranks)
                and runner_log.rstrip().endswith(exact_apply_failure)
            )
        )
    )
    neutral_flags = {
        "accepted": False,
        "gradient": False,
        "incumbent": False,
        "range": False,
    }
    if (
        summary.get("diagnostic_only") is not True
        or summary.get("selection_sha256") != sha256_file(selection_path)
        or physical < 0
        or physical > 5
        or valid < 0
        or technical < 0
        or budget_attempts > 5
        or budget_attempts != 5
        or valid > physical
        or physical > budget_attempts
        or not ledger_valid
        or summary.get("baseline_params_restored") is not True
        or (summary.get("baseline_restore") or {}).get("success") is not True
        or summary.get("initial_state") != summary.get("final_state")
        or summary.get("formal_update_flags") != neutral_flags
        or sha256_file(STATE) != state_sha_before
        or counter(GLOBAL_COUNTER) - counters_before["global"] != physical
        or counter(FORMAL_REPLAY_COUNTER) != counters_before["formal_replay"]
        or counter(ACCEPTED_COUNTER) != counters_before["accepted"]
    ):
        raise FailClosed(f"diagnostic replay contract violation: {summary_path}")
    return physical == 5 and valid == 5 and technical == 0, summary


def workload_counters() -> dict[str, int]:
    return {
        "global": counter(GLOBAL_COUNTER),
        "formal_replay": counter(FORMAL_REPLAY_COUNTER),
        "accepted": counter(ACCEPTED_COUNTER),
    }


def run_diagnostic(
    selection: Path,
    replay: Path,
    phase_root: Path,
) -> tuple[bool, dict[str, Any]]:
    state_sha_before, _ = state_pin()
    audit_path = phase_root / "diagnostic_audit.json"
    summary_path = replay / "summary.json"
    runner_log_path = phase_root / "runner.log"
    if audit_path.exists():
        audit = read_json(audit_path)
        summary = read_json(summary_path)
        status = audit.get("status")
        if (
            status not in ("clean5_state_neutral", "technical_state_neutral")
            or audit.get("selection_sha256") != sha256_file(selection)
            or audit.get("summary_sha256") != sha256_file(summary_path)
            or audit.get("formal_state_sha256") != state_sha_before
            or int(audit.get("accepted_counter", -1)) != counter(ACCEPTED_COUNTER)
        ):
            raise FailClosed(f"sealed diagnostic audit mismatch: {audit_path}")
        return status == "clean5_state_neutral", summary
    counters_before_path = phase_root / "counters_before.json"
    if counters_before_path.exists():
        counters_before = read_json(counters_before_path)
    else:
        counters_before = workload_counters()
        atomic_json(counters_before_path, counters_before)
    if not summary_path.exists():
        if replay.exists():
            raise FailClosed(
                f"partial diagnostic replay cannot be repeated: {replay}"
            )
        run_logged(
            [
                sys.executable,
                str(BASE_RUNNER),
                str(selection),
                str(replay),
                "--authority-root",
                str(P / "authority"),
                "--observations",
                str(P / "job_round8_v6_observations.json"),
                "--exclusion",
                str(P / "round7_replayed_fingerprints.json"),
                "--state",
                str(STATE),
                "--accepted-target",
                str(TARGET_ACCEPTED),
                "--diagnostic-only",
            ],
            runner_log_path,
            timeout_seconds=14400,
            allowed_returncodes=(0, 1),
        )
    clean5, summary = validate_diagnostic(
        selection,
        summary_path,
        runner_log_path,
        state_sha_before,
        {name: int(value) for name, value in counters_before.items()},
    )
    atomic_json(
        audit_path,
        {
            "status": "clean5_state_neutral" if clean5 else "technical_state_neutral",
            "selection_sha256": sha256_file(selection),
            "summary_sha256": sha256_file(summary_path),
            "physical_calls": int(summary["physical_invocation_count"]),
            "batch_budget_attempts": (
                int(summary["successful_real_replay_count"])
                + int(summary["technical_failure_count"])
            ),
            "valid_replays": int(summary["successful_real_replay_count"]),
            "technical_failures": int(summary["technical_failure_count"]),
            "pre_invocation_technical_failure_count": (
                int(summary["successful_real_replay_count"])
                + int(summary["technical_failure_count"])
                - int(summary["physical_invocation_count"])
            ),
            "accepted_added": 0,
            "accepted_counter": counter(ACCEPTED_COUNTER),
            "formal_state_sha256": state_sha_before,
        },
    )
    return clean5, summary


def iter_json_objects(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    yield value
        return
    yield read_json(path)


def extract_fingerprints(value: Any, result: set[str]) -> None:
    if isinstance(value, dict):
        fingerprint = value.get("three_level_fingerprint")
        if isinstance(fingerprint, dict):
            identifier = fingerprint.get("id")
            if isinstance(identifier, str) and identifier:
                result.add(identifier)
        for child in value.values():
            extract_fingerprints(child, result)
    elif isinstance(value, list):
        for child in value:
            extract_fingerprints(child, result)
    elif isinstance(value, str) and value.startswith("three-level-sha256:"):
        result.add(value)


def exclusion_inventory(output: Path) -> set[str]:
    if output.exists():
        inventory = read_json(output)
        identifiers = {
            str((item.get("three_level_fingerprint") or {}).get("id") or "")
            for item in inventory.get("fingerprints") or []
            if isinstance(item, dict)
        }
        identifiers.discard("")
        if (
            inventory.get("schema_version")
            != "job_global_three_level_exclusion_v1"
            or int(inventory.get("fingerprint_count", -1)) != len(identifiers)
        ):
            raise FailClosed("sealed exclusion inventory is invalid")
        return identifiers
    patterns = ("top5*.json", "*selection*.json", "successor*.json")
    paths: set[Path] = {P / "round7_replayed_fingerprints.json"}
    # The fresh campaign inherited exclusions from earlier JOB diagnostics
    # under the same JOB-only attempt root, not only from T.  Scan that bounded
    # root so a restart cannot rediscover an old three-level identity.
    for pattern in patterns:
        paths.update(B.rglob(pattern))
    paths.discard(output)
    fingerprints: set[str] = set()
    sources: list[dict[str, str]] = []
    for path in sorted(paths, key=str):
        if not path.is_file():
            continue
        try:
            value = read_json(path)
        except (json.JSONDecodeError, FailClosed):
            raise FailClosed(f"cannot audit exclusion JSON: {path}")
        before = len(fingerprints)
        extract_fingerprints(value, fingerprints)
        if len(fingerprints) != before:
            sources.append({"path": str(path), "sha256": sha256_file(path)})
    payload = {
        "schema_version": "job_global_three_level_exclusion_v1",
        "sources": sources,
        "fingerprint_count": len(fingerprints),
        "fingerprints": [
            {"three_level_fingerprint": {"id": identifier}}
            for identifier in sorted(fingerprints)
        ],
    }
    atomic_json(output, payload)
    return fingerprints


def event_files() -> list[Path]:
    return sorted(
        (
            path
            for path in T.rglob("events.jsonl")
            if path.is_file() and "/quarantine_" not in str(path)
        ),
        key=str,
    )


def incumbent_evidence(
    state: Mapping[str, Any],
) -> tuple[Path, dict[str, Any], Path]:
    incumbent_params = state.get("incumbent_params")
    incumbent_score = float(state.get("incumbent_score"))
    if not isinstance(incumbent_params, Mapping):
        raise FailClosed("formal state has no incumbent params")
    matches: list[tuple[Path, dict[str, Any], Path]] = []
    for path in event_files():
        for event in iter_json_objects(path):
            if (
                event.get("event") != "job_round8_candidate_replay_complete"
                or event.get("accepted") is not True
                or event.get("technical_failure") is not False
            ):
                continue
            binding = event.get("candidate_binding") or {}
            params = binding.get("candidate_params")
            score = event.get("actual_score")
            if (
                not isinstance(params, Mapping)
                or score is None
                or not same_params(params, incumbent_params)
                or not math.isclose(
                    float(score), incumbent_score, rel_tol=1e-12, abs_tol=1e-12
                )
            ):
                continue
            protocol = event.get("measurement_protocol") or {}
            point_result = Path(str(protocol.get("point_result_path") or ""))
            plan = (binding.get("three_level_fingerprint") or {}).get(
                "plan_level"
            )
            if (
                not point_result.is_file()
                or not isinstance(plan, str)
                or not plan.startswith("ratio-plan-sha256:")
            ):
                raise FailClosed("matching incumbent evidence is incomplete")
            matches.append((path, event, point_result))
    if not matches:
        raise FailClosed("no accepted event matches the authoritative incumbent")
    return matches[-1]


def clean_seed_from_formal(selection_path: Path) -> tuple[Path, Path, Path]:
    selection = read_json(selection_path)
    trusted = [
        item
        for item in candidate_list(selection)
        if item.get("selection_role") == "trusted"
    ]
    if len(trusted) != 3:
        raise FailClosed("formal seed is not trusted3+probe2")
    source_pairs: set[tuple[str, str]] = set()
    for candidate in trusted:
        policy = candidate.get("robust_acceptance_policy") or {}
        observations = policy.get("prior_observations") or []
        if (
            policy.get("contract")
            != "job_three_independent_exact_point_median_acceptance_v2"
            or len(observations) != 2
        ):
            raise FailClosed("formal seed lacks robust prior observations")
        last = observations[-1]
        source_pairs.add(
            (str(last.get("selection_path") or ""), str(last.get("summary_path") or ""))
        )
    if len(source_pairs) != 1:
        raise FailClosed("formal seed candidates do not share one clean source")
    selection_raw, summary_raw = next(iter(source_pairs))
    diagnostic_selection = Path(selection_raw)
    diagnostic_summary = Path(summary_raw)
    diagnostic_events = diagnostic_summary.with_name("events.jsonl")
    if not all(
        path.is_file()
        for path in (diagnostic_selection, diagnostic_summary, diagnostic_events)
    ):
        raise FailClosed("formal seed diagnostic evidence is missing")
    summary = read_json(diagnostic_summary)
    if (
        summary.get("diagnostic_only") is not True
        or summary.get("successful_real_replay_count") != 5
        or summary.get("technical_failure_count") != 0
        or summary.get("selection_sha256") != sha256_file(diagnostic_selection)
    ):
        raise FailClosed("formal seed diagnostic evidence is not clean5")
    return diagnostic_selection, diagnostic_summary, diagnostic_events


def formal_artifacts(root: Path) -> tuple[Path, Path, Path]:
    return (
        root / "package/top5_promoted_formal.json",
        root / "replay/summary.json",
        root / "replay/formal_state_commit_transaction.json",
    )


def validate_committed_formal(
    root: Path,
    *,
    require_current_state: bool = True,
) -> dict[str, Any]:
    selection_path, summary_path, transaction_path = formal_artifacts(root)
    if not all(path.is_file() for path in (selection_path, summary_path, transaction_path)):
        raise FailClosed(f"formal batch is incomplete: {root}")
    summary = read_json(summary_path)
    transaction = read_json(transaction_path)
    physical = int(summary.get("physical_invocation_count", -1))
    successful = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    budget_attempts = successful + technical
    declared_attempts = int(
        summary.get("batch_budget_attempt_count", budget_attempts)
    )
    pre_invocation_failures = int(
        summary.get(
            "pre_invocation_technical_failure_count",
            budget_attempts - physical,
        )
    )
    pre_invocation_ranks = summary.get(
        "pre_invocation_technical_failure_ranks",
        [] if pre_invocation_failures == 0 else None,
    )
    if (
        transaction.get("status") != "committed"
        or summary.get("baseline_params_restored") is not True
        or summary.get("selection_sha256") != sha256_file(selection_path)
        or physical < 0
        or physical > 5
        or successful < 0
        or technical < 0
        or budget_attempts > 5
        or declared_attempts != budget_attempts
        or pre_invocation_failures != budget_attempts - physical
        or not isinstance(pre_invocation_ranks, list)
        or len(pre_invocation_ranks) != pre_invocation_failures
        or len(set(pre_invocation_ranks)) != len(pre_invocation_ranks)
        or successful > physical
        or physical > budget_attempts
        or summary.get("three_observation_acceptance_contract")
        != "job_three_independent_exact_point_median_acceptance_v2"
    ):
        raise FailClosed(f"formal batch commit contract violation: {root}")
    if require_current_state:
        current_state_sha, state = state_pin()
        if (
            int(transaction.get("accepted_after", -1))
            != int(state["accepted_count"])
            or transaction.get("state_sha256_after") != current_state_sha
            or int(transaction.get("global_ordinal_after", -1))
            != int(state.get("global_ordinal", -2))
            or int(transaction.get("formal_replay_count_after", -1))
            != int(state.get("formal_replay_count", -2))
            or int(state.get("formal_replay_count", -1))
            != counter(FORMAL_REPLAY_COUNTER)
        ):
            raise FailClosed("formal transaction/state/counter mismatch")
    return summary


def validate_pending_diagnostic_counters(
    latest_formal: Path,
    first_cycle: int,
) -> None:
    """Account for state-neutral diagnostic ordinals after the latest commit."""
    _state_sha, state = state_pin()
    running_global = int(state.get("global_ordinal", -1))
    formal_replay = int(state.get("formal_replay_count", -1))
    accepted = int(state["accepted_count"])
    if running_global < 0 or formal_replay < 0:
        raise FailClosed("formal state lacks committed replay counters")
    latest_cycle = (
        4
        if latest_formal == INITIAL_FORMAL
        else int(latest_formal.parent.name.rsplit("_", 1)[-1])
    )
    pending_roots = [
        ROOT / f"cycle_{ordinal:03d}"
        for ordinal in range(latest_cycle + 1, first_cycle + 1)
        if (ROOT / f"cycle_{ordinal:03d}").is_dir()
    ]
    counter_files = sorted(
        (
            path
            for root in pending_roots
            for path in root.rglob("counters_before.json")
        ),
        key=str,
    )
    for before_path in counter_files:
        phase = before_path.parent
        before = read_json(before_path)
        if (
            int(before.get("global", -1)) != running_global
            or int(before.get("formal_replay", -1)) != formal_replay
            or int(before.get("accepted", -1)) != accepted
        ):
            raise FailClosed(f"diagnostic counter chain mismatch: {phase}")
        audit_path = phase / "diagnostic_audit.json"
        summary_path = phase / "replay/summary.json"
        if audit_path.exists():
            audit = read_json(audit_path)
            physical = int(audit.get("physical_calls", -1))
            if audit.get("summary_sha256") != sha256_file(summary_path):
                raise FailClosed(f"diagnostic summary SHA mismatch: {phase}")
        elif summary_path.exists():
            # The controller may have stopped after the atomic runner summary
            # but before sealing its audit.  Count the already-spent ordinals;
            # run_diagnostic will perform the full state-neutral validation.
            summary = read_json(summary_path)
            physical = int(summary.get("physical_invocation_count", -1))
        else:
            raise FailClosed(
                f"partial diagnostic has no sealed summary; refusing replay: {phase}"
            )
        if physical < 0 or physical > 5:
            raise FailClosed(f"diagnostic physical count is invalid: {phase}")
        running_global += physical
    if (
        running_global != counter(GLOBAL_COUNTER)
        or formal_replay != counter(FORMAL_REPLAY_COUNTER)
        or accepted != counter(ACCEPTED_COUNTER)
    ):
        raise FailClosed("pending diagnostic/global counter accounting mismatch")


def ensure_generated_discovery(
    cycle_root: Path,
    evidence_selection: Path,
    evidence_events: Path,
    incumbent_events: Path,
    point_result: Path,
    state_sha_before: str,
) -> Path:
    output = cycle_root / "discovery_top5.json"
    second = cycle_root / "discovery_top5.second.json"
    inventory = cycle_root / "global_exclusion.json"
    excluded_before = exclusion_inventory(inventory)
    command = [
        sys.executable,
        str(GENERATOR),
        "--history",
        str(HISTORY),
        "--evidence-top5",
        str(evidence_selection),
        "--events",
        str(evidence_events),
        "--formal-state",
        str(STATE),
        "--incumbent-events",
        str(incumbent_events),
        "--config",
        str(CONFIG),
        "--capture",
        str(CAPTURE),
        "--incumbent-point-result",
        str(point_result),
        "--code-root",
        str(CODE_ROOT),
        "--exclude",
        str(inventory),
        "--mode",
        "diagnostic",
        "--force-probe-discovery",
    ]
    for target, log_name in ((output, "generator_a.log"), (second, "generator_b.log")):
        if not target.exists():
            run_logged(
                [*command, "--output", str(target)],
                cycle_root / log_name,
                timeout_seconds=7200,
            )
    if output.read_bytes() != second.read_bytes():
        raise FailClosed("successor discovery is not deterministic")
    selection = validate_top5(
        output,
        roles=("probe",) * 5,
        diagnostic_only=True,
    )
    identifiers = {
        item["three_level_fingerprint"]["id"]
        for item in candidate_list(selection)
    }
    if identifiers & excluded_before or len(identifiers) != 5:
        raise FailClosed("successor discovery fingerprints are not globally novel")
    if sha256_file(STATE) != state_sha_before:
        raise FailClosed("EXPLAIN discovery mutated formal state")
    atomic_json(
        cycle_root / "discovery_audit.json",
        {
            "status": "five_globally_novel_noncorner_probe_fingerprints",
            "selection_sha256": sha256_file(output),
            "fingerprints": sorted(identifiers),
            "excluded_fingerprint_count_before": len(excluded_before),
            "new_workload_calls": 0,
            "formal_state_sha256": state_sha_before,
        },
    )
    return output


def exact_parameter_set(selection_path: Path) -> set[str]:
    return {
        json.dumps(item["candidate_params"], sort_keys=True, separators=(",", ":"))
        for item in candidate_list(read_json(selection_path))
    }


def fingerprint_set(selection_path: Path) -> set[str]:
    return {
        str(item["three_level_fingerprint"]["id"])
        for item in candidate_list(read_json(selection_path))
    }


def build_repeat(source_root: Path, target_root: Path) -> Path:
    output = target_root / "top5.json"
    second = target_root / "top5.second.json"
    source_selection = source_root / "top5.json"
    source_summary = source_root / "replay/summary.json"
    command = [
        sys.executable,
        str(EXACT_REPEAT_BUILDER),
        "--selection",
        str(source_selection),
        "--summary",
        str(source_summary),
    ]
    target_root.mkdir(parents=True, exist_ok=True)
    for target, log_name in ((output, "builder_a.log"), (second, "builder_b.log")):
        if not target.exists():
            run_logged(
                [*command, "--output", str(target)],
                target_root / log_name,
                timeout_seconds=600,
            )
    if output.read_bytes() != second.read_bytes():
        raise FailClosed("exact-repeat builder is not deterministic")
    validate_top5(
        output,
        roles=("trusted", "trusted", "trusted", "probe", "probe"),
        diagnostic_only=True,
    )
    return output


def evaluate_gate(target_root: Path) -> tuple[bool, Path]:
    output = target_root / "entry_gate_raw.json"
    if not output.exists():
        run_logged(
            [
                sys.executable,
                str(GATE_TOOL),
                "--selection",
                str(target_root / "top5.json"),
                "--summary",
                str(target_root / "replay/summary.json"),
                "--output",
                str(output),
            ],
            target_root / "gate.log",
            timeout_seconds=600,
            allowed_returncodes=(0, 2),
        )
    gate = read_json(output)
    checks = gate.get("checks")
    passed = bool(
        gate.get("passed") is True
        and isinstance(checks, dict)
        and checks
        and all(value is True for value in checks.values())
    )
    return passed, output


def obtain_clean_discovery(discovery: Path, cycle_root: Path) -> Path:
    for attempt in range(1, MAX_DIAGNOSTIC_ATTEMPTS + 1):
        root = cycle_root / f"discovery_replay_{attempt:02d}"
        selection = root / "top5.json"
        root.mkdir(parents=True, exist_ok=True)
        if not selection.exists():
            shutil.copy2(discovery, selection)
        elif selection.read_bytes() != discovery.read_bytes():
            raise FailClosed("sealed discovery replay selection changed")
        clean5, _summary = run_diagnostic(selection, root / "replay", root)
        if clean5:
            return root
    raise FailClosed("five discovery diagnostic batches exhausted without clean5")


def obtain_passed_repeat(
    discovery_root: Path,
    cycle_root: Path,
) -> tuple[Path, Path, Path]:
    source = discovery_root
    for attempt in range(1, MAX_DIAGNOSTIC_ATTEMPTS + 1):
        target = cycle_root / f"repeat_{attempt:02d}"
        selection = build_repeat(source, target)
        if exact_parameter_set(selection) != exact_parameter_set(
            discovery_root / "top5.json"
        ):
            raise FailClosed("repeat candidates differ from novel discovery points")
        clean5, _summary = run_diagnostic(selection, target / "replay", target)
        if not clean5:
            continue
        passed, gate = evaluate_gate(target)
        if passed:
            return source, target, gate
        source = target
    raise FailClosed("five clean5 gate attempts exhausted without admission")


def seal_entry_gate(
    raw_gate: Path,
    output: Path,
    discovery: Path,
    state_sha_before: str,
) -> dict[str, Any]:
    gate = read_json(raw_gate)
    checks = gate.get("checks")
    if (
        gate.get("passed") is not True
        or not isinstance(checks, dict)
        or not checks
        or not all(value is True for value in checks.values())
    ):
        raise FailClosed("cannot seal a failed entry gate")
    state = read_json(STATE)
    pins = workload_counters()
    if pins["accepted"] != int(state["accepted_count"]):
        raise FailClosed("accepted state/counter mismatch while sealing gate")
    discovery_audit = read_json(discovery.parent / "discovery_audit.json")
    gate.update(
        {
            "formal_state_sha256_before": state_sha_before,
            "formal_state_sha256_after": state_sha_before,
            "formal_counter_pins_before_start": {
                "global_ordinal": pins["global"],
                "formal_search_replay_count": pins["formal_replay"],
                "formal_search_accepted_count": pins["accepted"],
            },
            "formal_accepted_total": pins["accepted"],
            "accepted_target": TARGET_ACCEPTED,
            "formal_admission_contract": (
                "three_independent_exact_point_median_acceptance_v2"
            ),
            "formal_expected_improvement_required": False,
            "novel_discovery_selection_sha256": sha256_file(discovery),
            "novel_discovery_fingerprints": discovery_audit["fingerprints"],
            "formal_candidates_same_parameter_set_as_novel_discovery": True,
            "qps_used_for_inner_scoring": False,
        }
    )
    if output.exists():
        if read_json(output) != gate:
            raise FailClosed("sealed entry gate changed on restart")
    else:
        atomic_json(output, gate)
    return gate


def prepare_formal(
    cycle_root: Path,
    discovery: Path,
    first_source: Path,
    passed_source: Path,
    raw_gate: Path,
    state_sha_before: str,
) -> Path:
    formal = cycle_root / "formal"
    package = formal / "package"
    package.mkdir(parents=True, exist_ok=True)
    copies = {
        FORMAL_CONTROLLER: package / "job_formal_target10_batch_controller.py",
        BASE_RUNNER: package / "run_job_round8_strict_serial_base_v6.py",
        ROBUST_RUNNER_V7: package / "run_job_round8_strict_serial_robust_v7.py",
        ROBUST_RUNNER: package / "run_job_round8_strict_serial.py",
        ROBUST_SELECTION_BUILDER: package / "build_job_three_observation_formal_selection_v1.py",
        CONTRACT: package / "job_round8_strict_replay_contract.py",
    }
    for source, target in copies.items():
        if not target.exists():
            shutil.copy2(source, target)
        if sha256_file(target) != sha256_file(source):
            raise FailClosed(f"formal package copy mismatch: {target}")
    gate_path = package / "entry_gate.json"
    seal_entry_gate(raw_gate, gate_path, discovery, state_sha_before)
    selection = package / "top5_promoted_formal.json"
    if not selection.exists():
        run_logged(
            [
                sys.executable,
                str(package / "build_job_three_observation_formal_selection_v1.py"),
                "--first-selection",
                str(first_source / "top5.json"),
                "--first-summary",
                str(first_source / "replay/summary.json"),
                "--second-selection",
                str(passed_source / "top5.json"),
                "--second-summary",
                str(passed_source / "replay/summary.json"),
                "--gate",
                str(gate_path),
                "--output",
                str(selection),
            ],
            package / "selection_builder.log",
            timeout_seconds=600,
        )
    validate_top5(
        selection,
        roles=("trusted", "trusted", "trusted", "probe", "probe"),
        diagnostic_only=False,
    )
    if exact_parameter_set(selection) != exact_parameter_set(discovery):
        raise FailClosed("formal candidates differ from novel discovery points")
    historical_ids = exclusion_inventory(cycle_root / "global_exclusion.json")
    collisions = fingerprint_set(selection) & historical_ids
    if collisions:
        raise NoveltyCollision(
            "robust formal fingerprint collided with historical evidence: "
            + ",".join(sorted(collisions))
        )
    dry_run = package / "dry_run.json"
    if not dry_run.exists():
        run_logged(
            [
                sys.executable,
                str(package / "run_job_round8_strict_serial.py"),
                str(selection),
                str(formal / "replay"),
                "--authority-root",
                str(P / "authority"),
                "--observations",
                str(P / "job_round8_v6_observations.json"),
                "--exclusion",
                str(P / "round7_replayed_fingerprints.json"),
                "--state",
                str(STATE),
                "--accepted-target",
                str(TARGET_ACCEPTED),
                "--dry-run",
            ],
            dry_run,
            timeout_seconds=600,
            cwd=package,
        )
    dry_run_result = read_json(dry_run)
    if (
        dry_run_result.get("remote_actions_executed") is not False
        or int(dry_run_result.get("candidate_count", -1)) != 5
        or int(dry_run_result.get("physical_invocation_cap", -1)) != 5
    ):
        raise FailClosed("formal dry-run contract validation failed")
    if sha256_file(STATE) != state_sha_before:
        raise FailClosed("formal dry-run mutated authoritative state")
    return formal


def execute_formal(formal: Path, state_sha_before: str) -> None:
    package = formal / "package"
    selection = package / "top5_promoted_formal.json"
    gate = package / "entry_gate.json"
    replay = formal / "replay"
    transaction = replay / "formal_state_commit_transaction.json"
    if transaction.exists():
        validate_committed_formal(formal)
        return
    if replay.exists():
        raise FailClosed(f"partial formal replay cannot be repeated: {replay}")
    run_logged(
        [
            sys.executable,
            str(package / "job_formal_target10_batch_controller.py"),
            "--selection",
            str(selection),
            "--selection-sha256",
            sha256_file(selection),
            "--gate",
            str(gate),
            "--gate-sha256",
            sha256_file(gate),
            "--state",
            str(STATE),
            "--state-sha256",
            state_sha_before,
            "--authority-root",
            str(P / "authority"),
            "--observations",
            str(P / "job_round8_v6_observations.json"),
            "--exclusion",
            str(P / "round7_replayed_fingerprints.json"),
            "--replay-root",
            str(replay),
            "--global-counter",
            str(GLOBAL_COUNTER),
            "--formal-replay-counter",
            str(FORMAL_REPLAY_COUNTER),
            "--accepted-counter",
            str(ACCEPTED_COUNTER),
        ],
        formal / "controller.log",
        timeout_seconds=14400,
        cwd=package,
    )
    validate_committed_formal(formal)


def write_controller_state(**values: Any) -> None:
    current = {
        "schema_version": "job_fresh10_target10_autonomous_v49",
        "target_accepted": TARGET_ACCEPTED,
        "qps_used_for_inner_scoring": False,
        "manual_candidate_injection": False,
        **values,
    }
    atomic_json(ROOT / "controller_state.json", current)


def completed_cycle_audit(cycle_root: Path) -> dict[str, Any]:
    formal = cycle_root / "formal"
    validate_committed_formal(formal, require_current_state=True)
    selection_path, summary_path, transaction_path = formal_artifacts(formal)
    transaction = read_json(transaction_path)
    gate = read_json(formal / "package/entry_gate.json")
    discovery = cycle_root / "discovery_top5.json"
    audit = {
        "status": "complete",
        "cycle": int(cycle_root.name.rsplit("_", 1)[-1]),
        "accepted_before": int(transaction["accepted_before"]),
        "accepted_after": int(transaction["accepted_after"]),
        "accepted_delta": (
            int(transaction["accepted_after"])
            - int(transaction["accepted_before"])
        ),
        "state_sha256_before": str(gate["formal_state_sha256_before"]),
        "state_sha256_after": str(transaction["state_sha256_after"]),
        "novel_discovery_selection_sha256": sha256_file(discovery),
        "formal_selection_sha256": sha256_file(selection_path),
        "formal_summary_sha256": sha256_file(summary_path),
        "manual_candidate_injection": False,
        "qps_used_for_inner_scoring": False,
    }
    return audit


def resume_position() -> tuple[Path, int]:
    """Return the latest committed formal root and first unfinished cycle."""
    validate_committed_formal(INITIAL_FORMAL, require_current_state=False)
    latest = INITIAL_FORMAL
    cycle = 5
    while True:
        cycle_root = ROOT / f"cycle_{cycle:03d}"
        if not cycle_root.exists():
            break
        audit_path = cycle_root / "cycle_audit.json"
        transaction_path = cycle_root / "formal/replay/formal_state_commit_transaction.json"
        if audit_path.exists():
            audit = read_json(audit_path)
            if audit.get("status") == (
                "abandoned_zero_formal_calls_fingerprint_collision"
            ):
                if (
                    int(audit.get("cycle", -1)) != cycle
                    or int(audit.get("formal_physical_calls", -1)) != 0
                    or transaction_path.exists()
                    or (cycle_root / "formal/replay/summary.json").exists()
                ):
                    raise FailClosed(
                        f"abandoned zero-call cycle audit mismatch: {cycle_root}"
                    )
                cycle += 1
                continue
            validate_committed_formal(
                cycle_root / "formal",
                require_current_state=False,
            )
            transaction = read_json(transaction_path)
            if (
                audit.get("status") != "complete"
                or int(audit.get("cycle", -1)) != cycle
                or int(audit.get("accepted_before", -1))
                != int(transaction.get("accepted_before", -2))
                or int(audit.get("accepted_after", -1))
                != int(transaction.get("accepted_after", -2))
            ):
                raise FailClosed(f"completed cycle audit mismatch: {cycle_root}")
            latest = cycle_root / "formal"
            cycle += 1
            continue
        if transaction_path.exists():
            transaction = read_json(transaction_path)
            if transaction.get("status") != "committed":
                raise FailClosed(f"uncommitted formal transaction: {transaction_path}")
            audit = completed_cycle_audit(cycle_root)
            atomic_json(audit_path, audit)
            latest = cycle_root / "formal"
            cycle += 1
            continue
        break
    validate_committed_formal(latest, require_current_state=True)
    later_roots = sorted(
        path
        for path in ROOT.glob("cycle_[0-9][0-9][0-9]")
        if path.is_dir() and path.name > f"cycle_{cycle:03d}"
    )
    if later_roots:
        raise FailClosed("cycle directory gap detected")
    validate_pending_diagnostic_counters(latest, cycle)
    return latest, cycle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-cycles", type=int, default=MAX_CYCLES)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.max_cycles < 1 or args.max_cycles > MAX_CYCLES:
        raise FailClosed(f"max-cycles must be in 1..{MAX_CYCLES}")
    require_hashes()
    ROOT.mkdir(parents=True, exist_ok=True)
    lock_handle = (ROOT / "controller.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise FailClosed("another JOB target10 controller holds the lock") from exc

    latest_formal, first_cycle = resume_position()
    if args.preflight_only:
        state_sha, current = state_pin()
        print(
            json.dumps(
                {
                    "status": "passed",
                    "accepted_count": int(current["accepted_count"]),
                    "accepted_target": TARGET_ACCEPTED,
                    "first_cycle": first_cycle,
                    "latest_formal": str(latest_formal),
                    "formal_state_sha256": state_sha,
                    "new_workload_calls": 0,
                    "explain_calls": 0,
                    "manual_candidate_injection": False,
                    "qps_used_for_inner_scoring": False,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    for cycle in range(first_cycle, first_cycle + args.max_cycles):
        state_sha_before, state = state_pin()
        accepted_before = int(state["accepted_count"])
        if accepted_before == TARGET_ACCEPTED:
            write_controller_state(
                status="complete",
                accepted_count=accepted_before,
                latest_formal=str(latest_formal),
                formal_state_sha256=state_sha_before,
            )
            return 0

        cycle_root = ROOT / f"cycle_{cycle:03d}"
        cycle_root.mkdir(parents=True, exist_ok=True)
        write_controller_state(
            status="running",
            cycle=cycle,
            phase="successor_discovery",
            accepted_count=accepted_before,
            latest_formal=str(latest_formal),
            formal_state_sha256=state_sha_before,
        )
        formal_selection = formal_artifacts(latest_formal)[0]
        evidence_selection, _evidence_summary, evidence_events = (
            clean_seed_from_formal(formal_selection)
        )
        incumbent_events, _incumbent_event, point_result = incumbent_evidence(state)
        discovery = ensure_generated_discovery(
            cycle_root,
            evidence_selection,
            evidence_events,
            incumbent_events,
            point_result,
            state_sha_before,
        )
        write_controller_state(
            status="running",
            cycle=cycle,
            phase="diagnostic_discovery",
            accepted_count=accepted_before,
            formal_state_sha256=state_sha_before,
        )
        discovery_root = obtain_clean_discovery(discovery, cycle_root)
        write_controller_state(
            status="running",
            cycle=cycle,
            phase="repeat_gate",
            accepted_count=accepted_before,
            formal_state_sha256=state_sha_before,
        )
        first_source, passed_source, raw_gate = obtain_passed_repeat(
            discovery_root, cycle_root
        )
        if sha256_file(STATE) != state_sha_before:
            raise FailClosed("diagnostic phases mutated formal state")
        write_controller_state(
            status="running",
            cycle=cycle,
            phase="formal",
            accepted_count=accepted_before,
            formal_state_sha256=state_sha_before,
        )
        try:
            formal = prepare_formal(
                cycle_root,
                discovery,
                first_source,
                passed_source,
                raw_gate,
                state_sha_before,
            )
        except NoveltyCollision as error:
            if sha256_file(STATE) != state_sha_before:
                raise FailClosed(
                    "formal novelty collision coincided with state mutation"
                ) from error
            atomic_json(
                cycle_root / "cycle_audit.json",
                {
                    "status": "abandoned_zero_formal_calls_fingerprint_collision",
                    "cycle": cycle,
                    "accepted_before": accepted_before,
                    "accepted_after": accepted_before,
                    "accepted_delta": 0,
                    "formal_physical_calls": 0,
                    "state_sha256_before": state_sha_before,
                    "state_sha256_after": state_sha_before,
                    "novel_discovery_selection_sha256": sha256_file(discovery),
                    "error": str(error),
                    "manual_candidate_injection": False,
                    "qps_used_for_inner_scoring": False,
                },
            )
            continue
        execute_formal(formal, state_sha_before)
        state_sha_after, state_after = state_pin()
        accepted_after = int(state_after["accepted_count"])
        if accepted_after < accepted_before or accepted_after > TARGET_ACCEPTED:
            raise FailClosed("invalid accepted transition after formal cycle")
        atomic_json(
            cycle_root / "cycle_audit.json",
            {
                "status": "complete",
                "cycle": cycle,
                "accepted_before": accepted_before,
                "accepted_after": accepted_after,
                "accepted_delta": accepted_after - accepted_before,
                "state_sha256_before": state_sha_before,
                "state_sha256_after": state_sha_after,
                "novel_discovery_selection_sha256": sha256_file(discovery),
                "formal_selection_sha256": sha256_file(formal_artifacts(formal)[0]),
                "formal_summary_sha256": sha256_file(formal_artifacts(formal)[1]),
                "manual_candidate_injection": False,
                "qps_used_for_inner_scoring": False,
            },
        )
        latest_formal = formal

    state_sha_after, state_after = state_pin()
    write_controller_state(
        status="fail_closed_cycle_cap",
        accepted_count=int(state_after["accepted_count"]),
        formal_state_sha256=state_sha_after,
        maximum_cycles=args.max_cycles,
    )
    raise FailClosed("autonomous cycle cap reached before accepted=10")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FailClosed as error:
        try:
            write_controller_state(status="fail_closed", error=str(error))
        except Exception:
            pass
        print(f"JOB target10 fail closed: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
