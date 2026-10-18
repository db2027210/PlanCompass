#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shell.resume_double_loop_grid_from_top15 import (  # noqa: E402
    _assert_stable_resume_source,
    _guest_instability_reason,
    _load_candidate_replay_cache,
    _param_signature,
    _usable_replays,
)
from shell.run_double_loop_grid_scenarios import (  # noqa: E402
    final_confirmation_is_independent,
)


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        real_dir = Path(temp_dir) / "none" / "real_validations"
        real_dir.mkdir(parents=True)
        usable = {
            "label": "none_double_loop_0001",
            "target": "none",
            "params": {"x": 0.12345678901234, "y": 2.0},
            "resources": {"cpu": 1.0, "memory": 2.0, "io": 3.0},
            "db_qps": 100.0,
            "status": "ok",
            "double_loop_measurement_usable": True,
        }
        (real_dir / "none_double_loop_0001.json").write_text(
            json.dumps(usable),
            encoding="utf-8",
        )
        unusable = dict(usable)
        unusable["label"] = "none_double_loop_0002"
        unusable["double_loop_measurement_usable"] = False
        (real_dir / "none_double_loop_0002.json").write_text(
            json.dumps(unusable),
            encoding="utf-8",
        )
        failed_but_flagged_usable = dict(usable)
        failed_but_flagged_usable["label"] = "none_double_loop_0003"
        failed_but_flagged_usable["status"] = "failed"
        (real_dir / "none_double_loop_0003.json").write_text(
            json.dumps(failed_but_flagged_usable),
            encoding="utf-8",
        )

        cache, audit = _load_candidate_replay_cache(real_dir.parent, "none")
        signature = _param_signature({"y": 2, "x": 0.1234567890123401})
        assert signature in cache
        assert cache[signature]["source_label"] == "none_double_loop_0001"
        assert cache[signature]["source_kind"] == "prior_partial_run"
        assert audit["scanned_json_count"] == 3
        assert audit["usable_exact_vector_count"] == 1
        assert audit["skipped_json_count"] == 2
        assert audit["required_transport_status"] == "ok"
        assert not audit["old_scores_loaded"]
        assert not audit["old_ranks_loaded"]

        calibration_path = Path(temp_dir) / "fresh_model_calibration.jsonl"
        usable_replay = {
            "event": "fresh_model_calibration_replay",
            "status": "usable",
            "params": {"x": 1.0},
            "measurement": {
                "db_qps": 1.0,
                "cpu": 1.0,
                "memory": 1.0,
                "io": 1.0,
            },
        }
        calibration_path.write_text(
            json.dumps(usable_replay) + "\n" + '{"event":"torn',
            encoding="utf-8",
        )
        recovered = _usable_replays(calibration_path)
        assert recovered == [usable_replay]

        stable_source = Path(temp_dir) / "stable" / "memory"
        stable_source.mkdir(parents=True)
        assert _guest_instability_reason(stable_source) is None
        _assert_stable_resume_source(stable_source, "test")

        task_root = Path(temp_dir) / "runs" / "a14_r1" / "attempt_1"
        unstable_source = task_root / "formal_runs" / "a14" / "memory"
        unstable_source.mkdir(parents=True)
        serial_path = task_root / "rootless_vm" / "serial.log"
        serial_path.parent.mkdir(parents=True)
        serial_path.write_text(
            "kernel: Out of memory: Killed process 123 (postgres)\n",
            encoding="utf-8",
        )
        assert "guest OOM" in str(_guest_instability_reason(unstable_source))
        try:
            _assert_stable_resume_source(unstable_source, "calibration")
        except RuntimeError as exc:
            assert "unsafe calibration source" in str(exc)
            assert "guest OOM" in str(exc)
        else:
            raise AssertionError("OOM-tainted resume source was accepted")

    assert not final_confirmation_is_independent(
        {
            "used_real_replay": False,
            "resumed_candidate_replay": {"used_new_real_replay": False},
        }
    )
    assert final_confirmation_is_independent(
        {"used_real_replay": True, "forced_new_real_replay": True}
    )

    print("resume double-loop grid tests passed", flush=True)


if __name__ == "__main__":
    main()
