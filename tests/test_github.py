from __future__ import annotations

import io
import os
import zipfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from xemu_pgraph_ci_tools.github import (
    _HW_GOLDEN_GIT_URL,
    _download_artifact,
    _fetch_github_release_info,
    _resolve_token,
    download_tester_iso,
    download_xemu,
    download_xemu_hdd,
    fetch_hw_goldens,
)


def test_resolve_token():
    # Direct argument takes highest precedence
    assert _resolve_token(github_token="token_a") == "token_a"

    # Environment variables fallback
    with patch.dict(os.environ, {"GITHUB_TOKEN": "token_env", "GH_TOKEN": "token_gh"}, clear=True):
        assert _resolve_token() == "token_env"

    with patch.dict(os.environ, {"GH_TOKEN": "token_gh"}, clear=True):
        assert _resolve_token() == "token_gh"

    # Empty strings or whitespace stripped
    assert _resolve_token(github_token="  token_trimmed  \n") == "token_trimmed"

    # Empty string if nothing set or whitespace
    with patch.dict(os.environ, {}, clear=True):
        assert _resolve_token() == ""
        assert _resolve_token(github_token="   ") == ""


def test_fetch_github_release_info_without_token():
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("requests.get") as mock_get,
    ):
        mock_response = MagicMock()
        mock_response.json.return_value = {"tag_name": "v1.0"}
        mock_response.links = {}
        mock_get.return_value = mock_response

        info = _fetch_github_release_info("https://api.github.com/repos/foo/bar")

        assert info == {"tag_name": "v1.0"}
        mock_get.assert_called_once()
        headers = mock_get.call_args[1]["headers"]
        assert "Authorization" not in headers
        assert headers["Accept"] == "application/vnd.github+json"


def test_fetch_github_release_info_with_token_arg():
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {"tag_name": "v1.0"}
        mock_response.links = {}
        mock_get.return_value = mock_response

        info = _fetch_github_release_info(
            "https://api.github.com/repos/foo/bar",
            github_token="secret_token_123",
        )

        assert info == {"tag_name": "v1.0"}
        headers = mock_get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret_token_123"


def test_fetch_github_release_info_with_token_bearer_or_token_prefix():
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.json.return_value = {"tag_name": "v1.0"}
        mock_response.links = {}
        mock_get.return_value = mock_response

        _fetch_github_release_info(
            "https://api.github.com/repos/foo/bar",
            github_token="Bearer custom_bearer_token",
        )
        headers = mock_get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer custom_bearer_token"

        _fetch_github_release_info(
            "https://api.github.com/repos/foo/bar",
            github_token="token ghp_token_prefix",
        )
        headers = mock_get.call_args[1]["headers"]
        assert headers["Authorization"] == "token ghp_token_prefix"


def test_fetch_github_release_info_with_env_var():
    with (
        patch.dict(os.environ, {"GITHUB_TOKEN": "env_secret_token"}, clear=True),
        patch("requests.get") as mock_get,
    ):
        mock_response = MagicMock()
        mock_response.json.return_value = {"tag_name": "v1.0"}
        mock_response.links = {}
        mock_get.return_value = mock_response

        info = _fetch_github_release_info("https://api.github.com/repos/foo/bar")

        assert info == {"tag_name": "v1.0"}
        headers = mock_get.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer env_secret_token"


def test_fetch_github_release_info_pagination_with_token():
    with patch("requests.get") as mock_get:
        resp1 = MagicMock()
        resp1.json.return_value = [{"tag_name": "v0.9"}]
        resp1.links = {"next": {"url": "https://api.github.com/repos/foo/bar/releases?page=2"}}

        resp2 = MagicMock()
        resp2.json.return_value = [{"tag_name": "v1.0"}]
        resp2.links = {}

        mock_get.side_effect = [resp1, resp2]

        info = _fetch_github_release_info(
            "https://api.github.com/repos/foo/bar",
            tag="v1.0",
            github_token="my_tok",
        )

        assert info == {"tag_name": "v1.0"}
        assert mock_get.call_count == 2
        for call in mock_get.call_args_list:
            assert call[1]["headers"]["Authorization"] == "Bearer my_tok"


def test_fetch_github_release_info_request_exception():
    with patch("requests.get", side_effect=requests.exceptions.RequestException("Rate limited")):
        info = _fetch_github_release_info("https://api.github.com/repos/foo/bar")
        assert info is None


def test_download_artifact_bad_scheme():
    with pytest.raises(ValueError, match="Bad download_url"):
        _download_artifact("/tmp/test.bin", "http://insecure.url/file.bin")


