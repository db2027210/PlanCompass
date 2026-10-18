#!/usr/bin/env python3
"""TPC-H v54 controller for missing-or-empty zero-call recovery."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping


REMOTE_V51 = Path("/tmp/tpch_v51_safe_unknown_probe_recovery.py")
LOCAL_V51 = Path(__file__).with_name(REMOTE_V51.name)
EXPECTED_V51_SHA256 = (
    "2a9aaac13bf3af4d9007466c4fec3e606d9aa41fb5a4ca08da60e28dd80a0d91"
)
V54_RUNNER = Path(
    "/tmp/run_tpch_round29_diagnostic_exact_plan_regime_batch_"
    "v54_pinned_03be.py"
)
LOCAL_V54_RUNNER = Path(__file__).with_name(V54_RUNNER.name)
EXPECTED_V54_RUNNER_SHA256 = (
    "c4a1ee538080cb5590a719f4d03045cba394a225f3a5300a1a49d4ea173af86c"
)
CONTRACT = "tpch_v54_missing_or_empty_preworkload_compatibility_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"TPC-H v54 expected JSON object: {path}")
    return value


def import_path(name: str, path: Path) -> Any:
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"cannot import {name}: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def import_v51() -> Any:
    path = REMOTE_V51 if REMOTE_V51.is_file() else LOCAL_V51
    if not path.is_file() or sha256(path) != EXPECTED_V51_SHA256:
        raise RuntimeError("TPC-H v54 v51 controller SHA mismatch")
    return import_path("tpch_v54_v51", path)


def install(v51: Any, compatibility: Any, runner_path: Path) -> None:
    """Install only runner authority and crash-safe selection reuse."""

    if sha256(runner_path) != EXPECTED_V54_RUNNER_SHA256:
        raise RuntimeError("TPC-H v54 compatibility runner SHA mismatch")
    expected_runner_sha = EXPECTED_V54_RUNNER_SHA256
    original_import_v50 = v51.import_v50

    def import_v50() -> Any:
        v50 = original_import_v50()
        original_strict = v50.strict_probe5_selection

        def strict_reusable_selection(selection: Mapping[str, Any]) -> bool:
            if original_strict(selection):
                return True
            try:
                compatibility.validate_safe_unknown_selection(selection)
            except (KeyError, TypeError, ValueError, RuntimeError):
                return False
            return True

        v50.strict_probe5_selection = strict_reusable_selection
        original_import_v49 = v50.import_v49
        v49 = original_import_v49()
        original_import_v48 = v49.import_v48

        def import_v48() -> Any:
            v48 = original_import_v48()
            old_runner = v48.STABILITY_DIAGNOSTIC_RUNNER
            v48.EXPECTED.pop(old_runner, None)
            v48.STABILITY_DIAGNOSTIC_RUNNER = runner_path
            v48.EXPECTED[runner_path] = expected_runner_sha
            return v48

        v49.import_v48 = import_v48
        v50.import_v49 = lambda: v49
        original_import_boundary = v50.import_boundary_validator

        def import_boundary_validator() -> Any:
            validator = original_import_boundary()
            original_validate = validator.validate_boundary

            def validate_boundary(*args: Any, **kwargs: Any) -> dict[str, Any]:
                boundary = original_validate(*args, **kwargs)
                first = boundary.get("existing_probe")
                if first is None:
                    raise RuntimeError("TPC-H v54 existing probe selection is missing")
                first_path = Path(str(first))
                second_path = first_path.with_name("probe_top5.b.json")
                if not (
                    first_path.is_file()
                    and second_path.is_file()
                    and first_path.read_bytes() == second_path.read_bytes()
                ):
                    raise RuntimeError("TPC-H v54 sealed probe pair mismatch")
                selection = load_json(first_path)
                compatibility.validate_safe_unknown_selection(selection)
                preflight = boundary.setdefault("preflight", {})
                preflight.update(
                    {
                        "v54_contract": CONTRACT,
                        "deployed_base_runner_sha256": (
                            compatibility.EXPECTED_V45_SHA256
                        ),
                        "safe_unknown_selection_sha256": sha256(first_path),
                        "safe_unknown_selection_reused": True,
                        "explain_rebuilt": False,
                        "new_workload_calls": 0,
                        "formal_state_mutated": False,
                        "accepted_counter_mutated": False,
                        "runner_compatibility_translation_only": True,
                    }
                )
                return boundary

            validator.validate_boundary = validate_boundary
            return validator

        v50.import_boundary_validator = import_boundary_validator
        return v50

    v51.import_v50 = import_v50
    v51.CONTRACT = CONTRACT


def main() -> int:
    runner_path = V54_RUNNER if V54_RUNNER.is_file() else LOCAL_V54_RUNNER
    if not runner_path.is_file() or sha256(runner_path) != EXPECTED_V54_RUNNER_SHA256:
        raise RuntimeError(f"TPC-H v54 compatibility runner is missing: {runner_path}")
    compatibility = import_path("tpch_v54_runner_compat", runner_path)
    v51 = import_v51()
    install(v51, compatibility, runner_path)
    return int(v51.main())


if __name__ == "__main__":
    raise SystemExit(main())
