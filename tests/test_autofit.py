"""Unit tests for mdtopdf_skill.autofit and mdtopdf_skill.renderer."""

from __future__ import annotations

import base64
from pathlib import Path
from types import TracebackType
from typing import Any

import PIL.Image
import pymupdf
import pytest

import mdtopdf_skill.renderer as renderer_mod
from mdtopdf_skill.autofit import inspect_pdf_pages, run_autofit_render
from mdtopdf_skill.markdown_pipeline import BuiltDocument
from mdtopdf_skill.models import (
    AutoFitOutcome,
    ConversionOptions,
    FrontmatterData,
    PageGeometry,
    compute_page_geometry,
)
from mdtopdf_skill.renderer import _count_embedded_images, render_pdf
from mdtopdf_skill.sanitizer import ASSETS_DIR


def _make_pdf_bytes(page_specs: list[dict[str, Any]]) -> bytes:
    """Create a real multi-page PDF byte stream via PyMuPDF for deterministic inspection."""
    with pymupdf.open() as doc:  # type: ignore[no-untyped-call]
        for spec in page_specs:
            width = float(spec.get("width", 595.28))
            height = float(spec.get("height", 841.89))
            page = doc.new_page(width=width, height=height)
            for x, y, text in spec.get("texts", []):
                page.insert_text((x, y), text, fontsize=10)
            for rect_tuple in spec.get("rects", []):
                rect = pymupdf.Rect(*rect_tuple)  # type: ignore[no-untyped-call]
                page.draw_rect(rect, color=(0, 0, 1), fill=(0.9, 0.9, 1.0))
            for img_rect_tuple in spec.get("images", []):
                rect = pymupdf.Rect(*img_rect_tuple)  # type: ignore[no-untyped-call]
                img = PIL.Image.new("RGB", (20, 20), (30, 100, 200))
                import io

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                page.insert_image(rect, stream=buf.getvalue())
        return bytes(doc.tobytes())


class _FakeCdpSession:
    """Deterministic in-memory stub implementing the CdpSession surface for autofit tests."""

    def __init__(
        self,
        *,
        initial_report: dict[str, Any],
        inspect_reports: list[dict[str, Any]] | None = None,
        pdf_sequence: list[bytes] | None = None,
    ) -> None:
        self.initial_report = initial_report
        self.inspect_reports = list(inspect_reports or [])
        self.pdf_sequence = list(pdf_sequence or [_make_pdf_bytes([{"texts": [(50, 100, "p1")]}])])
        self.injected_styles: list[tuple[str, str]] = []
        self.print_calls: list[dict[str, Any]] = []
        self.evaluations: list[str] = []
        self.navigated_paths: list[str] = []
        self.emulated_geometries: list[PageGeometry] = []

    def __enter__(self) -> _FakeCdpSession:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None

    def emulate_print_viewport(self, geometry: PageGeometry) -> None:
        self.emulated_geometries.append(geometry)

    def navigate(self, url_or_path: str | Path, timeout: float | None = None) -> None:
        _ = timeout
        self.navigated_paths.append(str(url_or_path))

    def evaluate(
        self,
        expression: str,
        await_promise: bool = True,
        return_by_value: bool = True,
    ) -> Any:
        _ = (await_promise, return_by_value)
        self.evaluations.append(expression)
        if "__mdtopdfRenderAndInspect" in expression:
            return self.initial_report
        if "__mdtopdfInspectGeometry" in expression:
            if self.inspect_reports:
                return self.inspect_reports.pop(0)
            return {"table_overflows": [], "wide_elements": [], "printable_width_px": 672.0}
        return None

    def inject_style(self, style_id: str, css_text: str) -> None:
        self.injected_styles.append((style_id, css_text))

    def print_to_pdf(
        self,
        *,
        geometry: PageGeometry | None = None,
        display_header_footer: bool = False,
        header_template: str = "",
        footer_template: str = "",
        prefer_css_page_size: bool | None = None,
        print_background: bool = True,
    ) -> bytes:
        self.print_calls.append(
            {
                "geometry": geometry,
                "display_header_footer": display_header_footer,
                "header_template": header_template,
                "footer_template": footer_template,
                "prefer_css_page_size": prefer_css_page_size,
                "print_background": print_background,
            }
        )
        if len(self.pdf_sequence) > 1:
            return self.pdf_sequence.pop(0)
        return self.pdf_sequence[0]


