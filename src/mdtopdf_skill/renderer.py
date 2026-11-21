"""End-to-end PDF conversion orchestration for mdtopdf-skill."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymupdf

from mdtopdf_skill.autofit import run_autofit_render
from mdtopdf_skill.cdp_browser import CdpSession, find_browser_executable
from mdtopdf_skill.frontmatter import parse_frontmatter
from mdtopdf_skill.markdown_pipeline import build_html_document
from mdtopdf_skill.models import (
    ConversionOptions,
    ConversionResult,
    FrontmatterData,
    compute_page_geometry,
    resolve_effective_options,
)
from mdtopdf_skill.sanitizer import ASSETS_DIR
from mdtopdf_skill.themes import (
    THEMES,
    build_header_footer_templates,
    build_metadata_header_html,
    build_theme_css,
    get_theme,
)

__all__ = ["render_pdf"]

_WIN_FILE_PATH_RE = re.compile(r"^/([A-Za-z]:[\\/].*)$")


class _BodyImageCounter(HTMLParser):
    """Count embedded local file and data:image/* <img> tags inside HTML <body>."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_body = False
        self.count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower_tag = tag.lower()
        if lower_tag == "body":
            self._in_body = True
            return
        if not self._in_body or lower_tag != "img":
            return
        self._check_img(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._in_body and tag.lower() == "img":
            self._check_img(attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "body":
            self._in_body = False

    def _check_img(self, attrs: list[tuple[str, str | None]]) -> None:
        src = ""
        for name, value in attrs:
            if name.lower() == "src" and value is not None:
                src = value.strip()
                break
        if not src:
            return
        lower_src = src.lower()
        if lower_src.startswith("data:image/"):
            self.count += 1
            return
        if lower_src.startswith("file:"):
            parsed = urlparse(src)
            if parsed.netloc.lower() not in {"", "localhost"}:
                return
            decoded = unquote(parsed.path)
            win_match = _WIN_FILE_PATH_RE.match(decoded)
            if win_match is not None:
                decoded = win_match.group(1)
            if Path(decoded).is_file():
                self.count += 1


def _count_embedded_images(html_text: str) -> int:
    """Count valid existing local or data-URI <img> elements inside HTML body."""
    parser = _BodyImageCounter()
    parser.feed(html_text)
    parser.close()
    return parser.count


def _atomic_write_bytes(target_path: Path, data: bytes) -> None:
    """Atomically write bytes via a sibling temporary file and os.replace."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target_path.parent,
            prefix=".mdtopdf_tmp_",
            suffix=target_path.suffix or ".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(data)
            tmp_file.flush()
        os.replace(tmp_path, target_path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def _export_preview_pngs(pdf_bytes: bytes, preview_dir: str, dpi: int) -> list[str]:
    """Render each PDF page as page_001.png, page_002.png, ... at the configured DPI."""
    out_dir = Path(preview_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exported: list[str] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
        for idx, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            png_path = out_dir / f"page_{idx:03d}.png"
            _atomic_write_bytes(png_path, bytes(pix.tobytes("png")))
            exported.append(str(png_path))
    return exported


def render_pdf(
    md_text: str,
    base_dir: str,
    output_pdf_path: str,
    options: ConversionOptions | None = None,
    *,
    frontmatter: FrontmatterData | None = None,
    md_body: str | None = None,
) -> ConversionResult:
    """Convert Markdown text into a print-ready PDF using headless Edge/Chrome via CDP."""
    t0 = time.monotonic()

    if frontmatter is None or md_body is None:
        parsed_fm, parsed_body = parse_frontmatter(md_text)
        fm = frontmatter if frontmatter is not None else parsed_fm
        body = md_body if md_body is not None else parsed_body
    else:
        fm = frontmatter
        body = md_body

    effective_opts = (
        options
        if options is not None
        else resolve_effective_options(frontmatter=fm, themes=THEMES)
    )
    theme_spec = get_theme(effective_opts.theme)
    geometry = compute_page_geometry(effective_opts.page_size, effective_opts.margin_css)

    theme_css = build_theme_css(effective_opts, geometry.printable_height_mm)
    meta_header_html, dedup_title = build_metadata_header_html(effective_opts, fm)
    header_footer = build_header_footer_templates(effective_opts, fm)

    built_doc = build_html_document(
        body,
        base_dir,
        effective_opts,
        fm,
        geometry.printable_height_mm,
        theme_css=theme_css,
        metadata_header_html=meta_header_html,
        dedup_title=dedup_title,
    )
    combined_warnings = list(dict.fromkeys([*fm.warnings, *built_doc.warnings]))
    embedded_images = _count_embedded_images(built_doc.html)

    browser_path = find_browser_executable()

    with tempfile.TemporaryDirectory(
        prefix="mdtopdf_render_", ignore_cleanup_errors=True
    ) as tmp_dir:
        html_path = Path(tmp_dir) / "document.html"
        html_path.write_text(built_doc.html, encoding="utf-8")

        allowed_roots: list[str | Path] = (
            [tmp_dir, ASSETS_DIR]
            if effective_opts.allow_outside_base_dir
            else [tmp_dir, ASSETS_DIR, base_dir]
        )
        with CdpSession(
            browser_path=browser_path,
            allowed_file_roots=allowed_roots,
            allow_any_local_file=effective_opts.allow_outside_base_dir,
        ) as session:
            session.emulate_print_viewport(geometry)
            session.navigate(html_path)
            outcome = run_autofit_render(
                session=session,
                doc=built_doc,
                options=effective_opts,
                header_footer=header_footer,
                geometry=geometry,
            )

    out_pdf = Path(output_pdf_path)
    _atomic_write_bytes(out_pdf, outcome.pdf_bytes)

    preview_images: list[str] = []
    if effective_opts.preview_dir and outcome.page_count >= 1 and outcome.pdf_bytes:
        preview_images = _export_preview_pngs(
            outcome.pdf_bytes,
            effective_opts.preview_dir,
            effective_opts.preview_dpi,
        )

    elapsed_seconds = max(0.0001, round(time.monotonic() - t0, 4))
    return ConversionResult(
        output_pdf_path=str(out_pdf),
        theme=theme_spec.name,
        page_count=outcome.page_count,
        embedded_images=embedded_images,
        suppressed_images=list(built_doc.suppressed_images),
        warnings=combined_warnings,
        layout_issues=outcome.layout_issues,
        mermaid_errors=outcome.mermaid_errors,
        math_errors=outcome.math_errors,
        adjustments_applied=outcome.adjustments_applied,
        last_page_fill_ratio=outcome.last_page_fill_ratio,
        preview_images=preview_images,
        elapsed_seconds=elapsed_seconds,
    )
