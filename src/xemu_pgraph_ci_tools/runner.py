# ruff: noqa: T201, S310

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import glob
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from shutil import SameFileError
from subprocess import CalledProcessError
from time import sleep
from typing import TYPE_CHECKING, Any
from urllib.request import urlcleanup, urlretrieve

import nxdk_pgraph_test_runner
import requests
from nxdk_pgraph_test_repacker import ensure_extract_xiso, extract_config, repack_config
from nxdk_pgraph_test_runner import Config
from nxdk_pgraph_test_runner.emulator_output import EmulatorOutput
from nxdk_pgraph_test_runner.host_profile import HostProfile
from nxdk_pgraph_test_runner.runner import get_output_directory

from xemu_pgraph_ci_tools.models import TestResultsManifest
from xemu_pgraph_ci_tools.schema import emit_json_schema

if TYPE_CHECKING:
    from collections.abc import Collection

logger = logging.getLogger(__name__)

if sys.platform == "win32":
    import threading
    import time

    import win32con
    import win32gui

    class AbortDialogHandler:
        def __init__(self):
            self.stop_event = threading.Event()
            self.dialog_found = False

        def find_and_click_abort(self):
            dialog_title = "Microsoft Visual C++ Runtime Library"

            while not self.stop_event.is_set():
                hwnd = win32gui.FindWindow(None, dialog_title)
                if hwnd:

                    def enum_child_proc(child_hwnd, lparam):
                        del lparam
                        button_text = win32gui.GetWindowText(child_hwnd)
                        if "abort" in button_text.lower():
                            win32gui.SendMessage(child_hwnd, win32con.BM_CLICK, 0, 0)
                            self.dialog_found = True
                        return True

                    win32gui.EnumChildWindows(hwnd, enum_child_proc, None)

                    if self.dialog_found:
                        time.sleep(2)

                time.sleep(0.2)

        def start(self):
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.find_and_click_abort, daemon=True)
            self.thread.start()

        def stop(self):
            self.stop_event.set()
else:

    class AbortDialogHandler:
        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass


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
    urlretrieve(download_url, target_path)
    urlcleanup()
    return True


