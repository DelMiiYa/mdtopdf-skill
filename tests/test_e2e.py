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

    def test_e2e_sparse_trailing_page_9_to_14_percent_flagged_and_compacted(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _require_browser()

        calibrated_md: Path | None = None
        calibrated_fill = 0.0
        for count in range(44, 68):
            candidate = tmp_path / f"sparse_{count}.md"
            probe_pdf = tmp_path / f"sparse_{count}_probe.pdf"
            paragraphs = "\n\n".join(
                f"Paragraph {i}: Dense technical prose line used to calibrate trailing page fill."
                for i in range(1, count + 1)
            )
            candidate.write_text(f"# Sparse Calibration\n\n{paragraphs}\n", encoding="utf-8")
            code = main(
                [str(candidate), "-o", str(probe_pdf), "--no-auto-fit", "--json"]
            )
            assert code == EXIT_OK
            probe = json.loads(capsys.readouterr().out.strip())
            if probe["pages"] == 2 and 0.08 <= probe["last_page_fill_ratio"] < 0.15:
                assert any(
                    issue["kind"] == "trailing_orphan_page" for issue in probe["layout_issues"]
                )
                calibrated_md = candidate
                calibrated_fill = float(probe["last_page_fill_ratio"])
                break

        assert calibrated_md is not None, (
            f"Could not calibrate a 2-page trailing page in [0.08, 0.15) (last={calibrated_fill})"
        )

        compacted_pdf = tmp_path / "sparse_compacted.pdf"
        code_fit = main(
            [str(calibrated_md), "-o", str(compacted_pdf), "--strict", "--json"]
        )
        assert code_fit == EXIT_OK
        fit_data = json.loads(capsys.readouterr().out.strip())
        assert fit_data["pages"] == 1
        assert fit_data["layout_issues"] == []
        assert any(
            adj["step"].startswith(("spacing_compact", "font_spacing_compact"))
            for adj in fit_data["adjustments_applied"]
        )

    def test_e2e_tight_pure_number_math_rendered_and_currency_literal(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _require_browser()

        md_file = tmp_path / "pure_number_math.md"
        out_pdf = tmp_path / "pure_number_math.pdf"
        md_file.write_text(
            "# Numeric Math vs Currency\n\n"
            "Tight math spans $20.17$ and $42$ render via KaTeX.\n\n"
            "Currency prose $5 and $10, $1,234.50, $5 - $10, and $5-$10 stays literal.\n",
            encoding="utf-8",
        )

        code = main([str(md_file), "-o", str(out_pdf), "--strict", "--json"])
        assert code == EXIT_OK
        data = json.loads(capsys.readouterr().out.strip())
        assert data["math_errors"] == []
        assert data["warnings"] == []
        assert data["layout_issues"] == []

        with pymupdf.open(str(out_pdf)) as doc:  # type: ignore[no-untyped-call]
            full_text = "\n".join(str(p.get_text("text")) for p in doc)

        assert "20.17" in full_text
        assert "42" in full_text
        assert "$20.17$" not in full_text
        assert "$42$" not in full_text
        assert "$5 and $10" in full_text
        assert "$1,234.50" in full_text
        assert "$5 - $10" in full_text
        assert "$5-$10" in full_text

    def test_e2e_multiline_heading_does_not_split_across_pages(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _require_browser()

        filler_sentence = (
            "Engineering architecture notes covering pagination stability, "
            "offline vector rendering, typography metrics, and print layout validation "
            "for multi-page specifications."
        )
        long_heading = (
            "## HEADING_START_ALPHA Comprehensive Multi-Line Architecture Review and "
            "HEADING_MIDDLE_BETA Verification Protocol for Distributed Printing "
            "Pipelines Under Tight Boundary Conditions Across Teams HEADING_END_GAMMA"
        )
        diagram_block = (
            "```mermaid\n"
            "flowchart LR\n"
            "    N1[Stage 1] --> N2[Stage 2] --> N3[Stage 3]\n"
            "```"
        )

        saw_page_1 = False
        saw_page_2 = False
        for count in range(18, 26):
            md_file = tmp_path / f"heading_split_{count}.md"
            out_pdf = tmp_path / f"heading_split_{count}.pdf"
            filler = "\n\n".join(
                f"Paragraph {idx}: {filler_sentence}"
                for idx in range(1, count + 1)
            )
            md_file.write_text(
                f"# Heading Pagination Check\n\n"
                f"{filler}\n\n"
                f"{long_heading}\n\n"
                f"Lead paragraph introducing the diagram below.\n\n"
                f"{diagram_block}\n",
                encoding="utf-8",
            )
            code = main([str(md_file), "-o", str(out_pdf), "--no-auto-fit", "--json"])
            assert code == EXIT_OK
            data = json.loads(capsys.readouterr().out.strip())
            assert data["mermaid_errors"] == []
            assert data["math_errors"] == []

            with pymupdf.open(str(out_pdf)) as doc:  # type: ignore[no-untyped-call]
                page_texts = [str(page.get_text("text")) for page in doc]

            token_pages: dict[str, list[int]] = {}
            for token in (
                "HEADING_START_ALPHA",
                "HEADING_MIDDLE_BETA",
                "HEADING_END_GAMMA",
            ):
                token_pages[token] = [
                    idx for idx, text in enumerate(page_texts) if token in text
                ]
                assert len(token_pages[token]) == 1, (
                    f"Missing or duplicated token {token} at count={count}: {token_pages}"
                )

            pages_set = {pages[0] for pages in token_pages.values()}
            assert len(pages_set) == 1, (
                f"Multi-line heading split across pages at count={count}: {token_pages}"
            )
            heading_page = next(iter(pages_set))
            if heading_page == 0:
                saw_page_1 = True
            elif heading_page == 1:
                saw_page_2 = True
            if saw_page_1 and saw_page_2:
                break

        assert saw_page_1 and saw_page_2, (
            f"Sweep did not cross page 1->2 boundary (saw_page_1={saw_page_1}, "
            f"saw_page_2={saw_page_2})"
        )
