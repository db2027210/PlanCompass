#!/usr/bin/env python3
"""Build a frozen operator-transition hard-reject artifact from JSONL logs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operator_resource_rules import build_rule_artifact, sha256_file  # noqa: E402


def read_object(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    output_path = Path(args.output).resolve()
    manifest = read_object(manifest_path)
    thresholds = manifest.get("thresholds") if isinstance(manifest.get("thresholds"), dict) else None
    artifact = build_rule_artifact(manifest, thresholds=thresholds)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "artifact": str(output_path),
        "artifact_sha256": sha256_file(output_path),
        "source_count": len(artifact.get("sources") or []),
        "contracts": {
            contract_id: {
                "usable_real_replay_points": contract.get("usable_real_replay_points"),
                "trusted_rule_count": contract.get("trusted_rule_count"),
            }
            for contract_id, contract in (artifact.get("contracts") or {}).items()
        },
        "qps_used": False,
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

