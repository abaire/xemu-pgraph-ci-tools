import json
import os
import tempfile
import unittest
from unittest.mock import patch

from xemu_pgraph_ci_tools.comparator import reduce_comparison_summaries
from xemu_pgraph_ci_tools.hw_diffs import (
    _comparison_path_to_source_path,
    find_result_dirs_without_hw_diffs,
)
from xemu_pgraph_ci_tools.models import DiffTask
from xemu_pgraph_ci_tools.xemu_diffs import (
    ResultsConfiguration,
    _find_best_comparator,
    generate_diffs,
    generate_tasks,
    identify_missing_xemu_diffs,
)


class TestDiffs(unittest.TestCase):
    def test_comparison_path_to_source_path(self):
        path = "/output/v1/Linux_x86_64/4.6_Core/4.60/Xbox__Xbox__DirectX__nv2a"
        src = _comparison_path_to_source_path(path)
        assert src == os.path.join("v1", "Linux_x86_64", "4.6_Core", "4.60")

    def test_find_result_dirs_without_hw_diffs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res_root = os.path.join(tmpdir, "results")
            run1 = os.path.join(res_root, "v1", "Linux", "4.6", "4.60")
            os.makedirs(run1)
            with open(os.path.join(run1, "results.json"), "w") as f:
                f.write("{}")

            output_root = os.path.join(tmpdir, "output")
            missing = find_result_dirs_without_hw_diffs(res_root, output_root)
            assert len(missing) == 1
            assert run1 in missing

            # Now simulate existing comparison
            comp_dir = os.path.join(output_root, "v1", "Linux", "4.6", "4.60", "Xbox__Xbox__DirectX__nv2a")
            os.makedirs(comp_dir)
            with open(os.path.join(comp_dir, "summary.json"), "w") as f:
                f.write("{}")

            missing_after = find_result_dirs_without_hw_diffs(res_root, output_root)
            assert len(missing_after) == 0

    def test_xemu_diffs_configuration_scoring(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run1_path = os.path.join(tmpdir, "Linux_x86_64", "4.6_Core", "4.60")
            os.makedirs(run1_path)
            with open(os.path.join(run1_path, "machine_info.txt"), "w") as f:
                f.write("OS_Version: Linux 6.8\n")
                f.write("GL_VENDOR: NVIDIA\n")
                f.write("GL_VERSION: 4.6.0 NVIDIA 550.54\n")
                f.write("GL_SHADING_LANGUAGE_VERSION: 4.60 NVIDIA\n")

            config1 = ResultsConfiguration(run1_path)
            assert config1.renderer == "OpenGL"
            assert config1.gl_vendor == "NVIDIA"

            golden1_path = os.path.join(tmpdir, "golden_Linux", "4.6_Core", "4.60")
            golden2_path = os.path.join(tmpdir, "golden_Darwin", "4.1_Core", "4.10")
            os.makedirs(golden1_path)
            os.makedirs(golden2_path)

            golden_configs = {
                golden1_path: ResultsConfiguration(golden1_path),
                golden2_path: ResultsConfiguration(golden2_path),
            }

            best = _find_best_comparator(config1, golden_configs)
            assert best is not None
            assert best[0] == golden1_path

    def test_identify_missing_xemu_diffs_and_sharding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res_dir = os.path.join(tmpdir, "results", "0.8.0", "Linux_x86_64", "4.6", "4.60")
            os.makedirs(os.path.join(res_dir, "Suite1"))
            with open(os.path.join(res_dir, "results.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(res_dir, "machine_info.txt"), "w") as f:
                f.write("GL_VENDOR: NVIDIA\nGL_VERSION: 4.6.0\nGL_SHADING_LANGUAGE_VERSION: 4.60\n")
            with open(os.path.join(res_dir, "Suite1", "test1.png"), "w") as f:
                f.write("img1")
            with open(os.path.join(res_dir, "Suite1", "test2.png"), "w") as f:
                f.write("img2")

            gold_dir = os.path.join(tmpdir, "baseline", "0.7.99", "Linux_x86_64", "4.6", "4.60")
            os.makedirs(os.path.join(gold_dir, "Suite1"))
            with open(os.path.join(gold_dir, "results.json"), "w") as f:
                f.write("{}")
            with open(os.path.join(gold_dir, "machine_info.txt"), "w") as f:
                f.write("GL_VENDOR: NVIDIA\nGL_VERSION: 4.6.0\nGL_SHADING_LANGUAGE_VERSION: 4.60\n")
            with open(os.path.join(gold_dir, "Suite1", "test1.png"), "w") as f:
                f.write("gold1")
            with open(os.path.join(gold_dir, "Suite1", "test2.png"), "w") as f:
                f.write("gold2")

            out_dir = os.path.join(tmpdir, "compare-results")

            # 1. Identify tasks
            registry, tasks = identify_missing_xemu_diffs(
                os.path.join(tmpdir, "results"),
                os.path.join(tmpdir, "baseline"),
                output_dir=out_dir,
            )
            assert len(registry) == 1
            assert len(tasks) == 2

            # 2. Run sharded diff generation
            with patch("xemu_pgraph_ci_tools.models.DiffTask.generate_difference_image") as mock_diff:
                mock_diff.return_value = (0, "5 pixels are different", "")
                generate_diffs(
                    os.path.join(tmpdir, "results"),
                    os.path.join(tmpdir, "baseline"),
                    output_dir=out_dir,
                    shard_index=0,
                    shard_count=2,
                )
                generate_diffs(
                    os.path.join(tmpdir, "results"),
                    os.path.join(tmpdir, "baseline"),
                    output_dir=out_dir,
                    shard_index=1,
                    shard_count=2,
                )

            # 3. Reduce summaries
            reduce_comparison_summaries(out_dir)

            comp_dir = os.path.join(
                out_dir,
                "0.8.0",
                "Linux_x86_64",
                "4.6",
                "4.60",
                "0.7.99__Linux_x86_64__4.6__4.60",
            )
            unified_summary_file = os.path.join(comp_dir, "summary.json")
            assert os.path.isfile(unified_summary_file)
            assert not os.path.isfile(os.path.join(comp_dir, "summary.shard_0.json"))
            assert not os.path.isfile(os.path.join(comp_dir, "summary.shard_1.json"))

    def test_generate_tasks_from_plan_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            task1 = DiffTask(
                suite="SuiteA",
                test_case="Test1",
                source_image=os.path.join(tmpdir, "src1.png"),
                golden_image=os.path.join(tmpdir, "gold1.png"),
                output_diff_image=os.path.join(tmpdir, "diff1.png"),
                comparison_output_dir=os.path.join(tmpdir, "comp_out"),
                results_identifier="RunA",
                golden_identifier="RunGolden",
            )
            with open(task1.source_image, "w") as f:
                f.write("src")
            with open(task1.golden_image, "w") as f:
                f.write("gold")

            plan_file = os.path.join(tmpdir, "diff_tasks.json")
            with open(plan_file, "w", encoding="utf-8") as f:
                json.dump({"registry": {"RunA": "RunGolden"}, "tasks": [task1.to_dict()]}, f)

            with patch("xemu_pgraph_ci_tools.models.DiffTask.generate_difference_image") as mock_diff:
                mock_diff.return_value = (0, "0 pixels are different", "")
                registry = generate_tasks(
                    tasks_file=plan_file,
                    output_dir=os.path.join(tmpdir, "comp_out"),
                    shard_index=0,
                    shard_count=1,
                )

            assert registry == {"RunA": "RunGolden"}
            shard_summary_file = os.path.join(tmpdir, "comp_out", "summary.shard_0.json")
            assert os.path.isfile(shard_summary_file)


if __name__ == "__main__":
    unittest.main()
