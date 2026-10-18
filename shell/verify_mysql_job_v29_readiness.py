"""Fail-closed verifier for the JOB v29 launcher-closure successor."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Mapping

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from shell.verify_mysql_job_v27_readiness import Check
from shell.verify_mysql_job_v28_readiness import (
    verify_document as verify_v28_document,
)


V28_ID = "59cbc6843414fa88ade1c56951b747e85ccf90154d98003909ebbaacdf3c1ec0"
EXPECTED_SERVICE = "shell.mysql_algorithm_c_local_services_v28:build"
EXPECTED_LAUNCHER = "launch_job_v29_debug.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pointer_ok(pointer: Any) -> bool:
    if not isinstance(pointer, Mapping):
        return False
    path = Path(str(pointer.get("path") or ""))
    expected = str(pointer.get("sha256") or "").lower()
    return (
        len(expected) == 64
        and path.is_file()
        and not path.is_symlink()
        and _sha256(path) == expected
    )


def _method(document: Mapping[str, Any]) -> Mapping[str, Any]:
    method_bundle = document.get("method_bundle")
    pointer = (
        method_bundle.get("method_manifest")
        if isinstance(method_bundle, Mapping)
        else None
    )
    if not _pointer_ok(pointer):
        return {}
    loaded = json.loads(
        Path(str(pointer["path"])).read_text(encoding="utf-8")
    )
    return loaded if isinstance(loaded, Mapping) else {}


def verify_document(
    document: Mapping[str, Any], root: Path | None = None,
) -> list[Check]:
    checks = list(verify_v28_document(document, root))

    def add(
        requirement: str, item: str, passed: bool, reason: str,
    ) -> None:
        checks.append(Check(requirement, item, bool(passed), reason))

    execution = document.get("execution_authority")
    method = _method(document)
    runtime = method.get("runtime_binding")
    launcher = (
        Path(str(runtime.get("entrypoint") or ""))
        if isinstance(runtime, Mapping)
        else Path("")
    )
    add(
        "SCHEMA",
        "v29_successor",
        document.get("schema_version") == "mysql_job_v29_readiness_v1"
        and isinstance(execution, Mapping)
        and execution.get("schema_version")
        == "mysql_job_v29_execution_authority_v1"
        and execution.get("supersedes_bundle_id") == V28_ID,
        "readiness must explicitly supersede the defective v28 launcher",
    )
    add(
        "AUTH",
        "launcher_binding",
        isinstance(runtime, Mapping)
        and launcher.is_absolute()
        and launcher.name == EXPECTED_LAUNCHER
        and launcher.is_file()
        and not launcher.is_symlink()
        and runtime.get("service_factory") == EXPECTED_SERVICE
        and runtime.get("launcher_import_closure_verified") is True,
        "method runtime must bind the immutable v29 launcher and v28 factory",
    )
    launcher_pointer = (
        (document.get("method_bundle") or {}).get("launcher")
        if isinstance(document.get("method_bundle"), Mapping)
        else None
    )
    add(
        "R07",
        "launcher_checksum",
        _pointer_ok(launcher_pointer)
        and Path(str(launcher_pointer.get("path"))) == launcher,
        "readiness must checksum-bind the exact runtime launcher",
    )
    add(
        "R12",
        "accepted_round_gate",
        isinstance(execution, Mapping)
        and execution.get("accepted_round_authorized") is False
        and (document.get("scenarios") or {})
        .get("job_memory", {})
        .get("accepted_round_authorized")
        is False,
        "v29 is debug/fresh-R0 only and cannot count accepted rounds",
    )
    return checks


def verify_launcher_help(document: Mapping[str, Any]) -> dict[str, Any]:
    method = _method(document)
    runtime = method.get("runtime_binding")
    if not isinstance(runtime, Mapping):
        raise RuntimeError("runtime binding is absent")
    launcher = Path(str(runtime.get("entrypoint") or ""))
    if not launcher.is_file() or launcher.is_symlink():
        raise RuntimeError("bound launcher is absent or symlinked")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    with tempfile.TemporaryDirectory(prefix="mysql_job_v29_verify.") as cwd:
        completed = subprocess.run(
            [sys.executable, "-I", str(launcher), "--help"],
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    combined = completed.stdout + completed.stderr
    passed = (
        completed.returncode == 0
        and "Run or analyze one isolated MySQL Algorithm C" in combined
        and "ModuleNotFoundError" not in combined
    )
    return {
        "passed": passed,
        "returncode": completed.returncode,
        "clean_cwd": True,
        "isolated_python": True,
        "pythonpath_removed": True,
        "help_signature_found":
            "Run or analyze one isolated MySQL Algorithm C" in combined,
        "module_not_found": "ModuleNotFoundError" in combined,
        "stderr": completed.stderr,
    }


def verify_ready(document: Mapping[str, Any]) -> None:
    failed = [item for item in verify_document(document) if not item.passed]
    if failed:
        detail = "; ".join(f"{item.item}: {item.reason}" for item in failed)
        raise RuntimeError(f"JOB v29 readiness rejected: {detail}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text(encoding="utf-8"))
    checks = verify_document(document)
    failed = [item for item in checks if not item.passed]
    launcher = (
        verify_launcher_help(document)
        if not failed
        else {"passed": False, "skipped": "document checks failed"}
    )
    print(json.dumps({
        "passed": not failed and launcher["passed"],
        "check_count": len(checks),
        "failed": [
            {
                "requirement": item.requirement,
                "item": item.item,
                "reason": item.reason,
            }
            for item in failed
        ],
        "launcher_help": launcher,
    }, sort_keys=True))
    return 0 if not failed and launcher["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
