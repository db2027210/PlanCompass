#!/usr/bin/env python3
"""Round-6 XP/I/O A resume bound to the refreshed post-recovery authority."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_io_round6_checkpoint_resume_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_io_round6_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)

contract = (
    Path(__file__).resolve().parents[1]
    / "config/formal"
    / "frozen_mysql_algorithm_a_xp_io_round6_checkpoint_resume_20260828_v2"
    / "transition_contract.json"
)
implementation["CONTRACT"] = contract
implementation["validate_contract"].__globals__["CONTRACT"] = contract
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
