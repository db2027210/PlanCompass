from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def load_v66():
    sys.path.insert(0, str(TOOLS))
    try:
        return importlib.import_module("job_fresh10_target10_autonomous_v66_gradient_refresh")
    finally:
        sys.path.pop(0)


def candidate(rank: int) -> dict:
    params = {"p": 1.0}
    components = {
        "p": {
            resource: {"phase": "current_round_directional_secant"}
            for resource in ("cpu", "io", "memory")
        }
    }
    return {
        "rank": rank,
        "selection_role": "probe",
        "boundary_corner": False,
        "candidate_params": params,
        "three_level_fingerprint": {
            "id": f"three-level-sha256:{rank:064x}",
            "valid": True,
        },
        "feature_ratio": {"cpu": 1.0, "io": 1.0, "memory": 1.0},
        "predicted_resource": {"cpu": 1.0, "io": 100.0, "memory": 2.5},
        "utilization": {"cpu": 1.0 / 3.0, "io": 100.0 / 256.0, "memory": 0.86},
        "dScore/dU": {"p": {name: 1.0 for name in ("cpu", "io", "memory")}},
        "T": {"p": {name: 0.0 for name in ("cpu", "io", "memory")}},
        "confidence": {"p": 1.0},
        "g_j": {"p": 0.0},
        "eta": 0.0,
        "delta_param": {"p": {"absolute": 0.0}},
        "candidate_score": 1.0,
        "gradient_components": components,
        "gradient_contract": "job_current_round_directional_secant_v1",
        "gradient_source": "current_incumbent_and_current_candidate_model",
        "gradient_zero_guard_passed": True,
    }


def test_v66_pins_refreshed_generator_and_keeps_v65_sealed() -> None:
    module = load_v66()
    assert module.implementation.CONTROLLER_VERSION == "v66"
    assert hashlib.sha256(module.GENERATOR.read_bytes()).hexdigest() == (
        module.EXPECTED_GENERATOR_SHA256
    )
    assert hashlib.sha256(Path(module.v65.__file__).read_bytes()).hexdigest() == (
        module.EXPECTED_V65_SHA256
    )


def test_v66_top5_gate_rejects_stale_gradient(tmp_path: Path) -> None:
    module = load_v66()
    path = tmp_path / "top5.json"
    payload = {
        "diagnostic_only": True,
        "top5": [candidate(rank) for rank in range(1, 6)],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    module.validate_top5_v66(
        path,
        roles=("probe",) * 5,
        diagnostic_only=True,
    )

    payload["top5"][2]["gradient_components"]["p"]["cpu"]["phase"] = (
        "original_gradient"
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.implementation.v49.FailClosed, match="rank.*3"):
        module.validate_top5_v66(
            path,
            roles=("probe",) * 5,
            diagnostic_only=True,
        )
