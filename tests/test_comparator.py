from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from xemu_pgraph_ci_tools.comparator import (
    _discover_results,
    perform_comparison,
    reduce_comparison_summaries,
)
from xemu_pgraph_ci_tools.golden_config import GoldenConfig
from xemu_pgraph_ci_tools.models import ComparisonSummary, Difference


class TestComparator(unittest.TestCase):
    def test_discover_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run1 = os.path.join(tmpdir, "v1", "Linux", "4.6", "4.60")
            os.makedirs(run1)
            with open(os.path.join(run1, "results.json"), "w") as f:
                f.write("{}")

            run2 = os.path.join(tmpdir, "v2", "Darwin", "4.1", "4.10")
            os.makedirs(run2)
            with open(os.path.join(run2, "results.json"), "w") as f:
                f.write("{}")

            discovered = _discover_results(tmpdir)
            assert len(discovered) == 2
            assert any("v1" in p for p in discovered)
            assert any("v2" in p for p in discovered)

    def test_perform_comparison_mocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res_dir = os.path.join(tmpdir, "results", "v1", "Linux", "4.6", "4.60")
            res_suite = os.path.join(res_dir, "Suite1")
            os.makedirs(res_suite)
            with open(os.path.join(res_suite, "Test1.png"), "w") as f:
                f.write("res")

            golden_dir = os.path.join(tmpdir, "goldens")
            golden_suite = os.path.join(golden_dir, "Suite1")
            os.makedirs(golden_suite)
            with open(os.path.join(golden_suite, "Test1.png"), "w") as f:
                f.write("gold")

            output_dir = os.path.join(tmpdir, "output")

            with patch.object(
                Difference,
                "generate_difference_image",
                return_value=(0, "0 pixels are different", ""),
            ):
                summary = perform_comparison(
                    results_path=res_dir,
                    golden_path=golden_dir,
                    output_dir=output_dir,
                    use_lpips=False,
                )

                assert summary.result_identifier == "v1:Linux:4.6:4.60"
                assert summary.tests_without_goldens == []
                assert summary.goldens_without_results == []
                assert summary.tests_with_differences == {}

    def test_difference_generate_diff_image_mocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            diff = Difference(
                test_suite="SuiteA",
                test_case="Test1",
                result_artifact=os.path.join(tmpdir, "res.png"),
                golden_artifact=os.path.join(tmpdir, "gold.png"),
                distance=0.0,
            )
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1,
                    stdout=b"100 pixels are different\n",
                    stderr=b"",
                )
                code, stdout, _stderr = diff.generate_difference_image("perceptualdiff", tmpdir)
                assert code == 1
                assert "100 pixels are different" in stdout
                mock_run.assert_called_once()

    def test_reduce_comparison_summaries_with_golden_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            comp_dir = os.path.join(tmpdir, "comp_run")
            os.makedirs(comp_dir)
            shard1 = ComparisonSummary(
                result_identifier="v1:Linux",
                golden_identifier="Xbox_Hardware",
                goldens_without_results=["SuiteA:Test1", "SuiteB:DeprecatedTest"],
                tests_with_differences={"SuiteA:Test1": 10.0, "SuiteB:DeprecatedTest": 20.0},
            )
            shard1.save_to_file(os.path.join(comp_dir, "summary.shard1.json"))

            cfg = GoldenConfig(deprecated_tests={"SuiteB": ["DeprecatedTest"]})
            reduce_comparison_summaries(tmpdir, golden_config=cfg)

            final_summary = ComparisonSummary.load_from_file(os.path.join(comp_dir, "summary.json"))
            assert "SuiteB:DeprecatedTest" not in final_summary.goldens_without_results
            assert "SuiteA:Test1" in final_summary.goldens_without_results
            assert "SuiteB:DeprecatedTest" not in final_summary.tests_with_differences
            assert "SuiteA:Test1" in final_summary.tests_with_differences


if __name__ == "__main__":
    unittest.main()
