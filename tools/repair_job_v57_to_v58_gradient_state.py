#!/usr/bin/env python3
"""Zero-call, SHA-bound repair of one committed JOB v57 formal batch.

The script never discovers result paths or trusts live files implicitly.  A
caller-created binding manifest must name and SHA-bind every input.  Audit is
the default; ``--apply`` performs an atomic-file migration while holding the
controller lock.  Any mismatch or interrupted prior migration fails closed.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any, Iterable, Mapping


V57_CAMPAIGN_CONTRACT = "job_v57_fresh0_resource_median_no_retry_v1"
GRADIENT_CONTRACT = "job_v58_complete_gradient_state_transition_v1"
TRANSACTION_CONTRACT = "job_v58_gradient_state_commit_transaction_v1"
REPAIR_CONTRACT = "job_v57_to_v58_zero_call_gradient_repair_v1"
EXPECTED_CODE_SHA256 = {
    "formal_controller": "3c9246b67a7fbaf5e28115efc59e4ac68066fc96e6515b6524099ca804c69668",
    "robust_resource_runner": "e4738c9eb95e123a38e31410bb067bf817bb8b54e234acbb2f5bc14ed89f2b03",
    "state_guard": "e717449a109669d6ed274e29d5aaf9f4285af8b37c586aeb0ad768970ba02693",
    "v57_controller": "ebc884eefe3cc5663ec4ae5738d46ff564afe78e4b56c68ef4b440996198285b",
}
REQUIRED_BINDINGS = frozenset(
    {
        "summary",
        "events",
        "state",
        "global_counter",
        "formal_replay_counter",
        "accepted_counter",
        "transaction",
        "selection",
        "gate",
        "cycle_audit",
        "controller_lock",
        *EXPECTED_CODE_SHA256,
    }
)


class RepairFailure(RuntimeError):
    """A repair precondition or crash-safety invariant failed."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RepairFailure(f"cannot read JSON binding: {path}") from error
    if not isinstance(value, dict):
        raise RepairFailure(f"JSON binding is not one object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def read_counter(path: Path) -> int:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw.isdigit():
        raise RepairFailure(f"invalid bound counter: {path}")
    return int(raw)


def _binding_paths(manifest: Mapping[str, Any]) -> dict[str, Path]:
    raw = manifest.get("bindings")
    if not isinstance(raw, Mapping) or set(raw) != REQUIRED_BINDINGS:
        raise RepairFailure("binding manifest has an incomplete/excessive file set")
    paths: dict[str, Path] = {}
    for name, binding in raw.items():
        if not isinstance(binding, Mapping):
            raise RepairFailure(f"binding is not an object: {name}")
        path_raw = binding.get("path")
        digest = binding.get("sha256")
        if not isinstance(path_raw, str) or not path_raw.startswith("/"):
            raise RepairFailure(f"binding path is not absolute: {name}")
        if name == "cycle_audit":
            if binding.get("must_be_absent") is not True or digest is not None:
                raise RepairFailure(
                    "cycle_audit binding must require absence without a SHA"
                )
        elif not isinstance(digest, str) or len(digest) != 64:
            raise RepairFailure(f"binding SHA is invalid: {name}")
        paths[name] = Path(path_raw).resolve()
    return paths


def _repair_paths(state_path: Path) -> tuple[Path, Path]:
    package = state_path.parent
    return (
        package / "v57_to_v58_gradient_repair_transaction.json",
        package / "v57_to_v58_gradient_repair_audit.json",
    )


def _verify_committed_repair(journal: Mapping[str, Any], paths: Mapping[str, Path]) -> dict[str, Any]:
    if journal.get("status") != "committed" or journal.get("contract") != REPAIR_CONTRACT:
        raise RepairFailure("prior v57-to-v58 repair is incomplete; manual audit required")
    output_sha = journal.get("output_sha256")
    if not isinstance(output_sha, Mapping):
        raise RepairFailure("committed repair lacks output SHA bindings")
    for name in ("state", "transaction", "cycle_audit"):
        if not paths[name].is_file() or output_sha.get(name) != sha256_file(paths[name]):
            raise RepairFailure(f"committed repair output changed: {name}")
    state = load_json(paths["state"])
    transaction = load_json(paths["transaction"])
    if (
        state.get("campaign_contract") != V57_CAMPAIGN_CONTRACT
        or transaction.get("gradient_transaction_contract") != TRANSACTION_CONTRACT
        or int(state.get("gradient_observation_count", -1))
        != int(transaction.get("gradient_observation_count_after", -2))
    ):
        raise RepairFailure("committed repair state/transaction contract mismatch")
    return {
        "status": "already_committed",
        "contract": REPAIR_CONTRACT,
        "gradient_observation_count": int(state["gradient_observation_count"]),
        "new_workload_calls": 0,
        "counter_updates": 0,
    }


def _verify_bindings(
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path],
) -> dict[str, str]:
    observed: dict[str, str] = {}
    raw = manifest["bindings"]
    for name, path in paths.items():
        if name == "cycle_audit":
            if path.exists():
                raise RepairFailure(
                    "cycle_audit appeared after the sealed formal commit; refusing migration"
                )
            observed[name] = "ABSENT_REQUIRED"
            continue
        if not path.is_file():
            raise RepairFailure(f"bound file is missing: {name}: {path}")
        digest = sha256_file(path)
        if digest != raw[name]["sha256"]:
            raise RepairFailure(f"bound file SHA mismatch: {name}: {digest}")
        observed[name] = digest
    for name, expected in EXPECTED_CODE_SHA256.items():
        if observed[name] != expected:
            raise RepairFailure(f"v57 vulnerable code SHA is not the sealed authority: {name}")
    return observed


