#!/usr/bin/env python3
"""Seal the JOB dedicated-daemon mount-helper successor without launching."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import stat


ROOT = Path(__file__).resolve().parents[1]
PARENT_ID = "0da7aa3b81240e46dcf2b7040fd4cc232fa5eff819e4adbef5b490d60d085e05"
PARENT_SHA = "983496d3b189ff518b7d26156f394f813746c517e4e64f1ca28e6a85785d9fc1"
PARENT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v6" / PARENT_ID
OUTPUT = ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v7"
SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v7"
AUTHORITY = "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py"
MATERIALIZER = "qualification/mysql_job_dedicated_daemon_materializer_v5.py"
TEST = "tests/test_mysql_job_module_closure_successor_v5.py"
BUILDER = "authority/build_mysql_job_dedicated_mount_helper_successor_v7.py"
INVALIDATION = "authority/mysql_job_0da7_measurement_permission_invalidation_20260801.json"
FAILED_ATTEMPT = "job-v2-20260731t164041z-9fddce9e4b34019f0dee34d3"
BROKEN_HELPER_BUNDLE_ID = "f89f31eecdab1e20d3b16cb108c29e7e284cf5c1b8afccf8fc5f38cef8b9f037"
BROKEN_HELPER_MANIFEST_SHA256 = "5e2470ee92814931339657e068598f9731dfb7327cda30961eb7229fa676f880"
BROKEN_HELPER_ATTEMPT = "job-v2-20260731t165045z-974ce63a7b2f39a7a6cf254c"
UNPRIVILEGED_READBACK_BUNDLE_ID = "c36832d83c9adac96cbec71dd1b151cf70d5ba59ab32c58303355224a85fa17a"
UNPRIVILEGED_READBACK_MANIFEST_SHA256 = "aa0ee08e0c7aea5121ff1fbb19bf29d0ff98a41abae4032f3c2c7ede0e2973b1"
UNPRIVILEGED_READBACK_ATTEMPT = "job-v2-20260731t165326z-296f38839da6c6a5f77638c3"
NSENTER_SHA256 = "d534366bc3ba23eead6e75f1838189df199ad1554da77a9889fbc036417e6c43"


class BuildError(RuntimeError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def replace_once(source, old, new, label):
    if source.count(old) != 1:
        raise BuildError(label + " patch cardinality differs")
    return source.replace(old, new)


def verify_parent():
    path = PARENT / "bundle_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    identity = dict(manifest)
    identity.pop("bundle_id", None)
    if (manifest.get("bundle_id") != PARENT_ID
            or manifest.get("schema_version")
            != "mysql_job_raw_all_subset_three_level_successor_bundle_v6"
            or digest(path) != PARENT_SHA or sha(canonical(identity)) != PARENT_ID):
        raise BuildError("JOB v6 parent identity differs")
    for relative, expected in manifest.get("files", {}).items():
        candidate = PARENT / relative
        if candidate.is_symlink() or not candidate.is_file() or digest(candidate) != expected:
            raise BuildError("JOB v6 parent closure differs: " + relative)
    return manifest


def patch_authority(payload):
    source = payload.decode("utf-8")
    helper = r'''

NSENTER_PATH = Path("/usr/bin/nsenter")
NSENTER_SHA256 = "d534366bc3ba23eead6e75f1838189df199ad1554da77a9889fbc036417e6c43"


def _dedicated_daemon_mount_measurement(spec, measurement):
    """Create the exact host tmpfs through only the dedicated JOB daemon."""
    nsenter_stat = NSENTER_PATH.stat(follow_symlinks=False)
    if (NSENTER_PATH.is_symlink() or not NSENTER_PATH.is_file()
            or sha256(NSENTER_PATH) != NSENTER_SHA256
            or nsenter_stat.st_uid != 0 or nsenter_stat.st_gid != 0
            or stat.S_IMODE(nsenter_stat.st_mode) != 0o755):
        raise AuthorityError("manifest-bound host nsenter identity differs")
    helper_name = "ra_mysql_job_v2_mount_" + hashlib.sha256(
        str(measurement).encode("utf-8")
    ).hexdigest()[:20]
    script = (
        'set -eu; target="$1"; size="$2"; uid="$3"; gid="$4"; '
        'enter="/usr/local/bin/nsenter -t 1 -m --root=/proc/1/root --wd=/proc/1/root"; '
        '$enter /usr/bin/mkdir -m 0700 -- "$target"; '
        '$enter /usr/bin/mount -t tmpfs -o '
        '"rw,nosuid,nodev,noexec,size=${size},uid=${uid},gid=${gid},mode=0700" '
        'tmpfs "$target"'
    )
    argv = [
        "docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock",
        "run", "--rm", "--name", helper_name,
        "--label", "ra.owner=mysql_algorithm_c_job_v2",
        "--label", "ra.role=host_measurement_tmpfs_mount_helper",
        "--label", "ra.attempt=" + spec["attempt_token"],
        "--network", "none", "--read-only", "--pid", "host", "--privileged",
        "--mount", "type=bind,src=/usr/bin/nsenter,dst=/usr/local/bin/nsenter,readonly",
        "--entrypoint", "/bin/sh", spec["mysql_image"]["image_id"],
        "-ceu", script, "mount-helper", str(measurement),
        str(CLIENT_ENVELOPE["tmpfs_bytes"]), str(CLIENT_RUNTIME_UID),
        str(CLIENT_RUNTIME_GID),
    ]
    result = _run(argv, timeout=60, check=False)
    if result.returncode != 0:
        raise AuthorityError(
            "dedicated JOB measurement mount helper failed: %s" % result.stderr
        )


def _dedicated_daemon_root_findmnt_major_minor(image_id, path):
    """Read one root-owned daemon path device without exposing host root."""
    nsenter_stat = NSENTER_PATH.stat(follow_symlinks=False)
    if (NSENTER_PATH.is_symlink() or not NSENTER_PATH.is_file()
            or sha256(NSENTER_PATH) != NSENTER_SHA256
            or nsenter_stat.st_uid != 0 or nsenter_stat.st_gid != 0
            or stat.S_IMODE(nsenter_stat.st_mode) != 0o755):
        raise AuthorityError("manifest-bound host nsenter identity differs")
    helper_name = "ra_mysql_job_v2_findmnt_" + hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()[:20]
    script = (
        'exec /usr/local/bin/nsenter -t 1 -m --root=/proc/1/root '
        '--wd=/proc/1/root /usr/bin/findmnt -n -o MAJ:MIN --target "$1"'
    )
    result = _run([
        "docker", "--host", "unix:///run/ra-docker-mysql-job-v1.sock",
        "run", "--rm", "--name", helper_name,
        "--label", "ra.owner=mysql_algorithm_c_job_v2",
        "--label", "ra.role=host_graphdriver_findmnt_helper",
        "--network", "none", "--read-only", "--pid", "host", "--privileged",
        "--mount", "type=bind,src=/usr/bin/nsenter,dst=/usr/local/bin/nsenter,readonly",
        "--entrypoint", "/bin/sh", image_id,
        "-ceu", script, "findmnt-helper", str(path),
    ], timeout=60, check=False)
    value = result.stdout.strip()
    if result.returncode != 0 or re.fullmatch(r"[0-9]+:[0-9]+", value) is None:
        raise AuthorityError(
            "dedicated JOB graphdriver findmnt helper failed: %s" % result.stderr
        )
    return value

'''
    source = replace_once(source, "\ndef execute_bootstrap(spec, output, main_agent_token_file):\n",
                          helper + "\ndef execute_bootstrap(spec, output, main_agent_token_file):\n",
                          "mount helper insertion")
    old = '''    measurement.mkdir(mode=0o700)
    _run([
        "mount", "-t", "tmpfs", "-o",
        "rw,nosuid,nodev,noexec,size=%d,uid=%d,gid=%d,mode=0700"
        % (CLIENT_ENVELOPE["tmpfs_bytes"], CLIENT_RUNTIME_UID, CLIENT_RUNTIME_GID),
        "tmpfs", str(measurement),
    ])
'''
    source = replace_once(source, old,
                          "    _dedicated_daemon_mount_measurement(spec, measurement)\n",
                          "direct mount replacement")
    source = replace_once(
        source,
        '            mount_devices[path] = _run(["findmnt", "-n", "-o", "MAJ:MIN", "--target", path], timeout=30).stdout.strip()\n',
        '            mount_devices[path] = _dedicated_daemon_root_findmnt_major_minor(row.get("Image"), path)\n',
        "graphdriver root findmnt replacement",
    )
    return source.encode("utf-8")


def patch_materializer(payload):
    source = payload.decode("utf-8")
    source = replace_once(source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v6"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v7"',
        "materializer schema")
    source = replace_once(source,
        '"mysql_job_dedicated_daemon_materials_v6"',
        '"mysql_job_dedicated_daemon_materials_v7"',
        "materials schema")
    marker = '                "job-v2-20260731t163142z-4208a76411f1b68f98a5e559",\n'
    return replace_once(source, marker,
        marker + '                "' + FAILED_ATTEMPT + '",\n'
        + '                "' + BROKEN_HELPER_ATTEMPT + '",\n'
        + '                "' + UNPRIVILEGED_READBACK_ATTEMPT + '",\n',
        "failed attempt invalidation").encode("utf-8")


def patch_test(payload):
    source = payload.decode("utf-8")
    source = replace_once(source, "JOB v6 successor", "JOB v7 successor", "test title")
    source = replace_once(source,
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v6"',
        'SCHEMA = "mysql_job_raw_all_subset_three_level_successor_bundle_v7"',
        "test schema")
    anchor = '''    assert '"mysql_job_authorization_install_plan_v2"' in materializer
'''
    injected = anchor + '''    authority = (ROOT / "qualification/mysql_job_distributed_v2_authority_tmpfs_v2.py").read_text()
    assert "_dedicated_daemon_mount_measurement" in authority
    assert "unix:///run/ra-docker-mysql-job-v1.sock" in authority
    assert '\"--pid\", \"host\", \"--privileged\"' in authority
    assert "sha256(NSENTER_PATH) != NSENTER_SHA256" in authority
    assert "_dedicated_daemon_root_findmnt_major_minor" in authority
    assert "host_graphdriver_findmnt_helper" in authority
'''
    return replace_once(source, anchor, injected, "mount helper assertions").encode("utf-8")


def invalidation():
    return {
        "schema_version": "mysql_job_0da7_measurement_permission_invalidation_v1",
        "status": "INVALID_ZERO_CREDIT",
        "invalidated_at_utc": "2026-08-01T00:45:00+08:00",
        "invalidated_bundle_id": PARENT_ID,
        "invalidated_manifest_sha256": PARENT_SHA,
        "invalidated_attempt_token": FAILED_ATTEMPT,
        "reason": "ROOT_OWNED_0711_MEASUREMENT_PARENT_REJECTED_UNPRIVILEGED_DIRECT_MKDIR_BEFORE_ANY_WORKLOAD",
        "workload_started": False,
        "formal_r0_credit": 0, "candidate_credit": 0,
        "top5_replay_credit": 0, "accepted_round_credit": 0,
        "fresh_attempt_required": True,
        "secondary_invalidation": {
            "bundle_id": BROKEN_HELPER_BUNDLE_ID,
            "manifest_sha256": BROKEN_HELPER_MANIFEST_SHA256,
            "attempt_token": BROKEN_HELPER_ATTEMPT,
            "reason": "MOUNT_HELPER_USED_ABSENT_DATABASE_IMAGE_SPEC_KEY_BEFORE_ANY_WORKLOAD",
            "credit": 0,
        },
        "tertiary_invalidation": {
            "bundle_id": UNPRIVILEGED_READBACK_BUNDLE_ID,
            "manifest_sha256": UNPRIVILEGED_READBACK_MANIFEST_SHA256,
            "attempt_token": UNPRIVILEGED_READBACK_ATTEMPT,
            "reason": "UNPRIVILEGED_GRAPHDRIVER_FINDMNT_REJECTED_ROOT_OWNED_OVERLAY_PATH_AFTER_BOOTSTRAP",
            "credit": 0,
        },
        "dedicated_daemon_helper_probe": {
            "status": "PASS_CREATED_READBACK_UNMOUNTED_REMOVED",
            "target": "/dev/shm/ra_mysql_job_v2/.probe-job-mount-20260801t0047",
            "size_bytes": 1048576, "uid": 1008, "gid": 1008, "mode": "0700",
            "postgresql_mutation": False,
        },
    }


def freeze(path):
    for candidate in sorted(Path(path).rglob("*"), reverse=True):
        if candidate.is_symlink():
            raise BuildError("symlink in successor")
        os.chmod(candidate, 0o444 if candidate.is_file() else 0o555)
    os.chmod(path, 0o555)


def build():
    parent = verify_parent()
    payload, modes = {}, {}
    for relative in parent["files"]:
        if relative == "closure.json":
            continue
        path = PARENT / relative
        payload[relative] = path.read_bytes()
        modes[relative] = stat.S_IMODE(path.stat().st_mode)
    payload[AUTHORITY] = patch_authority(payload[AUTHORITY])
    payload[MATERIALIZER] = patch_materializer(payload[MATERIALIZER])
    payload[TEST] = patch_test(payload[TEST])
    payload[INVALIDATION] = canonical(invalidation())
    payload[BUILDER] = Path(__file__).read_bytes()
    modes.update({AUTHORITY: 0o555, MATERIALIZER: 0o555, TEST: 0o444,
                  INVALIDATION: 0o444, BUILDER: 0o444})
    closure = json.loads((PARENT / "closure.json").read_text(encoding="utf-8"))
    closure.update({
        "schema_version": "mysql_job_module_closure_v7",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "dedicated_daemon_host_mount_helper": {
            "socket": "/run/ra-docker-mysql-job-v1.sock",
            "host_nsenter_sha256": NSENTER_SHA256,
            "target_prefix": "/dev/shm/ra_mysql_job_v2/",
            "graphdriver_findmnt_read_only": True,
            "postgresql_mutation_prohibited": True,
        },
        "invalidated_measurement_permission_parent": INVALIDATION,
    })
    closure["files"] = {name: sha(data) for name, data in sorted(payload.items())}
    payload["closure.json"] = canonical(closure)
    modes["closure.json"] = 0o444
    manifest = dict(parent)
    manifest.pop("bundle_id", None)
    manifest.update({
        "schema_version": SCHEMA,
        "status": "SEALED_DEDICATED_MOUNT_HELPER_DEBUG_BOOTSTRAP_NOT_R0_OR_FORMAL_AUTHORITY",
        "predecessor_bundle_id": PARENT_ID,
        "predecessor_bundle_manifest_sha256": PARENT_SHA,
        "dedicated_daemon_host_mount_helper_required": True,
        "host_nsenter_sha256": NSENTER_SHA256,
        "invalidated_0da7_measurement_permission_attempt": FAILED_ATTEMPT,
        "invalidated_0da7_measurement_permission_credit": 0,
        "invalidated_f89f_helper_key_bundle_id": BROKEN_HELPER_BUNDLE_ID,
        "invalidated_f89f_helper_key_attempt": BROKEN_HELPER_ATTEMPT,
        "invalidated_f89f_helper_key_credit": 0,
        "invalidated_c368_unprivileged_readback_bundle_id": UNPRIVILEGED_READBACK_BUNDLE_ID,
        "invalidated_c368_unprivileged_readback_attempt": UNPRIVILEGED_READBACK_ATTEMPT,
        "invalidated_c368_unprivileged_readback_credit": 0,
        "invalidated_0da7_measurement_permission_authority_sha256": sha(payload[INVALIDATION]),
        "builder_sha256": sha(payload[BUILDER]),
        "formal_launch_authorized": False, "formal10_authorized": False,
        "accepted_round_authorized": False,
        "files": {name: sha(data) for name, data in sorted(payload.items())},
    })
    bundle_id = sha(canonical(manifest))
    manifest["bundle_id"] = bundle_id
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / bundle_id
    if target.exists():
        raise BuildError("immutable successor already exists")
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
        sha(raw) + "  bundle_manifest.json\n", encoding="ascii")
    freeze(stage)
    os.rename(stage, target)
    return target, manifest


if __name__ == "__main__":
    try:
        target, manifest = build()
        print(json.dumps({"status": manifest["status"], "bundle": str(target),
                          "bundle_id": manifest["bundle_id"],
                          "manifest_sha256": digest(target / "bundle_manifest.json"),
                          "formal_launch_authorized": False,
                          "accepted_round_authorized": False}, sort_keys=True))
    except (BuildError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
