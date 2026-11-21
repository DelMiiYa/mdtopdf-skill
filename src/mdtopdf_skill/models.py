"""Core contracts, presets, validators, and option resolution for mdtopdf-skill."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_NO_BROWSER = 3
EXIT_RENDER_ERROR = 4
EXIT_EMPTY_PDF = 5
EXIT_STRICT_ISSUE = 6

DEFAULT_THEME = "office"
DEFAULT_LANG = "th"
DEFAULT_TITLE = "Markdown Document"

FONT_SIZE_MIN = 6.0
FONT_SIZE_MAX = 72.0
LINE_HEIGHT_MIN = 1.0
LINE_HEIGHT_MAX = 3.0
SPACING_SCALE_MIN = 0.5
SPACING_SCALE_MAX = 2.0

THEME_NAMES: frozenset[str] = frozenset(
    {"office", "student", "paper", "tech-spec", "executive", "minimal", "classic"}
)

MARGIN_PRESETS: dict[str, str] = {
    "compact": "10mm 12mm",
    "standard": "12mm 14mm",
    "comfortable": "15mm 18mm",
    "wide": "18mm 20mm",
}

PAGE_SIZE_PRESETS: dict[str, tuple[float, float]] = {
    "a4": (210.0, 297.0),
    "a4 portrait": (210.0, 297.0),
    "a4 landscape": (297.0, 210.0),
    "letter": (215.9, 279.4),
    "letter portrait": (215.9, 279.4),
    "letter landscape": (279.4, 215.9),
    "legal": (215.9, 355.6),
    "legal portrait": (215.9, 355.6),
    "legal landscape": (355.6, 215.9),
}

_CANONICAL_PAGE_SIZES: dict[str, str] = {
    "a4": "A4 portrait",
    "a4 portrait": "A4 portrait",
    "a4 landscape": "A4 landscape",
    "letter": "Letter portrait",
    "letter portrait": "Letter portrait",
    "letter landscape": "Letter landscape",
    "legal": "Legal portrait",
    "legal portrait": "Legal portrait",
    "legal landscape": "Legal landscape",
}

_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72.0,
    "pc": 25.4 / 6.0,
    "px": 25.4 / 96.0,
}

_LENGTH_TOKEN_RE = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)(mm|cm|in|pt|pc|px)$", flags=re.IGNORECASE
)
_CSS_MARGIN_RE = re.compile(
    r"^(?:0|[0-9]+(?:\.[0-9]+)?(?:mm|cm|in|pt|pc|px))"
    r"(?:\s+(?:0|[0-9]+(?:\.[0-9]+)?(?:mm|cm|in|pt|pc|px))){0,3}$",
    flags=re.IGNORECASE,
)
_CSS_PAGE_SIZE_RE = re.compile(
    r"^([0-9]+(?:\.[0-9]+)?)(mm|cm|in|pt|pc|px)\s+([0-9]+(?:\.[0-9]+)?)(mm|cm|in|pt|pc|px)$",
    flags=re.IGNORECASE,
)
_ACCENT_COLOR_RE = re.compile(
    r"^(?:#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|[a-zA-Z]{3,20})$"
)
LANG_RE = re.compile(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})*$")

_MM_PER_INCH = 25.4
_PX_PER_INCH = 96.0
_PT_PER_INCH = 72.0


def _safe_repr(value: object, max_len: int = 200) -> str:
    """Format value with repr(), guarding against >4300-digit int ValueError and huge outputs."""
    try:
        rendered = repr(value)
    except ValueError:
        return f"<{type(value).__name__} (too large to format)>"
    if len(rendered) > max_len:
        return f"{rendered[: max_len - 3]}..."
    return rendered


@dataclass(frozen=True)
class LayoutIssue:
    """Layout diagnostic emitted during browser inspection or PDF auto-fit."""

    kind: Literal["table_overflow", "trailing_orphan_page", "wide_element"]
    detail: str
    suggestion: str
    table_index: int | None = None
    overflow_px: float | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-ready dictionary."""
        data: dict[str, object] = {
            "kind": self.kind,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }
        if self.table_index is not None:
            data["table_index"] = self.table_index
        if self.overflow_px is not None:
            data["overflow_px"] = self.overflow_px
        return data


