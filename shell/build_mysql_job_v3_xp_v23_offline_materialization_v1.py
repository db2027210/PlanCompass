#!/usr/bin/env python3
"""Build a secret-free offline materialization kit for JOB v3 + XP v23."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
JOB_CONTROL_ID = "7801df9f783c61fb5a72341c8bfc6ac04c28bd6f7729d12f7a5e987d28e3b1b3"
JOB_CONTROL_MANIFEST_SHA256 = "ce1f9cd0c4bc6d493c7775d89933853bc8abbf4ef318e9c9a1426ff64768e650"
JOB_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
XP_ID = "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
XP_CHAIN_IDS = (
    "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f",
    "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2",
    "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad",
    XP_ID,
)
XP_MANIFEST_SHA256 = "e03f095d21876c687e9b916b8b2b5e4dca6999d5ece745d3b6261becfc5eecf0"
JOB_CONTROL = ROOT / "artifacts/mysql_job_v13_qualification_r0_closure_v3" / JOB_CONTROL_ID
XP_LOCAL = ROOT / "artifacts/mysql_xp_node02_successor_v23" / XP_ID
OUTPUT = ROOT / "artifacts/mysql_job_v3_xp_v23_offline_materialization_v1"
REMOTE_JOB_ROOT = Path("/mnt/mysql_nvme1n1/anonymous/resource_adaptive_mysql/job/runtime_algorithm_c_successor_20260729")
REMOTE_XP_ROOT = Path("/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/runtime_algorithm_c_successor_20260729")
REMOTE_SUPPORT = REMOTE_JOB_ROOT / "bundles" / JOB_CONTROL_ID
REMOTE_JOB = REMOTE_JOB_ROOT / "bundles" / JOB_ID
REMOTE_XP = REMOTE_XP_ROOT / "bundles" / XP_ID
ATTEMPT = "job-xp-v23-debug-7801df9f-ba68d4a5"
CONTROL = Path("/tmp") / (ATTEMPT + "-control")
TMPFS = Path("/dev/shm/ra_mysql_job_xp_four_flow") / ATTEMPT
REMOTE_BINDING = CONTROL / "xp_successor_binding.json"
TOKEN_RE = re.compile(r"\[[A-Z0-9_]+\]")


class BuildError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(bundle: Path, bundle_id: str, manifest_sha: str) -> dict[str, Any]:
    path = bundle / "bundle_manifest.json"
    sidecar = Path(str(path) + ".sha256")
    row = json.loads(path.read_text(encoding="utf-8"))
    unsigned = dict(row)
    actual_id = unsigned.pop("bundle_id", None)
    if (
        bundle.is_symlink() or not bundle.is_dir()
        or actual_id != bundle_id or bundle.name != bundle_id
        or hashlib.sha256(canonical(unsigned)).hexdigest() != bundle_id
        or sha256(path) != manifest_sha
        or sidecar.read_text(encoding="ascii") != "%s  bundle_manifest.json\n" % manifest_sha
        or row.get("formal_scope") != "mysql_only"
        or row.get("cross_engine_comparison_prohibited") is not True
        or row.get("postgresql_mutation_prohibited") is not True
        or row.get("accepted_round_authorized") is not False
    ):
        raise BuildError("sealed input manifest differs")
    return row


def verify_control() -> dict[str, Any]:
    row = load_manifest(JOB_CONTROL, JOB_CONTROL_ID, JOB_CONTROL_MANIFEST_SHA256)
    actual = {
        path.relative_to(JOB_CONTROL).as_posix(): sha256(path)
        for path in JOB_CONTROL.rglob("*") if path.is_file()
        and path.name not in {"bundle_manifest.json", "bundle_manifest.json.sha256"}
    }
    if actual != row.get("files") or row.get("current_verified_xp_bundle_id") != XP_ID:
        raise BuildError("JOB control exact closure differs")
    return row


def import_renderer():
    path = JOB_CONTROL / "controller/render_mysql_job_xp_successor_short_ttl_v3.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("_sealed_job_v3_renderer", path)
    if spec is None or spec.loader is None:
        raise BuildError("cannot import sealed renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixed_values(shared_dispatch_sha: str) -> dict[str, str]:
    def qid(name: str) -> str:
        return "%s-%s" % (name, hashlib.sha256((ATTEMPT + ":" + name).encode()).hexdigest()[:32])

    return {
        "[PYTHON]": "/usr/bin/python3",
        "[SUPPORT_BUNDLE_ID]": JOB_CONTROL_ID,
        "[LOCAL_SUPPORT]": str(JOB_CONTROL),
        "[REMOTE_INCOMING_PARENT_NEW_PATH]": str(REMOTE_JOB_ROOT / "bundles" / (".incoming-" + ATTEMPT)),
        "[REMOTE_CONTROL_DIR_NEW_PATH]": str(CONTROL),
        "[SUPPORT]": str(REMOTE_SUPPORT),
        "[SHARED_DISPATCH_SHA256_FROM_SUPPORT_MANIFEST]": shared_dispatch_sha,
        "[JOB_BUNDLE]": str(REMOTE_JOB),
        "[JOB_SOLO_ID]": qid("job-solo"),
        "[JOB_PAIRED_ID]": qid("job-paired"),
        "[XP_SOLO_ID]": qid("xp-solo"),
        "[XP_PAIRED_ID]": qid("xp-paired"),
        "[JOB_FRESH_R0_ID]": qid("job-fresh-r0"),
        "[JOB_SOLO_AUTH_DIR_NEW_PATH]": str(TMPFS / "job-solo"),
        "[JOB_PAIRED_AUTH_DIR_NEW_PATH]": str(TMPFS / "job-paired"),
        "[XP_SOLO_AUTH_DIR_NEW_PATH]": str(TMPFS / "xp-solo"),
        "[XP_PAIRED_AUTH_DIR_NEW_PATH]": str(TMPFS / "xp-paired"),
        "[JOB_FRESH_R0_AUTH_DIR_NEW_PATH]": str(TMPFS / "job-fresh-r0"),
        "[JOB_PAIRED_METRIC_STREAM]": str(TMPFS / "job-paired/workload_metrics.jsonl"),
        "[JOB_LIVE_READBACK_NEW_PATH]": str(CONTROL / "job_live_readback.json"),
        "[XP_LIVE_READBACK_NEW_PATH]": str(CONTROL / "xp_live_readback.json"),
        "[JOB_GATE_OUTPUT_NEW_PATH]": str(CONTROL / "job_dynamic_gate.json"),
        "[XP_GATE_OUTPUT_NEW_PATH]": str(CONTROL / "xp_dynamic_gate.json"),
        "[FOUR_FLOW_PLAN_NEW_PATH]": str(CONTROL / "four_flow_plan.json"),
        "[FOUR_FLOW_AUTHORITY_NEW_PATH]": str(CONTROL / "four_flow_authority.json"),
        "[FOUR_FLOW_TOKEN_0600_NEW_PATH]": str(CONTROL / "four_flow_token"),
        "[FRESH_R0_HANDOFF_NEW_PATH]": str(CONTROL / "fresh_r0_handoff.json"),
        "[FRESH_R0_OUTPUT_NEW_PATH]": str(CONTROL / "fresh_r0_output.json"),
        "[XP_ADOPTED_SERVICE]": str(CONTROL / "xp_adopted_service.json"),
        "[XP_SOLO_TOKEN_0600]": str(CONTROL / "xp_solo_token"),
        "[XP_PAIRED_TOKEN_0600]": str(CONTROL / "xp_paired_token"),
        "[JOB_ATTEMPT_TOKEN]": qid("job-gate"),
        "[XP_ATTEMPT_TOKEN]": qid("xp-gate"),
    }


def replace(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        for token, replacement in replacements.items():
            value = value.replace(token, replacement)
        return value
    if isinstance(value, list):
        return [replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: replace(item, replacements) for key, item in value.items()}
    return value


def replacement_table(tokens: set[str], fixed: dict[str, str]) -> dict[str, Any]:
    rows: dict[str, dict[str, Any]] = {}
    for token, value in fixed.items():
        rows[token] = {
            "phase": "prepublication" if token in {
                "[SUPPORT_BUNDLE_ID]", "[LOCAL_SUPPORT]", "[REMOTE_INCOMING_PARENT_NEW_PATH]"
            } else "post_publication_protocol",
            "resolution_kind": "fixed_non_secret_literal",
            "value": value,
            "secret_value_included": False,
            "validation": "exact literal; every *_NEW_PATH must be absent before execution",
        }
    rows["[LOCAL_XP_SUCCESSOR_BINDING]"] = {
        "phase": "prepublication",
        "resolution_kind": "artifact_relative_path",
        "value": "xp_successor_binding.json",
        "secret_value_included": False,
        "validation": "resolve inside this offline bundle and transfer with its .sha256 sidecar",
    }
    rows["[NODE02_SSH_TARGET]"] = {
        "phase": "prepublication",
        "resolution_kind": "operator_transport_alias_before_publication",
        "value": None,
        "secret_value_included": False,
        "validation": "existing SSH alias for node02; never store credentials in this table",
    }
    job_sources = {
        "[JOB_BOOTSTRAP_SPEC]": "sealed current JOB bootstrap spec path",
        "[JOB_BOOTSTRAP_EVIDENCE]": "sealed current JOB bootstrap evidence path",
        "[JOB_DATABASE_CONTAINER]": "database.name from current JOB bootstrap/adoption binding",
        "[JOB_CLIENT_CONTAINER]": "client.name from current JOB bootstrap/adoption binding",
        "[JOB_MYSQL_DEFAULTS_FILE]": "absolute private mysql defaults path from current JOB bootstrap binding",
    }
    for token, source in job_sources.items():
        rows[token] = {
            "phase": "post_publication_existing_job_readback",
            "resolution_kind": "existing_sealed_job_runtime_field",
            "value": None,
            "source": source,
            "secret_value_included": False,
            "validation": "must match fresh JOB collect-only live identity; path sidecar required where JSON",
        }
    xp_sources = {
        "[XP_DATABASE_CONTAINER]": "/database/name",
        "[XP_CLIENT_CONTAINER]": "/client/name",
        "[XP_PRIVATE_CREDENTIAL_DIR]": "/private_credentials/directory",
    }
    for token, pointer in xp_sources.items():
        rows[token] = {
            "phase": "post_publication_wait_xp_adopted_service",
            "resolution_kind": "xp_adopted_service_json_pointer",
            "value": None,
            "source_artifact": str(CONTROL / "xp_adopted_service.json"),
            "json_pointer": pointer,
            "secret_value_included": False,
            "validation": "sealed PASS_ADOPTED_SAME_LIVE_SERVICE for exact XP v23; fresh collect-only must agree",
        }
    if set(rows) != tokens:
        raise BuildError("replacement table is not complete: missing=%s extra=%s" % (
            sorted(tokens - set(rows)), sorted(set(rows) - tokens),
        ))
    return {
        "schema_version": "mysql_job_v3_xp_v23_complete_replacement_table_v1",
        "status": "COMPLETE_SECRET_FREE_OFFLINE_TABLE_NOT_EXECUTED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "placeholder_count": len(rows),
        "rows": rows,
        "real_remote_secrets_included": False,
        "remote_actions_performed": 0,
    }


def payloads() -> dict[str, bytes]:
    control = verify_control()
    xp = load_manifest(XP_LOCAL, XP_ID, XP_MANIFEST_SHA256)
    renderer = import_renderer()
    template = JOB_CONTROL / "templates/mysql_job_v13_xp_successor_short_ttl_commands_v3.json"
    binding, rendered = renderer.render(template, XP_LOCAL, REMOTE_XP, REMOTE_BINDING)
    if (
        binding.get("xp_bundle_id") != XP_ID
        or binding.get("xp_manifest_sha256") != XP_MANIFEST_SHA256
        or binding.get("formal_launch_authorized") is not False
        or len(binding.get("sealed_chain", [])) != 4
        or xp.get("status") != "SEALED_GENERATOR_SERVICE_60_MINUTE_TTL_PARITY_NOT_LAUNCHED"
    ):
        raise BuildError("XP v23 binding differs")
    encoded = json.dumps(rendered, ensure_ascii=True, sort_keys=True)
    tokens = set(TOKEN_RE.findall(encoded))
    shared_sha = control["files"]["qualification/mysql_job_xp_shared_qualification_dispatch_v1.py"]
    fixed = fixed_values(shared_sha)
    table = replacement_table(tokens, fixed)
    materialized = replace(rendered, fixed)
    remaining = sorted(set(TOKEN_RE.findall(json.dumps(materialized, sort_keys=True))))
    expected_remaining = sorted(set(table["rows"]) - set(fixed))
    if remaining != expected_remaining:
        raise BuildError("non-secret materialization closure differs")
    readiness = {
        "schema_version": "mysql_job_v3_xp_v23_offline_readiness_v1",
        "status": "PASS_OFFLINE_READY_WAITING_ONLY_FOR_XP_ADOPTED_SERVICE_AFTER_PUBLICATION",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "job_control_bundle_id": JOB_CONTROL_ID,
        "job_control_manifest_sha256": JOB_CONTROL_MANIFEST_SHA256,
        "xp_bundle_id": XP_ID,
        "xp_manifest_sha256": XP_MANIFEST_SHA256,
        "xp_chain_bundle_ids": [row["bundle_id"] for row in binding["sealed_chain"]],
        "replacement_placeholder_count": len(tokens),
        "fixed_non_secret_values_materialized": len(fixed),
        "remaining_placeholders": remaining,
        "external_readiness_blockers_after_publication": ["xp_adopted_service"],
        "claim_preconditions": [
            "JOB control v3 and its manifest sidecar are transactionally published",
            "exact XP v20, v21, v22, and v23 chain bundles are sibling-published under the XP bundle root",
            "current sealed JOB bootstrap/adoption binding remains live",
            "all declared *_NEW_PATH locations are absent",
        ],
        "required_remote_publication_paths": {
            "job_control": str(REMOTE_SUPPORT),
            "job_bundle": str(REMOTE_JOB),
            "xp_chain": [str(REMOTE_XP_ROOT / "bundles" / bundle_id) for bundle_id in XP_CHAIN_IDS],
            "xp_binding": str(REMOTE_BINDING),
        },
        "existing_inputs_to_re_read": ["sealed JOB bootstrap/adoption binding"],
        "runtime_protocol_actions_not_external_blockers": [
            "create new control/tmpfs directories",
            "generate exactly two XP 0600 token contents on node02",
            "materialize short-lived authorities",
            "run fresh JOB and XP collect-only readbacks",
            "execute solo plus concurrent paired four-flow gate",
        ],
        "secret_policy": {
            "real_remote_secrets_generated": False,
            "real_remote_secrets_stored": False,
            "real_remote_secrets_transferred": False,
            "only_secret_file_paths_predeclared": True,
        },
        "qualification_credit": 0,
        "r0_credit": 0,
        "accepted_rounds_credit": 0,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions_performed": 0,
    }
    result = {
        "xp_successor_binding.json": canonical(binding),
        "rendered_commands.json": canonical(rendered),
        "nonsecret_materialized_commands.json": canonical(materialized),
        "complete_replacement_table.json": canonical(table),
        "offline_readiness.json": canonical(readiness),
        "builder/build_mysql_job_v3_xp_v23_offline_materialization_v1.py": Path(__file__).read_bytes(),
    }
    forbidden = re.compile(rb"(?i)(password\s*=|private[_-]?key\s*=|token[_-]?value\s*=)")
    if any(forbidden.search(data) for data in result.values()):
        raise BuildError("secret-like payload detected")
    return result


def seal(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.chmod(path, 0o444 if path.is_file() else 0o555)
    os.chmod(root, 0o555)


def build() -> tuple[dict[str, Any], Path]:
    data = payloads()
    files: dict[str, str] = {}
    for relative, payload in data.items():
        files[relative] = hashlib.sha256(payload).hexdigest()
        files[relative + ".sha256"] = hashlib.sha256(
            ("%s  %s\n" % (files[relative], Path(relative).name)).encode("ascii")
        ).hexdigest()
    unsigned = {
        "schema_version": "mysql_job_v3_xp_v23_offline_materialization_bundle_v1",
        "status": "SEALED_SECRET_FREE_OFFLINE_MATERIALIZATION_NOT_EXECUTED",
        "job_control_bundle_id": JOB_CONTROL_ID,
        "job_control_manifest_sha256": JOB_CONTROL_MANIFEST_SHA256,
        "xp_bundle_id": XP_ID,
        "xp_manifest_sha256": XP_MANIFEST_SHA256,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "postgresql_mutation_prohibited": True,
        "real_remote_secrets_included": False,
        "formal_launch_authorized": False,
        "accepted_round_authorized": False,
        "remote_actions_performed": 0,
        "files": files,
    }
    bundle_id = hashlib.sha256(canonical(unsigned)).hexdigest()
    manifest = dict(unsigned, bundle_id=bundle_id)
    destination = OUTPUT / bundle_id
    if destination.exists():
        if json.loads((destination / "bundle_manifest.json").read_text()) != manifest:
            raise BuildError("existing offline bundle differs")
        return manifest, destination
    destination.mkdir(parents=True, mode=0o700)
    try:
        for relative, payload in data.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            sidecar = Path(str(target) + ".sha256")
            sidecar.write_text("%s  %s\n" % (hashlib.sha256(payload).hexdigest(), target.name), encoding="ascii")
        manifest_payload = canonical(manifest)
        (destination / "bundle_manifest.json").write_bytes(manifest_payload)
        (destination / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % hashlib.sha256(manifest_payload).hexdigest(), encoding="ascii"
        )
        seal(destination)
    except Exception:
        for path in sorted(destination.rglob("*"), reverse=True):
            try:
                os.chmod(path, 0o600 if path.is_file() else 0o700)
            except OSError:
                pass
        raise
    return manifest, destination


def main() -> int:
    manifest, destination = build()
    print(json.dumps({
        "bundle_id": manifest["bundle_id"],
        "path": str(destination),
        "status": manifest["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