def _filter_release_info_by_tag(release_infos: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
    for info in release_infos:
        if info.get("tag_name") == tag:
            return info
    return None


def _download_tester_iso(output_dir: str, tag: str = "latest") -> str | None:
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


def _download_xemu_hdd(output_dir: str, tag: str = "latest") -> str | None:
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


def _download_xemu(output_dir: str, tag: str = "latest") -> str | None:
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
                tag_info_file.write(requested_version)

    return target_file


def _ensure_path(path: str) -> str:
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    return path


def _ensure_cache_path(cache_path: str) -> str:
    if not cache_path:
        msg = "cache_path may not be empty"
        raise ValueError(msg)
    return _ensure_path(cache_path)


def _ensure_results_path(results_path: str) -> str:
    if not results_path:
        msg = "results_path may not be empty"
        raise ValueError(msg)
    return _ensure_path(results_path)


def _generate_xemu_toml(
    file_path: str,
    bootrom_path: str,
    flashrom_path: str,
    eeprom_path: str,
    hdd_path: str,
    *,
    memory: int = 64,
    use_vulkan: bool = False,
) -> None:
    if not isinstance(memory, int) or memory <= 0:
        msg = f"Invalid memory configuration: {memory}. Must be an integer > 0."
        raise ValueError(msg)

    bootrom_path = os.path.abspath(os.path.expanduser(bootrom_path)).replace("\\", "/") if bootrom_path else ""
    flashrom_path = os.path.abspath(os.path.expanduser(flashrom_path)).replace("\\", "/") if flashrom_path else ""
    eeprom_path = os.path.abspath(os.path.expanduser(eeprom_path)).replace("\\", "/") if eeprom_path else ""
    hdd_path = os.path.abspath(os.path.expanduser(hdd_path)).replace("\\", "/") if hdd_path else ""

    content = [
        "[general]",
        "show_welcome = false",
        "skip_boot_anim = true",
        "",
        "[general.updates]",
        "check = false",
        "",
        "[net]",
        "enable = true",
        "",
        "[sys]",
        f"mem_limit = '{memory}'",
        "",
        "[sys.files]",
        f"bootrom_path = '{bootrom_path}'",
        f"flashrom_path = '{flashrom_path}'",
        f"eeprom_path = '{eeprom_path}'",
        f"hdd_path = '{hdd_path}'",
    ]

    if use_vulkan:
        content.extend(["", "[display]", "renderer = 'VULKAN'"])

    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as outfile:
        outfile.write("\n".join(content))


def _build_macos_xemu_binary_paths(xemu_path: str) -> tuple[str, str]:
    """Configures DYLD_FALLBACK_LIBRARY_PATH and returns (xemu_binary, resources_path) for macOS."""
    contents_path = None
    config_path = os.path.dirname(xemu_path)

    if xemu_path.endswith(".app") and os.path.isdir(xemu_path):
        contents_path = os.path.join(xemu_path, "Contents")
        xemu_binary = os.path.join(contents_path, "MacOS", "xemu")
        if os.path.isfile(xemu_binary):
            os.chmod(xemu_binary, 0o700)
            xemu_path = xemu_binary
        config_path = os.path.join(contents_path, "Resources")
    elif "Contents/MacOS" in xemu_path:
        contents_path = os.path.dirname(os.path.dirname(xemu_path))
        config_path = os.path.join(contents_path, "Resources")
    else:
        # Check nearby dist/xemu.app or xemu.app
        candidates = [
            os.path.join(os.path.dirname(xemu_path), "..", "dist", "xemu.app", "Contents"),
            os.path.join(os.path.dirname(xemu_path), "xemu.app", "Contents"),
            os.path.join(os.path.dirname(xemu_path), "dist", "xemu.app", "Contents"),
        ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                contents_path = candidate
                break

    if contents_path and os.path.isdir(contents_path):
        library_path = os.path.join(contents_path, "Libraries", platform.uname().machine)
        if os.path.isdir(library_path):
            existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
            if library_path not in existing.split(":"):
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{library_path}:{existing}" if existing else library_path
                logger.debug("Set DYLD_FALLBACK_LIBRARY_PATH to %s", os.environ["DYLD_FALLBACK_LIBRARY_PATH"])

    if "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ or not os.environ["DYLD_FALLBACK_LIBRARY_PATH"]:
        arch = platform.uname().machine
        for cache_candidate in [
            "cache",
            os.path.join(os.path.dirname(xemu_path), "cache"),
            os.path.join(os.getcwd(), "cache"),
        ]:
            if os.path.isdir(cache_candidate):
                for root, _, _ in os.walk(cache_candidate):
                    if root.endswith(os.path.join("Contents", "Libraries", arch)):
                        existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
                        if root not in existing.split(":"):
                            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = f"{root}:{existing}" if existing else root
                            logger.debug("Added cache fallback DYLD_FALLBACK_LIBRARY_PATH: %s", root)
                        break

    return xemu_path, config_path


def _build_emulator_command(
    xemu_path: str,
    *,
    no_bundle: bool = False,
    custom_toml_path: str | None = None,
    enable_serial: bool = False,
    snapshot: bool = False,
) -> tuple[str, str]:
    portable_mode_config_path = os.path.dirname(xemu_path)

    system = platform.system()
    if system == "Darwin":
        if not no_bundle:
            xemu_path, portable_mode_config_path = _build_macos_xemu_binary_paths(xemu_path)
    elif system == "Linux":
        if xemu_path.endswith("AppImage"):
            # AppImages need to have the xemu.toml file within their home dir.
            portable_mode_config_path = os.path.join(f"{xemu_path}.home", ".local", "share", "xemu", "xemu")
    elif system == "Windows":
        pass
    else:
        msg = f"Platform {system} not supported."
        raise NotImplementedError(msg)

    cmd = f'"{xemu_path}" -dvd_path {{ISO}}'
    if snapshot:
        cmd += " -snapshot"
    if enable_serial:
        cmd += " -device lpc47m157 -serial stdio"
    if custom_toml_path:
        cmd += f' -config_path "{custom_toml_path}"'
        toml_path = custom_toml_path
    else:
        toml_path = os.path.join(portable_mode_config_path, "xemu.toml")

    return cmd, toml_path


def _determine_output_directory(results_path: str, emulator_command: str, *, is_vulkan: bool) -> str | None:
    command = Config(emulator_command=emulator_command + " -display none").build_emulator_command(
        "__this_file_does_not_exist"
    )
    stderr = ""
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=1, env=os.environ)
        stderr = result.stderr or ""
    except subprocess.TimeoutExpired as err:
        stderr = err.stderr.decode() if isinstance(err.stderr, bytes) else (err.stderr or "")
        sleep(0.5)
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.decode() if isinstance(err.stderr, bytes) else (err.stderr or "")
        logger.exception("Failed to run emulator command to determine output directory: %s", stderr)
        raise

    emulator_output = EmulatorOutput.parse(stdout=[], stderr=stderr.split("\n"))
    output_directory = get_output_directory(emulator_output.emulator_version, HostProfile(), is_vulkan=is_vulkan)

    return os.path.join(results_path, output_directory)


def _get_macos_bundle_identifier(xemu_path: str, *, no_bundle: bool) -> str | None:
    if no_bundle or platform.system() != "Darwin":
        return None

    app_path = None
    if xemu_path.endswith(".app") and os.path.isdir(xemu_path):
        app_path = xemu_path
    elif "Contents/MacOS" in xemu_path:
        app_path = os.path.dirname(os.path.dirname(os.path.dirname(xemu_path)))
    else:
        candidates = [
            os.path.join(os.path.dirname(xemu_path), "..", "dist", "xemu.app"),
            os.path.join(os.path.dirname(xemu_path), "xemu.app"),
            os.path.join(os.path.dirname(xemu_path), "dist", "xemu.app"),
        ]
        for candidate in candidates:
            if os.path.isdir(candidate):
                app_path = candidate
                break

    if not app_path or not os.path.exists(app_path):
        return None

    try:
        command = ["mdls", "-name", "kMDItemCFBundleIdentifier", "-r", app_path]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except (CalledProcessError, OSError):
        return None
    else:
        bundle_id = result.stdout.strip()
        return bundle_id if bundle_id and bundle_id != "(null)" else None


def _set_apple_persistence_ignore_state(macos_bundle_identifier: str, *, ignore: bool | None) -> bool | None:
    command = [
        "defaults",
        "read",
        macos_bundle_identifier,
        "ApplePersistenceIgnoreState",
    ]

    current_value = None
    with contextlib.suppress(CalledProcessError):
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        current_value = result.stdout.strip().startswith("1")

    if current_value != ignore:
        if ignore is None:
            command = [
                "defaults",
                "delete",
                macos_bundle_identifier,
                "ApplePersistenceIgnoreState",
            ]
        else:
            command = [
                "defaults",
                "write",
                macos_bundle_identifier,
                "ApplePersistenceIgnoreState",
                "-bool",
                "true" if ignore else "false",
            ]
        with contextlib.suppress(CalledProcessError):
            subprocess.run(command, capture_output=True, text=True, check=True)

    return current_value


def run(
    iso_path: str,
    work_path: str,
    inputs_path: str,
    results_path: str,
    xemu_path: str,
    hdd_path: str,
    *,
    bootrom_path: str | None = None,
    flashrom_path: str | None = None,
    eeprom_path: str | None = None,
    memory: int = 64,
    enable_serial: bool = False,
    overwrite_existing_outputs: bool,
    no_bundle: bool = False,
    use_vulkan: bool = False,
    just_suites: Collection[str] | None = None,
    custom_toml_path: str | None = None,
    timeout: int = 0,
    stall_timeout: int = 0,
    snapshot: bool = False,
):
    if not isinstance(memory, int) or memory <= 0:
        msg = f"Invalid memory configuration: {memory}. Must be an integer > 0."
        raise ValueError(msg)

    emulator_command, toml_path = _build_emulator_command(
        xemu_path,
        no_bundle=no_bundle,
        custom_toml_path=custom_toml_path,
        enable_serial=enable_serial,
        snapshot=snapshot,
    )
    if not emulator_command:
        return 1

    bootrom_file = os.path.join(inputs_path, "mcpx.bin") if bootrom_path is None else bootrom_path
    flashrom_file = os.path.join(inputs_path, "bios.bin") if flashrom_path is None else flashrom_path
    eeprom_file = os.path.join(inputs_path, "eeprom.bin") if eeprom_path is None else eeprom_path

    _generate_xemu_toml(
        toml_path,
        bootrom_path=bootrom_file,
        flashrom_path=flashrom_file,
        eeprom_path=eeprom_file,
        hdd_path=hdd_path,
        memory=memory,
        use_vulkan=use_vulkan,
    )

    output_directory = _determine_output_directory(
        results_path, emulator_command=emulator_command, is_vulkan=use_vulkan
    )
    if output_directory and not overwrite_existing_outputs and os.path.isdir(output_directory):
        logger.error("Output directory %s already exists, exiting", output_directory)
        return 200

    test_failure_retries = 2

    config = Config(
        work_dir=work_path,
        output_dir=results_path,
        emulator_command=emulator_command,
        iso_path=iso_path,
        ftp_ip="127.0.0.1",
        ftp_ip_override="10.0.2.2",
        xbox_artifact_path=r"c:\nxdk_pgraph_tests",
        test_failure_retries=test_failure_retries,
        timeout_seconds=timeout,
        stall_timeout_seconds=stall_timeout,
        network_config={"config_automatic": True},
        suite_allowlist=just_suites,
    )

    macos_bundle_identifier = _get_macos_bundle_identifier(xemu_path, no_bundle=no_bundle)
    original_ignore_value: bool | None = None
    if macos_bundle_identifier:
        original_ignore_value = _set_apple_persistence_ignore_state(macos_bundle_identifier, ignore=True)

    handler: AbortDialogHandler | None = None
    if sys.platform == "win32":
        handler = AbortDialogHandler()
        handler.start()

    ret = nxdk_pgraph_test_runner.entrypoint(config)

    if handler:
        handler.stop()

    if output_directory and os.path.isdir(output_directory):
        with open(os.path.join(output_directory, "renderer.json"), "w") as outfile:
            json.dump({"vulkan": use_vulkan}, outfile)
        with open(os.path.join(output_directory, "runner.json"), "w") as outfile:
            json.dump(
                {
                    "iso": os.path.basename(iso_path),
                    "test_failure_retries": test_failure_retries,
                    "suite_allowlist": just_suites,
                },
                outfile,
            )

        manifest_path = os.path.join(output_directory, "results.json")
        if os.path.isfile(manifest_path):
            with open(manifest_path) as f:
                manifest = json.load(f)
            for state in ("passed", "failed", "flaky"):
                for test_info in manifest.get(state, {}).values():
                    if "artifacts" in test_info:
                        test_info["artifacts"] = [os.path.basename(p) for p in test_info["artifacts"]]
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2, sort_keys=True)

    if macos_bundle_identifier:
        _set_apple_persistence_ignore_state(macos_bundle_identifier, ignore=original_ignore_value)

    return ret


