#!/usr/bin/env python3
"""Seal the v39 TPC-C diagnostic-to-formal package without launching it."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict


PROJECT = Path("/home/anonymous/program/resource_adaptive")
PACKAGE = PROJECT / "history/tpcc_v39_plan_regime_absorption_package_20260803"
TOP5 = PACKAGE / "top5.json"
FORMAL = PROJECT / "history/tpcc_final_algorithm_fresh0_v36_20260803/formal_state.json"
BUILDER = PROJECT / "tools/build_tpcc_v39_plan_regime_absorption.py"
RUNNER = PROJECT / "tools/run_tpcc_v39_plan_regime_strict_serial.py"
BASE_RUNNER = PROJECT / "tools/run_tpcc_v36_unique_exact_strict_serial.py"
POLICY = PROJECT / "tools/tpcc_round11_current_correctness_policy.py"
AUDIT = PROJECT / "tools/audit_tpcc_v39_postrun_gate.py"
HANDOFF = PROJECT / "tools/tpcc_v39_postrun_gate_and_formal_handoff.py"
FORMAL_RUNNER = PROJECT / "tools/run_tpcc_formal_target10_batch_v3.py"
TEST = PROJECT / "tests/test_tpcc_v39_plan_regime_absorption_review.py"
BASELINE = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "candidate_measurements/point_20260729_022349_1052396/summary.json"
)
ADAPTIVE = (
    PROJECT
    / "history/tpcc_v38_fresh_unique_package_20260803/runtime/adaptive_replay_policy.py"
)
TARGET = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh0_v36_20260803/autonomous_runs_v39/"
    "autonomous_diagnostic_validation_01"
)
EXPECTED_FORMAL_SHA = "62e3fd40c93c163727367afd20b151c291c3646d63fbe0c5c5e2d32c3160aeb0"
EXPECTED_POLICY_SHA = "e6bb7aefc67eb01e3d9138c7bf36cf93367d87779d58013cad41b2302f9c05f7"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected object: {path}")
    return value


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp.package")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def atomic_text(path: Path, value: str, mode: int = 0o700) -> None:
    temporary = path.with_name(path.name + ".tmp.package")
    temporary.write_text(value, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def main() -> int:
    selection = load_object(TOP5)
    formal = load_object(FORMAL)
    contract = selection.get("v39_plan_regime_absorption_contract") or {}
    identifiers = [
        item["three_level_fingerprint"]["id"] for item in selection.get("top5") or []
    ]
    if (
        contract.get("contract") != "tpcc_v39_clean_probe_plan_regime_absorption_v1"
        or contract.get("five_new_noncorner_fingerprints") is not True
        or contract.get("historical_probe_roles_rewritten") is not False
        or contract.get("promotion_certificate_updates_current_selection_role") is not True
        or contract.get("absorbed_probe_updates_formal_acceptance") is not False
        or contract.get("absorbed_exact_point_promoted_by_certificate") is not True
        or int(contract.get("promotion_evaluation_rows", -1)) != 3
        or int(contract.get("formal_state_update_rows", -1)) != 0
        or (contract.get("promotion_certificate") or {}).get("passed") is not True
        or contract.get("manual_candidate_selection") is not False
        or [item.get("selection_role") for item in selection.get("top5") or []]
        != ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"]
        or len(set(identifiers)) != 5
        or any(item.get("boundary_corner") is not False for item in selection["top5"])
        or sha256(FORMAL) != EXPECTED_FORMAL_SHA
        or int(formal.get("accepted_count", -1)) != 0
        or int(formal.get("formal_physical_call_count", -1)) != 0
        or sha256(POLICY) != EXPECTED_POLICY_SHA
    ):
        raise RuntimeError("TPC-C v39 package preflight failed")

    runtime = PACKAGE / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    sources = {
        RUNNER: RUNNER.name,
        POLICY: POLICY.name,
        ADAPTIVE: ADAPTIVE.name,
        PROJECT / "gradient_replay_metrics.py": "gradient_replay_metrics.py",
        PROJECT / "resource_utility.py": "resource_utility.py",
        PROJECT / "tools/tpcc_remaining_budget_selector.py": "tpcc_remaining_budget_selector.py",
    }
    runtime_pins = []
    for source, name in sources.items():
        target = runtime / name
        shutil.copy2(source, target)
        if sha256(target) != sha256(source):
            raise RuntimeError(f"runtime copy mismatch: {name}")
        runtime_pins.append({"path": str(target), "sha256": sha256(target)})

    replay = TARGET / "top5_real_replay_round16_diagnostic_v1"
    handoff_root = TARGET / "postrun_gate_and_formal_handoff"
    formal_batch_root = TARGET / "formal_batch_01"
    manifest = {
        "schema_version": "tpcc_v39_plan_regime_absorption_activation_manifest_v1",
        "scenario": "tpcc",
        "diagnostic_only": True,
        "maximum_real_workload_invocations": 5,
        "same_rank_retry_allowed": False,
        "technical_call_advances_to_next_unique_rank": True,
        "roles": ["trusted", "trusted", "trusted", "uncertain_probe", "uncertain_probe"],
        "qps_role": "outer_audit_only_not_inner_score",
        "state_mutation_contract": {
            "accepted_update": False,
            "gradient_update": False,
            "incumbent_update": False,
            "range_update": False,
        },
        "algorithm_contract": copy_contract(contract),
        "selection_path": str(TOP5),
        "formal_state_path": str(FORMAL),
        "formal_state_sha256": EXPECTED_FORMAL_SHA,
        "runner_path": str(runtime / RUNNER.name),
        "correctness_policy_path": str(runtime / POLICY.name),
        "correctness_policy_sha256": EXPECTED_POLICY_SHA,
        "python": "/home/anonymous/miniconda3/envs/resource_adaptive/bin/python",
        "baseline_summary_path": str(BASELINE),
        "target_run_root": str(TARGET),
        "target_replay_root": str(replay),
        "postrun_audit_path": str(AUDIT),
        "formal_handoff_path": str(HANDOFF),
        "formal_runner_path": str(FORMAL_RUNNER),
        "formal_handoff_root": str(handoff_root),
        "formal_batch_root": str(formal_batch_root),
        "pins": [
            {"path": str(TOP5), "sha256": sha256(TOP5)},
            {"path": str(BUILDER), "sha256": sha256(BUILDER)},
            {"path": str(RUNNER), "sha256": sha256(RUNNER)},
            {"path": str(BASE_RUNNER), "sha256": sha256(BASE_RUNNER)},
            {"path": str(AUDIT), "sha256": sha256(AUDIT)},
            {"path": str(HANDOFF), "sha256": sha256(HANDOFF)},
            {"path": str(FORMAL_RUNNER), "sha256": sha256(FORMAL_RUNNER)},
            {"path": str(TEST), "sha256": sha256(TEST)},
            {"path": str(FORMAL), "sha256": sha256(FORMAL)},
            {"path": str(BASELINE), "sha256": sha256(BASELINE)},
            *runtime_pins,
        ],
    }
    manifest_path = PACKAGE / "manifest.json"
    atomic_json(manifest_path, manifest)
    manifest_sha = sha256(manifest_path)
    pin_checks = "".join(
        f'test "$(sha256sum "{pin["path"]}" | awk \'{{print $1}}\')" = "{pin["sha256"]}"\n'
        for pin in manifest["pins"]
    )
    launcher = PACKAGE / "launch_diagnostic_then_formal.sh"
    atomic_text(
        launcher,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'test "$(sha256sum "{manifest_path}" | awk \'{{print $1}}\')" = "{manifest_sha}"\n'
        f"{pin_checks}"
        f'test "$(sha256sum "{FORMAL}" | awk \'{{print $1}}\')" = "{EXPECTED_FORMAL_SHA}"\n'
        f'test ! -e "{TARGET}"\n'
        f'mkdir -p "{TARGET}"\n'
        f'cp "{TOP5}" "{TARGET}/top5.json"\n'
        f'export PYTHONPATH="{runtime}:{PROJECT}"\n'
        f'"{manifest["python"]}" -u "{manifest["runner_path"]}" '
        f'"{TARGET}/top5.json" "{replay}" "{BASELINE}"\n'
        f'exec "{manifest["python"]}" -u "{HANDOFF}" '
        f'--selection "{TARGET}/top5.json" '
        f'--ledger "{TARGET}/strict_top5_real_replay_round16_ledger_v1.json" '
        f'--events "{replay}/events.jsonl" --summary "{replay}/summary.json" '
        f'--formal-state "{FORMAL}" --expected-formal-sha256 "{EXPECTED_FORMAL_SHA}" '
        f'--output-root "{handoff_root}" --formal-batch-root "{formal_batch_root}" '
        f'--formal-runner "{FORMAL_RUNNER}" --diagnostic-runner "{manifest["runner_path"]}" '
        f'--baseline "{BASELINE}" --armed\n',
    )
    preflight = PACKAGE / "launch_preflight_only.sh"
    atomic_text(
        preflight,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'test "$(sha256sum "{manifest_path}" | awk \'{{print $1}}\')" = "{manifest_sha}"\n'
        f"{pin_checks}"
        f'test "$(sha256sum "{FORMAL}" | awk \'{{print $1}}\')" = "{EXPECTED_FORMAL_SHA}"\n'
        f'export PYTHONPATH="{runtime}:{PROJECT}"\n'
        f'exec "{manifest["python"]}" -c '
        f"'from pathlib import Path; import run_tpcc_v39_plan_regime_strict_serial as w; "
        f"r=w.load_runtime(); p=Path(\"{TOP5}\"); "
        f"a,g,c,x=r[\"validate_generation_and_top5\"](p,p.parent/\"top5_real_replay_round16_preflight_only\"); "
        f"print(\"TPC-C v39 preflight passed: candidates=\",len(c))'\n",
    )
    package_manifest = {
        "status": "sealed_not_launched",
        "accepted_count": int(formal["accepted_count"]),
        "accepted_target": int(formal["accepted_target"]),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha,
        "launcher_path": str(launcher),
        "launcher_sha256": sha256(launcher),
        "preflight_path": str(preflight),
        "preflight_sha256": sha256(preflight),
        "selection_sha256": sha256(TOP5),
        "runner_sha256": sha256(runtime / RUNNER.name),
        "formal_state_sha256": sha256(FORMAL),
        "workload_calls_added": 0,
    }
    atomic_json(PACKAGE / "package_manifest.json", package_manifest)
    print(json.dumps(package_manifest, sort_keys=True), flush=True)
    return 0


def copy_contract(value: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(value))


if __name__ == "__main__":
    raise SystemExit(main())
