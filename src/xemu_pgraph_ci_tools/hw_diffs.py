# ruff: noqa: T201, PLR2004

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
import tempfile

from xemu_pgraph_ci_tools.comparator import (
    _ensure_cache_path,
    _fetch_hw_goldens,
    perform_comparison,
)
from xemu_pgraph_ci_tools.models import ComparisonSummary
from xemu_pgraph_ci_tools.schema import emit_json_schema

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
        logger.info(
            "  Output directory '%s' does not exist (no prior comparisons)", output_dir
        )
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
    source_paths = {
        os.path.join(results_dir, _comparison_path_to_source_path(path))
        for path in hw_comparison_paths
    }

    if source_paths:
        logger.info(
            "Mapped %d existing comparison(s) back to source paths:", len(source_paths)
        )
        for sp in sorted(source_paths):
            logger.info("  %s", sp)

    missing = result_paths - source_paths
    logger.info("%d result directory(ies) still need HW comparisons", len(missing))
    for m in sorted(missing):
        logger.info("  %s", m)

    return missing


def _discover_test_suites(result_dir: str) -> list[str]:
    try:
        suites = [
            entry.name
            for entry in os.scandir(result_dir)
            if entry.is_dir() and not entry.name.startswith(".")
        ]
    except OSError:
        logger.warning("Could not scan result directory: %s", result_dir)
        suites = []
    return sorted(suites)


def generate_missing_hw_diffs(
    results_dir: str,
    output_dir: str,
    compare_script: str | None = None,
    golden_dir: str | None = None,
    cache_path: str = "cache",
    perceptualdiff: str = "perceptualdiff",
    shard_index: int | None = None,
    shard_count: int | None = None,
) -> None:
    results_missing_comparisons = find_result_dirs_without_hw_diffs(
        results_dir, output_dir
    )

    if not results_missing_comparisons:
        logger.warning("No result directories need HW comparisons. Nothing to do.")
        return

    if not golden_dir:
        cache_path = _ensure_cache_path(cache_path)
        hw_golden_root = os.path.join(cache_path, "nxdk_pgraph_tests_golden_results")
        if not os.path.isdir(hw_golden_root):
            _fetch_hw_goldens(hw_golden_root)
        golden_dir = (
            os.path.join(hw_golden_root, "results")
            if os.path.isdir(os.path.join(hw_golden_root, "results"))
            else hw_golden_root
        )
    elif os.path.isdir(os.path.join(golden_dir, "results")):
        golden_dir = os.path.join(golden_dir, "results")

    flat_items: list[tuple[str, str]] = []
    for result_dir in sorted(results_missing_comparisons):
        suites = _discover_test_suites(result_dir)
        logger.info("Found %d test suite(s) in %s", len(suites), result_dir)
        flat_items.extend((result_dir, suite) for suite in suites)

    logger.info(
        "Total (result_dir, suite) pairs to process (before sharding): %d",
        len(flat_items),
    )
    if not flat_items:
        logger.warning("No test suites found. Nothing to do.")
        return

    if shard_index is not None and shard_count is not None:
        logger.info("Sharding: index=%d, count=%d", shard_index, shard_count)
        flat_items = [
            item for i, item in enumerate(flat_items) if i % shard_count == shard_index
        ]
        logger.info("This shard will process %d pair(s)", len(flat_items))
        if not flat_items:
            logger.warning("Shard %d has no work to process.", shard_index)
            return

    suites_by_result_dir: dict[str, list[str]] = {}
    for result_dir, suite in flat_items:
        suites_by_result_dir.setdefault(result_dir, []).append(suite)

    for result_dir, suites in sorted(suites_by_result_dir.items()):
        sorted_suites = sorted(suites)
        logger.info(
            "Running comparison for %s with %d suite(s): %s",
            result_dir,
            len(suites),
            ", ".join(sorted_suites),
        )

        if compare_script:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write("\n".join(sorted_suites))
                suites_file = f.name
            try:
                cmd = (
                    shlex.split(compare_script)
                    if isinstance(compare_script, str)
                    else list(compare_script)
                )
                cmd.extend(
                    [
                        result_dir,
                        "--against",
                        golden_dir,
                        "--output-dir",
                        output_dir,
                        "--perceptualdiff",
                        perceptualdiff,
                        "--verbose",
                        "--include-suites-file",
                        suites_file,
                    ]
                )
                subprocess.run(cmd, check=False)
            finally:
                os.unlink(suites_file)
        else:
            perform_comparison(
                results_path=result_dir,
                golden_path=golden_dir,
                output_dir=output_dir,
                perceptualdiff=perceptualdiff,
                diff_threshold=0.00001,
                use_lpips=False,
                include_suites=set(sorted_suites),
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-dir", default="results", help="Directory including test outputs"
    )
    parser.add_argument(
        "--output-dir", default="compare-results", help="Directory for diff results"
    )
    parser.add_argument("--golden-dir", help="Directory containing golden HW results")
    parser.add_argument(
        "--compare-script", default=None, help="Optional compare script"
    )
    parser.add_argument(
        "--perceptualdiff",
        default="perceptualdiff",
        help="Path to perceptualdiff binary",
    )
    parser.add_argument(
        "--shard-index", type=int, default=None, help="Shard index (0-based)"
    )
    parser.add_argument(
        "--shard-count", type=int, default=None, help="Total number of shards"
    )
    parser.add_argument(
        "--emit-schema",
        "--schema",
        action="store_true",
        help="Emit JSON Schema for summary.json output artifact and exit.",
    )

    args = parser.parse_args()

    if args.emit_schema:
        print(emit_json_schema(ComparisonSummary))
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

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
