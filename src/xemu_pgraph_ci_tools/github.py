# ruff: noqa: PLC0415 import should be at top-level of file

import logging
import os
import platform
import zipfile
from typing import Any
from urllib.request import urlcleanup, urlretrieve

import requests

logger = logging.getLogger(__name__)

_HW_GOLDEN_GIT_URL = "https://github.com/abaire/nxdk_pgraph_tests_golden_results.git"


def fetch_hw_goldens(output_dir: str) -> None:
    """Clones the Xbox hardware golden result repo."""
    from git import Repo

    logger.info("Cloning from %s", _HW_GOLDEN_GIT_URL)
    Repo.clone_from(_HW_GOLDEN_GIT_URL, output_dir, depth=1)


def _fetch_github_release_info(api_url: str, tag: str = "latest") -> dict[str, Any] | None:
    full_url = f"{api_url}/releases/latest" if not tag or tag == "latest" else f"{api_url}/releases?per_page=60"

    def fetch_and_filter(url: str):
        try:
            response = requests.get(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=15,
            )
            response.raise_for_status()
            release_info = response.json()
        except requests.exceptions.RequestException:
            logger.exception("Failed to retrieve information from %s", url)
            return None

        if isinstance(release_info, list):
            release_info = _filter_release_info_by_tag(release_info, tag)
        if release_info:
            return release_info

        if not response.links:
            return None

        next_link = response.links.get("next", {}).get("url")
        if not next_link:
            return None
        if "per_page=60" not in next_link:
            next_link = next_link + "&per_page=60"
        return fetch_and_filter(next_link)

    return fetch_and_filter(full_url)


def _download_artifact(
    target_path: str,
    download_url: str,
    artifact_path_override: str | None = None,
    *,
    force_download: bool = False,
) -> bool:
    if os.path.exists(target_path) and not force_download:
        return False

    if artifact_path_override and os.path.exists(artifact_path_override) and not force_download:
        return True

    if not download_url.startswith("https://"):
        logger.error("Download URL '%s' has unexpected scheme", download_url)
        msg = f"Bad download_url '{download_url} - non HTTPS scheme"
        raise ValueError(msg)

    logger.debug("Downloading %s from %s", target_path, download_url)
    if artifact_path_override:
        target_path = artifact_path_override
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    urlretrieve(download_url, target_path)  # noqa: S310
    urlcleanup()
    return True


