from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from xemu_pgraph_ci_tools.pipeline import run_pipeline


class TestPipeline(unittest.TestCase):
    def test_run_pipeline_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Results dir structure: results/v1/Linux_x86_64/4.6_Core/4.60/SuiteA/Test1.png
            results_dir = os.path.join(tmpdir, "results", "v1", "Linux_x86_64", "4.6_Core", "4.60")
            suite_a = os.path.join(results_dir, "SuiteA")
            os.makedirs(suite_a)
            with open(os.path.join(results_dir, "results.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(suite_a, "Test1.png"), "w") as f:
                f.write("res1")
            with open(os.path.join(suite_a, "Test2.png"), "w") as f:
                f.write("res2")

            # Golden HW dir
            golden_dir = os.path.join(tmpdir, "hw_goldens")
            golden_suite_a = os.path.join(golden_dir, "SuiteA")
            os.makedirs(golden_suite_a)
            with open(os.path.join(golden_suite_a, "Test1.png"), "w") as f:
                f.write("gold1")
            with open(os.path.join(golden_suite_a, "Test2.png"), "w") as f:
                f.write("gold2")

            # Known issues file
            known_issues_path = os.path.join(tmpdir, "known_issues.json")
            with open(known_issues_path, "w") as f:
                json.dump(
                    {"SuiteA": {"Test2": {"issues": [{"text": "Expected driver bug on Test2"}]}}},
                    f,
                )

            output_dir = os.path.join(tmpdir, "output")

            with patch("xemu_pgraph_ci_tools.comparator._compare_perceptualdiff") as mock_comp:
                mock_comp.return_value = (set(), set(), [])

                report = run_pipeline(
                    results_dir=os.path.join(tmpdir, "results"),
                    golden_dir=golden_dir,
                    xemu_baseline_dir=None,
                    output_dir=output_dir,
                    known_issues_path=known_issues_path,
                    branch="test-branch",
                )

                assert report.total_tests == 2
                assert report.passed_tests == 2
                assert report.regressions_count == 0
                assert report.metadata["branch"] == "test-branch"

                report_json_path = os.path.join(output_dir, "report.json")
                assert os.path.isfile(report_json_path)

                with open(report_json_path) as f:
                    saved = json.load(f)
                assert saved["total_tests"] == 2
                assert len(saved["test_results"]) == 2


if __name__ == "__main__":
    unittest.main()
