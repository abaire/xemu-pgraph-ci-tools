# ruff: noqa: PLR2004

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

PERCEPTUALDIFF_DIFFERENCE_RE = re.compile(r"(\d+) pixels are different")


def _match_pattern(pattern: str, value: str) -> bool:
    """Matches a string against a pattern containing '*' wildcards."""
    if not pattern:
        return True
    elements = pattern.split("*")
    escaped = [re.escape(component) for component in elements]
    regex = "^" + ".*".join(escaped) + "$"
    return bool(re.match(regex, value))


@dataclass
class ResultsInfo:
    """Metadata about a test run directory, its environment, and discovered images."""

    result_path: str
    xemu_version: str
    platform_info: str
    gl_info: str
    test_suites: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))

    @property
    def run_identifier(self) -> str:
        return f"{self.xemu_version}:{self.platform_info}:{self.gl_info}"

    @property
    def output_subdirectory(self) -> str:
        return os.path.join(self.xemu_version, self.platform_info, self.gl_info.replace(":", "/"))

    @property
    def run_identifier_subdirectory(self) -> str:
        return self.run_identifier.replace(":", "__")

    @property
    def gl_version(self) -> str:
        parts = self.gl_info.split(":")
        return parts[0] if parts else ""

    @property
    def glsl_version(self) -> str:
        parts = self.gl_info.split(":")
        return parts[1] if len(parts) > 1 else ""

    def get_flattened_tests(self) -> set[str]:
        """Return a flattened set of test_suite::test_case."""
        ret = set()
        for suite_name, test_cases in self.test_suites.items():
            suite_dir_name = suite_name.replace(" ", "_")
            for test_case in test_cases:
                ret.add(f"{suite_dir_name}:{test_case}")
        return ret

    def find_result_images(self, include_suites: set[str] | None = None) -> ResultsInfo:
        """Walks the result_path to find all png images."""
        if not os.path.isdir(self.result_path):
            return self

        for root, dirnames, filenames in os.walk(self.result_path):
            if os.path.basename(root).startswith("."):
                dirnames.clear()
                continue

            if dirnames:
                continue

            test_suite = os.path.basename(root)
            if test_suite in {"perceptualdiff", "scripts", "cache"}:
                continue

            if include_suites and test_suite not in include_suites:
                continue

            for filename in filenames:
                if filename.endswith(".png") and not filename.endswith("-diff.png"):
                    test_case = os.path.splitext(filename)[0]
                    self.test_suites[test_suite][test_case] = os.path.join(root, filename)
        return self

    @classmethod
    def parse(cls, result_path: str, include_suites: set[str] | None = None) -> ResultsInfo:
        result_path = os.path.abspath(os.path.expanduser(result_path))
        components = [c for c in result_path.rstrip(os.sep).split(os.sep) if c]
        if "results" in components:
            idx = len(components) - 1 - components[::-1].index("results")
            subparts = components[idx + 1 :]
            if len(subparts) >= 3:
                xemu_version = subparts[0]
                platform_info = subparts[1]
                gl_info = ":".join(subparts[2:])
            elif len(subparts) == 2:
                xemu_version = subparts[0]
                platform_info = subparts[1]
                gl_info = "unknown"
            elif len(subparts) == 1:
                xemu_version = subparts[0]
                platform_info = "unknown"
                gl_info = "unknown"
            else:
                xemu_version = "unknown"
                platform_info = "unknown"
                gl_info = "unknown"
        elif "baseline" in components:
            idx = len(components) - 1 - components[::-1].index("baseline")
            subparts = components[idx + 1 :]
            if len(subparts) >= 3:
                xemu_version = subparts[0]
                platform_info = subparts[1]
                gl_info = ":".join(subparts[2:])
            elif len(subparts) == 2:
                xemu_version = subparts[0]
                platform_info = subparts[1]
                gl_info = "unknown"
            else:
                xemu_version = "unknown"
                platform_info = "unknown"
                gl_info = "unknown"
        elif len(components) >= 4:
            xemu_version = components[-4]
            platform_info = components[-3]
            gl_info = f"{components[-2]}:{components[-1]}"
        elif len(components) == 3:
            xemu_version = components[-3]
            platform_info = components[-2]
            gl_info = components[-1]
        else:
            xemu_version = "unknown"
            platform_info = "unknown"
            gl_info = "unknown:unknown"

        return cls(
            result_path=result_path,
            xemu_version=xemu_version,
            platform_info=platform_info,
            gl_info=gl_info,
            test_suites=defaultdict(dict),
        ).find_result_images(include_suites)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_path": self.result_path,
            "xemu_version": self.xemu_version,
            "platform_info": self.platform_info,
            "gl_info": self.gl_info,
            "test_suites": {k: dict(v) for k, v in self.test_suites.items()},
        }


