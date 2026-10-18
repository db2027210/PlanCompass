#!/usr/bin/env python3
"""Resume TPC-H v49 with automatic probe-only trust evidence acquisition.

V49 can stop before workload when fewer than three trusted exact-plan regimes
remain in the expanded candidate pool.  This adapter keeps every existing
quality threshold, admits only the builder's explicit probe5 evidence contract,
and commits a clean state-neutral batch directly to automatic re-exploration.
It never promotes an unknown or one-observation regime to trusted.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, Mapping


V49 = Path("/tmp/tpch_v49_preparing_cycle_continuation.py")
LOCAL_V49 = Path(__file__).with_name("tpch_v49_preparing_cycle_continuation.py")
EXPECTED_V49_SHA256 = (
    "40231d64d94c98af5d98e28b9e27c82933aa46f9a36d4a4113d2cb7da747c405"
)
PATCHED_BUILDER = Path(
    "/tmp/build_tpch_autonomous_successor_top5_v11_evidence_acquisition.py"
)
EXPECTED_PATCHED_BUILDER_SHA256 = (
    "8447df5251c53b823bca21e89cc345ea903e333f9688468627c20bfd811d7b49"
)
BOUNDARY_VALIDATOR = Path("/tmp/validate_tpch_v50_liveness_fix_boundary.py")
LOCAL_BOUNDARY_VALIDATOR = Path(__file__).with_name(BOUNDARY_VALIDATOR.name)
EXPECTED_BOUNDARY_VALIDATOR_SHA256 = (
    "cf957dca63f22edc7b69431c7a749b5e55286852fee2e5d50e80ddec7f1e3d31"
)
CONTRACT = "tpch_v50_probe_only_trusted_evidence_acquisition_v1"
ACQUISITION_SELECTION_CONTRACT = (
    "exact_structural_plan_regime_stability_probe5_diagnostic_v1"
)
REEXPLORE_PHRASE = (
    "probe-only trusted-evidence acquisition clean5 requires automatic "
    "reexploration"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_v49() -> Any:
    path = V49 if V49.is_file() else LOCAL_V49
    if not path.is_file() or sha256(path) != EXPECTED_V49_SHA256:
        raise RuntimeError("v49 controller SHA mismatch")
    specification = importlib.util.spec_from_file_location("tpch_v50_v49", path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import v49 controller: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def import_boundary_validator() -> Any:
    path = (
        BOUNDARY_VALIDATOR
        if BOUNDARY_VALIDATOR.is_file()
        else LOCAL_BOUNDARY_VALIDATOR
    )
    if not path.is_file() or sha256(path) != EXPECTED_BOUNDARY_VALIDATOR_SHA256:
        raise RuntimeError("v50 liveness-fix boundary validator SHA mismatch")
    specification = importlib.util.spec_from_file_location(
        "tpch_v50_boundary_validator",
        path,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import boundary validator: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def strict_probe5_selection(selection: Mapping[str, Any]) -> bool:
    candidates = selection.get("top5") or []
    update_fields = (
        "accepted_update_allowed",
        "incumbent_update_allowed",
        "range_update_allowed",
        "gradient_update_allowed",
    )
    return bool(
        selection.get("selection_contract") == ACQUISITION_SELECTION_CONTRACT
        and selection.get("trusted_evidence_acquisition_mode") is True
        and selection.get("automatic_reexploration_required_after_clean5")
        is True
        and selection.get("diagnostic_only") is True
        and selection.get("formal_continuation") is False
        and selection.get("formal_admission_from_this_selection_allowed")
        is False
        and selection.get("unknown_plan_regime_trusted_allowed") is False
        and int(selection.get("maximum_real_replay_invocations", -1)) == 5
        and int(selection.get("trusted_slots", -1)) == 0
        and int(selection.get("probe_slots", -1)) == 5
        and selection.get("qps_used_for_inner_scoring") is False
        and all(selection.get(field) is False for field in update_fields)
        and isinstance(candidates, list)
        and len(candidates) == 5
        and all(
            isinstance(candidate, dict)
            and candidate.get("selection_role") == "probe"
            and candidate.get("trusted") is False
            and candidate.get("uncertain_probe") is True
            and candidate.get("trusted_evidence_acquisition_probe") is True
            and candidate.get("diagnostic_only") is True
            and candidate.get("qps_used_for_inner_scoring") is False
            and all(candidate.get(field) is False for field in update_fields)
            for candidate in candidates
        )
    )


def install_probe5_clean_evidence(v48: Any) -> None:
    """Reuse v48's strict clean5 validator after a role-only audit view."""
    original_clean5 = v48.clean5_evidence
    original_load = v48.load

    def clean5_with_probe_only_acquisition(
        selection: Path,
        run_root: Path,
        accepted: int,
        expected_resume_state_sha256: str,
    ) -> Dict[str, Any]:
        payload = original_load(selection)
        if payload.get("selection_contract") != ACQUISITION_SELECTION_CONTRACT:
            return original_clean5(
                selection,
                run_root,
                accepted,
                expected_resume_state_sha256,
            )
        if not strict_probe5_selection(payload):
            raise RuntimeError("probe-only evidence acquisition contract mismatch")

        selection_resolved = selection.resolve()
        summary_resolved = (run_root / "diagnostic_summary.json").resolve()

        def translated_load(path: Path) -> Dict[str, Any]:
            value = original_load(path)
            resolved = Path(path).resolve()
            if resolved == selection_resolved:
                value = copy.deepcopy(value)
                for index, candidate in enumerate(value["top5"]):
                    candidate["selection_role"] = (
                        "trusted" if index < 3 else "probe"
                    )
            elif resolved == summary_resolved:
                value = copy.deepcopy(value)
                for index, result in enumerate(value.get("successful") or []):
                    result["selection_role"] = (
                        "trusted" if index < 3 else "probe"
                    )
            return value

        v48.load = translated_load
        try:
            evidence = original_clean5(
                selection,
                run_root,
                accepted,
                expected_resume_state_sha256,
            )
        finally:
            v48.load = original_load
        evidence["selection_roles"] = ["probe"] * 5
        evidence["trusted_evidence_acquisition"] = True
        evidence["formal_state_mutated"] = False
        return evidence

    v48.clean5_evidence = clean5_with_probe_only_acquisition


