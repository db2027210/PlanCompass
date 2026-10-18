#!/usr/bin/env python3
"""Regression checks for formal-matrix runtime argument forwarding."""

from __future__ import annotations

from pathlib import Path

import launch_double_loop_formal_matrix as launcher


def main() -> int:
    vm = {
        "benchmark": "hybench_xp",
        "target": "io",
        "name": "ra-matrix-v15-hyxp-io-r1",
        "exp_id": "A18",
        "repeat_id": 1,
        "mac": "52:54:00:bf:8d:06",
        "ip": "127.0.0.1",
        "cpu": 13,
        "memory_mib": 4096,
        "read_mb": 5.0,
        "write_mb": 0.1,
        "overlay": "/tmp/a18-work-overlay.qcow2",
    }
    hybench_root = Path("/tmp/isolated-hybench-xp-runtime")
    command = launcher.prepare_command(
        vm,
        Path("/tmp/a18-runtime"),
        hybench_root=hybench_root,
        benchbase_max_parallel_workers_per_gather=0,
    )
    position = command.index("--hybench-root")
    assert command[position + 1] == str(hybench_root)
    assert command.count("--hybench-root") == 1
    parallel_position = command.index("--benchbase-max-parallel-workers-per-gather")
    assert command[parallel_position + 1] == "0"
    resume = launcher.formal_command(
        {**vm, "target": "none"},
        Path("/tmp/e13-runtime"),
        Path("/tmp/e13-run"),
        resume_calibration_dir="/tmp/current-top15",
        resume_model_training_seed_dir="/tmp/old-support",
        resume_independent_exact_training_seed_dir="/tmp/same-campaign-repeat",
        resume_model_training_seed_include_candidates=True,
    )
    exact_position = resume.index("--independent-exact-training-seed-dir")
    assert resume[exact_position + 1] == "/tmp/same-campaign-repeat"
    assert "--model-training-seed-include-candidates" in resume
    retrained = launcher.formal_command(
        {**vm, "target": "memory"},
        Path("/tmp/a13-runtime"),
        Path("/tmp/a13-run"),
        model_training_seed_dir="/tmp/a13-independent-top15",
        model_training_seed_include_candidates=True,
    )
    seed_position = retrained.index("--model-training-seed-dir")
    assert retrained[seed_position + 1] == "/tmp/a13-independent-top15"
    assert "--model-training-seed-include-candidates" in retrained
    assert "resume_double_loop_grid_from_top15.py" not in " ".join(retrained)
    # Formal jobs use a soft preflight quality band and a tighter online band.
    # The online failure budget remains non-blocking while experiments are in
    # the algorithm-iteration phase, but the decision-tree may expand real K
    # from 15 up to 30 when the model still needs evidence.
    assert retrained[retrained.index("--model-quality-max-relative-error-p95") + 1] == "20.0"
    assert retrained[retrained.index("--model-quality-max-relative-error-max") + 1] == "50.0"
    assert retrained[retrained.index("--model-quality-min-r2") + 1] == "-1000000000.0"
    assert retrained[retrained.index("--model-quality-ranking-inversion-threshold") + 1] == "1.0"
    assert retrained[
        retrained.index("--model-quality-online-max-relative-error-p95") + 1
    ] == "2.0"
    assert retrained[
        retrained.index("--model-quality-online-max-relative-error-max") + 1
    ] == "10.0"
    assert retrained[retrained.index("--model-quality-online-min-r2") + 1] == "0.0"
    assert retrained[
        retrained.index("--model-quality-online-ranking-inversion-threshold") + 1
    ] == "0.80"
    assert retrained[retrained.index("--adaptive-topk-min-k") + 1] == "15"
    assert retrained[retrained.index("--real-top-k") + 1] == "30"
    try:
        launcher.formal_command(
            {**vm, "target": "memory"},
            Path("/tmp/a13-runtime"),
            Path("/tmp/a13-run"),
            model_training_seed_include_candidates=True,
        )
    except ValueError as exc:
        assert "requires" in str(exc)
    else:
        raise AssertionError("candidate support requires a training seed")
    try:
        launcher.formal_command(
            {**vm, "target": "memory"},
            Path("/tmp/a13-runtime"),
            Path("/tmp/a13-run"),
            model_training_seed_dir="/tmp/a13-independent-top15",
            resume_calibration_dir="/tmp/current-top15",
            resume_model_training_seed_dir="/tmp/old-support",
        )
    except ValueError as exc:
        assert "mutually exclusive" in str(exc)
    else:
        raise AssertionError("fresh training seed must not mix with Top15 resume")
    print("formal-matrix HyBench root forwarding tests passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
