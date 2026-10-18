#!/usr/bin/env python3
"""Build a pilot-only v6 soft replay quarantine from completed full-5 waves."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_replay_quarantine_v6_final3_20260813T1925p0800/replay_quarantine.json"
PILOT = ROOT / "history/mysql_four_scene_20260809/v18_pruning_v2_pilot_nonformal_v16_full5_pg_ignored_20260814T0216p0800"
OUTPUT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v18_pilot_soft_replay_full5_20260814T0830p0800/replay_quarantine.json"
SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def param_key(params: Mapping[str, Any]) -> str:
    return json.dumps(
        {name: float(params[name]) for name in sorted(params)},
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.parent.exists():
        raise RuntimeError(f"fresh output directory required: {OUTPUT.parent}")
    prior = load(PRIOR)
    if (
        prior.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or prior.get("status") != "PASS"
        or any((prior.get("quarantined_param_keys_by_scene") or {}).get(scene) for scene in SCENES)
    ):
        raise RuntimeError("empty v18 source quarantine required")

    rows = {scene: [] for scene in SCENES}
    batch_bindings: dict[str, dict[str, str]] = {}
    for rank in range(1, 6):
        path = PILOT / "outer_005" / f"top5_batch_{rank}" / "batch_result.json"
        batch = load(path)
        if batch.get("status") != "PASS" or batch.get("failures") not in ([], None):
            raise RuntimeError(f"valid completed pilot batch required: {path}")
        batch_bindings[f"pilot_batch_{rank}"] = {"path": str(path), "sha256": sha(path)}
        measurements = batch.get("measurements") or {}
        for scene in SCENES:
            matches = [
                value for value in measurements.values()
                if value.get("scene") == scene
            ]
            if (
                len(matches) != 1
                or int(matches[0].get("rank") or -1) != rank
                or any(value.get("hard_gate_passed") is not True for value in matches)
            ):
                raise RuntimeError(f"one valid candidate measurement required for {scene} rank {rank}")
            keys = {param_key(value.get("params") or {}) for value in matches}
            if len(keys) != 1:
                raise RuntimeError(f"worker parameter mismatch for {scene} rank {rank}")
            rows[scene].append({
                "optimizer_params_key": keys.pop(),
                "classification": "soft_replayed_nonformal_pilot_only",
                "source_outer_attempt": 5,
                "source_rank": rank,
                "source_batch_path": str(path),
                "source_batch_sha256": sha(path),
                "formal_state_mutation": False,
                "accepted_state_credit": False,
            })

    soft = {scene: [row["optimizer_params_key"] for row in rows[scene]] for scene in SCENES}
    if any(len(values) != 5 or len(set(values)) != 5 for values in soft.values()):
        raise RuntimeError("five unique soft replay keys required per scene")
    document = dict(prior)
    document.update({
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "quarantined_counts_by_scene": {scene: 5 for scene in SCENES},
        "soft_replayed_counts_by_scene": {scene: 5 for scene in SCENES},
        "permanent_invalid_counts_by_scene": {scene: 0 for scene in SCENES},
        "quarantined_param_keys_by_scene": soft,
        "soft_replayed_param_keys_by_scene": soft,
        "permanent_invalid_param_keys_by_scene": {scene: [] for scene in SCENES},
        "rows_by_scene": rows,
        "initialization": "pilot_only_soft_replay_from_completed_nonformal_full5",
        "pilot_only": True,
        "formal_resume_use_prohibited": True,
        "formal_accepted_credit_prohibited": True,
        "formal_state_mutation": False,
        "accepted_rounds_mutation": False,
        "rejected_rounds_mutation": False,
        "interval_convergence_mutation": False,
        "evidence": {
            **dict(prior.get("evidence") or {}),
            **batch_bindings,
        },
        "builder_source_path": str(Path(__file__).resolve()),
    })
    OUTPUT.parent.mkdir(parents=True, mode=0o700)
    temporary = OUTPUT.with_name(f".{OUTPUT.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT)
    OUTPUT.with_name(OUTPUT.name + ".sha256").write_text(f"{sha(OUTPUT)}  {OUTPUT.name}\n", encoding="ascii")
    print(json.dumps({"status": "PASS", "output": str(OUTPUT), "sha256": sha(OUTPUT), "counts": {scene: 5 for scene in SCENES}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