def _prepare_sharded_iso(iso_path: str, shard_index: int, shard_count: int, output_iso_path: str) -> bool:
    extract_xiso = ensure_extract_xiso()
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        if not extract_config(iso_path, config_path, extract_xiso):
            logger.error("Failed to extract config from ISO: %s", iso_path)
            return False

        with open(config_path) as f:
            config_data = json.load(f)

        if "settings" not in config_data:
            config_data["settings"] = {}
        config_data["settings"]["sharding"] = {
            "index": shard_index,
            "count": shard_count,
        }

        updated_config_path = os.path.join(tmpdir, "updated_config.json")
        with open(updated_config_path, "w") as f:
            json.dump(config_data, f)

        if not repack_config(iso_path, output_iso_path, updated_config_path, extract_xiso):
            logger.error("Failed to repack ISO for shard %d", shard_index)
            return False

    return True


def _run_shard(
    shard_index: int,
    shard_count: int,
    temp_path: str,
    iso_path: str,
    hdd_path: str,
    mcpx_path: str | None,
    bios_path: str | None,
    eeprom_path: str | None,
    xemu_path: str,
    results_path: str,
    *,
    memory: int = 64,
    enable_serial: bool = False,
    overwrite_existing_outputs: bool,
    no_bundle: bool,
    use_vulkan: bool,
    just_suites: Collection[str] | None,
    timeout: int = 0,
    stall_timeout: int = 0,
    snapshot: bool = False,
) -> int:
    inputs_path = os.path.join(temp_path, "inputs")
    os.makedirs(inputs_path, exist_ok=True)

    if shard_count > 1:
        effective_iso_path = os.path.join(inputs_path, f"test_runner_shard_{shard_index}.iso")
        if not _prepare_sharded_iso(iso_path, shard_index, shard_count, effective_iso_path):
            return 1
    else:
        effective_iso_path = iso_path

    bootrom_target: str | None = mcpx_path
    if mcpx_path:
        if not os.path.isfile(mcpx_path):
            logger.error("Invalid MCPX path '%s'", mcpx_path)
            return 1
        bootrom_target = os.path.join(inputs_path, "mcpx.bin")
        with contextlib.suppress(SameFileError):
            shutil.copy(mcpx_path, bootrom_target)

    flashrom_target: str | None = bios_path
    if bios_path:
        if not os.path.isfile(bios_path):
            logger.error("Invalid BIOS path '%s'", bios_path)
            return 1
        flashrom_target = os.path.join(inputs_path, "bios.bin")
        with contextlib.suppress(SameFileError):
            shutil.copy(bios_path, flashrom_target)

    eeprom_target: str | None = eeprom_path
    if eeprom_path:
        if not os.path.isfile(eeprom_path):
            logger.error("Invalid EEPROM path '%s'", eeprom_path)
            return 1
        eeprom_target = os.path.join(inputs_path, "eeprom.bin")
        with contextlib.suppress(SameFileError):
            shutil.copy(eeprom_path, eeprom_target)

    hdd_copy = os.path.join(inputs_path, "test_runner_hdd.qcow2")
    with contextlib.suppress(SameFileError):
        shutil.copy(hdd_path, hdd_copy)

    return run(
        iso_path=effective_iso_path,
        work_path=temp_path,
        inputs_path=inputs_path,
        results_path=results_path,
        xemu_path=xemu_path,
        hdd_path=hdd_copy,
        bootrom_path=bootrom_target,
        flashrom_path=flashrom_target,
        eeprom_path=eeprom_target,
        memory=memory,
        enable_serial=enable_serial,
        overwrite_existing_outputs=overwrite_existing_outputs,
        no_bundle=no_bundle,
        use_vulkan=use_vulkan,
        just_suites=just_suites,
        custom_toml_path=os.path.join(inputs_path, "xemu.toml"),
        timeout=timeout,
        stall_timeout=stall_timeout,
        snapshot=snapshot,
    )