@dataclass(frozen=True)
class MermaidError:
    """Error captured while rendering a Mermaid diagram block."""

    index: int
    message: str
    snippet: str

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-ready dictionary."""
        return {
            "index": self.index,
            "message": self.message,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class MathError:
    """Error captured while rendering a KaTeX math expression."""

    index: int
    display_mode: bool
    message: str
    tex: str

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-ready dictionary."""
        return {
            "index": self.index,
            "display_mode": self.display_mode,
            "message": self.message,
            "tex": self.tex,
        }


@dataclass(frozen=True)
class AdjustmentRecord:
    """Record of an automatic layout remediation step applied during rendering."""

    step: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        """Serialize to JSON-ready dictionary."""
        return {
            "step": self.step,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ThemeSpec:
    """Immutable specification of a visual theme."""

    name: str
    description: str
    font_size_pt: float
    line_height: float
    margin_css: str
    accent_color: str
    page_numbers: bool
    font_family: str
    mono_family: str
    is_default: bool = False


@dataclass(frozen=True)
class PageGeometry:
    """Resolved physical page dimensions and printable area in mm, px (96 DPI), and pt."""

    page_size: str
    page_width_mm: float
    page_height_mm: float
    margin_top_mm: float
    margin_right_mm: float
    margin_bottom_mm: float
    margin_left_mm: float
    printable_width_mm: float
    printable_height_mm: float
    printable_width_px: float
    printable_height_px: float
    margin_top_pt: float
    margin_bottom_pt: float


@dataclass(frozen=True)
class PdfInspection:
    """Result of inspecting rendered PDF pages via PyMuPDF."""

    page_count: int
    last_page_fill_ratio: float
    body_line_count: int
    body_images: int
    body_vector_diagrams: int
    is_trailing_orphan: bool


@dataclass
class AutoFitOutcome:
    """Outcome produced by the auto-fit render and inspection pipeline."""

    pdf_bytes: bytes
    page_count: int
    last_page_fill_ratio: float
    layout_issues: list[LayoutIssue] = field(default_factory=list)
    mermaid_errors: list[MermaidError] = field(default_factory=list)
    math_errors: list[MathError] = field(default_factory=list)
    adjustments_applied: list[AdjustmentRecord] = field(default_factory=list)


@dataclass
class FrontmatterData:
    """Validated metadata and presentation overrides extracted from YAML frontmatter."""

    theme: str | None = None
    lang: str | None = None
    title: str | None = None
    subtitle: str | None = None
    author: str | None = None
    date: str | None = None
    course: str | None = None
    instructor: str | None = None
    student: str | None = None
    affiliation: str | None = None
    abstract: str | None = None
    status: str | None = None
    version: str | None = None
    owner: str | None = None
    font_size_pt: float | None = None
    line_height: float | None = None
    spacing_scale: float | None = None
    margin_css: str | None = None
    page_size: str | None = None
    accent_color: str | None = None
    page_numbers: bool | None = None
    header: str | bool | None = None
    footer: str | bool | None = None
    toc: bool | None = None
    css: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ConversionOptions:
    """Effective conversion options after 4-layer precedence resolution."""

    theme: str = "office"
    lang: str = "th"
    font_size_pt: float = 9.5
    line_height: float = 1.45
    spacing_scale: float = 1.0
    margin_css: str = "15mm 16mm"
    page_size: str = "A4 portrait"
    img_max_width: int = 90
    img_max_height: int = 70
    suppress_missing_images: bool = True
    keep_blocks_together: bool = True
    accent_color: str = "#0f3d7a"
    title: str = "Markdown Document"
    page_numbers: bool = True
    header: str | bool | None = None
    footer: str | bool | None = None
    toc: bool = False
    frontmatter_css: str = ""
    cli_css: str = ""
    auto_fit: bool = True
    strict: bool = False
    allow_outside_base_dir: bool = False
    preview_dir: str | None = None
    preview_dpi: int = 144


@dataclass
class ConversionResult:
    """Result returned by the PDF conversion pipeline."""

    output_pdf_path: str
    theme: str
    page_count: int
    embedded_images: int
    suppressed_images: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    layout_issues: list[LayoutIssue] = field(default_factory=list)
    mermaid_errors: list[MermaidError] = field(default_factory=list)
    math_errors: list[MathError] = field(default_factory=list)
    adjustments_applied: list[AdjustmentRecord] = field(default_factory=list)
    last_page_fill_ratio: float = 1.0
    preview_images: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


_DEFAULT_THEME_SPEC = ThemeSpec(
    name=DEFAULT_THEME,
    description="Corporate memos, business reports, and SOPs",
    font_size_pt=9.5,
    line_height=1.45,
    margin_css="15mm 16mm",
    accent_color="#0f3d7a",
    page_numbers=True,
    font_family=(
        "'Leelawadee UI', 'Tahoma', 'Noto Sans Thai', 'Sarabun', "
        "'Noto Sans', 'Segoe UI Emoji', 'Noto Color Emoji', sans-serif"
    ),
    mono_family="'Consolas', 'JetBrains Mono', 'Noto Sans Mono', 'DejaVu Sans Mono', monospace",
    is_default=True,
)


def validate_finite_float(
    value: float | int | str,
    name: str,
    min_val: float,
    max_val: float,
) -> float:
    """Validate that value is a finite float within [min_val, max_val] (rejecting bool)."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, got bool")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a valid number, got {_safe_repr(value)}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite (NaN/inf not allowed), got {_safe_repr(value)}")
    if parsed < min_val or parsed > max_val:
        raise ValueError(f"{name} must be between {min_val} and {max_val}, got {parsed}")
    return parsed


def validate_font_size(value: float | int | str) -> float:
    """Validate font size in points within [6.0, 72.0]."""
    return validate_finite_float(value, "font_size", FONT_SIZE_MIN, FONT_SIZE_MAX)


def validate_line_height(value: float | int | str) -> float:
    """Validate line height multiplier within [1.0, 3.0]."""
    return validate_finite_float(value, "line_height", LINE_HEIGHT_MIN, LINE_HEIGHT_MAX)


def validate_spacing_scale(value: float | int | str) -> float:
    """Validate vertical spacing scale factor within [0.5, 2.0]."""
    return validate_finite_float(value, "spacing_scale", SPACING_SCALE_MIN, SPACING_SCALE_MAX)


def validate_positive_int(value: int | str, name: str = "value") -> int:
    """Validate that value is an integer >= 1 (rejecting bool and floats)."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer, got bool")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if not re.fullmatch(r"[+-]?[0-9]+", cleaned):
            raise ValueError(f"{name} must be a positive integer, got {_safe_repr(value)}")
        try:
            parsed = int(cleaned)
        except ValueError as exc:
            raise ValueError(
                f"{name} must be a positive integer, got {_safe_repr(value)}"
            ) from exc
    else:
        raise ValueError(f"{name} must be a positive integer, got {_safe_repr(value)}")
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1, got {_safe_repr(parsed)}")
    return parsed


