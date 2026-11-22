"""Tests for v2.0.0 CLI argument parsing, validation, and 4-layer option resolution."""

from __future__ import annotations

import math

import pytest

from mdtopdf_skill import __version__
from mdtopdf_skill.cli import (
    CliArgumentError,
    build_conversion_options,
    main,
    parse_args,
    resolve_default_title,
    resolve_margin_css,
)
from mdtopdf_skill.models import MARGIN_PRESETS, ConversionOptions


class TestCliArgParsingDefaults:
    """Verify unset overridable options stay None so frontmatter/theme defaults apply."""

    def test_minimal_positional_input(self) -> None:
        args = parse_args(["document.md"])
        assert args.input == "document.md"
        assert args.output is None
        assert args.base_dir is None
        assert args.theme is None
        assert not hasattr(args, "list_themes")
        assert args.font_size is None
        assert args.line_height is None
        assert args.spacing_scale is None
        assert args.margin is None
        assert args.page_size is None
        assert args.img_max_width is None
        assert args.img_max_height is None
        assert args.accent is None
        assert args.title is None
        assert args.css is None
        assert args.toc is None
        assert args.page_numbers is None
        assert args.keep_missing_images is False
        assert args.no_keep_together is False
        assert args.no_auto_fit is False
        assert args.strict is False
        assert args.preview_dir is None
        assert args.allow_outside_base_dir is False
        assert args.json is False
        assert args.quiet is False

    def test_explicit_output_base_dir_and_removed_list_themes(self) -> None:
        args = parse_args([
            "doc.md",
            "-o",
            "out.pdf",
            "--base-dir",
            "/custom/dir",
        ])
        assert args.output == "out.pdf"
        assert args.base_dir == "/custom/dir"

        with pytest.raises(CliArgumentError):
            parse_args(["--list-themes"])

        with pytest.raises(CliArgumentError):
            parse_args([])


class TestFontSizeValidation:
    """Verify --font-size validation (finite float in [6.0, 72.0])."""

    @pytest.mark.parametrize(
        ("raw_value", "expected"),
        [
            ("6.0", 6.0),
            ("6", 6.0),
            ("72.0", 72.0),
            ("72", 72.0),
            ("9.5", 9.5),
            ("12", 12.0),
            ("10.25", 10.25),
        ],
    )
    def test_valid_font_size(self, raw_value: str, expected: float) -> None:
        args = parse_args(["doc.md", "--font-size", raw_value])
        assert math.isclose(args.font_size, expected)

    @pytest.mark.parametrize(
        "raw_value",
        ["5.99", "5.9", "0", "-1", "-10", "72.01", "72.1", "100", "999"],
    )
    def test_out_of_range_font_size(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"font_size must be between 6\.0 and 72\.0"):
            parse_args(["doc.md", "--font-size", raw_value])

    @pytest.mark.parametrize(
        "raw_value",
        ["nan", "NaN", "NAN", "inf", "-inf", "Infinity", "-Infinity", "+inf"],
    )
    def test_non_finite_font_size(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"font_size must be finite"):
            parse_args(["doc.md", "--font-size", raw_value])

    @pytest.mark.parametrize("raw_value", ["abc", "12pt", "small", "", " "])
    def test_invalid_string_font_size(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"font_size must be a valid number"):
            parse_args(["doc.md", "--font-size", raw_value])


