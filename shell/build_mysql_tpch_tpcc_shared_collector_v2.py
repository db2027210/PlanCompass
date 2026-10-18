#!/usr/bin/env python3
"""Seal the shared collector arity-compatibility successor without launch."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "4f89a52402d7b6804bd6313e2e143bb852a8803b900a5bda59a9577d7ff18b0c"
PARENT_SHA256 = "5c63eac062285459d6b812cfcbc7ef7dcdc8b0ea5766171e81b220d208ebdf74"
PARENT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v1" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_tpch_tpcc_shared_collector_v2"
ENTRY = "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py"
BUILDER = "authority/build_mysql_tpch_tpcc_shared_collector_v2.py"
TEST = "tests/test_shared_collector_tpch_client_readback_arity_v2.py"
INVALIDATION = "authority/tpch_a_solo_v3_arity_failure_invalidation.json"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def verify_parent():
    manifest_path = PARENT / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if (
        manifest.get("bundle_id") != PARENT_ID
        or digest(manifest_path) != PARENT_SHA256
        or digest_bytes(canonical(identity)) != PARENT_ID
        or manifest.get("schema_version")
        != "mysql_tpch_tpcc_shared_collector_bundle_v1"
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("shared collector v1 parent identity differs")
    for relative, expected in (manifest.get("files") or {}).items():
        path = PARENT / relative
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise BuildError("shared collector v1 closure differs: " + relative)
    return manifest


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def patch_entry(payload):
    anchor = b'''_original_rolling_process_inventory = core.rolling_process_inventory
_active_profile = None
'''
    replacement = b'''_original_rolling_process_inventory = core.rolling_process_inventory
_active_profile = None


def validate_tpch_client_container_readback(
    container_row,
    expected,
    binding,
    tmpfs_destination,
    host_tmpfs_directory=None,
    target_major_minor=None,
):
    """Preserve legacy four-argument TPC-H calls after the six-arg hardening.

    The two added arguments are consumed only by TPC-C tmpfs/volume checks.
    TPC-H requires host_tmpfs_bind, whose full outer readback still supplies
    the concrete paths while the counter bootstrap and rolling identity check
    intentionally use the legacy four-argument form.
    """
    if expected.get("measurement_output_medium") != "host_tmpfs_bind":
        raise core.QualificationError(
            "TPC-H client readback wrapper received non-TPC-H envelope"
        )
    return _original_tpch_validate_client(
        container_row,
        expected,
        binding,
        tmpfs_destination,
        host_tmpfs_directory,
        target_major_minor,
    )
'''
    payload = replace_once(payload, anchor, replacement, "TPC-H wrapper insertion")
    return replace_once(
        payload,
        b"core.validate_client_container_readback = _original_tpch_validate_client\n",
        b"core.validate_client_container_readback = validate_tpch_client_container_readback\n",
        "TPC-H wrapper activation",
    )


def invalidation():
    return {
        "schema_version": "mysql_tpch_debug_qualification_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "scenario": "tpch_memory",
        "qualification_attempt_id":
            "tpch-current-a-solo-v3-20260731t173000z-6c3809b818ae9bbba8e70884",
        "unit": "ra-mysql-tpch-a-solo-v3-6c3809b8.service",
        "parent_collector_bundle_id": PARENT_ID,
        "failure":
            "TPC_H_CLIENT_READBACK_SIX_ARGUMENT_SIGNATURE_CALLED_FROM_LEGACY_FOUR_ARGUMENT_PATH",
        "workload_started": False,
        "r0_credit": 0,
        "candidate_credit": 0,
        "top5_replay_credit": 0,
        "accepted_round_credit": 0,
        "fresh_qualification_attempt_required": True,
        "postgresql_mutation": False,
    }


TEST_SOURCE = r'''#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    manifest = json.loads((ROOT / "bundle_manifest.json").read_text())
    unsigned = dict(manifest)
    unsigned.pop("bundle_id")
    assert manifest["bundle_id"] == ROOT.name
    assert hashlib.sha256(canonical(unsigned)).hexdigest() == ROOT.name
    assert manifest["schema_version"] == "mysql_tpch_tpcc_shared_collector_bundle_v2"
    assert manifest["formal_launch_authorized"] is False
    assert manifest["accepted_round_authorized"] is False
    for relative, expected in manifest["files"].items():
        path = ROOT / relative
        assert path.is_file() and not path.is_symlink()
        assert digest(path) == expected

    module = runpy.run_path(
        str(ROOT / "qualification/mysql_tpch_tpcc_shared_collector_entry_v1.py")
    )
    calls = []

    def fake(*args):
        calls.append(args)
        assert len(args) == 6
        return {"ok": True, "args": args}

    wrapper = module["validate_tpch_client_container_readback"]
    wrapper.__globals__["_original_tpch_validate_client"] = fake
    expected = {"measurement_output_medium": "host_tmpfs_bind"}
    assert wrapper("row", expected, "binding", "/measurement")["ok"] is True
    assert calls[-1][-2:] == (None, None)
    assert wrapper(
        "row", expected, "binding", "/measurement", "/dev/shm/attempt", "259:1"
    )["ok"] is True
    assert calls[-1][-2:] == ("/dev/shm/attempt", "259:1")
    try:
        wrapper(
            "row", {"measurement_output_medium": "tmpfs"}, "binding", "/measurement"
        )
    except module["core"].QualificationError:
        pass
    else:
        raise AssertionError("TPC-H wrapper accepted a TPC-C envelope")


if __name__ == "__main__":
    main()
'''.encode("utf-8")


def freeze(path):
    for candidate in sorted(Path(path).rglob("*"), reverse=True):
        if candidate.is_symlink():
            raise BuildError("symlink in shared collector v2")
        os.chmod(candidate, 0o444 if candidate.is_file() else 0o555)
    os.chmod(path, 0o555)


def main():
    parent = verify_parent()
    payload = {}
    modes = {}
    for relative in parent["files"]:
        path = PARENT / relative
        payload[relative] = path.read_bytes()
        modes[relative] = stat.S_IMODE(path.stat().st_mode)
    payload[ENTRY] = patch_entry(payload[ENTRY])
    payload[BUILDER] = Path(__file__).read_bytes()
    payload[TEST] = TEST_SOURCE
    payload[INVALIDATION] = canonical(invalidation())
    modes.update({ENTRY: 0o555, BUILDER: 0o444, TEST: 0o444, INVALIDATION: 0o444})

    manifest = {
        "schema_version": "mysql_tpch_tpcc_shared_collector_bundle_v2",
        "status":
            "SEALED_TPCH_ARITY_COMPATIBLE_BYTE_IDENTICAL_FOUR_FLOW_COLLECTOR_NOT_LAUNCH_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_manifest_sha256": PARENT_SHA256,
        "fix": "TPC_H_FOUR_AND_SIX_ARGUMENT_CLIENT_READBACK_COMPATIBILITY",
        "same_collector_required_for_all_four_flows": True,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "files": {name: digest_bytes(data) for name, data in sorted(payload.items())},
    }
    bundle_id = digest_bytes(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / bundle_id
    if target.exists():
        raise BuildError("immutable shared collector v2 already exists")
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    for relative, data in payload.items():
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        os.chmod(destination, modes[relative] & ~0o222)
    raw = canonical(manifest)
    (stage / "bundle_manifest.json").write_bytes(raw)
    (stage / "bundle_manifest.json.sha256").write_text(
        digest_bytes(raw) + "  bundle_manifest.json\n", encoding="ascii"
    )
    freeze(stage)
    os.rename(stage, target)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "bundle_id": bundle_id,
                "bundle": str(target),
                "manifest_sha256": digest(target / "bundle_manifest.json"),
                "formal_launch_authorized": False,
                "accepted_round_authorized": False,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (BuildError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
