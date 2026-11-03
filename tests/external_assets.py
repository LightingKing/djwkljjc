"""Locate optional, untracked client evidence independently of checkout layout.

LJJC_TEST_ASSET_ROOT points to the directory containing analysis/ and runtime/.
An individual LJJC_* setting overrides that root. Missing external fixtures
skip only their integration tests; repository-owned fixtures remain required.
"""
import os
from pathlib import Path
import unittest


def external_path(variable, relative):
    root = Path(os.environ.get("LJJC_TEST_ASSET_ROOT", Path(__file__).resolve().parents[3]))
    return Path(os.environ.get(variable, root / relative)).expanduser().resolve()


def require_file(path, variable):
    if not path.is_file():
        raise unittest.SkipTest(f"external fixture unavailable: {path}; set {variable} or LJJC_TEST_ASSET_ROOT")
    return path
