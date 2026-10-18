#!/usr/bin/env python3
"""Build immutable XP v17 successor fixing all retained-unit argv hashes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parent
HERE = Path(__file__).resolve()
PRE_ID = "24d5f2f9f12d30c92ff6af621513d25963daba765cfed6d91798d37ee58e685b"
PRE_SHA = "8fea1df55260e26ad946787ab1b4d72a643af506198850408b6af0c289ce5cc5"
PRE = ROOT / "artifacts/mysql_xp_compute00_fresh_ttl_successor_v16" / PRE_ID
OUT = ROOT / "artifacts/mysql_xp_compute00_argv_hash_successor_v17"
IMAGE_ONLY_ATTEMPT = "xp-formal-qual-20260804T082652Z-a5f9121eeef9124e914316ff"
IMAGE_ONLY_UNIT = "ra-mysql-xp-v16-image-0582f5d645555ec6.service"


def canonical(value):
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                   allow_nan=False) + "\n"
    ).encode()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def file_map(root):
    return {
        path.relative_to(root).as_posix(): sha_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name not in ("bundle_manifest.json", "bundle_manifest.json.sha256")
    }


def patched_materializer():
    source = (PRE / "materializer/mysql_xp_v16_materializer.py").read_text()
    source = source.replace(
        'EXPIRED_ATTEMPT="xp-formal-qual-20260803T152502Z-fbffaf8b0debd96e4c836215";EXPIRED_AT="2026-08-03T19:25:02+00:00"',
        'EXPIRED_ATTEMPT="xp-formal-qual-20260803T152502Z-fbffaf8b0debd96e4c836215";EXPIRED_AT="2026-08-03T19:25:02+00:00"\n'
        'IMAGE_ONLY_ATTEMPT="%s";IMAGE_ONLY_UNIT="%s"' % (IMAGE_ONLY_ATTEMPT, IMAGE_ONLY_UNIT),
    )
    source = source.replace("ra-mysql-xp-v16-", "ra-mysql-xp-v17-")
    source = source.replace(
        "bootstrap_spec_v4_fresh_ttl_dedicated_image_output_split",
        "bootstrap_spec_v5_argv_hash_closed_fresh_ttl_dedicated_image_output_split",
    ).replace(
        "command_plan_v5_fresh_ttl_dedicated_image_output_split",
        "command_plan_v6_argv_hash_closed_fresh_ttl_dedicated_image_output_split",
    ).replace(
        "materials_manifest_v5_fresh_ttl_dedicated_image_output_split",
        "materials_manifest_v6_argv_hash_closed_fresh_ttl_dedicated_image_output_split",
    )
    anchor = 'direct["frozen_image_publish"]=image_direct;host["frozen_image_publish"]=image_host;polls["frozen_image_publish"]=poll(units["image"],image_direct)\n'
    closure = anchor + (
        ' for stage in ("physical_copy","jdk_publish","plan_only","bootstrap","adopt_readback","frozen_image_publish"):\n'
        '  expected=list(host[stage][6:])\n'
        '  if direct[stage]!=expected:raise Error("host/direct argv closure differs: "+stage)\n'
        '  polls[stage]["expected_exec_start_argv"]=expected\n'
        '  polls[stage]["expected_exec_start_argv_sha256"]=sha_bytes(canonical(expected))\n'
    )
    if source.count(anchor) != 1:
        raise RuntimeError("argv-hash repair anchor differs")
    source = source.replace(anchor, closure)
    anchor = " manifest_sha=write(output,\"materials_manifest.json\",manifest)\n"
    invalidation = (
        ' manifest["invalidated_failed_attempts"].append({' 
        '"attempt_id":IMAGE_ONLY_ATTEMPT,"failed_unit":IMAGE_ONLY_UNIT,'
        '"fresh_error":"image-only stage launched under argv-hash-inconsistent v16 plan",'
        '"image_only_zero_credit_abandoned":True,"formal_r0_credit":0,'
        '"accepted_rounds_credit":0,"rerun_prohibited":True,"delete_prohibited":True})\n'
        + anchor
    )
    if source.count(anchor) != 1:
        raise RuntimeError("invalidation anchor differs")
    return source.replace(anchor, invalidation)


def patched_test():
    source = (PRE / "tests/test_mysql_xp_v16_materializer.py").read_text()
    source = source.replace("XP_V16_BASE", "XP_V17_BASE")
    source = source.replace("mysql_xp_v16_materializer.py", "mysql_xp_v17_materializer.py")
    source = source.replace("ra-mysql-xp-v16-", "ra-mysql-xp-v17-")
    anchor = '  assert all(x.startswith("ra-mysql-xp-v17-") for x in s["attempt"]["executor_units"].values())\n'
    extra = anchor + (
        '  host=p["host_systemd_run_commands_after_separate_authorization"];polls=p["controller_poll_after_each_systemd_run"]\n'
        '  assert set(host)==set(polls)=={"physical_copy","jdk_publish","plan_only","bootstrap","adopt_readback","frozen_image_publish"}\n'
        '  for stage in sorted(host):\n'
        '   assert host[stage][6:]==polls[stage]["expected_exec_start_argv"]\n'
        '   assert M.sha_bytes(M.canonical(host[stage][6:]))==polls[stage]["expected_exec_start_argv_sha256"]\n'
        '   assert host[stage][4]=="--unit="+polls[stage]["success_state"]["Id"]\n'
        '   assert not any(x in host[stage] for x in ("--wait","--collect","--scope"))\n'
    )
    if source.count(anchor) != 1:
        raise RuntimeError("test argv anchor differs")
    source = source.replace(anchor, extra)
    anchor = '  assert m["invalidated_failed_attempts"][1]["attempt_id"]==M.EXPIRED_ATTEMPT and m["invalidated_failed_attempts"][1]["reuse_prohibited"] and m["invalidated_failed_attempts"][1]["delete_prohibited"]\n'
    extra = anchor + (
        '  invalid=m["invalidated_failed_attempts"][2]\n'
        '  assert invalid["attempt_id"]==M.IMAGE_ONLY_ATTEMPT and invalid["failed_unit"]==M.IMAGE_ONLY_UNIT\n'
        '  assert invalid["image_only_zero_credit_abandoned"] and invalid["rerun_prohibited"] and invalid["formal_r0_credit"]==invalid["accepted_rounds_credit"]==0\n'
        '  assert m["attempt_id"]!=M.IMAGE_ONLY_ATTEMPT and M.IMAGE_ONLY_ATTEMPT not in s["paths"]["attempt_root"]\n'
    )
    if source.count(anchor) != 1:
        raise RuntimeError("test invalidation anchor differs")
    return source.replace(anchor, extra)


def build():
    predecessor = json.loads((PRE / "bundle_manifest.json").read_text())
    identity_without_id = dict(predecessor)
    identity_without_id.pop("bundle_id")
    if (
        PRE.name != PRE_ID
        or sha_file(PRE / "bundle_manifest.json") != PRE_SHA
        or sha_bytes(canonical(identity_without_id)) != PRE_ID
        or file_map(PRE) != predecessor["files"]
    ):
        raise RuntimeError("v16 predecessor closure differs")
    publisher = (PRE / "runtime/mysql_xp_dedicated_frozen_image_publication_v1.py").read_bytes()
    content = {
        "builder/build_mysql_xp_compute00_argv_hash_successor_v17.py": HERE.read_bytes(),
        "runtime/mysql_xp_dedicated_frozen_image_publication_v1.py": publisher,
        "materializer/mysql_xp_v17_materializer.py": patched_materializer().encode(),
        "tests/test_mysql_xp_v17_materializer.py": patched_test().encode(),
        "README.md": (
            "XP v17 immutable successor. Recomputes and tests normalized inner argv hashes "
            "for all six retained systemd-run stages. The v16 image-only attempt is abandoned, "
            "rerun-prohibited and zero-credit. Every materialization requires a fresh attempt, "
            "fresh units, fresh paths and 240-minute TTL. MySQL-only; PostgreSQL mutation "
            "prohibited. Offline bundle: no data-plane action.\n"
        ).encode(),
    }
    files = {name: sha_bytes(value) for name, value in sorted(content.items())}
    identity = {
        "schema_version": "mysql_xp_compute00_argv_hash_successor_bundle_v17",
        "status": "PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED",
        "immutable": True,
        "predecessor_support_bundle_id": PRE_ID,
        "predecessor_support_manifest_sha256": PRE_SHA,
        "frozen_image_publisher_sha256": sha_bytes(publisher),
        "invalidated_image_only_attempt": IMAGE_ONLY_ATTEMPT,
        "invalidated_image_only_unit": IMAGE_ONLY_UNIT,
        "invalidated_image_only_zero_credit": True,
        "invalidated_image_only_rerun_prohibited": True,
        "new_attempt_required": True,
        "fresh_image_authority_required_even_if_image_present": True,
        "all_six_normalized_argv_hashes_required": True,
        "ttl_minutes_required": 240,
        "unit_prefix": "ra-mysql-xp-v17-",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_r0_authorized": False,
        "accepted_round_authorized": False,
        "accepted_rounds_credit": 0,
        "data_plane_actions": 0,
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity, bundle_id=bundle_id)
    destination = OUT / bundle_id
    if destination.exists():
        return destination
    OUT.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".xp-v17-", dir=OUT))
    try:
        for name, value in content.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        raw = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(raw)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(raw) + "  bundle_manifest.json\n"
        )
        for path in sorted(stage.rglob("*"), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(stage, 0o555)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if file_map(destination) != files:
        raise RuntimeError("v17 closure differs")
    return destination


if __name__ == "__main__":
    result = build()
    print(json.dumps({
        "status": "PASS_IMMUTABLE_OFFLINE_NOT_EXECUTED",
        "bundle": str(result),
        "bundle_id": result.name,
        "manifest_sha256": sha_file(result / "bundle_manifest.json"),
        "data_plane_actions": 0,
    }, sort_keys=True))
