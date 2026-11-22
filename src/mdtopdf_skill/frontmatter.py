"""Safe YAML frontmatter parser for Markdown inputs."""

from __future__ import annotations

import datetime
import math
import re

import yaml

from mdtopdf_skill.models import (
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    LINE_HEIGHT_MAX,
    LINE_HEIGHT_MIN,
    SPACING_SCALE_MAX,
    SPACING_SCALE_MIN,
    FrontmatterData,
    _safe_repr,
    resolve_margin_css,
    resolve_page_size,
    resolve_theme_name,
    validate_accent_color,
    validate_finite_float,
    validate_lang,
)

__all__ = ["parse_frontmatter"]

_FRONTMATTER_RE = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)")
_YAML_KEY_LINE_RE = re.compile(r"^[ \t]*[^#\s][^:\r\n]*:(?:[ \t]|\r?$)", re.MULTILINE)

_METADATA_STR_FIELDS: tuple[str, ...] = (
    "title",
    "subtitle",
    "author",
    "date",
    "affiliation",
    "abstract",
)


def _pick_key(mapping: dict[object, object], *candidates: str) -> tuple[str, object] | None:
    """Return the first matching non-None key-value pair in candidate priority order."""
    for key in candidates:
        if key in mapping and mapping[key] is not None:
            return (key, mapping[key])
    return None


def _coerce_bounded_float(
    raw: object,
    key: str,
    min_val: float,
    max_val: float,
    warnings: list[str],
) -> float | None:
    """Coerce numeric scalar to a finite float in [min_val, max_val], rejecting bool."""
    if isinstance(raw, bool):
        warnings.append(f"frontmatter_invalid_field: {key} must be a number, got bool")
        return None
    if not isinstance(raw, (int, float, str)):
        warnings.append(
            f"frontmatter_invalid_field: {key} must be a number, got {type(raw).__name__}"
        )
        return None
    try:
        return validate_finite_float(raw, key, min_val, max_val)
    except ValueError as exc:
        warnings.append(f"frontmatter_invalid_field: {exc}")
        return None


def _coerce_bool(raw: object, key: str, warnings: list[str]) -> bool | None:
    """Coerce YAML bool or exact 'true'/'false' string into a bool."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    warnings.append(f"frontmatter_invalid_field: {key} must be a boolean, got {_safe_repr(raw)}")
    return None


def _coerce_metadata_str(raw: object, key: str, warnings: list[str]) -> str | None:
    """Coerce string, date/datetime, or finite number into a raw metadata string."""
    if isinstance(raw, bool):
        warnings.append(f"frontmatter_invalid_field: {key} must be a string, got bool")
        return None
    if isinstance(raw, datetime.date):
        return raw.isoformat()
    if isinstance(raw, int):
        try:
            return str(raw)
        except ValueError as exc:
            warnings.append(f"frontmatter_invalid_field: {key} cannot be converted ({exc})")
            return None
    if isinstance(raw, float):
        if not math.isfinite(raw):
            warnings.append(
                f"frontmatter_invalid_field: {key} cannot be non-finite ({_safe_repr(raw)})"
            )
            return None
        return str(raw)
    if isinstance(raw, str):
        cleaned = raw.strip()
        return cleaned or None
    warnings.append(
        f"frontmatter_invalid_field: {key} must be a string, got {type(raw).__name__}"
    )
    return None


def _coerce_header_footer(
    raw: object,
    key: str,
    warnings: list[str],
) -> str | bool | None:
    """Coerce header/footer value (bool or non-empty string, unescaped)."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        cleaned = raw.strip()
        return cleaned or None
    warnings.append(
        f"frontmatter_invalid_field: {key} must be a string or boolean, got {type(raw).__name__}"
    )
    return None


