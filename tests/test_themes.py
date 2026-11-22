"""Unit tests for mdtopdf_skill.themes."""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path

import pytest

from mdtopdf_skill.models import (
    _DEFAULT_THEME_SPEC,
    DEFAULT_TITLE,
    THEME_NAMES,
    ConversionOptions,
    FrontmatterData,
    ThemeSpec,
    resolve_effective_options,
)
from mdtopdf_skill.themes import (
    THEMES,
    build_header_footer_templates,
    build_metadata_header_html,
    build_theme_css,
    get_theme,
)


class TestThemeRegistryAndLookup:
    """Verify THEMES registry, defaults, font stacks, and get_theme()."""

    def test_themes_match_models_theme_names_and_single_default(self) -> None:
        assert set(THEMES.keys()) == THEME_NAMES
        assert len(THEMES) == 2
        defaults = [name for name, spec in THEMES.items() if spec.is_default]
        assert defaults == ["classic"]
        assert THEMES["classic"] == _DEFAULT_THEME_SPEC

    @pytest.mark.parametrize(
        (
            "name",
            "font_size_pt",
            "line_height",
            "margin_css",
            "accent_color",
            "page_numbers",
        ),
        [
            ("classic", 9.5, 1.45, "12mm 14mm", "#0d47a1", True),
            ("paper", 10.0, 1.48, "18mm 20mm", "#1e293b", True),
        ],
    )
    def test_theme_spec_defaults_and_thai_font_stacks(
        self,
        name: str,
        font_size_pt: float,
        line_height: float,
        margin_css: str,
        accent_color: str,
        page_numbers: bool,
    ) -> None:
        spec = THEMES[name]
        assert isinstance(spec, ThemeSpec)
        assert spec.name == name
        assert spec.font_size_pt == font_size_pt
        assert spec.line_height == line_height
        assert spec.margin_css == margin_css
        assert spec.accent_color == accent_color
        assert spec.page_numbers is page_numbers

        if name == "paper":
            for font in ("Noto Serif Thai", "Sarabun", "Liberation Serif", "DejaVu Serif"):
                assert font in spec.font_family
        else:
            for font in ("Leelawadee UI", "Tahoma", "Noto Sans Thai", "Noto Sans"):
                assert font in spec.font_family

        assert "Consolas" in spec.mono_family
        assert "Noto Sans Mono" in spec.mono_family

    @pytest.mark.parametrize(
        ("raw_name", "expected"),
        [
            ("classic", "classic"),
            ("  CLASSIC ", "classic"),
            ("Paper", "paper"),
            ("  paper\t", "paper"),
        ],
    )
    def test_get_theme_normalizes_whitespace_and_case(
        self, raw_name: str, expected: str
    ) -> None:
        spec = get_theme(raw_name)
        assert spec is THEMES[expected]

    @pytest.mark.parametrize(
        "bad_name",
        [
            "",
            "   ",
            "office",
            "student",
            "tech-spec",
            "executive",
            "minimal",
            "unknown",
            123,
            None,
        ],
    )
    def test_get_theme_invalid_raises_value_error(self, bad_name: object) -> None:
        with pytest.raises(ValueError) as exc_info:
            get_theme(bad_name)  # type: ignore[arg-type]
        msg = str(exc_info.value)
        if isinstance(bad_name, str):
            for theme_name in sorted(THEME_NAMES):
                assert theme_name in msg

    def test_resolve_effective_options_integrates_with_themes(self) -> None:
        classic_opts = resolve_effective_options(theme="classic", themes=THEMES)
        assert classic_opts.theme == "classic"
        assert classic_opts.accent_color == "#0d47a1"
        assert classic_opts.margin_css == "12mm 14mm"
        assert classic_opts.page_numbers is True

        paper_opts = resolve_effective_options(
            frontmatter=FrontmatterData(theme="paper", title="Research"),
            themes=THEMES,
        )
        assert paper_opts.theme == "paper"
        assert paper_opts.font_size_pt == 10.0
        assert paper_opts.margin_css == "18mm 20mm"
        assert paper_opts.page_numbers is True


