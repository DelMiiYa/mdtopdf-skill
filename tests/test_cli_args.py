"""Tests for CLI argument parsing, validation, and ConversionOptions construction."""

from __future__ import annotations

import math

import pytest
from mdtopdf_core import MARGIN_PRESETS, ConversionOptions

from mdtopdf_skill import __version__
from mdtopdf_skill.cli import (
    CliArgumentError,
    build_conversion_options,
    main,
    parse_args,
    resolve_margin_css,
)


class TestCliArgParsingDefaults:
    """Verify default values when minimal arguments are provided."""

    def test_minimal_positional_input(self) -> None:
        args = parse_args(["document.md"])
        assert args.input == "document.md"
        assert args.output is None
        assert args.base_dir is None
        assert args.font_size == 9.5
        assert args.line_height == 1.45
        assert args.margin_css == "12mm 14mm"
        assert args.img_max_width == 90
        assert args.img_max_height == 70
        assert args.keep_missing_images is False
        assert args.no_keep_together is False
        assert args.accent == "#0d47a1"
        assert args.title is None
        assert args.allow_outside_base_dir is False
        assert args.json is False
        assert args.quiet is False

    def test_explicit_output_and_base_dir(self) -> None:
        args = parse_args([
            "doc.md",
            "-o",
            "out.pdf",
            "--base-dir",
            "/custom/dir",
        ])
        assert args.output == "out.pdf"
        assert args.base_dir == "/custom/dir"


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
        [
            "5.99",
            "5.9",
            "0",
            "-1",
            "-10",
            "72.01",
            "72.1",
            "100",
            "999",
        ],
    )
    def test_out_of_range_font_size(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"--font-size must be between 6 and 72"):
            parse_args(["doc.md", "--font-size", raw_value])

    @pytest.mark.parametrize(
        "raw_value",
        ["nan", "NaN", "NAN", "inf", "-inf", "Infinity", "-Infinity", "+inf"],
    )
    def test_non_finite_font_size(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"--font-size must be a finite number"):
            parse_args(["doc.md", "--font-size", raw_value])

    @pytest.mark.parametrize(
        "raw_value",
        ["abc", "12pt", "small", "", " "],
    )
    def test_invalid_string_font_size(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"--font-size must be a number"):
            parse_args(["doc.md", "--font-size", raw_value])


class TestLineHeightValidation:
    """Verify --line-height validation (finite float in [1.0, 3.0])."""

    @pytest.mark.parametrize(
        ("raw_value", "expected"),
        [
            ("1.0", 1.0),
            ("1", 1.0),
            ("3.0", 3.0),
            ("3", 3.0),
            ("1.45", 1.45),
            ("2.0", 2.0),
            ("2.5", 2.5),
        ],
    )
    def test_valid_line_height(self, raw_value: str, expected: float) -> None:
        args = parse_args(["doc.md", "--line-height", raw_value])
        assert math.isclose(args.line_height, expected)

    @pytest.mark.parametrize(
        "raw_value",
        [
            "0.99",
            "0.9",
            "0",
            "-1.5",
            "-3",
            "3.01",
            "3.1",
            "4.0",
            "10",
        ],
    )
    def test_out_of_range_line_height(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"--line-height must be between 1 and 3"):
            parse_args(["doc.md", "--line-height", raw_value])

    @pytest.mark.parametrize(
        "raw_value",
        ["nan", "NaN", "NAN", "inf", "-inf", "Infinity", "-Infinity"],
    )
    def test_non_finite_line_height(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"--line-height must be a finite number"):
            parse_args(["doc.md", "--line-height", raw_value])

    @pytest.mark.parametrize(
        "raw_value",
        ["xyz", "normal", "1.5x", "", " "],
    )
    def test_invalid_string_line_height(self, raw_value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"--line-height must be a number"):
            parse_args(["doc.md", "--line-height", raw_value])


class TestImageDimensionValidation:
    """Verify --img-max-width and --img-max-height validation (positive int >= 1)."""

    @pytest.mark.parametrize(
        ("flag", "value", "expected"),
        [
            ("--img-max-width", "90", 90),
            ("--img-max-width", "1", 1),
            ("--img-max-width", "500", 500),
            ("--img-max-height", "70", 70),
            ("--img-max-height", "1", 1),
            ("--img-max-height", "1200", 1200),
        ],
    )
    def test_valid_image_dimensions(self, flag: str, value: str, expected: int) -> None:
        args = parse_args(["doc.md", flag, value])
        attr = flag.lstrip("-").replace("-", "_")
        assert getattr(args, attr) == expected

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--img-max-width", "0"),
            ("--img-max-width", "-1"),
            ("--img-max-width", "-50"),
            ("--img-max-height", "0"),
            ("--img-max-height", "-10"),
        ],
    )
    def test_non_positive_image_dimensions(self, flag: str, value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"must be >= 1"):
            parse_args(["doc.md", flag, value])

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--img-max-width", "10.5"),
            ("--img-max-height", "25.8"),
        ],
    )
    def test_non_integer_float_image_dimensions(self, flag: str, value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"must be an integer"):
            parse_args(["doc.md", flag, value])

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--img-max-width", "nan"),
            ("--img-max-width", "inf"),
            ("--img-max-height", "nan"),
            ("--img-max-height", "-inf"),
        ],
    )
    def test_non_finite_image_dimensions(self, flag: str, value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"must be a finite integer"):
            parse_args(["doc.md", flag, value])

    @pytest.mark.parametrize(
        ("flag", "value"),
        [
            ("--img-max-width", "big"),
            ("--img-max-height", "small"),
        ],
    )
    def test_string_image_dimensions(self, flag: str, value: str) -> None:
        with pytest.raises(CliArgumentError, match=r"must be a positive integer"):
            parse_args(["doc.md", flag, value])


