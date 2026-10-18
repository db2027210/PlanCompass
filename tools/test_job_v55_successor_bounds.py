#!/usr/bin/env python3
"""Offline tests for JOB v55 bounded successor-to-replay handoff."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "tools/job_fresh10_target10_autonomous_v55.py"


def load_controller():
    spec = importlib.util.spec_from_file_location("job_v55_bounds_test", CONTROLLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_fakes(
    module: Any,
    *,
    timeout: bool = False,
    third_call: bool = False,
    produce_artifact: bool = True,
) -> list[int]:
    observed_timeouts: list[int] = []

    def fake_run_logged(
        command: Sequence[str],
        log_path: Path,
        *,
        timeout_seconds: int,
        allowed_returncodes: Sequence[int] = (0,),
        cwd: Path | None = None,
    ) -> int:
        del allowed_returncodes, cwd
        observed_timeouts.append(timeout_seconds)
        if timeout:
            raise subprocess.TimeoutExpired(list(command), timeout_seconds)
        target = Path(command[list(command).index("--output") + 1])
        if produce_artifact:
            target.write_text('{"training": true}\n', encoding="utf-8")
        log_path.write_text("offline generator stub\n", encoding="utf-8")
        return 0

    def fake_generate(cycle_root: Path, replay_attempt: int, **kwargs: Any) -> Path:
        del kwargs
        fresh_root = cycle_root / f"fresh_discovery_{replay_attempt:02d}"
        for index, name in enumerate(module.SUCCESSOR_GENERATOR_SLOTS):
            module.implementation.v49.run_logged(
                ["python", "generator", "--output", str(fresh_root / name)],
                fresh_root / f"generator_{index}.log",
                timeout_seconds=7200,
            )
        if third_call:
            module.implementation.v49.run_logged(
                [
                    "python",
                    "generator",
                    "--output",
                    str(fresh_root / module.SUCCESSOR_GENERATOR_SLOTS[0]),
                ],
                fresh_root / "generator_third.log",
                timeout_seconds=7200,
            )
        output = fresh_root / "top5.json"
        second = fresh_root / "top5.second.json"
        output.write_text('{"top5": []}\n', encoding="utf-8")
        second.write_bytes(output.read_bytes())
        return output

    module._ORIGINAL_RUN_LOGGED = fake_run_logged
    module._ORIGINAL_GENERATE_FRESH_DISCOVERY = fake_generate
    return observed_timeouts


def test_success_is_two_bounded_generators_then_immediate_handoff() -> None:
    module = load_controller()
    observed_timeouts = install_fakes(module)
    handoff_seen = False

    def fake_obtain(initial_discovery: Path, cycle_root: Path, **kwargs: Any):
        nonlocal handoff_seen
        del cycle_root, kwargs
        handoff = module.implementation.v49.read_json(
            initial_discovery.parent / "v55_first_replay_handoff.json"
        )
        handoff_seen = handoff["status"] == "replay_handoff_started"
        return initial_discovery, initial_discovery.parent, 1

    module._ORIGINAL_OBTAIN_CLEAN_DISCOVERY = fake_obtain
    with tempfile.TemporaryDirectory() as directory:
        cycle_root = Path(directory) / "cycle_001"
        selection = module._bounded_generate_fresh_discovery(cycle_root, 1)
        budget = module.implementation.v49.read_json(
            selection.parent / "v55_successor_budget.json"
        )
        assert budget["status"] == "ready_for_first_replay"
        assert [item["status"] for item in budget["invocations"]] == [
            "complete",
            "complete",
        ]
        assert observed_timeouts == [1800, 1800]
        module._guarded_obtain_clean_discovery(selection, cycle_root)
        assert handoff_seen


def test_third_generator_invocation_fails_closed() -> None:
    module = load_controller()
    observed_timeouts = install_fakes(module, third_call=True)
    with tempfile.TemporaryDirectory() as directory:
        try:
            module._bounded_generate_fresh_discovery(Path(directory) / "cycle_001", 1)
        except module.implementation.v49.FailClosed:
            pass
        else:
            raise AssertionError("third successor generator invocation must fail closed")
    assert observed_timeouts == [1800, 1800]


def test_generator_timeout_is_sealed_and_not_retried() -> None:
    module = load_controller()
    observed_timeouts = install_fakes(module, timeout=True)
    with tempfile.TemporaryDirectory() as directory:
        cycle_root = Path(directory) / "cycle_001"
        try:
            module._bounded_generate_fresh_discovery(cycle_root, 1)
        except module.implementation.v49.FailClosed:
            pass
        else:
            raise AssertionError("generator timeout must fail closed")
        budget_path = (
            cycle_root / "fresh_discovery_01/v55_successor_budget.json"
        )
        budget = module.implementation.v49.read_json(budget_path)
        assert budget["status"] == "timeout"
        assert budget["invocations"][0]["status"] == "timeout"
        try:
            module._bounded_generate_fresh_discovery(cycle_root, 1)
        except module.implementation.v49.FailClosed:
            pass
        else:
            raise AssertionError("sealed timeout must not auto-regenerate")
    assert observed_timeouts == [1800]


def test_generator_without_artifact_is_no_progress_failure() -> None:
    module = load_controller()
    observed_timeouts = install_fakes(module, produce_artifact=False)
    with tempfile.TemporaryDirectory() as directory:
        cycle_root = Path(directory) / "cycle_001"
        try:
            module._bounded_generate_fresh_discovery(cycle_root, 1)
        except module.implementation.v49.FailClosed:
            pass
        else:
            raise AssertionError("zero-artifact generator must fail closed")
        budget = module.implementation.v49.read_json(
            cycle_root / "fresh_discovery_01/v55_successor_budget.json"
        )
        assert budget["status"] == "failed"
        assert "no artifact" in budget["failure"]
    assert observed_timeouts == [1800]


def test_expired_wall_budget_never_starts_generator() -> None:
    module = load_controller()
    observed_timeouts = install_fakes(module)
    with tempfile.TemporaryDirectory() as directory:
        cycle_root = Path(directory) / "cycle_001"
        fresh_root = cycle_root / "fresh_discovery_01"
        fresh_root.mkdir(parents=True)
        budget = module._new_successor_budget(fresh_root, 1)
        budget["started_unix_seconds"] = 1.0
        budget["deadline_unix_seconds"] = 1.0 + module.SUCCESSOR_DISCOVERY_WALL_SECONDS
        module._write_successor_budget(
            fresh_root / "v55_successor_budget.json",
            budget,
        )
        try:
            module._bounded_generate_fresh_discovery(cycle_root, 1)
        except module.implementation.v49.FailClosed:
            pass
        else:
            raise AssertionError("expired wall budget must fail before generation")
        sealed = module.implementation.v49.read_json(
            fresh_root / "v55_successor_budget.json"
        )
        assert sealed["status"] == "timeout"
    assert observed_timeouts == []


def main() -> int:
    test_success_is_two_bounded_generators_then_immediate_handoff()
    test_third_generator_invocation_fails_closed()
    test_generator_timeout_is_sealed_and_not_retried()
    test_generator_without_artifact_is_no_progress_failure()
    test_expired_wall_budget_never_starts_generator()
    print(
        json.dumps(
            {
                "status": "passed",
                "contract": "job_v55_successor_bounded_handoff_v1",
                "tests": 5,
                "generator_cap": 2,
                "per_generator_timeout_seconds": 1800,
                "wall_timeout_seconds": 3720,
                "workload_calls": 0,
                "ssh_calls": 0,
                "mysql_touched": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
