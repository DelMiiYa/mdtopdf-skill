"""Opaque-box 4-Tier E2E test suite for mdtopdf-skill v2.0.0.

All tests invoke the real CLI via subprocess (``python -m mdtopdf_skill.cli`` or
the installed ``mdtopdf`` console script) and verify observable outputs only:
process exit codes, ``--json`` schemas, human stdout/stderr, PDF structure via
``pymupdf``, and 144 DPI preview PNGs via ``Pillow``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import PIL.Image
import pymupdf
import pytest

from mdtopdf_skill.models import (
    EXIT_BAD_ARGS,
    EXIT_NO_BROWSER,
    EXIT_OK,
    EXIT_RENDER_ERROR,
    EXIT_STRICT_ISSUE,
)

pytestmark = pytest.mark.e2e

SUCCESS_JSON_KEYS: frozenset[str] = frozenset(
    {
        "ok",
        "output",
        "theme",
        "pages",
        "embedded_images",
        "suppressed_images",
        "warnings",
        "layout_issues",
        "mermaid_errors",
        "math_errors",
        "adjustments_applied",
        "last_page_fill_ratio",
        "preview_images",
        "version",
        "elapsed_seconds",
    }
)

ALL_THEME_NAMES: tuple[str, ...] = (
    "classic",
    "paper",
)

_MM_TO_PT = 72.0 / 25.4


@dataclass(frozen=True)
class CliRunResult:
    """Captured result of a subprocess CLI invocation."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class PdfInspectionSummary:
    """Observable PDF metrics extracted via PyMuPDF."""

    page_count: int
    page_sizes_pt: list[tuple[float, float]]
    per_page_text: list[str]
    full_text: str
    image_count: int
    drawing_count: int


def _entry_point_path() -> Path:
    exe_name = "mdtopdf.exe" if sys.platform == "win32" else "mdtopdf"
    venv_candidate = Path(sys.executable).resolve().parent / exe_name
    if venv_candidate.is_file():
        return venv_candidate
    which_candidate = shutil.which("mdtopdf")
    if which_candidate:
        return Path(which_candidate)
    return venv_candidate


def run_cli(
    args: Sequence[str],
    *,
    stdin_text: str | None = None,
    cwd: Path | None = None,
    env_overrides: Mapping[str, str | None] | None = None,
    timeout: float = 120.0,
    use_entry_point: bool = False,
) -> CliRunResult:
    """Invoke mdtopdf CLI in an isolated child process."""
    cmd = (
        [str(_entry_point_path()), *args]
        if use_entry_point
        else [sys.executable, "-m", "mdtopdf_skill.cli", *args]
    )
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_overrides:
        for key, val in env_overrides.items():
            if val is None:
                env.pop(key, None)
            else:
                env[key] = val

    proc = subprocess.run(
        cmd,
        input=stdin_text,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=timeout,
    )
    return CliRunResult(returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)


def _discover_standard_browser() -> Path | None:
    """Check standard Edge/Chrome/Chromium locations on the host OS."""
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
        found = shutil.which(name)
        if found and Path(found).is_file():
            return Path(found)
    return None


def _discover_host_browser() -> Path | None:
    env_browser = os.environ.get("MDTOPDF_BROWSER")
    if env_browser:
        candidate = Path(env_browser)
        if candidate.is_file():
            return candidate
    return _discover_standard_browser()


def _is_ci_or_strict_e2e() -> bool:
    return (
        os.environ.get("CI") == "true"
        or os.environ.get("GITHUB_ACTIONS") == "true"
        or os.environ.get("MDTOPDF_E2E_REQUIRE") == "1"
    )


@pytest.fixture(scope="session")
def require_v1_cli() -> None:
    """Fail immediately if the v2.0.0 CLI is unavailable or broken."""
    result = run_cli(["--version"])
    if result.returncode != EXIT_OK or "mdtopdf-skill 2.0.0" not in result.stdout:
        pytest.fail(
            "v2.0.0 CLI unavailable: "
            f"returncode={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
        )


@pytest.fixture(scope="session")
def require_browser(require_v1_cli: None) -> Path:
    """Return a compatible browser binary; fail in CI/strict mode if missing."""
    browser = _discover_host_browser()
    if browser is None:
        if _is_ci_or_strict_e2e():
            pytest.fail("No Edge/Chrome/Chromium binary found under CI/strict E2E mode.")
        pytest.skip(
            "No Edge/Chrome/Chromium binary installed locally; "
            "set MDTOPDF_BROWSER to execute browser E2E tests."
        )
    return browser


def inspect_pdf(pdf_path: Path) -> PdfInspectionSummary:
    """Validate and inspect a generated PDF using PyMuPDF."""
    assert pdf_path.is_file(), f"Missing output PDF: {pdf_path}"
    raw_bytes = pdf_path.read_bytes()
    assert len(raw_bytes) > 0, f"Empty PDF file: {pdf_path}"
    assert raw_bytes.startswith(b"%PDF-"), f"Invalid PDF header in {pdf_path}"

    with pymupdf.open(str(pdf_path)) as doc:  # type: ignore[no-untyped-call]
        page_count = int(doc.page_count)
        assert page_count >= 1
        page_sizes_pt: list[tuple[float, float]] = []
        per_page_text: list[str] = []
        image_count = 0
        drawing_count = 0
        for page in doc:
            page_sizes_pt.append((float(page.rect.width), float(page.rect.height)))
            text = str(page.get_text("text"))
            per_page_text.append(text)
            imgs: list[Any] = page.get_images(full=True)
            image_count += len(imgs)
            drawings: list[Any] = page.get_drawings()
            drawing_count += len(drawings)

    return PdfInspectionSummary(
        page_count=page_count,
        page_sizes_pt=page_sizes_pt,
        per_page_text=per_page_text,
        full_text="\n".join(per_page_text),
        image_count=image_count,
        drawing_count=drawing_count,
    )


def assert_page_size_mm(
    summary: PdfInspectionSummary,
    width_mm: float,
    height_mm: float,
    tol_pt: float = 3.0,
) -> None:
    """Verify every page in the PDF matches the expected physical mm dimensions."""
    expected_w_pt = width_mm * _MM_TO_PT
    expected_h_pt = height_mm * _MM_TO_PT
    for idx, (actual_w, actual_h) in enumerate(summary.page_sizes_pt):
        assert abs(actual_w - expected_w_pt) <= tol_pt, (
            f"Page {idx} width {actual_w:.2f}pt != expected {expected_w_pt:.2f}pt"
        )
        assert abs(actual_h - expected_h_pt) <= tol_pt, (
            f"Page {idx} height {actual_h:.2f}pt != expected {expected_h_pt:.2f}pt"
        )


def inspect_preview_pngs(preview_dir: Path, expected_pages: int) -> list[tuple[int, int]]:
    """Verify 144 DPI preview PNGs exist, are valid PNGs, and contain non-blank pixels."""
    assert preview_dir.is_dir(), f"Missing preview dir: {preview_dir}"
    found = sorted(preview_dir.glob("page_*.png"))
    expected_names = [f"page_{i:03d}.png" for i in range(1, expected_pages + 1)]
    assert [p.name for p in found] == expected_names

    dimensions: list[tuple[int, int]] = []
    for png_path in found:
        with PIL.Image.open(png_path) as img:
            assert img.format == "PNG"
            w, h = img.size
            assert w > 800 and h > 800, f"Unexpectedly small 144 DPI PNG: {(w, h)}"
            min_px, max_px = img.convert("L").getextrema()
            assert isinstance(min_px, (int, float)) and isinstance(max_px, (int, float))
            assert min_px < max_px, f"Blank preview PNG detected: {png_path}"
            dimensions.append((w, h))
    return dimensions