def parse_frontmatter(md_text: str) -> tuple[FrontmatterData, str]:
    """Strip optional UTF-8 BOM, safely parse leading YAML frontmatter, and return (data, body)."""
    cleaned = md_text.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(cleaned)
    if match is None:
        return FrontmatterData(), cleaned

    raw_yaml = match.group(1)
    if not _YAML_KEY_LINE_RE.search(raw_yaml):
        return FrontmatterData(), cleaned

    try:
        parsed: object = yaml.safe_load(raw_yaml)
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        return FrontmatterData(warnings=[f"frontmatter_invalid: {exc}"]), cleaned

    if not isinstance(parsed, dict):
        return (
            FrontmatterData(
                warnings=[
                    f"frontmatter_invalid: expected YAML mapping, got {type(parsed).__name__}"
                ]
            ),
            cleaned,
        )

    md_body = cleaned[match.end() :]
    warnings: list[str] = []
    fm = FrontmatterData(warnings=warnings)

    # theme
    picked = _pick_key(parsed, "theme")
    if picked is not None:
        _, val = picked
        if isinstance(val, str):
            try:
                fm.theme = resolve_theme_name(val)
            except ValueError as exc:
                warnings.append(f"frontmatter_invalid_field: {exc}")
        else:
            warnings.append(
                f"frontmatter_invalid_field: theme must be a string, got {type(val).__name__}"
            )

    # lang
    picked = _pick_key(parsed, "lang")
    if picked is not None:
        _, val = picked
        if isinstance(val, str):
            try:
                fm.lang = validate_lang(val)
            except ValueError as exc:
                warnings.append(f"frontmatter_invalid_field: {exc}")
        else:
            warnings.append(
                f"frontmatter_invalid_field: lang must be a string, got {type(val).__name__}"
            )

    # metadata string fields
    for field_name in _METADATA_STR_FIELDS:
        picked = _pick_key(parsed, field_name)
        if picked is not None:
            _, val = picked
            setattr(fm, field_name, _coerce_metadata_str(val, field_name, warnings))

    # bounded floats
    picked = _pick_key(parsed, "font_size", "font-size")
    if picked is not None:
        key, val = picked
        fm.font_size_pt = _coerce_bounded_float(val, key, FONT_SIZE_MIN, FONT_SIZE_MAX, warnings)

    picked = _pick_key(parsed, "line_height", "line-height")
    if picked is not None:
        key, val = picked
        fm.line_height = _coerce_bounded_float(val, key, LINE_HEIGHT_MIN, LINE_HEIGHT_MAX, warnings)

    picked = _pick_key(parsed, "spacing_scale", "spacing-scale")
    if picked is not None:
        key, val = picked
        fm.spacing_scale = _coerce_bounded_float(
            val, key, SPACING_SCALE_MIN, SPACING_SCALE_MAX, warnings
        )

    # margin
    picked = _pick_key(parsed, "margin")
    if picked is not None:
        _, val = picked
        if isinstance(val, int) and not isinstance(val, bool) and val == 0:
            fm.margin_css = "0"
        elif isinstance(val, str):
            try:
                fm.margin_css = resolve_margin_css(val)
            except ValueError as exc:
                warnings.append(f"frontmatter_invalid_field: {exc}")
        else:
            warnings.append(
                f"frontmatter_invalid_field: margin must be a string, got {type(val).__name__}"
            )

    # page_size
    picked = _pick_key(parsed, "page_size", "page-size")
    if picked is not None:
        _, val = picked
        if isinstance(val, str):
            try:
                fm.page_size = resolve_page_size(val)
            except ValueError as exc:
                warnings.append(f"frontmatter_invalid_field: {exc}")
        else:
            warnings.append(
                f"frontmatter_invalid_field: page_size must be a string, got {type(val).__name__}"
            )

    # accent_color
    picked = _pick_key(parsed, "accent_color", "accent", "accent-color")
    if picked is not None:
        _, val = picked
        if isinstance(val, str):
            try:
                fm.accent_color = validate_accent_color(val)
            except ValueError as exc:
                warnings.append(f"frontmatter_invalid_field: {exc}")
        else:
            warnings.append(
                "frontmatter_invalid_field: accent_color must be a string, "
                f"got {type(val).__name__}"
            )

    # booleans
    picked = _pick_key(parsed, "page_numbers", "page-numbers")
    if picked is not None:
        key, val = picked
        fm.page_numbers = _coerce_bool(val, key, warnings)

    picked = _pick_key(parsed, "toc")
    if picked is not None:
        key, val = picked
        fm.toc = _coerce_bool(val, key, warnings)

    # header & footer
    picked = _pick_key(parsed, "header")
    if picked is not None:
        _, val = picked
        fm.header = _coerce_header_footer(val, "header", warnings)

    picked = _pick_key(parsed, "footer")
    if picked is not None:
        _, val = picked
        fm.footer = _coerce_header_footer(val, "footer", warnings)

    # css (raw inline snippet or relative .css path; sanitized later with base_dir)
    picked = _pick_key(parsed, "css")
    if picked is not None:
        _, val = picked
        if isinstance(val, str):
            stripped_css = val.strip()
            fm.css = stripped_css or None
        else:
            warnings.append(
                f"frontmatter_invalid_field: css must be a string, got {type(val).__name__}"
            )

    return fm, md_body
