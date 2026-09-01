import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from xemu_pgraph_ci_tools.comparator import (
    discover_diff_tasks,
    get_shard_slice,
    partition_diff_tasks,
    process_diff_tasks,
    reduce_comparison_summaries,
)
from xemu_pgraph_ci_tools.hw_diffs import identify_missing_hw_diffs, process_plan_tasks
from xemu_pgraph_ci_tools.models import ComparisonSummary, DiffTask


class TestHwDiffs(unittest.TestCase):
    def test_discover_diff_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            suite_dir = os.path.join(tmpdir, "Blend_Tests")
            os.makedirs(suite_dir)
            test1_png = os.path.join(suite_dir, "test_alpha.png")
            test2_png = os.path.join(suite_dir, "test_color.png")
            with open(test1_png, "w") as f:
                f.write("img1")
            with open(test2_png, "w") as f:
                f.write("img2")

            def get_out(s: str, t: str, _src: str) -> str:
                return os.path.join(tmpdir, "out", s, f"{t}-diff.png")

            def get_gold(s: str, t: str, _src: str) -> str:
                return os.path.join(tmpdir, "gold", s, f"{t}.png")

            tasks = discover_diff_tasks(tmpdir, get_out, get_gold)
            assert len(tasks) == 2
            assert {t.test_case for t in tasks} == {"test_alpha", "test_color"}
            assert tasks[0].suite == "Blend_Tests"

            # With skip_existing
            out_img = get_out("Blend_Tests", "test_alpha", "")
            os.makedirs(os.path.dirname(out_img), exist_ok=True)
            with open(out_img, "w") as f:
                f.write("diff")

            tasks_skipped = discover_diff_tasks(tmpdir, get_out, get_gold, skip_existing=True)
            assert len(tasks_skipped) == 1
            assert tasks_skipped[0].test_case == "test_color"

    def test_identify_missing_hw_diffs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            res_dir = os.path.join(tmpdir, "results", "0.8.0", "Linux_x86_64", "4.6", "4.60")
            suite_dir = os.path.join(res_dir, "MySuite")
            os.makedirs(suite_dir)
            with open(os.path.join(res_dir, "results.json"), "w") as f:
                f.write("{}")

            with open(os.path.join(suite_dir, "test1.png"), "w") as f:
                f.write("t1")
            with open(os.path.join(suite_dir, "test2.png"), "w") as f:
                f.write("t2")

            golden_dir = os.path.join(tmpdir, "golden")
            os.makedirs(os.path.join(golden_dir, "MySuite"))
            with open(os.path.join(golden_dir, "MySuite", "test1.png"), "w") as f:
                f.write("g1")
            with open(os.path.join(golden_dir, "MySuite", "test2.png"), "w") as f:
                f.write("g2")

            output_dir = os.path.join(tmpdir, "compare-results")

            # 1. No output summary yet -> both tests identified
            tasks = identify_missing_hw_diffs(os.path.join(tmpdir, "results"), output_dir, golden_dir=golden_dir)
            assert len(tasks) == 2

            # 2. Simulate existing comparison where test1 was evaluated and passed (no diff), test2 was not present
            comp_dir = os.path.join(
                output_dir,
                "0.8.0",
                "Linux_x86_64",
                "4.6",
                "4.60",
                "Xbox__Xbox__DirectX__nv2a",
            )
            os.makedirs(comp_dir, exist_ok=True)
            summary = ComparisonSummary(
                result_identifier="0.8.0:Linux_x86_64:4.6:4.60",
                golden_identifier="Xbox_Hardware",
                tests_evaluated=["MySuite:test1"],
            )
            summary.save_to_file(os.path.join(comp_dir, "summary.json"))

            # 3. Running identify_missing_hw_diffs again should only identify test2 (the new test)
            tasks_after = identify_missing_hw_diffs(os.path.join(tmpdir, "results"), output_dir, golden_dir=golden_dir)
            assert len(tasks_after) == 1
            assert tasks_after[0].test_case == "test2"

            # 4. Simulate a legacy summary without tests_evaluated (tests_evaluated=[])
            legacy_summary = ComparisonSummary(
                result_identifier="0.8.0:Linux_x86_64:4.6:4.60",
                golden_identifier="Xbox_Hardware",
                tests_evaluated=[],
            )
            legacy_summary.save_to_file(os.path.join(comp_dir, "summary.json"))

            # Since both test1 and test2 have golden images in golden_dir, legacy summary assumes both passed
            tasks_legacy = identify_missing_hw_diffs(os.path.join(tmpdir, "results"), output_dir, golden_dir=golden_dir)
            assert len(tasks_legacy) == 0

    def test_partitioning_and_slices(self) -> None:
        tasks = [
            DiffTask(
                suite="s",
                test_case=f"t{i}",
                source_image=f"src{i}",
                golden_image=f"gold{i}",
                output_diff_image=f"out{i}",
            )
            for i in range(10)
        ]

        partitions = partition_diff_tasks(tasks, max_shards=4)
        assert len(partitions) == 4
        assert sum(len(p) for p in partitions) == 10

        # get_shard_slice
        slice0 = get_shard_slice(tasks, 0, 4)
        slice1 = get_shard_slice(tasks, 1, 4)
        assert slice0 == [tasks[0], tasks[4], tasks[8]]
        assert slice1 == [tasks[1], tasks[5], tasks[9]]

    @patch("xemu_pgraph_ci_tools.models.DiffTask.generate_difference_image")
    def test_process_diff_tasks(self, mock_diff: MagicMock) -> None:
        mock_diff.return_value = (0, "15 pixels are different", "")
        with tempfile.TemporaryDirectory() as tmpdir:
            golden_img = os.path.join(tmpdir, "gold.png")
            with open(golden_img, "w") as f:
                f.write("gold")

            comp_dir = os.path.join(tmpdir, "comp_out")
            task1 = DiffTask(
                suite="SuiteA",
                test_case="Test1",
                source_image=os.path.join(tmpdir, "src1.png"),
                golden_image=golden_img,
                output_diff_image=os.path.join(comp_dir, "SuiteA", "Test1-diff.png"),
                comparison_output_dir=comp_dir,
                results_identifier="RunA",
                golden_identifier="Xbox_Hardware",
            )
            task2_no_golden = DiffTask(
                suite="SuiteA",
                test_case="Test2",
                source_image=os.path.join(tmpdir, "src2.png"),
                golden_image=os.path.join(tmpdir, "nonexistent.png"),
                output_diff_image=os.path.join(comp_dir, "SuiteA", "Test2-diff.png"),
                comparison_output_dir=comp_dir,
                results_identifier="RunA",
                golden_identifier="Xbox_Hardware",
            )

            summaries = process_diff_tasks([task1, task2_no_golden], comp_dir, shard_id="shard_0")
            assert comp_dir in summaries
            summary = summaries[comp_dir]

            assert summary.tests_without_goldens == ["SuiteA:Test2"]
            assert "SuiteA:Test1" in summary.tests_with_differences
            assert summary.tests_with_differences["SuiteA:Test1"] == 15.0
            assert summary.tests_evaluated == ["SuiteA:Test1", "SuiteA:Test2"]

            # Verify shard summary file written
            shard_file = os.path.join(comp_dir, "summary.shard_0.json")
            assert os.path.isfile(shard_file)

    def test_reduce_comparison_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            comp_dir = os.path.join(tmpdir, "compare-results", "RunA", "Xbox_Hardware")
            os.makedirs(comp_dir)

            # Base summary
            base = ComparisonSummary(
                result_identifier="RunA",
                golden_identifier="Xbox_Hardware",
                tests_evaluated=["SuiteA:Test0"],
                tests_with_differences={"SuiteA:Test0": 5.0},
            )
            base.save_to_file(os.path.join(comp_dir, "summary.json"))

            # Shard 0 summary
            s0 = ComparisonSummary(
                result_identifier="RunA",
                golden_identifier="Xbox_Hardware",
                tests_evaluated=["SuiteA:Test1"],
                tests_with_differences={"SuiteA:Test1": 10.0},
                tests_without_goldens=["SuiteA:Missing1"],
            )
            s0.save_to_file(os.path.join(comp_dir, "summary.shard_0.json"))

            # Shard 1 summary
            s1 = ComparisonSummary(
                result_identifier="RunA",
                golden_identifier="Xbox_Hardware",
                tests_evaluated=["SuiteA:Test2"],
                tests_with_differences={"SuiteA:Test2": 20.0},
            )
            s1.save_to_file(os.path.join(comp_dir, "summary.shard_1.json"))

            reduce_comparison_summaries(os.path.join(tmpdir, "compare-results"))

            # Shard files should be removed
            assert not os.path.isfile(os.path.join(comp_dir, "summary.shard_0.json"))
            assert not os.path.isfile(os.path.join(comp_dir, "summary.shard_1.json"))

            # Unified summary.json should contain merged results
            unified_file = os.path.join(comp_dir, "summary.json")
            assert os.path.isfile(unified_file)
            unified = ComparisonSummary.load_from_file(unified_file)

            assert unified.tests_evaluated == ["SuiteA:Test0", "SuiteA:Test1", "SuiteA:Test2"]
            assert unified.tests_with_differences == {"SuiteA:Test0": 5.0, "SuiteA:Test1": 10.0, "SuiteA:Test2": 20.0}
            assert unified.tests_without_goldens == ["SuiteA:Missing1"]

    @patch("xemu_pgraph_ci_tools.models.DiffTask.generate_difference_image")
    def test_generate_missing_hw_diffs_from_tasks_file(self, mock_diff: MagicMock) -> None:
        mock_diff.return_value = (0, "0 pixels are different", "")
        with tempfile.TemporaryDirectory() as tmpdir:
            task1 = DiffTask(
                suite="SuiteA",
                test_case="Test1",
                source_image=os.path.join(tmpdir, "src1.png"),
                golden_image=os.path.join(tmpdir, "gold1.png"),
                output_diff_image=os.path.join(tmpdir, "diff1.png"),
                comparison_output_dir=os.path.join(tmpdir, "comp_out"),
                results_identifier="RunA",
                golden_identifier="Xbox_Hardware",
            )
            with open(task1.source_image, "w") as f:
                f.write("src")
            with open(task1.golden_image, "w") as f:
                f.write("gold")

            plan_file = os.path.join(tmpdir, "diff_tasks.json")
            with open(plan_file, "w", encoding="utf-8") as f:
                json.dump([task1.to_dict()], f)

            process_plan_tasks(
                tasks_file=plan_file,
                output_dir=os.path.join(tmpdir, "comp_out"),
                shard_index=0,
                shard_count=1,
            )

            shard_summary_file = os.path.join(tmpdir, "comp_out", "summary.shard_0.json")
            assert os.path.isfile(shard_summary_file)
            summary = ComparisonSummary.load_from_file(shard_summary_file)
            assert summary.tests_evaluated == ["SuiteA:Test1"]

    @patch("xemu_pgraph_ci_tools.models.DiffTask.generate_difference_image")
    def test_generate_tasks_with_stage_dir(self, mock_diff: MagicMock) -> None:
        def side_effect(_pdiff: str) -> tuple[int, str, str]:
            os.makedirs(os.path.dirname(task1.output_diff_image), exist_ok=True)
            with open(task1.output_diff_image, "w") as f:
                f.write("diff")
            return (0, "15 pixels are different", "")

        mock_diff.side_effect = side_effect

        with tempfile.TemporaryDirectory() as tmpdir:
            stage_dir = os.path.join(tmpdir, "diff-staging")
            task1 = DiffTask(
                suite="SuiteA",
                test_case="Test1",
                source_image=os.path.join(tmpdir, "src1.png"),
                golden_image=os.path.join(tmpdir, "gold1.png"),
                output_diff_image=os.path.join(tmpdir, "comp_out", "SuiteA", "Test1-diff.png"),
                comparison_output_dir=os.path.join(tmpdir, "comp_out"),
                results_identifier="RunA",
                golden_identifier="Xbox_Hardware",
            )
            with open(task1.source_image, "w") as f:
                f.write("src")
            with open(task1.golden_image, "w") as f:
                f.write("gold")

            plan_file = os.path.join(tmpdir, "diff_tasks.json")
            with open(plan_file, "w", encoding="utf-8") as f:
                json.dump([task1.to_dict()], f)

            process_plan_tasks(
                tasks_file=plan_file,
                output_dir=os.path.join(tmpdir, "comp_out"),
                shard_index=0,
                shard_count=1,
                stage_dir=stage_dir,
            )

            # Check that diff image and summary were staged in stage_dir
            staged_summary = os.path.join(stage_dir, "comp_out", "summary.shard_0.json")
            staged_diff = os.path.join(stage_dir, "comp_out", "SuiteA", "Test1-diff.png")
            assert os.path.isfile(staged_summary)
            assert os.path.isfile(staged_diff)

    @patch("xemu_pgraph_ci_tools.github.fetch_hw_goldens")
    @patch("xemu_pgraph_ci_tools.models.DiffTask.generate_difference_image")
    def test_generate_tasks_fetches_missing_goldens(self, mock_diff: MagicMock, mock_fetch: MagicMock) -> None:
        mock_diff.return_value = (0, "0 pixels are different", "")
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = os.path.join(tmpdir, "cache")
            golden_path = os.path.join(cache_path, "nxdk_pgraph_tests_golden_results", "results", "SuiteA", "Test1.png")

            def fetch_side_effect(_out_dir: str) -> None:
                os.makedirs(os.path.dirname(golden_path), exist_ok=True)
                with open(golden_path, "w") as f:
                    f.write("gold")

            mock_fetch.side_effect = fetch_side_effect

            task1 = DiffTask(
                suite="SuiteA",
                test_case="Test1",
                source_image=os.path.join(tmpdir, "src1.png"),
                golden_image=golden_path,
                output_diff_image=os.path.join(tmpdir, "diff1.png"),
                comparison_output_dir=os.path.join(tmpdir, "comp_out"),
                results_identifier="RunA",
                golden_identifier="Xbox_Hardware",
            )
            with open(task1.source_image, "w") as f:
                f.write("src")

            plan_file = os.path.join(tmpdir, "diff_tasks.json")
            with open(plan_file, "w", encoding="utf-8") as f:
                json.dump([task1.to_dict()], f)

            process_plan_tasks(
                tasks_file=plan_file,
                output_dir=os.path.join(tmpdir, "comp_out"),
                cache_path=cache_path,
                shard_index=0,
                shard_count=1,
            )

            mock_fetch.assert_called_once()
            shard_summary_file = os.path.join(tmpdir, "comp_out", "summary.shard_0.json")
            assert os.path.isfile(shard_summary_file)
            summary = ComparisonSummary.load_from_file(shard_summary_file)
            assert summary.tests_evaluated == ["SuiteA:Test1"]
            assert summary.tests_without_goldens == []


if __name__ == "__main__":
    unittest.main()