class TestThemesModuleBoundaries:
    """Verify themes.py import constraints and ssot boundary compliance."""

    def test_themes_py_imports_only_models_stdlib_and_pygments(self) -> None:
        themes_path = (
            Path(__file__).resolve().parent.parent / "src" / "mdtopdf_skill" / "themes.py"
        )
        source = themes_path.read_text(encoding="utf-8")
        assert "def build_css(" not in source

        tree = ast.parse(source, filename=str(themes_path))
        forbidden_submodules = {
            "frontmatter",
            "markdown_pipeline",
            "sanitizer",
            "cdp_browser",
            "autofit",
            "renderer",
            "cli",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] in {"html", "math", "re", "pygments"}
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("mdtopdf_skill"):
                    parts = node.module.split(".")
                    assert len(parts) == 2 and parts[1] == "models"
                    assert not (set(parts) & forbidden_submodules)
                else:
                    assert node.module.split(".")[0] in {"__future__", "pygments"}


class TestBuildThemeCss:
    """Verify print CSS generation, v0.2.0 classic parity, guardrails, and validation."""

    @pytest.mark.parametrize("theme_name", sorted(THEME_NAMES))
    def test_all_themes_include_mandatory_v1_guardrails_and_components(
        self, theme_name: str
    ) -> None:
        opts = resolve_effective_options(theme=theme_name, themes=THEMES)
        css = build_theme_css(opts, printable_height_mm=267.0)

        assert f"size: {opts.page_size};" in css
        assert f"margin: {opts.margin_css};" in css
        assert "--md-spacing-scale: 1;" in css
        assert "calc(" in css

        # Code wrapping without horizontal clipping
        assert "white-space: pre-wrap;" in css
        assert "overflow-wrap: anywhere;" in css
        assert "word-break: break-word;" in css
        assert "overflow-x: auto" not in css

        # Pygments CSS emitted before theme pre override
        pygments_pos = css.find("pre { line-height: 125%; }")
        theme_pre_pos = css.find("white-space: pre-wrap;")
        assert pygments_pos != -1
        assert theme_pre_pos > pygments_pos
        assert ".highlight" in css
        assert ".codehilite" in css

        # Thai line breaking
        assert "overflow-wrap: break-word;" in css
        assert ":lang(th)" in css
        assert "word-break: normal;" in css

        # Mermaid sizing & error styling
        assert ".mermaid-block, .mermaid-container" in css
        assert ".mermaid-block svg, .mermaid-container svg" in css
        assert "max-height: 259.00mm;" in css
        assert ".mermaid-error-box" in css
        assert ".mermaid-error-badge" in css

        # Math, callouts, tasks, TOC, footnotes, doc-header
        for selector in (
            ".math-error",
            ".math-inline",
            ".math-display",
            ".katex-display",
            ".callout",
            ".callout-title",
            ".callout-note",
            ".callout-tip",
            ".callout-important",
            ".callout-warning",
            ".callout-caution",
            "li.task-item",
            'li.task-item input[type="checkbox"]',
            ".toc",
            ".toc .toctitle",
            ".footnote",
            ".footnote-ref",
            ".footnote-backref",
            ".doc-header",
            f".doc-header-{theme_name}",
        ):
            assert selector in css

        for removed in (
            ".station-block",
            ".doc-header-status-badge",
            ".doc-header-meta-grid",
            ".doc-header-office",
            ".doc-header-student",
            ".doc-header-tech-spec",
            ".doc-header-executive",
            ".doc-header-minimal",
        ):
            assert removed not in css

    def test_classic_theme_v020_parity(self) -> None:
        opts = resolve_effective_options(theme="classic", themes=THEMES)
        css = build_theme_css(opts, printable_height_mm=273.0)

        assert "size: A4 portrait;" in css
        assert "margin: 12mm 14mm;" in css
        assert "font-size: 9.5pt;" in css
        assert "line-height: 1.45;" in css
        assert "font-size: 15.5pt;" in css
        assert "text-align: center;" in css
        assert "font-size: 11.5pt;" in css
        assert "border-bottom: 1.5px solid #1976d2;" in css
        assert "font-size: 9.8pt;" in css
        assert "font-size: 8.5pt;" in css
        assert "background-color: #f0f4f9;" in css
        assert "color: #0d47a1;" in css
        assert "border-left: 3.5px solid #1976d2;" in css
        assert "max-width: 90px;" in css
        assert "max-height: 70px;" in css
        assert "break-inside: avoid;" in css

    def test_paper_theme_booktabs_and_prose_justification(self) -> None:
        opts = resolve_effective_options(theme="paper", themes=THEMES)
        css = build_theme_css(opts, printable_height_mm=261.0)

        assert "text-align: justify;" in css
        assert "border-left: none;" in css
        assert "border-right: none;" in css
        assert "border-top: 1.5px solid #1e293b;" in css
        assert "border-bottom: 1.5px solid #1e293b;" in css

    def test_keep_blocks_together_toggle_affects_table_rows(self) -> None:
        opts_avoid = ConversionOptions(theme="classic", keep_blocks_together=True)
        css_avoid = build_theme_css(opts_avoid, printable_height_mm=250.0)
        assert "tr {\n    break-inside: avoid;" in css_avoid

        opts_auto = ConversionOptions(theme="classic", keep_blocks_together=False)
        css_auto = build_theme_css(opts_auto, printable_height_mm=250.0)
        assert "tr {\n    break-inside: auto;" in css_auto

    @pytest.mark.parametrize("theme_name", sorted(THEME_NAMES))
    def test_headings_h1_through_h4_prevent_page_break_inside(self, theme_name: str) -> None:
        opts = resolve_effective_options(theme=theme_name, themes=THEMES)
        css = build_theme_css(opts, printable_height_mm=267.0)
        for tag in ("h1", "h2", "h3", "h4"):
            match = re.search(rf"(?m)^{tag} \{{([^}}]+)\}}", css)
            assert match is not None, f"Missing {tag} rule in {theme_name} CSS"
            block = match.group(1)
            assert "break-inside: avoid;" in block
            assert "page-break-inside: avoid;" in block

    def test_custom_options_spacing_scale_and_image_limits(self) -> None:
        opts = ConversionOptions(
            theme="paper",
            page_size="a4 landscape",
            margin_css="compact",
            accent_color="crimson",
            font_size_pt=11.0,
            line_height=1.6,
            spacing_scale=0.75,
            img_max_width=140,
            img_max_height=105,
        )
        css = build_theme_css(opts, printable_height_mm=190.0)

        assert "size: A4 landscape;" in css
        assert "margin: 10mm 12mm;" in css
        assert "--md-spacing-scale: 0.75;" in css
        assert "color: crimson;" in css or "solid crimson;" in css
        assert "max-width: 140px;" in css
        assert "max-height: 105px;" in css
        assert "max-height: 182.00mm;" in css

    @pytest.mark.parametrize(
        ("height_mm", "expected_css"),
        [
            (267.0, "max-height: 259.00mm;"),
            (8.0, "max-height: 1.00mm;"),
            (4.0, "max-height: 1.00mm;"),
            (0.5, "max-height: 0.50mm;"),
        ],
    )
    def test_printable_height_clamping_for_diagrams(
        self, height_mm: float, expected_css: str
    ) -> None:
        css = build_theme_css(ConversionOptions(), printable_height_mm=height_mm)
        assert expected_css in css

    @pytest.mark.parametrize("bad_height", [0.0, -5.0, math.nan, math.inf, -math.inf, True, "abc"])
    def test_invalid_printable_height_raises_value_error(self, bad_height: object) -> None:
        with pytest.raises(ValueError):
            build_theme_css(ConversionOptions(), printable_height_mm=bad_height)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("field", "bad_val"),
        [
            ("accent_color", "red; } </style><script>alert(1)</script>"),
            ("margin_css", "10mm; color: red"),
            ("page_size", "A4; background: url(http://evil)"),
            ("font_size_pt", 3.0),
            ("line_height", 5.0),
            ("spacing_scale", 0.1),
            ("img_max_width", 0),
            ("img_max_height", -10),
        ],
    )
    def test_build_theme_css_rejects_injection_and_out_of_bounds_options(
        self, field: str, bad_val: object
    ) -> None:
        opts = ConversionOptions()
        setattr(opts, field, bad_val)
        with pytest.raises(ValueError):
            build_theme_css(opts, printable_height_mm=250.0)


