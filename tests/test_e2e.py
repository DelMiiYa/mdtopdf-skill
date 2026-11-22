"""End-to-end smoke tests requiring a headless browser (Edge/Chrome/Chromium)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pymupdf
import pytest

from mdtopdf_skill import __version__
from mdtopdf_skill.cdp_browser import find_browser_executable
from mdtopdf_skill.cli import EXIT_OK, EXIT_STRICT_ISSUE, main


def _require_browser() -> str:
    """Return browser executable path or skip the calling test."""
    try:
        return str(find_browser_executable())
    except FileNotFoundError as exc:
        pytest.skip(f"No headless browser available: {exc}")


@pytest.mark.e2e
class TestCliEndToEnd:
    """Real end-to-end conversion tests using installed headless browser."""

    def test_e2e_real_conversion_with_thai_tables_mermaid_and_katex(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _require_browser()

        md_file = tmp_path / "thai_rich.md"
        out_pdf = tmp_path / "thai_rich.pdf"
        md_content = (
            "---\n"
            "theme: paper\n"
            "title: รายงานสรุปผลการทดสอบระบบ\n"
            "---\n\n"
            "# รายงานสรุปผลการทดสอบระบบ\n\n"
            "เอกสารนี้ทดสอบการแสดงผลภาษาไทย ตาราง สมการ $E = mc^2$ และแผนภาพ Mermaid\n\n"
            "| ลำดับ | รายการ | สถานะ | หมายเหตุ |\n"
            "| :--- | :--- | :---: | ---: |\n"
            "| 1 | การติดตั้งโมดูล | ผ่าน | พร้อมใช้งาน |\n"
            "| 2 | การแปลงตัวอักษรไทย | ผ่าน | สระและวรรณยุกต์ถูกต้อง |\n\n"
            "```mermaid\ngraph TD\n    A[Start] --> B[Verified]\n```\n"
        )
        md_file.write_text(md_content, encoding="utf-8")

        code = main([str(md_file), "-o", str(out_pdf), "--strict", "--json"])
        assert code == EXIT_OK
        assert out_pdf.is_file() and out_pdf.stat().st_size > 0

        data = json.loads(capsys.readouterr().out.strip())
        assert data["ok"] is True
        assert data["theme"] == "paper"
        assert data["version"] == __version__
        assert "core_version" not in data
        assert data["mermaid_errors"] == []
        assert data["math_errors"] == []
        assert data["layout_issues"] == []

        with pymupdf.open(str(out_pdf)) as doc:  # type: ignore[no-untyped-call]
            assert doc.page_count >= 1
            assert doc.page_count == data["pages"]

    def test_e2e_stdin_mode_and_default_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _require_browser()

        stdin_content = "# Document From Stdin\n\n- Item A\n- Item B (ภาษาไทย)\n"
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_content))
        out_pdf = tmp_path / "stdin_rendered.pdf"
        assert main(["-", "--base-dir", str(tmp_path), "-o", str(out_pdf)]) == EXIT_OK
        with pymupdf.open(str(out_pdf)) as doc:  # type: ignore[no-untyped-call]
            assert doc.page_count >= 1

        default_md = tmp_path / "default_out.md"
        default_md.write_text("# Default Output\n\nBody.\n", encoding="utf-8")
        assert main([str(default_md)]) == EXIT_OK
        assert default_md.with_suffix(".pdf").is_file()

    def test_e2e_table_overflow_autofit_and_strict_exit_6(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _require_browser()

        headers = " | ".join(f"Col{i}" for i in range(1, 19))
        sep = " | ".join(":---" for _ in range(1, 19))
        row = " | ".join(f"UnbreakableLongCellValue_{i}_XYZ" for i in range(1, 19))
        md_file = tmp_path / "extreme.md"
        md_file.write_text(
            f"# Extreme Table\n\n| {headers} |\n| {sep} |\n| {row} |\n", encoding="utf-8"
        )
        code = main([str(md_file), "--strict", "--json"])
        assert code == EXIT_STRICT_ISSUE
        data = json.loads(capsys.readouterr().out.strip())
        assert data["ok"] is False
        assert data["code"] == EXIT_STRICT_ISSUE
        assert any(issue["kind"] == "table_overflow" for issue in data["layout_issues"])

    def test_e2e_suppressed_missing_image_warning(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _require_browser()

        md_file = tmp_path / "missing_img.md"
        out_pdf = tmp_path / "missing_img.pdf"
        md_file.write_text(
            "# Missing Image Test\n\n![Missing Image](does_not_exist_image_xyz.png)\n",
            encoding="utf-8",
        )

        code = main([str(md_file), "-o", str(out_pdf), "--json"])
        assert code == EXIT_OK
        captured = capsys.readouterr()
        assert "Warning: 1 suppressed image(s)" in captured.err
        assert "does_not_exist_image_xyz.png" in captured.err
        data = json.loads(captured.out.strip())
        assert data["ok"] is True
        assert "does_not_exist_image_xyz.png" in data["suppressed_images"]
        assert data["pages"] >= 1

    def test_e2e_commonmark_nested_lists_tight_lists_and_math_in_list(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _require_browser()

        md_file = tmp_path / "commonmark_lists.md"
        out_pdf = tmp_path / "commonmark_lists.pdf"
        md_file.write_text(
            "# CommonMark List Continuity\n\n"
            "Intro paragraph immediately before tight list:\n"
            "1. Parent ordered item\n"
            "  - Two-space nested sub-bullet\n"
            "   - Three-space nested sub-bullet\n"
            "2. Item containing display math\n"
            "  $$x^2 + y^2 = z^2$$\n"
            "  - Sub-bullet after display math\n"
            "3. Final ordered item\n\n"
            "## Two-Digit List\n\n"
            "10. Double-digit item\n"
            "  - Nested child under ten\n",
            encoding="utf-8",
        )

        code = main([str(md_file), "-o", str(out_pdf), "--strict", "--json"])
        assert code == EXIT_OK
        data = json.loads(capsys.readouterr().out.strip())
        assert data["math_errors"] == []
        assert data["layout_issues"] == []

        with pymupdf.open(str(out_pdf)) as doc:  # type: ignore[no-untyped-call]
            full_text = "\n".join(str(p.get_text("text")) for p in doc)
        assert "1. Parent ordered item" in full_text
        assert "2. Item containing display math" in full_text
        assert "3. Final ordered item" in full_text
        assert "10. Double-digit item" in full_text
