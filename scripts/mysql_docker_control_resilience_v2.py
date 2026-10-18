#!/usr/bin/env python3
"""Docker control resilience for both reset and measurement call paths.

The v1 successor covered ``reset_mysql_xp_data_planes_v6._docker_json``.
Memory-scene measurements, however, read post-measure container state through
``prepare_mysql_four_scene_v4._docker_json`` directly.  Serialize and retry
that exact entry point as well, while retaining the individual-call-only lock
scope required by the full-parallel launch contract.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

from scripts import mysql_docker_control_resilience_v1 as prior
from scripts import prepare_mysql_four_scene_v4 as prep


INSPECT_ATTEMPTS = 4
_ORIGINAL_PREP_JSON = prep._docker_json


def resilient_prep_json(*argv: str) -> dict[str, Any]:
    last: BaseException | None = None
    for attempt in range(1, INSPECT_ATTEMPTS + 1):
        try:
            return prior._serialized(lambda: _ORIGINAL_PREP_JSON(*argv))
        except subprocess.TimeoutExpired as error:
            last = error
            if attempt < INSPECT_ATTEMPTS:
                time.sleep(float(attempt * 2))
    assert last is not None
    raise last


def install() -> None:
    prior.install()
    prep._docker_json = resilient_prep_json


def uninstall() -> None:
    prep._docker_json = _ORIGINAL_PREP_JSON
    prior.uninstall()

