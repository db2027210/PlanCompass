#!/usr/bin/env python3
"""Bounded retry for read-only post-reset benchmark-account probes."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable


RELEASE = "v208-read-only-benchmark-probe-retry"
MAXIMUM_ATTEMPTS = 3
BACKOFF_SECONDS = (5.0, 10.0)


def wrap(original: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    def resilient(
        worker: dict[str, Any], output_root: Path, verify_existing: bool,
        rotate_all: bool,
    ) -> dict[str, Any]:
        for attempt in range(1, MAXIMUM_ATTEMPTS + 1):
            try:
                return original(worker, output_root, verify_existing, rotate_all)
            except subprocess.TimeoutExpired:
                if not verify_existing or rotate_all or attempt >= MAXIMUM_ATTEMPTS:
                    raise
                time.sleep(BACKOFF_SECONDS[attempt - 1])
        raise AssertionError("unreachable benchmark probe retry state")

    resilient.__name__ = "resilient_read_only_benchmark_probe"
    resilient.__doc__ = (
        "Retry only timeout failures from the post-reset read-only credential "
        "verification path; all other failures remain fail-closed."
    )
    return resilient