class TestLineHeightAndSpacingScaleValidation:
    """Verify --line-height ([1.0, 3.0]) and --spacing-scale ([0.5, 2.0]) validation."""

    @pytest.mark.parametrize(
        ("raw_value", "expected"),
        [("1.0", 1.0), ("1", 1.0), ("3.0", 3.0), ("1.45", 1.45), ("2.0", 2.0)],
    )
    def test_valid_line_height(self, raw_value: str, expected: float) -> None:
        args = parse_args(["doc.md", "--line-height", raw_value])
        assert math.isclose(args.line_height, expected)

    @pytest.mark.parametrize("raw_value", ["0.99", "0", "-1.5", "3.01", "10", "nan", "inf"])
    def test_invalid_line_height(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"line_height"):
            parse_args(["doc.md", "--line-height", raw_value])

    @pytest.mark.parametrize(
        ("raw_value", "expected"),
        [("0.5", 0.5), ("1.0", 1.0), ("1.35", 1.35), ("2.0", 2.0)],
    )
    def test_valid_spacing_scale(self, raw_value: str, expected: float) -> None:
        args = parse_args(["doc.md", "--spacing-scale", raw_value])
        assert math.isclose(args.spacing_scale, expected)

    @pytest.mark.parametrize("raw_value", ["0.49", "0", "-1", "2.01", "nan", "inf", "wide"])
    def test_invalid_spacing_scale(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"spacing_scale"):
            parse_args(["doc.md", "--spacing-scale", raw_value])


class TestThemeAndPageSizeValidation:
    """Verify --theme (case-insensitive) and --page-size presets/dimensions."""

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
        args = parse_args(["doc.md", "--theme", raw_theme])
        assert args.theme == expected

    @pytest.mark.parametrize(
        "bad_theme",
        ["", "office", "student", "tech-spec", "executive", "minimal", "unknown-theme", "darkmode"],
    )
    def test_invalid_theme_raises(self, bad_theme: str) -> None:
        with pytest.raises(CliArgumentError, match=r"Unknown theme"):
            parse_args(["doc.md", "--theme", bad_theme])

    @pytest.mark.parametrize(
        ("raw_size", "expected"),
        [
            ("A4", "A4 portrait"),
            ("a4 landscape", "A4 landscape"),
            ("letter", "Letter portrait"),
            ("Letter Landscape", "Letter landscape"),
            ("legal", "Legal portrait"),
            ("180mm 250mm", "180mm 250mm"),
        ],
    )
    def test_valid_page_size(self, raw_size: str, expected: str) -> None:
        args = parse_args(["doc.md", "--page-size", raw_size])
        assert args.page_size == expected

    @pytest.mark.parametrize("bad_size", ["", "A5", "0mm 200mm", "10% 100%", "nanmm 100mm"])
    def test_invalid_page_size(self, bad_size: str) -> None:
        with pytest.raises(CliArgumentError, match=r"page_size"):
            parse_args(["doc.md", "--page-size", bad_size])


class TestImageDimensionValidation:
    """Verify --img-max-width and --img-max-height positive integer validation."""

    @pytest.mark.parametrize("flag", ["--img-max-width", "--img-max-height"])
    @pytest.mark.parametrize(("raw_value", "expected"), [("1", 1), ("90", 90), ("500", 500)])
    def test_valid_positive_int_dimensions(
        self, flag: str, raw_value: str, expected: int
    ) -> None:
        args = parse_args(["doc.md", flag, raw_value])
        attr = flag.lstrip("-").replace("-", "_")
        assert getattr(args, attr) == expected

    @pytest.mark.parametrize("flag", ["--img-max-width", "--img-max-height"])
    @pytest.mark.parametrize(
        "raw_value",
        ["0", "-1", "-90", "12.5", "nan", "inf", "-inf", "abc", "100px", ""],
    )
    def test_invalid_dimensions(self, flag: str, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"img_max_(?:width|height)"):
            parse_args(["doc.md", flag, raw_value])


