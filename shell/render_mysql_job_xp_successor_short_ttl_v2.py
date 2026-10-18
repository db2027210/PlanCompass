#!/usr/bin/env python3
"""Render JOB four-flow control against the exact sealed XP v23 chain."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_xp_successor_binding_v1 as binding_api


class RenderError(RuntimeError):
    pass


TOKENS = {
    "[XP_BUNDLE_ID_FROM_BINDING]": "xp_bundle_id",
    "[XP_BUNDLE_SCHEMA_FROM_BINDING]": "xp_bundle_schema_version",
    "[XP_MANIFEST_SHA256_FROM_BINDING]": "xp_manifest_sha256",
}


def replace(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for old, new in replacements.items():
            result = result.replace(old, new)
        return result
    if isinstance(value, list):
        return [replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: replace(item, replacements) for key, item in value.items()}
    return value


def render(
    template_path: Path,
    local_xp_bundle: Path,
    remote_xp_bundle: Path,
    remote_binding_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not remote_xp_bundle.is_absolute() or not remote_binding_path.is_absolute():
        raise RenderError("remote XP bundle/binding paths must be absolute")
    binding = binding_api.build_binding(local_xp_bundle, remote_xp_bundle)
    if (
        binding.get("xp_bundle_id") != binding_api.V23
        or [row.get("bundle_id") for row in binding.get("sealed_chain", [])]
        != [binding_api.V20, binding_api.V21, binding_api.V22, binding_api.V23]
    ):
        raise RenderError("exact XP v23-to-v22-to-v21-to-v20 chain is required")
    if remote_binding_path.name != "xp_successor_binding.json":
        raise RenderError("remote XP binding filename differs")
    template = json.loads(template_path.read_text(encoding="utf-8"))
    if (
        template.get("schema_version")
        != "mysql_job_v13_xp_successor_short_ttl_commands_v3"
        or template.get("status") != "TEMPLATE_NOT_AUTHORITY_NOT_EXECUTED"
        or template.get("formal_scope") != "mysql_only"
        or template.get("cross_engine_comparison_prohibited") is not True
        or template.get("postgresql_mutation_prohibited") is not True
    ):
        raise RenderError("short-TTL template boundary differs")
    replacements = {
        token: str(binding[key]) for token, key in TOKENS.items()
    }
    replacements.update({
        "[XP_BUNDLE]": str(remote_xp_bundle),
        "[XP_SUCCESSOR_BINDING]": str(remote_binding_path),
        "[XP_COLLECTOR_SHA256_FROM_BINDING]": binding["artifacts"]["collector"]["sha256"],
        "[XP_QUALIFICATION_AUTHORITY_SHA256_FROM_BINDING]": binding["artifacts"]["qualification_authority"]["sha256"],
        "[XP_ADOPTED_SERVICE_SHA256_FROM_BINDING]": binding["artifacts"]["adopted_service"]["sha256"],
        "[XP_FORMAL10_MATERIALIZER_SHA256_FROM_BINDING]": binding["artifacts"]["formal10_materializer"]["sha256"],
        "[XP_MATERIALS_GENERATOR_SHA256_FROM_BINDING]": binding["artifacts"]["qualification_materials_generator"]["sha256"],
        "[XP_TTL_REMEDIATION_SHA256_FROM_BINDING]": binding["artifacts"]["ttl_remediation"]["sha256"],
        "[XP_AUTHORITY_TTL_PARITY_REMEDIATION_SHA256_FROM_BINDING]": binding["artifacts"]["authority_ttl_parity_remediation"]["sha256"],
    })
    result = replace(template, replacements)
    encoded = json.dumps(result, sort_keys=True)
    if any(token in encoded for token in replacements) or binding["xp_bundle_id"] not in encoded:
        raise RenderError("XP successor placeholder closure differs")
    result["status"] = "RENDERED_NOT_AUTHORITY_NOT_EXECUTED"
    result["xp_sealed_chain"] = binding["sealed_chain"]
    result["xp_successor_binding_sha256"] = hashlib.sha256(
        binding_api.canonical(binding)
    ).hexdigest()
    return binding, result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--local-xp-bundle", type=Path, required=True)
    parser.add_argument("--remote-xp-bundle", type=Path, required=True)
    parser.add_argument("--remote-binding-path", type=Path, required=True)
    parser.add_argument("--binding-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.binding_output.name != "xp_successor_binding.json":
        raise RenderError("local XP binding filename must match remote binding filename")
    binding, result = render(
        args.template, args.local_xp_bundle, args.remote_xp_bundle,
        args.remote_binding_path,
    )
    binding_api.write_exclusive(args.binding_output, binding)
    binding_api.write_exclusive(args.output, result)
    print(json.dumps({
        "binding": str(args.binding_output),
        "rendered_template": str(args.output),
        "xp_bundle_id": binding["xp_bundle_id"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (binding_api.BindingError, RenderError, OSError, ValueError) as exc:
        raise SystemExit("NOT_READY: %s" % exc)
