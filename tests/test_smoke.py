"""Smoke tests verifying standalone v2.0.0 package exports and decoupling."""

from __future__ import annotations

import importlib
import importlib.util

import pytest

import mdtopdf_skill
from mdtopdf_skill import THEMES, find_browser_executable, render_pdf
from mdtopdf_skill.cli import main


def test_scaffold_imports() -> None:
    """Verify mdtopdf_skill v2.0.0 exports and absence of legacy mdtopdf_core."""
    assert mdtopdf_skill.__version__ == "2.0.0"
    assert len(THEMES) == 2
    assert callable(render_pdf)
    assert callable(find_browser_executable)
    assert callable(main)
    assert importlib.util.find_spec("mdtopdf_core") is None
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("mdtopdf_core")