class TestInspectPdfPages:
    """Tests for PyMuPDF body-rect inspection and orphan page detection."""

    def test_empty_bytes_returns_zeroed_inspection(self) -> None:
        insp = inspect_pdf_pages(b"", 42.5, 42.5)
        assert insp.page_count == 0
        assert insp.last_page_fill_ratio == 0.0
        assert insp.body_line_count == 0
        assert insp.body_images == 0
        assert insp.body_vector_diagrams == 0
        assert insp.is_trailing_orphan is False

    def test_single_sparse_page_not_flagged_as_orphan(self) -> None:
        pdf = _make_pdf_bytes([{"texts": [(50, 70, "Single line on page 1")]}])
        insp = inspect_pdf_pages(pdf, 40.0, 40.0)
        assert insp.page_count == 1
        assert insp.body_line_count == 1
        assert insp.is_trailing_orphan is False

    def test_multipage_sparse_body_ignoring_header_footer_is_orphan(self) -> None:
        # Margins: 42.5pt top & bottom on 841.89pt tall A4 page.
        # Running header at y=20, footer at y=825 -> strictly outside body rect!
        pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1 content")]},
                {
                    "texts": [
                        (50, 20, "Running Header Title"),
                        (50, 70, "Trailing orphan body line."),
                        (50, 825, "Page 2 / 2"),
                    ]
                },
            ]
        )
        insp = inspect_pdf_pages(pdf, 42.5, 42.5)
        assert insp.page_count == 2
        assert insp.body_line_count == 1
        assert insp.body_images == 0
        assert insp.body_vector_diagrams == 0
        assert insp.is_trailing_orphan is True

    def test_multipage_with_body_image_is_not_orphan(self) -> None:
        pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"images": [(60, 80, 200, 180)]},
            ]
        )
        insp = inspect_pdf_pages(pdf, 42.5, 42.5)
        assert insp.page_count == 2
        assert insp.body_images == 1
        assert insp.is_trailing_orphan is False

    def test_multipage_with_vector_diagram_is_not_orphan(self) -> None:
        pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"rects": [(60, 80, 240, 200)]},
            ]
        )
        insp = inspect_pdf_pages(pdf, 42.5, 42.5)
        assert insp.page_count == 2
        assert insp.body_vector_diagrams == 1
        assert insp.is_trailing_orphan is False

    def test_full_page_bg_and_thin_rules_ignored_for_vector_diagrams(self) -> None:
        pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {
                    "texts": [(50, 70, "Single line")],
                    "rects": [
                        (0, 0, 595, 841),  # full-page background
                        (50, 90, 500, 92),  # 2pt horizontal rule
                    ],
                },
            ]
        )
        insp = inspect_pdf_pages(pdf, 42.5, 42.5)
        assert insp.body_vector_diagrams == 0
        assert insp.body_line_count == 1
        assert insp.is_trailing_orphan is True

    def test_multipage_well_filled_final_page_is_not_orphan(self) -> None:
        texts = [(50, 70 + i * 24, f"Substantial body line {i}") for i in range(12)]
        pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": texts},
            ]
        )
        insp = inspect_pdf_pages(pdf, 42.5, 42.5)
        assert insp.page_count == 2
        assert insp.body_line_count == 12
        assert insp.last_page_fill_ratio > 0.15
        assert insp.is_trailing_orphan is False

    def test_multipage_five_lines_or_eleven_percent_fill_is_orphan(self) -> None:
        texts = [(50, 62 + i * 14, f"Trailing line {i}") for i in range(5)]
        pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": texts},
            ]
        )
        insp = inspect_pdf_pages(pdf, 42.5, 42.5)
        assert insp.page_count == 2
        assert insp.body_line_count == 5
        assert 0.08 <= insp.last_page_fill_ratio < 0.15
        assert insp.is_trailing_orphan is True

    def test_multipage_seven_lines_low_fill_vs_above_threshold(self) -> None:
        tight_texts = [(50, 56 + i * 10, f"Tight line {i}") for i in range(7)]
        tight_pdf = _make_pdf_bytes([{"texts": [(50, 100, "P1")]}, {"texts": tight_texts}])
        tight_insp = inspect_pdf_pages(tight_pdf, 42.5, 42.5)
        assert tight_insp.body_line_count == 7
        assert tight_insp.last_page_fill_ratio < 0.15
        assert tight_insp.is_trailing_orphan is True

        spaced_texts = [(50, 70 + i * 22, f"Spaced line {i}") for i in range(7)]
        spaced_pdf = _make_pdf_bytes([{"texts": [(50, 100, "P1")]}, {"texts": spaced_texts}])
        spaced_insp = inspect_pdf_pages(spaced_pdf, 42.5, 42.5)
        assert spaced_insp.body_line_count == 7
        assert spaced_insp.last_page_fill_ratio >= 0.15
        assert spaced_insp.is_trailing_orphan is False

    def test_degenerate_huge_margins_fallback_to_full_page(self) -> None:
        pdf = _make_pdf_bytes([{"texts": [(50, 100, "Body")]}])
        insp = inspect_pdf_pages(pdf, 500.0, 500.0)
        assert insp.page_count == 1
        assert insp.body_line_count == 1


