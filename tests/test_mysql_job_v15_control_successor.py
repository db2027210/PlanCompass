from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


HERE = Path(__file__).resolve()
PACKAGE_ROOT = HERE.parents[1]
ROOT = next(candidate for candidate in HERE.parents
            if (candidate / "artifacts/mysql_xp_node02_successor_v23").is_dir())
XP_V20 = (
    ROOT / "artifacts/mysql_xp_node02_successor_v20"
    / "f2dd55ea6b5ea4db704aa533bddc9161b378c30735b9606a092efccc1315179f"
)
XP_V21 = (
    ROOT / "artifacts/mysql_xp_node02_successor_v21"
    / "f6528795e2eabc84c7d29ff8d387bc50ffd0bbb055e4fc0987da5aa8626717c2"
)
XP_V22 = (
    ROOT / "artifacts/mysql_xp_node02_successor_v22"
    / "21d1a853548ebdff573d18a82c7a124b810b5656e3e3a330146f87d3e0b460ad"
)
XP_V23 = (
    ROOT / "artifacts/mysql_xp_node02_successor_v23"
    / "ba68d4a57f7ebb03f8e9c662250492aac7341757c66c56256665c41b5ee3a77e"
)


def module(name: str, relative: str):
    path = ROOT / relative
    if (PACKAGE_ROOT / "controller").is_dir() and relative.startswith("shell/"):
        basename = Path(relative).name
        packaged = {
            "mysql_xp_successor_binding_v1.py": "controller/" + basename,
            "render_mysql_job_xp_successor_short_ttl_v2.py":
                "controller/render_mysql_job_xp_successor_short_ttl_v3.py",
            "mysql_job_xp_four_flow_controller_v1.py": "controller/" + basename,
        }
        path = PACKAGE_ROOT / packaged.get(basename, relative)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class XpSuccessorBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binding = module("xp_successor_binding_test", "shell/mysql_xp_successor_binding_v1.py")

    def test_final_v23_binding_and_three_hop_chain_are_exact(self):
        row = self.binding.build_binding(XP_V23)
        self.assertEqual(self.binding.V23, row["xp_bundle_id"])
        self.assertEqual(
            [self.binding.V20, self.binding.V21, self.binding.V22, self.binding.V23],
            [item["bundle_id"] for item in row["sealed_chain"]],
        )
        self.assertEqual(
            "e993fa1dd8fe45af42a18980e4bd0623c99409248bb1a62d627da40439b88ca3",
            row["artifacts"]["collector"]["sha256"],
        )
        self.assertEqual(self.binding.V23_CRITICAL_SHA256, {
            role: item["sha256"] for role, item in row["artifacts"].items()
        })

    def synthetic_v21(self, root: Path, predecessor: str) -> Path:
        payloads = {
            relative: ("fixture " + role + "\n").encode()
            for role, relative in self.binding.FILES.items()
        }
        unsigned = {
            "schema_version": "mysql_xp_node02_successor_bundle_v21",
            "status": "SEALED_TEST_SUCCESSOR_NOT_LAUNCHED",
            "predecessor_bundle_id": predecessor,
            "scenario": "hybench_xp_io",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "postgresql_mutation_prohibited": True,
            "formal_launch_authorized": False,
            "accepted_round_authorized": False,
            "files": {
                relative: hashlib.sha256(payload).hexdigest()
                for relative, payload in payloads.items()
            },
        }
        bundle_id = hashlib.sha256(self.binding.canonical(unsigned)).hexdigest()
        bundle = root / bundle_id
        bundle.mkdir()
        for relative, payload in payloads.items():
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        manifest = dict(unsigned, bundle_id=bundle_id)
        encoded = self.binding.canonical(manifest)
        path = bundle / "bundle_manifest.json"
        path.write_bytes(encoded)
        (bundle / "bundle_manifest.json.sha256").write_text(
            "%s  bundle_manifest.json\n" % hashlib.sha256(encoded).hexdigest(), encoding="ascii"
        )
        return bundle

    def test_intermediate_v22_has_no_v23_chain_credit(self):
        row = self.binding.build_binding(XP_V22)
        self.assertEqual(self.binding.V22, row["sealed_chain"][-1]["bundle_id"])

    def test_v21_wrong_predecessor_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            bundle = self.synthetic_v21(Path(raw), "0" * 64)
            with self.assertRaises(self.binding.BindingError):
                self.binding.build_binding(bundle)


class ShortTtlRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = module(
            "job_xp_short_ttl_renderer_test",
            "shell/render_mysql_job_xp_successor_short_ttl_v2.py",
        )
        cls.template = ROOT / "templates/mysql_job_v13_xp_successor_short_ttl_commands_v2.json"

    def test_template_fixes_publication_tokens_and_live_readbacks(self):
        raw = self.template.read_text(encoding="utf-8")
        self.assertNotIn("f2dd55ea", raw)
        template = json.loads(raw)
        stages = [row["stage"] for row in template["commands"]]
        self.assertIn("transfer_exact_support_bundle_to_incoming", stages)
        self.assertIn("transactionally_publish_support_bundle_on_node02", stages)
        self.assertIn("fresh_job_collect_only_live_readback", stages)
        self.assertIn("fresh_xp_collect_only_live_readback", stages)
        preconditions = " ".join(template["preconditions"])
        self.assertIn("exactly two externally generated 0600 flow tokens", preconditions)
        self.assertIn("JOB solo and JOB paired token paths do not exist", preconditions)
        self.assertNotIn("XP v19", preconditions)

    def test_renderer_resolves_final_v23_and_sealed_chain(self):
        remote = Path(
            "/mnt/mysql_nvme0n1p1/anonymous/resource_adaptive_mysql/hybench_xp/"
            "runtime_algorithm_c_successor_20260729/bundles/" + XP_V23.name
        )
        binding_path = Path("/tmp/attempt-control/xp_successor_binding.json")
        binding, rendered = self.renderer.render(self.template, XP_V23, remote, binding_path)
        self.assertEqual("RENDERED_NOT_AUTHORITY_NOT_EXECUTED", rendered["status"])
        self.assertEqual(XP_V23.name, rendered["xp_bundle_id"])
        self.assertEqual(str(binding_path), rendered["xp_successor_binding"])
        encoded = json.dumps(rendered)
        self.assertNotIn("[XP_BUNDLE_ID_FROM_BINDING]", encoded)
        self.assertEqual(XP_V23.name, binding["xp_bundle_id"])
        self.assertEqual(binding["sealed_chain"], rendered["xp_sealed_chain"])

    def test_renderer_rejects_intermediate_v22(self):
        remote = Path("/remote/bundles") / XP_V22.name
        with self.assertRaises(self.renderer.RenderError):
            self.renderer.render(
                self.template, XP_V22, remote,
                Path("/tmp/attempt-control/xp_successor_binding.json"),
            )

    def test_xp_dispatch_command_carries_successor_binding_before_mode(self):
        flow = module("job_xp_v2_command_test", "shell/mysql_job_xp_four_flow_controller_v1.py")
        launch = {
            "scenario_backend": "xp",
            "mode": "qualification_solo",
            "bundle": str(XP_V23),
            "container": "xp-db",
            "mysql_client_container": "xp-client",
            "mysql_defaults_file": "/private/mysql-client.cnf",
            "output": "/tmp/xp-evidence.json",
            "main_agent_token_file": "/tmp/xp-token",
            "main_agent_authority": "/tmp/xp-authority.json",
            "command_spec": "/tmp/xp-command.json",
            "xp_successor_binding": "/tmp/xp_successor_binding.json",
        }
        argv = flow.command(Path("/support/shared.py"), launch)
        binding_index = argv.index("--xp-successor-binding")
        mode_index = argv.index("--qualification-solo")
        self.assertLess(binding_index, mode_index)
        self.assertEqual("/tmp/xp_successor_binding.json", argv[binding_index + 1])


if __name__ == "__main__":
    unittest.main()