def _merge_shard_results(shard_results_paths: list[str], final_results_path: str) -> None:
    merged_passed = {}
    merged_failed = {}
    merged_flaky = {}
    merged_missing = []
    output_dir_rel = None

    for shard_path in shard_results_paths:
        manifest_path = None
        for root, _, files in os.walk(shard_path):
            if "results.json" in files:
                manifest_path = os.path.join(root, "results.json")
                break

        if not manifest_path:
            logger.warning("No results.json found in %s", shard_path)
            continue

        if not output_dir_rel:
            output_dir_rel = os.path.relpath(os.path.dirname(manifest_path), shard_path)

        with open(manifest_path) as f:
            manifest = json.load(f)

        merged_passed.update(manifest.get("passed", {}))
        merged_failed.update(manifest.get("failed", {}))
        merged_flaky.update(manifest.get("flaky", {}))
        merged_missing.extend(manifest.get("missing_artifacts", []))

        src_dir = os.path.dirname(manifest_path)
        dest_dir = os.path.join(final_results_path, output_dir_rel)
        os.makedirs(dest_dir, exist_ok=True)

        for item in os.listdir(src_dir):
            src_item = os.path.join(src_dir, item)
            dest_item = os.path.join(dest_dir, item)
            if os.path.isdir(src_item):
                if not os.path.exists(dest_item):
                    shutil.copytree(src_item, dest_item)
                else:
                    for suite_item in os.listdir(src_item):
                        shutil.copy2(
                            os.path.join(src_item, suite_item),
                            os.path.join(dest_item, suite_item),
                        )
            elif item in (
                "machine_info.txt",
                "renderer.json",
                "runner.json",
            ) and not os.path.exists(dest_item):
                shutil.copy2(src_item, dest_item)

    if output_dir_rel:
        final_manifest_path = os.path.join(final_results_path, output_dir_rel, "results.json")
        merged_manifest: dict[str, Any] = {
            "passed": merged_passed,
            "failed": merged_failed,
            "flaky": merged_flaky,
        }
        if merged_missing:
            merged_manifest["missing_artifacts"] = merged_missing

        with open(final_manifest_path, "w") as f:
            json.dump(merged_manifest, f, indent=2, sort_keys=True)


