# ruff: noqa: PLR2004

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass

from xemu_pgraph_ci_tools.comparator import (
    discover_diff_tasks,
    get_shard_slice,
    process_diff_tasks,
    reduce_comparison_summaries,
)
from xemu_pgraph_ci_tools.models import (
    ComparisonSummary,
    DiffTask,
    ResultsInfo,
)

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


def identify_missing_xemu_diffs(
    results_dir: str,
    golden_dir: str,
    output_dir: str = "compare-results",
    include_suites: set[str] | None = None,
) -> tuple[dict[str, str], list[DiffTask]]:
    """Identifies missing diff tasks between test runs and baseline xemu runs."""
    result_paths = _find_results_paths(results_dir)
    golden_configurations = _build_configurations(golden_dir)

    if not golden_configurations:
        msg = f"No baseline results found in {golden_dir}"
        raise ValueError(msg)

    registry: dict[str, str] = {}
    all_tasks: list[DiffTask] = []

    for path in sorted(result_paths):
        results_config = ResultsConfiguration(path)
        best_match = _find_best_comparator(results_config, golden_configurations)
        if not best_match:
            continue
        golden_path, _ = best_match
        registry[path] = golden_path

        results_info = ResultsInfo.parse(path, include_suites)
        golden_info = ResultsInfo.parse(golden_path, include_suites)

        comparison_output_dir = os.path.join(
            output_dir,
            results_info.output_subdirectory,
            golden_info.run_identifier_subdirectory,
        )

        existing_summary = None
        summary_path = os.path.join(comparison_output_dir, "summary.json")
        if os.path.isfile(summary_path):
            try:
                existing_summary = ComparisonSummary.load_from_file(summary_path)
            except (json.JSONDecodeError, OSError, TypeError, KeyError):
                logger.warning("Could not load summary from %s", summary_path)

        def get_output_path(suite: str, test_case: str, _src: str, c_dir: str = comparison_output_dir) -> str:
            return os.path.join(c_dir, suite, f"{test_case}-diff.png")

        def get_golden_path(suite: str, test_case: str, _src: str, g_dir: str = golden_path) -> str:
            return os.path.join(g_dir, suite, f"{test_case}.png")

        run_tasks = discover_diff_tasks(
            path,
            get_output_path_fn=get_output_path,
            get_golden_path_fn=get_golden_path,
            include_suites=include_suites,
            results_path=path,
            results_identifier=results_info.run_identifier,
            golden_identifier=golden_info.run_identifier,
            comparison_output_dir=comparison_output_dir,
            skip_existing=False,
        )

        for task in run_tasks:
            fq_name = task.fully_qualified_test_name
            if existing_summary:
                if existing_summary.tests_evaluated:
                    if fq_name in existing_summary.tests_evaluated:
                        continue
                else:
                    # Legacy summary without explicit tests_evaluated list:
                    if fq_name in existing_summary.tests_with_differences and os.path.isfile(task.output_diff_image):
                        continue
                    if fq_name in existing_summary.tests_without_goldens and not os.path.isfile(task.golden_image):
                        continue
                    if (
                        fq_name not in existing_summary.tests_with_differences
                        and fq_name not in existing_summary.tests_without_goldens
                        and os.path.isfile(task.golden_image)
                    ):
                        continue

            if os.path.isfile(task.output_diff_image):
                continue

            all_tasks.append(task)

    logger.info("Identified %d missing Xemu diff task(s) across %d run(s)", len(all_tasks), len(registry))
    return registry, all_tasks


