"""Opinionated print themes, CSS generation, metadata headers, and CDP chrome templates."""

from __future__ import annotations

import html
import math
import re

from pygments.formatters import HtmlFormatter

from mdtopdf_skill.models import (
    DEFAULT_TITLE,
    ConversionOptions,
    FrontmatterData,
    ThemeSpec,
    parse_margin_mm,
    resolve_margin_css,
    resolve_page_size,
    resolve_theme_name,
    validate_accent_color,
    validate_font_size,
    validate_line_height,
    validate_positive_int,
    validate_spacing_scale,
)

__all__ = [
    "THEMES",
    "build_header_footer_templates",
    "build_metadata_header_html",
    "build_theme_css",
    "get_theme",
]

_SANS_FONT_STACK = (
    "'Leelawadee UI', 'Tahoma', 'Noto Sans Thai', 'Sarabun', "
    "'Noto Sans', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif"
)
_SERIF_FONT_STACK = (
    "'Georgia', 'Times New Roman', 'Noto Serif Thai', 'Sarabun', "
    "'Liberation Serif', 'DejaVu Serif', 'Segoe UI Emoji', 'Noto Color Emoji', serif"
)
_MONO_FONT_STACK = (
    "'Consolas', 'JetBrains Mono', 'Noto Sans Mono', 'DejaVu Sans Mono', monospace"
)
_CLASSIC_FONT_STACK = (
    "'Leelawadee UI', 'Tahoma', 'Noto Sans Thai', 'Noto Sans', "
    "'Segoe UI Symbol', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif"
)
_CLASSIC_MONO_STACK = (
    "'Consolas', 'Noto Sans Mono', 'DejaVu Sans Mono', 'Courier New', monospace"
)

THEMES: dict[str, ThemeSpec] = {
    "office": ThemeSpec(
        name="office",
        description="Corporate memos, business reports, and SOPs",
        font_size_pt=9.5,
        line_height=1.45,
        margin_css="15mm 16mm",
        accent_color="#0f3d7a",
        page_numbers=True,
        font_family=_SANS_FONT_STACK,
        mono_family=_MONO_FONT_STACK,
        is_default=True,
    ),
    "student": ThemeSpec(
        name="student",
        description="Study notes, lab reports, homework, and exam guides",
        font_size_pt=10.0,
        line_height=1.52,
        margin_css="14mm 15mm",
        accent_color="#2b4c7e",
        page_numbers=True,
        font_family=_SANS_FONT_STACK,
        mono_family=_MONO_FONT_STACK,
        is_default=False,
    ),
    "paper": ThemeSpec(
        name="paper",
        description="Academic and research papers",
        font_size_pt=10.0,
        line_height=1.48,
        margin_css="18mm 20mm",
        accent_color="#1e293b",
        page_numbers=True,
        font_family=_SERIF_FONT_STACK,
        mono_family=_MONO_FONT_STACK,
        is_default=False,
    ),
    "tech-spec": ThemeSpec(
        name="tech-spec",
        description="Engineering RFCs, architecture specs, and API documentation",
        font_size_pt=9.2,
        line_height=1.42,
        margin_css="13mm 14mm",
        accent_color="#0369a1",
        page_numbers=True,
        font_family=_SANS_FONT_STACK,
        mono_family=_MONO_FONT_STACK,
        is_default=False,
    ),
    "executive": ThemeSpec(
        name="executive",
        description="Executive briefs, proposals, and summaries",
        font_size_pt=10.2,
        line_height=1.50,
        margin_css="16mm 18mm",
        accent_color="#1d4ed8",
        page_numbers=True,
        font_family=_SANS_FONT_STACK,
        mono_family=_MONO_FONT_STACK,
        is_default=False,
    ),
    "minimal": ThemeSpec(
        name="minimal",
        description="Swiss minimalist documents, CVs, and clean notes",
        font_size_pt=9.5,
        line_height=1.48,
        margin_css="16mm 18mm",
        accent_color="#111827",
        page_numbers=True,
        font_family=_SANS_FONT_STACK,
        mono_family=_MONO_FONT_STACK,
        is_default=False,
    ),
    "classic": ThemeSpec(
        name="classic",
        description="Backward-compatible v0.2.0 classic layout",
        font_size_pt=9.5,
        line_height=1.45,
        margin_css="12mm 14mm",
        accent_color="#0d47a1",
        page_numbers=False,
        font_family=_CLASSIC_FONT_STACK,
        mono_family=_CLASSIC_MONO_STACK,
        is_default=False,
    ),
}

_PYGMENTS_STYLES: dict[str, str] = {
    "office": "default",
    "student": "friendly",
    "paper": "default",
    "tech-spec": "tango",
    "executive": "default",
    "minimal": "default",
    "classic": "default",
}

_STRUCTURED_META_FIELDS: tuple[str, ...] = (
    "subtitle",
    "author",
    "date",
    "course",
    "instructor",
    "student",
    "affiliation",
    "abstract",
    "status",
    "version",
    "owner",
)

_META_LABELS: dict[str, str] = {
    "author": "Author",
    "date": "Date",
    "course": "Course",
    "instructor": "Instructor",
    "student": "Student",
    "affiliation": "Affiliation",
    "status": "Status",
    "version": "Version",
    "owner": "Owner",
}

_RUNNING_HEADER_DEFAULT_THEMES: frozenset[str] = frozenset({"office", "paper", "tech-spec"})


