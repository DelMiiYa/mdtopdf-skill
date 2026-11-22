"""PDF page inspection, DOM table overflow remediation, and bounded orphan-page auto-fit."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pymupdf

from mdtopdf_skill.cdp_browser import CdpSession
from mdtopdf_skill.markdown_pipeline import BuiltDocument
from mdtopdf_skill.models import (
    SPACING_SCALE_MIN,
    AdjustmentRecord,
    AutoFitOutcome,
    ConversionOptions,
    LayoutIssue,
    MathError,
    MermaidError,
    PageGeometry,
    PdfInspection,
    compute_page_geometry,
)

__all__ = [
    "inspect_pdf_pages",
    "run_autofit_render",
]

_TABLE_MIN_COL_WIDTH_PX = 74
_TABLE_MIN_SCALE = 0.65
_TABLE_MIN_FONT_PT = 7.0
_BODY_MIN_FONT_PT = 7.5
_BODY_MIN_LINE_HEIGHT = 1.20

# Bounded §9.C trailing orphan-page compaction steps:
# (step_name, spacing_mult, margin_delta_mm, font_delta_pt, lh_delta, lh_max_drop)
_ORPHAN_COMPACT_STEPS: tuple[tuple[str, float, float, float, float, float], ...] = (
    ("spacing_compact_1", 0.90, 2.0, 0.0, 0.05, 0.15),
    ("font_spacing_compact_2", 0.85, 2.0, 0.5, 0.08, 0.18),
    ("font_spacing_compact_3", 0.85, 3.0, 1.0, 0.10, 0.20),
)


def _compact_below(base_val: float, delta: float, floor_val: float) -> float:
    """Reduce base_val by delta down to floor_val; never raise a value already <= floor_val."""
    if base_val <= floor_val:
        return base_val
    return max(floor_val, round(base_val - delta, 4))


def inspect_pdf_pages(
    pdf_bytes: bytes,
    margin_top_pt: float,
    margin_bottom_pt: float,
) -> PdfInspection:
    """Inspect rendered PDF bytes via PyMuPDF for trailing orphan/sparse final pages."""
    if not pdf_bytes:
        return PdfInspection(
            page_count=0,
            last_page_fill_ratio=0.0,
            body_line_count=0,
            body_images=0,
            body_vector_diagrams=0,
            is_trailing_orphan=False,
        )

    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        page_count = int(doc.page_count)
        if page_count <= 0:
            return PdfInspection(
                page_count=0,
                last_page_fill_ratio=0.0,
                body_line_count=0,
                body_images=0,
                body_vector_diagrams=0,
                is_trailing_orphan=False,
            )

        last_page = doc[-1]
        page_w = float(last_page.rect.width)
        page_h = float(last_page.rect.height)

        body_y0 = max(0.0, margin_top_pt + 2.0)
        body_y1 = min(page_h, page_h - margin_bottom_pt - 2.0)
        printable_body_height = max(1.0, page_h - margin_top_pt - margin_bottom_pt)
        effective_top_pt = margin_top_pt
        if body_y1 <= body_y0:
            body_y0 = 0.0
            body_y1 = page_h
            printable_body_height = max(1.0, page_h)
            effective_top_pt = 0.0

        def _intersects_body(y0: float, y1: float) -> bool:
            return y1 > body_y0 and y0 < body_y1

        body_line_count = 0
        body_images = 0
        body_vector_diagrams = 0
        max_y1 = effective_top_pt

        text_dict = last_page.get_text("dict")
        if isinstance(text_dict, dict):
            for block in text_dict.get("blocks", []):
                if not isinstance(block, dict) or block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    if not isinstance(line, dict):
                        continue
                    spans = line.get("spans", [])
                    line_text = "".join(
                        str(span.get("text", ""))
                        for span in spans
                        if isinstance(span, dict)
                    )
                    if not line_text.strip():
                        continue
                    bbox = line.get("bbox")
                    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                        continue
                    y0 = float(bbox[1])
                    y1 = float(bbox[3])
                    if _intersects_body(y0, y1):
                        body_line_count += 1
                        max_y1 = max(max_y1, min(y1, body_y1))

        for img_info in last_page.get_images(full=True):
            xref = int(img_info[0])
            for rect in last_page.get_image_rects(xref):
                if (
                    float(rect.width) > 1.0
                    and float(rect.height) > 1.0
                    and _intersects_body(float(rect.y0), float(rect.y1))
                ):
                    body_images += 1
                    max_y1 = max(max_y1, min(float(rect.y1), body_y1))

        for drawing in last_page.get_drawings():
            if not isinstance(drawing, dict):
                continue
            rect = drawing.get("rect")
            if rect is None:
                continue
            rect_w = float(rect.width)
            rect_h = float(rect.height)
            if rect_w >= page_w * 0.95 and rect_h >= page_h * 0.95:
                continue
            if rect_w <= 12.0 or rect_h <= 12.0:
                continue
            if _intersects_body(float(rect.y0), float(rect.y1)):
                body_vector_diagrams += 1
                max_y1 = max(max_y1, min(float(rect.y1), body_y1))

    if body_line_count == 0 and body_images == 0 and body_vector_diagrams == 0:
        fill_ratio = 0.0
    else:
        fill_ratio = min(1.0, max(0.0, (max_y1 - effective_top_pt) / printable_body_height))

    is_trailing_orphan = (
        page_count > 1
        and body_images == 0
        and body_vector_diagrams == 0
        and (body_line_count <= 6 or fill_ratio < 0.15)
    )

    return PdfInspection(
        page_count=page_count,
        last_page_fill_ratio=round(fill_ratio, 4),
        body_line_count=body_line_count,
        body_images=body_images,
        body_vector_diagrams=body_vector_diagrams,
        is_trailing_orphan=is_trailing_orphan,
    )


def _normalize_report_dict(raw: object) -> dict[str, Any]:
    """Coerce a CDP Runtime.evaluate report value into a plain dict."""
    return raw if isinstance(raw, dict) else {}


def _parse_mermaid_errors(raw_items: object) -> list[MermaidError]:
    if not isinstance(raw_items, list):
        return []
    errors: list[MermaidError] = []
    for seq_idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        errors.append(
            MermaidError(
                index=int(item.get("index", seq_idx)),
                message=str(item.get("message", "")),
                snippet=str(item.get("snippet", "")),
            )
        )
    return errors


def _parse_math_errors(raw_items: object) -> list[MathError]:
    if not isinstance(raw_items, list):
        return []
    errors: list[MathError] = []
    for seq_idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        errors.append(
            MathError(
                index=int(item.get("index", seq_idx)),
                display_mode=bool(item.get("display_mode", False)),
                message=str(item.get("message", "")),
                tex=str(item.get("tex", "")),
            )
        )
    return errors


def _extract_dict_list(raw_items: object) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _inspect_geometry(session: CdpSession, printable_width_px: float) -> dict[str, Any]:
    raw = session.evaluate(
        f"window.__mdtopdfInspectGeometry({printable_width_px!r})",
        await_promise=False,
        return_by_value=True,
    )
    return _normalize_report_dict(raw)


def _build_tier1_table_css(table_indices: Sequence[int], font_size_pt: float) -> str:
    tier1_font_pt = min(font_size_pt, max(_TABLE_MIN_FONT_PT, round(font_size_pt - 1.0, 2)))
    rules: list[str] = []
    for idx in table_indices:
        rules.append(
            f'table[data-mdtopdf-table-idx="{idx}"] {{\n'
            "  table-layout: auto !important;\n"
            "  width: 100% !important;\n"
            "  max-width: 100% !important;\n"
            f"  font-size: {tier1_font_pt:g}pt !important;\n"
            "  overflow-wrap: anywhere !important;\n"
            "  word-break: break-word !important;\n"
            "}\n"
            f'table[data-mdtopdf-table-idx="{idx}"] th,\n'
            f'table[data-mdtopdf-table-idx="{idx}"] td {{\n'
            "  padding: 3px 4px !important;\n"
            "  overflow-wrap: anywhere !important;\n"
            "  word-break: break-word !important;\n"
            f"  min-width: {_TABLE_MIN_COL_WIDTH_PX}px !important;\n"
            "  box-sizing: border-box !important;\n"
            "}"
        )
    return "\n".join(rules)


def _build_tier2_table_css(
    table_overflows: Sequence[dict[str, Any]],
    printable_width_px: float,
) -> tuple[str, list[float]]:
    rules: list[str] = []
    scales: list[float] = []
    for entry in table_overflows:
        idx = int(entry.get("table_index", 0))
        scroll_w = max(
            float(entry.get("scroll_width", 0.0)),
            float(entry.get("rect_width", 0.0)),
            1.0,
        )
        required_scale = max(0.01, (printable_width_px - 2.0) / scroll_w)
        applied_scale = max(_TABLE_MIN_SCALE, min(1.0, required_scale))
        scales.append(applied_scale)
        rules.append(
            f'table[data-mdtopdf-table-idx="{idx}"] {{\n'
            f"  width: {scroll_w:.2f}px !important;\n"
            "  max-width: none !important;\n"
            f"  transform: scale({applied_scale:.3f}) !important;\n"
            "  transform-origin: top left !important;\n"
            "}"
        )
    return "\n".join(rules), scales


def run_autofit_render(
    session: CdpSession,
    doc: BuiltDocument,
    options: ConversionOptions,
    header_footer: tuple[bool, str, str],
    geometry: PageGeometry,
) -> AutoFitOutcome:
    """Execute render_bridge.js, remediate table overflows/orphan pages, and inspect PDF."""
    _ = doc
    initial_report = _normalize_report_dict(
        session.evaluate(
            f"window.__mdtopdfRenderAndInspect({geometry.printable_width_px!r})",
            await_promise=True,
            return_by_value=True,
        )
    )
    mermaid_errors = _parse_mermaid_errors(initial_report.get("mermaid_errors"))
    math_errors = _parse_math_errors(initial_report.get("math_errors"))
    active_table_overflows = _extract_dict_list(initial_report.get("table_overflows"))
    wide_elements = _extract_dict_list(initial_report.get("wide_elements"))
    adjustments: list[AdjustmentRecord] = []

    tier1_css = ""
    if options.auto_fit and active_table_overflows:
        tier1_indices = [int(item.get("table_index", 0)) for item in active_table_overflows]
        tier1_css = _build_tier1_table_css(tier1_indices, options.font_size_pt)
        session.inject_style("mdtopdf-autofit-tables", tier1_css)
        adjustments.append(
            AdjustmentRecord(
                step="table_wrap_compact",
                detail=(
                    f"Applied overflow-wrap and compact padding to "
                    f"{len(tier1_indices)} overflowing table(s)"
                ),
            )
        )
        post_tier1 = _inspect_geometry(session, geometry.printable_width_px)
        active_table_overflows = _extract_dict_list(post_tier1.get("table_overflows"))
        wide_elements = _extract_dict_list(post_tier1.get("wide_elements"))

        if active_table_overflows:
            tier2_css, scales = _build_tier2_table_css(
                active_table_overflows, geometry.printable_width_px
            )
            combined_table_css = f"{tier1_css}\n{tier2_css}"
            session.inject_style("mdtopdf-autofit-tables", combined_table_css)
            min_scale = min(scales) if scales else _TABLE_MIN_SCALE
            adjustments.append(
                AdjustmentRecord(
                    step="table_proportional_scale",
                    detail=(
                        f"Applied proportional transform scale (min {min_scale:.2f}x) "
                        f"to {len(active_table_overflows)} table(s)"
                    ),
                )
            )
            post_tier2 = _inspect_geometry(session, geometry.printable_width_px)
            active_table_overflows = _extract_dict_list(post_tier2.get("table_overflows"))
            wide_elements = _extract_dict_list(post_tier2.get("wide_elements"))

    display_hf, header_tpl, footer_tpl = header_footer
    best_pdf_bytes = session.print_to_pdf(
        geometry=geometry,
        display_header_footer=display_hf,
        header_template=header_tpl,
        footer_template=footer_tpl,
    )
    best_inspection = inspect_pdf_pages(
        best_pdf_bytes,
        geometry.margin_top_pt,
        geometry.margin_bottom_pt,
    )
    unresolved_orphan = best_inspection.is_trailing_orphan

    if options.auto_fit and best_inspection.is_trailing_orphan and best_inspection.page_count > 1:
        margin_floor_mm = 10.0 if display_hf else 8.0
        initial_pages = best_inspection.page_count
        accepted_step = False

        for (
            step_name,
            spacing_mult,
            margin_delta_mm,
            font_delta_pt,
            lh_delta,
            lh_max_drop,
        ) in _ORPHAN_COMPACT_STEPS:
            new_spacing = min(
                options.spacing_scale,
                max(SPACING_SCALE_MIN, round(options.spacing_scale * spacing_mult, 4)),
            )
            new_font = _compact_below(
                options.font_size_pt,
                font_delta_pt,
                max(_BODY_MIN_FONT_PT, options.font_size_pt - 1.0),
            )
            new_lh = _compact_below(
                options.line_height,
                lh_delta,
                max(_BODY_MIN_LINE_HEIGHT, options.line_height - lh_max_drop),
            )
            new_top_mm = _compact_below(geometry.margin_top_mm, margin_delta_mm, margin_floor_mm)
            new_bottom_mm = _compact_below(
                geometry.margin_bottom_mm, margin_delta_mm, margin_floor_mm
            )

            step_margin_css = (
                f"{new_top_mm:g}mm {geometry.margin_right_mm:g}mm "
                f"{new_bottom_mm:g}mm {geometry.margin_left_mm:g}mm"
            )
            step_geometry = compute_page_geometry(options.page_size, step_margin_css)

            spacing_css = (
                f"@page {{ margin: {step_margin_css} !important; }}\n"
                f":root {{ --md-spacing-scale: {new_spacing:g} !important; }}\n"
                f"body {{ font-size: {new_font:g}pt !important; "
                f"line-height: {new_lh:g} !important; }}"
            )
            session.inject_style("mdtopdf-autofit-spacing", spacing_css)
            cand_geom = _inspect_geometry(session, step_geometry.printable_width_px)
            cand_table_overflows = _extract_dict_list(cand_geom.get("table_overflows"))
            cand_wide_elements = _extract_dict_list(cand_geom.get("wide_elements"))

            cand_pdf = session.print_to_pdf(
                geometry=step_geometry,
                display_header_footer=display_hf,
                header_template=header_tpl,
                footer_template=footer_tpl,
            )
            cand_insp = inspect_pdf_pages(
                cand_pdf,
                step_geometry.margin_top_pt,
                step_geometry.margin_bottom_pt,
            )

            if 1 <= cand_insp.page_count < initial_pages and len(cand_table_overflows) <= len(
                active_table_overflows
            ):
                adjustments.append(
                    AdjustmentRecord(
                        step=step_name,
                        detail=(
                            f"Reduced page count from {initial_pages} to {cand_insp.page_count} "
                            f"(spacing_scale={new_spacing:.2f}, font={new_font:g}pt, "
                            f"line_height={new_lh:g}, "
                            f"margin_v={new_top_mm:g}mm/{new_bottom_mm:g}mm)"
                        ),
                    )
                )
                best_pdf_bytes = cand_pdf
                best_inspection = cand_insp
                active_table_overflows = cand_table_overflows
                wide_elements = cand_wide_elements
                unresolved_orphan = cand_insp.is_trailing_orphan
                accepted_step = True
                break

        if not accepted_step:
            session.inject_style("mdtopdf-autofit-spacing", "")
            unresolved_orphan = True

    layout_issues: list[LayoutIssue] = []
    for item in active_table_overflows:
        table_idx = int(item.get("table_index", 0))
        overflow_px = round(float(item.get("overflow_px", 0.0)), 1)
        cols = int(item.get("column_count", 0))
        layout_issues.append(
            LayoutIssue(
                kind="table_overflow",
                detail=(
                    f"Table #{table_idx} overflows printable width by {overflow_px:g}px "
                    f"({cols} columns)"
                ),
                suggestion='Re-run with --page-size "A4 landscape" or reduce column count',
                table_index=table_idx,
                overflow_px=overflow_px,
            )
        )

    for item in wide_elements:
        if item.get("in_table", False):
            continue
        tag = str(item.get("tag", "element"))
        overflow_px = round(float(item.get("overflow_px", 0.0)), 1)
        layout_issues.append(
            LayoutIssue(
                kind="wide_element",
                detail=f"Element <{tag}> overflows printable width by {overflow_px:g}px",
                suggestion='Reduce image/element size or re-run with --page-size "A4 landscape"',
                overflow_px=overflow_px,
            )
        )

    if unresolved_orphan:
        layout_issues.append(
            LayoutIssue(
                kind="trailing_orphan_page",
                detail=(
                    f"Final page {best_inspection.page_count} has sparse content "
                    f"({best_inspection.body_line_count} line(s), "
                    f"fill={best_inspection.last_page_fill_ratio:.2f})"
                ),
                suggestion="Re-run with --spacing-scale 0.85 or --margin compact",
            )
        )

    return AutoFitOutcome(
        pdf_bytes=best_pdf_bytes,
        page_count=best_inspection.page_count,
        last_page_fill_ratio=best_inspection.last_page_fill_ratio,
        layout_issues=layout_issues,
        mermaid_errors=mermaid_errors,
        math_errors=math_errors,
        adjustments_applied=adjustments,
    )
