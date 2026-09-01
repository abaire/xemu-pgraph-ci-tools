from __future__ import annotations

import os
import platform
import tempfile
import unittest
from unittest.mock import patch

from xemu_pgraph_ci_tools.macos import build_macos_xemu_binary_paths
from xemu_pgraph_ci_tools.runner import (
    _build_emulator_command,
    _generate_xemu_toml,
)


class TestRunner(unittest.TestCase):
    def test_generate_xemu_toml_with_all_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            toml_path = os.path.join(tmpdir, "xemu.toml")
            _generate_xemu_toml(
                toml_path,
                bootrom_path="/path/to/mcpx.bin",
                flashrom_path="/path/to/bios.bin",
                eeprom_path="/path/to/eeprom.bin",
                hdd_path="/path/to/hdd.qcow2",
                memory=128,
                use_vulkan=True,
            )
            assert os.path.isfile(toml_path)
            with open(toml_path, encoding="utf-8") as f:
                content = f.read()

            assert "mem_limit = '128'" in content
            assert "renderer = 'VULKAN'" in content
            assert "bootrom_path = '/path/to/mcpx.bin'" in content
            assert "flashrom_path = '/path/to/bios.bin'" in content
            assert "eeprom_path = '/path/to/eeprom.bin'" in content
            assert "hdd_path = '/path/to/hdd.qcow2'" in content

    def test_generate_xemu_toml_with_empty_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            toml_path = os.path.join(tmpdir, "xemu.toml")
            _generate_xemu_toml(
                toml_path,
                bootrom_path="",
                flashrom_path="/path/to/bios.bin",
                eeprom_path="",
                hdd_path="/path/to/hdd.qcow2",
                memory=64,
                use_vulkan=False,
            )
            assert os.path.isfile(toml_path)
            with open(toml_path, encoding="utf-8") as f:
                content = f.read()

            assert "mem_limit = '64'" in content
            assert "renderer = 'VULKAN'" not in content
            assert "bootrom_path = ''" in content
            assert "eeprom_path = ''" in content
            assert "flashrom_path = '/path/to/bios.bin'" in content

    def test_build_macos_xemu_binary_paths_from_bundle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_dir = os.path.join(tmpdir, "xemu.app")
            macos_dir = os.path.join(app_dir, "Contents", "MacOS")
            lib_dir = os.path.join(app_dir, "Contents", "Libraries", platform.uname().machine)
            res_dir = os.path.join(app_dir, "Contents", "Resources")
            os.makedirs(macos_dir)
            os.makedirs(lib_dir)
            os.makedirs(res_dir)

            binary_path = os.path.join(macos_dir, "xemu")
            with open(binary_path, "w") as f:
                f.write("#!/bin/sh\n")

            with patch.dict(os.environ, {}, clear=True):
                bin_res, config_res = build_macos_xemu_binary_paths(app_dir)
                assert bin_res == binary_path
                assert config_res == res_dir
                assert lib_dir in os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")

    def test_build_emulator_command_linux_appimage(self):
        with patch("platform.system", return_value="Linux"):
            cmd, toml_path = _build_emulator_command("/path/to/xemu.AppImage")
            assert cmd == '"/path/to/xemu.AppImage" -dvd_path {ISO}'
            assert toml_path == "/path/to/xemu.AppImage.home/.local/share/xemu/xemu/xemu.toml"


if __name__ == "__main__":
    unittest.main()
