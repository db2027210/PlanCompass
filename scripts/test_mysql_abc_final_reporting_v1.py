#!/usr/bin/env python3
"""Offline regression tests for final A/B/C evidence aggregation and plots."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts import build_mysql_abc_final_report_v1 as report


class FinalReportingTest(unittest.TestCase):
    def test_memory_pair_projection_resolves_physical_decision_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "memory_pair" / "run_state.json"
            source.parent.mkdir()
            source.write_text("{}\n", encoding="utf-8")
            state = {
                "memory_pair_source_state": str(source),
                "memory_pair_source_state_sha256": report.sha(source),
            }
            self.assertEqual(
                report.c_evidence_root(root / "projected", state), source.parent,
            )

    def test_strict_outer_progress_rejects_nonmonotonic_curve(self) -> None:
        with self.assertRaises(RuntimeError):
            report.assert_strict_outer_progress(
                [{"outer_metric_qps": 1.0}, {"outer_metric_qps": 0.9}],
                scene="job_memory", algorithm="B",
            )

    def test_actual_completed_b_curves_are_strict(self) -> None:
        roots = {
            "job_memory": report.B_ROOTS["job_memory"],
            "tpch_sf4_memory": report.B_ROOTS["tpch_sf4_memory"],
        }
        for scene, root in roots.items():
            curve = report.bc_curve(root, scene, "B")
            self.assertTrue(curve)
            report.assert_strict_outer_progress(curve, scene=scene, algorithm="B")

    def test_plotter_exports_pdf_and_png(self) -> None:
        curves = {
            scene: {
                algorithm: [
                    {
                        "accepted_index": index,
                        "client_incumbent_qps": float(index + offset),
                    }
                    for index in (1, 2, 3)
                ]
                for offset, algorithm in enumerate(("A", "B", "C"), 1)
            }
            for scene in report.SCENES
        }
        summary = {
            "status": "PASS",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "convergence_curves": curves,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "summary.json"
            output = root / "figures"
            source.write_text(json.dumps(summary), encoding="utf-8")
            subprocess.run([
                str(report.PLOT_PYTHON), str(report.PLOTTER),
                "--summary", str(source), "--output-dir", str(output),
            ], check=True, capture_output=True, text=True)
            self.assertGreater(
                (output / "mysql_abc_accepted_client_incumbent_curves.pdf").stat().st_size,
                0,
            )
            self.assertGreater(
                (output / "mysql_abc_accepted_client_incumbent_curves.png").stat().st_size,
                0,
            )


if __name__ == "__main__":
    unittest.main()
