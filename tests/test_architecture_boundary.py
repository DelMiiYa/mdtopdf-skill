"""Architecture boundary tests for standalone mdtopdf-skill v1.0.0."""

from __future__ import annotations

import ast
from pathlib import Path


class TestArchitectureBoundary:
    """Enforce standalone packaging, offline CSP, decoupling, and GUI-free boundaries."""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    FORBIDDEN_TOP_MODULES = frozenset(
        {"mdtopdf_core", "markdown2pdf", "tkinter", "app", "converter", "fitz"}
    )
    FORBIDDEN_CDN_HOSTS = (
        "cdn.jsdelivr.net",
        "unpkg.com",
        "cdnjs.cloudflare.com",
        "fonts.googleapis.com",
    )

    def _iter_py_files(self, *dirs: str) -> list[Path]:
        files: list[Path] = []
        self_path = Path(__file__).resolve()
        for folder_name in dirs:
            folder = self.REPO_ROOT / folder_name
            if folder.exists():
                for py_file in folder.rglob("*.py"):
                    if py_file.resolve() != self_path:
                        files.append(py_file)
        return files

    def test_no_forbidden_imports_in_src_or_tests(self) -> None:
        violations: list[str] = []
        for py_file in self._iter_py_files("src", "tests"):
            tree = ast.parse(py_file.read_text(encoding="utf-8-sig"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        if top in self.FORBIDDEN_TOP_MODULES:
                            rel = py_file.relative_to(self.REPO_ROOT)
                            violations.append(f"{rel}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    if top in self.FORBIDDEN_TOP_MODULES:
                        rel = py_file.relative_to(self.REPO_ROOT)
                        violations.append(f"{rel}: from {node.module} import ...")
        assert not violations, "\n".join(violations)

    def test_no_cdn_urls_in_src_or_skills(self) -> None:
        violations: list[str] = []
        for folder_name in ("src", "skills"):
            folder = self.REPO_ROOT / folder_name
            if not folder.exists():
                continue
            for file_path in folder.rglob("*"):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in {".py", ".js", ".css", ".md", ".html"}:
                    continue
                if "THIRD_PARTY_LICENSES" in file_path.name:
                    continue
                text = file_path.read_text(encoding="utf-8", errors="replace").lower()
                for host in self.FORBIDDEN_CDN_HOSTS:
                    if host in text:
                        rel = file_path.relative_to(self.REPO_ROOT)
                        violations.append(f"{rel}: contains forbidden CDN host {host!r}")
        assert not violations, "\n".join(violations)

    def test_markdown_pipeline_has_no_module_level_themes_import(self) -> None:
        pipeline_path = self.REPO_ROOT / "src" / "mdtopdf_skill" / "markdown_pipeline.py"
        tree = ast.parse(pipeline_path.read_text(encoding="utf-8"), filename=str(pipeline_path))
        for stmt in tree.body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    assert "themes" not in alias.name.split("."), (
                        f"Module-level themes import forbidden: {alias.name}"
                    )
            elif isinstance(stmt, ast.ImportFrom) and stmt.module:
                assert "themes" not in stmt.module.split("."), (
                    f"Module-level themes import forbidden: {stmt.module}"
                )

    def test_markdown_pipeline_enforces_csp_nonce_and_katex_link_stylesheet(self) -> None:
        pipeline_path = self.REPO_ROOT / "src" / "mdtopdf_skill" / "markdown_pipeline.py"
        text = pipeline_path.read_text(encoding="utf-8")
        assert "secrets.token_hex" in text
        assert "script-src 'nonce-" in text
        assert '<link rel="stylesheet" href=' in text
        assert "katex.min.css" in text

    def test_no_hardcoded_theme_specs_outside_themes_and_models(self) -> None:
        for rel_path in (
            "src/mdtopdf_skill/cli.py",
            "src/mdtopdf_skill/renderer.py",
            "src/mdtopdf_skill/autofit.py",
        ):
            file_path = self.REPO_ROOT / rel_path
            text = file_path.read_text(encoding="utf-8")
            assert "ThemeSpec(" not in text, f"ThemeSpec hardcoded in {rel_path}"
            assert "THEMES =" not in text, f"THEMES mapping hardcoded in {rel_path}"
