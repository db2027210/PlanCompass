#!/usr/bin/env python3
"""Top4 B loader for PG-compatible persistent rejection memory."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mysql_workload_adapter import run_hybench as adapter_run_hybench


PREDECESSOR = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v2.py"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v12"
PHYSICAL_REPLAY_PARALLELISM = 4

_source = PREDECESSOR.read_text(encoding="utf-8")
if _source.count("if len(rows) != 2:") != 1 or _source.count(" / 2.0") != 4:
    raise RuntimeError("Top4 B predecessor two-lane baseline block differs")
_source = _source.replace(
    "if len(rows) != 2:",
    "if len(rows) != PHYSICAL_REPLAY_PARALLELISM:",
)
_source = _source.replace(" / 2.0", " / float(len(rows))")
_impl: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_b_xp_top4_async_v31_impl",
    "__package__": "scripts",
}
exec(compile(_source, str(Path(__file__).resolve()), "exec"), _impl)
_impl["PHYSICAL_REPLAY_PARALLELISM"] = PHYSICAL_REPLAY_PARALLELISM
_original_configure_runtime = _impl["configure_runtime"]
_original_validate_resume_source = _impl["validate_resume_source"]
_implementation_main = _impl["main"]


def run_hybench_adapter_compatible(*args: Any, **kwargs: Any) -> dict[str, Any]:
    control = kwargs.get("formal_control")
    if control is not None:
        control = dict(control)
        requested = int(control.get("graceful_quiescence_seconds", 0))
        if requested != 300:
            raise RuntimeError(
                "Top4 B predecessor graceful-quiescence authority differs: "
                f"{requested}"
            )
        control["graceful_quiescence_seconds"] = 120
        kwargs["formal_control"] = control
    return adapter_run_hybench(*args, **kwargs)


def configure_runtime() -> dict[str, Any]:
    contract = _original_configure_runtime()
    raw = os.environ.get("MYSQL_XP4_AUTHORITY_ROOT", "")
    authority_root = Path(raw).expanduser().resolve()
    history = Path(_impl["HISTORY"]).resolve()
    required = {
        "preflight": authority_root / "transition_preflight.json",
        "prepared": authority_root / "prepared_binding.json",
        "accounts": authority_root / "benchmark_accounts_binding.json",
    }
    if (
        not raw or history not in authority_root.parents or authority_root.is_symlink()
        or any(not path.is_file() or path.is_symlink() for path in required.values())
    ):
        raise RuntimeError("current XP4 authority root differs")
    alg = _impl["alg"]
    alg.r0mod.PREFLIGHT = required["preflight"]
    alg.r0mod.RUNTIME_PREFLIGHT = required["preflight"]
    alg.r0mod.PREPARED = required["prepared"]
    alg.r0mod.BENCHMARK_ACCOUNTS = required["accounts"]
    alg.run_hybench = run_hybench_adapter_compatible
    return contract


def validate_resume_source(source: Path) -> dict[str, Any]:
    resume = _original_validate_resume_source(source)
    raw = os.environ.get("MYSQL_B_VERIFIED_BASELINE", "")
    if not raw:
        return resume
    baseline_path = Path(raw).expanduser().resolve()
    history = Path(_impl["HISTORY"]).resolve()
    if history not in baseline_path.parents or baseline_path.is_symlink():
        raise RuntimeError("verified B baseline path differs")
    _impl["_verified_sidecar"](baseline_path)
    baseline = _impl["alg"].load(baseline_path)
    rows = baseline.get("measurements")
    scene = _impl["SCENES"][0]
    expected_workers = {
        f"{'xp_cpu' if scene == 'hybench_xp_cpu' else 'xp_io'}_w{index}"
        for index in range(1, PHYSICAL_REPLAY_PARALLELISM + 1)
    }
    expected_params = resume["state"]["params_by_scene"][scene]
    if (
        baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
        or not isinstance(rows, Mapping)
        or set(rows) != expected_workers
        or any(
            row.get("scene") != scene
            or int(row.get("rank") or 0) != 0
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or row.get("params") != expected_params
            for row in rows.values()
        )
    ):
        raise RuntimeError("verified four-lane B baseline differs")
    failed_state_path = baseline_path.parents[1] / "run_state.json"
    _impl["_verified_sidecar"](failed_state_path)
    failed = _impl["alg"].load(failed_state_path)
    if (
        failed.get("algorithm") != "B"
        or failed.get("async_scene") != scene
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or failed.get("error") != f"RuntimeError: {scene} independent baseline lane count differs"
        or int(failed.get("outer_attempt") or 0) != 0
        or int((failed.get("accepted_rounds") or {}).get(scene) or 0) != 0
        or int((failed.get("rejected_outer_attempts") or {}).get(scene) or 0) != 0
    ):
        raise RuntimeError("verified B baseline technical-continuation state differs")
    resume["baseline_path"] = baseline_path
    resume["baseline_rows"] = copy.deepcopy(dict(rows))
    resume["baseline_sha256"] = _impl["alg"].sha(baseline_path)
    resume["verified_baseline_failure_state"] = {
        "path": str(failed_state_path),
        "sha256": _impl["alg"].sha(failed_state_path),
        "accepted_or_outer_credit": False,
    }
    return resume


def install() -> None:
    _impl["CONTRACT"] = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
    _impl["COORDINATION_CONTRACT"] = CONTRACT_ROOT / "coordination_contract.json"
    _impl["configure_runtime"] = configure_runtime
    _impl["validate_resume_source"] = validate_resume_source
    _impl["REUSE_VERIFIED_BASELINE"] = bool(
        os.environ.get("MYSQL_B_VERIFIED_BASELINE", "")
    )


def main() -> int:
    install()
    return int(_implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
