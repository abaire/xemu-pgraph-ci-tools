# ruff: noqa: T201, PLC0415

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import sys
from typing import Any

from xemu_pgraph_ci_tools.hw_diffs import generate_missing_hw_diffs
from xemu_pgraph_ci_tools.models import (
    ComparisonSummary,
    KnownIssuesRegistry,
    PipelineReport,
    ResultsInfo,
    TestResultItem,
)
from xemu_pgraph_ci_tools.xemu_diffs import generate_diffs as generate_xemu_diffs

logger = logging.getLogger(__name__)


def _find_known_issues_file(
    explicit_path: str | None,
    results_dir: str,
    golden_dir: str,
    xemu_baseline_dir: str | None,
) -> str | None:
    if explicit_path and os.path.isfile(explicit_path):
        return explicit_path

    candidates = [
        os.path.join(results_dir, "known_issues.json"),
        os.path.join(results_dir, "inputs", "known_issues.json"),
        os.path.join(golden_dir, "known_issues.json"),
        os.path.join(golden_dir, "results", "known_issues.json"),
    ]
    if xemu_baseline_dir:
        candidates.extend(
            [
                os.path.join(xemu_baseline_dir, "known_issues.json"),
                os.path.join(xemu_baseline_dir, "results", "known_issues.json"),
            ]
        )

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def run_pipeline(
    results_dir: str,
    golden_dir: str,
    xemu_baseline_dir: str | None,
    output_dir: str,
    *,
    report_output: str | None = None,
    known_issues_path: str | None = None,
    perceptualdiff: str = "perceptualdiff",
    branch: str = "main",
    metadata: dict[str, Any] | None = None,
) -> PipelineReport:
    results_dir = os.path.abspath(os.path.expanduser(results_dir))
    output_dir = os.path.abspath(os.path.expanduser(output_dir))
    golden_dir = os.path.abspath(os.path.expanduser(golden_dir))
    if xemu_baseline_dir:
        xemu_baseline_dir = os.path.abspath(os.path.expanduser(xemu_baseline_dir))

    hw_comparison_dir = os.path.join(output_dir, "compare_hw")
    xemu_comparison_dir = os.path.join(output_dir, "compare_xemu")
    os.makedirs(hw_comparison_dir, exist_ok=True)
    os.makedirs(xemu_comparison_dir, exist_ok=True)

    # 1. Generate Hardware Diffs
    logger.info("Generating hardware golden diffs...")
    generate_missing_hw_diffs(
        results_dir=results_dir,
        output_dir=hw_comparison_dir,
        golden_dir=golden_dir,
        perceptualdiff=perceptualdiff,
    )

    # 2. Generate Xemu Baseline Diffs
    if xemu_baseline_dir and os.path.isdir(xemu_baseline_dir):
        logger.info("Generating xemu baseline diffs against %s...", xemu_baseline_dir)
        generate_xemu_diffs(
            results_dir=results_dir,
            golden_dir=xemu_baseline_dir,
            cache_dir=os.path.join(output_dir, "cache"),
            output_dir=xemu_comparison_dir,
            perceptualdiff=perceptualdiff,
        )
    else:
        logger.warning("No valid xemu baseline directory provided; creating empty comparisons.json")
        with open(os.path.join(xemu_comparison_dir, "comparisons.json"), "w", encoding="utf-8") as f:
            f.write("{}")

    # 3. Load Known Issues
    resolved_known_issues_path = _find_known_issues_file(known_issues_path, results_dir, golden_dir, xemu_baseline_dir)
    registry = (
        KnownIssuesRegistry.load_from_file(resolved_known_issues_path)
        if resolved_known_issues_path
        else KnownIssuesRegistry({})
    )

    # 4. Assemble Structured Pipeline Report
    logger.info("Building structured pipeline report...")
    test_result_items: list[TestResultItem] = []
    total_tests = 0
    passed_tests = 0
    differing_tests = 0
    missing_goldens = 0
    regressions_count = 0

    # Discover result runs
    for root, dirnames, filenames in os.walk(results_dir):
        if "results.json" not in filenames:
            continue
        dirnames.clear()

        run_info = ResultsInfo.parse(root)
        machine = run_info.platform_info
        gl = run_info.gl_version
        glsl = run_info.glsl_version

        # Load HW summary for this run
        hw_summary_path = os.path.join(
            hw_comparison_dir,
            run_info.output_subdirectory,
            "Xbox__Xbox__DirectX__nv2a",
            "summary.json",
        )
        hw_summary = ComparisonSummary.load_from_file(hw_summary_path) if os.path.isfile(hw_summary_path) else None

        # Load Xemu baseline summary for this run
        xemu_summary = None
        comparisons_json_path = os.path.join(xemu_comparison_dir, "comparisons.json")
        if os.path.isfile(comparisons_json_path):
            with open(comparisons_json_path, encoding="utf-8") as f:
                comparisons_map = json.load(f)
            rel_path = os.path.relpath(root, os.getcwd())
            baseline_path = comparisons_map.get(rel_path) or comparisons_map.get(root)
            if baseline_path:
                baseline_info = ResultsInfo.parse(baseline_path)
                xemu_summary_path = os.path.join(
                    xemu_comparison_dir,
                    run_info.output_subdirectory,
                    baseline_info.run_identifier_subdirectory,
                    "summary.json",
                )
                if os.path.isfile(xemu_summary_path):
                    xemu_summary = ComparisonSummary.load_from_file(xemu_summary_path)

        for suite_name, test_cases in sorted(run_info.test_suites.items()):
            for test_case, result_img in sorted(test_cases.items()):
                total_tests += 1
                fq_test_name = f"{suite_name}:{test_case}"

                # Check HW diff
                hw_diff_score: float | None = None
                hw_diff_img: str | None = None
                if hw_summary and fq_test_name in hw_summary.tests_with_differences:
                    hw_diff_score = hw_summary.tests_with_differences[fq_test_name]
                    potential_diff_img = os.path.join(
                        hw_comparison_dir,
                        run_info.output_subdirectory,
                        "Xbox__Xbox__DirectX__nv2a",
                        suite_name,
                        f"{test_case}-diff.png",
                    )
                    if os.path.isfile(potential_diff_img):
                        hw_diff_img = potential_diff_img

                # Check HW golden existence
                hw_golden_path = os.path.join(golden_dir, suite_name, f"{test_case}.png")
                hw_golden_img: str | None = hw_golden_path if os.path.isfile(hw_golden_path) else None
                if hw_golden_img is None:
                    missing_goldens += 1

                # Check Xemu baseline diff
                xemu_diff_score: float | None = None
                xemu_diff_img: str | None = None
                xemu_golden_img: str | None = None
                if xemu_summary and fq_test_name in xemu_summary.tests_with_differences:
                    xemu_diff_score = xemu_summary.tests_with_differences[fq_test_name]
                    if baseline_path:
                        potential_xemu_diff_img = os.path.join(
                            xemu_comparison_dir,
                            run_info.output_subdirectory,
                            baseline_info.run_identifier_subdirectory,
                            suite_name,
                            f"{test_case}-diff.png",
                        )
                        if os.path.isfile(potential_xemu_diff_img):
                            xemu_diff_img = potential_xemu_diff_img
                        potential_xemu_golden = os.path.join(baseline_path, suite_name, f"{test_case}.png")
                        if os.path.isfile(potential_xemu_golden):
                            xemu_golden_img = potential_xemu_golden

                known_issues = registry.get_known_issues(suite_name, test_case, machine, gl, glsl)

                item = TestResultItem(
                    suite=suite_name,
                    test_name=test_case,
                    result_image_path=result_img,
                    machine=machine,
                    gl=gl,
                    glsl=glsl,
                    hw_golden_image_path=hw_golden_img,
                    hw_diff_image_path=hw_diff_img,
                    hw_diff_score=hw_diff_score,
                    xemu_golden_image_path=xemu_golden_img,
                    xemu_diff_image_path=xemu_diff_img,
                    xemu_diff_score=xemu_diff_score,
                    known_issues=known_issues,
                )

                if item.has_diff:
                    differing_tests += 1
                else:
                    passed_tests += 1

                if item.is_regression:
                    regressions_count += 1

                test_result_items.append(item)

    report = PipelineReport(
        generated_at=datetime.datetime.now(datetime.UTC).isoformat(),
        results_dir=results_dir,
        golden_dir=golden_dir,
        xemu_baseline_dir=xemu_baseline_dir,
        total_tests=total_tests,
        passed_tests=passed_tests,
        differing_tests=differing_tests,
        missing_goldens=missing_goldens,
        regressions_count=regressions_count,
        test_results=test_result_items,
        metadata={
            "branch": branch,
            **(metadata or {}),
        },
    )

    final_report_path = report_output or os.path.join(output_dir, "report.json")
    report.save_json(final_report_path)
    logger.info("Pipeline report written to %s", final_report_path)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate PGraph regression visual diff report & structured JSON.")
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory containing the new test results.",
    )
    parser.add_argument(
        "--golden-dir",
        default="cache/nxdk_pgraph_tests_golden_results",
        help="Directory containing hardware golden results.",
    )
    parser.add_argument(
        "--xemu-baseline-dir",
        default=None,
        help="Directory containing baseline xemu results for comparison (optional).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory where diff artifacts and report will be generated.",
    )
    parser.add_argument(
        "--report-output",
        default=None,
        help="Path where report.json will be written (defaults to <output-dir>/report.json).",
    )
    parser.add_argument(
        "--known-issues",
        default=None,
        help="Path to known_issues.json file.",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Git branch name for report metadata.",
    )
    parser.add_argument(
        "--perceptualdiff",
        default="perceptualdiff",
        help="Path to perceptualdiff executable.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )
    parser.add_argument(
        "--emit-schema",
        "--schema",
        action="store_true",
        help="Emit JSON Schema for the output report.json artifact and exit.",
    )

    args = parser.parse_args()

    if args.emit_schema:
        from xemu_pgraph_ci_tools.schema import emit_json_schema

        print(emit_json_schema(PipelineReport))
        return 0

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    report = run_pipeline(
        results_dir=args.results_dir,
        golden_dir=args.golden_dir,
        xemu_baseline_dir=args.xemu_baseline_dir,
        output_dir=args.output_dir,
        report_output=args.report_output,
        known_issues_path=args.known_issues,
        branch=args.branch,
        perceptualdiff=args.perceptualdiff,
    )

    logger.info(
        "Summary: %d total tests, %d passed, %d differing, %d regressions",
        report.total_tests,
        report.passed_tests,
        report.differing_tests,
        report.regressions_count,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
