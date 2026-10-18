from __future__ import annotations

import json
from pathlib import Path

import pytest

from mysql_parameter_optimizer import validate_config


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config/formal/mysql_sgd_production/hybench_xp_io.algorithm_c.optimizer.json"


def test_xp_optimizer_config_is_rejected_until_real_registry_binding_is_authorized() -> None:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="registry_query_ids|not authorized|execution"):
        validate_config(config)
