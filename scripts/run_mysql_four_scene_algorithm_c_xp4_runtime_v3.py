#!/usr/bin/env python3
"""XP4 Algorithm C runtime with adapter-valid graceful quiescence."""

from __future__ import annotations

from typing import Any

from mysql_workload_adapter import run_hybench as adapter_run_hybench
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v1 as runtime_v1


# Re-export the predecessor API without allowing its internal ``predecessor``
# name to replace the runtime module alias used below.
for _name in dir(runtime_v1):
    if not _name.startswith("__") and _name != "predecessor":
        globals().setdefault(_name, getattr(runtime_v1, _name))


GRACEFUL_QUIESCENCE_SECONDS = 120


def run_hybench_adapter_compatible(*args: Any, **kwargs: Any) -> dict[str, Any]:
    control = kwargs.get("formal_control")
    if control is not None:
        control = dict(control)
        requested = int(control.get("graceful_quiescence_seconds", 0))
        if requested != 300:
            raise RuntimeError(
                "XP4 predecessor graceful-quiescence authority differs: "
                f"{requested}"
            )
        control["graceful_quiescence_seconds"] = GRACEFUL_QUIESCENCE_SECONDS
        kwargs["formal_control"] = control
    return adapter_run_hybench(*args, **kwargs)


def install() -> None:
    runtime_v1.alg.run_hybench = run_hybench_adapter_compatible


def main() -> int:
    install()
    return runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())
