#!/usr/bin/env python3
"""CPU-A v13 with the post-install hook bound to the nested runtime core."""

from pathlib import Path
from typing import Any


SOURCE = Path(__file__).resolve().with_name(
    "run_mysql_algorithm_a_xp_cpu_round3_wave1_recovered_resume_v12.py"
)
text = SOURCE.read_text(encoding="utf-8")
replacements = {
    '        "    original_install = runtime.install\\n"\n':
        '        "    runtime_core = runtime.implementation[\\"implementation\\"]\\n"\n'
        '        "    original_install = runtime_core[\\"install\\"]\\n"\n',
    '        "    runtime.install = install_with_continuation\\n"\n':
        '        "    runtime_core[\\"install\\"] = install_with_continuation\\n"\n',
    '        "        runtime.install = original_install\\n"\n':
        '        "        runtime_core[\\"install\\"] = original_install\\n"\n',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise RuntimeError(f"CPU-A v13 predecessor differs: {old!r}")
    text = text.replace(old, new)
implementation: dict[str, Any] = {
    "__file__": str(Path(__file__).resolve()),
    "__name__": "scripts._run_mysql_algorithm_a_xp_cpu_round3_resume_v13_impl",
    "__package__": "scripts",
}
exec(compile(text, str(Path(__file__).resolve()), "exec"), implementation)
for name, value in implementation.items():
    if not name.startswith("__"):
        globals()[name] = value


if __name__ == "__main__":
    raise SystemExit(implementation["main"]())
