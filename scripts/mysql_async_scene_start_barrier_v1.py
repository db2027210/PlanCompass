#!/usr/bin/env python3
"""One-time authority barrier for independently progressing scene lanes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Iterable


def wait_ready(
    *, barrier_root: Path, scene: str, expected_scenes: Iterable[str],
    authority: dict[str, Any], timeout_seconds: float = 1800.0,
) -> dict[str, Any]:
    expected = tuple(sorted(str(value) for value in expected_scenes))
    if scene not in expected or timeout_seconds <= 0:
        raise ValueError("invalid asynchronous scene start barrier")
    barrier_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    ready = barrier_root / f"{scene}.ready.json"
    temporary = ready.with_name(f".{ready.name}.{os.getpid()}.tmp")
    payload = {
        "schema_version": "mysql_async_scene_start_authority_v1",
        "status": "READY",
        "scene": scene,
        "pid": os.getpid(),
        "expected_scenes": list(expected),
        "authority": authority,
        "per_round_scene_barrier": False,
        "one_time_prestart_authority_barrier": True,
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(ready)
    started = time.monotonic()
    while True:
        rows = {}
        for path in barrier_root.glob("*.ready.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            lane = str(row.get("scene") or "")
            if (
                row.get("status") == "READY"
                and tuple(sorted(row.get("expected_scenes") or [])) == expected
                and lane in expected
            ):
                rows[lane] = row
        if set(rows) == set(expected):
            return {
                "status": "PASS",
                "scene": scene,
                "ready_scenes": sorted(rows),
                "wait_seconds": time.monotonic() - started,
                "per_round_scene_barrier": False,
            }
        if time.monotonic() - started >= timeout_seconds:
            raise RuntimeError(
                "asynchronous scene start authority barrier timed out: "
                f"ready={sorted(rows)} expected={list(expected)}"
            )
        time.sleep(1.0)


__all__ = ["wait_ready"]
