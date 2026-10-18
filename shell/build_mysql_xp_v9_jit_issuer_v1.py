#!/usr/bin/env python3
"""Build the immutable local-only XP v9 JIT issuer bundle."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
V9_ID = "168f1505fa89a80dee9fb39533300b7e1e1a4ecce6e9d60af2704072e4cb67ac"
V9_SHA = "02c85d1bc350fbd8a95a0e5007581fd16721402c6faec59f48ee89dd2151ca26"
V9 = ROOT / "artifacts/mysql_xp_formal10_launch_v9" / V9_ID
OUTPUT = ROOT / "artifacts/mysql_xp_v9_jit_issuer_v1"
ISSUER = "issuer/mysql_xp_v9_jit_issuer_v1.py"
MATERIALIZER = (
    "materializer/mysql_xp_v9_qualification_materializer_v1.py"
)
TEST = "tests/test_mysql_xp_v9_jit_issuer_v1.py"
EMBEDDED_RUNTIME_ROOT = V9 / "predecessor/predecessor"
EMBEDDED_RUNTIME_TEST_RUNNER = (
    EMBEDDED_RUNTIME_ROOT / "tests/run_sealed_bundle_tests.py"
)


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


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(
            "%s patch cardinality differs: %d"
            % (label, source.count(old))
        )
    return source.replace(old, new)


def verify_v9():
    manifest_path = V9 / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        V9.name != V9_ID
        or manifest.get("bundle_id") != V9_ID
        or digest(manifest_path) != V9_SHA
        or manifest.get("self_contained_bundle_tests") is not True
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
    ):
        raise BuildError("XP v9 identity differs")
    # v9 advertises self-contained bundle tests.  Its inherited runtime test
    # runner derives the runtime root from its own parent and then requires
    # that directory name to equal the embedded runtime manifest bundle_id.
    # The v9 layout puts that runner below ``predecessor/predecessor`` instead,
    # so it cannot run against the sealed runtime as packaged.  Do not issue
    # a fresh JIT authority from a bundle whose claimed local test closure is
    # demonstrably unusable; a successor must either package the runtime at
    # its content-addressed root or use a runner explicitly parameterized and
    # sealed for the embedded location.
    runtime_manifest_path = EMBEDDED_RUNTIME_ROOT / "manifest.json"
    if (
        not EMBEDDED_RUNTIME_TEST_RUNNER.is_file()
        or not runtime_manifest_path.is_file()
    ):
        raise BuildError("XP v9 embedded runtime self-test closure is absent")
    runtime_manifest = json.loads(runtime_manifest_path.read_text(encoding="utf-8"))
    runner_source = EMBEDDED_RUNTIME_TEST_RUNNER.read_text(encoding="utf-8")
    if (
        "BUNDLE = Path(__file__).resolve().parents[1]" in runner_source
        and EMBEDDED_RUNTIME_ROOT.name != runtime_manifest.get("bundle_id")
    ):
        raise BuildError(
            "XP v9 embedded runtime self-test runner is not relocatable"
        )
    return manifest


def patched_materializer():
    source = (
        ROOT / "shell/mysql_xp_qualification_bootstrap_materials_v5.py"
    ).read_text(encoding="utf-8")
    source = source.replace(
        "for the XP v8 successor", "for the XP v9 successor", 1
    )
    source = replace_once(
        source,
        'ROOT = Path(__file__).resolve().parents[1]\n',
        'ROOT = Path(__file__).resolve().parents[1]\n'
        'ARTIFACTS_ROOT = ROOT.parents[1]\n',
        "materializer artifact root",
    )
    source = replace_once(
        source,
        'DEFAULT_BUNDLE = (\n'
        '    ROOT / "artifacts/mysql_xp_formal10_launch_v8/"\n'
        '    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"\n'
        ')\n',
        'DEFAULT_BUNDLE = (\n'
        '    ARTIFACTS_ROOT / "mysql_xp_formal10_launch_v9/"\n'
        '    "%s"\n'
        ')\n' % V9_ID,
        "materializer v9 bundle",
    )
    source = replace_once(
        source,
        '    "2f7824bf1b7f234ebd754bf6e3283420c6eaa789618a7a38f6d75472338b2087"\n',
        '    "%s"\n' % V9_ID,
        "materializer expected bundle",
    )
    source = replace_once(
        source,
        '    "8cb77864c55b5b51d846abc736c5ec872cb0cdcf7883550e86652e5e190c6412"\n',
        '    "%s"\n' % V9_SHA,
        "materializer expected manifest",
    )
    source = replace_once(
        source,
        'DEFAULT_OUTPUT_ROOT = ROOT / "artifacts/mysql_xp_qualification_bootstrap_v8"\n',
        'DEFAULT_OUTPUT_ROOT = (\n'
        '    ARTIFACTS_ROOT / "mysql_xp_qualification_bootstrap_v9"\n'
        ')\n',
        "materializer v9 output",
    )
    source = replace_once(
        source,
        'SYSTEMD_HELP_FIXTURE = (\n'
        '    ROOT / "artifacts/worker_systemd_run_help_20260731/"\n'
        '    "node02_node04_systemd_run_help.stdout"\n'
        ')\n',
        'SYSTEMD_HELP_FIXTURE = (\n'
        '    DEFAULT_BUNDLE / "authority/node02_systemd_run_help.stdout"\n'
        ')\n',
        "materializer v9 help fixture",
    )
    return source.encode("utf-8")


def freeze(root):
    for path in sorted(Path(root).rglob("*"), reverse=True):
        if path.is_symlink():
            raise BuildError("issuer symlink prohibited")
        os.chmod(path, stat.S_IMODE(path.stat().st_mode) & ~0o222)
    os.chmod(root, stat.S_IMODE(Path(root).stat().st_mode) & ~0o222)


def verify_bundle(path):
    manifest_path = path / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    unsigned = dict(manifest)
    bundle_id = unsigned.pop("bundle_id", None)
    actual = {}
    bytecode = []
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise BuildError("issuer symlink prohibited")
        if stat.S_IMODE(candidate.stat().st_mode) & 0o222:
            raise BuildError("issuer writable entry")
        if candidate.name == "__pycache__" or candidate.suffix == ".pyc":
            bytecode.append(str(candidate))
        if candidate.is_file():
            relative = candidate.relative_to(path).as_posix()
            if relative not in (
                "bundle_manifest.json",
                "bundle_manifest.json.sha256",
            ):
                actual[relative] = digest(candidate)
    if (
        bundle_id != path.name
        or sha_bytes(canonical(unsigned)) != bundle_id
        or actual != manifest.get("files")
        or manifest.get("v9_bundle_id") != V9_ID
        or manifest.get("issuer_valid_minutes") != 60
        or manifest.get("child_runtime_valid_minutes_max") != 30
        or manifest.get("formal_launch_authorized") is not False
        or manifest.get("accepted_round_authorized") is not False
        or bytecode
    ):
        raise BuildError("issuer immutable identity differs")
    return manifest


def publish():
    verify_v9()
    payload = {
        ISSUER:
            (ROOT / "shell/mysql_xp_v9_jit_issuer_v1.py").read_bytes(),
        MATERIALIZER: patched_materializer(),
        TEST:
            (ROOT / "tests/test_mysql_xp_v9_jit_issuer_v1.py").read_bytes(),
        "authority/v9_bundle_manifest.json":
            (V9 / "bundle_manifest.json").read_bytes(),
        "authority/v9_bundle_manifest.json.sha256":
            (V9 / "bundle_manifest.json.sha256").read_bytes(),
        "authority/v9_xp_executable_launch_contract_v3.json":
            (V9 / "xp_executable_launch_contract_v3.json").read_bytes(),
    }
    files = {
        relative: sha_bytes(value)
        for relative, value in sorted(payload.items())
    }
    gate = {
        "formal_scope": "mysql_only",
        "database_cpu_quota_cores": 5.3,
        "database_io_read_bps": 29360128,
        "database_io_write_bps": 524288,
        "capture_only_io_bps_per_direction": 1205862400,
        "capture_accepted_round_credit": 0,
        "required_nonempty_subsets": 127,
        "required_trace_transactions": [
            "AT1", "AT2", "AT3", "AT4", "AT5", "AT6",
        ],
        "authorization_outside_attempt_required": True,
        "private_directory_mode": "0700",
        "private_file_mode": "0600",
    }
    identity = {
        "schema_version": "mysql_xp_v9_jit_issuer_bundle_v1",
        "status": "SEALED_LOCAL_ISSUER_NO_FORMAL_ATTEMPT_GENERATED",
        "v9_bundle_id": V9_ID,
        "v9_bundle_manifest_sha256": V9_SHA,
        "issuer_valid_minutes": 60,
        "child_runtime_valid_minutes_default": 25,
        "child_runtime_valid_minutes_max": 30,
        "runtime_60min_prohibited_by_sealed_service": True,
        "runtime_service_max_seconds": 1800,
        "just_in_time_child_generation": True,
        "formal_attempt_generated_by_build": False,
        "remote_actions": 0,
        "formal_r0_authorized": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "gate_binding": gate,
        "exact_generation_commands": {
            "prepare_60min_issuer_lease": [
                "python3", "-B",
                "<ISSUER_BUNDLE>/issuer/mysql_xp_v9_jit_issuer_v1.py",
                "prepare-lease",
                "--bundle", "<V9_BUNDLE>",
                "--issuer-token-file", "<PRIVATE_0600_TOKEN>",
                "--output", "<PRIVATE_0700_WORKDIR>/issuer_lease.json",
            ],
            "issue_25min_child_attempt": [
                "python3", "-B",
                "<ISSUER_BUNDLE>/issuer/mysql_xp_v9_jit_issuer_v1.py",
                "issue",
                "--bundle", "<V9_BUNDLE>",
                "--issuer-token-file", "<PRIVATE_0600_TOKEN>",
                "--lease", "<PRIVATE_0700_WORKDIR>/issuer_lease.json",
                "--output-root",
                "<PROJECT>/artifacts/mysql_xp_qualification_bootstrap_v9",
                "--runtime-valid-minutes", "25",
            ],
        },
        "files": files,
    }
    bundle_id = sha_bytes(canonical(identity))
    manifest = dict(identity)
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    final = OUTPUT / bundle_id
    if final.exists() or final.is_symlink():
        freeze(final)
        verify_bundle(final)
        return final
    stage = OUTPUT / (".stage-" + bundle_id + "-" + secrets.token_hex(6))
    stage.mkdir(mode=0o700)
    try:
        for relative, value in payload.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
            os.chmod(target, 0o555 if relative.endswith(".py") else 0o444)
        manifest_payload = canonical(manifest)
        (stage / "bundle_manifest.json").write_bytes(manifest_payload)
        (stage / "bundle_manifest.json.sha256").write_text(
            sha_bytes(manifest_payload) + "  bundle_manifest.json\n",
            encoding="ascii",
        )
        os.chmod(stage / "bundle_manifest.json", 0o444)
        os.chmod(stage / "bundle_manifest.json.sha256", 0o444)
        os.rename(stage, final)
    except BaseException:
        raise
    freeze(final)
    verify_bundle(final)
    return final


if __name__ == "__main__":
    result = publish()
    print(
        json.dumps(
            {
                "status": "SEALED_LOCAL_ISSUER_NO_FORMAL_ATTEMPT_GENERATED",
                "bundle": str(result),
                "bundle_id": result.name,
                "bundle_manifest_sha256":
                    digest(result / "bundle_manifest.json"),
                "formal_attempt_generated": False,
                "remote_actions": 0,
            },
            sort_keys=True,
        )
    )
