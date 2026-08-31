from __future__ import annotations

import json
import os
import tempfile
import unittest

from xemu_pgraph_ci_tools.models import (
    ComparisonSummary,
    KnownIssueFilter,
    KnownIssuesRegistry,
    PipelineReport,
    ResultsInfo,
    TestResultItem,
)


class TestModels(unittest.TestCase):
    def test_results_info_parsing(self):
        fake_path = "/path/to/results/v0.8.15/Linux_x86_64/4.6_Core/4.60"
        info = ResultsInfo.parse(fake_path)
        assert info.xemu_version == "v0.8.15"
        assert info.platform_info == "Linux_x86_64"
        assert info.gl_info == "4.6_Core:4.60"
        assert info.gl_version == "4.6_Core"
        assert info.glsl_version == "4.60"
        assert info.run_identifier == "v0.8.15:Linux_x86_64:4.6_Core:4.60"
        assert info.output_subdirectory == os.path.join("v0.8.15", "Linux_x86_64", "4.6_Core", "4.60")
        assert info.run_identifier_subdirectory == "v0.8.15__Linux_x86_64__4.6_Core__4.60"

    def test_results_info_find_images(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            suite_dir = os.path.join(tmpdir, "v1", "Darwin", "4.1", "4.10", "suite1")
            os.makedirs(suite_dir)
            test_png = os.path.join(suite_dir, "test1.png")
            diff_png = os.path.join(suite_dir, "test1-diff.png")
            with open(test_png, "w") as f:
                f.write("fake image")
            with open(diff_png, "w") as f:
                f.write("fake diff")

            info = ResultsInfo.parse(os.path.join(tmpdir, "v1", "Darwin", "4.1", "4.10"))
            assert "suite1" in info.test_suites
            assert "test1" in info.test_suites["suite1"]
            assert "test1-diff" not in info.test_suites["suite1"]
            assert info.get_flattened_tests() == {"suite1:test1"}

    def test_known_issue_filter(self):
        flt = KnownIssueFilter(platform="Linux*", gl="4.*", glsl="4.*")
        assert flt.matches("Linux_x86_64", "4.6_Core", "4.60")
        assert not flt.matches("Darwin_arm64", "4.1_Core", "4.10")
        assert not flt.matches("Linux_x86_64", "3.3", "3.30")

    def test_known_issues_registry(self):
        data = {
            "SuiteA": {
                "issues": [
                    {
                        "text": "Suite level bug on macOS",
                        "filter": {"platform": "Darwin*"},
                    }
                ],
                "Test1": {
                    "issues": [
                        {
                            "text": "Test1 broken on all platforms",
                        }
                    ]
                },
            }
        }
        registry = KnownIssuesRegistry(data)

        darwin_issues = registry.get_known_issues("SuiteA", "Test1", "Darwin_arm64", "4.1", "4.10")
        assert "Suite level bug on macOS" in darwin_issues
        assert "Test1 broken on all platforms" in darwin_issues

        linux_issues = registry.get_known_issues("SuiteA", "Test1", "Linux_x86_64", "4.6", "4.60")
        assert "Suite level bug on macOS" not in linux_issues
        assert "Test1 broken on all platforms" in linux_issues

    def test_comparison_summary_serialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = os.path.join(tmpdir, "summary.json")
            summary = ComparisonSummary(
                result_identifier="run1",
                golden_identifier="Xbox_Hardware",
                tests_without_goldens=["SuiteA:Test1"],
                goldens_without_results=["SuiteB:Test2"],
                tests_with_differences={"SuiteA:Test3": 12.5},
            )
            summary.save_to_file(summary_file)

            loaded = ComparisonSummary.load_from_file(summary_file)
            assert loaded.result_identifier == "run1"
            assert loaded.golden_identifier == "Xbox_Hardware"
            assert loaded.tests_without_goldens == ["SuiteA:Test1"]
            assert loaded.tests_with_differences == {"SuiteA:Test3": 12.5}

    def test_test_result_item_regression(self):
        item_pass = TestResultItem(
            suite="SuiteA",
            test_name="TestPass",
            result_image_path="/path/test.png",
            hw_diff_score=0.0,
        )
        assert not item_pass.is_regression

        item_known = TestResultItem(
            suite="SuiteA",
            test_name="TestKnown",
            result_image_path="/path/test.png",
            hw_diff_score=15.0,
            known_issues=["Known HW glitch"],
        )
        assert not item_known.is_regression

        item_regression = TestResultItem(
            suite="SuiteA",
            test_name="TestReg",
            result_image_path="/path/test.png",
            hw_diff_score=50.0,
        )
        assert item_regression.is_regression

    def test_pipeline_report_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_file = os.path.join(tmpdir, "report.json")
            report = PipelineReport(
                generated_at="2026-08-30T12:00:00Z",
                results_dir="/results",
                golden_dir="/goldens",
                xemu_baseline_dir=None,
                total_tests=10,
                passed_tests=8,
                differing_tests=2,
                missing_goldens=0,
                regressions_count=1,
                test_results=[
                    TestResultItem(
                        suite="SuiteA",
                        test_name="Test1",
                        result_image_path="/results/SuiteA/Test1.png",
                        hw_diff_score=10.0,
                    )
                ],
                metadata={"branch": "feature-x"},
            )
            report.save_json(report_file)

            with open(report_file) as f:
                data = json.load(f)
            assert data["total_tests"] == 10
            assert data["regressions_count"] == 1
            assert data["metadata"]["branch"] == "feature-x"
            assert len(data["test_results"]) == 1


if __name__ == "__main__":
    unittest.main()
