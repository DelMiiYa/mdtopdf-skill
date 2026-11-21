"""Command-line interface for mdtopdf-skill v1.0.0."""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath
from typing import NoReturn

from mdtopdf_skill import __version__
from mdtopdf_skill.cdp_browser import find_browser_executable
from mdtopdf_skill.frontmatter import parse_frontmatter
from mdtopdf_skill.models import (
    EXIT_BAD_ARGS,
    EXIT_EMPTY_PDF,
    EXIT_NO_BROWSER,
    EXIT_OK,
    EXIT_RENDER_ERROR,
    EXIT_STRICT_ISSUE,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    LINE_HEIGHT_MAX,
    LINE_HEIGHT_MIN,
    MARGIN_PRESETS,
    SPACING_SCALE_MAX,
    SPACING_SCALE_MIN,
    ConversionOptions,
    ConversionResult,
    FrontmatterData,
    compute_page_geometry,
    resolve_effective_options,
    resolve_margin_css,
    resolve_page_size,
    resolve_theme_name,
    validate_accent_color,
    validate_font_size,
    validate_line_height,
    validate_positive_int,
    validate_spacing_scale,
)
from mdtopdf_skill.renderer import render_pdf
from mdtopdf_skill.themes import THEMES

__all__ = [
    "EXIT_BAD_ARGS",
    "EXIT_EMPTY_PDF",
    "EXIT_NO_BROWSER",
    "EXIT_OK",
    "EXIT_RENDER_ERROR",
    "EXIT_STRICT_ISSUE",
    "CliArgumentError",
    "build_conversion_options",
    "build_parser",
    "main",
    "parse_args",
    "resolve_default_title",
    "resolve_margin_css",
    "run",
]

