#!/usr/bin/env python3
"""JOB strict runner with an explicit sealed-selection binding.

The v10 runner resolved the selection through ``sys.argv[1]``.  Formal runs
import the runner and call ``run(args)``, so that process-global value is not a
valid authority.  This adapter binds the selection from ``args.selection``
before parameter application and keeps it immutable for the whole batch.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Mapping


DEPENDENCY_NAME = "run_job_round8_strict_serial_base_v10_sealed_explain.py"
_ACTIVE_SELECTION_PATH: Path | None = None
_ACTIVE_SELECTION_SHA256: str | None = None


def _load_v10() -> ModuleType:
    configured = os.environ.get("JOB_ROUND8_BASE_V10_RUNNER")
    path = (
        Path(configured).resolve()
        if configured
        else Path(__file__).with_name(DEPENDENCY_NAME).resolve()
    )
    if not path.is_file():
        raise RuntimeError(f"missing sealed JOB v10 dependency: {path}")
    spec = importlib.util.spec_from_file_location("job_base_v10_dependency", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sealed JOB v10 dependency")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v10 = _load_v10()
for _name in dir(v10):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(v10, _name))


def _explicit_sealed_explain_binding(costs: Mapping[str, float]) -> dict[str, Any]:
    if _ACTIVE_SELECTION_PATH is None or _ACTIVE_SELECTION_SHA256 is None:
        raise RuntimeError("explicit sealed EXPLAIN selection was not bound by run(args)")
    selection_path = _ACTIVE_SELECTION_PATH
    if (
        not selection_path.is_absolute()
        or not selection_path.is_file()
        or hashlib.sha256(selection_path.read_bytes()).hexdigest()
        != _ACTIVE_SELECTION_SHA256
    ):
        raise RuntimeError("explicit sealed EXPLAIN selection changed after binding")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if not isinstance(selection, Mapping):
        raise RuntimeError("sealed EXPLAIN selection is not an object")
    candidates = selection.get("top5") or selection.get("candidates") or []
    if not isinstance(candidates, list):
        raise RuntimeError("sealed EXPLAIN candidate list is invalid")
    canonical = json.dumps(dict(costs), sort_keys=True, separators=(",", ":"))
    matches = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        raw_params = candidate.get("candidate_params") or candidate.get("optimized_params")
        if not isinstance(raw_params, Mapping):
            continue
        try:
            candidate_costs = v10._canonical_costs(raw_params)
        except (TypeError, ValueError, RuntimeError):
            continue
        encoded = json.dumps(candidate_costs, sort_keys=True, separators=(",", ":"))
        if encoded == canonical:
            matches.append(candidate)
    if len(matches) != 1:
        raise RuntimeError("exact candidate vector must bind to exactly one sealed EXPLAIN row")
    candidate = matches[0]
    fingerprint = candidate.get("three_level_fingerprint") or {}
    identifier = str(fingerprint.get("id") or "")
    plan_level = str(fingerprint.get("plan_level") or "")
    if (
        not identifier.startswith("three-level-sha256:")
        or not plan_level
        or candidate.get("boundary_corner") is not False
    ):
        raise RuntimeError("sealed EXPLAIN fingerprint contract mismatch")
    return {
        "contract": "job_v55_preexecution_sealed_explain_binding_v1",
        "binding_implementation": "job_v57_explicit_args_selection_v1",
        "selection_path": str(selection_path),
        "selection_sha256": _ACTIVE_SELECTION_SHA256,
        "candidate_parameter_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "three_level_fingerprint": copy.deepcopy(dict(fingerprint)),
        "fingerprint_id": identifier,
        "plan_level": plan_level,
        "post_run_pg_stat_statements_seed_used": False,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    global _ACTIVE_SELECTION_PATH, _ACTIVE_SELECTION_SHA256
    selection_path = Path(args.selection).resolve()
    if not selection_path.is_file():
        raise RuntimeError("explicit sealed EXPLAIN selection is missing")
    selection_sha = hashlib.sha256(selection_path.read_bytes()).hexdigest()
    if _ACTIVE_SELECTION_PATH is not None:
        raise RuntimeError("sealed selection binding leaked across JOB batches")
    _ACTIVE_SELECTION_PATH = selection_path
    _ACTIVE_SELECTION_SHA256 = selection_sha
    v10._sealed_explain_binding = _explicit_sealed_explain_binding
    # Robust formal adapters replace this wrapper attribute after import.
    # Propagate that hook into v10 before it delegates to the frozen runner.
    v10.decide_state_update = globals()["decide_state_update"]
    try:
        return dict(v10.run(args))
    finally:
        _ACTIVE_SELECTION_PATH = None
        _ACTIVE_SELECTION_SHA256 = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("selection", type=Path)
    parser.add_argument("replay_root", type=Path)
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--exclusion", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--accepted-target", type=int, default=10)
    parser.add_argument("--diagnostic-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(
            json.dumps(
                v10.frozen.preflight(
                    args.selection.resolve(),
                    args.authority_root.resolve(),
                    args.observations.resolve(),
                    args.exclusion.resolve(),
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
