"""Command-line interface for mdtopdf-skill (thin wrapper over mdtopdf_core)."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from mdtopdf_core import (
    MARGIN_PRESETS,
    ConversionOptions,
    ConversionResult,
    find_browser_executable,
    render_pdf,
)

from mdtopdf_skill import __version__

__all__ = [
    "EXIT_BAD_ARGS",
    "EXIT_EMPTY_PDF",
    "EXIT_NO_BROWSER",
    "EXIT_OK",
    "EXIT_RENDER_ERROR",
    "CliArgumentError",
    "build_conversion_options",
    "build_parser",
    "main",
    "parse_args",
    "resolve_margin_css",
    "run",
]

EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_NO_BROWSER = 3
EXIT_RENDER_ERROR = 4
EXIT_EMPTY_PDF = 5

_DEFAULT_RENDER_PDF = render_pdf
_DEFAULT_FIND_BROWSER = find_browser_executable

_FONT_SIZE_MIN = 6.0
_FONT_SIZE_MAX = 72.0
_LINE_HEIGHT_MIN = 1.0
_LINE_HEIGHT_MAX = 3.0

# Build case-insensitive preset map directly from upstream MARGIN_PRESETS (SSOT)
_MARGIN_PRESET_MAP: dict[str, str] = {}
for _preset_label, _preset_css in MARGIN_PRESETS.items():
    _MARGIN_PRESET_MAP[_preset_label.strip().lower()] = _preset_css
    _short_name = _preset_label.split()[0].strip().lower()
    _MARGIN_PRESET_MAP[_short_name] = _preset_css

_CSS_MARGIN_RE = re.compile(
    r"^(?:0|\d+(?:\.\d+)?(?:mm|cm|in|pt|px|pc|%))"
    r"(?:\s+(?:0|\d+(?:\.\d+)?(?:mm|cm|in|pt|px|pc|%))){0,3}$",
    flags=re.IGNORECASE,
)
_ACCENT_COLOR_RE = re.compile(
    r"^(?:#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})|[a-zA-Z]{3,20})$"
)


class CliArgumentError(ValueError, SystemExit):
    """Exception raised for invalid CLI arguments (exit code 2)."""

    def __init__(self, message: str, code: int = EXIT_BAD_ARGS) -> None:
        SystemExit.__init__(self, code)
        ValueError.__init__(self, message)
        self.message: str = message
        self.exit_code: int = code

    def __str__(self) -> str:
        return self.message


class _CliArgumentParser(argparse.ArgumentParser):
    """ArgumentParser subclass that raises CliArgumentError instead of printing/exiting directly."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._negative_number_matcher = re.compile(
            r"^-(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$|^-(?:inf|infinity|nan)$",
            flags=re.IGNORECASE,
        )

    def error(self, message: str) -> NoReturn:
        raise CliArgumentError(message, EXIT_BAD_ARGS)