def _clean_opt_str(value: str | None) -> str | None:
    """Return stripped string or None if empty/whitespace-only."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _esc(value: str) -> str:
    """Escape text safely for HTML text nodes and double-quoted attributes."""
    return html.escape(value, quote=True)


def _sanitize_status_slug(status: str) -> str:
    """Sanitize a status string into a safe lowercase CSS class suffix."""
    slug = re.sub(r"[^a-z0-9-]+", "-", status.lower()).strip("-")
    return slug or "default"


def _sc(px: float) -> str:
    """Emit a vertical spacing expression scaled by --md-spacing-scale."""
    formatted = f"{px:g}"
    return f"calc({formatted}px * var(--md-spacing-scale, 1))"


def _validate_printable_height_mm(value: float) -> float:
    """Validate finite positive printable_height_mm."""
    if isinstance(value, bool):
        raise ValueError("printable_height_mm must be a finite positive number, got bool")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"printable_height_mm must be a valid number, got {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(
            f"printable_height_mm must be finite and > 0, got {parsed!r}"
        )
    return parsed


def _get_pygments_css(style_name: str) -> str:
    """Generate Pygments CSS for .highlight and .codehilite blocks."""
    formatter = HtmlFormatter(style=style_name)
    return str(formatter.get_style_defs([".highlight", ".codehilite"]))


def get_theme(name: str) -> ThemeSpec:
    """Return the normalized ThemeSpec for name, raising ValueError if unknown."""
    resolved_name = resolve_theme_name(name, THEMES)
    return THEMES[resolved_name]


def _build_theme_variant_css(
    theme_name: str,
    theme_spec: ThemeSpec,
    accent: str,
    font_pt: float,
    line_h: float,
    keep_together: bool,
) -> str:
    """Generate theme-specific typography, heading, table, code, and blockquote rules."""
    break_rule = "avoid" if keep_together else "auto"
    table_font_pt = round(max(7.5, font_pt - 1.0), 1)
    h1_pt = round(font_pt * 1.63, 1)
    h2_pt = round(font_pt * 1.21, 1)
    h3_pt = round(font_pt * 1.03, 1)

    if theme_name == "classic":
        return f"""
