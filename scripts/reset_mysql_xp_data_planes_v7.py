#!/usr/bin/env python3
"""XP reset v7: allow Docker extra time to publish a proven PID exit.

The discard path, identity checks, and data-plane rebuild are unchanged from
v6.  Attempt 052 proved that Docker published the exact container's SIGKILL
exit five seconds after the v6 180-second wait expired, so this successor
extends only that bounded state-convergence wait to 240 seconds.
"""

from __future__ import annotations

from scripts import reset_mysql_xp_data_planes_v6 as _v6
from scripts.reset_mysql_xp_data_planes_v6 import *  # noqa: F401,F403

# The reset authority is consumed as a module and its audited recovery paths
# intentionally call a few private inspection helpers.  Re-export the complete
# v6 module surface, excluding only Python's dunder metadata.
for _name in dir(_v6):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_v6, _name))


AUTHORITY_VERSION = "v7"
OUTPUT_SCHEMA = "mysql_four_scene_v7_xp_reprepared_binding_v1"
HOST_PID_KILL_EXIT_WAIT_SECONDS = 240

# Functions imported from v6 retain v6's module globals.  Bind the successor
# wait there as well; all other v6 constants and behavior remain unchanged.
_v6.HOST_PID_KILL_EXIT_WAIT_SECONDS = HOST_PID_KILL_EXIT_WAIT_SECONDS
_v6.AUTHORITY_VERSION = AUTHORITY_VERSION
_v6.OUTPUT_SCHEMA = OUTPUT_SCHEMA
