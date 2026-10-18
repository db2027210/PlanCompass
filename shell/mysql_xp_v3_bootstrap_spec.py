#!/usr/bin/env python3
"""Validate a short-lived, main-agent-filled XP bootstrap specification.

This is intentionally an offline planner: it never opens a network connection,
invokes Docker, or starts a workload.  The returned argv lists are commands to
be run *on node02* only after the main agent has separately authorized them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping


BUNDLE_ID = "a142ebdc48d5bf685d148c7a0c2e5b4f34e7123363731061651e90970df27a36"
MANIFEST_SHA256 = (
    "f7c62f56be38eebf180c4ca2f568d87f2f9278e964093c748c0277e8d77b1ffe"
)
HOST = "node02"
ROOT = "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/runtime_algorithm_c_successor_20260729"
BUNDLE = ROOT + "/bundles/" + BUNDLE_ID
JDK_SOURCE = "/usr/lib/jvm/jdk-23.0.2-oracle-x64"
JDK_TREE = "578395e883d31c692a1c01c37910d22dbdafbcf95e373c7e0f6776a26f5c844c"
JDK_TARGET = "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/runtime/jdk-23.0.2-oracle-x64-" + JDK_TREE
JAVA_SHA = "c8f339ba5d433ef5ecacef4de0640b0a35c99381804413bfbc24375a0410e51c"
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{16,128}$")
HEX64 = re.compile(r"[0-9a-f]{64}$")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}$")
MYSQL_IMAGE_REFERENCE = "mysql:8.0"
MYSQL_IMAGE_ID = (
    "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
)
HOST_CONTROL_CPUSET = "21-33,57-69"


class SpecError(RuntimeError):
    pass


def make_spec(attempt_id: str, main_agent_token_sha256: str,
              database_reference: str, database_image_id: str,
              client_reference: str, client_image_id: str,
              now: datetime | None = None) -> dict[str, Any]:
    """Build a valid 25-minute spec from main-agent read-backs only."""
    now = now or datetime.now(timezone.utc)
    root = ROOT + "/" + attempt_id
    result = {
        "schema_version": "mysql_xp_v3_short_lived_bootstrap_spec_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "scenario": "hybench_xp_io", "host": HOST,
        "bundle": {"id": BUNDLE_ID, "path": BUNDLE,
                   "manifest_sha256": MANIFEST_SHA256},
        "attempt": {"id": attempt_id, "handoff_token": attempt_id,
                    "main_agent_token_sha256": main_agent_token_sha256},
        "jdk": {"source": JDK_SOURCE, "target": JDK_TARGET,
                "tree_sha256": JDK_TREE, "java_sha256": JAVA_SHA,
                "publication": root + "/jdk_publication.json"},
        "images": {"database": {"reference": database_reference,
                                  "image_id": database_image_id},
                   "client": {"reference": client_reference,
                              "image_id": client_image_id}},
        "paths": {"config_dir": BUNDLE + "/qualification/config",
                  "datadir": root + "/datadir",
                  "host_output_tmpfs": root + "/host_measurement_tmpfs",
                  "copy_authority": root + "/physical_copy_authority.json",
                  "adoption": root + "/adopted_service.json"},
        "created_at_utc": now.isoformat(),
        "expires_at_utc": (now.replace(microsecond=0) + timedelta(minutes=25)).isoformat(),
        "qualification_only": True, "formal_r0_authorized": False,
        "formal_authorized": False, "accepted_round_authorized": False,
    }
    return validate(result, now=now)


def _keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise SpecError(label + " key set differs")


def _under(value: Any, parent: str, label: str) -> None:
    if not isinstance(value, str) or not value.startswith("/"):
        raise SpecError(label + " must be absolute")
    path, base = PurePosixPath(value), PurePosixPath(parent)
    if ".." in path.parts or path == base or base not in path.parents:
        raise SpecError(label + " escapes frozen root")


def validate(spec: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    _keys(dict(spec), {"schema_version", "formal_scope", "cross_engine_comparison_prohibited", "postgresql_mutation_prohibited", "scenario", "host", "bundle", "attempt", "jdk", "images", "paths", "created_at_utc", "expires_at_utc", "qualification_only", "formal_r0_authorized", "formal_authorized", "accepted_round_authorized"}, "spec")
    if (spec["schema_version"] != "mysql_xp_v3_short_lived_bootstrap_spec_v1" or spec["formal_scope"] != "mysql_only" or spec["cross_engine_comparison_prohibited"] is not True or spec["postgresql_mutation_prohibited"] is not True or spec["scenario"] != "hybench_xp_io" or spec["host"] != HOST or spec["qualification_only"] is not True or spec["formal_r0_authorized"] is not False or spec["formal_authorized"] is not False or spec["accepted_round_authorized"] is not False):
        raise SpecError("scope/authority boundary differs")
    _keys(spec["bundle"], {"id", "path", "manifest_sha256"}, "bundle")
    if spec["bundle"] != {
        "id": BUNDLE_ID,
        "path": BUNDLE,
        "manifest_sha256": MANIFEST_SHA256,
    }:
        raise SpecError("bundle binding differs")
    _keys(spec["attempt"], {"id", "handoff_token", "main_agent_token_sha256"}, "attempt")
    attempt = spec["attempt"]
    if attempt["id"] != attempt["handoff_token"] or TOKEN.fullmatch(str(attempt["id"])) is None or HEX64.fullmatch(str(attempt["main_agent_token_sha256"])) is None:
        raise SpecError("attempt binding differs")
    _keys(spec["jdk"], {"source", "target", "tree_sha256", "java_sha256", "publication"}, "jdk")
    if spec["jdk"]["source"] != JDK_SOURCE or spec["jdk"]["target"] != JDK_TARGET or spec["jdk"]["tree_sha256"] != JDK_TREE or spec["jdk"]["java_sha256"] != JAVA_SHA:
        raise SpecError("JDK source/target identity differs")
    _under(spec["jdk"]["publication"], ROOT, "JDK publication")
    _keys(spec["images"], {"database", "client"}, "images")
    for role in ("database", "client"):
        _keys(spec["images"][role], {"reference", "image_id"}, role + " image")
        if (
            spec["images"][role]["reference"] != MYSQL_IMAGE_REFERENCE
            or spec["images"][role]["image_id"] != MYSQL_IMAGE_ID
            or IMAGE.fullmatch(str(spec["images"][role]["image_id"])) is None
        ):
            raise SpecError(role + " image provenance/exact ID differs")
    _keys(spec["paths"], {"config_dir", "datadir", "host_output_tmpfs", "copy_authority", "adoption"}, "paths")
    root = ROOT + "/" + attempt["id"]
    expected = {"config_dir": BUNDLE + "/qualification/config", "datadir": root + "/datadir", "host_output_tmpfs": root + "/host_measurement_tmpfs", "copy_authority": root + "/physical_copy_authority.json", "adoption": root + "/adopted_service.json"}
    if spec["paths"] != expected:
        raise SpecError("attempt-owned paths differ")
    try:
        created = datetime.fromisoformat(str(spec["created_at_utc"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(spec["expires_at_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise SpecError("timestamp differs") from exc
    now = now or datetime.now(timezone.utc)
    if created.tzinfo is None or expires.tzinfo is None or expires <= created or (expires-created).total_seconds() > 1800 or now >= expires.astimezone(timezone.utc):
        raise SpecError("spec is stale or validity exceeds 30 minutes")
    return dict(spec)


def command_plan(spec: Mapping[str, Any]) -> dict[str, Any]:
    spec = validate(spec)
    attempt, paths = spec["attempt"], spec["paths"]
    svc = BUNDLE + "/launch/mysql_xp_node02_adopted_service_v1.py"
    jdk = BUNDLE + "/runtime/mysql_xp_node02_jdk_publish_v1.py"
    common = ["--bundle", BUNDLE, "--database-image", spec["images"]["database"]["image_id"], "--client-image", spec["images"]["client"]["image_id"], "--config-dir", paths["config_dir"], "--datadir", paths["datadir"], "--host-output-tmpfs", paths["host_output_tmpfs"], "--runtime-publication", spec["jdk"]["publication"], "--attempt-id", attempt["id"], "--handoff-token", attempt["handoff_token"], "--main-agent-token-sha256", attempt["main_agent_token_sha256"], "--authorization-created-at-utc", spec["created_at_utc"], "--authorization-expires-at-utc", spec["expires_at_utc"]]
    taskset = ["taskset", "-c", HOST_CONTROL_CPUSET]
    host_python = ["env", "PYTHONDONTWRITEBYTECODE=1", "python3", "-B"]
    return {"schema_version": "mysql_xp_v3_main_agent_command_plan_v1", "host": HOST, "remote_shell_wrapper_prohibited": True, "ssh_prohibited": True, "formal_launch_authorized": False, "accepted_round_authorized": False, "main_agent_token_required_for_bootstrap": True, "host_control_cpu_affinity": HOST_CONTROL_CPUSET, "physical_copy_effective_uid_required": 0, "root_helper_host_python_contract": {"argv_prefix": host_python, "python_no_bytecode_flag_required": True, "python_dont_write_bytecode_environment_required": True, "sealed_bundle_must_remain_read_only_and_exact": True}, "execution_order": ["physical_copy", "jdk_publish", "main_agent_mount_private_host_tmpfs", "renew_short_lived_spec_if_needed", "plan_only", "bootstrap", "read_existing_adoption_and_pid_checks"], "adopt_readback_is_alternative_to_bootstrap_only": True, "main_agent_mount_private_host_tmpfs": {"path": paths["host_output_tmpfs"], "minimum_bytes": 2147483648, "filesystem": "tmpfs", "required_options": ["rw", "nosuid", "nodev", "noexec"], "must_exist_before_bootstrap": True}, "expected_readbacks": {"database_image_id": spec["images"]["database"]["image_id"], "client_image_id": spec["images"]["client"]["image_id"], "data_partition": "/dev/nvme0n1p1", "data_partition_major_minor": "259:4", "io_throttle_parent_device": "/dev/nvme0n1", "io_throttle_parent_major_minor": "259:1", "unique_partition_and_parent_exclusivity_gate": True, "attempt_datadir_owner": {"uid": 999, "gid": 999}, "attempt_datadir_directory_mode": 448, "attempt_datadir_regular_mode": 384, "source_metadata_before_after_unchanged": True, "sealed_bundle_unchanged": True, "sealed_bundle_write_count": 0, "sealed_bundle_bytecode_artifacts": [], "bridge": ["docker", "exec", "ATTEMPT_DERIVED_CLIENT", "mysql", "--protocol=TCP", "--host=127.0.0.1", "--port=65104", "--batch", "--raw", "--skip-column-names"]}, "commands_to_run_on_node02": {"jdk_publish": taskset + host_python + [jdk, "--bundle", BUNDLE, "--output", spec["jdk"]["publication"]], "physical_copy": taskset + host_python + [svc, "--prepare-copy", "--attempt-id", attempt["id"], "--output", paths["copy_authority"]], "plan_only": taskset + host_python + [svc, "--plan"] + common, "bootstrap": taskset + host_python + [svc, "--bootstrap"] + common + ["--main-agent-token", "MAIN_AGENT_TOKEN_REQUIRED", "--physical-copy-authority", paths["copy_authority"], "--output", paths["adoption"]], "adopt_readback": taskset + host_python + [svc, "--adopt"] + common + ["--physical-copy-authority", paths["copy_authority"], "--output", paths["adoption"]]}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--spec")
    modes.add_argument("--emit-spec", action="store_true")
    parser.add_argument("--attempt-id")
    parser.add_argument("--main-agent-token-sha256")
    parser.add_argument("--database-reference")
    parser.add_argument("--database-image-id")
    parser.add_argument("--client-reference")
    parser.add_argument("--client-image-id")
    args = parser.parse_args()
    if args.emit_spec:
        values = (args.attempt_id, args.main_agent_token_sha256,
                  args.database_reference, args.database_image_id,
                  args.client_reference, args.client_image_id)
        if any(value is None for value in values):
            raise SpecError("--emit-spec requires attempt/token/image read-backs")
        value = make_spec(*values)
    else:
        spec = json.loads(open(args.spec, encoding="utf-8").read())
        value = command_plan(spec)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
