#!/usr/bin/env python3
"""XP8 Algorithm-A v14 with the current C49 measurement adapter binding."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_top4_mixed_eight_worker_v13.py"
)
text = SOURCE.read_text(encoding="utf-8")
old = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v13"
new = "frozen_mysql_xp_top4_algorithm_a_mixed_eight_worker_20260828_v14"
if text.count(old) != 1:
    raise RuntimeError("XP8 A v14 predecessor contract differs")
text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp8_v14_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


C49_ADAPTER = (
    Path(__file__).resolve().parents[1]
    / "config/formal/frozen_mysql_algorithm_a_c49_measurement_adapter_20260828_v2"
    / "algorithm_c_measurement_adapter_contract.json"
)
_original_c49_validate = base.c49.validate_contract


def _validate_current_c49_adapter():
    base.c49.ASYNC_CONTRACT = C49_ADAPTER
    return _original_c49_validate()


base.c49.validate_contract = _validate_current_c49_adapter


if __name__ == "__main__":
    raise SystemExit(main())
