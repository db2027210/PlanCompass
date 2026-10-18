#!/usr/bin/env python3
"""XP8 Algorithm-A runtime with refreshed c49 measurement-adapter binding."""

from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v1.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v1"
new = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260827_v2"
if text.count(old) != 1:
    raise RuntimeError("XP8 Algorithm-A v2 predecessor differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp8_v2_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
implementation["implementation"]["base"].c49.ASYNC_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260827_v1"
    / "algorithm_c_measurement_adapter_contract.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
