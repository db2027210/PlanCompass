#!/usr/bin/env python3
"""XP reset v8: bound credential metadata operations at 120 seconds.

The v7 reset allows 600 seconds for recursive data ownership repair but only
30 seconds for the single ``reset-init.sql`` ownership change.  A transient
filesystem metadata stall can therefore fail before any template copy or
workload starts.  This successor changes only that one command's timeout from
30 to 120 seconds; command identity, ownership, permissions, and all
fail-closed behavior remain unchanged.
"""

from __future__ import annotations

import subprocess as _stdlib_subprocess
from typing import Any

from scripts import reset_mysql_xp_data_planes_v6 as _v6
from scripts import reset_mysql_xp_data_planes_v7 as _v7
from scripts.reset_mysql_xp_data_planes_v7 import *  # noqa: F401,F403


AUTHORITY_VERSION = "v8"
CREDENTIAL_METADATA_TIMEOUT_SECONDS = 120
_ORIGINAL_SUBPROCESS = _v6.subprocess


def _credential_chown(argv: Any) -> bool:
    return (
        isinstance(argv, (list, tuple))
        and len(argv) == 5
        and list(argv[:4]) == ["sudo", "-n", "chown", "999:999"]
        and str(argv[-1]).endswith("/credentials/reset-init.sql")
    )


class _SubprocessProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(_ORIGINAL_SUBPROCESS, name)

    def run(self, argv: Any, *args: Any, **kwargs: Any):
        if _credential_chown(argv):
            kwargs["timeout"] = max(
                float(kwargs.get("timeout") or 0),
                float(CREDENTIAL_METADATA_TIMEOUT_SECONDS),
            )
        return _ORIGINAL_SUBPROCESS.run(argv, *args, **kwargs)


def install() -> None:
    _v6.AUTHORITY_VERSION = AUTHORITY_VERSION
    _v6.subprocess = _SubprocessProxy()


install()
