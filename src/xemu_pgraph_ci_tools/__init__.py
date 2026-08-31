from __future__ import annotations

from typing import Any

from xemu_pgraph_ci_tools.models import (
    ComparisonSummary,
    Difference,
    KnownIssue,
    KnownIssueFilter,
    KnownIssuesRegistry,
    PipelineReport,
    ResultsInfo,
    TestResultItem,
)

__all__ = [
    "ComparisonSummary",
    "Difference",
    "KnownIssue",
    "KnownIssueFilter",
    "KnownIssuesRegistry",
    "PipelineReport",
    "ResultsInfo",
    "TestResultItem",
    "generate_missing_hw_diffs",
    "generate_xemu_diffs",
    "merge_main",
    "perform_comparison",
    "run_pipeline",
    "runner_main",
]


def __getattr__(name: str) -> Any:
    if name == "perform_comparison":
        from xemu_pgraph_ci_tools.comparator import perform_comparison  # noqa: PLC0415

        return perform_comparison
    if name == "generate_missing_hw_diffs":
        from xemu_pgraph_ci_tools.hw_diffs import generate_missing_hw_diffs  # noqa: PLC0415

        return generate_missing_hw_diffs
    if name == "generate_xemu_diffs":
        from xemu_pgraph_ci_tools.xemu_diffs import generate_diffs  # noqa: PLC0415

        return generate_diffs
    if name == "run_pipeline":
        from xemu_pgraph_ci_tools.pipeline import run_pipeline  # noqa: PLC0415

        return run_pipeline
    if name == "runner_main":
        from xemu_pgraph_ci_tools.runner import main  # noqa: PLC0415

        return main
    if name == "merge_main":
        from xemu_pgraph_ci_tools.runner import merge_main  # noqa: PLC0415

        return merge_main
    msg = f"module '{__name__}' has no attribute '{name}'"
    raise AttributeError(msg)