def _configure_stdio_utf8() -> None:
    """Ensure stdout and stderr can emit UTF-8 text safely on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            with contextlib.suppress(Exception):
                encoding = getattr(stream, "encoding", None) or ""
                if encoding.lower().replace("-", "") != "utf8":
                    stream.reconfigure(encoding="utf-8", errors="replace")


def _validate_finite_float(raw: str, flag_name: str, min_val: float, max_val: float) -> float:
    """Parse a finite float within [min_val, max_val] or raise CliArgumentError."""
    try:
        value = float(raw)
    except ValueError as exc:
        raise CliArgumentError(f"{flag_name} must be a number, got {raw!r}.") from exc

    if not math.isfinite(value):
        raise CliArgumentError(f"{flag_name} must be a finite number, got {raw!r}.")

    if not (min_val <= value <= max_val):
        raise CliArgumentError(
            f"{flag_name} must be between {min_val:g} and {max_val:g}, got {value:g}."
        )
    return value


def _validate_positive_int(raw: str, flag_name: str) -> int:
    """Parse a finite positive integer (>= 1) or raise CliArgumentError."""
    try:
        float_val = float(raw)
    except ValueError as exc:
        raise CliArgumentError(f"{flag_name} must be a positive integer, got {raw!r}.") from exc

    if not math.isfinite(float_val):
        raise CliArgumentError(f"{flag_name} must be a finite integer, got {raw!r}.")

    try:
        value = int(raw)
    except ValueError as exc:
        raise CliArgumentError(f"{flag_name} must be an integer, got {raw!r}.") from exc

    if value < 1:
        raise CliArgumentError(f"{flag_name} must be >= 1, got {value}.")
    return value


def resolve_margin_css(raw_margin: str) -> str:
    """Resolve a margin preset name or validate a raw CSS margin string."""
    cleaned = raw_margin.strip()
    if not cleaned:
        raise CliArgumentError("--margin must not be empty.")

    preset_match = _MARGIN_PRESET_MAP.get(cleaned.lower())
    if preset_match is not None:
        return preset_match

    if _CSS_MARGIN_RE.match(cleaned):
        return re.sub(r"\s+", " ", cleaned)

    valid_presets = ", ".join(sorted({k.split()[0].lower() for k in MARGIN_PRESETS}))
    raise CliArgumentError(
        f"--margin must be a preset ({valid_presets}) or a valid CSS margin string "
        f"(e.g. '12mm 14mm'), got {raw_margin!r}."
    )


def _validate_accent_color(raw_accent: str) -> str:
    """Validate a CSS hex or named accent color."""
    cleaned = raw_accent.strip()
    if not _ACCENT_COLOR_RE.match(cleaned):
        raise CliArgumentError(
            f"--accent must be a valid hex color (e.g. '#0d47a1'), got {raw_accent!r}."
        )
    return cleaned


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI ArgumentParser per §5 of docs/HANDOFF.md."""
    defaults = ConversionOptions()
    parser = _CliArgumentParser(
        prog="mdtopdf",
        description="Convert Markdown documents to print-ready A4 PDFs via mdtopdf_core.",
    )
    parser.add_argument(
        "input",
        metavar="INPUT.md",
        help="Input Markdown file path, or '-' to read UTF-8 Markdown from stdin.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="OUT.pdf",
        default=None,
        help="Output PDF file path (defaults to INPUT with .pdf extension).",
    )
    parser.add_argument(
        "--base-dir",
        metavar="DIR",
        default=None,
        help=(
            "Base directory for resolving relative local images "
            "(defaults to input file's directory; required when INPUT.md is '-')."
        ),
    )
    parser.add_argument(
        "--font-size",
        metavar="PT",
        default=str(defaults.font_size_pt),
        help=(
            f"Base font size in points ({_FONT_SIZE_MIN:g}-{_FONT_SIZE_MAX:g}, "
            f"default: {defaults.font_size_pt:g})."
        ),
    )
    parser.add_argument(
        "--line-height",
        metavar="N",
        default=str(defaults.line_height),
        help=(
            f"Line-height multiplier ({_LINE_HEIGHT_MIN:g}-{_LINE_HEIGHT_MAX:g}, "
            f"default: {defaults.line_height:g})."
        ),
    )
    parser.add_argument(
        "--margin",
        metavar="MARGIN",
        default=defaults.margin_css,
        help=(
            "Page margin preset ({compact,standard,comfortable,wide}) or raw CSS margin string "
            f'(e.g. "12mm 14mm", default: "{defaults.margin_css}").'
        ),
    )
    parser.add_argument(
        "--img-max-width",
        metavar="PX",
        default=str(defaults.img_max_width),
        help=f"Maximum table image width in pixels (default: {defaults.img_max_width}).",
    )
    parser.add_argument(
        "--img-max-height",
        metavar="PX",
        default=str(defaults.img_max_height),
        help=f"Maximum table image height in pixels (default: {defaults.img_max_height}).",
    )
    parser.add_argument(
        "--keep-missing-images",
        action="store_true",
        default=False,
        help="Do not strip missing local image tags before rendering.",
    )
    parser.add_argument(
        "--no-keep-together",
        action="store_true",
        default=False,
        help="Allow page breaks inside tables and grouped heading/list blocks.",
    )
    parser.add_argument(
        "--accent",
        metavar='"#hex"',
        default=defaults.accent_color,
        help=f'Heading and table accent color (default: "{defaults.accent_color}").',
    )
    parser.add_argument(
        "--title",
        metavar="TEXT",
        default=None,
        help="Optional document title metadata.",
    )
    parser.add_argument(
        "--allow-outside-base-dir",
        action="store_true",
        default=False,
        help=(
            "WARNING: Disable path-traversal protection and allow embedding local images "
            "located outside --base-dir (off by default for security; UNC and remote "
            "file://host URLs remain blocked)."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Print a single JSON result object to stdout.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress non-error human-readable status output.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate CLI arguments, returning a populated Namespace."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    args.font_size = _validate_finite_float(
        str(args.font_size), "--font-size", _FONT_SIZE_MIN, _FONT_SIZE_MAX
    )
    args.line_height = _validate_finite_float(
        str(args.line_height), "--line-height", _LINE_HEIGHT_MIN, _LINE_HEIGHT_MAX
    )
    args.img_max_width = _validate_positive_int(str(args.img_max_width), "--img-max-width")
    args.img_max_height = _validate_positive_int(str(args.img_max_height), "--img-max-height")
    args.margin_css = resolve_margin_css(str(args.margin))
    args.accent = _validate_accent_color(str(args.accent))

    if args.input == "-" and (args.base_dir is None or not str(args.base_dir).strip()):
        raise CliArgumentError("--base-dir is required when reading from stdin ('-').")

    return args


def build_conversion_options(args: argparse.Namespace) -> ConversionOptions:
    """Build a mdtopdf_core.ConversionOptions instance from validated CLI arguments."""
    defaults = ConversionOptions()
    return ConversionOptions(
        font_size_pt=float(args.font_size),
        line_height=float(args.line_height),
        margin_css=str(args.margin_css),
        img_max_width=int(args.img_max_width),
        img_max_height=int(args.img_max_height),
        suppress_missing_images=not bool(args.keep_missing_images),
        keep_blocks_together=not bool(args.no_keep_together),
        accent_color=str(args.accent),
        title=str(args.title) if args.title is not None else defaults.title,
        allow_outside_base_dir=bool(args.allow_outside_base_dir),
    )


def _emit_error(message: str, code: int, json_mode: bool) -> int:
    """Write error output to stderr (or stdout as JSON when --json is active) and return code."""
    if json_mode:
        payload = {"ok": False, "error": message, "code": code}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


def _read_markdown_input(input_arg: str) -> str:
    """Read UTF-8 Markdown (tolerating BOM) from a file path or stdin ('-')."""
    if input_arg == "-":
        try:
            if hasattr(sys.stdin, "reconfigure"):
                with contextlib.suppress(Exception):
                    sys.stdin.reconfigure(encoding="utf-8-sig")
            raw_text = sys.stdin.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise CliArgumentError(f"Failed to read UTF-8 Markdown from stdin: {exc}") from exc
        return raw_text.removeprefix("\ufeff")

    input_path = Path(input_arg)
    if not input_path.exists():
        raise CliArgumentError(f"Input file not found: {input_arg}")
    if not input_path.is_file():
        raise CliArgumentError(f"Input path is not a file: {input_arg}")

    try:
        return input_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliArgumentError(f"Failed to read input file {input_arg!r}: {exc}") from exc


def run(args: argparse.Namespace) -> int:
    """Execute the conversion workflow from validated CLI arguments."""
    json_mode = bool(getattr(args, "json", False))
    quiet_mode = bool(getattr(args, "quiet", False))

    try:
        md_text = _read_markdown_input(str(args.input))

        if args.input == "-":
            base_dir_path = Path(str(args.base_dir))
            output_path = (
                Path(str(args.output))
                if args.output is not None
                else base_dir_path / "stdin.pdf"
            )
        else:
            input_path = Path(str(args.input))
            base_dir_path = (
                Path(str(args.base_dir))
                if args.base_dir is not None
                else input_path.resolve().parent
            )
            output_path = (
                Path(str(args.output))
                if args.output is not None
                else input_path.with_suffix(".pdf")
            )

        if base_dir_path.exists() and not base_dir_path.is_dir():
            raise CliArgumentError(f"--base-dir is not a directory: {base_dir_path}")

        options = build_conversion_options(args)
    except CliArgumentError as exc:
        return _emit_error(exc.message, exc.exit_code, json_mode)

    try:
        if (
            find_browser_executable is not _DEFAULT_FIND_BROWSER
            or render_pdf is _DEFAULT_RENDER_PDF
        ):
            find_browser_executable()

        result: ConversionResult = render_pdf(
            md_text,
            str(base_dir_path),
            str(output_path),
            options=options,
        )
    except FileNotFoundError as exc:
        msg = str(exc) or "No headless browser found."
        if "MDTOPDF_BROWSER" not in msg:
            msg = (
                f"{msg} Set the MDTOPDF_BROWSER environment variable to the path of "
                "Microsoft Edge, Google Chrome, or Chromium."
            )
        return _emit_error(msg, EXIT_NO_BROWSER, json_mode)
    except Exception as exc:
        return _emit_error(f"PDF rendering failed: {exc}", EXIT_RENDER_ERROR, json_mode)

    resolved_out = Path(result.output_pdf_path) if result.output_pdf_path else output_path
    file_empty = resolved_out.exists() and resolved_out.stat().st_size == 0
    file_missing = render_pdf is _DEFAULT_RENDER_PDF and not resolved_out.exists()
    if result.page_count <= 0 or file_empty or file_missing:
        return _emit_error(
            f"Output PDF is empty or has 0 pages: {resolved_out}",
            EXIT_EMPTY_PDF,
            json_mode,
        )

    if result.suppressed_images:
        joined = ", ".join(result.suppressed_images)
        print(
            f"Warning: {len(result.suppressed_images)} suppressed image(s) "
            f"(missing or outside base-dir): {joined}",
            file=sys.stderr,
        )

    if json_mode:
        payload = {
            "ok": True,
            "output": str(result.output_pdf_path),
            "pages": int(result.page_count),
            "embedded_images": int(result.embedded_images),
            "suppressed_images": list(result.suppressed_images),
            "elapsed_seconds": round(float(result.elapsed_seconds), 4),
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
    elif not quiet_mode:
        print(
            f"Rendered {result.output_pdf_path} "
            f"({result.page_count} page(s), {result.embedded_images} image(s), "
            f"{result.elapsed_seconds:.2f}s)",
            file=sys.stdout,
        )

    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``mdtopdf`` console script."""
    _configure_stdio_utf8()
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in effective_argv

    try:
        args = parse_args(effective_argv)
    except CliArgumentError as exc:
        return _emit_error(exc.message, exc.exit_code, json_requested)
    except SystemExit as exc:
        if exc.code in (0, None):
            return EXIT_OK
        code = exc.code if isinstance(exc.code, int) else EXIT_BAD_ARGS
        return _emit_error(f"Invalid command-line arguments (exit {code}).", code, json_requested)

    return run(args)


if __name__ == "__main__":
    sys.exit(main())
