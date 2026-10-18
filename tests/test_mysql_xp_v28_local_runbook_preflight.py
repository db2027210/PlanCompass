import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = (
    ROOT
    / "artifacts/mysql_xp_node02_successor_v28"
    / "a0187979839c8fdb9530da525425a031bca9d955234d754cc495dbe0d5b342e9"
)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fresh_stdin_equivalent_materials_pass_local_preflight(tmp_path):
    producer = load(
        "xp_v28_materializer",
        BUNDLE / "test_support/mysql_xp_qualification_bootstrap_materials_v5.py",
    )
    checker = load(
        "xp_v28_preflight", ROOT / "shell/preflight_mysql_xp_v28_materials.py"
    )
    result = producer.create_materials(
        BUNDLE, tmp_path / "materials", 10, "local-dummy-not-a-real-secret"
    )
    materials = Path(result["materials_directory"])
    checker.verify_bundle(BUNDLE)
    plan, remaining = checker.verify_materials(materials, BUNDLE)
    assert remaining > 0
    assert plan["ssh_prohibited"] is True
    assert not list(BUNDLE.rglob("__pycache__"))
    assert not list(BUNDLE.rglob("*.pyc"))