def _filter_release_info_by_tag(release_infos: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    for info in release_infos:
        if info.get("tag_name") == tag:
            return info
    return None


def download_tester_iso(output_dir: str, tag: str = "latest") -> str | None:
    """Downloads the latest nxdk_pgraph_tests ISO."""
    logger.info("Fetching info on nxdk_pgraph_tests ISO at release tag %s...", tag)
    release_info = _fetch_github_release_info("https://api.github.com/repos/abaire/nxdk_pgraph_tests", tag)
    if not release_info:
        return None

    release_tag = release_info.get("tag_name")
    if not release_tag:
        logger.error("Failed to retrieve release tag from GitHub.")
        return None

    download_url = ""
    for asset in release_info.get("assets", []):
        if not asset.get("name", "").endswith(".iso"):
            continue
        download_url = asset.get("browser_download_url", "")
        break

    if not download_url:
        logger.error("Failed to fetch download URL for latest nxdk_pgraph_tests release")
        return None

    target_file = os.path.join(output_dir, f"nxdk_pgraph_tests-{release_tag}.iso")
    _download_artifact(target_file, download_url)
    return target_file


def download_xemu_hdd(output_dir: str, tag: str = "latest") -> str | None:
    """Downloads the latest xemu_hdd image."""
    logger.info("Fetching info on xemu_hdd at release tag %s...", tag)
    release_info = _fetch_github_release_info("https://api.github.com/repos/xemu-project/xemu-hdd-image", tag)
    if not release_info:
        return None

    release_tag = release_info.get("tag_name")
    if not release_tag:
        logger.error("Failed to retrieve release tag from GitHub.")
        return None

    download_url = ""
    for asset in release_info.get("assets", []):
        if not asset.get("name", "").endswith(".qcow2.zip"):
            continue
        download_url = asset.get("browser_download_url", "")
        break

    if not download_url:
        logger.error("Failed to fetch download URL for latest xemu_hdd release")
        return None

    target_file = os.path.join(output_dir, f"xbox_hdd-{release_tag}.qcow2")
    if os.path.exists(target_file):
        return target_file

    download_file = os.path.join(output_dir, f"xbox_hdd-{release_tag}.qcow2.zip")
    _download_artifact(download_file, download_url)
    with zipfile.ZipFile(download_file, "r") as zip_ref:
        for file_info in zip_ref.infolist():
            if file_info.filename.endswith(".qcow2"):
                zip_ref.extract(file_info, output_dir)
                extracted_path = os.path.join(output_dir, file_info.filename)
                os.rename(extracted_path, target_file)
                return target_file
    return None


def _macos_extract_app(archive_file: str, target_app_bundle: str) -> None:
    """Extracts the xemu.app bundle from the given archive and renames it."""
    app_bundle_directory = os.path.dirname(target_app_bundle)

    try:
        with zipfile.ZipFile(archive_file, "r") as zip_ref:
            os.makedirs(app_bundle_directory, exist_ok=True)

            for file_info in zip_ref.infolist():
                if file_info.filename.startswith("xemu.app/") and not file_info.is_dir():
                    zip_ref.extract(file_info, app_bundle_directory)

            if not os.path.isfile(os.path.join(app_bundle_directory, "xemu.app", "Contents", "MacOS", "xemu")):
                msg = f"xemu archive was downloaded at '{archive_file}' but app bundle could not be extracted"
                raise ValueError(msg)

    except FileNotFoundError:
        logger.exception("Archive not found when extracting xemu app bundle")
        raise
    except zipfile.BadZipFile:
        logger.exception("Invalid zip archive when extracting xemu app bundle")
        raise


def _windows_extract_app(archive_file: str, target_executable: str) -> None:
    """Extracts xemu.exe from the given archive."""
    try:
        with zipfile.ZipFile(archive_file, "r") as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.filename == "xemu.exe":
                    target_dir = os.path.dirname(target_executable)
                    zip_ref.extract(file_info, target_dir)
                    if os.path.basename(target_executable) != "xemu.exe":
                        os.rename(os.path.join(target_dir, "xemu.exe"), target_executable)
                    return

    except FileNotFoundError:
        logger.exception("Archive not found when extracting xemu.exe")
        raise
    except zipfile.BadZipFile:
        logger.exception("Invalid zip archive when extracting xemu.exe")
        raise


def download_xemu(output_dir: str, tag: str = "latest") -> str | None:
    """Downloads the latest xemu release for the current platform."""
    logger.info("Fetching info on xemu at release tag %s...", tag)
    release_info = _fetch_github_release_info("https://api.github.com/repos/xemu-project/xemu", tag)
    if not release_info:
        return None

    release_tag = release_info.get("tag_name")
    if not release_tag:
        logger.error("Failed to retrieve release tag from GitHub.")
        return None

    system = platform.system()
    if system == "Linux":

        def check_asset(asset_name: str) -> bool:
            if not asset_name.startswith("xemu-") or "-dbg-" in asset_name:
                return False
            return asset_name.endswith(".AppImage") and platform.machine() in asset_name
    elif system == "Darwin":

        def check_asset(asset_name: str) -> bool:
            return asset_name == "xemu-macos-universal-release.zip" or asset_name.endswith(
                "-macos-universal-unsigned.zip"
            )
    elif system == "Windows":

        def check_asset(asset_name: str) -> bool:
            if not asset_name.startswith("xemu-win-") or not asset_name.endswith("release.zip"):
                return False
            platform_name = platform.machine()
            if platform_name == "AMD64":
                platform_name = "x86_64"
            return platform_name.lower() in asset_name
    else:
        msg = f"System '{system}' not supported"
        raise NotImplementedError(msg)

    asset_name = ""
    download_url = ""
    for asset in release_info.get("assets", []):
        asset_name = asset.get("name", "")
        if not check_asset(asset_name):
            continue
        download_url = asset.get("browser_download_url", "")
        break

    if not download_url:
        logger.error("Failed to fetch download URL for latest xemu release")
        return None

    if system == "Linux":
        target_file = os.path.join(output_dir, asset_name)
        artifact_path_override = None
    elif system == "Darwin":
        target_file = os.path.join(output_dir, f"xemu-macos-{release_tag}", "xemu.app")
        artifact_path_override = f"{target_file}.zip"
    elif system == "Windows":
        target_file = os.path.join(output_dir, "xemu.exe")
        artifact_path_override = f"{target_file}.zip"
    else:
        msg = f"System '{system}' not supported"
        raise NotImplementedError(msg)

    tag_info_file_path = os.path.join(output_dir, "xemu-tag.info")

    requested_version = release_info.get("tag_name")
    force_download = False
    if not requested_version or not os.path.isfile(tag_info_file_path):
        force_download = True
    else:
        with open(tag_info_file_path) as tag_info_file:
            cached_tag = tag_info_file.readline().strip()
            force_download = cached_tag != requested_version

    was_downloaded = _download_artifact(
        target_file, download_url, artifact_path_override, force_download=force_download
    )

    if was_downloaded:
        if system == "Linux":
            os.chmod(target_file, 0o700)
        elif system == "Darwin" and artifact_path_override:
            _macos_extract_app(artifact_path_override, target_file)
        elif system == "Windows" and artifact_path_override:
            _windows_extract_app(artifact_path_override, target_file)

        if requested_version:
            with open(tag_info_file_path, "w") as tag_info_file:
                tag_info_file.write(str(requested_version))

    return target_file
