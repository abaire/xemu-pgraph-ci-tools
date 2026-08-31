from __future__ import annotations

import os
import tempfile
import unittest

from xemu_pgraph_ci_tools.hw_diffs import (
    _comparison_path_to_source_path,
    find_result_dirs_without_hw_diffs,
)
from xemu_pgraph_ci_tools.xemu_diffs import ResultsConfiguration, _find_best_comparator


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
            comp_dir = os.path.join(
                output_root, "v1", "Linux", "4.6", "4.60", "Xbox__Xbox__DirectX__nv2a"
            )
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


if __name__ == "__main__":
    unittest.main()
