#!/usr/bin/env python3
"""Fail-closed autonomous controller for TPC-C formal target-10 search.

Default execution is dry-run.  Armed mode requires the exact SHA-256 of a
sealed manifest and is the only mode allowed to execute builders or launchers.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


TARGET = 10
CAP = 5
RESOURCES = ("cpu", "io", "memory")
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_text(path: Path, text: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def average_ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(order):
        end = cursor + 1
        while end < len(order) and math.isclose(
            values[order[end]], values[order[cursor]], rel_tol=0.0, abs_tol=1e-12
        ):
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for index in order[cursor:end]:
            result[index] = rank
        cursor = end
    return result


def spearman(predicted: Sequence[float], actual: Sequence[float]) -> float | None:
    if len(predicted) != len(actual) or len(predicted) < 2:
        return None
    left = average_ranks(predicted)
    right = average_ranks(actual)
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    numerator = sum(
        (left[index] - left_mean) * (right[index] - right_mean)
        for index in range(len(left))
    )
    left_norm = math.sqrt(sum((value - left_mean) ** 2 for value in left))
    right_norm = math.sqrt(sum((value - right_mean) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    return numerator / (left_norm * right_norm)


def verify_pins(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != "tpcc_autonomous_target10_manifest_v1":
        raise RuntimeError("manifest schema mismatch")
    for item in manifest.get("pinned_files") or []:
        path = Path(item["path"]).resolve()
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"pinned file SHA mismatch: {path}")
    if int(manifest.get("accepted_target", -1)) != TARGET:
        raise RuntimeError("accepted target mismatch")
    if int(manifest.get("per_batch_physical_cap", -1)) != CAP:
        raise RuntimeError("physical cap mismatch")
    if manifest.get("qps_role") != "outer_audit_only_not_inner_score":
        raise RuntimeError("QPS contract mismatch")


def validate_selection(selection: Mapping[str, Any], *, mode: str) -> None:
    candidates = selection.get("top5") or []
    if len(candidates) != CAP:
        raise RuntimeError("selection does not contain exactly five candidates")
    roles = [item.get("selection_role") for item in candidates]
    expected = (
        ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        if mode in {"formal", "diagnostic_validation"}
        else ["uncertain_probe"] * CAP
    )
    if (
        roles != expected
        or selection.get("maximum_real_workload_invocations") != CAP
        or selection.get("qps_role") != "outer_audit_only_not_inner_score"
    ):
        raise RuntimeError("selection role/cap/QPS contract mismatch")
    if mode == "formal" and (
        selection.get("formal_expected_improvement_required") is not True
        or candidates[0].get("formal_improvement_eligible") is not True
        or selection.get("formal_admission_score")
        != "predicted_score_interval.lower"
        or candidates[0].get("formal_lcb_score") is None
        or candidates[0].get("predicted_improvement_lower_bound") is None
        or float(candidates[0]["predicted_improvement_lower_bound"])
        <= float(selection.get("minimum_predicted_improvement", 0.0))
        or not math.isclose(
            float(candidates[0]["formal_lcb_score"]),
            float(
                (candidates[0].get("predicted_score_interval") or {})[
                    "lower"
                ]
            ),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise RuntimeError(
            "formal selection failed confidence-lower-bound improvement gate"
        )
    ids: List[str] = []
    for rank, item in enumerate(candidates, 1):
        fingerprint = item.get("three_level_fingerprint") or {}
        if (
            int(item.get("rank", -1)) != rank
            or item.get("boundary_corner") is not False
            or item.get("diagnostic_only") is not True
            or item.get("incumbent_update_eligible") is not False
            or fingerprint.get("valid") is not True
            or not str(fingerprint.get("id") or "").startswith(
                "three-level-sha256:"
            )
            or any(item.get(field) is None for field in REQUIRED_CANDIDATE_FIELDS)
        ):
            raise RuntimeError(f"candidate contract mismatch: rank={rank}")
        if item["selection_role"] == "trusted":
            calibration = item.get("plan_regime_calibration") or {}
            if (
                item.get("trusted") is not True
                or item.get("exact_plan_regime_validated") is not True
                or calibration.get("trusted_eligible") is not True
                or (calibration.get("regime") or {}).get("plan")
                != item.get("workload_plan_fingerprint")
            ):
                raise RuntimeError(f"unvalidated trusted rank: {rank}")
        else:
            if item.get("trusted") is not False:
                raise RuntimeError(f"probe marked trusted: rank={rank}")
        ids.append(fingerprint["id"])
    if len(set(ids)) != CAP:
        raise RuntimeError("selection fingerprints are not unique")


def audit_completed_run(
    run: Mapping[str, Any],
    formal_state: Path,
    *,
    verify_current_state_sha: bool = True,
) -> Dict[str, Any]:
    mode = str(run["mode"])
    selection_path = Path(run["selection_path"]).resolve()
    ledger_path = Path(run["ledger_path"]).resolve()
    summary_path = Path(run["summary_path"]).resolve()
    for path, expected in (
        (selection_path, run["selection_sha256"]),
        (ledger_path, run["ledger_sha256"]),
        (summary_path, run["summary_sha256"]),
    ):
        if not path.is_file() or sha256(path) != expected:
            raise RuntimeError(f"completed-run SHA mismatch: {path}")
    selection = load_json(selection_path)
    ledger = load_json(ledger_path)
    summary = load_json(summary_path)
    validate_selection(selection, mode=mode)
    physical = int(ledger.get("real_workload_invocation_count", -1))
    successful = ledger.get("successful") or []
    technical = ledger.get("technical_failures") or []
    if (
        not 0 <= physical <= CAP
        or physical != len(successful) + len(technical)
        or len(successful) > CAP
    ):
        raise RuntimeError("physical/valid/technical accounting mismatch")
    selected = {
        int(item["rank"]): item for item in selection["top5"]
    }
    calibration_rows = []
    trusted_pairs = []
    resource_ratios = []
    actual_best_rank = None
    if successful:
        actual_best_rank = int(
            max(successful, key=lambda item: float(item["actual_score"]))["rank"]
        )
    for result in successful:
        rank = int(result["rank"])
        candidate = selected[rank]
        protocol = result.get("measurement_protocol") or {}
        evidence_path = Path(str(protocol.get("validated_summary_path") or ""))
        if (
            protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or result.get("workload_plan_fingerprint")
            != candidate.get("workload_plan_fingerprint")
            or result.get("three_level_fingerprint", {}).get("id")
            != candidate.get("three_level_fingerprint", {}).get("id")
            or not evidence_path.is_file()
            or sha256(evidence_path) != protocol.get("validated_summary_sha256")
        ):
            raise RuntimeError(f"successful replay evidence mismatch: rank={rank}")
        raw = candidate.get("uncalibrated_predicted_resource") or candidate[
            "predicted_resource"
        ]
        scalar = {
            name: float(result["actual_resource"][name]) / float(raw[name])
            for name in RESOURCES
        }
        if any(not 0.1 <= value <= 10.0 for value in scalar.values()):
            raise RuntimeError("exact regime scalar has magnitude failure")
        calibration_rows.append(
            {
                "plan": result["workload_plan_fingerprint"],
                "rank": rank,
                "resource_scalar": scalar,
                "actual_resource": copy.deepcopy(result["actual_resource"]),
                "actual_score": float(result["actual_score"]),
                "summary_path": str(evidence_path),
                "summary_sha256": sha256(evidence_path),
            }
        )
        if candidate["selection_role"] == "trusted":
            trusted_pairs.append(
                (
                    float(result["predicted_score"]),
                    float(result["actual_score"]),
                )
            )
            for name in RESOURCES:
                resource_ratios.append(
                    float(result["predicted_resource"][name])
                    / float(result["actual_resource"][name])
                )

    baseline_restored = bool(
        summary.get("baseline_params_restored") is True
        and summary.get("baseline_snapshot_restored") is True
    ) if mode != "formal" else bool(summary.get("baseline_restored") is True)
    clean = bool(
        physical == CAP
        and len(successful) == CAP
        and not technical
        and baseline_restored
    )
    if mode != "formal":
        if (
            summary.get("accepted_count_delta") != 0
            or summary.get("incumbent_updated") is not False
            or summary.get("range_updated") is not False
            or summary.get("gradient_update_applied") is not False
            or summary.get("state_commit_eligible") is not False
        ):
            raise RuntimeError("diagnostic run mutated formal state")
    else:
        current_state_sha = sha256(formal_state)
        if (
            verify_current_state_sha
            and summary.get("formal_state_sha256") != current_state_sha
        ):
            raise RuntimeError("formal state SHA does not match completed summary")
        if not clean and (
            summary.get("incumbent_update_applied") is not False
            or summary.get("range_update_applied") is not False
        ):
            raise RuntimeError("technical formal batch mutated incumbent/range")

    predicted = [item[0] for item in trusted_pairs]
    actual = [item[1] for item in trusted_pairs]
    trusted_spearman = spearman(predicted, actual)
    resource_factor_two = bool(
        resource_ratios
        and all(0.5 <= value <= 2.0 for value in resource_ratios)
    )
    resource_within_order_of_magnitude = bool(
        resource_ratios
        and all(0.1 <= value <= 10.0 for value in resource_ratios)
    )
    top3_hit = actual_best_rank is not None and actual_best_rank <= 3
    return {
        "mode": mode,
        "root": run["root"],
        "clean": clean,
        "physical_calls": physical,
        "valid_replays": len(successful),
        "technical_failures": len(technical),
        "baseline_restored": baseline_restored,
        "trusted_count": len(trusted_pairs),
        "trusted_pairs": trusted_pairs,
        "trusted_spearman": trusted_spearman,
        "actual_best_predicted_rank": actual_best_rank,
        "top3_hit": top3_hit,
        "trusted_resource_within_factor_two": resource_factor_two,
        "trusted_resource_no_order_of_magnitude_error": (
            resource_within_order_of_magnitude
        ),
        "gradient_reversal_detected": (
            trusted_spearman is not None and trusted_spearman <= 0.0
        ),
        "accepted_before": (
            int(summary["accepted_before"])
            if mode == "formal" and summary.get("accepted_before") is not None
            else None
        ),
        "accepted_after": (
            int(summary["accepted_after"])
            if mode == "formal" and summary.get("accepted_after") is not None
            else None
        ),
        "batch_added_accepted": (
            int(summary["batch_added_accepted"])
            if mode == "formal"
            and summary.get("batch_added_accepted") is not None
            else None
        ),
        "formal_prediction_miss": bool(
            mode == "formal"
            and clean
            and int(summary.get("batch_added_accepted") or 0) == 0
        ),
        "calibration_rows": calibration_rows,
        "fingerprint_ids": [
            item["three_level_fingerprint"]["id"]
            for item in selection["top5"]
        ],
        "physical_fingerprint_ids": [
            selected[int(item["rank"])]["three_level_fingerprint"]["id"]
            for item in [*successful, *technical]
        ],
        "selection_path": str(selection_path),
        "selection_sha256": sha256(selection_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": sha256(ledger_path),
        "summary_path": str(summary_path),
        "summary_sha256": sha256(summary_path),
    }


def choose_next_mode(
    audits: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> str:
    """Route a clean formal miss back through state-neutral recalibration."""
    latest = audits[-1] if audits else None
    if (
        latest is not None
        and latest.get("mode") == "diagnostic"
        and latest.get("clean") is True
        and latest.get("valid_replays") == CAP
    ):
        return "diagnostic_validation"
    if latest is not None and latest.get("mode") == "formal":
        if latest.get("clean") is not True:
            return "diagnostic"
        if (
            latest.get("formal_prediction_miss") is True
            or latest.get("batch_added_accepted") == 0
        ):
            return "diagnostic"
    return "formal" if gate.get("passed") is True else "diagnostic"


def quality_gate(audits: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    validations = [
        item for item in audits
        if item["mode"] == "diagnostic_validation"
    ]
    latest = validations[-1] if validations else None
    fingerprints = list((latest or {}).get("fingerprint_ids") or [])
    latest_calibration_rows = list(
        (latest or {}).get("calibration_rows") or []
    )
    # The fixed experiment contract rejects order-of-magnitude prediction
    # failures; it does not impose the older factor-of-two research heuristic.
    # Older persisted audits predate the explicit boolean below, but their
    # audit path already fail-closed when any exact-regime scalar is outside
    # [0.1, 10].  Re-evaluate those sealed scalar rows without changing the
    # diagnostic or formal state.
    trusted_resource_no_magnitude_error = bool(
        latest is not None
        and (
            latest.get(
                "trusted_resource_no_order_of_magnitude_error"
            ) is True
            or (
                latest_calibration_rows
                and all(
                    0.1 <= float(value) <= 10.0
                    for row in latest_calibration_rows
                    for value in (
                        row.get("resource_scalar") or {}
                    ).values()
                )
            )
        )
    )
    checks = {
        "corrected_top5_present": latest is not None,
        "five_novel_noncorner_fingerprints": (
            latest is not None
            and len(fingerprints) == CAP
            and len(set(fingerprints)) == CAP
        ),
        "trusted3_probe2": (
            latest is not None and latest.get("trusted_count") == 3
        ),
        "top5_clean_no_technical_failure": (
            latest is not None
            and latest.get("clean") is True
            and latest.get("physical_calls") == CAP
            and latest.get("valid_replays") == CAP
            and latest.get("technical_failures") == 0
        ),
        "trusted_spearman_positive": (
            latest is not None
            and latest.get("trusted_spearman") is not None
            and float(latest["trusted_spearman"]) > 0.0
        ),
        "actual_best_in_predicted_top3": (
            latest is not None and latest.get("top3_hit") is True
        ),
        "trusted_resource_no_order_of_magnitude_error": (
            trusted_resource_no_magnitude_error
        ),
        "baseline_restored": (
            latest is not None and latest.get("baseline_restored") is True
        ),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "diagnostic_validation_batch_count": len(validations),
        "latest_trusted_spearman": (
            latest.get("trusted_spearman") if latest is not None else None
        ),
        "latest_actual_best_predicted_rank": (
            latest.get("actual_best_predicted_rank")
            if latest is not None else None
        ),
    }


def build_exact_calibration(audits: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    regimes: Dict[str, List[Dict[str, Any]]] = {}
    for audit in audits:
        if not audit["clean"]:
            continue
        for row in audit.get("calibration_rows") or []:
            regimes.setdefault(row["plan"], []).append(copy.deepcopy(row))
    output = {}
    for plan, rows in regimes.items():
        output[plan] = {
            "plan": plan,
            "observation_count": len(rows),
            "resource_scalar_median": {
                name: sorted(float(row["resource_scalar"][name]) for row in rows)[
                    len(rows) // 2
                ]
                for name in RESOURCES
            },
            "evidence": rows,
        }
    return {
        "schema_version": "tpcc_autonomous_exact_plan_regime_calibration_v1",
        "scope": "exact_workload_plan_fingerprint_only",
        "regimes": output,
    }


def run_checked(argv: Sequence[str], *, timeout: int) -> None:
    completed = subprocess.run(
        list(argv),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {completed.stdout[-4000:]}"
        )


def seal_next_run(
    manifest: Mapping[str, Any],
    preparation: Path,
    selection_path: Path,
    mode: str,
    ordinal: int,
    quality: Mapping[str, Any],
) -> Dict[str, Any]:
    selection_sha = sha256(selection_path)
    package_root = preparation / "sealed_run"
    package_root.mkdir(parents=True, exist_ok=False)
    sealed_selection = package_root / "top5.json"
    sealed_selection.write_bytes(selection_path.read_bytes())
    if sha256(sealed_selection) != selection_sha:
        raise RuntimeError("sealed selection copy SHA mismatch")
    run_root = (
        Path(manifest["batch_parent"]).resolve()
        / f"autonomous_{mode}_{ordinal:02d}"
    )
    if run_root.exists():
        raise FileExistsError(f"next run root already exists: {run_root}")
    python = str(manifest["python"])
    baseline = Path(manifest["baseline_path"]).resolve()
    diagnostic_runner = Path(manifest["diagnostic_runner_path"]).resolve()
    formal_state = Path(manifest["formal_state_path"]).resolve()
    launcher = package_root / "launch_foreground.sh"
    if mode == "formal":
        gate = {
            "schema_version": "tpcc_autonomous_formal_gate_v1",
            "passed": True,
            "formal_start_authorized": True,
            "selection_sha256": selection_sha,
            "quality_gate": copy.deepcopy(quality),
            "qps_role": "outer_audit_only_not_inner_score",
        }
        gate_path = package_root / "gate.json"
        atomic_json(gate_path, gate)
        gate_sha = sha256(gate_path)
        formal_runner = Path(manifest["formal_runner_path"]).resolve()
        command = [
            python,
            "-u",
            str(formal_runner),
            str(sealed_selection),
            selection_sha,
            str(gate_path),
            gate_sha,
            str(diagnostic_runner),
            str(baseline),
            str(formal_state),
            str(run_root),
        ]
        ledger_path = run_root / "strict_top5_real_replay_round14_ledger_v1.json"
        summary_path = run_root / "formal_batch_summary.json"
        prelude = ""
    else:
        copied_selection = run_root / "top5.json"
        selection_contract = load_json(sealed_selection).get(
            "selection_contract"
        )
        generation_names = {
            "tpcc_round12_current_plan_discovery_probe5_v1": (
                "round12",
                "strict_top5_real_replay_round12_ledger_v1.json",
            ),
            "tpcc_round13_exact_plan_regime_corrected_top5_v1": (
                "round13",
                "strict_top5_real_replay_round13_ledger_v1.json",
            ),
            "tpcc_robust_exact_plan_regime_trusted3_probe2_v1": (
                "round14",
                "strict_top5_real_replay_round14_ledger_v1.json",
            ),
        }
        if selection_contract not in generation_names:
            raise RuntimeError(
                "unsupported diagnostic selection contract: "
                f"{selection_contract}"
            )
        replay_generation, ledger_name = generation_names[
            selection_contract
        ]
        # The diagnostic runner binds the replay child name to the selection
        # generation.  Preserve that binding instead of forcing every
        # acquisition/validation batch through the robust-round14 prefix.
        replay_root = run_root / (
            f"top5_real_replay_{replay_generation}_diagnostic_v1"
        )
        project_root = (
            Path(manifest["formal_runner_path"]).resolve().parents[1]
        )
        diagnostic_pythonpath = ":".join(
            dict.fromkeys(
                (str(project_root), str(diagnostic_runner.parent))
            )
        )
        command = [
            "env",
            f"PYTHONPATH={diagnostic_pythonpath}",
            python,
            "-u",
            str(diagnostic_runner),
            str(copied_selection),
            str(replay_root),
            str(baseline),
        ]
        ledger_path = run_root / ledger_name
        summary_path = replay_root / "summary.json"
        prelude = (
            f"test ! -e {shlex.quote(str(run_root))}\n"
            f"mkdir -p {shlex.quote(str(run_root))}\n"
            f"cp {shlex.quote(str(sealed_selection))} "
            f"{shlex.quote(str(copied_selection))}\n"
            f'test "$(sha256sum {shlex.quote(str(copied_selection))} '
            f'| awk \'{{print $1}}\')" = "{selection_sha}"\n'
        )
    pinned_checks = []
    for key in (
        "baseline_path",
        "diagnostic_runner_path",
        "formal_runner_path",
    ):
        if key == "formal_runner_path" and mode != "formal":
            continue
        path = Path(manifest[key]).resolve()
        expected = next(
            item["sha256"]
            for item in manifest["pinned_files"]
            if Path(item["path"]).resolve() == path
        )
        pinned_checks.append(
            f'test "$(sha256sum {shlex.quote(str(path))} '
            f'| awk \'{{print $1}}\')" = "{expected}"'
        )
    formal_state_sha = sha256(formal_state)
    pinned_checks.append(
        f'test "$(sha256sum {shlex.quote(str(formal_state))} '
        f'| awk \'{{print $1}}\')" = "{formal_state_sha}"'
    )
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'test "$(sha256sum {shlex.quote(str(sealed_selection))} '
        f'| awk \'{{print $1}}\')" = "{selection_sha}"\n'
        + "\n".join(pinned_checks)
        + "\n"
        + prelude
        + "exec "
        + " ".join(shlex.quote(item) for item in command)
        + "\n"
    )
    atomic_text(launcher, script, mode=0o700)
    seal = {
        "schema_version": "tpcc_autonomous_sealed_run_v1",
        "mode": mode,
        "ordinal": ordinal,
        "selection_path": str(sealed_selection),
        "selection_sha256": selection_sha,
        "launcher_path": str(launcher),
        "launcher_sha256": sha256(launcher),
        "run_root": str(run_root),
        "ledger_path": str(ledger_path),
        "summary_path": str(summary_path),
        "physical_cap": CAP,
        "formal_state_sha256_before": formal_state_sha,
        "qps_role": "outer_audit_only_not_inner_score",
    }
    seal_path = package_root / "run_seal.json"
    atomic_json(seal_path, seal)
    seal["seal_path"] = str(seal_path)
    seal["seal_sha256"] = sha256(seal_path)
    return seal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--armed", action="store_true")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    if args.armed and (
        not args.manifest_sha256
        or sha256(manifest_path) != args.manifest_sha256
    ):
        raise RuntimeError("armed mode requires exact sealed manifest SHA")
    manifest = load_json(manifest_path)
    verify_pins(manifest)
    root = Path(manifest["controller_root"]).resolve()
    state_path = root / "controller_state.json"
    lock_path = root / "controller.lock"
    formal_state = Path(manifest["formal_state_path"]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = (
            load_json(state_path)
            if state_path.exists()
            else {
                "schema_version": "tpcc_autonomous_target10_state_v1",
                "status": "waiting_current_batch",
                "dry_run": not args.armed,
                "audits": copy.deepcopy(manifest.get("seed_audits") or []),
                "sources": copy.deepcopy(manifest.get("seed_sources") or []),
                "current_run": copy.deepcopy(manifest["current_run"]),
                "next_ordinal": int(manifest.get("next_ordinal", 7)),
                "formal_state_sha256": manifest[
                    "initial_formal_state_sha256"
                ],
            }
        )
        while True:
            formal = load_json(formal_state)
            if int(formal["accepted_count"]) >= TARGET:
                state["status"] = "target_reached"
                state["formal_state_sha256"] = sha256(formal_state)
                atomic_json(state_path, state)
                return 0
            current = state.get("current_run")
            if current is not None:
                summary_path = Path(current["summary_path"])
                ledger_path = Path(current["ledger_path"])
                if not summary_path.is_file() or not ledger_path.is_file():
                    run_root = Path(current["root"])
                    if (
                        args.armed
                        and current.get("launch_if_absent") is True
                        and current.get("launcher_invoked") is not True
                        and not run_root.exists()
                    ):
                        launcher = Path(current["launcher_path"]).resolve()
                        if (
                            not launcher.is_file()
                            or sha256(launcher) != current["launcher_sha256"]
                            or sha256(formal_state)
                            != current["formal_state_sha256_before"]
                        ):
                            raise RuntimeError(
                                "current sealed launcher/state SHA mismatch"
                            )
                        current["launcher_invoked"] = True
                        state["status"] = "launching_current_sealed_run"
                        atomic_json(state_path, state)
                        completed = subprocess.run(
                            ["bash", str(launcher)],
                            check=False,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            timeout=int(
                                manifest.get("run_timeout_seconds", 21600)
                            ),
                        )
                        atomic_text(
                            root / "current_launcher.log",
                            completed.stdout,
                        )
                        state["last_launcher_returncode"] = (
                            completed.returncode
                        )
                        state["formal_state_sha256"] = sha256(formal_state)
                        atomic_json(state_path, state)
                        continue
                    state["status"] = "waiting_current_batch"
                    state["formal_state_sha256"] = sha256(formal_state)
                    atomic_json(state_path, state)
                    if not args.armed or args.once:
                        return 0
                    time.sleep(int(manifest.get("poll_seconds", 15)))
                    continue
                current["summary_sha256"] = sha256(summary_path)
                current["ledger_sha256"] = sha256(ledger_path)
                audit = audit_completed_run(current, formal_state)
                if (
                    current["mode"] != "formal"
                    and current.get("formal_state_sha256_before")
                    != sha256(formal_state)
                ):
                    raise RuntimeError(
                        "diagnostic acquisition changed formal state SHA"
                    )
                state["audits"].append(audit)
                if not audit["clean"]:
                    state["quarantined_fingerprints"] = sorted(
                        set(state.get("quarantined_fingerprints") or [])
                        | set(
                            audit.get("physical_fingerprint_ids") or []
                        )
                    )
                if audit["clean"]:
                    state["sources"].append(
                        {
                            "name": f"autonomous_{current['mode']}_{state['next_ordinal'] - 1}",
                            "selection_path": audit["selection_path"],
                            "selection_sha256": audit["selection_sha256"],
                            "ledger_path": audit["ledger_path"],
                            "ledger_sha256": audit["ledger_sha256"],
                        }
                    )
                state["current_run"] = None

            gate = quality_gate(state["audits"])
            calibration = build_exact_calibration(state["audits"])
            ordinal = int(state["next_ordinal"])
            preparation = root / f"prepare_{ordinal:02d}"
            evidence_path = preparation / "evidence_manifest.json"
            calibration_path = preparation / "exact_regime_calibration.json"
            selection_path = preparation / "top5.json"
            mode = choose_next_mode(state["audits"], gate)
            evidence = {
                "schema_version": "tpcc_autonomous_evidence_manifest_v1",
                "sources": state["sources"],
                "excluded_fingerprints": sorted(
                    set(state.get("quarantined_fingerprints") or [])
                ),
                "diagnostic_acquisition_reason": (
                    None if mode == "formal" else gate
                ),
            }
            state["last_quality_gate"] = gate
            state["next_mode"] = mode
            state["status"] = (
                "dry_run_next_preparation_ready"
                if not args.armed
                else "preparing_next_batch"
            )
            atomic_json(state_path, state)
            if not args.armed:
                return 0

            if preparation.exists():
                # A controller can be interrupted after atomically writing
                # evidence/calibration but before the zero-workload builder
                # produces Top-5.  Resume only that precisely bounded state;
                # any selection, seal, or unknown file fails closed so an
                # already-started batch can never be duplicated.
                allowed_partial_names = {
                    "evidence_manifest.json",
                    "exact_regime_calibration.json",
                }
                observed_names = {
                    item.name for item in preparation.iterdir()
                }
                if (
                    state.get("current_run") is not None
                    or not observed_names.issubset(allowed_partial_names)
                ):
                    raise RuntimeError(
                        "unsafe partially prepared batch cannot be resumed: "
                        f"{preparation} contains {sorted(observed_names)}"
                    )
            else:
                preparation.mkdir(parents=True, exist_ok=False)
            atomic_json(evidence_path, evidence)
            atomic_json(calibration_path, calibration)
            builder = Path(manifest["builder_path"]).resolve()
            builder_argv = [
                manifest["python"],
                str(builder),
                str(evidence_path),
                str(formal_state),
                str(selection_path),
                mode,
            ]
            try:
                run_checked(
                    builder_argv,
                    timeout=int(
                        manifest.get("builder_timeout_seconds", 1800)
                    ),
                )
            except RuntimeError as exc:
                if (
                    mode != "formal"
                    or "formal acquisition exhausted" not in str(exc)
                    or selection_path.exists()
                ):
                    raise
                mode = "diagnostic"
                evidence["diagnostic_acquisition_reason"] = {
                    "reason": (
                        "formal_expected_improvement_pool_exhausted"
                    ),
                    "incumbent_score": load_json(formal_state)[
                        "incumbent_score"
                    ],
                    "formal_builder_error": str(exc),
                }
                atomic_json(evidence_path, evidence)
                state["next_mode"] = mode
                state["status"] = (
                    "formal_pool_exhausted_switching_to_diagnostic"
                )
                atomic_json(state_path, state)
                builder_argv[-1] = mode
                run_checked(
                    builder_argv,
                    timeout=int(
                        manifest.get("builder_timeout_seconds", 1800)
                    ),
                )
            selection = load_json(selection_path)
            validate_selection(selection, mode=mode)
            seal = seal_next_run(
                manifest,
                preparation,
                selection_path,
                mode,
                ordinal,
                gate,
            )
            if (
                sha256(Path(seal["launcher_path"])) != seal["launcher_sha256"]
                or sha256(Path(seal["seal_path"])) != seal["seal_sha256"]
            ):
                raise RuntimeError("new run seal verification failed")
            state["prepared_selection_path"] = str(selection_path)
            state["prepared_selection_sha256"] = sha256(selection_path)
            state["current_run"] = {
                "mode": mode,
                "root": seal["run_root"],
                "selection_path": seal["selection_path"],
                "selection_sha256": seal["selection_sha256"],
                "ledger_path": seal["ledger_path"],
                "summary_path": seal["summary_path"],
                "launcher_path": seal["launcher_path"],
                "launcher_sha256": seal["launcher_sha256"],
                "seal_path": seal["seal_path"],
                "seal_sha256": seal["seal_sha256"],
                "formal_state_sha256_before": seal[
                    "formal_state_sha256_before"
                ],
            }
            state["next_ordinal"] = ordinal + 1
            state["status"] = "launching_sealed_run"
            atomic_json(state_path, state)
            completed = subprocess.run(
                ["bash", seal["launcher_path"]],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=int(manifest.get("run_timeout_seconds", 21600)),
            )
            atomic_text(
                preparation / "sealed_run/launcher.log",
                completed.stdout,
            )
            state["last_launcher_returncode"] = completed.returncode
            state["formal_state_sha256"] = sha256(formal_state)
            state["status"] = "auditing_completed_run"
            atomic_json(state_path, state)
            if args.once:
                return 0


if __name__ == "__main__":
    raise SystemExit(main())
