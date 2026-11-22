"""Unit tests for mdtopdf_skill.frontmatter and mdtopdf_skill.models."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

import mdtopdf_skill
from mdtopdf_skill import (
    DEFAULT_LANG,
    DEFAULT_THEME,
    EXIT_BAD_ARGS,
    EXIT_EMPTY_PDF,
    EXIT_NO_BROWSER,
    EXIT_OK,
    EXIT_RENDER_ERROR,
    EXIT_STRICT_ISSUE,
    MARGIN_PRESETS,
    PAGE_SIZE_PRESETS,
    THEME_NAMES,
    AdjustmentRecord,
    AutoFitOutcome,
    ConversionOptions,
    ConversionResult,
    FrontmatterData,
    LayoutIssue,
    MathError,
    MermaidError,
    PageGeometry,
    PdfInspection,
    ThemeSpec,
    compute_page_geometry,
    parse_frontmatter,
    parse_margin_mm,
    parse_page_size_mm,
    resolve_effective_options,
    resolve_margin_css,
    resolve_page_size,
    validate_accent_color,
)
from mdtopdf_skill.models import (
    DEFAULT_TITLE,
    resolve_theme_name,
    validate_finite_float,
    validate_font_size,
    validate_lang,
    validate_line_height,
    validate_positive_int,
    validate_spacing_scale,
)


def _mock_themes() -> dict[str, ThemeSpec]:
    """Create mock ThemeSpec presets without importing themes.py."""
    return {
        "classic": ThemeSpec(
            name="classic",
            description="Clean sans-serif layout for general documents and reports",
            font_size_pt=9.5,
            line_height=1.45,
            margin_css="12mm 14mm",
            accent_color="#0d47a1",
            page_numbers=True,
            font_family="sans-serif",
            mono_family="monospace",
            is_default=True,
        ),
        "paper": ThemeSpec(
            name="paper",
            description="Academic and research papers",
            font_size_pt=10.0,
            line_height=1.48,
            margin_css="18mm 20mm",
            accent_color="#1e293b",
            page_numbers=True,
            font_family="serif",
            mono_family="monospace",
            is_default=False,
        ),
    }


class TestFrontmatterBoundariesAndBom:
    """Verify BOM stripping, strict --- delimiters, and non-frontmatter preservation."""

    def test_plain_markdown_without_frontmatter(self) -> None:
        md = "# Heading\n\nParagraph text.\n"
        fm, body = parse_frontmatter(md)
        assert fm == FrontmatterData()
        assert body == md

    def test_empty_and_bom_only(self) -> None:
        fm_empty, body_empty = parse_frontmatter("")
        assert fm_empty == FrontmatterData()
        assert body_empty == ""

        fm_bom, body_bom = parse_frontmatter("\ufeff")
        assert fm_bom == FrontmatterData()
        assert body_bom == ""

    def test_bom_stripped_with_and_without_frontmatter(self) -> None:
        fm_doc, body_doc = parse_frontmatter("\ufeff---\ntitle: Hello\n---\n# Body\n")
        assert fm_doc.title == "Hello"
        assert fm_doc.warnings == []
        assert body_doc == "# Body\n"

        fm_plain, body_plain = parse_frontmatter("\ufeff# Heading\nText\n")
        assert fm_plain == FrontmatterData()
        assert body_plain == "# Heading\nText\n"

    @pytest.mark.parametrize(
        "raw",
        [
            "---\n---",
            "---\n\n---",
            "---\n# just a comment\n---\n# Body\n",
            "---\n\n# Heading\n\nBody\n\n---\n",
            "---\nhttps://example.com\n---\n",
            "\n---\ntitle: Late\n---\nBody",
            " ---\ntitle: Indented\n---\nBody",
            "---\ntitle: DotClosed\n...\nBody",
            "---\ntitle: Unclosed\n# Body\n",
        ],
    )
    def test_non_frontmatter_delimiters_and_rules_untouched(self, raw: str) -> None:
        fm, body = parse_frontmatter(raw)
        assert fm.title is None
        assert fm.warnings == []
        assert body == raw

    def test_crlf_and_eof_without_trailing_newline(self) -> None:
        fm_crlf, body_crlf = parse_frontmatter("---\r\ntitle: CRLF\r\n---\r\n# Body\r\n")
        assert fm_crlf.title == "CRLF"
        assert fm_crlf.warnings == []
        assert body_crlf == "# Body\r\n"

        fm_eof, body_eof = parse_frontmatter("---\ntitle: EOF\n---")
        assert fm_eof.title == "EOF"
        assert fm_eof.warnings == []
        assert body_eof == ""

    def test_crlf_empty_value_key_followed_by_comment(self) -> None:
        fm, body = parse_frontmatter("---\r\nsubtitle:\r\n# note\r\n---\r\nBody")
        assert fm.subtitle is None
        assert fm.warnings == []
        assert body == "Body"

        raw_url = "---\r\nhttps://example.com\r\n# note\r\n---\r\nBody"
        fm_url, body_url = parse_frontmatter(raw_url)
        assert fm_url == FrontmatterData()
        assert body_url == raw_url


class TestFrontmatterMalformedYaml:
    """Verify non-crashing handling of invalid YAML and non-mapping YAML roots."""

    def test_yaml_syntax_error_emits_warning_and_preserves_body(self) -> None:
        raw = "\ufeff---\ntitle: [unclosed\n---\n# Body\n"
        fm, body = parse_frontmatter(raw)
        assert fm.title is None
        assert len(fm.warnings) == 1
        assert fm.warnings[0].startswith("frontmatter_invalid:")
        assert body == "---\ntitle: [unclosed\n---\n# Body\n"

    @pytest.mark.parametrize(
        "raw",
        [
            "---\n|-\n  key: val\n---\nBody\n",
            "---\n- item: one\n- item: two\n---\nBody\n",
        ],
    )
    def test_non_dict_yaml_root_emits_warning(self, raw: str) -> None:
        fm, body = parse_frontmatter(raw)
        assert len(fm.warnings) == 1
        assert fm.warnings[0].startswith("frontmatter_invalid: expected YAML mapping")
        assert body == raw


class TestFrontmatterKeysAndAliases:
    """Verify all 19 FrontmatterData fields, aliases, collision order, and unknown keys."""

    def test_all_19_fields_parsed_together(self) -> None:
        md = (
            "---\n"
            "theme: paper\n"
            "lang: en-US\n"
            "title: Architecture Spec\n"
            "subtitle: Core Contracts\n"
            "author: Jane Doe\n"
            "date: 2026-11-19\n"
            "affiliation: Thai Lab\n"
            "abstract: Short summary.\n"
            "font_size: 10.5\n"
            "line_height: 1.5\n"
            "spacing_scale: 0.9\n"
            "margin: compact\n"
            "page_size: A4 landscape\n"
            "accent_color: '#0369a1'\n"
            "page_numbers: false\n"
            "header: Internal RFC\n"
            "footer: Confidential\n"
            "toc: true\n"
            "css: 'body { color: #111; }'\n"
            "---\n"
            "# Body\n"
        )
        fm, body = parse_frontmatter(md)
        assert fm.warnings == []
        assert body == "# Body\n"
        assert fm.theme == "paper"
        assert fm.lang == "en-US"
        assert fm.title == "Architecture Spec"
        assert fm.subtitle == "Core Contracts"
        assert fm.author == "Jane Doe"
        assert fm.date == "2026-11-19"
        assert fm.affiliation == "Thai Lab"
        assert fm.abstract == "Short summary."
        assert math.isclose(fm.font_size_pt or 0.0, 10.5)
        assert math.isclose(fm.line_height or 0.0, 1.5)
        assert math.isclose(fm.spacing_scale or 0.0, 0.9)
        assert fm.margin_css == "10mm 12mm"
        assert fm.page_size == "A4 landscape"
        assert fm.accent_color == "#0369a1"
        assert fm.page_numbers is False
        assert fm.header == "Internal RFC"
        assert fm.footer == "Confidential"
        assert fm.toc is True
        assert fm.css == "body { color: #111; }"

    def test_removed_legacy_metadata_keys_ignored_without_warnings(self) -> None:
        md = (
            "---\n"
            "course: CS101\n"
            "instructor: Dr. Smith\n"
            "student: Alex\n"
            "status: Draft\n"
            "version: 1.0\n"
            "owner: Platform Team\n"
            "title: Clean Title\n"
            "---\n"
            "# Body\n"
        )
        fm, body = parse_frontmatter(md)
        assert fm.warnings == []
        assert fm.title == "Clean Title"
        assert body == "# Body\n"

    @pytest.mark.parametrize(
        ("yaml_line", "attr", "expected"),
        [
            ("font_size: 11", "font_size_pt", 11.0),
            ("font-size: 11", "font_size_pt", 11.0),
            ("line_height: 1.6", "line_height", 1.6),
            ("line-height: 1.6", "line_height", 1.6),
            ("spacing_scale: 1.25", "spacing_scale", 1.25),
            ("spacing-scale: 1.25", "spacing_scale", 1.25),
            ("page_size: letter", "page_size", "Letter portrait"),
            ("page-size: letter", "page_size", "Letter portrait"),
            ("accent: '#123456'", "accent_color", "#123456"),
            ("accent_color: '#123456'", "accent_color", "#123456"),
            ("accent-color: '#123456'", "accent_color", "#123456"),
            ("page_numbers: true", "page_numbers", True),
            ("page-numbers: true", "page_numbers", True),
        ],
    )
    def test_hyphen_underscore_and_alias_mapping(
        self,
        yaml_line: str,
        attr: str,
        expected: object,
    ) -> None:
        fm, _ = parse_frontmatter(f"---\n{yaml_line}\n---\n")
        assert fm.warnings == []
        assert getattr(fm, attr) == expected

    def test_canonical_key_takes_precedence_over_alias(self) -> None:
        md = (
            "---\n"
            "font-size: 9.0\n"
            "font_size: 12.0\n"
            "accent-color: '#111'\n"
            "accent: '#222'\n"
            "accent_color: '#333'\n"
            "---\n"
        )
        fm, _ = parse_frontmatter(md)
        assert fm.warnings == []
        assert fm.font_size_pt == 12.0
        assert fm.accent_color == "#333"

    def test_unknown_keys_non_str_keys_and_nulls_silently_ignored(self) -> None:
        md = (
            "---\n"
            "tags: [a, b]\n"
            "draft: true\n"
            "description: Ignored key\n"
            "123: numeric_key\n"
            "subtitle:\n"
            "author: null\n"
            "toc: ~\n"
            "title: Valid\n"
            "---\n"
        )
        fm, _ = parse_frontmatter(md)
        assert fm.warnings == []
        assert fm.title == "Valid"
        assert fm.subtitle is None
        assert fm.author is None
        assert fm.toc is None


class TestFrontmatterTypeCoercionAndValidation:
    """Verify PyYAML type coercion, boundary validation, and warning generation."""

    def test_date_and_scalar_metadata_coercion(self) -> None:
        md = "---\ndate: 2026-11-19\ntitle: 1.0\n---\n"
        fm, _ = parse_frontmatter(md)
        assert fm.warnings == []
        assert fm.date == "2026-11-19"
        assert fm.title == "1.0"

        md_dt = "---\ndate: 2026-11-19T10:20:30Z\nsubtitle: 2\n---\n"
        fm_dt, _ = parse_frontmatter(md_dt)
        assert fm_dt.warnings == []
        assert fm_dt.date == "2026-11-19T10:20:30+00:00"
        assert fm_dt.subtitle == "2"

        md_bad = "---\ndate: true\ntitle: .nan\nauthor: [a, b]\n---\n"
        fm_bad, _ = parse_frontmatter(md_bad)
        assert fm_bad.date is None
        assert fm_bad.title is None
        assert fm_bad.author is None
        assert len(fm_bad.warnings) == 3

    @pytest.mark.parametrize(
        "bad_val",
        ["true", "false", ".nan", ".inf", "-.inf", "+.inf", '"nan"', '"inf"', '"-inf"', "[1]"],
    )
    def test_numeric_fields_reject_bool_nan_inf_and_collections(self, bad_val: str) -> None:
        md = (
            f"---\nfont_size: {bad_val}\nline_height: {bad_val}\nspacing_scale: {bad_val}\n---\n"
        )
        fm, _ = parse_frontmatter(md)
        assert fm.font_size_pt is None
        assert fm.line_height is None
        assert fm.spacing_scale is None
        assert len(fm.warnings) == 3

    @pytest.mark.parametrize(
        ("field", "valid_vals", "invalid_vals"),
        [
            ("font_size", ["6.0", "72.0", "10", '"11.5"'], ["5.99", "72.01", "-1", '"12pt"']),
            ("line_height", ["1.0", "3.0", "1.5", '"2.0"'], ["0.99", "3.01", "0", '"normal"']),
            ("spacing_scale", ["0.5", "2.0", "1", '"0.85"'], ["0.49", "2.01", "-0.5", '"wide"']),
        ],
    )
    def test_numeric_exact_bounds(
        self,
        field: str,
        valid_vals: list[str],
        invalid_vals: list[str],
    ) -> None:
        attr = "font_size_pt" if field == "font_size" else field
        for val in valid_vals:
            fm, _ = parse_frontmatter(f"---\n{field}: {val}\n---\n")
            assert getattr(fm, attr) is not None
            assert fm.warnings == []
        for val in invalid_vals:
            fm, _ = parse_frontmatter(f"---\n{field}: {val}\n---\n")
            assert getattr(fm, attr) is None
            assert len(fm.warnings) == 1

    @pytest.mark.parametrize(
        ("raw_theme", "expected"),
        [
            ("classic", "classic"),
            ("Classic", "classic"),
            ("paper", "paper"),
            ("PAPER", "paper"),
        ],
    )
    def test_valid_themes_case_insensitive(self, raw_theme: str, expected: str) -> None:
        fm, _ = parse_frontmatter(f"---\ntheme: {raw_theme}\n---\n")
        assert fm.theme == expected
        assert fm.warnings == []

    @pytest.mark.parametrize(
        "bad_theme",
        ["office", "student", "tech-spec", "executive", "minimal", "neon", "123", "off"],
    )
    def test_invalid_theme_emits_warning(self, bad_theme: str) -> None:
        fm, _ = parse_frontmatter(f"---\ntheme: {bad_theme}\n---\n")
        assert fm.theme is None
        assert len(fm.warnings) == 1

    @pytest.mark.parametrize("valid_lang", ["th", "en", "en-US", "zh-Hans", "tha"])
    def test_valid_lang(self, valid_lang: str) -> None:
        fm, _ = parse_frontmatter(f"---\nlang: {valid_lang}\n---\n")
        assert fm.lang == valid_lang
        assert fm.warnings == []

    @pytest.mark.parametrize("bad_lang", ['"123"', "toolongtag", "en_US", "no"])
    def test_invalid_lang(self, bad_lang: str) -> None:
        fm, _ = parse_frontmatter(f"---\nlang: {bad_lang}\n---\n")
        assert fm.lang is None
        assert len(fm.warnings) == 1

    def test_margin_page_size_and_accent_validation(self) -> None:
        fm_ok, _ = parse_frontmatter(
            "---\nmargin: 0\npage_size: 210mm 297mm\naccent: crimson\n---\n"
        )
        assert fm_ok.margin_css == "0"
        assert fm_ok.page_size == "210mm 297mm"
        assert fm_ok.accent_color == "crimson"
        assert fm_ok.warnings == []

        fm_bad, _ = parse_frontmatter(
            "---\nmargin: 10%\npage_size: 210mm\naccent: '#12'\n---\n"
        )
        assert fm_bad.margin_css is None
        assert fm_bad.page_size is None
        assert fm_bad.accent_color is None
        assert len(fm_bad.warnings) == 3

        fm_non_str, _ = parse_frontmatter(
            "---\nmargin: 12\npage_size: 210\naccent: 123\n---\n"
        )
        assert fm_non_str.margin_css is None
        assert fm_non_str.page_size is None
        assert fm_non_str.accent_color is None
        assert len(fm_non_str.warnings) == 3

    def test_booleans_header_footer_css_and_multiline_metadata(self) -> None:
        md = (
            "---\n"
            "page_numbers: 'FALSE'\n"
            "toc: 'true'\n"
            "header: 'Draft & <Internal>'\n"
            "footer: false\n"
            "css: 'styles/custom.css'\n"
            "abstract: |\n"
            "  สวัสดี Line 1\n"
            "  Line 2\n"
            "---\n"
        )
        fm, _ = parse_frontmatter(md)
        assert fm.warnings == []
        assert fm.page_numbers is False
        assert fm.toc is True
        assert fm.header == "Draft & <Internal>"
        assert fm.footer is False
        assert fm.css == "styles/custom.css"
        assert fm.abstract == "สวัสดี Line 1\nLine 2"

        md_bad = (
            "---\n"
            "page_numbers: 1\n"
            "toc: 'yes'\n"
            "header: 123\n"
            "footer: ['a']\n"
            "css: 456\n"
            "---\n"
        )
        fm_bad, _ = parse_frontmatter(md_bad)
        assert fm_bad.page_numbers is None
        assert fm_bad.toc is None
        assert fm_bad.header is None
        assert fm_bad.footer is None
        assert fm_bad.css is None
        assert len(fm_bad.warnings) == 5

    def test_large_yaml_integers_do_not_crash(self) -> None:
        digits_350 = "9" * 350
        fm_meta, body_meta = parse_frontmatter(
            f"---\nsubtitle: {digits_350}\ntitle: {digits_350}\n---\nBody\n"
        )
        assert fm_meta.subtitle == digits_350
        assert fm_meta.title == digits_350
        assert fm_meta.warnings == []
        assert body_meta == "Body\n"

        fm_floats, body_floats = parse_frontmatter(
            "---\n"
            f"font_size: {digits_350}\n"
            f"line_height: {digits_350}\n"
            f"spacing_scale: {digits_350}\n"
            "---\n"
            "Body\n"
        )
        assert fm_floats.font_size_pt is None
        assert fm_floats.line_height is None
        assert fm_floats.spacing_scale is None
        assert len(fm_floats.warnings) == 3
        assert all(w.startswith("frontmatter_invalid_field:") for w in fm_floats.warnings)
        assert body_floats == "Body\n"

        digits_4400 = "9" * 4400
        raw_4400 = f"---\ntitle: {digits_4400}\n---\nBody\n"
        fm_4400, body_4400 = parse_frontmatter(raw_4400)
        assert fm_4400.title is None
        assert len(fm_4400.warnings) == 1
        assert fm_4400.warnings[0].startswith("frontmatter_invalid:")
        assert body_4400 == raw_4400

        hex_4400 = "0x" + ("f" * 3700)
        fm_hex, body_hex = parse_frontmatter(
            f"---\ntitle: {hex_4400}\nfont_size: {hex_4400}\n---\nBody\n"
        )
        assert fm_hex.title is None
        assert fm_hex.font_size_pt is None
        assert len(fm_hex.warnings) == 2
        assert all(w.startswith("frontmatter_invalid_field:") for w in fm_hex.warnings)
        assert body_hex == "Body\n"

    @pytest.mark.parametrize("field_name", ["toc", "page_numbers", "page-numbers"])
    def test_boolean_fields_reject_large_hex_octal_and_nested_containers(
        self, field_name: str
    ) -> None:
        hex_payload = "0x" + ("f" * 4400)
        oct_payload = "0" + ("7" * 5000)
        nested_payload = f"[{hex_payload}]"
        for payload in (hex_payload, oct_payload, nested_payload):
            fm, body = parse_frontmatter(f"---\n{field_name}: {payload}\n---\nBody\n")
            assert fm.toc is None
            assert fm.page_numbers is None
            assert len(fm.warnings) == 1
            assert fm.warnings[0].startswith("frontmatter_invalid_field:")
            assert body == "Body\n"

    def test_non_finite_margin_rejected_in_frontmatter(self) -> None:
        huge_margin = ("9" * 400) + "mm"
        fm, body = parse_frontmatter(f"---\nmargin: {huge_margin}\n---\nBody\n")
        assert fm.margin_css is None
        assert len(fm.warnings) == 1
        assert fm.warnings[0].startswith("frontmatter_invalid_field:")
        assert body == "Body\n"

    def test_non_ascii_unicode_digits_rejected_in_frontmatter(self) -> None:
        fm, body = parse_frontmatter(
            "---\nmargin: '١٠mm'\npage_size: '٢١٠mm ٢٩٧mm'\n---\nBody\n"
        )
        assert fm.margin_css is None
        assert fm.page_size is None
        assert len(fm.warnings) == 2
        assert all(w.startswith("frontmatter_invalid_field:") for w in fm.warnings)
        assert body == "Body\n"


class TestExitCodesAndPresets:
    """Verify pinned exit codes, presets, theme names, and package exports."""

    def test_exit_codes_and_package_version(self) -> None:
        assert mdtopdf_skill.__version__ == "2.0.0"
        assert EXIT_OK == 0
        assert EXIT_BAD_ARGS == 2
        assert EXIT_NO_BROWSER == 3
        assert EXIT_RENDER_ERROR == 4
        assert EXIT_EMPTY_PDF == 5
        assert EXIT_STRICT_ISSUE == 6
        assert DEFAULT_THEME == "classic"
        assert DEFAULT_LANG == "th"
        expected_themes = frozenset({"classic", "paper"})
        assert expected_themes == THEME_NAMES

    def test_margin_and_page_size_presets(self) -> None:
        assert MARGIN_PRESETS == {
            "compact": "10mm 12mm",
            "standard": "12mm 14mm",
            "comfortable": "15mm 18mm",
            "wide": "18mm 20mm",
        }
        assert PAGE_SIZE_PRESETS["a4"] == (210.0, 297.0)
        assert PAGE_SIZE_PRESETS["a4 portrait"] == (210.0, 297.0)
        assert PAGE_SIZE_PRESETS["a4 landscape"] == (297.0, 210.0)
        assert PAGE_SIZE_PRESETS["letter portrait"] == (215.9, 279.4)
        assert PAGE_SIZE_PRESETS["letter landscape"] == (279.4, 215.9)
        assert PAGE_SIZE_PRESETS["legal portrait"] == (215.9, 355.6)
        assert PAGE_SIZE_PRESETS["legal landscape"] == (355.6, 215.9)


class TestModelsDataclassesAndToDict:
    """Verify dataclass immutability, defaults, and JSON to_dict() methods."""

    def test_to_dict_outputs(self) -> None:
        table_issue = LayoutIssue(
            kind="table_overflow",
            detail="Wide table",
            suggestion="Use landscape",
            table_index=0,
            overflow_px=42.5,
        )
        assert table_issue.to_dict() == {
            "kind": "table_overflow",
            "detail": "Wide table",
            "suggestion": "Use landscape",
            "table_index": 0,
            "overflow_px": 42.5,
        }

        orphan_issue = LayoutIssue(
            kind="trailing_orphan_page",
            detail="2 lines on last page",
            suggestion="Compact spacing",
        )
        assert orphan_issue.to_dict() == {
            "kind": "trailing_orphan_page",
            "detail": "2 lines on last page",
            "suggestion": "Compact spacing",
        }

        m_err = MermaidError(index=1, message="Syntax error", snippet="graph TD")
        assert m_err.to_dict() == {"index": 1, "message": "Syntax error", "snippet": "graph TD"}

        k_err = MathError(index=0, display_mode=True, message="Undefined", tex=r"\foo")
        assert k_err.to_dict() == {
            "index": 0,
            "display_mode": True,
            "message": "Undefined",
            "tex": r"\foo",
        }

        adj = AdjustmentRecord(step="table_wrap_compact", detail="Wrapped table 0")
        assert adj.to_dict() == {"step": "table_wrap_compact", "detail": "Wrapped table 0"}

    def test_frozen_immutability_and_mutable_defaults(self) -> None:
        issue = LayoutIssue(kind="wide_element", detail="d", suggestion="s")
        with pytest.raises(FrozenInstanceError):
            issue.detail = "x"  # type: ignore[misc]

        geom = compute_page_geometry("A4", "15mm 16mm")
        with pytest.raises(FrozenInstanceError):
            geom.page_size = "Letter"  # type: ignore[misc]

        insp = PdfInspection(
            page_count=2,
            last_page_fill_ratio=0.5,
            body_line_count=10,
            body_images=0,
            body_vector_diagrams=0,
            is_trailing_orphan=False,
        )
        with pytest.raises(FrozenInstanceError):
            insp.page_count = 3  # type: ignore[misc]

        fm1, fm2 = FrontmatterData(), FrontmatterData()
        fm1.warnings.append("w1")
        assert fm2.warnings == []

        res1 = ConversionResult(
            output_pdf_path="a.pdf", theme="classic", page_count=1, embedded_images=0
        )
        res2 = ConversionResult(
            output_pdf_path="b.pdf", theme="classic", page_count=1, embedded_images=0
        )
        res1.suppressed_images.append("missing.png")
        assert res2.suppressed_images == []

        outcome = AutoFitOutcome(pdf_bytes=b"%PDF", page_count=1, last_page_fill_ratio=0.5)
        assert outcome.layout_issues == []

        opts = ConversionOptions()
        assert opts.theme == "classic"
        assert opts.margin_css == "12mm 14mm"
        assert opts.accent_color == "#0d47a1"
        assert opts.lang == "th"
        assert opts.page_numbers is True
        assert opts.preview_dpi == 144


class TestModelsValidatorsAndGeometry:
    """Verify shared validators, margin/page-size parsing, and 96 DPI geometry."""

    def test_scalar_validators_and_rejections(self) -> None:
        assert validate_font_size("10.5") == 10.5
        assert validate_line_height(1.5) == 1.5
        assert validate_spacing_scale("0.85") == 0.85
        assert validate_positive_int("144", "preview_dpi") == 144
        assert validate_lang(" en-US ") == "en-US"
        assert validate_accent_color(" #0f3d7a ") == "#0f3d7a"
        assert resolve_theme_name(" PAPER ") == "paper"
        assert resolve_theme_name("Custom", themes={"custom": _mock_themes()["classic"]}) == (
            "custom"
        )

        invalid_calls: list[Callable[[], object]] = [
            lambda: validate_finite_float(True, "x", 0.0, 10.0),
            lambda: validate_finite_float("abc", "x", 0.0, 10.0),
            lambda: validate_finite_float(float("nan"), "x", 0.0, 10.0),
            lambda: validate_finite_float(11.0, "x", 0.0, 10.0),
            lambda: validate_positive_int(True, "w"),
            lambda: validate_positive_int("10.5", "w"),
            lambda: validate_positive_int(0, "w"),
            lambda: validate_positive_int(1.5, "w"),  # type: ignore[arg-type]
            lambda: validate_lang("en_US"),
            lambda: validate_lang(123),  # type: ignore[arg-type]
            lambda: validate_accent_color("red; color: blue"),
            lambda: validate_accent_color(123),  # type: ignore[arg-type]
            lambda: resolve_theme_name("unknown"),
            lambda: resolve_theme_name(123),  # type: ignore[arg-type]
            lambda: resolve_margin_css(123),  # type: ignore[arg-type]
            lambda: resolve_page_size(123),  # type: ignore[arg-type]
        ]
        for fn_call in invalid_calls:
            with pytest.raises(ValueError):
                fn_call()

    @pytest.mark.parametrize(
        ("raw", "expected_css", "expected_mm"),
        [
            ("COMPACT", "10mm 12mm", (10.0, 12.0, 10.0, 12.0)),
            ("10mm", "10mm", (10.0, 10.0, 10.0, 10.0)),
            ("1cm  25.4mm   0", "1cm 25.4mm 0", (10.0, 25.4, 0.0, 25.4)),
            ("1in 72pt 6pc 96px", "1in 72pt 6pc 96px", (25.4, 25.4, 25.4, 25.4)),
        ],
    )
    def test_margin_resolution_and_mm_conversion(
        self,
        raw: str,
        expected_css: str,
        expected_mm: tuple[float, float, float, float],
    ) -> None:
        assert resolve_margin_css(raw) == expected_css
        parsed = parse_margin_mm(raw)
        for got, exp in zip(parsed, expected_mm, strict=True):
            assert math.isclose(got, exp, rel_tol=1e-9)

    @pytest.mark.parametrize(
        "bad_margin",
        ["", "   ", "10%", "10mm 5%", "12", "1mm 2mm 3mm 4mm 5mm", "10mm; color: red"],
    )
    def test_invalid_margins_rejected(self, bad_margin: str) -> None:
        with pytest.raises(ValueError):
            resolve_margin_css(bad_margin)

    def test_page_size_and_geometry_computation(self) -> None:
        assert resolve_page_size("a4") == "A4 portrait"
        assert resolve_page_size("  letter   LANDSCAPE ") == "Letter landscape"
        assert resolve_page_size("8.5in 11IN") == "8.5in 11in"
        w_mm, h_mm = parse_page_size_mm("8.5in 11in")
        assert math.isclose(w_mm, 215.9, rel_tol=1e-9)
        assert math.isclose(h_mm, 279.4, rel_tol=1e-9)

        for bad_size in ["", "210mm", "0mm 100mm", "50% 50%", "A4; margin: 0"]:
            with pytest.raises(ValueError):
                resolve_page_size(bad_size)

        geom = compute_page_geometry("A4", "15mm 16mm")
        assert isinstance(geom, PageGeometry)
        assert geom.page_size == "A4 portrait"
        assert math.isclose(geom.printable_width_mm, 178.0)
        assert math.isclose(geom.printable_height_mm, 267.0)
        assert math.isclose(geom.printable_width_px, 178.0 * 96.0 / 25.4)
        assert math.isclose(geom.printable_height_px, 267.0 * 96.0 / 25.4)
        assert math.isclose(geom.margin_top_pt, 15.0 * 72.0 / 25.4)
        assert math.isclose(geom.margin_bottom_pt, 15.0 * 72.0 / 25.4)

        with pytest.raises(ValueError, match="non-positive printable area"):
            compute_page_geometry("A4 portrait", "120mm")

    def test_validate_finite_float_large_integer_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="must be a valid number"):
            validate_finite_float(10**400, "x", 0.0, 100.0)
        with pytest.raises(ValueError, match="must be a valid number"):
            validate_finite_float(10**4400, "x", 0.0, 100.0)

    def test_validate_positive_int_negative_huge_integer_and_nested_container(self) -> None:
        with pytest.raises(ValueError, match="must be >= 1"):
            validate_positive_int(-(10**5000), "x")
        with pytest.raises(ValueError, match="must be a positive integer"):
            validate_positive_int([10**5000], "x")  # type: ignore[arg-type]

    def test_non_finite_css_lengths_rejected(self) -> None:
        with pytest.raises(ValueError, match="Non-finite length token"):
            resolve_margin_css(("9" * 400) + "mm")
        with pytest.raises(ValueError, match="Non-finite length token"):
            resolve_margin_css("1" + ("0" * 308) + "in")

        overflow_size = "1" + ("0" * 308) + "in 100mm"
        with pytest.raises(ValueError, match="Non-finite length token"):
            resolve_page_size(overflow_size)
        with pytest.raises(ValueError, match="Non-finite length token"):
            parse_page_size_mm(overflow_size)
        with pytest.raises(ValueError, match="Non-finite length token"):
            compute_page_geometry(overflow_size, "10mm")

        # Finite in mm (1e308 mm) but overflows to inf when converting to px/pt in PageGeometry
        mm_borderline_size = "1" + ("0" * 308) + "mm 100mm"
        with pytest.raises(ValueError, match="non-finite"):
            compute_page_geometry(mm_borderline_size, "10mm")

    def test_non_ascii_unicode_digits_rejected_in_validators(self) -> None:
        with pytest.raises(ValueError):
            resolve_margin_css("١٠mm")
        with pytest.raises(ValueError):
            resolve_page_size("٢١٠mm ٢٩٧mm")
        with pytest.raises(ValueError):
            validate_positive_int("١٠", "preview_dpi")
        with pytest.raises(ValueError):
            validate_positive_int("9" * 4400, "preview_dpi")


class TestResolveEffectiveOptionsPrecedence:
    """Verify 4-layer option precedence: CLI > Frontmatter > Theme > Global."""

    def test_global_defaults_and_theme_layer(self) -> None:
        themes = _mock_themes()
        opts_default = resolve_effective_options(themes=themes)
        assert opts_default.theme == "classic"
        assert opts_default.lang == "th"
        assert opts_default.font_size_pt == 9.5
        assert opts_default.margin_css == "12mm 14mm"
        assert opts_default.accent_color == "#0d47a1"
        assert opts_default.page_numbers is True
        assert opts_default.title == DEFAULT_TITLE

        opts_paper = resolve_effective_options(themes=themes, theme="paper")
        assert opts_paper.theme == "paper"
        assert opts_paper.margin_css == "18mm 20mm"
        assert opts_paper.accent_color == "#1e293b"
        assert opts_paper.page_numbers is True

    def test_frontmatter_overrides_theme_and_cli_overrides_frontmatter(self) -> None:
        themes = _mock_themes()
        fm = FrontmatterData(
            theme="paper",
            lang="en",
            title="FM Title",
            font_size_pt=11.0,
            line_height=1.6,
            spacing_scale=0.9,
            margin_css="10mm 12mm",
            page_size="Letter portrait",
            accent_color="#222222",
            page_numbers=True,
            header="FM Header",
            footer="FM Footer",
            toc=True,
            css="p { margin: 0; }",
        )

        # Layer 2 (Frontmatter) > Layer 3 (paper theme) > Layer 4 (Global)
        opts_fm = resolve_effective_options(frontmatter=fm, themes=themes)
        assert opts_fm.theme == "paper"
        assert opts_fm.lang == "en"
        assert opts_fm.title == "FM Title"
        assert opts_fm.font_size_pt == 11.0
        assert opts_fm.line_height == 1.6
        assert opts_fm.spacing_scale == 0.9
        assert opts_fm.margin_css == "10mm 12mm"
        assert opts_fm.page_size == "Letter portrait"
        assert opts_fm.accent_color == "#222222"
        assert opts_fm.page_numbers is True
        assert opts_fm.header == "FM Header"
        assert opts_fm.footer == "FM Footer"
        assert opts_fm.toc is True
        assert opts_fm.frontmatter_css == "p { margin: 0; }"
        assert opts_fm.cli_css == ""

        # Partial CLI override: --theme classic overrides fm.theme, while fm.font_size_pt remains
        opts_partial = resolve_effective_options(frontmatter=fm, themes=themes, theme="classic")
        assert opts_partial.theme == "classic"
        assert opts_partial.font_size_pt == 11.0

        # Full CLI overrides: explicit False overrides frontmatter True
        opts_cli = resolve_effective_options(
            frontmatter=fm,
            themes=themes,
            theme="classic",
            lang="th",
            font_size_pt=8.5,
            line_height=1.3,
            spacing_scale=0.8,
            margin_css="wide",
            page_size="A4 landscape",
            accent_color="#000000",
            title="CLI Title",
            page_numbers=False,
            header=False,
            footer="CLI Footer",
            toc=False,
            cli_css="h1 { color: red; }",
            img_max_width=120,
            img_max_height=100,
            suppress_missing_images=False,
            keep_blocks_together=False,
            auto_fit=False,
            strict=True,
            allow_outside_base_dir=True,
            preview_dir="previews",
            preview_dpi=72,
        )
        assert opts_cli.theme == "classic"
        assert opts_cli.lang == "th"
        assert opts_cli.font_size_pt == 8.5
        assert opts_cli.line_height == 1.3
        assert opts_cli.spacing_scale == 0.8
        assert opts_cli.margin_css == "18mm 20mm"
        assert opts_cli.page_size == "A4 landscape"
        assert opts_cli.accent_color == "#000000"
        assert opts_cli.title == "CLI Title"
        assert opts_cli.page_numbers is False
        assert opts_cli.header is False
        assert opts_cli.footer == "CLI Footer"
        assert opts_cli.toc is False
        assert opts_cli.frontmatter_css == "p { margin: 0; }"
        assert opts_cli.cli_css == "h1 { color: red; }"
        assert opts_cli.img_max_width == 120
        assert opts_cli.img_max_height == 100
        assert opts_cli.suppress_missing_images is False
        assert opts_cli.keep_blocks_together is False
        assert opts_cli.auto_fit is False
        assert opts_cli.strict is True
        assert opts_cli.allow_outside_base_dir is True
        assert opts_cli.preview_dir == "previews"
        assert opts_cli.preview_dpi == 72

    def test_title_fallback_and_unvalidated_frontmatter_theme_fallback(self) -> None:
        paper_spec = _mock_themes()["paper"]
        bad_fm = FrontmatterData(theme="nonexistent", title="   ")
        opts = resolve_effective_options(
            frontmatter=bad_fm,
            theme_spec=paper_spec,
            title="",
            default_title="Fallback H1",
        )
        assert opts.theme == "paper"
        assert opts.title == "Fallback H1"

        opts_default = resolve_effective_options(
            frontmatter=FrontmatterData(theme="nonexistent"),
            default_title="   ",
        )
        assert opts_default.theme == "classic"
        assert opts_default.title == DEFAULT_TITLE


def test_deeply_nested_yaml_does_not_crash() -> None:
    text = "---\ntitle: " + "[" * 5000 + "]" * 5000 + "\n---\nbody\n"
    fm, body = parse_frontmatter(text)
    assert any(w.startswith("frontmatter_invalid") for w in fm.warnings)
    assert "body" in body