def validate_lang(value: str) -> str:
    """Validate an HTML BCP-47 language tag."""
    if not isinstance(value, str):
        raise ValueError(f"lang must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not LANG_RE.fullmatch(cleaned):
        raise ValueError(f"Invalid language tag {_safe_repr(value)}")
    return cleaned


def validate_accent_color(value: str) -> str:
    """Validate a CSS hex (#rgb/#rgba/#rrggbb/#rrggbbaa) or named color."""
    if not isinstance(value, str):
        raise ValueError(f"accent_color must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not _ACCENT_COLOR_RE.fullmatch(cleaned):
        raise ValueError(f"Invalid accent color {_safe_repr(value)}")
    return cleaned


def resolve_theme_name(
    value: str,
    themes: Mapping[str, ThemeSpec] | None = None,
) -> str:
    """Normalize and validate a theme name against themes mapping or THEME_NAMES."""
    if not isinstance(value, str):
        raise ValueError(f"theme must be a string, got {type(value).__name__}")
    cleaned = value.strip().lower()
    valid_names = frozenset(themes.keys()) if themes is not None else THEME_NAMES
    if cleaned not in valid_names:
        available = ", ".join(sorted(valid_names))
        raise ValueError(f"Unknown theme {_safe_repr(value)}. Available themes: {available}")
    return cleaned


def resolve_margin_css(value: str) -> str:
    """Resolve a margin preset or 1-4 physical CSS length tokens (no '%')."""
    if not isinstance(value, str):
        raise ValueError(f"margin must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("margin cannot be empty")
    preset = MARGIN_PRESETS.get(cleaned.lower())
    if preset is not None:
        return preset
    if not _CSS_MARGIN_RE.fullmatch(cleaned):
        raise ValueError(
            f"Invalid margin {_safe_repr(value)}; expected preset "
            f"({', '.join(MARGIN_PRESETS)}) or 1-4 physical CSS lengths (mm/cm/in/pt/pc/px/0)"
        )
    normalized = re.sub(r"\s+", " ", cleaned).lower()
    for token in normalized.split(" "):
        _token_to_mm(token)
    return normalized


def _token_to_mm(raw_length: str) -> float:
    """Convert a single physical CSS length token or '0' to millimeters."""
    if raw_length == "0":
        return 0.0
    match = _LENGTH_TOKEN_RE.fullmatch(raw_length)
    if match is None:
        raise ValueError(f"Invalid physical length token: {_safe_repr(raw_length)}")
    amount = float(match.group(1))
    unit = match.group(2).lower()
    result = amount * _UNIT_TO_MM[unit]
    if not math.isfinite(result):
        raise ValueError(f"Non-finite length token: {_safe_repr(raw_length)}")
    return result


def parse_margin_mm(margin_css: str) -> tuple[float, float, float, float]:
    """Parse CSS margin string into (top_mm, right_mm, bottom_mm, left_mm)."""
    resolved = resolve_margin_css(margin_css)
    tokens = [_token_to_mm(tok) for tok in resolved.split(" ")]
    if len(tokens) == 1:
        val = tokens[0]
        return (val, val, val, val)
    if len(tokens) == 2:
        vertical, horizontal = tokens
        return (vertical, horizontal, vertical, horizontal)
    if len(tokens) == 3:
        top, horizontal, bottom = tokens
        return (top, horizontal, bottom, horizontal)
    if len(tokens) == 4:
        return (tokens[0], tokens[1], tokens[2], tokens[3])
    raise ValueError(f"Invalid margin token count in {_safe_repr(margin_css)}")


def resolve_page_size(value: str) -> str:
    """Resolve a page-size preset or explicit '<width> <height>' physical CSS size."""
    if not isinstance(value, str):
        raise ValueError(f"page_size must be a string, got {type(value).__name__}")
    normalized = re.sub(r"\s+", " ", value.strip())
    if not normalized:
        raise ValueError("page_size cannot be empty")
    canonical = _CANONICAL_PAGE_SIZES.get(normalized.lower())
    if canonical is not None:
        return canonical
    match = _CSS_PAGE_SIZE_RE.fullmatch(normalized)
    if match is None:
        raise ValueError(
            f"Invalid page_size {_safe_repr(value)}; expected preset (A4/Letter/Legal "
            "[portrait|landscape]) or '<width><unit> <height><unit>'"
        )
    width_raw, width_unit, height_raw, height_unit = match.groups()
    width_mm = _token_to_mm(f"{width_raw}{width_unit}")
    height_mm = _token_to_mm(f"{height_raw}{height_unit}")
    if width_mm <= 0 or height_mm <= 0:
        raise ValueError(f"page_size dimensions must be positive, got {_safe_repr(value)}")
    return f"{width_raw}{width_unit.lower()} {height_raw}{height_unit.lower()}"


def parse_page_size_mm(page_size: str) -> tuple[float, float]:
    """Parse a page-size preset or custom '<width> <height>' into (width_mm, height_mm)."""
    resolved = resolve_page_size(page_size)
    preset = PAGE_SIZE_PRESETS.get(resolved.lower())
    if preset is not None:
        return preset
    match = _CSS_PAGE_SIZE_RE.fullmatch(resolved)
    if match is None:
        raise ValueError(f"Invalid resolved page_size {_safe_repr(resolved)}")
    width_raw, width_unit, height_raw, height_unit = match.groups()
    width_mm = _token_to_mm(f"{width_raw}{width_unit}")
    height_mm = _token_to_mm(f"{height_raw}{height_unit}")
    return (width_mm, height_mm)


def compute_page_geometry(page_size: str, margin_css: str) -> PageGeometry:
    """Compute physical page and printable box geometry at 96 DPI (px) and 72 DPI (pt)."""
    resolved_size = resolve_page_size(page_size)
    page_width_mm, page_height_mm = parse_page_size_mm(resolved_size)
    top_mm, right_mm, bottom_mm, left_mm = parse_margin_mm(margin_css)

    printable_width_mm = page_width_mm - left_mm - right_mm
    printable_height_mm = page_height_mm - top_mm - bottom_mm
    if not (math.isfinite(printable_width_mm) and math.isfinite(printable_height_mm)):
        raise ValueError(
            f"Computed printable dimensions are non-finite for {_safe_repr(resolved_size)} "
            f"with margins {_safe_repr(margin_css)}"
        )
    if printable_width_mm <= 0 or printable_height_mm <= 0:
        raise ValueError(
            f"Margins ({_safe_repr(margin_css)}) leave non-positive printable area on "
            f"{_safe_repr(resolved_size)}: "
            f"{printable_width_mm:.2f}mm x {printable_height_mm:.2f}mm"
        )

    printable_width_px = printable_width_mm * _PX_PER_INCH / _MM_PER_INCH
    printable_height_px = printable_height_mm * _PX_PER_INCH / _MM_PER_INCH
    margin_top_pt = top_mm * _PT_PER_INCH / _MM_PER_INCH
    margin_bottom_pt = bottom_mm * _PT_PER_INCH / _MM_PER_INCH

    numeric_fields = (
        page_width_mm,
        page_height_mm,
        top_mm,
        right_mm,
        bottom_mm,
        left_mm,
        printable_width_mm,
        printable_height_mm,
        printable_width_px,
        printable_height_px,
        margin_top_pt,
        margin_bottom_pt,
    )
    if not all(math.isfinite(val) for val in numeric_fields):
        raise ValueError(
            f"Computed page geometry is non-finite for {_safe_repr(resolved_size)} "
            f"with margins {_safe_repr(margin_css)}"
        )

    return PageGeometry(
        page_size=resolved_size,
        page_width_mm=page_width_mm,
        page_height_mm=page_height_mm,
        margin_top_mm=top_mm,
        margin_right_mm=right_mm,
        margin_bottom_mm=bottom_mm,
        margin_left_mm=left_mm,
        printable_width_mm=printable_width_mm,
        printable_height_mm=printable_height_mm,
        printable_width_px=printable_width_px,
        printable_height_px=printable_height_px,
        margin_top_pt=margin_top_pt,
        margin_bottom_pt=margin_bottom_pt,
    )


def resolve_effective_options(
    *,
    frontmatter: FrontmatterData | None = None,
    theme_spec: ThemeSpec | None = None,
    themes: Mapping[str, ThemeSpec] | None = None,
    theme: str | None = None,
    lang: str | None = None,
    font_size_pt: float | None = None,
    line_height: float | None = None,
    spacing_scale: float | None = None,
    margin_css: str | None = None,
    page_size: str | None = None,
    img_max_width: int | None = None,
    img_max_height: int | None = None,
    suppress_missing_images: bool | None = None,
    keep_blocks_together: bool | None = None,
    accent_color: str | None = None,
    title: str | None = None,
    page_numbers: bool | None = None,
    header: str | bool | None = None,
    footer: str | bool | None = None,
    toc: bool | None = None,
    frontmatter_css: str | None = None,
    cli_css: str | None = None,
    auto_fit: bool | None = None,
    strict: bool | None = None,
    allow_outside_base_dir: bool | None = None,
    preview_dir: str | None = None,
    preview_dpi: int | None = None,
    default_title: str = DEFAULT_TITLE,
) -> ConversionOptions:
    """Resolve options via 4-layer precedence: CLI > Frontmatter > Theme > Global."""
    fm = frontmatter

    # 1. Resolve theme name (CLI > Frontmatter > theme_spec > Global DEFAULT_THEME)
    if theme is not None:
        resolved_theme = resolve_theme_name(theme, themes)
    elif fm is not None and fm.theme is not None:
        try:
            resolved_theme = resolve_theme_name(fm.theme, themes)
        except ValueError:
            if theme_spec is not None:
                try:
                    resolved_theme = resolve_theme_name(theme_spec.name, themes)
                except ValueError:
                    resolved_theme = DEFAULT_THEME
            else:
                resolved_theme = DEFAULT_THEME
    elif theme_spec is not None:
        try:
            resolved_theme = resolve_theme_name(theme_spec.name, themes)
        except ValueError:
            resolved_theme = theme_spec.name.strip().lower() or DEFAULT_THEME
    else:
        resolved_theme = DEFAULT_THEME

    # Select active ThemeSpec
    if themes is not None and resolved_theme in themes:
        active_spec = themes[resolved_theme]
    elif theme_spec is not None:
        active_spec = theme_spec
    else:
        active_spec = _DEFAULT_THEME_SPEC

    # 2. Resolve 4-layer scalar options (explicit `is not None` checks only)
    fm_font = fm.font_size_pt if fm is not None else None
    raw_font_size = (
        font_size_pt
        if font_size_pt is not None
        else (fm_font if fm_font is not None else active_spec.font_size_pt)
    )
    resolved_font_size = validate_font_size(raw_font_size)

    fm_lh = fm.line_height if fm is not None else None
    raw_line_height = (
        line_height
        if line_height is not None
        else (fm_lh if fm_lh is not None else active_spec.line_height)
    )
    resolved_line_height = validate_line_height(raw_line_height)

    fm_scale = fm.spacing_scale if fm is not None else None
    raw_spacing_scale = (
        spacing_scale
        if spacing_scale is not None
        else (fm_scale if fm_scale is not None else 1.0)
    )
    resolved_spacing_scale = validate_spacing_scale(raw_spacing_scale)

    fm_margin = fm.margin_css if fm is not None else None
    raw_margin = (
        margin_css
        if margin_css is not None
        else (fm_margin if fm_margin is not None else active_spec.margin_css)
    )
    resolved_margin = resolve_margin_css(raw_margin)

    fm_page_size = fm.page_size if fm is not None else None
    raw_page_size = (
        page_size
        if page_size is not None
        else (fm_page_size if fm_page_size is not None else "A4 portrait")
    )
    resolved_page_size = resolve_page_size(raw_page_size)

    fm_accent = fm.accent_color if fm is not None else None
    raw_accent = (
        accent_color
        if accent_color is not None
        else (fm_accent if fm_accent is not None else active_spec.accent_color)
    )
    resolved_accent = validate_accent_color(raw_accent)

    fm_lang = fm.lang if fm is not None else None
    raw_lang = lang if lang is not None else (fm_lang if fm_lang is not None else DEFAULT_LANG)
    resolved_lang = validate_lang(raw_lang)

    fm_page_numbers = fm.page_numbers if fm is not None else None
    resolved_page_numbers = (
        page_numbers
        if page_numbers is not None
        else (fm_page_numbers if fm_page_numbers is not None else active_spec.page_numbers)
    )

    resolved_toc = (
        toc
        if toc is not None
        else (fm.toc if fm is not None and fm.toc is not None else False)
    )

    resolved_header: str | bool | None = (
        header
        if header is not None
        else (fm.header if fm is not None and fm.header is not None else None)
    )

    resolved_footer: str | bool | None = (
        footer
        if footer is not None
        else (fm.footer if fm is not None and fm.footer is not None else None)
    )

    # 3. Title fallback (first non-empty stripped string)
    title_candidates: tuple[str | None, ...] = (
        title,
        fm.title if fm is not None else None,
        default_title,
        DEFAULT_TITLE,
    )
    resolved_title = DEFAULT_TITLE
    for candidate in title_candidates:
        if candidate is not None:
            stripped = candidate.strip()
            if stripped:
                resolved_title = stripped
                break

    # 4. CSS layering (Theme -> Frontmatter -> CLI -> Auto-fit)
    effective_fm_css = (
        frontmatter_css
        if frontmatter_css is not None
        else (fm.css if fm is not None and fm.css is not None else "")
    )
    effective_cli_css = cli_css if cli_css is not None else ""

    # 5. CLI/Global-only options (never read from frontmatter)
    resolved_img_max_width = (
        validate_positive_int(img_max_width, "img_max_width")
        if img_max_width is not None
        else 90
    )
    resolved_img_max_height = (
        validate_positive_int(img_max_height, "img_max_height")
        if img_max_height is not None
        else 70
    )
    resolved_suppress_missing = (
        suppress_missing_images if suppress_missing_images is not None else True
    )
    resolved_keep_together = (
        keep_blocks_together if keep_blocks_together is not None else True
    )
    resolved_auto_fit = auto_fit if auto_fit is not None else True
    resolved_strict = strict if strict is not None else False
    resolved_allow_outside = (
        allow_outside_base_dir if allow_outside_base_dir is not None else False
    )
    resolved_preview_dpi = (
        validate_positive_int(preview_dpi, "preview_dpi")
        if preview_dpi is not None
        else 144
    )

    return ConversionOptions(
        theme=resolved_theme,
        lang=resolved_lang,
        font_size_pt=resolved_font_size,
        line_height=resolved_line_height,
        spacing_scale=resolved_spacing_scale,
        margin_css=resolved_margin,
        page_size=resolved_page_size,
        img_max_width=resolved_img_max_width,
        img_max_height=resolved_img_max_height,
        suppress_missing_images=resolved_suppress_missing,
        keep_blocks_together=resolved_keep_together,
        accent_color=resolved_accent,
        title=resolved_title,
        page_numbers=resolved_page_numbers,
        header=resolved_header,
        footer=resolved_footer,
        toc=resolved_toc,
        frontmatter_css=effective_fm_css,
        cli_css=effective_cli_css,
        auto_fit=resolved_auto_fit,
        strict=resolved_strict,
        allow_outside_base_dir=resolved_allow_outside,
        preview_dir=preview_dir,
        preview_dpi=resolved_preview_dpi,
    )


__all__ = [
    "AdjustmentRecord",
    "AutoFitOutcome",
    "ConversionOptions",
    "ConversionResult",
    "DEFAULT_LANG",
    "DEFAULT_THEME",
    "DEFAULT_TITLE",
    "EXIT_BAD_ARGS",
    "EXIT_EMPTY_PDF",
    "EXIT_NO_BROWSER",
    "EXIT_OK",
    "EXIT_RENDER_ERROR",
    "EXIT_STRICT_ISSUE",
    "FONT_SIZE_MAX",
    "FONT_SIZE_MIN",
    "FrontmatterData",
    "LANG_RE",
    "LINE_HEIGHT_MAX",
    "LINE_HEIGHT_MIN",
    "LayoutIssue",
    "MARGIN_PRESETS",
    "MathError",
    "MermaidError",
    "PAGE_SIZE_PRESETS",
    "PageGeometry",
    "PdfInspection",
    "SPACING_SCALE_MAX",
    "SPACING_SCALE_MIN",
    "THEME_NAMES",
    "ThemeSpec",
    "compute_page_geometry",
    "parse_margin_mm",
    "parse_page_size_mm",
    "resolve_effective_options",
    "resolve_margin_css",
    "resolve_page_size",
    "resolve_theme_name",
    "validate_accent_color",
    "validate_finite_float",
    "validate_font_size",
    "validate_lang",
    "validate_line_height",
    "validate_positive_int",
    "validate_spacing_scale",
]
