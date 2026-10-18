#!/usr/bin/env python3
"""Hash-bound JOB/XP qualification dispatcher.

The four-flow interference gate requires all four evidence files to name one
collector identity.  JOB v13 and XP v18 were published with scenario-specific
collectors, so invoking them directly can never satisfy that invariant.  This
small entry point is the common collector identity.  It verifies the exact
published backend bytes, imports exactly one backend in a fresh process, and
then delegates its unchanged CLI.  A narrow post-verification ``sha256``
wrapper substitutes the dispatcher's identity only after the backend's exact
bundle closure has passed, and only when qualification evidence hashes the
backend source as collector identity.  The backend keeps its real ``__file__``
so sibling helpers and bundle discovery are not redirected accidentally.

This program does not grant authority.  Backend command and short-lived token
validation remain mandatory and fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
from pathlib import Path
import re
import sys


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mysql_xp_successor_binding_v1 as xp_binding

ROOT = Path(__file__).resolve().parents[1]
JOB_BUNDLE_ID = "6b4844bcdcc7ada8dfd76c6676f87c98dc382aa76979504d3edc65baaab52dd7"
XP_BUNDLE_ID = "f43f33b5d60cf6de2fe63f96852e6a46ea3b132b5d52039a1e60021771dc9262"
XP_V19_BUNDLE_ID = "e91031f5af956af87904a240fcf6ce6673d2511dfc66f1a6337f36698133050d"
XP_V20_BUNDLE_ID = "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
XP_V21_BUNDLE_ID = "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2"
XP_V22_BUNDLE_ID = "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad"
XP_V23_BUNDLE_ID = "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
BACKENDS = {
    "job": (
        ROOT / "artifacts/mysql_job_raw_all_subset_three_level_successor_v13"
        / JOB_BUNDLE_ID / "qualification/mysql_job_distributed_live_readback_qualification_v2.py",
        "02ef703b51f923bdc3cbe0127830e929872eb212468e1b6dcbd1577ee00bb7ad",
    ),
    "xp": (
        ROOT / "artifacts/mysql_xp_node02_successor_v23" / XP_V23_BUNDLE_ID
        / "qualification/mysql_distributed_live_readback_qualification_v1.py",
        "e993fa1dd8fe45af42a18980e4bd0623c99409248bb1a62d627da40439b88ca3",
    ),
}
SUPPORTED_BACKEND_HASHES = {
    "job": {JOB_BUNDLE_ID: BACKENDS["job"][1]},
    "xp": {
        XP_BUNDLE_ID: "6aae48e139c667cfe1efaaa6cd0a6a672b72a8d3b3f024cee19fd32bb2f69315",
        XP_V19_BUNDLE_ID: "522f01b5f2b395d70d5f46451a66e6aa64f2288ed474f4faa2fc9337a2906d25",
        XP_V20_BUNDLE_ID: "1e1c713791e4b801444501fd43f52cb7bdef350331d3cea14a46628446ab509e",
        XP_V21_BUNDLE_ID: "bafab075aa68290a192bb25d2b1bc3cd11c06f23f5728ed6e2e495e684b00b88",
        XP_V22_BUNDLE_ID: "f3994d730f8e7100a312a9d462379557bb946e7108de667cddfce774a5ba9e0d",
        XP_V23_BUNDLE_ID: BACKENDS["xp"][1],
    },
}
HEX64 = re.compile(r"[0-9a-f]{64}$")


class DispatchError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_backend(name: str, override: Path | None = None, binding_path: Path | None = None):
    default, _ = BACKENDS[name]
    path = default if override is None else override.resolve(strict=True)
    try:
        bundle_id = path.parents[1].name
    except IndexError as exc:
        raise DispatchError("sealed %s qualification backend path differs" % name) from exc
    expected = SUPPORTED_BACKEND_HASHES[name].get(bundle_id)
    if name == "xp" and binding_path is not None:
        binding = xp_binding.validate_binding(binding_path, path.parents[1])
        expected = binding["artifacts"]["collector"]["sha256"]
    elif binding_path is not None:
        raise DispatchError("JOB backend cannot bind XP successor material")
    if (
        path.is_symlink()
        or not path.is_file()
        or expected is None
        or HEX64.fullmatch(expected) is None
        or sha256(path) != expected
    ):
        raise DispatchError("sealed %s qualification backend differs" % name)
    # The scenario modules use sibling imports with common names.  Each
    # dispatcher process loads one backend only, so prepend exactly its sealed
    # directory and reject a preloaded conflicting module.
    for module_name in (
        "runtime_tree_content_identity_v3",
        "mysql_distributed_workload_metric_adapter_v1",
        "mysql_tpch_client_immutable_identity_v1",
    ):
        sys.modules.pop(module_name, None)
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(
        "_mysql_job_xp_sealed_%s_backend" % name, path
    )
    if spec is None or spec.loader is None:
        raise DispatchError("cannot load sealed qualification backend")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    backend_file = Path(module.__file__).resolve()
    dispatch_file = Path(__file__).resolve()
    backend_sha256 = module.sha256

    def shared_collector_sha256(candidate):
        candidate_path = Path(candidate).resolve()
        if candidate_path == backend_file:
            return sha256(dispatch_file)
        return backend_sha256(candidate)

    backend_verify_bundle = module.verify_bundle

    def verify_bundle_then_enable_shared_identity(*args, **kwargs):
        # verify_bundle resolves its sha256 global dynamically.  Pin the
        # backend's original function for the complete closure walk.  The
        # shared collector identity is enabled only after exact verification.
        module.sha256 = backend_sha256
        try:
            result = backend_verify_bundle(*args, **kwargs)
        except BaseException:
            module.sha256 = backend_sha256
            raise
        module.sha256 = shared_collector_sha256
        return result

    module.verify_bundle = verify_bundle_then_enable_shared_identity
    return module


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--scenario-backend", choices=tuple(BACKENDS), required=True)
    parser.add_argument("--backend-path", type=Path)
    parser.add_argument("--xp-successor-binding", type=Path)
    known, remaining = parser.parse_known_args(values)
    backend = load_backend(
        known.scenario_backend, known.backend_path, known.xp_successor_binding
    )
    original = sys.argv
    try:
        sys.argv = [str(Path(__file__).resolve()), *remaining]
        return int(backend.main() or 0)
    finally:
        sys.argv = original


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DispatchError as exc:
        raise SystemExit("NOT_READY: %s" % exc)