def _process_xemu_diffs(
    registry: dict[str, str],
    tasks: list[DiffTask],
    golden_dir: str | None,
    output_dir: str,
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
    stage_dir: str | None = None,
) -> dict[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    if registry:
        comparisons_path = os.path.join(output_dir, "comparisons.json")
        with open(comparisons_path, "w", encoding="utf-8") as outfile:
            json.dump(registry, outfile, indent=2)

    if golden_dir:
        known_issues_file = os.path.join(golden_dir, "results", "known_issues.json")
        if not os.path.isfile(known_issues_file):
            known_issues_file = os.path.join(golden_dir, "known_issues.json")
        if os.path.isfile(known_issues_file):
            shutil.copy(known_issues_file, os.path.join(output_dir, "known_issues.json"))

    if not tasks:
        logger.warning("No missing Xemu diff tasks found. Nothing to do.")
        if stage_dir:
            os.makedirs(stage_dir, exist_ok=True)
            with open(os.path.join(stage_dir, "KEEP_ARTIFACT"), "w", encoding="utf-8") as f:
                f.write("")
        return registry

    if shard_index is not None and shard_count is not None:
        logger.info("Sharding: index=%d, count=%d", shard_index, shard_count)
        tasks = get_shard_slice(tasks, shard_index, shard_count)
        logger.info("This shard will process %d task(s)", len(tasks))
        if not tasks:
            logger.warning("Shard %d has no work to process.", shard_index)
            if stage_dir:
                os.makedirs(stage_dir, exist_ok=True)
                with open(os.path.join(stage_dir, "KEEP_ARTIFACT"), "w", encoding="utf-8") as f:
                    f.write("")
            return registry

    shard_id = f"shard_{shard_index}" if shard_index is not None else None
    process_diff_tasks(
        tasks,
        output_dir=output_dir,
        perceptualdiff=perceptualdiff,
        shard_id=shard_id,
        staging_dir=stage_dir,
    )

    return registry


def generate_diffs(
    results_dir: str,
    golden_dir: str,
    compare_script: str | None = None,  # noqa: ARG001
    cache_dir: str = "cache",  # noqa: ARG001
    output_dir: str = "compare-results",
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
    stage_dir: str | None = None,
) -> dict[str, str]:
    registry, tasks = identify_missing_xemu_diffs(
        results_dir=results_dir,
        golden_dir=golden_dir,
        output_dir=output_dir,
    )
    return _process_xemu_diffs(
        registry=registry,
        tasks=tasks,
        golden_dir=golden_dir,
        output_dir=output_dir,
        perceptualdiff=perceptualdiff,
        shard_index=shard_index,
        shard_count=shard_count,
        stage_dir=stage_dir,
    )


def generate_tasks(
    tasks_file: str,
    output_dir: str = "compare-results",
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
    stage_dir: str | None = None,
) -> dict[str, str]:
    logger.info("Loading tasks from plan file: %s", tasks_file)
    with open(tasks_file, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "tasks" in data:
        registry = data.get("registry", {})
        tasks = [DiffTask.from_dict(d) for d in data["tasks"]]
    elif isinstance(data, list):
        registry = {}
        tasks = [DiffTask.from_dict(d) for d in data]
    else:
        registry = {}
        tasks = []

    return _process_xemu_diffs(
        registry=registry,
        tasks=tasks,
        golden_dir=None,
        output_dir=output_dir,
        perceptualdiff=perceptualdiff,
        shard_index=shard_index,
        shard_count=shard_count,
        stage_dir=stage_dir,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default="results")
    parser.add_argument("--output-dir", default="compare-results")
    parser.add_argument("--compare-script", default=None)
    parser.add_argument("--baseline-dir", required=False, help="Path to baseline directory")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--perceptualdiff", default="perceptualdiff")
    parser.add_argument("--shard-index", type=int, default=None, help="Shard index (0-based)")
    parser.add_argument("--shard-count", type=int, default=None, help="Total number of shards")
    parser.add_argument("--stage-dir", default=None, help="Directory to stage created diff artifacts into")
    parser.add_argument("--tasks-file", default=None, help="Path to pre-computed plan file of DiffTasks")
    parser.add_argument("--output-plan-file", default=None, help="Path to save pre-computed plan file of DiffTasks")
    parser.add_argument(
        "--reduce-summaries",
        action="store_true",
        help="Merge all partial summary.*.json files into final summary.json in output-dir",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.reduce_summaries:
        reduce_comparison_summaries(args.output_dir)
        return 0

    if args.tasks_file:
        if (args.shard_index is None) != (args.shard_count is None):
            parser.error("--shard-index and --shard-count must be used together")

        generate_tasks(
            tasks_file=args.tasks_file,
            output_dir=args.output_dir,
            perceptualdiff=args.perceptualdiff,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            stage_dir=args.stage_dir,
        )
        return 0

    if not args.baseline_dir:
        parser.error("--baseline-dir is required unless --reduce-summaries or --tasks-file is specified")

    golden_dir = os.path.abspath(os.path.expanduser(args.baseline_dir))
    if not os.path.isdir(golden_dir):
        logger.error("Baseline directory %s not found.", golden_dir)
        return 1

    if args.output_plan_file:
        registry, tasks = identify_missing_xemu_diffs(
            args.results_dir,
            golden_dir,
            output_dir=args.output_dir,
        )
        task_dicts = [t.to_dict() for t in tasks]
        os.makedirs(os.path.dirname(os.path.abspath(args.output_plan_file)), exist_ok=True)
        with open(args.output_plan_file, "w", encoding="utf-8") as f:
            json.dump({"registry": registry, "tasks": task_dicts}, f, indent=2)
        logger.info("Saved %d diff tasks to %s", len(tasks), args.output_plan_file)
        return 0

    if (args.shard_index is None) != (args.shard_count is None):
        parser.error("--shard-index and --shard-count must be used together")

    generate_diffs(
        args.results_dir,
        golden_dir,
        compare_script=args.compare_script,
        cache_dir=args.cache_dir,
        output_dir=args.output_dir,
        perceptualdiff=args.perceptualdiff,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        stage_dir=args.stage_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
