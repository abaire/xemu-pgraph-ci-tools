# ruff: noqa: PLR2004

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

from xemu_pgraph_ci_tools.comparator import perform_comparison

logger = logging.getLogger(__name__)


def _find_results_paths(results_dir: str) -> set[str]:
    ret: set[str] = set()
    for root, dirnames, filenames in os.walk(results_dir):
        if not dirnames:
            continue
        if "results.json" not in filenames:
            continue
        ret.add(root)
        dirnames.clear()
    cwd = os.getcwd()
    return {os.path.relpath(absolute_path, cwd) for absolute_path in ret}


@dataclass
class ResultsConfiguration:
    cpu: str = "any"
    os_version: str = "any"
    gl_vendor: str = "any"
    gl_renderer: str = "any"
    gl_version: str = "any"
    glsl_version: str = "any"
    renderer: str = "OpenGL"
    sanitized_glsl: str = "any"
    sanitized_gl: str = "any"
    sanitized_os_arch: str = "any"

    def __init__(self, results_path: str):
        machine_info_path = os.path.join(results_path, "machine_info.txt")
        if os.path.isfile(machine_info_path):
            with open(machine_info_path, encoding="utf-8", errors="replace") as machine_info:
                for full_line in machine_info:
                    line = full_line.strip()
                    if line.startswith("CPU:"):
                        self.cpu = line.split(":", 1)[1].strip()
                    elif line.startswith("OS_Version:"):
                        self.os_version = line.split(":", 1)[1].strip()
                    elif line.startswith("GL_VENDOR:"):
                        self.gl_vendor = line.split(":", 1)[1].strip()
                    elif line.startswith("GL_RENDERER:"):
                        self.gl_renderer = line.split(":", 1)[1].strip()
                    elif line.startswith("GL_VERSION:"):
                        self.gl_version = line.split(":", 1)[1].strip()
                    elif line.startswith("GL_SHADING_LANGUAGE_VERSION:"):
                        self.glsl_version = line.split(":", 1)[1].strip()
                    elif line.startswith("- VK_"):
                        self.renderer = "Vulkan"

        path_components = results_path.replace("\\", "/").split("/")
        if len(path_components) >= 3:
            self.sanitized_glsl = path_components[-1]
            self.sanitized_gl = path_components[-2]
            self.sanitized_os_arch = path_components[-3]

    def score(self, other: ResultsConfiguration) -> int:
        def prefix_match(a: str, b: str, value: int, perfect_bonus: int) -> int:
            ret = 0
            for idx in range(min(len(a), len(b))):
                if a[idx] != b[idx]:
                    return ret
                ret += value
            return ret + perfect_bonus

        ret = 0
        if self.renderer == other.renderer:
            ret += 500000
        ret += prefix_match(self.sanitized_os_arch, other.sanitized_os_arch, 100, 100000)
        ret += prefix_match(self.glsl_version, other.glsl_version, 50, 500)
        ret += prefix_match(self.gl_version, other.gl_version, 50, 500)
        return ret


def _find_best_comparator(
    results: ResultsConfiguration, golden_paths: dict[str, ResultsConfiguration]
) -> tuple[str, ResultsConfiguration] | None:
    best_config = None
    best_score = -1

    for path, config in golden_paths.items():
        score = results.score(config)
        if score > best_score:
            best_config = (path, config)
            best_score = score

    return best_config


def _build_configurations(base_dir: str) -> dict[str, ResultsConfiguration]:
    paths = _find_results_paths(base_dir)
    return {path: ResultsConfiguration(path) for path in paths}


def generate_diffs(
    results_dir: str,
    golden_dir: str,
    compare_script: str | None = None,
    cache_dir: str = "cache",
    output_dir: str = "compare-results",
    perceptualdiff: str = "perceptualdiff",
) -> dict[str, str]:
    result_paths = _find_results_paths(results_dir)
    golden_configurations = _build_configurations(golden_dir)

    if not golden_configurations:
        msg = f"No baseline results found in {golden_dir}"
        raise ValueError(msg)

    registry = {}
    for path in result_paths:
        results_config = ResultsConfiguration(path)
        best_match = _find_best_comparator(results_config, golden_configurations)
        if not best_match:
            continue
        golden_path, _ = best_match
        registry[path] = golden_path

        if compare_script:
            cmd = shlex.split(compare_script) if isinstance(compare_script, str) else list(compare_script)
            cmd.extend(
                [
                    path,
                    "--against",
                    golden_path,
                    "--output-dir",
                    output_dir,
                    "--cache-path",
                    cache_dir,
                    "--perceptualdiff",
                    perceptualdiff,
                    "--verbose",
                ]
            )
            subprocess.run(cmd, check=False)
        else:
            perform_comparison(
                results_path=path,
                golden_path=golden_path,
                output_dir=output_dir,
                perceptualdiff=perceptualdiff,
                diff_threshold=0.00001,
                use_lpips=False,
            )

    os.makedirs(output_dir, exist_ok=True)
    with open(os.path.join(output_dir, "comparisons.json"), "w", encoding="utf-8") as outfile:
        json.dump(registry, outfile, indent=2)

    known_issues_file = os.path.join(golden_dir, "results", "known_issues.json")
    if os.path.isfile(known_issues_file):
        shutil.copy(known_issues_file, os.path.join(output_dir, "known_issues.json"))

    return registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--output-dir", default="compare-results")
    parser.add_argument("--compare-script", default=None)
    parser.add_argument("--baseline-dir", required=True, help="Path to baseline directory")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--perceptualdiff", default="perceptualdiff")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    golden_dir = os.path.abspath(os.path.expanduser(args.baseline_dir))
    if not os.path.isdir(golden_dir):
        logger.error("Baseline directory %s not found.", golden_dir)
        return 1

    generate_diffs(
        args.results_dir,
        golden_dir,
        compare_script=args.compare_script,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        perceptualdiff=args.perceptualdiff,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
