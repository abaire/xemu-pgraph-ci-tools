# ruff: noqa: PLR2004

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

PERCEPTUALDIFF_DIFFERENCE_RE = re.compile(r"(\d+) pixels are different")


@dataclass(frozen=True)
class RunIdentifier:
    """Encapsulates components of a test run identifier (xemu version, platform, OpenGL/GLSL info)."""

    xemu_version: str
    platform_info: str
    gl_info: str = ""
    run_identifier: tuple[str, ...] = ()

    @property
    def gl_version(self) -> str:
        parts = self.gl_info.split(":") if ":" in self.gl_info else self.gl_info.split("--")
        return parts[0] if parts else ""

    @property
    def glsl_version(self) -> str:
        parts = self.gl_info.split(":") if ":" in self.gl_info else self.gl_info.split("--")
        return parts[1] if len(parts) > 1 else ""

    @property
    def path(self) -> str:
        if self.run_identifier:
            return str(os.path.join(*self.run_identifier)).replace(":", "--")
        return str(os.path.join(self.xemu_version, self.platform_info, self.gl_info)).replace(":", "--")

    @property
    def minimal_path(self) -> str:
        """Returns a path consisting of 'xemu/platform/gl'."""
        return self.minimal_identifier().path

    def minimal_identifier(self) -> RunIdentifier:
        """Returns a RunIdentifier that omits any extraneous components of the run_identifier member."""
        formatted_gl = self.gl_info.replace(":", "--")
        return RunIdentifier(
            run_identifier=(self.xemu_version, self.platform_info, formatted_gl),
            xemu_version=self.xemu_version,
            platform_info=self.platform_info,
            gl_info=formatted_gl,
        )

    @property
    def string_identifier(self) -> str:
        return f"{self.xemu_version}:{self.platform_info}:{self.gl_info}"

    @classmethod
    def parse(cls, path_or_identifier: str) -> RunIdentifier:
        """Parses a result directory path or colon/slash delimited identifier into a RunIdentifier."""
        if not path_or_identifier:
            return cls(xemu_version="unknown", platform_info="unknown", gl_info="unknown")

        # Handle colon-separated identifiers like "xemu-0.8.134:Darwin_arm64:gl_Apple"
        if ":" in path_or_identifier and not os.path.exists(path_or_identifier):
            parts = [p for p in path_or_identifier.split(":") if p]
            if len(parts) >= 3:
                return cls(
                    xemu_version=parts[0],
                    platform_info=parts[1],
                    gl_info=":".join(parts[2:]),
                    run_identifier=tuple(parts),
                )
            if len(parts) == 2:
                return cls(
                    xemu_version=parts[0],
                    platform_info=parts[1],
                    gl_info="unknown",
                    run_identifier=tuple(parts),
                )

        clean_path = path_or_identifier.replace("\\", "/").rstrip("/")
        components = [c for c in clean_path.split("/") if c]

        start_idx = 0
        for i, c in enumerate(components):
            if c in ("results", "baseline"):
                start_idx = i + 1
                while start_idx < len(components) and components[start_idx] in ("results", "baseline"):
                    start_idx += 1
                break

        subparts = components[start_idx:] if start_idx < len(components) else components
        if len(subparts) >= 4:
            xemu_version = subparts[0]
            platform_info = subparts[1]
            gl_info = ":".join(subparts[2:])
        elif len(subparts) == 3:
            xemu_version = subparts[0]
            platform_info = subparts[1]
            gl_info = subparts[2]
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

        return cls(
            run_identifier=tuple(subparts),
            xemu_version=xemu_version,
            platform_info=platform_info,
            gl_info=gl_info,
        )


@dataclass(frozen=True)
class SourceTestIdentifier:
    """Encapsulates the identification of a specific test artifact within a test run."""

    xemu_version: str
    platform_info: str
    suite_name: str
    test_name: str


@dataclass
class ResultsInfo:
    """Metadata about a test run directory, its environment, and discovered images."""

    result_path: str
    identifier: RunIdentifier
    test_suites: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))

    def __init__(
        self,
        result_path: str,
        xemu_version: str | None = None,
        platform_info: str | None = None,
        gl_info: str | None = None,
        test_suites: dict[str, dict[str, str]] | None = None,
        identifier: RunIdentifier | None = None,
    ) -> None:
        self.result_path = result_path
        if identifier is not None:
            self.identifier = identifier
        else:
            self.identifier = RunIdentifier(
                xemu_version=xemu_version or "unknown",
                platform_info=platform_info or "unknown",
                gl_info=gl_info or "unknown",
            )
        self.test_suites = test_suites if test_suites is not None else defaultdict(dict)

    @property
    def xemu_version(self) -> str:
        return self.identifier.xemu_version

    @property
    def platform_info(self) -> str:
        return self.identifier.platform_info

    @property
    def gl_info(self) -> str:
        return self.identifier.gl_info

    @property
    def gl_version(self) -> str:
        return self.identifier.gl_version

    @property
    def glsl_version(self) -> str:
        return self.identifier.glsl_version

    @property
    def run_identifier(self) -> str:
        return self.identifier.string_identifier

    @property
    def output_subdirectory(self) -> str:
        return os.path.join(self.xemu_version, self.platform_info, self.gl_info.replace(":", "/"))

    @property
    def run_identifier_subdirectory(self) -> str:
        return self.run_identifier.replace(":", "__")

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
        identifier = RunIdentifier.parse(result_path)
        return cls(
            result_path=result_path,
            identifier=identifier,
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
