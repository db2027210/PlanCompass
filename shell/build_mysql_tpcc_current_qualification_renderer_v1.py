#!/usr/bin/env python3
"""Build the immutable current-bundle TPC-C qualification renderer."""

import hashlib
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
FAMILY = ROOT / "artifacts/mysql_tpcc_current_qualification_renderer_v1"
SOURCES = (
    "shell/mysql_tpcc_current_qualification_spec_authority_renderer_v1.py",
    "shell/mysql_tpch_client_immutable_identity_v1.py",
)


def canonical(value):
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    FAMILY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tpcc-renderer-v1-", dir=str(FAMILY)) as raw:
        stage = Path(raw)
        renderer = stage / "renderer"
        renderer.mkdir()
        files = {}
        for relative in SOURCES:
            source = ROOT / relative
            target = renderer / source.name
            shutil.copyfile(str(source), str(target))
            files[str(target.relative_to(stage))] = sha256(target)
        manifest = {
            "schema_version":
                "mysql_tpcc_current_qualification_renderer_bundle_v1",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "qualification_only": True,
            "debug_only": True,
            "capture_authorized": False,
            "formal_r0_authorized": False,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "data_plane_launch_authority": "main_agent_only",
            "files": dict(sorted(files.items())),
        }
        bundle_id = hashlib.sha256(canonical(manifest)).hexdigest()
        manifest["bundle_id"] = bundle_id
        manifest_path = stage / "bundle_manifest.json"
        manifest_path.write_bytes(canonical(manifest))
        (stage / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % sha256(manifest_path),
            encoding="ascii",
        )
        target = FAMILY / bundle_id
        if target.exists():
            existing = json.loads((target / "bundle_manifest.json").read_text())
            if existing != manifest:
                raise RuntimeError("existing content-address collision")
            print(str(target))
            return
        stage.rename(target)
        print(str(target))


if __name__ == "__main__":
    main()