def _walk_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def _event_objects(path: Path) -> list[Mapping[str, Any]]:
    values: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RepairFailure(f"events JSONL is malformed at line {line_number}") from error
        values.extend(_walk_objects(value))
    return values


def _fingerprint(row: Mapping[str, Any]) -> str:
    binding = row.get("candidate_binding") or {}
    direct = binding.get("fingerprint_id")
    if direct:
        return str(direct)
    fingerprint = binding.get("three_level_fingerprint") or {}
    return str(fingerprint.get("id") or "")


def _state_without_gradient(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("accepted_count", "incumbent_score", "incumbent_params", "range_by_parameter")
    if any(field not in value for field in fields):
        raise RepairFailure("row state evidence is incomplete")
    return {field: copy.deepcopy(value[field]) for field in fields}


def build_plan(
    manifest: Mapping[str, Any],
    paths: Mapping[str, Path],
    observed_sha: Mapping[str, str],
    authority_expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    summary = load_json(paths["summary"])
    state = load_json(paths["state"])
    transaction = load_json(paths["transaction"])
    selection = load_json(paths["selection"])
    gate = load_json(paths["gate"])
    successful = summary.get("successful") or []
    failures = summary.get("technical_failures") or []
    if not isinstance(successful, list) or not isinstance(failures, list):
        raise RepairFailure("formal summary transition ledgers are malformed")
    physical = int(summary.get("physical_invocation_count", -1))
    valid = int(summary.get("successful_real_replay_count", -1))
    technical = int(summary.get("technical_failure_count", -1))
    attempts = int(summary.get("batch_budget_attempt_count", valid + technical))
    if (
        transaction.get("status") != "committed"
        or summary.get("diagnostic_only") is True
        or summary.get("selection_sha256") != observed_sha["selection"]
        or len(successful) != valid
        or len(failures) != technical
        or not (0 <= valid <= physical <= attempts <= 5)
        or attempts != valid + technical
        or summary.get("baseline_params_restored") is not True
        or transaction.get("state_sha256_after") != observed_sha["state"]
        or int(transaction.get("accepted_after", -1)) != int(state.get("accepted_count", -2))
        or int(transaction.get("global_ordinal_after", -1)) != read_counter(paths["global_counter"])
        or int(transaction.get("formal_replay_count_after", -1))
        != read_counter(paths["formal_replay_counter"])
        or read_counter(paths["accepted_counter"]) != int(state.get("accepted_count", -1))
        or paths["cycle_audit"].exists()
        or gate.get("passed") is not True
        or not isinstance(gate.get("checks"), Mapping)
        or not gate["checks"]
        or not all(value is True for value in gate["checks"].values())
    ):
        raise RepairFailure("v57 formal batch/state/counter contract mismatch")
    candidates = selection.get("top5") or selection.get("candidates") or []
    if not isinstance(candidates, list) or len(candidates) != 5:
        raise RepairFailure("v57 formal selection is not Top-5")
    roles = [str(item.get("selection_role") or "") for item in candidates if isinstance(item, Mapping)]
    if roles != ["trusted", "trusted", "trusted", "probe", "probe"]:
        raise RepairFailure("v57 formal roles are not trusted3+probe2")

    expected = None
    accepted_rows = 0
    successful_by_key: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in successful:
        if not isinstance(row, Mapping):
            raise RepairFailure("successful formal row is malformed")
        rank = int(row.get("rank", -1))
        fingerprint = _fingerprint(row)
        if rank < 1 or rank > 5 or not fingerprint:
            raise RepairFailure("successful row lacks exact rank/fingerprint binding")
        before_raw = row.get("incumbent_state_before")
        after_raw = row.get("incumbent_state_after")
        if not isinstance(before_raw, Mapping) or not isinstance(after_raw, Mapping):
            raise RepairFailure("successful row lacks before/after state")
        before = _state_without_gradient(before_raw)
        after = _state_without_gradient(after_raw)
        if expected is not None and before != expected:
            raise RepairFailure("v57 successful rows break the state chain")
        accepted = row.get("accepted") is True
        flags = (
            row.get("gradient_update_applied") is True,
            row.get("incumbent_update_applied") is True,
            row.get("range_update_applied") is True,
        )
        if accepted:
            if row.get("selection_role") != "trusted" or flags != (True, True, True):
                raise RepairFailure("accepted v57 row lacks complete trusted update flags")
            if int(after["accepted_count"]) != int(before["accepted_count"]) + 1:
                raise RepairFailure("accepted v57 row has an invalid accepted transition")
            accepted_rows += 1
        elif flags != (False, False, False) or after != before:
            raise RepairFailure("rejected/probe v57 row is not state-neutral")
        expected = after
        successful_by_key[(rank, fingerprint)] = row
    for row in failures:
        if not isinstance(row, Mapping):
            raise RepairFailure("technical formal row is malformed")
        if (
            row.get("technical_failure") is not True
            or row.get("accepted") is not False
            or row.get("gradient_update_applied") is not False
            or row.get("incumbent_update_applied") is not False
            or row.get("range_update_applied") is not False
        ):
            raise RepairFailure("technical v57 row has a state update flag")
    if int(transaction["accepted_after"]) - int(transaction["accepted_before"]) != accepted_rows:
        raise RepairFailure("transaction accepted delta differs from accepted rows")

    event_objects = _event_objects(paths["events"])
    for key, row in successful_by_key.items():
        matching = [
            item
            for item in event_objects
            if int(item.get("rank", -1)) == key[0]
            and _fingerprint(item) == key[1]
            and item.get("accepted") is row.get("accepted")
            and item.get("gradient_update_applied")
            is row.get("gradient_update_applied")
        ]
        if not matching:
            raise RepairFailure(f"events do not bind successful rank {key[0]}")

    gradient_before = int(state.get("gradient_observation_count", -1))
    if gradient_before < 0:
        raise RepairFailure("v57 state lacks a valid gradient count")
    gradient_after = gradient_before + accepted_rows
    if authority_expected is not None:
        exact_checks = {
            "state_sha256": observed_sha["state"],
            "summary_sha256": observed_sha["summary"],
            "events_sha256": observed_sha["events"],
            "transaction_sha256": observed_sha["transaction"],
            "accepted_count": int(state.get("accepted_count", -1)),
            "gradient_before": gradient_before,
            "gradient_after": gradient_after,
            "incumbent_score": float(state.get("incumbent_score")),
            "global_counter": read_counter(paths["global_counter"]),
            "formal_replay_counter": read_counter(paths["formal_replay_counter"]),
        }
        for name, observed in exact_checks.items():
            if observed != authority_expected.get(name):
                raise RepairFailure(
                    f"explicit remote authority mismatch: {name}: "
                    f"expected={authority_expected.get(name)!r} observed={observed!r}"
                )
    repaired_state = copy.deepcopy(state)
    repaired_state["gradient_observation_count"] = gradient_after
    if any(
        repaired_state[name] != state[name]
        for name in state
        if name != "gradient_observation_count"
    ):
        raise RepairFailure("repair plan would change non-gradient formal state")

    repaired_transaction = copy.deepcopy(transaction)
    repaired_transaction.update(
        {
            "state_sha256_before": gate["formal_state_sha256_before"],
            "gradient_observation_count_before": gradient_before,
            "gradient_observation_count_after": gradient_after,
            "gradient_observation_count_delta": accepted_rows,
            "gradient_summary_sha256": observed_sha["summary"],
            "gradient_events_sha256": observed_sha["events"],
            "gradient_state_contract": GRADIENT_CONTRACT,
            "gradient_transaction_contract": TRANSACTION_CONTRACT,
            "v57_to_v58_repair_contract": REPAIR_CONTRACT,
        }
    )
    cycle_name = paths["cycle_audit"].parent.name
    if not cycle_name.startswith("cycle_") or not cycle_name[6:].isdigit():
        raise RepairFailure("absent cycle_audit path is not under one exact cycle")
    repaired_cycle_audit = {
        "status": "complete",
        "cycle": int(cycle_name[6:]),
        "accepted_before": int(transaction["accepted_before"]),
        "accepted_after": int(transaction["accepted_after"]),
        "accepted_delta": accepted_rows,
        "state_sha256_before": gate["formal_state_sha256_before"],
        "state_sha256_after": None,
        "novel_discovery_selection_sha256": gate[
            "novel_discovery_selection_sha256"
        ],
        "formal_selection_sha256": observed_sha["selection"],
        "formal_summary_sha256": observed_sha["summary"],
        "manual_candidate_injection": False,
        "qps_used_for_inner_scoring": False,
        "gradient_observation_count_before": gradient_before,
        "gradient_observation_count_after": gradient_after,
        "gradient_observation_count_delta": accepted_rows,
        "gradient_state_contract": GRADIENT_CONTRACT,
        "v57_to_v58_repair_contract": REPAIR_CONTRACT,
        "recovered_from_committed_formal_before_v57_outer_audit": True,
        "old_cycle_audit_absent_required": True,
        "new_workload_calls": 0,
    }
    return {
        "contract": REPAIR_CONTRACT,
        "input_sha256": dict(observed_sha),
        "manifest_schema_version": manifest.get("schema_version"),
        "accepted_rows": accepted_rows,
        "gradient_observation_count_before": gradient_before,
        "gradient_observation_count_after": gradient_after,
        "repaired_state": repaired_state,
        "repaired_transaction": repaired_transaction,
        "repaired_cycle_audit": repaired_cycle_audit,
        "new_workload_calls": 0,
        "counter_updates": 0,
    }


def apply_plan(
    plan: Mapping[str, Any],
    paths: Mapping[str, Path],
    journal_path: Path,
    audit_path: Path,
    *,
    fail_after_stage: str | None = None,
) -> dict[str, Any]:
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    backups = {}
    for name in ("state", "transaction"):
        backup = paths[name].with_name(f"{paths[name].name}.bak.v58.{stamp}.{os.getpid()}")
        shutil.copy2(paths[name], backup)
        backups[name] = {"path": str(backup), "sha256": sha256_file(backup)}
    backups["cycle_audit"] = {
        "path": str(paths["cycle_audit"]),
        "status": "absent_before_repair",
    }
    journal = {
        "status": "prepared",
        "contract": REPAIR_CONTRACT,
        "input_sha256": copy.deepcopy(plan["input_sha256"]),
        "backups": backups,
        "gradient_observation_count_before": plan["gradient_observation_count_before"],
        "gradient_observation_count_after": plan["gradient_observation_count_after"],
        "new_workload_calls": 0,
        "counter_updates": 0,
    }
    atomic_json(journal_path, journal)
    if fail_after_stage == "prepared":
        raise RepairFailure("injected crash after prepared journal")

    atomic_json(paths["state"], plan["repaired_state"])
    state_sha_after = sha256_file(paths["state"])
    if fail_after_stage == "state":
        raise RepairFailure("injected crash after state replacement")

    cycle_audit = copy.deepcopy(plan["repaired_cycle_audit"])
    cycle_audit["state_sha256_after"] = state_sha_after
    atomic_json(paths["cycle_audit"], cycle_audit)
    if fail_after_stage == "cycle_audit":
        raise RepairFailure("injected crash after cycle audit replacement")

    transaction = copy.deepcopy(plan["repaired_transaction"])
    transaction["state_sha256_after"] = state_sha_after
    transaction["gradient_state_sha256_after"] = state_sha_after
    atomic_json(paths["transaction"], transaction)
    if fail_after_stage == "transaction":
        raise RepairFailure("injected crash after formal transaction replacement")

    output_sha = {
        name: sha256_file(paths[name]) for name in ("state", "transaction", "cycle_audit")
    }
    audit = {
        "status": "complete",
        "contract": REPAIR_CONTRACT,
        "input_sha256": copy.deepcopy(plan["input_sha256"]),
        "output_sha256": output_sha,
        "backups": backups,
        "accepted_rows": plan["accepted_rows"],
        "gradient_observation_count_before": plan["gradient_observation_count_before"],
        "gradient_observation_count_after": plan["gradient_observation_count_after"],
        "new_workload_calls": 0,
        "counter_updates": 0,
    }
    atomic_json(audit_path, audit)
    journal.update({"status": "committed", "output_sha256": output_sha, "audit_sha256": sha256_file(audit_path)})
    atomic_json(journal_path, journal)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding-manifest", type=Path, required=True)
    parser.add_argument("--binding-manifest-sha256", required=True)
    parser.add_argument("--expected-state-sha256", required=True)
    parser.add_argument("--expected-summary-sha256", required=True)
    parser.add_argument("--expected-events-sha256", required=True)
    parser.add_argument("--expected-transaction-sha256", required=True)
    parser.add_argument("--expected-accepted-count", type=int, required=True)
    parser.add_argument("--expected-gradient-before", type=int, required=True)
    parser.add_argument("--expected-gradient-after", type=int, required=True)
    parser.add_argument("--expected-incumbent-score", type=float, required=True)
    parser.add_argument("--expected-global-counter", type=int, required=True)
    parser.add_argument("--expected-formal-replay-counter", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest_path = args.binding_manifest.resolve()
    if not manifest_path.is_file() or sha256_file(manifest_path) != args.binding_manifest_sha256:
        raise RepairFailure("binding manifest SHA mismatch")
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != "job_v57_to_v58_binding_manifest_v1":
        raise RepairFailure("binding manifest schema mismatch")
    paths = _binding_paths(manifest)
    journal_path, audit_path = _repair_paths(paths["state"])

    lock_handle = paths["controller_lock"].open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RepairFailure("JOB controller is running; refusing zero-call repair") from error
    if journal_path.exists():
        result = _verify_committed_repair(load_json(journal_path), paths)
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        return 0
    observed_sha = _verify_bindings(manifest, paths)
    plan = build_plan(
        manifest,
        paths,
        observed_sha,
        {
            "state_sha256": args.expected_state_sha256,
            "summary_sha256": args.expected_summary_sha256,
            "events_sha256": args.expected_events_sha256,
            "transaction_sha256": args.expected_transaction_sha256,
            "accepted_count": args.expected_accepted_count,
            "gradient_before": args.expected_gradient_before,
            "gradient_after": args.expected_gradient_after,
            "incumbent_score": args.expected_incumbent_score,
            "global_counter": args.expected_global_counter,
            "formal_replay_counter": args.expected_formal_replay_counter,
        },
    )
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "audit_passed_ready_to_apply",
                    "contract": REPAIR_CONTRACT,
                    "accepted_rows": plan["accepted_rows"],
                    "gradient_observation_count_before": plan[
                        "gradient_observation_count_before"
                    ],
                    "gradient_observation_count_after": plan[
                        "gradient_observation_count_after"
                    ],
                    "new_workload_calls": 0,
                    "counter_updates": 0,
                },
                indent=2,
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    result = apply_plan(plan, paths, journal_path, audit_path)
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RepairFailure, OSError, ValueError, TypeError) as error:
        print(
            json.dumps(
                {
                    "status": "fail_closed",
                    "error": str(error),
                    "new_workload_calls": 0,
                    "counter_updates": 0,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
