from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
V49 = TOOLS / "job_fresh10_target10_autonomous_v49.py"
V50 = TOOLS / "job_fresh10_target10_autonomous_v50.py"
V52 = TOOLS / "job_fresh10_target10_autonomous_v52.py"
BOOTSTRAP = TOOLS / "bootstrap_job_v52_fresh0.py"
LAUNCHER = TOOLS / "launch_job_target10_autonomous_v52_fresh0.sh"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_v52():
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location("job_v52_binding_test", V52)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot import JOB v52")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if sys.path and sys.path[0] == str(TOOLS):
            sys.path.pop(0)


@unittest.skip("JOB v52r2 deployment is superseded by the independent v53 campaign")
class JobV52ControllerBindingTest(unittest.TestCase):
    def test_v52_uses_independent_root_and_campaign_contract(self) -> None:
        module = import_v52()
        implementation = module.implementation
        self.assertEqual(implementation.CONTROLLER_VERSION, "v52r2")
        self.assertEqual(
            implementation.FRESH_ROOT.name,
            "autonomous_target10_v52r2_fresh0",
        )
        self.assertEqual(
            implementation.CAMPAIGN_CONTRACT,
            "job_v52r2_fresh0_v50_technical_excluded_remote974_v1",
        )
        self.assertEqual(implementation.v49.ROOT, implementation.FRESH_ROOT)
        self.assertEqual(implementation.v49.STATE, implementation.FRESH_STATE)

    def test_launcher_pins_new_root_initializer_and_zero_call_preflight(self) -> None:
        text = LAUNCHER.read_text(encoding="utf-8")
        for path in (V49, V50, V52, BOOTSTRAP):
            self.assertIn(sha(path), text)
        self.assertIn("autonomous_target10_v52r2_fresh0", text)
        self.assertIn("--preflight-only", text)
        self.assertNotIn("bootstrap_job_v50_fresh0.py", text)
        self.assertNotIn("deploy_job_v51_control_bridge.py", text)
        self.assertNotIn("c53fbb20b401941ec83555bfd64211162", text)
        self.assertIn(
            "974634b25e4c421ef7674c357f19438a5c43441c498f44534cf97b86dca162e4",
            text,
        )
        self.assertNotIn("ssh ", text)


if __name__ == "__main__":
    unittest.main()
