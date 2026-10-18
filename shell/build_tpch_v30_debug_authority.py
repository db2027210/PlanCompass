#!/usr/bin/env python3
"""Publish an immutable readiness authority for the local TPC-H v30 debug run.

The local v28 source bundle is the executable Python closure.  Its campaign
must nevertheless evaluate the shared readiness evidence relative to the
project root, because those evidence pointers are deliberately project-root
relative.  The generated launcher preloads only the frozen root verifier, then
executes the debug entry point from the immutable v28 source bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
BASE_READINESS = (
    ROOT
    / "config/formal/frozen_mysql_deployment_schedule_20260723_v4_mysql_only"
    / "readiness_manifest.json"
)
BASE_METHOD = (
    ROOT
    / "history/mysql_gradient_debug/readiness_job_recovery_journal_v2"
    / "method-b9f332b5fa1b6f6421e2"
    / "experience_table_sgd_method_manifest.json"
)
SOURCE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
    / "source-v28-b4a7a8bec9b033ae280cf4ad23459ed0a4d0abd81e8e25d2da0c3c6f7412dfb8"
)
CONFIG = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
    / "tpch_v29_config_cpu190_io320_parallel4_affinity_v1.json"
)
PROFILE = (
    ROOT
    / "history/mysql_gradient_debug/readiness_mysql_early_stop_v1"
    / "tpch_v29_profile_cpu190_io320_parallel4_affinity_v1.json"
)
DESTINATION = ROOT / "history/mysql_gradient_debug/tpch_v30_debug_authority_v1"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical(document: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def pointer(path: Path) -> dict[str, str]:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file():
        raise RuntimeError(f"authority input is absent or symlinked: {resolved}")
    return {"path": str(resolved), "sha256": sha256(resolved)}


def checked_sidecar(path: Path) -> None:
    sidecar = path.with_name(path.name + ".sha256")
    expected = f"{sha256(path)}  {path.name}\n"
    if sidecar.is_symlink() or sidecar.read_text(encoding="ascii") != expected:
        raise RuntimeError(f"invalid checksum sidecar: {sidecar}")


def source_entries() -> list[dict[str, Any]]:
    source_manifest_path = SOURCE / "source_manifest.json"
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if (
        source_manifest.get("schema_version") != "mysql_tpch_local_source_v28"
        or source_manifest.get("bundle_id") != SOURCE.name.removeprefix("source-v28-")
        or source_manifest.get("formal_scope") != "mysql_only"
        or source_manifest.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("v28 source manifest identity/scope differs")
    entries: list[dict[str, Any]] = []
    for relative, expected in sorted(source_manifest["files"].items()):
        candidate = (SOURCE / relative).resolve(strict=True)
        if candidate.is_symlink() or sha256(candidate) != expected:
            raise RuntimeError(f"v28 source closure mismatch: {relative}")
        entries.append(
            {
                "path": str(candidate),
                "sha256": expected,
                "role": "source",
                "scenario_scope": ["tpch_memory"],
            }
        )
    entries.append(
        {
            **pointer(source_manifest_path),
            "role": "source_manifest",
            "scenario_scope": ["tpch_memory"],
        }
    )
    return entries


def launcher_bytes() -> bytes:
    verifier = ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
    debug = SOURCE / "shell/debug_mysql_gradient_round_v25.py"
    return f"""#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import runpy
import sys

