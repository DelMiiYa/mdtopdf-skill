"""Scaffold smoke tests verifying package and upstream core imports."""

from __future__ import annotations

import mdtopdf_core

import mdtopdf_skill
from mdtopdf_skill.cli import main


def test_scaffold_imports() -> None:
    """Verify mdtopdf_skill and upstream mdtopdf_core import cleanly."""
    assert mdtopdf_skill.__version__ == "0.2.0"
    assert callable(mdtopdf_core.render_pdf)
    assert callable(main)
