#!/usr/bin/env python3
"""Resume CPU-A Round3 and reuse its recovered reset for any retry number."""

import os
from pathlib import Path
import traceback
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round3_direct_resume_v14.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = 'if reset_reused or "round_003_wave_001_attempt_01" not in token:'
new = 'if reset_reused or "round_003_wave_001_attempt_" not in token:'
if text.count(old) != 1:
    raise RuntimeError("CPU-A Round3 reset-reuse predicate differs")
text = text.replace(old, new)
bridge: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round3_v15_bridge",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), bridge)
implementation = bridge["implementation"]
history = implementation["HISTORY"]
implementation["RECOVERED_ROUND2_RESET"] = (
    history / "v788_to_a_cpu_round3_partial_recovery_20260827"
    / "xp_resets/v786_to_a_cpu_direct_round3_resume_20260827_xp_cpu_"
      "round_003_wave_001_attempt_02/reset_binding.json"
)
implementation["RECOVERED_AUTHORITY"] = (
    history / "v789_to_a_cpu_round3_partial_authority_20260827/live_authority.json"
)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    try:
        raise SystemExit(implementation["main"]())
    except BaseException:
        diagnostic = os.environ.get("MYSQL_XP_CPU_A_FAILURE_DIAGNOSTIC", "")
        if diagnostic:
            Path(diagnostic).write_text(traceback.format_exc(), encoding="utf-8")
        raise
