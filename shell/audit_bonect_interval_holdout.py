#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bonect_interval_lineage import (  # noqa: E402
    audit_frozen_round_holdout,
    canonical_sha256,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit a frozen Bonect interval against a teacher holdout without changing search artifacts."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--parent-manifest", required=True)
    parser.add_argument("--teacher-params", required=True)
    parser.add_argument("--candidate-file-after", required=True)
    parser.add_argument("--candidate-order-after", required=True)
    parser.add_argument("--teacher-plan-hashes", default="")
    parser.add_argument("--superior-substitute", default="")
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manifest = read_json(Path(args.manifest))
    parent_manifest = read_json(Path(args.parent_manifest))
    teacher_params = read_json(Path(args.teacher_params))
    candidate_after = read_json(Path(args.candidate_file_after))
    order_after = read_json(Path(args.candidate_order_after))
    teacher_plan_doc: Dict[str, Any] = (
        read_json(Path(args.teacher_plan_hashes)) if args.teacher_plan_hashes else {}
    )
    teacher_plan_hashes = dict(teacher_plan_doc.get("teacher_plan_hashes") or teacher_plan_doc)
    superior_substitute: Dict[str, Any] = (
        read_json(Path(args.superior_substitute)) if args.superior_substitute else {}
    )
    receipt = audit_frozen_round_holdout(
        manifest=manifest,
        parent_manifest=parent_manifest,
        teacher_params=teacher_params,
        candidate_sha_after_holdout=canonical_sha256(candidate_after),
        candidate_order_sha_after_holdout=canonical_sha256(order_after),
        teacher_plan_hashes=teacher_plan_hashes,
        teacher_plan_features=teacher_plan_doc.get("plan_features") or {},
        superior_substitute=superior_substitute,
    )
    write_json(Path(args.output), receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if receipt.get("outer_run_allowed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