ROOT = Path({str(ROOT)!r})
SOURCE = Path({str(SOURCE)!r})
VERIFIER = Path({str(verifier)!r})
DEBUG = Path({str(debug)!r})
os.chdir("/tmp")
shell_package = type(sys)("shell")
shell_package.__path__ = [str(SOURCE / "shell"), str(ROOT / "shell")]
sys.modules["shell"] = shell_package
spec = importlib.util.spec_from_file_location(
    "shell.verify_mysql_sgd_v4_readiness", VERIFIER
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load frozen root readiness verifier")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
sys.path[:] = [str(SOURCE)] + [
    item for item in sys.path if item not in ("", str(ROOT), str(SOURCE))
]
runpy.run_path(str(DEBUG), run_name="__main__")
""".encode("utf-8")


def verify_published(readiness_path: Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT))
    try:
        from shell.verify_mysql_sgd_v4_readiness import verify_document
    finally:
        sys.path.pop(0)
    document = json.loads(readiness_path.read_text(encoding="utf-8"))
    checks = verify_document(document, ROOT)
    failed = [
        {
            "requirement": check.requirement,
            "item": check.item,
            "reason": check.reason,
        }
        for check in checks
        if not check.passed
    ]
    if failed:
        raise RuntimeError(f"published readiness failed closed: {failed[:8]}")
    return {
        "verifier": "PASS",
        "passed": len(checks),
        "failed": 0,
    }


def main() -> int:
    for path in (BASE_READINESS, BASE_METHOD, SOURCE / "source_manifest.json", CONFIG, PROFILE):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"required authority input is absent or symlinked: {path}")
    checked_sidecar(CONFIG)
    checked_sidecar(PROFILE)

    launcher = launcher_bytes()
    closure = {
        "schema_version": "mysql_tpch_v30_debug_authority_closure_v1",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "base_readiness_sha256": sha256(BASE_READINESS),
        "base_method_sha256": sha256(BASE_METHOD),
        "source_manifest_sha256": sha256(SOURCE / "source_manifest.json"),
        "config_sha256": sha256(CONFIG),
        "config_sidecar_sha256": sha256(CONFIG.with_name(CONFIG.name + ".sha256")),
        "profile_sha256": sha256(PROFILE),
        "profile_sidecar_sha256": sha256(PROFILE.with_name(PROFILE.name + ".sha256")),
        "root_verifier_sha256": sha256(
            ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
        ),
        "launcher_sha256": sha256_bytes(launcher),
    }
    authority_id = sha256_bytes(canonical(closure))
    final = DESTINATION / authority_id
    if final.exists():
        readiness_paths = sorted(final.glob("readiness_manifest-*.json"))
        if len(readiness_paths) != 1:
            raise RuntimeError(f"existing authority is incomplete: {final}")
        result = verify_published(readiness_paths[0])
        print(
            json.dumps(
                {
                    "status": "EXISTS_VERIFIED",
                    "authority_id": authority_id,
                    "directory": str(final),
                    "readiness_manifest": str(readiness_paths[0]),
                    "launcher": str(final / "launch_tpch_v30_debug.py"),
                    **result,
                },
                sort_keys=True,
            )
        )
        return 0

    DESTINATION.mkdir(parents=True, exist_ok=True)
    stage = DESTINATION / f".{authority_id}.tmp.{os.getpid()}"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        launcher_path = stage / "launch_tpch_v30_debug.py"
        launcher_path.write_bytes(launcher)
        launcher_path.chmod(0o555)

        entries = source_entries()
        for path, role in (
            (CONFIG, "debug_config_authority"),
            (CONFIG.with_name(CONFIG.name + ".sha256"), "checksum"),
            (PROFILE, "resource_profile"),
            (PROFILE.with_name(PROFILE.name + ".sha256"), "checksum"),
            (
                ROOT / "shell/verify_mysql_sgd_v4_readiness.py",
                "root_evidence_verifier",
            ),
        ):
            entries.append(
                {
                    **pointer(path),
                    "role": role,
                    "scenario_scope": ["tpch_memory"],
                }
            )
        final_launcher = final / launcher_path.name
        entries.append(
            {
                "path": str(final_launcher),
                "sha256": sha256_bytes(launcher),
                "role": "launch_wrapper",
                "scenario_scope": ["tpch_memory"],
            }
        )

        method = json.loads(BASE_METHOD.read_text(encoding="utf-8"))
        method["registered_sources"] = entries
        method["formal_launch_authorized"] = True
        method["formal_scope"] = "mysql_only"
        method["cross_engine_comparison_authorized"] = False
        method["runtime_binding"] = {
            "entrypoint": str(final_launcher),
            "source_directory": str(SOURCE),
            "config": str(CONFIG),
            "profile": str(PROFILE),
            "root_readiness_verifier_only": str(
                ROOT / "shell/verify_mysql_sgd_v4_readiness.py"
            ),
        }
        method_bytes = canonical(method)
        method_path = stage / "experience_table_sgd_method_manifest.json"
        method_path.write_bytes(method_bytes)

        readiness = json.loads(BASE_READINESS.read_text(encoding="utf-8"))
        bundle = readiness["global_requirements"]["method_bundle"]
        bundle["ready"] = True
        bundle["registered_sources"] = [
            {"path": entry["path"], "sha256": entry["sha256"]}
            for entry in entries
        ]
        bundle["method_manifest"] = {
            "path": str(final / method_path.name),
            "sha256": sha256_bytes(method_bytes),
        }
        readiness["status"] = "READY"
        readiness["launch_authorized"] = True
        readiness_bytes = canonical(readiness)
        readiness_sha = sha256_bytes(readiness_bytes)
        readiness_name = f"readiness_manifest-{readiness_sha}.json"
        (stage / readiness_name).write_bytes(readiness_bytes)
        (stage / "closure.json").write_bytes(canonical(closure))

        audit = {
            "schema_version": "mysql_tpch_v30_debug_authority_audit_v1",
            "authority_id": authority_id,
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "source_directory": str(SOURCE),
            "registered_sources": len(entries),
            "method_manifest_sha256": sha256_bytes(method_bytes),
            "readiness_manifest_sha256": readiness_sha,
            "launcher_sha256": sha256_bytes(launcher),
            "publication": "staged_then_atomic_rename",
        }
        (stage / "deployment_audit.json").write_bytes(canonical(audit))
        stage.rename(final)
        try:
            result = verify_published(final / readiness_name)
        except BaseException:
            quarantine = DESTINATION / f".{authority_id}.failed.{os.getpid()}"
            final.rename(quarantine)
            raise
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    print(
        json.dumps(
            {
                "status": "PUBLISHED_VERIFIED",
                "authority_id": authority_id,
                "directory": str(final),
                "readiness_manifest": str(final / readiness_name),
                "launcher": str(final / "launch_tpch_v30_debug.py"),
                **result,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
