#!/usr/bin/env python3
"""Algorithm C recovery successor for independent-scene timeout handling.

This keeps the v41 PostgreSQL-aligned outer flow and corrects three recovery
boundaries: standalone JOB/TPC-H workers are eligible for candidate-local
timeout classification, valid siblings of a timed-out expansion candidate are
retained, and an outer-boundary resume restores its replay archive instead of
silently starting that metadata from empty lists.
"""

from __future__ import annotations

from pathlib import Path

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v41 as v41
from scripts import build_mysql_algorithm_c_scene_resume_checkpoint_v1 as scene_checkpoint


_source = v41._source


def _replace_once(old: str, new: str, label: str) -> None:
    global _source
    if _source.count(old) != 1:
        raise RuntimeError(f"Algorithm C v41 {label} boundary differs")
    _source = _source.replace(old, new)


# Independent-scene controllers submit JOB or TPC-H directly.  The v35
# classifier recognized only XP workers and the old mixed JOB/TPC-H pair task,
# so a valid standalone TPC-H timeout escaped candidate-level classification.
_replace_once(
    '''    workload_tasks = set(alg.XP_IDS).intersection(assignments)
''',
    '''    workload_tasks = {
        worker_id for worker_id in assignments
        if worker_id in set(alg.XP_IDS)
        or worker_id.startswith("job_w")
        or worker_id.startswith("tpch_w")
    }
''',
    "standalone relational timeout classification",
)

# Match the ordinary Top-5 path: a timed-out candidate is ineligible, while a
# passing sibling in the same physical wave remains valid evidence.
_replace_once(
    '''            technical_exclusions.append({
                **technical,
                "batch_kind": batch_kind,
                "batch_index": batch_index,
                "candidate_ranks": list(ranks),
                "batch_result_path": str(batch_path),
                "batch_result_sha256": alg.sha(batch_path),
            })
            continue
''',
    '''            failed_worker_ids = set(technical["failed_worker_ids"])
            excluded_ranks = sorted({
                int((assignments[worker_id] or {}).get("rank") or 0)
                for worker_id in failed_worker_ids
            })
            retained_worker_ids: list[str] = []
            for worker_id, row in measured.items():
                if worker_id in failed_worker_ids:
                    continue
                if (
                    row.get("hard_gate_passed") is not True
                    or row.get("coverage_complete") is not True
                ):
                    raise RuntimeError(
                        "technical expansion classification left an invalid "
                        f"sibling: {worker_id}"
                    )
                scene = str(row["scene"])
                if worker_id in companion_workers:
                    companion_audit.append({
                        "batch_kind": batch_kind,
                        "batch_index": batch_index,
                        "worker_id": worker_id,
                        "scene": scene,
                        "hard_gate_passed": True,
                        "coverage_complete": True,
                        "retained_from_partially_failed_physical_wave": True,
                    })
                else:
                    row["candidate_ranking"] = copy.deepcopy(
                        candidate_sets[scene][int(row["rank"]) - 1]
                    )
                    row["semantic_batch_kind"] = batch_kind
                    evaluations[scene].append(row)
                retained_worker_ids.append(worker_id)
            technical_exclusions.append({
                **technical,
                "batch_kind": batch_kind,
                "batch_index": batch_index,
                "candidate_ranks": excluded_ranks,
                "physical_wave_ranks": list(ranks),
                "retained_valid_worker_ids": sorted(retained_worker_ids),
                "valid_sibling_measurements_retained": bool(retained_worker_ids),
                "batch_result_path": str(batch_path),
                "batch_result_sha256": alg.sha(batch_path),
            })
            continue
''',
    "partial expansion-wave retention",
)

# A clean resume should not remeasure the last accepted incumbent merely due
# to controller recovery.  Its values are SHA-bound in the recovery checkpoint.
_replace_once(
    "REUSE_CHECKPOINT_INCUMBENT_BASELINE = False\n",
    "REUSE_CHECKPOINT_INCUMBENT_BASELINE = True\n",
    "checkpoint incumbent baseline reuse",
)

# Restore replay metadata that is not part of the legacy v2 checkpoint schema
# but is present and verified in the new scene-boundary recovery checkpoint.
_replace_once(
    '''        "valid_replay_archive_by_scene": {
            scene: [] for scene in SCENES
        },
        "previously_accepted_parameter_signatures": {
            scene: [] for scene in SCENES
        },
''',
    '''        "valid_replay_archive_by_scene": {
            scene: copy.deepcopy(
                (reconstructed.get("valid_replay_archive_by_scene") or {}).get(
                    scene, []
                )
            )
            for scene in SCENES
        },
        "previously_accepted_parameter_signatures": {
            scene: copy.deepcopy(
                (
                    reconstructed.get(
                        "previously_accepted_parameter_signatures"
                    ) or {}
                ).get(scene, [])
            )
            for scene in SCENES
        },
''',
    "resume replay metadata",
)

# The standard verifier remains authoritative for old checkpoints; the scene
# recovery verifier is used only for its separately versioned schema.
_replace_once(
    "    checkpoint = checkpointmod.verify_checkpoint(args.checkpoint)\n",
    "    checkpoint = scene_checkpoint.verify_or_standard(args.checkpoint)\n",
    "scene recovery checkpoint verifier",
)

# The immutable quarantine was published for the complete four-scene cohort.
# An independent scene controller validates and consumes only its own slice;
# sibling rows remain bound in the source file but are not runtime state.
_replace_once(
    '''    value = alg.load(path)
    scene_keys = _scene_mapping(
        value.get("quarantined_param_keys_by_scene"),
''',
    '''    value = alg.load(path)
    for field in (
        "quarantined_param_keys_by_scene", "rows_by_scene",
        "soft_replayed_param_keys_by_scene",
        "permanent_invalid_param_keys_by_scene",
        "quarantined_counts_by_scene", "soft_replayed_counts_by_scene",
        "permanent_invalid_counts_by_scene",
    ):
        raw = value.get(field)
        if isinstance(raw, Mapping) and set(SCENES).issubset(raw):
            value[field] = {scene: copy.deepcopy(raw[scene]) for scene in SCENES}
    scene_keys = _scene_mapping(
        value.get("quarantined_param_keys_by_scene"),
''',
    "single-scene quarantine projection",
)

exec(compile(_source, str(Path(__file__).resolve()), "exec"), globals())
