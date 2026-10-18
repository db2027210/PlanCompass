#!/usr/bin/env python3
"""Bounded resource-sampler teardown recovery for formal MySQL runs."""

from __future__ import annotations

from typing import Any

from scripts import run_mysql_four_scene_r0_v5 as r0


EXTRA_JOIN_SECONDS = 120.0
_ORIGINAL_FINISH = r0.CgroupSampler.finish


def resilient_finish(self: Any, active_windows: Any = None):
    try:
        return _ORIGINAL_FINISH(self, active_windows)
    except RuntimeError as error:
        if str(error) != "resource sampler did not stop":
            raise
        # finish() has already set stop_event.  Permit a sample that was
        # delayed by transient cgroup/filesystem pressure to complete, then
        # invoke the unchanged summarization path.  No incomplete resource
        # evidence is accepted.
        self.thread.join(timeout=EXTRA_JOIN_SECONDS)
        if self.thread.is_alive():
            raise RuntimeError(
                "resource sampler did not stop after bounded recovery"
            ) from error
        return _ORIGINAL_FINISH(self, active_windows)


def install() -> None:
    r0.CgroupSampler.finish = resilient_finish


def uninstall() -> None:
    r0.CgroupSampler.finish = _ORIGINAL_FINISH
