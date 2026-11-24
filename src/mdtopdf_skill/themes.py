"""Opinionated print themes, CSS generation, metadata headers, and CDP chrome templates."""

from __future__ import annotations

import html

from pygments.formatters import HtmlFormatter

from mdtopdf_skill.models import (
    _DEFAULT_THEME_SPEC,
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
    validate_positive_finite_float,
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

_SERIF_FONT_STACK = (
    "'Georgia', 'Times New Roman', 'Noto Serif Thai', 'Sarabun', "
    "'Liberation Serif', 'DejaVu Serif', 'Segoe UI Emoji', 'Noto Color Emoji', serif"
)
_MONO_FONT_STACK = (
    "'Consolas', 'JetBrains Mono', 'Noto Sans Mono', 'DejaVu Sans Mono', monospace"
)

THEMES: dict[str, ThemeSpec] = {
    "classic": _DEFAULT_THEME_SPEC,
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
}

_STRUCTURED_META_FIELDS: tuple[str, ...] = (
    "subtitle",
    "author",
    "date",
    "affiliation",
    "abstract",
)

_META_LABELS: dict[str, str] = {
    "author": "Author",
    "date": "Date",
    "affiliation": "Affiliation",
}


def _clean_opt_str(value: str | None) -> str | None:
    """Return stripped string or None if empty/whitespace-only."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _esc(value: str) -> str:
    """Escape text safely for HTML text nodes and double-quoted attributes."""
    return html.escape(value, quote=True)


def _sc(px: float) -> str:
    """Emit a vertical spacing expression scaled by --md-spacing-scale."""
    formatted = f"{px:g}"
    return f"calc({formatted}px * var(--md-spacing-scale, 1))"


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
    break-inside: avoid;
    page-break-inside: avoid;
}}
h2 {{
    font-size: {round(font_pt * 1.22, 1):g}pt;
    color: {accent};
    border-bottom: 1px solid #cbd5e1;
    padding-bottom: {_sc(3)};
    margin-top: {_sc(14)};
    margin-bottom: {_sc(6)};
    break-inside: avoid;
    page-break-inside: avoid;
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {round(font_pt * 1.05, 1):g}pt;
    color: #334155;
    font-style: italic;
    margin-top: {_sc(10)};
    margin-bottom: {_sc(4)};
    break-inside: avoid;
    page-break-inside: avoid;
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #475569;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    break-inside: avoid;
    page-break-inside: avoid;
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

    # Default: "classic"
    h1_pt = round(font_pt * 1.63, 1)
    h2_pt = round(font_pt * 1.21, 1)
    h3_pt = round(font_pt * 1.03, 1)
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
    break-inside: avoid;
    page-break-inside: avoid;
}}
h2 {{
    font-size: {h2_pt:g}pt;
    color: {accent};
    border-bottom: 1.5px solid #1976d2;
    padding-bottom: {_sc(3)};
    margin-top: {_sc(12)};
    margin-bottom: {_sc(6)};
    break-inside: avoid;
    page-break-inside: avoid;
    break-after: avoid;
    page-break-after: avoid;
}}
h3 {{
    font-size: {h3_pt:g}pt;
    color: #1565c0;
    margin-top: {_sc(8)};
    margin-bottom: {_sc(4)};
    break-inside: avoid;
    page-break-inside: avoid;
    break-after: avoid;
    page-break-after: avoid;
}}
h4 {{
    font-size: {font_pt:g}pt;
    color: #1e3a8a;
    margin-top: {_sc(6)};
    margin-bottom: {_sc(3)};
    break-inside: avoid;
    page-break-inside: avoid;
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


def _build_callout_css() -> str:
    """Build GitHub-style admonition/callout CSS."""
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
    printable_h = validate_positive_finite_float(printable_height_mm, "printable_height_mm")

    mermaid_max_height_mm = min(printable_h, max(1.0, printable_h - 8.0))
    pygments_css = _get_pygments_css("default")
    theme_variant_css = _build_theme_variant_css(
        theme_name=theme_name,
        theme_spec=theme_spec,
        accent=accent,
        font_pt=font_pt,
        line_h=line_h,
        keep_together=bool(options.keep_blocks_together),
    )
    callout_css = _build_callout_css()

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
    field_order: tuple[str, ...],
) -> str:
    """Render non-empty metadata fields in priority order inside .doc-header-meta."""
    items: list[str] = []
    for field_name in field_order:
        val = _clean_opt_str(getattr(fm, field_name))
        if val is None:
            continue
        label = _META_LABELS.get(field_name, field_name.title())
        items.append(
            f'<span class="doc-header-meta-item">'
            f'<span class="doc-header-meta-label">{_esc(label)}:</span>'
            f'<span class="doc-header-meta-value">{_esc(val)}</span></span>'
        )

    if not items:
        return ""
    return f'<div class="doc-header-meta">{"".join(items)}</div>'


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

    parts: list[str] = [f'<header class="doc-header doc-header-{theme_name}">']
    if display_title is not None:
        parts.append(f'<h1 class="doc-header-title">{_esc(display_title)}</h1>')
    if subtitle is not None:
        parts.append(f'<p class="doc-header-subtitle">{_esc(subtitle)}</p>')

    priority_order = (
        ("author", "affiliation", "date")
        if theme_name == "paper"
        else ("author", "date", "affiliation")
    )
    meta_html = _render_meta_items(fm, priority_order)
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
    elif options.header is True or theme_name == "paper":
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
            if theme_name == "classic":
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