class TestMarginResolution:
    """Verify margin preset resolution and raw CSS margin validation."""

    @pytest.mark.parametrize("preset_key", sorted(MARGIN_PRESETS.keys()))
    def test_valid_presets_case_insensitive(self, preset_key: str) -> None:
        expected = MARGIN_PRESETS[preset_key]
        assert resolve_margin_css(preset_key) == expected
        assert resolve_margin_css(preset_key.upper()) == expected
        args = parse_args(["doc.md", "--margin", preset_key.upper()])
        assert args.margin == expected

    @pytest.mark.parametrize(
        ("raw_css", "expected"),
        [
            ("12mm 14mm", "12mm 14mm"),
            ("10mm", "10mm"),
            ("10mm 15mm 20mm", "10mm 15mm 20mm"),
            ("10mm   12mm   14mm   16mm", "10mm 12mm 14mm 16mm"),
            ("0", "0"),
            ("0 12pt 1.5cm 2in", "0 12pt 1.5cm 2in"),
        ],
    )
    def test_valid_css_margin_strings(self, raw_css: str, expected: str) -> None:
        assert resolve_margin_css(raw_css) == expected
        args = parse_args(["doc.md", "--margin", raw_css])
        assert args.margin == expected

    @pytest.mark.parametrize(
        "invalid_margin",
        [
            "",
            "   ",
            "tiny",
            "10%",
            "10mm 20mm 30mm 40mm 50mm",
            "-5mm",
            "10mm; color: red",
            "auto",
        ],
    )
    def test_invalid_margin_rejected(self, invalid_margin: str) -> None:
        with pytest.raises(ValueError):
            resolve_margin_css(invalid_margin)

        with pytest.raises(CliArgumentError):
            parse_args(["doc.md", "--margin", invalid_margin])


class TestAccentColorValidation:
    """Verify --accent hex and named color validation."""

    @pytest.mark.parametrize(
        ("raw_accent", "expected"),
        [
            ("#0d47a1", "#0d47a1"),
            ("#fff", "#fff"),
            ("#12345678", "#12345678"),
            ("red", "red"),
            ("navy", "navy"),
            ("  #0d47a1  ", "#0d47a1"),
        ],
    )
    def test_valid_accent_colors(self, raw_accent: str, expected: str) -> None:
        args = parse_args(["doc.md", "--accent", raw_accent])
        assert args.accent == expected

    @pytest.mark.parametrize(
        "invalid_accent",
        ["", "   ", "#12", "#gggggg", "red; background: black", "bad_color_name"],
    )
    def test_invalid_accent_colors(self, invalid_accent: str) -> None:
        with pytest.raises(CliArgumentError, match=r"Invalid accent color"):
            parse_args(["doc.md", "--accent", invalid_accent])


class TestBooleanFlagsAndPrecedence:
    """Verify tri-state flags, mutually exclusive --toc/--no-toc, and 4-layer precedence."""

    def test_flags_enabled_and_mutually_exclusive_toc(self) -> None:
        args = parse_args([
            "doc.md",
            "--theme",
            "paper",
            "--toc",
            "--no-page-numbers",
            "--keep-missing-images",
            "--no-keep-together",
            "--no-auto-fit",
            "--strict",
            "--allow-outside-base-dir",
            "--preview-dir",
            "previews",
            "--json",
            "--quiet",
            "--title",
            "Project Spec",
        ])
        assert args.toc is True
        assert args.page_numbers is False
        assert args.no_auto_fit is True
        assert args.strict is True
        assert args.preview_dir == "previews"

        options: ConversionOptions = build_conversion_options(args)
        assert options.theme == "paper"
        assert options.toc is True
        assert options.page_numbers is False
        assert options.suppress_missing_images is False
        assert options.keep_blocks_together is False
        assert options.auto_fit is False
        assert options.strict is True
        assert options.allow_outside_base_dir is True
        assert options.preview_dir == "previews"
        assert options.title == "Project Spec"

        with pytest.raises(CliArgumentError):
            parse_args(["doc.md", "--toc", "--no-toc"])

    def test_precedence_cli_over_frontmatter_over_theme_over_global(self) -> None:
        md_text = (
            "---\n"
            "theme: paper\n"
            "font-size: 11.5\n"
            "accent: '#222222'\n"
            "toc: true\n"
            "page_numbers: true\n"
            "---\n"
            "# Ignored Because FM/CLI\n\nBody.\n"
        )
        # Unset CLI args let frontmatter & paper theme defaults apply
        args_fm = parse_args(["doc.md"])
        opts_fm = build_conversion_options(args_fm, md_text=md_text)
        assert opts_fm.theme == "paper"
        assert opts_fm.font_size_pt == 11.5
        assert opts_fm.margin_css == "18mm 20mm"  # paper theme default
        assert opts_fm.accent_color == "#222222"
        assert opts_fm.toc is True
        assert opts_fm.page_numbers is True

        # Explicit CLI flags override frontmatter and theme
        args_cli = parse_args(
            ["doc.md", "--theme", "classic", "--no-toc", "--accent", "#0369a1"]
        )
        opts_cli = build_conversion_options(args_cli, md_text=md_text)
        assert opts_cli.theme == "classic"
        assert opts_cli.font_size_pt == 11.5  # still from frontmatter
        assert opts_cli.margin_css == "12mm 14mm"  # classic theme default
        assert opts_cli.accent_color == "#0369a1"  # CLI override
        assert opts_cli.toc is False  # --no-toc override

    def test_default_classic_options(self) -> None:
        args = parse_args(["doc.md"])
        options = build_conversion_options(args)
        assert options.theme == "classic"
        assert options.font_size_pt == 9.5
        assert options.line_height == 1.45
        assert options.margin_css == "12mm 14mm"
        assert options.page_size == "A4 portrait"
        assert options.accent_color == "#0d47a1"
        assert options.page_numbers is True
        assert options.toc is False
        assert options.auto_fit is True
        assert options.title == "doc"