_VALUE_TAKING_FLAGS: frozenset[str] = frozenset(
    {
        "-o",
        "--output",
        "--base-dir",
        "--theme",
        "--font-size",
        "--line-height",
        "--spacing-scale",
        "--margin",
        "--page-size",
        "--img-max-width",
        "--img-max-height",
        "--accent",
        "--title",
        "--css",
        "--preview-dir",
    }
)
_CONTROL_CHAR_RE = re.compile(r"[\r\n\x00-\x1f\x7f-\x9f]")
_FENCED_CODE_RE = re.compile(
    r"^[ \t]{0,3}(`{3,}|~{3,})[\s\S]*?^[ \t]{0,3}\1[ \t]*$",
    flags=re.MULTILINE,
)
_ATX_H1_RE = re.compile(
    r"^[ \t]{0,3}#[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$",
    flags=re.MULTILINE,
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
    """ArgumentParser subclass raising CliArgumentError instead of exiting directly."""

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


def _wrap_validation(func: object, *args: object) -> object:
    """Run a models.py validator, translating ValueError into CliArgumentError."""
    try:
        return func(*args)  # type: ignore[operator]
    except ValueError as exc:
        raise CliArgumentError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI ArgumentParser per §4 of handoff/IMPLEMENTATION_PLAN.md."""
    theme_choices = ", ".join(THEMES.keys())
    margin_choices = ", ".join(MARGIN_PRESETS.keys())

    parser = _CliArgumentParser(
        prog="mdtopdf",
        description=(
            "Convert Markdown documents (themes, frontmatter, Mermaid, KaTeX, Thai/Unicode) "
            "to print-ready PDFs via headless Chromium/Edge CDP."
        ),
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        metavar="INPUT.md",
        help=(
            "Input Markdown file path, or '-' to read UTF-8 Markdown from stdin "
            "(required unless --list-themes is passed)."
        ),
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
            "Base directory for resolving relative local images and CSS files "
            "(defaults to input file's directory; required when INPUT.md is '-')."
        ),
    )
    parser.add_argument(
        "--theme",
        metavar="NAME",
        default=None,
        help=f"Print theme ({theme_choices}; overrides frontmatter, default: office).",
    )
    parser.add_argument(
        "--list-themes",
        action="store_true",
        default=False,
        help="List all built-in print themes with descriptions and exit 0.",
    )
    parser.add_argument(
        "--font-size",
        metavar="PT",
        default=None,
        help=(
            f"Base body font size in points ({FONT_SIZE_MIN:g}-{FONT_SIZE_MAX:g}; "
            "overrides frontmatter/theme)."
        ),
    )
    parser.add_argument(
        "--line-height",
        metavar="N",
        default=None,
        help=(
            f"Body line-height multiplier ({LINE_HEIGHT_MIN:g}-{LINE_HEIGHT_MAX:g}; "
            "overrides frontmatter/theme)."
        ),
    )
    parser.add_argument(
        "--spacing-scale",
        metavar="N",
        default=None,
        help=(
            f"Vertical spacing scale factor ({SPACING_SCALE_MIN:g}-{SPACING_SCALE_MAX:g}; "
            "overrides frontmatter, default: 1.0)."
        ),
    )
    parser.add_argument(
        "--margin",
        metavar="MARGIN",
        default=None,
        help=(
            f"Page margin preset ({margin_choices}) or 1-4 CSS physical lengths "
            '(e.g. "12mm 14mm"; overrides frontmatter/theme).'
        ),
    )
    parser.add_argument(
        "--page-size",
        metavar="SPEC",
        default=None,
        help=(
            'Page size preset (A4/Letter/Legal [portrait|landscape]) or "<W><unit> <H><unit>" '
            '(e.g. "A4 landscape"; overrides frontmatter, default: "A4 portrait").'
        ),
    )
    parser.add_argument(
        "--img-max-width",
        metavar="PX",
        default=None,
        help="Maximum image width inside table cells in CSS pixels (default: 90).",
    )
    parser.add_argument(
        "--img-max-height",
        metavar="PX",
        default=None,
        help="Maximum image height inside table cells in CSS pixels (default: 70).",
    )
    parser.add_argument(
        "--accent",
        metavar='"#hex"',
        default=None,
        help="Heading and table accent CSS hex/named color (overrides frontmatter/theme).",
    )
    parser.add_argument(
        "--title",
        metavar="TEXT",
        default=None,
        help="Document title metadata (overrides frontmatter, else first H1 or filename).",
    )
    parser.add_argument(
        "--css",
        metavar="PATH",
        default=None,
        help="Path to a local CSS file (must resolve inside --base-dir unless opted out).",
    )

    toc_group = parser.add_mutually_exclusive_group()
    toc_group.add_argument(
        "--toc",
        action="store_const",
        const=True,
        dest="toc",
        default=None,
        help="Insert a generated Table of Contents (overrides frontmatter).",
    )
    toc_group.add_argument(
        "--no-toc",
        action="store_const",
        const=False,
        dest="toc",
        default=None,
        help="Disable Table of Contents even if enabled in frontmatter.",
    )

    parser.add_argument(
        "--no-page-numbers",
        action="store_const",
        const=False,
        dest="page_numbers",
        default=None,
        help="Disable page numbers in the running footer (overrides frontmatter/theme).",
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
        help="Allow page breaks inside table rows and grouped heading/list blocks.",
    )
    parser.add_argument(
        "--no-auto-fit",
        action="store_true",
        default=False,
        help=(
            "Skip automatic table compaction and orphan-page spacing retries "
            "(layout issue detection still runs)."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Exit with code 6 if unresolved layout_issues, mermaid_errors, or math_errors remain.",
    )
    parser.add_argument(
        "--preview-dir",
        metavar="DIR",
        default=None,
        help="Render each PDF page as page_001.png, page_002.png, ... (144 DPI) into DIR.",
    )
    parser.add_argument(
        "--allow-outside-base-dir",
        action="store_true",
        default=False,
        help=(
            "WARNING: Disable path-traversal protection and allow embedding local images and "
            "CSS files located outside --base-dir (off by default for security; UNC and "
            "remote file://host URLs remain blocked)."
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
        version=f"mdtopdf-skill {__version__}",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse and validate CLI arguments without resolving frontmatter/theme defaults."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.theme is not None:
        args.theme = _wrap_validation(resolve_theme_name, str(args.theme), THEMES)
    if args.font_size is not None:
        args.font_size = _wrap_validation(validate_font_size, str(args.font_size))
    if args.line_height is not None:
        args.line_height = _wrap_validation(validate_line_height, str(args.line_height))
    if args.spacing_scale is not None:
        args.spacing_scale = _wrap_validation(validate_spacing_scale, str(args.spacing_scale))
    if args.margin is not None:
        args.margin = _wrap_validation(resolve_margin_css, str(args.margin))
    if args.page_size is not None:
        args.page_size = _wrap_validation(resolve_page_size, str(args.page_size))
    if args.img_max_width is not None:
        args.img_max_width = _wrap_validation(
            validate_positive_int, str(args.img_max_width), "img_max_width"
        )
    if args.img_max_height is not None:
        args.img_max_height = _wrap_validation(
            validate_positive_int, str(args.img_max_height), "img_max_height"
        )
    if args.accent is not None:
        args.accent = _wrap_validation(validate_accent_color, str(args.accent))

    if not args.list_themes:
        if args.input is None or not str(args.input).strip():
            raise CliArgumentError("the following arguments are required: INPUT.md")
        if args.input == "-" and (args.base_dir is None or not str(args.base_dir).strip()):
            raise CliArgumentError("--base-dir is required when reading from stdin ('-').")

    return args


def resolve_default_title(md_text: str, input_arg: str) -> str:
    """Resolve fallback document title from first ATX H1 outside fences, else filename stem."""
    cleaned = md_text.lstrip("\ufeff")
    without_fences = _FENCED_CODE_RE.sub("", cleaned)
    match = _ATX_H1_RE.search(without_fences)
    if match:
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", match.group(1))
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"(\*\*|__|`)(.+?)\1", r"\2", text)
        text = re.sub(r"(?<!\w)(\*|_)(.+?)\1(?!\w)", r"\2", text)
        text = text.replace("`", "").strip()
        if text:
            return text

    if input_arg and input_arg != "-":
        input_path = Path(input_arg)
        return input_path.stem or input_path.name
    return "stdin"


def build_conversion_options(
    args: argparse.Namespace,
    md_text: str = "",
    *,
    frontmatter: FrontmatterData | None = None,
    md_body: str | None = None,
    cli_css: str = "",
    preview_dir: str | None = None,
) -> ConversionOptions:
    """Resolve ConversionOptions using 4-layer precedence: CLI > Frontmatter > Theme > Global."""
    if frontmatter is None or md_body is None:
        parsed_fm, parsed_body = parse_frontmatter(md_text)
        fm = frontmatter if frontmatter is not None else parsed_fm
        body = md_body if md_body is not None else parsed_body
    else:
        fm = frontmatter
        body = md_body

    default_title = resolve_default_title(body, str(getattr(args, "input", None) or "-"))
    keep_missing = bool(getattr(args, "keep_missing_images", False))
    no_keep_together = bool(getattr(args, "no_keep_together", False))
    no_auto_fit = bool(getattr(args, "no_auto_fit", False))
    effective_preview_dir = (
        preview_dir if preview_dir is not None else getattr(args, "preview_dir", None)
    )

    return resolve_effective_options(
        frontmatter=fm,
        themes=THEMES,
        theme=getattr(args, "theme", None),
        font_size_pt=getattr(args, "font_size", None),
        line_height=getattr(args, "line_height", None),
        spacing_scale=getattr(args, "spacing_scale", None),
        margin_css=getattr(args, "margin", None),
        page_size=getattr(args, "page_size", None),
        img_max_width=getattr(args, "img_max_width", None),
        img_max_height=getattr(args, "img_max_height", None),
        suppress_missing_images=not keep_missing if keep_missing else None,
        keep_blocks_together=not no_keep_together if no_keep_together else None,
        accent_color=getattr(args, "accent", None),
        title=getattr(args, "title", None),
        page_numbers=getattr(args, "page_numbers", None),
        toc=getattr(args, "toc", None),
        cli_css=cli_css if cli_css else None,
        auto_fit=not no_auto_fit if no_auto_fit else None,
        strict=bool(getattr(args, "strict", False)),
        allow_outside_base_dir=bool(getattr(args, "allow_outside_base_dir", False)),
        preview_dir=effective_preview_dir,
        default_title=default_title,
    )


def _emit_error(message: str, code: int, json_mode: bool) -> int:
    """Write error output to stderr (or stdout as JSON when --json is active) and return code."""
    if json_mode:
        payload = {"ok": False, "error": message, "code": code}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
    else:
        print(f"error: {message}", file=sys.stderr)
    return code


def _sanitize_for_stderr(text: str) -> str:
    """Escape control characters and newlines before printing untrusted strings to stderr."""
    return _CONTROL_CHAR_RE.sub(lambda m: repr(m.group(0))[1:-1], text)


def _is_json_requested(argv: Sequence[str]) -> bool:
    """Detect whether --json was passed as an option token (not an option value or after '--')."""
    skip_next = False
    for arg in argv:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            break
        if arg == "--json":
            return True
        if arg in _VALUE_TAKING_FLAGS:
            skip_next = True
    return False


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


def _is_directory_writable(dir_path: Path) -> bool:
    """Return True if dir_path exists or can be created and accepts new files."""
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=dir_path, prefix=".mdtopdf_write_probe_", delete=True
        ):
            return True
    except OSError:
        return False


def _resolve_writable_output_path(
    desired_output: Path,
    explicit_output: bool,
    input_path: Path | None,
) -> tuple[Path, str | None]:
    """Ensure output directory is writable, falling back to CWD/tempdir for default paths."""
    if _is_directory_writable(desired_output.parent):
        return desired_output, None

    if explicit_output:
        raise CliArgumentError(f"Output directory is not writable: {desired_output.parent}")

    for candidate_dir in (Path.cwd(), Path(tempfile.gettempdir())):
        candidate = candidate_dir / desired_output.name
        if candidate.exists() and candidate.is_dir():
            continue
        if input_path is not None and candidate.resolve() == input_path.resolve():
            continue
        if _is_directory_writable(candidate.parent):
            warning = (
                f"output_fallback: default output directory '{desired_output.parent}' "
                f"is not writable; wrote PDF to '{candidate}'"
            )
            return candidate, warning

    raise CliArgumentError(f"Output directory is not writable: {desired_output.parent}")


def _read_cli_css_file(
    css_arg: str,
    base_dir_path: Path,
    allow_outside_base_dir: bool,
) -> str:
    """Validate containment and read a CLI --css file, raising CliArgumentError on error."""
    raw = css_arg.strip()
    if not raw:
        raise CliArgumentError("--css path must not be empty.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise CliArgumentError("--css path contains invalid control characters.")
    if (
        raw.startswith("//")
        or raw.startswith("\\\\")
        or PureWindowsPath(raw).drive.startswith("\\\\")
    ):
        raise CliArgumentError(f"UNC paths are not allowed for --css: {css_arg!r}")

    raw_path = Path(raw)
    if raw_path.is_absolute():
        candidate = raw_path
    else:
        cwd_candidate = Path.cwd() / raw_path
        base_candidate = base_dir_path / raw_path
        candidate = cwd_candidate if cwd_candidate.exists() else base_candidate

    resolved_css = candidate.resolve(strict=False)
    resolved_base = base_dir_path.resolve(strict=False)

    if not allow_outside_base_dir and not resolved_css.is_relative_to(resolved_base):
        raise CliArgumentError(
            f"--css file {css_arg!r} resolves outside --base-dir {str(resolved_base)!r}. "
            "Pass --allow-outside-base-dir to override."
        )
    if not resolved_css.exists():
        raise CliArgumentError(f"--css file not found: {css_arg}")
    if not resolved_css.is_file():
        raise CliArgumentError(f"--css path is not a file: {css_arg}")

    try:
        return resolved_css.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliArgumentError(f"Failed to read --css file {css_arg!r}: {exc}") from exc


def _validate_preview_dir(
    preview_arg: str,
    input_path: Path | None,
    output_path: Path,
) -> str:
    """Validate --preview-dir does not collide with input/output files and is writable."""
    raw = preview_arg.strip()
    if not raw:
        raise CliArgumentError("--preview-dir must not be empty.")
    preview_path = Path(raw)
    resolved_preview = preview_path.resolve(strict=False)

    if input_path is not None and resolved_preview == input_path.resolve(strict=False):
        raise CliArgumentError("--preview-dir cannot be the same path as INPUT.md.")
    if resolved_preview == output_path.resolve(strict=False):
        raise CliArgumentError("--preview-dir cannot be the same path as the output PDF.")
    if preview_path.exists() and not preview_path.is_dir():
        raise CliArgumentError(f"--preview-dir path exists and is not a directory: {preview_arg}")
    if not _is_directory_writable(preview_path):
        raise CliArgumentError(f"--preview-dir is not writable: {preview_arg}")

    return str(preview_path)


def _run_list_themes(json_mode: bool, quiet_mode: bool) -> int:
    """Handle --list-themes in JSON or human-readable format."""
    if json_mode:
        payload = {
            "ok": True,
            "themes": [
                {
                    "name": spec.name,
                    "default": spec.is_default,
                    "description": spec.description,
                }
                for spec in THEMES.values()
            ],
            "version": __version__,
        }
        print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
        return EXIT_OK

    if not quiet_mode:
        for spec in THEMES.values():
            marker = " (default)" if spec.is_default else ""
            print(f"{spec.name:<12} {spec.description}{marker}", file=sys.stdout)
    return EXIT_OK


def _build_strict_error_summary(result: ConversionResult) -> str:
    parts: list[str] = []
    if result.layout_issues:
        parts.append(f"{len(result.layout_issues)} layout issue(s)")
    if result.mermaid_errors:
        parts.append(f"{len(result.mermaid_errors)} mermaid error(s)")
    if result.math_errors:
        parts.append(f"{len(result.math_errors)} math error(s)")
    return f"Strict mode failed: {', '.join(parts)}"


def _build_result_payload(
    result: ConversionResult,
    warnings: list[str],
    *,
    strict_error: str | None = None,
) -> dict[str, object]:
    """Build the §4 JSON payload for conversion success (exit 0) or strict failure (exit 6)."""
    payload: dict[str, object] = {}
    if strict_error is not None:
        payload["ok"] = False
        payload["code"] = EXIT_STRICT_ISSUE
        payload["error"] = strict_error
    else:
        payload["ok"] = True

    payload.update(
        {
            "output": str(result.output_pdf_path),
            "theme": str(result.theme),
            "pages": int(result.page_count),
            "embedded_images": int(result.embedded_images),
            "suppressed_images": list(result.suppressed_images),
            "warnings": warnings,
            "layout_issues": [issue.to_dict() for issue in result.layout_issues],
            "mermaid_errors": [err.to_dict() for err in result.mermaid_errors],
            "math_errors": [err.to_dict() for err in result.math_errors],
            "adjustments_applied": [adj.to_dict() for adj in result.adjustments_applied],
            "last_page_fill_ratio": round(float(result.last_page_fill_ratio), 4),
            "preview_images": list(result.preview_images),
            "version": __version__,
            "elapsed_seconds": round(float(result.elapsed_seconds), 4),
        }
    )
    return payload


def run(args: argparse.Namespace) -> int:
    """Execute the conversion workflow from validated CLI arguments."""
    json_mode = bool(getattr(args, "json", False))
    quiet_mode = bool(getattr(args, "quiet", False))

    if getattr(args, "list_themes", False):
        return _run_list_themes(json_mode, quiet_mode)

    try:
        md_text = _read_markdown_input(str(args.input))
        input_path: Path | None = None

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
            if output_path.resolve() == input_path.resolve():
                raise CliArgumentError(
                    "Output PDF path cannot be the same file as the input file."
                )

        if not base_dir_path.exists():
            raise CliArgumentError(f"--base-dir does not exist: {base_dir_path}")
        if not base_dir_path.is_dir():
            raise CliArgumentError(f"--base-dir is not a directory: {base_dir_path}")

        if output_path.exists() and output_path.is_dir():
            raise CliArgumentError(f"Output path is a directory: {output_path}")

        output_path, fallback_warning = _resolve_writable_output_path(
            output_path,
            explicit_output=args.output is not None,
            input_path=input_path,
        )

        cli_css_text = ""
        raw_css_arg = getattr(args, "css", None)
        if raw_css_arg is not None:
            cli_css_text = _read_cli_css_file(
                str(raw_css_arg),
                base_dir_path,
                bool(getattr(args, "allow_outside_base_dir", False)),
            )

        validated_preview_dir: str | None = None
        raw_preview_arg = getattr(args, "preview_dir", None)
        if raw_preview_arg is not None:
            validated_preview_dir = _validate_preview_dir(
                str(raw_preview_arg),
                input_path,
                output_path,
            )

        fm, md_body = parse_frontmatter(md_text)
        options = build_conversion_options(
            args,
            md_text=md_text,
            frontmatter=fm,
            md_body=md_body,
            cli_css=cli_css_text,
            preview_dir=validated_preview_dir,
        )
        compute_page_geometry(options.page_size, options.margin_css)
    except CliArgumentError as exc:
        return _emit_error(exc.message, exc.exit_code, json_mode)
    except ValueError as exc:
        return _emit_error(str(exc), EXIT_BAD_ARGS, json_mode)

    try:
        find_browser_executable()
        result: ConversionResult = render_pdf(
            md_text,
            str(base_dir_path),
            str(output_path),
            options=options,
            frontmatter=fm,
            md_body=md_body,
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
    if (
        result.page_count <= 0
        or not resolved_out.is_file()
        or resolved_out.stat().st_size == 0
    ):
        return _emit_error(
            f"Output PDF is empty or has 0 pages: {resolved_out}",
            EXIT_EMPTY_PDF,
            json_mode,
        )

    warnings: list[str] = []
    if fallback_warning is not None:
        warnings.append(fallback_warning)
    for item in result.warnings:
        w_str = str(item)
        if w_str not in warnings:
            warnings.append(w_str)

    if result.suppressed_images:
        sanitized = [_sanitize_for_stderr(img) for img in result.suppressed_images]
        joined = ", ".join(sanitized)
        print(
            f"Warning: {len(result.suppressed_images)} suppressed image(s) "
            f"(missing or outside base-dir): {joined}",
            file=sys.stderr,
        )

    if warnings:
        sanitized_warnings = [_sanitize_for_stderr(w) for w in warnings]
        print(f"Warning: {'; '.join(sanitized_warnings)}", file=sys.stderr)

    strict_failed = bool(options.strict) and bool(
        result.layout_issues or result.mermaid_errors or result.math_errors
    )
    if strict_failed:
        strict_error = _build_strict_error_summary(result)
        if json_mode:
            payload = _build_result_payload(result, warnings, strict_error=strict_error)
            print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
        else:
            print(f"error: {_sanitize_for_stderr(strict_error)}", file=sys.stderr)
        return EXIT_STRICT_ISSUE

    if json_mode:
        payload = _build_result_payload(result, warnings)
        print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
    elif not quiet_mode:
        print(
            f"Rendered {result.output_pdf_path} "
            f"({result.page_count} page(s), {result.embedded_images} image(s), "
            f"[{result.theme}], {result.elapsed_seconds:.2f}s)",
            file=sys.stdout,
        )

    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``mdtopdf`` console script."""
    _configure_stdio_utf8()
    effective_argv = list(argv) if argv is not None else sys.argv[1:]
    json_requested = _is_json_requested(effective_argv)

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