def install_builder_authority(v48: Any) -> Path:
    if not PATCHED_BUILDER.is_file():
        local_builder = Path(__file__).with_name(PATCHED_BUILDER.name)
        if local_builder.is_file():
            v48.STABILITY_ACQUISITION_BUILDER = local_builder
            builder_path = local_builder
        else:
            raise RuntimeError(f"patched builder is missing: {PATCHED_BUILDER}")
    else:
        builder_path = PATCHED_BUILDER
    if sha256(builder_path) != EXPECTED_PATCHED_BUILDER_SHA256:
        raise RuntimeError("patched builder SHA mismatch")

    old_builder = v48.STABILITY_ACQUISITION_BUILDER
    v48.EXPECTED.pop(old_builder, None)
    v48.STABILITY_ACQUISITION_BUILDER = builder_path
    v48.EXPECTED[builder_path] = EXPECTED_PATCHED_BUILDER_SHA256
    return builder_path


def install_algorithm(v48: Any, v41: Any) -> None:
    builder_path = v48.STABILITY_ACQUISITION_BUILDER
    if (
        not builder_path.is_file()
        or v48.EXPECTED.get(builder_path) != EXPECTED_PATCHED_BUILDER_SHA256
        or sha256(builder_path) != EXPECTED_PATCHED_BUILDER_SHA256
    ):
        raise RuntimeError("v50 builder authority is not sealed")
    install_probe5_clean_evidence(v48)
    failures = tuple(v48.DETERMINISTIC_FAILURES["exact_repeat"])
    if REEXPLORE_PHRASE not in failures:
        v48.DETERMINISTIC_FAILURES["exact_repeat"] = (
            *failures,
            REEXPLORE_PHRASE,
        )

    original_configure = v41.configure_controller

    def configure_with_evidence_acquisition(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        v26, common = original_configure(*args, **kwargs)
        original_build_probe = v26.build_probe
        original_exact_repeat = v26.build_exact_repeat

        def probe_with_crash_safe_reuse(
            common_module: Any,
            cycle_root: Path,
            previous_selection: Path,
            previous_run: Path,
            resume_state: Path,
            accepted: int,
            environment: Mapping[str, str],
        ) -> Path:
            first = cycle_root / "probe_top5.a.json"
            second = cycle_root / "probe_top5.b.json"
            if first.exists() or second.exists():
                if not (
                    first.is_file()
                    and second.is_file()
                    and first.read_bytes() == second.read_bytes()
                ):
                    raise RuntimeError("partial v50 probe selection pair")
                selection = v26.load(first)
                expansion = (selection.get("source_evidence") or {}).get(
                    "expansion"
                ) or {}
                expansion_path = Path(str(expansion.get("path") or ""))
                try:
                    expansion_path.resolve().relative_to(cycle_root.resolve())
                except ValueError as exc:
                    raise RuntimeError(
                        "v50 reused probe expansion escaped cycle root"
                    ) from exc
                if not (
                    strict_probe5_selection(selection)
                    and int(selection.get("accepted_count_start", -1)) == accepted
                    and int(selection.get("inherited_accepted_count", -1))
                    == accepted
                    and expansion_path.is_file()
                    and sha256(expansion_path) == expansion.get("sha256")
                    and not (cycle_root / "diagnostic_probe").exists()
                ):
                    raise RuntimeError("v50 probe selection reuse binding mismatch")
                return first
            return original_build_probe(
                common_module,
                cycle_root,
                previous_selection,
                previous_run,
                resume_state,
                accepted,
                environment,
            )

        def exact_repeat_or_reexplore(
            common_module: Any,
            cycle_root: Path,
            probe: Path,
            probe_run: Path,
            accepted: int,
            environment: Mapping[str, str],
        ) -> Path:
            selection = v26.load(probe)
            if selection.get("selection_contract") == ACQUISITION_SELECTION_CONTRACT:
                if not strict_probe5_selection(selection):
                    raise RuntimeError(
                        "probe-only evidence acquisition semantic mismatch"
                    )
                failure_log = cycle_root / "exact_builder_a.log"
                if failure_log.exists():
                    raise RuntimeError(
                        "trusted-evidence reexploration log already exists"
                    )
                failure_log.write_text(REEXPLORE_PHRASE + "\n", encoding="utf-8")
                raise RuntimeError(REEXPLORE_PHRASE)
            return original_exact_repeat(
                common_module,
                cycle_root,
                probe,
                probe_run,
                accepted,
                environment,
            )

        v26.build_probe = probe_with_crash_safe_reuse
        v26.build_exact_repeat = exact_repeat_or_reexplore
        return v26, common

    v41.configure_controller = configure_with_evidence_acquisition


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--resume", action="store_true")
    parser.add_argument("--expected-controller-state-sha256", required=True)
    parser.add_argument("--expected-formal-state-sha256", required=True)
    parser.add_argument("--expected-counter-sha256", required=True)
    parser.add_argument("--expected-cycle", type=int, required=True)
    args = parser.parse_args()

    v49 = import_v49()
    boundary_validator = import_boundary_validator()
    v48 = v49.import_v48()
    # Install the SHA-bound builder before v48 verifies and configures v41.
    install_builder_authority(v48)
    v41 = v48.verified_v41()
    install_algorithm(v48, v41)
    original_builder = v48.STABILITY_ACQUISITION_BUILDER
    if original_builder not in v48.EXPECTED:
        raise RuntimeError("v50 builder authority was not installed")

    paths = v48.current_paths(v41)
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    with paths["lock"].open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        boundary = boundary_validator.validate_boundary(
            v49,
            v48,
            v41,
            paths,
            expected_state_sha=args.expected_controller_state_sha256,
            expected_formal_sha=args.expected_formal_state_sha256,
            expected_counter_sha=args.expected_counter_sha256,
            expected_cycle=args.expected_cycle,
        )
        boundary["preflight"].update(
            {
                "contract": CONTRACT,
                "patched_builder": str(original_builder.resolve()),
                "patched_builder_sha256": EXPECTED_PATCHED_BUILDER_SHA256,
                "unknown_regimes_remain_probe_only": True,
                "credible_probe_score_threshold_relaxed": False,
                "evidence_acquisition_physical_cap": 5,
                "evidence_acquisition_state_neutral": True,
                "next_action": (
                    "probe5_clean5_then_automatic_reexploration_then_"
                    "normal_gate_or_formal"
                ),
            }
        )
        if args.preflight_only:
            print(json.dumps(boundary["preflight"], sort_keys=True), flush=True)
            return 0
        return v49.resume_boundary(v48, v41, paths, boundary)


if __name__ == "__main__":
    raise SystemExit(main())
