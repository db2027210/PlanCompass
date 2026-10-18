#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "shell") not in sys.path:
    sys.path.insert(0, str(ROOT / "shell"))

from run_bonect_phase2_vm_teacher_recovery_pipeline import (  # noqa: E402
    COST_PARAM_KEYS,
    DEFAULT_TEACHER_RECOVERY_CONFIG,
    DEFAULT_TEACHER_MANIFEST,
    read_json,
    scenario_command,
)


def test_commands_keep_diagnostic_and_blind_modes_separate() -> None:
    manifest = read_json(DEFAULT_TEACHER_MANIFEST)
    scenario_cfg = manifest["scenarios"]["memory"]
    with tempfile.TemporaryDirectory(prefix="phase2-recovery-pipeline-") as temp_dir:
        artifact = Path(temp_dir) / "artifact.json"
        artifact.write_text("{}\n", encoding="utf-8")
        diagnostic = scenario_command(
            run_id="diagnostic",
            scenario="memory",
            scenario_cfg=scenario_cfg,
            delta_artifact=artifact,
            teacher_recovery_config=DEFAULT_TEACHER_RECOVERY_CONFIG,
            answer_blind=False,
            execute=True,
        )
        blind = scenario_command(
            run_id="blind",
            scenario="memory",
            scenario_cfg=scenario_cfg,
            delta_artifact=artifact,
            teacher_recovery_config=None,
            answer_blind=True,
            execute=True,
        )
    forbidden = {
        "--bonect-initial-param",
        "--bonect-param-range",
        "--bruteforce-param-values",
        "--bruteforce-param-step",
    }
    assert not forbidden.intersection(diagnostic)
    assert not forbidden.intersection(blind)
    assert "--phase2-teacher-recovery-config" in diagnostic
    assert "--phase2-blind-recovery" not in diagnostic
    assert "--phase2-blind-recovery" in blind
    assert "--phase2-teacher-recovery-config" not in blind
    assert "--adaptive-replay-controller-enabled" not in diagnostic
    assert "--adaptive-replay-controller-enabled" in blind
    assert "--bonect-memory-use-xp" in diagnostic
    active_index = blind.index("--bonect-active-params") + 1
    assert set(blind[active_index].split(",")) == set(COST_PARAM_KEYS)
    assert blind[blind.index("--bonect-pair-probe-max-candidates") + 1] == "78125"
    assert blind[blind.index("--bruteforce-range-update-strategy") + 1] == "binary_interval"


def main() -> None:
    test_commands_keep_diagnostic_and_blind_modes_separate()
    print("PASS Phase2 VM teacher recovery pipeline tests", flush=True)


if __name__ == "__main__":
    main()