@dataclass
class Difference:
    """Encapsulates the difference between a result artifact and its golden reference."""

    test_suite: str
    test_case: str
    result_artifact: str
    golden_artifact: str
    distance: float

    @property
    def fully_qualified_test_name(self) -> str:
        return f"{self.test_suite}:{self.test_case}"

    @property
    def difference_filename(self) -> str:
        return f"{os.path.join(self.test_suite, self.test_case)}-diff.png"

    def generate_difference_image(self, perceptualdiff: str, output_path: str) -> tuple[int, str, str]:
        """Generates a diff image in the given output_path using perceptualdiff.

        Returns tuple[ExitCode, STDOUT, STDERR]
        """
        target_filename = os.path.join(output_path, self.difference_filename)
        target_dir = os.path.dirname(target_filename)
        os.makedirs(target_dir, exist_ok=True)
        result = subprocess.run(
            [
                perceptualdiff,
                "-output",
                target_filename,
                self.result_artifact,
                self.golden_artifact,
            ],
            check=False,
            capture_output=True,
        )
        return (
            result.returncode,
            result.stdout.decode("utf-8", errors="replace"),
            result.stderr.decode("utf-8", errors="replace"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_suite": self.test_suite,
            "test_case": self.test_case,
            "result_artifact": self.result_artifact,
            "golden_artifact": self.golden_artifact,
            "distance": self.distance,
        }


@dataclass
class DiffTask:
    """A task representing an individual test diff between a source image and a golden reference image."""

    suite: str
    test_case: str
    source_image: str
    golden_image: str
    output_diff_image: str
    results_path: str = ""
    results_identifier: str = ""
    golden_identifier: str = ""
    comparison_output_dir: str = ""

    @property
    def fully_qualified_test_name(self) -> str:
        return f"{self.suite}:{self.test_case}"

    def generate_difference_image(self, perceptualdiff: str) -> tuple[int, str, str]:
        """Generates a diff image at output_diff_image using perceptualdiff.

        Returns tuple[ExitCode, STDOUT, STDERR]
        """
        target_dir = os.path.dirname(self.output_diff_image)
        os.makedirs(target_dir, exist_ok=True)
        result = subprocess.run(
            [
                perceptualdiff,
                "-output",
                self.output_diff_image,
                self.source_image,
                self.golden_image,
            ],
            check=False,
            capture_output=True,
        )
        return (
            result.returncode,
            result.stdout.decode("utf-8", errors="replace"),
            result.stderr.decode("utf-8", errors="replace"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "test_case": self.test_case,
            "source_image": self.source_image,
            "golden_image": self.golden_image,
            "output_diff_image": self.output_diff_image,
            "results_path": self.results_path,
            "results_identifier": self.results_identifier,
            "golden_identifier": self.golden_identifier,
            "comparison_output_dir": self.comparison_output_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiffTask:
        return cls(
            suite=data["suite"],
            test_case=data["test_case"],
            source_image=data["source_image"],
            golden_image=data["golden_image"],
            output_diff_image=data["output_diff_image"],
            results_path=data.get("results_path", ""),
            results_identifier=data.get("results_identifier", ""),
            golden_identifier=data.get("golden_identifier", ""),
            comparison_output_dir=data.get("comparison_output_dir", ""),
        )


@dataclass
class ComparisonSummary:
    """Summary of a comparison between a test run and a golden reference set."""

    result_identifier: str
    golden_identifier: str
    tests_without_goldens: list[str] = field(default_factory=list)
    goldens_without_results: list[str] = field(default_factory=list)
    tests_with_differences: dict[str, float] = field(default_factory=dict)
    tests_evaluated: list[str] = field(default_factory=list)

    def merge(self, other: ComparisonSummary) -> ComparisonSummary:
        """Merges another ComparisonSummary into this one."""
        if other.result_identifier and not self.result_identifier:
            self.result_identifier = other.result_identifier
        if other.golden_identifier and not self.golden_identifier:
            self.golden_identifier = other.golden_identifier

        self.tests_with_differences.update(other.tests_with_differences)
        self.tests_without_goldens = sorted(set(self.tests_without_goldens) | set(other.tests_without_goldens))
        self.goldens_without_results = sorted(set(self.goldens_without_results) | set(other.goldens_without_results))
        self.tests_evaluated = sorted(set(self.tests_evaluated) | set(other.tests_evaluated))
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_identifier": self.result_identifier,
            "golden_identifier": self.golden_identifier,
            "tests_without_goldens": sorted(self.tests_without_goldens),
            "goldens_without_results": sorted(self.goldens_without_results),
            "tests_with_differences": self.tests_with_differences,
            "tests_evaluated": sorted(self.tests_evaluated),
        }

    def save_to_file(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)

    @classmethod
    def load_from_file(cls, path: str) -> ComparisonSummary:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            result_identifier=data.get("result_identifier", ""),
            golden_identifier=data.get("golden_identifier", ""),
            tests_without_goldens=data.get("tests_without_goldens", []),
            goldens_without_results=data.get("goldens_without_results", []),
            tests_with_differences=data.get("tests_with_differences", {}),
            tests_evaluated=data.get("tests_evaluated", []),
        )


@dataclass
class KnownIssueFilter:
    """Filter rule matching platform and GL information."""

    platform: str | None = None
    gl: str | None = None
    glsl: str | None = None

    def matches(self, machine: str, gl: str, glsl: str) -> bool:
        if self.platform and not _match_pattern(self.platform, machine):
            return False
        if self.gl and not _match_pattern(self.gl, gl):
            return False
        return not (self.glsl and not _match_pattern(self.glsl, glsl))


@dataclass
class KnownIssue:
    """A known issue description with optional environment filters."""

    text: str
    filter: KnownIssueFilter | None = None

    def matches(self, machine: str, gl: str, glsl: str) -> bool:
        if not self.filter:
            return True
        return self.filter.matches(machine, gl, glsl)


class KnownIssuesRegistry:
    """Registry of known issues per test suite and test case."""

    def __init__(self, data: dict[str, Any] | None = None):
        self._raw_data: dict[str, Any] = data or {}

    @classmethod
    def load_from_file(cls, file_path: str) -> KnownIssuesRegistry:
        if not os.path.isfile(file_path):
            logger.debug("Known issues file '%s' not found.", file_path)
            return cls({})
        try:
            with open(file_path, encoding="utf-8") as f:
                return cls(json.load(f))
        except Exception:
            logger.exception("Failed to load known issues from '%s'", file_path)
            return cls({})

    def get_known_issues(self, suite: str, test_name: str, machine: str, gl: str, glsl: str) -> list[str]:
        """Returns all matching known issues for a given test suite, test case, and environment."""
        issues: list[str] = []
        suite_data = self._raw_data.get(suite)
        if not suite_data:
            return issues

        # Suite-level issues
        for issue_dict in suite_data.get("issues", []):
            issue = self._parse_issue(issue_dict)
            if issue and issue.matches(machine, gl, glsl):
                issues.append(issue.text)

        # Test-level issues
        test_data = suite_data.get(test_name)
        if test_data:
            for issue_dict in test_data.get("issues", []):
                issue = self._parse_issue(issue_dict)
                if issue and issue.matches(machine, gl, glsl):
                    issues.append(issue.text)

        return issues

    @staticmethod
    def _parse_issue(data: dict[str, Any]) -> KnownIssue | None:
        text = data.get("text")
        if not text:
            return None
        filter_dict = data.get("filter")
        flt = None
        if filter_dict:
            flt = KnownIssueFilter(
                platform=filter_dict.get("platform"),
                gl=filter_dict.get("gl"),
                glsl=filter_dict.get("glsl"),
            )
        return KnownIssue(text=text, filter=flt)


@dataclass
class TestResultItem:
    """Consolidated representation of a test case comparison result."""

    __test__ = False

    suite: str
    test_name: str
    result_image_path: str
    machine: str = ""
    gl: str = ""
    glsl: str = ""
    hw_golden_image_path: str | None = None
    hw_diff_image_path: str | None = None
    hw_diff_score: float | None = None
    xemu_golden_image_path: str | None = None
    xemu_diff_image_path: str | None = None
    xemu_diff_score: float | None = None
    known_issues: list[str] = field(default_factory=list)

    @property
    def has_diff(self) -> bool:
        return bool(self.hw_diff_image_path or self.xemu_diff_image_path)

    @property
    def is_regression(self) -> bool:
        # A difference against hardware golden is a regression if there's no baseline diff or it's new and not a known issue
        return bool(
            self.hw_diff_score is not None
            and self.hw_diff_score > 0
            and not self.known_issues
            and (self.xemu_diff_score is None or self.xemu_diff_score > 0)
        )

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class PipelineReport:
    """Top-level structured JSON report produced by the comparison pipeline."""

    generated_at: str
    results_dir: str
    golden_dir: str
    xemu_baseline_dir: str | None
    total_tests: int = 0
    passed_tests: int = 0
    differing_tests: int = 0
    missing_goldens: int = 0
    regressions_count: int = 0
    test_results: list[TestResultItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "results_dir": self.results_dir,
            "golden_dir": self.golden_dir,
            "xemu_baseline_dir": self.xemu_baseline_dir,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "differing_tests": self.differing_tests,
            "missing_goldens": self.missing_goldens,
            "regressions_count": self.regressions_count,
            "metadata": self.metadata,
            "test_results": [item.to_dict() for item in self.test_results],
        }

    def save_json(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)
