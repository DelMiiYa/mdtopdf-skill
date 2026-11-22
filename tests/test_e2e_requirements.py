"""Phase 2+ browser-free E2E requirements, asset integrity, and governance tests.

This module intentionally has NO ``@pytest.mark.e2e`` marker and zero imports of
unimplemented Phase 2-4 submodules (``cli``, ``themes``, ``sanitizer``,
``markdown_pipeline``, ``cdp_browser``, ``autofit``, ``renderer``) or legacy
``mdtopdf_core`` so it passes immediately during parallel Phase 2 development.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.resources
import importlib.util
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import PIL.Image
import pymupdf
import pytest

import mdtopdf_skill
from mdtopdf_skill.frontmatter import parse_frontmatter
from mdtopdf_skill.models import (
    EXIT_BAD_ARGS,
    EXIT_EMPTY_PDF,
    EXIT_NO_BROWSER,
    EXIT_OK,
    EXIT_RENDER_ERROR,
    EXIT_STRICT_ISSUE,
    MARGIN_PRESETS,
    PAGE_SIZE_PRESETS,
    THEME_NAMES,
    AutoFitOutcome,
    ConversionOptions,
    ConversionResult,
    PageGeometry,
    PdfInspection,
    compute_page_geometry,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
ASSETS_DIR = REPO_ROOT / "src" / "mdtopdf_skill" / "assets"
LICENSES_PATH = ASSETS_DIR / "THIRD_PARTY_LICENSES.md"
DECISIONS_PATH = REPO_ROOT / "docs" / "DECISIONS.md"
README_PATH = REPO_ROOT / "README.md"
TIERS_TEST_PATH = REPO_ROOT / "tests" / "test_e2e_tiers.py"

_UNIMPLEMENTED_V1_SUBMODULES = frozenset(
    {
        "cli",
        "themes",
        "sanitizer",
        "markdown_pipeline",
        "cdp_browser",
        "autofit",
        "renderer",
    }
)

_EXPECTED_TIER_CLASSES = frozenset(
    {
        "TestTier1FeatureCoverage",
        "TestTier2BoundaryAndErrorCases",
        "TestTier3CrossFeatureCombinations",
        "TestTier4RealWorldScenarios",
    }
)


def _fallback_parse_pyproject(raw_text: str) -> dict[str, Any]:
    """Minimal Python 3.10 fallback TOML parser for pyproject.toml verification."""
    data: dict[str, Any] = {}
    section_stack: list[str] = []
    lines = raw_text.splitlines()
    idx = 0

    def _set_nested(keys: list[str], key: str, value: Any) -> None:
        target: dict[str, Any] = data
        for part in keys:
            nxt = target.setdefault(part, {})
            if isinstance(nxt, dict):
                target = nxt
        target[key] = value

    def _parse_scalar(token: str) -> Any:
        cleaned = token.strip()
        if cleaned.startswith('"') and cleaned.endswith('"'):
            return cleaned[1:-1]
        if cleaned.startswith("'") and cleaned.endswith("'"):
            return cleaned[1:-1]
        if cleaned == "true":
            return True
        if cleaned == "false":
            return False
        if cleaned.startswith("[") and cleaned.endswith("]"):
            inner = cleaned[1:-1].strip()
            if not inner:
                return []
            return [
                _parse_scalar(item)
                for item in re.findall(r'"[^"]*"|\'[^\']*\'|[^,\s]+', inner)
            ]
        return cleaned

    while idx < len(lines):
        line = lines[idx].split("#", 1)[0].strip()
        idx += 1
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip()
            section_stack = [part.strip() for part in header.split(".")]
            continue
        if "=" not in line:
            continue
        key_part, val_part = line.split("=", 1)
        key = key_part.strip().strip('"').strip("'")
        val = val_part.strip()
        while val.startswith("[") and not val.endswith("]") and idx < len(lines):
            next_line = lines[idx].split("#", 1)[0].strip()
            idx += 1
            val = f"{val} {next_line}"
        _set_nested(section_stack, key, _parse_scalar(val))
    return data


def _load_pyproject() -> dict[str, Any]:
    """Load pyproject.toml safely on Python 3.10+ (using tomllib on 3.11+ with fallback)."""
    raw_bytes = PYPROJECT_PATH.read_bytes()
    if sys.version_info >= (3, 11):
        import tomllib

        loaded = tomllib.loads(raw_bytes.decode("utf-8"))
        assert isinstance(loaded, dict)
        return loaded
    return _fallback_parse_pyproject(raw_bytes.decode("utf-8"))


def _probe_host_browser_paths() -> Path | None:
    """Probe MDTOPDF_BROWSER, standard OS Edge/Chrome/Chromium paths, and PATH."""
    env_browser = os.environ.get("MDTOPDF_BROWSER")
    if env_browser:
        candidate = Path(env_browser)
        if candidate.is_file():
            return candidate

    candidates: list[Path] = []
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        candidates.extend(
            [
                Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
                Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
            ]
        )
        if local_app_data:
            candidates.extend(
                [
                    Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe",
                    Path(local_app_data) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                    Path(local_app_data) / "Chromium" / "Application" / "chrome.exe",
                ]
            )
        which_names = ("msedge", "chrome", "chromium", "chromium-browser")
    else:
        candidates.extend(
            [
                Path("/usr/bin/google-chrome"),
                Path("/usr/bin/google-chrome-stable"),
                Path("/usr/bin/chromium"),
                Path("/usr/bin/chromium-browser"),
                Path("/usr/bin/microsoft-edge"),
                Path("/snap/bin/chromium"),
                Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ]
        )
        which_names = ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for name in which_names:
        resolved = shutil.which(name)
        if resolved and Path(resolved).is_file():
            return Path(resolved)
    return None


def _is_ci_or_strict_e2e() -> bool:
    return (
        os.environ.get("CI") == "true"
        or os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("MDTOPDF_E2E_REQUIRE") == "1"
    )


class TestPyprojectAndPackaging:
    """Verify standalone v2.0.0 packaging metadata, dependencies, markers, and PDF/PNG tools."""

    def test_pyproject_metadata_and_zero_legacy_deps(self) -> None:
        raw_text = PYPROJECT_PATH.read_text(encoding="utf-8")
        assert "mdtopdf_core" not in raw_text
        assert "markdown2pdf" not in raw_text.lower()
        assert importlib.util.find_spec("mdtopdf_core") is None

        config = _load_pyproject()
        project = config["project"]
        assert project["name"] == "mdtopdf-skill"
        assert project["version"] == "2.0.0"
        assert project["requires-python"] == ">=3.10"
        assert project["scripts"]["mdtopdf"] == "mdtopdf_skill.cli:main"

    def test_runtime_and_dev_dependencies(self) -> None:
        config = _load_pyproject()
        runtime_deps = "\n".join(config["project"]["dependencies"]).lower()
        for pkg in ("markdown-it-py", "pymupdf", "pyyaml", "pygments"):
            assert pkg in runtime_deps
        assert "pillow" not in runtime_deps
        assert "bandit" not in runtime_deps
        assert len(config["project"]["dependencies"]) == 4

        dev_deps = "\n".join(config["project"]["optional-dependencies"]["dev"]).lower()
        for pkg in ("pillow", "pytest", "pytest-cov", "ruff", "mypy", "bandit"):
            assert pkg in dev_deps

    def test_package_data_and_e2e_marker_registered(self) -> None:
        config = _load_pyproject()
        pkg_data = config["tool"]["setuptools"]["package-data"]["mdtopdf_skill"]
        assert "assets/*" in pkg_data
        assert "assets/katex/*" in pkg_data
        assert "assets/katex/fonts/*.woff2" in pkg_data

        markers = config["tool"]["pytest"]["ini_options"]["markers"]
        assert any(m.startswith("e2e:") for m in markers)

    def test_pymupdf_and_pillow_importable(self) -> None:
        assert hasattr(pymupdf, "open")
        assert hasattr(PIL.Image, "open")


class TestBundledOfflineAssets:
    """Verify all 23 vendored offline Mermaid/KaTeX assets and SHA-256 checksums."""

    def test_expected_asset_layout_and_woff2_only_fonts(self) -> None:
        assert ASSETS_DIR.is_dir()
        assert LICENSES_PATH.is_file()
        assert (ASSETS_DIR / "mermaid.min.js").is_file()
        assert (ASSETS_DIR / "katex" / "katex.min.js").is_file()
        assert (ASSETS_DIR / "katex" / "katex.min.css").is_file()

        fonts_dir = ASSETS_DIR / "katex" / "fonts"
        assert fonts_dir.is_dir()
        font_files = sorted(p for p in fonts_dir.iterdir() if p.is_file())
        assert len(font_files) == 20
        assert all(p.suffix == ".woff2" for p in font_files)

        all_files = [p for p in ASSETS_DIR.rglob("*") if p.is_file()]
        forbidden_exts = {".ttf", ".woff", ".eot", ".map"}
        assert not any(p.suffix.lower() in forbidden_exts for p in all_files)

        pkg_assets = importlib.resources.files("mdtopdf_skill") / "assets"
        assert (pkg_assets / "mermaid.min.js").is_file()
        assert (pkg_assets / "katex" / "katex.min.js").is_file()
        assert (pkg_assets / "katex" / "katex.min.css").is_file()

    def test_all_23_sha256_checksums_match_third_party_licenses(self) -> None:
        licenses_text = LICENSES_PATH.read_text(encoding="utf-8")
        row_re = re.compile(
            r"^\|\s*`([^`]+)`\s*\|\s*([0-9,]+)\s*\|\s*`([0-9a-f]{64})`\s*\|$",
            flags=re.MULTILINE,
        )
        rows = row_re.findall(licenses_text)
        assert len(rows) == 23

        for rel_path, size_str, expected_sha256 in rows:
            asset_path = ASSETS_DIR / rel_path
            assert asset_path.is_file(), f"Missing vendored asset: {rel_path}"
            raw_bytes = asset_path.read_bytes()
            expected_size = int(size_str.replace(",", ""))
            assert len(raw_bytes) == expected_size, f"Size mismatch for {rel_path}"
            actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
            assert actual_sha256 == expected_sha256, f"SHA-256 mismatch for {rel_path}"

    def test_katex_css_is_offline_and_uses_woff2_only(self) -> None:
        css_text = (ASSETS_DIR / "katex" / "katex.min.css").read_text(encoding="utf-8")
        assert "http://" not in css_text.lower()
        assert "https://" not in css_text.lower()
        woff2_refs = re.findall(r"fonts/[A-Za-z0-9_-]+\.woff2", css_text)
        assert len(woff2_refs) >= 20


class TestV1CoreContracts:
    """Verify models.py and frontmatter.py v2.0.0 baseline contracts."""

    def test_version_and_exit_code_constants(self) -> None:
        assert mdtopdf_skill.__version__ == "2.0.0"
        assert (
            EXIT_OK,
            EXIT_BAD_ARGS,
            EXIT_NO_BROWSER,
            EXIT_RENDER_ERROR,
            EXIT_EMPTY_PDF,
            EXIT_STRICT_ISSUE,
        ) == (0, 2, 3, 4, 5, 6)

    def test_theme_margin_and_page_size_presets(self) -> None:
        assert frozenset({"classic", "paper"}) == THEME_NAMES
        assert set(MARGIN_PRESETS.keys()) == {"compact", "standard", "comfortable", "wide"}
        assert set(PAGE_SIZE_PRESETS.keys()) == {
            "a4",
            "a4 portrait",
            "a4 landscape",
            "letter",
            "letter portrait",
            "letter landscape",
            "legal",
            "legal portrait",
            "legal landscape",
        }

    def test_dataclass_fields_and_compute_page_geometry(self) -> None:
        opts = ConversionOptions()
        assert opts.theme == "classic"
        assert opts.auto_fit is True
        assert opts.strict is False
        assert opts.preview_dpi == 144

        res = ConversionResult(
            output_pdf_path="out.pdf", theme="classic", page_count=1, embedded_images=0
        )
        assert res.layout_issues == []
        assert res.mermaid_errors == []
        assert res.math_errors == []
        assert res.adjustments_applied == []
        assert res.preview_images == []

        assert hasattr(PageGeometry, "__dataclass_fields__")
        assert hasattr(PdfInspection, "__dataclass_fields__")
        assert hasattr(AutoFitOutcome, "__dataclass_fields__")
        assert "is_trailing_orphan" in PdfInspection.__dataclass_fields__
        assert "last_page_fill_ratio" in AutoFitOutcome.__dataclass_fields__

        geom = compute_page_geometry("A4 portrait", "15mm 16mm")
        assert geom.page_size == "A4 portrait"
        assert geom.page_width_mm == 210.0
        assert geom.page_height_mm == 297.0
        assert geom.printable_width_px > 600.0
        assert geom.printable_height_px > 900.0

        with pytest.raises(ValueError):
            compute_page_geometry("50mm 50mm", "30mm")

    def test_frontmatter_bom_and_aliases(self) -> None:
        raw_md = (
            "\ufeff---\r\n"
            "theme: paper\r\n"
            "font-size: 11.5\r\n"
            "line-height: 1.6\r\n"
            "spacing-scale: 0.9\r\n"
            "page-size: Letter landscape\r\n"
            "accent: '#0369a1'\r\n"
            "page-numbers: false\r\n"
            "---\r\n"
            "# Document Body\n"
        )
        fm, body = parse_frontmatter(raw_md)
        assert fm.warnings == []
        assert fm.theme == "paper"
        assert fm.font_size_pt == 11.5
        assert fm.line_height == 1.6
        assert fm.spacing_scale == 0.9
        assert fm.page_size == "Letter landscape"
        assert fm.accent_color == "#0369a1"
        assert fm.page_numbers is False
        assert body == "# Document Body\n"


class TestBrowserDiscoveryReadiness:
    """Verify browser discovery docs, env vars, and readiness without failing early."""

    def test_docs_document_cdp_env_vars_and_exit_3(self) -> None:
        decisions_text = DECISIONS_PATH.read_text(encoding="utf-8")
        readme_text = README_PATH.read_text(encoding="utf-8")
        combined = f"{decisions_text}\n{readme_text}"
        assert "MDTOPDF_BROWSER" in combined
        assert "MDTOPDF_TIMEOUT" in combined
        assert "127.0.0.1" in combined
        assert "EXIT_NO_BROWSER" in combined

    def test_env_override_if_set_and_ci_browser_presence(self) -> None:
        env_browser = os.environ.get("MDTOPDF_BROWSER")
        if env_browser:
            assert Path(env_browser).is_file(), (
                f"MDTOPDF_BROWSER points to missing file: {env_browser}"
            )

        found = _probe_host_browser_paths()
        if _is_ci_or_strict_e2e():
            assert found is not None, (
                "No Edge/Chrome/Chromium executable found under CI=true / MDTOPDF_E2E_REQUIRE=1"
            )

    def test_cdp_browser_contract_when_implemented(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if importlib.util.find_spec("mdtopdf_skill.cdp_browser") is None:
            return
        cdp_browser = importlib.import_module("mdtopdf_skill.cdp_browser")
        monkeypatch.setenv("MDTOPDF_BROWSER", str(REPO_ROOT / "__nonexistent_browser_bin__"))
        try:
            resolved = cdp_browser.find_browser_executable()
            assert Path(resolved).is_file()
        except FileNotFoundError as exc:
            assert "MDTOPDF_BROWSER" in str(exc)


class TestE2ETiersGovernance:
    """Enforce opaque-box isolation, markers, and AST hygiene on tests/test_e2e_tiers.py."""

    def _parse_tiers_ast(self) -> tuple[ast.Module, str]:
        assert TIERS_TEST_PATH.is_file(), f"Missing E2E tiers suite: {TIERS_TEST_PATH}"
        source = TIERS_TEST_PATH.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(TIERS_TEST_PATH)), source

    def test_no_forbidden_imports_in_e2e_tiers(self) -> None:
        tree, _ = self._parse_tiers_ast()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_mod = alias.name.split(".", 1)[0]
                    assert root_mod not in {"mdtopdf_core", "fitz"}, (
                        f"Forbidden import in test_e2e_tiers.py: {alias.name}"
                    )
                    if alias.name.startswith("mdtopdf_skill."):
                        submod = alias.name.split(".", 2)[1]
                        assert submod not in _UNIMPLEMENTED_V1_SUBMODULES, (
                            f"Opaque-box violation: {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                root_mod = mod.split(".", 1)[0]
                assert root_mod not in {"mdtopdf_core", "fitz"}, (
                    f"Forbidden import-from in test_e2e_tiers.py: {mod}"
                )
                if mod == "mdtopdf_skill":
                    for alias in node.names:
                        assert alias.name not in _UNIMPLEMENTED_V1_SUBMODULES, (
                            f"Opaque-box violation: from mdtopdf_skill import {alias.name}"
                        )
                elif mod.startswith("mdtopdf_skill."):
                    submod = mod.split(".", 2)[1]
                    assert submod not in _UNIMPLEMENTED_V1_SUBMODULES, (
                        f"Opaque-box violation: from {mod} import ..."
                    )

    def test_e2e_markers_and_all_four_tier_classes_present(self) -> None:
        tree, source = self._parse_tiers_ast()
        has_module_pytestmark = any(
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets)
            and "pytest.mark.e2e" in ast.get_source_segment(source, node.value)  # type: ignore[operator]
            for node in tree.body
        )
        assert has_module_pytestmark, "test_e2e_tiers.py must define pytestmark = pytest.mark.e2e"

        class_defs = {
            node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
        }
        missing_classes = _EXPECTED_TIER_CLASSES - set(class_defs.keys())
        assert not missing_classes, f"Missing tier classes: {sorted(missing_classes)}"

        for cls_name in _EXPECTED_TIER_CLASSES:
            cls_node = class_defs[cls_name]
            deco_texts = [
                ast.get_source_segment(source, dec) or "" for dec in cls_node.decorator_list
            ]
            assert any("pytest.mark.e2e" in d for d in deco_texts), (
                f"{cls_name} must be decorated with @pytest.mark.e2e"
            )

    def test_subprocess_tmp_path_and_no_unconditional_skip_governance(self) -> None:
        tree, source = self._parse_tiers_ast()
        assert "subprocess.run" in source

        func_defs: dict[str, ast.FunctionDef] = {}
        test_methods: list[ast.FunctionDef] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                func_defs[node.name] = node
            elif isinstance(node, ast.ClassDef) and node.name in _EXPECTED_TIER_CLASSES:
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name.startswith("test_"):
                        test_methods.append(item)

        assert len(test_methods) >= 50, "Expected comprehensive test methods across all 4 tiers"
        assert any("tmp_path" in [arg.arg for arg in m.args.args] for m in test_methods)

        # require_v1_cli must fail (never pytest.skip) if CLI is broken
        req_cli = func_defs.get("require_v1_cli")
        assert req_cli is not None
        req_cli_src = ast.get_source_segment(source, req_cli) or ""
        assert "pytest.fail" in req_cli_src
        assert "pytest.skip" not in req_cli_src

        # No bare unconditional pytest.skip inside any test_* method
        for method in test_methods:
            method_src = ast.get_source_segment(source, method) or ""
            assert "pytest.skip" not in method_src, (
                f"Test method {method.name} must not call pytest.skip directly"
            )