def create_test_png(
    path: Path,
    width: int = 240,
    height: int = 120,
    color: tuple[int, int, int] = (20, 90, 180),
) -> Path:
    """Generate a real PNG file inside tmp_path via Pillow."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = PIL.Image.new("RGB", (width, height), color)
    for x in range(0, width, 4):
        for y in range(0, min(20, height)):
            img.putpixel((x, y), (240, 240, 240))
    img.save(path, format="PNG")
    return path


def _validate_diagnostic_lists(data: dict[str, Any]) -> None:
    assert isinstance(data["suppressed_images"], list)
    assert all(isinstance(x, str) for x in data["suppressed_images"])
    assert isinstance(data["warnings"], list)
    assert all(isinstance(x, str) for x in data["warnings"])
    assert isinstance(data["preview_images"], list)
    assert all(isinstance(x, str) for x in data["preview_images"])

    assert isinstance(data["layout_issues"], list)
    for issue in data["layout_issues"]:
        assert isinstance(issue, dict)
        assert issue["kind"] in {"table_overflow", "trailing_orphan_page", "wide_element"}
        assert isinstance(issue["detail"], str)
        assert isinstance(issue["suggestion"], str)
        if "table_index" in issue:
            assert isinstance(issue["table_index"], int)
        if "overflow_px" in issue:
            assert isinstance(issue["overflow_px"], (int, float))

    assert isinstance(data["mermaid_errors"], list)
    for err in data["mermaid_errors"]:
        assert isinstance(err, dict)
        assert set(err.keys()) == {"index", "message", "snippet"}
        assert isinstance(err["index"], int)
        assert isinstance(err["message"], str)
        assert isinstance(err["snippet"], str)

    assert isinstance(data["math_errors"], list)
    for err in data["math_errors"]:
        assert isinstance(err, dict)
        assert set(err.keys()) == {"index", "display_mode", "message", "tex"}
        assert isinstance(err["index"], int)
        assert isinstance(err["display_mode"], bool)
        assert isinstance(err["message"], str)
        assert isinstance(err["tex"], str)

    assert isinstance(data["adjustments_applied"], list)
    for adj in data["adjustments_applied"]:
        assert isinstance(adj, dict)
        assert set(adj.keys()) == {"step", "detail"}
        assert isinstance(adj["step"], str)
        assert isinstance(adj["detail"], str)


def assert_success_json(stdout: str, *, expected_theme: str | None = None) -> dict[str, Any]:
    """Assert v2.0.0 Conversion Success JSON schema."""
    data: dict[str, Any] = json.loads(stdout.strip())
    assert "core_version" not in data
    assert set(data.keys()) == SUCCESS_JSON_KEYS
    assert data["ok"] is True
    assert data["version"] == "2.0.0"
    assert isinstance(data["output"], str) and len(data["output"]) > 0
    assert isinstance(data["theme"], str)
    if expected_theme is not None:
        assert data["theme"] == expected_theme
    assert isinstance(data["pages"], int) and data["pages"] >= 1
    assert isinstance(data["embedded_images"], int) and data["embedded_images"] >= 0
    assert isinstance(data["last_page_fill_ratio"], (int, float))
    assert 0.0 <= float(data["last_page_fill_ratio"]) <= 1.0
    assert isinstance(data["elapsed_seconds"], (int, float))
    assert float(data["elapsed_seconds"]) > 0.0
    _validate_diagnostic_lists(data)
    return data


def assert_strict_failure_json(stdout: str) -> dict[str, Any]:
    """Assert v2.0.0 Strict Failure JSON schema (exit 6)."""
    data: dict[str, Any] = json.loads(stdout.strip())
    assert "core_version" not in data
    assert set(data.keys()) == SUCCESS_JSON_KEYS | {"code", "error"}
    assert data["ok"] is False
    assert data["code"] == EXIT_STRICT_ISSUE
    assert isinstance(data["error"], str) and len(data["error"]) > 0
    assert data["version"] == "2.0.0"
    assert isinstance(data["pages"], int) and data["pages"] >= 1
    _validate_diagnostic_lists(data)
    return data


def assert_error_json(stdout: str, expected_code: int) -> dict[str, Any]:
    """Assert v2.0.0 Standard Error JSON schema (exit 2..5)."""
    data: dict[str, Any] = json.loads(stdout.strip())
    assert "core_version" not in data
    assert set(data.keys()) == {"ok", "error", "code"}
    assert data["ok"] is False
    assert data["code"] == expected_code
    assert isinstance(data["error"], str) and len(data["error"]) > 0
    return data


def _calibrate_orphan_markdown(
    tmp_path: Path,
    *,
    prefix_frontmatter: str = "",
    extra_cli_args: Sequence[str] = (),
) -> tuple[Path, dict[str, Any]]:
    """Find a paragraph count producing a 2-page trailing_orphan_page under --no-auto-fit."""
    for count in range(32, 72, 2):
        candidate = tmp_path / f"orphan_probe_{count}.md"
        out_pdf = tmp_path / f"orphan_probe_{count}.pdf"
        paragraphs = "\n\n".join(
            f"Paragraph {i}: Structured technical documentation line for pagination calibration."
            for i in range(1, count + 1)
        )
        candidate.write_text(
            f"{prefix_frontmatter}# Calibration\n\n{paragraphs}\n", encoding="utf-8"
        )
        res = run_cli(
            [
                str(candidate),
                "-o",
                str(out_pdf),
                "--no-auto-fit",
                "--json",
                *extra_cli_args,
            ]
        )
        if res.returncode == EXIT_OK:
            data = assert_success_json(res.stdout)
            if data["pages"] == 2 and any(
                issue["kind"] == "trailing_orphan_page" for issue in data["layout_issues"]
            ):
                return candidate, data
    pytest.fail("Could not calibrate a 2-page trailing orphan document within 32..70 paragraphs.")


# =============================================================================
# TIER 1: CORE FEATURE COVERAGE (HAPPY PATH)
# =============================================================================


@pytest.mark.e2e
class TestTier1FeatureCoverage:
    """Tier 1 happy-path coverage across all 10 major feature groups (F1-F10)."""

    # --- F1: CLI Basics, I/O & Discovery ---

    def test_f1_version_module_and_entry_point(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        _ = tmp_path
        res_mod = run_cli(["--version"])
        assert res_mod.returncode == EXIT_OK
        assert "mdtopdf-skill 2.0.0" in res_mod.stdout

        ep = _entry_point_path()
        if ep.is_file():
            res_ep = run_cli(["--version"], use_entry_point=True)
            assert res_ep.returncode == EXIT_OK
            assert "mdtopdf-skill 2.0.0" in res_ep.stdout

    def test_f1_default_output_adjacent_pdf_and_human_summary(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "quarterly_summary.md"
        expected_pdf = tmp_path / "quarterly_summary.pdf"
        md_file.write_text("# Quarterly Summary\n\nDefault output path test.\n", encoding="utf-8")

        res = run_cli([str(md_file)])
        assert res.returncode == EXIT_OK
        assert expected_pdf.is_file()
        assert len(res.stdout.strip()) > 0
        summary = inspect_pdf(expected_pdf)
        assert "Quarterly Summary" in summary.full_text

    def test_f1_explicit_output_creates_nested_parent_dirs(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "doc.md"
        out_pdf = tmp_path / "nested" / "build" / "final_report.pdf"
        md_file.write_text("# Nested Output\n\nCreates parent dirs.\n", encoding="utf-8")

        res = run_cli([str(md_file), "-o", str(out_pdf)])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(out_pdf)
        assert "Nested Output" in summary.full_text

    def test_f1_stdin_default_and_explicit_output_with_base_dir(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        base_dir = tmp_path / "stdin_base"
        base_dir.mkdir()
        res_default = run_cli(
            ["-", "--base-dir", str(base_dir), "--json"],
            stdin_text="# Stdin Default\n\nPiped markdown.\n",
        )
        assert res_default.returncode == EXIT_OK
        data_default = assert_success_json(res_default.stdout)
        default_pdf = base_dir / "stdin.pdf"
        assert Path(data_default["output"]).resolve() == default_pdf.resolve()
        inspect_pdf(default_pdf)

        custom_pdf = tmp_path / "custom_stdin.pdf"
        res_custom = run_cli(
            ["-", "--base-dir", str(base_dir), "-o", str(custom_pdf)],
            stdin_text="# Stdin Custom\n\nExplicit output.\n",
        )
        assert res_custom.returncode == EXIT_OK
        summary = inspect_pdf(custom_pdf)
        assert "Stdin Custom" in summary.full_text

    def test_f1_json_schema_and_quiet_mode(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "json_quiet.md"
        md_file.write_text("# JSON and Quiet\n\nBody text.\n", encoding="utf-8")

        res_json = run_cli([str(md_file), "--json"])
        assert res_json.returncode == EXIT_OK
        data = assert_success_json(res_json.stdout, expected_theme="classic")
        inspect_pdf(Path(data["output"]))

        quiet_pdf = tmp_path / "quiet.pdf"
        res_quiet = run_cli([str(md_file), "-o", str(quiet_pdf), "--quiet"])
        assert res_quiet.returncode == EXIT_OK
        assert res_quiet.stdout == ""
        inspect_pdf(quiet_pdf)

    # --- F2: Print Themes (classic, paper) ---

    @pytest.mark.parametrize("theme_name", ALL_THEME_NAMES)
    def test_f2_render_each_theme(
        self, require_browser: Path, tmp_path: Path, theme_name: str
    ) -> None:
        md_file = tmp_path / f"{theme_name}.md"
        out_pdf = tmp_path / f"{theme_name}.pdf"
        md_file.write_text(
            "---\n"
            f"title: Theme Verification ({theme_name})\n"
            "author: QA Engineer\n"
            "date: 2026-11-19\n"
            "---\n"
            f"# Theme Verification ({theme_name})\n\n"
            "ทดสอบภาษาไทยควบคู่กับ English prose.\n\n"
            "| Column A | Column B |\n"
            "| :--- | ---: |\n"
            "| Alpha | 100 |\n"
            "| Beta | 200 |\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "-o", str(out_pdf), "--theme", theme_name, "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme=theme_name)
        summary = inspect_pdf(out_pdf)
        assert summary.page_count == data["pages"]
        assert "Theme Verification" in summary.full_text
        assert "Alpha" in summary.full_text

    # --- F3: YAML Frontmatter & 4-Layer Precedence ---

    def test_f3_frontmatter_all_19_fields_parsed_without_warnings(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "all_19.md"
        md_file.write_text(
            "---\n"
            "theme: paper\n"
            "lang: en\n"
            "title: Complete Spec\n"
            "subtitle: All 19 Frontmatter Keys\n"
            "author: Alice\n"
            "date: 2026-11-19\n"
            "affiliation: Engineering Lab\n"
            "abstract: Executive abstract summary.\n"
            "font_size: 10.0\n"
            "line_height: 1.45\n"
            "spacing_scale: 0.95\n"
            "margin: standard\n"
            "page_size: A4 portrait\n"
            "accent_color: '#0369a1'\n"
            "page_numbers: true\n"
            "header: Custom Top Header\n"
            "footer: Custom Bottom Footer\n"
            "toc: true\n"
            "css: 'h2 { letter-spacing: 0.5px; }'\n"
            "---\n"
            "## Overview\n\nBody paragraph.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="paper")
        assert data["warnings"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "Complete Spec" in summary.full_text
        assert "Engineering Lab" in summary.full_text

    def test_f3_frontmatter_hyphen_and_accent_aliases(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "aliases.md"
        md_file.write_text(
            "---\n"
            "theme: paper\n"
            "font-size: 11.0\n"
            "line-height: 1.6\n"
            "spacing-scale: 1.1\n"
            "page-size: Letter landscape\n"
            "accent: '#1d4ed8'\n"
            "page-numbers: false\n"
            "---\n"
            "# Alias Verification\n\nHyphen keys accepted cleanly.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="paper")
        assert data["warnings"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert_page_size_mm(summary, 279.4, 215.9)

    def test_f3_frontmatter_utf8_bom_and_crlf(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bom_crlf.md"
        raw = (
            "\ufeff---\r\n"
            "theme: paper\r\n"
            "title: BOM Header Title\r\n"
            "---\r\n"
            "Content after CRLF BOM frontmatter.\r\n"
        )
        md_file.write_bytes(raw.encode("utf-8"))
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="paper")
        assert data["warnings"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "BOM Header Title" in summary.full_text
        assert "\ufeff" not in summary.full_text

    def test_f3_precedence_cli_over_frontmatter_over_theme_over_global(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "precedence.md"
        md_file.write_text(
            "---\n"
            "theme: paper\n"
            "page_size: Letter portrait\n"
            "title: Frontmatter Title\n"
            "---\n"
            "Precedence body.\n",
            encoding="utf-8",
        )
        res = run_cli(
            [
                str(md_file),
                "--theme",
                "classic",
                "--page-size",
                "A4 landscape",
                "--title",
                "CLI Title Override",
                "--json",
            ]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        summary = inspect_pdf(Path(data["output"]))
        assert_page_size_mm(summary, 297.0, 210.0)
        assert "CLI Title Override" in summary.full_text

    def test_f3_frontmatter_inline_css_and_relative_css_file(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        css_file = tmp_path / "custom_theme.css"
        css_file.write_text("h2 { text-transform: uppercase; }\n", encoding="utf-8")

        md_rel = tmp_path / "rel_css.md"
        md_rel.write_text(
            "---\ncss: custom_theme.css\n---\n# Relative CSS\n\n## Section One\n\nText.\n",
            encoding="utf-8",
        )
        res_rel = run_cli([str(md_rel), "--json"])
        assert res_rel.returncode == EXIT_OK
        data_rel = assert_success_json(res_rel.stdout)
        assert data_rel["warnings"] == []

        md_inline = tmp_path / "inline_css.md"
        md_inline.write_text(
            "---\ncss: 'p { color: #111111; }'\n---\n# Inline CSS\n\nText.\n",
            encoding="utf-8",
        )
        res_inline = run_cli([str(md_inline), "--json"])
        assert res_inline.returncode == EXIT_OK
        assert assert_success_json(res_inline.stdout)["warnings"] == []

    # --- F4: Typography, Page Geometry & Header/Footer ---

    def test_f4_font_size_line_height_and_spacing_scale(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "typography.md"
        md_file.write_text("# Typography\n\n" + ("Line of prose.\n\n" * 10), encoding="utf-8")
        out_pdf = tmp_path / "typography.pdf"

        res = run_cli(
            [
                str(md_file),
                "-o",
                str(out_pdf),
                "--font-size",
                "11.5",
                "--line-height",
                "1.65",
                "--spacing-scale",
                "1.25",
                "--json",
            ]
        )
        assert res.returncode == EXIT_OK
        assert_success_json(res.stdout)
        inspect_pdf(out_pdf)

    def test_f4_margin_presets_and_custom_css_lengths(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "margins.md"
        md_file.write_text("# Margin Presets\n\nBody text.\n", encoding="utf-8")
        for idx, margin_val in enumerate(
            ("compact", "standard", "comfortable", "wide", "14mm 16mm")
        ):
            out_pdf = tmp_path / f"margin_{idx}.pdf"
            res = run_cli([str(md_file), "-o", str(out_pdf), "--margin", margin_val])
            assert res.returncode == EXIT_OK
            inspect_pdf(out_pdf)

    def test_f4_page_size_a4_landscape(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "landscape.md"
        md_file.write_text("# A4 Landscape\n\nWide layout check.\n", encoding="utf-8")
        res = run_cli([str(md_file), "--page-size", "A4 landscape", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        assert_page_size_mm(summary, 297.0, 210.0)

    def test_f4_page_size_letter_and_legal(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "sizes.md"
        md_file.write_text("# Letter & Legal\n\nPage geometry verification.\n", encoding="utf-8")

        letter_pdf = tmp_path / "letter.pdf"
        res_letter = run_cli([str(md_file), "-o", str(letter_pdf), "--page-size", "Letter"])
        assert res_letter.returncode == EXIT_OK
        assert_page_size_mm(inspect_pdf(letter_pdf), 215.9, 279.4)

        legal_pdf = tmp_path / "legal.pdf"
        res_legal = run_cli([str(md_file), "-o", str(legal_pdf), "--page-size", "Legal"])
        assert res_legal.returncode == EXIT_OK
        assert_page_size_mm(inspect_pdf(legal_pdf), 215.9, 355.6)

    def test_f4_accent_color_and_explicit_title(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "accent_title.md"
        md_file.write_text("## Section Without H1\n\nBody text.\n", encoding="utf-8")
        res = run_cli(
            [
                str(md_file),
                "--theme",
                "paper",
                "--accent",
                "#b91c1c",
                "--title",
                "Explicit CLI Header Title",
                "--json",
            ]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="paper")
        summary = inspect_pdf(Path(data["output"]))
        assert "Explicit CLI Header Title" in summary.full_text

    def test_f4_no_page_numbers_suppresses_footer(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "no_page_nums.md"
        md_file.write_text(
            "---\nheader: false\n---\n# Clean Margin Test\n\nOnly body text here.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--no-page-numbers", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        assert "1 / 1" not in summary.full_text
        assert "Page 1" not in summary.full_text

    # --- F5: Structural Markdown, H1 Dedup, TOC & CSS Cascade ---

    def test_f5_h1_deduplication_with_frontmatter_title(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "h1_dedup.md"
        unique_title = "Unique Architecture Review Title"
        md_file.write_text(
            "---\n"
            f"title: {unique_title}\n"
            "author: Lead Architect\n"
            "header: false\n"
            "---\n"
            f"# {unique_title}\n\n"
            "First paragraph under deduplicated heading.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--no-page-numbers", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        assert summary.per_page_text[0].count(unique_title) == 1

    def test_f5_toc_generation_and_section_headings(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "toc_doc.md"
        md_file.write_text(
            "# Document With TOC\n\n"
            "## Alpha Section\n\nAlpha content.\n\n"
            "## Beta Section\n\nBeta content.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--toc", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        assert summary.full_text.count("Alpha Section") >= 2
        assert summary.full_text.count("Beta Section") >= 2

    def test_f5_github_alerts_task_lists_and_footnotes(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "rich_md.md"
        md_file.write_text(
            "# Structural Markdown\n\n"
            "> [!NOTE]\n> Informational note.\n\n"
            "> [!TIP]\n> Helpful tip.\n\n"
            "> [!IMPORTANT]\n> Key detail.\n\n"
            "> [!WARNING]\n> Caution warning.\n\n"
            "> [!CAUTION]\n> Critical alert.\n\n"
            "- [x] Completed task\n"
            "- [ ] Pending task\n\n"
            "Statement with footnote reference[^fn1].\n\n"
            "[^fn1]: Footnote explanation text.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        for expected in (
            "Informational note.",
            "Helpful tip.",
            "Key detail.",
            "Caution warning.",
            "Critical alert.",
            "Completed task",
            "Pending task",
            "Footnote explanation text.",
        ):
            assert expected in summary.full_text

    def test_f5_codehilite_and_long_code_line_wrapping(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "long_code.md"
        long_token = "VERY_LONG_CONFIGURATION_IDENTIFIER_" + ("X" * 140)
        md_file.write_text(
            "# Code Wrapping\n\n"
            "```python\n"
            f"CONFIG_VALUE = \"{long_token}\"\n"
            "def compute(x: int) -> int:\n"
            "    return x * 2\n"
            "```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--theme", "paper", "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="paper")
        assert data["layout_issues"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "VERY_LONG_CONFIGURATION_IDENTIFIER_" in summary.full_text

    def test_f5_css_cascade_order_with_keep_together(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        cli_css = tmp_path / "override.css"
        cli_css.write_text("h3 { letter-spacing: 0.4px; }\n", encoding="utf-8")
        md_file = tmp_path / "cascade.md"
        md_file.write_text(
            "---\n"
            "theme: classic\n"
            "css: 'h3 { font-weight: 700; }'\n"
            "---\n"
            "# Station Protocol\n\n"
            "### Station 1: Intake\n"
            "- Check vitals\n"
            "  - Blood pressure\n"
            "- Record timestamp\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--css", str(cli_css), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        summary = inspect_pdf(Path(data["output"]))
        assert "Station 1: Intake" in summary.full_text
        assert "Blood pressure" in summary.full_text

    # --- F6: Thai / Unicode & lang Auto-Detection ---

    def test_f6_thai_heavy_document_renders_intact(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "thai_doc.md"
        md_file.write_text(
            "# รายงานสรุปผลการดำเนินงานประจำไตรมาส\n\n"
            "เอกสารฉบับนี้ทดสอบการตัดคำภาษาไทยและวรรณยุกต์ เช่น ปู่ย่าตายาย กิ๊บเก๋ และเลขไทย ๑๒๓๔๕\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        assert "รายงานสรุปผลการดำเนินงาน" in summary.full_text
        assert "๑๒๓๔๕" in summary.full_text

    def test_f6_english_only_document_renders_intact(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "english_doc.md"
        md_file.write_text(
            "# English Technical Brief\n\n"
            "Pure English prose triggers automatic lang=en selection cleanly.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        assert "English Technical Brief" in summary.full_text

    def test_f6_explicit_frontmatter_lang_override(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "lang_override.md"
        md_file.write_text(
            "---\nlang: en-US\n---\n# Bilingual Note\n\nข้อความภาษาไทยในเอกสาร en-US\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["warnings"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "Bilingual Note" in summary.full_text

    def test_f6_multilingual_unicode_and_emoji(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "intl.md"
        md_file.write_text(
            "# Multilingual Document\n\n"
            "French: café résumé | German: Größe | Japanese: 日本語テスト | Status: ✅\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        summary = inspect_pdf(Path(data["output"]))
        assert "café résumé" in summary.full_text
        assert "Größe" in summary.full_text

    def test_f6_windows_paths_with_spaces_and_thai_characters(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        nested = tmp_path / "My Folder With Spaces" / "เอกสารสำคัญ"
        nested.mkdir(parents=True)
        create_test_png(nested / "รูป ภาพ.png")
        md_file = nested / "รายงาน ทดสอบ.md"
        out_pdf = nested / "ผลลัพธ์ รายงาน.pdf"
        md_file.write_text(
            "# รายงานในโฟลเดอร์ภาษาไทย\n\n![Logo](รูป ภาพ.png)\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "-o", str(out_pdf), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        summary = inspect_pdf(out_pdf)
        assert summary.image_count >= 1

    # --- F7: Offline Mermaid Diagrams & KaTeX Math ---

    def test_f7_mermaid_flowchart_offline_vector_svg(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "mermaid_flow.md"
        md_file.write_text(
            "# Flowchart\n\n"
            "```mermaid\n"
            "graph TD\n"
            "    A[Input Markdown] --> B[Sanitizer]\n"
            "    B --> C[Headless CDP]\n"
            "```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["mermaid_errors"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert summary.drawing_count > 0
        assert "Input Markdown" in summary.full_text

    def test_f7_mermaid_sequence_diagram_offline(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "mermaid_seq.md"
        md_file.write_text(
            "# Sequence Diagram\n\n"
            "```mermaid\n"
            "sequenceDiagram\n"
            "    CLI->>CDP: Page.navigate\n"
            "    CDP-->>CLI: printToPDF\n"
            "```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["mermaid_errors"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert summary.drawing_count > 0
        assert "Page.navigate" in summary.full_text

    def test_f7_katex_inline_and_display_math(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "katex_math.md"
        md_file.write_text(
            "# KaTeX Equations\n\n"
            "Inline energy relation $E = mc^2$ and quadratic formula:\n\n"
            "$$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["math_errors"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "mc" in summary.full_text
        assert "4ac" in summary.full_text

    def test_f7_currency_and_escaped_dollars_not_treated_as_math(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "currency.md"
        md_file.write_text(
            "# Pricing\n\n"
            "Plan A costs $5 and $10 per month. Enterprise costs $1,234.50 or \\$100.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["math_errors"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "$5" in summary.full_text
        assert "$10" in summary.full_text
        assert "$1,234.50" in summary.full_text

    def test_f7_combined_thai_mermaid_and_katex_under_csp(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "thai_mermaid_katex.md"
        md_file.write_text(
            "# สถาปัตยกรรมและสมการ\n\n"
            "ค่าพลังงานจลน์ $K = \\frac{1}{2}mv^2$ สำหรับระบบวิเคราะห์ข้อมูล\n\n"
            "```mermaid\n"
            "graph LR\n"
            "    Start[เริ่มต้น] --> End[เสร็จสิ้น]\n"
            "```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["mermaid_errors"] == []
        assert data["math_errors"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert summary.drawing_count > 0
        assert "สถาปัตยกรรมและสมการ" in summary.full_text

    # --- F8: Local Image Containment & Security ---

    def test_f8_relative_markdown_image_embedded(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "assets" / "chart.png", width=320, height=160)
        md_file = tmp_path / "img_md.md"
        md_file.write_text("# Image Test\n\n![Chart](assets/chart.png)\n", encoding="utf-8")

        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        assert data["suppressed_images"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert summary.image_count >= 1

    def test_f8_raw_html_img_tag_embedded(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "badge.png", width=160, height=80)
        md_file = tmp_path / "raw_img.md"
        md_file.write_text(
            '# Raw HTML Image\n\n<img src="badge.png" alt="Badge">\n',
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        summary = inspect_pdf(Path(data["output"]))
        assert summary.image_count >= 1

    def test_f8_img_max_width_and_max_height_in_table_cells(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "thumb.png", width=400, height=300)
        md_file = tmp_path / "table_img.md"
        md_file.write_text(
            "# Table Image Caps\n\n"
            "| Preview | Description |\n"
            "| :---: | :--- |\n"
            "| ![Thumb](thumb.png) | Constrained cell image |\n",
            encoding="utf-8",
        )
        res = run_cli(
            [str(md_file), "--img-max-width", "60", "--img-max-height", "45", "--json"]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        assert inspect_pdf(Path(data["output"])).image_count >= 1

    def test_f8_allow_outside_base_dir_embeds_parent_image(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "shared_logo.png")
        sub = tmp_path / "docs"
        sub.mkdir()
        md_file = sub / "outside_ok.md"
        md_file.write_text("# Outside Opt-In\n\n![Logo](../shared_logo.png)\n", encoding="utf-8")

        res = run_cli([str(md_file), "--allow-outside-base-dir", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        assert data["suppressed_images"] == []

    def test_f8_keep_missing_images_flag(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "keep_missing.md"
        md_file.write_text(
            "# Keep Missing\n\n![Missing Alt](nonexistent_pic.png)\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--keep-missing-images", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 0
        inspect_pdf(Path(data["output"]))

    # --- F9: Self-Healing Auto-Fit, Strict Mode & Preview PNGs ---

    def test_f9_wide_table_remediated_by_autofit(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "wide_8col.md"
        headers = " | ".join(f"MetricColumn{i}" for i in range(1, 9))
        sep = " | ".join(":---" for _ in range(1, 9))
        row = " | ".join(f"LongCellValue_{i}_AlphaBeta" for i in range(1, 9))
        md_file.write_text(
            f"# Wide Table Remediation\n\n| {headers} |\n| {sep} |\n| {row} |\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["layout_issues"] == []
        steps = {item["step"] for item in data["adjustments_applied"]}
        assert steps & {"table_wrap_compact", "table_proportional_scale"}

    def test_f9_very_wide_table_tier2_proportional_scale(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "wide_11col.md"
        headers = " | ".join(f"Col_{i:02d}_Header" for i in range(1, 12))
        sep = " | ".join(":---" for _ in range(1, 12))
        row = " | ".join(f"UnbrokenToken{i}ABCXYZ" for i in range(1, 12))
        md_file.write_text(
            f"# Tier 2 Table Scale\n\n| {headers} |\n| {sep} |\n| {row} |\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        steps = {item["step"] for item in data["adjustments_applied"]}
        assert steps & {"table_wrap_compact", "table_proportional_scale"}

    def test_f9_trailing_orphan_page_compacted_by_autofit(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        orphan_md, baseline = _calibrate_orphan_markdown(tmp_path)
        assert baseline["pages"] == 2

        autofit_pdf = tmp_path / "orphan_compacted.pdf"
        res = run_cli([str(orphan_md), "-o", str(autofit_pdf), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["pages"] == 1
        assert data["layout_issues"] == []
        steps = {item["step"] for item in data["adjustments_applied"]}
        assert steps & {"spacing_compact_1", "font_spacing_compact_2", "font_spacing_compact_3"}

    def test_f9_preview_dir_exports_144dpi_pngs(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "preview_doc.md"
        preview_dir = tmp_path / "previews"
        body = "\n\n".join(f"Section paragraph {i} with multi-page text." for i in range(1, 70))
        md_file.write_text(f"# Preview Export\n\n{body}\n", encoding="utf-8")

        res = run_cli([str(md_file), "--preview-dir", str(preview_dir), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert len(data["preview_images"]) == data["pages"]
        inspect_preview_pngs(preview_dir, data["pages"])

    def test_f9_strict_mode_succeeds_on_clean_document(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "clean_strict.md"
        md_file.write_text("# Clean Strict Doc\n\nNo overflows or errors.\n", encoding="utf-8")
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["layout_issues"] == []
        assert data["mermaid_errors"] == []
        assert data["math_errors"] == []

    # --- F10: Error Handling, Telemetry & Cleanup ---

    def test_f10_missing_local_image_warns_and_exits_0(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "missing_img.md"
        md_file.write_text("# Missing Image\n\n![Missing](absent_diagram.png)\n", encoding="utf-8")
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert "absent_diagram.png" in data["suppressed_images"]
        assert "absent_diagram.png" in res.stderr

    def test_f10_non_strict_mermaid_error_renders_box_and_exits_0(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_mermaid.md"
        md_file.write_text(
            "# Broken Mermaid\n\n```mermaid\ngraph TD\n    A -->> B === ???\n```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert len(data["mermaid_errors"]) >= 1
        summary = inspect_pdf(Path(data["output"]))
        assert "Mermaid Render Error" in summary.full_text

    def test_f10_non_strict_katex_error_renders_box_and_exits_0(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_katex.md"
        md_file.write_text(
            "# Broken Math\n\nInvalid macro: $\\definitelyUndefinedMacro{x}$\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert len(data["math_errors"]) >= 1
        summary = inspect_pdf(Path(data["output"]))
        assert "definitelyUndefinedMacro" in summary.full_text

    def test_f10_help_flag_exits_0(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        _ = tmp_path
        res = run_cli(["--help"])
        assert res.returncode == EXIT_OK
        assert "--theme" in res.stdout
        assert "--strict" in res.stdout
        assert "--preview-dir" in res.stdout

    def test_f10_no_stray_temp_files_in_workspace(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "clean_cleanup.md"
        out_pdf = tmp_path / "clean_cleanup.pdf"
        md_file.write_text("# Temp Cleanup\n\nCheck stray artifacts.\n", encoding="utf-8")

        res = run_cli([str(md_file), "-o", str(out_pdf)], cwd=tmp_path)
        assert res.returncode == EXIT_OK
        entries = {p.name for p in tmp_path.iterdir()}
        assert entries == {"clean_cleanup.md", "clean_cleanup.pdf"}


# =============================================================================
# TIER 2: BOUNDARY & ERROR CASES (>= 5 PER F1-F10)
# =============================================================================


@pytest.mark.e2e
class TestTier2BoundaryAndErrorCases:
    """Tier 2 boundary values, invalid inputs, security blocks, and exit codes."""

    # --- F1: CLI Basics & Arg Validation ---

    def test_f1_no_args_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        _ = tmp_path
        res = run_cli([])
        assert res.returncode == EXIT_BAD_ARGS
        assert "error" in res.stderr.lower() or "required" in res.stderr.lower()

    def test_f1_missing_input_file_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        res = run_cli([str(tmp_path / "missing.md"), "--json"])
        assert res.returncode == EXIT_BAD_ARGS
        assert_error_json(res.stdout, EXIT_BAD_ARGS)

    def test_f1_input_path_is_directory_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        res = run_cli([str(tmp_path), "--json"])
        assert res.returncode == EXIT_BAD_ARGS
        assert_error_json(res.stdout, EXIT_BAD_ARGS)

    def test_f1_stdin_without_base_dir_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        _ = tmp_path
        res = run_cli(["-", "--json"], stdin_text="# Hello\n")
        assert res.returncode == EXIT_BAD_ARGS
        data = assert_error_json(res.stdout, EXIT_BAD_ARGS)
        assert "--base-dir" in data["error"]

    def test_f1_base_dir_missing_or_file_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Doc\n", encoding="utf-8")

        res_missing = run_cli([str(md_file), "--base-dir", str(tmp_path / "no_such_dir")])
        assert res_missing.returncode == EXIT_BAD_ARGS

        res_file = run_cli([str(md_file), "--base-dir", str(md_file)])
        assert res_file.returncode == EXIT_BAD_ARGS

    def test_f1_output_collision_with_input_or_existing_dir_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Original\n", encoding="utf-8")

        res_same = run_cli([str(md_file), "-o", str(md_file)])
        assert res_same.returncode == EXIT_BAD_ARGS
        assert md_file.read_text(encoding="utf-8") == "# Original\n"

        out_dir = tmp_path / "dir_target"
        out_dir.mkdir()
        res_dir = run_cli([str(md_file), "-o", str(out_dir)])
        assert res_dir.returncode == EXIT_BAD_ARGS

    def test_f1_removed_list_themes_flag_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        _ = tmp_path
        res = run_cli(["--list-themes"])
        assert res.returncode == EXIT_BAD_ARGS

    # --- F2: Print Themes Boundaries ---

    @pytest.mark.parametrize(
        "bad_theme",
        ["nonexistent-theme", "office", "student", "tech-spec", "executive", "minimal"],
    )
    def test_f2_invalid_or_removed_cli_theme_exits_2(
        self, require_v1_cli: None, tmp_path: Path, bad_theme: str
    ) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), "--theme", bad_theme])
        assert res.returncode == EXIT_BAD_ARGS

    def test_f2_cli_theme_case_insensitive(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "case_theme.md"
        md_file.write_text("# Case Theme\n\nBody.\n", encoding="utf-8")
        res = run_cli([str(md_file), "--theme", "PAPER", "--json"])
        assert res.returncode == EXIT_OK
        assert_success_json(res.stdout, expected_theme="paper")

    def test_f2_invalid_frontmatter_theme_warns_and_falls_back_to_classic(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_fm_theme.md"
        md_file.write_text(
            "---\ntheme: unknown_preset\n---\n# Fallback Theme\n\nBody.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        assert any("frontmatter_invalid_field" in w for w in data["warnings"])

    def test_f2_classic_default_page_numbers_without_running_header(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_default = tmp_path / "classic_default.md"
        md_default.write_text(
            "---\ntitle: Classic Running Header Check\n---\n"
            "# Classic Running Header Check\n\nBody text.\n",
            encoding="utf-8",
        )
        res_default = run_cli([str(md_default), "--theme", "classic", "--json"])
        assert res_default.returncode == EXIT_OK
        summary_default = inspect_pdf(Path(assert_success_json(res_default.stdout)["output"]))
        assert "1 / 1" in summary_default.full_text
        assert "Page 1" not in summary_default.full_text

        res_no_num = run_cli(
            [str(md_default), "--theme", "classic", "--no-page-numbers", "--json"]
        )
        assert res_no_num.returncode == EXIT_OK
        summary_no_num = inspect_pdf(Path(assert_success_json(res_no_num.stdout)["output"]))
        assert "1 / 1" not in summary_no_num.full_text

    def test_f2_invalid_theme_with_json_emits_error_json(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_theme_json.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), "--theme", "bogus", "--json"])
        assert res.returncode == EXIT_BAD_ARGS
        assert_error_json(res.stdout, EXIT_BAD_ARGS)

    # --- F3: YAML Frontmatter Boundaries ---

    def test_f3_malformed_yaml_frontmatter_does_not_crash(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "malformed_yaml.md"
        md_file.write_text(
            "---\ntitle: [unclosed bracket\n---\n# Body After Malformed FM\n\nText.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert any("frontmatter_invalid" in w for w in data["warnings"])
        summary = inspect_pdf(Path(data["output"]))
        assert "Body After Malformed FM" in summary.full_text

    def test_f3_non_dict_and_deep_nested_frontmatter_warns(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_list = tmp_path / "list_fm.md"
        md_list.write_text("---\n- a: 1\n- b: 2\n---\n# List Root\n", encoding="utf-8")
        res_list = run_cli([str(md_list), "--json"])
        assert res_list.returncode == EXIT_OK
        list_warnings = assert_success_json(res_list.stdout)["warnings"]
        assert any("frontmatter_invalid" in w for w in list_warnings)

        deep = ("[" * 500) + ("]" * 500)
        md_deep = tmp_path / "deep_fm.md"
        md_deep.write_text(f"---\ntitle: {deep}\n---\n# Deep YAML\n", encoding="utf-8")
        res_deep = run_cli([str(md_deep), "--json"])
        assert res_deep.returncode == EXIT_OK
        deep_warnings = assert_success_json(res_deep.stdout)["warnings"]
        assert any("frontmatter_invalid" in w for w in deep_warnings)

    def test_f3_non_frontmatter_thematic_breaks_preserved_as_hr(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "thematic_break.md"
        md_file.write_text(
            "---\nhttps://example.com\n---\n\n# Body Heading\n\nParagraph text.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["warnings"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "Body Heading" in summary.full_text

    def test_f3_invalid_frontmatter_field_values_emit_warnings(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_fields.md"
        md_file.write_text(
            "---\nfont_size: 999\nmargin: 10%\nlang: invalid!\n---\n# Bad Fields\n\nBody.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        invalid_warnings = [w for w in data["warnings"] if "frontmatter_invalid_field" in w]
        assert len(invalid_warnings) >= 3

    def test_f3_frontmatter_missing_css_file_warns_without_crashing(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "fm_missing_css.md"
        md_file.write_text(
            "---\ncss: missing_style.css\n---\n# Missing FM CSS\n\nText.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert any("frontmatter_css_missing" in w for w in data["warnings"])

    def test_f3_frontmatter_outside_css_file_blocked_with_warning(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        (tmp_path / "secret.css").write_text("body { color: red; }\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        md_file = sub / "fm_outside_css.md"
        md_file.write_text(
            "---\ncss: ../secret.css\n---\n# Blocked FM CSS\n\nText.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert any("frontmatter_css_blocked" in w for w in data["warnings"])

    # --- F4: Typography, Page Geometry & Header/Footer Boundaries ---

    def test_f4_font_size_exact_bounds_succeed(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "font_bounds.md"
        md_file.write_text("# Bounds\n\nShort text.\n", encoding="utf-8")
        for idx, pt in enumerate(("6", "72")):
            res = run_cli(
                [str(md_file), "-o", str(tmp_path / f"font_{idx}.pdf"), "--font-size", pt]
            )
            assert res.returncode == EXIT_OK

    @pytest.mark.parametrize("bad_size", ["5.99", "72.01", "nan", "inf"])
    def test_f4_font_size_invalid_exits_2(
        self, require_v1_cli: None, tmp_path: Path, bad_size: str
    ) -> None:
        md_file = tmp_path / "bad_font.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), "--font-size", bad_size])
        assert res.returncode == EXIT_BAD_ARGS

    def test_f4_line_height_and_spacing_scale_exact_bounds_succeed(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "lh_scale_bounds.md"
        md_file.write_text("# Bounds\n\nLine height and scale bounds.\n", encoding="utf-8")
        res_min = run_cli(
            [
                str(md_file),
                "-o",
                str(tmp_path / "min.pdf"),
                "--line-height",
                "1.0",
                "--spacing-scale",
                "0.5",
            ]
        )
        assert res_min.returncode == EXIT_OK
        res_max = run_cli(
            [
                str(md_file),
                "-o",
                str(tmp_path / "max.pdf"),
                "--line-height",
                "3.0",
                "--spacing-scale",
                "2.0",
            ]
        )
        assert res_max.returncode == EXIT_OK

    @pytest.mark.parametrize(
        ("flag", "bad_val"),
        [
            ("--line-height", "0.99"),
            ("--line-height", "3.01"),
            ("--spacing-scale", "0.49"),
            ("--spacing-scale", "2.01"),
        ],
    )
    def test_f4_line_height_and_spacing_scale_invalid_exits_2(
        self, require_v1_cli: None, tmp_path: Path, flag: str, bad_val: str
    ) -> None:
        md_file = tmp_path / "bad_lh_scale.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), flag, bad_val])
        assert res.returncode == EXIT_BAD_ARGS

    def test_f4_margin_invalid_units_and_excess_tokens_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_margin.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        for bad_margin in ("10%", "10mm 10mm 10mm 10mm 10mm", "unknown_preset", "-5mm"):
            res = run_cli([str(md_file), "--margin", bad_margin])
            assert res.returncode == EXIT_BAD_ARGS

    def test_f4_margins_exceeding_page_size_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "tiny_page.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), "--page-size", "50mm 50mm", "--margin", "30mm"])
        assert res.returncode == EXIT_BAD_ARGS

    def test_f4_invalid_accent_and_page_size_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_accent_size.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        assert run_cli([str(md_file), "--accent", "#12"]).returncode == EXIT_BAD_ARGS
        assert run_cli([str(md_file), "--accent", "#gggggg"]).returncode == EXIT_BAD_ARGS
        assert run_cli([str(md_file), "--page-size", "A5 bogus"]).returncode == EXIT_BAD_ARGS

    def test_f4_header_footer_disabled_when_margin_below_8mm(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "small_margin_hf.md"
        md_file.write_text(
            "---\nheader: Top Banner Text\nfooter: Bottom Footer Text\n---\n"
            "# Small Margin\n\nBody text only.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--margin", "6mm 10mm", "--json"])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(Path(assert_success_json(res.stdout)["output"]))
        assert "Top Banner Text" not in summary.full_text
        assert "Bottom Footer Text" not in summary.full_text

    # --- F5: Structural Markdown, TOC & CLI CSS Boundaries ---

    def test_f5_toc_and_no_toc_mutually_exclusive_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "toc_conflict.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), "--toc", "--no-toc"])
        assert res.returncode == EXIT_BAD_ARGS

    def test_f5_cli_css_missing_or_directory_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res_missing = run_cli([str(md_file), "--css", str(tmp_path / "missing.css")])
        assert res_missing.returncode == EXIT_BAD_ARGS
        assert run_cli([str(md_file), "--css", str(tmp_path)]).returncode == EXIT_BAD_ARGS

    def test_f5_cli_css_outside_base_dir_blocked_without_opt_in_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        outside_css = tmp_path / "outside.css"
        outside_css.write_text("body { color: #000; }\n", encoding="utf-8")
        sub = tmp_path / "inner"
        sub.mkdir()
        md_file = sub / "doc.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), "--css", str(outside_css)])
        assert res.returncode == EXIT_BAD_ARGS

    def test_f5_no_keep_together_flag_disables_station_block_grouping(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "no_keep.md"
        items = "\n".join(f"- Step item {i}" for i in range(1, 20))
        md_file.write_text(f"# Steps\n\n### Station A\n{items}\n", encoding="utf-8")
        res = run_cli([str(md_file), "--no-keep-together", "--json"])
        assert res.returncode == EXIT_OK
        inspect_pdf(Path(assert_success_json(res.stdout)["output"]))

    def test_f5_h1_not_deduplicated_when_mismatching_or_no_fm_title(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "h1_keep.md"
        md_file.write_text(
            "---\ntitle: Frontmatter Title\nheader: false\n---\n"
            "# Different Body Heading\n\nBody.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--no-page-numbers", "--json"])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(Path(assert_success_json(res.stdout)["output"]))
        assert "Frontmatter Title" in summary.full_text
        assert "Different Body Heading" in summary.full_text

    # --- F6: Thai / Unicode & I/O Encoding Boundaries ---

    def test_f6_invalid_frontmatter_lang_warns_and_falls_back(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_lang.md"
        md_file.write_text("---\nlang: invalid_lang!\n---\n# Lang Fallback\n", encoding="utf-8")
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert any("frontmatter_invalid_field" in w for w in data["warnings"])

    def test_f6_invalid_utf8_input_file_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        bad_file = tmp_path / "invalid_utf8.md"
        bad_file.write_bytes(b"\xff\xfe\x00\x80# Not UTF-8")
        res = run_cli([str(bad_file), "--json"])
        assert res.returncode == EXIT_BAD_ARGS
        assert_error_json(res.stdout, EXIT_BAD_ARGS)

    def test_f6_empty_and_whitespace_only_markdown_handled_gracefully(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        empty_md = tmp_path / "empty.md"
        empty_md.write_text("   \n\n\t\n", encoding="utf-8")
        res = run_cli([str(empty_md), "--json"])
        assert res.returncode in (EXIT_OK, 5)
        if res.returncode == EXIT_OK:
            data = assert_success_json(res.stdout)
            inspect_pdf(Path(data["output"]))
        else:
            assert_error_json(res.stdout, 5)

    def test_f6_thai_combining_marks_and_thai_digits_preserved(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "thai_diacritics.md"
        md_file.write_text(
            "# ทดสอบสระและวรรณยุกต์\n\n"
            "คำว่า น้ำผึ้ง ปู่ย่าตายาย กิ๊บเก๋ พื้นที่ ๑๒๓๔๕๖๗๘๙๐\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(Path(assert_success_json(res.stdout)["output"]))
        assert "น้ำผึ้ง" in summary.full_text
        assert "๑๒๓๔๕๖๗๘๙๐" in summary.full_text

    def test_f6_json_flag_value_disambiguation_with_unicode_title(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        _ = tmp_path
        res_opt = run_cli(["doc.md", "--title", "--json", "--font-size", "nan"])
        assert res_opt.returncode == EXIT_BAD_ARGS
        assert res_opt.stdout == ""
        assert "error:" in res_opt.stderr.lower()

        res_dash = run_cli(["--font-size", "nan", "--", "--json"])
        assert res_dash.returncode == EXIT_BAD_ARGS
        assert res_dash.stdout == ""
        assert "error:" in res_dash.stderr.lower()

    # --- F7: Mermaid, KaTeX & --strict Boundaries ---

    def test_f7_invalid_mermaid_without_strict_exits_0_with_telemetry(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_mmd_nonstrict.md"
        md_file.write_text(
            "# Bad Mermaid\n\n```mermaid\nflowchart TD\n    A --->>> ??? B\n```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert len(data["mermaid_errors"]) >= 1
        assert data["mermaid_errors"][0]["index"] == 0

    def test_f7_invalid_mermaid_with_strict_exits_6(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_mmd_strict.md"
        md_file.write_text(
            "# Bad Mermaid Strict\n\n```mermaid\ngraph TD\n    A -->> ???\n```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_STRICT_ISSUE
        data = assert_strict_failure_json(res.stdout)
        assert len(data["mermaid_errors"]) >= 1

    def test_f7_invalid_katex_without_strict_exits_0_with_telemetry(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_katex_nonstrict.md"
        md_file.write_text(
            "# Bad KaTeX\n\n$$\\noSuchCommandHere{y}$$\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert len(data["math_errors"]) >= 1
        assert data["math_errors"][0]["display_mode"] is True

    def test_f7_invalid_katex_with_strict_exits_6(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "bad_katex_strict.md"
        md_file.write_text("# Bad KaTeX Strict\n\n$\\badMacro{x}$\n", encoding="utf-8")
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_STRICT_ISSUE
        data = assert_strict_failure_json(res.stdout)
        assert len(data["math_errors"]) >= 1

    def test_f7_placeholder_collision_in_markdown_preserved_verbatim(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "collision.md"
        md_file.write_text(
            "# Placeholder Collision\n\n"
            "Literal tokens MDTOPDFMERMAID0END and MDTOPDFMATH0END in prose.\n\n"
            "Inline math $a + b = c$.\n\n"
            "```mermaid\ngraph TD\n    X --> Y\n```\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(Path(assert_success_json(res.stdout)["output"]))
        assert "MDTOPDFMERMAID0END" in summary.full_text
        assert "MDTOPDFMATH0END" in summary.full_text

    # --- F8: Image Containment & Security Sanitization ---

    def test_f8_traversal_image_suppressed_by_default(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "secret.png")
        sub = tmp_path / "work"
        sub.mkdir()
        md_file = sub / "traversal.md"
        md_file.write_text("# Traversal\n\n![Secret](../secret.png)\n", encoding="utf-8")
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 0
        assert len(data["suppressed_images"]) == 1

    def test_f8_remote_http_https_image_always_blocked_even_with_allow_outside(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "remote_img.md"
        md_file.write_text(
            "# Remote Block\n\n"
            "![RemoteA](https://example.com/pixel.png)\n"
            "![RemoteB](http://example.org/track.png)\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--allow-outside-base-dir", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 0
        assert len(data["suppressed_images"]) == 2

    def test_f8_unc_and_remote_file_host_image_always_blocked(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "unc_img.md"
        md_file.write_text(
            "# UNC Block\n\n"
            "![UNC](//fileserver/share/secret.png)\n"
            "![FileHost](file://fileserver/share/secret2.png)\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--allow-outside-base-dir", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 0
        assert len(data["suppressed_images"]) == 2

    def test_f8_xss_payloads_stripped_from_html_output(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "xss.md"
        md_file.write_text(
            "# Security Sanitization\n\n"
            '<script>document.body.innerHTML="XSS_EXECUTED";</script>\n'
            '<img src="x" onerror="document.title=\'PWNED\'">\n'
            '<iframe src="https://evil.example"></iframe>\n'
            "[Safe Click](javascript:alert(1))\n\n"
            "Normal clean paragraph remains.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(Path(assert_success_json(res.stdout)["output"]))
        assert "XSS_EXECUTED" not in summary.full_text
        assert "Normal clean paragraph remains." in summary.full_text

    def test_f8_css_injection_payloads_stripped(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        evil_css = tmp_path / "evil.css"
        evil_css.write_text(
            "@import url('https://evil.example/leak.css');\n"
            "body { background-image: url(https://evil.example/bg.png); }\n"
            "</style><script>document.body.innerText='CSS_BREAKOUT';</script>\n"
            "h1 { letter-spacing: 1px; }\n",
            encoding="utf-8",
        )
        md_file = tmp_path / "css_inj.md"
        md_file.write_text("# Clean Heading\n\nClean body.\n", encoding="utf-8")
        res = run_cli([str(md_file), "--css", str(evil_css), "--json"])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(Path(assert_success_json(res.stdout)["output"]))
        assert "CSS_BREAKOUT" not in summary.full_text
        assert "Clean body." in summary.full_text

    def test_f8_img_max_width_height_invalid_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        assert run_cli([str(md_file), "--img-max-width", "0"]).returncode == EXIT_BAD_ARGS
        assert run_cli([str(md_file), "--img-max-height", "-10"]).returncode == EXIT_BAD_ARGS
        assert run_cli([str(md_file), "--img-max-width", "12.5"]).returncode == EXIT_BAD_ARGS

    # --- F9: Auto-Fit, --strict & Preview Boundaries ---

    def test_f9_extreme_18_col_table_reports_table_overflow_and_strict_exits_6(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "extreme_table.md"
        headers = " | ".join(f"ExtremeCol{i}" for i in range(1, 19))
        sep = " | ".join(":---" for _ in range(1, 19))
        row = " | ".join(f"UnbreakableLongCellValue_{i}_XYZ" for i in range(1, 19))
        md_file.write_text(
            f"# Extreme Overflow\n\n| {headers} |\n| {sep} |\n| {row} |\n",
            encoding="utf-8",
        )
        res_non_strict = run_cli([str(md_file), "--json"])
        assert res_non_strict.returncode == EXIT_OK
        data_ok = assert_success_json(res_non_strict.stdout)
        assert any(i["kind"] == "table_overflow" for i in data_ok["layout_issues"])

        res_strict = run_cli([str(md_file), "--strict", "--json"])
        assert res_strict.returncode == EXIT_STRICT_ISSUE
        data_strict = assert_strict_failure_json(res_strict.stdout)
        assert any(i["kind"] == "table_overflow" for i in data_strict["layout_issues"])

    def test_f9_no_auto_fit_skips_remediation_but_still_detects_overflow(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "no_autofit_table.md"
        headers = " | ".join(f"OverflowCol{i}" for i in range(1, 11))
        sep = " | ".join(":---" for _ in range(1, 11))
        row = " | ".join(f"WideContentCell_{i}_Token" for i in range(1, 11))
        md_file.write_text(
            f"# No Auto-Fit\n\n| {headers} |\n| {sep} |\n| {row} |\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--no-auto-fit", "--strict", "--json"])
        assert res.returncode == EXIT_STRICT_ISSUE
        data = assert_strict_failure_json(res.stdout)
        assert data["adjustments_applied"] == []
        assert any(i["kind"] == "table_overflow" for i in data["layout_issues"])

    def test_f9_uncompactable_trailing_orphan_reverts_and_reports_issue(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        css_file = tmp_path / "force_break.css"
        css_file.write_text(".force-break { break-before: page; }\n", encoding="utf-8")
        md_file = tmp_path / "uncompactable_orphan.md"
        md_file.write_text(
            "# Page One\n\nFirst page content.\n\n"
            '<div class="force-break">Single uncompactable trailing orphan line.</div>\n',
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--css", str(css_file), "--strict", "--json"])
        assert res.returncode == EXIT_STRICT_ISSUE
        data = assert_strict_failure_json(res.stdout)
        assert data["pages"] == 2
        assert any(i["kind"] == "trailing_orphan_page" for i in data["layout_issues"])

    def test_f9_sparse_final_page_with_image_or_mermaid_not_flagged_as_orphan(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "diagram_pic.png", width=360, height=220)
        css_file = tmp_path / "page_break.css"
        css_file.write_text(".next-page { break-before: page; }\n", encoding="utf-8")
        md_file = tmp_path / "image_final_page.md"
        md_file.write_text(
            "# Page One\n\nIntro section.\n\n"
            '<div class="next-page"></div>\n\n'
            "![Diagram](diagram_pic.png)\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--css", str(css_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["pages"] == 2
        assert not any(i["kind"] == "trailing_orphan_page" for i in data["layout_issues"])

    def test_f9_wide_non_table_element_reports_wide_element(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "wide_katex.md"
        terms = " + ".join(f"x_{{{i}}}" for i in range(1, 55))
        md_file.write_text(f"# Wide Display Equation\n\n$${terms}$$\n", encoding="utf-8")
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_STRICT_ISSUE
        data = assert_strict_failure_json(res.stdout)
        assert any(i["kind"] == "wide_element" for i in data["layout_issues"])

    def test_f9_preview_dir_collision_with_input_or_file_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        assert run_cli([str(md_file), "--preview-dir", str(md_file)]).returncode == EXIT_BAD_ARGS

        blocker = tmp_path / "blocker_file.txt"
        blocker.write_text("x", encoding="utf-8")
        assert run_cli([str(md_file), "--preview-dir", str(blocker)]).returncode == EXIT_BAD_ARGS

    # --- F10: Browser, Timeout, Errors & Exit Codes ---

    def test_f10_missing_browser_or_invalid_mdtopdf_browser_behavior(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "no_browser.md"
        md_file.write_text("# Browser Probe\n\nBody.\n", encoding="utf-8")
        nonexistent = tmp_path / "missing_browser_dir" / "no_such_browser.exe"
        res = run_cli(
            [str(md_file), "--json"],
            env_overrides={"MDTOPDF_BROWSER": str(nonexistent), "PATH": ""},
        )
        standard_browser = _discover_standard_browser()
        if standard_browser is None or res.returncode == EXIT_NO_BROWSER:
            assert res.returncode == EXIT_NO_BROWSER
            data = assert_error_json(res.stdout, EXIT_NO_BROWSER)
            assert "MDTOPDF_BROWSER" in data["error"]
        else:
            # Consistent with §8 "checks MDTOPDF_BROWSER, then standard OS paths"
            assert res.returncode == EXIT_OK
            assert_success_json(res.stdout)

    def test_f10_non_browser_executable_in_mdtopdf_browser_fails_render_or_browser(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        script_name = "fake_browser.bat" if sys.platform == "win32" else "fake_browser.sh"
        fake_browser = tmp_path / script_name
        fake_browser.write_text("exit 1\n", encoding="utf-8")
        if sys.platform != "win32":
            fake_browser.chmod(0o755)
        md_file = tmp_path / "fake_browser_doc.md"
        md_file.write_text("# Fake Browser\n", encoding="utf-8")

        res = run_cli(
            [str(md_file), "--json"],
            env_overrides={"MDTOPDF_BROWSER": str(fake_browser)},
        )
        assert res.returncode in (EXIT_NO_BROWSER, EXIT_RENDER_ERROR)
        assert_error_json(res.stdout, res.returncode)

    def test_f10_timeout_env_var_triggers_exit_4(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "timeout_doc.md"
        md_file.write_text("# Timeout Check\n\nBody.\n", encoding="utf-8")
        res = run_cli(
            [str(md_file), "--json"],
            env_overrides={"MDTOPDF_TIMEOUT": "0.001"},
        )
        assert res.returncode == EXIT_RENDER_ERROR
        assert_error_json(res.stdout, EXIT_RENDER_ERROR)

    def test_f10_stderr_sanitizes_control_chars_and_newlines_on_suppressed_image(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "stderr_sanitize.md"
        malicious_src = "missing.png\x1b[31mINJECTED"
        md_file.write_text(f"# Sanitize Stderr\n\n![X]({malicious_src})\n", encoding="utf-8")
        res = run_cli([str(md_file)])
        assert res.returncode == EXIT_OK
        assert "\x1b" not in res.stderr
        assert len(res.stderr.strip().splitlines()) <= 2

    def test_f10_non_writable_explicit_output_parent_exits_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        blocker = tmp_path / "not_a_dir"
        blocker.write_text("file", encoding="utf-8")
        md_file = tmp_path / "doc.md"
        md_file.write_text("# Doc\n", encoding="utf-8")
        res = run_cli([str(md_file), "-o", str(blocker / "out.pdf"), "--json"])
        assert res.returncode == EXIT_BAD_ARGS
        assert_error_json(res.stdout, EXIT_BAD_ARGS)

    def test_f10_json_error_schema_for_exit_2(
        self, require_v1_cli: None, tmp_path: Path
    ) -> None:
        _ = tmp_path
        res = run_cli(["--json"])
        assert res.returncode == EXIT_BAD_ARGS
        data = assert_error_json(res.stdout, EXIT_BAD_ARGS)
        assert "INPUT.md" in data["error"] or "required" in data["error"].lower()


# =============================================================================
# TIER 3: PAIRWISE CROSS-FEATURE COMBINATIONS (>= 10 TESTS)
# =============================================================================


@pytest.mark.e2e
class TestTier3CrossFeatureCombinations:
    """Tier 3 pairwise and multi-feature interaction tests."""

    def test_t3_01_precedence_aliases_and_themes(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "t3_01.md"
        md_file.write_text(
            "---\n"
            "theme: paper\n"
            "font-size: 11.5\n"
            "accent: '#222222'\n"
            "toc: true\n"
            "---\n"
            "# Precedence Combo\n\n"
            "## Unique Section Heading\n\n"
            "Body text.\n",
            encoding="utf-8",
        )
        res = run_cli(
            [
                str(md_file),
                "--theme",
                "classic",
                "--accent",
                "#0369a1",
                "--no-toc",
                "--json",
            ]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        summary = inspect_pdf(Path(data["output"]))
        assert summary.full_text.count("Unique Section Heading") == 1

    def test_t3_02_css_cascade_theme_fm_cli_and_sanitization(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        (tmp_path / "fm.css").write_text("h2 { letter-spacing: 1px; }\n", encoding="utf-8")
        override_css = tmp_path / "override.css"
        override_css.write_text(
            "@import url('https://evil.example/x.css');\n"
            "</style><script>document.body.innerText='PWN';</script>\n"
            "h2 { text-transform: uppercase; }\n",
            encoding="utf-8",
        )
        md_file = tmp_path / "t3_02.md"
        md_file.write_text(
            "---\ntheme: paper\ncss: fm.css\n---\n# Cascade\n\n## Sub Heading\n\nBody.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--css", str(override_css), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="paper")
        assert data["warnings"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "PWN" not in summary.full_text

    def test_t3_03_header_footer_margin_floor_and_orphan_autofit(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        orphan_md, baseline = _calibrate_orphan_markdown(
            tmp_path,
            prefix_frontmatter="---\ntheme: classic\npage_numbers: true\n---\n",
            extra_cli_args=("--margin", "11mm 15mm"),
        )
        assert baseline["pages"] == 2

        res = run_cli(
            [
                str(orphan_md),
                "-o",
                str(tmp_path / "t3_03.pdf"),
                "--margin",
                "11mm 15mm",
                "--strict",
                "--json",
            ]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        assert data["pages"] == 1
        assert data["layout_issues"] == []

    def test_t3_04_header_footer_below_8mm_margin_and_classic_overrides(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "t3_04.md"
        md_file.write_text(
            "---\n"
            "theme: classic\n"
            "page_numbers: true\n"
            "header: 'Header <script>alert(1)</script>'\n"
            "margin: 6mm 14mm\n"
            "---\n"
            "# Classic Small Margin\n\n"
            "Body paragraph remains intact.\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        summary = inspect_pdf(Path(data["output"]))
        assert "alert(1)" not in summary.full_text
        assert "Body paragraph remains intact." in summary.full_text

    def test_t3_05_h1_dedup_metadata_and_thai_auto_detect(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "t3_05.md"
        title_th = "รายงานการวิเคราะห์ระบบประจำปี"
        md_file.write_text(
            "---\n"
            f"title: {title_th}\n"
            "author: สมชาย ใจดี\n"
            "date: 2026-11-19\n"
            "header: false\n"
            "---\n"
            f"# **{title_th}**\n\n"
            "เนื้อหาภาษาไทยหลังหัวเรื่องที่ถูกลบซ้ำอัตโนมัติ\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--no-page-numbers", "--json"])
        assert res.returncode == EXIT_OK
        summary = inspect_pdf(Path(assert_success_json(res.stdout)["output"]))
        assert summary.per_page_text[0].count(title_th) == 1
        assert "สมชาย ใจดี" in summary.full_text

    def test_t3_06_mermaid_katex_local_image_csp_and_xss(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "diagram_ref.png")
        md_file = tmp_path / "t3_06.md"
        md_file.write_text(
            "# Combined Rich Pipeline\n\n"
            "Price is $49.99 and math is $\\alpha + \\beta = \\gamma$.\n\n"
            "$$\\int_0^1 x^2\\,dx = \\frac{1}{3}$$\n\n"
            "```mermaid\ngraph TD\n    A[Start] --> B[Verified]\n```\n\n"
            "![Local Ref](diagram_ref.png)\n\n"
            '<script>window.__xss=1;document.body.innerHTML="BAD";</script>\n',
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        assert data["mermaid_errors"] == []
        assert data["math_errors"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert summary.image_count >= 1
        assert summary.drawing_count > 0
        assert "$49.99" in summary.full_text
        assert "BAD" not in summary.full_text

    def test_t3_07_table_overflow_orphan_compaction_preview_and_json(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        preview_dir = tmp_path / "t3_07_previews"
        headers = " | ".join(f"Col{i}" for i in range(1, 10))
        sep = " | ".join(":---" for _ in range(1, 10))
        row = " | ".join(f"ValueToken_{i}_Wide" for i in range(1, 10))
        paragraphs = "\n\n".join(f"Detail item {i}: narrative prose." for i in range(1, 36))
        md_file = tmp_path / "t3_07.md"
        md_file.write_text(
            f"# Combined Auto-Fit & Preview\n\n"
            f"| {headers} |\n| {sep} |\n| {row} |\n\n{paragraphs}\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--preview-dir", str(preview_dir), "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        steps = {item["step"] for item in data["adjustments_applied"]}
        assert steps & {"table_wrap_compact", "table_proportional_scale"}
        assert len(data["preview_images"]) == data["pages"]
        inspect_preview_pngs(preview_dir, data["pages"])

    def test_t3_08_stdin_bom_base_dir_landscape_and_strict(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "banner.png")
        headers = " | ".join(f"Field{i}" for i in range(1, 11))
        sep = " | ".join(":---" for _ in range(1, 11))
        row = " | ".join(f"Item_{i}_Data" for i in range(1, 11))
        stdin_md = (
            "\ufeff# Stdin BOM Landscape\n\n"
            "![Banner](banner.png)\n\n"
            f"| {headers} |\n| {sep} |\n| {row} |\n"
        )
        out_pdf = tmp_path / "t3_08.pdf"
        res = run_cli(
            [
                "-",
                "--base-dir",
                str(tmp_path),
                "-o",
                str(out_pdf),
                "--page-size",
                "A4 landscape",
                "--strict",
                "--json",
            ],
            stdin_text=stdin_md,
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        summary = inspect_pdf(out_pdf)
        assert_page_size_mm(summary, 297.0, 210.0)
        assert summary.image_count >= 1

    def test_t3_09_cli_css_and_fm_css_with_allow_outside_base_dir(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "ext_asset.png")
        ext_css = tmp_path / "external.css"
        ext_css.write_text("h1 { letter-spacing: 0.5px; }\n", encoding="utf-8")

        base_dir = tmp_path / "workspace"
        base_dir.mkdir()
        (base_dir / "local_fm.css").write_text("p { margin-bottom: 6px; }\n", encoding="utf-8")
        md_file = base_dir / "t3_09.md"
        md_file.write_text(
            "---\ncss: local_fm.css\n---\n# Outside Opt-In Combo\n\n![Ext](../ext_asset.png)\n",
            encoding="utf-8",
        )
        res = run_cli(
            [
                str(md_file),
                "--css",
                str(ext_css),
                "--allow-outside-base-dir",
                "--strict",
                "--json",
            ]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout)
        assert data["embedded_images"] == 1
        assert data["warnings"] == []

    def test_t3_10_no_auto_fit_with_strict_on_wide_table_and_orphan(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        headers = " | ".join(f"WideHeader{i}" for i in range(1, 13))
        sep = " | ".join(":---" for _ in range(1, 13))
        row = " | ".join(f"LongTokenValue_{i}" for i in range(1, 13))
        md_file = tmp_path / "t3_10.md"
        md_file.write_text(
            f"# Strict No Auto-Fit\n\n| {headers} |\n| {sep} |\n| {row} |\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--no-auto-fit", "--strict", "--json"])
        assert res.returncode == EXIT_STRICT_ISSUE
        data = assert_strict_failure_json(res.stdout)
        assert data["adjustments_applied"] == []
        assert len(data["layout_issues"]) >= 1


# =============================================================================
# TIER 4: REALISTIC END-TO-END USER SCENARIOS (>= 6 SCENARIOS)
# =============================================================================


@pytest.mark.e2e
class TestTier4RealWorldScenarios:
    """Tier 4 realistic multi-feature documents representing real user workflows."""

    def test_t4_01_thai_corporate_sop_classic_theme(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "corp_logo.png", width=260, height=90)
        preview_dir = tmp_path / "sop_previews"
        md_file = tmp_path / "thai_sop.md"
        md_file.write_text(
            "---\n"
            "title: คู่มือมาตรฐานการปฏิบัติงาน (SOP)\n"
            "subtitle: ระบบอนุมัติเอกสารประจำไตรมาส 4\n"
            "author: ฝ่ายปฏิบัติการองค์กร\n"
            "date: 2026-11-19\n"
            "header: false\n"
            "---\n"
            "# คู่มือมาตรฐานการปฏิบัติงาน (SOP)\n\n"
            "![Company Logo](corp_logo.png)\n\n"
            "> [!IMPORTANT]\n"
            "> ทุกหน่วยงานต้องตรวจสอบข้อมูลให้ครบถ้วนก่อนส่งอนุมัติ\n\n"
            "| รหัส | ขั้นตอนการดำเนินงาน | ผู้รับผิดชอบ | ระยะเวลา | สถานะ |\n"
            "| :--- | :--- | :--- | :---: | :---: |\n"
            "| SOP-01 | ตรวจทานเอกสารต้นฉบับ | หัวหน้าแผนก | 1 วัน | พร้อม |\n"
            "| SOP-02 | ลงนามรับรองผล | ผู้อำนวยการ | 2 วัน | พร้อม |\n",
            encoding="utf-8",
        )
        res = run_cli(
            [str(md_file), "--preview-dir", str(preview_dir), "--no-page-numbers", "--json"]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        assert data["embedded_images"] == 1
        assert data["layout_issues"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert summary.per_page_text[0].count("คู่มือมาตรฐานการปฏิบัติงาน (SOP)") == 1
        assert "ฝ่ายปฏิบัติการองค์กร" in summary.full_text
        inspect_preview_pngs(preview_dir, data["pages"])

    def test_t4_02_university_engineering_lab_report_classic_theme(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "lab_report.md"
        md_file.write_text(
            "---\n"
            "theme: classic\n"
            "title: Lab 04 - RLC Circuit Frequency Response\n"
            "subtitle: EE302 Signals and Systems\n"
            "author: Nattapong K.\n"
            "date: 2026-11-19\n"
            "toc: true\n"
            "---\n"
            "## Objectives\n\n"
            "- [x] Calibrate oscilloscope probe ($10x$ attenuation)\n"
            "- [ ] Verify $25.00 component budget\n\n"
            "> [!TIP]\n> Record resonance frequency where phase shift crosses zero.\n\n"
            "> [!NOTE]\n> Supply voltage kept constant at $5\\text{ V}$ RMS.\n\n"
            "## Transfer Function\n\n"
            "$$H(j\\omega) = \\frac{1}{1 - \\omega^2 LC + j\\omega RC}$$\n\n"
            "| Frequency (Hz) | Gain (dB) | Phase (deg) |\n"
            "| ---: | ---: | ---: |\n"
            "| 100 | -0.2 | -5.1 |\n"
            "| 1000 | -3.0 | -45.0 |\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        assert data["math_errors"] == []
        assert data["layout_issues"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "EE302 Signals and Systems" in summary.full_text
        assert "$25.00" in summary.full_text

    def test_t4_03_academic_research_paper_paper_theme(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        preview_dir = tmp_path / "paper_previews"
        md_file = tmp_path / "research_paper.md"
        md_file.write_text(
            "---\n"
            "theme: paper\n"
            "lang: en\n"
            "title: Deterministic Headless Print Pagination\n"
            "author: Dr. Elena Vance\n"
            "affiliation: Department of Computer Science\n"
            "date: 2026-11-19\n"
            "abstract: We present a bounded DOM and PyMuPDF remediation loop for print PDFs.\n"
            "---\n"
            "## 1. Introduction\n\n"
            "Browser pagination requires print-media emulation prior to measurement[^cite1].\n\n"
            "## 2. System Pipeline\n\n"
            "```mermaid\n"
            "graph LR\n"
            "    MD[Markdown] --> HTML[Sanitized HTML]\n"
            "    HTML --> CDP[Print Emulation]\n"
            "    CDP --> PDF[Bounded Auto-Fit]\n"
            "```\n\n"
            "$$\\text{fill} = \\frac{y_{\\max} - m_{\\text{top}}}{H_{\\text{body}}}$$\n\n"
            "| Method | Pages | Overflow Resolved |\n"
            "| :--- | :---: | :---: |\n"
            "| Baseline | 4 | No |\n"
            "| Bounded Auto-Fit | 3 | Yes |\n\n"
            "[^cite1]: Chromium PrintToPDF Technical Specification.\n",
            encoding="utf-8",
        )
        res = run_cli(
            [str(md_file), "--preview-dir", str(preview_dir), "--strict", "--json"]
        )
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="paper")
        assert data["mermaid_errors"] == []
        assert data["math_errors"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "bounded DOM and PyMuPDF remediation" in summary.full_text
        assert "Chromium PrintToPDF Technical Specification." in summary.full_text
        assert summary.drawing_count > 0
        inspect_preview_pngs(preview_dir, data["pages"])

    def test_t4_04_engineering_architecture_rfc_classic_theme(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "rfc_042.md"
        long_endpoint = (
            "/api/v1/organizations/{org_id}/workspaces/{workspace_id}"
            "/exports/pdf-render-jobs/status"
        )
        md_file.write_text(
            "---\n"
            "theme: classic\n"
            "title: RFC-042 Standalone CDP Rendering Engine\n"
            "affiliation: Core Infrastructure\n"
            "date: 2026-11-19\n"
            "toc: true\n"
            "---\n"
            "## Protocol Sequence\n\n"
            "```mermaid\n"
            "sequenceDiagram\n"
            "    Client->>Browser: Emulation.setEmulatedMedia\n"
            "    Browser-->>Client: Page.printToPDF\n"
            "```\n\n"
            "## Guardrails\n\n"
            "> [!WARNING]\n> Always terminate ephemeral browser profiles in finally blocks.\n\n"
            "> [!CAUTION]\n> Never inject untrusted HTML before sanitization.\n\n"
            "```python\n"
            f'ENDPOINT_TEMPLATE = "{long_endpoint}"\n'
            "```\n\n"
            "| Endpoint | Method | Timeout |\n"
            "| :--- | :---: | ---: |\n"
            "| `/render` | POST | 120s |\n"
            "| `/health` | GET | 5s |\n",
            encoding="utf-8",
        )
        res = run_cli([str(md_file), "--strict", "--json"])
        assert res.returncode == EXIT_OK
        data = assert_success_json(res.stdout, expected_theme="classic")
        assert data["mermaid_errors"] == []
        assert data["layout_issues"] == []
        summary = inspect_pdf(Path(data["output"]))
        assert "Core Infrastructure" in summary.full_text
        assert summary.drawing_count > 0

    def test_t4_05_executive_proposal_and_self_healing_loop(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        md_file = tmp_path / "exec_proposal.md"
        cols = " | ".join(f"Region_{i:02d}_FY26" for i in range(1, 17))
        sep = " | ".join("---:" for _ in range(1, 17))
        row = " | ".join(f"${i * 125},000" for i in range(1, 17))
        original_content = (
            "---\n"
            "theme: classic\n"
            "title: FY26 Global Expansion Proposal\n"
            "subtitle: Regional Revenue & Capital Allocation\n"
            "date: 2026-11-19\n"
            "---\n"
            "# FY26 Global Expansion Proposal\n\n"
            "> Strong margin expansion across APAC and EMEA quarters.\n\n"
            f"| {cols} |\n| {sep} |\n| {row} |\n"
        )
        md_file.write_text(original_content, encoding="utf-8")

        # Run 1: Portrait strict check catches unresolved table_overflow (exit 6)
        res_portrait = run_cli(
            [str(md_file), "-o", str(tmp_path / "exec_portrait.pdf"), "--strict", "--json"]
        )
        assert res_portrait.returncode == EXIT_STRICT_ISSUE
        strict_data = assert_strict_failure_json(res_portrait.stdout)
        overflow_issues = [
            i for i in strict_data["layout_issues"] if i["kind"] == "table_overflow"
        ]
        assert len(overflow_issues) >= 1
        assert "landscape" in overflow_issues[0]["suggestion"].lower()

        # Run 2: Self-healing re-run on untouched .md with A4 landscape -> clean exit 0
        landscape_pdf = tmp_path / "exec_landscape.pdf"
        res_landscape = run_cli(
            [
                str(md_file),
                "-o",
                str(landscape_pdf),
                "--page-size",
                "A4 landscape",
                "--strict",
                "--json",
            ]
        )
        assert res_landscape.returncode == EXIT_OK
        clean_data = assert_success_json(res_landscape.stdout, expected_theme="classic")
        assert clean_data["layout_issues"] == []
        summary = inspect_pdf(landscape_pdf)
        assert_page_size_mm(summary, 297.0, 210.0)
        assert md_file.read_text(encoding="utf-8") == original_content

    def test_t4_06_classic_thai_station_protocol_and_paper_resume(
        self, require_browser: Path, tmp_path: Path
    ) -> None:
        create_test_png(tmp_path / "station_map.png", width=200, height=120)
        classic_md = tmp_path / "station_protocol.md"
        classic_md.write_text(
            "# แนวทางปฏิบัติประจำจุดตรวจคัดกรอง\n\n"
            "### จุดที่ 1: ลงทะเบียนผู้รับบริการ\n"
            "- ตรวจสอบบัตรประจำตัวประชาชน\n"
            "  - บันทึกเวลาเข้าพื้นที่\n"
            "- แจกเอกสารแนะนำขั้นตอน\n\n"
            "| รายการ | ภาพประกอบ |\n"
            "| :--- | :---: |\n"
            "| แผนผังจุดบริการ | ![Map](station_map.png) |\n",
            encoding="utf-8",
        )
        res_classic = run_cli(
            [str(classic_md), "--theme", "classic", "--no-page-numbers", "--strict", "--json"]
        )
        assert res_classic.returncode == EXIT_OK
        data_classic = assert_success_json(res_classic.stdout, expected_theme="classic")
        assert data_classic["embedded_images"] == 1
        classic_summary = inspect_pdf(Path(data_classic["output"]))
        assert "1 / 1" not in classic_summary.full_text
        assert "Page 1" not in classic_summary.full_text

        paper_md = tmp_path / "paper_cv.md"
        paper_md.write_text(
            "# Jordan Lee — Staff Systems Engineer\n\n"
            "## Experience\n\n"
            "- Built offline PDF rendering engines with deterministic pagination.\n"
            "- Reduced CI latency by 45% across Windows and Linux runners.\n",
            encoding="utf-8",
        )
        res_paper = run_cli(
            [
                str(paper_md),
                "--theme",
                "paper",
                "--accent",
                "#111827",
                "--margin",
                "comfortable",
                "--strict",
                "--json",
            ]
        )
        assert res_paper.returncode == EXIT_OK
        data_paper = assert_success_json(res_paper.stdout, expected_theme="paper")
        assert "Jordan Lee" in inspect_pdf(Path(data_paper["output"])).full_text