class TestRunAutofitRender:
    """Tests for table remediation, wide elements, orphan retries, and error parsing."""

    def test_clean_single_page_render_no_adjustments(self) -> None:
        session = _FakeCdpSession(
            initial_report={
                "mermaid_errors": [],
                "math_errors": [],
                "table_overflows": [],
                "wide_elements": [],
                "printable_width_px": 672.76,
            }
        )
        geom = compute_page_geometry("A4 portrait", "15mm 16mm")
        opts = ConversionOptions()
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            opts,
            (False, "", ""),
            geom,
        )
        assert outcome.page_count == 1
        assert outcome.layout_issues == []
        assert outcome.adjustments_applied == []
        assert session.injected_styles == []

    def test_no_auto_fit_skips_remediation_but_reports_all_issues(self) -> None:
        orphan_pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": [(50, 70, "Orphan line")]},
            ]
        )
        session = _FakeCdpSession(
            initial_report={
                "mermaid_errors": [{"index": 0, "message": "Syntax error", "snippet": "graph"}],
                "math_errors": [
                    {"index": 1, "display_mode": True, "message": "Undefined", "tex": "\\bad"}
                ],
                "table_overflows": [
                    {
                        "table_index": 0,
                        "rect_width": 900.0,
                        "scroll_width": 900.0,
                        "overflow_px": 227.2,
                        "column_count": 10,
                    }
                ],
                "wide_elements": [
                    {"tag": "katex-display", "overflow_px": 150.0, "in_table": False},
                    {"tag": "img", "overflow_px": 80.0, "in_table": True},
                ],
            },
            pdf_sequence=[orphan_pdf],
        )
        geom = compute_page_geometry("A4 portrait", "15mm 16mm")
        opts = ConversionOptions(auto_fit=False)
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            opts,
            (False, "", ""),
            geom,
        )
        assert session.injected_styles == []
        assert outcome.adjustments_applied == []
        assert len(outcome.mermaid_errors) == 1
        assert len(outcome.math_errors) == 1
        kinds = [issue.kind for issue in outcome.layout_issues]
        assert kinds == ["table_overflow", "wide_element", "trailing_orphan_page"]

    def test_table_tier1_resolves_overflow_and_never_raises_small_font(self) -> None:
        session = _FakeCdpSession(
            initial_report={
                "table_overflows": [
                    {
                        "table_index": 0,
                        "rect_width": 750.0,
                        "scroll_width": 750.0,
                        "overflow_px": 77.2,
                        "column_count": 8,
                    }
                ],
                "wide_elements": [],
            },
            inspect_reports=[{"table_overflows": [], "wide_elements": []}],
        )
        geom = compute_page_geometry("A4 portrait", "15mm 16mm")
        opts = ConversionOptions(font_size_pt=6.5)
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            opts,
            (False, "", ""),
            geom,
        )
        assert [a.step for a in outcome.adjustments_applied] == ["table_wrap_compact"]
        assert outcome.layout_issues == []
        style_id, css_text = session.injected_styles[0]
        assert style_id == "mdtopdf-autofit-tables"
        assert "font-size: 6.5pt !important;" in css_text

    def test_table_tier2_scales_and_reports_remaining_overflow(self) -> None:
        overflow_entry = {
            "table_index": 0,
            "rect_width": 1200.0,
            "scroll_width": 1200.0,
            "overflow_px": 527.2,
            "column_count": 18,
        }
        session = _FakeCdpSession(
            initial_report={"table_overflows": [overflow_entry], "wide_elements": []},
            inspect_reports=[
                {"table_overflows": [overflow_entry], "wide_elements": []},
                {
                    "table_overflows": [
                        {
                            "table_index": 0,
                            "rect_width": 780.0,
                            "scroll_width": 1200.0,
                            "overflow_px": 107.2,
                            "column_count": 18,
                        }
                    ],
                    "wide_elements": [],
                },
            ],
        )
        geom = compute_page_geometry("A4 portrait", "15mm 16mm")
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            ConversionOptions(),
            (False, "", ""),
            geom,
        )
        assert [a.step for a in outcome.adjustments_applied] == [
            "table_wrap_compact",
            "table_proportional_scale",
        ]
        assert "transform: scale(0.650)" in session.injected_styles[1][1]
        assert len(outcome.layout_issues) == 1
        assert outcome.layout_issues[0].kind == "table_overflow"
        assert outcome.layout_issues[0].table_index == 0

    def test_orphan_remediation_succeeds_respecting_10mm_hf_floor(self) -> None:
        two_page_orphan = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": [(50, 70, "Last line")]},
            ]
        )
        one_page_fit = _make_pdf_bytes([{"texts": [(50, 100, "All on page 1")]}])
        session = _FakeCdpSession(
            initial_report={"table_overflows": [], "wide_elements": []},
            inspect_reports=[
                {"table_overflows": [], "wide_elements": []},  # step 1 still 2 pages
                {"table_overflows": [], "wide_elements": []},  # step 2 reduces to 1 page
            ],
            pdf_sequence=[two_page_orphan, two_page_orphan, one_page_fit],
        )
        geom = compute_page_geometry("A4 portrait", "11mm 15mm")
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            ConversionOptions(),
            (True, "<span>H</span>", "<span>F</span>"),
            geom,
        )
        assert outcome.page_count == 1
        assert outcome.layout_issues == []
        assert [a.step for a in outcome.adjustments_applied] == ["font_spacing_compact_2"]
        # Top/bottom margin floor with display_header_footer=True is 10mm (11mm -> 10mm, never 9mm)
        last_print_geom: PageGeometry = session.print_calls[-1]["geometry"]
        assert last_print_geom.margin_top_mm == 10.0
        assert last_print_geom.margin_bottom_mm == 10.0

    def test_orphan_remediation_never_raises_below_floor_and_reverts_when_uncompactable(
        self,
    ) -> None:
        two_page_orphan = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": [(50, 70, "Forced break line")]},
            ]
        )
        session = _FakeCdpSession(
            initial_report={"table_overflows": [], "wide_elements": []},
            inspect_reports=[
                {"table_overflows": [], "wide_elements": []},
                {"table_overflows": [], "wide_elements": []},
                {"table_overflows": [], "wide_elements": []},
            ],
            pdf_sequence=[two_page_orphan, two_page_orphan, two_page_orphan, two_page_orphan],
        )
        geom = compute_page_geometry("A4 portrait", "6mm 14mm")
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            ConversionOptions(),
            (False, "", ""),
            geom,
        )
        assert outcome.page_count == 2
        assert outcome.adjustments_applied == []
        assert [issue.kind for issue in outcome.layout_issues] == ["trailing_orphan_page"]
        # Margin was 6mm (< 8mm floor): must remain 6mm, never raised
        for call in session.print_calls:
            assert call["geometry"].margin_top_mm == 6.0
        # Final style injection clears mdtopdf-autofit-spacing
        assert session.injected_styles[-1] == ("mdtopdf-autofit-spacing", "")

    def test_orphan_step_rejected_if_it_introduces_new_table_overflow(self) -> None:
        two_page_orphan = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": [(50, 70, "Orphan")]},
            ]
        )
        one_page_fit = _make_pdf_bytes([{"texts": [(50, 100, "One page")]}])
        session = _FakeCdpSession(
            initial_report={"table_overflows": [], "wide_elements": []},
            inspect_reports=[
                {"table_overflows": [{"table_index": 0}], "wide_elements": []},  # step 1 rejected
                {"table_overflows": [], "wide_elements": []},  # step 2 accepted
            ],
            pdf_sequence=[two_page_orphan, one_page_fit, one_page_fit],
        )
        geom = compute_page_geometry("A4 portrait", "15mm 16mm")
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            ConversionOptions(),
            (False, "", ""),
            geom,
        )
        assert [a.step for a in outcome.adjustments_applied] == ["font_spacing_compact_2"]

    def test_orphan_remediation_triggers_on_five_line_eleven_percent_page(self) -> None:
        five_line_orphan = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": [(50, 62 + i * 14, f"Sparse trailing {i}") for i in range(5)]},
            ]
        )
        one_page_fit = _make_pdf_bytes([{"texts": [(50, 100, "Compacted onto page 1")]}])
        session = _FakeCdpSession(
            initial_report={"table_overflows": [], "wide_elements": []},
            inspect_reports=[{"table_overflows": [], "wide_elements": []}],
            pdf_sequence=[five_line_orphan, one_page_fit],
        )
        geom = compute_page_geometry("A4 portrait", "12mm 14mm")
        outcome = run_autofit_render(
            session,  # type: ignore[arg-type]
            BuiltDocument(html="<html></html>"),
            ConversionOptions(),
            (False, "", ""),
            geom,
        )
        assert outcome.page_count == 1
        assert outcome.layout_issues == []
        assert [a.step for a in outcome.adjustments_applied] == ["spacing_compact_1"]


