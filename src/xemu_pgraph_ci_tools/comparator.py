# ruff: noqa: T201, PLC0415

from __future__ import annotations

import argparse
import glob
import logging
import os
import shutil
import sys
from collections import defaultdict

from xemu_pgraph_ci_tools.models import (
    PERCEPTUALDIFF_DIFFERENCE_RE,
    ComparisonSummary,
    Difference,
    ResultsInfo,
)

logger = logging.getLogger(__name__)

_HW_GOLDEN_GIT_URL = "https://github.com/abaire/nxdk_pgraph_tests_golden_results.git"


def _ensure_path(path: str) -> str:
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    return path


def _ensure_cache_path(cache_path: str) -> str:
    if not cache_path:
        msg = "cache_path may not be empty"
        raise ValueError(msg)
    return _ensure_path(cache_path)


def _fetch_hw_goldens(output_dir: str) -> None:
    from git import Repo

    logger.info("Cloning from %s", _HW_GOLDEN_GIT_URL)
    Repo.clone_from(_HW_GOLDEN_GIT_URL, output_dir, depth=1)


def _compare_lpips(results_info: ResultsInfo, golden_info: ResultsInfo) -> tuple[set[str], set[str], list[Difference]]:
    import lpips

    loss_fn = lpips.LPIPS(net="alex")

    results_tests = results_info.get_flattened_tests()
    golden_tests = golden_info.get_flattened_tests()

    only_results = results_tests - golden_tests
    only_goldens = golden_tests - results_tests

    differences: list[Difference] = []

    logger.info("Comparing image files (this may take some time)...")
    for test_suite in sorted(results_info.test_suites.keys()):
        print(test_suite)
        test_cases = results_info.test_suites[test_suite]
        golden_suite = golden_info.test_suites.get(test_suite, {})
        for test_case, artifact in test_cases.items():
            print(".", end="", flush=True)
            golden_artifact = golden_suite.get(test_case)
            if not golden_artifact:
                continue

            # Load images
            artifact_image = lpips.im2tensor(lpips.load_image(artifact))
            golden_image = lpips.im2tensor(lpips.load_image(golden_artifact))

            distance = loss_fn(artifact_image, golden_image)
            distance_value = float(distance.item())
            logger.debug(
                "LPIPS distance between %s and %s = %G",
                artifact,
                golden_artifact,
                distance_value,
            )

            differences.append(Difference(test_suite, test_case, artifact, golden_artifact, distance_value))
        print()

    return only_results, only_goldens, differences


def _compare_perceptualdiff(
    results_info: ResultsInfo,
    golden_info: ResultsInfo,
    perceptualdiff: str,
    comparison_output_directory: str,
) -> tuple[set[str], set[str], list[Difference]]:
    results_tests = results_info.get_flattened_tests()
    golden_tests = golden_info.get_flattened_tests()

    only_results = results_tests - golden_tests
    only_goldens = golden_tests - results_tests

    differences: list[Difference] = []
    logger.info("Comparing image files (this may take some time)...")
    for test_suite in sorted(results_info.test_suites.keys()):
        print(test_suite)
        test_cases = results_info.test_suites[test_suite]
        golden_suite = golden_info.test_suites.get(test_suite, {})
        for test_case, artifact in test_cases.items():
            print(".", end="", flush=True)
            golden_artifact = golden_suite.get(test_case)
            if not golden_artifact:
                continue

            diff = Difference(test_suite, test_case, artifact, golden_artifact, -1)
            result, stdout, _stderr = diff.generate_difference_image(perceptualdiff, comparison_output_directory)
            if not result:
                continue

            diff_score = -1.0
            for line in stdout.split("\n"):
                match = PERCEPTUALDIFF_DIFFERENCE_RE.match(line)
                if match:
                    diff_score = float(match.group(1))
            diff = Difference(test_suite, test_case, artifact, golden_artifact, diff_score)
            differences.append(diff)
        print()

    return only_results, only_goldens, differences


