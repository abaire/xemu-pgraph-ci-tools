# ruff: noqa: PLR2004

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from xemu_pgraph_ci_tools import github, util
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

    logger.info("Searching for result directories in '%s'", results_dir)
    if not os.path.isdir(results_dir):
        logger.warning("Results directory '%s' does not exist", results_dir)
        return ret

    for root, dirnames, filenames in os.walk(results_dir):
        if "results.json" not in filenames:
            continue

        logger.info("  Found result directory: %s", root)
        ret.add(root)
        dirnames.clear()

    logger.info("Found %d result directory(ies)", len(ret))
    return ret


def _find_hw_comparison_paths(output_dir: str) -> set[str]:
    ret: set[str] = set()

    logger.info("Searching for existing HW comparisons in '%s'", output_dir)
    if not os.path.isdir(output_dir):
        logger.info("  Output directory '%s' does not exist (no prior comparisons)", output_dir)
        return ret

    for root, dirnames, filenames in os.walk(output_dir):
        if "summary.json" not in filenames:
            continue

        if os.path.basename(root) != "Xbox__Xbox__DirectX__nv2a":
            continue
        logger.info("  Found existing comparison: %s", root)
        ret.add(root)
        dirnames.clear()

    logger.info("Found %d existing comparison(s)", len(ret))
    return ret


def _comparison_path_to_source_path(comparison_path: str, _output_dir: str = "compare-results") -> str:
    components = [c for c in comparison_path.replace("\\", "/").rstrip("/").split("/") if c]
    if "compare-results" in components:
        idx = len(components) - 1 - components[::-1].index("compare-results")
        subparts = components[idx + 1 : -1]
        return os.path.join(*subparts) if subparts else ""
    if "output" in components:
        idx = len(components) - 1 - components[::-1].index("output")
        subparts = components[idx + 1 : -1]
        return os.path.join(*subparts) if subparts else ""
    if len(components) >= 2:
        return os.path.join(*components[:-1])
    return ""


def find_result_dirs_without_hw_diffs(results_dir: str, output_dir: str) -> set[str]:
    result_paths = _find_results_paths(results_dir)
    hw_comparison_paths = _find_hw_comparison_paths(output_dir)
    source_paths = {
        os.path.join(results_dir, _comparison_path_to_source_path(path, output_dir)) for path in hw_comparison_paths
    }

    if source_paths:
        logger.info("Mapped %d existing comparison(s) back to source paths:", len(source_paths))
        for sp in sorted(source_paths):
            logger.info("  %s", sp)

    missing = result_paths - source_paths
    logger.info("%d result directory(ies) still need HW comparisons", len(missing))
    for m in sorted(missing):
        logger.info("  %s", m)

    return missing


def _discover_test_suites(result_dir: str) -> list[str]:
    try:
        suites = [entry.name for entry in os.scandir(result_dir) if entry.is_dir() and not entry.name.startswith(".")]
    except OSError:
        logger.warning("Could not scan result directory: %s", result_dir)
        suites = []
    return sorted(suites)


