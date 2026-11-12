"""End-to-end tests requiring a headless browser (Edge/Chrome/Chromium).

These tests run the actual rendering pipeline through main() and verify that
a valid PDF with at least 1 page is created on disk. They skip gracefully
when no compatible browser is found.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pymupdf
import pytest
from mdtopdf_core import find_browser_executable

from mdtopdf_skill.cli import EXIT_OK, main


def _require_browser() -> str:
    """Return browser executable path or skip the calling test."""
    try:
        return str(find_browser_executable())
    except FileNotFoundError as exc:
        pytest.skip(f"No headless browser available: {exc}")


@pytest.mark.e2e
class TestCliEndToEnd:
    """Real end-to-end conversion tests using installed headless browser."""

    def test_e2e_real_conversion_with_thai_and_tables(self, tmp_path: Path) -> None:
        _require_browser()

        md_file = tmp_path / "thai_table.md"
        out_pdf = tmp_path / "thai_table.pdf"
        md_content = (
            "# รายงานสรุปผลการทดสอบระบบ\n\n"
            "เอกสารนี้ทดสอบการแสดงผลภาษาไทย ตาราง และการตัดหน้าอัตโนมัติ\n\n"
            "| ลำดับ | รายการ | สถานะ | หมายเหตุ |\n"
            "| :--- | :--- | :---: | ---: |\n"
            "| 1 | การติดตั้งโมดูล | ผ่าน | พร้อมใช้งาน |\n"
            "| 2 | การแปลงตัวอักษรไทย | ผ่าน | สระและวรรณยุกต์ถูกต้อง |\n"
            "| 3 | ตารางข้อมูล Markdown | ผ่าน | เส้นขอบและสีชัดเจน |\n\n"
            "```python\n"
            "# Code block highlighting test\n"
            "def greet(name: str) -> str:\n"
            "    return f'สวัสดี, {name}!'\n"
            "```\n"
        )
        md_file.write_text(md_content, encoding="utf-8")

        code = main([
            str(md_file),
            "-o",
            str(out_pdf),
            "--font-size",
            "10",
            "--line-height",
            "1.5",
            "--margin",
            "comfortable",
            "--accent",
            "#1565c0",
            "--title",
            "Thai Table E2E Test",
        ])
        assert code == EXIT_OK
        assert out_pdf.exists()
        assert out_pdf.stat().st_size > 0

        with pymupdf.open(str(out_pdf)) as doc:  # type: ignore[no-untyped-call]
            assert doc.page_count >= 1

    def test_e2e_json_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _require_browser()

        md_file = tmp_path / "json_sample.md"
        md_file.write_text(
            "# JSON Flag E2E Test\n\nThis tests the --json output schema on real conversion.\n",
            encoding="utf-8",
        )

        code = main([str(md_file), "--json"])
        assert code == EXIT_OK

        captured = capsys.readouterr()
        data = json.loads(captured.out.strip())

        assert data["ok"] is True
        assert data["pages"] >= 1
        assert data["embedded_images"] == 0
        assert data["suppressed_images"] == []
        assert data["elapsed_seconds"] > 0

        out_path = Path(data["output"])
        assert out_path.exists()
        assert out_path.stat().st_size > 0

        with pymupdf.open(str(out_path)) as doc:  # type: ignore[no-untyped-call]
            assert doc.page_count == data["pages"]

    def test_e2e_stdin_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _require_browser()

        stdin_content = (
            "# Document From Stdin\n\n"
            "This document was piped through standard input.\n\n"
            "- Item A\n"
            "- Item B\n"
            "- Item C (ภาษาไทย)\n"
        )
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_content))

        out_pdf = tmp_path / "stdin_rendered.pdf"
        code = main([
            "-",
            "--base-dir",
            str(tmp_path),
            "-o",
            str(out_pdf),
            "--margin",
            "compact",
        ])
        assert code == EXIT_OK
        assert out_pdf.exists()
        assert out_pdf.stat().st_size > 0

        with pymupdf.open(str(out_pdf)) as doc:  # type: ignore[no-untyped-call]
            assert doc.page_count >= 1

    def test_e2e_default_output_path(self, tmp_path: Path) -> None:
        _require_browser()

        md_file = tmp_path / "default_out.md"
        expected_pdf = tmp_path / "default_out.pdf"
        md_file.write_text("# Default Output Test\n\nNo -o flag passed.\n", encoding="utf-8")

        code = main([str(md_file)])
        assert code == EXIT_OK
        assert expected_pdf.exists()
        assert expected_pdf.stat().st_size > 0

        with pymupdf.open(str(expected_pdf)) as doc:  # type: ignore[no-untyped-call]
            assert doc.page_count >= 1

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
        assert out_pdf.exists()

        captured = capsys.readouterr()
        assert "Warning: 1 suppressed image(s)" in captured.err
        assert "does_not_exist_image_xyz.png" in captured.err

        data = json.loads(captured.out.strip())
        assert data["ok"] is True
        assert "does_not_exist_image_xyz.png" in data["suppressed_images"]
        assert data["pages"] >= 1
