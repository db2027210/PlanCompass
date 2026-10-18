#!/usr/bin/env python3
"""Split accumulated replay evidence into soft replay and permanent invalid keys."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


SCENES = (
    "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink JSON required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cumulative-quarantine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.cumulative_quarantine.expanduser().resolve()
    prior = load(source)
    all_keys = prior.get("quarantined_param_keys_by_scene") or {}
    rows = prior.get("rows_by_scene") or {}
    if (
        prior.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v2"
        or prior.get("status") != "PASS"
        or prior.get("formal_state_mutation") is not False
        or set(all_keys) != set(SCENES)
        or set(rows) != set(SCENES)
    ):
        raise RuntimeError("tiered replay quarantine authority differs")
    soft: dict[str, list[str]] = {}
    permanent: dict[str, list[str]] = {}
    for scene in SCENES:
        scene_rows = list(rows[scene])
        if (
            len(scene_rows) != len(all_keys[scene])
            or [row.get("optimizer_params_key") for row in scene_rows]
            != all_keys[scene]
            or any(row.get("formal_state_mutation") is not False for row in scene_rows)
        ):
            raise RuntimeError(f"{scene} tiered replay rows differ")
        permanent[scene] = [
            str(row["optimizer_params_key"])
            for row in scene_rows
            if row.get("outcome") == "FORMAL_WALL_TIMEOUT_INVALID_MEASUREMENT"
        ]
        soft[scene] = [
            str(row["optimizer_params_key"])
            for row in scene_rows
            if row.get("outcome") == "VALID_REPLAY_OUTER_ABORTED_BEFORE_DECISION"
        ]
        if (
            len(permanent[scene]) != 3
            or len(soft[scene]) != 6
            or set(permanent[scene]).intersection(soft[scene])
            or set(permanent[scene]) | set(soft[scene]) != set(all_keys[scene])
        ):
            raise RuntimeError(f"{scene} tiered replay classification differs")
    document = {
        **prior,
        "schema_version": "mysql_algorithm_c_replay_quarantine_v3",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "quarantined_per_scene": 9,
        "soft_replayed_per_scene": 6,
        "permanent_invalid_per_scene": 3,
        "soft_replayed_param_keys_by_scene": soft,
        "permanent_invalid_param_keys_by_scene": permanent,
        "cycle_reset_may_clear_soft_replayed": True,
        "cycle_reset_may_clear_permanent_invalid": False,
        "tiered_source_path": str(source),
        "tiered_source_sha256": sha(source),
        "tiered_builder_source_path": str(Path(__file__).resolve()),
        "tiered_builder_source_sha256": sha(Path(__file__).resolve()),
        "evidence": {
            **dict(prior.get("evidence") or {}),
            "cumulative_quarantine": {
                "path": str(source), "sha256": sha(source),
            },
        },
    }
    output = args.output.expanduser().resolve()
    if output.exists() or output.parent.exists():
        raise RuntimeError(f"fresh output directory required: {output.parent}")
    atomic(output, document)
    print(json.dumps({
        "status": "PASS", "output": str(output),
        "soft_replayed_per_scene": 6,
        "permanent_invalid_per_scene": 3,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