def identify_missing_hw_diffs(
    results_dir: str,
    output_dir: str,
    golden_dir: str | None = None,
    cache_path: str = "cache",
    include_suites: set[str] | None = None,
) -> list[DiffTask]:
    """Identifies all missing hardware diff tasks at the test-case level."""
    if not golden_dir:
        cache_path = util.ensure_cache_path(cache_path)
        hw_golden_root = os.path.join(cache_path, "nxdk_pgraph_tests_golden_results")
        if not os.path.isdir(hw_golden_root):
            github.fetch_hw_goldens(hw_golden_root)
        resolved_golden_dir = (
            os.path.join(hw_golden_root, "results")
            if os.path.isdir(os.path.join(hw_golden_root, "results"))
            else hw_golden_root
        )
    elif os.path.isdir(os.path.join(golden_dir, "results")):
        resolved_golden_dir = os.path.join(golden_dir, "results")
    else:
        resolved_golden_dir = golden_dir

    result_paths = _find_results_paths(results_dir)
    all_tasks: list[DiffTask] = []

    for run_dir in sorted(result_paths):
        results_info = ResultsInfo.parse(run_dir, include_suites)
        comparison_output_dir = os.path.join(
            output_dir,
            results_info.output_subdirectory,
            "Xbox__Xbox__DirectX__nv2a",
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

        def get_golden_path(suite: str, test_case: str, _src: str, g_dir: str = resolved_golden_dir) -> str:
            return os.path.join(g_dir, suite, f"{test_case}.png")

        run_tasks = discover_diff_tasks(
            run_dir,
            get_output_path_fn=get_output_path,
            get_golden_path_fn=get_golden_path,
            include_suites=include_suites,
            results_path=run_dir,
            results_identifier=results_info.run_identifier,
            golden_identifier="Xbox_Hardware",
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

    logger.info("Identified %d missing HW diff task(s)", len(all_tasks))
    return all_tasks


def _process_hw_diffs(
    tasks: list[DiffTask],
    output_dir: str,
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
    stage_dir: str | None = None,
) -> None:
    if not tasks:
        logger.warning("No missing HW diff tasks found. Nothing to do.")
        if stage_dir:
            os.makedirs(stage_dir, exist_ok=True)
            with open(os.path.join(stage_dir, "KEEP_ARTIFACT"), "w", encoding="utf-8") as f:
                f.write("")
        return

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
            return

    shard_id = f"shard_{shard_index}" if shard_index is not None else None
    process_diff_tasks(
        tasks,
        output_dir=output_dir,
        perceptualdiff=perceptualdiff,
        shard_id=shard_id,
        staging_dir=stage_dir,
    )


def generate_missing_hw_diffs(
    results_dir: str,
    output_dir: str,
    compare_script: str | None = None,  # noqa: ARG001
    golden_dir: str | None = None,
    cache_path: str = "cache",
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
    stage_dir: str | None = None,
) -> None:
    tasks = identify_missing_hw_diffs(
        results_dir=results_dir,
        output_dir=output_dir,
        golden_dir=golden_dir,
        cache_path=cache_path,
    )
    _process_hw_diffs(
        tasks,
        output_dir=output_dir,
        perceptualdiff=perceptualdiff,
        shard_index=shard_index,
        shard_count=shard_count,
        stage_dir=stage_dir,
    )


def process_plan_tasks(
    tasks_file: str,
    output_dir: str,
    golden_dir: str | None = None,
    cache_path: str = "cache",
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
    stage_dir: str | None = None,
) -> None:
    """Generates diffs for entries in the given plan task file."""
    logger.info("Loading tasks from plan file: %s", tasks_file)
    with open(tasks_file, encoding="utf-8") as f:
        task_dicts = json.load(f)
    tasks = [DiffTask.from_dict(d) for d in task_dicts]

    if not golden_dir:
        cache_path = util.ensure_cache_path(cache_path)
        hw_golden_root = os.path.join(cache_path, "nxdk_pgraph_tests_golden_results")
        if not os.path.isdir(hw_golden_root) and any(
            "nxdk_pgraph_tests_golden_results" in t.golden_image for t in tasks
        ):
            github.fetch_hw_goldens(hw_golden_root)

    _process_hw_diffs(
        tasks,
        output_dir=output_dir,
        perceptualdiff=perceptualdiff,
        shard_index=shard_index,
        shard_count=shard_count,
        stage_dir=stage_dir,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default="results", help="Directory including test outputs")
    parser.add_argument("--output-dir", default="compare-results", help="Directory for diff results")
    parser.add_argument("--golden-dir", help="Directory containing golden HW results")
    parser.add_argument("--cache-path", default="cache", help="Path to cache directory for goldens")
    parser.add_argument("--compare-script", default=None, help="Optional compare script")
    parser.add_argument(
        "--perceptualdiff",
        default="perceptualdiff",
        help="Path to perceptualdiff binary",
    )
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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if args.reduce_summaries:
        reduce_comparison_summaries(args.output_dir)
        return 0

    if args.output_plan_file:
        tasks = identify_missing_hw_diffs(
            args.results_dir,
            args.output_dir,
            golden_dir=args.golden_dir,
            cache_path=args.cache_path,
        )
        task_dicts = [t.to_dict() for t in tasks]
        os.makedirs(os.path.dirname(os.path.abspath(args.output_plan_file)), exist_ok=True)
        with open(args.output_plan_file, "w", encoding="utf-8") as f:
            json.dump(task_dicts, f, indent=2)
        logger.info("Saved %d diff tasks to %s", len(tasks), args.output_plan_file)
        return 0

    if (args.shard_index is None) != (args.shard_count is None):
        parser.error("--shard-index and --shard-count must be used together")

    if args.tasks_file:
        process_plan_tasks(
            tasks_file=args.tasks_file,
            output_dir=args.output_dir,
            golden_dir=args.golden_dir,
            cache_path=args.cache_path,
            perceptualdiff=args.perceptualdiff,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            stage_dir=args.stage_dir,
        )
        return 0

    generate_missing_hw_diffs(
        args.results_dir,
        args.output_dir,
        compare_script=args.compare_script,
        golden_dir=args.golden_dir,
        cache_path=args.cache_path,
        perceptualdiff=args.perceptualdiff,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        stage_dir=args.stage_dir,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