def test_download_artifact_existing_file_no_force(tmp_path):
    target = tmp_path / "test.bin"
    target.write_text("existing")

    with patch("urllib.request.urlretrieve") as mock_urlretrieve:
        downloaded = _download_artifact(str(target), "https://example.com/test.bin", force_download=False)
        assert downloaded is False
        mock_urlretrieve.assert_not_called()


def test_download_tester_iso(tmp_path):
    fake_release = {
        "tag_name": "v1.2.3",
        "assets": [
            {
                "name": "ignore.txt",
                "browser_download_url": "https://example.com/ignore.txt",
            },
            {
                "name": "test.iso",
                "browser_download_url": "https://example.com/test.iso",
            },
        ],
    }

    with (
        patch(
            "xemu_pgraph_ci_tools.github._fetch_github_release_info",
            return_value=fake_release,
        ) as mock_fetch,
        patch("xemu_pgraph_ci_tools.github._download_artifact") as mock_download,
    ):
        result = download_tester_iso(
            str(tmp_path),
            tag="v1.2.3",
            github_token="dummy_token",
        )

        expected_iso_path = os.path.join(str(tmp_path), "nxdk_pgraph_tests-v1.2.3.iso")
        assert result == expected_iso_path
        mock_fetch.assert_called_once_with(
            "https://api.github.com/repos/abaire/nxdk_pgraph_tests",
            "v1.2.3",
            github_token="dummy_token",
        )
        mock_download.assert_called_once_with(
            expected_iso_path,
            "https://example.com/test.iso",
        )


def test_download_tester_iso_missing_asset(tmp_path):
    fake_release = {"tag_name": "v1.2.3", "assets": []}
    with patch(
        "xemu_pgraph_ci_tools.github._fetch_github_release_info",
        return_value=fake_release,
    ):
        assert download_tester_iso(str(tmp_path), github_token="dummy") is None


def test_download_xemu_hdd(tmp_path):
    fake_release = {
        "tag_name": "v2.0",
        "assets": [
            {
                "name": "hdd.qcow2.zip",
                "browser_download_url": "https://example.com/hdd.qcow2.zip",
            },
        ],
    }

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("xbox_hdd.qcow2", b"fake_qcow2_data")
    zip_bytes = zip_buffer.getvalue()

    def fake_download_artifact(target_path, *args: Any, **kwargs: Any) -> bool:
        del args, kwargs
        with open(target_path, "wb") as f:
            f.write(zip_bytes)
        return True

    with (
        patch(
            "xemu_pgraph_ci_tools.github._fetch_github_release_info",
            return_value=fake_release,
        ) as mock_fetch,
        patch(
            "xemu_pgraph_ci_tools.github._download_artifact",
            side_effect=fake_download_artifact,
        ),
    ):
        result = download_xemu_hdd(str(tmp_path), github_token="tok_hdd")

        expected_file = os.path.join(str(tmp_path), "xbox_hdd-v2.0.qcow2")
        assert result == expected_file
        assert os.path.exists(expected_file)
        mock_fetch.assert_called_once_with(
            "https://api.github.com/repos/xemu-project/xemu-hdd-image",
            "latest",
            github_token="tok_hdd",
        )


def test_download_xemu_linux(tmp_path):
    fake_release = {
        "tag_name": "0.7.118",
        "assets": [
            {
                "name": "xemu-x86_64.AppImage",
                "browser_download_url": "https://example.com/xemu.AppImage",
            },
        ],
    }

    with (
        patch("platform.system", return_value="Linux"),
        patch("platform.machine", return_value="x86_64"),
        patch(
            "xemu_pgraph_ci_tools.github._fetch_github_release_info",
            return_value=fake_release,
        ) as mock_fetch,
        patch("xemu_pgraph_ci_tools.github._download_artifact", return_value=True),
        patch("os.chmod") as mock_chmod,
    ):
        result = download_xemu(str(tmp_path), tag="0.7.118", github_token="tok_xemu")
        assert result == os.path.join(str(tmp_path), "xemu-x86_64.AppImage")
        mock_fetch.assert_called_once_with(
            "https://api.github.com/repos/xemu-project/xemu",
            "0.7.118",
            github_token="tok_xemu",
        )
        mock_chmod.assert_called_once()


def test_fetch_hw_goldens_with_token(tmp_path):
    with patch("git.Repo.clone_from") as mock_clone:
        fetch_hw_goldens(str(tmp_path), github_token="tok_golden")
        mock_clone.assert_called_once()
        called_url = mock_clone.call_args[0][0]
        assert "x-access-token:tok_golden@" in called_url


def test_fetch_hw_goldens_without_token(tmp_path):
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("git.Repo.clone_from") as mock_clone,
    ):
        fetch_hw_goldens(str(tmp_path))
        mock_clone.assert_called_once_with(
            _HW_GOLDEN_GIT_URL,
            str(tmp_path),
            depth=1,
        )
