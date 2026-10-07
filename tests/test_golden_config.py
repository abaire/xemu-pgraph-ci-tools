from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

from xemu_pgraph_ci_tools.golden_config import (
    GoldenConfig,
    load_golden_config,
)


def test_golden_config_is_deprecated() -> None:
    config = GoldenConfig(
        deprecated_tests={
            "Blend_tests": ["0_ADD_1", "0_MAX_1"],
            "Texture cubemap": ["Cubemap"],
        }
    )

    assert config.has_deprecated_tests is True
    assert config.is_deprecated("Blend_tests", "0_ADD_1") is True
    assert config.is_deprecated("Blend_tests", "0_MAX_1") is True
    assert config.is_deprecated("Blend_tests", "Other_test") is False

    # Normalization: space vs underscore in suite name
    assert config.is_deprecated("Texture cubemap", "Cubemap") is True
    assert config.is_deprecated("Texture_cubemap", "Cubemap") is True


def test_golden_config_is_deprecated_fq() -> None:
    config = GoldenConfig(
        deprecated_tests={
            "Blend_tests": ["0_ADD_1"],
            "Texture cubemap": ["Cubemap"],
        }
    )

    assert config.is_deprecated_fq("Blend_tests:0_ADD_1") is True
    assert config.is_deprecated_fq("Blend_tests::0_ADD_1") is True
    assert config.is_deprecated_fq("Blend_tests :: 0_ADD_1") is True

    assert config.is_deprecated_fq("Texture_cubemap:Cubemap") is True
    assert config.is_deprecated_fq("Texture cubemap:Cubemap") is True
    assert config.is_deprecated_fq("Texture cubemap :: Cubemap") is True

    assert config.is_deprecated_fq("Blend_tests:active_test") is False
    assert config.is_deprecated_fq("Unknown_suite:unknown_test") is False


def test_golden_config_from_dict() -> None:
    data = {
        "version": 1,
        "deprecated_tests": {
            "Suite1": ["Test1", "Test2"],
        },
    }
    config = GoldenConfig.from_dict(data)
    assert config.version == 1
    assert config.is_deprecated("Suite1", "Test1") is True
    assert config.is_deprecated("Suite1", "Test2") is True
    assert config.is_deprecated("Suite1", "Test3") is False


def test_golden_config_empty() -> None:
    config = GoldenConfig()
    assert config.has_deprecated_tests is False
    assert config.is_deprecated("Suite", "Test") is False
    assert config.is_deprecated_fq("Suite:Test") is False


def test_load_golden_config_from_explicit_path(tmp_path: Path) -> None:
    cfg_file = tmp_path / "custom_config.json"
    cfg_file.write_text(
        json.dumps({"version": 1, "deprecated_tests": {"Suite": ["Test"]}}),
        encoding="utf-8",
    )

    loaded = load_golden_config(config_path=str(cfg_file))
    assert loaded.is_deprecated("Suite", "Test") is True


def test_load_golden_config_from_golden_dir(tmp_path: Path) -> None:
    golden_dir = tmp_path / "goldens"
    golden_dir.mkdir()
    (tmp_path / "config.json").write_text(
        json.dumps({"version": 1, "deprecated_tests": {"Suite": ["DepTest"]}}),
        encoding="utf-8",
    )

    loaded = load_golden_config(golden_dir=str(golden_dir))
    assert loaded.is_deprecated("Suite", "DepTest") is True


def test_load_golden_config_fallback_to_url() -> None:
    fake_data = json.dumps({"version": 1, "deprecated_tests": {"UrlSuite": ["UrlTest"]}}).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = fake_data
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=mock_resp):
        loaded = load_golden_config(
            config_path="/nonexistent/config.json",
            golden_dir="/nonexistent/dir",
            cache_path="/nonexistent/cache",
            config_url="https://example.com/config.json",
        )
        assert loaded.is_deprecated("UrlSuite", "UrlTest") is True
