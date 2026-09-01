import contextlib
import logging
import os
import platform
import subprocess
from subprocess import CalledProcessError

logger = logging.getLogger(__name__)


def build_macos_xemu_binary_paths(xemu_path: str) -> tuple[str, str]:
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


def get_macos_bundle_identifier(xemu_path: str, *, no_bundle: bool) -> str | None:
    """Returns the bundle identifier for the given xemu.app bundle path."""
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


def set_apple_persistence_ignore_state(macos_bundle_identifier: str, *, ignore: bool | None) -> bool | None:
    """Attempts to suppress app persistent dialog."""
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