body {{
    font-family: {theme_spec.font_family};
    font-size: {font_pt:g}pt;
    line-height: {line_h:g};
    color: #1a1a1a;
    margin: 0;
}}
h1 {{
    font-size: {h1_pt:g}pt;
    color: {accent};
    text-align: center;
    margin-top: 0;
    margin-bottom: {_sc(6)};
    line-height: 1.25;
}}
h2 {{
    font-size: {h2_pt:g}pt;
    color: {accent};
    border-bottom: 1.5px solid #1976d2;
    padding-bottom: {_sc(3)};
    margin-top: {_sc(12)};
    margin-bottom: {_sc(6)};
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {h3_pt:g}pt;
    color: #1565c0;
    margin-top: {_sc(8)};
    margin-bottom: {_sc(4)};
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #1e3a8a;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    break-after: avoid;
    page-break-after: avoid;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {table_font_pt:g}pt;
    margin: {_sc(6)} 0 {_sc(10)} 0;
}}
th, td {{
    border: 1px solid #a0aab2;
    padding: {_sc(4)} 6px;
    vertical-align: middle;
}}
th {{
    background-color: #f0f4f9;
    color: {accent};
    font-weight: 600;
    text-align: center;
}}
tr {{
    break-inside: {break_rule};
    page-break-inside: {break_rule};
}}
tbody tr:nth-child(even) {{
    background-color: #fbfcfd;
}}
code {{
    font-family: {theme_spec.mono_family};
    font-size: 0.88em;
    background-color: #f3f4f6;
    padding: 1px 4px;
    border-radius: 3px;
    border: 1px solid #e0e0e0;
}}
pre {{
    background-color: #f6f8fa;
    border: 1px solid #d0d7de;
    border-radius: 4px;
    padding: {_sc(8)} 10px;
    font-family: {theme_spec.mono_family};
    font-size: 0.85em;
    line-height: {line_h:g};
    margin: {_sc(6)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
blockquote {{
    margin: {_sc(6)} 0;
    padding: {_sc(4)} 12px;
    border-left: 3.5px solid #1976d2;
    background-color: #f8fafc;
    color: #334155;
}}
hr {{
    border: none;
    border-top: 1px solid #cfd8dc;
    margin: {_sc(10)} 0;
}}
"""

    if theme_name == "paper":
        return f"""
body {{
    font-family: {theme_spec.font_family};
    font-size: {font_pt:g}pt;
    line-height: {line_h:g};
    color: #111827;
    margin: 0;
}}
p {{
    text-align: justify;
}}
h1 {{
    font-size: {round(font_pt * 1.65, 1):g}pt;
    color: {accent};
    text-align: center;
    margin-top: 0;
    margin-bottom: {_sc(8)};
    line-height: 1.25;
    font-weight: 700;
}}
h2 {{
    font-size: {round(font_pt * 1.22, 1):g}pt;
    color: {accent};
    border-bottom: 1px solid #cbd5e1;
    padding-bottom: {_sc(3)};
    margin-top: {_sc(14)};
    margin-bottom: {_sc(6)};
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {round(font_pt * 1.05, 1):g}pt;
    color: #334155;
    font-style: italic;
    margin-top: {_sc(10)};
    margin-bottom: {_sc(4)};
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #475569;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    break-after: avoid;
    page-break-after: avoid;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {table_font_pt:g}pt;
    margin: {_sc(8)} 0 {_sc(12)} 0;
    border-top: 1.5px solid {accent};
    border-bottom: 1.5px solid {accent};
    border-left: none;
    border-right: none;
}}
th, td {{
    border-left: none;
    border-right: none;
    border-top: none;
    border-bottom: 0.5px solid #e2e8f0;
    padding: {_sc(5)} 8px;
    vertical-align: middle;
}}
thead th {{
    background-color: transparent;
    color: {accent};
    font-weight: 700;
    text-align: left;
    border-bottom: 1px solid {accent};
}}
tbody tr:last-child td {{
    border-bottom: none;
}}
tr {{
    break-inside: {break_rule};
    page-break-inside: {break_rule};
}}
code {{
    font-family: {theme_spec.mono_family};
    font-size: 0.87em;
    background-color: #f8fafc;
    padding: 1px 4px;
    border-radius: 2px;
    border: 1px solid #e2e8f0;
}}
pre {{
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 3px;
    padding: {_sc(8)} 10px;
    font-family: {theme_spec.mono_family};
    font-size: 0.84em;
    line-height: {line_h:g};
    margin: {_sc(8)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
blockquote {{
    margin: {_sc(8)} 0;
    padding: {_sc(4)} 14px;
    border-left: 3px solid {accent};
    background-color: #f8fafc;
    color: #334155;
    font-style: italic;
}}
hr {{
    border: none;
    border-top: 0.75px solid #cbd5e1;
    margin: {_sc(12)} 0;
}}
"""

    if theme_name == "tech-spec":
        return f"""
body {{
    font-family: {theme_spec.font_family};
    font-size: {font_pt:g}pt;
    line-height: {line_h:g};
    color: #0f172a;
    margin: 0;
}}
h1 {{
    font-size: {round(font_pt * 1.65, 1):g}pt;
    color: {accent};
    text-align: left;
    margin-top: 0;
    margin-bottom: {_sc(6)};
    line-height: 1.2;
    font-weight: 700;
}}
h2 {{
    font-size: {round(font_pt * 1.24, 1):g}pt;
    color: {accent};
    border-bottom: 1.5px solid {accent};
    padding-bottom: {_sc(3)};
    margin-top: {_sc(13)};
    margin-bottom: {_sc(6)};
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {round(font_pt * 1.06, 1):g}pt;
    color: #0f172a;
    border-left: 3px solid {accent};
    padding-left: 6px;
    margin-top: {_sc(9)};
    margin-bottom: {_sc(4)};
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #334155;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    break-after: avoid;
    page-break-after: avoid;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {table_font_pt:g}pt;
    margin: {_sc(6)} 0 {_sc(10)} 0;
}}
th, td {{
    border: 1px solid #cbd5e1;
    padding: {_sc(4)} 6px;
    vertical-align: top;
}}
th {{
    background-color: #e0f2fe;
    color: #0c4a6e;
    font-weight: 700;
    text-align: left;
}}
tr {{
    break-inside: {break_rule};
    page-break-inside: {break_rule};
}}
tbody tr:nth-child(even) {{
    background-color: #f8fafc;
}}
td code, th code {{
    font-size: 0.9em;
}}
code {{
    font-family: {theme_spec.mono_family};
    font-size: 0.88em;
    background-color: #f1f5f9;
    color: #0f172a;
    padding: 1px 4px;
    border-radius: 3px;
    border: 1px solid #cbd5e1;
}}
pre {{
    background-color: #f1f5f9;
    border: 1px solid #94a3b8;
    border-left: 3px solid {accent};
    border-radius: 4px;
    padding: {_sc(8)} 10px;
    font-family: {theme_spec.mono_family};
    font-size: 0.85em;
    line-height: {line_h:g};
    margin: {_sc(6)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.mermaid-block, .mermaid-container {{
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: {_sc(8)} 10px;
    background-color: #ffffff;
}}
blockquote {{
    margin: {_sc(6)} 0;
    padding: {_sc(5)} 12px;
    border-left: 3.5px solid {accent};
    background-color: #f0f9ff;
    color: #0f172a;
}}
hr {{
    border: none;
    border-top: 1px solid #cbd5e1;
    margin: {_sc(10)} 0;
}}
"""

    if theme_name == "student":
        return f"""
body {{
    font-family: {theme_spec.font_family};
    font-size: {font_pt:g}pt;
    line-height: {line_h:g};
    color: #1f2937;
    margin: 0;
}}
h1 {{
    font-size: {round(font_pt * 1.6, 1):g}pt;
    color: {accent};
    text-align: left;
    margin-top: 0;
    margin-bottom: {_sc(7)};
    line-height: 1.25;
}}
h2 {{
    font-size: {round(font_pt * 1.22, 1):g}pt;
    color: {accent};
    border-bottom: 1.5px solid #93c5fd;
    padding-bottom: {_sc(3)};
    margin-top: {_sc(12)};
    margin-bottom: {_sc(6)};
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {round(font_pt * 1.05, 1):g}pt;
    color: #1e40af;
    margin-top: {_sc(9)};
    margin-bottom: {_sc(4)};
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #334155;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    break-after: avoid;
    page-break-after: avoid;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {table_font_pt:g}pt;
    margin: {_sc(7)} 0 {_sc(10)} 0;
}}
th, td {{
    border: 1px solid #cbd5e1;
    padding: {_sc(5)} 7px;
    vertical-align: middle;
}}
th {{
    background-color: #eff6ff;
    color: {accent};
    font-weight: 600;
    text-align: left;
}}
tr {{
    break-inside: {break_rule};
    page-break-inside: {break_rule};
}}
tbody tr:nth-child(even) {{
    background-color: #f8fafc;
}}
code {{
    font-family: {theme_spec.mono_family};
    font-size: 0.88em;
    background-color: #f1f5f9;
    padding: 1px 4px;
    border-radius: 3px;
    border: 1px solid #e2e8f0;
}}
pre {{
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 5px;
    padding: {_sc(8)} 10px;
    font-family: {theme_spec.mono_family};
    font-size: 0.85em;
    line-height: {line_h:g};
    margin: {_sc(6)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
blockquote {{
    margin: {_sc(6)} 0;
    padding: {_sc(5)} 12px;
    border-left: 3.5px solid {accent};
    background-color: #f8fafc;
    color: #334155;
    border-radius: 0 4px 4px 0;
}}
hr {{
    border: none;
    border-top: 1px solid #cbd5e1;
    margin: {_sc(10)} 0;
}}
"""

    if theme_name == "executive":
        return f"""
body {{
    font-family: {theme_spec.font_family};
    font-size: {font_pt:g}pt;
    line-height: {line_h:g};
    color: #0f172a;
    margin: 0;
}}
h1 {{
    font-size: {round(font_pt * 1.72, 1):g}pt;
    color: #0f172a;
    border-left: 4px solid {accent};
    padding-left: 10px;
    text-align: left;
    margin-top: 0;
    margin-bottom: {_sc(8)};
    line-height: 1.22;
    font-weight: 700;
}}
h2 {{
    font-size: {round(font_pt * 1.25, 1):g}pt;
    color: {accent};
    border-bottom: 1.5px solid #dbeafe;
    padding-bottom: {_sc(4)};
    margin-top: {_sc(14)};
    margin-bottom: {_sc(7)};
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {round(font_pt * 1.06, 1):g}pt;
    color: #0f172a;
    margin-top: {_sc(10)};
    margin-bottom: {_sc(4)};
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #334155;
    margin-top: {_sc(7)};
    margin-bottom: {_sc(3)};
    break-after: avoid;
    page-break-after: avoid;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {table_font_pt:g}pt;
    margin: {_sc(8)} 0 {_sc(12)} 0;
}}
th, td {{
    border-left: none;
    border-right: none;
    border-top: none;
    border-bottom: 1px solid #e2e8f0;
    padding: {_sc(6)} 8px;
    vertical-align: middle;
}}
th {{
    background-color: #f8fafc;
    color: #0f172a;
    font-weight: 700;
    text-align: left;
    border-bottom: 2px solid {accent};
}}
tr {{
    break-inside: {break_rule};
    page-break-inside: {break_rule};
}}
code {{
    font-family: {theme_spec.mono_family};
    font-size: 0.87em;
    background-color: #f1f5f9;
    padding: 1px 4px;
    border-radius: 3px;
}}
pre {{
    background-color: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 4px;
    padding: {_sc(8)} 12px;
    font-family: {theme_spec.mono_family};
    font-size: 0.85em;
    line-height: {line_h:g};
    margin: {_sc(7)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
blockquote {{
    margin: {_sc(8)} 0;
    padding: {_sc(6)} 14px;
    border-left: 4px solid {accent};
    background-color: #f8fafc;
    color: #1e293b;
    font-style: italic;
}}
hr {{
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: {_sc(12)} 0;
}}
"""

    if theme_name == "minimal":
        return f"""
body {{
    font-family: {theme_spec.font_family};
    font-size: {font_pt:g}pt;
    line-height: {line_h:g};
    color: #111827;
    margin: 0;
}}
h1 {{
    font-size: {round(font_pt * 1.68, 1):g}pt;
    color: {accent};
    text-align: left;
    margin-top: 0;
    margin-bottom: {_sc(8)};
    line-height: 1.25;
    font-weight: 600;
    letter-spacing: -0.01em;
}}
h2 {{
    font-size: {round(font_pt * 1.2, 1):g}pt;
    color: {accent};
    border-bottom: 0.5pt solid #d1d5db;
    padding-bottom: {_sc(3)};
    margin-top: {_sc(14)};
    margin-bottom: {_sc(6)};
    font-weight: 600;
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {round(font_pt * 1.04, 1):g}pt;
    color: #374151;
    margin-top: {_sc(9)};
    margin-bottom: {_sc(4)};
    font-weight: 600;
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #4b5563;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    font-weight: 600;
    break-after: avoid;
    page-break-after: avoid;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {table_font_pt:g}pt;
    margin: {_sc(8)} 0 {_sc(11)} 0;
}}
th, td {{
    border-left: none;
    border-right: none;
    border-top: none;
    border-bottom: 0.5pt solid #e5e7eb;
    padding: {_sc(5)} 6px;
    vertical-align: middle;
}}
th {{
    background-color: transparent;
    color: {accent};
    font-weight: 600;
    text-align: left;
    border-bottom: 1pt solid #9ca3af;
}}
tr {{
    break-inside: {break_rule};
    page-break-inside: {break_rule};
}}
code {{
    font-family: {theme_spec.mono_family};
    font-size: 0.88em;
    background-color: #f3f4f6;
    padding: 1px 4px;
    border-radius: 2px;
}}
pre {{
    background-color: #f9fafb;
    border: 0.5pt solid #d1d5db;
    border-radius: 2px;
    padding: {_sc(8)} 10px;
    font-family: {theme_spec.mono_family};
    font-size: 0.85em;
    line-height: {line_h:g};
    margin: {_sc(7)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
blockquote {{
    margin: {_sc(7)} 0;
    padding: {_sc(3)} 12px;
    border-left: 2px solid #9ca3af;
    background-color: transparent;
    color: #4b5563;
}}
hr {{
    border: none;
    border-top: 0.5pt solid #d1d5db;
    margin: {_sc(12)} 0;
}}
"""

    # Default: "office"
    return f"""
body {{
    font-family: {theme_spec.font_family};
    font-size: {font_pt:g}pt;
    line-height: {line_h:g};
    color: #1e293b;
    margin: 0;
}}
h1 {{
    font-size: {round(font_pt * 1.65, 1):g}pt;
    color: {accent};
    text-align: left;
    border-bottom: 2px solid {accent};
    padding-bottom: {_sc(4)};
    margin-top: 0;
    margin-bottom: {_sc(8)};
    line-height: 1.25;
}}
h2 {{
    font-size: {h2_pt:g}pt;
    color: {accent};
    border-bottom: 1.5px solid {accent};
    padding-bottom: {_sc(3)};
    margin-top: {_sc(12)};
    margin-bottom: {_sc(6)};
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {h3_pt:g}pt;
    color: #1e3a8a;
    margin-top: {_sc(8)};
    margin-bottom: {_sc(4)};
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #334155;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    break-after: avoid;
    page-break-after: avoid;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: {table_font_pt:g}pt;
    margin: {_sc(6)} 0 {_sc(10)} 0;
}}
th, td {{
    border: 1px solid #cbd5e1;
    padding: {_sc(4)} 6px;
    vertical-align: middle;
}}
th {{
    background-color: #eef2f7;
    color: {accent};
    font-weight: 600;
    text-align: left;
}}
tr {{
    break-inside: {break_rule};
    page-break-inside: {break_rule};
}}
tbody tr:nth-child(even) {{
    background-color: #f8fafc;
}}
code {{
    font-family: {theme_spec.mono_family};
    font-size: 0.88em;
    background-color: #f1f5f9;
    padding: 1px 4px;
    border-radius: 3px;
    border: 1px solid #e2e8f0;
}}
pre {{
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    padding: {_sc(8)} 10px;
    font-family: {theme_spec.mono_family};
    font-size: 0.85em;
    line-height: {line_h:g};
    margin: {_sc(6)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
blockquote {{
    margin: {_sc(6)} 0;
    padding: {_sc(4)} 12px;
    border-left: 3.5px solid {accent};
    background-color: #f8fafc;
    color: #334155;
}}
hr {{
    border: none;
    border-top: 1px solid #cbd5e1;
    margin: {_sc(10)} 0;
}}
"""


def _build_callout_css(theme_name: str) -> str:
    """Build GitHub-style admonition/callout CSS tailored to the theme."""
    if theme_name == "minimal":
        return f"""
.callout {{
    margin: {_sc(7)} 0;
    padding: {_sc(5)} 12px;
    border-left: 2px solid #4b5563;
    background-color: transparent;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.callout-title {{
    font-weight: 600;
    margin-bottom: {_sc(3)};
    font-size: 0.92em;
    color: #111827;
}}
.callout-note {{ border-left-color: #2563eb; }}
.callout-note .callout-title {{ color: #1d4ed8; }}
.callout-tip {{ border-left-color: #16a34a; }}
.callout-tip .callout-title {{ color: #15803d; }}
.callout-important {{ border-left-color: #7c3aed; }}
.callout-important .callout-title {{ color: #6d28d9; }}
.callout-warning {{ border-left-color: #d97706; }}
.callout-warning .callout-title {{ color: #b45309; }}
.callout-caution {{ border-left-color: #dc2626; }}
.callout-caution .callout-title {{ color: #b91c1c; }}
"""
    if theme_name == "student":
        return f"""
.callout {{
    margin: {_sc(7)} 0;
    padding: {_sc(7)} 12px;
    border-left: 4px solid #2b4c7e;
    border-radius: 0 5px 5px 0;
    background-color: #f8fafc;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.callout-title {{
    font-weight: 700;
    margin-bottom: {_sc(3)};
    font-size: 0.92em;
}}
.callout-note {{ border-left-color: #2563eb; background-color: #eff6ff; }}
.callout-note .callout-title {{ color: #1d4ed8; }}
.callout-tip {{ border-left-color: #16a34a; background-color: #f0fdf4; }}
.callout-tip .callout-title {{ color: #15803d; }}
.callout-important {{ border-left-color: #7c3aed; background-color: #f5f3ff; }}
.callout-important .callout-title {{ color: #6d28d9; }}
.callout-warning {{ border-left-color: #d97706; background-color: #fffbeb; }}
.callout-warning .callout-title {{ color: #b45309; }}
.callout-caution {{ border-left-color: #dc2626; background-color: #fef2f2; }}
.callout-caution .callout-title {{ color: #b91c1c; }}
"""
    return f"""
.callout {{
    margin: {_sc(7)} 0;
    padding: {_sc(6)} 12px;
    border-left: 3.5px solid #2563eb;
    border-radius: 0 4px 4px 0;
    background-color: #f8fafc;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.callout-title {{
    font-weight: 700;
    margin-bottom: {_sc(3)};
    font-size: 0.92em;
}}
.callout-note {{ border-left-color: #2563eb; background-color: #f0f7ff; }}
.callout-note .callout-title {{ color: #1e40af; }}
.callout-tip {{ border-left-color: #16a34a; background-color: #f2fbf5; }}
.callout-tip .callout-title {{ color: #166534; }}
.callout-important {{ border-left-color: #7c3aed; background-color: #f6f3ff; }}
.callout-important .callout-title {{ color: #5b21b6; }}
.callout-warning {{ border-left-color: #d97706; background-color: #fffaf0; }}
.callout-warning .callout-title {{ color: #92400e; }}
.callout-caution {{ border-left-color: #dc2626; background-color: #fef2f2; }}
.callout-caution .callout-title {{ color: #991b1b; }}
"""


def build_theme_css(options: ConversionOptions, printable_height_mm: float) -> str:
    """Build print-ready CSS for the resolved ConversionOptions and printable page height."""
    theme_spec = get_theme(options.theme)
    theme_name = theme_spec.name
    page_size = resolve_page_size(options.page_size)
    margin_css = resolve_margin_css(options.margin_css)
    accent = validate_accent_color(options.accent_color)
    font_pt = validate_font_size(options.font_size_pt)
    line_h = validate_line_height(options.line_height)
    spacing_scale = validate_spacing_scale(options.spacing_scale)
    img_w = validate_positive_int(options.img_max_width, "img_max_width")
    img_h = validate_positive_int(options.img_max_height, "img_max_height")
    printable_h = _validate_printable_height_mm(printable_height_mm)

    mermaid_max_height_mm = min(printable_h, max(1.0, printable_h - 8.0))
    pygments_style = _PYGMENTS_STYLES.get(theme_name, "default")
    pygments_css = _get_pygments_css(pygments_style)
    theme_variant_css = _build_theme_variant_css(
        theme_name=theme_name,
        theme_spec=theme_spec,
        accent=accent,
        font_pt=font_pt,
        line_h=line_h,
        keep_together=bool(options.keep_blocks_together),
    )
    callout_css = _build_callout_css(theme_name)

    return f"""@page {{
    size: {page_size};
    margin: {margin_css};
}}
:root {{
    --md-spacing-scale: {spacing_scale:g};
}}
/* Pygments syntax highlighting (placed before theme pre/.highlight overrides) */
{pygments_css}
{theme_variant_css}
/* Shared print layout, spacing, and Thai/Unicode wrapping rules */
body, p, li, h1, h2, h3, h4, h5, h6, th, td, blockquote, .callout, .doc-header, .toc, .footnote {{
    overflow-wrap: break-word;
}}
:lang(th) {{
    word-break: normal;
    overflow-wrap: break-word;
}}
h2 + p, h2 + ul, h2 + ol, h2 + table,
h3 + p, h3 + ul, h3 + ol, h3 + table {{
    break-before: avoid;
    page-break-before: avoid;
}}
.station-block {{
    break-inside: avoid;
    page-break-inside: avoid;
    margin-bottom: {_sc(6)};
}}
.station-block h3 {{
    margin-top: {_sc(5)};
    margin-bottom: {_sc(2)};
}}
.station-block ul, .station-block ol {{
    margin-top: {_sc(2)};
    margin-bottom: {_sc(5)};
}}
p {{
    margin: {_sc(3)} 0 {_sc(6)} 0;
}}
ul, ol {{
    margin: {_sc(3)} 0 {_sc(6)} 0;
    padding-left: 20px;
}}
li {{
    margin-bottom: {_sc(2)};
}}
.highlight, .codehilite {{
    background: transparent;
    margin: {_sc(6)} 0;
    padding: 0;
}}
.highlight pre, .codehilite pre {{
    margin: 0;
}}
pre, .highlight pre, .codehilite pre, pre code, .highlight code, .codehilite code {{
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    word-break: break-word;
}}
pre code, .highlight pre code, .codehilite pre code {{
    background: none;
    border: none;
    padding: 0;
    font-size: inherit;
}}
td img, th img {{
    max-width: {img_w}px;
    max-height: {img_h}px;
    width: auto;
    height: auto;
    display: block;
    margin: 0 auto;
    object-fit: contain;
}}
p > img {{
    max-width: 100%;
    max-height: {mermaid_max_height_mm:.2f}mm;
    width: auto;
    height: auto;
    display: block;
    margin: {_sc(6)} auto;
}}
.mermaid-block, .mermaid-container {{
    break-inside: avoid;
    page-break-inside: avoid;
    margin: {_sc(8)} 0;
    text-align: center;
}}
.mermaid-block svg, .mermaid-container svg {{
    max-width: 100%;
    max-height: {mermaid_max_height_mm:.2f}mm;
    width: auto;
    height: auto;
    display: block;
    margin: 0 auto;
}}
.mermaid-error-box {{
    border: 1px solid #dc2626;
    background-color: #fef2f2;
    color: #991b1b;
    border-radius: 4px;
    padding: {_sc(8)} 10px;
    margin: {_sc(8)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.mermaid-error-badge {{
    display: inline-block;
    font-size: 0.78em;
    font-weight: 700;
    letter-spacing: 0.04em;
    background-color: #dc2626;
    color: #ffffff;
    padding: 1px 6px;
    border-radius: 3px;
    margin-bottom: {_sc(4)};
}}
.mermaid-error-box pre {{
    background: #ffffff;
    border-color: #fecaca;
    color: #7f1d1d;
    margin: {_sc(4)} 0 0 0;
}}
.math-inline {{
    display: inline;
}}
.math-display, .katex-display {{
    display: block;
    margin: {_sc(8)} 0;
    overflow-wrap: break-word;
    max-width: 100%;
    break-inside: avoid;
    page-break-inside: avoid;
    text-align: center;
}}
.math-error {{
    color: #b91c1c;
    background-color: #fef2f2;
    border: 1px solid #fca5a5;
    border-radius: 3px;
    padding: 1px 4px;
    font-family: {theme_spec.mono_family};
    font-size: 0.85em;
}}
{callout_css}
li.task-item {{
    list-style-type: none;
    margin-left: -14px;
}}
li.task-item input[type="checkbox"] {{
    margin: 0 6px 0 0;
    vertical-align: middle;
    accent-color: {accent};
}}
.toc {{
    border: 1px solid #e2e8f0;
    background-color: #f8fafc;
    border-radius: 4px;
    padding: {_sc(8)} 12px;
    margin: {_sc(8)} 0 {_sc(12)} 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}
.toc .toctitle {{
    display: block;
    font-weight: 700;
    color: {accent};
    margin-bottom: {_sc(4)};
}}
.toc ul {{
    margin: {_sc(2)} 0;
    padding-left: 18px;
}}
.toc li {{
    margin-bottom: {_sc(2)};
}}
.toc a {{
    color: {accent};
    text-decoration: none;
}}
.footnote {{
    margin-top: {_sc(14)};
    font-size: 0.88em;
    color: #475569;
}}
.footnote hr {{
    border: none;
    border-top: 1px solid #cbd5e1;
    margin: {_sc(8)} 0;
}}
.footnote ol {{
    padding-left: 18px;
}}
.footnote li {{
    margin-bottom: {_sc(3)};
}}
.footnote-ref {{
    font-size: 0.78em;
    vertical-align: super;
    color: {accent};
    text-decoration: none;
}}
.footnote-backref {{
    color: {accent};
    text-decoration: none;
    margin-left: 4px;
}}
/* Document metadata header blocks */
.doc-header {{
    margin-bottom: {_sc(12)};
    padding-bottom: {_sc(8)};
    border-bottom: 1.5px solid #cbd5e1;
}}
.doc-header-title {{
    margin-top: 0;
    margin-bottom: {_sc(4)};
}}
.doc-header-subtitle {{
    font-size: 1.08em;
    color: #475569;
    margin: 0 0 {_sc(6)} 0;
}}
.doc-header-meta {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    font-size: 0.88em;
    color: #475569;
    margin-top: {_sc(4)};
}}
.doc-header-meta-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
    gap: 4px 12px;
    font-size: 0.88em;
    color: #334155;
    margin-top: {_sc(6)};
}}
.doc-header-meta-item {{
    overflow-wrap: break-word;
}}
.doc-header-meta-label {{
    font-weight: 600;
    color: #1e293b;
    margin-right: 4px;
}}
.doc-header-meta-value {{
    color: #475569;
}}
.doc-header-status-badge {{
    display: inline-block;
    font-size: 0.78em;
    font-weight: 700;
    letter-spacing: 0.04em;
    padding: 1px 7px;
    border-radius: 999px;
    background-color: #e0f2fe;
    color: {accent};
    border: 1px solid #bae6fd;
    vertical-align: middle;
}}
.doc-header-abstract {{
    margin-top: {_sc(8)};
    padding: {_sc(6)} 12px;
    background-color: #f8fafc;
    border-left: 3px solid {accent};
    font-size: 0.92em;
    color: #334155;
}}
.doc-header-abstract-label {{
    display: block;
    font-weight: 700;
    color: {accent};
    margin-bottom: {_sc(2)};
}}
.doc-header-office {{
    border-top: 3px solid {accent};
    padding-top: {_sc(8)};
}}
.doc-header-office .doc-header-title {{
    border-bottom: none;
    padding-bottom: 0;
}}
.doc-header-student {{
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-top: 3px solid {accent};
    border-radius: 4px;
    padding: {_sc(8)} 12px;
}}
.doc-header-paper {{
    text-align: center;
    border-bottom: 1px solid #cbd5e1;
}}
.doc-header-paper .doc-header-meta {{
    justify-content: center;
}}
.doc-header-paper .doc-header-abstract {{
    text-align: left;
    margin-left: 12px;
    margin-right: 12px;
    border-left: none;
    border-top: 0.5px solid #cbd5e1;
    border-bottom: 0.5px solid #cbd5e1;
}}
.doc-header-tech-spec {{
    border-bottom: 2px solid {accent};
}}
.doc-header-tech-spec .doc-header-title-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
}}
.doc-header-executive {{
    border-left: 5px solid {accent};
    background-color: #f8fafc;
    padding: {_sc(8)} 14px;
    border-bottom: 1px solid #e2e8f0;
}}
.doc-header-executive .doc-header-title {{
    border-left: none;
    padding-left: 0;
}}
.doc-header-minimal {{
    border-bottom: 0.5pt solid #d1d5db;
}}
.doc-header-classic {{
    text-align: center;
    border-bottom: 1px solid #cfd8dc;
}}
.doc-header-classic .doc-header-meta {{
    justify-content: center;
}}
"""


def _render_meta_items(
    fm: FrontmatterData,
    priority_order: tuple[str, ...],
    container_class: str,
) -> str:
    """Render non-empty metadata fields in priority order inside a container."""
    ordered_fields: list[str] = list(priority_order)
    for field_name in _STRUCTURED_META_FIELDS:
        if field_name not in ("subtitle", "abstract") and field_name not in ordered_fields:
            ordered_fields.append(field_name)

    items: list[str] = []
    for field_name in ordered_fields:
        val = _clean_opt_str(getattr(fm, field_name))
        if val is None:
            continue
        label = _META_LABELS.get(field_name, field_name.title())
        if field_name == "status":
            slug = _sanitize_status_slug(val)
            items.append(
                f'<span class="doc-header-meta-item">'
                f'<span class="doc-header-meta-label">{_esc(label)}:</span>'
                f'<span class="doc-header-status-badge doc-header-status-{slug}">'
                f"{_esc(val)}</span></span>"
            )
        else:
            items.append(
                f'<span class="doc-header-meta-item">'
                f'<span class="doc-header-meta-label">{_esc(label)}:</span>'
                f'<span class="doc-header-meta-value">{_esc(val)}</span></span>'
            )

    if not items:
        return ""
    return f'<div class="{container_class}">{"".join(items)}</div>'


def build_metadata_header_html(
    options: ConversionOptions,
    fm: FrontmatterData,
) -> tuple[str, str | None]:
    """Build escaped theme-tailored <header> metadata HTML and optional H1 dedup title."""
    theme_name = get_theme(options.theme).name
    fm_title = _clean_opt_str(fm.title)
    has_structured_meta = any(
        _clean_opt_str(getattr(fm, field_name)) is not None
        for field_name in _STRUCTURED_META_FIELDS
    )

    if fm_title is None and not has_structured_meta:
        return ("", None)

    opt_title = _clean_opt_str(options.title)
    if opt_title is not None and opt_title != DEFAULT_TITLE:
        display_title: str | None = opt_title
    elif fm_title is not None:
        display_title = fm_title
    else:
        display_title = None

    subtitle = _clean_opt_str(fm.subtitle)
    abstract = _clean_opt_str(fm.abstract)
    status = _clean_opt_str(fm.status)

    parts: list[str] = [f'<header class="doc-header doc-header-{theme_name}">']

    if theme_name == "tech-spec":
        if display_title is not None or status is not None:
            parts.append('<div class="doc-header-title-row">')
            if display_title is not None:
                parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
            if status is not None:
                slug = _sanitize_status_slug(status)
                parts.append(
                    f'<span class="doc-header-status-badge doc-header-status-{slug}">'
                    f"{_esc(status)}</span>"
                )
            parts.append("</div>")
        if subtitle is not None:
            parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')
        # Avoid duplicating status in grid when already shown in title row
        grid_fields = ("version", "owner", "date", "author", "affiliation")
        grid_items: list[str] = []
        for field_name in grid_fields + tuple(
            f
            for f in _STRUCTURED_META_FIELDS
            if f not in ("subtitle", "abstract", "status", *grid_fields)
        ):
            val = _clean_opt_str(getattr(fm, field_name))
            if val is not None:
                label = _META_LABELS.get(field_name, field_name.title())
                grid_items.append(
                    f'<span class="doc-header-meta-item">'
                    f'<span class="doc-header-meta-label">{_esc(label)}:</span>'
                    f'<span class="doc-header-meta-value">{_esc(val)}</span></span>'
                )
        if grid_items:
            parts.append(f'<div class="doc-header-meta-grid">{"".join(grid_items)}</div>')
    elif theme_name == "student":
        if display_title is not None:
            parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
        if subtitle is not None:
            parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')
        meta_html = _render_meta_items(
            fm,
            ("course", "instructor", "student", "date"),
            "doc-header-meta-grid",
        )
        if meta_html:
            parts.append(meta_html)
    elif theme_name == "paper":
        if display_title is not None:
            parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
        if subtitle is not None:
            parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')
        meta_html = _render_meta_items(
            fm,
            ("author", "affiliation", "date"),
            "doc-header-meta",
        )
        if meta_html:
            parts.append(meta_html)
    elif theme_name == "executive":
        if display_title is not None:
            parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
        if subtitle is not None:
            parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')
        meta_html = _render_meta_items(
            fm,
            ("date", "author", "status", "version", "owner"),
            "doc-header-meta",
        )
        if meta_html:
            parts.append(meta_html)
    elif theme_name == "minimal":
        if display_title is not None:
            parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
        if subtitle is not None:
            parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')
        meta_html = _render_meta_items(
            fm,
            ("author", "date", "version", "status"),
            "doc-header-meta",
        )
        if meta_html:
            parts.append(meta_html)
    elif theme_name == "classic":
        if display_title is not None:
            parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
        if subtitle is not None:
            parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')
        meta_html = _render_meta_items(
            fm,
            ("author", "date", "version"),
            "doc-header-meta",
        )
        if meta_html:
            parts.append(meta_html)
    else:
        # "office"
        if display_title is not None:
            parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
        if subtitle is not None:
            parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')
        meta_html = _render_meta_items(
            fm,
            ("author", "date", "version", "status", "owner"),
            "doc-header-meta",
        )
        if meta_html:
            parts.append(meta_html)

    if abstract is not None:
        parts.append(
            '<div class="doc-header-abstract">'
            '<span class="doc-header-abstract-label">Abstract</span>'
            f"<p>{_esc(abstract)}</p></div>"
        )

    parts.append("</header>")
    return ("".join(parts), display_title)


def build_header_footer_templates(
    options: ConversionOptions,
    fm: FrontmatterData,
) -> tuple[bool, str, str]:
    """Build Chromium CDP (displayHeaderFooter, headerTemplate, footerTemplate)."""
    top_mm, right_mm, bottom_mm, left_mm = parse_margin_mm(options.margin_css)
    if top_mm < 8.0 or bottom_mm < 8.0:
        return (False, "", "")

    theme_spec = get_theme(options.theme)
    theme_name = theme_spec.name

    opt_title = _clean_opt_str(options.title)
    fm_title = _clean_opt_str(fm.title)
    doc_title = (
        opt_title if (opt_title is not None and opt_title != DEFAULT_TITLE) else fm_title
    )

    # Resolve header text independently
    header_text: str | None
    if options.header is False:
        header_text = None
    elif isinstance(options.header, str):
        header_text = _clean_opt_str(options.header)
    elif options.header is True or theme_name in _RUNNING_HEADER_DEFAULT_THEMES:
        header_text = doc_title
    else:
        header_text = None

    # Resolve footer text independently
    fm_author = _clean_opt_str(fm.author)
    fm_date = _clean_opt_str(fm.date)
    footer_text: str | None
    if options.footer is False:
        footer_text = None
    elif isinstance(options.footer, str):
        footer_text = _clean_opt_str(options.footer)
    elif options.footer is True:
        footer_parts = [p for p in (fm_author, fm_date) if p is not None]
        footer_text = " · ".join(footer_parts) if footer_parts else doc_title
    elif theme_name == "paper":
        paper_parts = [p for p in (fm_author, fm_date) if p is not None]
        footer_text = " · ".join(paper_parts) if paper_parts else None
    elif theme_name == "executive":
        exec_parts = [p for p in (doc_title, fm_date) if p is not None]
        footer_text = " · ".join(exec_parts) if exec_parts else None
    else:
        footer_text = None

    show_page_numbers = bool(options.page_numbers)
    has_header = header_text is not None
    has_footer = (footer_text is not None) or show_page_numbers

    if not has_header and not has_footer:
        return (False, "", "")

    safe_font = html.escape(theme_spec.font_family, quote=False).replace('"', "'")
    base_style = (
        f"font-size: 8px; font-family: {safe_font}; width: 100%; "
        f"box-sizing: border-box; padding: 0 {right_mm:g}mm 0 {left_mm:g}mm; "
        "color: #64748b; display: flex; justify-content: space-between; "
        "align-items: center; -webkit-print-color-adjust: exact;"
    )

    if header_text is not None:
        header_template = (
            f'<div style="{base_style} border-bottom: 0.5px solid #e2e8f0; '
            f'padding-bottom: 2px;"><span>{_esc(header_text)}</span><span></span></div>'
        )
    else:
        header_template = "<span></span>"

    if has_footer:
        left_span = (
            f"<span>{_esc(footer_text)}</span>"
            if footer_text is not None
            else "<span></span>"
        )
        if show_page_numbers:
            if theme_name in ("minimal", "classic"):
                page_span = (
                    '<span><span class="pageNumber"></span> / '
                    '<span class="totalPages"></span></span>'
                )
            else:
                page_span = (
                    '<span>Page <span class="pageNumber"></span> / '
                    '<span class="totalPages"></span></span>'
                )
        else:
            page_span = "<span></span>"
        footer_template = f'<div style="{base_style}">{left_span}{page_span}</div>'
    else:
        footer_template = "<span></span>"

    return (True, header_template, footer_template)
