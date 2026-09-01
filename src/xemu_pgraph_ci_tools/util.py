import os


def ensure_path(path: str) -> str:
    """Creates a directory if it doesn't exist already."""
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(path, exist_ok=True)
    return path


def ensure_cache_path(cache_path: str) -> str:
    """Creates a cache directory if it doesn't exist already."""
    if not cache_path:
        msg = "cache_path may not be empty"
        raise ValueError(msg)
    return ensure_path(cache_path)


def ensure_results_path(results_path: str) -> str:
    """Creates a results directory if it doesn't exist already."""
    if not results_path:
        msg = "results_path may not be empty"
        raise ValueError(msg)
    return ensure_path(results_path)
