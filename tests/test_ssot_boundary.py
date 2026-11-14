"""SSOT boundary tests verifying that conversion logic is not duplicated in this repo.

Per §2 and §8.3 of docs/HANDOFF.md:
- mdtopdf_core is the single source of truth (SSOT).
- This repo must never vendor, fork, or duplicate core functions (render_pdf,
  build_css, _sanitize_html, etc.).
- This repo must never import Tk 'app' or legacy top-level 'converter'.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


class TestSsotBoundary:
    """Enforce the single source of truth (SSOT) boundary."""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    FORBIDDEN_CORE_DEFINITIONS = [
        re.compile(r"^\s*def\s+render_pdf\s*\(", re.MULTILINE),
        re.compile(r"^\s*def\s+build_css\s*\(", re.MULTILINE),
        re.compile(r"^\s*def\s+_sanitize_html\s*\(", re.MULTILINE),
        re.compile(r"^\s*class\s+HtmlSanitizer\b", re.MULTILINE),
        re.compile(r"^\s*def\s+_resolve_image\s*\(", re.MULTILINE),
        re.compile(r"^\s*def\s+_read_image_cached\s*\(", re.MULTILINE),
        re.compile(r"^\s*def\s+render_pdf_pages_to_pil\s*\(", re.MULTILINE),
    ]
    FORBIDDEN_IMPORT_PATTERNS = [
        re.compile(r"^\s*from\s+app\b", re.MULTILINE),
        re.compile(r"^\s*import\s+app\b", re.MULTILINE),
        re.compile(r"^\s*from\s+converter\b", re.MULTILINE),
        re.compile(r"^\s*import\s+converter\b", re.MULTILINE),
    ]

    def _collect_repo_py_files(self) -> list[Path]:
        """Collect all .py files in src/, skills/, and tests/, excluding this boundary test."""
        py_files: list[Path] = []
        for folder_name in ("src", "skills", "tests"):
            folder = self.REPO_ROOT / folder_name
            if folder.exists():
                for py_file in folder.rglob("*.py"):
                    if py_file.resolve() != Path(__file__).resolve():
                        py_files.append(py_file)
        return py_files

    def test_no_duplicated_core_functions_in_repo(self) -> None:
        """Verify no files in src/, skills/, or tests/ define core conversion functions."""
        py_files = self._collect_repo_py_files()
        assert len(py_files) > 0, "Expected Python files to inspect."

        violations: list[str] = []
        for py_file in py_files:
            content = py_file.read_text(encoding="utf-8-sig")
            for pattern in self.FORBIDDEN_CORE_DEFINITIONS:
                match = pattern.search(content)
                if match:
                    rel_path = py_file.relative_to(self.REPO_ROOT)
                    violations.append(
                        f"Found forbidden core definition '{match.group(0).strip()}' in {rel_path}"
                    )

        assert not violations, "\n".join(violations)

    def test_no_forbidden_app_or_converter_imports(self) -> None:
        """Verify no files import legacy Tk 'app' or top-level 'converter'."""
        py_files = self._collect_repo_py_files()
        violations: list[str] = []

        for py_file in py_files:
            content = py_file.read_text(encoding="utf-8-sig")
            for pattern in self.FORBIDDEN_IMPORT_PATTERNS:
                match = pattern.search(content)
                if match:
                    rel_path = py_file.relative_to(self.REPO_ROOT)
                    violations.append(
                        f"Found forbidden import '{match.group(0).strip()}' in {rel_path}"
                    )

        assert not violations, "\n".join(violations)

    def test_cli_imports_only_core_and_stdlib(self) -> None:
        """Verify cli.py only imports from mdtopdf_core, mdtopdf_skill, or stdlib."""
        cli_path = self.REPO_ROOT / "src" / "mdtopdf_skill" / "cli.py"
        tree = ast.parse(cli_path.read_text(encoding="utf-8"), filename=str(cli_path))

        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_module = alias.name.split(".")[0]
                    imported_modules.add(top_module)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_module = node.module.split(".")[0]
                imported_modules.add(top_module)

        stdlib_and_allowed = {
            "__future__",
            "argparse",
            "contextlib",
            "json",
            "math",
            "re",
            "sys",
            "tempfile",
            "collections",
            "pathlib",
            "typing",
            "mdtopdf_core",
            "mdtopdf_skill",
        }

        disallowed = imported_modules - stdlib_and_allowed
        assert (
            not disallowed
        ), f"cli.py contains unauthorized external imports: {disallowed}"
