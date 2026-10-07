from __future__ import annotations

import os
import tempfile
import unittest

from xemu_pgraph_ci_tools.golden_config import GoldenConfig
from xemu_pgraph_ci_tools.models import (
    ComparisonSummary,
    Difference,
    DiffTask,
    ResultsInfo,
    RunIdentifier,
    SourceTestIdentifier,
)


class TestModels(unittest.TestCase):
    def test_run_identifier(self):
        ident = RunIdentifier.parse("results/v0.8.15/Linux_x86_64/4.6_Core/4.60")
        assert ident.xemu_version == "v0.8.15"
        assert ident.platform_info == "Linux_x86_64"
        assert ident.gl_info == "4.6_Core:4.60"
        assert ident.gl_version == "4.6_Core"
        assert ident.glsl_version == "4.60"
        assert ident.string_identifier == "v0.8.15:Linux_x86_64:4.6_Core:4.60"

        source_test = SourceTestIdentifier(
            xemu_version="v0.8.15",
            platform_info="Linux_x86_64",
            suite_name="SuiteA",
            test_name="Test1",
        )
        assert source_test.suite_name == "SuiteA"
        assert source_test.test_name == "Test1"

    def test_results_info_parsing(self):
        fake_path = "/path/to/results/v0.8.15/Linux_x86_64/4.6_Core/4.60"
        info = ResultsInfo.parse(fake_path)
        assert info.xemu_version == "v0.8.15"
        assert info.platform_info == "Linux_x86_64"
        assert info.gl_info == "4.6_Core:4.60"
        assert info.gl_version == "4.6_Core"
        assert info.glsl_version == "4.60"
        assert info.run_identifier == "v0.8.15:Linux_x86_64:4.6_Core:4.60"
        assert info.output_subdirectory == os.path.join("v0.8.15", "Linux_x86_64", "4.6_Core--4.60")
        assert info.run_identifier_subdirectory == "v0.8.15--Linux_x86_64--4.6_Core--4.60"

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

    def test_run_identifier_double_underscore(self):
        ident = RunIdentifier(
            xemu_version="xemu-0.8.136",
            platform_info="Darwin_arm64",
            gl_info="gl_Apple:4.10",
        )
        assert ident.minimal_path == os.path.join("xemu-0.8.136", "Darwin_arm64", "gl_Apple--4.10")
        assert ident.minimal_identifier().gl_info == "gl_Apple--4.10"
        assert ident.gl_version == "gl_Apple"
        assert ident.glsl_version == "4.10"

        # Parsing compare-results path with double underscore
        p = "compare-results/xemu-0.8.136/Darwin_arm64/gl_Apple__4.10/Xbox__Xbox__DirectX__nv2a"
        parsed = RunIdentifier.parse(p)
        assert parsed.xemu_version == "xemu-0.8.136"
        assert parsed.platform_info == "Darwin_arm64"
        assert parsed.gl_version == "gl_Apple"
        assert parsed.glsl_version == "4.10"

        # Parsing legacy path with double hyphen
        p_legacy = "compare-results/xemu-0.8.136/Darwin_arm64/gl_Apple--4.10/Xbox--Xbox--DirectX--nv2a"
        parsed_legacy = RunIdentifier.parse(p_legacy)
        assert parsed_legacy.xemu_version == "xemu-0.8.136"
        assert parsed_legacy.platform_info == "Darwin_arm64"
        assert parsed_legacy.gl_version == "gl_Apple"
        assert parsed_legacy.glsl_version == "4.10"

    def test_comparison_summary_merge_clears_resolved_goldens(self):
        # Base summary had false tests_without_goldens
        base = ComparisonSummary(
            result_identifier="run1",
            golden_identifier="Xbox_Hardware",
            tests_without_goldens=["SuiteA:Test1", "SuiteA:Test2", "SuiteA:MissingGolden"],
            tests_evaluated=["SuiteA:Test1", "SuiteA:Test2", "SuiteA:MissingGolden"],
        )
        # Other summary (e.g. legacy or re-run) evaluated Test1 with diff, and Test2 passed cleanly
        other = ComparisonSummary(
            result_identifier="run1",
            golden_identifier="Xbox_Hardware",
            tests_with_differences={"SuiteA:Test1": 15.0},
            tests_evaluated=["SuiteA:Test1", "SuiteA:Test2"],
            tests_without_goldens=[],
        )
        base.merge(other)
        # Test1 has difference -> NOT without golden
        # Test2 evaluated and not without golden -> NOT without golden
        # MissingGolden -> still without golden
        assert base.tests_without_goldens == ["SuiteA:MissingGolden"]
        assert "SuiteA:Test1" in base.tests_with_differences
        assert base.tests_with_differences["SuiteA:Test1"] == 15.0

    def test_comparison_summary_merge_clears_clean_passes(self):
        # Base summary had a difference on Test1
        base = ComparisonSummary(
            result_identifier="run1",
            golden_identifier="Xbox_Hardware",
            tests_with_differences={"SuiteA:Test1": 10.0, "SuiteA:Test2": 20.0},
            tests_evaluated=["SuiteA:Test1", "SuiteA:Test2"],
        )
        # Re-run found Test1 now cleanly passes (0 diff, in tests_evaluated, not in diffs or missing)
        rerun = ComparisonSummary(
            result_identifier="run1",
            golden_identifier="Xbox_Hardware",
            tests_with_differences={},
            tests_evaluated=["SuiteA:Test1"],
            tests_without_goldens=[],
        )
        base.merge(rerun)
        assert "SuiteA:Test1" not in base.tests_with_differences
        assert "SuiteA:Test2" in base.tests_with_differences

    def test_comparison_summary_filter_deprecated(self):
        summary = ComparisonSummary(
            result_identifier="run1",
            golden_identifier="Xbox_Hardware",
            goldens_without_results=["SuiteA:Test1", "SuiteB:TestDeprecated"],
            tests_with_differences={"SuiteA:Test1": 10.0, "SuiteB:TestDeprecated": 20.0},
        )
        cfg = GoldenConfig(deprecated_tests={"SuiteB": ["TestDeprecated"]})
        summary.filter_deprecated(cfg)
        assert "SuiteB:TestDeprecated" not in summary.goldens_without_results
        assert "SuiteA:Test1" in summary.goldens_without_results
        assert "SuiteB:TestDeprecated" not in summary.tests_with_differences
        assert "SuiteA:Test1" in summary.tests_with_differences


if __name__ == "__main__":
    unittest.main()