def perform_comparison(
    results_path: str,
    golden_path: str,
    output_dir: str,
    perceptualdiff: str = "perceptualdiff",
    diff_threshold: float = 0.00001,
    *,
    use_lpips: bool = True,
    include_suites: set[str] | None = None,
) -> ComparisonSummary:
    results_info = ResultsInfo.parse(results_path, include_suites)

    if "nxdk_pgraph_tests_golden_results" in golden_path:
        golden_info = ResultsInfo(
            xemu_version="Xbox",
            platform_info="Xbox",
            gl_info="DirectX:nv2a",
            result_path=golden_path,
            test_suites=defaultdict(dict),
        ).find_result_images(include_suites)
        against_name = "Xbox_Hardware"
    else:
        golden_info = ResultsInfo.parse(golden_path, include_suites)
        against_name = golden_info.run_identifier

    logger.debug("Comparing %s to %s", results_info.run_identifier, against_name)

    comparison_output_directory = os.path.join(
        output_dir,
        results_info.output_subdirectory,
        golden_info.run_identifier_subdirectory,
    )
    if os.path.isdir(comparison_output_directory):
        shutil.rmtree(comparison_output_directory)
    os.makedirs(comparison_output_directory, exist_ok=True)

    if use_lpips:
        only_results, only_golden, diffs = _compare_lpips(results_info, golden_info)
        if not (only_results or only_golden or diffs):
            summary = ComparisonSummary(
                result_identifier=results_info.run_identifier,
                golden_identifier=against_name,
            )
            summary.save_to_file(os.path.join(comparison_output_directory, "summary.json"))
            return summary

        for diff in sorted(diffs, key=lambda x: f"{x.test_suite}:{x.test_case}"):
            if diff.distance < diff_threshold:
                logger.info(
                    "Not generating diff image for %s with distance %G below threshold",
                    diff.fully_qualified_test_name,
                    diff.distance,
                )
                continue
            logger.info("Generating diff image for %s", diff.fully_qualified_test_name)
            diff.generate_difference_image(perceptualdiff, comparison_output_directory)
    else:
        only_results, only_golden, diffs = _compare_perceptualdiff(
            results_info, golden_info, perceptualdiff, comparison_output_directory
        )
        if not (only_results or only_golden or diffs):
            summary = ComparisonSummary(
                result_identifier=results_info.run_identifier,
                golden_identifier=against_name,
            )
            summary.save_to_file(os.path.join(comparison_output_directory, "summary.json"))
            return summary

    logger.debug("Writing output to %s", comparison_output_directory)

    summary = ComparisonSummary(
        result_identifier=results_info.run_identifier,
        golden_identifier=against_name,
        tests_without_goldens=sorted(only_results),
        goldens_without_results=sorted(only_golden),
        tests_with_differences={diff.fully_qualified_test_name: diff.distance for diff in diffs},
    )
    summary.save_to_file(os.path.join(comparison_output_directory, "summary.json"))
    return summary


def _discover_results(results_root: str) -> list[str]:
    results_files = glob.glob("**/results.json", root_dir=results_root, recursive=True)

    return [os.path.join(results_root, os.path.dirname(file)) for file in results_files]


def _process_arguments_and_run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        "-v",
        help="Enables verbose logging information",
        action="store_true",
    )
    parser.add_argument(
        "results",
        help="Path to the root of the results to compare against the golden results.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List likely test result sets in the <results> directory.",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        metavar="path_to_output_directory",
        default="compare-results",
        help="Path to directory into which diff artifacts will be written.",
    )
    parser.add_argument(
        "--against",
        "-a",
        help="Path to the root of the results to consider golden. Omit to test against the HW results repo.",
    )
    parser.add_argument("--cache-path", "-C", default="cache", help="Path to persistent cache area.")
    parser.add_argument(
        "--perceptualdiff",
        default="perceptualdiff",
        help="Path to the perceptualdiff binary.",
    )
    parser.add_argument(
        "--diff-threshold",
        "-t",
        type=float,
        default=0.00001,
        help="LPIPS distance threshold below which images are considered equal.",
    )
    parser.add_argument(
        "--use-lpips",
        action="store_true",
        help="Use LPIPS to pre-filter diffs before perceptualdiff.",
    )
    parser.add_argument(
        "--include-suites-file",
        help="Path to a file containing test suite names to process (one per line). If omitted, all suites are processed.",
    )

    args = parser.parse_args()

    if args.list:
        local_results = _discover_results(args.results)
        print("Discovered test runs:")
        if not local_results:
            print("  None")
        else:
            for result in sorted(local_results):
                print(f"  {result}")
        return 0

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level)

    if not os.path.isdir(args.results):
        logger.error("Source directory '%s' does not exist", args.results)
        return 1

    if not args.against:
        cache_path = _ensure_cache_path(args.cache_path)
        hw_golden_root = os.path.join(cache_path, "nxdk_pgraph_tests_golden_results")
        if not os.path.isdir(hw_golden_root):
            _fetch_hw_goldens(hw_golden_root)
        golden_dir = (
            os.path.join(hw_golden_root, "results")
            if os.path.isdir(os.path.join(hw_golden_root, "results"))
            else hw_golden_root
        )
    else:
        golden_dir = args.against

    if not os.path.isdir(golden_dir):
        logger.error("Comparison directory '%s' does not exist", golden_dir)
        return 1

    os.makedirs(args.output_dir, exist_ok=True)

    include_suites = None
    if args.include_suites_file:
        with open(args.include_suites_file) as f:
            include_suites = {line.strip() for line in f if line.strip()}
        logger.info(
            "Filtering to %d suite(s) from %s",
            len(include_suites),
            args.include_suites_file,
        )

    perform_comparison(
        args.results,
        golden_dir,
        args.output_dir,
        args.perceptualdiff,
        args.diff_threshold,
        use_lpips=args.use_lpips,
        include_suites=include_suites,
    )

    return 0


def main() -> int:
    return _process_arguments_and_run()


if __name__ == "__main__":
    sys.exit(main())
