#!/usr/bin/env python3
"""XP4 Algorithm C runtime with adapter-valid graceful quiescence.

The predecessor passed 300 seconds for post-stop graceful quiescence even
though the frozen MySQL workload adapter accepts 30..120 seconds.  That made
every XP replay fail before Java was launched and the sampler then obscured
the primary exception.  This successor changes only that control value to
120 seconds.  The native workload, 300..900 second adaptive measurement,
candidate ordering, acceptance, shrink, and convergence rules are unchanged.
"""

from __future__ import annotations

from typing import Any

from mysql_workload_adapter import run_hybench as adapter_run_hybench
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v1 as predecessor
from scripts.run_mysql_four_scene_algorithm_c_xp4_runtime_v1 import *  # noqa: F401,F403


for _name in dir(predecessor):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(predecessor, _name))


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
    predecessor.alg.run_hybench = run_hybench_adapter_compatible


def main() -> int:
    install()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