class TestStdinArgRequirements:
    """Verify stdin '-' requires --base-dir."""

    def test_stdin_without_base_dir_fails(self) -> None:
        with pytest.raises(
            CliArgumentError, match=r"--base-dir is required when reading from stdin"
        ):
            parse_args(["-"])

    def test_stdin_with_empty_base_dir_fails(self) -> None:
        with pytest.raises(
            CliArgumentError, match=r"--base-dir is required when reading from stdin"
        ):
            parse_args(["-", "--base-dir", "   "])

    def test_stdin_with_valid_base_dir_succeeds(self) -> None:
        args = parse_args(["-", "--base-dir", "C:/some/path"])
        assert args.input == "-"
        assert args.base_dir == "C:/some/path"


class TestResolveDefaultTitle:
    """Verify default title extraction from H1 headings and filename fallbacks."""

    def test_extracts_first_h1_ignoring_fenced_code_and_bom(self) -> None:
        md = (
            "\ufeff```python\n"
            "# Not a title inside code\n"
            "```\n\n"
            "~~~bash\n"
            "# Also in tilde fence\n"
            "~~~\n\n"
            "# **สรุปผล** [Lab 1](https://example.com) `v2` ###\n"
            "# Second Heading\n"
        )
        assert resolve_default_title(md, "notes/lab1.md") == "สรุปผล Lab 1 v2"

    def test_falls_back_to_filename_stem_or_stdin(self) -> None:
        assert resolve_default_title("## Only H2", "reports/quarterly_summary.md") == (
            "quarterly_summary"
        )
        assert resolve_default_title("# ![](img.png)", "notes/chart_only.md") == "chart_only"
        assert resolve_default_title("Plain text.", "-") == "stdin"


class TestVersionAndHelp:
    """Verify --version and --help invocations via main()."""

    def test_version_prints_and_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--version"])
        assert code == 0
        captured = capsys.readouterr()
        expected = f"mdtopdf-skill {__version__}"
        assert expected in captured.out or expected in captured.err
        assert "mdtopdf_core" not in captured.out

    def test_help_prints_and_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--help"])
        assert code == 0
        captured = capsys.readouterr()
        assert "usage: mdtopdf" in captured.out
        assert "--theme" in captured.out
        assert "--strict" in captured.out
        assert "--preview-dir" in captured.out
        assert "--allow-outside-base-dir" in captured.out
