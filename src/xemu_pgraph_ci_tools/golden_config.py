from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_HW_GOLDEN_CONFIG_URL = (
    "https://raw.githubusercontent.com/abaire/nxdk_pgraph_tests_golden_results/refs/heads/main/config.json"
)


class GoldenConfig:
    """Encapsulates configuration and metadata for golden test results."""

    def __init__(
        self,
        deprecated_tests: dict[str, list[str]] | set[str] | list[str] | None = None,
        version: int = 1,
    ) -> None:
        self.version = version
        self._deprecated_set: set[str] = set()
        self._deprecated_tuples: set[tuple[str, str]] = set()

        if isinstance(deprecated_tests, dict):
            for suite, tests in deprecated_tests.items():
                norm_suite = suite.replace(" ", "_")
                for test in tests:
                    self._deprecated_tuples.add((norm_suite, test))
                    self._deprecated_tuples.add((suite, test))
                    self._deprecated_set.add(f"{norm_suite}:{test}")
                    self._deprecated_set.add(f"{suite}:{test}")
                    self._deprecated_set.add(f"{norm_suite}::{test}")
                    self._deprecated_set.add(f"{suite}::{test}")
        elif isinstance(deprecated_tests, (set, list)):
            for item in deprecated_tests:
                if ":" in item:
                    suite, test = item.split("::" if "::" in item else ":", 1)
                    suite_norm = suite.replace(" ", "_")
                    self._deprecated_tuples.add((suite_norm, test))
                    self._deprecated_tuples.add((suite, test))
                    self._deprecated_set.add(f"{suite_norm}:{test}")
                    self._deprecated_set.add(f"{suite}:{test}")
                    self._deprecated_set.add(f"{suite_norm}::{test}")
                    self._deprecated_set.add(f"{suite}::{test}")
                else:
                    self._deprecated_set.add(item)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenConfig:
        deprecated = data.get("deprecated_tests", {})
        version = data.get("version", 1)
        return cls(deprecated_tests=deprecated, version=version)

    def is_deprecated(self, suite_name: str, test_name: str) -> bool:
        """Checks if a given suite and test case is deprecated."""
        norm_suite = suite_name.replace(" ", "_")
        return (
            (norm_suite, test_name) in self._deprecated_tuples
            or (suite_name, test_name) in self._deprecated_tuples
            or f"{norm_suite}:{test_name}" in self._deprecated_set
            or f"{suite_name}:{test_name}" in self._deprecated_set
        )

    def is_deprecated_fq(self, fq_name: str) -> bool:
        """Checks if a fully-qualified test name ('Suite:Test' or 'Suite::Test') is deprecated."""
        if " :: " in fq_name:
            suite, test = fq_name.split(" :: ", 1)
            return self.is_deprecated(suite, test)
        if "::" in fq_name:
            suite, test = fq_name.split("::", 1)
            return self.is_deprecated(suite, test)
        if ":" in fq_name:
            suite, test = fq_name.split(":", 1)
            return self.is_deprecated(suite, test)
        return fq_name in self._deprecated_set

    @property
    def has_deprecated_tests(self) -> bool:
        """Returns True if there are any deprecated tests configured."""
        return bool(self._deprecated_set)


def load_golden_config(
    config_path: str | None = None,
    golden_dir: str | None = None,
    cache_path: str | None = None,
    config_url: str | None = DEFAULT_HW_GOLDEN_CONFIG_URL,
) -> GoldenConfig:
    """Attempts to load GoldenConfig from a local file, or from config_url if local file is not found."""
    candidates: list[str] = []
    if config_path:
        candidates.append(config_path)

    if golden_dir:
        candidates.append(os.path.join(golden_dir, "config.json"))
        candidates.append(os.path.join(golden_dir, "..", "config.json"))

    if cache_path:
        candidates.append(os.path.join(cache_path, "nxdk_pgraph_tests_golden_results", "config.json"))
        candidates.append(os.path.join(cache_path, "config.json"))

    candidates.append(os.path.join("cache", "nxdk_pgraph_tests_golden_results", "config.json"))
    candidates.append(os.path.join("..", "nxdk_pgraph_tests_golden_results", "config.json"))

    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                with open(cand, encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("Loaded golden config from %s", cand)
                return GoldenConfig.from_dict(data)
            except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Failed to load golden config from %s: %s", cand, e)

    if config_url and config_url.startswith(("https://", "http://")):
        try:
            logger.info("Fetching golden config from %s...", config_url)
            req = urllib.request.Request(  # noqa: S310
                config_url, headers={"User-Agent": "xemu-pgraph-ci-tools/1.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
            logger.info("Successfully fetched golden config from %s", config_url)
            return GoldenConfig.from_dict(data)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Could not fetch golden config from %s: %s", config_url, e)

    return GoldenConfig()