class TestBuildMetadataHeaderHtml:
    """Verify metadata header rendering, H1 dedup title, and HTML/XSS escaping."""

    def test_returns_empty_when_no_title_or_metadata(self) -> None:
        opts = ConversionOptions(theme="classic")
        fm = FrontmatterData()
        assert build_metadata_header_html(opts, fm) == ("", None)

        fm_ws = FrontmatterData(title="   ", author="  \t ")
        assert build_metadata_header_html(opts, fm_ws) == ("", None)

    def test_fm_title_with_default_options_renders_fm_title(self) -> None:
        opts = ConversionOptions(theme="classic", title=DEFAULT_TITLE)
        fm = FrontmatterData(title="Quarterly Engineering Report")
        header_html, dedup_title = build_metadata_header_html(opts, fm)

        assert dedup_title == "Quarterly Engineering Report"
        assert '<header class="doc-header doc-header-classic">' in header_html
        assert '<h1 class="doc-header-title">Quarterly Engineering Report</h1>' in header_html

    def test_cli_non_default_title_overrides_fm_title(self) -> None:
        opts = ConversionOptions(theme="classic", title="CLI Override Title")
        fm = FrontmatterData(title="FM Title", author="Alice")
        header_html, dedup_title = build_metadata_header_html(opts, fm)

        assert dedup_title == "CLI Override Title"
        assert "CLI Override Title" in header_html
        assert "FM Title" not in header_html
        assert "Alice" in header_html

    def test_metadata_only_without_cli_or_fm_title_does_not_emit_fake_heading(self) -> None:
        opts = ConversionOptions(theme="classic", title=DEFAULT_TITLE)
        fm = FrontmatterData(author="Alice", date="2026-01-15")
        header_html, dedup_title = build_metadata_header_html(opts, fm)

        assert dedup_title is None
        assert "<h1" not in header_html
        assert DEFAULT_TITLE not in header_html
        assert "Alice" in header_html
        assert "2026-01-15" in header_html

    def test_metadata_only_with_non_default_cli_title_renders_heading(self) -> None:
        opts = ConversionOptions(theme="classic", title="Explicit CLI Title")
        fm = FrontmatterData(author="Alice")
        header_html, dedup_title = build_metadata_header_html(opts, fm)

        assert dedup_title == "Explicit CLI Title"
        assert '<h1 class="doc-header-title">Explicit CLI Title</h1>' in header_html
        assert "Alice" in header_html

    @pytest.mark.parametrize("theme_name", sorted(THEME_NAMES))
    def test_all_themes_render_all_structured_metadata_fields(self, theme_name: str) -> None:
        opts = ConversionOptions(theme=theme_name, title="Full Metadata Spec")
        fm = FrontmatterData(
            title="Full Metadata Spec",
            subtitle="Subheading",
            author="Dr. Somchai",
            date="2026-03-10",
            affiliation="Bangkok Tech",
            abstract="Summary of findings.",
        )
        header_html, dedup_title = build_metadata_header_html(opts, fm)

        assert dedup_title == "Full Metadata Spec"
        assert f'class="doc-header doc-header-{theme_name}"' in header_html
        for val in (
            "Full Metadata Spec",
            "Subheading",
            "Dr. Somchai",
            "2026-03-10",
            "Bangkok Tech",
            "Summary of findings.",
        ):
            assert val in header_html

    def test_html_and_xss_escaping_across_metadata(self) -> None:
        opts = ConversionOptions(theme="paper", title='<script>alert("t")</script>')
        fm = FrontmatterData(
            title="Ignored",
            subtitle='<b>"sub"</b>',
            author="A & B <dev@example.com>",
            date="2026-01-01<img>",
            affiliation="'aff'",
            abstract="<script>bad()</script>",
        )
        header_html, dedup_title = build_metadata_header_html(opts, fm)

        # Dedup title remains unescaped for markdown H1 matching
        assert dedup_title == '<script>alert("t")</script>'
        # Rendered HTML contains zero unescaped tags/scripts
        assert "<script>" not in header_html
        assert "<img>" not in header_html
        assert "<b>" not in header_html
        assert "&lt;script&gt;alert(&quot;t&quot;)&lt;/script&gt;" in header_html
        assert "A &amp; B &lt;dev@example.com&gt;" in header_html
        assert "&#x27;aff&#x27;" in header_html


