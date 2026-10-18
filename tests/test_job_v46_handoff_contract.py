from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
V45 = ROOT / "tools/launch_job_fresh10_v45_diagnostic_probe004.sh"
V46 = ROOT / "tools/watch_job_fresh10_v46_repeat_gate_formal004.sh"
V47 = ROOT / "tools/run_job_fresh10_successor004_autonomous_v47.sh"
V49 = ROOT / "tools/job_fresh10_target10_autonomous_v49.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class JobV46HandoffContractTest(unittest.TestCase):
    def test_shell_syntax(self) -> None:
        subprocess.run(
            ["bash", "-n", str(V45), str(V46), str(V47)],
            check=True,
        )

    def test_current_formal_state_is_pinned(self) -> None:
        text = V46.read_text(encoding="utf-8")
        self.assertIn(
            "EXPECTED_STATE_SHA="
            "be1b8fecccd9a1092b0d49c7bfb76b86f5d61fab5031387169250b5380606ef4",
            text,
        )
        self.assertNotIn("EXPECTED_STATE_SHA=7fde8e94", text)

    def test_formal_path_uses_three_observation_median(self) -> None:
        text = V46.read_text(encoding="utf-8")
        self.assertIn("run_job_round8_strict_serial_robust_v7.py", text)
        self.assertIn("build_job_three_observation_formal_selection_v1.py", text)
        self.assertIn("three_independent_exact_point_median_acceptance_v2", text)
        self.assertIn("--first-selection", text)
        self.assertIn("--second-selection", text)

    def test_technical_failure_is_state_neutral_and_not_calibration(self) -> None:
        text = V46.read_text(encoding="utf-8")
        self.assertIn("validate_state_neutral_diagnostic", text)
        self.assertRegex(
            text,
            re.compile(
                r"if ! validate_clean_diagnostic.*?"
                r"validate_state_neutral_diagnostic.*?continue",
                re.DOTALL,
            ),
        )
        self.assertIn(".initial_state == .final_state", text)

    def test_v47_hash_pins_and_completed_probe_handoff(self) -> None:
        text = V47.read_text(encoding="utf-8")
        self.assertIn(f"EXPECTED_PROBE_SHA={sha256(V45)}", text)
        self.assertIn(f"EXPECTED_PROMOTE_SHA={sha256(V46)}", text)
        self.assertIn(f"EXPECTED_TARGET10_SHA={sha256(V49)}", text)
        self.assertIn('if ! test -f "${PROBE_AUDIT}"', text)
        self.assertIn('jq -e \'.status == "committed"\'', text)
        self.assertIn('exec python3 -u "${TARGET10}"', text)


if __name__ == "__main__":
    unittest.main()
