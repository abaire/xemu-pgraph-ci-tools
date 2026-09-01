from __future__ import annotations

import os
import tempfile
import unittest

from xemu_pgraph_ci_tools.models import (
    ComparisonSummary,
    Difference,
    DiffTask,
    ResultsInfo,
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

    def test_results_info_parsing_3_levels(self):
        fake_path = (
            "/path/to/results/xemu-0.8.134-fc9980d2962cbec656253106ea2e121fab1e68d4/Darwin_arm64/gl_Apple_Apple_M5_Max"
        )
        info = ResultsInfo.parse(fake_path)
        assert info.xemu_version == "xemu-0.8.134-fc9980d2962cbec656253106ea2e121fab1e68d4"
        assert info.platform_info == "Darwin_arm64"
        assert info.gl_info == "gl_Apple_Apple_M5_Max"
        assert info.output_subdirectory == os.path.join(
            "xemu-0.8.134-fc9980d2962cbec656253106ea2e121fab1e68d4", "Darwin_arm64", "gl_Apple_Apple_M5_Max"
        )

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

    def test_difference_properties(self):
        diff = Difference(
            test_suite="suite1",
            test_case="test1",
            result_artifact="/path/result.png",
            golden_artifact="/path/golden.png",
            distance=5.0,
        )
        assert diff.fully_qualified_test_name == "suite1:test1"
        assert diff.difference_filename == os.path.join("suite1", "test1-diff.png")
        d = diff.to_dict()
        assert d["distance"] == 5.0

    def test_diff_task_serialization(self):
        task = DiffTask(
            suite="suite1",
            test_case="test1",
            source_image="/path/source.png",
            golden_image="/path/golden.png",
            output_diff_image="/path/diff.png",
            results_path="/results",
            results_identifier="run1",
            golden_identifier="golden1",
            comparison_output_dir="/output",
        )
        assert task.fully_qualified_test_name == "suite1:test1"
        data = task.to_dict()
        loaded = DiffTask.from_dict(data)
        assert loaded.suite == task.suite
        assert loaded.test_case == task.test_case
        assert loaded.source_image == task.source_image
        assert loaded.golden_image == task.golden_image
        assert loaded.output_diff_image == task.output_diff_image

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


if __name__ == "__main__":
    unittest.main()
