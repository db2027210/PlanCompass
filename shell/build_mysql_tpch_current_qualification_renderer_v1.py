#!/usr/bin/env python3
"""Build the immutable live-schema TPC-H qualification renderer."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shell/mysql_tpch_current_qualification_spec_renderer_v1.py"
FAMILY = ROOT / "artifacts/mysql_tpch_current_qualification_renderer_v1"


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    FAMILY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tpch-current-renderer-v1-", dir=str(FAMILY)) as raw:
        stage = Path(raw)
        target_source = stage / "renderer" / SOURCE.name
        target_source.parent.mkdir()
        shutil.copyfile(str(SOURCE), str(target_source))
        manifest = {
            "schema_version": "mysql_tpch_current_qualification_renderer_bundle_v1",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "qualification_only": True,
            "live_show_databases_readback_only": True,
            "fresh_metric_paths_required": True,
            "workload_launch_implemented": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": {str(target_source.relative_to(stage)): sha256(target_source)},
        }
        bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = bundle_id
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha256(manifest_path), encoding="ascii"
        )
        destination = FAMILY / bundle_id
        if destination.exists():
            if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
                raise RuntimeError("existing renderer bundle differs")
        else:
            stage.rename(destination)
        print(str(destination))


if __name__ == "__main__":
    main()
