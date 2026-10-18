#!/usr/bin/env python3
"""Audit every credited Algorithm C acceptance against the 0.8 hard cap."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod


SCENES = ("job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io")
TARGET = {
    "job_memory": "memory",
    "tpch_sf4_memory": "memory",
    "hybench_xp_cpu": "cpu",
    "hybench_xp_io": "io",
}
THRESHOLD = 0.8
SCHEMA = "mysql_algorithm_c_non_target_bottleneck_audit_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def bound(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink evidence required: {path}")
    return {"path": str(path), "sha256": sha(path)}


def accepted_evaluation(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    eligible = [
        mapping(row, "decision evaluation")
        for row in decision.get("evaluations") or []
        if mapping(row, "decision evaluation").get("outer_decision", {}).get(
            "accepted"
        ) is True
    ]
    if decision.get("accepted") is not True:
        if eligible:
            raise RuntimeError("rejected decision contains accepted evaluation")
        return None
    if not eligible:
        raise RuntimeError("accepted decision has no accepted evaluation")
    return min(eligible, key=lambda row: int(row["rank"]))


def build_audit(checkpoint_path: Path) -> dict[str, Any]:
    checkpoint_path = checkpoint_path.expanduser().resolve()
    root = checkpointmod.verify_checkpoint(checkpoint_path)
    expected_freeze_sha = str(root.get("freeze_sha256") or "")
    credited_counts = mapping(
        mapping(root.get("reconstructed_state"), "root reconstructed state").get(
            "accepted_rounds"
        ),
        "root accepted rounds",
    )
    audited_counts = {scene: 0 for scene in SCENES}
    rows: list[dict[str, Any]] = []
    checkpoint_chain: list[dict[str, Any]] = []
    seen: set[Path] = set()
    current_path: Path | None = checkpoint_path
    while current_path is not None:
        current_path = current_path.resolve()
        if current_path in seen:
            raise RuntimeError("Algorithm C checkpoint chain cycle detected")
        seen.add(current_path)
        # The root is fully verified above.  Every predecessor is then pinned by
        # the SHA in its already-pinned child.  Loading predecessors this way is
        # intentional: checkpoints predating replay-history-v2 cannot be passed
        # through the newest verifier, even though their immutable SHA bindings
        # and decision bindings remain valid evidence.
        checkpoint = root if current_path == checkpoint_path else load(current_path)
        checkpoint_binding = bound(current_path)
        checkpoint_binding["freeze_sha256"] = str(
            checkpoint.get("freeze_sha256") or ""
        )
        checkpoint_chain.append(checkpoint_binding)
        if isinstance(checkpoint.get("evidence"), Mapping):
            evidence = mapping(checkpoint.get("evidence"), "checkpoint evidence")
        elif checkpoint.get("schema_version") == "mysql_algorithm_c_outer1_checkpoint_v1":
            legacy_paths = mapping(
                checkpoint.get("decision_paths"), "legacy decision paths"
            )
            legacy_hashes = mapping(
                checkpoint.get("decision_sha256"), "legacy decision hashes"
            )
            evidence = {
                "outers": {
                    "1": {
                        "decisions": {
                            scene: {
                                "path": legacy_paths[scene],
                                "sha256": legacy_hashes[scene],
                            }
                            for scene in SCENES
                        }
                    }
                },
                "source_checkpoint": None,
            }
        else:
            raise RuntimeError("checkpoint evidence object required")
        for outer_text, outer_value in mapping(
            evidence.get("outers"), "checkpoint outer evidence",
        ).items():
            outer = int(outer_text)
            decisions = mapping(
                mapping(outer_value, "outer evidence").get("decisions"),
                "outer decision bindings",
            )
            for scene, binding_value in decisions.items():
                if scene not in SCENES:
                    raise RuntimeError(f"unexpected Algorithm C scene: {scene}")
                binding = mapping(binding_value, "decision binding")
                path = Path(str(binding.get("path") or "")).resolve()
                if binding.get("sha256") != sha(path):
                    raise RuntimeError(f"decision SHA differs: {path}")
                decision = load(path)
                chosen = accepted_evaluation(decision)
                if chosen is None:
                    continue
                outer_decision = mapping(
                    chosen.get("outer_decision"), "chosen outer decision",
                )
                resources = mapping(
                    chosen.get("resource_means"), "chosen resources",
                )
                target = TARGET[scene]
                non_targets = {
                    name: float(resources[name])
                    for name in ("cpu", "memory", "io") if name != target
                }
                violations = sorted(
                    name for name, value in non_targets.items()
                    if value >= THRESHOLD
                )
                recorded = sorted(
                    outer_decision.get("candidate_non_target_bottlenecks") or []
                )
                passed = (
                    outer_decision.get("hard_gate_passed") is True
                    and float(
                        outer_decision.get("non_target_bottleneck_threshold")
                    ) == THRESHOLD
                    and not violations
                    and not recorded
                )
                rows.append({
                    "outer_attempt": outer,
                    "scene": scene,
                    "rank": int(chosen["rank"]),
                    "decision": bound(path),
                    "non_target_utilization": non_targets,
                    "computed_non_target_bottlenecks": violations,
                    "recorded_non_target_bottlenecks": recorded,
                    "strictly_below_0_8": passed,
                })
                audited_counts[scene] += 1
        source = evidence.get("source_checkpoint")
        if source is None:
            current_path = None
        else:
            source_binding = mapping(source, "source checkpoint binding")
            next_path = Path(str(source_binding.get("path") or "")).resolve()
            if source_binding.get("sha256") != sha(next_path):
                raise RuntimeError("source checkpoint SHA differs")
            current_path = next_path
    # The chain includes accepted rows later removed by an explicit migration
    # or reset (notably the v18 XP/I/O reset).  Auditing the entire provenance
    # chain is deliberately stronger than auditing only the currently credited
    # subset, so audited_counts need not equal credited_counts.
    failures = [row for row in rows if row["strictly_below_0_8"] is not True]
    return {
        "schema_version": SCHEMA,
        "status": "PASS" if not failures else "FAILED_CLOSED",
        "captured_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "threshold": THRESHOLD,
        "algorithm_c_checkpoint": bound(checkpoint_path),
        "checkpoint_chain": checkpoint_chain,
        "currently_credited_counts": {
            scene: int(credited_counts[scene]) for scene in SCENES
        },
        "audited_provenance_accepted_counts": audited_counts,
        "accepted_rows": sorted(
            rows, key=lambda row: (row["outer_attempt"], row["scene"]),
        ),
        "failure_count": len(failures),
        "failures": failures,
        "formal_state_mutation": False,
        "accepted_state_credit_mutation": False,
        "builder_source": bound(Path(__file__).resolve()),
    }


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError(f"fresh output required: {args.output}")
    result = build_audit(args.checkpoint)
    atomic(args.output.resolve(), result)
    print(json.dumps({
        "status": result["status"],
        "currently_credited_counts": result["currently_credited_counts"],
        "audited_provenance_accepted_counts": result[
            "audited_provenance_accepted_counts"
        ],
        "failure_count": result["failure_count"],
        "output": str(args.output.resolve()),
    }, sort_keys=True))
    return 0 if result["status"] == "PASS" else 4


if __name__ == "__main__":
    raise SystemExit(main())
