#!/usr/bin/env python3
"""Fail-closed, attempt-specific XP v16 qualification stage runner.

The default mode only validates the sealed local materials.  Host/data-plane
actions require both --execute and the exact --confirm-attempt value.  This
runner never invokes SSH and contains no PostgreSQL mutation operation.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
import sys
import time


MATERIALS = Path(
    "/tmp/mysql-xp-v16-materials-final-20260804T1615Z/"
    "xp-formal-qual-20260804T082652Z-a5f9121eeef9124e914316ff"
)
MATERIALS_MANIFEST_SHA256 = (
    "59f4bb9095d3a9fa6371dc79da2cd9ce331a424b0f338a8c9d01103dfcc0f8a9"
)
SUPPORT_BUNDLE_ID = (
    "24d5f2f9f12d30c92ff6af621513d25963daba765cfed6d91798d37ee58e685b"
)
SUPPORT_BUNDLE_MANIFEST_SHA256 = (
    "8fea1df55260e26ad946787ab1b4d72a643af506198850408b6af0c289ce5cc5"
)
ATTEMPT = "xp-formal-qual-20260804T082652Z-a5f9121eeef9124e914316ff"
RUNNER_SCHEMA = "mysql_xp_v16_attempt_stage_runner_receipt_v1"
STAGES = (
    "frozen_image_publish",
    "physical_copy",
    "copy_private_materials",
    "jdk_publish",
    "mount_attempt_tmpfs",
    "plan_only",
    "bootstrap",
    "adopt_readback",
)
SYSTEMD_STAGES = tuple(stage for stage in STAGES if stage not in {
    "copy_private_materials", "mount_attempt_tmpfs"
})
OUTPUT_STATUS = {
    "frozen_image_publish": (
        "mysql_xp_dedicated_frozen_image_publication_authority_v1", "PASS"
    ),
    "physical_copy": (
        "mysql_xp_compute00_physical_copy_authority_v2",
        "PASS_BYTE_COPY_FIEMAP_AND_MYSQL_UID_NORMALIZATION",
    ),
    "jdk_publish": (
        "mysql_xp_compute00_jdk_runtime_publication_v1",
        "PUBLISHED_EXACT_MANIFEST_BOUND",
    ),
    "bootstrap": (
        "mysql_xp_compute00_adopted_service_authority_v1",
        "PASS_ADOPTED_SAME_LIVE_SERVICE",
    ),
    "adopt_readback": (
        "mysql_xp_compute00_adopted_service_authority_v1",
        "PASS_ADOPTED_SAME_LIVE_SERVICE",
    ),
}


class GateError(RuntimeError):
    pass


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def exact_mode(path: Path, mode: int) -> None:
    observed = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    if observed != mode:
        raise GateError("mode differs for %s: %04o" % (path, observed))


def verify_sidecar(path: Path, required_mode: int = 0o600) -> str:
    if path.is_symlink() or not path.is_file():
        raise GateError("sealed output missing or non-regular: %s" % path)
    sidecar = Path(str(path) + ".sha256")
    if sidecar.is_symlink() or not sidecar.is_file():
        raise GateError("sealed output sidecar missing: %s" % sidecar)
    exact_mode(path, required_mode)
    exact_mode(sidecar, required_mode)
    digest = sha_file(path)
    expected = "%s  %s\n" % (digest, path.name)
    if sidecar.read_text(encoding="ascii") != expected:
        raise GateError("output sidecar differs: %s" % sidecar)
    return digest


def verify_published_bundle(root: Path, expected_manifest_sha256: str) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise GateError("published bundle root missing/non-directory: %s" % root)
    manifest_path = root / "bundle_manifest.json"
    sidecar = root / "bundle_manifest.json.sha256"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise GateError("published bundle manifest missing")
    if sha_file(manifest_path) != expected_manifest_sha256:
        raise GateError("published bundle manifest hash differs: %s" % root)
    if sidecar.read_text(encoding="ascii") != (
        expected_manifest_sha256 + "  bundle_manifest.json\n"
    ):
        raise GateError("published bundle manifest sidecar differs: %s" % root)
    document = load_json(manifest_path)
    for relative, expected in document["files"].items():
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file() or sha_file(candidate) != expected:
            raise GateError("published bundle member differs: %s" % candidate)
        if stat.S_IMODE(candidate.stat().st_mode) & 0o222:
            raise GateError("published bundle member remains writable: %s" % candidate)
    if stat.S_IMODE(manifest_path.stat().st_mode) & 0o222:
        raise GateError("published bundle manifest remains writable")
    return {"root": str(root), "manifest_sha256": expected_manifest_sha256}


def verify_local_materials(root: Path):
    root = root.resolve()
    manifest_path = root / "materials_manifest.json"
    if sha_file(manifest_path) != MATERIALS_MANIFEST_SHA256:
        raise GateError("materials manifest identity differs")
    sidecar = root / "materials_manifest.json.sha256"
    if sidecar.read_text(encoding="ascii") != (
        MATERIALS_MANIFEST_SHA256 + "  materials_manifest.json\n"
    ):
        raise GateError("materials manifest sidecar differs")
    manifest = load_json(manifest_path)
    plan = load_json(root / "command_plan.json")
    spec = load_json(root / "bootstrap_spec.json")
    install = load_json(root / "authorization_install_plan.json")
    for relative, expected in manifest["files"].items():
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise GateError("material missing or symlink: %s" % relative)
        if sha_file(candidate) != expected:
            raise GateError("material hash differs: %s" % relative)
    if manifest["attempt_id"] != ATTEMPT or manifest["support_bundle_id"] != SUPPORT_BUNDLE_ID:
        raise GateError("attempt or support bundle identity differs")
    if plan["remote_bundle_manifest_sha256_required"] != manifest["bundle_manifest_sha256"]:
        raise GateError("data bundle manifest binding differs")
    if manifest["formal_scope"] != "mysql_only" or not manifest["postgresql_mutation_prohibited"]:
        raise GateError("formal scope or PostgreSQL prohibition differs")
    if any(manifest[key] for key in (
        "formal_authorized", "formal_r0_authorized", "accepted_round_authorized"
    )) or manifest["accepted_rounds_credit"] != 0 or manifest["data_plane_actions"] != 0:
        raise GateError("qualification zero-credit binding differs")
    expiry = dt.datetime.fromisoformat(manifest["expires_at_utc"])
    gate = manifest["formal_gate_binding"]
    if (
        gate["database_cpu_quota_cores"] != 5.3
        or gate["database_io_read_bps"] != 29360128
        or gate["database_io_write_bps"] != 524288
        or gate["required_trace_transactions"] != ["AT1", "AT2", "AT3", "AT4", "AT5", "AT6"]
        or gate["required_nonempty_subsets"] != 127
        or gate["fixed_real_replay_top_k"] != 5
        or gate["candidate_order"] != "global_descending_final_candidate_score"
    ):
        raise GateError("formal algorithm gate binding differs")
    required_order = [
        "publish_frozen_image_to_dedicated_daemon",
        "verify_sealed_dedicated_image_authority",
        "physical_copy", "verify_sealed_physical_copy_authority",
        "copy_external_authorization_into_attempt_owned_private_materials_0700_0600",
        "jdk_publish", "mount_attempt_owned_host_tmpfs", "plan_only",
        "bootstrap_with_private_token", "adopt_readback_and_real_pid_check",
    ]
    positions = [plan["execution_order"].index(name) for name in required_order]
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise GateError("sealed stage order differs")
    host = plan["host_systemd_run_commands_after_separate_authorization"]
    polls = plan["controller_poll_after_each_systemd_run"]
    for stage in SYSTEMD_STAGES:
        command = host[stage]
        poll = polls[stage]
        if command[:4] != [
            "systemd-run", "--quiet", "--service-type=oneshot", "--remain-after-exit"
        ] or command[5] != "--":
            raise GateError("systemd-run seal differs: %s" % stage)
        if any(option in command for option in ("--wait", "--collect", "--scope")):
            raise GateError("prohibited systemd-run option: %s" % stage)
        if command[6:] != poll["expected_exec_start_argv"]:
            raise GateError("systemd inner argv differs: %s" % stage)
        if hashlib.sha256(canonical(command[6:])).hexdigest() != poll["expected_exec_start_argv_sha256"]:
            raise GateError("systemd inner argv hash differs: %s" % stage)
    image_command = host["frozen_image_publish"]
    expected_control_path = "/control_bundles/%s/" % SUPPORT_BUNDLE_ID
    if not any(expected_control_path in token for token in image_command):
        raise GateError("frozen image publisher is not v16 control-bundle-bound")
    for directory in (root / "private_authority", root / "private_credentials"):
        if directory.is_symlink() or not directory.is_dir():
            raise GateError("private source directory differs")
        exact_mode(directory, 0o700)
        for candidate in directory.iterdir():
            exact_mode(candidate, 0o600)
    return manifest, plan, spec, install, expiry


def extract_output(command: list[str]) -> Path | None:
    if "--output" not in command:
        return None
    index = command.index("--output")
    return Path(command[index + 1])


def unit_name(command: list[str]) -> str:
    value = command[4]
    if not value.startswith("--unit="):
        raise GateError("unit option is not sealed")
    return value.split("=", 1)[1]


def run_checked(argv: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, check=True, text=False, stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def parse_properties(raw: bytes) -> dict[str, str]:
    result = {}
    for line in raw.decode("utf-8", "strict").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def parse_exec_argv(value: str) -> list[str]:
    match = re.search(r"(?:^|;\s*)argv\[\]=(.*?)(?:\s*;\s*ignore_errors=|\s*}$)", value)
    if not match:
        raise GateError("cannot normalize retained unit ExecStart")
    return shlex.split(match.group(1))


def verify_exec(props: dict[str, str], expected: list[str]) -> None:
    if parse_exec_argv(props.get("ExecStart", "")) != expected:
        raise GateError("retained unit ExecStart argv differs")


def poll_unit(stage: str, poll: dict, expected: list[str]) -> dict:
    deadline = time.monotonic() + poll["deadline_seconds"]
    seen_running = False
    while time.monotonic() < deadline:
        raw = run_checked(poll["argv"], capture=True).stdout
        props = parse_properties(raw)
        verify_exec(props, expected)
        common = {"Id": poll["success_state"]["Id"], "Transient": "yes", "RemainAfterExit": "yes"}
        if any(props.get(key) != value for key, value in common.items()):
            raise GateError("%s retained unit identity differs" % stage)
        running = poll["running_state"]
        if props.get("ActiveState") == "activating" and props.get("SubState") == "start":
            if not props.get("MainPID", "").isdigit() or int(props["MainPID"]) <= 0:
                raise GateError("%s running unit has no real PID" % stage)
            if props.get("ControlGroup") != running["ControlGroup"]:
                raise GateError("%s running cgroup differs" % stage)
            seen_running = True
            time.sleep(poll["poll_interval_seconds"])
            continue
        success = poll["success_state"]
        fixed = {key: value for key, value in success.items() if key != "ControlGroup"}
        if all(props.get(key) == value for key, value in fixed.items()):
            cgroup = props.get("ControlGroup", "")
            if cgroup != success["ControlGroup"]:
                compatibility = poll.get("systemd219_empty_control_group_terminal_success")
                if cgroup != "" or compatibility is None or not seen_running:
                    raise GateError("%s terminal cgroup lacks sealed provenance" % stage)
            journal = run_checked(
                ["journalctl", "-u", success["Id"], "--no-pager", "--output=short-iso"],
                capture=True,
            ).stdout
            if not journal:
                raise GateError("%s retained unit journal is empty" % stage)
            return {
                "unit": success["Id"],
                "exec_argv_sha256": hashlib.sha256(canonical(expected)).hexdigest(),
                "journal_sha256": hashlib.sha256(journal).hexdigest(),
                "seen_running": seen_running,
                "terminal_control_group": cgroup,
            }
        raise GateError("%s reached non-sealed terminal state: %r" % (stage, props))
    raise GateError("%s poll deadline exceeded; attempt must be abandoned" % stage)


def verify_output(stage: str, path: Path, attempt: str, spec: dict) -> dict:
    digest = verify_sidecar(path)
    document = load_json(path)
    schema, status = OUTPUT_STATUS[stage]
    if document.get("schema_version") != schema or document.get("status") != status:
        raise GateError("%s sealed output status differs" % stage)
    if stage != "jdk_publish" and document.get("attempt_id") != attempt:
        raise GateError("%s output attempt differs" % stage)
    if stage == "frozen_image_publish":
        gate = spec["dedicated_frozen_image_gate"]
        if (
            document.get("image_id") != gate["image_id"]
            or document.get("required_repo_digest") != gate["required_repo_digest"]
            or document.get("layers_exact_match") is not True
        ):
            raise GateError("dedicated image authority differs")
    if stage == "jdk_publish":
        if (
            document.get("tree_content_sha256") != spec["jdk"]["tree_content_sha256"]
            or document.get("java_binary_sha256") != spec["jdk"]["java_binary_sha256"]
            or document.get("publish_gate_passed") is not True
        ):
            raise GateError("JDK publication identity differs")
    if stage in ("bootstrap", "adopt_readback"):
        for role in ("database", "client"):
            identity = document.get(role, {})
            if not isinstance(identity.get("pid"), int) or identity["pid"] <= 0:
                raise GateError("%s %s real PID missing" % (stage, role))
            if not isinstance(identity.get("pid_starttime_ticks"), int) or identity["pid_starttime_ticks"] <= 0:
                raise GateError("%s %s PID starttime missing" % (stage, role))
    return {"output": str(path), "output_sha256": digest, "status": status}


def verify_private_tree(path: Path, expected: dict[str, str], prefix: str, owner=None):
    if path.is_symlink() or not path.is_dir():
        raise GateError("private directory missing/non-directory: %s" % path)
    exact_mode(path, 0o700)
    directory_owner = (path.stat().st_uid, path.stat().st_gid)
    if owner is not None and directory_owner != owner:
        raise GateError("private directory owner differs: %s" % path)
    names = sorted(relative.split("/", 1)[1] for relative in expected if relative.startswith(prefix + "/"))
    observed = sorted(candidate.name for candidate in path.iterdir())
    if observed != names:
        raise GateError("private directory closure differs: %s" % path)
    for name in names:
        candidate = path / name
        if candidate.is_symlink() or not candidate.is_file():
            raise GateError("private file is not regular: %s" % candidate)
        exact_mode(candidate, 0o600)
        if (candidate.stat().st_uid, candidate.stat().st_gid) != directory_owner:
            raise GateError("private file owner differs: %s" % candidate)
        if sha_file(candidate) != expected[prefix + "/" + name]:
            raise GateError("private file hash differs: %s" % candidate)
    return directory_owner


def exclusive_copy_private(install: dict, manifest: dict) -> dict:
    expected = manifest["files"]
    records = {}
    # Both destinations must be absent before the first exclusive create.
    for key in ("private_authority", "private_credentials"):
        destination = Path(install[key]["attempt_runtime_destination"])
        if destination.exists() or destination.is_symlink():
            raise GateError("attempt private destination already exists: %s" % destination)
    for key in ("private_authority", "private_credentials"):
        source = Path(install[key]["destination"])
        destination = Path(install[key]["attempt_runtime_destination"])
        owner = verify_private_tree(source, expected, key)
        if owner != (os.geteuid(), os.getegid()):
            raise GateError("runner uid/gid differs from sealed external authorization owner")
        os.mkdir(destination, 0o700)
        exact_mode(destination, 0o700)
        for candidate in sorted(source.iterdir(), key=lambda item: item.name):
            target = destination / candidate.name
            source_fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                target_fd = os.open(
                    target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                )
                try:
                    while True:
                        block = os.read(source_fd, 1024 * 1024)
                        if not block:
                            break
                        os.write(target_fd, block)
                    os.fsync(target_fd)
                finally:
                    os.close(target_fd)
            finally:
                os.close(source_fd)
        verify_private_tree(destination, expected, key, owner=owner)
        records[key] = {
            "destination": str(destination),
            "file_hashes": {
                item.name: sha_file(item) for item in sorted(destination.iterdir())
            },
        }
    return records


def mount_tmpfs(spec: dict) -> dict:
    contract = spec["host_output_tmpfs"]
    path = Path(contract["path"])
    if path.exists() or path.is_symlink():
        raise GateError("host tmpfs path already exists")
    os.mkdir(path, 0o700)
    exact_mode(path, 0o700)
    options = "size=%d,rw,nosuid,nodev,noexec,mode=0700" % contract["minimum_bytes"]
    run_checked(["mount", "-t", "tmpfs", "-o", options, "tmpfs", str(path)])
    found = None
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        if len(fields) >= 6 and fields[4] == str(path):
            found = (set(fields[5].split(",")), right.split())
            break
    if found is None or len(found[1]) < 3 or found[1][0] != "tmpfs":
        raise GateError("attempt output path is not exact tmpfs mount")
    options_seen = found[0] | set(found[1][2].split(","))
    if not set(contract["required_options"]).issubset(options_seen):
        raise GateError("tmpfs mount options differ")
    capacity = os.statvfs(path).f_frsize * os.statvfs(path).f_blocks
    if capacity < contract["minimum_bytes"]:
        raise GateError("tmpfs capacity below sealed minimum")
    return {"path": str(path), "filesystem": "tmpfs", "options": sorted(options_seen), "bytes": capacity}


def assert_prelaunch_fresh(plan: dict) -> None:
    attempt_root = Path(plan["fresh_attempt_root_gate"]["path"])
    if attempt_root.exists() or attempt_root.is_symlink():
        raise GateError("fresh attempt root no longer absent")
    image_output = extract_output(plan["host_systemd_run_commands_after_separate_authorization"]["frozen_image_publish"])
    if image_output.exists() or Path(str(image_output) + ".sha256").exists():
        raise GateError("fresh image authority output already exists")
    for stage in SYSTEMD_STAGES:
        command = plan["host_systemd_run_commands_after_separate_authorization"][stage]
        completed = subprocess.run(
            ["systemctl", "show", unit_name(command), "--property=LoadState", "--value"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        if completed.returncode == 0 and completed.stdout.strip() not in ("", "not-found"):
            raise GateError("sealed transient unit already exists: %s" % unit_name(command))


def verify_remote_closure(plan: dict, spec: dict) -> list[dict]:
    data = verify_published_bundle(
        Path(spec["bundle"]["remote_path"]), spec["bundle"]["remote_manifest_sha256_required"]
    )
    image_command = plan["host_systemd_run_commands_after_separate_authorization"]["frozen_image_publish"]
    python_index = image_command.index("python3")
    if image_command[python_index + 1] != "-B":
        raise GateError("frozen image publisher python argv differs")
    publisher = Path(image_command[python_index + 2])
    control_root = publisher.parent.parent
    if control_root.name != SUPPORT_BUNDLE_ID:
        raise GateError("frozen image publisher control-bundle id differs")
    control = verify_published_bundle(control_root, SUPPORT_BUNDLE_MANIFEST_SHA256)
    control_manifest = load_json(control_root / "bundle_manifest.json")
    publisher_relative = publisher.relative_to(control_root).as_posix()
    if (
        publisher_relative != "runtime/mysql_xp_dedicated_frozen_image_publication_v1.py"
        or control_manifest["files"].get(publisher_relative) != sha_file(publisher)
    ):
        raise GateError("v16 frozen image publisher hash differs")
    return [data, control]


def execute(root: Path, manifest: dict, plan: dict, spec: dict, install: dict, expiry: dt.datetime):
    if os.geteuid() != 0:
        raise GateError("execution requires effective UID 0")
    if socket.gethostname().split(".")[0] != "compute00":
        raise GateError("execution is host-bound to compute00")
    if dt.datetime.now(dt.timezone.utc) >= expiry:
        raise GateError("authorization expired; generate a new attempt")
    closure = verify_remote_closure(plan, spec)
    assert_prelaunch_fresh(plan)
    receipt = {
        "schema_version": RUNNER_SCHEMA,
        "attempt_id": ATTEMPT,
        "formal_scope": "mysql_only",
        "postgresql_mutation_prohibited": True,
        "accepted_rounds_credit": 0,
        "verified_remote_closure": closure,
        "stages": [],
    }
    commands = plan["host_systemd_run_commands_after_separate_authorization"]
    polls = plan["controller_poll_after_each_systemd_run"]
    for stage in STAGES:
        print("STAGE_START %s" % stage, flush=True)
        if stage == "copy_private_materials":
            record = {"stage": stage, "private": exclusive_copy_private(install, manifest)}
        elif stage == "mount_attempt_tmpfs":
            record = {"stage": stage, "tmpfs": mount_tmpfs(spec)}
        else:
            command = commands[stage]
            run_checked(command)
            record = {"stage": stage}
            record.update(poll_unit(stage, polls[stage], command[6:]))
            output = extract_output(command)
            if stage in OUTPUT_STATUS:
                record.update(verify_output(stage, output, ATTEMPT, spec))
        receipt["stages"].append(record)
        print("STAGE_PASS %s" % stage, flush=True)
    receipt["status"] = "PASS_QUALIFICATION_BOOTSTRAP_ADOPT_ZERO_CREDIT"
    output = Path("/tmp") / (ATTEMPT + ".stage_runner_receipt.json")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, canonical(receipt))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(json.dumps({"status": receipt["status"], "receipt": str(output)}, sort_keys=True))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--materials", type=Path, default=MATERIALS)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-attempt")
    args = parser.parse_args(argv)
    manifest, plan, spec, install, expiry = verify_local_materials(args.materials)
    if not args.execute:
        print(json.dumps({
            "status": "PASS_LOCAL_ONLY_NO_DATA_PLANE_ACTION",
            "attempt_id": ATTEMPT,
            "expires_at_utc": expiry.isoformat(),
            "stage_order": list(STAGES),
            "materials_manifest_sha256": MATERIALS_MANIFEST_SHA256,
        }, sort_keys=True))
        return 0
    if args.confirm_attempt != ATTEMPT:
        raise GateError("--confirm-attempt must equal the sealed attempt id")
    execute(args.materials, manifest, plan, spec, install, expiry)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GateError, OSError, subprocess.SubprocessError) as error:
        print("FAIL_CLOSED_INVALIDATE_ATTEMPT_ZERO_CREDIT: %s" % error, file=sys.stderr)
        raise SystemExit(1)
