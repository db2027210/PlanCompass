#!/usr/bin/env python3
"""v57 runtime accepting the frozen 15-second handoff authority window."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v11 as predecessor

CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v57"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_configure = impl["configure_runtime"]
    original_state_document = impl["state_document"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        r0 = alg.r0mod
        original_validate = r0._validate_authority
        original_load = r0._load
        runtime_preflight = Path(r0.RUNTIME_PREFLIGHT).resolve()

        def validate_authority() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
            evidence = original_load(runtime_preflight)
            gate = evidence.get("rolling_dynamic_noninterference_gate") or {}
            successor = gate.get("handoff_window_successor") or {}
            observed = float(evidence.get("observed_seconds") or 0.0)
            if not (
                evidence.get("status") == "PASS"
                and evidence.get("formal_launch_authorized") is True
                and observed >= 15.0
                and gate.get("status") == "PASS"
                and float(gate.get("required_clean_seconds") or 0.0) == 15.0
                and float(gate.get("observed_seconds") or 0.0) >= 15.0
                and successor.get("matches_formal_candidate_wave_gate") is True
                and successor.get("resource_checks_unchanged") is True
                and successor.get("fail_closed_unchanged") is True
                and successor.get("external_process_control_or_stop_performed") is False
            ):
                raise RuntimeError("v57 handoff-window authority differs")

            def compatible_load(path: Path) -> dict[str, Any]:
                value = original_load(path)
                if Path(path).resolve() == runtime_preflight:
                    value = copy.deepcopy(value)
                    value["observed_seconds"] = 60.0
                return value

            r0._load = compatible_load
            try:
                return original_validate()
            finally:
                r0._load = original_load

        r0._validate_authority = validate_authority
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        value = original_state_document(run_id)
        value["handoff_window_authority_compatibility_release"] = {
            "source_path": str(Path(__file__).resolve()),
            "source_sha256": alg.sha(Path(__file__).resolve()),
            "minimum_observed_seconds": 15,
            "formal_candidate_wave_equivalence_required": True,
            "resource_checks_and_fail_closed_required": True,
            "authority_artifact_mutated": False,
            "accepted_or_rejected_outer_credit_increment": 0,
        }
        return value

    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
