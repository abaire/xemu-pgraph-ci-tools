# ruff: noqa: PLC0415

from __future__ import annotations

from typing import Any

from xemu_pgraph_ci_tools.models import (
    ComparisonSummary,
    Difference,
    DiffTask,
    KnownIssue,
    KnownIssueFilter,
    KnownIssuesRegistry,
    PipelineReport,
    ResultsInfo,
    TestResultItem,
)

__all__ = [
    "ComparisonSummary",
    "DiffTask",
    "Difference",
    "KnownIssue",
    "KnownIssueFilter",
    "KnownIssuesRegistry",
    "PipelineReport",
    "ResultsInfo",
    "TestResultItem",
    "discover_diff_tasks",
    "generate_missing_hw_diffs",
    "generate_xemu_diffs",
    "get_shard_slice",
    "identify_missing_hw_diffs",
    "identify_missing_xemu_diffs",
    "merge_main",
    "partition_diff_tasks",
    "perform_comparison",
    "process_diff_tasks",
    "reduce_comparison_summaries",
    "run_pipeline",
    "runner_main",
]


def __getattr__(name: str) -> Any:
    if name == "perform_comparison":
        from xemu_pgraph_ci_tools.comparator import perform_comparison

        return perform_comparison
    if name in {
        "discover_diff_tasks",
        "partition_diff_tasks",
        "get_shard_slice",
        "process_diff_tasks",
        "reduce_comparison_summaries",
    }:
        import xemu_pgraph_ci_tools.comparator as comp

        return getattr(comp, name)
    if name in {"generate_missing_hw_diffs", "identify_missing_hw_diffs"}:
        import xemu_pgraph_ci_tools.hw_diffs as hd

        return getattr(hd, name)
    if name in {"generate_xemu_diffs", "identify_missing_xemu_diffs"}:
        import xemu_pgraph_ci_tools.xemu_diffs as xd

        if name == "generate_xemu_diffs":
            return xd.generate_diffs
        return xd.identify_missing_xemu_diffs
    if name == "run_pipeline":
        from xemu_pgraph_ci_tools.pipeline import run_pipeline

        return run_pipeline
    if name == "runner_main":
        from xemu_pgraph_ci_tools.runner import main

        return main
    if name == "merge_main":
        from xemu_pgraph_ci_tools.runner import merge_main

        return merge_main
    msg = f"module '{__name__}' has no attribute '{name}'"
    raise AttributeError(msg)