class TestMarginResolution:
    """Verify --margin resolution with presets and raw CSS margin strings."""

    @pytest.mark.parametrize(
        ("preset_input", "expected_key"),
        [
            ("compact", "Compact (10×12mm)"),
            ("Compact", "Compact (10×12mm)"),
            ("COMPACT", "Compact (10×12mm)"),
            ("standard", "Standard (12×14mm)"),
            ("Standard", "Standard (12×14mm)"),
            ("comfortable", "Comfortable (15×18mm)"),
            ("Comfortable", "Comfortable (15×18mm)"),
            ("wide", "Wide (18×20mm)"),
            ("WIDE", "Wide (18×20mm)"),
            ("  compact  ", "Compact (10×12mm)"),
        ],
    )
    def test_margin_presets_case_insensitive(
        self, preset_input: str, expected_key: str
    ) -> None:
        expected_css = MARGIN_PRESETS[expected_key]
        resolved = resolve_margin_css(preset_input)
        assert resolved == expected_css

        args = parse_args(["doc.md", "--margin", preset_input])
        assert args.margin_css == expected_css

    @pytest.mark.parametrize(
        ("raw_css", "expected"),
        [
            ("12mm 14mm", "12mm 14mm"),
            ("10mm", "10mm"),
            ("15mm 20mm 15mm 20mm", "15mm 20mm 15mm 20mm"),
            ("1in 1.5in", "1in 1.5in"),
            ("2cm", "2cm"),
            ("0 10pt", "0 10pt"),
            ("  14mm   16mm  ", "14mm 16mm"),
        ],
    )
    def test_valid_raw_css_margins(self, raw_css: str, expected: str) -> None:
        resolved = resolve_margin_css(raw_css)
        assert resolved == expected

    @pytest.mark.parametrize(
        "invalid_margin",
        [
            "",
            "   ",
            "invalid",
            "12",
            "12mm 14mm 16mm 18mm 20mm",
            "12foo",
            "12mm; color: red;",
        ],
    )
    def test_invalid_margin_raises(self, invalid_margin: str) -> None:
        with pytest.raises(CliArgumentError):
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
            ("#FFF", "#FFF"),
            ("#12345678", "#12345678"),
            ("#aabbcc", "#aabbcc"),
            ("red", "red"),
            ("darkblue", "darkblue"),
            ("navy", "navy"),
            ("  #0d47a1  ", "#0d47a1"),
        ],
    )
    def test_valid_accent_colors(self, raw_accent: str, expected: str) -> None:
        args = parse_args(["doc.md", "--accent", raw_accent])
        assert args.accent == expected

    @pytest.mark.parametrize(
        "invalid_accent",
        [
            "",
            "   ",
            "#12",
            "#12345",
            "#gggggg",
            "#123456789",
            "red; background: black",
            "this_color_name_has_underscores",
            "way_too_long_to_be_a_valid_css_color_name_abcdefghijklmnop",
        ],
    )
    def test_invalid_accent_colors(self, invalid_accent: str) -> None:
        with pytest.raises(CliArgumentError, match=r"--accent must be a valid hex color"):
            parse_args(["doc.md", "--accent", invalid_accent])


class TestBooleanFlagsAndOptions:
    """Verify boolean flags and ConversionOptions translation."""

    def test_flags_enabled(self) -> None:
        args = parse_args([
            "doc.md",
            "--keep-missing-images",
            "--no-keep-together",
            "--allow-outside-base-dir",
            "--json",
            "--quiet",
            "--title",
            "Project Spec",
        ])
        assert args.keep_missing_images is True
        assert args.no_keep_together is True
        assert args.allow_outside_base_dir is True
        assert args.json is True
        assert args.quiet is True
        assert args.title == "Project Spec"

        options: ConversionOptions = build_conversion_options(args)
        assert options.suppress_missing_images is False
        assert options.keep_blocks_together is False
        assert options.allow_outside_base_dir is True
        assert options.title == "Project Spec"

    def test_flags_defaults_conversion_options(self) -> None:
        args = parse_args(["doc.md"])
        options: ConversionOptions = build_conversion_options(args)

        assert options.font_size_pt == 9.5
        assert options.line_height == 1.45
        assert options.margin_css == "12mm 14mm"
        assert options.img_max_width == 90
        assert options.img_max_height == 70
        assert options.suppress_missing_images is True
        assert options.keep_blocks_together is True
        assert options.accent_color == "#0d47a1"
        assert options.title == "Markdown Document"
        assert options.allow_outside_base_dir is False


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


class TestVersionAndHelp:
    """Verify --version and --help invocations via main()."""

    def test_version_prints_and_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--version"])
        assert code == 0
        captured = capsys.readouterr()
        assert f"mdtopdf {__version__}" in captured.out or f"mdtopdf {__version__}" in captured.err

    def test_help_prints_and_exits_0(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--help"])
        assert code == 0
        captured = capsys.readouterr()
        assert "usage: mdtopdf" in captured.out
        assert "--font-size" in captured.out
        assert "--allow-outside-base-dir" in captured.out
