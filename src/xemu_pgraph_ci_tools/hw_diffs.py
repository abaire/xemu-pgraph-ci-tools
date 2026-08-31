# ruff: noqa: PLR2004

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from xemu_pgraph_ci_tools.comparator import (
    _ensure_cache_path,
    _fetch_hw_goldens,
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


def _comparison_path_to_source_path(comparison_path: str) -> str:
    components = comparison_path.replace("\\", "/").split("/")
    if len(components) >= 5:
        xemu = components[-5]
        platform = components[-4]
        gl = components[-3]
        glsl = components[-2]
        return os.path.join(xemu, platform, gl, glsl)
    return ""


def find_result_dirs_without_hw_diffs(results_dir: str, output_dir: str) -> set[str]:
    result_paths = _find_results_paths(results_dir)
    hw_comparison_paths = _find_hw_comparison_paths(output_dir)
    source_paths = {os.path.join(results_dir, _comparison_path_to_source_path(path)) for path in hw_comparison_paths}

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
        cache_path = _ensure_cache_path(cache_path)
        hw_golden_root = os.path.join(cache_path, "nxdk_pgraph_tests_golden_results")
        if not os.path.isdir(hw_golden_root):
            _fetch_hw_goldens(hw_golden_root)
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
                if fq_name in existing_summary.tests_evaluated:
                    continue
                if fq_name in existing_summary.tests_with_differences and os.path.isfile(task.output_diff_image):
                    continue
                if fq_name in existing_summary.tests_without_goldens and not os.path.isfile(task.golden_image):
                    continue

            if os.path.isfile(task.output_diff_image):
                continue

            all_tasks.append(task)

    logger.info("Identified %d missing HW diff task(s)", len(all_tasks))
    return all_tasks


def generate_missing_hw_diffs(
    results_dir: str,
    output_dir: str,
    compare_script: str | None = None,  # noqa: ARG001
    golden_dir: str | None = None,
    cache_path: str = "cache",
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> None:
    tasks = identify_missing_hw_diffs(
        results_dir=results_dir,
        output_dir=output_dir,
        golden_dir=golden_dir,
        cache_path=cache_path,
    )

    if not tasks:
        logger.warning("No missing HW diff tasks found. Nothing to do.")
        return

    if shard_index is not None and shard_count is not None:
        logger.info("Sharding: index=%d, count=%d", shard_index, shard_count)
        tasks = get_shard_slice(tasks, shard_index, shard_count)
        logger.info("This shard will process %d task(s)", len(tasks))
        if not tasks:
            logger.warning("Shard %d has no work to process.", shard_index)
            return

    shard_id = f"shard_{shard_index}" if shard_index is not None else None
    process_diff_tasks(
        tasks,
        output_dir=output_dir,
        perceptualdiff=perceptualdiff,
        shard_id=shard_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default="results", help="Directory including test outputs")
    parser.add_argument("--output-dir", default="compare-results", help="Directory for diff results")
    parser.add_argument("--golden-dir", help="Directory containing golden HW results")
    parser.add_argument("--compare-script", default=None, help="Optional compare script")
    parser.add_argument(
        "--perceptualdiff",
        default="perceptualdiff",
        help="Path to perceptualdiff binary",
    )
    parser.add_argument("--shard-index", type=int, default=None, help="Shard index (0-based)")
    parser.add_argument("--shard-count", type=int, default=None, help="Total number of shards")
    parser.add_argument(
        "--identify-only",
        action="store_true",
        help="Only identify missing diff tasks and output JSON summary",
    )
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

    if args.identify_only:
        tasks = identify_missing_hw_diffs(
            args.results_dir,
            args.output_dir,
            golden_dir=args.golden_dir,
        )
        [t.to_dict() for t in tasks]
        return 0

    if (args.shard_index is None) != (args.shard_count is None):
        parser.error("--shard-index and --shard-count must be used together")

    generate_missing_hw_diffs(
        args.results_dir,
        args.output_dir,
        compare_script=args.compare_script,
        golden_dir=args.golden_dir,
        perceptualdiff=args.perceptualdiff,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
