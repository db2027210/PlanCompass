#!/usr/bin/env python3
"""Execute one XP capture-only blkio transition as a recoverable transaction.

This entrypoint is host-bound to node02 and intentionally has no formal/R0
authority.  It swaps only the adopted database cgroup's declared parent-device
throttles, read-backs each write, runs the sealed raw executor only in the
already-adopted client cgroup, and restores/read-backs the formal values in a
finally block.  No caller supplied resource JSON can replace these readbacks.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from mysql_xp_node02_formal10_materializer_v1 import (  # noqa: E402
    MaterializerError, atomic_json, load_sealed, sha256, verify_adoption,
    verify_bundle,
)

HOST = "node02"
DEVICE = "259:1"
CAPTURE = {"io_read_bps": 1205862400, "io_write_bps": 1205862400}
FORMAL = {"io_read_bps": 29360128, "io_write_bps": 524288}


class TransitionError(MaterializerError):
    pass


def _cgroup(pid):
    for row in Path("/proc/%d/cgroup" % int(pid)).read_text(encoding="ascii").splitlines():
        fields = row.split(":", 2)
        if len(fields) == 3 and "blkio" in fields[1].split(","):
            root = Path("/sys/fs/cgroup/blkio") / fields[2].lstrip("/")
            if root.is_dir() and not root.is_symlink():
                return root
    raise TransitionError("adopted database blkio cgroup is absent")


def _lines(path):
    if path.is_symlink() or not path.is_file():
        raise TransitionError("blkio throttle file is absent")
    return path.read_text(encoding="ascii").splitlines()


def _value(path):
    matches = [row for row in _lines(path) if row.split()[:1] == [DEVICE]]
    if len(matches) != 1 or len(matches[0].split()) != 2:
        raise TransitionError("declared blkio parent-device readback differs")
    try:
        return int(matches[0].split()[1])
    except ValueError as exc:
        raise TransitionError("blkio value is not an integer") from exc


def _write(path, value):
    descriptor = os.open(str(path), os.O_WRONLY)
    try:
        payload = (DEVICE + " " + str(int(value)) + "\n").encode("ascii")
        if os.write(descriptor, payload) != len(payload):
            raise TransitionError("short blkio throttle write")
    finally:
        os.close(descriptor)
    if _value(path) != int(value):
        raise TransitionError("blkio throttle readback differs")


def _authority(path, bundle_id, schema, status):
    value = load_sealed(path, "capture authority")
    if value.get("schema_version") != schema or value.get("status") != status or value.get("bundle_id") != bundle_id:
        raise TransitionError("capture authority binding differs")
    return value


def _executor_args(path, bundle, identity, capture_readback, formal_readback):
    if path.is_symlink() or not path.is_file():
        raise TransitionError("executor argv is absent")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TransitionError("executor argv differs")
    expected = str(bundle / "launch/mysql_xp_raw_evidence_executor_v1.py")
    if len(value) < 3 or Path(value[1]).resolve() != Path(expected).resolve():
        raise TransitionError("executor is not bundle-bound")
    pairs = dict(zip(value[2::2], value[3::2]))
    if (pairs.get("--mode") != "capture" or pairs.get("--bundle") != str(bundle)
            or pairs.get("--client-container") != identity["client_container_id"]
            or pairs.get("--capture-readback") != str(capture_readback)
            or pairs.get("--formal-readback") != str(formal_readback)
            or pairs.get("--stop-file") != "/measurement/hybench-stop-request"):
        raise TransitionError("executor capture placement/binding differs")
    return value


def execute(args):
    if socket.gethostname().split(".")[0] != HOST:
        raise TransitionError("capture transition is host-bound to node02")
    bundle = Path(args.bundle).resolve(strict=True)
    manifest = verify_bundle(bundle)
    adoption, identity = verify_adoption(args.adopted_service, manifest["bundle_id"])
    runtime = _authority(args.runtime_authority, manifest["bundle_id"], "xp_runtime_authority_bundle_v1", "SEALED")
    capture_authority = _authority(args.capture_authorization, manifest["bundle_id"], "mysql_xp_capture_launch_authorization_v1", "CAPTURE_READY")
    if (capture_authority.get("runtime_authority_sha256") != sha256(args.runtime_authority)
            or runtime.get("adopted_service_sha256") != sha256(args.adopted_service)
            or capture_authority.get("adopted_service_sha256") != sha256(args.adopted_service)):
        raise TransitionError("capture authority same-live binding differs")
    before = load_sealed(args.formal_readback, "formal readback")
    envelope = before.get("resource_envelope")
    if (before.get("bundle_id") != manifest["bundle_id"]
            or not isinstance(envelope, dict)
            or envelope.get("io_read_bps") != FORMAL["io_read_bps"]
            or envelope.get("io_write_bps") != FORMAL["io_write_bps"]):
        raise TransitionError("formal readback differs")
    root = _cgroup(identity["mysql_pid"])
    read = root / "blkio.throttle.read_bps_device"
    write = root / "blkio.throttle.write_bps_device"
    if (_value(read), _value(write)) != (FORMAL["io_read_bps"], FORMAL["io_write_bps"]):
        raise TransitionError("formal blkio readback is not current")
    output = Path(args.output_directory).resolve()
    if output.exists() or output.is_symlink():
        raise TransitionError("transition output must be fresh")
    output.mkdir(mode=0o700, parents=True)
    capture_readback = output / "capture_readback.json"
    restored_readback = output / "formal_restore_readback.json"
    transition = output / "capture_transition.json"
    # Set before the first mutation so even a write that changes the cgroup
    # and then fails its readback is restored in the finally block.
    restore_required = False
    executor_result = None
    try:
        restore_required = True
        _write(read, CAPTURE["io_read_bps"])
        _write(write, CAPTURE["io_write_bps"])
        atomic_json(capture_readback, {"schema_version": "mysql_xp_capture_resource_readback_v1", "status": "PASS_CAPTURE_BLKIO_READBACK", "bundle_id": manifest["bundle_id"], "formal_scope": "mysql_only", "resource_envelope": CAPTURE, "database_pid": identity["mysql_pid"], "database_container_id": identity["database_container_id"], "blkio_cgroup": str(root), "capture_only": True, "accepted_rounds_credit": 0})
        command = _executor_args(args.executor_argv_json, bundle, identity, capture_readback, args.formal_readback)
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        if completed.returncode:
            raise TransitionError("bundle raw executor failed")
        executor_result = {"argv_sha256": hashlib.sha256("\0".join(command).encode("utf-8")).hexdigest(), "stdout": completed.stdout.strip()}
    finally:
        if restore_required:
            restore_error = None
            try:
                _write(read, FORMAL["io_read_bps"]); _write(write, FORMAL["io_write_bps"])
            except Exception as exc:
                restore_error = str(exc)
            if restore_error:
                raise TransitionError("formal blkio restoration failed: " + restore_error)
    restored = dict(before)
    restored["resource_transition"] = {"schema_version": "mysql_xp_capture_blkio_transition_v1", "capture_readback_sha256": sha256(capture_readback), "formal_values_restored": True, "formal_blkio_readback": FORMAL, "executor": executor_result}
    atomic_json(restored_readback, restored)
    atomic_json(transition, {"schema_version": "mysql_xp_capture_transition_result_v1", "status": "PASS_CAPTURE_EXECUTOR_AND_FORMAL_RESTORE", "bundle_id": manifest["bundle_id"], "capture_readback": str(capture_readback), "formal_restore_readback": str(restored_readback), "capture_only": True, "formal_r0_authorized": False, "accepted_round_authorized": False})
    return {"capture_readback": str(capture_readback), "formal_restore_readback": str(restored_readback), "transition": str(transition)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True); parser.add_argument("--adopted-service", type=Path, required=True)
    parser.add_argument("--runtime-authority", type=Path, required=True); parser.add_argument("--capture-authorization", type=Path, required=True)
    parser.add_argument("--formal-readback", type=Path, required=True); parser.add_argument("--executor-argv-json", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True); args = parser.parse_args()
    print(json.dumps(execute(args), sort_keys=True))


if __name__ == "__main__":
    try: main()
    except (OSError, ValueError, TransitionError) as exc: raise SystemExit("FAIL_CLOSED: %s" % exc)