class TestRendererModule:
    """Tests for renderer.render_pdf orchestration, atomic writes, images, and previews."""

    def test_count_embedded_images_local_data_and_missing(self, tmp_path: Path) -> None:
        img_file = tmp_path / "pic with space.png"
        PIL.Image.new("RGB", (10, 10), (255, 0, 0)).save(img_file, format="PNG")
        missing_uri = (tmp_path / "missing.png").resolve().as_uri()
        data_uri = "data:image/png;base64," + base64.b64encode(b"png").decode("ascii")
        html_doc = (
            "<html><head></head><body>"
            f'<img src="{img_file.resolve().as_uri()}">'
            f'<img src="{missing_uri}">'
            f'<img src="{data_uri}">'
            '<img src="https://example.com/nope.png">'
            "</body></html>"
        )
        assert _count_embedded_images(html_doc) == 2

    def test_render_pdf_orchestrates_pipeline_atomic_write_and_previews(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        img_path = tmp_path / "logo.png"
        PIL.Image.new("RGB", (40, 40), (10, 80, 160)).save(img_path, format="PNG")
        out_pdf = tmp_path / "nested" / "out.pdf"
        preview_dir = tmp_path / "previews"
        sample_pdf = _make_pdf_bytes(
            [
                {"texts": [(50, 100, "Page 1")]},
                {"texts": [(50, 100, "Page 2")]},
            ]
        )

        monkeypatch.setattr(renderer_mod, "find_browser_executable", lambda: "/fake/chrome")

        captured: dict[str, Any] = {}

        class _FakeSession:
            def __init__(self, browser_path: str | Path | None = None, **kwargs: Any) -> None:
                captured["browser_path"] = str(browser_path)
                captured["kwargs"] = kwargs

            def __enter__(self) -> _FakeSession:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def emulate_print_viewport(self, geometry: PageGeometry) -> None:
                captured["geometry"] = geometry

            def navigate(self, url_or_path: str | Path, timeout: float | None = None) -> None:
                _ = timeout
                captured["html_exists_during_session"] = Path(url_or_path).is_file()
                captured["html_text"] = Path(url_or_path).read_text(encoding="utf-8")

        def _fake_autofit(
            *,
            session: Any,
            doc: BuiltDocument,
            options: ConversionOptions,
            header_footer: tuple[bool, str, str],
            geometry: PageGeometry,
        ) -> AutoFitOutcome:
            _ = (session, header_footer, geometry)
            captured["built_doc"] = doc
            captured["options"] = options
            return AutoFitOutcome(
                pdf_bytes=sample_pdf,
                page_count=2,
                last_page_fill_ratio=0.45,
            )

        monkeypatch.setattr(renderer_mod, "CdpSession", _FakeSession)
        monkeypatch.setattr(renderer_mod, "run_autofit_render", _fake_autofit)

        md_text = (
            "---\n"
            "theme: paper\n"
            "title: Test Paper\n"
            "unknown_key: ignored\n"
            "---\n"
            "# Test Paper\n\n"
            "![Logo](logo.png)\n"
        )
        opts = ConversionOptions(
            theme="paper",
            title="Test Paper",
            preview_dir=str(preview_dir),
            preview_dpi=144,
        )
        result = render_pdf(md_text, str(tmp_path), str(out_pdf), options=opts)

        assert captured["browser_path"] == "/fake/chrome"
        assert captured["kwargs"]["allow_any_local_file"] is False
        assert list(captured["kwargs"]["allowed_file_roots"])[1:] == [ASSETS_DIR, str(tmp_path)]
        assert captured["html_exists_during_session"] is True
        assert out_pdf.is_file()
        assert out_pdf.read_bytes() == sample_pdf
        assert result.theme == "paper"
        assert result.page_count == 2
        assert result.embedded_images == 1
        assert result.last_page_fill_ratio == 0.45
        assert len(result.preview_images) == 2
        assert (preview_dir / "page_001.png").is_file()
        assert (preview_dir / "page_002.png").is_file()
        # No stray temp HTML written in base_dir
        assert {p.name for p in tmp_path.iterdir()} == {"logo.png", "nested", "previews"}

    def test_render_pdf_propagates_no_browser_before_creating_output(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            renderer_mod,
            "find_browser_executable",
            lambda: (_ for _ in ()).throw(FileNotFoundError("Set MDTOPDF_BROWSER")),
        )
        out_pdf = tmp_path / "should_not_exist.pdf"
        with pytest.raises(FileNotFoundError, match="MDTOPDF_BROWSER"):
            render_pdf(
                "# Hello",
                str(tmp_path),
                str(out_pdf),
                frontmatter=FrontmatterData(),
                md_body="# Hello",
            )
        assert not out_pdf.exists()