class TestBuildHeaderFooterTemplates:
    """Verify CDP running header/footer templates, toggles, and margin cutoffs."""

    @pytest.mark.parametrize(
        "thin_margin",
        [
            "7.9mm 15mm",
            "5mm",
            "15mm 15mm 5mm",
            "5mm 15mm 15mm 15mm",
            "15mm 15mm 7.5mm 15mm",
            "20pt 15mm",
            "24px 15mm",
            "0.3in 15mm",
            "0.7cm 15mm",
            "0 15mm",
        ],
    )
    def test_margins_below_8mm_gracefully_disable_header_footer(
        self, thin_margin: str
    ) -> None:
        opts = ConversionOptions(
            theme="classic",
            margin_css=thin_margin,
            title="Report",
            page_numbers=True,
            header="Header",
            footer="Footer",
        )
        assert build_header_footer_templates(opts, FrontmatterData(title="Report")) == (
            False,
            "",
            "",
        )

    @pytest.mark.parametrize(
        "ok_margin",
        ["8mm 12mm", "23pt 12mm", "0.5in 12mm", "1cm 12mm", "15mm 5mm 10mm 5mm"],
    )
    def test_margins_at_or_above_8mm_allow_header_footer(self, ok_margin: str) -> None:
        opts = ConversionOptions(
            theme="paper",
            margin_css=ok_margin,
            title="Report",
            page_numbers=True,
        )
        display, header, footer = build_header_footer_templates(
            opts, FrontmatterData(title="Report")
        )
        assert display is True
        assert "Report" in header
        assert 'class="pageNumber"' in footer

    def test_classic_defaults_to_page_numbers_without_running_header(self) -> None:
        opts = resolve_effective_options(theme="classic", themes=THEMES)
        display, header, footer = build_header_footer_templates(opts, FrontmatterData())
        assert display is True
        assert header == "<span></span>"
        assert 'class="pageNumber"' in footer
        assert "Page <span" not in footer

        # With a title, classic still emits page numbers only (no running header)
        opts_with_title = resolve_effective_options(
            theme="classic",
            frontmatter=FrontmatterData(title="Classic Doc"),
            themes=THEMES,
        )
        display_t, header_t, footer_t = build_header_footer_templates(
            opts_with_title, FrontmatterData(title="Classic Doc")
        )
        assert display_t is True
        assert header_t == "<span></span>"
        assert "Classic Doc" not in header_t
        assert 'class="pageNumber"' in footer_t

        # Opting out of page numbers on classic disables header/footer completely
        opts_no_num = resolve_effective_options(
            theme="classic",
            frontmatter=FrontmatterData(title="Classic Doc"),
            page_numbers=False,
            themes=THEMES,
        )
        assert build_header_footer_templates(
            opts_no_num, FrontmatterData(title="Classic Doc")
        ) == (False, "", "")

    def test_paper_without_explicit_title_omits_default_placeholder_and_uses_span_stub(
        self,
    ) -> None:
        opts = resolve_effective_options(theme="paper", themes=THEMES)
        display, header, footer = build_header_footer_templates(opts, FrontmatterData())

        assert display is True
        assert header == "<span></span>"
        assert DEFAULT_TITLE not in header
        assert "font-size: 8px;" in footer
        assert 'class="pageNumber"' in footer
        assert 'class="totalPages"' in footer
        assert "Page <span" in footer

    def test_independent_toggles_header_footer_and_page_numbers(self) -> None:
        fm = FrontmatterData(title="Spec", author="Alice", date="2026-02-01")

        # page_numbers=False, header=False, footer=False -> disabled
        opts_all_off = ConversionOptions(
            theme="classic", title="Spec", page_numbers=False, header=False, footer=False
        )
        assert build_header_footer_templates(opts_all_off, fm) == (False, "", "")

        # header only -> inactive footer MUST be "<span></span>"
        opts_hdr_only = ConversionOptions(
            theme="classic", title="Spec", page_numbers=False, header="Confidential", footer=False
        )
        display, header, footer = build_header_footer_templates(opts_hdr_only, fm)
        assert display is True
        assert "Confidential" in header
        assert "font-size: 8px;" in header
        assert footer == "<span></span>"

        # footer=False does NOT suppress page_numbers=True
        opts_pg_only = ConversionOptions(
            theme="classic", title="Spec", page_numbers=True, header=False, footer=False
        )
        display, header, footer = build_header_footer_templates(opts_pg_only, fm)
        assert display is True
        assert header == "<span></span>"
        assert 'class="pageNumber"' in footer

        # header=True / footer=True explicit enable on classic
        opts_classic_hf = ConversionOptions(
            theme="classic",
            title="CV",
            page_numbers=True,
            header=True,
            footer=True,
        )
        display, header, footer = build_header_footer_templates(opts_classic_hf, fm)
        assert display is True
        assert "CV" in header
        assert "Alice · 2026-02-01" in footer
        assert 'class="pageNumber"' in footer
        assert "Page <span" not in footer

        # header/footer whitespace-only strings behave as empty
        opts_ws = ConversionOptions(
            theme="classic", title="Doc", page_numbers=False, header="   ", footer="\t"
        )
        assert build_header_footer_templates(opts_ws, fm) == (False, "", "")

        # footer text without page_numbers -> header is "<span></span>", footer has text only
        opts_ftr_only = ConversionOptions(
            theme="classic", title="Doc", page_numbers=False, header=False, footer="Internal Only"
        )
        display, header, footer = build_header_footer_templates(opts_ftr_only, fm)
        assert display is True
        assert header == "<span></span>"
        assert "Internal Only" in footer
        assert "pageNumber" not in footer

    def test_paper_footer_defaults(self) -> None:
        fm = FrontmatterData(title="Study", author="Dr. A", date="2026-04-01")

        paper_opts = resolve_effective_options(theme="paper", frontmatter=fm, themes=THEMES)
        display, header, footer = build_header_footer_templates(paper_opts, fm)
        assert display is True
        assert "Study" in header
        assert "Dr. A · 2026-04-01" in footer
        assert 'class="pageNumber"' in footer
        assert "Page <span" in footer

    def test_header_footer_html_escaping_prevents_xss(self) -> None:
        opts = ConversionOptions(
            theme="classic",
            header='<script>evil()</script> & "H"',
            footer="<img src=x onerror=alert(1)>",
            page_numbers=True,
        )
        display, header, footer = build_header_footer_templates(opts, FrontmatterData())
        assert display is True
        assert "<script>" not in header
        assert "<img" not in footer
        assert "&lt;script&gt;evil()&lt;/script&gt; &amp; &quot;H&quot;" in header
        assert "&lt;img src=x onerror=alert(1)&gt;" in footer