def _extract_info_from_xemu_toml(toml_path: str) -> tuple[str, str] | None:
    toml_path = os.path.abspath(os.expanduser(toml_path))
    if os.path.isdir(toml_path):
        toml_path = os.path.join(toml_path, "xemu.toml")
    if not os.path.isfile(toml_path):
        logger.error("No xemu toml file found at '%s'", toml_path)
        return None

    with open(toml_path, "rb") as infile:
        data = tomllib.load(infile)

    files = data.get("sys", {}).get("files", {})
    return files.get("bootrom_path"), files.get("flashrom_path")


def _process_arguments_and_run() -> int:
    parser = argparse.ArgumentParser(description="Headless PGraph test runner for xemu.")
    parser.add_argument(
        "--verbose",
        "-v",
        help="Enables verbose logging information",
        action="store_true",
    )
    parser.add_argument("--iso", "-I", help="Path to the nxdk_pgraph_tests.iso xiso file.")
    parser.add_argument(
        "--pgraph-tag",
        default="latest",
        help="Release tag to use when downloading ISO.",
    )
    parser.add_argument("--xemu", "-X", help="Path to the xemu executable.")
    parser.add_argument(
        "--xemu-tag",
        default="latest",
        help="Release tag to use when downloading xemu from GitHub.",
    )
    parser.add_argument("--hdd", "-H", help="Path to xemu hard disk image to use.")
    parser.add_argument(
        "--bios",
        "-B",
        default="inputs/bios.bin",
        help="Path to Xbox BIOS image to use.",
    )
    parser.add_argument(
        "--mcpx",
        "-M",
        default="inputs/mcpx.bin",
        help="Path to Xbox MCPX boot ROM image to use. Pass '' for no MCPX.",
    )
    parser.add_argument("--eeprom", "-E", default="", help="Path to Xbox EEPROM image to use.")
    parser.add_argument(
        "--memory",
        "--mem",
        type=int,
        default=64,
        help="Xbox RAM size in MB (e.g. 64, 128).",
    )
    parser.add_argument(
        "--enable-serial",
        "--serial-output",
        action="store_true",
        help="Attach LPC debug UART and debugcon to stdio.",
    )
    parser.add_argument("--cache-path", "-C", default="cache", help="Path to persistent cache area.")
    parser.add_argument("--temp-path", help="Temporary path used during execution of tests")
    parser.add_argument("--results-path", "-R", default="results", help="Path to store results.")
    parser.add_argument("--overwrite-existing-outputs", "-f", action="store_true")
    parser.add_argument(
        "--no-bundle",
        action="store_true",
        help="Suppress attempt to set DYLD_FALLBACK_LIBRARY_PATH on macOS.",
    )
    parser.add_argument("--use-vulkan", action="store_true")
    parser.add_argument("--just-suites", nargs="+")
    parser.add_argument(
        "--toml",
        "--import-install",
        "-T",
        help="Import bios and mcpx from an existing xemu install",
        metavar="xemu_toml_path",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        default=None,
        help="Index of this shard to run (0-based).",
    )
    parser.add_argument("--shard-count", "-S", type=int, default=0, help="Total number of shards.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        help="Total timeout in seconds for each emulator run.",
    )
    parser.add_argument(
        "--stall-timeout",
        type=int,
        default=0,
        help="Inactivity timeout in seconds without FTP updates before killing emulator.",
    )
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Run emulator in snapshot mode (discards HDD changes on exit).",
    )
    parser.add_argument(
        "--emit-schema",
        "--schema",
        action="store_true",
        help="Emit JSON Schema for results.json output artifact and exit.",
    )

    args = parser.parse_args()

    if args.emit_schema:
        print(emit_json_schema(TestResultsManifest))
        return 0

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger().setLevel(log_level)

    if args.memory <= 0:
        logger.error("Memory size must be greater than 0")
        return 1

    if args.shard_index is not None:
        if args.shard_index < 0:
            logger.error("shard-index must be >= 0")
            return 1
        if args.shard_count <= args.shard_index:
            logger.error(
                "shard-count (%d) must be greater than shard-index (%d)",
                args.shard_count,
                args.shard_index,
            )
            return 1
    elif args.shard_count < 0:
        logger.error("shard-count must be >= 0")
        return 1

    cache_path = _ensure_cache_path(args.cache_path)
    results_path = _ensure_results_path(args.results_path)

    xemu = os.path.abspath(os.expanduser(args.xemu)) if args.xemu else _download_xemu(cache_path, args.xemu_tag)
    if not xemu:
        logger.error("Failed to download or locate xemu")
        return 1
    if not os.path.exists(xemu):
        logger.error("Invalid xemu path '%s'", xemu)
        return 1

    if not args.overwrite_existing_outputs and not args.just_suites:
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_toml = os.path.join(temp_dir, "xemu.toml")
                _generate_xemu_toml(
                    temp_toml,
                    bootrom_path=args.mcpx if args.mcpx else "",
                    flashrom_path=args.bios if args.bios else "",
                    eeprom_path=args.eeprom if args.eeprom else "",
                    hdd_path=args.hdd if args.hdd else os.path.join(temp_dir, "hdd.img"),
                    memory=args.memory,
                    use_vulkan=args.use_vulkan,
                )
                emulator_command, _ = _build_emulator_command(
                    xemu,
                    no_bundle=args.no_bundle,
                    enable_serial=args.enable_serial,
                    custom_toml_path=temp_toml,
                )
                if emulator_command:
                    output_directory = _determine_output_directory(
                        results_path,
                        emulator_command=emulator_command,
                        is_vulkan=args.use_vulkan,
                    )

                    if output_directory:
                        existing_summaries = glob.glob(os.path.join(output_directory, "*", "summary.json"))
                        if existing_summaries:
                            logger.warning(
                                "Found %d existing summary.json files in %s. Skipping execution. Use --overwrite-existing-outputs to force run.",
                                len(existing_summaries),
                                output_directory,
                            )
                            return 0
        except Exception:
            logger.exception("Failed to check for existing results, assuming none exist")

    iso = os.path.abspath(os.expanduser(args.iso)) if args.iso else _download_tester_iso(cache_path, args.pgraph_tag)
    if not iso or not os.path.isfile(iso):
        logger.error("Invalid ISO path '%s'", iso)
        return 1

    hdd = os.path.abspath(os.expanduser(args.hdd)) if args.hdd else _download_xemu_hdd(cache_path)
    if not hdd or not os.path.isfile(hdd):
        logger.error("Invalid xemu_hdd path")
        return 1

    if args.toml:
        result = _extract_info_from_xemu_toml(args.toml)
        if not result:
            logger.error("Failed to extract mcpx and bios from xemu toml at '%s'", args.toml)
            return 1
        args.mcpx, args.bios = result

    def _copy_inputs_and_run(temp_path: str, *, overwrite_existing_outputs: bool) -> int:
        if args.shard_index is not None or args.shard_count <= 1:
            shard_index = 0 if args.shard_index is None else args.shard_index
            shard_count = 1 if args.shard_index is None else args.shard_count
            return _run_shard(
                shard_index,
                shard_count,
                temp_path,
                iso,
                hdd,
                args.mcpx,
                args.bios,
                args.eeprom,
                xemu,
                results_path,
                memory=args.memory,
                enable_serial=args.enable_serial,
                overwrite_existing_outputs=overwrite_existing_outputs,
                no_bundle=args.no_bundle,
                use_vulkan=args.use_vulkan,
                just_suites=args.just_suites,
                timeout=args.timeout,
                stall_timeout=args.stall_timeout,
                snapshot=args.snapshot,
            )

        futures = []
        shard_paths = []
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.shard_count) as executor:
            for i in range(args.shard_count):
                shard_temp_path = os.path.join(temp_path, f"shard_{i}")
                os.makedirs(shard_temp_path, exist_ok=True)
                shard_results_path = os.path.join(shard_temp_path, "results")
                shard_paths.append(shard_results_path)

                futures.append(
                    executor.submit(
                        _run_shard,
                        i,
                        args.shard_count,
                        shard_temp_path,
                        iso,
                        hdd,
                        args.mcpx,
                        args.bios,
                        args.eeprom,
                        xemu,
                        shard_results_path,
                        memory=args.memory,
                        enable_serial=args.enable_serial,
                        overwrite_existing_outputs=True,
                        no_bundle=args.no_bundle,
                        use_vulkan=args.use_vulkan,
                        just_suites=args.just_suites,
                        timeout=args.timeout,
                        stall_timeout=args.stall_timeout,
                        snapshot=args.snapshot,
                    )
                )

            for future in concurrent.futures.as_completed(futures):
                ret = future.result()
                if ret != 0:
                    logger.error("Shard failed with exit code %d, aborting all shards.", ret)
                    for f in futures:
                        f.cancel()
                    return ret

        _merge_shard_results(shard_paths, results_path)
        return 0

    if args.temp_path:
        return _copy_inputs_and_run(
            _ensure_path(args.temp_path),
            overwrite_existing_outputs=args.overwrite_existing_outputs,
        )

    with tempfile.TemporaryDirectory() as temp_path:
        return _copy_inputs_and_run(
            _ensure_path(temp_path),
            overwrite_existing_outputs=args.overwrite_existing_outputs,
        )


def main() -> int:
    return _process_arguments_and_run()


def merge_main() -> int:
    parser = argparse.ArgumentParser(description="Merge multiple shard results into a single directory.")
    parser.add_argument(
        "--inputs",
        "-i",
        nargs="+",
        required=False,
        help="List of shard results directories.",
    )
    parser.add_argument("--output-dir", "-o", required=False, help="Directory to store merged results.")
    parser.add_argument(
        "--emit-schema",
        "--schema",
        action="store_true",
        help="Emit JSON Schema for results.json output artifact and exit.",
    )

    args = parser.parse_args()

    if args.emit_schema:
        print(emit_json_schema(TestResultsManifest))
        return 0

    if not args.inputs or not args.output_dir:
        parser.error("the following arguments are required: --inputs, --output-dir")

    _merge_shard_results(args.inputs, args.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
